#!/usr/bin/env python
"""
Sync data ingestion script - uses psycopg2 instead of asyncpg.
Avoids Windows asyncio issues with Docker.

Usage:
    python -m backend.scripts.ingest_sync --season 2023
"""
import argparse
import time
from pathlib import Path

import httpx
import psycopg2
from psycopg2.extras import execute_values
import pandas as pd
from io import StringIO

# Add parent to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# -------------------------------------------------------------------------
# Config
# -------------------------------------------------------------------------

DB_CONFIG = {
    "host": "127.0.0.1",  # Use IP instead of localhost to avoid IPv6 issues
    "port": 5433,
    "database": "powerplai",
    "user": "powerplai",
    "password": "powerplai_dev",
}

NHL_API_BASE = "https://api-web.nhle.com/v1"
MONEYPUCK_BASE = "https://moneypuck.com/moneypuck/playerData"


# -------------------------------------------------------------------------
# NHL API Client (sync)
# -------------------------------------------------------------------------

def nhl_get(path: str) -> dict:
    """Make a GET request to NHL API."""
    url = f"{NHL_API_BASE}/{path}"
    print(f"  Fetching: {url}")
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.json()


def get_standings() -> dict:
    return nhl_get("standings/now")


def get_team_roster(team_abbrev: str, season: str) -> dict:
    return nhl_get(f"roster/{team_abbrev}/{season}")


# -------------------------------------------------------------------------
# MoneyPuck
# -------------------------------------------------------------------------

def download_moneypuck(season: str) -> pd.DataFrame:
    """Download MoneyPuck season stats."""
    # Try different URL formats (MoneyPuck changes these sometimes)
    urls_to_try = [
        f"{MONEYPUCK_BASE}/seasonSummary/{season}/regular/skaters.csv",
        f"{MONEYPUCK_BASE}/seasonSummary/{season}/regular/all_teams.csv",
        f"https://moneypuck.com/moneypuck/playerData/careers/gameByGame/{season}/skaters.csv",
    ]

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        for url in urls_to_try:
            print(f"  Trying: {url}")
            try:
                response = client.get(url)
                if response.status_code == 200:
                    print(f"  Success!")
                    return pd.read_csv(StringIO(response.text))
            except Exception as e:
                print(f"  Failed: {e}")
                continue

    raise Exception(f"Could not find MoneyPuck data for season {season}")


# -------------------------------------------------------------------------
# Ingestion Functions
# -------------------------------------------------------------------------

def ingest_teams(conn):
    """Ingest teams from standings."""
    print("\n[1/3] Ingesting teams...")
    standings = get_standings()

    cursor = conn.cursor()
    teams_data = []

    for record in standings.get("standings", []):
        team_abbrev = record.get("teamAbbrev", {}).get("default")
        team_name = record.get("teamName", {}).get("default")
        conference = record.get("conferenceName")
        division = record.get("divisionName")

        teams_data.append((
            hash(team_abbrev) % 1000,  # placeholder nhl_id
            team_name,
            team_abbrev,
            conference,
            division,
        ))

    execute_values(
        cursor,
        """
        INSERT INTO teams (nhl_id, name, abbrev, conference, division)
        VALUES %s
        ON CONFLICT (abbrev) DO UPDATE SET
            name = EXCLUDED.name,
            conference = EXCLUDED.conference,
            division = EXCLUDED.division
        """,
        teams_data,
    )
    conn.commit()
    print(f"  Inserted/updated {len(teams_data)} teams")

    # Return team abbreviations for roster fetch
    cursor.execute("SELECT abbrev FROM teams")
    return [row[0] for row in cursor.fetchall()]


def ingest_rosters(conn, teams: list, season: str):
    """Ingest players from team rosters."""
    print(f"\n[2/3] Ingesting rosters for {len(teams)} teams...")
    cursor = conn.cursor()
    total_players = 0

    for i, team in enumerate(teams):
        print(f"  [{i+1}/{len(teams)}] {team}...", end=" ")
        try:
            roster = get_team_roster(team, season)
        except Exception as e:
            print(f"SKIP ({e})")
            continue

        players_data = []
        for group in ["forwards", "defensemen", "goalies"]:
            for player in roster.get(group, []):
                player_id = player.get("id")
                first_name = player.get("firstName", {}).get("default", "")
                last_name = player.get("lastName", {}).get("default", "")
                name = f"{first_name} {last_name}".strip()

                players_data.append((
                    player_id,
                    name,
                    player.get("positionCode"),
                    team,
                    player.get("shootsCatches"),
                    player.get("heightInInches"),
                    player.get("weightInPounds"),
                ))

        if players_data:
            execute_values(
                cursor,
                """
                INSERT INTO players (nhl_id, name, position, team_abbrev, shoots_catches, height_inches, weight_lbs)
                VALUES %s
                ON CONFLICT (nhl_id) DO UPDATE SET
                    team_abbrev = EXCLUDED.team_abbrev,
                    name = EXCLUDED.name
                """,
                players_data,
            )
            conn.commit()

        print(f"{len(players_data)} players")
        total_players += len(players_data)
        time.sleep(0.3)  # Rate limiting

    print(f"  Total: {total_players} players ingested")


