"""
Odds Integration & Value Calculator - Real-time odds comparison with expected value.

This module:
1. Fetches live odds from The Odds API (free tier: 500 requests/month)
2. Compares sportsbook odds to model probabilities
3. Calculates Expected Value (EV) for each bet
4. Recommends bet sizing using Kelly Criterion
5. Tracks value bets for ROI analysis

The core formula:
    EV = (Probability × Payout) - (1 - Probability)

A bet has positive EV when our model probability exceeds implied probability.
Kelly Criterion tells us optimal bet sizing based on edge size.
"""
import os
import structlog
from dataclasses import dataclass, field
from datetime import datetime, date, date
from typing import Optional
import httpx

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


# The Odds API configuration
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"


@dataclass
class OddsLine:
    """A single odds line from a sportsbook."""
    sportsbook: str
    player_name: str
    market: str  # "anytime_scorer", "points_over", etc.
    odds: int  # American odds
    implied_probability: float
    line: Optional[float] = None  # For over/under (e.g., 0.5 points)


@dataclass
class ValueBet:
    """A bet identified as having positive expected value."""
    player_name: str
    team: str
    opponent: str
    game_time: str

    # Market details
    market: str
    best_odds: int
    sportsbook: str

    # Model vs market
    model_probability: float
    implied_probability: float
    edge: float  # model_prob - implied_prob

    # Value metrics
    expected_value: float  # EV per $1 bet
    expected_roi_pct: float  # EV as percentage

    # Kelly Criterion
    kelly_fraction: float  # Optimal bet size as fraction of bankroll
    kelly_half: float  # Half-Kelly (more conservative)
    recommended_bet_pct: float  # Our recommendation (usually half-Kelly)

    # Confidence
    model_confidence: str
    value_grade: str  # A+, A, B+, B based on EV

    def to_dict(self) -> dict:
        return {
            "player_name": self.player_name,
            "team": self.team,
            "opponent": self.opponent,
            "game_time": self.game_time,
            "market": self.market,
            "best_odds": self.best_odds,
            "sportsbook": self.sportsbook,
            "model_probability": round(self.model_probability, 3),
            "implied_probability": round(self.implied_probability, 3),
            "edge": round(self.edge, 3),
            "expected_value": round(self.expected_value, 3),
            "expected_roi_pct": round(self.expected_roi_pct, 1),
            "kelly_fraction": round(self.kelly_fraction, 4),
            "kelly_half": round(self.kelly_half, 4),
            "recommended_bet_pct": round(self.recommended_bet_pct, 2),
            "model_confidence": self.model_confidence,
            "value_grade": self.value_grade,
        }


@dataclass
class ValueReport:
    """Complete value betting report."""
    generated_at: str
    games_analyzed: int
    odds_source: str
    api_calls_remaining: Optional[int]

    # Value bets found
    value_bets: list[ValueBet]
    total_positive_ev: int

    # Bankroll recommendations
    total_kelly_exposure: float  # Sum of recommended bets
    max_single_bet_pct: float

    # Summary by grade
    a_plus_bets: int
    a_bets: int
    b_plus_bets: int

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "games_analyzed": self.games_analyzed,
            "odds_source": self.odds_source,
            "api_calls_remaining": self.api_calls_remaining,
            "value_bets": [b.to_dict() for b in self.value_bets],
            "total_positive_ev": self.total_positive_ev,
            "bankroll_guidance": {
                "total_kelly_exposure": round(self.total_kelly_exposure, 2),
                "max_single_bet_pct": round(self.max_single_bet_pct, 2),
            },
            "summary": {
                "a_plus": self.a_plus_bets,
                "a": self.a_bets,
                "b_plus": self.b_plus_bets,
            },
        }


