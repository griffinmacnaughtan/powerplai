"""
Team and goalie statistics ingestion for enhanced predictions.

Fetches:
- Goalie stats (save %, GAA, games started)
- Team pace metrics (goals for/against, shots, special teams)
- Injury reports
- Probable starting goalies
"""
import asyncio
from datetime import date, datetime
from typing import Any
import structlog

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from backend.src.db.database import async_session_maker
from backend.src.config import get_settings
from backend.src.ingestion.scheduler import get_current_season_code

logger = structlog.get_logger()
settings = get_settings()

NHL_STATS_API = "https://api.nhle.com/stats/rest/en"


async def fetch_goalie_stats(season: str | None = None) -> list[dict]:
    """Fetch all goalie stats from NHL Stats API."""
    season = season or get_current_season_code()
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{NHL_STATS_API}/goalie/summary",
            params={
                "cayenneExp": f"seasonId={season} and gameTypeId=2",
                "limit": 200,
                "sort": "wins",
                "direction": "DESC"
            }
        )
        response.raise_for_status()
        data = response.json()
        return data.get("data", [])


async def fetch_team_stats(season: str | None = None) -> list[dict]:
    """Fetch all team stats from NHL Stats API."""
    season = season or get_current_season_code()
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{NHL_STATS_API}/team/summary",
            params={
                "cayenneExp": f"seasonId={season} and gameTypeId=2",
                "limit": 50,
            }
        )
        response.raise_for_status()
        data = response.json()
        return data.get("data", [])


async def ingest_goalie_stats(db: AsyncSession, season: str | None = None) -> int:
    """
    Ingest goalie statistics for the season.

    Returns number of goalies upserted.
    """
    season = season or get_current_season_code()
    goalies = await fetch_goalie_stats(season)
    count = 0

    for g in goalies:
        player_id = g.get("playerId")
        if not player_id:
            continue

        # Extract team abbreviation once (can be comma-separated for traded players)
        team_abbrevs_raw = g.get("teamAbbrevs") or ""
        team_abbrev = team_abbrevs_raw.split(",")[0] if team_abbrevs_raw else None

        # Get internal player ID (or create if not exists)
        result = await db.execute(
            text("SELECT id FROM players WHERE nhl_id = :nhl_id"),
            {"nhl_id": player_id}
        )
        row = result.fetchone()

        if not row:
            # Insert player if not exists
            await db.execute(
                text("""
                    INSERT INTO players (nhl_id, name, position, team_abbrev, created_at, updated_at)
                    VALUES (:nhl_id, :name, 'G', :team, NOW(), NOW())
                    ON CONFLICT (nhl_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        team_abbrev = EXCLUDED.team_abbrev,
                        updated_at = NOW()
                """),
                {
                    "nhl_id": player_id,
                    "name": g.get("goalieFullName", "Unknown"),
                    "team": team_abbrev
                }
            )
            result = await db.execute(
                text("SELECT id FROM players WHERE nhl_id = :nhl_id"),
                {"nhl_id": player_id}
            )
            row = result.fetchone()

        if not row:
            logger.warning("goalie_player_not_found_after_upsert", nhl_id=player_id)
            continue

        internal_id = row[0]

        # Upsert goalie stats
        await db.execute(
            text("""
                INSERT INTO goalie_stats (
                    player_id, season, team_abbrev,
                    games_played, games_started, wins, losses, ot_losses,
                    save_pct, goals_against_avg, shutouts,
                    shots_against, saves, time_on_ice,
                    created_at, updated_at
                ) VALUES (
                    :player_id, :season, :team_abbrev,
                    :games_played, :games_started, :wins, :losses, :ot_losses,
                    :save_pct, :gaa, :shutouts,
                    :shots_against, :saves, :toi,
                    NOW(), NOW()
                )
                ON CONFLICT (player_id, season) DO UPDATE SET
                    team_abbrev = EXCLUDED.team_abbrev,
                    games_played = EXCLUDED.games_played,
                    games_started = EXCLUDED.games_started,
                    wins = EXCLUDED.wins,
                    losses = EXCLUDED.losses,
                    ot_losses = EXCLUDED.ot_losses,
                    save_pct = EXCLUDED.save_pct,
                    goals_against_avg = EXCLUDED.goals_against_avg,
                    shutouts = EXCLUDED.shutouts,
                    shots_against = EXCLUDED.shots_against,
                    saves = EXCLUDED.saves,
                    time_on_ice = EXCLUDED.time_on_ice,
                    updated_at = NOW()
            """),
            {
                "player_id": internal_id,
                "season": season,
                "team_abbrev": team_abbrev,
                "games_played": g.get("gamesPlayed", 0),
                "games_started": g.get("gamesStarted", 0),
                "wins": g.get("wins", 0),
                "losses": g.get("losses", 0),
                "ot_losses": g.get("otLosses", 0),
                "save_pct": g.get("savePct"),
                "gaa": g.get("goalsAgainstAverage"),
                "shutouts": g.get("shutouts", 0),
                "shots_against": g.get("shotsAgainst"),
                "saves": g.get("saves"),
                "toi": g.get("timeOnIce"),
            }
        )
        count += 1

    await db.commit()
    logger.info("ingested_goalie_stats", season=season, count=count)
    return count


