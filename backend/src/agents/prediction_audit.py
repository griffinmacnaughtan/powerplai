"""
Prediction Audit & Validation System

This module provides infrastructure to:
1. Log predictions BEFORE games happen (immutable audit trail)
2. Record actual outcomes AFTER games
3. Calculate accuracy metrics (calibration, Brier score, ROI)
4. Generate validation reports for marketability claims

Key Concepts:
- Calibration: Do 35% predictions actually hit 35% of the time?
- Brier Score: Overall probability accuracy (lower = better)
- ROI: If you bet our picks, do you profit?
"""
import math
import structlog
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.db.database import async_session_maker

logger = structlog.get_logger()


# -------------------------------------------------------------------------
# Data Classes
# -------------------------------------------------------------------------

@dataclass
class PredictionRecord:
    """A single prediction logged for audit."""
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    # Game context
    game_date: date = None
    game_type: str = "nhl"  # nhl, olympic
    game_id: Optional[str] = None

    # Prediction details
    player_name: str = ""
    player_id: Optional[int] = None
    team: str = ""
    opponent: str = ""
    is_home: bool = True

    # Probabilities (logged BEFORE game)
    prob_goal: float = 0.0
    prob_point: float = 0.0
    prob_multi_point: float = 0.0
    expected_goals: float = 0.0
    expected_points: float = 0.0

    # Model metadata
    confidence: str = "medium"
    confidence_score: float = 0.5
    model_version: str = "v1"
    factors: list[str] = field(default_factory=list)

    # Actual outcomes (filled AFTER game)
    actual_goals: Optional[int] = None
    actual_assists: Optional[int] = None
    actual_points: Optional[int] = None
    validated_at: Optional[datetime] = None

    # Computed after validation
    goal_hit: Optional[bool] = None  # Did player score at least 1 goal?
    point_hit: Optional[bool] = None  # Did player get at least 1 point?


@dataclass
class CalibrationBucket:
    """Stats for a probability bucket (e.g., 30-40%)."""
    bucket_min: float
    bucket_max: float
    total_predictions: int = 0
    actual_hits: int = 0

    @property
    def actual_rate(self) -> float:
        if self.total_predictions == 0:
            return 0.0
        return self.actual_hits / self.total_predictions

    @property
    def expected_rate(self) -> float:
        return (self.bucket_min + self.bucket_max) / 2

    @property
    def calibration_error(self) -> float:
        """How far off is actual from expected?"""
        return abs(self.actual_rate - self.expected_rate)

    @property
    def is_well_calibrated(self) -> bool:
        """Within 5 percentage points of expected?"""
        return self.calibration_error < 0.05


@dataclass
class ValidationReport:
    """Complete validation report for a time period."""
    period_start: date
    period_end: date
    total_predictions: int = 0
    validated_predictions: int = 0

    # Calibration
    goal_calibration_buckets: list[CalibrationBucket] = field(default_factory=list)
    point_calibration_buckets: list[CalibrationBucket] = field(default_factory=list)

    # Accuracy metrics
    goal_brier_score: float = 0.0
    point_brier_score: float = 0.0

    # Hit rates by confidence
    high_confidence_goal_rate: float = 0.0
    medium_confidence_goal_rate: float = 0.0
    low_confidence_goal_rate: float = 0.0

    # ROI simulation (hypothetical $100 bets)
    total_bets: int = 0
    winning_bets: int = 0
    total_wagered: float = 0.0
    total_returned: float = 0.0
    roi_percent: float = 0.0

    # Model performance
    model_version: str = "v1"

    def to_dict(self) -> dict:
        return {
            "period": {
                "start": self.period_start.isoformat(),
                "end": self.period_end.isoformat(),
            },
            "sample_size": {
                "total_predictions": self.total_predictions,
                "validated": self.validated_predictions,
                "validation_rate": round(self.validated_predictions / max(self.total_predictions, 1), 2),
            },
            "accuracy": {
                "goal_brier_score": round(self.goal_brier_score, 4),
                "point_brier_score": round(self.point_brier_score, 4),
                "brier_interpretation": self._interpret_brier(self.goal_brier_score),
            },
            "calibration": {
                "goal_buckets": [
                    {
                        "range": f"{int(b.bucket_min*100)}-{int(b.bucket_max*100)}%",
                        "predictions": b.total_predictions,
                        "hits": b.actual_hits,
                        "actual_rate": f"{b.actual_rate:.1%}",
                        "expected_rate": f"{b.expected_rate:.1%}",
                        "calibrated": b.is_well_calibrated,
                    }
                    for b in self.goal_calibration_buckets
                ],
            },
            "confidence_performance": {
                "high": f"{self.high_confidence_goal_rate:.1%}",
                "medium": f"{self.medium_confidence_goal_rate:.1%}",
                "low": f"{self.low_confidence_goal_rate:.1%}",
            },
            "roi_simulation": {
                "total_bets": self.total_bets,
                "wins": self.winning_bets,
                "win_rate": f"{self.winning_bets / max(self.total_bets, 1):.1%}",
                "total_wagered": f"${self.total_wagered:,.0f}",
                "total_returned": f"${self.total_returned:,.0f}",
                "profit_loss": f"${self.total_returned - self.total_wagered:+,.0f}",
                "roi": f"{self.roi_percent:+.1f}%",
            },
            "model_version": self.model_version,
        }

    def _interpret_brier(self, score: float) -> str:
        if score < 0.10:
            return "Excellent - highly accurate probabilities"
        elif score < 0.15:
            return "Good - reliable predictions"
        elif score < 0.20:
            return "Decent - better than naive baselines"
        elif score < 0.25:
            return "Fair - marginally better than random"
        else:
            return "Poor - no better than random guessing"


