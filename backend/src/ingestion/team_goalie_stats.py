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

logger = structlog.get_logger()
settings = get_settings()

NHL_STATS_API = "https://api.nhle.com/stats/rest/en"


async def fetch_goalie_stats(season: str = "20252026") -> list[dict]:
    """Fetch all goalie stats from NHL Stats API."""
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


async def fetch_team_stats(season: str = "20252026") -> list[dict]:
    """Fetch all team stats from NHL Stats API."""
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


async def ingest_goalie_stats(db: AsyncSession, season: str = "20252026") -> int:
    """
    Ingest goalie statistics for the season.

    Returns number of goalies upserted.
    """
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


async def ingest_team_stats(db: AsyncSession, season: str = "20252026") -> int:
    """
    Ingest team statistics for the season.

    Returns number of teams upserted.
    """
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


async def get_team_pace(db: AsyncSession, team_abbrev: str, season: str = "20252026") -> dict | None:
    """Get team pace/strength metrics."""
    result = await db.execute(
        text("""
            SELECT
                goals_for_per_game, goals_against_per_game, total_goals_per_game,
                shots_for_per_game, shots_against_per_game,
                power_play_pct, penalty_kill_pct
            FROM team_season_stats
            WHERE team_abbrev = :team AND season = :season
        """),
        {"team": team_abbrev, "season": season}
    )
    row = result.fetchone()
    if not row:
        return None

    return {
        "goals_for_pg": float(row.goals_for_per_game) if row.goals_for_per_game else 0,
        "goals_against_pg": float(row.goals_against_per_game) if row.goals_against_per_game else 0,
        "total_goals_pg": float(row.total_goals_per_game) if row.total_goals_per_game else 0,
        "shots_for_pg": float(row.shots_for_per_game) if row.shots_for_per_game else 0,
        "shots_against_pg": float(row.shots_against_per_game) if row.shots_against_per_game else 0,
        "pp_pct": float(row.power_play_pct) if row.power_play_pct else 0,
        "pk_pct": float(row.penalty_kill_pct) if row.penalty_kill_pct else 0,
    }


async def get_goalie_stats(db: AsyncSession, team_abbrev: str, season: str = "20252026") -> dict | None:
    """Get the starting goalie stats for a team (goalie with most starts)."""
    result = await db.execute(
        text("""
            SELECT
                p.name, gs.save_pct, gs.goals_against_avg,
                gs.games_started, gs.wins, gs.losses, gs.shutouts
            FROM goalie_stats gs
            JOIN players p ON gs.player_id = p.id
            WHERE gs.team_abbrev = :team AND gs.season = :season
            ORDER BY gs.games_started DESC
            LIMIT 1
        """),
        {"team": team_abbrev, "season": season}
    )
    row = result.fetchone()
    if not row:
        return None

    return {
        "name": row.name,
        "save_pct": float(row.save_pct) if row.save_pct else 0.900,
        "gaa": float(row.goals_against_avg) if row.goals_against_avg else 3.0,
        "games_started": row.games_started,
        "wins": row.wins,
        "losses": row.losses,
        "shutouts": row.shutouts,
    }


async def get_matchup_context(
    db: AsyncSession,
    home_team: str,
    away_team: str,
    season: str = "20252026"
) -> dict:
    """
    Get full matchup context including team pace and goalie matchups.
    """
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


async def refresh_all_stats(season: str = "20252026") -> dict:
    """Refresh all team and goalie stats."""
    async with async_session_maker() as db:
        goalies = await ingest_goalie_stats(db, season)
        teams = await ingest_team_stats(db, season)
        return {"goalies": goalies, "teams": teams}
