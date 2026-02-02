"""
Game and game log ingestion for PowerplAI predictions.

Handles:
- Schedule/game ingestion from NHL API
- Player game log ingestion
- Historical data backfill
"""
import asyncio
from datetime import date, datetime, timedelta
from typing import Any
import structlog

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.db.database import async_session_maker
from backend.src.ingestion.nhl_api import NHLAPIClient, _parse_toi

logger = structlog.get_logger()


def parse_game_from_schedule(game_data: dict[str, Any], season: str, day_date: str | None = None) -> dict[str, Any]:
    """Transform NHL API schedule game to our schema."""
    # Parse start_time_utc
    start_time_str = game_data.get("startTimeUTC")
    start_time_parsed = None
    game_date_parsed = None

    if start_time_str:
        try:
            # Handle ISO format with Z suffix
            clean_time = start_time_str.replace("Z", "+00:00")
            start_time_parsed = datetime.fromisoformat(clean_time).replace(tzinfo=None)
        except (ValueError, AttributeError):
            pass

    # PREFER day_date from schedule (the actual game date in local time)
    # Don't derive from UTC time as evening games show as next day in UTC
    if day_date:
        try:
            game_date_parsed = datetime.strptime(day_date, "%Y-%m-%d").date()
        except ValueError:
            pass

    # Fall back to UTC date only if day_date not available
    if game_date_parsed is None and start_time_parsed:
        game_date_parsed = start_time_parsed.date()

    return {
        "nhl_game_id": game_data.get("id"),
        "season": season,
        "game_type": game_data.get("gameType", 2),
        "game_date": game_date_parsed,
        "start_time_utc": start_time_parsed,
        "venue": game_data.get("venue", {}).get("default") if isinstance(game_data.get("venue"), dict) else game_data.get("venue"),
        "home_team_abbrev": game_data.get("homeTeam", {}).get("abbrev"),
        "away_team_abbrev": game_data.get("awayTeam", {}).get("abbrev"),
        "home_score": game_data.get("homeTeam", {}).get("score"),
        "away_score": game_data.get("awayTeam", {}).get("score"),
        "game_state": game_data.get("gameState", "FUT"),
        "is_completed": game_data.get("gameState") in ("FINAL", "OFF"),
    }


