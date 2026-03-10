# Workday Integration Monitoring Agent

An AI-powered monitoring agent that automatically reads Freshservice incidents, performs root cause analysis on Workday HR integration failures using RAG (Retrieval-Augmented Generation), and resolves tickets with grounded, sourced answers.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Web UI (FastAPI + Jinja2)             │
│              http://localhost:8000                       │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│              LangGraph Monitoring Agent                  │
│  fetch_ticket → enrich_context → retrieve_rag →         │
│  analyze_llm → validate_and_act                         │
└──────┬────────────────┬──────────────────┬──────────────┘
       │                │                  │
┌──────▼──────┐  ┌──────▼──────┐  ┌───────▼──────┐
│ Freshservice │  │   Workday   │  │  RAG Service │
│  API Client  │  │  API Client │  │  (ChromaDB)  │
└─────────────┘  └─────────────┘  └──────────────┘
                                         │
                              ┌──────────▼──────────┐
                              │  Knowledge Base      │
                              │  (.md / .txt files)  │
                              └─────────────────────┘
```

---

## Features

- **Natural Language Queries**: Ask about any Workday integration issue in plain English
- **Freshservice Integration**: Fetch, analyze, update, and resolve tickets automatically
- **RAG with Grounding**: Answers are grounded in your knowledge base — no hallucination
- **Workday Error Classification**: Automatically classifies errors (auth, data validation, connectivity, etc.)
- **LangGraph Agent**: Multi-step agentic workflow with state management
- **Auto-Resolution**: Optionally auto-resolves high-confidence tickets
- **Batch Processing**: Sweep all open tickets with one API call
- **Knowledge Base Management**: Ingest runbooks, post-mortems, Workday docs via UI or API

---

## Quick Start

### 1. Clone and Configure

```bash
git clone <repo-url>
cd workday-integration-agent
cp .env.example .env
# Edit .env with your API keys
```

### 2. Install Dependencies

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Seed the Knowledge Base

```bash
# The agent auto-loads files from data/knowledge_base/ on startup
# Add your own runbooks, error guides as .md or .txt files
```

### 4. Run the Agent

```bash
python main.py
# Open: http://localhost:8000
```

### 5. Or with Docker

```bash
docker-compose up --build
```

---

## Configuration

All configuration is via environment variables (`.env` file). See `.env.example` for all options.

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | ✅ | Claude API key |
| `FRESHSERVICE_DOMAIN` | ✅ | e.g. `company.freshservice.com` |
| `FRESHSERVICE_API_KEY` | ✅ | Freshservice API key |
| `WORKDAY_TENANT_URL` | ⚠️ | Workday REST API base URL |
| `WORKDAY_CLIENT_ID` | ⚠️ | OAuth2 client ID |
| `AGENT_AUTO_RESOLVE` | | `true` to enable auto-resolution (default: `false`) |
| `AGENT_CONFIDENCE_THRESHOLD` | | Minimum confidence to auto-resolve (default: `0.85`) |
| `RAG_SIMILARITY_THRESHOLD` | | Minimum similarity score for RAG (default: `0.7`) |

---

## API Reference

### Agent
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/agent/query` | POST | Natural language query |
| `/api/v1/agent/analyze-ticket` | POST | Analyze specific ticket |
| `/api/v1/agent/poll-tickets` | POST | Batch process all open tickets |

### Tickets
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/tickets/open` | GET | List open Freshservice tickets |
| `/api/v1/tickets/{id}` | GET | Get ticket by ID |
| `/api/v1/tickets/{id}` | PUT | Update ticket |

### Knowledge Base
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/knowledge/ingest` | POST | Ingest a document |
| `/api/v1/knowledge/ingest-file` | POST | Upload .txt/.md file |
| `/api/v1/knowledge/search` | POST | Search knowledge base |
| `/api/v1/knowledge/stats` | GET | Knowledge base statistics |

### Health
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/health` | GET | System health check |

Full interactive docs: `http://localhost:8000/docs`

---

## Knowledge Base

Add documents to `data/knowledge_base/` in subdirectories by category:

```
data/knowledge_base/
├── workday_integration/    # Error guides, integration docs
├── runbook/                # Incident response runbooks
├── postmortem/             # Post-mortem reports
└── freshservice/           # ITSM process docs
```

The agent auto-ingests these on startup. You can also use the web UI or API.

---

## Grounding

The agent uses **grounding** to ensure LLM responses are anchored to retrieved knowledge:

1. Query is matched against ChromaDB vector store
2. Top-K relevant documents are retrieved (similarity ≥ threshold)
3. LLM is instructed via system prompt to **only** use retrieved context
4. Response is validated: confidence = f(similarity scores, citation presence)
5. Responses below confidence threshold are flagged for manual review

---

## Running Tests

```bash
pytest tests/ -v
```

---

## Project Structure

```
workday-integration-agent/
├── main.py                         # Entry point
├── .env.example                    # Config template
├── requirements.txt
├── Dockerfile / docker-compose.yml
├── app/
│   ├── agents/
│   │   └── monitoring_agent.py     # LangGraph agent
│   ├── api/
│   │   ├── app.py                  # FastAPI factory
│   │   └── routes/
│   │       ├── agent.py
│   │       ├── health_tickets.py
│   │       └── knowledge.py
│   ├── core/
│   │   ├── config.py               # Settings (Pydantic)
│   │   ├── dependencies.py         # DI container
│   │   └── logging.py
│   ├── models/
│   │   ├── database.py             # SQLAlchemy models
│   │   └── schemas.py              # Pydantic schemas
│   └── services/
│       ├── freshservice_client.py
│       ├── workday_client.py
│       └── rag_service.py
├── frontend/
│   ├── templates/index.html
│   └── static/
│       ├── css/main.css
│       └── js/main.js
├── data/
│   ├── knowledge_base/             # RAG documents
│   └── chroma_db/                  # Vector store (auto-created)
└── tests/
    └── unit/
        └── test_rag_service.py
```
