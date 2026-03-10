# PowerplAI Data Dictionary

This document describes the database schema, field definitions, and data sources for the PowerplAI platform.

---

## Database Overview

- **Database**: PostgreSQL 16 with pgvector extension
- **Connection**: AsyncPG for async operations
- **ORM**: SQLAlchemy 2.0 with mapped classes

---

## Core Tables

### players

Player registry with biographical information.

| Column | Type | Description | Source |
|--------|------|-------------|--------|
| `id` | SERIAL | Internal primary key | Auto-generated |
| `nhl_id` | INTEGER | Official NHL player ID | NHL API |
| `name` | VARCHAR(255) | Full name | NHL API |
| `position` | VARCHAR(10) | Position code (C, LW, RW, D, G) | NHL API |
| `team_abbrev` | VARCHAR(10) | Current team abbreviation | NHL API |
| `birth_date` | DATE | Date of birth | NHL API |
| `shoots_catches` | VARCHAR(1) | Handedness (L/R) | NHL API |
| `height_inches` | INTEGER | Height in inches | NHL API |
| `weight_lbs` | INTEGER | Weight in pounds | NHL API |
| `cap_hit_cents` | BIGINT | Cap hit in cents (e.g., 1250000000 = $12.5M) | PuckPedia |
| `contract_expiry` | INTEGER | Contract expiry year | PuckPedia |
| `created_at` | TIMESTAMP | Record creation time | System |
| `updated_at` | TIMESTAMP | Last update time | System |

**Indexes**: `idx_players_name`, `idx_players_nhl_id`

---

### teams

Team registry with conference/division information.

| Column | Type | Description | Source |
|--------|------|-------------|--------|
| `id` | SERIAL | Internal primary key | Auto-generated |
| `nhl_id` | INTEGER | Official NHL team ID | NHL API |
| `name` | VARCHAR(255) | Full team name | NHL API |
| `abbrev` | VARCHAR(10) | Team abbreviation (TOR, BOS, etc.) | NHL API |
| `conference` | VARCHAR(50) | Eastern/Western | NHL API |
| `division` | VARCHAR(50) | Atlantic, Metropolitan, Central, Pacific | NHL API |
| `created_at` | TIMESTAMP | Record creation time | System |

---

### player_season_stats

Aggregated season statistics per player.

| Column | Type | Description | Source |
|--------|------|-------------|--------|
| `id` | SERIAL | Internal primary key | Auto-generated |
| `player_id` | INTEGER | FK to players.id | System |
| `season` | VARCHAR(10) | Season code (e.g., "20252026") | NHL API |
| `team_abbrev` | VARCHAR(10) | Team played for | NHL API |
| `games_played` | INTEGER | Games played | NHL API |
| `goals` | INTEGER | Goals scored | NHL API |
| `assists` | INTEGER | Assists | NHL API |
| `points` | INTEGER | Total points (G + A) | NHL API |
| `plus_minus` | INTEGER | Plus/minus rating | NHL API |
| `pim` | INTEGER | Penalty minutes | NHL API |
| `shots` | INTEGER | Shots on goal | NHL API |
| `shooting_pct` | DECIMAL(5,2) | Shooting percentage | Calculated |
| `toi_per_game` | DECIMAL(6,2) | Average TOI in minutes | NHL API |
| `xg` | DECIMAL(6,2) | Expected goals | MoneyPuck |
| `xg_per_60` | DECIMAL(6,3) | xG per 60 minutes | MoneyPuck |
| `corsi_for_pct` | DECIMAL(5,2) | Corsi For % (shot attempts) | MoneyPuck |
| `fenwick_for_pct` | DECIMAL(5,2) | Fenwick For % (unblocked shots) | MoneyPuck |
| `created_at` | TIMESTAMP | Record creation time | System |

**Indexes**: `idx_player_stats_season`, `idx_player_stats_player`

**Unique Constraint**: `(player_id, season)`

---

### game_logs

Per-game player performance records.

