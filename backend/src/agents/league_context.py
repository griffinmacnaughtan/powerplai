"""
League-wide context for the prediction engine.

Replaces the hardcoded league-average constants
(LEAGUE_AVG_SAVE_PCT=0.905, LEAGUE_AVG_GAA=3.00,
LEAGUE_AVG_GOALS_PER_GAME=3.10) with values computed from the current
season's data. These drift season-over-season (rule changes, league
expansion, coaching trends), so locking them in code biases every
prediction we surface.

Design
------
- All averages are computed once per process and cached for `TTL_SECONDS`.
  Refresh is lazy on first use after expiry; we don't add a background
  job because the values change slowly.
- Each value has a hardcoded fallback. If the DB query returns nothing
  (e.g. table empty at startup, query errors), we fall back rather than
  poison every prediction with NaN.
- The computed numbers go through sanity bounds (sv% in [0.85, 0.95],
  GAA in [2.0, 4.5], total goals/game in [4.5, 8.0]) to defend against
  freak early-season noise from very small samples.

Public API
----------
- `await get_league_averages(db)` -> LeagueAverages dataclass
- `await refresh_league_averages(db)` -> forces a refresh
- `LeagueAverages.save_pct`, `.gaa`, `.goals_per_game`
"""
from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from typing import Optional

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


# Hardcoded fallbacks. Used when (a) the DB is empty, (b) the query fails,
# or (c) the computed value is out of plausible bounds. These match the
# historical constants so we never regress vs. the old behaviour.
FALLBACK_SAVE_PCT = 0.905
FALLBACK_GAA = 3.00
FALLBACK_GOALS_PER_GAME = 3.10  # per team

# Plausibility bounds. Defends against early-season tiny samples.
BOUNDS_SAVE_PCT = (0.85, 0.95)
BOUNDS_GAA = (2.0, 4.5)
BOUNDS_GOALS_PER_GAME = (2.0, 5.0)

# Refresh every 6 hours. Stats tables update at most once per day in our
# pipeline, so this is plenty fresh without hammering the DB.
TTL_SECONDS = 6 * 3600


@dataclass
class LeagueAverages:
    """Snapshot of league-average constants for the active season."""
    save_pct: float
    gaa: float
    goals_per_game: float  # per team per game
    season: Optional[str]
    sample_size_goalies: int
    sample_size_teams: int
    computed_at: float
    is_fallback: bool  # True if any value came from the hardcoded fallback

    def to_dict(self) -> dict:
        d = asdict(self)
        d["age_seconds"] = round(time.time() - self.computed_at, 1)
        return d


# Module-level cache (per worker).
_cache: Optional[LeagueAverages] = None


def _clamp(value: float, bounds: tuple[float, float]) -> tuple[float, bool]:
    """Return (clamped_value, was_clamped)."""
    lo, hi = bounds
    if value < lo:
        return lo, True
    if value > hi:
        return hi, True
    return value, False


