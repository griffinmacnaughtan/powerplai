"""
xG Regression Tracker - Identifies players due for positive/negative regression.

Expected Goals (xG) measures shot quality - the probability each shot becomes a goal
based on location, shot type, game state, etc. Over time, actual goals regress toward xG.

This module identifies:
1. UNDERPERFORMERS: Players with Goals << xG (due for positive regression = BET ON)
2. OVERPERFORMERS: Players with Goals >> xG (due for negative regression = FADE)
3. SHOOTING LUCK: Players with extreme shooting percentages (high or low)

The key insight: A player with 5 goals on 12 xG isn't "bad" - they're unlucky.
Statistically, they should score ~7 more goals than average going forward.
This creates betting value because casual bettors see "5 goals" not "12 xG".
"""
import structlog
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


@dataclass
class RegressionCandidate:
    """A player identified as a regression candidate."""
    player_name: str
    team: str
    position: str
    games_played: int

    # Core stats
    goals: int
    xg: float
    differential: float  # goals - xG (negative = due for more goals)

    # Shooting analysis
    shots: int
    shooting_pct: float  # Actual
    expected_shooting_pct: float  # Based on xG
    shooting_luck: float  # Actual - Expected (negative = unlucky)

    # Per-game rates
    goals_per_game: float
    xg_per_game: float

    # Regression projection
    expected_regression_goals: float  # How many more/fewer goals expected
    regression_confidence: str  # high/medium/low based on sample size

    # Betting guidance
    regression_type: str  # "positive" or "negative"
    bet_recommendation: str  # What to do with this info
    value_rating: int  # 1-5 stars

    def to_dict(self) -> dict:
        return {
            "player_name": self.player_name,
            "team": self.team,
            "position": self.position,
            "games_played": self.games_played,
            "goals": self.goals,
            "xg": round(self.xg, 2),
            "differential": round(self.differential, 2),
            "shots": self.shots,
            "shooting_pct": round(self.shooting_pct, 3),
            "expected_shooting_pct": round(self.expected_shooting_pct, 3),
            "shooting_luck": round(self.shooting_luck, 3),
            "goals_per_game": round(self.goals_per_game, 2),
            "xg_per_game": round(self.xg_per_game, 2),
            "expected_regression_goals": round(self.expected_regression_goals, 1),
            "regression_confidence": self.regression_confidence,
            "regression_type": self.regression_type,
            "bet_recommendation": self.bet_recommendation,
            "value_rating": self.value_rating,
        }


@dataclass
class RegressionReport:
    """Complete regression analysis report."""
    generated_at: str
    season: str

    # Candidates
    positive_regression: list[RegressionCandidate]  # Due for more goals
    negative_regression: list[RegressionCandidate]  # Due for fewer goals

    # League context
    league_avg_shooting_pct: float
    league_avg_xg_per_game: float

    # Summary
    total_analyzed: int
    strong_positive_candidates: int  # >2 goal differential
    strong_negative_candidates: int

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "season": self.season,
            "positive_regression": [c.to_dict() for c in self.positive_regression],
            "negative_regression": [c.to_dict() for c in self.negative_regression],
            "league_context": {
                "avg_shooting_pct": round(self.league_avg_shooting_pct, 3),
                "avg_xg_per_game": round(self.league_avg_xg_per_game, 2),
            },
            "summary": {
                "total_analyzed": self.total_analyzed,
                "strong_positive_candidates": self.strong_positive_candidates,
                "strong_negative_candidates": self.strong_negative_candidates,
            },
        }