def ingest_moneypuck(conn, season: str):
    """Ingest MoneyPuck advanced stats."""
    print(f"\n[3/3] Ingesting MoneyPuck stats for {season}...")

    df = download_moneypuck(season)
    print(f"  Downloaded {len(df)} player records")

    cursor = conn.cursor()
    season_str = f"{season}{int(season)+1}"
    stats_inserted = 0

    for _, row in df.iterrows():
        # Only use "all" situation rows (combined stats across all situations)
        situation = row.get("situation", "all")
        if situation != "all":
            continue

        nhl_id = int(row.get("playerId", 0))
        if nhl_id == 0:
            continue

        games = row.get("games_played", row.get("GP", 0))
        if games == 0:
            continue

        # Check if player exists
        cursor.execute("SELECT id FROM players WHERE nhl_id = %s", (nhl_id,))
        result = cursor.fetchone()

        if not result:
            # Insert player
            cursor.execute(
                """
                INSERT INTO players (nhl_id, name, team_abbrev)
                VALUES (%s, %s, %s)
                ON CONFLICT (nhl_id) DO NOTHING
                RETURNING id
                """,
                (nhl_id, row.get("name", ""), row.get("team", "")),
            )
            result = cursor.fetchone()
            if not result:
                cursor.execute("SELECT id FROM players WHERE nhl_id = %s", (nhl_id,))
                result = cursor.fetchone()

        if result:
            player_id = result[0]
            icetime = row.get("icetime", 0)
            toi_per_game = round(icetime / games / 60, 2) if games > 0 else 0
            xg = float(row.get("I_F_xGoals", 0))
            xg_per_60 = round(xg / (icetime / 3600), 3) if icetime > 0 else 0

            cursor.execute(
                """
                INSERT INTO player_season_stats (
                    player_id, season, team_abbrev, games_played,
                    goals, assists, points, shots, toi_per_game,
                    xg, xg_per_60, corsi_for_pct, fenwick_for_pct
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (player_id, season) DO UPDATE SET
                    games_played = EXCLUDED.games_played,
                    goals = EXCLUDED.goals,
                    assists = EXCLUDED.assists,
                    points = EXCLUDED.points,
                    xg = EXCLUDED.xg,
                    corsi_for_pct = EXCLUDED.corsi_for_pct
                """,
                (
                    player_id,
                    season_str,
                    row.get("team", ""),
                    int(games),
                    int(row.get("I_F_goals", 0)),
                    int(row.get("I_F_primaryAssists", 0) + row.get("I_F_secondaryAssists", 0)),
                    int(row.get("I_F_points", 0)),
                    int(row.get("I_F_shots", 0)),
                    toi_per_game,
                    round(xg, 2),
                    xg_per_60,
                    round(float(row.get("onIce_corsiPercentage", 50)), 2),
                    round(float(row.get("onIce_fenwickPercentage", 50)), 2),
                ),
            )
            stats_inserted += 1

    conn.commit()
    print(f"  Inserted {stats_inserted} player season stats")


def main(season: str, skip_rosters: bool = False):
    """Main ingestion pipeline."""
    print(f"=" * 50)
    print(f"PowerplAI Data Ingestion")
    print(f"Season: {season}-{int(season)+1}")
    print(f"=" * 50)

    # Connect to database
    print("\nConnecting to database...")
    conn = psycopg2.connect(**DB_CONFIG)
    print("  Connected!")

    try:
        # 1. Ingest teams
        teams = ingest_teams(conn)

        # 2. Ingest rosters
        if not skip_rosters:
            season_str = f"{season}{int(season)+1}"
            ingest_rosters(conn, teams, season_str)
        else:
            print("\n[2/3] Skipping roster ingestion")

        # 3. Ingest MoneyPuck stats
        ingest_moneypuck(conn, season)

        print("\n" + "=" * 50)
        print("Ingestion complete!")
        print("=" * 50)

        # Show summary
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM teams")
        print(f"  Teams: {cursor.fetchone()[0]}")
        cursor.execute("SELECT COUNT(*) FROM players")
        print(f"  Players: {cursor.fetchone()[0]}")
        cursor.execute("SELECT COUNT(*) FROM player_season_stats")
        print(f"  Season stats: {cursor.fetchone()[0]}")

    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest NHL data (sync version)")
    parser.add_argument("--season", default="2023", help="Season year (e.g., 2023 for 2023-24)")
    parser.add_argument("--skip-rosters", action="store_true", help="Skip roster ingestion")

    args = parser.parse_args()
    main(args.season, args.skip_rosters)
