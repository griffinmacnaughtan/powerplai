"""
Confidence calibration for the PowerplAI prediction engine.

What this fixes
---------------
The model emits a raw `prob_goal` (Poisson-based) and a `confidence`
label ("high"/"medium"/"low") derived from a fixed threshold on
`prob_goal * 0.7 + data_quality * 0.3`. Those thresholds were guessed,
never tied to actual hit rates. So a "high confidence" pick could
historically hit 30% of the time or 70% of the time and nobody would
know.

This module closes the loop:

1. Reads validated `prediction_audit` rows from the last N days.
2. Bins predictions into probability buckets (10 buckets, 0-10%, 10-20%,
   ..., 90-100%) and computes the actual hit rate per bucket. That gives
   us a non-parametric calibration map (isotonic-style without enforcing
   monotonicity, because small early-season samples wobble).
3. Derives data-driven confidence thresholds: "high" = the prob_goal
   level where empirical hit-rate first reaches some target (default
   0.45), "medium" = 0.25, "low" = below that. Falls back to the legacy
   thresholds (0.30 / 0.18) when the sample is too small.
4. Computes the Brier score on the same window so we can report it back
   to fans / pros and track model drift over time.

Public API
----------
- `await get_calibration_state(db)` -> CalibrationState
- `await refresh_calibration_state(db)` -> forces refresh
- `CalibrationState.calibrate_prob(raw, kind)` maps raw probability
   to a calibrated value
- `CalibrationState.label_confidence(raw_score)` returns "high"/"medium"/"low"
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from typing import Optional

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


# Calibration window. Primary: 60 days - covers a typical playoff run
# plus a few weeks of regular season, which gives a healthy sample
# without baking in months of stale model versions. If 60 days doesn't
# clear MIN_SAMPLE_FOR_CALIBRATION we expand adaptively up to MAX_WINDOW.
WINDOW_DAYS = 60
MAX_WINDOW_DAYS = 180

# Refresh the calibration snapshot every 6 hours. The underlying audit
# data only refreshes overnight, so this is plenty fresh.
TTL_SECONDS = 6 * 3600

# Minimum total validated predictions needed before we trust the
# data-driven thresholds. Below this we fall back to the legacy fixed
# thresholds so a cold-start system still labels things sanely.
MIN_SAMPLE_FOR_CALIBRATION = 60

# Hit-rate targets that define our confidence tiers. These are deliberately
# achievable: empirically, the prediction model rarely sustains >45% hit
# rate even in its strongest probability bucket, so a 45% target leaves us
# permanently uncalibrated. 0.35 / 0.20 reflects what the model can
# actually do, and lets the calibration system trade ANY "high confidence"
# label for honest evidence that the bucket really hits more often.
HIGH_TARGET_HIT_RATE = 0.35
MEDIUM_TARGET_HIT_RATE = 0.20

# Legacy fallback thresholds (the old fixed cuts).
LEGACY_HIGH_THRESHOLD = 0.30
LEGACY_MEDIUM_THRESHOLD = 0.18


@dataclass
class CalibrationBucket:
    """A probability bucket and its empirical hit rate."""
    lo: float
    hi: float
    n: int
    hits: int

    @property
    def midpoint(self) -> float:
        return (self.lo + self.hi) / 2.0

    @property
    def actual_rate(self) -> float:
        return self.hits / self.n if self.n > 0 else self.midpoint

    @property
    def calibration_error(self) -> float:
        return abs(self.actual_rate - self.midpoint)

    def to_dict(self) -> dict:
        return {
            "range": f"{int(self.lo * 100)}-{int(self.hi * 100)}%",
            "n": self.n,
            "hits": self.hits,
            "actual_rate": round(self.actual_rate, 3),
            "expected_rate": round(self.midpoint, 3),
            "error": round(self.calibration_error, 3),
        }


@dataclass
class CalibrationState:
    """Snapshot of model calibration for the active window."""
    window_days: int
    sample_size: int
    brier_goal: Optional[float]
    brier_point: Optional[float]
    goal_buckets: list[CalibrationBucket] = field(default_factory=list)
    point_buckets: list[CalibrationBucket] = field(default_factory=list)

    # Data-driven confidence thresholds (cut points on prob_goal).
    # If we don't have enough sample, falls back to legacy fixed values.
    high_threshold: float = LEGACY_HIGH_THRESHOLD
    medium_threshold: float = LEGACY_MEDIUM_THRESHOLD
    is_fallback: bool = True

    computed_at: float = 0.0

    def label_confidence(self, prob_goal: float) -> str:
        """Map a prediction's prob_goal to a calibrated confidence label."""
        if prob_goal >= self.high_threshold:
            return "high"
        if prob_goal >= self.medium_threshold:
            return "medium"
        return "low"

    def calibrate_prob(self, raw_prob: float, kind: str = "goal") -> float:
        """
        Map a raw predicted probability to a calibrated one.

        Strategy: look up the empirical hit rate of the bucket the raw
        probability falls into. If we have no bucket data (cold start),
        return the raw value. We blend toward the raw value when the
        bucket sample is small so a single low-N bucket doesn't dominate.
        """
        buckets = self.goal_buckets if kind == "goal" else self.point_buckets
        if not buckets:
            return raw_prob
        for b in buckets:
            if b.lo <= raw_prob < b.hi or (raw_prob >= 1.0 and b.hi >= 1.0):
                # Confidence-weighted blend: 5 hits = full trust in the
                # bucket's actual rate; less than that, blend with raw.
                trust = min(b.n / 30.0, 1.0)
                return raw_prob * (1 - trust) + b.actual_rate * trust
        return raw_prob

    def to_dict(self) -> dict:
        return {
            "window_days": self.window_days,
            "sample_size": self.sample_size,
            "thresholds": {
                "high": round(self.high_threshold, 3),
                "medium": round(self.medium_threshold, 3),
                "is_fallback": self.is_fallback,
            },
            "brier": {
                "goal": round(self.brier_goal, 4) if self.brier_goal is not None else None,
                "point": round(self.brier_point, 4) if self.brier_point is not None else None,
                "interpretation": _interpret_brier(self.brier_goal),
            },
            "goal_buckets": [b.to_dict() for b in self.goal_buckets],
            "point_buckets": [b.to_dict() for b in self.point_buckets],
            "age_seconds": round(time.time() - self.computed_at, 1) if self.computed_at else None,
        }


