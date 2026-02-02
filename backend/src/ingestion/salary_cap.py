"""
Salary Cap data ingestion for PowerplAI.

Since CapFriendly was acquired by the NHL and shut down to public access,
we use PuckPedia as the primary source for salary cap data.

This module scrapes player contract information including:
- Cap hit (AAV)
- Contract term
- Contract type (ELC, RFA, UFA)
- Signing bonus breakdown

Note: Web scraping should be done responsibly with rate limiting.
"""
import asyncio
import re
from datetime import date, datetime
from typing import Any
from decimal import Decimal
import structlog

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
from bs4 import BeautifulSoup

from backend.src.db.database import async_session_maker

logger = structlog.get_logger()

PUCKPEDIA_BASE = "https://puckpedia.com"

# NHL team slug mapping for PuckPedia URLs
TEAM_SLUGS = {
    "ANA": "anaheim-ducks",
    "ARI": "arizona-coyotes",
    "BOS": "boston-bruins",
    "BUF": "buffalo-sabres",
    "CGY": "calgary-flames",
    "CAR": "carolina-hurricanes",
    "CHI": "chicago-blackhawks",
    "COL": "colorado-avalanche",
    "CBJ": "columbus-blue-jackets",
    "DAL": "dallas-stars",
    "DET": "detroit-red-wings",
    "EDM": "edmonton-oilers",
    "FLA": "florida-panthers",
    "LAK": "los-angeles-kings",
    "MIN": "minnesota-wild",
    "MTL": "montreal-canadiens",
    "NSH": "nashville-predators",
    "NJD": "new-jersey-devils",
    "NYI": "new-york-islanders",
    "NYR": "new-york-rangers",
    "OTT": "ottawa-senators",
    "PHI": "philadelphia-flyers",
    "PIT": "pittsburgh-penguins",
    "SJS": "san-jose-sharks",
    "SEA": "seattle-kraken",
    "STL": "st-louis-blues",
    "TBL": "tampa-bay-lightning",
    "TOR": "toronto-maple-leafs",
    "UTA": "utah-hockey-club",
    "VAN": "vancouver-canucks",
    "VGK": "vegas-golden-knights",
    "WSH": "washington-capitals",
    "WPG": "winnipeg-jets",
}


def parse_cap_hit(cap_str: str) -> int | None:
    """Parse cap hit string like '$10,500,000' to integer cents."""
    if not cap_str:
        return None

    # Remove $ and commas
    clean = re.sub(r'[$,]', '', cap_str.strip())

    try:
        # Convert to cents to avoid float precision issues
        return int(float(clean) * 100)
    except (ValueError, TypeError):
        return None


def parse_contract_years(years_str: str) -> tuple[int | None, int | None]:
    """
    Parse contract years string like '2023-2028' or '2 years'.

    Returns (start_year, end_year) or (years_remaining, None).
    """
    if not years_str:
        return None, None

    # Try to parse year range like "2023-2028"
    range_match = re.search(r'(\d{4})\s*[-–]\s*(\d{4})', years_str)
    if range_match:
        return int(range_match.group(1)), int(range_match.group(2))

    # Try to parse years remaining like "2 years" or "3 yr"
    years_match = re.search(r'(\d+)\s*(?:year|yr)', years_str, re.IGNORECASE)
    if years_match:
        return int(years_match.group(1)), None

    return None, None


async def fetch_team_cap_data(team_abbrev: str) -> list[dict]:
    """
    Fetch salary cap data for a team from PuckPedia.

    Returns list of player contract records.
    """
    slug = TEAM_SLUGS.get(team_abbrev)
    if not slug:
        logger.warning("unknown_team_slug", team=team_abbrev)
        return []

    url = f"{PUCKPEDIA_BASE}/team/{slug}"

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            # Add user agent to be polite
            headers = {
                "User-Agent": "PowerplAI Hockey Analytics Bot (github.com/powerplai)"
            }
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            contracts = []

            # Find player contract rows
            # PuckPedia structure varies, so we try multiple selectors
            player_rows = soup.select('.player-row, .roster-player, tr[data-player]')

            if not player_rows:
                # Try table rows with player data
                tables = soup.select('table')
                for table in tables:
                    rows = table.select('tr')
                    for row in rows:
                        cells = row.select('td')
                        if len(cells) >= 3:
                            # Try to extract player name and cap hit
                            player_link = row.select_one('a[href*="/player/"]')
                            if player_link:
                                player_name = player_link.get_text(strip=True)

                                # Look for cap hit (usually has $ sign)
                                cap_cell = None
                                for cell in cells:
                                    text = cell.get_text(strip=True)
                                    if '$' in text:
                                        cap_cell = text
                                        break

                                if player_name and cap_cell:
                                    contracts.append({
                                        "player_name": player_name,
                                        "team_abbrev": team_abbrev,
                                        "cap_hit_cents": parse_cap_hit(cap_cell),
                                        "source": "puckpedia",
                                    })

            logger.debug("fetched_team_contracts", team=team_abbrev, count=len(contracts))
            return contracts

        except httpx.HTTPStatusError as e:
            logger.warning("puckpedia_fetch_failed", team=team_abbrev, status=e.response.status_code)
            return []
        except Exception as e:
            logger.warning("puckpedia_parse_failed", team=team_abbrev, error=str(e))
            return []


