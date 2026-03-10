"""
Olympic Hockey Data Ingestion - Milan Cortina 2026.

Fetches Olympic hockey statistics from ESPN, IIHF, and official Olympic sources.
Links Olympic performance to existing NHL player records where possible.

IMPORTANT: Olympic predictions require different methodology than NHL:
- Short tournament format (4-5 group games + knockouts)
- Mixed player sources (NHL, KHL, SHL, Liiga)
- Tournament pressure dynamics
- National team chemistry (new linemates)
- Goaltending weighted 2x higher than NHL model
- Historical international performance matters
"""
import re
import asyncio
import structlog
from datetime import date, datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.db.database import async_session_maker

logger = structlog.get_logger()

# ESPN Olympics Hockey endpoints
ESPN_OLYMPICS_BASE = "https://www.espn.com/olympics/hockey/men"
ESPN_STANDINGS = f"{ESPN_OLYMPICS_BASE}/standings"
ESPN_STATS = f"{ESPN_OLYMPICS_BASE}/stats"
ESPN_SCHEDULE = f"{ESPN_OLYMPICS_BASE}/schedule"

# ESPN API endpoints (more reliable than scraping)
ESPN_API_BASE = "https://site.api.espn.com/apis/site/v2/sports/hockey"
ESPN_OLYMPICS_API = f"{ESPN_API_BASE}/nhl/scoreboard"  # During Olympics, check for olympic games
ESPN_OLYMPICS_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/hockey/olympics-mens/scoreboard"
ESPN_OLYMPICS_STANDINGS_API = "https://site.api.espn.com/apis/site/v2/sports/hockey/olympics-mens/standings"
ESPN_OLYMPICS_LEADERS_API = "https://site.api.espn.com/apis/site/v2/sports/hockey/olympics-mens/leaders"

# Additional data sources
IIHF_BASE = "https://www.iihf.com"
IIHF_GAME_CENTER = "https://www.iihf.com/en/events/2026/om"
OLYMPICS_OFFICIAL = "https://olympics.com/en/olympic-games/milano-cortina-2026/sports/ice-hockey"
OLYMPICS_API = "https://olympics.com/OG2026/data/CIF_LIVE/ice-hockey"

# Cache for Olympic data (refreshed periodically)
_olympic_cache = {
    "data": None,
    "last_updated": None,
    "cache_ttl_minutes": 15,  # Refresh every 15 minutes during tournament
}


@dataclass
class OlympicPlayer:
    """Olympic player statistics."""
    name: str
    country: str
    country_code: str = ""
    games_played: int = 0
    goals: int = 0
    assists: int = 0
    points: int = 0
    plus_minus: int = 0
    pim: int = 0
    shots: int = 0
    ppg: float = 0.0  # Points per game in Olympics
    # Link to NHL player if exists
    nhl_player_id: Optional[int] = None
    nhl_team: Optional[str] = None
    nhl_ppg: Optional[float] = None  # NHL points per game for comparison
    # League source (NHL, KHL, SHL, etc.)
    primary_league: str = "NHL"
    # International experience
    prior_olympics: int = 0
    world_championships: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "country": self.country,
            "country_code": self.country_code,
            "games_played": self.games_played,
            "goals": self.goals,
            "assists": self.assists,
            "points": self.points,
            "plus_minus": self.plus_minus,
            "ppg": round(self.ppg, 2),
            "nhl_player_id": self.nhl_player_id,
            "nhl_team": self.nhl_team,
            "nhl_ppg": round(self.nhl_ppg, 2) if self.nhl_ppg else None,
            "primary_league": self.primary_league,
            "prior_olympics": self.prior_olympics,
        }


@dataclass
class OlympicGoalie:
    """Olympic goalie statistics."""
    name: str
    country: str
    country_code: str = ""
    games_played: int = 0
    games_started: int = 0
    wins: int = 0
    losses: int = 0
    gaa: float = 0.0
    save_pct: float = 0.0
    saves: int = 0
    goals_against: int = 0
    shutouts: int = 0
    toi: float = 0.0  # Minutes played
    # Link to NHL player
    nhl_player_id: Optional[int] = None
    nhl_team: Optional[str] = None
    nhl_save_pct: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "country": self.country,
            "country_code": self.country_code,
            "games_played": self.games_played,
            "games_started": self.games_started,
            "wins": self.wins,
            "losses": self.losses,
            "gaa": round(self.gaa, 2),
            "save_pct": round(self.save_pct, 3),
            "shutouts": self.shutouts,
            "nhl_team": self.nhl_team,
            "nhl_save_pct": round(self.nhl_save_pct, 3) if self.nhl_save_pct else None,
        }


@dataclass
class OlympicTeam:
    """Olympic team standings."""
    country: str
    country_code: str
    group: str
    games_played: int = 0
    wins: int = 0
    losses: int = 0
    ot_wins: int = 0
    ot_losses: int = 0
    points: int = 0
    goals_for: int = 0
    goals_against: int = 0
    goal_diff: int = 0
    # Computed metrics
    strength_rating: float = 0.0  # Aggregate roster strength
    nhl_player_count: int = 0  # How many NHL players on roster

    def to_dict(self) -> dict:
        return {
            "country": self.country,
            "country_code": self.country_code,
            "group": self.group,
            "games_played": self.games_played,
            "wins": self.wins,
            "losses": self.losses,
            "ot_wins": self.ot_wins,
            "ot_losses": self.ot_losses,
            "points": self.points,
            "goals_for": self.goals_for,
            "goals_against": self.goals_against,
            "goal_diff": self.goal_diff,
            "strength_rating": round(self.strength_rating, 1),
            "nhl_player_count": self.nhl_player_count,
        }


@dataclass
class OlympicGame:
    """Olympic game result/schedule."""
    game_id: str
    game_date: date
    game_time: Optional[str] = None
    home_country: str = ""
    away_country: str = ""
    home_country_code: str = ""
    away_country_code: str = ""
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    status: str = "scheduled"  # scheduled, live, final
    period: Optional[str] = None
    round: str = "group"  # group, quarterfinal, semifinal, bronze, gold
    venue: str = ""

    def to_dict(self) -> dict:
        return {
            "game_id": self.game_id,
            "game_date": self.game_date.isoformat(),
            "game_time": self.game_time,
            "home_country": self.home_country,
            "away_country": self.away_country,
            "home_country_code": self.home_country_code,
            "away_country_code": self.away_country_code,
            "home_score": self.home_score,
            "away_score": self.away_score,
            "status": self.status,
            "round": self.round,
            "venue": self.venue,
        }


@dataclass
class OlympicData:
    """Complete Olympic hockey data."""
    tournament: str
    last_updated: str
    tournament_status: str = "in_progress"  # upcoming, in_progress, completed
    current_round: str = "group"  # group, quarterfinal, semifinal, medal
    standings: list[OlympicTeam] = field(default_factory=list)
    skater_leaders: list[OlympicPlayer] = field(default_factory=list)
    goalie_leaders: list[OlympicGoalie] = field(default_factory=list)
    schedule: list[OlympicGame] = field(default_factory=list)
    # Tournament metadata
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    games_played: int = 0
    games_remaining: int = 0

    def to_dict(self) -> dict:
        return {
            "tournament": self.tournament,
            "last_updated": self.last_updated,
            "tournament_status": self.tournament_status,
            "current_round": self.current_round,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "games_played": self.games_played,
            "games_remaining": self.games_remaining,
            "standings": [t.to_dict() for t in self.standings],
            "skater_leaders": [p.to_dict() for p in self.skater_leaders],
            "goalie_leaders": [g.to_dict() for g in self.goalie_leaders],
            "schedule": [g.to_dict() for g in self.schedule],
        }


# Country code mapping (expanded)
COUNTRY_CODES = {
    "Canada": "CAN",
    "USA": "USA",
    "United States": "USA",
    "Sweden": "SWE",
    "Finland": "FIN",
    "Russia": "RUS",
    "ROC": "ROC",
    "Czechia": "CZE",
    "Czech Republic": "CZE",
    "Switzerland": "SUI",
    "Germany": "GER",
    "Slovakia": "SVK",
    "Latvia": "LAT",
    "Denmark": "DEN",
    "France": "FRA",
    "Italy": "ITA",
    "Norway": "NOR",
    "Austria": "AUT",
    "Slovenia": "SLO",
    "Kazakhstan": "KAZ",
    "Belarus": "BLR",
    "China": "CHN",
    "South Korea": "KOR",
    "Korea": "KOR",
    "Japan": "JPN",
    "Great Britain": "GBR",
    "Hungary": "HUN",
    "Poland": "POL",
}

