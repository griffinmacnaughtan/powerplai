from datetime import date, datetime
from sqlalchemy import Integer, String, Date, DateTime, Numeric, ForeignKey, Text, Index, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector

from backend.src.db.database import Base


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nhl_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[str | None] = mapped_column(String(10))
    team_abbrev: Mapped[str | None] = mapped_column(String(10))
    birth_date: Mapped[date | None] = mapped_column(Date)
    shoots_catches: Mapped[str | None] = mapped_column(String(1))
    height_inches: Mapped[int | None] = mapped_column(Integer)
    weight_lbs: Mapped[int | None] = mapped_column(Integer)

    # Contract/Salary info (in cents to avoid float precision issues)
    cap_hit_cents: Mapped[int | None] = mapped_column(Integer)  # Annual cap hit in cents
    contract_years: Mapped[int | None] = mapped_column(Integer)  # Years remaining
    contract_expiry: Mapped[int | None] = mapped_column(Integer)  # Expiry year (e.g., 2028)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    season_stats: Mapped[list["PlayerSeasonStats"]] = relationship(back_populates="player")
    game_logs: Mapped[list["GameLog"]] = relationship(back_populates="player")


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nhl_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    abbrev: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    conference: Mapped[str | None] = mapped_column(String(50))
    division: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class GoalieStats(Base):
    """Goalie season statistics for matchup analysis."""
    __tablename__ = "goalie_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    season: Mapped[str] = mapped_column(String(10), nullable=False)
    team_abbrev: Mapped[str | None] = mapped_column(String(10))

    # Core stats
    games_played: Mapped[int | None] = mapped_column(Integer, default=0)
    games_started: Mapped[int | None] = mapped_column(Integer, default=0)
    wins: Mapped[int | None] = mapped_column(Integer, default=0)
    losses: Mapped[int | None] = mapped_column(Integer, default=0)
    ot_losses: Mapped[int | None] = mapped_column(Integer, default=0)

    # Performance metrics
    save_pct: Mapped[float | None] = mapped_column(Numeric(5, 3))  # e.g., 0.915
    goals_against_avg: Mapped[float | None] = mapped_column(Numeric(4, 2))  # e.g., 2.85
    shutouts: Mapped[int | None] = mapped_column(Integer, default=0)

    # Workload
    shots_against: Mapped[int | None] = mapped_column(Integer)
    saves: Mapped[int | None] = mapped_column(Integer)
    time_on_ice: Mapped[int | None] = mapped_column(Integer)  # total minutes

    # Recent form (last 5 games)
    recent_save_pct: Mapped[float | None] = mapped_column(Numeric(5, 3))
    recent_gaa: Mapped[float | None] = mapped_column(Numeric(4, 2))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_goalie_stats_season", "season"),
        Index("idx_goalie_stats_player", "player_id"),
        Index("idx_goalie_stats_team", "team_abbrev"),
    )


class TeamSeasonStats(Base):
    """Team-level statistics for pace and strength analysis."""
    __tablename__ = "team_season_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_abbrev: Mapped[str] = mapped_column(String(10), nullable=False)
    season: Mapped[str] = mapped_column(String(10), nullable=False)

    # Record
    games_played: Mapped[int | None] = mapped_column(Integer, default=0)
    wins: Mapped[int | None] = mapped_column(Integer, default=0)
    losses: Mapped[int | None] = mapped_column(Integer, default=0)
    ot_losses: Mapped[int | None] = mapped_column(Integer, default=0)
    points: Mapped[int | None] = mapped_column(Integer, default=0)

    # Offensive metrics (pace indicators)
    goals_for: Mapped[int | None] = mapped_column(Integer, default=0)
    goals_for_per_game: Mapped[float | None] = mapped_column(Numeric(4, 2))
    shots_for_per_game: Mapped[float | None] = mapped_column(Numeric(4, 1))
    power_play_pct: Mapped[float | None] = mapped_column(Numeric(4, 1))  # e.g., 22.5

    # Defensive metrics
    goals_against: Mapped[int | None] = mapped_column(Integer, default=0)
    goals_against_per_game: Mapped[float | None] = mapped_column(Numeric(4, 2))
    shots_against_per_game: Mapped[float | None] = mapped_column(Numeric(4, 1))
    penalty_kill_pct: Mapped[float | None] = mapped_column(Numeric(4, 1))  # e.g., 81.2

    # Combined pace metric (total goals per game both teams)
    total_goals_per_game: Mapped[float | None] = mapped_column(Numeric(4, 2))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_team_stats_season", "season"),
        Index("idx_team_stats_team", "team_abbrev"),
    )


class Injury(Base):
    """Player injury tracking."""
    __tablename__ = "injuries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    team_abbrev: Mapped[str | None] = mapped_column(String(10))

    status: Mapped[str] = mapped_column(String(50))  # "Out", "Day-to-Day", "IR", "LTIR"
    injury_type: Mapped[str | None] = mapped_column(String(100))  # "Lower Body", "Upper Body", etc.
    description: Mapped[str | None] = mapped_column(Text)

    reported_date: Mapped[date | None] = mapped_column(Date)
    expected_return: Mapped[date | None] = mapped_column(Date)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # Currently injured?

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_injuries_player", "player_id"),
        Index("idx_injuries_active", "is_active"),
        Index("idx_injuries_team", "team_abbrev"),
    )


