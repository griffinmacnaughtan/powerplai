"""
Startup updates for PowerplAI.

Handles automatic data refresh on application startup:
- Game logs catch-up (covers missed days)
- Injury updates
- Team and goalie stats refresh
- Schedule refresh

Includes intelligent catch-up logic to handle gaps when the app hasn't run.
"""
import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path
import json
import structlog

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.db.database import async_session_maker
from backend.src.config import get_settings
from backend.src.ingestion.scheduler import get_current_season, load_progress, save_progress

logger = structlog.get_logger()
settings = get_settings()

# Progress tracking file
PROGRESS_FILE = Path("data/ingestion_progress.json")

# How far back to look for missed games (max catch-up window)
MAX_CATCHUP_DAYS = 14


def get_last_game_log_date() -> date | None:
    """Get the last date we ingested game logs for."""
    progress = load_progress()
    last_date_str = progress.get("last_game_log_date")
    if last_date_str:
        try:
            return datetime.fromisoformat(last_date_str).date()
        except (ValueError, TypeError):
            pass
    return None


def set_last_game_log_date(update_date: date):
    """Record the last date we ingested game logs for."""
    progress = load_progress()
    progress["last_game_log_date"] = update_date.isoformat()
    save_progress(progress)


def get_last_injury_update() -> datetime | None:
    """Get the last time we updated injuries."""
    progress = load_progress()
    last_update_str = progress.get("last_injury_update")
    if last_update_str:
        try:
            return datetime.fromisoformat(last_update_str)
        except (ValueError, TypeError):
            pass
    return None


def set_last_injury_update():
    """Record that we just updated injuries."""
    progress = load_progress()
    progress["last_injury_update"] = datetime.now().isoformat()
    save_progress(progress)


def get_last_team_stats_update() -> datetime | None:
    """Get the last time we updated team/goalie stats."""
    progress = load_progress()
    last_update_str = progress.get("last_team_stats_update")
    if last_update_str:
        try:
            return datetime.fromisoformat(last_update_str)
        except (ValueError, TypeError):
            pass
    return None


def set_last_team_stats_update():
    """Record that we just updated team/goalie stats."""
    progress = load_progress()
    progress["last_team_stats_update"] = datetime.now().isoformat()
    save_progress(progress)


def get_last_roster_sync() -> datetime | None:
    """Get the last time we synced rosters."""
    progress = load_progress()
    last_sync_str = progress.get("last_roster_sync")
    if last_sync_str:
        try:
            return datetime.fromisoformat(last_sync_str)
        except (ValueError, TypeError):
            pass
    return None


def set_last_roster_sync():
    """Record that we just synced rosters."""
    progress = load_progress()
    progress["last_roster_sync"] = datetime.now().isoformat()
    save_progress(progress)


def get_last_moneypuck_update() -> datetime | None:
    """Get the last time we updated MoneyPuck stats for current season."""
    progress = load_progress()
    last_update_str = progress.get("last_moneypuck_update")
    if last_update_str:
        try:
            return datetime.fromisoformat(last_update_str)
        except (ValueError, TypeError):
            pass
    return None


def set_last_moneypuck_update():
    """Record that we just updated MoneyPuck stats."""
    progress = load_progress()
    progress["last_moneypuck_update"] = datetime.now().isoformat()
    save_progress(progress)


