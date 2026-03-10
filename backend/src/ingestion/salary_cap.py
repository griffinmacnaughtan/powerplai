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


SPOTRAC_TEAM_SLUGS = {
    "ANA": "anaheim-ducks",
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


async def fetch_team_cap_data_spotrac(team_abbrev: str) -> list[dict]:
    """
    Fetch salary cap data for a team from Spotrac.

    Returns list of player contract records.
    """
    slug = SPOTRAC_TEAM_SLUGS.get(team_abbrev)
    if not slug:
        logger.warning("unknown_team_slug_spotrac", team=team_abbrev)
        return []

    url = f"https://www.spotrac.com/nhl/{slug}/cap/_/year/2025"

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            }
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            contracts = []

            # Spotrac uses table structure with player links
            # Find main roster table
            tables = soup.select('table')

            for table in tables:
                rows = table.select('tbody tr')
                for row in rows:
                    cells = row.select('td')
                    if len(cells) < 2:
                        continue

                    # Look for player name link
                    player_link = row.select_one('a[href*="/nhl/player/"]')
                    if not player_link:
                        continue

                    player_name = player_link.get_text(strip=True)
                    if not player_name:
                        continue

                    # Find cap hit - look for cell with dollar amount
                    cap_hit = None
                    contract_end = None

                    for cell in cells:
                        text = cell.get_text(strip=True)
                        # Cap hit will be something like "$10,500,000"
                        if '$' in text and ',' in text:
                            parsed = parse_cap_hit(text)
                            if parsed and parsed > 50000000:  # > $500k to filter noise
                                cap_hit = parsed
                                break

                    # Try to get contract end year from free agent column
                    fa_cell = row.select_one('td.text-center, td:last-child')
                    if fa_cell:
                        fa_text = fa_cell.get_text(strip=True)
                        year_match = re.search(r'20\d{2}', fa_text)
                        if year_match:
                            contract_end = int(year_match.group())

                    if player_name and cap_hit:
                        contracts.append({
                            "player_name": player_name,
                            "team_abbrev": team_abbrev,
                            "cap_hit_cents": cap_hit,
                            "contract_end": contract_end,
                            "source": "spotrac",
                        })

            logger.debug("fetched_team_contracts_spotrac", team=team_abbrev, count=len(contracts))
            return contracts

        except httpx.HTTPStatusError as e:
            logger.warning("spotrac_fetch_failed", team=team_abbrev, status=e.response.status_code)
            return []
        except Exception as e:
            logger.warning("spotrac_parse_failed", team=team_abbrev, error=str(e))
            return []


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
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            }
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            contracts = []

            # PuckPedia has various table structures - try multiple approaches
            # Approach 1: Look for roster table with player data
            roster_tables = soup.select('.roster-table, .cap-table, table.table')

            for table in roster_tables:
                rows = table.select('tr')
                for row in rows:
                    cells = row.select('td')
                    if len(cells) < 2:
                        continue

                    # Find player link
                    player_link = row.select_one('a[href*="/player/"]')
                    if not player_link:
                        continue

                    player_name = player_link.get_text(strip=True)
                    if not player_name:
                        continue

                    # Look for cap hit - usually has $ sign
                    cap_hit = None
                    contract_end = None

                    for cell in cells:
                        text = cell.get_text(strip=True)
                        if '$' in text:
                            parsed = parse_cap_hit(text)
                            # Filter out very small values (bonuses, etc.)
                            if parsed and parsed > 50000000:  # > $500k
                                cap_hit = parsed
                                break

                    # Look for contract years
                    for cell in cells:
                        text = cell.get_text(strip=True)
                        year_match = re.search(r'20\d{2}', text)
                        if year_match and int(year_match.group()) > 2024:
                            contract_end = int(year_match.group())
                            break

                    if player_name and cap_hit:
                        contracts.append({
                            "player_name": player_name,
                            "team_abbrev": team_abbrev,
                            "cap_hit_cents": cap_hit,
                            "contract_end": contract_end,
                            "source": "puckpedia",
                        })

            # Approach 2: If no contracts found, try broader table search
            if not contracts:
                tables = soup.select('table')
                for table in tables:
                    rows = table.select('tr')
                    for row in rows:
                        cells = row.select('td')
                        if len(cells) < 3:
                            continue

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
                                parsed = parse_cap_hit(cap_cell)
                                if parsed and parsed > 50000000:
                                    contracts.append({
                                        "player_name": player_name,
                                        "team_abbrev": team_abbrev,
                                        "cap_hit_cents": parsed,
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


async def ingest_team_salaries(db: AsyncSession, team_abbrev: str, source: str = "auto") -> dict:
    """
    Ingest salary data for a single team.

    Args:
        db: Database session
        team_abbrev: Team abbreviation (e.g., "TOR")
        source: "puckpedia", "spotrac", or "auto" (tries both)
    """
    contracts = []

    if source in ("puckpedia", "auto"):
        contracts = await fetch_team_cap_data(team_abbrev)

    # Fallback to Spotrac if PuckPedia returned nothing
    if not contracts and source in ("spotrac", "auto"):
        contracts = await fetch_team_cap_data_spotrac(team_abbrev)

    stats = {
        "team": team_abbrev,
        "fetched": len(contracts),
        "matched": 0,
        "updated": 0,
        "source": contracts[0]["source"] if contracts else "none"
    }

    for contract in contracts:
        if not contract.get("cap_hit_cents"):
            continue

        # Try to match player by exact name first
        player_name = contract['player_name']
        result = await db.execute(
            text("""
                SELECT id, name FROM players
                WHERE LOWER(name) = LOWER(:name) AND team_abbrev = :team
                LIMIT 1
            """),
            {"name": player_name, "team": team_abbrev}
        )
        player_row = result.fetchone()

        if not player_row:
            # Try partial match on team
            result = await db.execute(
                text("""
                    SELECT id, name FROM players
                    WHERE name ILIKE :name AND team_abbrev = :team
                    LIMIT 1
                """),
                {"name": f"%{player_name}%", "team": team_abbrev}
            )
            player_row = result.fetchone()

        if not player_row:
            # Try without team filter (for recently traded players)
            result = await db.execute(
                text("""
                    SELECT id, name FROM players
                    WHERE LOWER(name) = LOWER(:name)
                    LIMIT 1
                """),
                {"name": player_name}
            )
            player_row = result.fetchone()

        if player_row:
            stats["matched"] += 1

            # Update player's contract info
            update_params = {
                "id": player_row[0],
                "cap_hit": contract["cap_hit_cents"]
            }

            # Include contract_end if available
            if contract.get("contract_end"):
                await db.execute(
                    text("""
                        UPDATE players SET
                            cap_hit_cents = :cap_hit,
                            contract_expiry = :contract_end,
                            updated_at = NOW()
                        WHERE id = :id
                    """),
                    {**update_params, "contract_end": contract["contract_end"]}
                )
            else:
                await db.execute(
                    text("""
                        UPDATE players SET
                            cap_hit_cents = :cap_hit,
                            updated_at = NOW()
                        WHERE id = :id
                    """),
                    update_params
                )
            stats["updated"] += 1
        else:
            logger.debug("player_not_found_for_salary", player=player_name, team=team_abbrev)

    await db.commit()
    return stats


async def ingest_all_salaries(db: AsyncSession, source: str = "auto") -> dict:
    """
    Ingest salary data for all teams.

    Args:
        db: Database session
        source: "puckpedia", "spotrac", or "auto" (tries both, Spotrac as fallback)
    """
    total_stats = {
        "teams_processed": 0,
        "total_fetched": 0,
        "total_matched": 0,
        "total_updated": 0,
        "errors": [],
        "sources_used": {"puckpedia": 0, "spotrac": 0, "none": 0}
    }

    teams = list(TEAM_SLUGS.keys())
    logger.info("starting_salary_ingestion", teams=len(teams), source=source)

    for team_abbrev in teams:
        try:
            stats = await ingest_team_salaries(db, team_abbrev, source=source)
            total_stats["teams_processed"] += 1
            total_stats["total_fetched"] += stats["fetched"]
            total_stats["total_matched"] += stats["matched"]
            total_stats["total_updated"] += stats["updated"]
            total_stats["sources_used"][stats.get("source", "none")] += 1

            logger.debug("team_salary_processed", team=team_abbrev, **stats)

            # Rate limiting - be respectful (3 seconds between requests)
            await asyncio.sleep(3.0)

        except Exception as e:
            logger.warning("team_salary_failed", team=team_abbrev, error=str(e))
            total_stats["errors"].append(f"{team_abbrev}: {str(e)}")
            continue

    logger.info("salary_ingestion_complete", **total_stats)
    return total_stats


async def export_salaries_to_csv(db: AsyncSession, output_path: str | None = None) -> str:
    """
    Export all player salary data to CSV.

    Args:
        db: Database session
        output_path: Optional path to save CSV. If None, returns as string.

    Returns:
        CSV content as string
    """
    from pathlib import Path
    import csv
    from io import StringIO

    result = await db.execute(
        text("""
            SELECT
                p.name,
                p.team_abbrev,
                p.position,
                p.cap_hit_cents,
                p.contract_expiry,
                p.birth_date,
                s.goals,
                s.assists,
                s.points,
                s.games_played,
                s.xg
            FROM players p
            LEFT JOIN player_season_stats s ON p.id = s.player_id
                AND s.season = (SELECT MAX(season) FROM player_season_stats)
            WHERE p.cap_hit_cents IS NOT NULL
              AND p.cap_hit_cents > 0
            ORDER BY p.cap_hit_cents DESC
        """)
    )

    rows = result.fetchall()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "name", "team", "position", "cap_hit", "contract_end", "birth_date",
        "goals", "assists", "points", "games_played", "xg", "source_date"
    ])

    today = date.today().strftime("%Y-%m")

    for row in rows:
        cap_hit = row.cap_hit_cents // 100 if row.cap_hit_cents else 0
        writer.writerow([
            row.name,
            row.team_abbrev,
            row.position,
            cap_hit,
            row.contract_expiry or "",
            row.birth_date.strftime("%Y-%m-%d") if row.birth_date else "",
            row.goals or 0,
            row.assists or 0,
            row.points or 0,
            row.games_played or 0,
            round(row.xg, 2) if row.xg else 0,
            today
        ])

    csv_content = output.getvalue()

    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(csv_content)
        logger.info("salary_csv_exported", path=str(path), rows=len(rows))

    return csv_content


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


