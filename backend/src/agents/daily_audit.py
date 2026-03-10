"""
Daily Audit System - Automatic Prediction Logging & Validation

This module provides automatic, dynamic handling of predictions for:
- NHL regular season games
- Olympic tournament games (when active)

The system automatically detects what's active and logs/validates accordingly.

Usage:
- Called automatically on startup via startup_updates.py
- Can also be triggered manually via API endpoints

Flow:
1. On startup: Log predictions for today's games (NHL and/or Olympics)
2. On startup: Validate yesterday's predictions against actual outcomes
3. Generate rolling accuracy reports
"""
import structlog
from datetime import date, datetime, timedelta
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.db.database import async_session_maker

logger = structlog.get_logger()


@dataclass
class TodaysGames:
    """Summary of what games are happening today."""
    date: date
    nhl_games: list[dict]
    olympic_games: list[dict]
    is_olympics_active: bool
    total_games: int

    def to_dict(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "is_olympics_active": self.is_olympics_active,
            "nhl": {
                "game_count": len(self.nhl_games),
                "games": self.nhl_games,
            },
            "olympics": {
                "game_count": len(self.olympic_games),
                "games": self.olympic_games,
            },
            "total_games": self.total_games,
        }


async def get_todays_games_unified(db: AsyncSession) -> TodaysGames:
    """
    Get all games for today - both NHL and Olympics if active.

    This is the single source of truth for "what's playing today".
    """
    from backend.src.ingestion.olympics import (
        is_olympic_tournament_active,
        get_current_olympic_data,
    )
    from backend.src.ingestion.games import refresh_todays_schedule

    today = date.today()
    nhl_games = []
    olympic_games = []

    # Get NHL games
    try:
        await refresh_todays_schedule(db)

        result = await db.execute(
            text("""
                SELECT
                    nhl_game_id, game_date, start_time_utc,
                    home_team_abbrev, away_team_abbrev,
                    venue, game_state
                FROM games
                WHERE game_date = :today
                ORDER BY start_time_utc
            """),
            {"today": today}
        )

        for row in result.fetchall():
            nhl_games.append({
                "game_id": row.nhl_game_id,
                "type": "nhl",
                "home_team": row.home_team_abbrev,
                "away_team": row.away_team_abbrev,
                "venue": row.venue,
                "start_time": row.start_time_utc.isoformat() if row.start_time_utc else None,
                "state": row.game_state,
                "display": f"{row.away_team_abbrev} @ {row.home_team_abbrev}",
            })

    except Exception as e:
        logger.warning("nhl_games_fetch_failed", error=str(e))

    # Get Olympic games if tournament is active
    is_olympics = is_olympic_tournament_active()

    if is_olympics:
        try:
            olympic_data = get_current_olympic_data()

            # Check upcoming games for today
            for game in olympic_data.get("upcoming_games", []):
                game_date_str = game.get("date", "")
                if game_date_str == today.isoformat():
                    olympic_games.append({
                        "game_id": f"oly_{game['home']}_{game['away']}",
                        "type": "olympic",
                        "home_team": game["home"],
                        "away_team": game["away"],
                        "home_country": _get_country_name(game["home"]),
                        "away_country": _get_country_name(game["away"]),
                        "round": game.get("round", "group"),
                        "display": f"{game['away']} @ {game['home']} (Olympics)",
                    })

            # If no games found in upcoming, check if there should be games today
            # During group stage, there are typically games every day
            if not olympic_games and today >= date(2026, 2, 8) and today <= date(2026, 2, 22):
                # Fallback: generate expected games based on tournament schedule
                olympic_games = _get_expected_olympic_games_for_date(today)

        except Exception as e:
            logger.warning("olympic_games_fetch_failed", error=str(e))

    return TodaysGames(
        date=today,
        nhl_games=nhl_games,
        olympic_games=olympic_games,
        is_olympics_active=is_olympics,
        total_games=len(nhl_games) + len(olympic_games),
    )


def _get_country_name(code: str) -> str:
    """Get full country name from code."""
    names = {
        "CAN": "Canada", "USA": "United States", "SWE": "Sweden",
        "FIN": "Finland", "RUS": "Russia", "CZE": "Czechia",
        "SUI": "Switzerland", "GER": "Germany", "SVK": "Slovakia",
        "LAT": "Latvia", "DEN": "Denmark", "FRA": "France",
        "ITA": "Italy", "NOR": "Norway",
    }
    return names.get(code, code)


