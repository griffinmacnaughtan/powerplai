# PowerplAI Implementation Prompt

Use this prompt to guide implementation of the fantasy and betting features.
Feed it to Claude Code or any capable coding agent when starting each phase.

---

## Context Prompt (Include Every Time)

```
You are building features for PowerplAI, an NHL analytics platform.

Tech stack:
- Backend: Python 3.11+, FastAPI, SQLAlchemy async ORM, PostgreSQL 16 + pgvector
- Frontend: Next.js 14, TypeScript, Tailwind CSS
- AI: Claude API (Anthropic) for the copilot, sentence-transformers for RAG
- Infrastructure: Docker, Railway (backend), Vercel (frontend)

Existing architecture:
- Copilot agent (backend/src/agents/copilot.py) classifies queries, fetches
  context from DB, passes to Claude for synthesis
- Prediction engine (backend/src/agents/predictions.py) uses weighted factors
  to generate player scoring probabilities via Poisson conversion
- Prediction audit system (backend/src/agents/prediction_audit.py) logs
  predictions before games, validates after
- Data pipeline (backend/src/pipeline/) ingests from NHL API, MoneyPuck, ESPN
- Parlay tracker (backend/src/agents/parlay_tracker.py) generates and
  validates daily parlays
- RAG service (backend/src/agents/rag.py) does semantic search over
  hockey analytics articles
- Frontend chat interface with model performance modal

Database tables you can assume exist:
- players (id, nhl_id, name, position, team_abbrev, cap_hit_cents)
- player_season_stats (player_id, season, games_played, goals, assists, points, xg, corsi_for_pct)
- game_logs (player_id, game_id, game_date, goals, assists, points, shots, toi, powerplay_goals, etc.)
- games (game_id, game_date, home_team_abbrev, away_team_abbrev, home_score, away_score, is_completed)
- goalie_stats (player_id, season, save_pct, gaa, games_played)
- prediction_audit (game_date, player_name, prob_goal, prob_point, confidence, actual_goals, goal_hit, validated_at)
- daily_parlays (game_date, parlay_name, legs JSONB, combined_prob, result)
- documents (content, embedding vector[384], source)

Coding principles:
- No speculative abstractions. Build what's needed now.
- SQL queries use SQLAlchemy text() with parameterized queries.
- All predictions must be logged to prediction_audit before games start.
- Calibration is non-negotiable. Every probability must be validated against outcomes.
- Never present betting predictions as guaranteed. Always show probabilities.
- Test edge cases: no games today, player with no recent data, goalie unconfirmed.
```

---

## Phase A1: Yahoo Fantasy League Integration

```
Build Yahoo Fantasy Hockey API integration so users can connect their league.

Requirements:
1. OAuth2 flow:
   - Register app at https://developer.yahoo.com/apps/
   - Implement authorization code flow (not implicit)
   - Store access_token and refresh_token per user in a `user_tokens` table
   - Token refresh on 401 responses
   - Frontend: "Connect Yahoo League" button that initiates OAuth redirect

2. New database tables:
   - user_leagues: id, user_id, platform ('yahoo'|'espn'), external_league_id,
     league_name, scoring_format ('points'|'categories'|'roto'),
     scoring_rules (JSONB), roster_slots (JSONB), num_teams, synced_at
   - user_rosters: id, league_id, player_name, player_nhl_id (FK to players),
     roster_position, acquisition_type, acquired_at

3. Yahoo API endpoints to consume:
   - GET /fantasy/v2/users;use_login=1/games;game_keys=nhl/leagues
     → list user's leagues
   - GET /fantasy/v2/league/{league_key}/settings
     → scoring rules, roster positions, trade deadline
   - GET /fantasy/v2/league/{league_key}/standings
     → current standings
   - GET /fantasy/v2/league/{league_key}/players;status=A
     → available free agents
   - GET /fantasy/v2/team/{team_key}/roster
     → user's current roster
   - GET /fantasy/v2/league/{league_key}/scoreboard
     → current matchup scores

4. Sync service (backend/src/ingestion/fantasy_sync.py):
   - sync_league(user_id, league_key) → pulls settings, roster, standings
   - sync_free_agents(league_key) → pulls available players
   - Run on demand when user opens the app, cache for 15 minutes

5. API endpoints:
   - POST /api/fantasy/connect/yahoo → initiate OAuth
   - GET /api/fantasy/callback/yahoo → handle OAuth callback
   - GET /api/fantasy/leagues → list connected leagues
   - GET /api/fantasy/league/{id}/roster → user's roster with PowerplAI projections
   - GET /api/fantasy/league/{id}/free-agents → ranked free agents

6. Copilot integration:
   - New classification flag: is_fantasy_query
   - When user asks "should I start X or Y", check if they have a connected league
   - If yes, fetch their roster and scoring format, then recommend based on
     expected fantasy points in their format
   - If no league connected, give generic advice and suggest connecting

Do not build the ESPN integration yet. Yahoo first, ESPN in a separate phase.
Test with a real Yahoo Fantasy Hockey league.
```