# -------------------------------------------------------------------------
# Database Schema
# -------------------------------------------------------------------------

AUDIT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS prediction_audit (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMP DEFAULT NOW(),

    -- Game context
    game_date DATE NOT NULL,
    game_type VARCHAR(20) DEFAULT 'nhl',
    game_id VARCHAR(50),

    -- Prediction details
    player_name VARCHAR(100) NOT NULL,
    player_id INTEGER,
    team VARCHAR(10) NOT NULL,
    opponent VARCHAR(10) NOT NULL,
    is_home BOOLEAN DEFAULT TRUE,

    -- Probabilities (logged BEFORE game)
    prob_goal FLOAT NOT NULL,
    prob_point FLOAT NOT NULL,
    prob_multi_point FLOAT,
    expected_goals FLOAT,
    expected_points FLOAT,

    -- Model metadata
    confidence VARCHAR(20),
    confidence_score FLOAT,
    model_version VARCHAR(20) DEFAULT 'v1',
    factors JSONB,

    -- Actual outcomes (filled AFTER game)
    actual_goals INTEGER,
    actual_assists INTEGER,
    actual_points INTEGER,
    validated_at TIMESTAMP,

    -- Computed
    goal_hit BOOLEAN,
    point_hit BOOLEAN,

    -- Indexes
    UNIQUE(game_date, player_name, team, opponent)
);

CREATE INDEX IF NOT EXISTS idx_audit_game_date ON prediction_audit(game_date);
CREATE INDEX IF NOT EXISTS idx_audit_validated ON prediction_audit(validated_at);
CREATE INDEX IF NOT EXISTS idx_audit_model ON prediction_audit(model_version);
"""


async def create_audit_table(db: AsyncSession):
    """Create the prediction audit table if it doesn't exist."""
    # Execute each statement separately (asyncpg doesn't support multiple statements)
    statements = [s.strip() for s in AUDIT_TABLE_SQL.split(';') if s.strip()]
    for stmt in statements:
        await db.execute(text(stmt))
    await db.commit()
    logger.info("prediction_audit_table_created")


# -------------------------------------------------------------------------
# Logging Predictions (BEFORE games)
# -------------------------------------------------------------------------

async def log_prediction(
    db: AsyncSession,
    game_date: date,
    player_name: str,
    team: str,
    opponent: str,
    prob_goal: float,
    prob_point: float,
    is_home: bool = True,
    game_type: str = "nhl",
    game_id: str = None,
    player_id: int = None,
    prob_multi_point: float = None,
    expected_goals: float = None,
    expected_points: float = None,
    confidence: str = "medium",
    confidence_score: float = 0.5,
    model_version: str = "v1",
    factors: list[str] = None,
) -> int:
    """
    Log a prediction BEFORE the game happens.

    This creates an immutable record that can later be validated
    against actual outcomes.

    Returns the prediction ID.
    """
    import json

    result = await db.execute(
        text("""
            INSERT INTO prediction_audit (
                game_date, game_type, game_id,
                player_name, player_id, team, opponent, is_home,
                prob_goal, prob_point, prob_multi_point,
                expected_goals, expected_points,
                confidence, confidence_score, model_version, factors
            ) VALUES (
                :game_date, :game_type, :game_id,
                :player_name, :player_id, :team, :opponent, :is_home,
                :prob_goal, :prob_point, :prob_multi_point,
                :expected_goals, :expected_points,
                :confidence, :confidence_score, :model_version, :factors
            )
            ON CONFLICT (game_date, player_name, team, opponent)
            DO UPDATE SET
                prob_goal = EXCLUDED.prob_goal,
                prob_point = EXCLUDED.prob_point,
                confidence = EXCLUDED.confidence,
                model_version = EXCLUDED.model_version
            RETURNING id
        """),
        {
            "game_date": game_date,
            "game_type": game_type,
            "game_id": game_id,
            "player_name": player_name,
            "player_id": player_id,
            "team": team,
            "opponent": opponent,
            "is_home": is_home,
            "prob_goal": prob_goal,
            "prob_point": prob_point,
            "prob_multi_point": prob_multi_point,
            "expected_goals": expected_goals,
            "expected_points": expected_points,
            "confidence": confidence,
            "confidence_score": confidence_score,
            "model_version": model_version,
            "factors": json.dumps(factors) if factors else None,
        }
    )

    row = result.fetchone()
    await db.commit()

    logger.info(
        "prediction_logged",
        prediction_id=row[0],
        player=player_name,
        game_date=str(game_date),
        prob_goal=prob_goal,
    )

    return row[0]


