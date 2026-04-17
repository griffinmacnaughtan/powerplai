"""
Playoff analysis for PowerplAI.

Provides:
- Active playoff detection (based on scheduled/in-progress game_type=3 games)
- Bracket construction from game_logs + games (team series records)
- Per-player career playoff experience (games played, goals, assists, PPG)
- Playoff overview: top performers, Cinderella candidates, scoring pace
- "Most likely bets" ranked across tonight's playoff slate

The NHL marks game_type=3 as playoff games. We lean entirely on that flag
rather than inferring from dates, so this works across seasons and is
safe to run during the regular season (returns empty/inactive).
"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


PLAYOFF_GAME_TYPE = 3
REGULAR_GAME_TYPE = 2


@dataclass
class PlayoffExperience:
    """Career playoff performance for a single player."""
    player_id: int
    games: int
    goals: int
    assists: int
    points: int
    ppg: float  # points per game across all career playoff games
    gpg: float
    # Multiplier applied to the "experience" factor in predictions.
    # 1.00 = league-average rookie baseline. >1 = proven performer.
    experience_multiplier: float


async def is_playoffs_active(db: AsyncSession, on_date: date | None = None) -> bool:
    """Are we currently inside NHL playoffs (any game_type=3 game scheduled in a +/- 14d window)?"""
    target = on_date or date.today()
    window_start = target - timedelta(days=7)
    window_end = target + timedelta(days=14)

    result = await db.execute(
        text("""
            SELECT COUNT(*) AS c
            FROM games
            WHERE game_type = :pt
              AND game_date BETWEEN :start AND :end
        """),
        {"pt": PLAYOFF_GAME_TYPE, "start": window_start, "end": window_end},
    )
    row = result.fetchone()
    return bool(row and row.c > 0)


async def get_current_playoff_season(db: AsyncSession) -> str | None:
    """Return the most recent season that has any playoff games."""
    result = await db.execute(
        text("""
            SELECT season
            FROM games
            WHERE game_type = :pt
            ORDER BY game_date DESC
            LIMIT 1
        """),
        {"pt": PLAYOFF_GAME_TYPE},
    )
    row = result.fetchone()
    return row.season if row else None


async def get_player_playoff_experience(
    db: AsyncSession,
    player_id: int,
) -> PlayoffExperience:
    """
    Career playoff experience for a player.

    Joins game_logs to games (on nhl_game_id) filtered by game_type=3 so we
    don't depend on a separate flag on game_logs.
    """
    result = await db.execute(
        text("""
            SELECT
                COUNT(*) AS games,
                COALESCE(SUM(gl.goals), 0) AS goals,
                COALESCE(SUM(gl.assists), 0) AS assists,
                COALESCE(SUM(gl.points), 0) AS points
            FROM game_logs gl
            JOIN games g ON g.nhl_game_id = gl.game_id
            WHERE gl.player_id = :pid
              AND g.game_type = :pt
        """),
        {"pid": player_id, "pt": PLAYOFF_GAME_TYPE},
    )
    row = result.fetchone()

    games = int(row.games) if row and row.games else 0
    goals = int(row.goals) if row else 0
    assists = int(row.assists) if row else 0
    points = int(row.points) if row else 0

    ppg = float(points) / games if games > 0 else 0.0
    gpg = float(goals) / games if games > 0 else 0.0

    # Experience multiplier rewards both sample depth and per-game output.
    # - 0 career playoff games → 1.00 (neutral, no penalty)
    # - 20+ games at ~1.0 PPG → ~1.25
    # - 50+ games at ~1.2 PPG → ~1.45 (capped)
    depth = min(games / 30.0, 1.0)         # saturates at 30 games
    output = min(ppg / 0.85, 1.5)          # 0.85 PPG is elite playoff rate
    multiplier = 1.0 + 0.30 * depth * output
    multiplier = min(multiplier, 1.45)

    return PlayoffExperience(
        player_id=player_id,
        games=games,
        goals=goals,
        assists=assists,
        points=points,
        ppg=round(ppg, 3),
        gpg=round(gpg, 3),
        experience_multiplier=round(multiplier, 3),
    )


async def _series_games(
    db: AsyncSession,
    season: str,
) -> list[dict[str, Any]]:
    """All playoff games for the season, ordered by date."""
    result = await db.execute(
        text("""
            SELECT nhl_game_id, game_date, home_team_abbrev, away_team_abbrev,
                   home_score, away_score, game_state, is_completed, start_time_utc
            FROM games
            WHERE game_type = :pt AND season = :season
            ORDER BY game_date ASC, nhl_game_id ASC
        """),
        {"pt": PLAYOFF_GAME_TYPE, "season": season},
    )
    return [dict(r._mapping) for r in result.fetchall()]


def _series_key(team_a: str, team_b: str) -> tuple[str, str]:
    """Canonical key (alphabetical) so home/away swaps map to the same series."""
    return (team_a, team_b) if team_a < team_b else (team_b, team_a)


async def get_playoff_bracket(
    db: AsyncSession,
    season: str | None = None,
) -> dict[str, Any]:
    """
    Build a lightweight bracket view from recorded playoff games.

    Returns:
        {
            "season": "20252026",
            "round": 1,            # inferred from active series count
            "series": [
                {
                    "team_a": "TOR", "team_b": "BOS",
                    "team_a_wins": 2, "team_b_wins": 1,
                    "games_played": 3, "status": "in_progress" | "complete" | "scheduled",
                    "winner": "TOR" | null, "next_game_date": "2026-04-19" | null,
                    "next_game_time": "2026-04-19T23:00:00" | null,
                }, ...
            ]
        }
    """
    if season is None:
        season = await get_current_playoff_season(db)
    if not season:
        return {"season": None, "round": 0, "series": []}

    games = await _series_games(db, season)

    series_map: dict[tuple[str, str], dict[str, Any]] = {}
    for g in games:
        key = _series_key(g["home_team_abbrev"], g["away_team_abbrev"])
        s = series_map.setdefault(
            key,
            {
                "team_a": key[0],
                "team_b": key[1],
                "team_a_wins": 0,
                "team_b_wins": 0,
                "games_played": 0,
                "games_total": 0,
                "winner": None,
                "next_game_date": None,
                "next_game_time": None,
            },
        )
        s["games_total"] += 1

        completed = g["is_completed"] or g["game_state"] in ("FINAL", "OFF")
        if completed and g["home_score"] is not None and g["away_score"] is not None:
            s["games_played"] += 1
            winner = g["home_team_abbrev"] if g["home_score"] > g["away_score"] else g["away_team_abbrev"]
            if winner == key[0]:
                s["team_a_wins"] += 1
            else:
                s["team_b_wins"] += 1
        else:
            # Track earliest upcoming game for this series
            if s["next_game_date"] is None or (g["game_date"] and g["game_date"] < s["next_game_date"]):
                s["next_game_date"] = g["game_date"]
                s["next_game_time"] = g["start_time_utc"]

    out_series: list[dict[str, Any]] = []
    for s in series_map.values():
        if s["team_a_wins"] >= 4:
            s["winner"] = s["team_a"]
            status = "complete"
        elif s["team_b_wins"] >= 4:
            s["winner"] = s["team_b"]
            status = "complete"
        elif s["games_played"] == 0:
            status = "scheduled"
        else:
            status = "in_progress"
        s["status"] = status

        # Serialize dates
        if s["next_game_date"] is not None:
            s["next_game_date"] = s["next_game_date"].isoformat()
        if s["next_game_time"] is not None:
            s["next_game_time"] = s["next_game_time"].isoformat()

        del s["games_total"]
        out_series.append(s)

    # Sort: in-progress first, then scheduled, then complete; then by wins descending
    status_order = {"in_progress": 0, "scheduled": 1, "complete": 2}
    out_series.sort(key=lambda x: (status_order[x["status"]], -(x["team_a_wins"] + x["team_b_wins"])))

    # Round inference: first round = 8 series, second = 4, conf finals = 2, cup = 1.
    n = len(out_series)
    if n >= 8:
        current_round = 1
    elif n >= 4:
        current_round = 2
    elif n >= 2:
        current_round = 3
    elif n == 1:
        current_round = 4
    else:
        current_round = 0

    return {"season": season, "round": current_round, "series": out_series}


async def get_playoff_overview(
    db: AsyncSession,
    season: str | None = None,
    top_n: int = 5,
) -> dict[str, Any]:
    """
    Headline playoff stats: games played, avg goals/game, top scorers, hottest teams.
    """
    if season is None:
        season = await get_current_playoff_season(db)
    if not season:
        return {
            "season": None,
            "games_completed": 0,
            "avg_goals_per_game": 0.0,
            "total_goals": 0,
            "top_scorers": [],
            "hottest_teams": [],
        }

    games_res = await db.execute(
        text("""
            SELECT
                COUNT(*) FILTER (WHERE is_completed) AS completed,
                COALESCE(SUM(home_score + away_score) FILTER (WHERE is_completed), 0) AS total_goals
            FROM games
            WHERE game_type = :pt AND season = :season
        """),
        {"pt": PLAYOFF_GAME_TYPE, "season": season},
    )
    grow = games_res.fetchone()
    completed = int(grow.completed) if grow and grow.completed else 0
    total_goals = int(grow.total_goals) if grow and grow.total_goals else 0
    avg_gpg = round(total_goals / completed, 2) if completed else 0.0

    # Top scorers this postseason
    scorers_res = await db.execute(
        text("""
            SELECT
                p.id AS player_id,
                p.name,
                gl.team_abbrev AS team,
                COUNT(*) AS games,
                COALESCE(SUM(gl.goals), 0) AS goals,
                COALESCE(SUM(gl.assists), 0) AS assists,
                COALESCE(SUM(gl.points), 0) AS points
            FROM game_logs gl
            JOIN players p ON p.id = gl.player_id
            JOIN games g ON g.nhl_game_id = gl.game_id
            WHERE g.game_type = :pt
              AND g.season = :season
            GROUP BY p.id, p.name, gl.team_abbrev
            HAVING COUNT(*) > 0
            ORDER BY points DESC, goals DESC
            LIMIT :top_n
        """),
        {"pt": PLAYOFF_GAME_TYPE, "season": season, "top_n": top_n},
    )
    top_scorers = [
        {
            "player_id": r.player_id,
            "name": r.name,
            "team": r.team,
            "games": int(r.games),
            "goals": int(r.goals),
            "assists": int(r.assists),
            "points": int(r.points),
            "ppg": round(float(r.points) / r.games, 2) if r.games else 0.0,
        }
        for r in scorers_res.fetchall()
    ]

    # Team records in the playoffs
    teams_res = await db.execute(
        text("""
            WITH team_games AS (
                SELECT home_team_abbrev AS team,
                       (home_score > away_score)::int AS win,
                       (home_score < away_score)::int AS loss,
                       home_score AS gf, away_score AS ga
                FROM games
                WHERE game_type = :pt AND season = :season AND is_completed
                UNION ALL
                SELECT away_team_abbrev AS team,
                       (away_score > home_score)::int AS win,
                       (away_score < home_score)::int AS loss,
                       away_score AS gf, home_score AS ga
                FROM games
                WHERE game_type = :pt AND season = :season AND is_completed
            )
            SELECT team,
                   COUNT(*) AS gp,
                   SUM(win) AS wins,
                   SUM(loss) AS losses,
                   COALESCE(SUM(gf), 0) AS gf,
                   COALESCE(SUM(ga), 0) AS ga
            FROM team_games
            GROUP BY team
            ORDER BY wins DESC, (SUM(gf) - SUM(ga)) DESC
            LIMIT :top_n
        """),
        {"pt": PLAYOFF_GAME_TYPE, "season": season, "top_n": top_n},
    )
    hottest_teams = [
        {
            "team": r.team,
            "games": int(r.gp),
            "wins": int(r.wins or 0),
            "losses": int(r.losses or 0),
            "goal_diff": int((r.gf or 0) - (r.ga or 0)),
        }
        for r in teams_res.fetchall()
    ]

    return {
        "season": season,
        "games_completed": completed,
        "avg_goals_per_game": avg_gpg,
        "total_goals": total_goals,
        "top_scorers": top_scorers,
        "hottest_teams": hottest_teams,
    }


async def get_tonight_playoff_games(
    db: AsyncSession,
    target_date: date | None = None,
) -> list[dict[str, Any]]:
    """Playoff games scheduled for today (or a given date)."""
    d = target_date or date.today()
    result = await db.execute(
        text("""
            SELECT nhl_game_id, home_team_abbrev, away_team_abbrev, start_time_utc, venue, game_state
            FROM games
            WHERE game_type = :pt AND game_date = :d
            ORDER BY start_time_utc
        """),
        {"pt": PLAYOFF_GAME_TYPE, "d": d},
    )
    return [
        {
            "game_id": r.nhl_game_id,
            "home_team": r.home_team_abbrev,
            "away_team": r.away_team_abbrev,
            "start_time": r.start_time_utc.isoformat() if r.start_time_utc else None,
            "venue": r.venue,
            "state": r.game_state,
        }
        for r in result.fetchall()
    ]


async def get_most_likely_playoff_bets(
    db: AsyncSession,
    target_date: date | None = None,
    top_n: int = 5,
) -> dict[str, Any]:
    """
    Rank player prop bets for tonight's playoff slate.

    Uses the same prediction engine as regular season, but asks it to
    flag playoff games so the experience factor kicks in.
    """
    from backend.src.agents.predictions import PredictionEngine

    d = target_date or date.today()
    games = await get_tonight_playoff_games(db, d)
    if not games:
        return {"date": d.isoformat(), "is_playoffs": False, "picks": []}

    engine = PredictionEngine(db=db)
    all_preds = []
    for g in games:
        try:
            matchup = await engine.get_matchup_prediction(
                db,
                home_team=g["home_team"],
                away_team=g["away_team"],
                game_date=d,
                top_n=10,
                is_playoff=True,
            )
            for p in matchup.top_scorers:
                all_preds.append(p)
        except Exception as e:
            logger.warning("playoff_matchup_prediction_failed", home=g["home_team"], away=g["away_team"], error=str(e))

    # Rank by point probability (broader than goals, more bets hit); break ties by confidence
    all_preds.sort(key=lambda p: (p.prob_point, p.confidence_score), reverse=True)
    top = all_preds[:top_n]

    picks = []
    for p in top:
        # Pick the stronger leg type based on the probabilities
        if p.prob_goal >= 0.55:
            market, prob, line = "Anytime Goal Scorer", p.prob_goal, "1+ Goal"
        elif p.prob_multi_point >= 0.35:
            market, prob, line = "2+ Points", p.prob_multi_point, "2+ Pts"
        else:
            market, prob, line = "Anytime Point", p.prob_point, "1+ Pt"

        picks.append({
            "player_name": p.player_name,
            "team": p.team,
            "opponent": p.opponent,
            "is_home": p.is_home,
            "market": market,
            "line": line,
            "probability": round(prob, 3),
            "prob_goal": p.prob_goal,
            "prob_point": p.prob_point,
            "confidence": p.confidence,
            "expected_points": p.expected_points,
            "opponent_goalie": p.opponent_goalie,
            "factors": p.factors[:3],
        })

    return {"date": d.isoformat(), "is_playoffs": True, "picks": picks, "games": len(games)}