async def catchup_game_logs(db: AsyncSession, season: str) -> dict:
    """
    Catch up on missed game logs.

    Identifies games that were played since our last update and
    fetches game logs for players who participated.

    Returns stats about the catch-up operation.
    """
    from backend.src.ingestion.games import (
        ingest_schedule_range,
        ingest_all_player_game_logs,
    )
    from backend.src.ingestion.nhl_api import NHLAPIClient

    stats = {
        "days_missed": 0,
        "games_found": 0,
        "logs_updated": 0,
        "start_date": None,
        "end_date": None,
    }

    last_update = get_last_game_log_date()
    today = date.today()

    # Determine start date for catch-up
    if last_update is None:
        # First run - start from beginning of season or MAX_CATCHUP_DAYS ago
        # NHL season typically starts in October
        current_year = int(season[:4])
        season_start = date(current_year, 10, 1)

        # Don't go further back than MAX_CATCHUP_DAYS
        earliest_allowed = today - timedelta(days=MAX_CATCHUP_DAYS)
        start_date = max(season_start, earliest_allowed)
    else:
        # Start from day after last update
        start_date = last_update + timedelta(days=1)

    # If we're already up to date, nothing to do
    if start_date >= today:
        logger.info("game_logs_up_to_date", last_update=last_update)
        return stats

    stats["start_date"] = start_date.isoformat()
    stats["end_date"] = (today - timedelta(days=1)).isoformat()  # Yesterday
    stats["days_missed"] = (today - start_date).days

    logger.info(
        "catching_up_game_logs",
        start_date=start_date,
        end_date=today - timedelta(days=1),
        days_missed=stats["days_missed"]
    )

    # Step 1: Refresh schedule for the missed period
    client = NHLAPIClient()
    try:
        # Get games that were played in the catch-up window
        games_result = await db.execute(
            text("""
                SELECT DISTINCT game_date FROM games
                WHERE game_date >= :start_date
                  AND game_date < :today
                  AND is_completed = TRUE
                ORDER BY game_date
            """),
            {"start_date": start_date, "today": today}
        )
        completed_dates = [row[0] for row in games_result.fetchall()]

        # Also refresh schedule to make sure we have recent games
        games_count = await ingest_schedule_range(db, start_date, today - timedelta(days=1), client)
        stats["games_found"] = games_count

    finally:
        await client.close()

    # Step 2: Re-ingest game logs for all active players
    # This will update stats for any games played since last update
    if stats["days_missed"] > 0:
        logger.info("refreshing_player_game_logs", season=season)

        # For efficiency, just re-run the full game log ingestion
        # The API returns all season games, and we upsert, so it's safe
        result = await ingest_all_player_game_logs(db, season)
        stats["logs_updated"] = result.get("logs_ingested", 0)

    # Update progress
    set_last_game_log_date(today - timedelta(days=1))

    logger.info("game_log_catchup_complete", **stats)
    return stats


async def update_injuries(db: AsyncSession, season: str) -> dict:
    """
    Update injury information from ESPN API.

    Returns stats about injuries found.
    """
    from backend.src.ingestion.espn_injuries import ingest_espn_injuries

    # Check if we've updated recently (within last 4 hours)
    last_update = get_last_injury_update()
    if last_update:
        hours_since = (datetime.now() - last_update).total_seconds() / 3600
        if hours_since < 4:
            logger.info("injuries_recently_updated", hours_ago=round(hours_since, 1))
            return {"skipped": True, "reason": "recently_updated"}

    logger.info("updating_injuries_from_espn")
    stats = await ingest_espn_injuries(db)

    set_last_injury_update()
    return stats


async def update_team_goalie_stats(db: AsyncSession, season: str) -> dict:
    """
    Update team and goalie statistics.

    Returns stats about the update.
    """
    from backend.src.ingestion.team_goalie_stats import refresh_all_stats

    # Check if we've updated recently (within last 12 hours)
    last_update = get_last_team_stats_update()
    if last_update:
        hours_since = (datetime.now() - last_update).total_seconds() / 3600
        if hours_since < 12:
            logger.info("team_stats_recently_updated", hours_ago=round(hours_since, 1))
            return {"skipped": True, "reason": "recently_updated"}

    logger.info("updating_team_goalie_stats", season=season)
    stats = await refresh_all_stats(season)

    set_last_team_stats_update()
    return stats


