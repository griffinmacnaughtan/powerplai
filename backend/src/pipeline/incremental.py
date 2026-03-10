"""
Incremental loading utilities for PowerplAI pipelines.

Provides efficient change detection and delta loading:
- Watermark-based incremental loads
- Change data capture patterns
- Deduplication strategies
"""
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

# Default progress file location
PROGRESS_FILE = Path("data/ingestion_progress.json")


@dataclass
class IncrementalState:
    """Tracks incremental loading state for a pipeline."""
    pipeline_name: str
    last_run: datetime | None = None
    last_watermark: str | None = None  # Could be date, ID, or other marker
    records_since_last: int = 0
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

    def to_dict(self) -> dict:
        return {
            "pipeline_name": self.pipeline_name,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "last_watermark": self.last_watermark,
            "records_since_last": self.records_since_last,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "IncrementalState":
        last_run = None
        if data.get("last_run"):
            try:
                last_run = datetime.fromisoformat(data["last_run"])
            except ValueError:
                pass

        return cls(
            pipeline_name=data.get("pipeline_name", "unknown"),
            last_run=last_run,
            last_watermark=data.get("last_watermark"),
            records_since_last=data.get("records_since_last", 0),
            metadata=data.get("metadata", {}),
        )


class ProgressTracker:
    """
    Tracks pipeline progress and incremental state.

    Uses a JSON file for persistence (simple, works without extra infrastructure).
    In production, consider using a database table instead.
    """

    def __init__(self, progress_file: Path = PROGRESS_FILE):
        self.progress_file = progress_file
        self._state: dict[str, IncrementalState] = {}
        self._load()

    def _load(self):
        """Load state from file."""
        if self.progress_file.exists():
            try:
                with open(self.progress_file) as f:
                    data = json.load(f)

                # Handle both old format and new format
                if "pipelines" in data:
                    for name, state_data in data["pipelines"].items():
                        self._state[name] = IncrementalState.from_dict(state_data)
                else:
                    # Legacy format - convert
                    self._state["game_logs"] = IncrementalState(
                        pipeline_name="game_logs",
                        last_watermark=data.get("last_game_log_date"),
                        metadata={
                            "completed_seasons": data.get("completed_seasons", []),
                        },
                    )
                    if data.get("last_update"):
                        try:
                            self._state["game_logs"].last_run = datetime.fromisoformat(
                                data["last_update"]
                            )
                        except ValueError:
                            pass

            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("progress_load_failed", error=str(e))
                self._state = {}

    def _save(self):
        """Save state to file."""
        self.progress_file.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "version": 2,
            "last_saved": datetime.utcnow().isoformat(),
            "pipelines": {
                name: state.to_dict() for name, state in self._state.items()
            },
            # Keep legacy fields for backwards compatibility
            "last_game_log_date": self._state.get("game_logs", IncrementalState("game_logs")).last_watermark,
            "completed_seasons": self._state.get("game_logs", IncrementalState("game_logs")).metadata.get("completed_seasons", []),
        }

        with open(self.progress_file, "w") as f:
            json.dump(data, f, indent=2)

    def get_state(self, pipeline_name: str) -> IncrementalState:
        """Get current state for a pipeline."""
        if pipeline_name not in self._state:
            self._state[pipeline_name] = IncrementalState(pipeline_name=pipeline_name)
        return self._state[pipeline_name]

    def update_state(
        self,
        pipeline_name: str,
        watermark: str | None = None,
        records: int = 0,
        metadata: dict | None = None,
    ):
        """Update state after a pipeline run."""
        state = self.get_state(pipeline_name)
        state.last_run = datetime.utcnow()

        if watermark is not None:
            state.last_watermark = watermark
        state.records_since_last = records

        if metadata:
            state.metadata.update(metadata)

        self._save()
        logger.info(
            "progress_updated",
            pipeline=pipeline_name,
            watermark=watermark,
            records=records,
        )

    def get_all_states(self) -> dict[str, IncrementalState]:
        """Get all pipeline states."""
        return self._state.copy()


# Singleton tracker instance
progress_tracker = ProgressTracker()


