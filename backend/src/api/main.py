"""
PowerplAI API - FastAPI application.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, date, timedelta
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

# Rate limiting
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from backend.src.config import get_settings
from backend.src.db.database import get_db, engine, async_session_maker
from backend.src.agents.copilot import copilot
from backend.src.agents.rag import rag_service
from backend.src.ingestion.scheduler import (
    get_current_season,
    get_pending_seasons,
    load_progress,
)

logger = structlog.get_logger()
settings = get_settings()

# Rate limiter - uses IP address for identification
limiter = Limiter(key_func=get_remote_address)

# Track if auto-update is running
_auto_update_running = False
_startup_update_results = None


async def run_startup_updates():
    """
    Run comprehensive startup updates including:
    - Today's schedule
    - Game log catch-up (covers any missed days)
    - Injury updates
    - Team/goalie stats
    """
    global _auto_update_running, _startup_update_results

    if _auto_update_running:
        logger.info("startup_updates_already_running")
        return

    _auto_update_running = True

    try:
        from backend.src.ingestion.startup_updates import run_startup_updates as do_startup_updates
        _startup_update_results = await do_startup_updates()
        logger.info("startup_updates_finished", results=_startup_update_results)
    except Exception as e:
        logger.error("startup_updates_failed", error=str(e))
        _startup_update_results = {"error": str(e)}
    finally:
        _auto_update_running = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("starting_powerplai_api")

    # Run database migrations (ensure tables exist)
    from backend.src.db.migrations import run_migrations
    await run_migrations()

    # Run startup updates in background (don't block startup)
    # This includes: schedule refresh, game log catch-up, injuries, team/goalie stats
    if settings.auto_update_enabled:
        task = asyncio.create_task(run_startup_updates())
        # Add callback to log any unhandled exceptions
        def handle_task_exception(t):
            if t.exception() is not None:
                logger.error("startup_updates_task_failed", error=str(t.exception()))
        task.add_done_callback(handle_task_exception)

        # Start background scheduler for ongoing updates (hourly injuries, daily stats)
        try:
            from backend.src.pipeline.orchestrator import start_scheduler
            await start_scheduler()
            logger.info("background_scheduler_started")
        except ImportError:
            logger.warning("apscheduler_not_available", message="Install apscheduler for scheduled updates")
        except Exception as e:
            logger.warning("scheduler_start_failed", error=str(e))

    yield

    # Cleanup
    logger.info("shutting_down_powerplai_api")
    try:
        from backend.src.pipeline.orchestrator import stop_scheduler
        await stop_scheduler()
    except Exception:
        pass
    await engine.dispose()


app = FastAPI(
    title="PowerplAI",
    description="Hockey Analytics & Fantasy Copilot API",
    version="0.1.0",
    lifespan=lifespan,
    redirect_slashes=False,
)

# Add rate limiter to app state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS for frontend - configurable via CORS_ORIGINS env var
import os
cors_origins_env = os.getenv("CORS_ORIGINS", "")
cors_origins = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
    # GitHub Pages (any user/org)
    "https://griffinmacnaughtan.github.io",
]
# Add production origins from environment
if cors_origins_env:
    cors_origins.extend([o.strip() for o in cors_origins_env.split(",") if o.strip()])
# In debug mode, allow all origins for easier development
if settings.debug:
    cors_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------------------------------------------------------------------------
# Request/Response Models
# -------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class QueryRequest(BaseModel):
    query: str
    include_rag: bool = True
    messages: list[ChatMessage] = []  # Conversation history for context


class QueryResponse(BaseModel):
    response: str
    sources: list[dict]
    query_type: str


class PlayerStatsResponse(BaseModel):
    name: str
    position: str | None
    team: str | None
    games_played: int | None
    goals: int | None
    assists: int | None
    points: int | None
    xg: float | None


class DocumentRequest(BaseModel):
    content: str
    title: str | None = None
    source: str | None = None
    url: str | None = None


# -------------------------------------------------------------------------
# Endpoints
# -------------------------------------------------------------------------


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "powerplai"}


@app.get("/api/ping")
async def ping():
    """Simple ping endpoint for testing connectivity."""
    return {"pong": True, "timestamp": datetime.now().isoformat()}


@app.post("/api/query", response_model=QueryResponse)
@limiter.limit("20/minute")  # 20 queries per minute per IP (protects Anthropic API costs)
async def query_copilot(
    request: Request,
    query_request: QueryRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Main copilot endpoint - ask any hockey analytics question.

    Rate limited to 20 requests per minute per IP address.

    Examples:
    - "How many goals does Cale Makar have this season?"
    - "Compare McDavid vs Draisaitl"
    - "What is expected goals?"
    - "Who are the most underrated defensemen?"
    """
    try:
        # Convert chat messages to dict format for copilot
        history = [{"role": m.role, "content": m.content} for m in query_request.messages]
        result = await copilot.query(
            query_request.query,
            db,
            include_rag=query_request.include_rag,
            conversation_history=history,
        )
        return QueryResponse(**result)
    except Exception as e:
        logger.error("copilot_query_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/players/{player_name}", response_model=list[PlayerStatsResponse])
