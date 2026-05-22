const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

/**
 * Result type for endpoints that can fail in a way the UI needs to distinguish
 * (e.g. network down vs server saying "no playoffs active").
 *
 * The existing per-method `try { } catch { return empty }` pattern collapses
 * both into the same empty shape, making it impossible to render a "backend
 * unreachable" toast vs "no data yet" empty state. This wrapper preserves
 * the distinction without forcing every caller to update at once.
 */
export type ApiOutcome<T> =
  | { ok: true; data: T }
  | { ok: false; reason: 'offline' | 'server_error' | 'timeout'; status?: number; message?: string }

export type BracketSeries = {
  team_a: string
  team_b: string
  team_a_wins: number
  team_b_wins: number
  games_played: number
  status: 'in_progress' | 'scheduled' | 'complete'
  winner: string | null
  next_game_date: string | null
  next_game_time: string | null
  last_game_date?: string | null
  conference?: 'east' | 'west' | null
  round?: 1 | 2 | 3 | 4 | null
}

export type ConferenceBracket = {
  round_1: BracketSeries[]
  round_2: BracketSeries[]
  conference_final: BracketSeries | null
  champion: string | null
}

export type PickLockStatus = 'open' | 'locking_soon' | 'locked' | 'live' | 'final'

/**
 * Tiny fetch wrapper used by the new ApiOutcome helpers. Adds:
 *  - a 15s timeout (most reads should be sub-second; 15s is generous for
 *    Railway cold starts but short enough to fail fast on network issues)
 *  - a single retry on transient 5xx with 500ms backoff
 *  - origin-friendly error categorisation
 */
async function fetchWithRetry(
  url: string,
  init: RequestInit = {},
  { retries = 1, timeoutMs = 15000 }: { retries?: number; timeoutMs?: number } = {},
): Promise<Response> {
  let lastErr: unknown
  for (let attempt = 0; attempt <= retries; attempt++) {
    const controller = new AbortController()
    const t = setTimeout(() => controller.abort(), timeoutMs)
    try {
      const r = await fetch(url, { ...init, signal: controller.signal })
      clearTimeout(t)
      // Retry once on transient 5xx
      if (r.status >= 500 && r.status < 600 && attempt < retries) {
        await new Promise(res => setTimeout(res, 500))
        continue
      }
      return r
    } catch (e) {
      clearTimeout(t)
      lastErr = e
      if (attempt < retries) {
        await new Promise(res => setTimeout(res, 500))
        continue
      }
    }
  }
  throw lastErr ?? new Error('fetch failed')
}

export interface ChatHistoryMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ImageAttachment {
  data: string       // base64-encoded image data (no data-URI prefix)
  media_type: string // e.g. "image/png", "image/jpeg"
  name: string       // original filename
}

export interface QueryResponse {
  response: string
  sources: Array<{ type: string; data: string }>
  query_type: string
}

export interface PlayerStats {
  name: string
  position: string
  team_abbrev: string
  season: string
  games_played: number
  goals: number
  assists: number
  points: number
  xg: number | null
  corsi_for_pct: number | null
}

export interface LeaderboardEntry {
  rank: number
  player: PlayerStats
}

export interface Prediction {
  player_name: string
  team: string
  opponent: string
  is_home: boolean
  prob_goal: number
  prob_point: number
  prob_multi_point: number
  expected_goals: number
  expected_assists: number
  expected_points: number
  confidence: 'high' | 'medium' | 'low'
  confidence_score: number
  factors: string[]
}

export interface MatchupPrediction {
  game_date: string
  home_team: string
  away_team: string
  venue: string | null
  start_time: string | null
  home_players: Prediction[]
  away_players: Prediction[]
  top_scorers: Prediction[]
  expected_total_goals: number
  pace_rating: 'high' | 'average' | 'low'
  home_goalie?: { name: string; save_pct: number }
  away_goalie?: { name: string; save_pct: number }
}

export interface Game {
  game_id: number
  date: string
  start_time: string | null
  home_team: string
  away_team: string
  home_score: number | null
  away_score: number | null
  state: string
  venue: string | null
}

export interface CalibrationBucket {
  predicted: number
  actual: number
  sample_size: number
  calibrated: boolean
}

