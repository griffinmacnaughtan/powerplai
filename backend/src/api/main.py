"""
PowerplAI API - FastAPI application.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, date
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
        asyncio.create_task(run_startup_updates())

    yield
    logger.info("shutting_down_powerplai_api")
    await engine.dispose()


app = FastAPI(
    title="PowerplAI",
    description="Hockey Analytics & Fantasy Copilot API",
    version="0.1.0",
    lifespan=lifespan,
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


class QueryRequest(BaseModel):
    query: str
    include_rag: bool = True


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
        result = await copilot.query(
            query_request.query,
            db,
            include_rag=query_request.include_rag,
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
        "team_filter": request.team_abbrev,
        "player_limit": request.limit
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
async def refresh_salary_data(request: Request, background_tasks: BackgroundTasks):
    """
    Trigger a refresh of salary cap data from PuckPedia.
    Note: This scrapes web data and takes several minutes.
    Rate limited to 2/hour.
    """
    from backend.src.ingestion.salary_cap import refresh_all_salaries

    async def run_refresh():
        result = await refresh_all_salaries()
        logger.info("salary_data_refreshed", **result)

    background_tasks.add_task(run_refresh)
    return {"status": "started", "message": "Salary data refresh started (this takes a few minutes)"}


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
