# PowerplAI Roadmap: Fantasy + Betting

Two tracks with different goals, different bars, and different timelines.
Fantasy is the product. Betting is the research arm.

---

## Track A: Fantasy Product (Primary — ship this)

The bar: beat 9-11 league-mates who use gut feel and ESPN blurbs.
This is achievable with the current prediction model plus a decision layer.

### A1. League Integration (Foundation)

Connect to the user's actual fantasy league so every recommendation
is personalized, not generic.

**Yahoo Fantasy API (primary target):**
- OAuth2 flow — user authorizes, you get league access
- Pull: roster, scoring settings, matchup schedule, free agent pool,
  transaction history, draft results
- Store in `user_leagues`, `user_rosters`, `league_settings` tables
- Refresh daily or on-demand

**ESPN Fantasy API (secondary):**
- Unofficial but stable — use `espn-api` Python library
- Public leagues: no auth needed (league ID only)
- Private leagues: requires `espn_s2` + `SWID` cookies from user
- Same data shape: rosters, settings, free agents, matchups

**Data model:**
```
user_leagues
├── id, user_id, platform (yahoo/espn), league_id
├── league_name, scoring_format (points/categories/roto)
├── scoring_rules (JSONB — goals=3, assists=2, etc.)
├── roster_slots (JSONB — C=2, LW=2, RW=2, D=4, G=2, BN=3)
├── num_teams, current_matchup_period
└── synced_at

user_rosters
├── league_id, player_name, player_nhl_id
├── roster_position (C1, LW1, BN2, IR)
├── acquisition_type (draft/waiver/trade)
└── acquired_at
```

### A2. Scoring-Format-Aware Predictions

The current model predicts goals/assists/points. But fantasy leagues
score differently, and the optimal strategy changes dramatically.

**Points leagues:** Maximize total expected fantasy points per night.
- Map model outputs to league scoring: `expected_fp = expected_goals * goals_weight + expected_assists * assists_weight + expected_shots * shots_weight + ...`
- Rank by expected fantasy points, not raw goal probability

**Categories leagues (H2H):** Win individual stat categories.
- Identify which categories are close in the current matchup
- Recommend starts that target winnable categories
- "You're down 12-10 in shots and tied in goals — start the volume shooter, not the sniper"

**Roto leagues:** Season-long category ranking.
- Different optimization: punting weak categories, stacking strong ones
- Trade suggestions factor in category balance

### A3. Decision Engine

This is where PowerplAI becomes indispensable. Not better predictions —
better decisions given the predictions.