export interface ModelEvaluation {
  status: string
  metrics: {
    total_predictions: number
    classification_metrics: {
      accuracy: number
      precision: number
      recall: number
      f1_score: number
    }
    probabilistic_metrics: {
      brier_score: number
      log_loss: number
      roc_auc: number | null
    }
    calibration: {
      expected_calibration_error: number
      buckets: Array<{
        range: string
        predictions: number
        actual_rate: number
        expected_rate: number
        error: number
      }>
    }
    baseline_comparison: {
      baseline_accuracy: number
      baseline_brier: number
      improvement_pct: number
    }
  }
  interpretation: Record<string, string>
}

class PowerplAIAPI {
  private baseUrl: string

  constructor(baseUrl: string = API_BASE) {
    this.baseUrl = baseUrl
  }

  async query(
    query: string,
    includeRag: boolean = true,
    messages: ChatHistoryMessage[] = [],
    images: ImageAttachment[] = []
  ): Promise<QueryResponse> {
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), 120000) // 2 minute timeout

    try {
      const response = await fetch(`${this.baseUrl}/api/query`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          query,
          include_rag: includeRag,
          messages,
          images,
        }),
        signal: controller.signal,
      })

      clearTimeout(timeoutId)

      if (!response.ok) {
        const text = await response.text()
        throw new Error(`API error ${response.status}: ${text}`)
      }

      return response.json()
    } catch (error) {
      clearTimeout(timeoutId)
      if (error instanceof Error) {
        if (error.name === 'AbortError') {
          throw new Error('Request timed out - the query took too long')
        }
        throw error
      }
      throw new Error('Failed to fetch')
    }
  }

  async getPlayer(playerName: string, season?: string): Promise<PlayerStats> {
    const params = new URLSearchParams()
    if (season) params.set('season', season)

    const response = await fetch(
      `${this.baseUrl}/api/players/${encodeURIComponent(playerName)}?${params}`,
      { method: 'GET' }
    )

    if (!response.ok) {
      throw new Error(`API error: ${response.status}`)
    }

    return response.json()
  }

  async getLeaders(
    stat: 'goals' | 'assists' | 'points' | 'xg' | 'corsi_for_pct',
    season?: string,
    limit: number = 10
  ): Promise<LeaderboardEntry[]> {
    const params = new URLSearchParams()
    if (season) params.set('season', season)
    params.set('limit', limit.toString())

    const response = await fetch(
      `${this.baseUrl}/api/leaders/${stat}?${params}`,
      { method: 'GET' }
    )

    if (!response.ok) {
      throw new Error(`API error: ${response.status}`)
    }

    return response.json()
  }

  async healthCheck(): Promise<{ status: string; service: string }> {
    const response = await fetch(`${this.baseUrl}/health`)
    return response.json()
  }

  async getTodaysGames(): Promise<{ date: string; games: Game[] }> {
    const response = await fetch(`${this.baseUrl}/api/games/today`)
    if (!response.ok) {
      throw new Error(`API error: ${response.status}`)
    }
    return response.json()
  }

  async getPredictionsTonight(): Promise<MatchupPrediction[]> {
    const response = await fetch(`${this.baseUrl}/api/predictions/tonight`)
    if (!response.ok) {
      throw new Error(`API error: ${response.status}`)
    }
    return response.json()
  }

  async getMatchupPrediction(homeTeam: string, awayTeam: string): Promise<MatchupPrediction> {
    const response = await fetch(
      `${this.baseUrl}/api/predictions/matchup/${homeTeam}/${awayTeam}`
    )
    if (!response.ok) {
      throw new Error(`API error: ${response.status}`)
    }
    return response.json()
  }

  async getModelEvaluation(startDate?: string, endDate?: string): Promise<ModelEvaluation> {
    const params = new URLSearchParams()
    if (startDate) params.set('start_date', startDate)
    if (endDate) params.set('end_date', endDate)

    const response = await fetch(`${this.baseUrl}/api/model/evaluation?${params}`)
    if (!response.ok) {
      throw new Error(`API error: ${response.status}`)
    }
    return response.json()
  }

  async getCalibrationChart(
    startDate?: string,
    endDate?: string
  ): Promise<{
    title: string
    goal_calibration: CalibrationBucket[]
    brier_score: number
    interpretation: string
  }> {
    const params = new URLSearchParams()
    if (startDate) params.set('start_date', startDate)
    if (endDate) params.set('end_date', endDate)

    const response = await fetch(`${this.baseUrl}/api/audit/calibration-chart?${params}`)
    if (!response.ok) {
      throw new Error(`API error: ${response.status}`)
    }
    return response.json()
  }

  async getModelInfo(): Promise<{
    model_type: string
    description: string
    version: string
    weights: Record<string, number>
    features: string[]
    note: string
  }> {
    const response = await fetch(`${this.baseUrl}/api/model/info`)
    if (!response.ok) {
      throw new Error(`API error: ${response.status}`)
    }
    return response.json()
  }

  async getDataStats(): Promise<Record<string, unknown>> {
    const response = await fetch(`${this.baseUrl}/api/validation/stats`)
    if (!response.ok) {
      throw new Error(`API error: ${response.status}`)
    }
    return response.json()
  }

  async submitFeedback(payload: {
    feedback_type: 'thumbs_up' | 'thumbs_down'
    query_type?: string
    category?: string
    comment?: string
    response_preview?: string
  }): Promise<void> {
    await fetch(`${this.baseUrl}/api/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
  }

  async getParlayRecord(days = 30): Promise<{
    period_days: number
    by_type: Array<{
      parlay_name: string
      total: number
      wins: number
      losses: number
      win_rate: string
      avg_legs_hit_pct: string
      avg_model_prob_pct: string
    }>
    recent: Array<{
      date: string
      name: string
      combined_prob: string
      result: string | null
      legs_hit: number | null
      legs_total: number | null
    }>
  }> {
    try {
      const response = await fetch(`${this.baseUrl}/api/parlays/record?days=${days}`)
      if (!response.ok) return { period_days: days, by_type: [], recent: [] }
      return response.json()
    } catch {
      return { period_days: days, by_type: [], recent: [] }
    }
  }

  async getPicksHistory(days = 30): Promise<{
    picks: Array<{
      game_date: string
      game_type: string
      player_name: string
      team: string
      opponent: string
      is_home: boolean
      prob_goal: number
      prob_point: number
      confidence: string
      confidence_score: number
      actual_goals: number | null
      actual_assists: number | null
      actual_points: number | null
      goal_hit: boolean | null
      point_hit: boolean | null
      validated: boolean
    }>
    parlays: Array<{
      game_date: string
      name: string
      legs: Array<{
        leg_type: string
        player_name: string | null
        team: string
        opponent: string | null
        probability: number
        hit: boolean | null
      }>
      combined_prob: number
      result: string
      legs_hit: number | null
      legs_total: number | null
    }>
    summary: {
      total: number
      validated: number
      goal_hits: number
      point_hits: number
      goal_hit_rate: number | null
      point_hit_rate: number | null
    }
  }> {
    try {
      const response = await fetch(`${this.baseUrl}/api/audit/picks?days=${days}`)
      if (!response.ok) return { picks: [], parlays: [], summary: { total: 0, validated: 0, goal_hits: 0, point_hits: 0, goal_hit_rate: null, point_hit_rate: null } }
      return response.json()
    } catch {
      return { picks: [], parlays: [], summary: { total: 0, validated: 0, goal_hits: 0, point_hits: 0, goal_hit_rate: null, point_hit_rate: null } }
    }
  }

  async getPlayoffStatus(): Promise<{
    is_active: boolean
    season: string | null
    latest_game_date?: string | null
    next_game_date?: string | null
  }> {
    const empty = {
      is_active: false,
      season: null,
      latest_game_date: null,
      next_game_date: null,
    }
    try {
      const r = await fetch(`${this.baseUrl}/api/playoffs/status`)
      if (!r.ok) return empty
      return r.json()
    } catch {
      return empty
    }
  }

  async getPlayoffBracket(): Promise<{
    season: string | null
    round: number
    series: Array<BracketSeries>
    bracket_view?: {
      championship: BracketSeries | null
      east: ConferenceBracket
      west: ConferenceBracket
    }
  }> {
    try {
      const r = await fetch(`${this.baseUrl}/api/playoffs/bracket`)
      if (!r.ok) return { season: null, round: 0, series: [] }
      return r.json()
    } catch {
      return { season: null, round: 0, series: [] }
    }
  }

  async getPlayoffOverview(): Promise<{
    season: string | null
    games_completed: number
    avg_goals_per_game: number
    total_goals: number
    top_scorers: Array<{
      player_id: number
      name: string
      team: string
      games: number
      goals: number
      assists: number
      points: number
      ppg: number
    }>
    hottest_teams: Array<{
      team: string
      games: number
      wins: number
      losses: number
      goal_diff: number
    }>
  }> {
    try {
      const r = await fetch(`${this.baseUrl}/api/playoffs/overview`)
      if (!r.ok) {
        return {
          season: null,
          games_completed: 0,
          avg_goals_per_game: 0,
          total_goals: 0,
          top_scorers: [],
          hottest_teams: [],
        }
      }
      return r.json()
    } catch {
      return {
        season: null,
        games_completed: 0,
        avg_goals_per_game: 0,
        total_goals: 0,
        top_scorers: [],
        hottest_teams: [],
      }
    }
  }

  async getPlayoffBestBets(days = 3, topN = 8): Promise<{
    start_date: string
    days: number
    is_playoffs: boolean
    games: number
    picks: Array<{
      player_name: string
      team: string
      opponent: string
      is_home: boolean
      game_date: string
      start_time: string | null
      market: string
      line: string
      probability: number
      prob_goal: number
      prob_point: number
      confidence: 'high' | 'medium' | 'low'
      expected_points: number
      opponent_goalie: string | null
      factors: string[]
    }>
  }> {
    const empty = { start_date: '', days, is_playoffs: false, games: 0, picks: [] }
    try {
      const r = await fetch(`${this.baseUrl}/api/playoffs/best-bets?days=${days}&top_n=${topN}`)
      if (!r.ok) return empty
      return r.json()
    } catch {
      return empty
    }
  }

  async getPlayoffPicks(game_date?: string): Promise<{
    date: string
    count: number
    picks: Array<{
      date: string
      rank: number
      player_name: string
      team: string
      opponent: string
      is_home: boolean
      market: string
      line: string
      probability: number
      prob_goal: number | null
      prob_point: number | null
      confidence: 'high' | 'medium' | 'low'
      opponent_goalie: string | null
      hit: boolean | null
      actual_goals: number | null
      actual_assists: number | null
      actual_points: number | null
      graded: boolean
      start_time_utc?: string | null
      lock_time_utc?: string | null
      lock_lead_minutes?: number
      minutes_to_lock?: number | null
      minutes_to_puck_drop?: number | null
      lock_status?: PickLockStatus
      game_state?: string | null
    }>
  }> {
    const empty = { date: game_date ?? '', count: 0, picks: [] }
    try {
      const qs = game_date ? `?game_date=${game_date}` : ''
      const r = await fetch(`${this.baseUrl}/api/playoffs/picks${qs}`)
      if (!r.ok) return empty
      return r.json()
    } catch {
      return empty
    }
  }

  async getPlayoffPickRecord(days = 30): Promise<{
    period_days: number
    total_picks: number
    graded: number
    hits: number
    misses: number
    hit_rate: number | null
    avg_probability: number | null
    streak: { kind: 'hit' | 'miss'; length: number } | null
    nights: Array<{ date: string; picks: number; hits: number; graded: number }>
    recent: Array<{
      date: string
      rank: number
      player_name: string
      team: string
      opponent: string
      market: string
      line: string
      probability: number
      confidence: 'high' | 'medium' | 'low'
      hit: boolean | null
      actual_goals: number | null
      actual_points: number | null
    }>
  }> {
    const empty = {
      period_days: days,
      total_picks: 0,
      graded: 0,
      hits: 0,
      misses: 0,
      hit_rate: null,
      avg_probability: null,
      streak: null,
      nights: [],
      recent: [],
    }
    try {
      const r = await fetch(`${this.baseUrl}/api/playoffs/picks/record?days=${days}`)
      if (!r.ok) return empty
      return r.json()
    } catch {
      return empty
    }
  }

  async getAccuracySummary(days = 7): Promise<{
    nhl: { goal_hit_rate: string; validated: number; total: number } | null
    olympics: { goal_hit_rate: string; validated: number; total: number } | null
  }> {
    try {
      const response = await fetch(`${this.baseUrl}/api/audit/accuracy-summary?days=${days}`)
      if (!response.ok) return { nhl: null, olympics: null }
      const data = await response.json()
      // Backend returns { by_type: { nhl, olympics } } - flatten here
      const byType = data?.by_type ?? data
      return { nhl: byType?.nhl ?? null, olympics: byType?.olympics ?? null }
    } catch {
      return { nhl: null, olympics: null }
    }
  }
}

export const api = new PowerplAIAPI()
