"""
PowerplAI Prediction Engine

Calculates player scoring probabilities for upcoming games based on:
- Recent form (last N games performance)
- Head-to-head history (career performance vs opponent)
- Home/away splits
- Season baseline performance

Outputs probability estimates with confidence levels and explanations.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
import structlog

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


# Prediction model weights (tunable)
WEIGHTS = {
    "recent_form": 0.30,      # Last 5 games are most predictive
    "season_baseline": 0.25,  # Season average is stable baseline
    "h2h_history": 0.15,      # Historical performance vs opponent
    "home_away": 0.10,        # Home/away adjustment
    "goalie_matchup": 0.10,   # Opponent goalie quality
    "team_pace": 0.10,        # Game pace/environment
}

# Minimum sample sizes for reliable predictions
MIN_GAMES_RECENT = 3
MIN_GAMES_SEASON = 10
MIN_GAMES_H2H = 3

# League averages for normalization
LEAGUE_AVG_SAVE_PCT = 0.905
LEAGUE_AVG_GAA = 3.00
LEAGUE_AVG_GOALS_PER_GAME = 3.10  # Per team


@dataclass
class PlayerPrediction:
    """Prediction for a single player in a game."""
    player_name: str
    player_id: int
    team: str
    opponent: str
    is_home: bool

    # Probability estimates
    prob_goal: float        # Probability of scoring at least 1 goal
    prob_point: float       # Probability of at least 1 point
    prob_multi_point: float # Probability of 2+ points

    # Expected values
    expected_goals: float
    expected_assists: float
    expected_points: float
    expected_shots: float

    # Component scores (for transparency)
    recent_form_ppg: float
    season_avg_ppg: float
    h2h_ppg: float | None
    home_away_adjustment: float

    # Confidence and metadata
    confidence: str  # "high", "medium", "low"
    confidence_score: float  # 0-1
    games_analyzed: int
    factors: list[str]  # Key factors affecting prediction

    # New enhanced factors (with defaults)
    goalie_adjustment: float = 0.0      # Adjustment based on opponent goalie quality
    pace_adjustment: float = 0.0         # Adjustment based on expected game pace
    opponent_goalie: str | None = None   # Name of opponent's likely starter
    opponent_goalie_sv_pct: float | None = None  # Goalie's save percentage


@dataclass
class MatchupPrediction:
    """Prediction for a full game matchup."""
    game_id: int | None
    game_date: date
    home_team: str
    away_team: str
    venue: str | None
    start_time: str | None

    home_players: list[PlayerPrediction]
    away_players: list[PlayerPrediction]

    top_scorers: list[PlayerPrediction]  # Top 5 most likely scorers

    # Enhanced matchup context
    expected_total_goals: float | None = None
    home_expected_goals: float | None = None
    away_expected_goals: float | None = None
    home_goalie: dict | None = None
    away_goalie: dict | None = None
    pace_rating: str | None = None  # "high", "average", "low"


class PredictionEngine:
    """Engine for calculating player and game predictions."""

    async def get_matchup_prediction(
        self,
        db: AsyncSession,
        home_team: str,
        away_team: str,
        game_date: date | None = None,
        top_n: int = 10,
    ) -> MatchupPrediction:
        """
        Get predictions for a matchup between two teams.

        Args:
            home_team: Home team abbreviation (e.g., "TOR")
            away_team: Away team abbreviation (e.g., "BOS")
            game_date: Date of the game (defaults to today)
            top_n: Number of players per team to include

        Returns:
            MatchupPrediction with player predictions for both teams
        """
        if game_date is None:
            game_date = date.today()

        # Get game info if it exists
        game_info = await self._get_game_info(db, home_team, away_team, game_date)

        # Get matchup context (goalies, pace, etc.)
        matchup_context = await self._get_matchup_context(db, home_team, away_team)

        # Get predictions for home team players (playing against away goalie)
        home_players = await self._get_team_predictions(
            db, home_team, away_team, is_home=True, game_date=game_date,
            limit=top_n, matchup_context=matchup_context
        )

        # Get predictions for away team players (playing against home goalie)
        away_players = await self._get_team_predictions(
            db, away_team, home_team, is_home=False, game_date=game_date,
            limit=top_n, matchup_context=matchup_context
        )

        # Combine and rank by goal probability
        all_players = home_players + away_players
        top_scorers = sorted(all_players, key=lambda p: p.prob_goal, reverse=True)[:5]

        # Determine pace rating
        expected_total = matchup_context.get("expected_total_goals", 6.0)
        if expected_total >= 6.5:
            pace_rating = "high"
        elif expected_total <= 5.5:
            pace_rating = "low"
        else:
            pace_rating = "average"

        return MatchupPrediction(
            game_id=game_info.get("game_id") if game_info else None,
            game_date=game_date,
            home_team=home_team,
            away_team=away_team,
            venue=game_info.get("venue") if game_info else None,
            start_time=game_info.get("start_time") if game_info else None,
            home_players=home_players,
            away_players=away_players,
            top_scorers=top_scorers,
            expected_total_goals=matchup_context.get("expected_total_goals"),
            home_expected_goals=matchup_context.get("home_expected_goals"),
            away_expected_goals=matchup_context.get("away_expected_goals"),
            home_goalie=matchup_context.get("home_goalie"),
            away_goalie=matchup_context.get("away_goalie"),
            pace_rating=pace_rating,
        )

    async def get_player_prediction(
        self,
        db: AsyncSession,
        player_name: str,
        opponent: str,
        is_home: bool,
        game_date: date | None = None,
    ) -> PlayerPrediction | None:
        """
        Get prediction for a specific player against an opponent.

        Args:
            player_name: Player's name (fuzzy match)
            opponent: Opponent team abbreviation
            is_home: Whether the player's team is home
            game_date: Date of the game

        Returns:
            PlayerPrediction or None if player not found
        """
        if game_date is None:
            game_date = date.today()

        # Find player
        result = await db.execute(
            text("""
                SELECT p.id, p.name, s.team_abbrev
                FROM players p
                JOIN player_season_stats s ON p.id = s.player_id
                WHERE p.name ILIKE :name
                ORDER BY s.season DESC
                LIMIT 1
            """),
            {"name": f"%{player_name}%"}
        )
        row = result.fetchone()
        if not row:
            return None

        player_id, name, team = row.id, row.name, row.team_abbrev

        # Get matchup context for goalie/pace adjustments
        home_team = team if is_home else opponent
        away_team = opponent if is_home else team
        matchup_context = await self._get_matchup_context(db, home_team, away_team)

        return await self._calculate_player_prediction(
            db, player_id, name, team, opponent, is_home, game_date,
            matchup_context=matchup_context
        )

    async def _get_game_info(
        self,
        db: AsyncSession,
        home_team: str,
        away_team: str,
        game_date: date,
    ) -> dict | None:
        """Get game info from database if available."""
        result = await db.execute(
            text("""
                SELECT nhl_game_id, venue, start_time_utc
                FROM games
                WHERE home_team_abbrev = :home_team
                  AND away_team_abbrev = :away_team
                  AND game_date = :game_date
                LIMIT 1
            """),
            {"home_team": home_team, "away_team": away_team, "game_date": game_date}
        )
        row = result.fetchone()
        if row:
            return {
                "game_id": row.nhl_game_id,
                "venue": row.venue,
                "start_time": row.start_time_utc.isoformat() if row.start_time_utc else None,
            }
        return None

    async def _get_matchup_context(
        self,
        db: AsyncSession,
        home_team: str,
        away_team: str,
    ) -> dict:
        """
        Get enhanced matchup context including goalie and pace data.

        Returns dict with:
        - home_goalie: Starting goalie stats for home team
        - away_goalie: Starting goalie stats for away team
        - expected_total_goals: Expected total goals in game
        - home_expected_goals: Home team expected goals
        - away_expected_goals: Away team expected goals
        """
        try:
            from backend.src.ingestion.team_goalie_stats import get_matchup_context as fetch_context

            # Get current season
            season_result = await db.execute(
                text("SELECT MAX(season) FROM player_season_stats")
            )
            current_season = season_result.scalar() or "20252026"

            context = await fetch_context(db, home_team, away_team, current_season)
            return {
                "home_goalie": context.get("home_team", {}).get("goalie"),
                "away_goalie": context.get("away_team", {}).get("goalie"),
                "expected_total_goals": context.get("expected_total_goals", 6.0),
                "home_expected_goals": context.get("home_expected_goals", 3.0),
                "away_expected_goals": context.get("away_expected_goals", 3.0),
                "home_pace": context.get("home_team", {}).get("pace"),
                "away_pace": context.get("away_team", {}).get("pace"),
            }
        except Exception as e:
            logger.warning("matchup_context_unavailable", error=str(e))
            # Return defaults if data not available
            return {
                "home_goalie": None,
                "away_goalie": None,
                "expected_total_goals": 6.0,
                "home_expected_goals": 3.0,
                "away_expected_goals": 3.0,
            }

    async def _get_team_predictions(
        self,
        db: AsyncSession,
        team: str,
        opponent: str,
        is_home: bool,
        game_date: date,
        limit: int = 10,
        matchup_context: dict | None = None,
    ) -> list[PlayerPrediction]:
        """Get predictions for top players on a team."""
        # Get current season
        season_result = await db.execute(
            text("SELECT MAX(season) FROM player_season_stats")
        )
        current_season = season_result.scalar()

        # Get top players by points for this team
        result = await db.execute(
            text("""
                SELECT p.id, p.name, s.team_abbrev, s.points, s.games_played
                FROM players p
                JOIN player_season_stats s ON p.id = s.player_id
                WHERE s.team_abbrev = :team AND s.season = :season
                ORDER BY s.points DESC
                LIMIT :limit
            """),
            {"team": team, "season": current_season, "limit": limit}
        )

        predictions = []
        for row in result.fetchall():
            pred = await self._calculate_player_prediction(
                db, row.id, row.name, team, opponent, is_home, game_date,
                matchup_context=matchup_context
            )
            if pred:
                predictions.append(pred)

        return predictions

    async def _calculate_player_prediction(
        self,
        db: AsyncSession,
        player_id: int,
        player_name: str,
        team: str,
        opponent: str,
        is_home: bool,
        game_date: date,
        matchup_context: dict | None = None,
    ) -> PlayerPrediction:
        """
        Calculate prediction for a player using the enhanced weighted model.

        Model: P(score) = w1*recent + w2*season + w3*h2h + w4*home_away + w5*goalie + w6*pace
        """
        import math
        factors = []

        # 1. Get recent form (last 5 games)
        recent = await self._get_recent_form(db, player_id, game_date, n_games=5)
        recent_form_ppg = recent["ppg"] if recent["games"] >= MIN_GAMES_RECENT else None

        if recent_form_ppg is not None:
            if recent_form_ppg > recent.get("season_ppg", 0) * 1.2:
                factors.append(f"Hot streak: {recent_form_ppg:.2f} PPG in last {recent['games']} games")
            elif recent_form_ppg < recent.get("season_ppg", 0) * 0.8:
                factors.append(f"Cold streak: {recent_form_ppg:.2f} PPG in last {recent['games']} games")

        # 2. Get season baseline
        season = await self._get_season_stats(db, player_id)
        season_avg_ppg = season["ppg"] if season["games"] >= MIN_GAMES_SEASON else None

        # 3. Get head-to-head history
        h2h = await self._get_h2h_stats(db, player_id, opponent)
        h2h_ppg = h2h["ppg"] if h2h["games"] >= MIN_GAMES_H2H else None

        if h2h_ppg is not None:
            if h2h_ppg > (season_avg_ppg or 0) * 1.3:
                factors.append(f"Strong history vs {opponent}: {h2h_ppg:.2f} PPG in {h2h['games']} games")
            elif h2h_ppg < (season_avg_ppg or 0) * 0.7:
                factors.append(f"Struggles vs {opponent}: {h2h_ppg:.2f} PPG in {h2h['games']} games")

        # 4. Get home/away splits
        home_away = await self._get_home_away_stats(db, player_id, is_home)
        home_away_adjustment = home_away.get("adjustment", 0.0)

        if abs(home_away_adjustment) > 0.1:
            loc = "home" if is_home else "away"
            direction = "better" if home_away_adjustment > 0 else "worse"
            factors.append(f"Plays {direction} {loc}: {home_away_adjustment:+.2f} PPG adjustment")

        # 5. Calculate goalie matchup adjustment
        goalie_adjustment = 0.0
        opponent_goalie_name = None
        opponent_goalie_sv_pct = None

        if matchup_context:
            # If player is home, they face away goalie; if away, they face home goalie
            opp_goalie = matchup_context.get("away_goalie" if is_home else "home_goalie")
            if opp_goalie:
                opponent_goalie_name = opp_goalie.get("name")
                opponent_goalie_sv_pct = opp_goalie.get("save_pct")

                if opponent_goalie_sv_pct:
                    # Calculate adjustment: negative for good goalies, positive for weak goalies
                    # Each 0.01 difference in save % = ~0.05 PPG adjustment
                    sv_diff = LEAGUE_AVG_SAVE_PCT - opponent_goalie_sv_pct
                    goalie_adjustment = sv_diff * 5.0  # Scale factor

                    if sv_diff > 0.01:
                        factors.append(f"Favorable goalie matchup: {opponent_goalie_name} ({opponent_goalie_sv_pct:.3f} SV%)")
                    elif sv_diff < -0.01:
                        factors.append(f"Tough goalie matchup: {opponent_goalie_name} ({opponent_goalie_sv_pct:.3f} SV%)")

        # 6. Calculate pace adjustment
        pace_adjustment = 0.0

        if matchup_context:
            expected_total = matchup_context.get("expected_total_goals", 6.0)
            # Average game is ~6.2 total goals (2 teams * 3.1 per team)
            league_avg_total = LEAGUE_AVG_GOALS_PER_GAME * 2
            pace_diff = expected_total - league_avg_total

            # Each 0.5 goals above/below average = ~0.05 PPG adjustment
            pace_adjustment = pace_diff * 0.10

            if pace_diff > 0.5:
                factors.append(f"High-scoring game expected: {expected_total:.1f} total goals")
            elif pace_diff < -0.5:
                factors.append(f"Low-scoring game expected: {expected_total:.1f} total goals")

        # Calculate weighted prediction
        components = []
        weights_used = []

        if recent_form_ppg is not None:
            components.append(("recent", recent_form_ppg, WEIGHTS["recent_form"]))
            weights_used.append(WEIGHTS["recent_form"])

        if season_avg_ppg is not None:
            components.append(("season", season_avg_ppg, WEIGHTS["season_baseline"]))
            weights_used.append(WEIGHTS["season_baseline"])

        if h2h_ppg is not None:
            components.append(("h2h", h2h_ppg, WEIGHTS["h2h_history"]))
            weights_used.append(WEIGHTS["h2h_history"])

        # Normalize weights for the base components
        total_weight = sum(weights_used) if weights_used else 1.0

        # Calculate weighted expected points from base components
        expected_points = 0.0
        for name, value, weight in components:
            expected_points += value * (weight / total_weight)

        # Apply adjustments (these are additive modifiers)
        expected_points += home_away_adjustment * WEIGHTS["home_away"]
        expected_points += goalie_adjustment * WEIGHTS["goalie_matchup"]
        expected_points += pace_adjustment * WEIGHTS["team_pace"]

        # Ensure expected_points doesn't go negative
        expected_points = max(0.0, expected_points)

        # Calculate expected goals/assists (typically ~40% goals, 60% assists for forwards)
        goal_ratio = recent.get("goal_ratio", 0.4) if recent["games"] > 0 else 0.4
        expected_goals = expected_points * goal_ratio
        expected_assists = expected_points * (1 - goal_ratio)

        # Calculate probabilities using Poisson-like model
        # P(at least 1 goal) ≈ 1 - e^(-expected_goals)
        prob_goal = 1 - math.exp(-expected_goals) if expected_goals > 0 else 0.05
        prob_point = 1 - math.exp(-expected_points) if expected_points > 0 else 0.1
        prob_multi_point = 1 - math.exp(-expected_points) - expected_points * math.exp(-expected_points) if expected_points > 0 else 0.02

        # Calculate confidence
        games_analyzed = (recent.get("games", 0) + season.get("games", 0) + h2h.get("games", 0))
        confidence_score = min(1.0, games_analyzed / 50)  # Max confidence at 50+ games

        # Boost confidence if we have matchup context
        if matchup_context and matchup_context.get("home_goalie") and matchup_context.get("away_goalie"):
            confidence_score = min(1.0, confidence_score + 0.1)

        if confidence_score >= 0.7:
            confidence = "high"
        elif confidence_score >= 0.4:
            confidence = "medium"
        else:
            confidence = "low"
            factors.append("Limited data - prediction less reliable")

        return PlayerPrediction(
            player_name=player_name,
            player_id=player_id,
            team=team,
            opponent=opponent,
            is_home=is_home,
            prob_goal=round(prob_goal, 3),
            prob_point=round(prob_point, 3),
            prob_multi_point=round(prob_multi_point, 3),
            expected_goals=round(expected_goals, 2),
            expected_assists=round(expected_assists, 2),
            expected_points=round(expected_points, 2),
            expected_shots=round(recent.get("avg_shots", 2.5), 1),
            recent_form_ppg=round(recent_form_ppg, 2) if recent_form_ppg else 0,
            season_avg_ppg=round(season_avg_ppg, 2) if season_avg_ppg else 0,
            h2h_ppg=round(h2h_ppg, 2) if h2h_ppg else None,
            home_away_adjustment=round(home_away_adjustment, 2),
            goalie_adjustment=round(goalie_adjustment, 2),
            pace_adjustment=round(pace_adjustment, 2),
            opponent_goalie=opponent_goalie_name,
            opponent_goalie_sv_pct=round(opponent_goalie_sv_pct, 3) if opponent_goalie_sv_pct else None,
            confidence=confidence,
            confidence_score=round(confidence_score, 2),
            games_analyzed=games_analyzed,
            factors=factors,
        )

    async def _get_recent_form(
        self,
        db: AsyncSession,
        player_id: int,
        before_date: date,
        n_games: int = 5,
    ) -> dict:
        """Get player's recent form from game logs."""
        # Use subquery to get last N games, then aggregate
        result = await db.execute(
            text("""
                SELECT
                    COUNT(*) as games,
                    COALESCE(SUM(goals), 0) as goals,
                    COALESCE(SUM(assists), 0) as assists,
                    COALESCE(SUM(points), 0) as points,
                    COALESCE(AVG(shots), 0) as avg_shots
                FROM (
                    SELECT goals, assists, points, shots
                    FROM game_logs
                    WHERE player_id = :player_id
                      AND game_date < :before_date
                    ORDER BY game_date DESC
                    LIMIT :n_games
                ) recent_games
            """),
            {"player_id": player_id, "before_date": before_date, "n_games": n_games}
        )
        row = result.fetchone()

        if not row or row.games == 0:
            return {"games": 0, "ppg": 0, "gpg": 0, "avg_shots": 0, "goal_ratio": 0.4}

        games = row.games
        points = row.points
        goals = row.goals

        return {
            "games": games,
            "ppg": points / games if games > 0 else 0,
            "gpg": goals / games if games > 0 else 0,
            "avg_shots": float(row.avg_shots) if row.avg_shots else 2.5,
            "goal_ratio": goals / points if points > 0 else 0.4,
        }

    async def _get_season_stats(self, db: AsyncSession, player_id: int) -> dict:
        """Get player's season statistics."""
        result = await db.execute(
            text("""
                SELECT games_played, goals, assists, points, xg
                FROM player_season_stats
                WHERE player_id = :player_id
                ORDER BY season DESC
                LIMIT 1
            """),
            {"player_id": player_id}
        )
        row = result.fetchone()

        if not row or not row.games_played:
            return {"games": 0, "ppg": 0, "gpg": 0, "xg_per_game": 0}

        return {
            "games": row.games_played,
            "ppg": row.points / row.games_played if row.games_played > 0 else 0,
            "gpg": row.goals / row.games_played if row.games_played > 0 else 0,
            "xg_per_game": float(row.xg) / row.games_played if row.xg and row.games_played > 0 else 0,
        }

    async def _get_h2h_stats(
        self,
        db: AsyncSession,
        player_id: int,
        opponent: str,
    ) -> dict:
        """Get player's head-to-head stats against opponent."""
        result = await db.execute(
            text("""
                SELECT
                    COUNT(*) as games,
                    COALESCE(SUM(goals), 0) as goals,
                    COALESCE(SUM(assists), 0) as assists,
                    COALESCE(SUM(points), 0) as points
                FROM game_logs
                WHERE player_id = :player_id AND opponent = :opponent
            """),
            {"player_id": player_id, "opponent": opponent}
        )
        row = result.fetchone()

        if not row or row.games == 0:
            return {"games": 0, "ppg": 0, "gpg": 0}

        return {
            "games": row.games,
            "ppg": row.points / row.games if row.games > 0 else 0,
            "gpg": row.goals / row.games if row.games > 0 else 0,
        }

    async def _get_home_away_stats(
        self,
        db: AsyncSession,
        player_id: int,
        is_home: bool,
    ) -> dict:
        """Get player's home/away performance differential."""
        result = await db.execute(
            text("""
                SELECT
                    home_away,
                    COUNT(*) as games,
                    COALESCE(SUM(points), 0) as points
                FROM game_logs
                WHERE player_id = :player_id
                GROUP BY home_away
            """),
            {"player_id": player_id}
        )
        rows = result.fetchall()

        home_ppg = 0.0
        away_ppg = 0.0

        for row in rows:
            if row.home_away == "home" and row.games > 0:
                home_ppg = row.points / row.games
            elif row.home_away == "away" and row.games > 0:
                away_ppg = row.points / row.games

        # Calculate adjustment relative to average
        avg_ppg = (home_ppg + away_ppg) / 2 if (home_ppg + away_ppg) > 0 else 0
        if is_home:
            adjustment = home_ppg - avg_ppg
        else:
            adjustment = away_ppg - avg_ppg

        return {
            "home_ppg": home_ppg,
            "away_ppg": away_ppg,
            "adjustment": adjustment,
        }


# Singleton instance
prediction_engine = PredictionEngine()
