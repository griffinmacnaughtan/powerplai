"""
Playoff analysis for PowerplAI.

Provides:
- Active playoff detection (based on scheduled/in-progress game_type=3 games)
- Bracket construction from game_logs + games (team series records)
- Per-player career playoff experience (games played, goals, assists, PPG)
- Playoff overview: top performers, Cinderella candidates, scoring pace
- Nightly top-3 picks, persisted and graded against actual outcomes
- Aggregate record (hit rate, streak) that auto-updates daily

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


# ── Nightly picks schema ─────────────────────────────────────────────────────
PICKS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS playoff_picks (
    id               SERIAL PRIMARY KEY,
    game_date        DATE NOT NULL,
    generated_at     TIMESTAMP DEFAULT NOW(),
    rank             INTEGER NOT NULL,
    player_id        INTEGER,
    player_name      VARCHAR(255) NOT NULL,
    team             VARCHAR(10) NOT NULL,
    opponent         VARCHAR(10) NOT NULL,
    is_home          BOOLEAN,
    game_id          INTEGER,
    market           VARCHAR(50) NOT NULL,
    line             VARCHAR(30),
    probability      FLOAT NOT NULL,
    prob_goal        FLOAT,
    prob_point       FLOAT,
    confidence       VARCHAR(10),
    opponent_goalie  VARCHAR(255),
    validated_at     TIMESTAMP,
    hit              BOOLEAN,
    actual_goals     INTEGER,
    actual_assists   INTEGER,
    actual_points    INTEGER,
    UNIQUE (game_date, rank)
);

CREATE INDEX IF NOT EXISTS idx_playoff_picks_date ON playoff_picks(game_date);
CREATE INDEX IF NOT EXISTS idx_playoff_picks_validated ON playoff_picks(validated_at);
"""