---

## Phase A2: Scoring-Format-Aware Predictions

```
Extend the prediction engine to output expected fantasy points per player
based on a league's specific scoring format.

Requirements:
1. Add to predictions.py:
   - Method: calculate_fantasy_points(prediction: PlayerPrediction, scoring_rules: dict) -> float
   - Takes the existing prediction output (expected_goals, expected_assists,
     expected_shots, expected_points) and multiplies by the league's scoring weights
   - Example: if league scores Goals=3, Assists=2, Shots=0.5, PPP=1:
     expected_fp = expected_goals*3 + expected_assists*2 + expected_shots*0.5 + expected_pp_points*1

2. Extend PlayerPrediction dataclass:
   - Add expected_pp_points, expected_pim, expected_hits, expected_blocks
   - These require adding rolling averages for PIM, hits, blocks to the feature queries
   - Pull from game_logs (these columns already exist or need to be added to ingestion)

3. For categories leagues (H2H categories):
   - Instead of a single fantasy points number, return expected value per category
   - Compare to the user's current matchup to identify which categories to target
   - Method: get_category_impact(prediction, current_matchup_scores) -> dict
     Returns: {"goals": +0.4, "assists": +0.6, "shots": +3.2, "hits": +1.1, ...}

4. New copilot query type: FANTASY_START_SIT
   - "Should I start Necas or Nylander tonight?"
   - Fetch user's league scoring format
   - Calculate expected FP for both players
   - Recommend the higher one with explanation
   - If categories league, explain which categories each player helps

5. API endpoint:
   - GET /api/fantasy/league/{id}/projections/tonight
     → Returns user's roster with expected fantasy points for tonight,
       sorted by projected value, with start/sit recommendation
```

---

## Phase A3: Start/Sit Optimizer and Waiver Wire

```
Build the decision engine that turns predictions into actionable roster moves.

Requirements:
1. Start/Sit Optimizer (backend/src/agents/fantasy_optimizer.py):
   - Input: user's roster (all players), tonight's games, league settings
   - Constraint: must fill each roster slot (2C, 2LW, 2RW, 4D, 2G, 3BN)
   - Objective: maximize total expected fantasy points
   - Handle: players not playing tonight (off-night → bench automatically)
   - Handle: game-time decisions (flag uncertain starters)
   - Output: optimal lineup with reasoning per decision
   - Algorithm: simple greedy assignment (sort by expected FP, assign to
     matching position slots). No need for LP solver unless roster constraints
     get complex.

2. Waiver Wire Ranker (backend/src/agents/waiver_wire.py):
   - Input: free agent pool from Yahoo API, user's current roster
   - For each free agent: calculate expected remaining-season fantasy points
   - For each rostered bench player: same calculation
   - Rank add/drop pairs by marginal value: (FA expected FP) - (dropped player expected FP)
   - Filter: only suggest drops for bench players, never suggest dropping starters
   - Output: top 5 add/drop recommendations with reasoning
   - Factor in: remaining schedule (teams with more games = more value),
     positional eligibility, and whether the add fills a roster need

3. Streaming Planner:
   - For leagues allowing daily transactions
   - Look at the weekly schedule: which days does the user have empty roster spots?
   - Find free agents playing on those days with highest expected FP
   - Multi-day plan: "Add X for Tuesday, drop for Y on Thursday"
   - Max 2-3 transactions per day to stay practical

4. Copilot integration:
   - FANTASY_WAIVER: "who should I pick up?" → waiver wire ranker
   - FANTASY_LINEUP: "set my optimal lineup" → start/sit optimizer
   - FANTASY_STREAM: "streaming options this week?" → streaming planner

5. API endpoints:
   - GET /api/fantasy/league/{id}/optimize → optimal lineup
   - GET /api/fantasy/league/{id}/waivers → add/drop recommendations
   - GET /api/fantasy/league/{id}/streaming → weekly streaming plan
```

---

## Phase B1-B2: Betting Model (Feature Store + XGBoost)