async def update_moneypuck_stats(db: AsyncSession, season_year: str) -> dict:
    """
    Refresh MoneyPuck advanced stats for the current season.

    MoneyPuck updates their data regularly during the season,
    so we need to re-download to get latest xG, Corsi, etc.
    """
    from backend.src.ingestion.moneypuck import download_season_stats, transform_moneypuck_to_schema
    from pathlib import Path

    # Check if we've updated recently (within last 12 hours)
    last_update = get_last_moneypuck_update()
    if last_update:
        hours_since = (datetime.now() - last_update).total_seconds() / 3600
        if hours_since < 12:
            logger.info("moneypuck_recently_updated", hours_ago=round(hours_since, 1))
            return {"skipped": True, "reason": "recently_updated"}

    logger.info("updating_moneypuck_stats", season=season_year)

    try:
        # Download latest MoneyPuck data
        data_path = Path(f"data/raw/moneypuck_{season_year}.csv")
        df = await download_season_stats(season_year, save_path=data_path)
        records = transform_moneypuck_to_schema(df)

        season = f"{season_year}{int(season_year) + 1}"
        updated_count = 0

        for record in records:
            # Ensure player exists
            await db.execute(
                text("""
                    INSERT INTO players (nhl_id, name, team_abbrev)
                    VALUES (:nhl_id, :name, :team_abbrev)
                    ON CONFLICT (nhl_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        updated_at = NOW()
                """),
                {
                    "nhl_id": record["nhl_player_id"],
                    "name": record["player_name"],
                    "team_abbrev": record["team_abbrev"]
                },
            )

            # Get player id
            result = await db.execute(
                text("SELECT id FROM players WHERE nhl_id = :nhl_id"),
                {"nhl_id": record["nhl_player_id"]},
            )
            player_row = result.fetchone()

            if player_row:
                await db.execute(
                    text("""
                        INSERT INTO player_season_stats (
                            player_id, season, team_abbrev, games_played,
                            goals, assists, points, shots, toi_per_game,
                            xg, xg_per_60, corsi_for_pct, fenwick_for_pct
                        ) VALUES (
                            :player_id, :season, :team_abbrev, :games_played,
                            :goals, :assists, :points, :shots, :toi_per_game,
                            :xg, :xg_per_60, :corsi_for_pct, :fenwick_for_pct
                        )
                        ON CONFLICT (player_id, season) DO UPDATE SET
                            team_abbrev = EXCLUDED.team_abbrev,
                            games_played = EXCLUDED.games_played,
                            goals = EXCLUDED.goals,
                            assists = EXCLUDED.assists,
                            points = EXCLUDED.points,
                            shots = EXCLUDED.shots,
                            toi_per_game = EXCLUDED.toi_per_game,
                            xg = EXCLUDED.xg,
                            xg_per_60 = EXCLUDED.xg_per_60,
                            corsi_for_pct = EXCLUDED.corsi_for_pct,
                            fenwick_for_pct = EXCLUDED.fenwick_for_pct,
                            updated_at = NOW()
                    """),
                    {
                        "player_id": player_row[0],
                        "season": season,
                        "team_abbrev": record["team_abbrev"],
                        "games_played": record["games_played"],
                        "goals": record["goals"],
                        "assists": record["assists"],
                        "points": record["points"],
                        "shots": record["shots"],
                        "toi_per_game": record["toi_per_game"],
                        "xg": record["xg"],
                        "xg_per_60": record["xg_per_60"],
                        "corsi_for_pct": record["corsi_for_pct"],
                        "fenwick_for_pct": record["fenwick_for_pct"],
                    },
                )
                updated_count += 1

        await db.commit()
        set_last_moneypuck_update()

        logger.info("moneypuck_stats_updated", count=updated_count)
        return {"updated": updated_count, "season": season}

    except Exception as e:
        logger.error("moneypuck_update_failed", error=str(e))
        return {"error": str(e)}


async def update_rosters(db: AsyncSession, season: str) -> dict:
    """
    Sync current team rosters from NHL API.

    Updates player team assignments to reflect trades and roster moves.
    """
    from backend.src.ingestion.roster_sync import sync_team_rosters

    # Check if we've synced recently (within last 24 hours)
    last_sync = get_last_roster_sync()
    if last_sync:
        hours_since = (datetime.now() - last_sync).total_seconds() / 3600
        if hours_since < 24:
            logger.info("rosters_recently_synced", hours_ago=round(hours_since, 1))
            return {"skipped": True, "reason": "recently_synced"}

    logger.info("syncing_team_rosters", season=season)
    stats = await sync_team_rosters(db, season)

    set_last_roster_sync()
    return stats


async def refresh_todays_schedule(db: AsyncSession) -> int:
    """Refresh today's game schedule."""
    from backend.src.ingestion.games import ingest_schedule_for_date

    logger.info("refreshing_todays_schedule")
    return await ingest_schedule_for_date(db, date.today())