async def create_playoff_picks_table(db: AsyncSession) -> None:
    """Create the playoff_picks table if missing. Safe to call on every startup."""
    statements = [s.strip() for s in PICKS_TABLE_SQL.split(";") if s.strip()]
    for stmt in statements:
        await db.execute(text(stmt))
    await db.commit()


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
    """
    Are we currently inside NHL playoffs?

    Two conditions, either is sufficient:

    1. There is a playoff (game_type=3) game in a generous date window
       around `on_date` (covers normal operation: scheduled, today, just
       completed).

    2. The most recent season has at least one playoff series that is
       neither finished nor empty - i.e. a series where neither team has
       4 wins yet, but at least one game has been played. This is the
       safety net that keeps the dashboard live even when our schedule
       sync briefly lags behind the NHL API (e.g. between rounds, while
       the next bracket is being announced).

    The window in (1) is asymmetric: a long backward arm so a series that
    just wrapped is still "active" for a day or two of dashboard tail,
    and a forward arm long enough to span the typical 2-3 day gap
    between rounds.
    """
    target = on_date or date.today()
    window_start = target - timedelta(days=10)
    window_end = target + timedelta(days=21)

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
    if row and row.c > 0:
        return True

    # Safety net: an in-progress series exists in the most recent playoff
    # season. This handles the case where the schedule sync lapsed and
    # the most recent game in the DB is now older than the window above.
    season = await get_current_playoff_season(db)
    if not season:
        return False

    # Latest playoff game date for this season - if it's within the last
    # ~30 days it almost certainly means we're still mid-postseason.
    latest = await db.execute(
        text("""
            SELECT MAX(game_date) AS d
            FROM games
            WHERE game_type = :pt AND season = :season
        """),
        {"pt": PLAYOFF_GAME_TYPE, "season": season},
    )
    lrow = latest.fetchone()
    if not lrow or not lrow.d:
        return False

    days_since = (target - lrow.d).days
    return 0 <= days_since <= 30


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

    # Ghost-game filter. The DB accumulates placeholder schedule rows from
    # early in the postseason that never got finalized (e.g. round-1
    # matchups inserted before the real bracket was set). A game is a
    # ghost if its date is well in the past and it's still flagged as
    # not-completed - the schedule sync has had ample chances to update
    # it by now and hasn't.
    today = date.today()
    GHOST_THRESHOLD_DAYS = 5

    def _is_ghost(g: dict) -> bool:
        completed = g["is_completed"] or g["game_state"] in ("FINAL", "OFF")
        if completed:
            return False
        gd = g.get("game_date")
        if not gd:
            return False
        return (today - gd).days > GHOST_THRESHOLD_DAYS

    series_map: dict[tuple[str, str], dict[str, Any]] = {}
    for g in games:
        if _is_ghost(g):
            continue
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
                "last_game_date": None,
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
            gd = g.get("game_date")
            if gd and (s["last_game_date"] is None or gd > s["last_game_date"]):
                s["last_game_date"] = gd
        else:
            # Track earliest upcoming game for this series
            if s["next_game_date"] is None or (g["game_date"] and g["game_date"] < s["next_game_date"]):
                s["next_game_date"] = g["game_date"]
                s["next_game_time"] = g["start_time_utc"]

    out_series: list[dict[str, Any]] = []
    for s in series_map.values():
        # Drop empty shells (series with no surviving games after ghost filter).
        if s["games_total"] == 0:
            continue

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

        # Drop completed series that wrapped a long time ago - they're
        # historical context, not part of the active bracket view.
        if status == "complete" and s["last_game_date"]:
            days_since = (today - s["last_game_date"]).days
            if days_since > 28:
                continue

        # Drop orphan series: in_progress with no scheduled next game AND
        # last completed game is more than a week old. These are series
        # we have partial data for (e.g. the keep-alive lapsed mid-round
        # and we never picked up games 3-7), not anything fans care about.
        if status == "in_progress" and not s["next_game_date"]:
            if s["last_game_date"] and (today - s["last_game_date"]).days > 7:
                continue

        # Serialize dates
        if s["next_game_date"] is not None:
            s["next_game_date"] = s["next_game_date"].isoformat()
        if s["next_game_time"] is not None:
            s["next_game_time"] = s["next_game_time"].isoformat()
        if s["last_game_date"] is not None:
            s["last_game_date"] = s["last_game_date"].isoformat()

        del s["games_total"]
        out_series.append(s)

    # Sort: in-progress first (drama is happening right now), then scheduled,
    # then complete in reverse chronological order (most-recent wraps at top
    # so a fan opening the page sees the round that just ended, not round 1).
    # Python's sort is stable, so we do this in two passes.
    status_order = {"in_progress": 0, "scheduled": 1, "complete": 2}
    # Pass 1: order all series by last_game_date DESC. ISO dates sort
    # lexicographically so an empty string ("" for series with no completed
    # games yet) falls to the bottom.
    out_series.sort(key=lambda x: x.get("last_game_date") or "", reverse=True)
    # Pass 2: group by status. Stable sort preserves the date order within
    # each status bucket.
    out_series.sort(key=lambda x: status_order[x["status"]])

    # Round inference. NHL bracket has 8 series in round 1, 4 in round 2,
    # 2 in conference finals, 1 in Stanley Cup Final - so once round 1
    # wraps there are 8 complete series total, after round 2 there are
    # 12, after conference finals 14, after the Final 15.
    #
    # Counting complete series is the most robust signal because active
    # series can legitimately be 1 (one Cup Final game and one conference
    # final still wrapping) without us being in the Final yet. We use the
    # complete count as the floor and only advance past it if the active
    # count corroborates.
    active_count = sum(1 for s in out_series if s["status"] in ("in_progress", "scheduled"))
    complete_count = sum(1 for s in out_series if s["status"] == "complete")

    # Floor from complete count
    if complete_count >= 14:
        floor_round = 4   # Stanley Cup Final
    elif complete_count >= 12:
        floor_round = 3   # Conference Finals
    elif complete_count >= 8:
        floor_round = 2   # Second Round
    else:
        floor_round = 1   # First Round

    # Ceiling from active count (don't claim a later round than active series allow)
    if active_count >= 8:
        ceiling_round = 1
    elif active_count >= 4:
        ceiling_round = 2
    elif active_count >= 2:
        ceiling_round = 3
    elif active_count == 1:
        ceiling_round = 4
    else:
        # No active series at all - we're between rounds or postseason is over.
        # Use the floor only.
        ceiling_round = floor_round

    current_round = max(floor_round, ceiling_round) if active_count else floor_round
    # When nothing has even started, report 0 so the UI can show pre-postseason copy.
    if active_count == 0 and complete_count == 0:
        current_round = 0
    # Suppress the silly fallback path where we no-longer-have-anything-active
    # and complete_count somehow gives floor=1.
    n = active_count if active_count > 0 else complete_count  # noqa: F841 - kept for telemetry context

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
            SELECT nhl_game_id, game_date, home_team_abbrev, away_team_abbrev, start_time_utc, venue, game_state
            FROM games
            WHERE game_type = :pt AND game_date = :d
            ORDER BY start_time_utc
        """),
        {"pt": PLAYOFF_GAME_TYPE, "d": d},
    )
    return [
        {
            "game_id": r.nhl_game_id,
            "game_date": r.game_date.isoformat(),
            "home_team": r.home_team_abbrev,
            "away_team": r.away_team_abbrev,
            "start_time": r.start_time_utc.isoformat() if r.start_time_utc else None,
            "venue": r.venue,
            "state": r.game_state,
        }
        for r in result.fetchall()
    ]


async def get_upcoming_playoff_games(
    db: AsyncSession,
    start_date: date | None = None,
    days: int = 3,
) -> list[dict[str, Any]]:
    """Playoff games scheduled in a forward-looking window (inclusive)."""
    start = start_date or date.today()
    end = start + timedelta(days=max(0, days - 1))
    result = await db.execute(
        text("""
            SELECT nhl_game_id, game_date, home_team_abbrev, away_team_abbrev,
                   start_time_utc, venue, game_state
            FROM games
            WHERE game_type = :pt
              AND game_date BETWEEN :start AND :end
            ORDER BY game_date ASC, start_time_utc ASC
        """),
        {"pt": PLAYOFF_GAME_TYPE, "start": start, "end": end},
    )
    return [
        {
            "game_id": r.nhl_game_id,
            "game_date": r.game_date,
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
    start_date: date | None = None,
    top_n: int = 8,
    days: int = 3,
) -> dict[str, Any]:
    """
    Rank player prop bets across the next `days` of playoff games.

    Uses the same prediction engine as regular season, but asks it to
    flag playoff games so the experience factor kicks in. One pick per
    player across the window — we keep their highest-value leg so a
    star slated for multiple games doesn't dominate the list.
    """
    from backend.src.agents.predictions import PredictionEngine

    start = start_date or date.today()
    games = await get_upcoming_playoff_games(db, start, days)
    if not games:
        return {
            "start_date": start.isoformat(),
            "days": days,
            "is_playoffs": False,
            "games": 0,
            "picks": [],
        }

    engine = PredictionEngine(db=db)
    # Keep the single best prediction per player across the window
    by_player: dict[int, tuple[float, Any, dict[str, Any]]] = {}
    for g in games:
        try:
            matchup = await engine.get_matchup_prediction(
                db,
                home_team=g["home_team"],
                away_team=g["away_team"],
                game_date=g["game_date"],
                top_n=10,
                is_playoff=True,
            )
            for p in matchup.top_scorers:
                key = p.player_id
                # Rank same player across nights by point probability
                existing = by_player.get(key)
                if existing is None or p.prob_point > existing[0]:
                    by_player[key] = (p.prob_point, p, g)
        except Exception as e:
            logger.warning(
                "playoff_matchup_prediction_failed",
                home=g["home_team"], away=g["away_team"], date=str(g["game_date"]),
                error=str(e),
            )

    ranked = sorted(by_player.values(), key=lambda t: (t[0], t[1].confidence_score), reverse=True)

    picks = []
    for _, p, g in ranked[:top_n]:
        market, prob, line = _select_market(p)
        picks.append({
            "player_name": p.player_name,
            "team": p.team,
            "opponent": p.opponent,
            "is_home": p.is_home,
            "game_date": g["game_date"].isoformat(),
            "start_time": g["start_time"],
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

    return {
        "start_date": start.isoformat(),
        "days": days,
        "is_playoffs": True,
        "games": len(games),
        "picks": picks,
    }


def _select_market(p: Any) -> tuple[str, float, str]:
    """Choose the strongest prop market from a PlayerPrediction."""
    if p.prob_goal >= 0.55:
        return "Anytime Goal Scorer", p.prob_goal, "1+ Goal"
    if p.prob_multi_point >= 0.35:
        return "2+ Points", p.prob_multi_point, "2+ Pts"
    return "Anytime Point", p.prob_point, "1+ Pt"


# ── Nightly top-3 generation (persisted for record tracking) ────────────────

async def generate_nightly_playoff_picks(
    db: AsyncSession,
    target_date: date | None = None,
    per_night: int = 3,
) -> list[dict[str, Any]]:
    """
    Produce and persist the top `per_night` playoff picks for a single date.

    Skips re-generation if picks already exist for that date. Safe to call
    on a daily cron. Returns the picks written (or already present).
    """
    from backend.src.agents.predictions import PredictionEngine

    d = target_date or date.today()

    # Idempotent: skip if already generated for this date
    existing_res = await db.execute(
        text("SELECT rank, player_name, team, opponent, market, line, probability, prob_goal, prob_point, confidence, hit FROM playoff_picks WHERE game_date = :d ORDER BY rank"),
        {"d": d},
    )
    existing = existing_res.fetchall()
    if existing:
        return [dict(r._mapping) for r in existing]

    games = await get_tonight_playoff_games(db, d)
    if not games:
        return []

    engine = PredictionEngine(db=db)
    candidates: list[tuple[float, Any, dict[str, Any]]] = []
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
                candidates.append((p.prob_point, p, g))
        except Exception as e:
            logger.warning(
                "nightly_pick_matchup_failed",
                home=g["home_team"], away=g["away_team"], date=str(d), error=str(e),
            )

    if not candidates:
        return []

    # Rank by prob_point with confidence as tiebreak; one leg per player
    candidates.sort(key=lambda t: (t[0], t[1].confidence_score), reverse=True)
    chosen: list[tuple[Any, dict[str, Any]]] = []
    seen_players: set[int] = set()
    for _, p, g in candidates:
        if p.player_id in seen_players:
            continue
        seen_players.add(p.player_id)
        chosen.append((p, g))
        if len(chosen) >= per_night:
            break

    written: list[dict[str, Any]] = []
    for rank, (p, g) in enumerate(chosen, start=1):
        market, prob, line = _select_market(p)
        await db.execute(
            text("""
                INSERT INTO playoff_picks (
                    game_date, rank, player_id, player_name, team, opponent,
                    is_home, game_id, market, line, probability,
                    prob_goal, prob_point, confidence, opponent_goalie
                ) VALUES (
                    :game_date, :rank, :player_id, :player_name, :team, :opponent,
                    :is_home, :game_id, :market, :line, :probability,
                    :prob_goal, :prob_point, :confidence, :opponent_goalie
                )
                ON CONFLICT (game_date, rank) DO NOTHING
            """),
            {
                "game_date": d,
                "rank": rank,
                "player_id": p.player_id,
                "player_name": p.player_name,
                "team": p.team,
                "opponent": p.opponent,
                "is_home": p.is_home,
                "game_id": g["game_id"],
                "market": market,
                "line": line,
                "probability": round(prob, 4),
                "prob_goal": p.prob_goal,
                "prob_point": p.prob_point,
                "confidence": p.confidence,
                "opponent_goalie": p.opponent_goalie,
            },
        )
        written.append({
            "rank": rank,
            "player_name": p.player_name,
            "team": p.team,
            "opponent": p.opponent,
            "market": market,
            "line": line,
            "probability": round(prob, 3),
            "confidence": p.confidence,
        })

    await db.commit()
    logger.info("playoff_picks_generated", date=str(d), count=len(written))
    return written


async def validate_playoff_picks(
    db: AsyncSession,
    target_date: date | None = None,
) -> dict[str, Any]:
    """
    Grade any unvalidated picks for a date. Defaults to yesterday.

    A pick "hits" when the player's actual stat meets the market threshold:
    - Anytime Goal Scorer → goals >= 1
    - 2+ Points           → points >= 2
    - Anytime Point       → points >= 1
    """
    d = target_date or (date.today() - timedelta(days=1))

    rows_res = await db.execute(
        text("""
            SELECT id, player_id, player_name, market
            FROM playoff_picks
            WHERE game_date = :d AND validated_at IS NULL
        """),
        {"d": d},
    )
    rows = rows_res.fetchall()
    if not rows:
        return {"validated": 0, "date": str(d)}

    # Only grade once games are actually final for that date
    completeness = await db.execute(
        text("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE is_completed) AS done
            FROM games
            WHERE game_date = :d AND game_type = :pt
        """),
        {"d": d, "pt": PLAYOFF_GAME_TYPE},
    )
    crow = completeness.fetchone()
    if not crow or not crow.total or crow.done < crow.total:
        logger.info("playoff_picks_validate_skipped_incomplete", date=str(d),
                    done=(crow.done if crow else 0), total=(crow.total if crow else 0))
        return {"validated": 0, "date": str(d), "reason": "games_not_final"}

    validated = 0
    for r in rows:
        # Fetch actual line for the player on this date
        actual_res = await db.execute(
            text("""
                SELECT COALESCE(SUM(goals), 0) AS g,
                       COALESCE(SUM(assists), 0) AS a,
                       COALESCE(SUM(points), 0) AS p
                FROM game_logs
                WHERE player_id = :pid AND game_date = :d
            """),
            {"pid": r.player_id, "d": d},
        )
        arow = actual_res.fetchone()
        g = int(arow.g) if arow else 0
        a = int(arow.a) if arow else 0
        pts = int(arow.p) if arow else 0

        if r.market == "Anytime Goal Scorer":
            hit = g >= 1
        elif r.market == "2+ Points":
            hit = pts >= 2
        elif r.market == "Anytime Point":
            hit = pts >= 1
        else:
            hit = None

        await db.execute(
            text("""
                UPDATE playoff_picks
                SET validated_at = NOW(),
                    hit = :hit,
                    actual_goals = :g,
                    actual_assists = :a,
                    actual_points = :p
                WHERE id = :id
            """),
            {"hit": hit, "g": g, "a": a, "p": pts, "id": r.id},
        )
        validated += 1

    await db.commit()
    logger.info("playoff_picks_validated", date=str(d), count=validated)
    return {"validated": validated, "date": str(d)}


