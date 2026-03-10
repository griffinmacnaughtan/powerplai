"""
Model Evaluation Framework for PowerplAI Predictions.

Provides:
- Backtesting against historical data
- Calibration analysis
- Comparison against baseline models
- Evaluation metrics (Brier score, log loss, accuracy)
"""
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


@dataclass
class PredictionOutcome:
    """A single prediction and its actual outcome."""
    prediction_id: str
    player_id: int
    player_name: str
    game_date: date
    opponent: str

    # Predicted values
    prob_goal: float
    prob_point: float
    expected_goals: float
    expected_points: float
    confidence_score: float

    # Actual outcomes
    actual_goals: int | None = None
    actual_points: int | None = None
    scored_goal: bool | None = None
    scored_point: bool | None = None

    # Metadata
    model_version: str = "v1.0"
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class CalibrationBucket:
    """Calibration statistics for a probability bucket."""
    bucket_min: float
    bucket_max: float
    prediction_count: int
    actual_rate: float
    expected_rate: float  # Midpoint of bucket
    calibration_error: float  # |actual - expected|

    @property
    def bucket_label(self) -> str:
        return f"{int(self.bucket_min*100)}-{int(self.bucket_max*100)}%"


@dataclass
class EvaluationMetrics:
    """Comprehensive evaluation metrics."""
    # Sample info
    total_predictions: int
    date_range_start: date
    date_range_end: date

    # Binary classification metrics (for goal scoring)
    accuracy: float              # % of correct predictions (> 0.5 threshold)
    precision: float             # TP / (TP + FP)
    recall: float                # TP / (TP + FN)
    f1_score: float              # Harmonic mean of precision and recall

    # Probabilistic metrics
    brier_score: float           # Mean squared error of probabilities
    log_loss: float              # Cross-entropy loss
    roc_auc: float | None        # Area under ROC curve

    # Calibration
    calibration_error: float     # Expected calibration error (ECE)
    calibration_buckets: list[CalibrationBucket] = field(default_factory=list)

    # Baseline comparisons
    baseline_accuracy: float     # Naive baseline (always predict mode)
    baseline_brier: float        # Brier score of naive baseline
    improvement_vs_baseline: float  # % improvement over baseline

    def to_dict(self) -> dict:
        return {
            "total_predictions": self.total_predictions,
            "date_range": {
                "start": self.date_range_start.isoformat(),
                "end": self.date_range_end.isoformat(),
            },
            "classification_metrics": {
                "accuracy": round(self.accuracy, 4),
                "precision": round(self.precision, 4),
                "recall": round(self.recall, 4),
                "f1_score": round(self.f1_score, 4),
            },
            "probabilistic_metrics": {
                "brier_score": round(self.brier_score, 4),
                "log_loss": round(self.log_loss, 4),
                "roc_auc": round(self.roc_auc, 4) if self.roc_auc else None,
            },
            "calibration": {
                "expected_calibration_error": round(self.calibration_error, 4),
                "buckets": [
                    {
                        "range": b.bucket_label,
                        "predictions": b.prediction_count,
                        "actual_rate": round(b.actual_rate, 4),
                        "expected_rate": round(b.expected_rate, 4),
                        "error": round(b.calibration_error, 4),
                    }
                    for b in self.calibration_buckets
                ],
            },
            "baseline_comparison": {
                "baseline_accuracy": round(self.baseline_accuracy, 4),
                "baseline_brier": round(self.baseline_brier, 4),
                "improvement_pct": round(self.improvement_vs_baseline * 100, 2),
            },
        }


