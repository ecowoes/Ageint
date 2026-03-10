"""
Integration Monitoring Agent using LangGraph.
Orchestrates: ticket fetch → context enrichment → RAG retrieval → LLM analysis → resolution.
"""
import json
import time
from typing import Any, Dict, List, Optional, TypedDict

from anthropic import AsyncAnthropic
from langgraph.graph import END, StateGraph

from app.core.config import settings
from app.core.logging import get_logger
from app.models.schemas import (
    AgentQueryRequest,
    AgentQueryResponse,
    FreshserviceTicket,
    IncidentAnalysis,
    RAGSource,
    TicketUpdateRequest,
)
from app.services.freshservice_client import FreshserviceClient
from app.services.rag_service import RAGService
from app.services.workday_client import WorkdayClient

logger = get_logger(__name__)


# ─── Agent State ─────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    query: str
    ticket_id: Optional[int]
    ticket: Optional[FreshserviceTicket]
    workday_context: Optional[Dict]
    rag_sources: List[RAGSource]
    llm_response: Optional[str]
    root_cause: Optional[str]
    resolution_steps: List[str]
    confidence_score: float
    grounded: bool
    ticket_updated: bool
    error: Optional[str]
    iterations: int
    auto_resolve: bool
    final_answer: Optional[str]


# ─── Agent ───────────────────────────────────────────────────────────────────

