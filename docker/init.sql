-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Players table
CREATE TABLE IF NOT EXISTS players (
    id SERIAL PRIMARY KEY,
    nhl_id INTEGER UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    position VARCHAR(10),
    team_abbrev VARCHAR(10),
    birth_date DATE,
    shoots_catches VARCHAR(1),
    height_inches INTEGER,
    weight_lbs INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Teams table
CREATE TABLE IF NOT EXISTS teams (
    id SERIAL PRIMARY KEY,
    nhl_id INTEGER UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    abbrev VARCHAR(10) UNIQUE NOT NULL,
    conference VARCHAR(50),
    division VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Player season stats (structured data for SQL queries)
CREATE TABLE IF NOT EXISTS player_season_stats (
    id SERIAL PRIMARY KEY,
    player_id INTEGER REFERENCES players(id),
    season VARCHAR(10) NOT NULL,  -- e.g., "20232024"
    team_abbrev VARCHAR(10),
    games_played INTEGER,
    goals INTEGER,
    assists INTEGER,
    points INTEGER,
    plus_minus INTEGER,
    pim INTEGER,  -- penalties in minutes
    shots INTEGER,
    shooting_pct DECIMAL(5,2),
    toi_per_game DECIMAL(6,2),  -- time on ice per game in minutes
    -- Advanced stats (from MoneyPuck)
    xg DECIMAL(6,2),  -- expected goals
    xg_per_60 DECIMAL(6,3),
    corsi_for_pct DECIMAL(5,2),
    fenwick_for_pct DECIMAL(5,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(player_id, season)
);

-- Game logs for granular queries
CREATE TABLE IF NOT EXISTS game_logs (
    id SERIAL PRIMARY KEY,
    player_id INTEGER REFERENCES players(id),
    game_id INTEGER NOT NULL,
    game_date DATE NOT NULL,
    opponent VARCHAR(10),
    home_away VARCHAR(4),
    goals INTEGER,
    assists INTEGER,
    points INTEGER,
    shots INTEGER,
    toi DECIMAL(6,2),
    plus_minus INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(player_id, game_id)
);

-- Documents table for RAG (articles, analysis, etc.)
CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    title VARCHAR(500),
    source VARCHAR(255),  -- e.g., "moneypuck", "athletic", "user_upload"
    content TEXT NOT NULL,
    url VARCHAR(1000),
    published_at TIMESTAMP,
    embedding vector(384),  -- for all-MiniLM-L6-v2
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create index for vector similarity search
CREATE INDEX IF NOT EXISTS documents_embedding_idx
ON documents USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- Index for common queries
CREATE INDEX IF NOT EXISTS idx_player_stats_season ON player_season_stats(season);
CREATE INDEX IF NOT EXISTS idx_player_stats_player ON player_season_stats(player_id);
CREATE INDEX IF NOT EXISTS idx_game_logs_date ON game_logs(game_date);
CREATE INDEX IF NOT EXISTS idx_players_name ON players(name);