async def ingest_team_stats(db: AsyncSession, season: str | None = None) -> int:
    """
    Ingest team statistics for the season.

    Returns number of teams upserted.
    """
    season = season or get_current_season_code()
    teams = await fetch_team_stats(season)
    count = 0

    # Map team full names to abbreviations
    team_abbrev_map = {
        "New Jersey Devils": "NJD", "New York Islanders": "NYI", "New York Rangers": "NYR",
        "Philadelphia Flyers": "PHI", "Pittsburgh Penguins": "PIT", "Boston Bruins": "BOS",
        "Buffalo Sabres": "BUF", "Montréal Canadiens": "MTL", "Montreal Canadiens": "MTL",
        "Ottawa Senators": "OTT", "Toronto Maple Leafs": "TOR", "Carolina Hurricanes": "CAR",
        "Florida Panthers": "FLA", "Tampa Bay Lightning": "TBL", "Washington Capitals": "WSH",
        "Chicago Blackhawks": "CHI", "Detroit Red Wings": "DET", "Nashville Predators": "NSH",
        "St. Louis Blues": "STL", "Calgary Flames": "CGY", "Colorado Avalanche": "COL",
        "Edmonton Oilers": "EDM", "Vancouver Canucks": "VAN", "Anaheim Ducks": "ANA",
        "Dallas Stars": "DAL", "Los Angeles Kings": "LAK", "San Jose Sharks": "SJS",
        "Columbus Blue Jackets": "CBJ", "Minnesota Wild": "MIN", "Winnipeg Jets": "WPG",
        "Arizona Coyotes": "ARI", "Vegas Golden Knights": "VGK", "Seattle Kraken": "SEA",
        "Utah Hockey Club": "UTA",
        "Utah Mammoth": "UTA",
    }

    for t in teams:
        team_name = t.get("teamFullName", "")
        team_abbrev = team_abbrev_map.get(team_name)

        if not team_abbrev:
            logger.warning("unknown_team", team_name=team_name)
            continue

        gp = t.get("gamesPlayed", 1)
        gf = t.get("goalsFor", 0)
        ga = t.get("goalsAgainst", 0)

        await db.execute(
            text("""
                INSERT INTO team_season_stats (
                    team_abbrev, season, games_played, wins, losses, ot_losses, points,
                    goals_for, goals_for_per_game, shots_for_per_game, power_play_pct,
                    goals_against, goals_against_per_game, shots_against_per_game, penalty_kill_pct,
                    total_goals_per_game, created_at, updated_at
                ) VALUES (
                    :team_abbrev, :season, :games_played, :wins, :losses, :ot_losses, :points,
                    :goals_for, :gf_per_game, :sf_per_game, :pp_pct,
                    :goals_against, :ga_per_game, :sa_per_game, :pk_pct,
                    :total_gpg, NOW(), NOW()
                )
                ON CONFLICT (team_abbrev, season) DO UPDATE SET
                    games_played = EXCLUDED.games_played,
                    wins = EXCLUDED.wins,
                    losses = EXCLUDED.losses,
                    ot_losses = EXCLUDED.ot_losses,
                    points = EXCLUDED.points,
                    goals_for = EXCLUDED.goals_for,
                    goals_for_per_game = EXCLUDED.goals_for_per_game,
                    shots_for_per_game = EXCLUDED.shots_for_per_game,
                    power_play_pct = EXCLUDED.power_play_pct,
                    goals_against = EXCLUDED.goals_against,
                    goals_against_per_game = EXCLUDED.goals_against_per_game,
                    shots_against_per_game = EXCLUDED.shots_against_per_game,
                    penalty_kill_pct = EXCLUDED.penalty_kill_pct,
                    total_goals_per_game = EXCLUDED.total_goals_per_game,
                    updated_at = NOW()
            """),
            {
                "team_abbrev": team_abbrev,
                "season": season,
                "games_played": gp,
                "wins": t.get("wins", 0),
                "losses": t.get("losses", 0),
                "ot_losses": t.get("otLosses", 0),
                "points": t.get("points", 0),
                "goals_for": gf,
                "gf_per_game": t.get("goalsForPerGame"),
                "sf_per_game": t.get("shotsForPerGame"),
                "pp_pct": t.get("powerPlayPct"),
                "goals_against": ga,
                "ga_per_game": t.get("goalsAgainstPerGame"),
                "sa_per_game": t.get("shotsAgainstPerGame"),
                "pk_pct": t.get("penaltyKillPct"),
                "total_gpg": (gf + ga) / gp if gp > 0 else 0,
            }
        )
        count += 1

    await db.commit()
    logger.info("ingested_team_stats", season=season, count=count)
    return count


