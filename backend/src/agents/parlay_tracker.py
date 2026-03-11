"""
Daily Parlay Tracker

Generates daily model-based parlay picks, stores them before games,
then validates each leg against actual outcomes. Tracks long-run accuracy.

Parlay types generated each day:
  1. Best Bets    — top probability goal scorers + best point producers
  2. Value Play   — players with biggest model-vs-market edge (if odds available)
  3. Wild Card    — mid-tier scorers for higher payout

Leg types that can be validated from DB:
  - goal_scorer  : player scores >= 1 goal   (game_logs.goals)
  - point        : player records >= 1 point  (game_logs.points)
  - assist       : player records >= 1 assist (game_logs.assists)
  - moneyline    : team wins                  (games.home_score / away_score)
"""

import json
import structlog
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.agents.predictions import prediction_engine

logger = structlog.get_logger()

# ── Schema ────────────────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS daily_parlays (
    id            SERIAL PRIMARY KEY,
    game_date     DATE NOT NULL,
    generated_at  TIMESTAMP DEFAULT NOW(),
    parlay_name   VARCHAR(50) NOT NULL,
    legs          JSONB NOT NULL,
    combined_prob FLOAT NOT NULL,
    validated_at  TIMESTAMP,
    legs_hit      INTEGER,
    legs_total    INTEGER,
    result        VARCHAR(10)   -- 'win', 'loss', 'push', null = not yet
);