async def run_startup_updates() -> dict:
    """
    Run all startup updates.

    This is the main entry point called on application startup.
    Handles:
    1. Today's schedule refresh
    2. Game log catch-up
    3. Injury updates
    4. Team/goalie stats refresh

    Returns summary of all updates performed.
    """
    results = {
        "schedule": None,
        "moneypuck": None,
        "game_logs": None,
        "injuries": None,
        "team_stats": None,
        "rosters": None,
        "errors": [],
    }

    season = f"{get_current_season()}{int(get_current_season()) + 1}"
    season_year = get_current_season()

    logger.info("starting_startup_updates", season=season)

    async with async_session_maker() as db:
        # 0. Check if we need to load MoneyPuck stats (fresh deploy)
        try:
            stats_check = await db.execute(text("SELECT COUNT(*) FROM player_season_stats"))
            stats_count = stats_check.scalar()
            if stats_count == 0 or stats_count < 100:
                logger.info("loading_moneypuck_stats", reason="fresh_deploy", season=season_year)
                from backend.src.ingestion.moneypuck import download_season_stats, transform_moneypuck_to_schema
                from pathlib import Path

                # Download MoneyPuck data
                data_path = Path(f"data/raw/moneypuck_{season_year}.csv")
                df = await download_season_stats(season_year, save_path=data_path)
                records = transform_moneypuck_to_schema(df)

                # Insert players and stats
                for record in records:
                    # Ensure player exists
                    await db.execute(
                        text("""
                            INSERT INTO players (nhl_id, name, team_abbrev)
                            VALUES (:nhl_id, :name, :team_abbrev)
                            ON CONFLICT (nhl_id) DO UPDATE SET team_abbrev = EXCLUDED.team_abbrev
                        """),
                        {"nhl_id": record["nhl_player_id"], "name": record["player_name"], "team_abbrev": record["team_abbrev"]},
                    )
                    # Get player id
                    result = await db.execute(
                        text("SELECT id FROM players WHERE nhl_id = :nhl_id"),
                        {"nhl_id": record["nhl_player_id"]},
                    )
                    player_row = result.fetchone()
                    if player_row:
                        await db.execute(
                            text("""
                                INSERT INTO player_season_stats (
                                    player_id, season, team_abbrev, games_played,
                                    goals, assists, points, shots, toi_per_game,
                                    xg, xg_per_60, corsi_for_pct, fenwick_for_pct
                                ) VALUES (
                                    :player_id, :season, :team_abbrev, :games_played,
                                    :goals, :assists, :points, :shots, :toi_per_game,
                                    :xg, :xg_per_60, :corsi_for_pct, :fenwick_for_pct
                                )
                                ON CONFLICT (player_id, season) DO UPDATE SET
                                    games_played = EXCLUDED.games_played,
                                    goals = EXCLUDED.goals,
                                    assists = EXCLUDED.assists,
                                    points = EXCLUDED.points,
                                    xg = EXCLUDED.xg,
                                    corsi_for_pct = EXCLUDED.corsi_for_pct
                            """),
                            {
                                "player_id": player_row[0],
                                "season": season,
                                "team_abbrev": record["team_abbrev"],
                                "games_played": record["games_played"],
                                "goals": record["goals"],
                                "assists": record["assists"],
                                "points": record["points"],
                                "shots": record["shots"],
                                "toi_per_game": record["toi_per_game"],
                                "xg": record["xg"],
                                "xg_per_60": record["xg_per_60"],
                                "corsi_for_pct": record["corsi_for_pct"],
                                "fenwick_for_pct": record["fenwick_for_pct"],
                            },
                        )
                await db.commit()
                results["moneypuck"] = {"loaded": len(records)}
                logger.info("moneypuck_stats_loaded", count=len(records))
            else:
                results["moneypuck"] = {"skipped": True, "existing_count": stats_count}
                logger.info("moneypuck_stats_exist", count=stats_count)
        except Exception as e:
            logger.error("moneypuck_load_failed", error=str(e))
            results["errors"].append(f"moneypuck: {str(e)}")

        # 1. Refresh today's schedule
        try:
            results["schedule"] = await refresh_todays_schedule(db)
        except Exception as e:
            logger.error("schedule_refresh_failed", error=str(e))
            results["errors"].append(f"schedule: {str(e)}")

        # 2. Catch up on game logs
        try:
            results["game_logs"] = await catchup_game_logs(db, season)
        except Exception as e:
            logger.error("game_log_catchup_failed", error=str(e))
            results["errors"].append(f"game_logs: {str(e)}")

        # 3. Update injuries
        try:
            results["injuries"] = await update_injuries(db, season)
        except Exception as e:
            logger.error("injury_update_failed", error=str(e))
            results["errors"].append(f"injuries: {str(e)}")

        # 4. Update team/goalie stats
        try:
            results["team_stats"] = await update_team_goalie_stats(db, season)
        except Exception as e:
            logger.error("team_stats_update_failed", error=str(e))
            results["errors"].append(f"team_stats: {str(e)}")

        # 5. Sync team rosters (updates player team assignments for trades)
        try:
            results["rosters"] = await update_rosters(db, season)
        except Exception as e:
            logger.error("roster_sync_failed", error=str(e))
            results["errors"].append(f"rosters: {str(e)}")

        # 6. Refresh MoneyPuck advanced stats for current season (if not recently done)
        try:
            results["moneypuck_refresh"] = await update_moneypuck_stats(db, season_year)
        except Exception as e:
            logger.error("moneypuck_refresh_failed", error=str(e))
            results["errors"].append(f"moneypuck_refresh: {str(e)}")

    logger.info("startup_updates_complete", results=results)
    return results


