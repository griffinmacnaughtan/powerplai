"""
Roster sync module - updates player team assignments from NHL API.

This ensures players who have been traded are correctly assigned to their
current team, not the team from their historical stats.
"""
import asyncio
from datetime import date as date_type
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.ingestion.nhl_api import NHLAPIClient
from backend.src.ingestion.scheduler import get_current_season

logger = structlog.get_logger()

# All 32 NHL team abbreviations
NHL_TEAMS = [
    "ANA", "BOS", "BUF", "CGY", "CAR", "CHI", "COL",
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
        "players_added": 0,
        "players_unassigned": 0,
        "errors": []
    }
    # Rookies and off-season arrivals who aren't in the players table yet
    # are inserted rather than skipped; players no longer with an NHL team
    # (unsigned, retired, sent down) are unassigned so they stop showing up
    # in their old team's predictions.
    seen_nhl_ids: set[int] = set()

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
                        birth_date_str = player.get("birthDate")  # Format: "1996-01-22"
                        birth_date = date_type.fromisoformat(birth_date_str) if birth_date_str else None
                        position = player.get("positionCode", default_pos)

                        # Update player's current team and bio data in the database
                        result = await db.execute(
                            text("""
                                INSERT INTO players (nhl_id, name, team_abbrev, position, birth_date, created_at, updated_at)
                                VALUES (:nhl_id, COALESCE(NULLIF(:name, ''), 'Unknown'), :team, :position, :birth_date, NOW(), NOW())
                                ON CONFLICT (nhl_id) DO UPDATE
                                SET team_abbrev = EXCLUDED.team_abbrev,
                                    position = COALESCE(EXCLUDED.position, players.position),
                                    birth_date = COALESCE(EXCLUDED.birth_date, players.birth_date),
                                    name = COALESCE(NULLIF(:name, ''), players.name),
                                    updated_at = NOW()
                                RETURNING id, (xmax = 0) AS inserted
                            """),
                            {
                                "team": team_abbrev,
                                "nhl_id": player_id,
                                "position": position,
                                "birth_date": birth_date,
                                "name": name if name else None
                            }
                        )

                        upserted = result.fetchone()
                        if upserted and upserted.inserted:
                            stats["players_added"] += 1
                        elif upserted:
                            stats["players_updated"] += 1
                        else:
                            stats["players_not_found"] += 1
                        seen_nhl_ids.add(player_id)

                stats["teams_processed"] += 1
                logger.debug("roster_synced", team=team_abbrev)

            except Exception as e:
                logger.warning("roster_sync_team_error", team=team_abbrev, error=str(e))
                stats["errors"].append(f"{team_abbrev}: {str(e)}")
                continue

        # Roster endpoints omit injured-reserve players, so "missing from
        # every roster" isn't proof a player left. After a clean sync, ask
        # the player endpoint for each missing player's current team and
        # apply that (NULL only when the NHL lists no current team).
        if stats["teams_processed"] == len(NHL_TEAMS) and len(seen_nhl_ids) >= len(NHL_TEAMS) * 20:
            await _reconcile_unrostered(db, client, seen_nhl_ids, stats)

        await db.commit()
        logger.info("roster_sync_complete", **{k: v for k, v in stats.items() if k != "errors"}, errors=len(stats["errors"]))

    finally:
        await client.close()

    return stats


async def _reconcile_unrostered(
    db: AsyncSession,
    client: NHLAPIClient,
    seen_nhl_ids: set[int],
    stats: dict,
) -> None:
    result = await db.execute(
        text("""
            SELECT nhl_id, team_abbrev FROM players
            WHERE team_abbrev IS NOT NULL AND NOT (nhl_id = ANY(:seen))
        """),
        {"seen": list(seen_nhl_ids)},
    )
    missing = result.fetchall()
    semaphore = asyncio.Semaphore(8)

    async def current_team(nhl_id: int) -> tuple[int, str | None, bool]:
        async with semaphore:
            try:
                data = await client.get_player(nhl_id)
            except Exception as e:
                logger.debug("roster_reconcile_lookup_failed", nhl_id=nhl_id, error=str(e))
                return nhl_id, None, False
        team = data.get("currentTeamAbbrev") if data.get("isActive", True) else None
        return nhl_id, team, True

    lookups = await asyncio.gather(*(current_team(row.nhl_id) for row in missing))
    old_team = {row.nhl_id: row.team_abbrev for row in missing}
    for nhl_id, team, ok in lookups:
        if not ok or team == old_team[nhl_id]:
            continue  # lookup failed or unchanged (e.g. on IR) - leave as is
        await db.execute(
            text("UPDATE players SET team_abbrev = :team, updated_at = NOW() WHERE nhl_id = :nhl_id"),
            {"team": team, "nhl_id": nhl_id},
        )
        if team is None:
            stats["players_unassigned"] += 1
        else:
            stats["players_updated"] += 1


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
                birth_date_str = player.get("birthDate")
                birth_date = date_type.fromisoformat(birth_date_str) if birth_date_str else None
                position = player.get("positionCode", default_pos)

                result = await db.execute(
                    text("""
                        INSERT INTO players (nhl_id, name, team_abbrev, position, birth_date, created_at, updated_at)
                        VALUES (:nhl_id, COALESCE(NULLIF(:name, ''), 'Unknown'), :team, :position, :birth_date, NOW(), NOW())
                        ON CONFLICT (nhl_id) DO UPDATE
                        SET team_abbrev = EXCLUDED.team_abbrev,
                            position = COALESCE(EXCLUDED.position, players.position),
                            birth_date = COALESCE(EXCLUDED.birth_date, players.birth_date),
                            name = COALESCE(NULLIF(:name, ''), players.name),
                            updated_at = NOW()
                        RETURNING id, (xmax = 0) AS inserted
                    """),
                    {
                        "team": team_abbrev.upper(),
                        "nhl_id": player_id,
                        "position": position,
                        "birth_date": birth_date,
                        "name": name if name else None
                    }
                )

                upserted = result.fetchone()
                if upserted and upserted.inserted:
                    stats["players_added"] = stats.get("players_added", 0) + 1
                elif upserted:
                    stats["players_updated"] += 1
                else:
                    stats["players_not_found"] += 1

        await db.commit()
        logger.info("single_team_roster_synced", team=team_abbrev, **stats)

    finally:
        await client.close()

    return stats
