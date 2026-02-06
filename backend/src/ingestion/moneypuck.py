"""
MoneyPuck data ingestion for advanced stats (xG, Corsi, etc.).

Data available at: https://moneypuck.com/data.htm
"""
import httpx
import pandas as pd
import structlog
from io import StringIO
from pathlib import Path

logger = structlog.get_logger()

MONEYPUCK_BASE = "https://moneypuck.com/moneypuck/playerData"


async def download_season_stats(
    season: str,
    situation: str = "all",
    save_path: Path | None = None,
) -> pd.DataFrame:
    """
    Download MoneyPuck season summary stats.

    Args:
        season: Year (e.g., "2023" for 2023-24 season)
        situation: "all", "5on5", "5on4", etc. (Note: "all" uses skaters.csv)
        save_path: Optional path to save CSV

    Returns:
        DataFrame with player stats
    """
    # MoneyPuck uses skaters.csv for all-situation player stats
    filename = "skaters.csv" if situation == "all" else f"{situation}_skaters.csv"
    url = f"{MONEYPUCK_BASE}/seasonSummary/{season}/regular/{filename}"
    logger.info("downloading_moneypuck", url=url, season=season)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    async with httpx.AsyncClient(timeout=60.0, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()

    df = pd.read_csv(StringIO(response.text))

    if save_path:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(save_path, index=False)
        logger.info("saved_moneypuck_data", path=str(save_path), rows=len(df))

    return df


async def download_shot_data(
    season: str,
    save_path: Path | None = None,
) -> pd.DataFrame:
    """
    Download MoneyPuck shot-level data with xG.

    Warning: This file can be large (100MB+).
    """
    url = f"{MONEYPUCK_BASE}/shots_{season}.csv"
    logger.info("downloading_moneypuck_shots", url=url, season=season)

    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.get(url)
        response.raise_for_status()

    df = pd.read_csv(StringIO(response.text))

    if save_path:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(save_path, index=False)
        logger.info("saved_shot_data", path=str(save_path), rows=len(df))

    return df


def transform_moneypuck_to_schema(df: pd.DataFrame) -> list[dict]:
    """
    Transform MoneyPuck season summary to our player_season_stats schema.

    MoneyPuck columns we care about:
    - playerId, name, team, position
    - I_F_goals, I_F_primaryAssists, I_F_secondaryAssists
    - I_F_points, I_F_shotsOnGoal, icetime
    - I_F_xGoals, onIce_corsiPercentage, onIce_fenwickPercentage
    """
    records = []

    # Filter to only "all" situation rows (full stats, not 5on5/PP/PK splits)
    if "situation" in df.columns:
        df = df[df["situation"] == "all"]

    for _, row in df.iterrows():
        # MoneyPuck uses playerId which matches NHL API player IDs
        games = row.get("games_played", row.get("GP", 0))
        if pd.isna(games) or games == 0:
            continue

        # MoneyPuck icetime can be in seconds (older format) or minutes (newer format)
        # Try multiple column names
        icetime_total = row.get("icetime", row.get("iceTime", row.get("TOI", 0)))
        if pd.isna(icetime_total):
            icetime_total = 0

        # Calculate TOI per game - detect format based on value magnitude
        if games > 0 and icetime_total > 0:
            # If icetime > 10000, it's likely in seconds (e.g., 50000 sec = 833 min)
            # If icetime < 2000, it's likely already in minutes (e.g., 800 min for season)
            if icetime_total > 5000:
                # Seconds format: divide by 60 to get minutes
                toi_per_game = round(icetime_total / games / 60, 2)
            else:
                # Minutes format: don't divide by 60
                toi_per_game = round(icetime_total / games, 2)
        else:
            toi_per_game = 0

        # Handle NaN values
        def safe_int(val, default=0):
            return int(val) if pd.notna(val) else default

        def safe_float(val, default=0.0):
            return float(val) if pd.notna(val) else default

        # Corsi percentages in MoneyPuck are stored as decimals (0.52 = 52%)
        corsi_pct = safe_float(row.get("onIce_corsiPercentage", 0.5))
        fenwick_pct = safe_float(row.get("onIce_fenwickPercentage", 0.5))
        # Convert to percentage if stored as decimal
        if corsi_pct <= 1:
            corsi_pct *= 100
        if fenwick_pct <= 1:
            fenwick_pct *= 100

        records.append({
            "nhl_player_id": safe_int(row.get("playerId", 0)),
            "player_name": str(row.get("name", "")),
            "team_abbrev": str(row.get("team", "")),
            "games_played": safe_int(games),
            "goals": safe_int(row.get("I_F_goals", 0)),
            "assists": safe_int(row.get("I_F_primaryAssists", 0)) + safe_int(row.get("I_F_secondaryAssists", 0)),
            "points": safe_int(row.get("I_F_points", 0)),
            "shots": safe_int(row.get("I_F_shotsOnGoal", row.get("I_F_shots", 0))),
            "toi_per_game": toi_per_game,
            # Advanced stats
            "xg": round(safe_float(row.get("I_F_xGoals", 0)), 2),
            "xg_per_60": round(
                # Calculate per-60 rate: xG / (hours played)
                # Adjust divisor based on icetime format
                safe_float(row.get("I_F_xGoals", 0)) / (icetime_total / 3600 if icetime_total > 5000 else icetime_total / 60) if icetime_total > 0 else 0,
                3
            ),
            "corsi_for_pct": round(corsi_pct, 2),
            "fenwick_for_pct": round(fenwick_pct, 2),
        })

    return records


# -------------------------------------------------------------------------
# Convenience functions for common queries
# -------------------------------------------------------------------------


def get_xg_leaders(df: pd.DataFrame, top_n: int = 20) -> pd.DataFrame:
    """Get top players by expected goals."""
    return (
        df.nlargest(top_n, "I_F_xGoals")[
            ["name", "team", "position", "I_F_goals", "I_F_xGoals", "games_played"]
        ]
        .rename(columns={"I_F_goals": "goals", "I_F_xGoals": "xG"})
    )


def get_overperformers(df: pd.DataFrame, min_games: int = 20) -> pd.DataFrame:
    """Find players scoring more goals than expected (lucky or skilled finishers)."""
    filtered = df[df["games_played"] >= min_games].copy()
    filtered["goals_above_xg"] = filtered["I_F_goals"] - filtered["I_F_xGoals"]
    return (
        filtered.nlargest(20, "goals_above_xg")[
            ["name", "team", "I_F_goals", "I_F_xGoals", "goals_above_xg"]
        ]
        .rename(columns={"I_F_goals": "goals", "I_F_xGoals": "xG"})
    )


def get_underperformers(df: pd.DataFrame, min_games: int = 20) -> pd.DataFrame:
    """Find players scoring fewer goals than expected (unlucky or due for regression)."""
    filtered = df[df["games_played"] >= min_games].copy()
    filtered["goals_below_xg"] = filtered["I_F_xGoals"] - filtered["I_F_goals"]
    return (
        filtered.nlargest(20, "goals_below_xg")[
            ["name", "team", "I_F_goals", "I_F_xGoals", "goals_below_xg"]
        ]
        .rename(columns={"I_F_goals": "goals", "I_F_xGoals": "xG"})
    )