class OddsValueCalculator:
    """
    Integrates live odds with model predictions to find value bets.

    Uses The Odds API for live sportsbook odds (free tier available).
    Falls back to estimated "typical" odds if API unavailable.
    """

    # Minimum edge to consider (after accounting for variance)
    MIN_EDGE = 0.03  # 3% edge minimum

    # Kelly Criterion settings
    MAX_KELLY = 0.10  # Never recommend more than 10% of bankroll
    KELLY_DIVISOR = 2  # Use half-Kelly for safety

    def __init__(self, db: AsyncSession):
        self.db = db
        self.api_key = ODDS_API_KEY

    async def get_live_odds(self, sport: str = "icehockey_nhl") -> dict:
        """
        Fetch live odds from The Odds API.

        Supports:
        - icehockey_nhl (NHL regular season)
        - icehockey_olympic_mens (Olympic men's hockey - during tournament)

        Returns dict mapping game_key -> list of OddsLine
        """
        if not self.api_key:
            logger.warning("odds_api_key_not_set")
            return {}, None

        url = f"{ODDS_API_BASE}/sports/{sport}/odds"
        params = {
            "apiKey": self.api_key,
            "regions": "us",
            "markets": "player_goal_scorer,player_points_over_under",
            "oddsFormat": "american",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, params=params)

                if response.status_code == 422:
                    # Player props not available on current API tier
                    logger.warning("odds_api_player_props_unavailable", tier_msg="Upgrade to player props tier")
                    return {}, None

                response.raise_for_status()

                data = response.json()
                remaining = response.headers.get("x-requests-remaining")

                logger.info(
                    "odds_api_fetched",
                    games=len(data),
                    remaining=remaining,
                )

                return self._parse_odds_response(data), int(remaining) if remaining else None

        except httpx.HTTPStatusError as e:
            logger.warning("odds_api_error", status=e.response.status_code, body=e.response.text[:200])
            return {}, None
        except Exception as e:
            logger.warning("odds_api_failed", error=str(e))
            return {}, None

    def _parse_odds_response(self, data: list) -> dict:
        """Parse The Odds API response into our format."""
        result = {}

        for game in data:
            home = game.get("home_team", "")
            away = game.get("away_team", "")
            game_key = f"{away}@{home}"
            result[game_key] = []

            for bookmaker in game.get("bookmakers", []):
                book_name = bookmaker.get("title", "Unknown")

                for market in bookmaker.get("markets", []):
                    market_type = market.get("key", "")

                    for outcome in market.get("outcomes", []):
                        player_name = outcome.get("description", "")
                        odds = outcome.get("price", 0)
                        point = outcome.get("point")

                        if player_name and odds:
                            implied_prob = self._american_to_probability(odds)
                            result[game_key].append(OddsLine(
                                sportsbook=book_name,
                                player_name=player_name,
                                market=market_type,
                                odds=odds,
                                implied_probability=implied_prob,
                                line=point,
                            ))

        return result

    def _american_to_probability(self, odds: int) -> float:
        """Convert American odds to implied probability."""
        if odds > 0:
            return 100 / (odds + 100)
        else:
            return abs(odds) / (abs(odds) + 100)

    def _probability_to_american(self, prob: float) -> int:
        """Convert probability to American odds."""
        if prob <= 0:
            return 10000
        if prob >= 1:
            return -10000

        if prob >= 0.5:
            return int(-100 * prob / (1 - prob))
        else:
            return int(100 * (1 - prob) / prob)

    def calculate_expected_value(
        self,
        model_prob: float,
        odds: int,
    ) -> tuple[float, float]:
        """
        Calculate expected value for a bet.

        Returns:
            (EV per $1, ROI percentage)
        """
        if odds > 0:
            # Underdog: win = odds/100 profit
            profit_if_win = odds / 100
        else:
            # Favorite: win = 100/|odds| profit
            profit_if_win = 100 / abs(odds)

        # EV = P(win) * profit - P(lose) * stake
        ev = (model_prob * profit_if_win) - ((1 - model_prob) * 1)

        roi_pct = ev * 100  # As percentage

        return ev, roi_pct

    def calculate_kelly(
        self,
        model_prob: float,
        odds: int,
    ) -> tuple[float, float]:
        """
        Calculate Kelly Criterion bet sizing.

        Kelly formula: f* = (bp - q) / b
        where:
            b = decimal odds - 1 (net profit per $1)
            p = probability of winning
            q = probability of losing (1 - p)

        Returns:
            (full_kelly, half_kelly)
        """
        if odds > 0:
            b = odds / 100
        else:
            b = 100 / abs(odds)

        p = model_prob
        q = 1 - model_prob

        kelly = (b * p - q) / b if b > 0 else 0
        kelly = max(0, min(kelly, self.MAX_KELLY))  # Clamp to max

        return kelly, kelly / self.KELLY_DIVISOR

    async def find_value_bets(
        self,
        predictions: list,  # List of PlayerPrediction from our model
        min_edge: float = None,
    ) -> ValueReport:
        """
        Find value bets by comparing model to live odds.

        If no live odds available, uses estimated typical market odds.
        """
        min_edge = min_edge or self.MIN_EDGE

        # Try to get live odds
        live_odds, remaining = await self.get_live_odds()

        value_bets = []

        for pred in predictions:
            # Look for matching player in odds
            player_odds = self._find_player_odds(pred.player_name, live_odds)

            if player_odds:
                # Have live odds - compare directly
                for odds_line in player_odds:
                    edge = pred.prob_goal - odds_line.implied_probability

                    if edge >= min_edge:
                        ev, roi = self.calculate_expected_value(
                            pred.prob_goal, odds_line.odds
                        )
                        kelly, half_kelly = self.calculate_kelly(
                            pred.prob_goal, odds_line.odds
                        )

                        value_bets.append(ValueBet(
                            player_name=pred.player_name,
                            team=pred.team,
                            opponent=pred.opponent,
                            game_time=getattr(pred, 'game_time', 'TBD'),
                            market=odds_line.market,
                            best_odds=odds_line.odds,
                            sportsbook=odds_line.sportsbook,
                            model_probability=pred.prob_goal,
                            implied_probability=odds_line.implied_probability,
                            edge=edge,
                            expected_value=ev,
                            expected_roi_pct=roi,
                            kelly_fraction=kelly,
                            kelly_half=half_kelly,
                            recommended_bet_pct=half_kelly * 100,
                            model_confidence=pred.confidence,
                            value_grade=self._calculate_grade(ev, edge),
                        ))
            else:
                # No live odds - estimate typical market
                # Typical anytime scorer odds based on probability
                estimated_odds = self._estimate_typical_odds(pred.prob_goal)
                implied_prob = self._american_to_probability(estimated_odds)

                edge = pred.prob_goal - implied_prob

                if edge >= min_edge:
                    ev, roi = self.calculate_expected_value(
                        pred.prob_goal, estimated_odds
                    )
                    kelly, half_kelly = self.calculate_kelly(
                        pred.prob_goal, estimated_odds
                    )

                    value_bets.append(ValueBet(
                        player_name=pred.player_name,
                        team=pred.team,
                        opponent=pred.opponent,
                        game_time=getattr(pred, 'game_time', 'TBD'),
                        market="anytime_scorer_est",
                        best_odds=estimated_odds,
                        sportsbook="Estimated Market",
                        model_probability=pred.prob_goal,
                        implied_probability=implied_prob,
                        edge=edge,
                        expected_value=ev,
                        expected_roi_pct=roi,
                        kelly_fraction=kelly,
                        kelly_half=half_kelly,
                        recommended_bet_pct=half_kelly * 100,
                        model_confidence=pred.confidence,
                        value_grade=self._calculate_grade(ev, edge),
                    ))

        # Sort by expected value
        value_bets.sort(key=lambda b: b.expected_value, reverse=True)

        # Calculate summary stats
        total_kelly = sum(b.kelly_half for b in value_bets)
        max_single = max((b.kelly_half for b in value_bets), default=0)

        a_plus = sum(1 for b in value_bets if b.value_grade == "A+")
        a = sum(1 for b in value_bets if b.value_grade == "A")
        b_plus = sum(1 for b in value_bets if b.value_grade == "B+")

        return ValueReport(
            generated_at=datetime.utcnow().isoformat(),
            games_analyzed=len(set(p.opponent for p in predictions)),
            odds_source="The Odds API" if live_odds else "Estimated",
            api_calls_remaining=remaining,
            value_bets=value_bets,
            total_positive_ev=len(value_bets),
            total_kelly_exposure=total_kelly * 100,
            max_single_bet_pct=max_single * 100,
            a_plus_bets=a_plus,
            a_bets=a,
            b_plus_bets=b_plus,
        )

    def _find_player_odds(
        self,
        player_name: str,
        odds_data: dict,
    ) -> list[OddsLine]:
        """Find odds for a specific player across all games."""
        result = []
        name_lower = player_name.lower()

        for game_key, odds_list in odds_data.items():
            for odds_line in odds_list:
                if name_lower in odds_line.player_name.lower():
                    result.append(odds_line)

        return result

    def _estimate_typical_odds(self, probability: float) -> int:
        """
        Estimate typical sportsbook odds based on probability.

        Sportsbooks typically add ~10% vig, so we adjust accordingly.
        """
        # Add vig - books typically offer worse odds than true probability
        vig_adjusted_prob = probability * 1.10  # 10% vig

        if vig_adjusted_prob >= 1:
            vig_adjusted_prob = 0.95

        # Convert to American odds
        if vig_adjusted_prob >= 0.5:
            odds = int(-100 * vig_adjusted_prob / (1 - vig_adjusted_prob))
        else:
            odds = int(100 * (1 - vig_adjusted_prob) / vig_adjusted_prob)

        return odds

    def _calculate_grade(self, ev: float, edge: float) -> str:
        """Calculate value grade based on EV and edge."""
        score = (ev * 100) + (edge * 50)  # Combined score

        if score >= 15:
            return "A+"
        elif score >= 10:
            return "A"
        elif score >= 6:
            return "B+"
        elif score >= 3:
            return "B"
        else:
            return "C"


