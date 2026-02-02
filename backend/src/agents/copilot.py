"""
PowerplAI Copilot - The main agent that orchestrates queries.

Handles:
1. Query classification (stats lookup, comparison, analysis, prediction)
2. Routing to appropriate data sources (SQL vs RAG)
3. Synthesizing responses with citations
"""
import anthropic
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import structlog
import json

from backend.src.config import get_settings
from backend.src.agents.rag import rag_service

logger = structlog.get_logger()
settings = get_settings()


SYSTEM_PROMPT = """You are PowerplAI, an expert hockey analytics assistant. You help users understand NHL statistics, player performance, and make data-driven insights for fantasy hockey and predictions.

You have access to:
1. **Structured Stats Database**: Player stats, game logs, standings (via SQL)
2. **Analytics Knowledge Base**: Articles and analysis about hockey analytics (via RAG search)

When answering questions:
- Always cite your data sources (e.g., "According to MoneyPuck data..." or "Based on 2023-24 stats...")
- Distinguish between raw stats and advanced metrics (xG, Corsi, WAR)
- Be clear about the limitations of the data
- If you're uncertain, say so rather than making up stats

For player comparisons:
- Use per-60 or per-game stats to normalize for ice time
- Consider sample size (games played)
- Account for team effects and usage

Key hockey analytics concepts you understand:
- Expected Goals (xG): Probability a shot becomes a goal based on location, type, etc.
- Corsi: Shot attempt differential (shots + missed + blocked)
- Fenwick: Like Corsi but excludes blocked shots
- GAR/WAR: Goals/Wins Above Replacement (total player value)
- PDO: Shooting% + Save% (luck indicator, regresses to 100)

You will receive context from database queries and RAG searches. Use this information to provide accurate, data-backed responses."""


class QueryType:
    STATS_LOOKUP = "stats_lookup"       # "How many goals does Makar have?"
    COMPARISON = "comparison"           # "Compare McDavid vs Crosby"
    TREND_ANALYSIS = "trend_analysis"   # "How has MacKinnon performed lately?"
    EXPLAINER = "explainer"             # "What is expected goals?"
    PREDICTION = "prediction"           # "Will the Avs make playoffs?"
    MATCHUP_PREDICTION = "matchup_prediction"  # "Who will score in TOR vs BOS tonight?"
    TONIGHT_PREDICTION = "tonight_prediction"  # "Who should I start tonight?"


