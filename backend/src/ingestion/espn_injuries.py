"""
ESPN Injuries API integration for PowerplAI.

ESPN provides a comprehensive, free injuries API with real-time data for all NHL teams.
This is the best available source for injury information.

API Endpoint: https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries
"""
import asyncio
from datetime import date, datetime
from typing import Any
import structlog

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from backend.src.db.database import async_session_maker

logger = structlog.get_logger()

ESPN_INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries"

# Map ESPN team names to NHL abbreviations
ESPN_TEAM_MAP = {
    "Anaheim Ducks": "ANA",
    "Arizona Coyotes": "ARI",
    "Boston Bruins": "BOS",
    "Buffalo Sabres": "BUF",
    "Calgary Flames": "CGY",
    "Carolina Hurricanes": "CAR",
    "Chicago Blackhawks": "CHI",
    "Colorado Avalanche": "COL",
    "Columbus Blue Jackets": "CBJ",
    "Dallas Stars": "DAL",
    "Detroit Red Wings": "DET",
    "Edmonton Oilers": "EDM",
    "Florida Panthers": "FLA",
    "Los Angeles Kings": "LAK",
    "Minnesota Wild": "MIN",
    "Montreal Canadiens": "MTL",
    "Montréal Canadiens": "MTL",
    "Nashville Predators": "NSH",
    "New Jersey Devils": "NJD",
    "New York Islanders": "NYI",
    "New York Rangers": "NYR",
    "Ottawa Senators": "OTT",
    "Philadelphia Flyers": "PHI",
    "Pittsburgh Penguins": "PIT",
    "San Jose Sharks": "SJS",
    "Seattle Kraken": "SEA",
    "St. Louis Blues": "STL",
    "Tampa Bay Lightning": "TBL",
    "Toronto Maple Leafs": "TOR",
    "Utah Hockey Club": "UTA",
    "Utah Mammoth": "UTA",
    "Vancouver Canucks": "VAN",
    "Vegas Golden Knights": "VGK",
    "Washington Capitals": "WSH",
    "Winnipeg Jets": "WPG",
}


