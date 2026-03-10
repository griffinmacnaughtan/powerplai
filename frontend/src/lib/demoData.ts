/**
 * Demo data for GitHub Pages static deployment.
 * This allows the frontend to work standalone without a backend.
 */

import type { MatchupPrediction, Game, PlayerStats, LeaderboardEntry } from './api'

export const DEMO_MODE = process.env.NEXT_PUBLIC_DEMO_MODE === 'true'

export const demoGames: Game[] = [
  {
    game_id: 2025020001,
    date: new Date().toISOString().split('T')[0],
    start_time: '7:00 PM ET',
    home_team: 'TOR',
    away_team: 'BOS',
    home_score: null,
    away_score: null,
    state: 'FUT',
    venue: 'Scotiabank Arena',
  },
  {
    game_id: 2025020002,
    date: new Date().toISOString().split('T')[0],
    start_time: '7:30 PM ET',
    home_team: 'NYR',
    away_team: 'WSH',
    home_score: null,
    away_score: null,
    state: 'FUT',
    venue: 'Madison Square Garden',
  },
  {
    game_id: 2025020003,
    date: new Date().toISOString().split('T')[0],
    start_time: '10:00 PM ET',
    home_team: 'EDM',
    away_team: 'CGY',
    home_score: null,
    away_score: null,
    state: 'FUT',
    venue: 'Rogers Place',
  },
]

export const demoPredictions: MatchupPrediction[] = [
  {
    game_date: new Date().toISOString().split('T')[0],
    home_team: 'TOR',
    away_team: 'BOS',
    venue: 'Scotiabank Arena',
    start_time: '7:00 PM ET',
    expected_total_goals: 6.4,
    pace_rating: 'high',
    home_goalie: { name: 'Joseph Woll', save_pct: 0.912 },
    away_goalie: { name: 'Jeremy Swayman', save_pct: 0.918 },
    home_players: [
      {
        player_name: 'Auston Matthews',
        team: 'TOR',
        opponent: 'BOS',
        is_home: true,
        prob_goal: 0.42,
        prob_point: 0.68,
        prob_multi_point: 0.31,
        expected_goals: 0.55,
        expected_assists: 0.48,
        expected_points: 1.03,
        confidence: 'high',
        confidence_score: 0.85,
        factors: ['Hot streak: 1.2 PPG in last 5', 'High-scoring game expected'],
      },
      {
        player_name: 'Mitch Marner',
        team: 'TOR',
        opponent: 'BOS',
        is_home: true,
        prob_goal: 0.28,
        prob_point: 0.72,
        prob_multi_point: 0.35,
        expected_goals: 0.32,
        expected_assists: 0.82,
        expected_points: 1.14,
        confidence: 'high',
        confidence_score: 0.88,
        factors: ['Strong history vs BOS: 1.4 PPG', 'Plays better home'],
      },
      {
        player_name: 'William Nylander',
        team: 'TOR',
        opponent: 'BOS',
        is_home: true,
        prob_goal: 0.35,
        prob_point: 0.58,
        prob_multi_point: 0.22,
        expected_goals: 0.42,
        expected_assists: 0.35,
        expected_points: 0.77,
        confidence: 'high',
        confidence_score: 0.82,
        factors: ['Favorable goalie matchup'],
      },
    ],
    away_players: [
      {
        player_name: 'David Pastrnak',
        team: 'BOS',
        opponent: 'TOR',
        is_home: false,
        prob_goal: 0.38,
        prob_point: 0.62,
        prob_multi_point: 0.28,
        expected_goals: 0.48,
        expected_assists: 0.42,
        expected_points: 0.90,
        confidence: 'high',
        confidence_score: 0.86,
        factors: ['Hot streak: 1.1 PPG in last 5'],
      },
      {
        player_name: 'Brad Marchand',
        team: 'BOS',
        opponent: 'TOR',
        is_home: false,
        prob_goal: 0.25,
        prob_point: 0.52,
        prob_multi_point: 0.18,
        expected_goals: 0.28,
        expected_assists: 0.45,
        expected_points: 0.73,
        confidence: 'medium',
        confidence_score: 0.65,
        factors: ['Strong history vs TOR: 1.2 PPG'],
      },
    ],
    top_scorers: [],
  },
  {
    game_date: new Date().toISOString().split('T')[0],
    home_team: 'EDM',
    away_team: 'CGY',
    venue: 'Rogers Place',
    start_time: '10:00 PM ET',
    expected_total_goals: 7.1,
    pace_rating: 'high',
    home_goalie: { name: 'Stuart Skinner', save_pct: 0.898 },
    away_goalie: { name: 'Dustin Wolf', save_pct: 0.905 },
    home_players: [
      {
        player_name: 'Connor McDavid',
        team: 'EDM',
        opponent: 'CGY',
        is_home: true,
        prob_goal: 0.52,
        prob_point: 0.82,
        prob_multi_point: 0.48,
        expected_goals: 0.72,
        expected_assists: 0.95,
        expected_points: 1.67,
        confidence: 'high',
        confidence_score: 0.95,
        factors: ['Dominates vs CGY: 1.8 PPG career', 'Hot streak', 'High-pace game'],
      },
      {
        player_name: 'Leon Draisaitl',
        team: 'EDM',
        opponent: 'CGY',
        is_home: true,
        prob_goal: 0.48,
        prob_point: 0.75,
        prob_multi_point: 0.42,
        expected_goals: 0.65,
        expected_assists: 0.62,
        expected_points: 1.27,
        confidence: 'high',
        confidence_score: 0.92,
        factors: ['Strong history vs CGY', 'Favorable goalie matchup'],
      },
    ],
    away_players: [
      {
        player_name: 'Nazem Kadri',
        team: 'CGY',
        opponent: 'EDM',
        is_home: false,
        prob_goal: 0.28,
        prob_point: 0.48,
        prob_multi_point: 0.15,
        expected_goals: 0.32,
        expected_assists: 0.35,
        expected_points: 0.67,
        confidence: 'medium',
        confidence_score: 0.68,
        factors: ['Battle of Alberta motivation'],
      },
    ],
    top_scorers: [],
  },
]

