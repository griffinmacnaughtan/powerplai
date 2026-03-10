const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export interface ChatHistoryMessage {
  role: 'user' | 'assistant'
  content: string
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
    messages: ChatHistoryMessage[] = []
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
}

export const api = new PowerplAIAPI()