```
Build a proper ML prediction model to replace the hand-weighted factors.
This is research — do not ship to users until Phase B3 validation passes.

Requirements:
1. Feature Store (backend/src/ml/feature_store.py):
   - Build a player_game_features table with one row per player-game
   - Features (keep it to 8-10, not more):
     * rolling_5g_gpg: goals per game, last 5 games
     * rolling_10g_ppg: points per game, last 10 games
     * season_shooting_pct: current season shooting %
     * shooting_pct_delta: season shooting % minus career shooting %
       (positive = running hot, negative = due for regression)
     * opponent_ga_rate: opponent's goals-against per game, last 20 games
     * goalie_sv_pct: confirmed opposing goalie's save % last 10 starts
     * is_home: boolean
     * is_back_to_back: boolean
     * on_pp1: boolean (is this player on the first power play unit?)
     * team_pp_pct: team's power play percentage this season
   - Labels: scored_goal (bool), recorded_point (bool), recorded_assist (bool)
   - Rebuild nightly after game log ingestion
   - Backfill for 3-5 seasons of historical data

2. Model Training (backend/src/ml/train.py):
   - XGBoost binary classifier for each target (goal, point, assist)
   - Split: train on seasons 1-3, validate on season 4, test on season 5
   - NEVER use test set for any tuning decisions
   - Hyperparameters: max_depth=4, min_child_weight=50, subsample=0.8,
     colsample_bytree=0.8, learning_rate=0.05
   - Early stopping on validation Brier score (patience=50 rounds)
   - Post-processing: isotonic regression calibration on validation predictions
   - Save: model artifacts + calibrator + feature list + metrics to
     backend/src/ml/models/ with version timestamp

3. Evaluation (backend/src/ml/evaluate.py):
   - Metrics on TEST set only:
     * Brier score (overall and per probability bucket)
     * Calibration plot (predicted vs actual, 10 buckets)
     * ROC AUC
     * Comparison to baselines:
       - Baseline 1: position-average scoring rate (constant)
       - Baseline 2: player's season scoring rate (naive)
       - Baseline 3: current hand-weighted model
     * If XGBoost doesn't beat Baseline 2 by >1% Brier score, stop and
       investigate before proceeding.
   - Feature importance (SHAP values on test set)
   - Save evaluation report as JSON artifact alongside model

4. Integration with existing prediction engine:
   - Add a model_version config flag
   - When set to "xgboost_v1", use the trained model instead of hand-weighted factors
   - When set to "weighted" (default), keep current behavior
   - Both paths must log to prediction_audit with model_version column
   - This allows A/B comparison on live predictions

5. Do NOT:
   - Add more than 10 features without justification
   - Use any feature derived from fewer than 20 games of data per player
   - Retrain more than monthly during the paper betting phase
   - Ship this to users before Phase B3 completes (minimum 1 season)
```

---

## Phase B3: Paper Betting Validation

```
Track model predictions against closing lines for a full NHL season.
This is the make-or-break phase.

Requirements:
1. Closing line ingestion:
   - Integrate The Odds API (https://the-odds-api.com/)
   - Pull NHL player prop odds (anytime goal scorer) and game moneylines
   - Store in an odds_history table:
     game_date, player_name, market (anytime_goal/moneyline), book,
     odds (American), implied_prob, captured_at
   - Capture at 2 times: when model prediction is generated AND at game start (closing)
   - Use closing line for evaluation

2. Paper bet tracking (backend/src/ml/paper_betting.py):
   - For each prediction where |model_prob - market_implied_prob| > 0.05:
     log a paper bet
   - paper_bets table: game_date, player_name, bet_type, model_prob,
     market_prob, edge (model - market), simulated_stake (1 unit flat),
     outcome (win/loss), pnl
   - Daily summary: bets placed, wins, losses, daily PnL, cumulative PnL

3. Evaluation dashboard (extend ModelPerformanceModal):
   - New tab: "vs Market"
   - Model Brier score vs market Brier score (running, per week)
   - Cumulative simulated PnL chart
   - Hit rate vs break-even rate
   - Prediction count toward the 1,500 minimum with progress bar
   - Statistical significance indicator (binomial test p-value)

4. Decision gates:
   - At 1,500 predictions: run full statistical evaluation
   - If model Brier < market Brier AND simulated ROI > 0 AND p < 0.05:
     → proceed to B4 (live betting with real money, small stakes)
   - If any condition fails:
     → diagnose, retrain with refined features, restart B3 counter
     → if it fails twice, acknowledge the market is efficient for this
       bet type and focus on fantasy (Track A)

5. Copilot integration:
   - When user asks about a bet ("is McDavid +170 good value?"):
     show model probability vs implied probability
   - Flag as "model edge" or "no edge" based on the difference
   - Always show: "Paper tracking: X/1500 predictions validated,
     current ROI: Y%, p-value: Z"
   - Never say "this is a lock" or "guaranteed" — always probabilities
```

---

## Integration Sequence

Build in this order. Each phase depends on the previous.

```
Month 1:  A1 (Yahoo integration) + B1 (feature store + backfill)
Month 2:  A2 (scoring-format predictions)
Month 3:  A3 (start/sit + waiver wire) → Fantasy MVP ships here
Month 4:  B2 (train XGBoost, evaluate, compare to baselines)
Month 5:  A4 (alerts) + B3 starts (paper betting begins)
Month 6+: B3 continues (full season of tracking)
Month 13: B3 evaluation gate → B4 or pivot to fantasy-only
```

For each phase, write tests. For ML phases, the test is the evaluation
report. For fantasy phases, test with a real Yahoo league.
