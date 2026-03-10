"""
Edge Finder Engine - Identifies high-value betting opportunities.

This module scans all games and surfaces situations where multiple
positive factors stack to create potential betting edges:
- Hot streaks (recent form significantly above season average)
- Weak goalie matchups (backup or below-average starter)
- High-pace games (both teams score a lot)
- Strong H2H history (player dominates specific opponent)
- xG regression candidates (underperforming expected goals)

The edge score combines these factors with the base scoring probability
to rank opportunities by expected value.
"""
import structlog
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.agents.predictions import (
    PredictionEngine,
    PlayerPrediction,
    MatchupPrediction,
)

logger = structlog.get_logger()


class EdgeType(str, Enum):
    """Types of edges we can identify."""
    HOT_STREAK = "hot_streak"
    COLD_GOALIE = "cold_goalie"
    BACKUP_GOALIE = "backup_goalie"
    HIGH_PACE = "high_pace"
    H2H_DOMINATION = "h2h_domination"
    XG_REGRESSION = "xg_regression"
    HOME_COOKING = "home_cooking"
    REVENGE_GAME = "revenge_game"
    MULTI_STACK = "multi_stack"  # Multiple edges stacking


@dataclass
class EdgeFactor:
    """A single edge factor contributing to the opportunity."""
    edge_type: EdgeType
    description: str
    boost: float  # How much this boosts the edge score (0-1)
    details: dict = field(default_factory=dict)


@dataclass
class BettingEdge:
    """A complete betting edge opportunity."""
    player_name: str
    team: str
    opponent: str
    game_time: str
    is_home: bool

    # Core probabilities from model
    prob_goal: float
    prob_point: float
    prob_multi_point: float

    # Edge analysis
    edge_score: float  # 0-100, higher = stronger edge
    edge_grade: str  # A+, A, B+, B, C (only show B+ and above)

    # Context
    model_confidence: str
    recent_form_ppg: float
    season_avg_ppg: float
    xg_differential: float  # Goals - xG (negative = due for regression up)

    # Betting guidance
    suggested_bet: str  # "Anytime Scorer", "2+ Points", etc.
    estimated_fair_odds: int  # American odds based on model probability
    value_threshold: int  # Max odds to bet (where edge disappears)

    # Fields with defaults must come last
    edge_factors: list[EdgeFactor] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "player_name": self.player_name,
            "team": self.team,
            "opponent": self.opponent,
            "game_time": self.game_time,
            "is_home": self.is_home,
            "prob_goal": round(self.prob_goal, 3),
            "prob_point": round(self.prob_point, 3),
            "prob_multi_point": round(self.prob_multi_point, 3),
            "edge_score": round(self.edge_score, 1),
            "edge_grade": self.edge_grade,
            "edge_factors": [
                {
                    "type": f.edge_type.value,
                    "description": f.description,
                    "boost": round(f.boost, 2),
                }
                for f in self.edge_factors
            ],
            "model_confidence": self.model_confidence,
            "recent_form_ppg": round(self.recent_form_ppg, 2),
            "season_avg_ppg": round(self.season_avg_ppg, 2),
            "xg_differential": round(self.xg_differential, 2),
            "suggested_bet": self.suggested_bet,
            "estimated_fair_odds": self.estimated_fair_odds,
            "value_threshold": self.value_threshold,
        }


@dataclass
class TonightEdgeReport:
    """Complete edge report for tonight's games."""
    generated_at: str
    game_count: int
    edges_found: int
    top_edges: list[BettingEdge]
    by_game: dict[str, list[BettingEdge]]  # "TOR@BOS" -> edges

    # Summary stats
    a_plus_edges: int
    a_edges: int
    b_plus_edges: int

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "game_count": self.game_count,
            "edges_found": self.edges_found,
            "summary": {
                "a_plus": self.a_plus_edges,
                "a": self.a_edges,
                "b_plus": self.b_plus_edges,
            },
            "top_edges": [e.to_dict() for e in self.top_edges],
            "by_game": {
                game: [e.to_dict() for e in edges]
                for game, edges in self.by_game.items()
            },
        }