| Column | Type | Description | Source |
|--------|------|-------------|--------|
| `id` | SERIAL | Internal primary key | Auto-generated |
| `player_id` | INTEGER | FK to players.id | System |
| `game_id` | INTEGER | NHL game ID | NHL API |
| `game_date` | DATE | Game date | NHL API |
| `season` | VARCHAR(10) | Season code | NHL API |
| `team_abbrev` | VARCHAR(10) | Player's team | NHL API |
| `opponent` | VARCHAR(10) | Opponent team abbreviation | NHL API |
| `home_away` | VARCHAR(4) | "home" or "away" | NHL API |
| `goals` | INTEGER | Goals scored | NHL API |
| `assists` | INTEGER | Assists | NHL API |
| `points` | INTEGER | Total points | NHL API |
| `shots` | INTEGER | Shots on goal | NHL API |
| `toi` | DECIMAL(6,2) | Time on ice in minutes | NHL API |
| `plus_minus` | INTEGER | Plus/minus | NHL API |
| `pim` | INTEGER | Penalty minutes | NHL API |
| `powerplay_goals` | INTEGER | Power play goals | NHL API |
| `powerplay_points` | INTEGER | Power play points | NHL API |
| `shorthanded_goals` | INTEGER | Shorthanded goals | NHL API |
| `shorthanded_points` | INTEGER | Shorthanded points | NHL API |
| `game_winning_goals` | INTEGER | Game-winning goals | NHL API |
| `overtime_goals` | INTEGER | Overtime goals | NHL API |
| `shifts` | INTEGER | Number of shifts | NHL API |
| `created_at` | TIMESTAMP | Record creation time | System |

**Indexes**: `idx_game_logs_date`, `idx_game_logs_player`, `idx_game_logs_opponent`

**Unique Constraint**: `(player_id, game_id)`

---

### games

Game schedule and results.

| Column | Type | Description | Source |
|--------|------|-------------|--------|
| `id` | SERIAL | Internal primary key | Auto-generated |
| `nhl_game_id` | INTEGER | Official NHL game ID | NHL API |
| `season` | VARCHAR(10) | Season code | NHL API |
| `game_type` | INTEGER | 1=preseason, 2=regular, 3=playoffs | NHL API |
| `game_date` | DATE | Game date (local time) | NHL API |
| `start_time_utc` | TIMESTAMP | Game start time in UTC | NHL API |
| `venue` | VARCHAR(255) | Arena name | NHL API |
| `home_team_abbrev` | VARCHAR(10) | Home team | NHL API |
| `away_team_abbrev` | VARCHAR(10) | Away team | NHL API |
| `home_score` | INTEGER | Home team final score | NHL API |
| `away_score` | INTEGER | Away team final score | NHL API |
| `game_state` | VARCHAR(10) | FUT, LIVE, FINAL, OFF | NHL API |
| `is_completed` | BOOLEAN | True if game is final | NHL API |
| `created_at` | TIMESTAMP | Record creation time | System |
| `updated_at` | TIMESTAMP | Last update time | System |

**Indexes**: `idx_games_date`, `idx_games_teams`

**Unique Constraint**: `nhl_game_id`

---

### goalie_stats

Goaltender season statistics.

| Column | Type | Description | Source |
|--------|------|-------------|--------|
| `id` | SERIAL | Internal primary key | Auto-generated |
| `player_id` | INTEGER | FK to players.id | System |
| `season` | VARCHAR(10) | Season code | NHL API |
| `team_abbrev` | VARCHAR(10) | Team | NHL API |
| `games_played` | INTEGER | Games played | NHL API |
| `games_started` | INTEGER | Games started | NHL API |
| `wins` | INTEGER | Wins | NHL API |
| `losses` | INTEGER | Losses | NHL API |
| `ot_losses` | INTEGER | Overtime losses | NHL API |
| `save_pct` | DECIMAL(5,3) | Save percentage (e.g., 0.915) | NHL API |
| `gaa` | DECIMAL(4,2) | Goals against average | NHL API |
| `shutouts` | INTEGER | Shutouts | NHL API |
| `saves` | INTEGER | Total saves | NHL API |
| `shots_against` | INTEGER | Total shots faced | NHL API |
| `recent_save_pct` | DECIMAL(5,3) | Save % last 5 games | Calculated |
| `created_at` | TIMESTAMP | Record creation time | System |

---

### injuries

Active injury reports.

| Column | Type | Description | Source |
|--------|------|-------------|--------|
| `id` | SERIAL | Internal primary key | Auto-generated |
| `player_id` | INTEGER | FK to players.id | System |
| `status` | VARCHAR(50) | Injury status (IR, DTD, Out) | ESPN |
| `injury_type` | VARCHAR(100) | Type of injury | ESPN |
| `injury_detail` | TEXT | Additional details | ESPN |
| `expected_return` | DATE | Expected return date | ESPN |
| `is_active` | BOOLEAN | Currently injured | System |
| `created_at` | TIMESTAMP | When injury was reported | System |
| `updated_at` | TIMESTAMP | Last status update | System |

**Indexes**: `idx_injuries_active`, `idx_injuries_player`

---

### documents

RAG knowledge base for semantic search.