def parse_game_log_entry(
    player_id: int,
    entry: dict[str, Any],
    season: str | None = None
) -> dict[str, Any]:
    """Transform NHL API game log entry to our enhanced schema."""
    # Parse game_date from string to date object
    game_date_str = entry.get("gameDate")
    game_date_parsed = None
    if game_date_str:
        try:
            game_date_parsed = datetime.strptime(game_date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            pass

    return {
        "player_id": player_id,
        "game_id": entry.get("gameId"),
        "game_date": game_date_parsed,
        "season": season,
        "team_abbrev": entry.get("teamAbbrev"),
        "opponent": entry.get("opponentAbbrev"),
        "home_away": "home" if entry.get("homeRoadFlag") == "H" else "away",
        "goals": entry.get("goals", 0),
        "assists": entry.get("assists", 0),
        "points": entry.get("points", 0),
        "shots": entry.get("shots", 0),
        "toi": _parse_toi(entry.get("toi", "0:00")),
        "plus_minus": entry.get("plusMinus", 0),
        "pim": entry.get("pim", 0),
        "powerplay_goals": entry.get("powerPlayGoals", 0),
        "powerplay_points": entry.get("powerPlayPoints", 0),
        "shorthanded_goals": entry.get("shorthandedGoals", 0),
        "shorthanded_points": entry.get("shorthandedPoints", 0),
        "game_winning_goals": entry.get("gameWinningGoals", 0),
        "overtime_goals": entry.get("otGoals", 0),
        "shifts": entry.get("shifts"),
    }


async def ingest_schedule_for_date(
    db: AsyncSession,
    target_date: date | str,
    client: NHLAPIClient | None = None
) -> int:
    """
    Fetch and store games for a specific date.

    Returns number of games upserted.
    """
    close_client = False
    if client is None:
        client = NHLAPIClient()
        close_client = True

    try:
        date_str = target_date if isinstance(target_date, str) else target_date.isoformat()
        schedule_data = await client.get_schedule(date_str)

        games_upserted = 0
        game_week = schedule_data.get("gameWeek", [])

        for day_data in game_week:
            day_date = day_data.get("date")  # e.g., "2026-02-01"
            games = day_data.get("games", [])
            for game_data in games:
                season = str(game_data.get("season", ""))
                game = parse_game_from_schedule(game_data, season, day_date)

                if not game["nhl_game_id"] or not game["home_team_abbrev"]:
                    continue

                # Upsert game
                await db.execute(
                    text("""
                        INSERT INTO games (
                            nhl_game_id, season, game_type, game_date, start_time_utc,
                            venue, home_team_abbrev, away_team_abbrev,
                            home_score, away_score, game_state, is_completed,
                            created_at, updated_at
                        ) VALUES (
                            :nhl_game_id, :season, :game_type, :game_date, :start_time_utc,
                            :venue, :home_team_abbrev, :away_team_abbrev,
                            :home_score, :away_score, :game_state, :is_completed,
                            NOW(), NOW()
                        )
                        ON CONFLICT (nhl_game_id) DO UPDATE SET
                            home_score = EXCLUDED.home_score,
                            away_score = EXCLUDED.away_score,
                            game_state = EXCLUDED.game_state,
                            is_completed = EXCLUDED.is_completed,
                            updated_at = NOW()
                    """),
                    game
                )
                games_upserted += 1

        await db.commit()
        logger.info("ingested_schedule", date=date_str, games=games_upserted)
        return games_upserted

    finally:
        if close_client:
            await client.close()


async def ingest_schedule_range(
    db: AsyncSession,
    start_date: date,
    end_date: date,
    client: NHLAPIClient | None = None
) -> int:
    """Fetch and store games for a date range."""
    close_client = False
    if client is None:
        client = NHLAPIClient()
        close_client = True

    try:
        total_games = 0
        current_date = start_date

        while current_date <= end_date:
            games = await ingest_schedule_for_date(db, current_date, client)
            total_games += games
            current_date += timedelta(days=7)  # Schedule API returns a week at a time
            await asyncio.sleep(0.3)  # Rate limiting

        return total_games
    finally:
        if close_client:
            await client.close()


async def ingest_player_game_logs(
    db: AsyncSession,
    player_nhl_id: int,
    season: str,
    client: NHLAPIClient | None = None
) -> int:
    """
    Fetch and store game logs for a specific player and season.

    Args:
        player_nhl_id: NHL player ID
        season: Season in format "20252026"

    Returns number of game logs upserted.
    """
    close_client = False
    if client is None:
        client = NHLAPIClient()
        close_client = True

    try:
        # Get internal player_id
        result = await db.execute(
            text("SELECT id FROM players WHERE nhl_id = :nhl_id"),
            {"nhl_id": player_nhl_id}
        )
        row = result.fetchone()
        if not row:
            logger.warning("player_not_found", nhl_id=player_nhl_id)
            return 0

        internal_player_id = row[0]

        # Fetch game log from NHL API
        try:
            game_log_data = await client.get_player_game_log(player_nhl_id, season)
        except Exception as e:
            logger.warning("game_log_fetch_failed", player_id=player_nhl_id, error=str(e))
            return 0

        game_log = game_log_data.get("gameLog", [])
        logs_upserted = 0

        for entry in game_log:
            log = parse_game_log_entry(internal_player_id, entry, season)

            # Upsert game log
            await db.execute(
                text("""
                    INSERT INTO game_logs (
                        player_id, game_id, game_date, season, team_abbrev, opponent, home_away,
                        goals, assists, points, shots, toi, plus_minus, pim,
                        powerplay_goals, powerplay_points, shorthanded_goals, shorthanded_points,
                        game_winning_goals, overtime_goals, shifts, created_at
                    ) VALUES (
                        :player_id, :game_id, :game_date, :season, :team_abbrev, :opponent, :home_away,
                        :goals, :assists, :points, :shots, :toi, :plus_minus, :pim,
                        :powerplay_goals, :powerplay_points, :shorthanded_goals, :shorthanded_points,
                        :game_winning_goals, :overtime_goals, :shifts, NOW()
                    )
                    ON CONFLICT (player_id, game_id) DO UPDATE SET
                        goals = EXCLUDED.goals,
                        assists = EXCLUDED.assists,
                        points = EXCLUDED.points,
                        shots = EXCLUDED.shots,
                        toi = EXCLUDED.toi,
                        plus_minus = EXCLUDED.plus_minus,
                        pim = EXCLUDED.pim,
                        powerplay_goals = EXCLUDED.powerplay_goals,
                        powerplay_points = EXCLUDED.powerplay_points,
                        shorthanded_goals = EXCLUDED.shorthanded_goals,
                        shorthanded_points = EXCLUDED.shorthanded_points,
                        game_winning_goals = EXCLUDED.game_winning_goals,
                        overtime_goals = EXCLUDED.overtime_goals,
                        shifts = EXCLUDED.shifts
                """),
                log
            )
            logs_upserted += 1

        await db.commit()
        return logs_upserted

    finally:
        if close_client:
            await client.close()


async def ingest_all_player_game_logs(
    db: AsyncSession,
    season: str,
    team_abbrev: str | None = None,
    limit: int | None = None
) -> dict[str, int]:
    """
    Ingest game logs for players who have stats in the specified season.

    Args:
        season: Season in format "20252026"
        team_abbrev: Optional team filter
        limit: Max number of players to process

    Returns dict with stats about ingestion.
    """
    # Get list of players who have stats for this season (active players)
    query = """
        SELECT DISTINCT p.nhl_id, p.name, s.team_abbrev
        FROM players p
        JOIN player_season_stats s ON p.id = s.player_id
        WHERE s.season = :season
    """
    params = {"season": season}

    if team_abbrev:
        query += " AND s.team_abbrev = :team_abbrev"
        params["team_abbrev"] = team_abbrev

    query += " ORDER BY p.nhl_id"

    if limit:
        query += " LIMIT :limit"
        params["limit"] = limit

    result = await db.execute(text(query), params)
    players = result.fetchall()

    client = NHLAPIClient()
    stats = {"players_processed": 0, "logs_ingested": 0, "errors": 0}

    try:
        for player in players:
            try:
                logs = await ingest_player_game_logs(db, player.nhl_id, season, client)
                stats["logs_ingested"] += logs
                stats["players_processed"] += 1

                if stats["players_processed"] % 50 == 0:
                    logger.info(
                        "game_log_ingestion_progress",
                        processed=stats["players_processed"],
                        total=len(players),
                        logs=stats["logs_ingested"]
                    )

                # Rate limiting
                await asyncio.sleep(0.2)

            except Exception as e:
                logger.warning("player_game_log_error", player=player.name, error=str(e))
                stats["errors"] += 1
                continue

        logger.info("game_log_ingestion_complete", **stats)
        return stats

    finally:
        await client.close()


async def get_todays_games(db: AsyncSession) -> list[dict]:
    """Get today's scheduled games."""
    today = date.today()

    result = await db.execute(
        text("""
            SELECT
                nhl_game_id, game_date, start_time_utc,
                home_team_abbrev, away_team_abbrev,
                home_score, away_score, game_state, venue
            FROM games
            WHERE game_date = :today
            ORDER BY start_time_utc
        """),
        {"today": today}
    )

    rows = result.fetchall()
    return [
        {
            "game_id": row.nhl_game_id,
            "date": row.game_date.isoformat(),
            "start_time": row.start_time_utc.isoformat() if row.start_time_utc else None,
            "home_team": row.home_team_abbrev,
            "away_team": row.away_team_abbrev,
            "home_score": row.home_score,
            "away_score": row.away_score,
            "state": row.game_state,
            "venue": row.venue,
        }
        for row in rows
    ]


async def refresh_todays_schedule(db: AsyncSession) -> int:
    """Refresh today's schedule from NHL API."""
    client = NHLAPIClient()
    try:
        return await ingest_schedule_for_date(db, date.today(), client)
    finally:
        await client.close()
