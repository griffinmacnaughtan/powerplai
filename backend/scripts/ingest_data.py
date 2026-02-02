#!/usr/bin/env python
"""
Data ingestion script - populates the database with NHL and MoneyPuck data.

Usage:
    python -m backend.scripts.ingest_data --season 2023
"""
import asyncio
import argparse
from pathlib import Path
import structlog
from sqlalchemy import text

# Add parent to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.src.db.database import async_session_maker
from backend.src.ingestion.nhl_api import NHLAPIClient, parse_player_from_landing
from backend.src.ingestion.moneypuck import (
    download_season_stats,
    transform_moneypuck_to_schema,
)

logger = structlog.get_logger()


async def ingest_teams(client: NHLAPIClient, db_session):
    """Ingest all NHL teams from standings."""
    logger.info("ingesting_teams")

    standings = await client.get_standings()

    teams_inserted = 0
    for record in standings.get("standings", []):
        team_abbrev = record.get("teamAbbrev", {}).get("default")
        team_name = record.get("teamName", {}).get("default")
        conference = record.get("conferenceName")
        division = record.get("divisionName")

        # Get team ID from the logo URL or other field
        # NHL API doesn't directly expose team IDs in standings
        # We'll use a placeholder and update later
        # Use abbrev as a stable unique identifier for nhl_id
        team_nhl_id = sum(ord(c) * (i + 1) for i, c in enumerate(team_abbrev))
        await db_session.execute(
            text("""
                INSERT INTO teams (nhl_id, name, abbrev, conference, division)
                VALUES (:nhl_id, :name, :abbrev, :conference, :division)
                ON CONFLICT (abbrev) DO UPDATE SET
                    name = EXCLUDED.name,
                    conference = EXCLUDED.conference,
                    division = EXCLUDED.division,
                    nhl_id = EXCLUDED.nhl_id
            """),
            {
                "nhl_id": team_nhl_id,
                "name": team_name,
                "abbrev": team_abbrev,
                "conference": conference,
                "division": division,
            },
        )
        teams_inserted += 1

    await db_session.commit()
    logger.info("teams_ingested", count=teams_inserted)


async def ingest_roster_players(client: NHLAPIClient, db_session, team_abbrev: str, season: str):
    """Ingest players from a team's roster."""
    logger.info("ingesting_roster", team=team_abbrev, season=season)

    try:
        roster_data = await client.get_team_roster(team_abbrev, season)
    except Exception as e:
        logger.warning("roster_fetch_failed", team=team_abbrev, error=str(e))
        return 0

    players_inserted = 0

    for group in ["forwards", "defensemen", "goalies"]:
        for player in roster_data.get(group, []):
            player_id = player.get("id")
            first_name = player.get("firstName", {}).get("default", "")
            last_name = player.get("lastName", {}).get("default", "")
            name = f"{first_name} {last_name}".strip()

            await db_session.execute(
                text("""
                    INSERT INTO players (nhl_id, name, position, team_abbrev, shoots_catches, height_inches, weight_lbs)
                    VALUES (:nhl_id, :name, :position, :team_abbrev, :shoots_catches, :height_inches, :weight_lbs)
                    ON CONFLICT (nhl_id) DO UPDATE SET
                        team_abbrev = EXCLUDED.team_abbrev,
                        name = EXCLUDED.name
                """),
                {
                    "nhl_id": player_id,
                    "name": name,
                    "position": player.get("positionCode"),
                    "team_abbrev": team_abbrev,
                    "shoots_catches": player.get("shootsCatches"),
                    "height_inches": player.get("heightInInches"),
                    "weight_lbs": player.get("weightInPounds"),
                },
            )
            players_inserted += 1

    await db_session.commit()
    return players_inserted


