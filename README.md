# PowerplAI

A hockey analytics copilot that combines structured stats with RAG to answer natural language questions about the NHL. Think ChatGPT for hockey nerds, but grounded in real data.

Ask "Who should I start tonight?" and get probability-weighted predictions. Ask "Compare McDavid to Crosby" and get per-game normalized stats with context. Ask "What is expected goals?" and get an explanation pulled from indexed hockey analytics articles.

## Why I Built This

Most fantasy hockey tools are either:
- Raw stat dumps with no analysis
- Paywalled "expert" picks with no methodology
- Pure vibes-based

I wanted something that could explain *why* a player might pop off tonight, backed by actual data—recent form, goalie matchups, team pace, head-to-head history. And I wanted to ask questions in plain English.

## What It Actually Does

**Stats queries** → SQL against PostgreSQL with player/game data
```
"How many goals does Makar have this season?"
→ Queries player_season_stats, returns structured response
```

**Comparisons** → Normalized per-60 stats with usage context
```
"Compare Draisaitl and Matthews"
→ Fetches both players, calculates per-game rates, notes ice time differences
```

**Predictions** → Weighted ensemble model for tonight's games
```
"Who's most likely to score tonight?"
→ Runs prediction engine across all scheduled games, ranks by P(goal)
```

**Explainers** → RAG search over hockey analytics content
```
"What does Corsi mean?"
→ Semantic search against indexed articles, synthesizes answer
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        User Query                           │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                 Query Classification (Claude)               │
│                                                             │
│  stats_lookup | comparison | prediction | explainer         │
└─────────────────────────────┬───────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
┌──────────────────────────┐    ┌──────────────────────────┐
│     PostgreSQL           │    │     RAG (pgvector)       │
│                          │    │                          │
│ • players                │    │ • article embeddings     │
│ • player_season_stats    │    │ • semantic search        │
│ • game_logs              │    │ • MiniLM-L6-v2           │
│ • injuries (ESPN)        │    │                          │
│ • salaries (scraped)     │    │                          │
│ • team_stats             │    │                          │
└────────────┬─────────────┘    └────────────┬─────────────┘
             │                               │
             └───────────────┬───────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────┐
│               Response Synthesis (Claude)                   │
│                                                             │
│  Combines SQL results + RAG context → cited response        │
└─────────────────────────────────────────────────────────────┘
```

## Prediction Model

The scoring predictions aren't just "this guy has the most points." It's a weighted ensemble:

| Factor | Weight | Description |
|--------|--------|-------------|
| Recent form | 30% | Last 5 games PPG, weighted recency |
| Season baseline | 25% | Full season averages |
| Head-to-head | 15% | Historical performance vs this opponent |
| Home/away splits | 10% | Some players just perform better at home |
| Goalie matchup | 10% | Opponent starter's Sv% vs league avg |
| Team pace | 10% | Combined goals-per-game of both teams |

Confidence scoring considers: games played (min 10 for "high"), form consistency, and H2H sample size.

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.11+
- Node.js 18+ (for frontend)
- Anthropic API key

### Setup

```bash
# Clone
git clone https://github.com/yourusername/powerplai.git
cd powerplai

# Environment
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
```

### Environment Variables

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...

# Database (defaults work with docker-compose)
DATABASE_URL=postgresql+asyncpg://powerplai:powerplai_dev@localhost:5432/powerplai

# Optional
CHROMA_HOST=localhost
CHROMA_PORT=8001
NHL_API_BASE=https://api-web.nhle.com/v1
```

### Run Everything

```bash
# Start backend + database
docker-compose up -d

# This spins up:
# - PostgreSQL 16 + pgvector on :5432 (mapped to 5433 externally)
# - FastAPI backend on :8000
# - ChromaDB on :8001

# Ingest initial data (takes a few minutes)
pip install -e .
python -m backend.scripts.ingest_data --season 2024

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
# → http://localhost:3000
```

### Try It

```bash
# Health check
curl http://localhost:8000/health

# Ask a question
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Who leads the league in expected goals?"}'

# Get tonight's predictions
curl http://localhost:8000/api/predictions/tonight