async def _compute(db: AsyncSession) -> LeagueAverages:
    """Compute league averages from the current season's data."""
    save_pct: float = FALLBACK_SAVE_PCT
    gaa: float = FALLBACK_GAA
    goals_pg: float = FALLBACK_GOALS_PER_GAME
    is_fallback = False
    season: Optional[str] = None
    n_goalies = 0
    n_teams = 0

    # Pick the most recent season we have any goalie data for. Fall back
    # to the most recent team_season_stats season if no goalies are loaded.
    try:
        sres = await db.execute(
            text("""
                SELECT MAX(season) AS s FROM goalie_stats
                WHERE games_played IS NOT NULL AND games_played > 0
            """)
        )
        srow = sres.fetchone()
        season = srow.s if srow and srow.s else None
        if not season:
            sres = await db.execute(text("SELECT MAX(season) AS s FROM team_season_stats"))
            srow = sres.fetchone()
            season = srow.s if srow and srow.s else None
    except Exception as e:
        logger.warning("league_context_season_lookup_failed", error=str(e))

    if not season:
        return LeagueAverages(
            save_pct=FALLBACK_SAVE_PCT,
            gaa=FALLBACK_GAA,
            goals_per_game=FALLBACK_GOALS_PER_GAME,
            season=None,
            sample_size_goalies=0,
            sample_size_teams=0,
            computed_at=time.time(),
            is_fallback=True,
        )

    # Goalie averages: weight by games_played so a 3-GP backup doesn't
    # equal-weight an 60-GP starter. Require at least 8 GP to enter the
    # average so call-ups don't pollute it.
    try:
        gres = await db.execute(
            text("""
                SELECT
                    SUM(games_played * save_pct) / NULLIF(SUM(games_played), 0) AS wsv,
                    SUM(games_played * goals_against_avg) / NULLIF(SUM(games_played), 0) AS wgaa,
                    COUNT(*) AS n
                FROM goalie_stats
                WHERE season = :season
                  AND games_played >= 8
                  AND save_pct IS NOT NULL
                  AND goals_against_avg IS NOT NULL
            """),
            {"season": season},
        )
        grow = gres.fetchone()
        if grow and grow.n and grow.wsv is not None and grow.wgaa is not None:
            n_goalies = int(grow.n)
            sv_candidate, sv_clamped = _clamp(float(grow.wsv), BOUNDS_SAVE_PCT)
            gaa_candidate, gaa_clamped = _clamp(float(grow.wgaa), BOUNDS_GAA)
            save_pct = sv_candidate
            gaa = gaa_candidate
            if sv_clamped or gaa_clamped:
                logger.warning(
                    "league_context_goalie_clamped",
                    season=season, raw_sv=float(grow.wsv), raw_gaa=float(grow.wgaa),
                )
        else:
            is_fallback = True
    except Exception as e:
        logger.warning("league_context_goalie_query_failed", error=str(e))
        is_fallback = True

    # Team scoring pace: average of goals_for_per_game across teams. NHL
    # is 32 teams; if we have >= 20 with a value, we trust the average.
    try:
        tres = await db.execute(
            text("""
                SELECT
                    AVG(goals_for_per_game) AS gpg,
                    COUNT(*) AS n
                FROM team_season_stats
                WHERE season = :season
                  AND goals_for_per_game IS NOT NULL
                  AND games_played > 0
            """),
            {"season": season},
        )
        trow = tres.fetchone()
        if trow and trow.n and trow.gpg is not None and int(trow.n) >= 20:
            n_teams = int(trow.n)
            gpg_candidate, gpg_clamped = _clamp(float(trow.gpg), BOUNDS_GOALS_PER_GAME)
            goals_pg = gpg_candidate
            if gpg_clamped:
                logger.warning(
                    "league_context_team_gpg_clamped",
                    season=season, raw_gpg=float(trow.gpg),
                )
        else:
            # Not enough teams - keep fallback
            is_fallback = True
    except Exception as e:
        logger.warning("league_context_team_query_failed", error=str(e))
        is_fallback = True

    return LeagueAverages(
        save_pct=round(save_pct, 4),
        gaa=round(gaa, 3),
        goals_per_game=round(goals_pg, 3),
        season=season,
        sample_size_goalies=n_goalies,
        sample_size_teams=n_teams,
        computed_at=time.time(),
        is_fallback=is_fallback,
    )


async def get_league_averages(db: AsyncSession) -> LeagueAverages:
    """
    Return cached league averages, refreshing if older than TTL_SECONDS.

    Lazy: only triggers the DB query on first call after expiry. Threadsafe
    enough for our purposes - racing workers will just compute twice in
    parallel on cold start, no harm done.
    """
    global _cache
    now = time.time()
    if _cache is not None and (now - _cache.computed_at) < TTL_SECONDS:
        return _cache
    _cache = await _compute(db)
    logger.info(
        "league_context_refreshed",
        season=_cache.season,
        save_pct=_cache.save_pct,
        gaa=_cache.gaa,
        goals_per_game=_cache.goals_per_game,
        is_fallback=_cache.is_fallback,
        goalies=_cache.sample_size_goalies,
        teams=_cache.sample_size_teams,
    )
    return _cache


async def refresh_league_averages(db: AsyncSession) -> LeagueAverages:
    """Force a refresh, ignoring the TTL. Used by admin endpoints."""
    global _cache
    _cache = await _compute(db)
    return _cache


# Convenience getters that match the shape of the old constants.
# Predictions code calls these instead of importing constants.
async def league_save_pct(db: AsyncSession) -> float:
    avg = await get_league_averages(db)
    return avg.save_pct


async def league_gaa(db: AsyncSession) -> float:
    avg = await get_league_averages(db)
    return avg.gaa


async def league_goals_per_game(db: AsyncSession) -> float:
    avg = await get_league_averages(db)
    return avg.goals_per_game