# Early in a season a team's 2-3 games of data is mostly noise. Shrink
# current-season rates toward last season until this many games are in.
TEAM_PRIOR_GAMES = 15
GOALIE_PRIOR_STARTS = 8


def _prior_season_code(season: str) -> str:
    start = int(season[:4]) - 1
    return f"{start}{start + 1}"


def _blend(current: float, current_n: int, prior: float | None, prior_n: int) -> float:
    if prior is None or prior_n <= 0:
        return current
    return (current * current_n + prior * prior_n) / (current_n + prior_n)


async def _fetch_team_pace_row(db: AsyncSession, team_abbrev: str, season: str):
    result = await db.execute(
        text("""
            SELECT
                games_played,
                goals_for_per_game, goals_against_per_game, total_goals_per_game,
                shots_for_per_game, shots_against_per_game,
                power_play_pct, penalty_kill_pct
            FROM team_season_stats
            WHERE team_abbrev = :team AND season = :season
        """),
        {"team": team_abbrev, "season": season}
    )
    return result.fetchone()


async def get_team_pace(db: AsyncSession, team_abbrev: str, season: str | None = None) -> dict | None:
    """
    Get team pace/strength metrics.

    Blends toward last season while the current sample is small, so the
    first weeks of October don't swing on a single 7-1 game.
    """
    season = season or get_current_season_code()
    row = await _fetch_team_pace_row(db, team_abbrev, season)
    current_gp = (row.games_played or 0) if row else 0

    prior = None
    prior_weight = 0
    if current_gp < TEAM_PRIOR_GAMES:
        prior = await _fetch_team_pace_row(db, team_abbrev, _prior_season_code(season))
        if prior:
            prior_weight = TEAM_PRIOR_GAMES - current_gp

    if not row and not prior:
        return None

    def field(name: str) -> float:
        cur = float(getattr(row, name) or 0) if row else 0.0
        pri = float(getattr(prior, name) or 0) if prior else None
        return _blend(cur, current_gp, pri, prior_weight)

    return {
        "goals_for_pg": field("goals_for_per_game"),
        "goals_against_pg": field("goals_against_per_game"),
        "total_goals_pg": field("total_goals_per_game"),
        "shots_for_pg": field("shots_for_per_game"),
        "shots_against_pg": field("shots_against_per_game"),
        "pp_pct": field("power_play_pct"),
        "pk_pct": field("penalty_kill_pct"),
        "games_played": current_gp,
        "blended_with_prior": prior_weight > 0,
    }


