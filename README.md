# PowerplAI

**NHL Analytics & Prediction Platform**

A full-stack hockey analytics platform that aggregates data from 5+ sources, generates probability-based scoring predictions using a multi-factor weighted model, and provides an LLM-powered copilot for natural language queries.

[![Demo](https://img.shields.io/badge/Demo-GitHub%20Pages-blue)](https://yourusername.github.io/powerplai)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Next.js](https://img.shields.io/badge/Next.js-14-black.svg)](https://nextjs.org/)

---

## Overview

PowerplAI combines structured hockey data with AI to answer questions like:
- "Who's most likely to score tonight?"
- "Compare McDavid vs MacKinnon"
- "What is expected goals?"
- "Best value picks for fantasy?"

**Key Features:**
- Multi-factor probability model for scoring predictions
- RAG-powered explanations of hockey analytics concepts
- Real-time data from NHL API, MoneyPuck, ESPN
- Model evaluation with calibration analysis
- Config-driven data pipelines with validation

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Frontend (Next.js 14)                          │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐    │
│  │ Chat UI      │ │ Predictions  │ │ Charts       │ │ Leaderboards │    │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘    │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │ REST API
┌───────────────────────────────────▼─────────────────────────────────────┐
│                          Backend (FastAPI)                               │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                    Query Copilot (Claude)                         │   │
│  │  • 13+ query types with classification                            │   │
│  │  • Routes to SQL, RAG, or prediction engine                       │   │
│  │  • Synthesizes responses with citations                           │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  ┌─────────────────┐ ┌─────────────────┐ ┌──────────────────────────┐   │
│  │ Prediction      │ │ RAG Service     │ │ Data Pipelines           │   │
│  │ Engine          │ │                 │ │                          │   │
│  │                 │ │ • Semantic      │ │ • Orchestrated (cron)    │   │
│  │ • 6-factor      │ │   search        │ │ • Incremental loading    │   │
│  │   weighted      │ │ • Hybrid        │ │ • Validation checks      │   │
│  │   model         │ │   retrieval     │ │ • Progress tracking      │   │
│  │ • Poisson       │ │ • Re-ranking    │ │                          │   │
│  │   probability   │ │ • Citations     │ │ Sources:                 │   │
│  │ • Calibration   │ │                 │ │ • NHL API                │   │
│  │   tracking      │ │                 │ │ • MoneyPuck              │   │
│  │                 │ │                 │ │ • ESPN                   │   │
│  │                 │ │                 │ │ • PuckPedia              │   │
│  └────────┬────────┘ └────────┬────────┘ └─────────────┬────────────┘   │
│           │                   │                         │                │
└───────────┼───────────────────┼─────────────────────────┼────────────────┘
            │                   │                         │
            ▼                   ▼                         ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                    PostgreSQL 16 + pgvector                               │
│                                                                           │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────────────┐ │
│  │ players     │ │ game_logs   │ │ predictions │ │ documents           │ │
│  │ teams       │ │ games       │ │ audit_trail │ │ (embeddings)        │ │
│  │ season_stats│ │ injuries    │ │             │ │                     │ │
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## Prediction Model

The prediction engine uses a **multi-factor weighted probability model** (not an ensemble of ML models). It combines domain-knowledge-weighted features and converts expected values to probabilities using a Poisson distribution.

### Factor Weights

| Factor | Weight | Description |
|--------|--------|-------------|
| Recent Form | 30% | Last 5 games performance (most predictive for streaks) |
| Season Baseline | 25% | Full season average (stability) |
| H2H History | 15% | Career performance vs specific opponent |
| Home/Away | 10% | Location-based adjustments |
| Goalie Matchup | 10% | Opponent goalie save % vs league average |
| Team Pace | 10% | Combined goals-per-game environment |

### Probability Conversion

```
P(≥1 goal) = 1 - e^(-λ)   where λ = expected goals
```

This Poisson-based approach is appropriate for rare events (goals) with a known average rate.

### Model Evaluation

The system includes comprehensive evaluation:
- **Brier Score**: Measures calibration quality
- **Calibration Buckets**: Compares predicted vs actual rates
- **Baseline Comparison**: Validates model adds value over naive prediction

Access evaluation at: `GET /api/model/evaluation`

---

## Data Pipeline

### Sources

| Source | Data | Frequency |
|--------|------|-----------|
| NHL API | Rosters, schedules, game logs | Real-time |
| NHL Stats API | Team/goalie advanced stats | Daily |
| MoneyPuck | xG, Corsi, Fenwick, WAR | Daily |
| ESPN | Injury reports | Hourly |
| PuckPedia | Salary cap data | Weekly |

### Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Pipeline Orchestrator                         │
│                                                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐   │
│  │ Schedule │→│ Game     │→│ Player   │→│ Advanced Stats   │   │
│  │ Sync     │ │ Logs     │ │ Stats    │ │ (MoneyPuck)      │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────────┘   │
│       │                                                          │
│       │  ┌──────────┐ ┌──────────┐ ┌──────────────────────────┐ │
│       └→ │ Injuries │ │ Salary   │ │ Validation & Quality    │ │
│          │ (ESPN)   │ │ Cap      │ │ Checks                  │ │
│          └──────────┘ └──────────┘ └──────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

Features:
- **Dependency-aware execution**: Pipelines run in correct order
- **Incremental loading**: Only fetch new/changed data
- **Retry with backoff**: Handles API rate limits gracefully
- **Data validation**: Anomaly detection, completeness checks

---

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.11+ (for local development)
- Node.js 18+ (for frontend)
- Anthropic API key

### 1. Clone & Configure

```bash
git clone https://github.com/yourusername/powerplai.git
cd powerplai
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
```

### 2. Start Services

```bash
# Start everything with Docker
docker-compose up -d

# Services:
# - PostgreSQL + pgvector: localhost:5433
# - FastAPI backend: localhost:8000
# - Next.js frontend: localhost:3001
```

### 3. Initial Data Load

```bash
# Install Python package
pip install -e .

# Ingest current season data
python -m backend.scripts.ingest_data --season 2025
```

### 4. Try It

```bash
# Health check
curl http://localhost:8000/health

# Ask a question
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Who leads the league in expected goals?"}'

# Get tonight's predictions
curl http://localhost:8000/api/predictions/tonight
```

---

## API Reference

### Core Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/query` | POST | Main copilot - ask any question |
| `/api/players/{name}` | GET | Player stats lookup |
| `/api/leaders/{stat}` | GET | League leaders |
| `/api/predictions/tonight` | GET | Tonight's scoring predictions |
| `/api/predictions/matchup/{home}/{away}` | GET | Specific matchup prediction |

### Model & Evaluation

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/model/info` | GET | Model architecture details |
| `/api/model/evaluation` | GET | Evaluation metrics (Brier, calibration) |
| `/api/model/backtest` | GET | Rolling window backtest |
| `/api/audit/calibration-chart` | GET | Calibration visualization data |

### Pipeline & Data

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/pipeline/status` | GET | Pipeline orchestrator status |
| `/api/pipeline/run/{name}` | POST | Trigger specific pipeline |
| `/api/validation/run` | GET | Run data validation checks |
| `/api/validation/stats` | GET | Data statistics |

---

## Data Dictionary

### Core Tables

| Table | Description | Key Fields |
|-------|-------------|------------|
| `players` | Player registry | `nhl_id`, `name`, `position`, `team_abbrev` |
| `player_season_stats` | Season aggregates | `player_id`, `season`, `goals`, `assists`, `xg`, `corsi_for_pct` |
| `game_logs` | Per-game performance | `player_id`, `game_date`, `goals`, `assists`, `shots`, `toi` |
| `games` | Schedule & results | `nhl_game_id`, `home_team_abbrev`, `away_team_abbrev`, `game_state` |
| `goalie_stats` | Goalie metrics | `player_id`, `save_pct`, `gaa`, `wins` |
| `injuries` | Active injuries | `player_id`, `status`, `injury_type`, `expected_return` |
| `documents` | RAG knowledge base | `content`, `embedding` (vector[384]), `source`, `url` |
| `prediction_audit` | Prediction validation | `player_id`, `game_date`, `prob_goal`, `actual_goals` |

### Key Metrics

| Metric | Description | Source |
|--------|-------------|--------|
| `xG` (Expected Goals) | Goal probability based on shot quality | MoneyPuck |
| `Corsi For %` | Shot attempt differential (for/total) | MoneyPuck |
| `Fenwick For %` | Like Corsi, excluding blocked shots | MoneyPuck |
| `WAR` | Wins Above Replacement | MoneyPuck |
| `PDO` | Shooting % + Save % (luck indicator) | Calculated |

---

## Project Structure

```
powerplai/
├── backend/
│   ├── src/
│   │   ├── api/main.py           # FastAPI app (100+ endpoints)
│   │   ├── agents/
│   │   │   ├── copilot.py        # Query classification & synthesis
│   │   │   ├── predictions.py    # Multi-factor scoring model
│   │   │   ├── rag.py            # Semantic search with hybrid retrieval
│   │   │   └── model_evaluation.py # Metrics & backtesting
│   │   ├── pipeline/
│   │   │   ├── config.py         # Declarative pipeline definitions
│   │   │   ├── orchestrator.py   # Scheduling & execution
│   │   │   ├── validation.py     # Data quality checks
│   │   │   └── incremental.py    # Delta loading utilities
│   │   ├── db/
│   │   │   ├── models.py         # SQLAlchemy ORM
│   │   │   └── migrations.py     # Schema management
│   │   └── ingestion/            # Data source integrations
│   └── tests/
├── frontend/
│   ├── src/
│   │   ├── app/                  # Next.js app router
│   │   ├── components/
│   │   │   ├── chat/             # Chat UI components
│   │   │   ├── charts/           # Data visualizations
│   │   │   └── ui/               # Base components
│   │   ├── hooks/useChat.ts      # Chat state management
│   │   └── lib/
│   │       ├── api.ts            # API client
│   │       └── demoData.ts       # Demo mode data
│   └── package.json
├── docker/
│   ├── init.sql                  # Database schema
│   ├── Dockerfile.backend
│   └── Dockerfile.backend.prod
├── .github/workflows/
│   └── deploy-pages.yml          # GitHub Pages deployment
├── docker-compose.yml            # Development stack
├── docker-compose.prod.yml       # Production stack
├── pyproject.toml                # Python dependencies
├── AUDIT.md                      # Technical audit findings
└── DATA_DICTIONARY.md            # Schema documentation
```

---

## Development

### Local Setup (without Docker)

```bash
# Database (needs PostgreSQL with pgvector)
createdb powerplai

# Backend
pip install -e ".[dev]"
uvicorn backend.src.api.main:app --reload

# Frontend
cd frontend && npm install && npm run dev
```

### Testing

```bash
# Backend tests
pytest backend/tests/ -v

# Linting
ruff check backend/
mypy backend/src/
```

### Running Pipelines Manually

```bash
# Via API
curl -X POST http://localhost:8000/api/pipeline/run/schedule_sync
curl -X POST http://localhost:8000/api/pipeline/run/game_logs
curl -X POST http://localhost:8000/api/pipeline/run/injuries

# Check status
curl http://localhost:8000/api/pipeline/status
```

---

## Deployment

### GitHub Pages (Frontend Demo)

The frontend automatically deploys to GitHub Pages on push to `main`. The demo mode uses cached sample data.

### Full Stack (Railway + Vercel)

See [DEPLOY.md](./DEPLOY.md) for detailed instructions.

**Quick version:**
1. **Backend**: Railway with `docker/Dockerfile.backend.prod`
2. **Database**: Railway managed PostgreSQL
3. **Frontend**: Vercel or Railway

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| LLM | Claude 3.5 Sonnet (Anthropic) |
| Backend | Python 3.11, FastAPI, SQLAlchemy |
| Database | PostgreSQL 16 + pgvector |
| Embeddings | sentence-transformers (MiniLM-L6-v2) |
| Frontend | Next.js 14, TypeScript, Tailwind CSS |
| Charts | Recharts |
| Pipeline | APScheduler |
| Infrastructure | Docker, Railway, Vercel |

---

## Screenshots

*Add screenshots of the chat interface, prediction cards, and calibration charts here.*

---

## License

MIT

---

## Acknowledgments

- Data sources: NHL API, MoneyPuck, ESPN, PuckPedia
- Built with Claude (Anthropic) for LLM capabilities
