"""
Freshservice ITSM Integration Service.
Handles all interactions with the Freshservice API.
"""
import base64
from typing import Any, Dict, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import get_logger
from app.models.schemas import FreshserviceTicket, TicketUpdateRequest

logger = get_logger(__name__)


class FreshserviceClient:
    """Production-grade Freshservice API client with retry logic."""

    def __init__(self):
        api_key = settings.FRESHSERVICE_API_KEY.get_secret_value()
        credentials = base64.b64encode(f"{api_key}:X".encode()).decode()
        self.base_url = f"https://{settings.FRESHSERVICE_DOMAIN}/api/v2"
        self.headers = {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
        }
        self.timeout = httpx.Timeout(30.0)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _request(
        self,
        method: str,
        endpoint: str,
        payload: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=self.headers,
                json=payload,
                params=params,
            )
            response.raise_for_status()
            return response.json()

    async def get_open_tickets(
        self, page: int = 1, per_page: int = 30
    ) -> List[FreshserviceTicket]:
        """Fetch open/in-progress incidents from Freshservice."""
        try:
            data = await self._request(
                "GET",
                "/tickets",
                params={
                    "filter": "open",
                    "page": page,
                    "per_page": per_page,
                    "order_by": "created_at",
                    "order_type": "desc",
                },
            )
            tickets = []
            for t in data.get("tickets", []):
                tickets.append(self._parse_ticket(t))
            logger.info(f"Fetched {len(tickets)} open tickets from Freshservice")
            return tickets
        except httpx.HTTPStatusError as e:
            logger.error(f"Freshservice API error: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Failed to fetch tickets: {str(e)}")
            raise

    async def get_ticket(self, ticket_id: int) -> FreshserviceTicket:
        """Get a single ticket by ID."""
        data = await self._request("GET", f"/tickets/{ticket_id}")
        return self._parse_ticket(data["ticket"])

    async def update_ticket(
        self, ticket_id: int, update: TicketUpdateRequest
    ) -> FreshserviceTicket:
        """Update ticket status, priority, and add resolution note."""
        payload: Dict[str, Any] = {}

        if update.status is not None:
            payload["status"] = update.status
        if update.priority is not None:
            payload["priority"] = update.priority

        if payload:
            data = await self._request("PUT", f"/tickets/{ticket_id}", payload={"ticket": payload})
            logger.info(f"Updated ticket {ticket_id}: {payload}")

        # Add note if provided
        if update.note or update.resolution_note:
            note_body = update.resolution_note or update.note
            await self.add_note(ticket_id, note_body, private=False)

        return await self.get_ticket(ticket_id)

    async def add_note(
        self, ticket_id: int, body: str, private: bool = True
    ) -> Dict[str, Any]:
        """Add a note/conversation to a ticket."""
        payload = {
            "conversation": {
                "body": body,
                "private": private,
            }
        }
        data = await self._request(
            "POST", f"/tickets/{ticket_id}/notes", payload=payload
        )
        logger.info(f"Added {'private' if private else 'public'} note to ticket {ticket_id}")
        return data

    async def resolve_ticket(
        self, ticket_id: int, resolution_note: str
    ) -> FreshserviceTicket:
        """Resolve a ticket with a resolution note."""
        update = TicketUpdateRequest(
            status=settings.FRESHSERVICE_TICKET_STATUS_RESOLVED,
            resolution_note=f"[AI Agent Resolution]\n\n{resolution_note}",
        )
        return await self.update_ticket(ticket_id, update)

    async def search_tickets(self, query: str) -> List[FreshserviceTicket]:
        """Search tickets using Freshservice query language."""
        try:
            data = await self._request(
                "GET",
                "/tickets/filter",
                params={"query": f'"{query}"'},
            )
            return [self._parse_ticket(t) for t in data.get("tickets", [])]
        except Exception as e:
            logger.warning(f"Ticket search failed: {str(e)}")
            return []

    def _parse_ticket(self, raw: Dict[str, Any]) -> FreshserviceTicket:
        return FreshserviceTicket(
            id=raw["id"],
            subject=raw.get("subject", ""),
            description=raw.get("description", ""),
            description_text=raw.get("description_text", ""),
            status=raw.get("status", 2),
            priority=raw.get("priority", 2),
            created_at=raw.get("created_at"),
            updated_at=raw.get("updated_at"),
            requester_id=raw.get("requester_id"),
            responder_id=raw.get("responder_id"),
            tags=raw.get("tags", []),
            custom_fields=raw.get("custom_fields", {}),
        )

    async def health_check(self) -> bool:
        try:
            await self._request("GET", "/tickets", params={"per_page": 1})
            return True
        except Exception:
            return False