class ProbableGoalie(Base):
    """Probable starting goalies for upcoming games."""
    __tablename__ = "probable_goalies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int | None] = mapped_column(Integer)  # NHL game ID
    game_date: Mapped[date] = mapped_column(Date, nullable=False)
    team_abbrev: Mapped[str] = mapped_column(String(10), nullable=False)
    goalie_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))

    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str | None] = mapped_column(String(50))  # "NHL", "DailyFaceoff", etc.

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_probable_goalie_date", "game_date"),
        Index("idx_probable_goalie_team", "team_abbrev"),
    )


class Game(Base):
    """NHL game schedule and results."""
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nhl_game_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    season: Mapped[str] = mapped_column(String(10), nullable=False)  # e.g., "20252026"
    game_type: Mapped[int] = mapped_column(Integer, default=2)  # 2=regular, 3=playoffs
    game_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time_utc: Mapped[datetime | None] = mapped_column(DateTime)
    venue: Mapped[str | None] = mapped_column(String(255))

    home_team_abbrev: Mapped[str] = mapped_column(String(10), nullable=False)
    away_team_abbrev: Mapped[str] = mapped_column(String(10), nullable=False)
    home_score: Mapped[int | None] = mapped_column(Integer)  # null for future games
    away_score: Mapped[int | None] = mapped_column(Integer)

    game_state: Mapped[str] = mapped_column(String(20), default="FUT")  # FUT, LIVE, FINAL, OFF, etc.
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_games_date", "game_date"),
        Index("idx_games_season", "season"),
        Index("idx_games_teams", "home_team_abbrev", "away_team_abbrev"),
    )


class PlayerSeasonStats(Base):
    __tablename__ = "player_season_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    season: Mapped[str] = mapped_column(String(10), nullable=False)
    team_abbrev: Mapped[str | None] = mapped_column(String(10))
    games_played: Mapped[int | None] = mapped_column(Integer)
    goals: Mapped[int | None] = mapped_column(Integer)
    assists: Mapped[int | None] = mapped_column(Integer)
    points: Mapped[int | None] = mapped_column(Integer)
    plus_minus: Mapped[int | None] = mapped_column(Integer)
    pim: Mapped[int | None] = mapped_column(Integer)
    shots: Mapped[int | None] = mapped_column(Integer)
    shooting_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    toi_per_game: Mapped[float | None] = mapped_column(Numeric(6, 2))
    # Advanced stats
    xg: Mapped[float | None] = mapped_column(Numeric(6, 2))
    xg_per_60: Mapped[float | None] = mapped_column(Numeric(6, 3))
    corsi_for_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    fenwick_for_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    player: Mapped["Player"] = relationship(back_populates="season_stats")

    __table_args__ = (
        Index("idx_player_stats_season", "season"),
        Index("idx_player_stats_player", "player_id"),
    )


class GameLog(Base):
    """Player performance in individual games - crucial for predictions."""
    __tablename__ = "game_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    game_id: Mapped[int] = mapped_column(Integer, nullable=False)  # NHL game ID
    game_date: Mapped[date] = mapped_column(Date, nullable=False)
    season: Mapped[str | None] = mapped_column(String(10))  # e.g., "20252026"
    team_abbrev: Mapped[str | None] = mapped_column(String(10))  # player's team
    opponent: Mapped[str | None] = mapped_column(String(10))
    home_away: Mapped[str | None] = mapped_column(String(4))  # "home" or "away"

    # Basic stats
    goals: Mapped[int | None] = mapped_column(Integer, default=0)
    assists: Mapped[int | None] = mapped_column(Integer, default=0)
    points: Mapped[int | None] = mapped_column(Integer, default=0)
    shots: Mapped[int | None] = mapped_column(Integer, default=0)
    toi: Mapped[float | None] = mapped_column(Numeric(6, 2))  # decimal minutes
    plus_minus: Mapped[int | None] = mapped_column(Integer, default=0)
    pim: Mapped[int | None] = mapped_column(Integer, default=0)

    # Special teams & situational stats (key for predictions)
    powerplay_goals: Mapped[int | None] = mapped_column(Integer, default=0)
    powerplay_points: Mapped[int | None] = mapped_column(Integer, default=0)
    shorthanded_goals: Mapped[int | None] = mapped_column(Integer, default=0)
    shorthanded_points: Mapped[int | None] = mapped_column(Integer, default=0)
    game_winning_goals: Mapped[int | None] = mapped_column(Integer, default=0)
    overtime_goals: Mapped[int | None] = mapped_column(Integer, default=0)
    shifts: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    player: Mapped["Player"] = relationship(back_populates="game_logs")

    __table_args__ = (
        Index("idx_game_logs_date", "game_date"),
        Index("idx_game_logs_player_season", "player_id", "season"),
        Index("idx_game_logs_opponent", "player_id", "opponent"),  # For H2H lookups
        Index("idx_game_logs_game", "game_id"),
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str | None] = mapped_column(String(500))
    source: Mapped[str | None] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(String(1000))
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    embedding = mapped_column(Vector(384))  # all-MiniLM-L6-v2 dimension
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