async def refresh_all_salaries(source: str = "auto") -> dict:
    """Refresh all salary data from web sources."""
    async with async_session_maker() as db:
        return await ingest_all_salaries(db, source=source)


async def load_salaries_from_csv(db: AsyncSession, csv_path: str) -> dict:
    """
    Load salary data from a CSV file into the database.

    This is more reliable than web scraping since the CSV can be
    manually curated or downloaded from a trusted source.

    Expected CSV columns: name, team, cap_hit, contract_end (optional)

    Args:
        db: Database session
        csv_path: Path to CSV file

    Returns:
        Dict with import statistics
    """
    import csv
    from pathlib import Path

    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    stats = {"rows_processed": 0, "matched": 0, "updated": 0, "not_found": []}

    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)

        for row in reader:
            stats["rows_processed"] += 1

            player_name = row.get("name", "").strip()
            team = row.get("team", "").strip().upper()
            cap_hit_str = row.get("cap_hit", "0").strip()
            contract_end_str = row.get("contract_end", "").strip()

            if not player_name or not cap_hit_str:
                continue

            # Parse cap hit (could be "10000000" or "10,000,000" or "$10,000,000")
            cap_hit_cents = parse_cap_hit(cap_hit_str)
            if cap_hit_cents is None:
                # Try as plain integer
                try:
                    cap_hit_cents = int(cap_hit_str) * 100
                except ValueError:
                    continue

            # Parse contract end year
            contract_end = None
            if contract_end_str:
                try:
                    contract_end = int(contract_end_str)
                except ValueError:
                    pass

            # Try to match player in database
            result = await db.execute(
                text("""
                    SELECT id FROM players
                    WHERE LOWER(name) = LOWER(:name)
                    LIMIT 1
                """),
                {"name": player_name}
            )
            player_row = result.fetchone()

            if not player_row and team:
                # Try with team filter
                result = await db.execute(
                    text("""
                        SELECT id FROM players
                        WHERE name ILIKE :name AND team_abbrev = :team
                        LIMIT 1
                    """),
                    {"name": f"%{player_name}%", "team": team}
                )
                player_row = result.fetchone()

            if player_row:
                stats["matched"] += 1

                if contract_end:
                    await db.execute(
                        text("""
                            UPDATE players SET
                                cap_hit_cents = :cap_hit,
                                contract_expiry = :contract_end,
                                updated_at = NOW()
                            WHERE id = :id
                        """),
                        {"id": player_row[0], "cap_hit": cap_hit_cents, "contract_end": contract_end}
                    )
                else:
                    await db.execute(
                        text("""
                            UPDATE players SET
                                cap_hit_cents = :cap_hit,
                                updated_at = NOW()
                            WHERE id = :id
                        """),
                        {"id": player_row[0], "cap_hit": cap_hit_cents}
                    )
                stats["updated"] += 1
            else:
                stats["not_found"].append(player_name)

    await db.commit()
    logger.info("csv_salary_import_complete", **{k: v for k, v in stats.items() if k != "not_found"})

    return stats


async def import_salaries_from_data_file() -> dict:
    """Import salaries from the default data/salaries_2025_26.csv file."""
    from pathlib import Path

    csv_path = Path(__file__).parent.parent.parent.parent / "data" / "salaries_2025_26.csv"

    async with async_session_maker() as db:
        return await load_salaries_from_csv(db, str(csv_path))