async def log_predictions_batch(
    db: AsyncSession,
    predictions: list[dict],
) -> int:
    """Log multiple predictions at once."""
    count = 0
    for pred in predictions:
        try:
            await log_prediction(db, **pred)
            count += 1
        except Exception as e:
            logger.warning("prediction_log_failed", error=str(e), player=pred.get("player_name"))

    return count


# -------------------------------------------------------------------------
# Recording Outcomes (AFTER games)
# -------------------------------------------------------------------------

async def record_outcome(
    db: AsyncSession,
    game_date: date,
    player_name: str,
    team: str,
    opponent: str,
    actual_goals: int,
    actual_assists: int,
) -> bool:
    """
    Record actual outcome AFTER the game.

    This fills in the actual_* fields and computes hit/miss.
    """
    actual_points = actual_goals + actual_assists
    goal_hit = actual_goals >= 1
    point_hit = actual_points >= 1

    result = await db.execute(
        text("""
            UPDATE prediction_audit
            SET actual_goals = :actual_goals,
                actual_assists = :actual_assists,
                actual_points = :actual_points,
                goal_hit = :goal_hit,
                point_hit = :point_hit,
                validated_at = NOW()
            WHERE game_date = :game_date
              AND player_name = :player_name
              AND team = :team
              AND opponent = :opponent
              AND validated_at IS NULL
            RETURNING id
        """),
        {
            "game_date": game_date,
            "player_name": player_name,
            "team": team,
            "opponent": opponent,
            "actual_goals": actual_goals,
            "actual_assists": actual_assists,
            "actual_points": actual_points,
            "goal_hit": goal_hit,
            "point_hit": point_hit,
        }
    )

    row = result.fetchone()
    await db.commit()

    if row:
        logger.info(
            "outcome_recorded",
            prediction_id=row[0],
            player=player_name,
            goals=actual_goals,
            points=actual_points,
        )
        return True

    return False


async def validate_game_outcomes(
    db: AsyncSession,
    game_date: date,
) -> dict:
    """
    Fetch actual game results and validate all predictions for that date.

    This would typically pull from NHL API or game logs.
    """
    from backend.src.ingestion.games import get_game_results

    stats = {"validated": 0, "not_found": 0, "errors": 0}

    # Get all unvalidated predictions for this date
    result = await db.execute(
        text("""
            SELECT player_name, team, opponent, player_id
            FROM prediction_audit
            WHERE game_date = :game_date
              AND validated_at IS NULL
        """),
        {"game_date": game_date}
    )

    predictions = result.fetchall()

    for pred in predictions:
        try:
            # Look up actual stats from game_logs table
            actual = await db.execute(
                text("""
                    SELECT goals, assists
                    FROM game_logs
                    WHERE game_date = :game_date
                      AND player_id = :player_id
                    LIMIT 1
                """),
                {"game_date": game_date, "player_id": pred.player_id}
            )
            row = actual.fetchone()

            if row:
                success = await record_outcome(
                    db, game_date, pred.player_name, pred.team, pred.opponent,
                    row.goals or 0, row.assists or 0
                )
                if success:
                    stats["validated"] += 1
            else:
                stats["not_found"] += 1

        except Exception as e:
            logger.warning("validation_error", player=pred.player_name, error=str(e))
            stats["errors"] += 1

    return stats


# -------------------------------------------------------------------------
# Calculating Metrics
# -------------------------------------------------------------------------