def _get_expected_olympic_games_for_date(target_date: date) -> list[dict]:
    """
    Generate expected Olympic games based on tournament structure.

    Milano Cortina 2026 schedule:
    - Feb 8-9: Group stage begins
    - Feb 10-14: Group stage continues
    - Feb 15: Group stage ends
    - Feb 17: Quarterfinals
    - Feb 19: Semifinals
    - Feb 21: Bronze medal game
    - Feb 22: Gold medal game
    """
    # Group stage matchups by day (simplified)
    schedule = {
        date(2026, 2, 8): [
            ("CAN", "FRA"), ("SUI", "CZE"), ("SWE", "FIN"), ("SVK", "ITA"), ("USA", "LAT"), ("GER", "DEN")
        ],
        date(2026, 2, 10): [
            ("FRA", "CZE"), ("CAN", "SUI"), ("FIN", "ITA"), ("SWE", "SVK"), ("LAT", "DEN"), ("USA", "GER")
        ],
        date(2026, 2, 12): [
            ("CZE", "SUI"), ("CAN", "FRA"), ("ITA", "SVK"), ("SWE", "FIN"), ("DEN", "GER"), ("USA", "LAT")
        ],
        date(2026, 2, 13): [
            ("CAN", "CZE"), ("SUI", "FRA"), ("SWE", "ITA"), ("SVK", "FIN"), ("USA", "DEN"), ("GER", "LAT")
        ],
        date(2026, 2, 15): [
            ("CZE", "FRA"), ("SUI", "CAN"), ("FIN", "SVK"), ("ITA", "SWE"), ("DEN", "USA"), ("LAT", "GER")
        ],
        date(2026, 2, 17): [
            ("QF1", "QF1"), ("QF2", "QF2"), ("QF3", "QF3"), ("QF4", "QF4")
        ],
        date(2026, 2, 19): [
            ("SF1", "SF1"), ("SF2", "SF2")
        ],
        date(2026, 2, 21): [
            ("BRONZE", "BRONZE")
        ],
        date(2026, 2, 22): [
            ("GOLD", "GOLD")
        ],
    }

    games = []
    if target_date in schedule:
        for home, away in schedule[target_date]:
            round_type = "group"
            if target_date == date(2026, 2, 17):
                round_type = "quarterfinal"
            elif target_date == date(2026, 2, 19):
                round_type = "semifinal"
            elif target_date == date(2026, 2, 21):
                round_type = "bronze"
            elif target_date == date(2026, 2, 22):
                round_type = "gold"

            games.append({
                "game_id": f"oly_{home}_{away}_{target_date.isoformat()}",
                "type": "olympic",
                "home_team": home,
                "away_team": away,
                "home_country": _get_country_name(home),
                "away_country": _get_country_name(away),
                "round": round_type,
                "display": f"{away} @ {home} (Olympics - {round_type.title()})",
            })

    return games


async def log_todays_predictions(db: AsyncSession) -> dict:
    """
    Log predictions for all of today's games (NHL + Olympics).

    This is the main entry point for automatic daily logging.
    Should be called on startup BEFORE games begin.
    """
    from backend.src.agents.prediction_audit import (
        log_matchup_predictions,
        log_olympic_predictions,
        create_audit_table,
    )
    from backend.src.agents.predictions import prediction_engine
    from backend.src.ingestion.olympics import predict_olympic_game

    # Ensure audit table exists
    try:
        await create_audit_table(db)
    except Exception:
        pass  # Table might already exist

    results = {
        "date": date.today().isoformat(),
        "nhl": {"games": 0, "predictions": 0},
        "olympics": {"games": 0, "predictions": 0},
        "errors": [],
    }

    # Get today's games
    games = await get_todays_games_unified(db)

    # Log NHL predictions
    for game in games.nhl_games:
        try:
            matchup = await prediction_engine.get_matchup_prediction(
                db,
                game["home_team"],
                game["away_team"],
                date.today(),
                top_n=12,  # Top 12 players per team
            )

            count = await log_matchup_predictions(db, matchup, game_type="nhl")
            results["nhl"]["predictions"] += count
            results["nhl"]["games"] += 1

            logger.info(
                "nhl_predictions_logged",
                game=game["display"],
                count=count,
            )

        except Exception as e:
            results["errors"].append(f"NHL {game['display']}: {str(e)}")
            logger.warning("nhl_prediction_log_failed", game=game["display"], error=str(e))

    # Log Olympic predictions
    for game in games.olympic_games:
        try:
            prediction = await predict_olympic_game(
                db,
                game["home_team"],
                game["away_team"],
                game.get("round", "group"),
            )

            # Log each player prediction
            from backend.src.agents.prediction_audit import log_prediction
            import json

            all_players = prediction.get("home_players", []) + prediction.get("away_players", [])

            for pred in all_players:
                await log_prediction(
                    db,
                    game_date=date.today(),
                    player_name=pred.get("player_name", "Unknown"),
                    team=pred.get("country_code", game["home_team"]),
                    opponent=pred.get("opponent_code", game["away_team"]),
                    prob_goal=pred.get("prob_goal", 0),
                    prob_point=pred.get("prob_point", 0),
                    is_home=pred.get("country_code") == game["home_team"],
                    game_type="olympic",
                    game_id=game["game_id"],
                    expected_goals=pred.get("expected_goals"),
                    expected_points=pred.get("expected_points"),
                    confidence=pred.get("confidence", "medium"),
                    confidence_score=pred.get("confidence_score", 0.5),
                    model_version="olympic_v1",
                    factors=pred.get("factors", []),
                )
                results["olympics"]["predictions"] += 1

            results["olympics"]["games"] += 1

            logger.info(
                "olympic_predictions_logged",
                game=game["display"],
                count=len(all_players),
            )

        except Exception as e:
            results["errors"].append(f"Olympic {game['display']}: {str(e)}")
            logger.warning("olympic_prediction_log_failed", game=game["display"], error=str(e))

    results["total_predictions"] = results["nhl"]["predictions"] + results["olympics"]["predictions"]
    results["total_games"] = results["nhl"]["games"] + results["olympics"]["games"]

    logger.info("daily_predictions_logged", **results)
    return results