async def get_player_stats(
    player_name: str,
    season: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Get stats for a specific player."""
    query = """
        SELECT
            p.name, p.position, p.team_abbrev,
            s.games_played, s.goals, s.assists, s.points, s.xg
        FROM players p
        LEFT JOIN player_season_stats s ON p.id = s.player_id
        WHERE p.name ILIKE :name
    """
    params = {"name": f"%{player_name}%"}

    if season:
        query += " AND s.season = :season"
        params["season"] = season

    query += " ORDER BY s.season DESC LIMIT 5"

    result = await db.execute(text(query), params)
    rows = result.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found")

    return [
        PlayerStatsResponse(
            name=row.name,
            position=row.position,
            team=row.team_abbrev,
            games_played=row.games_played,
            goals=row.goals,
            assists=row.assists,
            points=row.points,
            xg=float(row.xg) if row.xg else None,
        )
        for row in rows
    ]


@app.get("/api/leaders/{stat}")
async def get_stat_leaders(
    stat: str,
    season: str = "20232024",
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """
    Get league leaders for a stat.

    Valid stats: goals, assists, points, xg, corsi_for_pct
    """
    valid_stats = ["goals", "assists", "points", "xg", "corsi_for_pct"]
    if stat not in valid_stats:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid stat. Valid options: {valid_stats}"
        )

    result = await db.execute(
        text(f"""
            SELECT p.name, p.team_abbrev, s.{stat}, s.games_played
            FROM players p
            JOIN player_season_stats s ON p.id = s.player_id
            WHERE s.season = :season AND s.{stat} IS NOT NULL
            ORDER BY s.{stat} DESC
            LIMIT :limit
        """),
        {"season": season, "limit": limit},
    )

    rows = result.fetchall()
    return [
        {
            "rank": i + 1,
            "name": row.name,
            "team": row.team_abbrev,
            stat: row[2],
            "games_played": row.games_played,
        }
        for i, row in enumerate(rows)
    ]


@app.post("/api/documents")
async def add_document(
    request: DocumentRequest,
    db: AsyncSession = Depends(get_db),
):
    """Add a document to the RAG knowledge base."""
    doc_id = await rag_service.add_document(
        db,
        content=request.content,
        title=request.title,
        source=request.source,
        url=request.url,
    )
    return {"id": doc_id, "status": "indexed"}


@app.get("/api/search")
async def search_documents(
    q: str,
    limit: int = 5,
    db: AsyncSession = Depends(get_db),
):
    """Search the RAG knowledge base."""
    results = await rag_service.search(db, q, limit=limit)
    return {"query": q, "results": results}


# -------------------------------------------------------------------------
# Data management endpoints
# -------------------------------------------------------------------------


@app.get("/api/data/status")
async def get_data_status(db: AsyncSession = Depends(get_db)):
    """Get ingestion status and available seasons."""
    progress = load_progress()
    pending = get_pending_seasons()

    # Get season counts from database
    result = await db.execute(text("""
        SELECT season, COUNT(*) as player_count
        FROM player_season_stats
        GROUP BY season
        ORDER BY season DESC
    """))
    seasons_in_db = {row[0]: row[1] for row in result.fetchall()}

    return {
        "current_season": get_current_season(),
        "completed_seasons": progress.get("completed_seasons", []),
        "pending_seasons": pending,
        "last_update": progress.get("last_update"),
        "current_season_last_update": progress.get("current_season_last_update"),
        "seasons_in_database": seasons_in_db,
        "auto_update_enabled": settings.auto_update_enabled,
    }


@app.post("/api/data/update")
async def trigger_update(background_tasks: BackgroundTasks):
    """Trigger a manual update of current season data."""
    if _auto_update_running:
        return {"status": "already_running", "message": "Update already in progress"}

    background_tasks.add_task(run_startup_update)
    return {"status": "started", "message": "Update started in background"}


@app.get("/api/seasons")
async def get_available_seasons(db: AsyncSession = Depends(get_db)):
    """Get list of seasons with data available."""
    result = await db.execute(text("""
        SELECT DISTINCT season
        FROM player_season_stats
        ORDER BY season DESC
    """))
    seasons = [row[0] for row in result.fetchall()]
    return {"seasons": seasons}


# -------------------------------------------------------------------------
# Pipeline & Validation endpoints
# -------------------------------------------------------------------------


@app.get("/api/pipeline/status")
async def get_pipeline_status():
    """Get current pipeline orchestrator status."""
    from backend.src.pipeline.orchestrator import orchestrator, get_scheduler_status
    return {
        "orchestrator": orchestrator.get_status(),
        "scheduler": get_scheduler_status(),
    }


@app.post("/api/pipeline/run/{pipeline_name}")
@limiter.limit("5/minute")
async def run_single_pipeline(
    request: Request,
    pipeline_name: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Trigger a single pipeline to run."""
    from backend.src.pipeline.orchestrator import orchestrator, register_all_pipelines

    # Ensure pipelines are registered
    if not orchestrator.pipelines:
        register_all_pipelines()

    if pipeline_name not in orchestrator.pipelines:
        raise HTTPException(
            status_code=400,
            detail=f"Pipeline '{pipeline_name}' not found. Available: {list(orchestrator.pipelines.keys())}",
        )

    result = await orchestrator.run_pipeline(pipeline_name, db)
    return result.to_dict()


@app.post("/api/pipeline/run-all")
@limiter.limit("2/minute")
async def run_all_pipelines(
    request: Request,
    background_tasks: BackgroundTasks,
    frequency: str | None = None,
):
    """Run all pipelines (optionally filtered by frequency)."""
    from backend.src.pipeline.orchestrator import orchestrator, register_all_pipelines
    from backend.src.pipeline.config import UpdateFrequency

    if not orchestrator.pipelines:
        register_all_pipelines()

    freq = None
    if frequency:
        try:
            freq = UpdateFrequency(frequency)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid frequency: {frequency}. Valid: {[f.value for f in UpdateFrequency]}",
            )

    result = await orchestrator.run_all(frequency=freq, validate=True)
    return result


@app.get("/api/validation/run")
async def run_validation(db: AsyncSession = Depends(get_db)):
    """Run all data validation checks."""
    from backend.src.pipeline.validation import run_all_validations
    return await run_all_validations(db)


@app.get("/api/validation/stats")
async def get_data_stats(db: AsyncSession = Depends(get_db)):
    """Get current data statistics."""
    from backend.src.pipeline.validation import DatabaseValidator
    validator = DatabaseValidator(db)
    return await validator.get_data_stats()


@app.get("/api/validation/missing")
async def get_missing_data(
    db: AsyncSession = Depends(get_db),
    season: str | None = None,
):
    """Identify missing data for a season."""
    from backend.src.pipeline.incremental import IncrementalLoader
    loader = IncrementalLoader(db)
    target_season = season or get_current_season()
    return await loader.identify_missing_data(target_season)


@app.get("/api/pipeline/progress")
async def get_pipeline_progress():
    """Get incremental loading progress for all pipelines."""
    from backend.src.pipeline.incremental import progress_tracker
    return {
        name: state.to_dict()
        for name, state in progress_tracker.get_all_states().items()
    }


# -------------------------------------------------------------------------
# Game and Schedule endpoints
# -------------------------------------------------------------------------


@app.get("/api/games/today")
async def get_todays_games(db: AsyncSession = Depends(get_db)):
    """Get today's scheduled games."""
    from backend.src.ingestion.games import get_todays_games as fetch_games
    games = await fetch_games(db)
    return {"date": date.today().isoformat(), "games": games}


