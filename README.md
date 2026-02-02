# PowerplAI

A hockey analytics and fantasy copilot powered by RAG, structured data, and Claude. Ask natural language questions about NHL players, stats, predictions, and fantasy hockey - get data-backed answers with citations.

## Features

- **Natural Language Queries**: "Compare McDavid vs Crosby this season"
- **Structured Stats Database**: PostgreSQL with player stats, game logs, standings
- **Advanced Analytics**: xG, Corsi, Fenwick from MoneyPuck
- **RAG Knowledge Base**: Semantic search over hockey analysis articles
- **Source Citations**: Every answer backed by verifiable data
- **Fantasy & Predictions**: Coming soon - player projections and fantasy insights

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    User Query                           │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│              Query Classification (Claude)              │
│  - stats_lookup / comparison / trend / explainer        │
└─────────────────────┬───────────────────────────────────┘
                      │
          ┌───────────┴───────────┐
          ▼                       ▼
┌──────────────────┐    ┌──────────────────────┐
│   PostgreSQL     │    │     pgvector         │
│   + pgvector     │    │   (RAG embeddings)   │
│                  │    │                      │
│ - Player stats   │    │ - Article chunks     │
│ - Game logs      │    │ - Analysis pieces    │
│ - MoneyPuck xG   │    │ - Hockey knowledge   │
└────────┬─────────┘    └──────────┬───────────┘
         │                         │
         └───────────┬─────────────┘
                     ▼
┌─────────────────────────────────────────────────────────┐
│              Response Generation (Claude)               │
│  - Synthesize data + context                           │
│  - Add citations                                       │
└─────────────────────────────────────────────────────────┘
```

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.11+
- Anthropic API key

### 1. Clone and configure

```bash
cd powerplai
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
```

### 2. Start services

```bash
docker-compose up -d
```

This starts:
- PostgreSQL with pgvector (port 5432)
- ChromaDB (port 8001)
- Backend API (port 8000)

### 3. Ingest data

```bash
# Install Python dependencies
pip install -e .

# Run data ingestion (downloads NHL + MoneyPuck data)
python -m backend.scripts.ingest_data --season 2023
```

### 4. Query the copilot

```bash
# Via API
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Who leads the league in expected goals?"}'
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/query` | POST | Main copilot endpoint - ask any question |
| `/api/players/{name}` | GET | Get stats for a specific player |
| `/api/leaders/{stat}` | GET | Get league leaders for a stat |
| `/api/documents` | POST | Add document to RAG knowledge base |
| `/api/search?q=` | GET | Search the RAG knowledge base |

## Data Sources

| Source | Data Type | Update Frequency |
|--------|-----------|------------------|
| [NHL API](https://api-web.nhle.com) | Rosters, schedules, game logs | Real-time |
| [MoneyPuck](https://moneypuck.com/data.htm) | xG, Corsi, advanced stats | Daily |
| User uploads | Articles, analysis | Manual |

## Project Structure

```
powerplai/
├── backend/
│   ├── src/
│   │   ├── api/          # FastAPI endpoints
│   │   ├── agents/       # Copilot + RAG logic
│   │   ├── db/           # Database models
│   │   └── ingestion/    # Data pipelines
│   ├── scripts/          # CLI utilities
│   └── tests/
├── frontend/             # Next.js UI
├── data/
│   ├── raw/             # Downloaded CSVs
│   └── processed/       # Transformed data
├── docker/
│   ├── Dockerfile.backend
│   └── init.sql
└── docker-compose.yml
```

## Roadmap

- [x] **v0.1**: Core RAG + stats queries
- [ ] **v0.2**: Frontend UI with chat interface
- [ ] **v0.3**: Prediction models (player performance, game outcomes)
- [ ] **v0.4**: Fantasy hockey module (lineup optimization, trade analysis)
- [ ] **v0.5**: Betting module (odds API, +EV detection)
- [ ] **v0.6**: Eval suite for measuring accuracy
- [ ] **v0.7**: Multi-agent system (specialized analysts)

## Tech Stack

- **Backend**: Python, FastAPI, SQLAlchemy
- **Database**: PostgreSQL + pgvector
- **LLM**: Claude (Anthropic)
- **Embeddings**: all-MiniLM-L6-v2
- **Frontend**: Next.js, TypeScript, Tailwind
- **Infra**: Docker Compose (local), AWS/Vercel (prod)

## License

MIT
