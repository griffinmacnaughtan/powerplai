"""
Database migrations for PowerplAI.

Handles schema updates without using a full migration framework.
Run these when adding new tables or columns.
"""
import asyncio
from sqlalchemy import text
import structlog

from backend.src.db.database import engine
from backend.src.db.models import Base

logger = structlog.get_logger()


async def create_all_tables():
    """Create all tables that don't exist yet."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("ensured_all_tables_exist")


async def migrate_players_table():
    """Add salary/contract columns to players table if they don't exist."""
    columns_to_add = [
        ("cap_hit_cents", "INTEGER"),
        ("contract_years", "INTEGER"),
        ("contract_expiry", "INTEGER"),
    ]

    async with engine.begin() as conn:
        for col_name, col_type in columns_to_add:
            try:
                await conn.execute(text(f"""
                    ALTER TABLE players ADD COLUMN IF NOT EXISTS {col_name} {col_type}
                """))
                logger.debug("added_column", table="players", column=col_name)
            except Exception as e:
                if "already exists" not in str(e).lower():
                    logger.warning("column_add_failed", column=col_name, error=str(e))

    logger.info("migrated_players_table")


async def migrate_game_logs_table():
    """Add new columns to game_logs table if they don't exist."""
    columns_to_add = [
        ("season", "VARCHAR(10)"),
        ("team_abbrev", "VARCHAR(10)"),
        ("pim", "INTEGER DEFAULT 0"),
        ("powerplay_goals", "INTEGER DEFAULT 0"),
        ("powerplay_points", "INTEGER DEFAULT 0"),
        ("shorthanded_goals", "INTEGER DEFAULT 0"),
        ("shorthanded_points", "INTEGER DEFAULT 0"),
        ("game_winning_goals", "INTEGER DEFAULT 0"),
        ("overtime_goals", "INTEGER DEFAULT 0"),
        ("shifts", "INTEGER"),
    ]

    async with engine.begin() as conn:
        for col_name, col_type in columns_to_add:
            try:
                await conn.execute(text(f"""
                    ALTER TABLE game_logs ADD COLUMN IF NOT EXISTS {col_name} {col_type}
                """))
                logger.debug("added_column", table="game_logs", column=col_name)
            except Exception as e:
                # Column might already exist (older Postgres doesn't have IF NOT EXISTS)
                if "already exists" not in str(e).lower():
                    logger.warning("column_add_failed", column=col_name, error=str(e))

        # Add new indexes for predictions
        indexes = [
            ("idx_game_logs_player_season", "game_logs", "player_id, season"),
            ("idx_game_logs_opponent", "game_logs", "player_id, opponent"),
            ("idx_game_logs_game", "game_logs", "game_id"),
        ]

        for idx_name, table, columns in indexes:
            try:
                await conn.execute(text(f"""
                    CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({columns})
                """))
            except Exception as e:
                if "already exists" not in str(e).lower():
                    logger.warning("index_create_failed", index=idx_name, error=str(e))

    logger.info("migrated_game_logs_table")


async def add_unique_constraints():
    """Add unique constraints needed for upserts."""
    constraints = [
        ("goalie_stats", "goalie_stats_player_season_key", "player_id, season"),
        ("team_season_stats", "team_season_stats_team_season_key", "team_abbrev, season"),
    ]

    async with engine.begin() as conn:
        for table, constraint_name, columns in constraints:
            try:
                # Check if constraint exists
                result = await conn.execute(text(f"""
                    SELECT 1 FROM pg_constraint WHERE conname = '{constraint_name}'
                """))
                if not result.fetchone():
                    await conn.execute(text(f"""
                        ALTER TABLE {table} ADD CONSTRAINT {constraint_name} UNIQUE ({columns})
                    """))
                    logger.debug("added_constraint", table=table, constraint=constraint_name)
            except Exception as e:
                if "already exists" not in str(e).lower():
                    logger.warning("constraint_add_failed", constraint=constraint_name, error=str(e))

        # Add partial unique index for active injuries (for upsert)
        try:
            await conn.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_injuries_player_active
                ON injuries (player_id) WHERE is_active = TRUE
            """))
            logger.debug("added_index", index="idx_injuries_player_active")
        except Exception as e:
            if "already exists" not in str(e).lower():
                logger.warning("index_create_failed", index="idx_injuries_player_active", error=str(e))

        # Add unique constraint for game_logs (player_id, game_id)
        try:
            result = await conn.execute(text("""
                SELECT 1 FROM pg_constraint WHERE conname = 'game_logs_player_game_key'
            """))
            if not result.fetchone():
                await conn.execute(text("""
                    ALTER TABLE game_logs ADD CONSTRAINT game_logs_player_game_key UNIQUE (player_id, game_id)
                """))
                logger.debug("added_constraint", table="game_logs", constraint="game_logs_player_game_key")
        except Exception as e:
            if "already exists" not in str(e).lower():
                logger.warning("constraint_add_failed", constraint="game_logs_player_game_key", error=str(e))


async def run_migrations():
    """Run all pending migrations."""
    logger.info("running_database_migrations")

    # Ensure all tables exist (creates games, goalie_stats, team_season_stats, injuries, etc.)
    await create_all_tables()

    # Add new columns to existing tables
    await migrate_players_table()
    await migrate_game_logs_table()

    # Add unique constraints for upserts
    await add_unique_constraints()

    logger.info("database_migrations_complete")


if __name__ == "__main__":
    asyncio.run(run_migrations())
