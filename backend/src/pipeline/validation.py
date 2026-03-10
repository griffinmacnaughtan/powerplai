"""
Data validation module for PowerplAI pipelines.

Provides validation at multiple stages:
1. Source validation - Check raw API responses
2. Transform validation - Check transformed records
3. Load validation - Check database state after loading
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.pipeline.config import VALIDATION_THRESHOLDS

logger = structlog.get_logger()


class ValidationSeverity(Enum):
    """Severity levels for validation issues."""
    INFO = "info"           # Informational, no action needed
    WARNING = "warning"     # Unusual but acceptable
    ERROR = "error"         # Data quality issue, should investigate
    CRITICAL = "critical"   # Data integrity issue, blocks pipeline


@dataclass
class ValidationIssue:
    """Represents a single validation issue."""
    severity: ValidationSeverity
    check_name: str
    message: str
    record_id: str | None = None
    field_name: str | None = None
    expected_value: Any = None
    actual_value: Any = None
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "severity": self.severity.value,
            "check_name": self.check_name,
            "message": self.message,
            "record_id": self.record_id,
            "field_name": self.field_name,
            "expected": str(self.expected_value) if self.expected_value else None,
            "actual": str(self.actual_value) if self.actual_value else None,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ValidationResult:
    """Result of a validation run."""
    pipeline_name: str
    passed: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    records_validated: int = 0
    records_passed: int = 0
    duration_ms: float = 0
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def pass_rate(self) -> float:
        if self.records_validated == 0:
            return 1.0
        return self.records_passed / self.records_validated

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity in (ValidationSeverity.ERROR, ValidationSeverity.CRITICAL))

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == ValidationSeverity.WARNING)

    def to_dict(self) -> dict:
        return {
            "pipeline_name": self.pipeline_name,
            "passed": self.passed,
            "pass_rate": self.pass_rate,
            "records_validated": self.records_validated,
            "records_passed": self.records_passed,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp.isoformat(),
            "issues": [i.to_dict() for i in self.issues[:100]],  # Limit to first 100
        }


class DataValidator:
    """Validates data at various pipeline stages."""

    def __init__(self, thresholds: Any = None):
        self.thresholds = thresholds or VALIDATION_THRESHOLDS

    def validate_game_log(self, record: dict) -> list[ValidationIssue]:
        """Validate a single game log record."""
        issues = []
        record_id = f"{record.get('player_id')}_{record.get('game_id')}"

        # Goals validation
        goals = record.get("goals", 0)
        if goals is not None and goals > self.thresholds.max_goals_per_game:
            issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                check_name="goals_range",
                message=f"Goals ({goals}) exceeds maximum ({self.thresholds.max_goals_per_game})",
                record_id=record_id,
                field_name="goals",
                expected_value=f"<= {self.thresholds.max_goals_per_game}",
                actual_value=goals,
            ))

        # Assists validation
        assists = record.get("assists", 0)
        if assists is not None and assists > self.thresholds.max_assists_per_game:
            issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                check_name="assists_range",
                message=f"Assists ({assists}) exceeds maximum ({self.thresholds.max_assists_per_game})",
                record_id=record_id,
                field_name="assists",
                expected_value=f"<= {self.thresholds.max_assists_per_game}",
                actual_value=assists,
            ))

        # Shots validation
        shots = record.get("shots", 0)
        if shots is not None and shots > self.thresholds.max_shots_per_game:
            issues.append(ValidationIssue(
                severity=ValidationSeverity.WARNING,
                check_name="shots_range",
                message=f"Shots ({shots}) exceeds typical maximum ({self.thresholds.max_shots_per_game})",
                record_id=record_id,
                field_name="shots",
                expected_value=f"<= {self.thresholds.max_shots_per_game}",
                actual_value=shots,
            ))

        # TOI validation
        toi = record.get("toi", 0)
        if toi is not None and toi > self.thresholds.max_toi_per_game:
            issues.append(ValidationIssue(
                severity=ValidationSeverity.WARNING,
                check_name="toi_range",
                message=f"TOI ({toi}) exceeds typical maximum ({self.thresholds.max_toi_per_game})",
                record_id=record_id,
                field_name="toi",
                expected_value=f"<= {self.thresholds.max_toi_per_game}",
                actual_value=toi,
            ))

        # Points consistency (goals + assists = points)
        points = record.get("points", 0)
        if points is not None and goals is not None and assists is not None:
            expected_points = goals + assists
            if points != expected_points:
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    check_name="points_consistency",
                    message=f"Points ({points}) doesn't match goals + assists ({expected_points})",
                    record_id=record_id,
                    field_name="points",
                    expected_value=expected_points,
                    actual_value=points,
                ))

        # Required fields
        for field_name in ["player_id", "game_id", "game_date"]:
            if not record.get(field_name):
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.CRITICAL,
                    check_name="required_field",
                    message=f"Required field '{field_name}' is missing or null",
                    record_id=record_id,
                    field_name=field_name,
                ))

        return issues

    def validate_season_stats(self, record: dict) -> list[ValidationIssue]:
        """Validate player season stats record."""
        issues = []
        record_id = f"{record.get('player_id')}_{record.get('season')}"

        # Season totals validation
        goals = record.get("goals", 0) or 0
        if goals > self.thresholds.max_season_goals:
            issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                check_name="season_goals_range",
                message=f"Season goals ({goals}) exceeds historical max ({self.thresholds.max_season_goals})",
                record_id=record_id,
                field_name="goals",
                expected_value=f"<= {self.thresholds.max_season_goals}",
                actual_value=goals,
            ))

        assists = record.get("assists", 0) or 0
        if assists > self.thresholds.max_season_assists:
            issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                check_name="season_assists_range",
                message=f"Season assists ({assists}) exceeds historical max ({self.thresholds.max_season_assists})",
                record_id=record_id,
                field_name="assists",
                expected_value=f"<= {self.thresholds.max_season_assists}",
                actual_value=assists,
            ))

        # Games played validation
        games = record.get("games_played", 0) or 0
        if games > self.thresholds.max_games_per_season:
            issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                check_name="games_played_range",
                message=f"Games played ({games}) exceeds max ({self.thresholds.max_games_per_season})",
                record_id=record_id,
                field_name="games_played",
                expected_value=f"<= {self.thresholds.max_games_per_season}",
                actual_value=games,
            ))

        # Percentage validations
        corsi = record.get("corsi_for_pct")
        if corsi is not None and (corsi < 0 or corsi > 100):
            issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                check_name="corsi_range",
                message=f"Corsi For % ({corsi}) out of valid range (0-100)",
                record_id=record_id,
                field_name="corsi_for_pct",
                expected_value="0-100",
                actual_value=corsi,
            ))

        return issues

    def validate_game(self, record: dict) -> list[ValidationIssue]:
        """Validate a game schedule record."""
        issues = []
        record_id = str(record.get("nhl_game_id", "unknown"))

        # Required fields
        for field_name in ["home_team_abbrev", "away_team_abbrev", "game_date"]:
            if not record.get(field_name):
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.CRITICAL,
                    check_name="required_field",
                    message=f"Required field '{field_name}' is missing",
                    record_id=record_id,
                    field_name=field_name,
                ))

        # Score validation for completed games
        if record.get("is_completed"):
            home_score = record.get("home_score")
            away_score = record.get("away_score")
            if home_score is None or away_score is None:
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    check_name="completed_game_scores",
                    message="Completed game missing scores",
                    record_id=record_id,
                ))

        return issues


class DatabaseValidator:
    """Validates database state after data loading."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.thresholds = VALIDATION_THRESHOLDS

    async def validate_data_freshness(self) -> ValidationResult:
        """Check that data is up-to-date."""
        issues = []
        start_time = datetime.utcnow()

        # Check schedule freshness
        result = await self.db.execute(
            text("SELECT MAX(updated_at) FROM games WHERE game_date >= CURRENT_DATE")
        )
        last_schedule_update = result.scalar()
        if last_schedule_update:
            hours_old = (datetime.utcnow() - last_schedule_update).total_seconds() / 3600
            if hours_old > self.thresholds.max_schedule_age:
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    check_name="schedule_freshness",
                    message=f"Schedule data is {hours_old:.1f} hours old (threshold: {self.thresholds.max_schedule_age}h)",
                    expected_value=f"< {self.thresholds.max_schedule_age}h",
                    actual_value=f"{hours_old:.1f}h",
                ))

        # Check injuries freshness
        result = await self.db.execute(
            text("SELECT MAX(updated_at) FROM injuries WHERE is_active = true")
        )
        last_injuries_update = result.scalar()
        if last_injuries_update:
            hours_old = (datetime.utcnow() - last_injuries_update).total_seconds() / 3600
            if hours_old > self.thresholds.max_injuries_age:
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    check_name="injuries_freshness",
                    message=f"Injury data is {hours_old:.1f} hours old (threshold: {self.thresholds.max_injuries_age}h)",
                    expected_value=f"< {self.thresholds.max_injuries_age}h",
                    actual_value=f"{hours_old:.1f}h",
                ))

        duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
        passed = all(i.severity not in (ValidationSeverity.ERROR, ValidationSeverity.CRITICAL) for i in issues)

        return ValidationResult(
            pipeline_name="freshness_check",
            passed=passed,
            issues=issues,
            duration_ms=duration_ms,
        )

    async def validate_data_completeness(self) -> ValidationResult:
        """Check for missing or incomplete data."""
        issues = []
        start_time = datetime.utcnow()
        records_validated = 0
        records_passed = 0

        # Check players per team
        result = await self.db.execute(
            text("""
                SELECT team_abbrev, COUNT(*) as player_count
                FROM players
                WHERE team_abbrev IS NOT NULL
                GROUP BY team_abbrev
            """)
        )
        for row in result.fetchall():
            records_validated += 1
            if row.player_count < self.thresholds.min_players_per_team:
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    check_name="team_completeness",
                    message=f"Team {row.team_abbrev} has only {row.player_count} players",
                    record_id=row.team_abbrev,
                    expected_value=f">= {self.thresholds.min_players_per_team}",
                    actual_value=row.player_count,
                ))
            else:
                records_passed += 1

        # Check for games without logs (completed games should have logs)
        result = await self.db.execute(
            text("""
                SELECT g.nhl_game_id, g.game_date, g.home_team_abbrev, g.away_team_abbrev,
                       COUNT(gl.id) as log_count
                FROM games g
                LEFT JOIN game_logs gl ON gl.game_date = g.game_date
                WHERE g.is_completed = true
                  AND g.game_date > CURRENT_DATE - INTERVAL '14 days'
                GROUP BY g.nhl_game_id, g.game_date, g.home_team_abbrev, g.away_team_abbrev
                HAVING COUNT(gl.id) < 10
                LIMIT 10
            """)
        )
        for row in result.fetchall():
            issues.append(ValidationIssue(
                severity=ValidationSeverity.WARNING,
                check_name="game_logs_completeness",
                message=f"Completed game {row.home_team_abbrev} vs {row.away_team_abbrev} on {row.game_date} has only {row.log_count} game logs",
                record_id=str(row.nhl_game_id),
                expected_value=">= 10 logs",
                actual_value=row.log_count,
            ))

        duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
        passed = all(i.severity not in (ValidationSeverity.ERROR, ValidationSeverity.CRITICAL) for i in issues)

        return ValidationResult(
            pipeline_name="completeness_check",
            passed=passed,
            issues=issues,
            records_validated=records_validated,
            records_passed=records_passed,
            duration_ms=duration_ms,
        )

    async def validate_data_integrity(self) -> ValidationResult:
        """Check for data integrity issues (duplicates, orphans, etc.)."""
        issues = []
        start_time = datetime.utcnow()

        # Check for duplicate game logs
        result = await self.db.execute(
            text("""
                SELECT player_id, game_id, COUNT(*) as dupe_count
                FROM game_logs
                GROUP BY player_id, game_id
                HAVING COUNT(*) > 1
                LIMIT 10
            """)
        )
        for row in result.fetchall():
            issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                check_name="duplicate_game_logs",
                message=f"Duplicate game log found: player {row.player_id}, game {row.game_id}",
                record_id=f"{row.player_id}_{row.game_id}",
                actual_value=row.dupe_count,
            ))

        # Check for orphan season stats (no matching player)
        result = await self.db.execute(
            text("""
                SELECT pss.player_id, pss.season
                FROM player_season_stats pss
                LEFT JOIN players p ON pss.player_id = p.id
                WHERE p.id IS NULL
                LIMIT 10
            """)
        )
        for row in result.fetchall():
            issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                check_name="orphan_season_stats",
                message=f"Season stats for non-existent player: {row.player_id}",
                record_id=f"{row.player_id}_{row.season}",
            ))

        duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
        passed = not any(i.severity == ValidationSeverity.CRITICAL for i in issues)

        return ValidationResult(
            pipeline_name="integrity_check",
            passed=passed,
            issues=issues,
            duration_ms=duration_ms,
        )

    async def get_data_stats(self) -> dict:
        """Get current data statistics."""
        stats = {}

        # Total counts
        for table in ["players", "games", "game_logs", "player_season_stats", "injuries", "documents"]:
            try:
                result = await self.db.execute(text(f"SELECT COUNT(*) FROM {table}"))
                stats[f"{table}_count"] = result.scalar()
            except Exception:
                stats[f"{table}_count"] = 0

        # Game logs by season
        result = await self.db.execute(
            text("""
                SELECT season, COUNT(*) as count
                FROM game_logs
                WHERE season IS NOT NULL
                GROUP BY season
                ORDER BY season DESC
            """)
        )
        stats["game_logs_by_season"] = {row.season: row.count for row in result.fetchall()}

        # Date ranges
        result = await self.db.execute(
            text("SELECT MIN(game_date), MAX(game_date) FROM game_logs")
        )
        row = result.fetchone()
        if row:
            stats["game_logs_date_range"] = {
                "min": row[0].isoformat() if row[0] else None,
                "max": row[1].isoformat() if row[1] else None,
            }

        return stats


async def run_all_validations(db: AsyncSession) -> dict:
    """Run all validation checks and return summary."""
    validator = DatabaseValidator(db)

    results = {
        "freshness": await validator.validate_data_freshness(),
        "completeness": await validator.validate_data_completeness(),
        "integrity": await validator.validate_data_integrity(),
    }

    stats = await validator.get_data_stats()

    all_passed = all(r.passed for r in results.values())
    total_errors = sum(r.error_count for r in results.values())
    total_warnings = sum(r.warning_count for r in results.values())

    return {
        "overall_passed": all_passed,
        "total_errors": total_errors,
        "total_warnings": total_warnings,
        "data_stats": stats,
        "validations": {name: result.to_dict() for name, result in results.items()},
        "timestamp": datetime.utcnow().isoformat(),
    }