async def run_daily_updates() -> dict:
    """
    Run daily scheduled updates (can be called by a scheduler/cron).

    More aggressive than startup updates - always refreshes everything.
    """
    results = {
        "schedule": None,
        "game_logs": None,
        "injuries": None,
        "team_stats": None,
        "rosters": None,
        "moneypuck": None,
        "errors": [],
    }

    season_year = get_current_season()
    season = f"{season_year}{int(season_year) + 1}"

    logger.info("starting_daily_updates", season=season)

    async with async_session_maker() as db:
        # Refresh schedule for next 7 days
        from backend.src.ingestion.games import ingest_schedule_range
        try:
            today = date.today()
            results["schedule"] = await ingest_schedule_range(
                db, today, today + timedelta(days=7)
            )
        except Exception as e:
            logger.error("schedule_refresh_failed", error=str(e))
            results["errors"].append(f"schedule: {str(e)}")

        # Force refresh game logs
        from backend.src.ingestion.games import ingest_all_player_game_logs
        try:
            result = await ingest_all_player_game_logs(db, season)
            results["game_logs"] = result
            set_last_game_log_date(date.today())
        except Exception as e:
            logger.error("game_log_update_failed", error=str(e))
            results["errors"].append(f"game_logs: {str(e)}")

        # Force refresh injuries from ESPN
        from backend.src.ingestion.espn_injuries import ingest_espn_injuries
        try:
            results["injuries"] = await ingest_espn_injuries(db)
            set_last_injury_update()
        except Exception as e:
            logger.error("injury_update_failed", error=str(e))
            results["errors"].append(f"injuries: {str(e)}")

        # Force refresh team/goalie stats
        from backend.src.ingestion.team_goalie_stats import refresh_all_stats
        try:
            results["team_stats"] = await refresh_all_stats(season)
            set_last_team_stats_update()
        except Exception as e:
            logger.error("team_stats_update_failed", error=str(e))
            results["errors"].append(f"team_stats: {str(e)}")

        # Force sync team rosters (catches trades)
        from backend.src.ingestion.roster_sync import sync_team_rosters
        try:
            results["rosters"] = await sync_team_rosters(db, season)
            set_last_roster_sync()
        except Exception as e:
            logger.error("roster_sync_failed", error=str(e))
            results["errors"].append(f"rosters: {str(e)}")

        # Force refresh MoneyPuck advanced stats for current season
        try:
            results["moneypuck"] = await update_moneypuck_stats(db, season_year)
        except Exception as e:
            logger.error("moneypuck_update_failed", error=str(e))
            results["errors"].append(f"moneypuck: {str(e)}")

    logger.info("daily_updates_complete", results=results)
    return results