async def get_goalie_stats(db: AsyncSession, team_abbrev: str, season: str | None = None) -> dict | None:
    """
    Get the likely starting goalie for a team.

    Mid-season: the goalie with the most starts this season. Early season
    (or after an off-season move) that's unreliable, so we also consider
    goalies currently rostered on the team (players.team_abbrev, kept fresh
    by roster sync) using their prior-season workload, and shrink save% /
    GAA toward the prior season until GOALIE_PRIOR_STARTS starts.
    """
    season = season or get_current_season_code()
    prior_season = _prior_season_code(season)
    result = await db.execute(
        text("""
            SELECT
                p.id, p.name,
                cur.save_pct, cur.goals_against_avg,
                COALESCE(cur.games_started, 0) AS games_started,
                cur.wins, cur.losses, cur.shutouts,
                pri.save_pct AS prior_save_pct,
                pri.goals_against_avg AS prior_gaa,
                COALESCE(pri.games_started, 0) AS prior_games_started
            FROM players p
            LEFT JOIN goalie_stats cur
              ON cur.player_id = p.id AND cur.season = :season
            LEFT JOIN goalie_stats pri
              ON pri.player_id = p.id AND pri.season = :prior_season
            WHERE (cur.team_abbrev = :team)
               OR (p.team_abbrev = :team AND p.position = 'G')
            ORDER BY COALESCE(cur.games_started, 0) * 4
                     + COALESCE(pri.games_started, 0) DESC
            LIMIT 1
        """),
        {"team": team_abbrev, "season": season, "prior_season": prior_season}
    )
    row = result.fetchone()
    if not row:
        return None

    starts = row.games_started or 0
    prior_weight = max(0, GOALIE_PRIOR_STARTS - starts) if row.prior_save_pct else 0
    cur_sv = float(row.save_pct) if row.save_pct else 0.900
    cur_gaa = float(row.goals_against_avg) if row.goals_against_avg else 3.0
    if starts == 0 and row.prior_save_pct:
        save_pct = float(row.prior_save_pct)
        gaa = float(row.prior_gaa) if row.prior_gaa else 3.0
    else:
        save_pct = _blend(cur_sv, starts, float(row.prior_save_pct) if row.prior_save_pct else None, prior_weight)
        gaa = _blend(cur_gaa, starts, float(row.prior_gaa) if row.prior_gaa else None, prior_weight)

    return {
        "name": row.name,
        "save_pct": round(save_pct, 4),
        "gaa": round(gaa, 2),
        "games_started": starts,
        "wins": row.wins or 0,
        "losses": row.losses or 0,
        "shutouts": row.shutouts or 0,
        "blended_with_prior": prior_weight > 0,
    }


async def get_matchup_context(
    db: AsyncSession,
    home_team: str,
    away_team: str,
    season: str | None = None
) -> dict:
    """
    Get full matchup context including team pace and goalie matchups.
    """
    season = season or get_current_season_code()
    home_pace = await get_team_pace(db, home_team, season)
    away_pace = await get_team_pace(db, away_team, season)
    home_goalie = await get_goalie_stats(db, home_team, season)
    away_goalie = await get_goalie_stats(db, away_team, season)

    # Calculate expected game pace
    if home_pace and away_pace:
        # Average of both teams' total goals per game
        expected_total_goals = (home_pace["total_goals_pg"] + away_pace["total_goals_pg"]) / 2
        # Adjust for opponent - high scoring team vs weak defense = higher
        home_offense_vs_away_defense = (home_pace["goals_for_pg"] + away_pace["goals_against_pg"]) / 2
        away_offense_vs_home_defense = (away_pace["goals_for_pg"] + home_pace["goals_against_pg"]) / 2
    else:
        expected_total_goals = 6.0  # League average ~6 goals per game
        home_offense_vs_away_defense = 3.0
        away_offense_vs_home_defense = 3.0

    return {
        "home_team": {
            "abbrev": home_team,
            "pace": home_pace,
            "goalie": home_goalie,
        },
        "away_team": {
            "abbrev": away_team,
            "pace": away_pace,
            "goalie": away_goalie,
        },
        "expected_total_goals": round(expected_total_goals, 2),
        "home_expected_goals": round(home_offense_vs_away_defense, 2),
        "away_expected_goals": round(away_offense_vs_home_defense, 2),
    }


async def refresh_all_stats(season: str | None = None) -> dict:
    """Refresh all team and goalie stats."""
    season = season or get_current_season_code()
    async with async_session_maker() as db:
        goalies = await ingest_goalie_stats(db, season)
        teams = await ingest_team_stats(db, season)
        return {"goalies": goalies, "teams": teams}