class ModelEvaluator:
    """Evaluates prediction model performance against historical outcomes."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_validated_predictions(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
        min_confidence: float = 0.0,
    ) -> list[PredictionOutcome]:
        """
        Fetch predictions that have been validated against actual outcomes.
        """
        # Default to last 30 days
        if end_date is None:
            end_date = date.today()
        if start_date is None:
            start_date = end_date - timedelta(days=30)

        # Query predictions joined with actual game logs
        result = await self.db.execute(
            text("""
                SELECT
                    pa.id as prediction_id,
                    pa.player_id,
                    p.name as player_name,
                    pa.game_date,
                    pa.opponent,
                    pa.prob_goal,
                    pa.prob_point,
                    pa.expected_goals,
                    pa.expected_points,
                    pa.confidence_score,
                    gl.goals as actual_goals,
                    gl.points as actual_points,
                    pa.model_version,
                    pa.created_at
                FROM prediction_audit pa
                JOIN players p ON pa.player_id = p.id
                LEFT JOIN game_logs gl ON gl.player_id = pa.player_id
                    AND gl.game_date = pa.game_date
                    AND gl.opponent = pa.opponent
                WHERE pa.game_date BETWEEN :start_date AND :end_date
                  AND pa.confidence_score >= :min_confidence
                  AND gl.id IS NOT NULL
                ORDER BY pa.game_date DESC
            """),
            {
                "start_date": start_date,
                "end_date": end_date,
                "min_confidence": min_confidence,
            },
        )

        outcomes = []
        for row in result.fetchall():
            outcomes.append(PredictionOutcome(
                prediction_id=str(row.prediction_id),
                player_id=row.player_id,
                player_name=row.player_name,
                game_date=row.game_date,
                opponent=row.opponent,
                prob_goal=row.prob_goal,
                prob_point=row.prob_point,
                expected_goals=row.expected_goals,
                expected_points=row.expected_points,
                confidence_score=row.confidence_score,
                actual_goals=row.actual_goals,
                actual_points=row.actual_points,
                scored_goal=row.actual_goals > 0 if row.actual_goals is not None else None,
                scored_point=row.actual_points > 0 if row.actual_points is not None else None,
                model_version=row.model_version or "v1.0",
                created_at=row.created_at,
            ))

        return outcomes

    def compute_metrics(self, outcomes: list[PredictionOutcome]) -> EvaluationMetrics:
        """Compute comprehensive evaluation metrics from outcomes."""
        if not outcomes:
            # Return empty metrics
            today = date.today()
            return EvaluationMetrics(
                total_predictions=0,
                date_range_start=today,
                date_range_end=today,
                accuracy=0.0,
                precision=0.0,
                recall=0.0,
                f1_score=0.0,
                brier_score=1.0,
                log_loss=float("inf"),
                roc_auc=None,
                calibration_error=1.0,
                baseline_accuracy=0.0,
                baseline_brier=1.0,
                improvement_vs_baseline=0.0,
            )

        # Filter to outcomes with actual results
        valid_outcomes = [o for o in outcomes if o.scored_goal is not None]
        if not valid_outcomes:
            today = date.today()
            return EvaluationMetrics(
                total_predictions=0,
                date_range_start=today,
                date_range_end=today,
                accuracy=0.0,
                precision=0.0,
                recall=0.0,
                f1_score=0.0,
                brier_score=1.0,
                log_loss=float("inf"),
                roc_auc=None,
                calibration_error=1.0,
                baseline_accuracy=0.0,
                baseline_brier=1.0,
                improvement_vs_baseline=0.0,
            )

        n = len(valid_outcomes)
        dates = [o.game_date for o in valid_outcomes]

        # Binary classification metrics
        tp = fp = tn = fn = 0
        for o in valid_outcomes:
            predicted_positive = o.prob_goal > 0.5
            actual_positive = o.scored_goal

            if predicted_positive and actual_positive:
                tp += 1
            elif predicted_positive and not actual_positive:
                fp += 1
            elif not predicted_positive and actual_positive:
                fn += 1
            else:
                tn += 1

        accuracy = (tp + tn) / n if n > 0 else 0
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

        # Probabilistic metrics
        brier_sum = 0.0
        log_loss_sum = 0.0
        epsilon = 1e-15  # Avoid log(0)

        for o in valid_outcomes:
            actual = 1.0 if o.scored_goal else 0.0
            prob = max(min(o.prob_goal, 1 - epsilon), epsilon)

            # Brier score: (prob - actual)^2
            brier_sum += (prob - actual) ** 2

            # Log loss: -[actual*log(prob) + (1-actual)*log(1-prob)]
            log_loss_sum += -(actual * math.log(prob) + (1 - actual) * math.log(1 - prob))

        brier_score = brier_sum / n
        log_loss = log_loss_sum / n

        # Calibration analysis (10 buckets)
        buckets = self._compute_calibration_buckets(valid_outcomes, num_buckets=10)
        calibration_error = sum(
            b.calibration_error * b.prediction_count for b in buckets
        ) / n if n > 0 else 0

        # Baseline metrics (always predict base rate)
        base_rate = sum(1 for o in valid_outcomes if o.scored_goal) / n
        baseline_accuracy = max(base_rate, 1 - base_rate)  # Predict majority class
        baseline_brier = base_rate * (1 - base_rate)  # Variance of binary outcome

        improvement = (baseline_brier - brier_score) / baseline_brier if baseline_brier > 0 else 0

        return EvaluationMetrics(
            total_predictions=n,
            date_range_start=min(dates),
            date_range_end=max(dates),
            accuracy=accuracy,
            precision=precision,
            recall=recall,
            f1_score=f1,
            brier_score=brier_score,
            log_loss=log_loss,
            roc_auc=self._compute_roc_auc(valid_outcomes),
            calibration_error=calibration_error,
            calibration_buckets=buckets,
            baseline_accuracy=baseline_accuracy,
            baseline_brier=baseline_brier,
            improvement_vs_baseline=improvement,
        )

    def _compute_calibration_buckets(
        self,
        outcomes: list[PredictionOutcome],
        num_buckets: int = 10,
    ) -> list[CalibrationBucket]:
        """Compute calibration buckets for probability calibration analysis."""
        buckets = []
        bucket_size = 1.0 / num_buckets

        for i in range(num_buckets):
            bucket_min = i * bucket_size
            bucket_max = (i + 1) * bucket_size

            # Filter outcomes in this bucket
            in_bucket = [
                o for o in outcomes
                if bucket_min <= o.prob_goal < bucket_max or (i == num_buckets - 1 and o.prob_goal == 1.0)
            ]

            if in_bucket:
                actual_rate = sum(1 for o in in_bucket if o.scored_goal) / len(in_bucket)
            else:
                actual_rate = 0.0

            expected_rate = (bucket_min + bucket_max) / 2
            error = abs(actual_rate - expected_rate)

            buckets.append(CalibrationBucket(
                bucket_min=bucket_min,
                bucket_max=bucket_max,
                prediction_count=len(in_bucket),
                actual_rate=actual_rate,
                expected_rate=expected_rate,
                calibration_error=error,
            ))

        return buckets

    def _compute_roc_auc(self, outcomes: list[PredictionOutcome]) -> float | None:
        """Compute ROC AUC using the trapezoidal rule."""
        if len(outcomes) < 2:
            return None

        # Sort by probability descending
        sorted_outcomes = sorted(outcomes, key=lambda o: o.prob_goal, reverse=True)

        # Count positives and negatives
        n_pos = sum(1 for o in outcomes if o.scored_goal)
        n_neg = len(outcomes) - n_pos

        if n_pos == 0 or n_neg == 0:
            return None

        # Compute TPR and FPR at each threshold
        tpr_prev, fpr_prev = 0.0, 0.0
        tp, fp = 0, 0
        auc = 0.0

        for o in sorted_outcomes:
            if o.scored_goal:
                tp += 1
            else:
                fp += 1

            tpr = tp / n_pos
            fpr = fp / n_neg

            # Trapezoidal rule
            auc += (fpr - fpr_prev) * (tpr + tpr_prev) / 2
            tpr_prev, fpr_prev = tpr, fpr

        return auc


async def run_model_evaluation(
    db: AsyncSession,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    """Run full model evaluation and return results."""
    evaluator = ModelEvaluator(db)

    # Get validated predictions
    outcomes = await evaluator.get_validated_predictions(start_date, end_date)

    if not outcomes:
        return {
            "status": "no_data",
            "message": "No validated predictions found for the date range",
            "suggestion": "Use /api/audit/log-tonight to log predictions, then /api/audit/validate/{date} after games complete",
        }

    # Compute metrics
    metrics = evaluator.compute_metrics(outcomes)

    return {
        "status": "success",
        "metrics": metrics.to_dict(),
        "interpretation": _interpret_metrics(metrics),
    }


def _interpret_metrics(metrics: EvaluationMetrics) -> dict:
    """Provide human-readable interpretation of metrics."""
    interpretations = {}

    # Brier score interpretation
    if metrics.brier_score < 0.15:
        interpretations["brier"] = "Excellent - predictions are well-calibrated"
    elif metrics.brier_score < 0.25:
        interpretations["brier"] = "Good - predictions are reasonably calibrated"
    elif metrics.brier_score < 0.35:
        interpretations["brier"] = "Fair - predictions have room for improvement"
    else:
        interpretations["brier"] = "Poor - predictions need significant work"

    # Calibration interpretation
    if metrics.calibration_error < 0.05:
        interpretations["calibration"] = "Well-calibrated - predicted probabilities match actual rates"
    elif metrics.calibration_error < 0.10:
        interpretations["calibration"] = "Slightly miscalibrated - minor adjustments needed"
    else:
        interpretations["calibration"] = "Miscalibrated - consider probability calibration techniques"

    # Baseline comparison
    if metrics.improvement_vs_baseline > 0.1:
        interpretations["vs_baseline"] = f"Model adds significant value ({metrics.improvement_vs_baseline*100:.1f}% better than baseline)"
    elif metrics.improvement_vs_baseline > 0:
        interpretations["vs_baseline"] = f"Model adds marginal value ({metrics.improvement_vs_baseline*100:.1f}% better than baseline)"
    else:
        interpretations["vs_baseline"] = "Model does not outperform naive baseline - needs improvement"

    return interpretations


# -------------------------------------------------------------------------
# Backtesting utilities
# -------------------------------------------------------------------------


async def backtest_model(
    db: AsyncSession,
    start_date: date,
    end_date: date,
    window_size: int = 30,
) -> list[dict]:
    """
    Run rolling window backtest of the prediction model.

    For each day in the range, generates predictions using only data
    available before that day, then compares to actual outcomes.
    """
    from backend.src.agents.predictions import PredictionEngine

    results = []
    current = start_date
    engine = PredictionEngine(db)

    while current <= end_date:
        # Get games for this day
        games_result = await db.execute(
            text("""
                SELECT home_team_abbrev, away_team_abbrev
                FROM games
                WHERE game_date = :date AND is_completed = true
            """),
            {"date": current}
        )

        games = games_result.fetchall()
        if not games:
            current += timedelta(days=1)
            continue

        day_predictions = []
        day_actuals = []

        for game in games:
            try:
                # Generate predictions (using only historical data)
                matchup = await engine.get_matchup_prediction(
                    db, game.home_team_abbrev, game.away_team_abbrev, current
                )

                for player in matchup.home_players + matchup.away_players:
                    # Get actual outcome
                    actual_result = await db.execute(
                        text("""
                            SELECT goals, points FROM game_logs
                            WHERE player_id = :player_id AND game_date = :date
                        """),
                        {"player_id": player.player_id, "date": current}
                    )
                    actual = actual_result.fetchone()

                    if actual:
                        day_predictions.append({
                            "player_id": player.player_id,
                            "prob_goal": player.prob_goal,
                            "prob_point": player.prob_point,
                        })
                        day_actuals.append({
                            "goals": actual.goals,
                            "points": actual.points,
                            "scored_goal": actual.goals > 0,
                            "scored_point": actual.points > 0,
                        })

            except Exception as e:
                logger.warning("backtest_game_failed", date=current, error=str(e))
                continue

        if day_predictions:
            # Compute day's metrics
            n = len(day_predictions)
            brier = sum(
                (p["prob_goal"] - (1 if a["scored_goal"] else 0)) ** 2
                for p, a in zip(day_predictions, day_actuals)
            ) / n

            accuracy = sum(
                1 for p, a in zip(day_predictions, day_actuals)
                if (p["prob_goal"] > 0.5) == a["scored_goal"]
            ) / n

            results.append({
                "date": current.isoformat(),
                "predictions": n,
                "brier_score": round(brier, 4),
                "accuracy": round(accuracy, 4),
            })

        current += timedelta(days=1)

    return results