def calculate_brier_score(predictions: list[tuple[float, bool]]) -> float:
    """
    Calculate Brier Score for probability predictions.

    Args:
        predictions: List of (predicted_probability, actual_outcome) tuples

    Returns:
        Brier score (0 = perfect, 0.25 = random, 1 = always wrong)
    """
    if not predictions:
        return 0.0

    total = 0.0
    for prob, actual in predictions:
        outcome = 1.0 if actual else 0.0
        total += (prob - outcome) ** 2

    return total / len(predictions)


def calculate_calibration_buckets(
    predictions: list[tuple[float, bool]],
    n_buckets: int = 10,
) -> list[CalibrationBucket]:
    """
    Calculate calibration across probability buckets.

    Groups predictions by probability range and compares
    predicted vs actual hit rates.
    """
    bucket_size = 1.0 / n_buckets
    buckets = []

    for i in range(n_buckets):
        bucket_min = i * bucket_size
        bucket_max = (i + 1) * bucket_size

        bucket_preds = [
            (prob, actual) for prob, actual in predictions
            if bucket_min <= prob < bucket_max
        ]

        bucket = CalibrationBucket(
            bucket_min=bucket_min,
            bucket_max=bucket_max,
            total_predictions=len(bucket_preds),
            actual_hits=sum(1 for _, actual in bucket_preds if actual),
        )
        buckets.append(bucket)

    return buckets


async def generate_validation_report(
    db: AsyncSession,
    start_date: date,
    end_date: date,
    model_version: str = None,
) -> ValidationReport:
    """
    Generate a comprehensive validation report for a time period.

    This is the main function to assess model accuracy.
    """
    # Fetch all validated predictions in the period
    query = """
        SELECT
            prob_goal, prob_point,
            goal_hit, point_hit,
            confidence, confidence_score,
            expected_goals, actual_goals
        FROM prediction_audit
        WHERE game_date BETWEEN :start_date AND :end_date
          AND validated_at IS NOT NULL
    """
    params = {"start_date": start_date, "end_date": end_date}

    if model_version:
        query += " AND model_version = :model_version"
        params["model_version"] = model_version

    result = await db.execute(text(query), params)
    rows = result.fetchall()

    if not rows:
        return ValidationReport(
            period_start=start_date,
            period_end=end_date,
            total_predictions=0,
            validated_predictions=0,
        )

    # Build prediction lists for metrics
    goal_preds = [(row.prob_goal, row.goal_hit) for row in rows if row.goal_hit is not None]
    point_preds = [(row.prob_point, row.point_hit) for row in rows if row.point_hit is not None]

    # Calculate Brier scores
    goal_brier = calculate_brier_score(goal_preds)
    point_brier = calculate_brier_score(point_preds)

    # Calculate calibration
    goal_buckets = calculate_calibration_buckets(goal_preds)
    point_buckets = calculate_calibration_buckets(point_preds)

    # Calculate hit rates by confidence
    high_conf = [row for row in rows if row.confidence == "high"]
    med_conf = [row for row in rows if row.confidence == "medium"]
    low_conf = [row for row in rows if row.confidence == "low"]

    high_rate = sum(1 for r in high_conf if r.goal_hit) / max(len(high_conf), 1)
    med_rate = sum(1 for r in med_conf if r.goal_hit) / max(len(med_conf), 1)
    low_rate = sum(1 for r in low_conf if r.goal_hit) / max(len(low_conf), 1)

    # ROI simulation (simple: bet $100 on players with >35% goal prob, +200 odds)
    high_prob_preds = [row for row in rows if row.prob_goal >= 0.35]
    total_bets = len(high_prob_preds)
    winning_bets = sum(1 for r in high_prob_preds if r.goal_hit)
    total_wagered = total_bets * 100
    # Assume average odds of +200 for anytime scorer
    total_returned = winning_bets * 300  # $100 bet + $200 profit
    roi = ((total_returned - total_wagered) / max(total_wagered, 1)) * 100

    # Get total predictions (including unvalidated)
    total_result = await db.execute(
        text("""
            SELECT COUNT(*) FROM prediction_audit
            WHERE game_date BETWEEN :start_date AND :end_date
        """),
        {"start_date": start_date, "end_date": end_date}
    )
    total_count = total_result.scalar()

    return ValidationReport(
        period_start=start_date,
        period_end=end_date,
        total_predictions=total_count,
        validated_predictions=len(rows),
        goal_calibration_buckets=goal_buckets,
        point_calibration_buckets=point_buckets,
        goal_brier_score=goal_brier,
        point_brier_score=point_brier,
        high_confidence_goal_rate=high_rate,
        medium_confidence_goal_rate=med_rate,
        low_confidence_goal_rate=low_rate,
        total_bets=total_bets,
        winning_bets=winning_bets,
        total_wagered=total_wagered,
        total_returned=total_returned,
        roi_percent=roi,
        model_version=model_version or "all",
    )


