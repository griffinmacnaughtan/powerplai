"""
Roster sync module - updates player team assignments from NHL API.

This ensures players who have been traded are correctly assigned to their
current team, not the team from their historical stats.
"""
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.ingestion.nhl_api import NHLAPIClient
from backend.src.ingestion.scheduler import get_current_season

logger = structlog.get_logger()

# All 32 NHL team abbreviations
NHL_TEAMS = [
    "ANA", "ARI", "BOS", "BUF", "CGY", "CAR", "CHI", "COL",
    "CBJ", "DAL", "DET", "EDM", "FLA", "LAK", "MIN", "MTL",
    "NSH", "NJD", "NYI", "NYR", "OTT", "PHI", "PIT", "SJS",
    "SEA", "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WSH", "WPG"
]


async def sync_team_rosters(db: AsyncSession, season: str | None = None) -> dict:
    """
    Sync current team rosters from NHL API.

    Updates the team_abbrev field in the players table to reflect
    each player's current team assignment.

    Returns:
        Dict with sync statistics
    """
    if not season:
        current_year = get_current_season()
        season = f"{current_year}{int(current_year) + 1}"

    client = NHLAPIClient()
    stats = {
        "teams_processed": 0,
        "players_updated": 0,
        "players_not_found": 0,
        "errors": []
    }

    try:
        for team_abbrev in NHL_TEAMS:
            try:
                roster_data = await client.get_team_roster(team_abbrev, season)

                # Process forwards, defensemen, and goalies
                for position_group in ["forwards", "defensemen", "goalies"]:
                    players = roster_data.get(position_group, [])
                    # Map position group to position code
                    pos_map = {"forwards": "F", "defensemen": "D", "goalies": "G"}
                    default_pos = pos_map.get(position_group, "F")

                    for player in players:
                        player_id = player.get("id")
                        if not player_id:
                            continue

                        # Extract player info from roster response
                        # NHL roster API includes: firstName, lastName, birthDate, positionCode
                        first_name = player.get("firstName", {})
                        last_name = player.get("lastName", {})
                        name = f"{first_name.get('default', '') if isinstance(first_name, dict) else first_name} {last_name.get('default', '') if isinstance(last_name, dict) else last_name}".strip()
                        birth_date = player.get("birthDate")  # Format: "1996-01-22"
                        position = player.get("positionCode", default_pos)

                        # Update player's current team and bio data in the database
                        result = await db.execute(
                            text("""
                                UPDATE players
                                SET team_abbrev = :team,
                                    position = COALESCE(:position, position),
                                    birth_date = COALESCE(:birth_date::date, birth_date),
                                    name = COALESCE(NULLIF(:name, ''), name),
                                    updated_at = NOW()
                                WHERE nhl_id = :nhl_id
                                RETURNING id
                            """),
                            {
                                "team": team_abbrev,
                                "nhl_id": player_id,
                                "position": position,
                                "birth_date": birth_date,
                                "name": name if name else None
                            }
                        )

                        if result.fetchone():
                            stats["players_updated"] += 1
                        else:
                            stats["players_not_found"] += 1

                stats["teams_processed"] += 1
                logger.debug("roster_synced", team=team_abbrev)

            except Exception as e:
                logger.warning("roster_sync_team_error", team=team_abbrev, error=str(e))
                stats["errors"].append(f"{team_abbrev}: {str(e)}")
                continue

        await db.commit()
        logger.info("roster_sync_complete", **stats)

    finally:
        await client.close()

    return stats


async def sync_single_team_roster(db: AsyncSession, team_abbrev: str, season: str | None = None) -> dict:
    """
    Sync roster for a single team.

    Useful for quick updates before a specific game prediction.
    """
    if not season:
        current_year = get_current_season()
        season = f"{current_year}{int(current_year) + 1}"

    client = NHLAPIClient()
    stats = {"players_updated": 0, "players_not_found": 0}

    try:
        roster_data = await client.get_team_roster(team_abbrev.upper(), season)

        for position_group in ["forwards", "defensemen", "goalies"]:
            players = roster_data.get(position_group, [])
            pos_map = {"forwards": "F", "defensemen": "D", "goalies": "G"}
            default_pos = pos_map.get(position_group, "F")

            for player in players:
                player_id = player.get("id")
                if not player_id:
                    continue

                # Extract player bio data
                first_name = player.get("firstName", {})
                last_name = player.get("lastName", {})
                name = f"{first_name.get('default', '') if isinstance(first_name, dict) else first_name} {last_name.get('default', '') if isinstance(last_name, dict) else last_name}".strip()
                birth_date = player.get("birthDate")
                position = player.get("positionCode", default_pos)

                result = await db.execute(
                    text("""
                        UPDATE players
                        SET team_abbrev = :team,
                            position = COALESCE(:position, position),
                            birth_date = COALESCE(:birth_date::date, birth_date),
                            name = COALESCE(NULLIF(:name, ''), name),
                            updated_at = NOW()
                        WHERE nhl_id = :nhl_id
                        RETURNING id
                    """),
                    {
                        "team": team_abbrev.upper(),
                        "nhl_id": player_id,
                        "position": position,
                        "birth_date": birth_date,
                        "name": name if name else None
                    }
                )

                if result.fetchone():
                    stats["players_updated"] += 1
                else:
                    stats["players_not_found"] += 1

        await db.commit()
        logger.info("single_team_roster_synced", team=team_abbrev, **stats)

    finally:
        await client.close()

    return stats