# Module-level cache (per worker).
_cache: Optional[CalibrationState] = None


def _interpret_brier(brier: Optional[float]) -> str:
    if brier is None:
        return "no data"
    if brier < 0.15:
        return "excellent"
    if brier < 0.20:
        return "good"
    if brier < 0.25:
        return "acceptable"
    return "needs work"


def _build_buckets(rows: list[tuple[float, bool]]) -> list[CalibrationBucket]:
    """
    Bin (prob, hit) pairs into 10 buckets of width 0.1.

    rows: list of (probability, hit) where hit is True/False/None.
    None hits are filtered out.
    """
    n_buckets = 10
    edges = [i / n_buckets for i in range(n_buckets + 1)]
    buckets = [CalibrationBucket(lo=edges[i], hi=edges[i + 1], n=0, hits=0) for i in range(n_buckets)]

    for prob, hit in rows:
        if hit is None or prob is None:
            continue
        # Find the bucket: prob in [lo, hi). Use last bucket for prob == 1.0
        if prob >= 1.0:
            idx = n_buckets - 1
        else:
            idx = min(int(prob * n_buckets), n_buckets - 1)
        buckets[idx].n += 1
        if hit:
            buckets[idx].hits += 1
    return buckets


def _brier_score(rows: list[tuple[float, bool]]) -> Optional[float]:
    """Mean (prob - actual)^2 across validated rows."""
    valid = [(p, 1.0 if h else 0.0) for p, h in rows if h is not None and p is not None]
    if not valid:
        return None
    return sum((p - a) ** 2 for p, a in valid) / len(valid)