class RegressionTracker:
    """
    Tracks xG regression candidates across the league.

    The core insight: Over a large sample, Goals → xG.
    Players significantly above/below their xG will regress.

    For betting:
    - Positive regression candidates (Goals < xG) = BET TO SCORE
    - Negative regression candidates (Goals > xG) = FADE

    Confidence increases with:
    - More games played (larger sample)
    - Larger differential (stronger signal)
    - Consistent shot volume (not just a few lucky/unlucky games)
    """

    # Minimum thresholds
    MIN_GAMES = 15  # Need sample size for regression to matter
    MIN_SHOTS = 30  # Need shot volume
    MIN_XG = 3.0  # Need meaningful xG total

    # Significance thresholds
    STRONG_DIFFERENTIAL = 3.0  # >3 goals off from xG = strong signal
    MODERATE_DIFFERENTIAL = 1.5  # >1.5 = moderate signal

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_regression_report(
        self,
        season: str = None,
        min_games: int = None,
        top_n: int = 15,
    ) -> RegressionReport:
        """
        Generate comprehensive regression report for the season.

        Args:
            season: Season to analyze (default: current)
            min_games: Minimum games played (default: 15)
            top_n: Number of candidates per category
        """
        if not season:
            result = await self.db.execute(
                text("SELECT MAX(season) FROM player_season_stats")
            )
            season = result.scalar() or "20252026"

        min_gp = min_games or self.MIN_GAMES

        # Get all players with sufficient sample
        result = await self.db.execute(
            text("""
                SELECT
                    p.name,
                    p.team_abbrev,
                    p.position,
                    s.games_played,
                    s.goals,
                    s.xg,
                    s.shots,
                    s.shooting_pct
                FROM player_season_stats s
                JOIN players p ON s.player_id = p.id
                WHERE s.season = :season
                  AND s.games_played >= :min_games
                  AND s.xg IS NOT NULL
                  AND s.xg >= :min_xg
                  AND s.shots >= :min_shots
                  AND p.position != 'G'
                ORDER BY (s.goals - s.xg) ASC
            """),
            {
                "season": season,
                "min_games": min_gp,
                "min_xg": self.MIN_XG,
                "min_shots": self.MIN_SHOTS,
            }
        )

        rows = result.fetchall()

        if not rows:
            return RegressionReport(
                generated_at=datetime.utcnow().isoformat(),
                season=season,
                positive_regression=[],
                negative_regression=[],
                league_avg_shooting_pct=0.10,
                league_avg_xg_per_game=0.30,
                total_analyzed=0,
                strong_positive_candidates=0,
                strong_negative_candidates=0,
            )

        # Calculate league averages (convert Decimals to float)
        total_goals = sum(int(r.goals or 0) for r in rows)
        total_shots = sum(int(r.shots or 0) for r in rows)
        total_xg = sum(float(r.xg or 0) for r in rows)
        total_games = sum(int(r.games_played or 0) for r in rows)

        league_shooting_pct = total_goals / total_shots if total_shots > 0 else 0.10
        league_xg_per_game = total_xg / total_games if total_games > 0 else 0.30

        positive_candidates = []
        negative_candidates = []
        strong_positive = 0
        strong_negative = 0

        for row in rows:
            # Convert database values to proper types
            goals = int(row.goals or 0)
            xg = float(row.xg or 0)
            shots = int(row.shots or 0)
            games_played = int(row.games_played or 0)
            shooting_pct_raw = float(row.shooting_pct) if row.shooting_pct else None

            differential = goals - xg
            goals_per_game = goals / games_played if games_played > 0 else 0
            xg_per_game = xg / games_played if games_played > 0 else 0

            # Expected shooting % based on xG
            expected_shooting_pct = xg / shots if shots > 0 else 0.10
            actual_shooting_pct = shooting_pct_raw if shooting_pct_raw else (
                goals / shots if shots > 0 else 0
            )
            shooting_luck = actual_shooting_pct - expected_shooting_pct

            # Calculate expected regression
            # Assume ~50% regression toward mean over remaining season
            expected_regression = -differential * 0.5

            # Confidence based on sample size
            if games_played >= 40:
                confidence = "high"
            elif games_played >= 25:
                confidence = "medium"
            else:
                confidence = "low"

            # Determine regression type and recommendation
            if differential < -self.STRONG_DIFFERENTIAL:
                regression_type = "positive"
                bet_recommendation = "STRONG BUY - Significantly underperforming xG"
                value_rating = 5
                strong_positive += 1
            elif differential < -self.MODERATE_DIFFERENTIAL:
                regression_type = "positive"
                bet_recommendation = "BUY - Underperforming xG, likely to score more"
                value_rating = 4
            elif differential > self.STRONG_DIFFERENTIAL:
                regression_type = "negative"
                bet_recommendation = "STRONG FADE - Significantly overperforming xG"
                value_rating = 5
                strong_negative += 1
            elif differential > self.MODERATE_DIFFERENTIAL:
                regression_type = "negative"
                bet_recommendation = "FADE - Overperforming xG, due for cooldown"
                value_rating = 4
            else:
                # Not significant enough
                continue

            candidate = RegressionCandidate(
                player_name=row.name,
                team=row.team_abbrev or "UNK",
                position=row.position or "F",
                games_played=games_played,
                goals=goals,
                xg=xg,
                differential=differential,
                shots=shots,
                shooting_pct=actual_shooting_pct,
                expected_shooting_pct=expected_shooting_pct,
                shooting_luck=shooting_luck,
                goals_per_game=goals_per_game,
                xg_per_game=xg_per_game,
                expected_regression_goals=expected_regression,
                regression_confidence=confidence,
                regression_type=regression_type,
                bet_recommendation=bet_recommendation,
                value_rating=value_rating,
            )

            if regression_type == "positive":
                positive_candidates.append(candidate)
            else:
                negative_candidates.append(candidate)

        # Sort by absolute differential (strongest signals first)
        positive_candidates.sort(key=lambda c: c.differential)
        negative_candidates.sort(key=lambda c: c.differential, reverse=True)

        return RegressionReport(
            generated_at=datetime.utcnow().isoformat(),
            season=season,
            positive_regression=positive_candidates[:top_n],
            negative_regression=negative_candidates[:top_n],
            league_avg_shooting_pct=league_shooting_pct,
            league_avg_xg_per_game=league_xg_per_game,
            total_analyzed=len(rows),
            strong_positive_candidates=strong_positive,
            strong_negative_candidates=strong_negative,
        )

    async def get_player_regression_analysis(
        self,
        player_name: str,
        season: str = None,
    ) -> Optional[RegressionCandidate]:
        """Get detailed regression analysis for a specific player."""
        if not season:
            result = await self.db.execute(
                text("SELECT MAX(season) FROM player_season_stats")
            )
            season = result.scalar() or "20252026"

        result = await self.db.execute(
            text("""
                SELECT
                    p.name,
                    p.team_abbrev,
                    p.position,
                    s.games_played,
                    s.goals,
                    s.xg,
                    s.shots,
                    s.shooting_pct
                FROM player_season_stats s
                JOIN players p ON s.player_id = p.id
                WHERE s.season = :season
                  AND p.name ILIKE :name
                LIMIT 1
            """),
            {"season": season, "name": f"%{player_name}%"}
        )

        row = result.fetchone()
        if not row:
            return None

        if row.xg is None or row.games_played < 5:
            return None

        # Convert database values to proper types
        goals = int(row.goals or 0)
        xg = float(row.xg or 0)
        shots = int(row.shots or 0)
        games_played = int(row.games_played or 0)
        shooting_pct_raw = float(row.shooting_pct) if row.shooting_pct else None

        differential = goals - xg
        goals_per_game = goals / games_played if games_played > 0 else 0
        xg_per_game = xg / games_played if games_played > 0 else 0

        expected_shooting_pct = xg / shots if shots > 0 else 0.10
        actual_shooting_pct = shooting_pct_raw if shooting_pct_raw else (
            goals / shots if shots > 0 else 0
        )
        shooting_luck = actual_shooting_pct - expected_shooting_pct

        expected_regression = -differential * 0.5

        if games_played >= 40:
            confidence = "high"
        elif games_played >= 25:
            confidence = "medium"
        else:
            confidence = "low"

        if differential < -self.STRONG_DIFFERENTIAL:
            regression_type = "positive"
            bet_recommendation = "STRONG BUY - Significantly underperforming xG"
            value_rating = 5
        elif differential < -self.MODERATE_DIFFERENTIAL:
            regression_type = "positive"
            bet_recommendation = "BUY - Underperforming xG"
            value_rating = 4
        elif differential < 0:
            regression_type = "positive"
            bet_recommendation = "Slight positive regression expected"
            value_rating = 3
        elif differential > self.STRONG_DIFFERENTIAL:
            regression_type = "negative"
            bet_recommendation = "STRONG FADE - Significantly overperforming xG"
            value_rating = 5
        elif differential > self.MODERATE_DIFFERENTIAL:
            regression_type = "negative"
            bet_recommendation = "FADE - Overperforming xG"
            value_rating = 4
        else:
            regression_type = "negative"
            bet_recommendation = "Slight negative regression expected"
            value_rating = 3

        return RegressionCandidate(
            player_name=row.name,
            team=row.team_abbrev or "UNK",
            position=row.position or "F",
            games_played=games_played,
            goals=goals,
            xg=xg,
            differential=differential,
            shots=shots,
            shooting_pct=actual_shooting_pct,
            expected_shooting_pct=expected_shooting_pct,
            shooting_luck=shooting_luck,
            goals_per_game=goals_per_game,
            xg_per_game=xg_per_game,
            expected_regression_goals=expected_regression,
            regression_confidence=confidence,
            regression_type=regression_type,
            bet_recommendation=bet_recommendation,
            value_rating=value_rating,
        )


async def get_regression_report(db: AsyncSession) -> RegressionReport:
    """Convenience function to get regression report."""
    tracker = RegressionTracker(db)
    return await tracker.get_regression_report()