# Player lookup
curl "http://localhost:8000/api/players/connor%20mcdavid"
```

## API Reference

### Core Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/query` | POST | Main copilot - ask any question (rate limited: 20/min) |
| `/api/players/{name}` | GET | Player stats lookup |
| `/api/leaders/{stat}` | GET | League leaders (goals, assists, points, xg, corsi_for_pct) |
| `/api/predictions/tonight` | GET | Scoring predictions for today's games |
| `/api/predictions/matchup/{home}/{away}` | GET | Predictions for specific matchup |

### Data Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/games/today` | GET | Today's schedule |
| `/api/games/logs/{player}` | GET | Player's recent game logs |
| `/api/injuries` | GET | Active injuries (from ESPN) |
| `/api/salary/team/{team}` | GET | Team cap breakdown |
| `/api/stats/matchup/{home}/{away}` | GET | Matchup context (pace, goalie stats) |

### Admin Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/data/status` | GET | Ingestion status |
| `/api/updates/run` | POST | Trigger data refresh |
| `/health` | GET | Health check |

## Data Pipeline

Data comes from multiple sources, each with different update schedules:

| Source | What | Frequency |
|--------|------|-----------|
| NHL API | Rosters, schedules, game logs | Real-time (on startup) |
| NHL Stats API | Team/goalie advanced stats | Daily |
| MoneyPuck | xG, Corsi, Fenwick, WAR | Daily CSV drops |
| ESPN | Injury reports | Hourly during season |
| PuckPedia | Salary cap data | Weekly (scraped) |

The backend auto-updates on startup and can be triggered manually via `/api/updates/run`.

## Project Structure

```
powerplai/
├── backend/
│   ├── src/
│   │   ├── api/main.py          # FastAPI app, all endpoints
│   │   ├── agents/
│   │   │   ├── copilot.py       # Query classification + response synthesis
│   │   │   ├── predictions.py   # Scoring model
│   │   │   └── rag.py           # Embedding + semantic search
│   │   ├── db/
│   │   │   ├── models.py        # SQLAlchemy models
│   │   │   └── migrations.py    # Schema management
│   │   └── ingestion/           # Data pipelines
│   │       ├── nhl_api.py       # Official NHL data
│   │       ├── moneypuck.py     # Advanced stats CSVs
│   │       ├── espn_injuries.py # Injury scraping
│   │       ├── salary_cap.py    # PuckPedia scraping
│   │       └── games.py         # Schedule + game logs
│   ├── scripts/
│   │   └── ingest_data.py       # CLI for bulk ingestion
│   └── tests/
├── frontend/                     # Next.js 14 + TypeScript
│   ├── src/
│   │   ├── app/                 # App router pages
│   │   ├── components/          # Chat UI, stat cards
│   │   └── hooks/useChat.ts     # Query state management
│   └── package.json
├── docker/
│   ├── Dockerfile.backend
│   ├── Dockerfile.backend.prod
│   └── init.sql
├── docker-compose.yml            # Local dev stack
├── docker-compose.prod.yml       # Production config
├── pyproject.toml                # Python deps
└── railway.json                  # Railway deployment config
```

## Development

### Local without Docker

```bash
# You'll need Postgres with pgvector running locally
createdb powerplai

# Backend
pip install -e ".[dev]"
uvicorn backend.src.api.main:app --reload

# Frontend
cd frontend && npm run dev
```

### Testing

```bash
pytest backend/tests/ -v
```

### Linting

```bash
ruff check backend/
mypy backend/src/
```

## Deployment

See [DEPLOY.md](./DEPLOY.md) for detailed instructions on deploying to Railway (backend) + Vercel (frontend).

Quick version:
- Backend: Railway with `docker/Dockerfile.backend.prod`
- Database: Railway managed Postgres
- Frontend: Vercel with `frontend/` as root

## Roadmap

- [x] Core stats queries + Claude synthesis
- [x] Prediction model with goalie/pace adjustments
- [x] Chat UI with streaming responses
- [x] Auto-updating data pipelines
- [ ] Fantasy lineup optimizer
- [ ] Historical trend charts
- [ ] Betting edge detection (odds API integration)
- [ ] Multi-model evaluation framework

## Tech Stack

| Layer | Tech |
|-------|------|
| LLM | Claude (Anthropic) |
| Backend | Python 3.11, FastAPI, SQLAlchemy |
| Database | PostgreSQL 16 + pgvector |
| Embeddings | sentence-transformers (MiniLM-L6-v2) |
| Frontend | Next.js 14, TypeScript, Tailwind |
| Infra | Docker, Railway, Vercel |

## License

MIT
