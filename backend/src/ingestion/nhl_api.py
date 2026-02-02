"""
NHL API Client for ingesting player and game data.

API Docs (community maintained): https://github.com/Zmalski/NHL-API-Reference
"""
import httpx
import structlog
from datetime import date
from typing import Any

from backend.src.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


class NHLAPIClient:
    """Client for the NHL Web API."""

    def __init__(self):
        self.base_url = settings.nhl_api_base
        self.stats_url = settings.nhl_stats_api_base
        self.client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)

    async def close(self):
        await self.client.aclose()

    async def _get(self, url: str) -> dict[str, Any]:
        """Make a GET request and return JSON response."""
        logger.debug("nhl_api_request", url=url)
        response = await self.client.get(url)
        response.raise_for_status()
        return response.json()

    # -------------------------------------------------------------------------
    # Player endpoints
    # -------------------------------------------------------------------------

    async def get_player(self, player_id: int) -> dict[str, Any]:
        """Get player landing page data (bio, current season stats, etc.)."""
        return await self._get(f"{self.base_url}/player/{player_id}/landing")

    async def get_player_game_log(
        self, player_id: int, season: str, game_type: int = 2
    ) -> dict[str, Any]:
        """
        Get player game log for a season.

        Args:
            player_id: NHL player ID
            season: Season in format "20232024"
            game_type: 2 = regular season, 3 = playoffs
        """
        return await self._get(
            f"{self.base_url}/player/{player_id}/game-log/{season}/{game_type}"
        )

    # -------------------------------------------------------------------------
    # Team endpoints
    # -------------------------------------------------------------------------

    async def get_standings(self, date_str: str | None = None) -> dict[str, Any]:
        """Get current standings or standings for a specific date."""
        if date_str:
            return await self._get(f"{self.base_url}/standings/{date_str}")
        return await self._get(f"{self.base_url}/standings/now")

    async def get_team_roster(self, team_abbrev: str, season: str) -> dict[str, Any]:
        """Get team roster for a season."""
        return await self._get(f"{self.base_url}/roster/{team_abbrev}/{season}")

    async def get_team_schedule(
        self, team_abbrev: str, month: str | None = None
    ) -> dict[str, Any]:
        """Get team schedule. Month format: "2024-01"."""
        if month:
            return await self._get(
                f"{self.base_url}/club-schedule/{team_abbrev}/month/{month}"
            )
        return await self._get(f"{self.base_url}/club-schedule/{team_abbrev}/now")

    # -------------------------------------------------------------------------
    # Game endpoints
    # -------------------------------------------------------------------------

    async def get_game_boxscore(self, game_id: int) -> dict[str, Any]:
        """Get boxscore for a specific game."""
        return await self._get(f"{self.base_url}/gamecenter/{game_id}/boxscore")

    async def get_game_play_by_play(self, game_id: int) -> dict[str, Any]:
        """Get play-by-play for a specific game."""
        return await self._get(f"{self.base_url}/gamecenter/{game_id}/play-by-play")

    async def get_schedule(self, date_str: str | None = None) -> dict[str, Any]:
        """Get league schedule for a date. Format: "2024-01-15"."""
        if date_str:
            return await self._get(f"{self.base_url}/schedule/{date_str}")
        return await self._get(f"{self.base_url}/schedule/now")

    # -------------------------------------------------------------------------
    # Stats API endpoints (different base URL)
    # -------------------------------------------------------------------------

    async def get_skater_stats_leaders(
        self, season: str, game_type: int = 2, limit: int = 50
    ) -> dict[str, Any]:
        """Get league leaders for skater stats."""
        return await self._get(
            f"{self.stats_url}/skater/summary"
            f"?cayenneExp=seasonId={season} and gameTypeId={game_type}"
            f"&limit={limit}&sort=points&direction=DESC"
        )

    async def get_goalie_stats(
        self, season: str, game_type: int = 2, limit: int = 50
    ) -> dict[str, Any]:
        """Get goalie stats for a season."""
        return await self._get(
            f"{self.stats_url}/goalie/summary"
            f"?cayenneExp=seasonId={season} and gameTypeId={game_type}"
            f"&limit={limit}&sort=wins&direction=DESC"
        )


# -------------------------------------------------------------------------
# Data transformation helpers
# -------------------------------------------------------------------------


def parse_player_from_landing(data: dict[str, Any]) -> dict[str, Any]:
    """Transform NHL API player landing response to our schema."""
    return {
        "nhl_id": data.get("playerId"),
        "name": f"{data.get('firstName', {}).get('default', '')} {data.get('lastName', {}).get('default', '')}".strip(),
        "position": data.get("position"),
        "team_abbrev": data.get("currentTeamAbbrev"),
        "birth_date": data.get("birthDate"),
        "shoots_catches": data.get("shootsCatches"),
        "height_inches": data.get("heightInInches"),
        "weight_lbs": data.get("weightInPounds"),
    }


def parse_game_log_entry(player_id: int, entry: dict[str, Any]) -> dict[str, Any]:
    """Transform a game log entry to our schema."""
    return {
        "player_id": player_id,
        "game_id": entry.get("gameId"),
        "game_date": entry.get("gameDate"),
        "opponent": entry.get("opponentAbbrev"),
        "home_away": "home" if entry.get("homeRoadFlag") == "H" else "away",
        "goals": entry.get("goals", 0),
        "assists": entry.get("assists", 0),
        "points": entry.get("points", 0),
        "shots": entry.get("shots", 0),
        "toi": _parse_toi(entry.get("toi", "0:00")),
        "plus_minus": entry.get("plusMinus", 0),
    }


def _parse_toi(toi_str: str) -> float:
    """Parse time on ice string (MM:SS) to decimal minutes."""
    try:
        parts = toi_str.split(":")
        minutes = int(parts[0])
        seconds = int(parts[1]) if len(parts) > 1 else 0
        return round(minutes + seconds / 60, 2)
    except (ValueError, IndexError):
        return 0.0