// Fill in top_scorers for each matchup
demoPredictions.forEach(matchup => {
  const allPlayers = [...matchup.home_players, ...matchup.away_players]
  matchup.top_scorers = allPlayers.sort((a, b) => b.prob_goal - a.prob_goal).slice(0, 5)
})

export const demoLeaders: Record<string, LeaderboardEntry[]> = {
  points: [
    { rank: 1, player: { name: 'Connor McDavid', position: 'C', team_abbrev: 'EDM', season: '20252026', games_played: 62, goals: 38, assists: 78, points: 116, xg: 32.5, corsi_for_pct: 56.2 } },
    { rank: 2, player: { name: 'Nikita Kucherov', position: 'RW', team_abbrev: 'TBL', season: '20252026', games_played: 60, goals: 35, assists: 72, points: 107, xg: 28.3, corsi_for_pct: 54.8 } },
    { rank: 3, player: { name: 'Leon Draisaitl', position: 'C', team_abbrev: 'EDM', season: '20252026', games_played: 62, goals: 42, assists: 58, points: 100, xg: 35.2, corsi_for_pct: 55.1 } },
    { rank: 4, player: { name: 'Nathan MacKinnon', position: 'C', team_abbrev: 'COL', season: '20252026', games_played: 58, goals: 30, assists: 65, points: 95, xg: 26.8, corsi_for_pct: 57.5 } },
    { rank: 5, player: { name: 'Auston Matthews', position: 'C', team_abbrev: 'TOR', season: '20252026', games_played: 55, goals: 45, assists: 38, points: 83, xg: 38.5, corsi_for_pct: 53.2 } },
  ],
  goals: [
    { rank: 1, player: { name: 'Auston Matthews', position: 'C', team_abbrev: 'TOR', season: '20252026', games_played: 55, goals: 45, assists: 38, points: 83, xg: 38.5, corsi_for_pct: 53.2 } },
    { rank: 2, player: { name: 'Leon Draisaitl', position: 'C', team_abbrev: 'EDM', season: '20252026', games_played: 62, goals: 42, assists: 58, points: 100, xg: 35.2, corsi_for_pct: 55.1 } },
    { rank: 3, player: { name: 'Connor McDavid', position: 'C', team_abbrev: 'EDM', season: '20252026', games_played: 62, goals: 38, assists: 78, points: 116, xg: 32.5, corsi_for_pct: 56.2 } },
    { rank: 4, player: { name: 'Nikita Kucherov', position: 'RW', team_abbrev: 'TBL', season: '20252026', games_played: 60, goals: 35, assists: 72, points: 107, xg: 28.3, corsi_for_pct: 54.8 } },
    { rank: 5, player: { name: 'David Pastrnak', position: 'RW', team_abbrev: 'BOS', season: '20252026', games_played: 61, goals: 34, assists: 42, points: 76, xg: 30.1, corsi_for_pct: 52.8 } },
  ],
}

export const demoResponses: Record<string, string> = {
  default: `I'm running in **demo mode** with sample data. In the full version with the backend connected, I can:

- Look up real-time player stats from the NHL API
- Generate probability-based predictions for tonight's games
- Compare players using advanced metrics (xG, Corsi, Fenwick)
- Identify betting edges and value opportunities
- Answer questions about hockey analytics concepts

**Sample Question:** "Who's most likely to score in TOR vs BOS tonight?"

**Sample Answer:** Based on the multi-factor probability model:

1. **Auston Matthews** (TOR) - 42% goal probability
   - Hot streak: 1.2 PPG in last 5 games
   - High-pace game expected (6.4 total goals)

2. **David Pastrnak** (BOS) - 38% goal probability
   - Strong recent form
   - Career 0.85 PPG vs Toronto

3. **Mitch Marner** (TOR) - 28% goal probability
   - Elite playmaker, 72% point probability
   - Strong H2H history vs Boston

*View the full stack locally for real predictions!*`,
  prediction: `**Tonight's Top Scorers (Demo Data)**

🥅 **Connor McDavid** vs CGY - 52% goal probability
- Dominates the Battle of Alberta: 1.8 PPG career vs Flames
- High-pace game: 7.1 expected total goals

🥅 **Auston Matthews** vs BOS - 42% goal probability
- Hot streak: 1.2 PPG last 5 games
- Home ice advantage

🥅 **Leon Draisaitl** vs CGY - 48% goal probability
- Favorable goalie matchup
- Elite finishing ability

*These are demo predictions. Run the full stack for live data!*`,
  stats: `**Demo Stats Response**

In the full version, I would look up real player statistics including:
- Season totals (G, A, P, +/-, PIM)
- Advanced metrics (xG, Corsi, Fenwick, WAR)
- Recent game logs
- Historical comparisons

The data comes from:
- NHL Official API (rosters, schedules, game logs)
- MoneyPuck (expected goals, shot metrics)
- ESPN (injuries)
- PuckPedia (salary cap data)`,
}

export function getDemoResponse(query: string): string {
  const queryLower = query.toLowerCase()

  if (queryLower.includes('predict') || queryLower.includes('score') || queryLower.includes('tonight')) {
    return demoResponses.prediction
  }
  if (queryLower.includes('stat') || queryLower.includes('goal') || queryLower.includes('point')) {
    return demoResponses.stats
  }

  return demoResponses.default
}