# -------------------------------------------------------------------------
# Convenience Functions
# -------------------------------------------------------------------------

async def get_unvalidated_predictions(
    db: AsyncSession,
    before_date: date = None,
) -> list[dict]:
    """Get predictions that haven't been validated yet."""
    query = """
        SELECT game_date, player_name, team, opponent, prob_goal, prob_point
        FROM prediction_audit
        WHERE validated_at IS NULL
    """
    params = {}

    if before_date:
        query += " AND game_date < :before_date"
        params["before_date"] = before_date

    query += " ORDER BY game_date DESC LIMIT 100"

    result = await db.execute(text(query), params)
    return [
        {
            "game_date": row.game_date.isoformat(),
            "player_name": row.player_name,
            "team": row.team,
            "opponent": row.opponent,
            "prob_goal": row.prob_goal,
            "prob_point": row.prob_point,
        }
        for row in result.fetchall()
    ]


async def get_prediction_stats(db: AsyncSession) -> dict:
    """Get overall prediction audit statistics."""
    result = await db.execute(text("""
        SELECT
            COUNT(*) as total,
            COUNT(validated_at) as validated,
            COUNT(*) FILTER (WHERE goal_hit = TRUE) as goal_hits,
            COUNT(*) FILTER (WHERE point_hit = TRUE) as point_hits,
            MIN(game_date) as earliest,
            MAX(game_date) as latest
        FROM prediction_audit
    """))
    row = result.fetchone()

    return {
        "total_predictions": row.total,
        "validated": row.validated,
        "pending_validation": row.total - row.validated,
        "goal_hits": row.goal_hits,
        "point_hits": row.point_hits,
        "goal_hit_rate": row.goal_hits / max(row.validated, 1),
        "point_hit_rate": row.point_hits / max(row.validated, 1),
        "date_range": {
            "earliest": row.earliest.isoformat() if row.earliest else None,
            "latest": row.latest.isoformat() if row.latest else None,
        }
    }


# -------------------------------------------------------------------------
# Integration with Prediction Engine
# -------------------------------------------------------------------------

async def log_matchup_predictions(
    db: AsyncSession,
    matchup_prediction,  # MatchupPrediction from predictions.py
    game_type: str = "nhl",
) -> int:
    """
    Log all predictions from a matchup prediction.

    Call this when generating predictions for a game.
    """
    count = 0

    all_players = matchup_prediction.home_players + matchup_prediction.away_players

    for pred in all_players:
        try:
            await log_prediction(
                db,
                game_date=matchup_prediction.game_date,
                player_name=pred.player_name,
                team=pred.team,
                opponent=pred.opponent,
                prob_goal=pred.prob_goal,
                prob_point=pred.prob_point,
                is_home=pred.is_home,
                game_type=game_type,
                player_id=pred.player_id,
                prob_multi_point=pred.prob_multi_point,
                expected_goals=pred.expected_goals,
                expected_points=pred.expected_points,
                confidence=pred.confidence,
                confidence_score=pred.confidence_score,
                model_version="nhl_v1",
                factors=pred.factors,
            )
            count += 1
        except Exception as e:
            logger.warning("log_prediction_failed", player=pred.player_name, error=str(e))

    return count


async def log_olympic_predictions(
    db: AsyncSession,
    olympic_prediction: dict,
) -> int:
    """Log predictions from an Olympic game prediction."""
    count = 0
    game = olympic_prediction.get("game", {})

    all_players = (
        olympic_prediction.get("home_players", []) +
        olympic_prediction.get("away_players", [])
    )

    for pred in all_players:
        try:
            await log_prediction(
                db,
                game_date=date.today(),  # Would get from game data
                player_name=pred.get("player_name"),
                team=pred.get("country_code"),
                opponent=pred.get("opponent_code"),
                prob_goal=pred.get("prob_goal", 0),
                prob_point=pred.get("prob_point", 0),
                is_home=game.get("home_code") == pred.get("country_code"),
                game_type="olympic",
                prob_multi_point=pred.get("prob_multi_point"),
                expected_goals=pred.get("expected_goals"),
                expected_points=pred.get("expected_points"),
                confidence=pred.get("confidence", "medium"),
                confidence_score=pred.get("confidence_score", 0.5),
                model_version="olympic_v1",
                factors=pred.get("factors", []),
            )
            count += 1
        except Exception as e:
            logger.warning("log_olympic_prediction_failed", error=str(e))

    return count
