"""
Injury data ingestion for PowerplAI predictions.

Sources:
- NHL API roster status (scratches, IR)
- NHL API player landing pages (injury details when available)

Updates injury status for players, tracking:
- IR (Injured Reserve)
- LTIR (Long-Term IR)
- Day-to-Day
- Out (general)
"""
import asyncio
from datetime import date, datetime
from typing import Any
import structlog

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.db.database import async_session_maker
from backend.src.ingestion.nhl_api import NHLAPIClient

logger = structlog.get_logger()

# All NHL team abbreviations
NHL_TEAMS = [
    "ANA", "ARI", "BOS", "BUF", "CGY", "CAR", "CHI", "COL", "CBJ", "DAL",
    "DET", "EDM", "FLA", "LAK", "MIN", "MTL", "NSH", "NJD", "NYI", "NYR",
    "OTT", "PHI", "PIT", "SJS", "SEA", "STL", "TBL", "TOR", "UTA", "VAN",
    "VGK", "WSH", "WPG"
]


async def fetch_team_roster_status(
    client: NHLAPIClient,
    team_abbrev: str,
    season: str
) -> list[dict]:
    """
    Fetch roster and identify players with injury-related statuses.

    The NHL API roster includes player status indicators.
    """
    try:
        roster_data = await client.get_team_roster(team_abbrev, season)

        injured_players = []

        # Process forwards, defensemen, and goalies
        for position_group in ["forwards", "defensemen", "goalies"]:
            players = roster_data.get(position_group, [])

            for player in players:
                player_id = player.get("id")
                first_name = player.get("firstName", {}).get("default", "")
                last_name = player.get("lastName", {}).get("default", "")
                name = f"{first_name} {last_name}".strip()

                # Check for injury indicators in the roster data
                # The API sometimes includes injury status
                injury_status = player.get("injuryStatus")

                if injury_status:
                    injured_players.append({
                        "nhl_id": player_id,
                        "name": name,
                        "team_abbrev": team_abbrev,
                        "status": injury_status,
                        "injury_type": None,  # Not always available
                    })

        return injured_players

    except Exception as e:
        logger.warning("roster_fetch_failed", team=team_abbrev, error=str(e))
        return []


async def fetch_player_injury_details(
    client: NHLAPIClient,
    player_nhl_id: int
) -> dict | None:
    """
    Fetch detailed injury info from player landing page if available.
    """
    try:
        player_data = await client.get_player(player_nhl_id)

        # Check for injury info in landing page
        injury_status = player_data.get("injuryStatus")
        injury_note = player_data.get("injuryNote")

        if injury_status or injury_note:
            return {
                "status": injury_status,
                "description": injury_note,
            }

        return None

    except Exception as e:
        logger.debug("player_injury_fetch_failed", player_id=player_nhl_id, error=str(e))
        return None


async def ingest_injuries(db: AsyncSession, season: str = "20252026") -> dict:
    """
    Ingest injury information for all teams.

    Returns stats about injuries found.
    """
    client = NHLAPIClient()
    stats = {"teams_checked": 0, "injuries_found": 0, "injuries_updated": 0}

    try:
        # First, mark all existing injuries as potentially resolved
        # (we'll re-activate ones that are still injured)
        await db.execute(
            text("UPDATE injuries SET is_active = FALSE WHERE is_active = TRUE")
        )

        for team in NHL_TEAMS:
            try:
                injured_players = await fetch_team_roster_status(client, team, season)
                stats["teams_checked"] += 1

                for player_info in injured_players:
                    stats["injuries_found"] += 1

                    # Get player's internal ID
                    result = await db.execute(
                        text("SELECT id FROM players WHERE nhl_id = :nhl_id"),
                        {"nhl_id": player_info["nhl_id"]}
                    )
                    row = result.fetchone()

                    if not row:
                        # Player not in our database, skip
                        continue

                    internal_id = row[0]

                    # Map injury status to our categories
                    status = _normalize_injury_status(player_info.get("status", ""))

                    # Check if player already has an active injury
                    existing = await db.execute(
                        text("SELECT id FROM injuries WHERE player_id = :player_id AND is_active = TRUE"),
                        {"player_id": internal_id}
                    )
                    existing_row = existing.fetchone()

                    if existing_row:
                        # Update existing injury
                        await db.execute(
                            text("""
                                UPDATE injuries SET
                                    status = :status,
                                    injury_type = :injury_type,
                                    description = :description,
                                    team_abbrev = :team_abbrev,
                                    updated_at = NOW()
                                WHERE id = :id
                            """),
                            {
                                "id": existing_row[0],
                                "status": status,
                                "injury_type": player_info.get("injury_type"),
                                "description": player_info.get("description"),
                                "team_abbrev": team,
                            }
                        )
                    else:
                        # Insert new injury
                        await db.execute(
                            text("""
                                INSERT INTO injuries (
                                    player_id, team_abbrev, status, injury_type, description,
                                    reported_date, is_active, created_at, updated_at
                                ) VALUES (
                                    :player_id, :team_abbrev, :status, :injury_type, :description,
                                    :reported_date, TRUE, NOW(), NOW()
                                )
                            """),
                            {
                                "player_id": internal_id,
                                "team_abbrev": team,
                                "status": status,
                                "injury_type": player_info.get("injury_type"),
                                "description": player_info.get("description"),
                                "reported_date": date.today(),
                            }
                        )
                    stats["injuries_updated"] += 1

                # Rate limiting
                await asyncio.sleep(0.3)

            except Exception as e:
                logger.warning("team_injury_fetch_failed", team=team, error=str(e))
                continue

        await db.commit()
        logger.info("injuries_ingested", **stats)
        return stats

    finally:
        await client.close()