async def ingest_moneypuck_stats(db_session, season: str):
    """Ingest MoneyPuck advanced stats."""
    logger.info("ingesting_moneypuck", season=season)

    # Download MoneyPuck data
    data_path = Path(f"data/raw/moneypuck_{season}.csv")
    df = await download_season_stats(season, save_path=data_path)

    # Transform to our schema
    records = transform_moneypuck_to_schema(df)
    logger.info("moneypuck_records", count=len(records))

    stats_inserted = 0
    for record in records:
        # First ensure player exists
        result = await db_session.execute(
            text("SELECT id FROM players WHERE nhl_id = :nhl_id"),
            {"nhl_id": record["nhl_player_id"]},
        )
        player_row = result.fetchone()

        if not player_row:
            # Insert player if not exists
            await db_session.execute(
                text("""
                    INSERT INTO players (nhl_id, name, team_abbrev)
                    VALUES (:nhl_id, :name, :team_abbrev)
                    ON CONFLICT (nhl_id) DO NOTHING
                """),
                {
                    "nhl_id": record["nhl_player_id"],
                    "name": record["player_name"],
                    "team_abbrev": record["team_abbrev"],
                },
            )
            await db_session.commit()

            # Fetch the player id
            result = await db_session.execute(
                text("SELECT id FROM players WHERE nhl_id = :nhl_id"),
                {"nhl_id": record["nhl_player_id"]},
            )
            player_row = result.fetchone()

        if player_row:
            player_id = player_row[0]
            season_str = f"{season}{int(season)+1}"

            await db_session.execute(
                text("""
                    INSERT INTO player_season_stats (
                        player_id, season, team_abbrev, games_played,
                        goals, assists, points, shots, toi_per_game,
                        xg, xg_per_60, corsi_for_pct, fenwick_for_pct
                    ) VALUES (
                        :player_id, :season, :team_abbrev, :games_played,
                        :goals, :assists, :points, :shots, :toi_per_game,
                        :xg, :xg_per_60, :corsi_for_pct, :fenwick_for_pct
                    )
                    ON CONFLICT (player_id, season) DO UPDATE SET
                        games_played = EXCLUDED.games_played,
                        goals = EXCLUDED.goals,
                        assists = EXCLUDED.assists,
                        points = EXCLUDED.points,
                        xg = EXCLUDED.xg,
                        corsi_for_pct = EXCLUDED.corsi_for_pct
                """),
                {
                    "player_id": player_id,
                    "season": season_str,
                    "team_abbrev": record["team_abbrev"],
                    "games_played": record["games_played"],
                    "goals": record["goals"],
                    "assists": record["assists"],
                    "points": record["points"],
                    "shots": record["shots"],
                    "toi_per_game": record["toi_per_game"],
                    "xg": record["xg"],
                    "xg_per_60": record["xg_per_60"],
                    "corsi_for_pct": record["corsi_for_pct"],
                    "fenwick_for_pct": record["fenwick_for_pct"],
                },
            )
            stats_inserted += 1

    await db_session.commit()
    logger.info("moneypuck_stats_inserted", count=stats_inserted)


async def main(season: str, skip_rosters: bool = False):
    """Main ingestion pipeline."""
    logger.info("starting_ingestion", season=season)

    client = NHLAPIClient()

    async with async_session_maker() as db_session:
        # 1. Ingest teams
        await ingest_teams(client, db_session)

        # 2. Ingest players from rosters
        if not skip_rosters:
            # Get team abbreviations
            result = await db_session.execute(text("SELECT abbrev FROM teams"))
            teams = [row[0] for row in result.fetchall()]

            total_players = 0
            for team in teams:
                count = await ingest_roster_players(
                    client, db_session, team, f"{season}{int(season)+1}"
                )
                total_players += count
                await asyncio.sleep(0.5)  # Rate limiting

            logger.info("players_ingested", total=total_players)

        # 3. Ingest MoneyPuck advanced stats
        await ingest_moneypuck_stats(db_session, season)

    await client.close()
    logger.info("ingestion_complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest NHL data")
    parser.add_argument("--season", default="2023", help="Season year (e.g., 2023 for 2023-24)")
    parser.add_argument("--skip-rosters", action="store_true", help="Skip roster ingestion")

    args = parser.parse_args()

    asyncio.run(main(args.season, args.skip_rosters))
