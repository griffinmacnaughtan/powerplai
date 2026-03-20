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


async def ingest_game_boxscore(
    db: AsyncSession,
    game_id: int,
    home_team: str,
    away_team: str,
    game_date: date,
    season: str,
    client: NHLAPIClient,
) -> int:
    """
    Fetch boxscore for a completed game and upsert player game logs.
    Returns number of player log rows upserted.
    """
    try:
        data = await client.get_game_boxscore(game_id)
    except Exception as e:
        logger.warning("boxscore_fetch_failed", game_id=game_id, error=str(e))
        return 0

    player_by_game = data.get("playerByGameStats", {})
    rows_upserted = 0

    for side, team_abbrev in [("awayTeam", away_team), ("homeTeam", home_team)]:
        opponent = home_team if side == "awayTeam" else away_team
        home_away = "away" if side == "awayTeam" else "home"
        side_data = player_by_game.get(side, {})

        skaters = (
            side_data.get("forwards", [])
            + side_data.get("defense", [])
        )

        for p in skaters:
            nhl_player_id = p.get("playerId")
            if not nhl_player_id:
                continue

            # Ensure player row exists
            await db.execute(
                text("""
                    INSERT INTO players (nhl_id, name, team_abbrev, created_at, updated_at)
                    VALUES (:nhl_id, :name, :team, NOW(), NOW())
                    ON CONFLICT (nhl_id) DO NOTHING
                """),
                {
                    "nhl_id": nhl_player_id,
                    "name": (p.get("name") or {}).get("default", f"Player {nhl_player_id}"),
                    "team": team_abbrev,
                },
            )
            player_row = (await db.execute(
                text("SELECT id FROM players WHERE nhl_id = :nhl_id"),
                {"nhl_id": nhl_player_id},
            )).fetchone()
            if not player_row:
                continue

            goals = p.get("goals", 0) or 0
            assists = p.get("assists", 0) or 0
            toi_str = p.get("toi", "0:00") or "0:00"

            await db.execute(
                text("""
                    INSERT INTO game_logs (
                        player_id, game_id, game_date, season,
                        team_abbrev, opponent, home_away,
                        goals, assists, points, shots, toi,
                        plus_minus, pim,
                        powerplay_goals, powerplay_points,
                        shorthanded_goals, shorthanded_points,
                        game_winning_goals, overtime_goals, shifts,
                        created_at
                    ) VALUES (
                        :player_id, :game_id, :game_date, :season,
                        :team, :opponent, :home_away,
                        :goals, :assists, :points, :shots, :toi,
                        :plus_minus, :pim,
                        :ppg, :ppp,
                        :shg, :shp,
                        :gwg, :otg, :shifts,
                        NOW()
                    )
                    ON CONFLICT (player_id, game_id) DO UPDATE SET
                        goals = EXCLUDED.goals,
                        assists = EXCLUDED.assists,
                        points = EXCLUDED.points,
                        shots = EXCLUDED.shots,
                        toi = EXCLUDED.toi
                """),
                {
                    "player_id": player_row[0],
                    "game_id": game_id,
                    "game_date": game_date,
                    "season": season,
                    "team": team_abbrev,
                    "opponent": opponent,
                    "home_away": home_away,
                    "goals": goals,
                    "assists": assists,
                    "points": goals + assists,
                    "shots": p.get("shots", p.get("sog", 0)) or 0,
                    "toi": _parse_toi(toi_str),
                    "plus_minus": p.get("plusMinus", 0) or 0,
                    "pim": p.get("pim", 0) or 0,
                    "ppg": p.get("powerPlayGoals", 0) or 0,
                    "ppp": p.get("powerPlayPoints", 0) or 0,
                    "shg": p.get("shorthandedGoals", 0) or 0,
                    "shp": p.get("shorthandedPoints", 0) or 0,
                    "gwg": p.get("gameWinningGoals", 0) or 0,
                    "otg": p.get("otGoals", 0) or 0,
                    "shifts": p.get("shifts"),
                },
            )
            rows_upserted += 1

    if rows_upserted:
        await db.commit()
    logger.info("boxscore_ingested", game_id=game_id, players=rows_upserted)
    return rows_upserted


async def ingest_recent_games(
    db: AsyncSession,
    days_back: int = 7,
) -> dict:
    """
    Backfill schedule rows and player box scores for the last N days.

    On every startup this ensures:
    - games table has all recent completed games with final scores
    - game_logs table has player box scores for those games (for parlay grading,
      "who played yesterday", recent form analysis, etc.)
    """
    from backend.src.ingestion.scheduler import get_current_season
    season_year = int(get_current_season())
    season = f"{season_year}{season_year + 1}"

    client = NHLAPIClient()
    results = {"schedule_games": 0, "boxscores_ingested": 0, "errors": []}

    try:
        today = date.today()
        start = today - timedelta(days=days_back)

        # 1. Ingest schedules for the window (also updates final scores)
        current = start
        while current <= today:
            try:
                n = await ingest_schedule_for_date(db, current, client)
                results["schedule_games"] += n
            except Exception as e:
                results["errors"].append(f"schedule {current}: {str(e)}")
            # NHL schedule API returns a week at a time - step by 7 days
            current += timedelta(days=7)

        # 2. Find completed games in the window with no box scores yet
        missing = await db.execute(
            text("""
                SELECT g.nhl_game_id, g.home_team_abbrev, g.away_team_abbrev,
                       g.game_date, g.game_state
                FROM games g
                WHERE g.game_date BETWEEN :start AND :yesterday
                  AND g.game_state IN ('FINAL', 'OFF')
                  AND NOT EXISTS (
                      SELECT 1 FROM game_logs gl WHERE gl.game_id = g.nhl_game_id
                  )
                ORDER BY g.game_date DESC
            """),
            {"start": start, "yesterday": today - timedelta(days=1)},
        )
        games_needing_boxscores = missing.fetchall()
        logger.info("boxscores_needed", count=len(games_needing_boxscores))

        for row in games_needing_boxscores:
            try:
                n = await ingest_game_boxscore(
                    db,
                    game_id=row.nhl_game_id,
                    home_team=row.home_team_abbrev,
                    away_team=row.away_team_abbrev,
                    game_date=row.game_date,
                    season=season,
                    client=client,
                )
                results["boxscores_ingested"] += n
                await asyncio.sleep(0.2)  # be polite to the API
            except Exception as e:
                results["errors"].append(f"boxscore {row.nhl_game_id}: {str(e)}")

    finally:
        await client.close()

    logger.info("recent_games_ingested", **results)
    return results


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