async def validate_yesterdays_predictions(db: AsyncSession) -> dict:
    """
    Validate predictions from yesterday against actual outcomes.

    This fetches actual game results and compares to logged predictions.
    """
    from backend.src.agents.prediction_audit import record_outcome

    yesterday = date.today() - timedelta(days=1)

    results = {
        "date": yesterday.isoformat(),
        "nhl": {"validated": 0, "not_found": 0},
        "olympics": {"validated": 0, "not_found": 0},
        "errors": [],
    }

    # Get unvalidated predictions from yesterday
    unvalidated = await db.execute(
        text("""
            SELECT id, game_type, player_name, team, opponent, player_id
            FROM prediction_audit
            WHERE game_date = :yesterday
              AND validated_at IS NULL
        """),
        {"yesterday": yesterday}
    )

    predictions = unvalidated.fetchall()

    for pred in predictions:
        try:
            if pred.game_type == "nhl":
                # Look up in game_logs table
                actual = await db.execute(
                    text("""
                        SELECT gl.goals, gl.assists
                        FROM game_logs gl
                        JOIN players p ON gl.player_id = p.id
                        WHERE gl.game_date = :game_date
                          AND p.name ILIKE :name
                        LIMIT 1
                    """),
                    {"game_date": yesterday, "name": f"%{pred.player_name}%"}
                )
                row = actual.fetchone()

                if row:
                    await record_outcome(
                        db, yesterday, pred.player_name, pred.team, pred.opponent,
                        row.goals or 0, row.assists or 0
                    )
                    results["nhl"]["validated"] += 1
                else:
                    results["nhl"]["not_found"] += 1

            elif pred.game_type == "olympic":
                # Olympic validation would need Olympic box scores
                # For now, mark as not found (would implement with actual data source)
                results["olympics"]["not_found"] += 1

        except Exception as e:
            results["errors"].append(f"{pred.player_name}: {str(e)}")

    logger.info("yesterday_validated", **results)
    return results


async def run_daily_audit(db: AsyncSession = None) -> dict:
    """
    Run the complete daily audit cycle.

    This is the main entry point called from startup_updates.

    1. Validate yesterday's predictions
    2. Log today's predictions
    """
    results = {
        "validation": None,
        "logging": None,
        "timestamp": datetime.utcnow().isoformat(),
    }

    if db is None:
        async with async_session_maker() as db:
            results["validation"] = await validate_yesterdays_predictions(db)
            results["logging"] = await log_todays_predictions(db)
    else:
        results["validation"] = await validate_yesterdays_predictions(db)
        results["logging"] = await log_todays_predictions(db)

    return results


async def get_accuracy_summary(db: AsyncSession, days: int = 7) -> dict:
    """
    Get a quick accuracy summary for the last N days.

    This provides a snapshot for the API without generating a full report.
    """
    start_date = date.today() - timedelta(days=days)

    result = await db.execute(
        text("""
            SELECT
                game_type,
                COUNT(*) as total,
                COUNT(validated_at) as validated,
                COUNT(*) FILTER (WHERE goal_hit = TRUE) as goal_hits,
                COUNT(*) FILTER (WHERE point_hit = TRUE) as point_hits,
                AVG(prob_goal) FILTER (WHERE validated_at IS NOT NULL) as avg_prob_goal,
                AVG(CASE WHEN goal_hit THEN 1.0 ELSE 0.0 END) as actual_goal_rate
            FROM prediction_audit
            WHERE game_date >= :start_date
            GROUP BY game_type
        """),
        {"start_date": start_date}
    )

    summary = {
        "period": f"Last {days} days",
        "start_date": start_date.isoformat(),
        "end_date": date.today().isoformat(),
        "by_type": {},
    }

    for row in result.fetchall():
        hit_rate = row.goal_hits / row.validated if row.validated > 0 else 0
        expected_rate = row.avg_prob_goal or 0

        summary["by_type"][row.game_type] = {
            "total_predictions": row.total,
            "validated": row.validated,
            "goal_hits": row.goal_hits,
            "point_hits": row.point_hits,
            "goal_hit_rate": f"{hit_rate:.1%}",
            "avg_predicted_probability": f"{expected_rate:.1%}",
            "calibration": "good" if abs(hit_rate - expected_rate) < 0.05 else "needs_review",
        }

    return summary