# Reverse mapping for code to country
CODE_TO_COUNTRY = {v: k for k, v in COUNTRY_CODES.items()}


def get_country_code(country: str) -> str:
    """Get country code from country name."""
    if len(country) == 3:
        return country.upper()
    return COUNTRY_CODES.get(country, country[:3].upper())


def get_country_name(code: str) -> str:
    """Get country name from code."""
    return CODE_TO_COUNTRY.get(code.upper(), code)


async def fetch_espn_article_stats() -> dict | None:
    """
    Fetch Olympic stats from ESPN's article page.

    NOTE: ESPN's HTML structure is complex and changes frequently.
    This scraper is unreliable - if it returns garbage data, we fall back
    to hardcoded CURRENT_OLYMPIC_DATA instead.

    Returns None to skip and use hardcoded data (more reliable).
    """
    # DISABLED: The HTML parsing was returning corrupted data like:
    #   {"name": "3", "country": "2", ...}
    # Until we can properly parse ESPN's dynamic content, use hardcoded data.
    logger.debug("espn_article_scraper_disabled_using_hardcoded_data")
    return None


async def fetch_live_olympic_data() -> OlympicData:
    """
    Fetch live Olympic hockey data from multiple sources.

    Tries sources in order of reliability:
    1. ESPN API (most reliable, structured JSON)
    2. ESPN web scraping (fallback)
    3. IIHF website (additional source)
    4. Hardcoded data (last resort)
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    data = OlympicData(
        tournament="Milano Cortina 2026",
        last_updated=datetime.utcnow().isoformat(),
        start_date=date(2026, 2, 8),
        end_date=date(2026, 2, 22),
    )

    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        # Try ESPN API first (structured JSON)
        try:
            standings_data = await _fetch_espn_api_standings(client)
            if standings_data:
                data.standings = standings_data
                logger.info("olympic_standings_from_espn_api", teams=len(standings_data))
        except Exception as e:
            logger.warning("espn_api_standings_failed", error=str(e))

        # Try ESPN API for scoreboard/schedule
        try:
            schedule_data = await _fetch_espn_api_scoreboard(client)
            if schedule_data:
                data.schedule = schedule_data
                data.games_played = sum(1 for g in schedule_data if g.status == "final")
                data.games_remaining = sum(1 for g in schedule_data if g.status in ("scheduled", "live"))
                logger.info("olympic_schedule_from_espn_api", games=len(schedule_data))
        except Exception as e:
            logger.warning("espn_api_schedule_failed", error=str(e))

        # Try ESPN API for leaders
        try:
            skaters, goalies = await _fetch_espn_api_leaders(client)
            if skaters:
                data.skater_leaders = skaters
            if goalies:
                data.goalie_leaders = goalies
            logger.info("olympic_leaders_from_espn_api", skaters=len(skaters), goalies=len(goalies))
        except Exception as e:
            logger.warning("espn_api_leaders_failed", error=str(e))

        # If API failed, try web scraping
        if not data.standings:
            try:
                scraped = await fetch_espn_olympic_stats()
                if scraped.standings:
                    data.standings = scraped.standings
                if scraped.skater_leaders:
                    data.skater_leaders = scraped.skater_leaders
                if scraped.goalie_leaders:
                    data.goalie_leaders = scraped.goalie_leaders
                if scraped.schedule:
                    data.schedule = scraped.schedule
            except Exception as e:
                logger.warning("espn_scraping_failed", error=str(e))

    return data


async def _fetch_espn_api_standings(client: httpx.AsyncClient) -> list[OlympicTeam]:
    """Fetch standings from ESPN API."""
    try:
        resp = await client.get(ESPN_OLYMPICS_STANDINGS_API)
        if resp.status_code != 200:
            return []

        data = resp.json()
        teams = []

        # Parse ESPN API response structure
        for group in data.get("children", []):
            group_name = group.get("name", "A")[-1]  # Extract group letter

            for standing in group.get("standings", {}).get("entries", []):
                team_data = standing.get("team", {})
                stats = {s["name"]: s["value"] for s in standing.get("stats", [])}

                teams.append(OlympicTeam(
                    country=team_data.get("displayName", "Unknown"),
                    country_code=team_data.get("abbreviation", "UNK"),
                    group=group_name,
                    games_played=int(stats.get("gamesPlayed", 0)),
                    wins=int(stats.get("wins", 0)),
                    losses=int(stats.get("losses", 0)),
                    ot_wins=int(stats.get("otWins", 0)),
                    ot_losses=int(stats.get("otLosses", 0)),
                    points=int(stats.get("points", 0)),
                    goals_for=int(stats.get("pointsFor", 0)),
                    goals_against=int(stats.get("pointsAgainst", 0)),
                ))

        return teams
    except Exception as e:
        logger.warning("espn_api_standings_parse_error", error=str(e))
        return []


async def _fetch_espn_api_scoreboard(client: httpx.AsyncClient) -> list[OlympicGame]:
    """Fetch today's games and recent results from ESPN API."""
    try:
        resp = await client.get(ESPN_OLYMPICS_SCOREBOARD)
        if resp.status_code != 200:
            return []

        data = resp.json()
        games = []

        for event in data.get("events", []):
            competition = event.get("competitions", [{}])[0]
            competitors = competition.get("competitors", [])

            if len(competitors) < 2:
                continue

            home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
            away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

            status_type = competition.get("status", {}).get("type", {})
            status = "scheduled"
            if status_type.get("completed"):
                status = "final"
            elif status_type.get("state") == "in":
                status = "live"

            # Parse date
            game_date_str = event.get("date", "")
            try:
                game_date = datetime.fromisoformat(game_date_str.replace("Z", "+00:00")).date()
            except Exception:
                game_date = date.today()

            # Determine round from event name
            event_name = event.get("name", "").lower()
            game_round = "group"
            if "quarterfinal" in event_name:
                game_round = "quarterfinal"
            elif "semifinal" in event_name:
                game_round = "semifinal"
            elif "bronze" in event_name:
                game_round = "bronze"
            elif "gold" in event_name or "final" in event_name:
                game_round = "gold"

            games.append(OlympicGame(
                game_id=str(event.get("id", "")),
                game_date=game_date,
                game_time=game_date_str,
                home_country=home.get("team", {}).get("displayName", ""),
                away_country=away.get("team", {}).get("displayName", ""),
                home_country_code=home.get("team", {}).get("abbreviation", ""),
                away_country_code=away.get("team", {}).get("abbreviation", ""),
                home_score=int(home.get("score", 0)) if status != "scheduled" else None,
                away_score=int(away.get("score", 0)) if status != "scheduled" else None,
                status=status,
                round=game_round,
                venue=competition.get("venue", {}).get("fullName", ""),
            ))

        return games
    except Exception as e:
        logger.warning("espn_api_scoreboard_parse_error", error=str(e))
        return []


async def _fetch_espn_api_leaders(client: httpx.AsyncClient) -> tuple[list[OlympicPlayer], list[OlympicGoalie]]:
    """Fetch scoring and goalie leaders from ESPN API."""
    skaters = []
    goalies = []

    try:
        resp = await client.get(ESPN_OLYMPICS_LEADERS_API)
        if resp.status_code != 200:
            return skaters, goalies

        data = resp.json()

        for category in data.get("leaders", []):
            cat_name = category.get("name", "").lower()

            for leader in category.get("leaders", []):
                athlete = leader.get("athlete", {})
                team = athlete.get("team", {})
                stats = {s["name"]: s["value"] for s in leader.get("statistics", [])}

                if "goaltending" in cat_name or "saves" in cat_name:
                    goalies.append(OlympicGoalie(
                        name=athlete.get("displayName", ""),
                        country=team.get("displayName", ""),
                        country_code=team.get("abbreviation", ""),
                        games_played=int(stats.get("gamesPlayed", 0)),
                        wins=int(stats.get("wins", 0)),
                        losses=int(stats.get("losses", 0)),
                        gaa=float(stats.get("goalsAgainstAverage", 0)),
                        save_pct=float(stats.get("savePct", 0)),
                        saves=int(stats.get("saves", 0)),
                        shutouts=int(stats.get("shutouts", 0)),
                    ))
                else:
                    gp = int(stats.get("gamesPlayed", 1)) or 1
                    pts = int(stats.get("points", 0))
                    skaters.append(OlympicPlayer(
                        name=athlete.get("displayName", ""),
                        country=team.get("displayName", ""),
                        country_code=team.get("abbreviation", ""),
                        games_played=gp,
                        goals=int(stats.get("goals", 0)),
                        assists=int(stats.get("assists", 0)),
                        points=pts,
                        plus_minus=int(stats.get("plusMinus", 0)),
                        ppg=pts / gp,
                    ))

        return skaters, goalies
    except Exception as e:
        logger.warning("espn_api_leaders_parse_error", error=str(e))
        return skaters, goalies