async def fetch_espn_injuries() -> list[dict]:
    """
    Fetch all current injuries from ESPN API.

    Returns list of injury records with team and player info.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(ESPN_INJURIES_URL)
            response.raise_for_status()
            data = response.json()

            injuries = []

            for team_data in data.get("injuries", []):
                team_name = team_data.get("displayName", "")
                team_abbrev = ESPN_TEAM_MAP.get(team_name)

                if not team_abbrev:
                    logger.warning("unknown_espn_team", team_name=team_name)
                    continue

                for injury in team_data.get("injuries", []):
                    athlete = injury.get("athlete", {})

                    # Parse injury date
                    injury_date = None
                    date_str = injury.get("date")
                    if date_str:
                        try:
                            injury_date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
                        except (ValueError, TypeError):
                            pass

                    injuries.append({
                        "espn_id": athlete.get("id"),
                        "player_name": athlete.get("displayName"),
                        "first_name": athlete.get("firstName"),
                        "last_name": athlete.get("lastName"),
                        "team_abbrev": team_abbrev,
                        "status": injury.get("status"),
                        "description": injury.get("longComment") or injury.get("shortComment"),
                        "injury_date": injury_date,
                        "position": athlete.get("position", {}).get("abbreviation"),
                    })

            logger.info("fetched_espn_injuries", count=len(injuries))
            return injuries

        except Exception as e:
            logger.error("espn_injuries_fetch_failed", error=str(e))
            return []


def normalize_injury_status(status: str) -> str:
    """Normalize ESPN injury status to our categories."""
    status_lower = status.lower() if status else ""

    if "injured reserve" in status_lower or "ir" == status_lower:
        return "IR"
    elif "long-term" in status_lower or "ltir" in status_lower:
        return "LTIR"
    elif "day-to-day" in status_lower or "dtd" in status_lower:
        return "Day-to-Day"
    elif "out" in status_lower:
        return "Out"
    elif "questionable" in status_lower:
        return "Questionable"
    elif "probable" in status_lower:
        return "Probable"
    elif "suspension" in status_lower:
        return "Suspended"
    elif status:
        return status
    else:
        return "Unknown"


async def ingest_espn_injuries(db: AsyncSession) -> dict:
    """
    Ingest injuries from ESPN API into database.

    Returns stats about ingestion.
    """
    stats = {
        "fetched": 0,
        "matched": 0,
        "updated": 0,
        "new": 0,
        "cleared": 0,
    }

    # Fetch injuries from ESPN
    injuries = await fetch_espn_injuries()
    stats["fetched"] = len(injuries)

    if not injuries:
        return stats

    # Mark all existing injuries as potentially resolved
    await db.execute(
        text("UPDATE injuries SET is_active = FALSE WHERE is_active = TRUE")
    )
    stats["cleared"] = 1  # Flag that we cleared

    for injury in injuries:
        # Try to match player by name
        # First try exact match, then fuzzy
        result = await db.execute(
            text("""
                SELECT id, nhl_id FROM players
                WHERE name ILIKE :name
                LIMIT 1
            """),
            {"name": f"%{injury['last_name']}%"}
        )
        player_row = result.fetchone()

        if not player_row:
            # Try with full name
            result = await db.execute(
                text("""
                    SELECT id, nhl_id FROM players
                    WHERE name ILIKE :name
                    LIMIT 1
                """),
                {"name": f"%{injury['player_name']}%"}
            )
            player_row = result.fetchone()

        if not player_row:
            logger.debug("injury_player_not_found", player=injury['player_name'])
            continue

        stats["matched"] += 1
        internal_id = player_row[0]

        # Normalize status
        status = normalize_injury_status(injury["status"])

        # Check if player already has an active injury record
        existing = await db.execute(
            text("SELECT id FROM injuries WHERE player_id = :player_id"),
            {"player_id": internal_id}
        )
        existing_row = existing.fetchone()

        if existing_row:
            # Update existing record and reactivate
            await db.execute(
                text("""
                    UPDATE injuries SET
                        status = :status,
                        injury_type = :injury_type,
                        description = :description,
                        team_abbrev = :team_abbrev,
                        reported_date = COALESCE(:reported_date, reported_date),
                        is_active = TRUE,
                        updated_at = NOW()
                    WHERE id = :id
                """),
                {
                    "id": existing_row[0],
                    "status": status,
                    "injury_type": injury.get("position"),  # Use position as context
                    "description": injury.get("description"),
                    "team_abbrev": injury["team_abbrev"],
                    "reported_date": injury.get("injury_date"),
                }
            )
            stats["updated"] += 1
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
                    "team_abbrev": injury["team_abbrev"],
                    "status": status,
                    "injury_type": injury.get("position"),
                    "description": injury.get("description"),
                    "reported_date": injury.get("injury_date") or date.today(),
                }
            )
            stats["new"] += 1

    await db.commit()
    logger.info("espn_injuries_ingested", **stats)
    return stats


async def get_injuries_by_team(db: AsyncSession, team_abbrev: str) -> list[dict]:
    """Get current injuries for a specific team."""
    result = await db.execute(
        text("""
            SELECT
                p.name, p.nhl_id, i.status, i.injury_type,
                i.description, i.reported_date
            FROM injuries i
            JOIN players p ON i.player_id = p.id
            WHERE i.team_abbrev = :team AND i.is_active = TRUE
            ORDER BY i.reported_date DESC
        """),
        {"team": team_abbrev}
    )

    return [
        {
            "player_name": row.name,
            "player_nhl_id": row.nhl_id,
            "status": row.status,
            "injury_type": row.injury_type,
            "description": row.description,
            "reported_date": row.reported_date.isoformat() if row.reported_date else None,
        }
        for row in result.fetchall()
    ]


async def get_all_injuries(db: AsyncSession) -> dict:
    """Get all current injuries grouped by team."""
    result = await db.execute(
        text("""
            SELECT
                i.team_abbrev, p.name, p.nhl_id, i.status,
                i.description, i.reported_date
            FROM injuries i
            JOIN players p ON i.player_id = p.id
            WHERE i.is_active = TRUE
            ORDER BY i.team_abbrev, i.reported_date DESC
        """)
    )

    injuries_by_team = {}
    total_count = 0

    for row in result.fetchall():
        team = row.team_abbrev
        if team not in injuries_by_team:
            injuries_by_team[team] = []

        injuries_by_team[team].append({
            "player_name": row.name,
            "player_nhl_id": row.nhl_id,
            "status": row.status,
            "description": row.description,
            "reported_date": row.reported_date.isoformat() if row.reported_date else None,
        })
        total_count += 1

    return {
        "total_injuries": total_count,
        "teams_affected": len(injuries_by_team),
        "injuries_by_team": injuries_by_team,
    }


async def refresh_espn_injuries() -> dict:
    """Refresh all injury data from ESPN."""
    async with async_session_maker() as db:
        return await ingest_espn_injuries(db)