def _derive_thresholds(buckets: list[CalibrationBucket]) -> tuple[float, float, bool]:
    """
    Find the prob_goal cut points at which the *aggregated* historical
    hit rate of predictions at-or-above that cut crosses each target tier.

    For each candidate cut T (the lower edge of each bucket), aggregate
    n and hits across all buckets [T, 1.0] and compute the resulting hit
    rate. The lowest T whose aggregated rate clears MEDIUM_TARGET_HIT_RATE
    becomes the medium threshold; the lowest T clearing HIGH_TARGET_HIT_RATE
    becomes the high threshold.

    This is much more robust than picking the lower edge of a single
    bucket: it averages out noise from small per-bucket samples and
    yields thresholds that actually correspond to picks hitting at
    the claimed rate.

    Returns (high_threshold, medium_threshold, is_fallback).
    """
    # Aggregate from a candidate cut all the way to 1.0. Walk LOW-TO-HIGH
    # candidate cuts; for each cut, sum n and hits of buckets >= cut.
    candidates: list[tuple[float, int, int, float]] = []  # (cut, n, hits, rate)
    for cut_bucket in buckets:
        cut = cut_bucket.lo
        n = sum(b.n for b in buckets if b.lo >= cut)
        hits = sum(b.hits for b in buckets if b.lo >= cut)
        if n < 5:
            continue
        rate = hits / n
        candidates.append((cut, n, hits, rate))

    if not candidates:
        return LEGACY_HIGH_THRESHOLD, LEGACY_MEDIUM_THRESHOLD, True

    # Find LOWEST cut where aggregated rate >= target.
    medium: Optional[float] = None
    high: Optional[float] = None
    for cut, n, hits, rate in candidates:
        if medium is None and rate >= MEDIUM_TARGET_HIT_RATE:
            medium = cut
        if high is None and rate >= HIGH_TARGET_HIT_RATE:
            high = cut

    # If high never qualifies but medium does, synthesize. Capped at 0.60
    # so we don't demand a threshold the model can't reach.
    if high is None and medium is not None:
        high = min(0.60, max(medium + 0.10, medium * 1.5))

    if high is None or medium is None:
        return LEGACY_HIGH_THRESHOLD, LEGACY_MEDIUM_THRESHOLD, True

    # Sanity: medium <= high, both within plausible bounds. Tiny samples
    # in early-season conditions can still produce noise so we guard.
    if medium > high:
        medium = max(0.10, high - 0.10)
    if not (0.15 <= high <= 0.65):
        return LEGACY_HIGH_THRESHOLD, LEGACY_MEDIUM_THRESHOLD, True
    if not (0.05 <= medium <= 0.50):
        return LEGACY_HIGH_THRESHOLD, LEGACY_MEDIUM_THRESHOLD, True

    return high, medium, False


async def _compute(db: AsyncSession) -> CalibrationState:
    """Pull validated audit rows and compute the calibration snapshot.

    Adaptive window: starts at WINDOW_DAYS. If that yields fewer than
    MIN_SAMPLE_FOR_CALIBRATION rows, widens the window in 30-day steps
    up to MAX_WINDOW_DAYS. Keeps us calibrated through sparse-prediction
    periods (e.g. between regular season and playoffs) without baking
    in indefinitely-old data when we have a fresh sample.
    """
    end = date.today()
    rows = []
    window_days_used = WINDOW_DAYS

    for window_days in range(WINDOW_DAYS, MAX_WINDOW_DAYS + 1, 30):
        start = end - timedelta(days=window_days)
        try:
            result = await db.execute(
                text("""
                    SELECT prob_goal, prob_point, goal_hit, point_hit
                    FROM prediction_audit
                    WHERE game_date BETWEEN :start AND :end
                      AND validated_at IS NOT NULL
                      AND goal_hit IS NOT NULL
                """),
                {"start": start, "end": end},
            )
            rows = result.fetchall()
            window_days_used = window_days
        except Exception as e:
            logger.warning("calibration_audit_query_failed", window=window_days, error=str(e))
            rows = []
            break
        if len(rows) >= MIN_SAMPLE_FOR_CALIBRATION:
            break

    goal_pairs: list[tuple[float, bool]] = [(r.prob_goal, r.goal_hit) for r in rows]
    point_pairs: list[tuple[float, bool]] = [(r.prob_point, r.point_hit) for r in rows]

    goal_buckets = _build_buckets(goal_pairs)
    point_buckets = _build_buckets(point_pairs)
    brier_goal = _brier_score(goal_pairs)
    brier_point = _brier_score(point_pairs)

    sample = len([r for r in rows if r.goal_hit is not None])
    if sample >= MIN_SAMPLE_FOR_CALIBRATION:
        high_t, med_t, fallback = _derive_thresholds(goal_buckets)
    else:
        high_t, med_t, fallback = LEGACY_HIGH_THRESHOLD, LEGACY_MEDIUM_THRESHOLD, True

    state = CalibrationState(
        window_days=window_days_used,
        sample_size=sample,
        brier_goal=brier_goal,
        brier_point=brier_point,
        goal_buckets=goal_buckets,
        point_buckets=point_buckets,
        high_threshold=high_t,
        medium_threshold=med_t,
        is_fallback=fallback,
        computed_at=time.time(),
    )
    logger.info(
        "calibration_refreshed",
        sample=sample,
        brier_goal=brier_goal,
        brier_point=brier_point,
        high_threshold=high_t,
        medium_threshold=med_t,
        is_fallback=fallback,
    )
    return state


async def get_calibration_state(db: AsyncSession) -> CalibrationState:
    """Return cached calibration, refreshing if older than TTL_SECONDS."""
    global _cache
    now = time.time()
    if _cache is not None and (now - _cache.computed_at) < TTL_SECONDS:
        return _cache
    _cache = await _compute(db)
    return _cache


async def refresh_calibration_state(db: AsyncSession) -> CalibrationState:
    """Force refresh, ignoring TTL."""
    global _cache
    _cache = await _compute(db)
    return _cache
