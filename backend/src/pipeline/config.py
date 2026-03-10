"""
Pipeline configuration for data ingestion.

All pipeline settings are defined here in a declarative way.
This makes it easy to modify schedules, sources, and transformations
without touching the pipeline code itself.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DataSource(Enum):
    """Available data sources."""
    NHL_API = "nhl_api"
    NHL_STATS_API = "nhl_stats_api"
    MONEYPUCK = "moneypuck"
    ESPN = "espn"
    PUCKPEDIA = "puckpedia"


class UpdateFrequency(Enum):
    """How often a pipeline should run."""
    REALTIME = "realtime"      # On every request / continuous
    HOURLY = "hourly"          # Every hour
    DAILY = "daily"            # Once per day
    WEEKLY = "weekly"          # Once per week
    ON_DEMAND = "on_demand"    # Manual trigger only
    ON_STARTUP = "on_startup"  # Run once when app starts


@dataclass
class RetryConfig:
    """Retry configuration for API calls."""
    max_attempts: int = 3
    initial_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    retry_on_status: list[int] = field(default_factory=lambda: [429, 500, 502, 503, 504])


@dataclass
class RateLimitConfig:
    """Rate limiting configuration."""
    requests_per_second: float = 2.0
    burst_size: int = 5
    cooldown_on_429: float = 60.0  # Seconds to wait after hitting rate limit


@dataclass
class PipelineConfig:
    """Configuration for a single data pipeline."""
    name: str
    source: DataSource
    frequency: UpdateFrequency
    enabled: bool = True
    retry: RetryConfig = field(default_factory=RetryConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    priority: int = 1  # Lower = higher priority
    depends_on: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


# Default pipeline configurations
PIPELINE_CONFIGS: dict[str, PipelineConfig] = {
    "schedule_sync": PipelineConfig(
        name="schedule_sync",
        source=DataSource.NHL_API,
        frequency=UpdateFrequency.HOURLY,
        priority=1,
        extra={"days_ahead": 7, "days_behind": 1},
    ),
    "game_logs": PipelineConfig(
        name="game_logs",
        source=DataSource.NHL_API,
        frequency=UpdateFrequency.DAILY,
        priority=2,
        depends_on=["schedule_sync"],
        extra={"catchup_days": 14, "batch_size": 50},
    ),
    "player_stats": PipelineConfig(
        name="player_stats",
        source=DataSource.NHL_API,
        frequency=UpdateFrequency.DAILY,
        priority=2,
        extra={"current_season_only": True},
    ),
    "advanced_stats": PipelineConfig(
        name="advanced_stats",
        source=DataSource.MONEYPUCK,
        frequency=UpdateFrequency.DAILY,
        priority=3,
        depends_on=["player_stats"],
        extra={"stats": ["xg", "corsi", "fenwick", "war"]},
    ),
    "team_goalie_stats": PipelineConfig(
        name="team_goalie_stats",
        source=DataSource.NHL_STATS_API,
        frequency=UpdateFrequency.DAILY,
        priority=2,
        extra={"include_goalies": True, "include_team_pace": True},
    ),
    "injuries": PipelineConfig(
        name="injuries",
        source=DataSource.ESPN,
        frequency=UpdateFrequency.HOURLY,
        priority=1,
        rate_limit=RateLimitConfig(requests_per_second=1.0),
    ),
    "salary_cap": PipelineConfig(
        name="salary_cap",
        source=DataSource.PUCKPEDIA,
        frequency=UpdateFrequency.WEEKLY,
        priority=4,
        rate_limit=RateLimitConfig(requests_per_second=0.5),
        extra={"scrape_all_teams": True},
    ),
    "roster_sync": PipelineConfig(
        name="roster_sync",
        source=DataSource.NHL_API,
        frequency=UpdateFrequency.DAILY,
        priority=1,
        extra={"track_trades": True},
    ),
}


# Validation thresholds for data quality checks
@dataclass
class ValidationThresholds:
    """Thresholds for data validation checks."""
    # Game log validation
    max_goals_per_game: int = 10
    max_assists_per_game: int = 10
    max_shots_per_game: int = 30
    max_toi_per_game: float = 40.0  # minutes
    min_games_for_season: int = 1
    max_games_per_season: int = 100  # including playoffs

    # Season stats validation
    max_season_goals: int = 100
    max_season_assists: int = 150
    max_season_points: int = 200

    # Completeness thresholds
    min_players_per_team: int = 15
    min_games_per_day: int = 0
    max_games_per_day: int = 16

    # Freshness thresholds (hours)
    max_schedule_age: float = 2.0
    max_injuries_age: float = 4.0
    max_stats_age: float = 24.0


VALIDATION_THRESHOLDS = ValidationThresholds()


# Dimension table definitions for data warehouse model
DIM_TABLES = {
    "dim_teams": {
        "primary_key": "team_id",
        "natural_key": "abbrev",
        "columns": ["team_id", "nhl_id", "name", "abbrev", "conference", "division", "arena", "founded_year"],
        "scd_type": 2,  # Slowly changing dimension type 2 (track history)
    },
    "dim_players": {
        "primary_key": "player_id",
        "natural_key": "nhl_id",
        "columns": ["player_id", "nhl_id", "name", "position", "birth_date", "nationality", "shoots_catches", "height_inches", "weight_lbs"],
        "scd_type": 2,
    },
    "dim_seasons": {
        "primary_key": "season_id",
        "natural_key": "season_code",
        "columns": ["season_id", "season_code", "start_date", "end_date", "is_current", "has_playoffs"],
        "scd_type": 1,  # Type 1 - overwrite
    },
    "dim_dates": {
        "primary_key": "date_id",
        "natural_key": "date",
        "columns": ["date_id", "date", "year", "month", "day", "day_of_week", "week_of_year", "is_weekend", "is_gameday"],
        "scd_type": 0,  # Static dimension
    },
}

FACT_TABLES = {
    "fact_game_logs": {
        "grain": "One row per player per game",
        "dimensions": ["dim_players", "dim_teams", "dim_dates", "dim_seasons"],
        "measures": ["goals", "assists", "points", "shots", "toi", "plus_minus", "pim",
                     "powerplay_goals", "powerplay_points", "shorthanded_goals", "game_winning_goals"],
        "degenerate_dims": ["game_id", "opponent", "home_away"],
    },
    "fact_games": {
        "grain": "One row per game",
        "dimensions": ["dim_teams", "dim_dates", "dim_seasons"],
        "measures": ["home_score", "away_score", "attendance", "game_duration_minutes"],
        "degenerate_dims": ["nhl_game_id", "game_state", "venue"],
    },
    "fact_player_season_stats": {
        "grain": "One row per player per season",
        "dimensions": ["dim_players", "dim_teams", "dim_seasons"],
        "measures": ["games_played", "goals", "assists", "points", "shots", "shooting_pct",
                     "toi_per_game", "xg", "xg_per_60", "corsi_for_pct", "fenwick_for_pct"],
        "degenerate_dims": [],
    },
    "fact_predictions": {
        "grain": "One row per player per predicted game",
        "dimensions": ["dim_players", "dim_teams", "dim_dates"],
        "measures": ["prob_goal", "prob_point", "prob_multi_point", "expected_goals",
                     "expected_assists", "expected_points", "confidence_score"],
        "degenerate_dims": ["prediction_id", "model_version"],
    },
    "fact_prediction_outcomes": {
        "grain": "One row per validated prediction",
        "dimensions": ["dim_players", "dim_teams", "dim_dates"],
        "measures": ["predicted_prob", "actual_outcome", "brier_contribution", "was_correct"],
        "degenerate_dims": ["prediction_id"],
    },
}
