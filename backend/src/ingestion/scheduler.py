"""
Data ingestion scheduler - handles multi-season ingestion and auto-updates.

Supports:
- Bulk historical ingestion (2007-present for advanced stats)
- Daily/startup auto-updates for current season
- Progress tracking and resumption
"""
import asyncio
from datetime import datetime, date
from pathlib import Path
import json
import structlog

from backend.src.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

# MoneyPuck data availability (xG tracking era)
MONEYPUCK_FIRST_SEASON = 2007  # 2007-08 season


def _season_start_year(today: date | None = None) -> int:
    """NHL seasons roll over in September (training camp / preseason)."""
    today = today or date.today()
    return today.year if today.month >= 9 else today.year - 1


# Import-time snapshot kept for default args in bulk-ingestion helpers.
# Runtime code should call get_current_season(), which re-evaluates the date
# so a server that stays up across the September rollover doesn't keep
# serving last season.
CURRENT_SEASON = _season_start_year()

# Progress file for tracking ingestion state
PROGRESS_FILE = Path("data/ingestion_progress.json")


def get_all_seasons(start_year: int = MONEYPUCK_FIRST_SEASON, end_year: int = CURRENT_SEASON) -> list[str]:
    """Get list of all seasons from start to end year."""
    return [str(year) for year in range(start_year, end_year + 1)]


def load_progress() -> dict:
    """Load ingestion progress from file."""
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text())
        except Exception as e:
            logger.warning("failed_to_load_progress", error=str(e))
    return {
        "completed_seasons": [],
        "last_update": None,
        "current_season_last_update": None,
    }


def save_progress(progress: dict):
    """Save ingestion progress to file."""
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2, default=str))


def mark_season_complete(season: str):
    """Mark a season as fully ingested."""
    progress = load_progress()
    if season not in progress["completed_seasons"]:
        progress["completed_seasons"].append(season)
    progress["last_update"] = datetime.now().isoformat()
    save_progress(progress)


def get_pending_seasons() -> list[str]:
    """Get seasons that haven't been ingested yet."""
    progress = load_progress()
    all_seasons = get_all_seasons()
    return [s for s in all_seasons if s not in progress["completed_seasons"]]


def should_update_current_season() -> bool:
    """Check if current season needs updating (daily updates during season)."""
    progress = load_progress()
    last_update = progress.get("current_season_last_update")

    if not last_update:
        return True

    last_update_date = datetime.fromisoformat(last_update).date()
    today = date.today()

    # Update if it's been more than a day
    return (today - last_update_date).days >= 1


def mark_current_season_updated():
    """Mark current season as updated today."""
    progress = load_progress()
    progress["current_season_last_update"] = datetime.now().isoformat()
    save_progress(progress)


class IngestionConfig:
    """Configuration for ingestion runs."""

    def __init__(
        self,
        seasons: list[str] | None = None,
        start_year: int = MONEYPUCK_FIRST_SEASON,
        end_year: int = CURRENT_SEASON,
        skip_completed: bool = True,
        include_rosters: bool = True,
        parallel_seasons: int = 3,  # How many seasons to process in parallel
        rate_limit_delay: float = 0.5,  # Delay between API calls
    ):
        self.seasons = seasons or get_all_seasons(start_year, end_year)
        self.skip_completed = skip_completed
        self.include_rosters = include_rosters
        self.parallel_seasons = parallel_seasons
        self.rate_limit_delay = rate_limit_delay

    def get_seasons_to_process(self) -> list[str]:
        """Get list of seasons to process based on config."""
        if self.skip_completed:
            progress = load_progress()
            return [s for s in self.seasons if s not in progress["completed_seasons"]]
        return self.seasons


# Export current season for use elsewhere
def get_current_season() -> str:
    """Get the current NHL season start year, e.g. "2026" for 2026-27."""
    return str(_season_start_year())


def get_current_season_code() -> str:
    """Current season in NHL API format, e.g. "20262027"."""
    year = _season_start_year()
    return f"{year}{year + 1}"


def get_season_label(season_code: str | None = None) -> str:
    """Human label for a season code, e.g. "20262027" -> "2026-27"."""
    code = season_code or get_current_season_code()
    return f"{code[:4]}-{code[6:8]}"