CREATE INDEX IF NOT EXISTS idx_parlays_game_date ON daily_parlays(game_date);
"""


@dataclass
class ParlayLeg:
    leg_type: str        # 'goal_scorer' | 'point' | 'assist' | 'moneyline'
    player_name: str | None
    team: str
    opponent: str | None
    probability: float   # model probability 0-1
    market_odds: str | None = None   # e.g. "-110" if available
    hit: bool | None = None          # set during validation


@dataclass
class Parlay:
    name: str
    legs: list[ParlayLeg]
    combined_prob: float
    game_date: date


# ── Table setup ───────────────────────────────────────────────────────────────

async def create_parlay_table(db: AsyncSession) -> None:
    statements = [s.strip() for s in CREATE_TABLE_SQL.split(';') if s.strip()]
    for stmt in statements:
        await db.execute(text(stmt))
    await db.commit()


# ── Generation ────────────────────────────────────────────────────────────────

async def generate_daily_parlays(
    db: AsyncSession,
    target_date: date | None = None,
) -> list[Parlay]:
    """
    Generate 3 model-based parlays for target_date (defaults to today).
    Skips generation if parlays already exist for that date.
    """
    target_date = target_date or date.today()

    # Skip if already generated today
    existing = await db.execute(
        text("SELECT COUNT(*) FROM daily_parlays WHERE game_date = :d"),
        {"d": target_date},
    )
    if existing.scalar() > 0:
        logger.info("parlays_already_generated", date=str(target_date))
        return []

    # Pull today's games
    games_result = await db.execute(
        text("""
            SELECT home_team, away_team FROM games
            WHERE game_date = :d AND is_completed = FALSE
            ORDER BY game_date
        """),
        {"d": target_date},
    )
    games = games_result.fetchall()
    if not games:
        logger.info("no_games_for_parlay", date=str(target_date))
        return []

    # Get predictions for all games
    all_preds = []
    for game in games:
        try:
            matchup = await prediction_engine.get_matchup_prediction(
                db, game.home_team, game.away_team, target_date, top_n=10
            )
            for pred in matchup.top_scorers:
                all_preds.append({
                    "player": pred.player_name,
                    "team": pred.team,
                    "opponent": game.away_team if pred.team == game.home_team else game.home_team,
                    "prob_goal": pred.prob_goal,
                    "prob_point": pred.prob_point,
                    "is_home": pred.is_home,
                    "home_team": game.home_team,
                    "away_team": game.away_team,
                })
        except Exception as e:
            logger.warning("parlay_game_prediction_failed", game=dict(game._mapping), error=str(e))

    if len(all_preds) < 4:
        logger.info("insufficient_predictions_for_parlay", count=len(all_preds))
        return []

    all_preds.sort(key=lambda p: p["prob_goal"], reverse=True)

    parlays: list[Parlay] = []

    # ── Parlay 1: Best Bets ──────────────────────────────────────────────────
    # Top 2 goal scorers + top 2 point producers (different players) + moneyline
    top_goals = all_preds[:2]
    top_points_pool = sorted(all_preds, key=lambda p: p["prob_point"], reverse=True)
    used = {p["player"] for p in top_goals}
    top_points = [p for p in top_points_pool if p["player"] not in used][:2]

    best_legs = []
    for p in top_goals:
        best_legs.append(ParlayLeg("goal_scorer", p["player"], p["team"], p["opponent"], p["prob_goal"]))
    for p in top_points:
        best_legs.append(ParlayLeg("point", p["player"], p["team"], p["opponent"], p["prob_point"]))

    # Moneyline: home team with higher combined top-scorer probability
    ml_game = games[0]
    home_prob = sum(p["prob_goal"] for p in all_preds if p["team"] == ml_game.home_team)
    away_prob = sum(p["prob_goal"] for p in all_preds if p["team"] == ml_game.away_team)
    ml_team = ml_game.home_team if home_prob >= away_prob else ml_game.away_team
    ml_prob = max(home_prob, away_prob) / (home_prob + away_prob) if (home_prob + away_prob) > 0 else 0.52
    ml_prob = min(max(ml_prob, 0.45), 0.65)  # cap reasonable range
    best_legs.append(ParlayLeg("moneyline", None, ml_team,
                                ml_game.away_team if ml_team == ml_game.home_team else ml_game.home_team,
                                round(ml_prob, 3)))

    combined = 1.0
    for leg in best_legs:
        combined *= leg.probability
    parlays.append(Parlay("Best Bets", best_legs, round(combined, 4), target_date))

    # ── Parlay 2: Value Play ─────────────────────────────────────────────────
    # 2 goal scorers ranked 3-8 (mid-tier, higher payout) + 2 assists (from top point players)
    mid_goals = all_preds[2:6]
    import random
    random.seed(int(target_date.strftime("%Y%m%d")) + 1)  # deterministic per day
    chosen_mid = random.sample(mid_goals, min(2, len(mid_goals)))
    used2 = {p["player"] for p in chosen_mid}
    assist_pool = sorted(all_preds, key=lambda p: p["prob_point"], reverse=True)
    assist_picks = [p for p in assist_pool if p["player"] not in used2][:2]

    value_legs = []
    for p in chosen_mid:
        value_legs.append(ParlayLeg("goal_scorer", p["player"], p["team"], p["opponent"], p["prob_goal"]))
    for p in assist_picks:
        # assist probability ≈ point_prob * 0.72 (NHL average assist rate ~72% of points)
        assist_prob = round(p["prob_point"] * 0.72, 3)
        value_legs.append(ParlayLeg("assist", p["player"], p["team"], p["opponent"], assist_prob))

    combined2 = 1.0
    for leg in value_legs:
        combined2 *= leg.probability
    parlays.append(Parlay("Value Play", value_legs, round(combined2, 4), target_date))

    # ── Parlay 3: Wild Card ──────────────────────────────────────────────────
    # 3 goal scorers ranked 4-10 (different games if possible)
    random.seed(int(target_date.strftime("%Y%m%d")) + 2)
    wc_pool = all_preds[3:10]
    wc_picks = random.sample(wc_pool, min(3, len(wc_pool)))

    wc_legs = []
    for p in wc_picks:
        wc_legs.append(ParlayLeg("goal_scorer", p["player"], p["team"], p["opponent"], p["prob_goal"]))

    combined3 = 1.0
    for leg in wc_legs:
        combined3 *= leg.probability
    parlays.append(Parlay("Wild Card", wc_legs, round(combined3, 4), target_date))

    # ── Persist to DB ────────────────────────────────────────────────────────
    for parlay in parlays:
        legs_json = json.dumps([
            {k: v for k, v in asdict(leg).items()} for leg in parlay.legs
        ])
        await db.execute(
            text("""
                INSERT INTO daily_parlays
                    (game_date, parlay_name, legs, combined_prob, legs_total)
                VALUES
                    (:game_date, :name, :legs, :prob, :total)
            """),
            {
                "game_date": parlay.game_date,
                "name": parlay.name,
                "legs": legs_json,
                "prob": parlay.combined_prob,
                "total": len(parlay.legs),
            },
        )
    await db.commit()
    logger.info("parlays_generated", date=str(target_date), count=len(parlays))
    return parlays


# ── Validation ────────────────────────────────────────────────────────────────

async def validate_parlays(
    db: AsyncSession,
    game_date: date | None = None,
) -> dict:
    """
    Validate parlay legs against actual game outcomes for game_date.
    Skips parlays already validated or games not yet complete.
    """
    game_date = game_date or (date.today() - timedelta(days=1))

    # Get unvalidated parlays for date
    result = await db.execute(
        text("""
            SELECT id, parlay_name, legs, legs_total
            FROM daily_parlays
            WHERE game_date = :d AND validated_at IS NULL
        """),
        {"d": game_date},
    )
    rows = result.fetchall()
    if not rows:
        return {"validated": 0, "date": str(game_date)}

    # Fetch actual player results for that date
    player_results = await db.execute(
        text("""
            SELECT p.name, gl.goals, gl.assists, gl.points, gl.team
            FROM game_logs gl
            JOIN players p ON p.id = gl.player_id
            WHERE gl.game_date = :d
        """),
        {"d": game_date},
    )
    actuals: dict[str, dict] = {}
    for row in player_results.fetchall():
        actuals[row.name.lower()] = {
            "goals": row.goals or 0,
            "assists": row.assists or 0,
            "points": row.points or 0,
            "team": row.team,
        }

    # Fetch game results for moneyline validation
    game_results = await db.execute(
        text("""
            SELECT home_team, away_team, home_score, away_score
            FROM games
            WHERE game_date = :d AND is_completed = TRUE AND home_score IS NOT NULL
        """),
        {"d": game_date},
    )
    winners: set[str] = set()
    for row in game_results.fetchall():
        if row.home_score is not None and row.away_score is not None:
            if row.home_score > row.away_score:
                winners.add(row.home_team)
            elif row.away_score > row.home_score:
                winners.add(row.away_team)

    validated_count = 0
    for row in rows:
        legs = json.loads(row.legs)
        legs_hit = 0

        for leg in legs:
            leg_type = leg["leg_type"]
            player_key = (leg["player_name"] or "").lower()

            if leg_type == "goal_scorer":
                actual = actuals.get(player_key)
                leg["hit"] = bool(actual and actual["goals"] >= 1)
            elif leg_type == "point":
                actual = actuals.get(player_key)
                leg["hit"] = bool(actual and actual["points"] >= 1)
            elif leg_type == "assist":
                actual = actuals.get(player_key)
                leg["hit"] = bool(actual and actual["assists"] >= 1)
            elif leg_type == "moneyline":
                leg["hit"] = leg["team"] in winners
            else:
                leg["hit"] = None  # unknown type

            if leg["hit"]:
                legs_hit += 1

        all_hit = all(leg["hit"] is True for leg in legs)
        any_unknown = any(leg["hit"] is None for leg in legs)
        result_val = "win" if all_hit else ("push" if any_unknown else "loss")

        await db.execute(
            text("""
                UPDATE daily_parlays
                SET validated_at = NOW(),
                    legs = :legs,
                    legs_hit = :legs_hit,
                    result = :result
                WHERE id = :id
            """),
            {
                "legs": json.dumps(legs),
                "legs_hit": legs_hit,
                "result": result_val,
                "id": row.id,
            },
        )
        validated_count += 1

    await db.commit()
    logger.info("parlays_validated", date=str(game_date), count=validated_count)
    return {"validated": validated_count, "date": str(game_date)}


# ── Stats / Context ───────────────────────────────────────────────────────────

async def get_parlay_record(db: AsyncSession, days: int = 30) -> dict:
    """Return win/loss record and leg accuracy for the last N days."""
    result = await db.execute(
        text("""
            SELECT
                parlay_name,
                COUNT(*) FILTER (WHERE result IS NOT NULL)          AS total,
                COUNT(*) FILTER (WHERE result = 'win')              AS wins,
                COUNT(*) FILTER (WHERE result = 'loss')             AS losses,
                ROUND(AVG(legs_hit::float / NULLIF(legs_total,0)) * 100, 1) AS avg_legs_hit_pct,
                ROUND(AVG(combined_prob) * 100, 1)                  AS avg_model_prob_pct
            FROM daily_parlays
            WHERE game_date >= CURRENT_DATE - (CAST(:days AS INTEGER) * INTERVAL '1 day')
            GROUP BY parlay_name
            ORDER BY wins DESC
        """),
        {"days": days},
    )
    rows = result.fetchall()

    record = []
    for row in rows:
        record.append({
            "parlay_name": row.parlay_name,
            "total": row.total,
            "wins": row.wins,
            "losses": row.losses,
            "win_rate": f"{round(row.wins / row.total * 100, 1)}%" if row.total else "—",
            "avg_legs_hit_pct": f"{row.avg_legs_hit_pct}%" if row.avg_legs_hit_pct else "—",
            "avg_model_prob_pct": f"{row.avg_model_prob_pct}%" if row.avg_model_prob_pct else "—",
        })

    # Recent parlays (last 7 days) with full leg detail
    recent_result = await db.execute(
        text("""
            SELECT game_date, parlay_name, legs, combined_prob, result, legs_hit, legs_total
            FROM daily_parlays
            WHERE game_date >= CURRENT_DATE - INTERVAL '7 days'
            ORDER BY game_date DESC, parlay_name
        """),
    )
    recent = []
    for row in recent_result.fetchall():
        recent.append({
            "date": row.game_date.isoformat(),
            "name": row.parlay_name,
            "combined_prob": f"{round(row.combined_prob * 100, 1)}%",
            "result": row.result or "pending",
            "legs_hit": row.legs_hit,
            "legs_total": row.legs_total,
            "legs": json.loads(row.legs),
        })

    return {
        "period_days": days,
        "by_type": record,
        "recent": recent,
    }


async def get_today_parlays_context(db: AsyncSession) -> str:
    """Format today's parlays as context text for Claude."""
    result = await db.execute(
        text("""
            SELECT parlay_name, legs, combined_prob
            FROM daily_parlays
            WHERE game_date = CURRENT_DATE
            ORDER BY combined_prob DESC
        """),
    )
    rows = result.fetchall()
    if not rows:
        return "No parlays generated for today yet."

    lines = [f"**Today's Model Parlays — {date.today().strftime('%B %d, %Y')}**\n"]
    for row in rows:
        prob_pct = round(row.combined_prob * 100, 1)
        lines.append(f"\n### {row.parlay_name} — Combined probability: {prob_pct}%")
        legs = json.loads(row.legs)
        for i, leg in enumerate(legs, 1):
            leg_prob = round(leg["probability"] * 100, 1)
            player_str = leg["player_name"] or leg["team"]
            lines.append(f"{i}. {player_str} ({leg['team']}) — {leg['leg_type'].replace('_', ' ').title()}: {leg_prob}%")
    return "\n".join(lines)