class PowerplAICopilot:
    """Main copilot agent for hockey analytics queries."""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    async def query(
        self,
        user_query: str,
        db: AsyncSession,
        include_rag: bool = True,
    ) -> dict:
        """
        Process a user query and return a response with sources.

        Returns:
            {
                "response": str,
                "sources": [{"type": "sql"|"rag", "data": ...}],
                "query_type": str
            }
        """
        sources = []

        # Step 1: Classify the query and extract entities
        classification = await self._classify_query(user_query)
        logger.info("query_classified", query=user_query[:50], classification=classification)

        # Step 2: Fetch relevant data based on query type
        context_parts = []

        # Check if this is a prediction query
        if classification.get("is_prediction_query") or classification.get("type") in ("matchup_prediction", "tonight_prediction"):
            prediction_context = await self._fetch_predictions(db, classification)
            if prediction_context:
                context_parts.append(f"## Scoring Predictions\n{prediction_context}")
                sources.append({"type": "prediction", "data": "scoring_predictions"})

        # Check if this is a trade query
        elif classification.get("is_trade_query") or classification.get("type") == "trade_suggestion":
            trade_context = await self._fetch_trade_suggestions(db, classification)
            if trade_context:
                context_parts.append(f"## Trade Analysis\n{trade_context}")
                sources.append({"type": "trade", "data": "trade_suggestions"})

        # Check if this is an all-teams breakdown query (e.g., "top 3 on each team")
        elif classification.get("is_all_teams_query"):
            stats_requested = classification.get("stats", ["goals"])
            top_n = classification.get("top_n", 3)
            all_teams_context = await self._fetch_all_teams_breakdown(db, stats_requested, top_n)
            if all_teams_context:
                context_parts.append(f"## All Teams Breakdown\n{all_teams_context}")
                sources.append({"type": "sql", "data": "all_teams_breakdown"})

        # Check if this is a team-specific query
        elif classification.get("teams"):
            stats_requested = classification.get("stats", ["points"])
            team_context = await self._fetch_team_stats(db, classification["teams"], stats_requested)
            if team_context:
                context_parts.append(f"## Team Statistics\n{team_context}")
                sources.append({"type": "sql", "data": "team_stats"})

        # Check if this is a leaders query (e.g., "who leads in xG?")
        elif classification.get("is_leaders_query") or classification.get("type") == "leaders":
            stats_requested = classification.get("stats", ["points"])
            # Extract season from timeframe (e.g., "2015-16" -> "20152016")
            season = None
            timeframe = classification.get("timeframe", "")
            if timeframe:
                # Try to extract year from timeframe like "2015-16", "2015", "2015-2016"
                import re
                year_match = re.search(r'(\d{4})', str(timeframe))
                if year_match:
                    year = year_match.group(1)
                    season = f"{year}{int(year)+1}"
            leaders_context = await self._fetch_league_leaders(db, stats_requested, season=season)
            if leaders_context:
                context_parts.append(f"## League Leaders\n{leaders_context}")
                sources.append({"type": "sql", "data": "league_leaders"})

        # Try to get structured stats if players are mentioned
        if classification.get("players"):
            stats_context = await self._fetch_player_stats(db, classification["players"])
            if stats_context:
                context_parts.append(f"## Player Statistics\n{stats_context}")
                sources.append({"type": "sql", "data": "player_stats"})

        # Get RAG context for additional knowledge
        if include_rag:
            rag_results = await rag_service.search(db, user_query, limit=3)
            if rag_results:
                rag_context = "\n\n".join([
                    f"### {doc['title'] or 'Document'} (source: {doc['source']})\n{doc['content'][:500]}..."
                    for doc in rag_results
                ])
                context_parts.append(f"## Related Analysis\n{rag_context}")
                sources.append({"type": "rag", "data": rag_results})

        # Step 3: Generate response with Claude
        context = "\n\n".join(context_parts) if context_parts else "No specific data found in database."

        response = await self._generate_response(user_query, context)

        return {
            "response": response,
            "sources": sources,
            "query_type": classification.get("type", "unknown"),
        }

    async def _classify_query(self, query: str) -> dict:
        """Use Claude to classify the query and extract entities."""
        message = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[
                {
                    "role": "user",
                    "content": f"""Classify this hockey analytics query and extract key entities.

Query: "{query}"

Respond with JSON only:
{{
    "type": "stats_lookup" | "comparison" | "trend_analysis" | "explainer" | "prediction" | "leaders" | "team_breakdown" | "matchup_prediction" | "tonight_prediction" | "trade_suggestion",
    "players": ["player names mentioned"],
    "teams": ["team names or abbreviations - convert full names to abbreviations like TOR, BOS, EDM"],
    "stats": ["specific stats mentioned like goals, xG, corsi"],
    "timeframe": "current season" | "career" | "tonight" | "tomorrow" | "monday" | "tuesday" | "wednesday" | "thursday" | "friday" | "saturday" | "sunday" | "this week" | "feb 3" | "january 15" | null,
    "is_leaders_query": true if asking about league leaders/top players/who leads in a stat,
    "is_all_teams_query": true if asking about all teams or each team (e.g. "top 3 on each team", "best player per team"),
    "is_prediction_query": true if asking about who will score, predictions, who to start, fantasy advice for tonight/tomorrow/upcoming games,
    "is_tonight_query": true if asking about tonight's games, today's games, tomorrow's games, or upcoming games without specific teams,
    "is_trade_query": true if asking about trades, trade value, who to trade for, trade targets, or package deals,
    "top_n": number if asking for top N players (e.g. "top 3" = 3, "top 5" = 5)
}}

Examples:
- "Who will score in TOR vs BOS tonight?" -> type: "matchup_prediction", teams: ["TOR", "BOS"], is_prediction_query: true
- "Who should I start tonight?" -> type: "tonight_prediction", is_prediction_query: true, is_tonight_query: true
- "Predictions for Edmonton vs Calgary" -> type: "matchup_prediction", teams: ["EDM", "CGY"], is_prediction_query: true
- "Who is going to score in the leafs game tomorrow?" -> type: "matchup_prediction", teams: ["TOR"], is_prediction_query: true, timeframe: "tomorrow"
- "Best bets for Monday's games" -> type: "tonight_prediction", is_prediction_query: true, is_tonight_query: true, timeframe: "monday"
- "Who is most likely to score on Tuesday?" -> type: "tonight_prediction", is_prediction_query: true, is_tonight_query: true, timeframe: "tuesday"
- "Who will score on Feb 3rd?" -> type: "tonight_prediction", is_prediction_query: true, is_tonight_query: true, timeframe: "feb 3"
- "Who should I start this week?" -> type: "tonight_prediction", is_prediction_query: true
- "Who should I trade McDavid for?" -> type: "trade_suggestion", players: ["McDavid"], is_trade_query: true
- "Trade value for Sherwood and Landeskog" -> type: "trade_suggestion", players: ["Sherwood", "Landeskog"], is_trade_query: true
- "Package Makar and Rantanen for who?" -> type: "trade_suggestion", players: ["Makar", "Rantanen"], is_trade_query: true"""
                }
            ],
        )

        try:
            text = message.content[0].text
            # Try to extract JSON from markdown code blocks if present
            if "```" in text:
                import re
                json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
                if json_match:
                    text = json_match.group(1)
            return json.loads(text)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("classification_parse_error", error=str(e), raw_text=message.content[0].text[:200])
            return {"type": "unknown", "players": [], "teams": [], "stats": []}

    async def _fetch_player_stats(
        self,
        db: AsyncSession,
        player_names: list[str],
    ) -> str | None:
        """Fetch stats for mentioned players from the database."""
        if not player_names:
            return None

        # Build query for players (fuzzy match on name)
        placeholders = ", ".join([f":name{i}" for i in range(len(player_names))])
        params = {f"name{i}": f"%{name}%" for i, name in enumerate(player_names)}

        result = await db.execute(
            text(f"""
                SELECT
                    p.name,
                    p.position,
                    p.team_abbrev,
                    s.season,
                    s.games_played,
                    s.goals,
                    s.assists,
                    s.points,
                    s.xg,
                    s.corsi_for_pct,
                    s.toi_per_game
                FROM players p
                LEFT JOIN player_season_stats s ON p.id = s.player_id
                WHERE {' OR '.join([f"p.name ILIKE :name{i}" for i in range(len(player_names))])}
                ORDER BY s.season DESC
                LIMIT 10
            """),
            params,
        )

        rows = result.fetchall()
        if not rows:
            return None

        # Format as readable text
        stats_text = []
        for row in rows:
            stats_text.append(
                f"**{row.name}** ({row.position}, {row.team_abbrev}) - {row.season or 'Career'}:\n"
                f"  GP: {row.games_played}, G: {row.goals}, A: {row.assists}, P: {row.points}\n"
                f"  xG: {row.xg}, CF%: {row.corsi_for_pct}, TOI/G: {row.toi_per_game}"
            )

        return "\n\n".join(stats_text)

    async def _fetch_team_stats(
        self,
        db: AsyncSession,
        teams: list[str],
        stats: list[str],
        limit: int = 15,
    ) -> str | None:
        """Fetch stats for players on specific teams."""
        if not teams:
            return None

        # Map team names to abbreviations
        team_mapping = {
            "toronto": "TOR", "maple leafs": "TOR", "leafs": "TOR",
            "montreal": "MTL", "canadiens": "MTL", "habs": "MTL",
            "ottawa": "OTT", "senators": "OTT", "sens": "OTT",
            "boston": "BOS", "bruins": "BOS",
            "buffalo": "BUF", "sabres": "BUF",
            "detroit": "DET", "red wings": "DET",
            "florida": "FLA", "panthers": "FLA",
            "tampa": "TBL", "tampa bay": "TBL", "lightning": "TBL",
            "carolina": "CAR", "hurricanes": "CAR", "canes": "CAR",
            "new jersey": "NJD", "devils": "NJD",
            "new york rangers": "NYR", "rangers": "NYR",
            "new york islanders": "NYI", "islanders": "NYI",
            "philadelphia": "PHI", "flyers": "PHI",
            "pittsburgh": "PIT", "penguins": "PIT", "pens": "PIT",
            "washington": "WSH", "capitals": "WSH", "caps": "WSH",
            "columbus": "CBJ", "blue jackets": "CBJ",
            "chicago": "CHI", "blackhawks": "CHI", "hawks": "CHI",
            "colorado": "COL", "avalanche": "COL", "avs": "COL",
            "dallas": "DAL", "stars": "DAL",
            "minnesota": "MIN", "wild": "MIN",
            "nashville": "NSH", "predators": "NSH", "preds": "NSH",
            "st louis": "STL", "st. louis": "STL", "blues": "STL",
            "winnipeg": "WPG", "jets": "WPG",
            "arizona": "ARI", "coyotes": "ARI",
            "utah": "UTA", "utah hockey club": "UTA",
            "anaheim": "ANA", "ducks": "ANA",
            "calgary": "CGY", "flames": "CGY",
            "edmonton": "EDM", "oilers": "EDM",
            "los angeles": "LAK", "kings": "LAK",
            "san jose": "SJS", "sharks": "SJS",
            "seattle": "SEA", "kraken": "SEA",
            "vancouver": "VAN", "canucks": "VAN",
            "vegas": "VGK", "golden knights": "VGK", "knights": "VGK",
        }

        # Convert team names to abbreviations
        team_abbrevs = []
        for team in teams:
            if not team:
                continue
            team_lower = team.lower()
            # Direct match
            if team_lower in team_mapping:
                team_abbrevs.append(team_mapping[team_lower])
            # 3-letter abbreviation
            elif len(team) == 3:
                team_abbrevs.append(team.upper())
            else:
                # Try partial matching - check if any key is in the team name
                for key, abbrev in team_mapping.items():
                    if key in team_lower or team_lower in key:
                        team_abbrevs.append(abbrev)
                        break

        if not team_abbrevs:
            return None

        # Determine sort column
        stat_mapping = {
            "goals": "goals", "g": "goals",
            "assists": "assists", "a": "assists",
            "points": "points", "p": "points",
            "xg": "xg", "expected goals": "xg",
        }
        sort_column = "points"
        stat_label = "Points"
        for stat in stats:
            if not stat:
                continue
            if stat.lower() in stat_mapping:
                sort_column = stat_mapping[stat.lower()]
                stat_label = stat.title()
                break

        # Get most recent season
        season_result = await db.execute(
            text("SELECT MAX(season) FROM player_season_stats")
        )
        latest_season = season_result.scalar()

        # Build query - use s.team_abbrev to get players who played for the team that season
        placeholders = ", ".join([f":team{i}" for i in range(len(team_abbrevs))])
        params = {f"team{i}": abbrev for i, abbrev in enumerate(team_abbrevs)}
        params["season"] = latest_season
        params["limit"] = limit

        result = await db.execute(
            text(f"""
                SELECT
                    p.name,
                    p.position,
                    s.team_abbrev,
                    s.season,
                    s.games_played,
                    s.goals,
                    s.assists,
                    s.points,
                    s.xg,
                    s.corsi_for_pct
                FROM players p
                JOIN player_season_stats s ON p.id = s.player_id
                WHERE s.team_abbrev IN ({placeholders})
                  AND s.season = :season
                  AND s.{sort_column} IS NOT NULL
                ORDER BY s.{sort_column} DESC
                LIMIT :limit
            """),
            params,
        )

        rows = result.fetchall()
        if not rows:
            return None

        # Format season for display
        display_season = rows[0].season if rows else "Unknown"
        if display_season and len(display_season) == 8:
            display_season = f"{display_season[:4]}-{display_season[6:8]}"

        team_names = ", ".join(team_abbrevs)
        stats_text = [f"**{team_names} players ranked by {stat_label} ({display_season} season):**\n"]
        for i, row in enumerate(rows, 1):
            stats_text.append(
                f"{i}. **{row.name}** ({row.position or 'F'}, {row.team_abbrev}):\n"
                f"   GP: {row.games_played}, G: {row.goals}, A: {row.assists}, P: {row.points}, "
                f"xG: {row.xg:.1f}" if row.xg else f"   GP: {row.games_played}, G: {row.goals}, A: {row.assists}, P: {row.points}"
            )

        return "\n".join(stats_text)

    async def _fetch_all_teams_breakdown(
        self,
        db: AsyncSession,
        stats: list[str],
        top_n: int = 3,
    ) -> str | None:
        """Fetch top N players per team for the given stat."""
        # Map common stat names to database columns
        stat_mapping = {
            "goals": "goals", "g": "goals",
            "assists": "assists", "a": "assists",
            "points": "points", "p": "points",
            "xg": "xg", "expected goals": "xg",
        }

        sort_column = "goals"
        stat_label = "Goals"
        for stat in stats:
            if not stat:
                continue
            if stat.lower() in stat_mapping:
                sort_column = stat_mapping[stat.lower()]
                stat_label = stat.title()
                break

        # Get most recent season
        season_result = await db.execute(
            text("SELECT MAX(season) FROM player_season_stats")
        )
        latest_season = season_result.scalar()

        # Use window function to rank players within each team
        result = await db.execute(
            text(f"""
                WITH ranked AS (
                    SELECT
                        p.name,
                        p.position,
                        s.team_abbrev,
                        s.games_played,
                        s.goals,
                        s.assists,
                        s.points,
                        s.xg,
                        ROW_NUMBER() OVER (PARTITION BY s.team_abbrev ORDER BY s.{sort_column} DESC) as rank
                    FROM players p
                    JOIN player_season_stats s ON p.id = s.player_id
                    WHERE s.season = :season AND s.{sort_column} IS NOT NULL
                )
                SELECT * FROM ranked WHERE rank <= :top_n
                ORDER BY team_abbrev, rank
            """),
            {"season": latest_season, "top_n": top_n},
        )

        rows = result.fetchall()
        if not rows:
            return None

        # Format season for display
        display_season = latest_season
        if display_season and len(display_season) == 8:
            display_season = f"{display_season[:4]}-{display_season[6:8]}"

        # Group by team
        teams = {}
        for row in rows:
            team = row.team_abbrev
            if team not in teams:
                teams[team] = []
            teams[team].append(row)

        # Format output
        stats_text = [f"**Top {top_n} players by {stat_label} on each team ({display_season} season):**\n"]

        for team in sorted(teams.keys()):
            players = teams[team]
            team_lines = [f"\n**{team}:**"]
            for row in players:
                stat_value = getattr(row, sort_column)
                team_lines.append(f"  {row.rank}. {row.name}: {stat_value} {stat_label.lower()}")
            stats_text.append("\n".join(team_lines))

        return "\n".join(stats_text)

    async def _fetch_league_leaders(
        self,
        db: AsyncSession,
        stats: list[str],
        limit: int = 10,
        season: str | None = None,
    ) -> str | None:
        """Fetch league leaders for the requested stats."""
        # Map common stat names to database columns
        stat_mapping = {
            "goals": "goals",
            "g": "goals",
            "assists": "assists",
            "a": "assists",
            "points": "points",
            "p": "points",
            "xg": "xg",
            "expected goals": "xg",
            "corsi": "corsi_for_pct",
            "cf%": "corsi_for_pct",
            "corsi_for_pct": "corsi_for_pct",
            "toi": "toi_per_game",
            "ice time": "toi_per_game",
        }

        # Determine which stat to sort by
        sort_column = "points"  # default
        stat_label = "Points"
        for stat in stats:
            if not stat:
                continue
            stat_lower = stat.lower()
            if stat_lower in stat_mapping:
                sort_column = stat_mapping[stat_lower]
                stat_label = stat.title()
                break

        # Build season filter - if no season specified, get most recent
        season_filter = ""
        params = {"limit": limit}

        if season:
            season_filter = "AND s.season = :season"
            params["season"] = season
        else:
            # Get the most recent season with data
            season_result = await db.execute(
                text("SELECT MAX(season) FROM player_season_stats")
            )
            latest_season = season_result.scalar()
            if latest_season:
                season_filter = "AND s.season = :season"
                params["season"] = latest_season

        result = await db.execute(
            text(f"""
                SELECT
                    p.name,
                    p.position,
                    p.team_abbrev,
                    s.season,
                    s.games_played,
                    s.goals,
                    s.assists,
                    s.points,
                    s.xg,
                    s.corsi_for_pct,
                    s.toi_per_game
                FROM players p
                JOIN player_season_stats s ON p.id = s.player_id
                WHERE s.{sort_column} IS NOT NULL {season_filter}
                ORDER BY s.{sort_column} DESC
                LIMIT :limit
            """),
            params,
        )

        rows = result.fetchall()
        if not rows:
            return None

        # Format season for display (20232024 -> 2023-24)
        display_season = rows[0].season if rows else "Unknown"
        if display_season and len(display_season) == 8:
            display_season = f"{display_season[:4]}-{display_season[6:8]}"

        # Format as readable text
        stats_text = [f"**Top {limit} players by {stat_label} ({display_season} season):**\n"]
        for i, row in enumerate(rows, 1):
            stats_text.append(
                f"{i}. **{row.name}** ({row.position}, {row.team_abbrev}):\n"
                f"   GP: {row.games_played}, G: {row.goals}, A: {row.assists}, P: {row.points}, "
                f"xG: {row.xg}, CF%: {row.corsi_for_pct}"
            )

        return "\n".join(stats_text)

    async def _fetch_predictions(
        self,
        db: AsyncSession,
        classification: dict,
    ) -> str | None:
        """Fetch scoring predictions for a matchup or tonight's games."""
        from backend.src.agents.predictions import prediction_engine
        from backend.src.ingestion.games import get_todays_games, refresh_todays_schedule
        from datetime import date, timedelta

        teams = classification.get("teams", [])
        timeframe = (classification.get("timeframe") or "").lower()

        # Day name to weekday mapping
        day_names = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6
        }

        is_tonight = classification.get("is_tonight_query", False) or timeframe in ("tonight", "tomorrow", "this week") or timeframe in day_names

        # Determine the target date based on timeframe
        target_date = date.today()
        if timeframe == "tomorrow":
            target_date = date.today() + timedelta(days=1)
        elif timeframe in day_names:
            # Find next occurrence of that day
            target_weekday = day_names[timeframe]
            days_ahead = target_weekday - target_date.weekday()
            if days_ahead <= 0:  # Target day already happened this week or is today
                days_ahead += 7
            target_date = target_date + timedelta(days=days_ahead)
        else:
            # Try to parse as a date string (e.g., "Feb 3", "February 3rd", "2026-02-03")
            import re
            date_match = re.search(r'(\w+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?', timeframe)
            if date_match:
                month_str, day_str, year_str = date_match.groups()
                months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                          "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
                          "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
                          "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12}
                month = months.get(month_str.lower())
                if month:
                    year = int(year_str) if year_str else target_date.year
                    try:
                        target_date = date(year, month, int(day_str))
                    except ValueError:
                        pass

        # If specific teams mentioned, get matchup prediction
        if len(teams) >= 2:
            # Convert team names to abbreviations if needed
            team_abbrevs = self._normalize_teams(teams[:2])
            if len(team_abbrevs) >= 2:
                try:
                    prediction = await prediction_engine.get_matchup_prediction(
                        db, team_abbrevs[0], team_abbrevs[1], target_date, top_n=8
                    )
                    return self._format_matchup_prediction(prediction)
                except Exception as e:
                    logger.warning("matchup_prediction_failed", teams=teams, error=str(e))

        # If only one team mentioned, find their scheduled game
        elif len(teams) == 1:
            team_abbrev = self._normalize_teams(teams)[0] if self._normalize_teams(teams) else None
            if team_abbrev:
                try:
                    # Refresh schedule and find the team's game
                    await refresh_todays_schedule(db)
                    from sqlalchemy import text
                    result = await db.execute(
                        text("""
                            SELECT home_team_abbrev, away_team_abbrev
                            FROM games
                            WHERE game_date = :target_date
                              AND (home_team_abbrev = :team OR away_team_abbrev = :team)
                            LIMIT 1
                        """),
                        {"target_date": target_date, "team": team_abbrev}
                    )
                    row = result.fetchone()
                    if row:
                        prediction = await prediction_engine.get_matchup_prediction(
                            db, row.home_team_abbrev, row.away_team_abbrev, target_date, top_n=8
                        )
                        return self._format_matchup_prediction(prediction)
                    else:
                        return f"No game scheduled for {team_abbrev} on {target_date.strftime('%B %d, %Y')}."
                except Exception as e:
                    logger.warning("single_team_prediction_failed", team=team_abbrev, error=str(e))

        # If asking about tonight/tomorrow generally, get all predictions for that date
        if is_tonight or not teams:
            try:
                # Refresh schedule first
                await refresh_todays_schedule(db)

                # Get games for the target date
                from sqlalchemy import text
                result = await db.execute(
                    text("""
                        SELECT nhl_game_id, game_date, start_time_utc,
                               home_team_abbrev, away_team_abbrev,
                               home_score, away_score, game_state, venue
                        FROM games
                        WHERE game_date = :target_date
                        ORDER BY start_time_utc
                    """),
                    {"target_date": target_date}
                )
                rows = result.fetchall()
                games = [
                    {
                        "game_id": row.nhl_game_id,
                        "date": row.game_date.isoformat(),
                        "start_time": row.start_time_utc.isoformat() if row.start_time_utc else None,
                        "home_team": row.home_team_abbrev,
                        "away_team": row.away_team_abbrev,
                        "venue": row.venue,
                    }
                    for row in rows
                ]

                if not games:
                    return f"No games scheduled for {target_date.strftime('%B %d, %Y')}."

                date_label = "Tonight's" if target_date == date.today() else target_date.strftime('%A, %B %d')
                predictions_text = [f"**{date_label} Games - {target_date.strftime('%B %d, %Y')}**\n"]

                all_top_scorers = []
                for game in games[:10]:  # Process up to 10 games
                    try:
                        matchup = await prediction_engine.get_matchup_prediction(
                            db, game["home_team"], game["away_team"], target_date, top_n=5
                        )
                        all_top_scorers.extend(matchup.top_scorers)

                        predictions_text.append(f"\n### {game['away_team']} @ {game['home_team']}")
                        if game.get("venue"):
                            predictions_text.append(f"*{game['venue']}*")

                        predictions_text.append("\n**Top Goal Scorers:**")
                        for i, pred in enumerate(matchup.top_scorers[:3], 1):
                            prob_pct = int(pred.prob_goal * 100)
                            predictions_text.append(
                                f"{i}. **{pred.player_name}** ({pred.team}) - {prob_pct}% chance to score"
                            )
                            if pred.factors:
                                predictions_text.append(f"   _{pred.factors[0]}_")
                    except Exception as e:
                        logger.warning("game_prediction_failed", game=game, error=str(e))
                        continue

                # Add overall top scorers
                all_top_scorers.sort(key=lambda p: p.prob_goal, reverse=True)
                if all_top_scorers:
                    predictions_text.append("\n### Overall Best Bets Tonight")
                    for i, pred in enumerate(all_top_scorers[:5], 1):
                        prob_pct = int(pred.prob_goal * 100)
                        matchup_str = f"vs {pred.opponent}" if pred.is_home else f"@ {pred.opponent}"
                        predictions_text.append(
                            f"{i}. **{pred.player_name}** ({pred.team} {matchup_str}) - "
                            f"{prob_pct}% goal, {int(pred.prob_point * 100)}% point"
                        )

                return "\n".join(predictions_text)
            except Exception as e:
                logger.warning("tonight_predictions_failed", error=str(e))
                return None

        return None

    def _format_matchup_prediction(self, prediction) -> str:
        """Format a matchup prediction as readable text."""
        lines = [
            f"**{prediction.away_team} @ {prediction.home_team}** - {prediction.game_date.strftime('%B %d, %Y')}"
        ]

        if prediction.venue:
            lines.append(f"*{prediction.venue}*")

        # Add matchup context (goalies, pace)
        if prediction.expected_total_goals:
            pace_desc = prediction.pace_rating or "average"
            lines.append(f"\n**Game Environment:** Expected {prediction.expected_total_goals:.1f} total goals ({pace_desc} pace)")

        if prediction.home_goalie or prediction.away_goalie:
            lines.append("\n**Goalie Matchup:**")
            if prediction.home_goalie:
                hg = prediction.home_goalie
                lines.append(f"- {prediction.home_team}: {hg.get('name', 'Unknown')} ({hg.get('save_pct', 0):.3f} SV%, {hg.get('gaa', 0):.2f} GAA)")
            if prediction.away_goalie:
                ag = prediction.away_goalie
                lines.append(f"- {prediction.away_team}: {ag.get('name', 'Unknown')} ({ag.get('save_pct', 0):.3f} SV%, {ag.get('gaa', 0):.2f} GAA)")

        lines.append("\n**Most Likely Scorers:**")
        for i, pred in enumerate(prediction.top_scorers[:5], 1):
            prob_pct = int(pred.prob_goal * 100)
            point_pct = int(pred.prob_point * 100)
            lines.append(
                f"{i}. **{pred.player_name}** ({pred.team}) - "
                f"{prob_pct}% goal probability, {point_pct}% point probability"
            )
            lines.append(f"   Expected: {pred.expected_goals:.2f}G, {pred.expected_assists:.2f}A, {pred.expected_points:.2f}P")
            if pred.factors:
                lines.append(f"   _{' | '.join(pred.factors[:2])}_")
            lines.append(f"   Confidence: {pred.confidence} ({int(pred.confidence_score * 100)}%)")

        # Add team breakdowns
        lines.append(f"\n**{prediction.home_team} (Home) Key Players:**")
        for pred in prediction.home_players[:3]:
            prob_pct = int(pred.prob_goal * 100)
            goalie_note = ""
            if pred.opponent_goalie:
                goalie_note = f" (vs {pred.opponent_goalie})"
            lines.append(f"- {pred.player_name}: {prob_pct}% goal, {pred.expected_points:.2f} expected points{goalie_note}")

        lines.append(f"\n**{prediction.away_team} (Away) Key Players:**")
        for pred in prediction.away_players[:3]:
            prob_pct = int(pred.prob_goal * 100)
            goalie_note = ""
            if pred.opponent_goalie:
                goalie_note = f" (vs {pred.opponent_goalie})"
            lines.append(f"- {pred.player_name}: {prob_pct}% goal, {pred.expected_points:.2f} expected points{goalie_note}")

        return "\n".join(lines)

    async def _fetch_trade_suggestions(
        self,
        db: AsyncSession,
        classification: dict,
    ) -> str | None:
        """Find trade targets based on player values."""
        players = classification.get("players", [])
        if not players:
            return None

        # Get stats for the players being traded
        player_stats = []
        total_value = 0.0

        for player_name in players:
            if not player_name:
                continue
            result = await db.execute(
                text("""
                    SELECT p.name, p.team_abbrev, p.position,
                           s.goals, s.assists, s.points, s.games_played,
                           s.xg, s.corsi_for_pct, s.toi_per_game
                    FROM players p
                    JOIN player_season_stats s ON p.id = s.player_id
                    WHERE LOWER(p.name) LIKE :name
                    ORDER BY s.season DESC
                    LIMIT 1
                """),
                {"name": f"%{player_name.lower()}%"},
            )
            row = result.fetchone()
            if row:
                # Calculate fantasy value: goals*3 + assists*2 + xg*2
                gp = row.games_played or 1
                ppg = (row.points or 0) / gp
                xg_per_game = (row.xg or 0) / gp
                value = ppg * 50 + xg_per_game * 30 + (row.corsi_for_pct or 50) * 0.5
                player_stats.append({
                    "name": row.name,
                    "team": row.team_abbrev,
                    "position": row.position,
                    "goals": row.goals,
                    "assists": row.assists,
                    "points": row.points,
                    "games": row.games_played,
                    "xg": row.xg,
                    "ppg": round(ppg, 2),
                    "value": round(value, 1),
                })
                total_value += value

        if not player_stats:
            return None

        # Find comparable players (within 20% of total value)
        value_min = total_value * 0.8
        value_max = total_value * 1.2

        # Build exclusion list for SQL
        exclude_names = [p["name"] for p in player_stats]
        exclude_placeholders = ", ".join([f":exclude_{i}" for i in range(len(exclude_names))])
        exclude_params = {f"exclude_{i}": name for i, name in enumerate(exclude_names)}

        result = await db.execute(
            text(f"""
                WITH player_values AS (
                    SELECT p.name, p.team_abbrev, p.position,
                           s.goals, s.assists, s.points, s.games_played,
                           s.xg, s.corsi_for_pct,
                           CASE WHEN s.games_played > 0 THEN
                               (s.points::float / s.games_played) * 50 +
                               (COALESCE(s.xg, 0)::float / s.games_played) * 30 +
                               COALESCE(s.corsi_for_pct, 50) * 0.5
                           ELSE 0 END as value
                    FROM players p
                    JOIN player_season_stats s ON p.id = s.player_id
                    WHERE s.season = (SELECT MAX(season) FROM player_season_stats)
                    AND s.games_played >= 20
                )
                SELECT name, team_abbrev, position, goals, assists, points, games_played, xg, value
                FROM player_values
                WHERE value BETWEEN :min_val AND :max_val
                AND name NOT IN ({exclude_placeholders})
                ORDER BY value DESC
                LIMIT 10
            """),
            {"min_val": value_min, "max_val": value_max, **exclude_params},
        )
        targets = result.fetchall()

        # Build response
        lines = ["**Players Being Traded:**"]
        for p in player_stats:
            xg = p['xg'] or 0
            lines.append(f"- {p['name']} ({p['team']}, {p['position']}): {p['points']} pts in {p['games']} GP ({p['ppg']} PPG), {xg:.1f} xG — Value: {p['value']}")

        lines.append(f"\n**Combined Trade Value:** {total_value:.1f}")
        lines.append(f"\n**Comparable Trade Targets** (value range {value_min:.1f} - {value_max:.1f}):")

        if targets:
            for t in targets:
                gp = t.games_played or 1
                ppg = round((t.points or 0) / gp, 2)
                xg = t.xg or 0
                lines.append(f"- {t.name} ({t.team_abbrev}, {t.position}): {t.points} pts ({ppg} PPG), {xg:.1f} xG — Value: {t.value:.1f}")
        else:
            lines.append("No comparable players found in the current season stats.")

        lines.append("\n**Trade Recommendation:** Target players with similar or slightly higher value scores. Higher xG suggests a player may be due for positive regression.")

        return "\n".join(lines)

    def _normalize_teams(self, teams: list[str]) -> list[str]:
        """Convert team names to abbreviations."""
        team_mapping = {
            "toronto": "TOR", "maple leafs": "TOR", "leafs": "TOR",
            "montreal": "MTL", "canadiens": "MTL", "habs": "MTL",
            "ottawa": "OTT", "senators": "OTT", "sens": "OTT",
            "boston": "BOS", "bruins": "BOS",
            "buffalo": "BUF", "sabres": "BUF",
            "detroit": "DET", "red wings": "DET",
            "florida": "FLA", "panthers": "FLA",
            "tampa": "TBL", "tampa bay": "TBL", "lightning": "TBL",
            "carolina": "CAR", "hurricanes": "CAR", "canes": "CAR",
            "new jersey": "NJD", "devils": "NJD",
            "rangers": "NYR", "new york rangers": "NYR",
            "islanders": "NYI", "new york islanders": "NYI",
            "philadelphia": "PHI", "flyers": "PHI",
            "pittsburgh": "PIT", "penguins": "PIT", "pens": "PIT",
            "washington": "WSH", "capitals": "WSH", "caps": "WSH",
            "columbus": "CBJ", "blue jackets": "CBJ",
            "chicago": "CHI", "blackhawks": "CHI", "hawks": "CHI",
            "colorado": "COL", "avalanche": "COL", "avs": "COL",
            "dallas": "DAL", "stars": "DAL",
            "minnesota": "MIN", "wild": "MIN",
            "nashville": "NSH", "predators": "NSH", "preds": "NSH",
            "st louis": "STL", "st. louis": "STL", "blues": "STL",
            "winnipeg": "WPG", "jets": "WPG",
            "arizona": "ARI", "coyotes": "ARI",
            "utah": "UTA", "utah hockey club": "UTA",
            "anaheim": "ANA", "ducks": "ANA",
            "calgary": "CGY", "flames": "CGY",
            "edmonton": "EDM", "oilers": "EDM",
            "los angeles": "LAK", "kings": "LAK",
            "san jose": "SJS", "sharks": "SJS",
            "seattle": "SEA", "kraken": "SEA",
            "vancouver": "VAN", "canucks": "VAN",
            "vegas": "VGK", "golden knights": "VGK", "knights": "VGK",
        }

        result = []
        for team in teams:
            if not team:
                continue
            team_lower = team.lower().strip()
            if team_lower in team_mapping:
                result.append(team_mapping[team_lower])
            elif len(team) == 3:
                result.append(team.upper())
            else:
                # Try partial matching
                for key, abbrev in team_mapping.items():
                    if key in team_lower or team_lower in key:
                        result.append(abbrev)
                        break

        return result

    async def _generate_response(self, query: str, context: str) -> str:
        """Generate the final response using Claude."""
        message = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"""Context from database and knowledge base:

{context}

---

User question: {query}

Provide a helpful, accurate response based on the context above.

IMPORTANT:
- Base your answer ONLY on the context provided above. Do not say you don't have access to data if it's in the context.
- If the context contains scoring predictions, present them clearly with percentages and player names.
- Always end your response with a "Sources:" section listing where the data came from, formatted as:

Sources:
- PowerplAI Scoring Model (NHL API game logs, recent form analysis)
- [Any other sources from the context]"""
                }
            ],
        )

        return message.content[0].text


# Singleton instance
copilot = PowerplAICopilot()