class EdgeFinder:
    """
    Identifies high-value betting opportunities by analyzing
    multiple factors that stack to create edges.
    """

    # Thresholds for edge detection
    HOT_STREAK_THRESHOLD = 0.3  # Recent form > season avg by this much
    COLD_GOALIE_THRESHOLD = 0.895  # Below this SV% = cold
    HIGH_PACE_THRESHOLD = 6.5  # Expected total goals
    H2H_BOOST_THRESHOLD = 0.25  # H2H PPG > season avg by this much
    XG_REGRESSION_THRESHOLD = -2.0  # Goals - xG below this = due

    # Edge score weights
    WEIGHTS = {
        EdgeType.HOT_STREAK: 15,
        EdgeType.COLD_GOALIE: 20,
        EdgeType.BACKUP_GOALIE: 25,
        EdgeType.HIGH_PACE: 10,
        EdgeType.H2H_DOMINATION: 12,
        EdgeType.XG_REGRESSION: 18,
        EdgeType.HOME_COOKING: 8,
        EdgeType.MULTI_STACK: 10,  # Bonus for 3+ factors
    }

    def __init__(self, db: AsyncSession):
        self.db = db
        self.prediction_engine = PredictionEngine(db)

    async def find_tonight_edges(
        self,
        min_grade: str = "B+",
        max_results: int = 20,
    ) -> TonightEdgeReport:
        """
        Find all betting edges for tonight's games.

        Args:
            min_grade: Minimum edge grade to include (A+, A, B+, B, C)
            max_results: Maximum edges to return in top list
        """
        # Get tonight's predictions
        matchups = await self.prediction_engine.predict_tonight()

        if not matchups:
            return TonightEdgeReport(
                generated_at=datetime.utcnow().isoformat(),
                game_count=0,
                edges_found=0,
                top_edges=[],
                by_game={},
                a_plus_edges=0,
                a_edges=0,
                b_plus_edges=0,
            )

        all_edges: list[BettingEdge] = []
        by_game: dict[str, list[BettingEdge]] = {}

        for matchup in matchups:
            game_key = f"{matchup.away_team}@{matchup.home_team}"
            game_edges = []

            # Analyze all players in this matchup
            all_players = matchup.home_players + matchup.away_players

            for player_pred in all_players:
                edge = await self._analyze_player_edge(
                    player_pred, matchup
                )

                if edge and self._meets_grade_threshold(edge.edge_grade, min_grade):
                    game_edges.append(edge)
                    all_edges.append(edge)

            by_game[game_key] = sorted(
                game_edges, key=lambda e: e.edge_score, reverse=True
            )[:5]  # Top 5 per game

        # Sort all edges by score
        all_edges.sort(key=lambda e: e.edge_score, reverse=True)
        top_edges = all_edges[:max_results]

        # Count grades
        a_plus = sum(1 for e in all_edges if e.edge_grade == "A+")
        a = sum(1 for e in all_edges if e.edge_grade == "A")
        b_plus = sum(1 for e in all_edges if e.edge_grade == "B+")

        return TonightEdgeReport(
            generated_at=datetime.utcnow().isoformat(),
            game_count=len(matchups),
            edges_found=len(all_edges),
            top_edges=top_edges,
            by_game=by_game,
            a_plus_edges=a_plus,
            a_edges=a,
            b_plus_edges=b_plus,
        )

    async def _analyze_player_edge(
        self,
        pred: PlayerPrediction,
        matchup: MatchupPrediction,
    ) -> Optional[BettingEdge]:
        """Analyze a single player for betting edges."""

        factors: list[EdgeFactor] = []
        base_score = pred.prob_goal * 100  # Start with goal probability

        # Get xG differential for this player
        xg_diff = await self._get_xg_differential(pred.player_name)

        # Factor 1: Hot Streak
        if pred.recent_form_ppg and pred.season_avg_ppg:
            form_diff = pred.recent_form_ppg - pred.season_avg_ppg
            if form_diff >= self.HOT_STREAK_THRESHOLD:
                boost = min(form_diff / 0.5, 1.0)  # Cap at 1.0
                factors.append(EdgeFactor(
                    edge_type=EdgeType.HOT_STREAK,
                    description=f"Hot streak: {pred.recent_form_ppg:.2f} PPG last 5 games (season avg: {pred.season_avg_ppg:.2f})",
                    boost=boost,
                    details={"recent": pred.recent_form_ppg, "season": pred.season_avg_ppg}
                ))

        # Factor 2: Goalie Matchup
        opp_goalie = (
            matchup.away_goalie if pred.is_home else matchup.home_goalie
        )
        if opp_goalie and opp_goalie.get("save_pct"):
            sv_pct = opp_goalie["save_pct"]
            if sv_pct < self.COLD_GOALIE_THRESHOLD:
                boost = (self.COLD_GOALIE_THRESHOLD - sv_pct) / 0.02  # Each 0.02 below = more boost
                boost = min(boost, 1.0)
                factors.append(EdgeFactor(
                    edge_type=EdgeType.COLD_GOALIE,
                    description=f"Weak goalie: {opp_goalie.get('name', 'Unknown')} ({sv_pct:.3f} SV%)",
                    boost=boost,
                    details={"goalie": opp_goalie.get("name"), "sv_pct": sv_pct}
                ))

            # Check if likely backup (GAA > 3.0 or SV% < 0.890)
            gaa = opp_goalie.get("gaa", 2.8)
            if sv_pct < 0.890 or gaa > 3.2:
                factors.append(EdgeFactor(
                    edge_type=EdgeType.BACKUP_GOALIE,
                    description=f"Likely backup: {opp_goalie.get('name', 'Unknown')} (GAA: {gaa:.2f})",
                    boost=0.8,
                    details={"goalie": opp_goalie.get("name"), "gaa": gaa}
                ))

        # Factor 3: High Pace Game
        if matchup.expected_total_goals and matchup.expected_total_goals >= self.HIGH_PACE_THRESHOLD:
            boost = (matchup.expected_total_goals - 6.0) / 1.5  # Scale from 6.0
            boost = min(max(boost, 0), 1.0)
            factors.append(EdgeFactor(
                edge_type=EdgeType.HIGH_PACE,
                description=f"High-pace game: {matchup.expected_total_goals:.1f} expected total goals",
                boost=boost,
                details={"expected_goals": matchup.expected_total_goals}
            ))

        # Factor 4: H2H Domination
        if pred.h2h_ppg and pred.season_avg_ppg:
            h2h_boost = pred.h2h_ppg - pred.season_avg_ppg
            if h2h_boost >= self.H2H_BOOST_THRESHOLD:
                boost = min(h2h_boost / 0.5, 1.0)
                factors.append(EdgeFactor(
                    edge_type=EdgeType.H2H_DOMINATION,
                    description=f"Owns this opponent: {pred.h2h_ppg:.2f} PPG vs {pred.opponent} (career)",
                    boost=boost,
                    details={"h2h_ppg": pred.h2h_ppg, "opponent": pred.opponent}
                ))

        # Factor 5: xG Regression (underperforming expected goals)
        if xg_diff < self.XG_REGRESSION_THRESHOLD:
            boost = min(abs(xg_diff) / 5.0, 1.0)  # Scale by how much under
            factors.append(EdgeFactor(
                edge_type=EdgeType.XG_REGRESSION,
                description=f"Due for regression: {abs(xg_diff):.1f} goals below expected",
                boost=boost,
                details={"xg_differential": xg_diff}
            ))

        # Factor 6: Home Cooking (strong home/away split)
        if pred.home_away_adjustment and pred.is_home:
            if pred.home_away_adjustment > 0.15:
                boost = min(pred.home_away_adjustment / 0.3, 1.0)
                factors.append(EdgeFactor(
                    edge_type=EdgeType.HOME_COOKING,
                    description=f"Home boost: +{pred.home_away_adjustment:.2f} PPG at home",
                    boost=boost,
                    details={"adjustment": pred.home_away_adjustment}
                ))

        # No significant edges found
        if not factors:
            return None

        # Calculate edge score
        edge_score = base_score
        for factor in factors:
            edge_score += self.WEIGHTS[factor.edge_type] * factor.boost

        # Multi-stack bonus (3+ factors)
        if len(factors) >= 3:
            edge_score += self.WEIGHTS[EdgeType.MULTI_STACK]
            factors.append(EdgeFactor(
                edge_type=EdgeType.MULTI_STACK,
                description=f"Multi-factor stack: {len(factors)-1} edges combining",
                boost=1.0,
            ))

        # Determine grade
        edge_grade = self._calculate_grade(edge_score)

        # Only return if meaningful edge
        if edge_score < 25:  # Minimum threshold
            return None

        # Calculate fair odds and value threshold
        fair_odds = self._probability_to_american_odds(pred.prob_goal)
        value_threshold = self._calculate_value_threshold(pred.prob_goal)

        # Determine suggested bet type
        if pred.prob_multi_point > 0.25:
            suggested_bet = "2+ Points"
        elif pred.prob_goal > 0.35:
            suggested_bet = "Anytime Scorer"
        elif pred.prob_point > 0.55:
            suggested_bet = "1+ Points"
        else:
            suggested_bet = "Anytime Scorer"

        return BettingEdge(
            player_name=pred.player_name,
            team=pred.team,
            opponent=pred.opponent,
            game_time=matchup.start_time or "TBD",
            is_home=pred.is_home,
            prob_goal=pred.prob_goal,
            prob_point=pred.prob_point,
            prob_multi_point=pred.prob_multi_point,
            edge_score=edge_score,
            edge_grade=edge_grade,
            model_confidence=pred.confidence,
            recent_form_ppg=pred.recent_form_ppg or 0,
            season_avg_ppg=pred.season_avg_ppg or 0,
            xg_differential=xg_diff,
            suggested_bet=suggested_bet,
            estimated_fair_odds=fair_odds,
            value_threshold=value_threshold,
            edge_factors=factors,  # Default field must be last
        )

    async def _get_xg_differential(self, player_name: str) -> float:
        """Get goals - xG differential for a player this season."""
        result = await self.db.execute(
            text("""
                SELECT goals, xg
                FROM player_season_stats
                WHERE player_id = (
                    SELECT id FROM players WHERE name ILIKE :name LIMIT 1
                )
                AND season = (SELECT MAX(season) FROM player_season_stats)
                LIMIT 1
            """),
            {"name": f"%{player_name}%"}
        )
        row = result.fetchone()

        if row and row.goals is not None and row.xg is not None:
            return row.goals - row.xg
        return 0.0

    def _calculate_grade(self, score: float) -> str:
        """Convert edge score to letter grade."""
        if score >= 70:
            return "A+"
        elif score >= 55:
            return "A"
        elif score >= 42:
            return "B+"
        elif score >= 30:
            return "B"
        else:
            return "C"

    def _meets_grade_threshold(self, grade: str, min_grade: str) -> bool:
        """Check if grade meets minimum threshold."""
        grade_order = ["C", "B", "B+", "A", "A+"]
        return grade_order.index(grade) >= grade_order.index(min_grade)

    def _probability_to_american_odds(self, prob: float) -> int:
        """Convert probability to American odds."""
        if prob <= 0:
            return 10000
        if prob >= 1:
            return -10000

        if prob >= 0.5:
            # Favorite: negative odds
            return int(-100 * prob / (1 - prob))
        else:
            # Underdog: positive odds
            return int(100 * (1 - prob) / prob)

    def _calculate_value_threshold(self, prob: float) -> int:
        """
        Calculate the maximum odds where betting still has value.

        Includes a 5% edge buffer - we want at least 5% expected ROI.
        """
        # Require 5% edge minimum
        adjusted_prob = prob * 0.95  # Reduce our confidence slightly
        return self._probability_to_american_odds(adjusted_prob)


async def get_tonight_edges(db: AsyncSession) -> TonightEdgeReport:
    """Convenience function to get tonight's edges."""
    finder = EdgeFinder(db)
    return await finder.find_tonight_edges()