| Column | Type | Description | Source |
|--------|------|-------------|--------|
| `id` | SERIAL | Internal primary key | Auto-generated |
| `title` | VARCHAR(500) | Document title | Various |
| `source` | VARCHAR(255) | Source identifier | Various |
| `content` | TEXT | Full document content | Various |
| `url` | VARCHAR(1000) | Source URL | Various |
| `published_at` | TIMESTAMP | Publication date | Various |
| `embedding` | VECTOR(384) | MiniLM-L6-v2 embedding | Generated |
| `metadata` | JSONB | Additional metadata | Various |
| `created_at` | TIMESTAMP | Record creation time | System |

**Indexes**: `documents_embedding_idx` (IVFFlat with 100 lists)

---

### prediction_audit

Tracks predictions for validation and model evaluation.

| Column | Type | Description | Source |
|--------|------|-------------|--------|
| `id` | SERIAL | Internal primary key | Auto-generated |
| `player_id` | INTEGER | FK to players.id | System |
| `game_date` | DATE | Predicted game date | System |
| `opponent` | VARCHAR(10) | Opponent team | System |
| `prob_goal` | DECIMAL(5,4) | Predicted P(goal) | Model |
| `prob_point` | DECIMAL(5,4) | Predicted P(point) | Model |
| `expected_goals` | DECIMAL(4,2) | Expected goals | Model |
| `expected_points` | DECIMAL(4,2) | Expected points | Model |
| `confidence_score` | DECIMAL(4,3) | Model confidence | Model |
| `actual_goals` | INTEGER | Actual goals (post-game) | NHL API |
| `actual_points` | INTEGER | Actual points (post-game) | NHL API |
| `validated` | BOOLEAN | Has outcome been recorded | System |
| `model_version` | VARCHAR(20) | Model version used | System |
| `created_at` | TIMESTAMP | Prediction creation time | System |

---

## Metric Definitions

### Expected Goals (xG)

**Definition**: The probability that a shot will become a goal, based on shot location, type, angle, distance, and game context.

**Range**: 0.0 - 1.0 per shot (aggregated to season totals)

**Source**: MoneyPuck

**Usage**:
- `xG > Goals`: Player underperforming (unlucky or poor finishing)
- `xG < Goals`: Player overperforming (lucky or elite finishing)

---

### Corsi For Percentage (CF%)

**Definition**: Shot attempt differential when a player is on ice.

**Formula**: `CF% = (Shot Attempts For) / (Shot Attempts For + Shot Attempts Against) * 100`

**Range**: 0-100% (league average ~50%)

**Interpretation**:
- `> 55%`: Excellent possession
- `50-55%`: Above average
- `45-50%`: Below average
- `< 45%`: Poor possession

---

### Fenwick For Percentage (FF%)

**Definition**: Like Corsi, but excludes blocked shots.

**Formula**: `FF% = (Shots + Missed Shots For) / (Shots + Missed Shots Total) * 100`

**Rationale**: Blocked shots may indicate defensive positioning rather than possession.

---

### PDO

**Definition**: "Luck" indicator combining shooting percentage and save percentage.

**Formula**: `PDO = (Team Shooting % + Team Save %) * 10`

**Range**: Typically 97-103 (regresses to 100 over time)

**Usage**:
- `> 102`: Team likely overperforming
- `< 98`: Team likely underperforming

---

### WAR (Wins Above Replacement)

**Definition**: Estimated wins a player adds compared to a replacement-level player.

**Source**: MoneyPuck (calculated using xG, defensive contributions, penalties)

**Range**: -3 to +5 per season for skaters

---

## Data Flow

```
NHL API ──────────────►┐
                       │
MoneyPuck CSVs ────────┼──► Ingestion ──► Validation ──► PostgreSQL
                       │    Pipelines      Checks
ESPN API ──────────────┤
                       │
PuckPedia (scrape) ────┘
```

### Update Frequencies

| Data Type | Frequency | Pipeline |
|-----------|-----------|----------|
| Schedule | Hourly | `schedule_sync` |
| Game Logs | Daily | `game_logs` |
| Advanced Stats | Daily | `advanced_stats` |
| Injuries | Hourly | `injuries` |
| Salary Cap | Weekly | `salary_cap` |

---

## Validation Rules

### Game Logs
- `goals <= 10` per game (error if exceeded)
- `assists <= 10` per game (error if exceeded)
- `points = goals + assists` (consistency check)
- `toi <= 40.0` minutes (warning if exceeded)

### Season Stats
- `goals <= 100` per season
- `assists <= 150` per season
- `games_played <= 100` (including playoffs)
- `corsi_for_pct` between 0-100

### Data Freshness
- Schedule data < 2 hours old
- Injury data < 4 hours old
- Season stats < 24 hours old