**Start/Sit Optimizer:**
- Input: user's roster, tonight's games, league scoring format
- Enumerate all legal lineup combinations
- Score each by total expected fantasy points (or category coverage)
- Return optimal lineup with explanation
- Account for: off-nights (some players don't play), game time certainty,
  injury game-time decisions

**Waiver Wire Ranker:**
- Compare available free agents to user's worst rostered players
- Rank add/drop candidates by marginal value over replacement
- "Drop [worst bench player] for [best available] — nets +2.3 expected FP/week"
- Factor in remaining schedule (teams with more games this week = more value)

**Streaming Planner:**
- For leagues that allow daily transactions
- Identify players on teams with favorable schedules (off-nights when
  your starters don't play, weak opponents, backup goalies)
- Multi-day optimization: "Pick up Player A for Tue/Thu, drop for Player B Fri/Sat"

**Trade Evaluator:**
- Player value = expected remaining fantasy points for the season,
  given schedule, league format, and positional scarcity
- Compare both sides of a proposed trade
- Account for: buy-low/sell-high (recent form vs season projection),
  positional need, playoff schedule (do they play 4 games in your
  fantasy playoff week?)

### A4. Alerts and Automation

**Pre-game alerts (push or in-app):**
- Goalie confirmation → lineup adjustment needed?
- Injury scratch → replacement recommendation
- Line promotion → breakout candidate tonight

**Weekly digest:**
- Matchup preview: strengths, weaknesses, streaming targets
- Waiver wire priority list
- Trade targets based on roster needs

### A5. Social / League Features

- Shareable pick cards (image export for group chats)
- League leaderboard of PowerplAI users (opt-in)
- "Was PowerplAI right?" — track start/sit accuracy per user

---

## Track B: Betting Research (Secondary — earn the right)

The bar: beat closing lines over 2,000+ predictions across 2 seasons.
Until you clear that bar, this is a research project, not a product.

### B1. Data Foundation

**Keep the feature set tight. 8-10 high-signal features, not 40.**

Features that survive scrutiny (real effect, adequate sample size):
1. Player rolling scoring rates (5-game, 10-game, season)
2. Season shooting percentage vs career average (regression signal)
3. Opponent goals-against rate (team defensive quality)
4. Confirmed goalie starter + recent save percentage
5. Home/away adjustment
6. Rest advantage (back-to-back yes/no, not travel distance km)
7. Power play deployment (PP1 unit, yes/no + team PP%)
8. Vegas team total (implied total goals for the game)

Features that sound good but fail scrutiny — **do not include:**
- Referee assignment (effect size below noise floor)
- Travel distance in km (back-to-back flag captures this adequately)
- Line combination chemistry (sample sizes too small within a season)
- Score state splits (25-30 games per state per player = noise)
- Venue-adjusted stats (effect is real but tiny and already in the line)

**Vegas lines as a feature, not just a benchmark:**
- The single most predictive input for any sports model is the market price itself.
  If the implied team total is 3.5, that tells you more about tonight's game than
  any feature you'll engineer.
- Use Vegas team totals as a feature in your model. Your job is then to find
  the *residual* — what the market is missing.

**Data sources:**
- NHL API: play-by-play, game logs, rosters (free, reliable)
- MoneyPuck: xG, Corsi, Fenwick (free)
- The Odds API: historical closing lines ($50-150/mo for NHL)
- Morning skate reports: goalie confirmations (scrape or manual)

**Feature store:**
```
player_game_features
├── player_id, game_id, game_date
├── rolling_5g_goals_per_game, rolling_10g_points_per_game
├── season_shooting_pct, career_shooting_pct, shooting_pct_delta
├── opponent_ga_per_game_last_20
├── opponent_goalie_sv_pct_last_10
├── is_home, is_back_to_back
├── on_pp1 (bool), team_pp_pct
├── vegas_team_total
├── label_scored (bool), label_point (bool), label_assist (bool)
```

One row per player-game. Rebuild nightly. ~50K rows per season.

### B2. Model v1 — Conservative and Calibrated

**Architecture: XGBoost with heavy regularization.**

- `max_depth=4` (prevent overfitting to interactions)
- `min_child_weight=50` (no predictions from tiny leaf nodes)
- `subsample=0.8, colsample_bytree=0.8` (feature/row noise injection)
- Early stopping on validation Brier score

**Training protocol:**
- Train on seasons S1-S3
- Validate on season S4 (hyperparameter tuning, early stopping)
- Test on season S5 (NEVER touch until final evaluation)
- Retrain annually, not weekly. The NHL doesn't change week to week.

**Separate models for:**
- Forward goal scoring (P >= 1 goal)
- Defenseman goal scoring (different base rate, different features)
- Point recording (P >= 1 point)
- Assist recording (P >= 1 assist)
- Team win (for moneyline legs)

**Mandatory post-processing:**
- Isotonic regression calibration on the validation set
- Calibration plot must show predicted vs actual within 3% per bucket
  before the model goes anywhere near production

**Baseline comparison before anything else:**
- Baseline 1: league-average scoring rate for that position (constant model)
- Baseline 2: player's season scoring rate (naive per-player model)
- If XGBoost doesn't beat Baseline 2 by >1% Brier score, stop. You don't
  have a model, you have overfitting.

### B3. Paper Betting (Minimum 1 Full Season)

**Do not bet real money until this phase is complete.**

- Log every prediction with timestamp before games start
- Compare to closing lines from The Odds API
- Track on a dashboard:
  - Model Brier score vs market Brier score (by prediction type)
  - ROI on simulated 1-unit flat bets where model disagrees with market by >5%
  - Calibration plot updated weekly
  - Cumulative P&L curve with confidence intervals

**Statistical significance:**
- Minimum 1,500 predictions before evaluating
- Use a one-sided binomial test: is your hit rate significantly above
  the break-even rate (including vig)?
- p < 0.05 or don't proceed. Anything above that could be variance.

**What you're looking for:**
- Consistent edge in a *specific* market (goal scorers, assists, team ML)
- Not edge everywhere — that's suspicious. Real edge is narrow.
- Stable calibration — no big swings between months

### B4. Live Betting (Only if B3 Passes)

**If after 1,500+ paper predictions you have a statistically significant edge:**

- Start with 1% fractional Kelly sizing (not full Kelly — never full Kelly)
- Cap exposure at 3% of bankroll per day
- Diversify across books (FanDuel, DraftKings, BetMGM) to avoid limits
- Track actual P&L separately from paper P&L
- Monthly review: if real P&L deviates significantly from paper model,
  investigate execution issues (line movement, limits, timing)

**Bankroll rules:**
- Stop-loss: if bankroll drops 25% from peak, pause and re-evaluate
- Never chase losses by increasing bet size
- Separate betting bankroll from personal finances entirely

### B5. Ongoing Evaluation

- Retrain model annually on expanded dataset
- If edge disappears for 3+ consecutive months, assume the market
  has adapted and go back to paper betting
- Track feature importance drift — if a feature stops being important,
  investigate why (rule change? market adaptation?)

---

## Shared Infrastructure (Both Tracks)

### Data Pipeline
- APScheduler (already in place) orchestrates daily ingestion
- NHL API refresh: rosters, game logs, schedule at 6 AM
- Goalie confirmations: 11 AM (morning skate) and 5 PM (final)
- Feature store rebuild: after game log ingestion
- Prediction generation: 2 hours before first puck drop
- Validation: morning after games complete

### Prediction Audit System
- Already built (prediction_audit table)
- Extend to track: model version, feature set hash, market odds at prediction time
- This is the backbone of B3 paper betting

### Copilot Integration
- Fantasy queries route through the decision engine (A3)
- Betting queries show model probability vs market odds and flag value
- All responses cite data sources and confidence level (from calibrated model)
- Never present a bet as "guaranteed" or "lock" — always probabilities

---

## Timeline (Realistic)

**Months 1-2:** A1 (Yahoo league integration) + B1 (feature store)
**Months 3-4:** A2 (scoring-format predictions) + A3 (start/sit optimizer)
**Month 5:** A4 (alerts) + B2 (model v1 trained and calibrated)
**Months 6-12:** A5 (social features) + B3 (paper betting — full season)
**Month 13+:** B4 (live betting, only if B3 passes)

Fantasy ships in month 3. Betting doesn't ship until month 13 at the earliest.

---

## Success Metrics

**Fantasy track:**
- User's start/sit accuracy vs league-mates (measurable via league standings)
- Waiver wire pickup success rate (picked-up players outperform dropped players)
- User retention: do they come back every week?

**Betting track:**
- Model Brier score vs closing line Brier score (must be lower)
- Simulated ROI on flat-bet strategy (must be positive after vig)
- Statistical significance (p < 0.05 on 1,500+ predictions)
- If any of these fail after a full season, pivot to fantasy-only. That's not
  failure — that's intellectual honesty.