async def fetch_injuries_from_scores_page(client: NHLAPIClient) -> list[dict]:
    """
    Alternative: Try to get injury info from scores/schedule API.
    Some endpoints include injury reports.
    """
    # The NHL API doesn't have a dedicated injuries endpoint,
    # but we can check the score/schedule API for scratches
    try:
        schedule = await client.get_schedule()

        injuries = []
        game_week = schedule.get("gameWeek", [])

        for day_data in game_week:
            games = day_data.get("games", [])
            for game in games:
                # Check for team scratches/injuries in game data
                # This is available closer to game time
                home_scratches = game.get("homeTeam", {}).get("scratches", [])
                away_scratches = game.get("awayTeam", {}).get("scratches", [])

                for scratch in home_scratches + away_scratches:
                    injuries.append({
                        "nhl_id": scratch.get("id"),
                        "name": f"{scratch.get('firstName', {}).get('default', '')} {scratch.get('lastName', {}).get('default', '')}".strip(),
                        "status": "Scratch",
                    })

        return injuries

    except Exception as e:
        logger.debug("scores_page_injury_fetch_failed", error=str(e))
        return []


def _normalize_injury_status(status: str) -> str:
    """Normalize injury status to standard categories."""
    status_lower = status.lower() if status else ""

    if "ir" in status_lower and "lt" in status_lower:
        return "LTIR"
    elif "ir" in status_lower:
        return "IR"
    elif "day" in status_lower or "dtd" in status_lower:
        return "Day-to-Day"
    elif "out" in status_lower:
        return "Out"
    elif "scratch" in status_lower:
        return "Scratch"
    elif status:
        return status
    else:
        return "Unknown"


async def get_active_injuries(db: AsyncSession, team_abbrev: str | None = None) -> list[dict]:
    """
    Get currently active injuries, optionally filtered by team.
    """
    query = """
        SELECT
            i.id, p.name, p.nhl_id, i.team_abbrev, i.status,
            i.injury_type, i.description, i.reported_date
        FROM injuries i
        JOIN players p ON i.player_id = p.id
        WHERE i.is_active = TRUE
    """
    params = {}

    if team_abbrev:
        query += " AND i.team_abbrev = :team_abbrev"
        params["team_abbrev"] = team_abbrev

    query += " ORDER BY i.team_abbrev, p.name"

    result = await db.execute(text(query), params)
    rows = result.fetchall()

    return [
        {
            "id": row.id,
            "player_name": row.name,
            "player_nhl_id": row.nhl_id,
            "team": row.team_abbrev,
            "status": row.status,
            "injury_type": row.injury_type,
            "description": row.description,
            "reported_date": row.reported_date.isoformat() if row.reported_date else None,
        }
        for row in rows
    ]


async def get_team_injury_impact(db: AsyncSession, team_abbrev: str, season: str = "20252026") -> dict:
    """
    Calculate the impact of injuries on a team's lineup.

    Returns info about injured players and their stats impact.
    """
    result = await db.execute(
        text("""
            SELECT
                p.name, p.position, i.status,
                s.goals, s.assists, s.points, s.games_played
            FROM injuries i
            JOIN players p ON i.player_id = p.id
            LEFT JOIN player_season_stats s ON p.id = s.player_id AND s.season = :season
            WHERE i.is_active = TRUE AND i.team_abbrev = :team
            ORDER BY s.points DESC NULLS LAST
        """),
        {"team": team_abbrev, "season": season}
    )
    rows = result.fetchall()

    total_points_lost = 0
    injured_players = []

    for row in rows:
        ppg = row.points / row.games_played if row.games_played and row.games_played > 0 else 0
        injured_players.append({
            "name": row.name,
            "position": row.position,
            "status": row.status,
            "season_points": row.points or 0,
            "ppg": round(ppg, 2),
        })
        total_points_lost += row.points or 0

    return {
        "team": team_abbrev,
        "injured_count": len(injured_players),
        "injured_players": injured_players,
        "total_points_on_ir": total_points_lost,
    }


async def refresh_injuries(season: str = "20252026") -> dict:
    """Refresh all injury data."""
    async with async_session_maker() as db:
        return await ingest_injuries(db, season)