async def ingest_team_salaries(db: AsyncSession, team_abbrev: str) -> dict:
    """Ingest salary data for a single team."""
    contracts = await fetch_team_cap_data(team_abbrev)

    stats = {"team": team_abbrev, "fetched": len(contracts), "matched": 0, "updated": 0}

    for contract in contracts:
        if not contract.get("cap_hit_cents"):
            continue

        # Try to match player
        result = await db.execute(
            text("""
                SELECT id FROM players
                WHERE name ILIKE :name AND team_abbrev = :team
                LIMIT 1
            """),
            {"name": f"%{contract['player_name']}%", "team": team_abbrev}
        )
        player_row = result.fetchone()

        if not player_row:
            # Try without team filter
            result = await db.execute(
                text("""
                    SELECT id FROM players
                    WHERE name ILIKE :name
                    LIMIT 1
                """),
                {"name": f"%{contract['player_name']}%"}
            )
            player_row = result.fetchone()

        if player_row:
            stats["matched"] += 1

            # Update player's contract info
            await db.execute(
                text("""
                    UPDATE players SET
                        cap_hit_cents = :cap_hit,
                        updated_at = NOW()
                    WHERE id = :id
                """),
                {"id": player_row[0], "cap_hit": contract["cap_hit_cents"]}
            )
            stats["updated"] += 1

    await db.commit()
    return stats


async def ingest_all_salaries(db: AsyncSession) -> dict:
    """Ingest salary data for all teams."""
    total_stats = {
        "teams_processed": 0,
        "total_fetched": 0,
        "total_matched": 0,
        "total_updated": 0,
    }

    for team_abbrev in TEAM_SLUGS.keys():
        try:
            stats = await ingest_team_salaries(db, team_abbrev)
            total_stats["teams_processed"] += 1
            total_stats["total_fetched"] += stats["fetched"]
            total_stats["total_matched"] += stats["matched"]
            total_stats["total_updated"] += stats["updated"]

            # Rate limiting - be respectful
            await asyncio.sleep(2.0)

        except Exception as e:
            logger.warning("team_salary_failed", team=team_abbrev, error=str(e))
            continue

    logger.info("salary_ingestion_complete", **total_stats)
    return total_stats


async def get_team_cap_summary(db: AsyncSession, team_abbrev: str) -> dict:
    """Get salary cap summary for a team."""
    result = await db.execute(
        text("""
            SELECT
                p.name, p.position, p.cap_hit_cents,
                s.goals, s.assists, s.points, s.games_played
            FROM players p
            LEFT JOIN player_season_stats s ON p.id = s.player_id
            WHERE p.team_abbrev = :team
              AND p.cap_hit_cents IS NOT NULL
              AND s.season = (SELECT MAX(season) FROM player_season_stats)
            ORDER BY p.cap_hit_cents DESC
        """),
        {"team": team_abbrev}
    )

    players = []
    total_cap = 0

    for row in result.fetchall():
        cap_hit = row.cap_hit_cents / 100 if row.cap_hit_cents else 0
        total_cap += cap_hit

        # Calculate value metrics
        ppg = row.points / row.games_played if row.games_played and row.games_played > 0 else 0
        cost_per_point = cap_hit / row.points if row.points and row.points > 0 else None

        players.append({
            "name": row.name,
            "position": row.position,
            "cap_hit": cap_hit,
            "cap_hit_formatted": f"${cap_hit:,.0f}",
            "goals": row.goals,
            "assists": row.assists,
            "points": row.points,
            "games_played": row.games_played,
            "ppg": round(ppg, 2),
            "cost_per_point": round(cost_per_point, 0) if cost_per_point else None,
        })

    # NHL salary cap for 2025-26
    SALARY_CAP = 95_500_000

    return {
        "team": team_abbrev,
        "players": players,
        "total_cap_used": total_cap,
        "salary_cap": SALARY_CAP,
        "cap_space": SALARY_CAP - total_cap,
        "cap_used_pct": round(total_cap / SALARY_CAP * 100, 1),
    }


async def get_best_value_players(db: AsyncSession, min_points: int = 30) -> list[dict]:
    """Get players with best value (points per cap dollar)."""
    result = await db.execute(
        text("""
            SELECT
                p.name, p.team_abbrev, p.position, p.cap_hit_cents,
                s.goals, s.assists, s.points, s.games_played
            FROM players p
            JOIN player_season_stats s ON p.id = s.player_id
            WHERE p.cap_hit_cents IS NOT NULL
              AND p.cap_hit_cents > 0
              AND s.points >= :min_points
              AND s.season = (SELECT MAX(season) FROM player_season_stats)
            ORDER BY (s.points::float / p.cap_hit_cents) DESC
            LIMIT 25
        """),
        {"min_points": min_points}
    )

    players = []
    for row in result.fetchall():
        cap_hit = row.cap_hit_cents / 100
        cost_per_point = cap_hit / row.points if row.points > 0 else None
        points_per_million = row.points / (cap_hit / 1_000_000) if cap_hit > 0 else 0

        players.append({
            "name": row.name,
            "team": row.team_abbrev,
            "position": row.position,
            "cap_hit": f"${cap_hit:,.0f}",
            "points": row.points,
            "goals": row.goals,
            "games_played": row.games_played,
            "cost_per_point": f"${cost_per_point:,.0f}" if cost_per_point else "N/A",
            "points_per_million": round(points_per_million, 1),
        })

    return players


async def refresh_all_salaries() -> dict:
    """Refresh all salary data."""
    async with async_session_maker() as db:
        return await ingest_all_salaries(db)