async def get_playoff_pick_record(
    db: AsyncSession,
    days: int = 30,
) -> dict[str, Any]:
    """Aggregate record + streak + recent nights of picks."""
    # Overall aggregate
    agg_res = await db.execute(
        text("""
            SELECT
                COUNT(*) FILTER (WHERE hit IS NOT NULL) AS graded,
                COUNT(*) FILTER (WHERE hit = TRUE) AS hits,
                COUNT(*) FILTER (WHERE hit = FALSE) AS misses,
                COUNT(*) AS total,
                COUNT(DISTINCT game_date) AS nights,
                AVG(probability) FILTER (WHERE hit IS NOT NULL) AS avg_prob
            FROM playoff_picks
            WHERE game_date >= CURRENT_DATE - (CAST(:days AS INTEGER) * INTERVAL '1 day')
        """),
        {"days": days},
    )
    agg = agg_res.fetchone()

    graded = int(agg.graded or 0)
    hits = int(agg.hits or 0)
    misses = int(agg.misses or 0)
    hit_rate = round(hits / graded, 3) if graded else None
    avg_prob = round(float(agg.avg_prob), 3) if agg and agg.avg_prob is not None else None

    # Per-night record (all 3 hit = "perfect" night)
    nights_res = await db.execute(
        text("""
            SELECT game_date,
                   COUNT(*) AS picks,
                   COUNT(*) FILTER (WHERE hit = TRUE) AS hits,
                   COUNT(*) FILTER (WHERE hit IS NOT NULL) AS graded
            FROM playoff_picks
            WHERE game_date >= CURRENT_DATE - (CAST(:days AS INTEGER) * INTERVAL '1 day')
            GROUP BY game_date
            ORDER BY game_date DESC
        """),
        {"days": days},
    )
    nights = [
        {
            "date": r.game_date.isoformat(),
            "picks": int(r.picks),
            "hits": int(r.hits or 0),
            "graded": int(r.graded or 0),
        }
        for r in nights_res.fetchall()
    ]

    # Most recent picks with grades
    recent_res = await db.execute(
        text("""
            SELECT game_date, rank, player_name, team, opponent, market, line,
                   probability, confidence, hit, actual_goals, actual_points
            FROM playoff_picks
            WHERE game_date >= CURRENT_DATE - (CAST(:days AS INTEGER) * INTERVAL '1 day')
            ORDER BY game_date DESC, rank ASC
            LIMIT 60
        """),
        {"days": days},
    )
    recent = [
        {
            "date": r.game_date.isoformat(),
            "rank": int(r.rank),
            "player_name": r.player_name,
            "team": r.team,
            "opponent": r.opponent,
            "market": r.market,
            "line": r.line,
            "probability": round(float(r.probability), 3),
            "confidence": r.confidence,
            "hit": r.hit,
            "actual_goals": r.actual_goals,
            "actual_points": r.actual_points,
        }
        for r in recent_res.fetchall()
    ]

    # Streak: consecutive graded picks with the same outcome, working back from most recent
    streak_kind: str | None = None
    streak_len = 0
    for p in recent:
        if p["hit"] is None:
            continue
        kind = "hit" if p["hit"] else "miss"
        if streak_kind is None:
            streak_kind = kind
            streak_len = 1
        elif kind == streak_kind:
            streak_len += 1
        else:
            break

    return {
        "period_days": days,
        "total_picks": int(agg.total or 0),
        "graded": graded,
        "hits": hits,
        "misses": misses,
        "hit_rate": hit_rate,
        "nights": nights,
        "recent": recent,
        "streak": {"kind": streak_kind, "length": streak_len} if streak_kind else None,
        "avg_probability": avg_prob,
    }


async def generate_picks_for_upcoming_playoffs(
    db: AsyncSession,
    days_ahead: int = 2,
) -> dict[str, int]:
    """
    Convenience wrapper used by the scheduler: generate (idempotent) picks for
    today through today + `days_ahead` for any date that has playoff games.
    """
    start = date.today()
    totals = {"dates_processed": 0, "picks_written": 0}
    for i in range(days_ahead + 1):
        d = start + timedelta(days=i)
        written = await generate_nightly_playoff_picks(db, d)
        if written:
            totals["dates_processed"] += 1
            totals["picks_written"] += len(written)
    return totals


async def validate_recent_playoff_picks(
    db: AsyncSession,
    days_back: int = 3,
) -> dict[str, int]:
    """Grade any unvalidated picks from the last `days_back` days."""
    today = date.today()
    totals = {"dates_validated": 0, "picks_validated": 0}
    for i in range(1, days_back + 1):
        d = today - timedelta(days=i)
        res = await validate_playoff_picks(db, d)
        v = int(res.get("validated", 0) or 0)
        if v:
            totals["dates_validated"] += 1
            totals["picks_validated"] += v
    return totals