class IntegrationMonitoringAgent:
    """
    LangGraph-based agentic workflow for Workday integration incident analysis.

    Workflow:
        START → fetch_ticket → enrich_workday_context → retrieve_rag_context
              → analyze_with_llm → validate_resolution → (resolve_ticket?) → END
    """

    def __init__(
        self,
        freshservice: FreshserviceClient,
        workday: WorkdayClient,
        rag: RAGService,
    ):
        self.freshservice = freshservice
        self.workday = workday
        self.rag = rag
        self.llm = AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY.get_secret_value()
        )
        self._graph = self._build_graph()

    def _build_graph(self) -> Any:
        """Build the LangGraph state machine."""
        workflow = StateGraph(AgentState)

        workflow.add_node("fetch_ticket", self._fetch_ticket)
        workflow.add_node("enrich_workday_context", self._enrich_workday_context)
        workflow.add_node("retrieve_rag_context", self._retrieve_rag_context)
        workflow.add_node("analyze_with_llm", self._analyze_with_llm)
        workflow.add_node("validate_and_act", self._validate_and_act)

        workflow.set_entry_point("fetch_ticket")
        workflow.add_edge("fetch_ticket", "enrich_workday_context")
        workflow.add_edge("enrich_workday_context", "retrieve_rag_context")
        workflow.add_edge("retrieve_rag_context", "analyze_with_llm")
        workflow.add_edge("analyze_with_llm", "validate_and_act")
        workflow.add_edge("validate_and_act", END)

        return workflow.compile()

    # ─── Node: Fetch Ticket ───────────────────────────────────────────────

    async def _fetch_ticket(self, state: AgentState) -> AgentState:
        """Fetch ticket details from Freshservice if ticket_id provided."""
        if not state.get("ticket_id"):
            return {**state, "ticket": None}

        try:
            ticket = await self.freshservice.get_ticket(state["ticket_id"])
            logger.info(f"Fetched ticket {ticket.id}: {ticket.subject}")
            return {**state, "ticket": ticket}
        except Exception as e:
            logger.error(f"Failed to fetch ticket {state['ticket_id']}: {e}")
            return {**state, "error": str(e), "ticket": None}

    # ─── Node: Enrich Workday Context ─────────────────────────────────────

    async def _enrich_workday_context(self, state: AgentState) -> AgentState:
        """Classify the error and extract Workday integration context."""
        query = state["query"]
        ticket = state.get("ticket")
        full_text = query

        if ticket:
            full_text = f"{ticket.subject}\n\n{ticket.description_text or ticket.description}"

        context = self.workday.extract_integration_context(full_text)
        logger.info(
            "Workday context extracted",
            extra={"category": context["error_classification"]["category"]},
        )
        return {**state, "workday_context": context}

    # ─── Node: Retrieve RAG Context ───────────────────────────────────────

    async def _retrieve_rag_context(self, state: AgentState) -> AgentState:
        """Retrieve relevant knowledge base documents using RAG."""
        query = state["query"]
        workday_ctx = state.get("workday_context", {})

        # Enrich the RAG query with classified error category for better retrieval
        error_category = workday_ctx.get("error_classification", {}).get("category", "")
        enriched_query = f"{query}"
        if error_category and error_category != "unknown":
            enriched_query = f"{error_category} {query}"

        sources = await self.rag.retrieve(enriched_query, top_k=settings.RAG_TOP_K)
        logger.info(f"Retrieved {len(sources)} RAG sources for query")
        return {**state, "rag_sources": sources}

    # ─── Node: Analyze with LLM ───────────────────────────────────────────

    async def _analyze_with_llm(self, state: AgentState) -> AgentState:
        """Core LLM analysis with grounded RAG context."""
        query = state["query"]
        sources = state.get("rag_sources", [])
        ticket = state.get("ticket")
        workday_ctx = state.get("workday_context", {})

        # Build ticket context string
        ticket_context = None
        if ticket:
            ticket_context = (
                f"Ticket ID: {ticket.id}\n"
                f"Subject: {ticket.subject}\n"
                f"Priority: {ticket.priority}\n"
                f"Description: {ticket.description_text or ticket.description}\n"
                f"Tags: {', '.join(ticket.tags)}"
            )
        
        if workday_ctx:
            ec = workday_ctx.get("error_classification", {})
            integrations = workday_ctx.get("likely_integrations", [])
            ticket_context = (ticket_context or "") + (
                f"\n\nWorkday Error Category: {ec.get('category', 'unknown')}"
                f"\nLikely Integrations: {', '.join(integrations) or 'Not determined'}"
            )

        system_prompt, user_prompt = self.rag.build_grounded_prompt(
            query=query,
            sources=sources,
            ticket_context=ticket_context,
        )

        try:
            message = await self.llm.messages.create(
                model=settings.LLM_MODEL,
                max_tokens=settings.LLM_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            response_text = message.content[0].text

            # Parse structured output from LLM response
            root_cause = self._extract_section(response_text, "root cause")
            resolution_steps = self._extract_steps(response_text)
            confidence, grounded = self.rag.calculate_grounding_confidence(sources, response_text)

            logger.info(
                "LLM analysis complete",
                extra={"confidence": confidence, "grounded": grounded},
            )
            return {
                **state,
                "llm_response": response_text,
                "root_cause": root_cause,
                "resolution_steps": resolution_steps,
                "confidence_score": confidence,
                "grounded": grounded,
                "final_answer": response_text,
                "iterations": state.get("iterations", 0) + 1,
            }
        except Exception as e:
            logger.error(f"LLM analysis failed: {e}")
            return {
                **state,
                "error": str(e),
                "final_answer": f"Analysis failed: {str(e)}",
                "confidence_score": 0.0,
                "grounded": False,
            }

    # ─── Node: Validate and Act ───────────────────────────────────────────

    async def _validate_and_act(self, state: AgentState) -> AgentState:
        """Decide whether to auto-resolve ticket or flag for manual review."""
        ticket = state.get("ticket")
        confidence = state.get("confidence_score", 0.0)
        auto_resolve = state.get("auto_resolve", False)
        final_answer = state.get("final_answer", "")

        if not ticket or not auto_resolve:
            return {**state, "ticket_updated": False}

        try:
            if confidence >= settings.AGENT_CONFIDENCE_THRESHOLD:
                # High confidence → resolve the ticket
                resolution_summary = self._format_resolution_note(state)
                await self.freshservice.resolve_ticket(ticket.id, resolution_summary)
                logger.info(
                    f"Ticket {ticket.id} auto-resolved",
                    extra={"confidence": confidence},
                )
                return {**state, "ticket_updated": True}
            else:
                # Low confidence → add investigation note and move to In Progress
                note = (
                    f"**[AI Agent Analysis - Confidence: {confidence:.0%}]**\n\n"
                    f"{final_answer}\n\n"
                    f"⚠️ Confidence below threshold ({settings.AGENT_CONFIDENCE_THRESHOLD:.0%}). "
                    f"Manual review recommended."
                )
                await self.freshservice.update_ticket(
                    ticket.id,
                    TicketUpdateRequest(
                        status=settings.FRESHSERVICE_TICKET_STATUS_IN_PROGRESS,
                        note=note,
                    ),
                )
                logger.info(
                    f"Ticket {ticket.id} marked in-progress (low confidence)",
                    extra={"confidence": confidence},
                )
                return {**state, "ticket_updated": True}
        except Exception as e:
            logger.error(f"Failed to update ticket {ticket.id}: {e}")
            return {**state, "ticket_updated": False, "error": str(e)}

    # ─── Public API ───────────────────────────────────────────────────────

    async def process_query(self, request: AgentQueryRequest) -> AgentQueryResponse:
        """Main entry point: process a natural language query or ticket ID."""
        start_time = time.time()

        initial_state: AgentState = {
            "query": request.query,
            "ticket_id": request.ticket_id,
            "ticket": None,
            "workday_context": None,
            "rag_sources": [],
            "llm_response": None,
            "root_cause": None,
            "resolution_steps": [],
            "confidence_score": 0.0,
            "grounded": False,
            "ticket_updated": False,
            "error": None,
            "iterations": 0,
            "auto_resolve": request.auto_resolve,
            "final_answer": None,
        }

        final_state = await self._graph.ainvoke(initial_state)
        elapsed_ms = int((time.time() - start_time) * 1000)

        return AgentQueryResponse(
            query=request.query,
            answer=final_state.get("final_answer") or "Unable to generate analysis.",
            root_cause=final_state.get("root_cause"),
            resolution_steps=final_state.get("resolution_steps", []),
            rag_sources=final_state.get("rag_sources", []),
            confidence_score=final_state.get("confidence_score", 0.0),
            ticket_id=request.ticket_id,
            ticket_updated=final_state.get("ticket_updated", False),
            processing_time_ms=elapsed_ms,
            agent_iterations=final_state.get("iterations", 0),
        )

    async def analyze_ticket(
        self, ticket_id: int, auto_resolve: bool = False
    ) -> AgentQueryResponse:
        """Analyze a specific Freshservice ticket."""
        ticket = await self.freshservice.get_ticket(ticket_id)
        query = f"{ticket.subject}\n\n{ticket.description_text or ticket.description}"
        return await self.process_query(
            AgentQueryRequest(
                query=query,
                ticket_id=ticket_id,
                auto_resolve=auto_resolve,
            )
        )

    async def poll_and_process_open_tickets(self) -> List[AgentQueryResponse]:
        """Batch process all open Freshservice tickets."""
        tickets = await self.freshservice.get_open_tickets()
        results = []
        for ticket in tickets:
            try:
                result = await self.analyze_ticket(
                    ticket.id, auto_resolve=settings.AGENT_AUTO_RESOLVE
                )
                results.append(result)
            except Exception as e:
                logger.error(f"Failed to process ticket {ticket.id}: {e}")
        return results

    # ─── Helpers ─────────────────────────────────────────────────────────

    def _extract_section(self, text: str, section_name: str) -> Optional[str]:
        """Extract a named section from LLM response."""
        lines = text.split("\n")
        capture = False
        result = []
        for line in lines:
            if section_name.lower() in line.lower():
                capture = True
                continue
            if capture:
                if line.strip().startswith(("2.", "3.", "4.", "**Resolution", "**Workaround", "**Confidence")):
                    break
                if line.strip():
                    result.append(line.strip())
        return " ".join(result) if result else None

    def _extract_steps(self, text: str) -> List[str]:
        """Extract numbered resolution steps from LLM response."""
        import re
        steps = re.findall(r"^\s*\d+\.\s+(.+)$", text, re.MULTILINE)
        return steps[:10]  # Cap at 10 steps

    def _format_resolution_note(self, state: AgentState) -> str:
        steps = state.get("resolution_steps", [])
        steps_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
        sources = state.get("rag_sources", [])
        sources_text = "\n".join(f"- {s.source}" for s in sources[:3])
        confidence = state.get("confidence_score", 0.0)

        return (
            f"**[AI Agent Auto-Resolution | Confidence: {confidence:.0%}]**\n\n"
            f"**Root Cause:**\n{state.get('root_cause', 'See full analysis below')}\n\n"
            f"**Resolution Steps Applied:**\n{steps_text}\n\n"
            f"**Knowledge Sources Used:**\n{sources_text}\n\n"
            f"---\n{state.get('final_answer', '')}"
        )