@dataclass
class BankrollTracker:
    """
    Track betting performance over time.

    Stored in database for persistence across sessions.
    """
    starting_bankroll: float
    current_bankroll: float
    total_bets: int
    winning_bets: int
    total_wagered: float
    total_profit: float

    win_rate: float
    roi_pct: float
    units_profit: float  # In units (1 unit = 1% of bankroll)

    @property
    def losing_bets(self) -> int:
        return self.total_bets - self.winning_bets


async def calculate_bet_recommendation(
    db: AsyncSession,
    player_name: str,
    offered_odds: int,
    model_probability: float,
    bankroll: float = 1000,
) -> dict:
    """
    Calculate bet recommendation for a specific opportunity.

    Args:
        player_name: Player to bet on
        offered_odds: American odds from sportsbook
        model_probability: Our model's probability
        bankroll: Current bankroll size

    Returns:
        Complete recommendation with EV, Kelly, and suggested bet size
    """
    calc = OddsValueCalculator(db)

    implied_prob = calc._american_to_probability(offered_odds)
    edge = model_probability - implied_prob

    ev, roi = calc.calculate_expected_value(model_probability, offered_odds)
    kelly, half_kelly = calc.calculate_kelly(model_probability, offered_odds)

    # Calculate actual dollar amounts
    kelly_bet = bankroll * kelly
    half_kelly_bet = bankroll * half_kelly

    return {
        "player": player_name,
        "offered_odds": offered_odds,
        "model_probability": round(model_probability, 3),
        "implied_probability": round(implied_prob, 3),
        "edge": round(edge, 3),
        "has_value": edge > 0.03,
        "expected_value": round(ev, 3),
        "expected_roi_pct": round(roi, 1),
        "kelly_fraction": round(kelly, 4),
        "kelly_bet": round(kelly_bet, 2),
        "recommended_bet": round(half_kelly_bet, 2),
        "recommendation": _get_recommendation_text(ev, edge, half_kelly_bet),
    }