async def fetch_espn_olympic_stats() -> OlympicData:
    """
    Fetch Olympic hockey statistics from ESPN.

    Returns comprehensive Olympic data including standings,
    scoring leaders, and goalie leaders.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    data = OlympicData(
        tournament="Milano Cortina 2026",
        last_updated=datetime.utcnow().isoformat(),
        start_date=date(2026, 2, 8),
        end_date=date(2026, 2, 22),
    )

    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        # Fetch standings
        try:
            resp = await client.get(ESPN_STANDINGS)
            if resp.status_code == 200:
                standings = _parse_espn_standings(resp.text)
                data.standings = standings
                logger.info("olympic_standings_fetched", teams=len(standings))
        except Exception as e:
            logger.warning("olympic_standings_failed", error=str(e))

        # Fetch player stats
        try:
            resp = await client.get(ESPN_STATS)
            if resp.status_code == 200:
                skaters, goalies = _parse_espn_stats(resp.text)
                data.skater_leaders = skaters
                data.goalie_leaders = goalies
                logger.info("olympic_stats_fetched", skaters=len(skaters), goalies=len(goalies))
        except Exception as e:
            logger.warning("olympic_stats_failed", error=str(e))

        # Fetch schedule
        try:
            resp = await client.get(ESPN_SCHEDULE)
            if resp.status_code == 200:
                schedule = _parse_espn_schedule(resp.text)
                data.schedule = schedule
                # Calculate games played/remaining
                data.games_played = sum(1 for g in schedule if g.status == "final")
                data.games_remaining = sum(1 for g in schedule if g.status in ("scheduled", "live"))
                logger.info("olympic_schedule_fetched", games=len(schedule))
        except Exception as e:
            logger.warning("olympic_schedule_failed", error=str(e))

    return data


def _parse_espn_standings(html: str) -> list[OlympicTeam]:
    """Parse ESPN standings page."""
    soup = BeautifulSoup(html, 'html.parser')
    teams = []

    # Find standings tables
    tables = soup.select('table')
    current_group = "A"

    for table in tables:
        rows = table.select('tbody tr')
        for row in rows:
            cells = row.select('td')
            if len(cells) >= 4:
                # Try to extract team name
                team_cell = cells[0]
                team_link = team_cell.select_one('a')
                team_name = team_link.get_text(strip=True) if team_link else team_cell.get_text(strip=True)

                if team_name and team_name in COUNTRY_CODES:
                    try:
                        gf = int(cells[-3].get_text(strip=True) or 0) if len(cells) >= 5 else 0
                        ga = int(cells[-2].get_text(strip=True) or 0) if len(cells) >= 5 else 0

                        teams.append(OlympicTeam(
                            country=team_name,
                            country_code=get_country_code(team_name),
                            group=current_group,
                            games_played=int(cells[1].get_text(strip=True) or 0),
                            wins=int(cells[2].get_text(strip=True) or 0),
                            points=int(cells[-1].get_text(strip=True) or 0),
                            goals_for=gf,
                            goals_against=ga,
                            goal_diff=gf - ga,
                        ))
                    except (ValueError, IndexError):
                        continue

    return teams


def _parse_espn_stats(html: str) -> tuple[list[OlympicPlayer], list[OlympicGoalie]]:
    """Parse ESPN stats page for player and goalie leaders."""
    soup = BeautifulSoup(html, 'html.parser')
    skaters = []
    goalies = []

    # Find stats tables
    tables = soup.select('table')

    for table in tables:
        headers = [th.get_text(strip=True).lower() for th in table.select('th')]

        # Detect if this is skater or goalie table
        is_goalie = any(h in headers for h in ['sv%', 'gaa', 'saves'])

        rows = table.select('tbody tr')
        for row in rows:
            cells = row.select('td')
            if len(cells) < 3:
                continue

            # Get player name and country
            name_cell = cells[0]
            player_link = name_cell.select_one('a')
            player_name = player_link.get_text(strip=True) if player_link else name_cell.get_text(strip=True)

            # Get country from second cell usually
            country = ""
            country_code = ""
            for cell in cells[1:3]:
                text = cell.get_text(strip=True)
                if text in COUNTRY_CODES or len(text) == 3:
                    country = get_country_name(text)
                    country_code = get_country_code(text)
                    break

            if not player_name or not country_code:
                continue

            try:
                if is_goalie:
                    gp = _safe_int(cells, 2)
                    goalies.append(OlympicGoalie(
                        name=player_name,
                        country=country,
                        country_code=country_code,
                        games_played=gp,
                        wins=_safe_int(cells, 3),
                        gaa=_safe_float(cells, -2),
                        save_pct=_safe_float(cells, -1),
                    ))
                else:
                    gp = _safe_int(cells, 1)
                    goals = _safe_int(cells, 2)
                    assists = _safe_int(cells, 3)
                    points = _safe_int(cells, 4)

                    skaters.append(OlympicPlayer(
                        name=player_name,
                        country=country,
                        country_code=country_code,
                        games_played=gp,
                        goals=goals,
                        assists=assists,
                        points=points,
                        ppg=points / gp if gp > 0 else 0.0,
                    ))
            except Exception:
                continue

    return skaters, goalies


def _parse_espn_schedule(html: str) -> list[OlympicGame]:
    """Parse ESPN schedule page for games."""
    soup = BeautifulSoup(html, 'html.parser')
    games = []

    # Find schedule sections
    sections = soup.select('.schedule-date') or soup.select('.ScoreCell')

    for i, section in enumerate(sections):
        try:
            # This is simplified - actual parsing depends on ESPN's HTML structure
            games.append(OlympicGame(
                game_id=f"oly2026_{i}",
                game_date=date.today(),  # Would parse actual date
                status="scheduled",
            ))
        except Exception:
            continue

    return games


def _safe_int(cells: list, idx: int) -> int:
    """Safely extract integer from cell."""
    try:
        return int(cells[idx].get_text(strip=True))
    except (IndexError, ValueError):
        return 0


def _safe_float(cells: list, idx: int) -> float:
    """Safely extract float from cell."""
    try:
        text = cells[idx].get_text(strip=True)
        # Handle percentages like ".923"
        if text.startswith('.'):
            return float(text)
        return float(text)
    except (IndexError, ValueError):
        return 0.0


async def link_olympic_to_nhl_players(
    db: AsyncSession,
    olympic_data: OlympicData,
) -> OlympicData:
    """
    Link Olympic players to their NHL player records.

    This allows us to show both Olympic and NHL stats together,
    which is crucial for predictions.
    """
    for player in olympic_data.skater_leaders:
        result = await db.execute(
            text("""
                SELECT p.id, p.nhl_id, p.team_abbrev, s.points, s.games_played
                FROM players p
                LEFT JOIN player_season_stats s ON p.id = s.player_id
                WHERE p.name ILIKE :name
                ORDER BY s.season DESC
                LIMIT 1
            """),
            {"name": f"%{player.name}%"}
        )
        row = result.fetchone()
        if row:
            player.nhl_player_id = row.nhl_id
            player.nhl_team = row.team_abbrev
            if row.games_played and row.games_played > 0:
                player.nhl_ppg = row.points / row.games_played
            player.primary_league = "NHL"

    for goalie in olympic_data.goalie_leaders:
        result = await db.execute(
            text("""
                SELECT p.id, p.nhl_id, p.team_abbrev
                FROM players p
                WHERE p.name ILIKE :name
                  AND p.position = 'G'
                LIMIT 1
            """),
            {"name": f"%{goalie.name}%"}
        )
        row = result.fetchone()
        if row:
            goalie.nhl_player_id = row.nhl_id
            goalie.nhl_team = row.team_abbrev

    return olympic_data


async def calculate_team_strength_ratings(
    db: AsyncSession,
    olympic_data: OlympicData,
) -> OlympicData:
    """
    Calculate team strength ratings based on roster composition.

    This aggregates NHL performance of players on each national team
    to give a power ranking.
    """
    for team in olympic_data.standings:
        # Count NHL players on this country's Olympic roster
        result = await db.execute(
            text("""
                SELECT
                    COUNT(*) as nhl_count,
                    AVG(s.points::float / NULLIF(s.games_played, 0)) as avg_ppg,
                    SUM(s.points) as total_points
                FROM players p
                JOIN player_season_stats s ON p.id = s.player_id
                WHERE s.season = (SELECT MAX(season) FROM player_season_stats)
                  AND s.games_played >= 10
                  -- This is a simplification - would need actual roster data
            """)
        )
        row = result.fetchone()

        if row and row.avg_ppg:
            team.nhl_player_count = row.nhl_count or 0
            # Strength rating: weighted by NHL performance
            team.strength_rating = (row.avg_ppg or 0) * 100

    return olympic_data


# -------------------------------------------------------------------------
# Olympic-Specific Prediction Logic
# -------------------------------------------------------------------------

# Olympic prediction weights - different from NHL model
OLYMPIC_WEIGHTS = {
    "nhl_baseline": 0.45,        # NHL season performance (reduced from NHL model)
    "olympic_form": 0.20,        # In-tournament performance (critical)
    "goalie_matchup": 0.20,      # DOUBLED from NHL model - goalies are kings
    "country_strength": 0.10,    # Team composition factor
    "international_exp": 0.05,   # Prior Olympic/WC experience
}


@dataclass
class OlympicPlayerPrediction:
    """Prediction for an Olympic player in a game."""
    player_name: str
    country: str
    country_code: str
    opponent_country: str
    opponent_code: str

    # Probabilities
    prob_goal: float
    prob_point: float
    prob_multi_point: float

    # Expected values
    expected_goals: float
    expected_points: float

    # Model components
    nhl_baseline_ppg: float
    olympic_form_ppg: float
    goalie_adjustment: float
    country_strength_diff: float

    # Confidence
    confidence: str  # high, medium, low
    confidence_score: float
    factors: list[str]

    # Tournament context
    round: str  # group, quarterfinal, semifinal, medal
    is_elimination: bool


async def predict_olympic_game(
    db: AsyncSession,
    home_country: str,
    away_country: str,
    game_round: str = "group",
) -> dict:
    """
    Generate predictions for an Olympic hockey game.

    Key differences from NHL predictions:
    1. Goalie impact is 2x higher
    2. In-tournament form matters more than season stats
    3. Elimination games have pressure coefficients
    4. Country strength differential is a factor
    """
    import math

    olympic_data = await get_olympic_summary_cached(db)

    home_code = get_country_code(home_country)
    away_code = get_country_code(away_country)

    is_elimination = game_round in ("quarterfinal", "semifinal", "bronze", "gold")

    # Get team strength ratings - handle both list and dict standings formats
    standings = olympic_data.get("standings", [])
    home_team = None
    away_team = None

    if isinstance(standings, dict):
        # Hardcoded format: {"A": [...], "B": [...]}
        for group_teams in standings.values():
            for t in group_teams:
                code = t.get("code") or t.get("country_code")
                if code == home_code:
                    home_team = t
                if code == away_code:
                    away_team = t
    else:
        # Live format: [...]
        home_team = next((t for t in standings if t.get("country_code") == home_code), None)
        away_team = next((t for t in standings if t.get("country_code") == away_code), None)

    home_strength = home_team.get("strength_rating", 50) if home_team else 50
    away_strength = away_team.get("strength_rating", 50) if away_team else 50
    strength_diff = (home_strength - away_strength) / 100

    # Get players for each team - handle both skater_leaders and scoring_leaders
    all_skaters = olympic_data.get("skater_leaders", []) or olympic_data.get("scoring_leaders", [])

    # Normalize player data to handle both formats
    def get_player_country(p):
        return p.get("country_code") or p.get("country", "")

    home_players = [p for p in all_skaters if get_player_country(p) == home_code]
    away_players = [p for p in all_skaters if get_player_country(p) == away_code]

    # Fetch NHL stats for all players from database
    async def enrich_with_nhl_stats(players: list) -> list:
        """Add NHL stats to player dicts for better predictions."""
        enriched = []
        for player in players:
            player_copy = dict(player)  # Don't modify original
            player_name = player.get("name", "")
            if player_name:
                try:
                    result = await db.execute(
                        text("""
                            SELECT s.goals, s.assists, s.points, s.games_played, s.xg
                            FROM players p
                            JOIN player_season_stats s ON p.id = s.player_id
                            WHERE p.name ILIKE :name
                              AND s.season = (SELECT MAX(season) FROM player_season_stats)
                            LIMIT 1
                        """),
                        {"name": f"%{player_name}%"}
                    )
                    row = result.fetchone()
                    if row and row.games_played and row.games_played > 0:
                        player_copy["nhl_gp"] = row.games_played
                        player_copy["nhl_goals"] = row.goals
                        player_copy["nhl_assists"] = row.assists
                        player_copy["nhl_points"] = row.points
                        player_copy["nhl_ppg"] = row.points / row.games_played
                except Exception as e:
                    logger.debug("nhl_stats_lookup_failed", player=player_name, error=str(e))
            enriched.append(player_copy)
        return enriched

    home_players = await enrich_with_nhl_stats(home_players[:10])
    away_players = await enrich_with_nhl_stats(away_players[:10])

    # Get goalies - handle both formats
    # Note: "opponent_goalie" is the goalie that team FACES (for probability calculation)
    # For display, we want each team's OWN goalie
    all_goalies = olympic_data.get("goalie_leaders", [])
    home_team_goalie = next((g for g in all_goalies
                        if (g.get("country_code") or g.get("country")) == home_code), None)
    away_team_goalie = next((g for g in all_goalies
                        if (g.get("country_code") or g.get("country")) == away_code), None)

    predictions = {
        "game": {
            "home_country": get_country_name(home_code),
            "home_code": home_code,
            "away_country": get_country_name(away_code),
            "away_code": away_code,
            "round": game_round,
            "is_elimination": is_elimination,
        },
        "matchup_context": {
            "home_strength": home_strength,
            "away_strength": away_strength,
            "strength_differential": round(strength_diff, 2),
            # Store each team's OWN goalie for display
            "home_goalie": home_team_goalie,
            "away_goalie": away_team_goalie,
        },
        "home_players": [],
        "away_players": [],
        "top_scorers": [],
    }

    # Calculate predictions for each player
    # Note: Home players face AWAY team's goalie, away players face HOME team's goalie
    all_predictions = []

    for player in home_players[:10]:
        pred = _calculate_olympic_player_prediction(
            player, home_code, away_code,
            away_team_goalie, strength_diff, is_elimination  # Home players face away team's goalie
        )
        predictions["home_players"].append(pred)
        all_predictions.append(pred)

    for player in away_players[:10]:
        pred = _calculate_olympic_player_prediction(
            player, away_code, home_code,
            home_team_goalie, -strength_diff, is_elimination  # Away players face home team's goalie
        )
        predictions["away_players"].append(pred)
        all_predictions.append(pred)

    # Sort by goal probability
    all_predictions.sort(key=lambda p: p["prob_goal"], reverse=True)
    predictions["top_scorers"] = all_predictions[:5]

    return predictions


def _calculate_olympic_player_prediction(
    player: dict,
    player_country: str,
    opponent_country: str,
    opponent_goalie: dict | None,
    strength_diff: float,
    is_elimination: bool,
) -> dict:
    """Calculate prediction for an Olympic player."""
    import math

    factors = []
    confidence_penalty = 1.0  # Multiplier for confidence when data is limited

    # Normalize player data - handle both live and hardcoded formats
    games_played = player.get("games_played") or player.get("gp") or 0
    points = player.get("points") or player.get("pts") or 0
    goals = player.get("goals") or player.get("g") or 0
    assists = player.get("assists") or player.get("a") or 0

    # Get NHL stats if available (these come from the player dict after linking)
    nhl_stats = player.get("nhl_stats", {})
    nhl_gp = nhl_stats.get("gp") or player.get("nhl_gp") or 0
    nhl_goals = nhl_stats.get("goals") or player.get("nhl_goals") or 0
    nhl_assists = nhl_stats.get("assists") or player.get("nhl_assists") or 0
    nhl_points = nhl_stats.get("points") or player.get("nhl_points") or 0

    # Base: NHL PPG (if available) or Olympic PPG
    nhl_ppg = player.get("nhl_ppg") or (nhl_points / nhl_gp if nhl_gp > 0 else 0)
    nhl_gpg = nhl_goals / nhl_gp if nhl_gp > 0 else 0
    olympic_ppg = player.get("ppg") or (points / games_played if games_played > 0 else 0)
    olympic_gpg = goals / games_played if games_played > 0 else 0

    # If player has NHL stats, weight them heavily but not exclusively
    # Use GPG for goal probability (not PPG) - this is more accurate
    # Reduce Olympic weight for small samples (< 3 games)
    olympic_weight = OLYMPIC_WEIGHTS["olympic_form"]
    if games_played < 3:
        olympic_weight *= (games_played / 3)  # Scale down: 1 game = 33%, 2 games = 67%
    nhl_weight = OLYMPIC_WEIGHTS["nhl_baseline"] + (OLYMPIC_WEIGHTS["olympic_form"] - olympic_weight)  # Redistribute to NHL

    if nhl_gpg > 0:
        # For GOAL probability, use goals-per-game directly
        base_gpg = nhl_gpg * nhl_weight + (olympic_gpg or 0) * olympic_weight
        # For POINT probability, use PPG
        base_ppg = nhl_ppg * nhl_weight + olympic_ppg * olympic_weight
        factors.append(f"NHL: {nhl_gpg:.3f} GPG, {nhl_ppg:.2f} PPG")
    elif nhl_ppg > 0:
        base_gpg = nhl_ppg * 0.4 * nhl_weight  # Estimate GPG from PPG
        base_ppg = nhl_ppg * nhl_weight + olympic_ppg * olympic_weight
        factors.append(f"NHL: {nhl_ppg:.2f} PPG")
    else:
        # Non-NHL player: apply confidence penalty
        # European league stats don't translate 1:1 to Olympic performance
        base_ppg = olympic_ppg * 0.75  # Discount Olympic-only stats
        base_gpg = olympic_gpg * 0.75 if olympic_gpg > 0 else base_ppg * 0.4
        confidence_penalty = 0.70  # Lower confidence
        factors.append(f"Non-NHL player: {olympic_ppg:.2f} Olympic PPG (discounted)")

        # If very limited Olympic sample, further reduce
        if games_played < 2:
            base_ppg *= 0.85
            base_gpg *= 0.85
            factors.append("Limited sample - high uncertainty")

    # If player has in-tournament stats, weight those heavily
    if games_played >= 2 and olympic_ppg > 0:
        if olympic_ppg > (nhl_ppg or 0) * 1.5:
            factors.append(f"Hot in tournament: {olympic_ppg:.2f} PPG")
            base_ppg *= 1.15  # Boost for tournament hot streak
            base_gpg *= 1.15
        elif olympic_ppg < (nhl_ppg or 0) * 0.5:
            factors.append(f"Struggling in tournament")
            base_ppg *= 0.85  # Reduce for cold player
            base_gpg *= 0.85

    # Goalie matchup adjustment (proportional, not additive)
    # Elite goalie reduces scoring by ~20-30%, weak goalie increases by ~10-20%
    goalie_multiplier = 1.0
    goalie_adj_display = 0.0
    if opponent_goalie:
        sv_pct = opponent_goalie.get("save_pct") or opponent_goalie.get("sv") or 0.905
        # League average is ~.905 in Olympics
        # Scale: .920 goalie = 0.85 multiplier, .890 goalie = 1.15 multiplier
        sv_diff = 0.905 - sv_pct
        goalie_multiplier = 1.0 + (sv_diff * 10)  # +/- 10% per 0.01 SV% diff
        goalie_multiplier = max(0.5, min(1.5, goalie_multiplier))  # Cap at 50-150%
        goalie_adj_display = goalie_multiplier - 1.0

        if sv_diff > 0.01:
            factors.append(f"Weak goalie boost: {opponent_goalie.get('name')} ({sv_pct:.3f} SV%) +{(goalie_multiplier-1)*100:.0f}%")
        elif sv_diff < -0.01:
            factors.append(f"Elite goalie penalty: {opponent_goalie.get('name')} ({sv_pct:.3f} SV%) {(goalie_multiplier-1)*100:.0f}%")

    # Country strength adjustment
    strength_adj = strength_diff * 0.3

    # Elimination game pressure
    elimination_mult = 1.0
    if is_elimination:
        # Some players rise to the occasion, adjust based on experience
        prior_olympics = player.get("prior_olympics", 0)
        if prior_olympics >= 2:
            factors.append(f"Veteran: {prior_olympics} prior Olympics")
            elimination_mult = 1.05
        else:
            # First Olympics + elimination = slight pressure
            factors.append("First Olympic elimination game")
            elimination_mult = 0.95

    # Country strength adjustment (proportional)
    strength_mult = 1.0 + (strength_diff * 0.2)  # +/- 20% based on team strength

    # Calculate expected goals and points with proportional adjustments
    expected_goals = base_gpg * goalie_multiplier * strength_mult * elimination_mult
    expected_points = base_ppg * goalie_multiplier * strength_mult * elimination_mult

    # Ensure non-negative
    expected_goals = max(0.05, expected_goals)
    expected_points = max(0.10, expected_points)

    # Calculate probabilities using Poisson
    prob_goal = 1 - math.exp(-expected_goals) if expected_goals > 0 else 0.05
    prob_point = 1 - math.exp(-expected_points) if expected_points > 0 else 0.10
    prob_multi = (1 - math.exp(-expected_points) - expected_points * math.exp(-expected_points)) if expected_points > 0 else 0.02

    # Confidence based on data availability (games_played already normalized above)
    has_nhl = nhl_ppg > 0 or nhl_gp > 0

    if has_nhl and games_played >= 3:
        confidence = "high"
        confidence_score = 0.85 * confidence_penalty
    elif has_nhl or games_played >= 2:
        confidence = "medium"
        confidence_score = 0.65 * confidence_penalty
    else:
        confidence = "low"
        confidence_score = 0.40 * confidence_penalty
        factors.append("Limited data - prediction less reliable")

    # Adjust confidence label if penalty applied
    if confidence_penalty < 1.0 and confidence == "high":
        confidence = "medium"
    elif confidence_penalty < 1.0 and confidence == "medium":
        confidence = "low"

    return {
        "player_name": player.get("name"),
        "country": player.get("country"),
        "country_code": player.get("country_code") or player.get("country"),
        "opponent_country": get_country_name(opponent_country),
        "opponent_code": opponent_country,
        "prob_goal": round(prob_goal, 3),
        "prob_point": round(prob_point, 3),
        "prob_multi_point": round(prob_multi, 3),
        "expected_goals": round(expected_goals, 2),
        "expected_points": round(expected_points, 2),
        # NHL season stats for context
        "nhl_gp": nhl_gp,
        "nhl_goals": nhl_goals,
        "nhl_assists": nhl_assists,
        "nhl_points": nhl_points,
        "nhl_ppg": round(nhl_ppg, 2) if nhl_ppg else None,
        "nhl_gpg": round(nhl_gpg, 3) if nhl_gpg else None,
        # Olympic tournament stats
        "olympic_gp": games_played,
        "olympic_goals": goals,
        "olympic_assists": assists,
        "olympic_points": points,
        "olympic_ppg": round(olympic_ppg, 2),
        "olympic_gpg": round(olympic_gpg, 3) if olympic_gpg else None,
        # Adjustments
        "goalie_adjustment": round(goalie_adj_display, 2),
        "goalie_multiplier": round(goalie_multiplier, 2),
        "confidence": confidence,
        "confidence_score": confidence_score,
        "factors": factors,
        "is_elimination": is_elimination,
    }


# -------------------------------------------------------------------------
# Caching and Update Functions
# -------------------------------------------------------------------------

async def get_olympic_summary_cached(db: AsyncSession = None) -> dict:
    """
    Get Olympic data with caching.

    During active tournament, refreshes every 15 minutes.
    Tries multiple sources in order:
    1. ESPN article page (most reliable during tournament)
    2. ESPN API endpoints (if they work)
    3. Cached/hardcoded data with NHL enrichment
    """
    global _olympic_cache

    now = datetime.utcnow()
    cache_valid = (
        _olympic_cache["data"] is not None and
        _olympic_cache["last_updated"] is not None and
        (now - _olympic_cache["last_updated"]).total_seconds() < _olympic_cache["cache_ttl_minutes"] * 60
    )

    if cache_valid:
        return _olympic_cache["data"]

    # Start with current hardcoded data as base
    result = dict(CURRENT_OLYMPIC_DATA)

    # Try ESPN article page first (most reliable)
    try:
        espn_data = await fetch_espn_article_stats()
        if espn_data:
            # Merge ESPN live data into our base
            if espn_data.get("scoring_leaders"):
                result["scoring_leaders"] = espn_data["scoring_leaders"]
            if espn_data.get("goalie_leaders"):
                result["goalie_leaders"] = espn_data["goalie_leaders"]
            result["source"] = "espn_live"
            result["last_updated"] = now.isoformat()
            logger.info("olympic_data_from_espn_article")
    except Exception as e:
        logger.warning("espn_article_failed", error=str(e))

    # If ESPN didn't return data, try the API endpoints
    if result.get("source") != "espn_live":
        try:
            data = await fetch_espn_olympic_stats()
            if data.skater_leaders:
                result["scoring_leaders"] = [p.to_dict() for p in data.skater_leaders]
            if data.goalie_leaders:
                result["goalie_leaders"] = [g.to_dict() for g in data.goalie_leaders]
            result["source"] = "espn_api"
        except Exception as e:
            logger.debug("espn_api_failed_using_hardcoded", error=str(e))
            result["source"] = "hardcoded"

    # Enrich with NHL stats if we have database access
    if db and result.get("scoring_leaders"):
        for player in result["scoring_leaders"]:
            if not player.get("nhl_ppg"):
                try:
                    nhl_result = await db.execute(
                        text("""
                            SELECT s.goals, s.assists, s.points, s.games_played
                            FROM players p
                            JOIN player_season_stats s ON p.id = s.player_id
                            WHERE p.name ILIKE :name
                              AND s.season = (SELECT MAX(season) FROM player_season_stats)
                            LIMIT 1
                        """),
                        {"name": f"%{player['name']}%"}
                    )
                    row = nhl_result.fetchone()
                    if row and row.games_played and row.games_played > 0:
                        player["nhl_gp"] = row.games_played
                        player["nhl_goals"] = row.goals
                        player["nhl_assists"] = row.assists
                        player["nhl_points"] = row.points
                        player["nhl_ppg"] = round(row.points / row.games_played, 2)
                except Exception:
                    pass

    _olympic_cache["data"] = result
    _olympic_cache["last_updated"] = now

    return result


def invalidate_olympic_cache():
    """Force cache invalidation (call after manual refresh)."""
    global _olympic_cache
    _olympic_cache["data"] = None
    _olympic_cache["last_updated"] = None


async def refresh_olympic_data(db: AsyncSession = None) -> dict:
    """Force refresh Olympic data."""
    invalidate_olympic_cache()
    return await get_olympic_summary_cached(db)


# -------------------------------------------------------------------------
# Dynamic Roster Builder (from NHL Database)
# -------------------------------------------------------------------------

# Country to nationality mapping (for database lookup)
COUNTRY_TO_NATIONALITY = {
    "CAN": ["Canada", "Canadian", "CAN"],
    "USA": ["United States", "American", "USA", "U.S.A."],
    "SWE": ["Sweden", "Swedish", "SWE"],
    "FIN": ["Finland", "Finnish", "FIN"],
    "RUS": ["Russia", "Russian", "RUS"],
    "CZE": ["Czech Republic", "Czechia", "Czech", "CZE"],
    "SUI": ["Switzerland", "Swiss", "SUI", "CHE"],
    "GER": ["Germany", "German", "GER", "DEU"],
    "SVK": ["Slovakia", "Slovak", "SVK"],
    "LAT": ["Latvia", "Latvian", "LAT", "LVA"],
    "DEN": ["Denmark", "Danish", "DEN", "DNK"],
    "NOR": ["Norway", "Norwegian", "NOR"],
    "AUT": ["Austria", "Austrian", "AUT"],
    "SLO": ["Slovenia", "Slovenian", "SLO", "SVN"],
}


async def build_olympic_rosters_from_nhl(db: AsyncSession) -> dict:
    """
    Dynamically build Olympic rosters from NHL player database.

    Queries top players by nationality based on current season stats.
    This is more accurate than hardcoded data and auto-updates.
    """
    rosters = {}

    for country_code, nationalities in COUNTRY_TO_NATIONALITY.items():
        # Build nationality filter
        nat_conditions = " OR ".join([f"p.birth_country ILIKE '%{n}%'" for n in nationalities])

        try:
            result = await db.execute(
                text(f"""
                    SELECT p.name, p.team_abbrev, p.position, p.birth_country,
                           s.goals, s.assists, s.points, s.games_played, s.xg
                    FROM players p
                    JOIN player_season_stats s ON p.id = s.player_id
                    WHERE ({nat_conditions})
                      AND s.season = (SELECT MAX(season) FROM player_season_stats)
                      AND s.games_played >= 10
                      AND p.position != 'G'
                    ORDER BY s.points DESC
                    LIMIT 15
                """)
            )

            players = []
            for row in result.fetchall():
                gp = row.games_played or 1
                players.append({
                    "name": row.name,
                    "country": country_code,
                    "gp": 0,  # Olympic GP (starts at 0)
                    "g": 0,   # Olympic goals
                    "a": 0,   # Olympic assists
                    "pts": 0, # Olympic points
                    # NHL stats for reference
                    "nhl_team": row.team_abbrev,
                    "nhl_gp": row.games_played,
                    "nhl_goals": row.goals,
                    "nhl_assists": row.assists,
                    "nhl_points": row.points,
                    "nhl_ppg": row.points / gp,
                })

            if players:
                rosters[country_code] = players
                logger.info("olympic_roster_built", country=country_code, players=len(players))

        except Exception as e:
            logger.warning("olympic_roster_build_failed", country=country_code, error=str(e))

    return rosters


async def get_dynamic_olympic_data(db: AsyncSession) -> dict:
    """
    Get Olympic data with dynamic roster building.

    Combines:
    - Dynamic rosters from NHL database (by nationality)
    - Live ESPN data for tournament stats (when available)
    - Hardcoded standings/goalies as fallback
    """
    # Start with hardcoded structure
    data = dict(CURRENT_OLYMPIC_DATA)

    # Try to build dynamic rosters from database
    try:
        rosters = await build_olympic_rosters_from_nhl(db)

        if rosters:
            # Merge dynamic rosters into scoring_leaders
            all_players = []
            for country_code, players in rosters.items():
                all_players.extend(players)

            # Sort by NHL PPG (best players first)
            all_players.sort(key=lambda p: p.get("nhl_ppg", 0), reverse=True)

            # Replace hardcoded scoring_leaders with dynamic data
            data["scoring_leaders"] = all_players
            data["roster_source"] = "nhl_database"
            logger.info("olympic_data_using_dynamic_rosters", total_players=len(all_players))
    except Exception as e:
        logger.warning("dynamic_roster_failed_using_hardcoded", error=str(e))
        data["roster_source"] = "hardcoded"

    return data


# -------------------------------------------------------------------------
# Hardcoded Current Data (Backup)
# -------------------------------------------------------------------------

# Current Olympic data - Milano Cortina 2026 (LIVE - Updated Feb 17, 2026)
# This data is updated via fetch_live_espn_olympic_stats() or manual API calls
CURRENT_OLYMPIC_DATA = {
    "tournament": "Milano Cortina 2026",
    "tournament_status": "in_progress",
    "current_round": "qualification",  # Group stage complete, qualification round today
    "last_updated": "2026-02-17T12:00:00Z",
    "standings": {
        "A": [
            {"country": "Canada", "code": "CAN", "w": 3, "otw": 0, "otl": 0, "l": 0, "pts": 9},
            {"country": "Switzerland", "code": "SUI", "w": 1, "otw": 1, "otl": 0, "l": 1, "pts": 5},
            {"country": "Czechia", "code": "CZE", "w": 1, "otw": 0, "otl": 1, "l": 1, "pts": 4},
            {"country": "France", "code": "FRA", "w": 0, "otw": 0, "otl": 0, "l": 2, "pts": 0},
        ],
        "B": [
            {"country": "Slovakia", "code": "SVK", "w": 2, "otw": 0, "otl": 0, "l": 1, "pts": 6},
            {"country": "Finland", "code": "FIN", "w": 2, "otw": 0, "otl": 0, "l": 1, "pts": 6},
            {"country": "Sweden", "code": "SWE", "w": 2, "otw": 0, "otl": 0, "l": 1, "pts": 6},
            {"country": "Italy", "code": "ITA", "w": 0, "otw": 0, "otl": 0, "l": 3, "pts": 0},
        ],
        "C": [
            {"country": "USA", "code": "USA", "w": 3, "otw": 0, "otl": 0, "l": 0, "pts": 9},
            {"country": "Germany", "code": "GER", "w": 1, "otw": 0, "otl": 0, "l": 2, "pts": 3},
            {"country": "Denmark", "code": "DEN", "w": 1, "otw": 0, "otl": 0, "l": 2, "pts": 3},
            {"country": "Latvia", "code": "LAT", "w": 1, "otw": 0, "otl": 0, "l": 2, "pts": 3},
        ],
    },
    "scoring_leaders": [
        # =====================================================================
        # CANADA - Full roster (updated Feb 17, 2026)
        # =====================================================================
        {"name": "Connor McDavid", "country": "CAN", "gp": 3, "g": 2, "a": 7, "pts": 9},
        {"name": "Macklin Celebrini", "country": "CAN", "gp": 3, "g": 4, "a": 2, "pts": 6},
        {"name": "Sidney Crosby", "country": "CAN", "gp": 3, "g": 2, "a": 4, "pts": 6},
        {"name": "Nathan MacKinnon", "country": "CAN", "gp": 3, "g": 2, "a": 3, "pts": 5},
        {"name": "Mark Stone", "country": "CAN", "gp": 3, "g": 2, "a": 2, "pts": 4},
        {"name": "Cale Makar", "country": "CAN", "gp": 3, "g": 1, "a": 3, "pts": 4},
        {"name": "Mitch Marner", "country": "CAN", "gp": 3, "g": 0, "a": 4, "pts": 4},
        {"name": "Thomas Harley", "country": "CAN", "gp": 3, "g": 1, "a": 2, "pts": 3},
        {"name": "Tom Wilson", "country": "CAN", "gp": 3, "g": 1, "a": 2, "pts": 3},
        {"name": "Bo Horvat", "country": "CAN", "gp": 3, "g": 2, "a": 0, "pts": 2},
        {"name": "Brandon Hagel", "country": "CAN", "gp": 3, "g": 1, "a": 0, "pts": 1},
        {"name": "Nick Suzuki", "country": "CAN", "gp": 3, "g": 1, "a": 0, "pts": 1},
        {"name": "Devon Toews", "country": "CAN", "gp": 3, "g": 1, "a": 0, "pts": 1},
        {"name": "Brad Marchand", "country": "CAN", "gp": 1, "g": 0, "a": 1, "pts": 1},
        {"name": "Sam Bennett", "country": "CAN", "gp": 3, "g": 0, "a": 1, "pts": 1},
        {"name": "Drew Doughty", "country": "CAN", "gp": 3, "g": 0, "a": 1, "pts": 1},
        {"name": "Sam Reinhart", "country": "CAN", "gp": 3, "g": 0, "a": 1, "pts": 1},
        {"name": "Shea Theodore", "country": "CAN", "gp": 3, "g": 0, "a": 1, "pts": 1},
        {"name": "Josh Morrissey", "country": "CAN", "gp": 1, "g": 0, "a": 0, "pts": 0},
        {"name": "Seth Jarvis", "country": "CAN", "gp": 2, "g": 0, "a": 0, "pts": 0},
        {"name": "Travis Sanheim", "country": "CAN", "gp": 2, "g": 0, "a": 0, "pts": 0},
        {"name": "Colton Parayko", "country": "CAN", "gp": 3, "g": 0, "a": 0, "pts": 0},
        # =====================================================================
        # OTHER COUNTRIES
        # =====================================================================
        {"name": "Timo Meier", "country": "SUI", "gp": 4, "g": 3, "a": 4, "pts": 7},
        {"name": "Martin Necas", "country": "CZE", "gp": 4, "g": 3, "a": 4, "pts": 7},
        {"name": "Tim Stutzle", "country": "GER", "gp": 4, "g": 4, "a": 2, "pts": 6},
        {"name": "Juraj Slafkovsky", "country": "SVK", "gp": 3, "g": 3, "a": 3, "pts": 6},
        {"name": "Leon Draisaitl", "country": "GER", "gp": 4, "g": 2, "a": 4, "pts": 6},
        {"name": "Nick Olesen", "country": "DEN", "gp": 4, "g": 4, "a": 1, "pts": 5},
        {"name": "Auston Matthews", "country": "USA", "gp": 3, "g": 3, "a": 2, "pts": 5},
        # USA
        {"name": "Jack Hughes", "country": "USA", "gp": 3, "g": 2, "a": 2, "pts": 4},
        {"name": "Matthew Tkachuk", "country": "USA", "gp": 3, "g": 1, "a": 3, "pts": 4},
        {"name": "Jack Eichel", "country": "USA", "gp": 3, "g": 2, "a": 1, "pts": 3},
        {"name": "Brady Tkachuk", "country": "USA", "gp": 3, "g": 1, "a": 2, "pts": 3},
        {"name": "Tage Thompson", "country": "USA", "gp": 3, "g": 2, "a": 0, "pts": 2},
        {"name": "Quinn Hughes", "country": "USA", "gp": 3, "g": 0, "a": 2, "pts": 2},
        {"name": "Adam Fox", "country": "USA", "gp": 3, "g": 0, "a": 2, "pts": 2},
        {"name": "Charlie McAvoy", "country": "USA", "gp": 3, "g": 0, "a": 1, "pts": 1},
        {"name": "Brock Nelson", "country": "USA", "gp": 3, "g": 1, "a": 1, "pts": 2},
        # Sweden
        {"name": "Rasmus Dahlin", "country": "SWE", "gp": 3, "g": 1, "a": 3, "pts": 4},
        {"name": "William Nylander", "country": "SWE", "gp": 3, "g": 2, "a": 1, "pts": 3},
        {"name": "Mika Zibanejad", "country": "SWE", "gp": 3, "g": 1, "a": 2, "pts": 3},
        {"name": "Filip Forsberg", "country": "SWE", "gp": 3, "g": 2, "a": 0, "pts": 2},
        {"name": "Gustav Forsling", "country": "SWE", "gp": 3, "g": 0, "a": 2, "pts": 2},
        {"name": "Elias Pettersson", "country": "SWE", "gp": 3, "g": 1, "a": 1, "pts": 2},
        # Finland
        {"name": "Aleksander Barkov", "country": "FIN", "gp": 3, "g": 2, "a": 2, "pts": 4},
        {"name": "Mikko Rantanen", "country": "FIN", "gp": 3, "g": 1, "a": 2, "pts": 3},
        {"name": "Sebastian Aho", "country": "FIN", "gp": 3, "g": 1, "a": 2, "pts": 3},
        {"name": "Miro Heiskanen", "country": "FIN", "gp": 3, "g": 0, "a": 2, "pts": 2},
        # Switzerland
        {"name": "Nino Niederreiter", "country": "SUI", "gp": 4, "g": 2, "a": 2, "pts": 4},
        {"name": "Nico Hischier", "country": "SUI", "gp": 4, "g": 1, "a": 2, "pts": 3},
        {"name": "Roman Josi", "country": "SUI", "gp": 4, "g": 0, "a": 3, "pts": 3},
        {"name": "Kevin Fiala", "country": "SUI", "gp": 4, "g": 1, "a": 1, "pts": 2},
        # Czechia
        {"name": "David Pastrnak", "country": "CZE", "gp": 4, "g": 2, "a": 2, "pts": 4},
        # Germany
        {"name": "Moritz Seider", "country": "GER", "gp": 4, "g": 0, "a": 3, "pts": 3},
        # Slovakia
        {"name": "Martin Fehervary", "country": "SVK", "gp": 3, "g": 0, "a": 2, "pts": 2},
    ],
    "goalie_leaders": [
        # LIVE goalie stats - Feb 17, 2026
        {"name": "Leonardo Genoni", "country": "SUI", "gp": 3, "w": 3, "gaa": 0.99, "sv": 0.962},
        {"name": "Connor Hellebuyck", "country": "USA", "gp": 2, "w": 2, "gaa": 1.00, "sv": 0.952},
        {"name": "Jordan Binnington", "country": "CAN", "gp": 2, "w": 2, "gaa": 1.00, "sv": 0.950},
        {"name": "Logan Thompson", "country": "CAN", "gp": 1, "w": 1, "gaa": 1.00, "sv": 0.960},
        {"name": "Juuse Saros", "country": "FIN", "gp": 3, "w": 2, "gaa": 1.34, "sv": 0.946},
        {"name": "Philipp Grubauer", "country": "GER", "gp": 3, "w": 2, "gaa": 2.03, "sv": 0.934},
        {"name": "Samuel Hlavaj", "country": "SVK", "gp": 2, "w": 1, "gaa": 3.00, "sv": 0.934},
        {"name": "Frederik Andersen", "country": "DEN", "gp": 2, "w": 1, "gaa": 2.58, "sv": 0.913},
        {"name": "Jacob Markstrom", "country": "SWE", "gp": 2, "w": 2, "gaa": 2.00, "sv": 0.905},
        {"name": "Filip Gustavsson", "country": "SWE", "gp": 2, "w": 2, "gaa": 2.55, "sv": 0.889},
    ],
    "upcoming_games": [
        # Quarterfinals - Feb 19 (after qualification round)
        {"home": "CAN", "away": "CZE", "round": "quarterfinal", "date": "2026-02-19"},
        {"home": "USA", "away": "SUI", "round": "quarterfinal", "date": "2026-02-19"},
        {"home": "SWE", "away": "FIN", "round": "quarterfinal", "date": "2026-02-19"},
        {"home": "SVK", "away": "GER", "round": "quarterfinal", "date": "2026-02-19"},
    ],
}


def get_current_olympic_data() -> dict:
    """Get current Olympic hockey data (hardcoded backup)."""
    return CURRENT_OLYMPIC_DATA


async def get_olympic_summary(db: AsyncSession = None) -> dict:
    """
    Get complete Olympic hockey summary.

    Tries to fetch live data, falls back to cached/hardcoded data.
    """
    try:
        return await get_olympic_summary_cached(db)
    except Exception as e:
        logger.warning("olympic_fetch_failed_using_cache", error=str(e))
        return CURRENT_OLYMPIC_DATA


# -------------------------------------------------------------------------
# Progress Tracking
# -------------------------------------------------------------------------

def get_last_olympic_update() -> datetime | None:
    """Get the last time Olympic data was refreshed."""
    from backend.src.ingestion.scheduler import load_progress

    progress = load_progress()
    last_update_str = progress.get("last_olympic_update")
    if last_update_str:
        try:
            return datetime.fromisoformat(last_update_str)
        except (ValueError, TypeError):
            pass
    return None


def set_last_olympic_update():
    """Record that Olympic data was just refreshed."""
    from backend.src.ingestion.scheduler import load_progress, save_progress

    progress = load_progress()
    progress["last_olympic_update"] = datetime.now().isoformat()
    save_progress(progress)


async def update_olympic_data() -> dict:
    """
    Update Olympic data and record progress.

    Called during startup updates if Olympics are active.
    """
    # Check if we've updated recently (within last 15 minutes during tournament)
    last_update = get_last_olympic_update()
    if last_update:
        minutes_since = (datetime.now() - last_update).total_seconds() / 60
        if minutes_since < 15:
            logger.info("olympics_recently_updated", minutes_ago=round(minutes_since, 1))
            return {"skipped": True, "reason": "recently_updated"}

    logger.info("updating_olympic_data")

    try:
        async with async_session_maker() as db:
            data = await refresh_olympic_data(db)
            set_last_olympic_update()

            return {
                "updated": True,
                "standings_count": len(data.get("standings", {})),
                "leaders_count": len(data.get("skater_leaders", [])),
                "goalie_count": len(data.get("goalie_leaders", [])),
            }
    except Exception as e:
        logger.error("olympic_update_failed", error=str(e))
        return {"error": str(e)}


def is_olympic_tournament_active() -> bool:
    """Check if Olympic hockey tournament is currently active."""
    today = date.today()
    # Milano Cortina 2026: Feb 8 - Feb 22
    start = date(2026, 2, 8)
    end = date(2026, 2, 22)

    return start <= today <= end


# -------------------------------------------------------------------------
# Stats Import/Update Functions
# -------------------------------------------------------------------------


def update_olympic_stats(stats_data: dict) -> dict:
    """
    Update Olympic stats from API or CSV import.

    Args:
        stats_data: Dict with optional keys:
            - scoring_leaders: List of player dicts with name, country, gp, g, a, pts
            - goalie_leaders: List of goalie dicts with name, country, w, gaa, sv
            - standings: Dict or list of team standings
            - merge: If True, merge with existing; if False, replace

    Returns:
        Summary of what was updated
    """
    global CURRENT_OLYMPIC_DATA

    merge = stats_data.get("merge", True)
    summary = {"players_added": 0, "players_updated": 0, "goalies_updated": 0}

    # Update scoring leaders
    if "scoring_leaders" in stats_data:
        new_players = stats_data["scoring_leaders"]

        if merge:
            # Create lookup by name for existing players
            existing = {p["name"].lower(): p for p in CURRENT_OLYMPIC_DATA["scoring_leaders"]}

            for player in new_players:
                name_lower = player["name"].lower()
                if name_lower in existing:
                    # Update existing player
                    existing[name_lower].update(player)
                    summary["players_updated"] += 1
                else:
                    # Add new player
                    CURRENT_OLYMPIC_DATA["scoring_leaders"].append(player)
                    summary["players_added"] += 1
        else:
            # Replace entirely
            CURRENT_OLYMPIC_DATA["scoring_leaders"] = new_players
            summary["players_added"] = len(new_players)

    # Update goalie leaders
    if "goalie_leaders" in stats_data:
        new_goalies = stats_data["goalie_leaders"]

        if merge:
            existing = {g["name"].lower(): g for g in CURRENT_OLYMPIC_DATA["goalie_leaders"]}

            for goalie in new_goalies:
                name_lower = goalie["name"].lower()
                if name_lower in existing:
                    existing[name_lower].update(goalie)
                    summary["goalies_updated"] += 1
                else:
                    CURRENT_OLYMPIC_DATA["goalie_leaders"].append(goalie)
                    summary["goalies_updated"] += 1
        else:
            CURRENT_OLYMPIC_DATA["goalie_leaders"] = new_goalies
            summary["goalies_updated"] = len(new_goalies)

    # Update standings
    if "standings" in stats_data:
        CURRENT_OLYMPIC_DATA["standings"] = stats_data["standings"]
        summary["standings_updated"] = True

    # Invalidate cache so changes take effect
    invalidate_olympic_cache()

    logger.info("olympic_stats_updated", **summary)
    return summary


def import_olympic_stats_from_csv(csv_content: str) -> dict:
    """
    Import Olympic stats from CSV content.

    Expected CSV format:
    name,country,gp,g,a,pts,position
    Connor McDavid,CAN,3,2,5,7,F
    Jordan Binnington,CAN,3,,,,.945 (for goalies: sv% in pts column)

    Or separate goalie format with header:
    name,country,gp,w,gaa,sv
    """
    import csv
    from io import StringIO

    reader = csv.DictReader(StringIO(csv_content))

    players = []
    goalies = []

    for row in reader:
        name = row.get("name", "").strip()
        country = row.get("country", "").strip()

        if not name or not country:
            continue

        # Detect if this is a goalie row
        is_goalie = (
            row.get("position", "").upper() == "G" or
            "sv" in row or
            "gaa" in row or
            (row.get("g") == "" and row.get("a") == "" and row.get("pts", "").startswith("."))
        )

        if is_goalie:
            goalie = {
                "name": name,
                "country": country,
                "gp": int(row.get("gp") or 0),
                "w": int(row.get("w") or 0),
                "gaa": float(row.get("gaa") or 0),
                "sv": float(row.get("sv") or row.get("pts") or 0),
            }
            goalies.append(goalie)
        else:
            player = {
                "name": name,
                "country": country,
                "gp": int(row.get("gp") or 0),
                "g": int(row.get("g") or 0),
                "a": int(row.get("a") or 0),
                "pts": int(row.get("pts") or 0),
            }
            players.append(player)

    # Update the data
    stats_data = {}
    if players:
        stats_data["scoring_leaders"] = players
    if goalies:
        stats_data["goalie_leaders"] = goalies
    stats_data["merge"] = True  # Merge by default for CSV imports

    result = update_olympic_stats(stats_data)
    result["rows_processed"] = len(players) + len(goalies)
    return result


def get_olympic_stats_csv() -> str:
    """
    Export current Olympic stats as CSV.

    Returns CSV string with all players and goalies.
    """
    import csv
    from io import StringIO

    output = StringIO()

    # Export players
    writer = csv.writer(output)
    writer.writerow(["name", "country", "gp", "g", "a", "pts", "position"])

    for player in CURRENT_OLYMPIC_DATA.get("scoring_leaders", []):
        writer.writerow([
            player.get("name"),
            player.get("country"),
            player.get("gp") or player.get("games_played", 0),
            player.get("g") or player.get("goals", 0),
            player.get("a") or player.get("assists", 0),
            player.get("pts") or player.get("points", 0),
            "F",  # Forward (we don't track D vs F in current data)
        ])

    # Add blank row separator
    writer.writerow([])

    # Export goalies
    writer.writerow(["name", "country", "gp", "w", "gaa", "sv", "position"])

    for goalie in CURRENT_OLYMPIC_DATA.get("goalie_leaders", []):
        writer.writerow([
            goalie.get("name"),
            goalie.get("country"),
            goalie.get("gp") or goalie.get("games_played", 0),
            goalie.get("w") or goalie.get("wins", 0),
            goalie.get("gaa", 0),
            goalie.get("sv") or goalie.get("save_pct", 0),
            "G",
        ])

    return output.getvalue()