@app.post("/api/games/refresh")
@limiter.limit("5/minute")  # Limit refresh calls to prevent API abuse
async def refresh_games(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """Refresh today's game schedule from NHL API. Rate limited to 5/minute."""
    from backend.src.ingestion.games import refresh_todays_schedule
    games_count = await refresh_todays_schedule(db)
    return {"status": "success", "games_refreshed": games_count}


class GameLogIngestionRequest(BaseModel):
    season: str | None = None  # e.g., "20252026"
    team_abbrev: str | None = None
    limit: int | None = None


@app.post("/api/games/ingest-logs")
@limiter.limit("2/minute")  # Heavy operation - strict limit
async def ingest_game_logs(
    request: Request,
    ingest_request: GameLogIngestionRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Ingest player game logs for the current or specified season.
    This can take a while for all players - runs in background.
    Rate limited to 2/minute.
    """
    from backend.src.ingestion.games import ingest_all_player_game_logs
    from backend.src.ingestion.scheduler import get_current_season

    season = ingest_request.season or f"{get_current_season()}{int(get_current_season()) + 1}"

    async def run_ingestion():
        async with async_session_maker() as session:
            await ingest_all_player_game_logs(
                session, season,
                team_abbrev=ingest_request.team_abbrev,
                limit=ingest_request.limit
            )

    background_tasks.add_task(run_ingestion)
    return {
        "status": "started",
        "message": f"Game log ingestion started for season {season}",
        "team_filter": ingest_request.team_abbrev,
        "player_limit": ingest_request.limit
    }


# -------------------------------------------------------------------------
# Team and Goalie Stats endpoints (for enhanced predictions)
# -------------------------------------------------------------------------


class TeamGoalieStatsRequest(BaseModel):
    season: str | None = None  # e.g., "20252026"


@app.post("/api/stats/ingest-team-goalie")
async def ingest_team_goalie_stats(
    request: TeamGoalieStatsRequest,
    background_tasks: BackgroundTasks,
):
    """
    Ingest team and goalie statistics from NHL Stats API.
    This includes goalie save %, GAA, and team pace metrics.
    """
    from backend.src.ingestion.team_goalie_stats import refresh_all_stats
    from backend.src.ingestion.scheduler import get_current_season

    season = request.season or f"{get_current_season()}{int(get_current_season()) + 1}"

    async def run_ingestion():
        result = await refresh_all_stats(season)
        logger.info("team_goalie_stats_ingested", **result)

    background_tasks.add_task(run_ingestion)
    return {
        "status": "started",
        "message": f"Team and goalie stats ingestion started for season {season}",
    }


@app.get("/api/stats/team/{team_abbrev}")
async def get_team_stats(
    team_abbrev: str,
    season: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Get team statistics including pace metrics."""
    from backend.src.ingestion.team_goalie_stats import get_team_pace
    from backend.src.ingestion.scheduler import get_current_season

    season = season or f"{get_current_season()}{int(get_current_season()) + 1}"
    pace = await get_team_pace(db, team_abbrev.upper(), season)

    if not pace:
        raise HTTPException(status_code=404, detail=f"No stats found for team {team_abbrev}")

    return {
        "team": team_abbrev.upper(),
        "season": season,
        **pace
    }


@app.get("/api/stats/goalie/{team_abbrev}")
async def get_team_goalie_stats(
    team_abbrev: str,
    season: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Get starting goalie stats for a team."""
    from backend.src.ingestion.team_goalie_stats import get_goalie_stats
    from backend.src.ingestion.scheduler import get_current_season

    season = season or f"{get_current_season()}{int(get_current_season()) + 1}"
    goalie = await get_goalie_stats(db, team_abbrev.upper(), season)

    if not goalie:
        raise HTTPException(status_code=404, detail=f"No goalie stats found for team {team_abbrev}")

    return {
        "team": team_abbrev.upper(),
        "season": season,
        **goalie
    }


@app.get("/api/stats/matchup/{home_team}/{away_team}")
async def get_matchup_context(
    home_team: str,
    away_team: str,
    season: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Get full matchup context including team pace and goalie matchups.
    This powers the enhanced prediction model.
    """
    from backend.src.ingestion.team_goalie_stats import get_matchup_context as fetch_context
    from backend.src.ingestion.scheduler import get_current_season

    season = season or f"{get_current_season()}{int(get_current_season()) + 1}"
    context = await fetch_context(db, home_team.upper(), away_team.upper(), season)

    return context


# -------------------------------------------------------------------------
# Injury endpoints (powered by ESPN API)
# -------------------------------------------------------------------------


@app.get("/api/injuries")
async def get_injuries(
    team: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Get all active injuries from ESPN, optionally filtered by team."""
    from backend.src.ingestion.espn_injuries import get_all_injuries, get_injuries_by_team

    if team:
        injuries = await get_injuries_by_team(db, team.upper())
        return {"team": team.upper(), "injuries": injuries, "count": len(injuries)}
    else:
        return await get_all_injuries(db)


@app.get("/api/injuries/team/{team_abbrev}")
async def get_team_injuries(
    team_abbrev: str,
    db: AsyncSession = Depends(get_db),
):
    """Get injuries for a specific team."""
    from backend.src.ingestion.espn_injuries import get_injuries_by_team

    injuries = await get_injuries_by_team(db, team_abbrev.upper())
    return {"team": team_abbrev.upper(), "injuries": injuries, "count": len(injuries)}


@app.post("/api/injuries/refresh")
@limiter.limit("5/minute")
async def refresh_injuries(request: Request, background_tasks: BackgroundTasks):
    """Trigger a refresh of injury data from ESPN. Rate limited to 5/minute."""
    from backend.src.ingestion.espn_injuries import refresh_espn_injuries

    async def run_refresh():
        result = await refresh_espn_injuries()
        logger.info("espn_injuries_refreshed", **result)

    background_tasks.add_task(run_refresh)
    return {"status": "started", "message": "ESPN injury refresh started in background"}


# -------------------------------------------------------------------------
# Salary Cap endpoints
# -------------------------------------------------------------------------


@app.get("/api/salary/team/{team_abbrev}")
async def get_team_salary_cap(
    team_abbrev: str,
    db: AsyncSession = Depends(get_db),
):
    """Get salary cap breakdown for a team."""
    from backend.src.ingestion.salary_cap import get_team_cap_summary

    return await get_team_cap_summary(db, team_abbrev.upper())


@app.get("/api/salary/best-value")
async def get_best_value_contracts(
    min_points: int = 30,
    db: AsyncSession = Depends(get_db),
):
    """Get players with best value contracts (points per cap dollar)."""
    from backend.src.ingestion.salary_cap import get_best_value_players

    players = await get_best_value_players(db, min_points)
    return {"min_points_threshold": min_points, "players": players}


@app.post("/api/salary/refresh")
@limiter.limit("2/hour")  # Very heavy operation - strict limit
async def refresh_salary_data(
    request: Request,
    background_tasks: BackgroundTasks,
    source: str = "auto",
):
    """
    Trigger a refresh of salary cap data from web sources.

    Args:
        source: Data source - "puckpedia", "spotrac", or "auto" (tries both, spotrac as fallback)

    Note: This scrapes web data and takes several minutes.
    Rate limited to 2/hour.
    """
    from backend.src.ingestion.salary_cap import ingest_all_salaries

    if source not in ("puckpedia", "spotrac", "auto"):
        raise HTTPException(status_code=400, detail="source must be 'puckpedia', 'spotrac', or 'auto'")

    async def run_refresh():
        async with async_session_maker() as db:
            result = await ingest_all_salaries(db, source=source)
            logger.info("salary_data_refreshed", source=source, **result)

    background_tasks.add_task(run_refresh)
    return {
        "status": "started",
        "source": source,
        "message": f"Salary data refresh started using {source} source (this takes several minutes)"
    }


@app.get("/api/salary/export")
async def export_salary_csv(db: AsyncSession = Depends(get_db)):
    """
    Export all player salary data as CSV.
    Returns CSV content that can be saved or processed.
    """
    from backend.src.ingestion.salary_cap import export_salaries_to_csv
    from fastapi.responses import Response

    csv_content = await export_salaries_to_csv(db)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=salaries.csv"}
    )


@app.post("/api/salary/import-csv")
async def import_salary_csv(db: AsyncSession = Depends(get_db)):
    """
    Import salary data from the default data/salaries_2025_26.csv file.
    This is more reliable than web scraping.
    """
    from backend.src.ingestion.salary_cap import load_salaries_from_csv
    from pathlib import Path

    # Path from backend/src/api/main.py -> project root/data/
    csv_path = Path(__file__).parent.parent.parent.parent / "data" / "salaries_2025_26.csv"

    if not csv_path.exists():
        raise HTTPException(status_code=404, detail=f"CSV file not found: {csv_path}")

    result = await load_salaries_from_csv(db, str(csv_path))
    return {
        "status": "success",
        "rows_processed": result["rows_processed"],
        "matched": result["matched"],
        "updated": result["updated"],
        "not_found_count": len(result.get("not_found", []))
    }


@app.get("/api/salary/scrape-test/{team_abbrev}")
async def test_salary_scrape(team_abbrev: str):
    """
    Test scraping salary data for a single team.
    Useful for debugging if the scrapers are working.
    """
    from backend.src.ingestion.salary_cap import fetch_team_cap_data, fetch_team_cap_data_spotrac

    team = team_abbrev.upper()

    puckpedia_result = await fetch_team_cap_data(team)
    spotrac_result = await fetch_team_cap_data_spotrac(team)

    return {
        "team": team,
        "puckpedia": {
            "count": len(puckpedia_result),
            "sample": puckpedia_result[:3] if puckpedia_result else []
        },
        "spotrac": {
            "count": len(spotrac_result),
            "sample": spotrac_result[:3] if spotrac_result else []
        }
    }


# -------------------------------------------------------------------------
# Startup/Update Status endpoints
# -------------------------------------------------------------------------


@app.get("/api/updates/status")
async def get_update_status():
    """Get status of startup updates and last update times."""
    progress = load_progress()

    return {
        "is_running": _auto_update_running,
        "last_results": _startup_update_results,
        "last_game_log_date": progress.get("last_game_log_date"),
        "last_injury_update": progress.get("last_injury_update"),
        "last_team_stats_update": progress.get("last_team_stats_update"),
        "current_season_last_update": progress.get("current_season_last_update"),
    }


@app.post("/api/updates/run")
async def trigger_updates(background_tasks: BackgroundTasks):
    """Manually trigger startup updates."""
    if _auto_update_running:
        return {"status": "already_running", "message": "Updates already in progress"}

    background_tasks.add_task(run_startup_updates)
    return {"status": "started", "message": "Updates started in background"}


@app.post("/api/updates/daily")
async def trigger_daily_updates(background_tasks: BackgroundTasks):
    """Trigger full daily updates (more aggressive than startup)."""
    from backend.src.ingestion.startup_updates import run_daily_updates

    if _auto_update_running:
        return {"status": "already_running", "message": "Updates already in progress"}

    async def run_daily():
        global _auto_update_running, _startup_update_results
        _auto_update_running = True
        try:
            _startup_update_results = await run_daily_updates()
        finally:
            _auto_update_running = False

    background_tasks.add_task(run_daily)
    return {"status": "started", "message": "Daily updates started in background"}


@app.post("/api/rosters/sync")
@limiter.limit("2/hour")
async def sync_rosters(request: Request, background_tasks: BackgroundTasks):
    """
    Sync team rosters from NHL API.

    Updates player team assignments to reflect trades and roster moves.
    Useful before making predictions to ensure current rosters.
    """
    from backend.src.ingestion.roster_sync import sync_team_rosters
    from backend.src.ingestion.scheduler import get_current_season

    season = f"{get_current_season()}{int(get_current_season()) + 1}"

    async def run_sync():
        async with async_session_maker() as db:
            await sync_team_rosters(db, season)

    background_tasks.add_task(run_sync)
    return {"status": "started", "message": "Roster sync started in background"}


@app.post("/api/rosters/sync/{team_abbrev}")
@limiter.limit("10/hour")
async def sync_single_team_roster(
    team_abbrev: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Sync roster for a single team (faster than full sync).

    Useful before a specific game prediction.
    """
    from backend.src.ingestion.roster_sync import sync_single_team_roster

    stats = await sync_single_team_roster(db, team_abbrev.upper())
    return {"status": "complete", "team": team_abbrev.upper(), **stats}


@app.post("/api/stats/moneypuck/refresh")
@limiter.limit("2/hour")
async def refresh_moneypuck_stats(request: Request, background_tasks: BackgroundTasks):
    """
    Refresh MoneyPuck advanced stats (xG, Corsi, etc.) for current season.

    MoneyPuck updates their data regularly during the season.
    Use this to get the latest advanced metrics.
    """
    from backend.src.ingestion.startup_updates import update_moneypuck_stats
    from backend.src.ingestion.scheduler import get_current_season

    season_year = get_current_season()

    async def run_refresh():
        async with async_session_maker() as db:
            await update_moneypuck_stats(db, season_year)

    background_tasks.add_task(run_refresh)
    return {
        "status": "started",
        "message": f"MoneyPuck stats refresh started for {season_year}-{int(season_year)+1} season"
    }


@app.get("/api/games/logs/{player_name}")
async def get_player_game_logs(
    player_name: str,
    season: str | None = None,
    limit: int = 10,
    db: AsyncSession = Depends(get_db)
):
    """Get recent game logs for a player."""
    from backend.src.ingestion.scheduler import get_current_season

    season_filter = ""
    params = {"name": f"%{player_name}%", "limit": limit}

    if season:
        season_filter = "AND gl.season = :season"
        params["season"] = season

    result = await db.execute(
        text(f"""
            SELECT
                p.name, gl.game_date, gl.opponent, gl.home_away,
                gl.goals, gl.assists, gl.points, gl.shots, gl.toi,
                gl.powerplay_goals, gl.powerplay_points, gl.plus_minus
            FROM game_logs gl
            JOIN players p ON gl.player_id = p.id
            WHERE p.name ILIKE :name {season_filter}
            ORDER BY gl.game_date DESC
            LIMIT :limit
        """),
        params
    )

    rows = result.fetchall()
    return {
        "player": player_name,
        "game_logs": [
            {
                "name": row.name,
                "date": row.game_date.isoformat(),
                "opponent": row.opponent,
                "home_away": row.home_away,
                "goals": row.goals,
                "assists": row.assists,
                "points": row.points,
                "shots": row.shots,
                "toi": float(row.toi) if row.toi else None,
                "pp_goals": row.powerplay_goals,
                "pp_points": row.powerplay_points,
                "plus_minus": row.plus_minus,
            }
            for row in rows
        ]
    }


# -------------------------------------------------------------------------
# Prediction endpoints
# -------------------------------------------------------------------------


def prediction_to_dict(pred) -> dict:
    """Convert PlayerPrediction to API-friendly dict."""
    return {
        "player_name": pred.player_name,
        "player_id": pred.player_id,
        "team": pred.team,
        "opponent": pred.opponent,
        "is_home": pred.is_home,
        "probabilities": {
            "goal": pred.prob_goal,
            "point": pred.prob_point,
            "multi_point": pred.prob_multi_point,
        },
        "expected": {
            "goals": pred.expected_goals,
            "assists": pred.expected_assists,
            "points": pred.expected_points,
            "shots": pred.expected_shots,
        },
        "model_components": {
            "recent_form_ppg": pred.recent_form_ppg,
            "season_avg_ppg": pred.season_avg_ppg,
            "h2h_ppg": pred.h2h_ppg,
            "home_away_adjustment": pred.home_away_adjustment,
            "goalie_adjustment": pred.goalie_adjustment,
            "pace_adjustment": pred.pace_adjustment,
        },
        "matchup_info": {
            "opponent_goalie": pred.opponent_goalie,
            "opponent_goalie_sv_pct": pred.opponent_goalie_sv_pct,
        },
        "confidence": pred.confidence,
        "confidence_score": pred.confidence_score,
        "games_analyzed": pred.games_analyzed,
        "factors": pred.factors,
    }


@app.get("/api/predictions/matchup/{home_team}/{away_team}")
async def get_matchup_predictions(
    home_team: str,
    away_team: str,
    game_date: str | None = None,
    top_n: int = 10,
    db: AsyncSession = Depends(get_db),
):
    """
    Get scoring predictions for a matchup between two teams.

    Args:
        home_team: Home team abbreviation (e.g., "TOR")
        away_team: Away team abbreviation (e.g., "BOS")
        game_date: Optional date in YYYY-MM-DD format (defaults to today)
        top_n: Number of players per team to include (default 10)
    """
    from backend.src.agents.predictions import prediction_engine

    # Parse date
    parsed_date = date.today()
    if game_date:
        try:
            parsed_date = datetime.strptime(game_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    prediction = await prediction_engine.get_matchup_prediction(
        db, home_team.upper(), away_team.upper(), parsed_date, top_n
    )

    return {
        "game_date": prediction.game_date.isoformat(),
        "home_team": prediction.home_team,
        "away_team": prediction.away_team,
        "venue": prediction.venue,
        "start_time": prediction.start_time,
        "matchup_context": {
            "expected_total_goals": prediction.expected_total_goals,
            "home_expected_goals": prediction.home_expected_goals,
            "away_expected_goals": prediction.away_expected_goals,
            "home_goalie": prediction.home_goalie,
            "away_goalie": prediction.away_goalie,
            "pace_rating": prediction.pace_rating,
        },
        "top_scorers": [prediction_to_dict(p) for p in prediction.top_scorers],
        "home_players": [prediction_to_dict(p) for p in prediction.home_players],
        "away_players": [prediction_to_dict(p) for p in prediction.away_players],
    }


@app.get("/api/predictions/player/{player_name}")
async def get_player_prediction(
    player_name: str,
    opponent: str,
    is_home: bool = True,
    game_date: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Get scoring prediction for a specific player against an opponent.

    Args:
        player_name: Player's name (fuzzy match)
        opponent: Opponent team abbreviation (e.g., "BOS")
        is_home: Whether the player's team is home (default True)
        game_date: Optional date in YYYY-MM-DD format (defaults to today)
    """
    from backend.src.agents.predictions import prediction_engine

    # Parse date
    parsed_date = date.today()
    if game_date:
        try:
            parsed_date = datetime.strptime(game_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    prediction = await prediction_engine.get_player_prediction(
        db, player_name, opponent.upper(), is_home, parsed_date
    )

    if not prediction:
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found")

    return prediction_to_dict(prediction)


@app.get("/api/predictions/tonight")
async def get_tonight_predictions(
    top_n: int = 5,
    db: AsyncSession = Depends(get_db),
):
    """
    Get scoring predictions for all games scheduled today.

    Returns top scorers across all games.
    """
    from backend.src.agents.predictions import prediction_engine
    from backend.src.ingestion.games import get_todays_games, refresh_todays_schedule

    # Refresh today's schedule
    await refresh_todays_schedule(db)

    # Get today's games
    games = await get_todays_games(db)

    if not games:
        return {
            "date": date.today().isoformat(),
            "message": "No games scheduled today",
            "games": [],
            "top_scorers": [],
        }

    all_top_scorers = []
    game_predictions = []

    for game in games:
        try:
            matchup = await prediction_engine.get_matchup_prediction(
                db,
                game["home_team"],
                game["away_team"],
                date.today(),
                top_n=top_n,
            )
            game_predictions.append({
                "home_team": game["home_team"],
                "away_team": game["away_team"],
                "venue": game["venue"],
                "start_time": game["start_time"],
                "state": game["state"],
                "top_scorers": [prediction_to_dict(p) for p in matchup.top_scorers[:3]],
            })
            all_top_scorers.extend(matchup.top_scorers)
        except Exception as e:
            logger.warning("matchup_prediction_failed", game=game, error=str(e))
            continue

    # Get overall top scorers
    all_top_scorers.sort(key=lambda p: p.prob_goal, reverse=True)
    overall_top = all_top_scorers[:10]

    return {
        "date": date.today().isoformat(),
        "games_count": len(game_predictions),
        "games": game_predictions,
        "top_scorers_overall": [prediction_to_dict(p) for p in overall_top],
        "methodology": {
            "model": "PowerplAI Scoring Model v1",
            "description": "Weighted ensemble model combining multiple factors to predict player scoring outcomes",
            "data_sources": [
                "NHL Official API (player stats, game logs, schedule)",
                "ESPN Injuries API (injury status)",
                "Team/goalie statistics (current season)",
            ],
            "model_weights": {
                "recent_form": 0.30,
                "season_baseline": 0.25,
                "head_to_head_history": 0.15,
                "home_away_splits": 0.10,
                "goalie_matchup": 0.10,
                "team_pace": 0.10,
            },
            "confidence_factors": [
                "Games played this season (min 10 for high confidence)",
                "Recent form consistency",
                "Historical matchup data availability",
            ],
            "notes": [
                "Probabilities based on expected goals/points and historical conversion rates",
                "Goalie adjustment uses opponent starter's save percentage vs league average",
                "Recent form weighted toward last 5 games",
            ],
        },
    }


# -------------------------------------------------------------------------
# Edge Finder & Value Betting Endpoints
# -------------------------------------------------------------------------


@app.get("/api/edges/tonight")
async def get_tonight_edges(
    db: AsyncSession = Depends(get_db),
    min_grade: str = "B+",
    max_results: int = 20,
):
    """
    Find betting edges for tonight's games.

    Analyzes all games and surfaces opportunities where multiple
    positive factors stack (hot streak + weak goalie + high pace, etc.).

    Args:
        min_grade: Minimum edge grade (A+, A, B+, B, C)
        max_results: Maximum edges to return

    Returns edge report with:
        - Top ranked opportunities
        - Edge factors explaining why
        - Estimated fair odds
        - Suggested bet type
    """
    from backend.src.agents.edge_finder import EdgeFinder

    finder = EdgeFinder(db)
    report = await finder.find_tonight_edges(min_grade=min_grade, max_results=max_results)
    return report.to_dict()


@app.get("/api/regression/report")
async def get_regression_report(
    db: AsyncSession = Depends(get_db),
    season: str = None,
    min_games: int = 15,
    top_n: int = 15,
):
    """
    Get xG regression analysis for the league.

    Identifies players significantly over/underperforming their
    expected goals (xG). Underperformers are statistically due
    for positive regression (more goals coming).

    This is a known edge in hockey analytics that casual bettors miss.

    Returns:
        - positive_regression: Players due for MORE goals (BET ON)
        - negative_regression: Players due for FEWER goals (FADE)
        - Confidence levels and bet recommendations
    """
    from backend.src.agents.regression_tracker import RegressionTracker

    tracker = RegressionTracker(db)
    report = await tracker.get_regression_report(
        season=season,
        min_games=min_games,
        top_n=top_n,
    )
    return report.to_dict()


@app.get("/api/regression/player/{player_name}")
async def get_player_regression(
    player_name: str,
    db: AsyncSession = Depends(get_db),
    season: str = None,
):
    """
    Get xG regression analysis for a specific player.

    Shows whether they're overperforming or underperforming
    their expected goals and what that means for betting.
    """
    from backend.src.agents.regression_tracker import RegressionTracker

    tracker = RegressionTracker(db)
    result = await tracker.get_player_regression_analysis(player_name, season)

    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"Player '{player_name}' not found or insufficient data"
        )

    return result.to_dict()


@app.get("/api/value/tonight")
async def get_value_bets(
    db: AsyncSession = Depends(get_db),
    min_edge: float = 0.03,
):
    """
    Find positive expected value (+EV) bets for tonight.

    Compares model probabilities to sportsbook odds (live if API key set,
    otherwise estimated) and calculates expected value for each opportunity.

    Includes Kelly Criterion bet sizing recommendations.

    Args:
        min_edge: Minimum edge to include (default 3%)

    Returns:
        - Value bets ranked by EV
        - Recommended bet sizing (half-Kelly)
        - Implied vs model probability comparison
    """
    from backend.src.agents.odds_value import OddsValueCalculator
    from backend.src.agents.predictions import PredictionEngine

    # Get tonight's predictions
    engine = PredictionEngine(db)
    matchups = await engine.predict_tonight()

    if not matchups:
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "games_analyzed": 0,
            "value_bets": [],
            "message": "No games scheduled tonight"
        }

    # Flatten all player predictions
    all_predictions = []
    for matchup in matchups:
        for pred in matchup.home_players + matchup.away_players:
            all_predictions.append(pred)

    # Find value
    calc = OddsValueCalculator(db)
    report = await calc.find_value_bets(all_predictions, min_edge=min_edge)

    return report.to_dict()


@app.post("/api/value/calculate")
async def calculate_bet_value(
    player_name: str,
    offered_odds: int,
    model_probability: float = None,
    bankroll: float = 1000,
    db: AsyncSession = Depends(get_db),
):
    """
    Calculate expected value for a specific bet opportunity.

    Use this when you see odds at a sportsbook and want to know
    if it's a good bet based on our model.

    Args:
        player_name: Player to bet on
        offered_odds: American odds from sportsbook (e.g., +180, -150)
        model_probability: Override model probability (optional)
        bankroll: Your bankroll for bet sizing (default $1000)

    Returns:
        - Expected value and ROI
        - Kelly Criterion recommended bet size
        - Clear recommendation text
    """
    from backend.src.agents.odds_value import calculate_bet_recommendation
    from backend.src.agents.predictions import PredictionEngine

    # If no probability provided, get from our model
    if model_probability is None:
        # Get player's base probability from recent performance
        result = await db.execute(
            text("""
                SELECT
                    s.goals::float / NULLIF(s.games_played, 0) as gpg,
                    s.games_played
                FROM player_season_stats s
                JOIN players p ON s.player_id = p.id
                WHERE p.name ILIKE :name
                  AND s.season = (SELECT MAX(season) FROM player_season_stats)
                LIMIT 1
            """),
            {"name": f"%{player_name}%"}
        )
        row = result.fetchone()

        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Player '{player_name}' not found"
            )

        # Use Poisson approximation: P(goal) = 1 - e^(-goals_per_game)
        import math
        gpg = row.gpg or 0.3
        model_probability = 1 - math.exp(-gpg)

    return await calculate_bet_recommendation(
        db,
        player_name,
        offered_odds,
        model_probability,
        bankroll,
    )


@app.get("/api/edges/summary")
async def get_edge_summary(db: AsyncSession = Depends(get_db)):
    """
    Get a quick summary of tonight's best betting opportunities.

    Returns a concise overview suitable for quick decision making.
    """
    from backend.src.agents.edge_finder import EdgeFinder
    from backend.src.agents.regression_tracker import RegressionTracker

    finder = EdgeFinder(db)
    edges = await finder.find_tonight_edges(min_grade="A", max_results=5)

    tracker = RegressionTracker(db)
    regression = await tracker.get_regression_report(top_n=5)

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "tonight": {
            "games": edges.game_count,
            "a_plus_edges": edges.a_plus_edges,
            "top_3": [
                {
                    "player": e.player_name,
                    "team": e.team,
                    "grade": e.edge_grade,
                    "prob_goal": round(e.prob_goal, 2),
                    "factors": [f.description for f in e.edge_factors[:2]],
                }
                for e in edges.top_edges[:3]
            ],
        },
        "regression_plays": {
            "positive": [
                {
                    "player": c.player_name,
                    "team": c.team,
                    "goals_below_xg": round(abs(c.differential), 1),
                    "recommendation": c.bet_recommendation,
                }
                for c in regression.positive_regression[:3]
            ],
        },
    }


# -------------------------------------------------------------------------
# Olympic Hockey Endpoints (Milano Cortina 2026)
# -------------------------------------------------------------------------


@app.get("/api/olympics/summary")
async def get_olympic_summary(db: AsyncSession = Depends(get_db)):
    """
    Get current Olympic hockey tournament summary.

    Returns standings, scoring leaders, and goalie leaders
    for the Milano Cortina 2026 Olympic hockey tournament.
    """
    from backend.src.ingestion.olympics import get_olympic_summary

    return await get_olympic_summary(db)


@app.get("/api/olympics/standings")
async def get_olympic_standings():
    """Get current Olympic hockey standings by group."""
    from backend.src.ingestion.olympics import get_current_olympic_data

    data = get_current_olympic_data()
    return {
        "tournament": data["tournament"],
        "standings": data["standings"],
    }


@app.get("/api/olympics/leaders")
async def get_olympic_leaders():
    """Get Olympic hockey scoring and goalie leaders."""
    from backend.src.ingestion.olympics import get_current_olympic_data

    data = get_current_olympic_data()
    return {
        "tournament": data["tournament"],
        "scoring_leaders": data["scoring_leaders"],
        "goalie_leaders": data["goalie_leaders"],
    }


@app.get("/api/olympics/player/{player_name}")
async def get_olympic_player(
    player_name: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Get Olympic stats for a specific player.

    Also returns their NHL stats for comparison.
    """
    from backend.src.ingestion.olympics import get_current_olympic_data

    data = get_current_olympic_data()

    # Find player in Olympic data
    olympic_stats = None
    for player in data["scoring_leaders"]:
        if player_name.lower() in player["name"].lower():
            olympic_stats = player
            break

    for goalie in data["goalie_leaders"]:
        if player_name.lower() in goalie["name"].lower():
            olympic_stats = goalie
            break

    # Get NHL stats
    nhl_stats = None
    result = await db.execute(
        text("""
            SELECT p.name, p.team_abbrev, p.position,
                   s.goals, s.assists, s.points, s.games_played
            FROM players p
            JOIN player_season_stats s ON p.id = s.player_id
            WHERE p.name ILIKE :name
              AND s.season = (SELECT MAX(season) FROM player_season_stats)
            LIMIT 1
        """),
        {"name": f"%{player_name}%"}
    )
    row = result.fetchone()
    if row:
        nhl_stats = {
            "name": row.name,
            "team": row.team_abbrev,
            "position": row.position,
            "goals": row.goals,
            "assists": row.assists,
            "points": row.points,
            "games_played": row.games_played,
        }

    if not olympic_stats and not nhl_stats:
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found")

    return {
        "player_name": player_name,
        "olympic_stats": olympic_stats,
        "nhl_stats": nhl_stats,
    }


@app.get("/api/olympics/predictions/{home_country}/{away_country}")
async def get_olympic_matchup_prediction(
    home_country: str,
    away_country: str,
    game_round: str = "group",
    db: AsyncSession = Depends(get_db),
):
    """
    Get scoring predictions for an Olympic hockey matchup.

    This uses a specialized Olympic prediction model that differs from NHL:
    - Goalie matchups weighted 2x higher (short tournament = hot goalie dominates)
    - In-tournament form matters more than season stats
    - Elimination games have pressure coefficients
    - Country strength ratings factor in roster composition

    Args:
        home_country: Home country code or name (e.g., "CAN", "Canada")
        away_country: Away country code or name (e.g., "USA", "United States")
        game_round: Tournament round (group, quarterfinal, semifinal, bronze, gold)

    Returns detailed predictions with:
    - Top scorers from both teams
    - Goal and point probabilities
    - Matchup context (goalies, team strength)
    - Confidence levels
    """
    from backend.src.ingestion.olympics import predict_olympic_game

    predictions = await predict_olympic_game(
        db, home_country, away_country, game_round
    )

    return {
        "model": "PowerplAI Olympic Prediction Model v1",
        "methodology": {
            "description": "Specialized model for short tournament format",
            "key_differences_from_nhl": [
                "Goalie matchup weighted 2x (20% vs 10% in NHL model)",
                "In-tournament form weighted higher than season stats",
                "Elimination game pressure coefficients applied",
                "Country strength differential (roster composition) factored in",
                "Cross-league normalization for non-NHL players",
            ],
            "weights": {
                "nhl_baseline": 0.45,
                "olympic_form": 0.20,
                "goalie_matchup": 0.20,
                "country_strength": 0.10,
                "international_experience": 0.05,
            }
        },
        **predictions
    }


@app.post("/api/olympics/refresh")
@limiter.limit("10/hour")
async def refresh_olympic_data(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Force refresh Olympic hockey data from ESPN.

    Fetches live stats from ESPN's Olympics coverage page and updates
    the in-memory data. This is the best way to get current stats.

    Rate limited to 10/hour.
    """
    from backend.src.ingestion.olympics import (
        fetch_espn_article_stats,
        update_olympic_stats,
        invalidate_olympic_cache,
        get_olympic_summary_cached,
    )

    # Invalidate cache to force fresh fetch
    invalidate_olympic_cache()

    # Fetch from ESPN article
    espn_data = await fetch_espn_article_stats()

    if espn_data and (espn_data.get("scoring_leaders") or espn_data.get("goalie_leaders")):
        # Update our data with ESPN's live stats
        result = update_olympic_stats(espn_data)
        return {
            "status": "success",
            "source": "espn_live",
            "players_updated": result.get("players_updated", 0) + result.get("players_added", 0),
            "goalies_updated": result.get("goalies_updated", 0),
        }

    # If ESPN article fetch failed, try to get cached data with NHL enrichment
    data = await get_olympic_summary_cached(db)
    return {
        "status": "partial",
        "source": data.get("source", "hardcoded"),
        "message": "ESPN fetch returned no data, using cached/hardcoded stats",
        "player_count": len(data.get("scoring_leaders", [])),
    }


@app.get("/api/olympics/status")
async def get_olympic_status():
    """
    Get Olympic tournament status and last update time.
    """
    from backend.src.ingestion.olympics import (
        is_olympic_tournament_active,
        get_last_olympic_update,
    )

    is_active = is_olympic_tournament_active()
    last_update = get_last_olympic_update()

    return {
        "tournament": "Milano Cortina 2026",
        "is_active": is_active,
        "last_update": last_update.isoformat() if last_update else None,
        "tournament_dates": {
            "start": "2026-02-08",
            "end": "2026-02-22",
        },
        "auto_refresh_interval_minutes": 15 if is_active else None,
    }


@app.get("/api/olympics/value-bets")
async def get_olympic_value_bets(db: AsyncSession = Depends(get_db)):
    """
    Get +EV betting opportunities for today's Olympic games.

    Returns value bets ranked by expected value with Kelly sizing.
    """
    from backend.src.agents.odds_value import get_olympic_value_report

    return await get_olympic_value_report(db)


@app.get("/api/olympics/value-bets/{home_country}/{away_country}")
async def get_olympic_game_value_bets(
    home_country: str,
    away_country: str,
    game_round: str = "group",
    db: AsyncSession = Depends(get_db),
):
    """
    Get +EV betting opportunities for a specific Olympic game.

    Args:
        home_country: Home team country code (e.g., CAN, USA, SWE)
        away_country: Away team country code
        game_round: Game round (group, quarterfinal, semifinal, bronze, gold)

    Returns value bets ranked by expected value.
    """
    from backend.src.agents.odds_value import find_olympic_value_bets

    report = await find_olympic_value_bets(db, home_country, away_country, game_round)
    return report.to_dict()


# -------------------------------------------------------------------------
# Olympic Stats Import/Export Endpoints
# -------------------------------------------------------------------------


class OlympicPlayerStats(BaseModel):
    name: str
    country: str  # Country code like "CAN", "USA", "SWE"
    gp: int = 0   # Games played
    g: int = 0    # Goals
    a: int = 0    # Assists
    pts: int = 0  # Points


class OlympicGoalieStats(BaseModel):
    name: str
    country: str
    gp: int = 0   # Games played
    w: int = 0    # Wins
    gaa: float = 0.0  # Goals against average
    sv: float = 0.0   # Save percentage (e.g., 0.920)


class OlympicStatsUpdate(BaseModel):
    scoring_leaders: list[OlympicPlayerStats] | None = None
    goalie_leaders: list[OlympicGoalieStats] | None = None
    merge: bool = True  # If True, merge with existing; if False, replace


@app.post("/api/olympics/stats")
async def update_olympic_stats(
    stats: OlympicStatsUpdate,
):
    """
    Update Olympic hockey player statistics.

    This is the main endpoint for updating Olympic stats when live data
    sources are unavailable. Accepts player and goalie stats in JSON format.

    Args:
        stats: Object containing:
            - scoring_leaders: List of player stats (name, country, gp, g, a, pts)
            - goalie_leaders: List of goalie stats (name, country, gp, w, gaa, sv)
            - merge: If True (default), updates existing and adds new players.
                     If False, replaces all existing data.

    Example payload:
    ```json
    {
        "scoring_leaders": [
            {"name": "Connor McDavid", "country": "CAN", "gp": 3, "g": 2, "a": 5, "pts": 7},
            {"name": "Auston Matthews", "country": "USA", "gp": 3, "g": 3, "a": 1, "pts": 4}
        ],
        "goalie_leaders": [
            {"name": "Connor Hellebuyck", "country": "USA", "gp": 3, "w": 2, "gaa": 1.67, "sv": 0.938}
        ],
        "merge": true
    }
    ```

    Returns summary of updates applied.
    """
    from backend.src.ingestion.olympics import update_olympic_stats as do_update

    # Convert Pydantic models to dicts
    stats_dict = {}
    if stats.scoring_leaders:
        stats_dict["scoring_leaders"] = [p.model_dump() for p in stats.scoring_leaders]
    if stats.goalie_leaders:
        stats_dict["goalie_leaders"] = [g.model_dump() for g in stats.goalie_leaders]
    stats_dict["merge"] = stats.merge

    result = do_update(stats_dict)
    return {
        "status": "success",
        **result,
    }


@app.post("/api/olympics/import-csv")
async def import_olympic_csv():
    """
    Import Olympic stats from the default CSV file (data/olympic_stats.csv).

    Expected CSV format for players:
    ```
    name,country,gp,g,a,pts,position
    Connor McDavid,CAN,3,2,5,7,F
    Auston Matthews,USA,3,3,1,4,F
    ```

    Expected CSV format for goalies (position=G):
    ```
    name,country,gp,w,gaa,sv,position
    Connor Hellebuyck,USA,3,2,1.67,0.938,G
    ```

    You can also include both in one file - goalies are detected by position=G
    or by having sv/gaa columns populated.
    """
    from backend.src.ingestion.olympics import import_olympic_stats_from_csv
    from pathlib import Path

    csv_path = Path(__file__).parent.parent.parent.parent / "data" / "olympic_stats.csv"

    if not csv_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"CSV file not found: {csv_path}. Create data/olympic_stats.csv with player stats."
        )

    with open(csv_path, "r", encoding="utf-8") as f:
        csv_content = f.read()

    result = import_olympic_stats_from_csv(csv_content)
    return {
        "status": "success",
        "source": str(csv_path),
        **result,
    }


@app.get("/api/olympics/export-csv")
async def export_olympic_csv():
    """
    Export current Olympic stats as CSV.

    Returns a downloadable CSV file with all player and goalie stats.
    Can be edited and re-imported via /api/olympics/import-csv.
    """
    from backend.src.ingestion.olympics import get_olympic_stats_csv
    from fastapi.responses import Response

    csv_content = get_olympic_stats_csv()
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=olympic_stats.csv"}
    )


@app.get("/api/olympics/all-players")
async def get_all_olympic_players():
    """
    Get ALL Olympic player stats currently loaded.

    Returns the complete list of players in the system, useful for:
    - Verifying what data is loaded
    - Getting a template for updates
    - Checking coverage before predictions
    """
    from backend.src.ingestion.olympics import get_current_olympic_data

    data = get_current_olympic_data()
    return {
        "player_count": len(data.get("scoring_leaders", [])),
        "goalie_count": len(data.get("goalie_leaders", [])),
        "countries": list(set(p.get("country") for p in data.get("scoring_leaders", []))),
        "scoring_leaders": data.get("scoring_leaders", []),
        "goalie_leaders": data.get("goalie_leaders", []),
    }


# -------------------------------------------------------------------------
# Prediction Audit & Validation Endpoints
# -------------------------------------------------------------------------


@app.get("/api/audit/stats")
async def get_audit_stats(db: AsyncSession = Depends(get_db)):
    """
    Get overall prediction audit statistics.

    Shows how many predictions have been logged and validated.
    """
    from backend.src.agents.prediction_audit import get_prediction_stats

    try:
        return await get_prediction_stats(db)
    except Exception as e:
        # Table might not exist yet
        return {
            "status": "not_initialized",
            "message": "Run /api/audit/init first to create the audit table",
            "error": str(e),
        }


@app.post("/api/audit/init")
async def initialize_audit_table(db: AsyncSession = Depends(get_db)):
    """
    Initialize the prediction audit table.

    Run this once to create the tracking infrastructure.
    """
    from backend.src.agents.prediction_audit import create_audit_table

    await create_audit_table(db)
    return {"status": "initialized", "message": "Prediction audit table created"}


@app.get("/api/audit/report")
async def get_validation_report(
    start_date: str = None,
    end_date: str = None,
    model_version: str = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a validation report for a time period.

    This is the main endpoint to assess model accuracy.

    Returns:
    - Brier scores (overall accuracy)
    - Calibration buckets (are probabilities accurate?)
    - Hit rates by confidence level
    - ROI simulation (hypothetical betting returns)

    Args:
        start_date: Start date (YYYY-MM-DD), defaults to 30 days ago
        end_date: End date (YYYY-MM-DD), defaults to today
        model_version: Filter by model version (nhl_v1, olympic_v1, etc.)
    """
    from backend.src.agents.prediction_audit import generate_validation_report

    # Parse dates
    if start_date:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
    else:
        start = date.today() - timedelta(days=30)

    if end_date:
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    else:
        end = date.today()

    report = await generate_validation_report(db, start, end, model_version)
    return report.to_dict()


@app.get("/api/audit/pending")
async def get_pending_validations(db: AsyncSession = Depends(get_db)):
    """
    Get predictions that haven't been validated yet.

    These are predictions where the game has happened but
    we haven't recorded the actual outcomes.
    """
    from backend.src.agents.prediction_audit import get_unvalidated_predictions

    predictions = await get_unvalidated_predictions(db, before_date=date.today())
    return {
        "pending_count": len(predictions),
        "predictions": predictions,
    }


@app.post("/api/audit/validate/{game_date}")
async def validate_game(
    game_date: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Validate predictions for a specific game date.

    Fetches actual game results and compares to predictions.

    Args:
        game_date: Date to validate (YYYY-MM-DD)
    """
    from backend.src.agents.prediction_audit import validate_game_outcomes

    try:
        parsed_date = datetime.strptime(game_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    if parsed_date >= date.today():
        raise HTTPException(
            status_code=400,
            detail="Cannot validate future games. Wait until the game is complete."
        )

    stats = await validate_game_outcomes(db, parsed_date)
    return {
        "game_date": game_date,
        "validation_results": stats,
    }


@app.post("/api/audit/log-tonight")
async def log_tonight_predictions(
    db: AsyncSession = Depends(get_db),
):
    """
    Log predictions for all games scheduled tonight.

    This creates an immutable audit trail BEFORE the games happen.
    Call this before games start to enable validation later.
    """
    from backend.src.agents.prediction_audit import log_matchup_predictions
    from backend.src.agents.predictions import prediction_engine
    from backend.src.ingestion.games import get_todays_games, refresh_todays_schedule

    # Refresh schedule
    await refresh_todays_schedule(db)
    games = await get_todays_games(db)

    if not games:
        return {
            "status": "no_games",
            "message": "No games scheduled today",
            "logged": 0,
        }

    total_logged = 0
    games_processed = 0

    for game in games:
        try:
            # Generate prediction
            matchup = await prediction_engine.get_matchup_prediction(
                db,
                game["home_team"],
                game["away_team"],
                date.today(),
                top_n=10,
            )

            # Log to audit trail
            count = await log_matchup_predictions(db, matchup, game_type="nhl")
            total_logged += count
            games_processed += 1

        except Exception as e:
            logger.warning("log_game_predictions_failed", game=game, error=str(e))

    return {
        "status": "success",
        "date": date.today().isoformat(),
        "games_processed": games_processed,
        "predictions_logged": total_logged,
        "message": f"Logged {total_logged} predictions for {games_processed} games. Run /api/audit/validate/{date.today().isoformat()} after games complete."
    }


@app.get("/api/games/all-today")
async def get_all_todays_games(db: AsyncSession = Depends(get_db)):
    """
    Get ALL games for today - both NHL and Olympics if active.

    This is the unified schedule endpoint that automatically
    includes Olympic games during the tournament.

    Returns:
    - NHL games with times and venues
    - Olympic games with round info (if tournament active)
    - Total count and breakdown
    """
    from backend.src.agents.daily_audit import get_todays_games_unified

    games = await get_todays_games_unified(db)
    return games.to_dict()


@app.post("/api/audit/run-daily")
async def run_daily_audit_now(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Manually trigger the daily audit cycle.

    This will:
    1. Validate yesterday's predictions against actual outcomes
    2. Log today's predictions for all scheduled games

    Normally runs automatically on startup, but can be triggered manually.
    """
    from backend.src.agents.daily_audit import run_daily_audit

    async def run_audit():
        async with async_session_maker() as session:
            result = await run_daily_audit(session)
            logger.info("manual_daily_audit_complete", **result)

    background_tasks.add_task(run_audit)
    return {
        "status": "started",
        "message": "Daily audit started in background. Check /api/audit/stats for results."
    }


@app.get("/api/audit/accuracy-summary")
async def get_accuracy_summary(
    days: int = 7,
    db: AsyncSession = Depends(get_db),
):
    """
    Get a quick accuracy summary for the last N days.

    Provides a snapshot of prediction accuracy by game type (NHL vs Olympic).
    """
    from backend.src.agents.daily_audit import get_accuracy_summary

    return await get_accuracy_summary(db, days)


@app.get("/api/audit/calibration-chart")
async def get_calibration_chart_data(
    start_date: str = None,
    end_date: str = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Get calibration data formatted for charting.

    Returns data to plot predicted vs actual hit rates.
    Perfect calibration = diagonal line from (0,0) to (1,1).
    """
    from backend.src.agents.prediction_audit import generate_validation_report

    # Parse dates
    if start_date:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
    else:
        start = date.today() - timedelta(days=30)

    if end_date:
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    else:
        end = date.today()

    report = await generate_validation_report(db, start, end)

    # Format for charting
    chart_data = {
        "title": "Prediction Calibration Chart",
        "description": "Predicted probability vs actual hit rate. Perfect calibration follows the diagonal.",
        "period": f"{start} to {end}",
        "goal_calibration": [
            {
                "predicted": (b.bucket_min + b.bucket_max) / 2,
                "actual": b.actual_rate,
                "sample_size": b.total_predictions,
                "calibrated": b.is_well_calibrated,
            }
            for b in report.goal_calibration_buckets
            if b.total_predictions > 0
        ],
        "ideal_line": [
            {"x": 0, "y": 0},
            {"x": 0.5, "y": 0.5},
            {"x": 1, "y": 1},
        ],
        "brier_score": report.goal_brier_score,
        "interpretation": report._interpret_brier(report.goal_brier_score),
    }

    return chart_data


@app.get("/api/model/evaluation")
async def get_model_evaluation(
    start_date: str = None,
    end_date: str = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Get comprehensive model evaluation metrics.

    Includes:
    - Classification metrics (accuracy, precision, recall, F1)
    - Probabilistic metrics (Brier score, log loss, ROC AUC)
    - Calibration analysis
    - Baseline comparison
    """
    from backend.src.agents.model_evaluation import run_model_evaluation
    from datetime import datetime as dt

    start = None
    end = None
    if start_date:
        start = dt.strptime(start_date, "%Y-%m-%d").date()
    if end_date:
        end = dt.strptime(end_date, "%Y-%m-%d").date()

    return await run_model_evaluation(db, start, end)


@app.get("/api/model/backtest")
async def run_backtest(
    start_date: str,
    end_date: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Run a rolling window backtest of the prediction model.

    Returns daily performance metrics for the date range.
    """
    from backend.src.agents.model_evaluation import backtest_model
    from datetime import datetime as dt

    start = dt.strptime(start_date, "%Y-%m-%d").date()
    end = dt.strptime(end_date, "%Y-%m-%d").date()

    if (end - start).days > 90:
        raise HTTPException(
            status_code=400,
            detail="Backtest range limited to 90 days",
        )

    results = await backtest_model(db, start, end)

    return {
        "date_range": {"start": start_date, "end": end_date},
        "total_days": len(results),
        "daily_results": results,
        "summary": {
            "avg_brier": sum(r["brier_score"] for r in results) / len(results) if results else None,
            "avg_accuracy": sum(r["accuracy"] for r in results) / len(results) if results else None,
            "total_predictions": sum(r["predictions"] for r in results),
        },
    }


@app.get("/api/model/info")
async def get_model_info():
    """Get information about the prediction model architecture."""
    from backend.src.agents.predictions import WEIGHTS, MIN_GAMES_RECENT, MIN_GAMES_SEASON, MIN_GAMES_H2H

    return {
        "model_type": "Multi-Factor Weighted Probability Model",
        "description": "Combines multiple statistical factors using domain-knowledge weights, then converts to probabilities via Poisson distribution",
        "version": "1.0",
        "weights": WEIGHTS,
        "minimum_samples": {
            "recent_form": MIN_GAMES_RECENT,
            "season": MIN_GAMES_SEASON,
            "head_to_head": MIN_GAMES_H2H,
        },
        "probability_model": "Poisson",
        "features": [
            "Recent form (last 5 games)",
            "Season baseline average",
            "Head-to-head history",
            "Home/away splits",
            "Goalie matchup adjustment",
            "Team pace adjustment",
        ],
        "note": "This is NOT an ensemble model. It uses fixed weights on statistical factors, not multiple ML models.",
    }


# -------------------------------------------------------------------------
# Debug endpoints (disable in production)
# -------------------------------------------------------------------------


if settings.debug:
    @app.get("/api/debug/tables")
    async def debug_tables(db: AsyncSession = Depends(get_db)):
        """List all tables and row counts."""
        result = await db.execute(text("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
        """))
        tables = [row[0] for row in result.fetchall()]

        counts = {}
        for table in tables:
            count_result = await db.execute(text(f"SELECT COUNT(*) FROM {table}"))
            counts[table] = count_result.scalar()

        return counts