def _get_recommendation_text(ev: float, edge: float, bet_amount: float) -> str:
    """Generate human-readable recommendation."""
    if edge < 0:
        return f"NO VALUE - Market has you beat by {abs(edge)*100:.1f}%"
    elif edge < 0.03:
        return f"MARGINAL - Edge too small ({edge*100:.1f}%) to overcome variance"
    elif ev < 0.05:
        return f"SMALL VALUE - {ev*100:.1f}% EV, consider ${bet_amount:.2f}"
    elif ev < 0.10:
        return f"GOOD VALUE - {ev*100:.1f}% EV, bet ${bet_amount:.2f}"
    else:
        return f"STRONG VALUE - {ev*100:.1f}% EV, bet ${bet_amount:.2f}"


# -------------------------------------------------------------------------
# Olympic +EV Functions
# -------------------------------------------------------------------------

async def find_olympic_value_bets(
    db: AsyncSession,
    home_country: str,
    away_country: str,
    game_round: str = "group",
    min_edge: float = 0.03,
) -> ValueReport:
    """
    Find +EV betting opportunities for an Olympic hockey game.

    Compares Olympic model predictions to market odds (estimated if live unavailable).
    Olympics have less liquid markets = potentially larger edges.
    """
    from backend.src.ingestion.olympics import predict_olympic_game

    # Get predictions from Olympic model
    predictions = await predict_olympic_game(db, home_country, away_country, game_round)

    calc = OddsValueCalculator(db)

    # Note: The Odds API doesn't cover Olympic hockey - only NHL/AHL/European leagues
    # So we always use estimated odds for Olympics
    live_odds, remaining = {}, None

    value_bets = []

    # Process all players from the prediction
    all_players = predictions.get("home_players", []) + predictions.get("away_players", [])

    for player in all_players:
        prob_goal = player.get("prob_goal", 0)

        if prob_goal < 0.10:  # Skip very unlikely scorers
            continue

        # Look for player in live odds
        player_odds = calc._find_player_odds(player.get("player_name", ""), live_odds)

        if player_odds:
            # Have live odds - compare directly
            for odds_line in player_odds:
                edge = prob_goal - odds_line.implied_probability

                if edge >= min_edge:
                    ev, roi = calc.calculate_expected_value(prob_goal, odds_line.odds)
                    kelly, half_kelly = calc.calculate_kelly(prob_goal, odds_line.odds)

                    value_bets.append(ValueBet(
                        player_name=player.get("player_name", "Unknown"),
                        team=player.get("country_code", ""),
                        opponent=player.get("opponent_code", ""),
                        game_time=game_round,
                        market=odds_line.market,
                        best_odds=odds_line.odds,
                        sportsbook=odds_line.sportsbook,
                        model_probability=prob_goal,
                        implied_probability=odds_line.implied_probability,
                        edge=edge,
                        expected_value=ev,
                        expected_roi_pct=roi,
                        kelly_fraction=kelly,
                        kelly_half=half_kelly,
                        recommended_bet_pct=half_kelly * 100,
                        model_confidence=player.get("confidence", "medium"),
                        value_grade=calc._calculate_grade(ev, edge),
                    ))
        else:
            # Estimate typical Olympic market odds
            # Olympic markets have higher vig (~15%) due to less liquidity
            estimated_odds = _estimate_olympic_odds(prob_goal)
            implied_prob = calc._american_to_probability(estimated_odds)
            edge = prob_goal - implied_prob

            if edge >= min_edge:
                ev, roi = calc.calculate_expected_value(prob_goal, estimated_odds)
                kelly, half_kelly = calc.calculate_kelly(prob_goal, estimated_odds)

                value_bets.append(ValueBet(
                    player_name=player.get("player_name", "Unknown"),
                    team=player.get("country_code", ""),
                    opponent=player.get("opponent_code", ""),
                    game_time=game_round,
                    market="anytime_scorer_olympic_est",
                    best_odds=estimated_odds,
                    sportsbook="Estimated Olympic Market",
                    model_probability=prob_goal,
                    implied_probability=implied_prob,
                    edge=edge,
                    expected_value=ev,
                    expected_roi_pct=roi,
                    kelly_fraction=kelly,
                    kelly_half=half_kelly,
                    recommended_bet_pct=half_kelly * 100,
                    model_confidence=player.get("confidence", "medium"),
                    value_grade=calc._calculate_grade(ev, edge),
                ))

    # Sort by expected value
    value_bets.sort(key=lambda b: b.expected_value, reverse=True)

    # Calculate summary stats
    total_kelly = sum(b.kelly_half for b in value_bets)
    max_single = max((b.kelly_half for b in value_bets), default=0)

    a_plus = sum(1 for b in value_bets if b.value_grade == "A+")
    a = sum(1 for b in value_bets if b.value_grade == "A")
    b_plus = sum(1 for b in value_bets if b.value_grade == "B+")

    return ValueReport(
        generated_at=datetime.utcnow().isoformat(),
        games_analyzed=1,
        odds_source="Olympic Market" if live_odds else "Estimated",
        api_calls_remaining=remaining,
        value_bets=value_bets,
        total_positive_ev=len(value_bets),
        total_kelly_exposure=total_kelly * 100,
        max_single_bet_pct=max_single * 100,
        a_plus_bets=a_plus,
        a_bets=a,
        b_plus_bets=b_plus,
    )


