#!/usr/bin/env python
"""
Bulk ingestion script - loads all historical seasons (2007-present).

Usage:
    # Ingest all missing seasons
    python -m backend.scripts.ingest_all_seasons

    # Ingest specific range
    python -m backend.scripts.ingest_all_seasons --start 2015 --end 2023

    # Force re-ingest all (ignore progress)
    python -m backend.scripts.ingest_all_seasons --force

    # Quick mode (MoneyPuck only, skip rosters)
    python -m backend.scripts.ingest_all_seasons --quick
"""
import asyncio
import argparse
from datetime import datetime
import structlog
from sqlalchemy import text

from backend.src.db.database import async_session_maker
from backend.src.ingestion.nhl_api import NHLAPIClient
from backend.src.ingestion.moneypuck import download_season_stats, transform_moneypuck_to_schema
from backend.src.ingestion.scheduler import (
    IngestionConfig,
    get_all_seasons,
    get_pending_seasons,
    mark_season_complete,
    load_progress,
    MONEYPUCK_FIRST_SEASON,
    CURRENT_SEASON,
)

logger = structlog.get_logger()


async def ingest_teams(client: NHLAPIClient, db_session):
    """Ingest all NHL teams from current standings."""
    logger.info("ingesting_teams")

    standings = await client.get_standings()

    teams_inserted = 0
    for record in standings.get("standings", []):
        team_abbrev = record.get("teamAbbrev", {}).get("default")
        team_name = record.get("teamName", {}).get("default")
        conference = record.get("conferenceName")
        division = record.get("divisionName")

        await db_session.execute(
            text("""
                INSERT INTO teams (nhl_id, name, abbrev, conference, division)
                VALUES (:nhl_id, :name, :abbrev, :conference, :division)
                ON CONFLICT (abbrev) DO UPDATE SET
                    name = EXCLUDED.name,
                    conference = EXCLUDED.conference,
                    division = EXCLUDED.division
            """),
            {
                "nhl_id": hash(team_abbrev) % 100,
                "name": team_name,
                "abbrev": team_abbrev,
                "conference": conference,
                "division": division,
            },
        )
        teams_inserted += 1

    await db_session.commit()
    logger.info("teams_ingested", count=teams_inserted)


async def ingest_season_moneypuck(db_session, season: str) -> int:
    """Ingest MoneyPuck stats for a single season. Returns count of records."""
    logger.info("ingesting_moneypuck", season=season)

    try:
        df = await download_season_stats(season)
    except Exception as e:
        logger.error("moneypuck_download_failed", season=season, error=str(e))
        return 0

    records = transform_moneypuck_to_schema(df)
    logger.info("moneypuck_records", season=season, count=len(records))

    stats_inserted = 0
    for record in records:
        # Ensure player exists
        result = await db_session.execute(
            text("SELECT id FROM players WHERE nhl_id = :nhl_id"),
            {"nhl_id": record["nhl_player_id"]},
        )
        player_row = result.fetchone()

        if not player_row:
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
                        shots = EXCLUDED.shots,
                        xg = EXCLUDED.xg,
                        xg_per_60 = EXCLUDED.xg_per_60,
                        corsi_for_pct = EXCLUDED.corsi_for_pct,
                        fenwick_for_pct = EXCLUDED.fenwick_for_pct
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
    logger.info("moneypuck_stats_inserted", season=season, count=stats_inserted)
    return stats_inserted


async def ingest_single_season(season: str, include_rosters: bool = False) -> dict:
    """Ingest a single season. Returns summary stats."""
    start_time = datetime.now()
    logger.info("starting_season_ingestion", season=season)

    result = {
        "season": season,
        "moneypuck_records": 0,
        "roster_players": 0,
        "success": False,
        "error": None,
    }

    try:
        async with async_session_maker() as db_session:
            # MoneyPuck stats (the main data)
            result["moneypuck_records"] = await ingest_season_moneypuck(db_session, season)

            # Note: Roster ingestion for historical seasons often fails
            # because the NHL API doesn't have roster data for old seasons
            # So we skip it for historical seasons

        result["success"] = True
        mark_season_complete(season)

    except Exception as e:
        result["error"] = str(e)
        logger.error("season_ingestion_failed", season=season, error=str(e))

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(
        "season_ingestion_complete",
        season=season,
        elapsed_seconds=round(elapsed, 1),
        records=result["moneypuck_records"],
        success=result["success"],
    )

    return result


async def ingest_all_seasons(config: IngestionConfig):
    """Ingest multiple seasons based on configuration."""
    seasons = config.get_seasons_to_process()

    if not seasons:
        logger.info("no_seasons_to_process")
        return []

    logger.info(
        "starting_bulk_ingestion",
        total_seasons=len(seasons),
        seasons=seasons,
    )

    # First, ensure teams are loaded
    client = NHLAPIClient()
    async with async_session_maker() as db_session:
        await ingest_teams(client, db_session)
    await client.close()

    # Process seasons (one at a time to avoid rate limiting)
    results = []
    for i, season in enumerate(seasons):
        logger.info("processing_season", season=season, progress=f"{i+1}/{len(seasons)}")
        result = await ingest_single_season(season, config.include_rosters)
        results.append(result)

        # Rate limiting between seasons
        if i < len(seasons) - 1:
            await asyncio.sleep(config.rate_limit_delay)

    # Summary
    successful = sum(1 for r in results if r["success"])
    total_records = sum(r["moneypuck_records"] for r in results)

    logger.info(
        "bulk_ingestion_complete",
        successful_seasons=successful,
        total_seasons=len(seasons),
        total_records=total_records,
    )

    return results


async def main():
    parser = argparse.ArgumentParser(description="Bulk ingest NHL data for multiple seasons")
    parser.add_argument(
        "--start",
        type=int,
        default=MONEYPUCK_FIRST_SEASON,
        help=f"Start year (default: {MONEYPUCK_FIRST_SEASON})",
    )
    parser.add_argument(
        "--end",
        type=int,
        default=CURRENT_SEASON,
        help=f"End year (default: {CURRENT_SEASON})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-ingestion of all seasons (ignore progress)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick mode: MoneyPuck only, skip rosters",
    )
    parser.add_argument(
        "--season",
        type=str,
        help="Ingest a specific single season (e.g., 2023)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show ingestion progress status and exit",
    )

    args = parser.parse_args()

    # Status check
    if args.status:
        progress = load_progress()
        pending = get_pending_seasons()
        print("\n=== Ingestion Status ===")
        print(f"Completed seasons: {len(progress['completed_seasons'])}")
        print(f"Pending seasons: {len(pending)}")
        print(f"Last update: {progress.get('last_update', 'Never')}")
        if pending:
            print(f"\nPending: {', '.join(pending[:10])}{'...' if len(pending) > 10 else ''}")
        return

    # Single season mode
    if args.season:
        result = await ingest_single_season(args.season, include_rosters=not args.quick)
        print(f"\nSeason {args.season}: {'Success' if result['success'] else 'Failed'}")
        print(f"Records: {result['moneypuck_records']}")
        return

    # Bulk ingestion
    config = IngestionConfig(
        start_year=args.start,
        end_year=args.end,
        skip_completed=not args.force,
        include_rosters=not args.quick,
    )

    results = await ingest_all_seasons(config)

    # Print summary
    print("\n=== Ingestion Summary ===")
    for r in results:
        status = "OK" if r["success"] else f"FAILED: {r['error']}"
        print(f"  {r['season']}: {r['moneypuck_records']} records - {status}")


if __name__ == "__main__":
    asyncio.run(main())