class IncrementalLoader:
    """Provides incremental loading strategies for different data types."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.tracker = progress_tracker

    async def get_games_to_update(self, days_back: int = 14) -> list[dict]:
        """
        Get games that need their logs updated.

        Returns games that are:
        1. Completed but have no/few game logs
        2. Recently completed (within days_back)
        """
        result = await self.db.execute(
            text("""
                SELECT g.nhl_game_id, g.game_date, g.home_team_abbrev, g.away_team_abbrev,
                       COUNT(DISTINCT gl.player_id) as log_count
                FROM games g
                LEFT JOIN game_logs gl ON gl.game_date = g.game_date
                WHERE g.is_completed = true
                  AND g.game_date >= CURRENT_DATE - :days_back
                GROUP BY g.nhl_game_id, g.game_date, g.home_team_abbrev, g.away_team_abbrev
                HAVING COUNT(DISTINCT gl.player_id) < 30
                ORDER BY g.game_date DESC
            """),
            {"days_back": days_back}
        )

        games = []
        for row in result.fetchall():
            games.append({
                "game_id": row.nhl_game_id,
                "game_date": row.game_date,
                "home_team": row.home_team_abbrev,
                "away_team": row.away_team_abbrev,
                "current_logs": row.log_count,
            })

        return games

    async def get_players_needing_update(
        self,
        season: str,
        last_update: datetime | None = None,
    ) -> list[dict]:
        """
        Get players whose game logs may be stale.

        If last_update is provided, only returns players who have played
        since then.
        """
        query = """
            SELECT DISTINCT p.id, p.nhl_id, p.name, s.team_abbrev,
                   MAX(gl.game_date) as last_game
            FROM players p
            JOIN player_season_stats s ON p.id = s.player_id
            LEFT JOIN game_logs gl ON p.id = gl.player_id
            WHERE s.season = :season
        """
        params = {"season": season}

        if last_update:
            query += " AND (gl.game_date IS NULL OR gl.game_date >= :last_update)"
            params["last_update"] = last_update.date()

        query += """
            GROUP BY p.id, p.nhl_id, p.name, s.team_abbrev
            ORDER BY last_game DESC NULLS FIRST
        """

        result = await self.db.execute(text(query), params)

        players = []
        for row in result.fetchall():
            players.append({
                "player_id": row.id,
                "nhl_id": row.nhl_id,
                "name": row.name,
                "team": row.team_abbrev,
                "last_game": row.last_game,
            })

        return players

    async def get_delta_stats(
        self,
        table: str,
        timestamp_column: str = "updated_at",
        since: datetime | None = None,
    ) -> dict:
        """Get statistics about records changed since a given time."""
        if since is None:
            state = self.tracker.get_state(table)
            since = state.last_run

        if since is None:
            # No previous run, get all records
            result = await self.db.execute(
                text(f"SELECT COUNT(*) FROM {table}")
            )
            total = result.scalar()
            return {
                "total_records": total,
                "new_records": total,
                "updated_records": 0,
                "is_full_load": True,
            }

        result = await self.db.execute(
            text(f"""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE created_at >= :since) as new,
                    COUNT(*) FILTER (WHERE {timestamp_column} >= :since AND created_at < :since) as updated
                FROM {table}
            """),
            {"since": since}
        )

        row = result.fetchone()
        return {
            "total_records": row.total,
            "new_records": row.new,
            "updated_records": row.updated,
            "is_full_load": False,
            "since": since.isoformat(),
        }

    async def identify_missing_data(self, season: str) -> dict:
        """Identify gaps in the data for a season."""
        # Check for games without logs
        games_without_logs = await self.db.execute(
            text("""
                SELECT g.nhl_game_id, g.game_date, g.home_team_abbrev, g.away_team_abbrev
                FROM games g
                LEFT JOIN game_logs gl ON gl.game_date = g.game_date
                WHERE g.season = :season
                  AND g.is_completed = true
                GROUP BY g.nhl_game_id, g.game_date, g.home_team_abbrev, g.away_team_abbrev
                HAVING COUNT(gl.id) = 0
                LIMIT 50
            """),
            {"season": season}
        )

        # Check for players without season stats
        players_without_stats = await self.db.execute(
            text("""
                SELECT p.id, p.name, p.team_abbrev
                FROM players p
                LEFT JOIN player_season_stats s ON p.id = s.player_id AND s.season = :season
                WHERE p.team_abbrev IS NOT NULL
                  AND s.id IS NULL
                LIMIT 50
            """),
            {"season": season}
        )

        # Check date coverage
        date_coverage = await self.db.execute(
            text("""
                SELECT
                    MIN(game_date) as first_game,
                    MAX(game_date) as last_game,
                    COUNT(DISTINCT game_date) as game_days
                FROM game_logs
                WHERE season = :season
            """),
            {"season": season}
        )

        date_row = date_coverage.fetchone()

        return {
            "season": season,
            "games_without_logs": [
                {
                    "game_id": row.nhl_game_id,
                    "date": row.game_date.isoformat(),
                    "matchup": f"{row.away_team_abbrev} @ {row.home_team_abbrev}",
                }
                for row in games_without_logs.fetchall()
            ],
            "players_without_stats": [
                {
                    "player_id": row.id,
                    "name": row.name,
                    "team": row.team_abbrev,
                }
                for row in players_without_stats.fetchall()
            ],
            "date_coverage": {
                "first_game": date_row.first_game.isoformat() if date_row.first_game else None,
                "last_game": date_row.last_game.isoformat() if date_row.last_game else None,
                "game_days": date_row.game_days,
            },
        }