def _estimate_olympic_odds(probability: float) -> int:
    """
    Estimate typical Olympic sportsbook odds.

    Olympic player props have ~15% vig due to lower liquidity.
    This is higher than NHL's ~10% vig.
    """
    # Higher vig for Olympic markets
    vig_adjusted_prob = probability * 1.15

    if vig_adjusted_prob >= 1:
        vig_adjusted_prob = 0.95

    # Convert to American odds
    if vig_adjusted_prob >= 0.5:
        odds = int(-100 * vig_adjusted_prob / (1 - vig_adjusted_prob))
    else:
        odds = int(100 * (1 - vig_adjusted_prob) / vig_adjusted_prob)

    return odds


async def get_olympic_value_report(
    db: AsyncSession,
) -> dict:
    """
    Get a complete +EV report for today's Olympic games.

    Scans all Olympic games scheduled for today and finds value bets.
    """
    from backend.src.ingestion.olympics import is_olympic_tournament_active, get_current_olympic_data

    if not is_olympic_tournament_active():
        return {
            "status": "inactive",
            "message": "Olympic tournament not currently active",
            "tournament_dates": "Feb 8-22, 2026",
        }

    olympic_data = get_current_olympic_data()
    today = date.today().isoformat()

    # Find today's games
    todays_games = [
        g for g in olympic_data.get("upcoming_games", [])
        if g.get("date") == today
    ]

    if not todays_games:
        return {
            "status": "no_games",
            "message": "No Olympic games scheduled for today",
            "date": today,
        }

    all_value_bets = []

    for game in todays_games:
        try:
            report = await find_olympic_value_bets(
                db,
                game["home"],
                game["away"],
                game.get("round", "group"),
            )
            all_value_bets.extend(report.value_bets)
        except Exception as e:
            logger.warning("olympic_value_scan_failed", game=game, error=str(e))

    # Sort all bets by EV
    all_value_bets.sort(key=lambda b: b.expected_value, reverse=True)

    return {
        "status": "active",
        "date": today,
        "games_scanned": len(todays_games),
        "value_bets_found": len(all_value_bets),
        "top_bets": [b.to_dict() for b in all_value_bets[:10]],
        "all_bets": [b.to_dict() for b in all_value_bets],
        "summary": {
            "a_plus": sum(1 for b in all_value_bets if b.value_grade == "A+"),
            "a": sum(1 for b in all_value_bets if b.value_grade == "A"),
            "b_plus": sum(1 for b in all_value_bets if b.value_grade == "B+"),
            "total_edge_opportunities": len(all_value_bets),
        },
    }
