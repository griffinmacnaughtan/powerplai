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
- CRITICAL: NEVER make up statistics. Only use stats explicitly provided in the context.
- If a stat is not provided (like TOI/time on ice), say "data not available" rather than inventing a value
- When explaining probability differences, use ONLY the stats provided in the context (GP, Goals, Assists, Points, xG, etc.)

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

You will receive context from database queries and RAG searches. Use this information to provide accurate, data-backed responses.

Formatting rules:
- Do NOT use emojis anywhere in your responses. Use plain text only.
- Use markdown headers (##, ###) and bold (**text**) for structure.
- Keep a clean, professional, analytical tone - like a quant sports analyst, not a sports broadcaster."""


class QueryType:
    STATS_LOOKUP = "stats_lookup"       # "How many goals does Makar have?"
    COMPARISON = "comparison"           # "Compare McDavid vs Crosby"
    TREND_ANALYSIS = "trend_analysis"   # "How has MacKinnon performed lately?"
    EXPLAINER = "explainer"             # "What is expected goals?"
    PREDICTION = "prediction"           # "Will the Avs make playoffs?"
    MATCHUP_PREDICTION = "matchup_prediction"  # "Who will score in TOR vs BOS tonight?"
    TONIGHT_PREDICTION = "tonight_prediction"  # "Who should I start tonight?"
    EDGE_FINDER = "edge_finder"         # "What are the best bets tonight?"
    REGRESSION = "regression"           # "Who is due for positive regression?"
    VALUE_BET = "value_bet"             # "Is McDavid +170 good value?"
    OLYMPICS = "olympics"               # "Olympic standings?" "How is McDavid doing in the Olympics?"
    SCHEDULE = "schedule"               # "What games are today?" "Who is playing tonight?"
    DAILY_BRIEFING = "daily_briefing"   # "Give me today's briefing" / "Daily briefing" button
    PARLAY_TRACK = "parlay_track"       # "Show me today's parlays" / "How are the parlays doing?"


class PowerplAICopilot:
    """Main copilot agent for hockey analytics queries."""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    async def query(
        self,
        user_query: str,
        db: AsyncSession,
        include_rag: bool = True,
        conversation_history: list[dict] | None = None,
        images: list[dict] | None = None,
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

        # Check if conversation history has Olympic context (for follow-up questions)
        has_olympic_context = False
        olympic_countries_in_history = []
        olympic_keywords = [
            "olympic", "parlay", "switzerland", "canada vs", "milano cortina",
            "quarterfinal", "semifinal", "medal", "gold medal",
            "czech", "sweden", "finland", "usa vs", "germany",
            "binnington", "hellebuyck", "genoni",  # Olympic goalies
            "celebrini", "mcdavid", "crosby", "mackinnon",  # Canadian stars
            "necas", "pastrnak",  # Czech stars
        ]
        if conversation_history:
            for msg in conversation_history:
                content = msg.get("content", "").lower()
                if any(word in content for word in olympic_keywords):
                    has_olympic_context = True
                    # Extract countries mentioned
                    country_map = {
                        "canada": "CAN", "canadian": "CAN",
                        "czech": "CZE", "czechia": "CZE",
                        "usa": "USA", "american": "USA", "united states": "USA",
                        "sweden": "SWE", "swedish": "SWE",
                        "finland": "FIN", "finnish": "FIN",
                        "switzerland": "SUI", "swiss": "SUI",
                        "germany": "GER", "german": "GER",
                        "slovakia": "SVK", "slovak": "SVK",
                    }
                    for name, code in country_map.items():
                        if name in content and code not in olympic_countries_in_history:
                            olympic_countries_in_history.append(code)
                    break

        # If Olympic context detected, inject it into classification
        if has_olympic_context and not classification.get("is_olympics_query"):
            query_lower = user_query.lower()
            # Trigger on betting terms OR follow-up scorer questions
            followup_triggers = [
                "payout", "pay out", "parlay", "odds", "bet", "value",
                "another", "other", "else", "different", "besides",
                "scorer", "score", "point", "goal", "who will",
            ]
            if any(word in query_lower for word in followup_triggers):
                classification["is_olympics_query"] = True
                classification["is_prediction_query"] = True
                # Inject countries from history
                if olympic_countries_in_history and not classification.get("countries"):
                    classification["countries"] = olympic_countries_in_history
                logger.info("injected_olympic_context_from_history", countries=olympic_countries_in_history)

        # Check if this is a vague follow-up query (e.g., "tell me more", "what else", "explain")
        is_followup = self._is_followup_query(user_query)
        if is_followup and conversation_history:
            # For follow-up queries, include the last assistant response as context
            last_assistant_msg = None
            for msg in reversed(conversation_history):
                if msg.get("role") == "assistant":
                    last_assistant_msg = msg.get("content", "")
                    break
            if last_assistant_msg:
                # Add previous response as context so Claude can elaborate
                sources.append({"type": "conversation", "data": "previous_response"})
                context_parts = [f"## Previous Response (for follow-up context)\n{last_assistant_msg}"]

                # Generate response with this context
                context = "\n\n".join(context_parts)
                response = await self._generate_response(user_query, context, conversation_history, images)
                return {
                    "response": response,
                    "sources": sources,
                    "query_type": "followup",
                }

        # Step 2: Fetch relevant data based on query type
        context_parts = []

        # HIGHEST PRIORITY: Daily briefing
        if classification.get("is_briefing_query") or classification.get("type") == "daily_briefing":
            briefing_context = await self._fetch_daily_briefing(db)
            if briefing_context:
                context_parts.append(briefing_context)
                sources.append({"type": "briefing", "data": "daily_briefing"})
            response = await self._generate_response(user_query, "\n\n".join(context_parts), conversation_history, images)
            return {
                "response": response,
                "sources": sources,
                "query_type": "daily_briefing",
            }

        # PRIORITY: Parlay tracker queries
        if classification.get("is_parlay_query") or classification.get("type") == "parlay_track":
            from backend.src.agents.parlay_tracker import get_today_parlays_context, get_parlay_record
            parlay_context = await get_today_parlays_context(db)
            record = await get_parlay_record(db, days=30)
            context_parts.append(parlay_context)
            if record.get("by_type"):
                record_lines = ["**Parlay Record (Last 30 Days)**"]
                for row in record["by_type"]:
                    record_lines.append(
                        f"- {row['parlay_name']}: {row['wins']}W / {row['losses']}L "
                        f"({row['win_rate']} win rate), avg leg hit rate: {row['avg_legs_hit_pct']}"
                    )
                context_parts.append("\n".join(record_lines))
            sources.append({"type": "parlay_tracker", "data": "model_parlays"})
            response = await self._generate_response(user_query, "\n\n".join(context_parts), conversation_history, images)
            return {
                "response": response,
                "sources": sources,
                "query_type": "parlay_track",
            }

        # PRIORITY: Check for Olympic betting queries first (parlay, value bets, edges)
        if classification.get("is_olympics_query") and classification.get("is_edge_query"):
            value_context = await self._fetch_olympic_value_bet(db, classification)
            if value_context:
                context_parts.append(f"## Olympic Value Bet Analysis\n{value_context}")
                sources.append({"type": "olympic_value", "data": "olympic_bet_calculator"})

        # Check if this is a prediction query (but not Olympic betting - handled above)
        elif classification.get("is_prediction_query") or classification.get("type") in ("matchup_prediction", "tonight_prediction"):
            # If Olympics prediction without betting context, use Olympics handler
            if classification.get("is_olympics_query"):
                olympics_context = await self._fetch_olympics_data(db, classification)
                if olympics_context:
                    context_parts.append(f"## Olympic Hockey - Milano Cortina 2026\n{olympics_context}")
                    sources.append({"type": "olympics", "data": "milano_cortina_2026"})
            else:
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

        # Check if this is a value/salary query
        elif classification.get("is_value_query") or classification.get("type") == "value_comparison":
            value_context = await self._fetch_value_comparison(db, classification)
            if value_context:
                context_parts.append(f"## Value Analysis\n{value_context}")
                sources.append({"type": "value", "data": "salary_cap"})

        # Check if this is an Olympic value bet query (backup - in case first check missed)
        elif classification.get("is_edge_query") and classification.get("is_olympics_query"):
            value_context = await self._fetch_olympic_value_bet(db, classification)
            if value_context:
                context_parts.append(f"## Olympic Value Bet Analysis\n{value_context}")
                sources.append({"type": "olympic_value", "data": "olympic_bet_calculator"})

        # Check if this is an edge finder query (best bets tonight)
        elif classification.get("is_edge_query") or classification.get("type") == "edge_finder":
            edge_context = await self._fetch_edge_analysis(db, classification)
            if edge_context:
                context_parts.append(f"## Betting Edge Analysis\n{edge_context}")
                sources.append({"type": "edges", "data": "edge_finder"})

        # Check if this is a regression query (xG underperformers/overperformers)
        elif classification.get("is_regression_query") or classification.get("type") == "regression":
            regression_context = await self._fetch_regression_analysis(db, classification)
            if regression_context:
                context_parts.append(f"## xG Regression Analysis\n{regression_context}")
                sources.append({"type": "regression", "data": "xg_regression"})

        # Check if this is an Olympics query
        elif classification.get("is_olympics_query") or classification.get("type") == "olympics":
            olympics_context = await self._fetch_olympics_data(db, classification)
            if olympics_context:
                context_parts.append(f"## Olympic Hockey - Milano Cortina 2026\n{olympics_context}")
                sources.append({"type": "olympics", "data": "milano_cortina_2026"})

        # Check if this is a recent results query (yesterday, last night, past games)
        elif classification.get("is_recent_results_query") or classification.get("type") == "recent_results":
            days_offset = classification.get("days_offset", 1)
            results_context = await self._fetch_recent_results(db, days_offset)
            if results_context:
                context_parts.append(f"## Recent Game Results\n{results_context}")
                sources.append({"type": "schedule", "data": "recent_results"})

        # Check if this is a schedule query (what games are today)
        elif classification.get("is_schedule_query") or classification.get("type") == "schedule":
            schedule_context = await self._fetch_todays_schedule(db, classification)
            if schedule_context:
                context_parts.append(f"## Today's Games\n{schedule_context}")
                sources.append({"type": "schedule", "data": "todays_games"})

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

        # Check if this is a multi-season / career leaders query
        elif classification.get("is_multi_season_query") and (
            classification.get("is_leaders_query") or classification.get("type") == "leaders"
        ):
            stats_requested = classification.get("stats", ["points"])
            seasons_count = classification.get("seasons_count")
            leaders_limit = max(classification.get("top_n") or 10, 10)
            multi_context = await self._fetch_multi_season_leaders(
                db, stats_requested, limit=leaders_limit, seasons_count=seasons_count
            )
            if multi_context:
                context_parts.append(f"## Multi-Season Leaders\n{multi_context}")
                sources.append({"type": "sql", "data": "multi_season_leaders"})

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
            # Use top_n from classification (handles "23rd best" etc.), minimum 25 so common
            # ordinal queries are covered even when the classifier returns a smaller number.
            leaders_limit = max(classification.get("top_n") or 25, 25)
            leaders_context = await self._fetch_league_leaders(db, stats_requested, limit=leaders_limit, season=season)
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
            # Pass query type for strategy-aware retrieval
            rag_results = await rag_service.search(
                db, user_query, limit=3,
                query_type=classification.get("type"),
            )
            if rag_results:
                # Format with citations for transparent sourcing
                rag_context = "\n\n".join([
                    f"### {doc['title'] or 'Document'} (source: {doc['source']})\n"
                    f"{doc['content']}\n"
                    f"*Citation: {doc.get('citation', '')}*"
                    for doc in rag_results
                ])
                context_parts.append(f"## Related Analysis\n{rag_context}")
                sources.append({
                    "type": "rag",
                    "data": rag_results,
                    "citations": [doc.get("citation", "") for doc in rag_results],
                })

        # Step 3: Generate response with Claude
        context = "\n\n".join(context_parts) if context_parts else "No specific data found in database."

        response = await self._generate_response(user_query, context, conversation_history, images)

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
    "type": "stats_lookup" | "comparison" | "trend_analysis" | "explainer" | "prediction" | "leaders" | "team_breakdown" | "matchup_prediction" | "tonight_prediction" | "trade_suggestion" | "value_comparison" | "edge_finder" | "regression" | "value_bet" | "olympics" | "schedule" | "daily_briefing",
    "players": ["player names mentioned"],
    "teams": ["team names or abbreviations - convert full names to abbreviations like TOR, BOS, EDM"],
    "countries": ["country names for Olympics - CAN, USA, SWE, FIN, RUS, CZE, SUI, GER, SVK, etc."],
    "stats": ["specific stats mentioned like goals, xG, corsi"],
    "timeframe": "current season" | "career" | "tonight" | "tomorrow" | "monday" | "tuesday" | "wednesday" | "thursday" | "friday" | "saturday" | "sunday" | "this week" | "feb 3" | "january 15" | null,
    "is_leaders_query": true if asking about league leaders/top players/who leads in a stat,
    "is_multi_season_query": true if asking about career stats, past/last multiple years, or historical performance spanning more than 1 season,
    "seasons_count": number if asking about last N seasons or years (e.g. "last 5 years" = 5, "past 3 seasons" = 3, null otherwise),
    "is_all_teams_query": true if asking about all teams or each team (e.g. "top 3 on each team", "best player per team"),
    "is_prediction_query": true if asking about who will score, predictions, who to start, fantasy advice for tonight/tomorrow/upcoming games,
    "is_tonight_query": true if asking about tonight's games, today's games, tomorrow's games, or upcoming games without specific teams,
    "is_trade_query": true if asking about trades, trade value, who to trade for, trade targets, or package deals,
    "is_value_query": true if asking about value, salary cap, contract, best value, points per dollar, cap hit, or cost efficiency,
    "is_edge_query": true if asking about edges, best bets, betting opportunities, value bets, or +EV plays,
    "is_regression_query": true if asking about regression, xG regression, due for goals, underperforming, overperforming, or shooting luck,
    "is_olympics_query": true if asking about Olympics, Olympic hockey, Team Canada/USA/Sweden, Milano Cortina 2026, or Olympic standings/stats,
    "is_schedule_query": true if asking about games today, tonight, what's playing, schedule, matchups,
    "is_recent_results_query": true if asking about past/completed games, who played yesterday, last night's games, recent results, scores, or what happened in a game,
    "days_offset": integer days back from today (0=today, 1=yesterday, 2=two days ago, etc.) - set when the query has a relative time reference,
    "is_briefing_query": true if asking for a daily briefing, morning digest, lineup summary, or today's overview,
    "is_parlay_query": true if asking about today's parlays, model picks, parlay tracker, parlay record, or how parlays are performing,
    "top_n": number if asking for top N players OR a specific rank (e.g. "top 3" = 3, "top 5" = 5, "23rd best" = 23, "10th" = 10, "who is ranked 15" = 15),
    "offered_odds": number if asking about a specific bet with odds (e.g. "+210" = 210, "-150" = -150)
}}

Examples:
- "Who will score in TOR vs BOS tonight?" -> type: "matchup_prediction", teams: ["TOR", "BOS"], is_prediction_query: true
- "Who should I start tonight?" -> type: "tonight_prediction", is_prediction_query: true, is_tonight_query: true
- "Predictions for Edmonton vs Calgary" -> type: "matchup_prediction", teams: ["EDM", "CGY"], is_prediction_query: true
- "Who is going to score in the leafs game tomorrow?" -> type: "matchup_prediction", teams: ["TOR"], is_prediction_query: true, timeframe: "tomorrow"
- "Best bets for Monday's games" -> type: "edge_finder", is_edge_query: true, is_tonight_query: true, timeframe: "monday"
- "What are the best edges tonight?" -> type: "edge_finder", is_edge_query: true, is_tonight_query: true
- "Any +EV plays tonight?" -> type: "value_bet", is_edge_query: true, is_tonight_query: true
- "Who is most likely to score on Tuesday?" -> type: "tonight_prediction", is_prediction_query: true, is_tonight_query: true, timeframe: "tuesday"
- "Who will score on Feb 3rd?" -> type: "tonight_prediction", is_prediction_query: true, is_tonight_query: true, timeframe: "feb 3"
- "Who should I start this week?" -> type: "tonight_prediction", is_prediction_query: true
- "Who should I trade McDavid for?" -> type: "trade_suggestion", players: ["McDavid"], is_trade_query: true
- "Trade value for Sherwood and Landeskog" -> type: "trade_suggestion", players: ["Sherwood", "Landeskog"], is_trade_query: true
- "Package Makar and Rantanen for who?" -> type: "trade_suggestion", players: ["Makar", "Rantanen"], is_trade_query: true
- "Who is better value, Cuylle or Matthews?" -> type: "value_comparison", players: ["Cuylle", "Matthews"], is_value_query: true
- "Best value players in the league" -> type: "value_comparison", is_value_query: true, is_leaders_query: true
- "Points per dollar leaders" -> type: "value_comparison", is_value_query: true, is_leaders_query: true
- "What's McDavid's cap hit?" -> type: "value_comparison", players: ["McDavid"], is_value_query: true
- "Who is due for positive regression?" -> type: "regression", is_regression_query: true
- "Players underperforming their xG?" -> type: "regression", is_regression_query: true
- "Is McDavid overperforming?" -> type: "regression", players: ["McDavid"], is_regression_query: true
- "Shooting luck leaders" -> type: "regression", is_regression_query: true, is_leaders_query: true
- "Is Matthews +180 good value?" -> type: "value_bet", players: ["Matthews"], is_edge_query: true
- "Is Celebrini +210 good value against Switzerland?" -> type: "value_bet", players: ["Celebrini"], countries: ["SUI"], is_edge_query: true, is_olympics_query: true, offered_odds: 210
- "McDavid anytime scorer +150 vs Czech Republic" -> type: "value_bet", players: ["McDavid"], countries: ["CZE"], is_edge_query: true, is_olympics_query: true, offered_odds: 150
- "Olympic standings?" -> type: "olympics", is_olympics_query: true
- "How is Canada doing in the Olympics?" -> type: "olympics", countries: ["CAN"], is_olympics_query: true
- "Olympic scoring leaders?" -> type: "olympics", is_olympics_query: true, is_leaders_query: true
- "How is McDavid doing in the Olympics?" -> type: "olympics", players: ["McDavid"], is_olympics_query: true
- "Who is leading the Olympics in goals?" -> type: "olympics", is_olympics_query: true, is_leaders_query: true
- "Sweden vs Finland Olympic game?" -> type: "olympics", countries: ["SWE", "FIN"], is_olympics_query: true
- "Who will score in Canada vs USA Olympic game?" -> type: "olympics", countries: ["CAN", "USA"], is_olympics_query: true, is_prediction_query: true
- "Predictions for Sweden vs Finland Olympics" -> type: "olympics", countries: ["SWE", "FIN"], is_olympics_query: true, is_prediction_query: true
- "Olympic predictions for the gold medal game?" -> type: "olympics", is_olympics_query: true, is_prediction_query: true
- "Why does McDavid have higher goal probability than MacKinnon in the Olympic game?" -> type: "olympics", players: ["McDavid", "MacKinnon"], is_olympics_query: true, is_prediction_query: true
- "Explain the goal probability for Canada vs Switzerland" -> type: "olympics", countries: ["CAN", "SUI"], is_olympics_query: true, is_prediction_query: true
- "Why is player X more likely to score than player Y in the Olympics?" -> type: "olympics", is_olympics_query: true, is_prediction_query: true
- "How is the Olympic goal probability calculated?" -> type: "olympics", is_olympics_query: true, is_prediction_query: true
- "What games are today?" -> type: "schedule", is_schedule_query: true
- "Who is playing tonight?" -> type: "schedule", is_schedule_query: true
- "Any games on right now?" -> type: "schedule", is_schedule_query: true
- "What's the schedule for today?" -> type: "schedule", is_schedule_query: true
- "Which teams play tonight?" -> type: "schedule", is_schedule_query: true
- "Who played yesterday?" -> type: "recent_results", is_recent_results_query: true, days_offset: 1
- "What happened last night?" -> type: "recent_results", is_recent_results_query: true, days_offset: 1
- "Last night's scores" -> type: "recent_results", is_recent_results_query: true, days_offset: 1
- "What were the results yesterday?" -> type: "recent_results", is_recent_results_query: true, days_offset: 1
- "Who scored two nights ago?" -> type: "recent_results", is_recent_results_query: true, days_offset: 2
- "Games from March 9th?" -> type: "recent_results", is_recent_results_query: true, days_offset: 2
- "Recap Monday's games" -> type: "recent_results", is_recent_results_query: true, days_offset: 1
- "Daily briefing" -> type: "daily_briefing", is_briefing_query: true
- "Give me today's briefing" -> type: "daily_briefing", is_briefing_query: true
- "Morning digest" -> type: "daily_briefing", is_briefing_query: true
- "What do I need to know today?" -> type: "daily_briefing", is_briefing_query: true
- "Show me today's parlays" -> type: "parlay_track", is_parlay_query: true
- "What parlays do you have today?" -> type: "parlay_track", is_parlay_query: true
- "How are the model parlays doing?" -> type: "parlay_track", is_parlay_query: true
- "Parlay record" -> type: "parlay_track", is_parlay_query: true
- "How accurate are the model picks?" -> type: "parlay_track", is_parlay_query: true
- "Show me the parlay tracker" -> type: "parlay_track", is_parlay_query: true
- "Who are the best ten players over the past five years?" -> type: "leaders", is_leaders_query: true, is_multi_season_query: true, seasons_count: 5, stats: ["points"]
- "Most goals in the last 3 seasons?" -> type: "leaders", is_leaders_query: true, is_multi_season_query: true, seasons_count: 3, stats: ["goals"]
- "Career stats for McDavid" -> type: "stats_lookup", players: ["McDavid"], is_multi_season_query: true
- "Top scorers over the past 3 years?" -> type: "leaders", is_leaders_query: true, is_multi_season_query: true, seasons_count: 3, stats: ["points"]"""
                }
            ],
        )

        try:
            # Safely access message content
            if not message.content or len(message.content) == 0:
                logger.warning("empty_message_content")
                return {"type": "unknown", "players": [], "teams": [], "stats": []}

            text = message.content[0].text
            # Try to extract JSON from markdown code blocks if present
            if "```" in text:
                import re
                json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
                if json_match:
                    text = json_match.group(1)
            return json.loads(text)
        except (json.JSONDecodeError, AttributeError, IndexError) as e:
            # Safely log without re-accessing potentially problematic content
            raw_preview = ""
            try:
                if message.content and len(message.content) > 0:
                    raw_preview = str(message.content[0].text)[:200]
            except Exception:
                raw_preview = "could not extract"
            logger.warning("classification_parse_error", error=str(e), raw_text=raw_preview)
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
                    p.birth_date,
                    p.cap_hit_cents,
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
        from datetime import date
        stats_text = []
        for row in rows:
            # Calculate age if birth_date is available
            age_str = ""
            if row.birth_date:
                today = date.today()
                age = today.year - row.birth_date.year
                # Adjust if birthday hasn't occurred this year
                if (today.month, today.day) < (row.birth_date.month, row.birth_date.day):
                    age -= 1
                age_str = f", Age: {age}"

            # Format cap hit if available
            cap_str = ""
            if row.cap_hit_cents and row.cap_hit_cents > 0:
                cap_millions = row.cap_hit_cents / 100_000_000
                cap_str = f", Cap Hit: ${cap_millions:.2f}M"

            # Format TOI properly (should be in minutes)
            toi_str = f"{row.toi_per_game:.1f} min" if row.toi_per_game and row.toi_per_game > 0 else "N/A"

            stats_text.append(
                f"**{row.name}** ({row.position or 'F'}, {row.team_abbrev}{age_str}{cap_str}) - {row.season or 'Career'}:\n"
                f"  GP: {row.games_played}, G: {row.goals}, A: {row.assists}, P: {row.points}\n"
                f"  xG: {row.xg or 0:.2f}, CF%: {row.corsi_for_pct or 50:.1f}%, TOI/G: {toi_str}"
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
        display_season = self._format_season_display(rows[0].season if rows else None)

        team_names = ", ".join(team_abbrevs)
        stats_text = [f"**{team_names} players ranked by {stat_label} ({display_season} season):**\n"]
        for i, row in enumerate(rows, 1):
            base_stats = f"GP: {row.games_played or 0}, G: {row.goals or 0}, A: {row.assists or 0}, P: {row.points or 0}"
            xg_str = f", xG: {row.xg:.1f}" if row.xg else ""
            stats_text.append(
                f"{i}. **{row.name or 'Unknown'}** ({row.position or 'F'}, {row.team_abbrev or 'N/A'}):\n"
                f"   {base_stats}{xg_str}"
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
        display_season = self._format_season_display(latest_season)

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

    async def _fetch_multi_season_leaders(
        self,
        db: AsyncSession,
        stats: list[str],
        limit: int = 10,
        seasons_count: int | None = None,
    ) -> str | None:
        """Fetch aggregated leaders across multiple seasons."""
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

        # Get available seasons ordered newest first
        seasons_result = await db.execute(
            text("SELECT DISTINCT season FROM player_season_stats ORDER BY season DESC")
        )
        available = [row[0] for row in seasons_result.fetchall()]
        if not available:
            return None

        if seasons_count:
            selected = available[:seasons_count]
        else:
            selected = available

        if not selected:
            return None

        placeholders = ", ".join([f":s{i}" for i in range(len(selected))])
        params = {f"s{i}": s for i, s in enumerate(selected)}
        params["limit"] = limit

        result = await db.execute(
            text(f"""
                SELECT
                    p.name,
                    p.position,
                    p.team_abbrev,
                    COUNT(DISTINCT s.season) AS seasons,
                    SUM(s.games_played) AS total_gp,
                    SUM(s.goals) AS total_goals,
                    SUM(s.assists) AS total_assists,
                    SUM(s.points) AS total_points,
                    ROUND(SUM(s.xg)::numeric, 1) AS total_xg
                FROM players p
                JOIN player_season_stats s ON p.id = s.player_id
                WHERE s.season IN ({placeholders})
                  AND s.{sort_column} IS NOT NULL
                GROUP BY p.id, p.name, p.position, p.team_abbrev
                HAVING SUM(s.games_played) >= 20
                ORDER BY SUM(s.{sort_column}) DESC
                LIMIT :limit
            """),
            params,
        )

        rows = result.fetchall()
        if not rows:
            return None

        # Format season range label
        year_range = f"{selected[-1][:4]}-{selected[0][4:]}" if len(selected) > 1 else selected[0][:4]
        seasons_label = f"last {len(selected)} seasons" if seasons_count else f"all available seasons ({year_range})"

        stats_text = [f"**Top {limit} players by {stat_label} - {seasons_label} (aggregated):**\n"]
        for i, row in enumerate(rows, 1):
            stats_text.append(
                f"{i}. **{row.name}** ({row.position or 'F'}, {row.team_abbrev or 'N/A'}):\n"
                f"   {int(row.seasons)} seasons, GP: {int(row.total_gp)}, "
                f"G: {int(row.total_goals)}, A: {int(row.total_assists)}, P: {int(row.total_points)}"
                + (f", xG: {row.total_xg}" if row.total_xg else "")
            )

        return "\n".join(stats_text)

    async def _fetch_league_leaders(
        self,
        db: AsyncSession,
        stats: list[str],
        limit: int = 25,
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

        # Format season for display
        display_season = self._format_season_display(rows[0].season if rows else None)

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
            normalized_teams = self._normalize_teams(teams)
            team_abbrev = normalized_teams[0] if normalized_teams else None
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

                # ── Fetch live market odds once for all games ──────────
                market_probs: dict[str, float] = {}
                try:
                    from backend.src.agents.odds_value import OddsValueCalculator
                    odds_calc = OddsValueCalculator(db)
                    all_odds_raw, _ = await odds_calc.get_live_odds()
                    for lines in (all_odds_raw or {}).values():
                        for ol in lines:
                            if "goal" in ol.market.lower() or "scorer" in ol.market.lower():
                                key = ol.player_name.lower()
                                if key not in market_probs or ol.implied_probability < market_probs[key]:
                                    market_probs[key] = ol.implied_probability
                except Exception:
                    pass  # odds are optional - degrade gracefully

                all_top_scorers = []
                for game in games[:10]:  # Process up to 10 games
                    try:
                        matchup = await prediction_engine.get_matchup_prediction(
                            db, game["home_team"], game["away_team"], target_date, top_n=10
                        )
                        all_top_scorers.extend(matchup.top_scorers)

                        predictions_text.append(f"\n### {game['away_team']} @ {game['home_team']}")
                        if game.get("venue"):
                            predictions_text.append(f"*{game['venue']}*")

                        predictions_text.append("\n**Top Goal Scorers:**")
                        for i, pred in enumerate(matchup.top_scorers[:5], 1):
                            prob_pct = int(pred.prob_goal * 100)
                            line = f"{i}. **{pred.player_name}** ({pred.team}) - Model: {prob_pct}%"
                            mkt = market_probs.get(pred.player_name.lower())
                            if mkt:
                                mkt_pct = int(mkt * 100)
                                edge = prob_pct - mkt_pct
                                edge_str = f" (+{edge}% edge)" if edge >= 5 else (f" ({edge}% edge)" if edge < -5 else "")
                                line += f" | Market: {mkt_pct}%{edge_str}"
                            predictions_text.append(line)
                            if pred.factors:
                                predictions_text.append(f"   _{pred.factors[0]}_")
                    except Exception as e:
                        logger.warning("game_prediction_failed", game=game, error=str(e))
                        continue

                # Add overall top scorers across all tonight's games (top 15 for full follow-up coverage)
                all_top_scorers.sort(key=lambda p: p.prob_goal, reverse=True)
                if all_top_scorers:
                    has_odds = bool(market_probs)
                    predictions_text.append("\n### Overall Best Bets Tonight")
                    if has_odds:
                        predictions_text.append(
                            "_Model probability vs live market implied probability. "
                            "Positive edge = model sees more value than the market._\n"
                        )
                    for i, pred in enumerate(all_top_scorers[:15], 1):
                        prob_pct = int(pred.prob_goal * 100)
                        matchup_str = f"vs {pred.opponent}" if pred.is_home else f"@ {pred.opponent}"
                        line = (
                            f"{i}. **{pred.player_name}** ({pred.team} {matchup_str}) - "
                            f"Model: {prob_pct}% | Point: {int(pred.prob_point * 100)}%"
                        )
                        mkt = market_probs.get(pred.player_name.lower())
                        if mkt:
                            mkt_pct = int(mkt * 100)
                            edge = prob_pct - mkt_pct
                            edge_str = f" **Edge: +{edge}%**" if edge >= 5 else (
                                f" Edge: {edge}%" if edge < -5 else f" Edge: {edge:+d}%"
                            )
                            line += f" | Market: {mkt_pct}%{edge_str}"
                        predictions_text.append(line)

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
        for i, pred in enumerate(prediction.top_scorers[:10], 1):
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
            lines.append(f"- {p['name']} ({p['team']}, {p['position']}): {p['points']} pts in {p['games']} GP ({p['ppg']} PPG), {xg:.1f} xG - Value: {p['value']}")

        lines.append(f"\n**Combined Trade Value:** {total_value:.1f}")
        lines.append(f"\n**Comparable Trade Targets** (value range {value_min:.1f} - {value_max:.1f}):")

        if targets:
            for t in targets:
                gp = t.games_played or 1
                ppg = round((t.points or 0) / gp, 2)
                xg = t.xg or 0
                lines.append(f"- {t.name} ({t.team_abbrev}, {t.position}): {t.points} pts ({ppg} PPG), {xg:.1f} xG - Value: {t.value:.1f}")
        else:
            lines.append("No comparable players found in the current season stats.")

        lines.append("\n**Trade Recommendation:** Target players with similar or slightly higher value scores. Higher xG suggests a player may be due for positive regression.")

        return "\n".join(lines)

    async def _fetch_value_comparison(
        self,
        db: AsyncSession,
        classification: dict,
    ) -> str | None:
        """Fetch salary cap value analysis for players."""
        players = classification.get("players", [])
        is_leaders = classification.get("is_leaders_query", False)

        lines = []

        # Disclaimer
        lines.append("*Note: Salary data as of Feb 2025. Contract values may have changed due to trades, extensions, or new signings.*\n")

        if players:
            # Compare specific players
            from datetime import date
            lines.append("**Player Value Comparison:**\n")
            for player_name in players:
                if not player_name:
                    continue
                result = await db.execute(
                    text("""
                        SELECT p.name, p.team_abbrev, p.position, p.cap_hit_cents, p.birth_date,
                               s.goals, s.assists, s.points, s.games_played, s.xg, s.toi_per_game
                        FROM players p
                        JOIN player_season_stats s ON p.id = s.player_id
                        WHERE LOWER(p.name) LIKE :name
                        AND s.season = (SELECT MAX(season) FROM player_season_stats)
                        LIMIT 1
                    """),
                    {"name": f"%{player_name.lower()}%"},
                )
                row = result.fetchone()
                if row:
                    cap = row.cap_hit_cents / 100 if row.cap_hit_cents else None
                    gp = row.games_played or 1
                    ppg = round((row.points or 0) / gp, 2)

                    # Calculate age
                    age_str = ""
                    if row.birth_date:
                        today = date.today()
                        age = today.year - row.birth_date.year
                        if (today.month, today.day) < (row.birth_date.month, row.birth_date.day):
                            age -= 1
                        age_str = f", Age {age}"

                    # Format TOI
                    toi_str = f"{row.toi_per_game:.1f} min/game" if row.toi_per_game and row.toi_per_game > 0 else ""

                    if cap and cap > 0:
                        cap_in_millions = cap / 1_000_000
                        pts_per_mil = round(row.points / cap_in_millions, 1) if row.points and cap_in_millions > 0 else 0
                        cost_per_point = round(cap / row.points, 0) if row.points and row.points > 0 else None
                        lines.append(f"**{row.name}** ({row.team_abbrev}, {row.position}{age_str})")
                        lines.append(f"- Cap Hit: ${cap:,.0f}")
                        lines.append(f"- Stats: {row.goals}G, {row.assists}A, {row.points}P in {row.games_played} GP ({ppg} PPG)")
                        # Add xG comparison for shooting luck analysis
                        xg = row.xg or 0
                        goals = row.goals or 0
                        if xg > 0:
                            goal_diff = goals - xg
                            luck_indicator = f"+{goal_diff:.1f}" if goal_diff >= 0 else f"{goal_diff:.1f}"
                            lines.append(f"- xG: {xg:.2f} (Goals vs xG: {luck_indicator})")
                        if toi_str:
                            lines.append(f"- Ice Time: {toi_str}")
                        lines.append(f"- Value: **{pts_per_mil} pts/$1M** (${cost_per_point:,.0f}/point)")
                        lines.append("")
                    else:
                        xg = row.xg or 0
                        goals = row.goals or 0
                        lines.append(f"**{row.name}** ({row.team_abbrev}{age_str})")
                        lines.append(f"- Stats: {row.goals}G, {row.assists}A, {row.points}P in {row.games_played} GP ({ppg} PPG)")
                        if xg > 0:
                            goal_diff = goals - xg
                            luck_indicator = f"+{goal_diff:.1f}" if goal_diff >= 0 else f"{goal_diff:.1f}"
                            lines.append(f"- xG: {xg:.2f} (Goals vs xG: {luck_indicator})")
                        if toi_str:
                            lines.append(f"- Ice Time: {toi_str}")
                        lines.append(f"- Cap Hit: *Not available*")
                        lines.append("")

        if is_leaders or not players:
            # Show league-wide value leaders
            result = await db.execute(
                text("""
                    SELECT p.name, p.team_abbrev, p.cap_hit_cents, s.points, s.games_played,
                           ROUND(s.points::numeric / NULLIF(p.cap_hit_cents/100000000.0, 0), 1) as pts_per_mil
                    FROM players p
                    JOIN player_season_stats s ON p.id = s.player_id
                    WHERE p.cap_hit_cents IS NOT NULL AND p.cap_hit_cents > 0
                    AND s.season = (SELECT MAX(season) FROM player_season_stats)
                    AND s.games_played >= 20
                    ORDER BY pts_per_mil DESC
                    LIMIT 10
                """)
            )
            leaders = result.fetchall()

            if leaders:
                lines.append("**Best Value Players (Points per $1M cap hit):**\n")
                lines.append("| Rank | Player | Team | Cap Hit | Points | Pts/$1M |")
                lines.append("|------|--------|------|---------|--------|---------|")
                for i, row in enumerate(leaders, 1):
                    cap = row.cap_hit_cents / 100
                    lines.append(f"| {i} | {row.name} | {row.team_abbrev} | ${cap:,.0f} | {row.points} | **{row.pts_per_mil}** |")

        if not lines or len(lines) <= 2:
            return None

        return "\n".join(lines)

    async def _fetch_edge_analysis(
        self,
        db: AsyncSession,
        classification: dict,
    ) -> str | None:
        """Fetch betting edge analysis for tonight's games, with live odds comparison."""
        from backend.src.agents.edge_finder import EdgeFinder
        from backend.src.agents.odds_value import OddsValueCalculator

        # Run edge finder and live odds fetch concurrently
        finder = EdgeFinder(db)
        calc = OddsValueCalculator(db)

        import asyncio
        report, (live_odds, remaining) = await asyncio.gather(
            finder.find_tonight_edges(min_grade="B+", max_results=15),
            calc.get_live_odds("icehockey_nhl"),
        )

        if not report.top_edges:
            return "No games scheduled tonight or no significant edges found."

        # Build lookup: player_name_lower -> best OddsLine across sportsbooks
        best_odds_by_player: dict[str, object] = {}
        for odds_list in live_odds.values():
            for line in odds_list:
                key = line.player_name.lower()
                existing = best_odds_by_player.get(key)
                # Prefer the line with the highest payout for the bettor
                if existing is None or line.odds > existing.odds:
                    best_odds_by_player[key] = line

        has_live_odds = bool(live_odds)
        from backend.src.agents.odds_value import ODDS_API_KEY as _ODDS_KEY
        if has_live_odds:
            odds_label = f"Live sportsbook odds ({remaining} API calls remaining)"
        elif _ODDS_KEY:
            odds_label = "Odds API key set but no player prop lines returned. Key may require player_props tier at the-odds-api.com. Showing model-estimated fair odds."
        else:
            odds_label = "No Odds API key configured. Showing model-estimated fair odds only."

        lines = []
        lines.append(f"**Tonight's Betting Edges** ({report.game_count} games, {report.edges_found} opportunities)\n")
        lines.append(f"Odds source: {odds_label}\n")

        if has_live_odds:
            lines.append("| Player | Team | Grade | Model | Market | Edge | Best Odds | Book | Key Factor |")
            lines.append("|--------|------|-------|-------|--------|------|-----------|------|------------|")
        else:
            lines.append("| Player | Team | Grade | Model | Est. Fair Odds | Key Factor |")
            lines.append("|--------|------|-------|-------|----------------|------------|")

        for edge in report.top_edges[:10]:
            key_factor = edge.edge_factors[0].description if edge.edge_factors else "Multiple factors"
            if len(key_factor) > 45:
                key_factor = key_factor[:42] + "..."

            market_line = best_odds_by_player.get(edge.player_name.lower())

            if has_live_odds and market_line:
                market_pct = f"{market_line.implied_probability:.1%}"
                edge_pct = edge.prob_goal - market_line.implied_probability
                edge_str = f"+{edge_pct:.1%}" if edge_pct >= 0 else f"{edge_pct:.1%}"
                lines.append(
                    f"| {edge.player_name} | {edge.team} | **{edge.edge_grade}** | "
                    f"{edge.prob_goal:.1%} | {market_pct} | {edge_str} | "
                    f"{market_line.odds:+d} | {market_line.sportsbook} | {key_factor} |"
                )
            elif has_live_odds:
                # No odds found for this player
                lines.append(
                    f"| {edge.player_name} | {edge.team} | **{edge.edge_grade}** | "
                    f"{edge.prob_goal:.1%} | - | - | - | No line | {key_factor} |"
                )
            else:
                lines.append(
                    f"| {edge.player_name} | {edge.team} | **{edge.edge_grade}** | "
                    f"{edge.prob_goal:.1%} | {edge.estimated_fair_odds:+d} | {key_factor} |"
                )

        lines.append("\n**Reading the table:**")
        lines.append("- Model = PowerplAI goal probability | Market = sportsbook implied probability")
        lines.append("- Edge = model minus market (positive = model sees more value than sportsbook)")
        lines.append("- Best Odds = best available American odds across books")

        return "\n".join(lines)

    async def _fetch_olympic_value_bet(
        self,
        db: AsyncSession,
        classification: dict,
    ) -> str | None:
        """
        Analyze a value bet for an Olympic hockey game.

        Combines:
        - Player's NHL stats (current season)
        - Olympic matchup context (goalie, team strength)
        - Offered odds from the query
        - Clear +EV calculation
        """
        import math
        from backend.src.ingestion.olympics import (
            get_current_olympic_data,
            get_country_code,
        )

        players = classification.get("players", [])
        countries = classification.get("countries", [])
        offered_odds = classification.get("offered_odds")

        # If no specific player, provide parlay/multi-game analysis
        if not players:
            return await self._fetch_olympic_parlay_analysis(db, classification)

        player_name = players[0]
        lines = []

        # Get player's NHL stats
        result = await db.execute(
            text("""
                SELECT p.name, p.team_abbrev, p.position,
                       s.goals, s.assists, s.points, s.games_played, s.xg
                FROM players p
                JOIN player_season_stats s ON p.id = s.player_id
                WHERE p.name ILIKE :name
                  AND s.season = (SELECT MAX(season) FROM player_season_stats)
                LIMIT 1
            """),
            {"name": f"%{player_name}%"}
        )
        row = result.fetchone()

        if not row:
            return f"Could not find NHL stats for {player_name}."

        # Calculate NHL metrics
        nhl_gpg = row.goals / row.games_played if row.games_played > 0 else 0
        nhl_ppg = row.points / row.games_played if row.games_played > 0 else 0
        xg = row.xg if row.xg else row.goals  # Fallback if no xG

        lines.append(f"## {row.name} Value Bet Analysis\n")
        lines.append(f"**NHL 2025-26 Stats:** {row.goals}G, {row.assists}A, {row.points}P in {row.games_played} GP ({row.team_abbrev})")
        lines.append(f"- Goals per game: **{nhl_gpg:.3f}** ({nhl_gpg*82:.0f} goal pace)")
        lines.append(f"- xG: {xg:.1f} (Actual vs xG: {row.goals - xg:+.1f})")
        lines.append("")

        # Get Olympic context
        olympic_data = get_current_olympic_data()

        # Determine opponent country - convert name to code if needed
        opponent_code = None
        opponent_country = None
        opponent_goalie = None
        player_country = None

        # Map country names to codes
        name_to_code = {
            "SWITZERLAND": "SUI", "SUI": "SUI",
            "CZECHIA": "CZE", "CZECH REPUBLIC": "CZE", "CZE": "CZE",
            "UNITED STATES": "USA", "USA": "USA", "AMERICA": "USA",
            "CANADA": "CAN", "CAN": "CAN",
            "SWEDEN": "SWE", "SWE": "SWE",
            "FINLAND": "FIN", "FIN": "FIN",
            "GERMANY": "GER", "GER": "GER",
            "SLOVAKIA": "SVK", "SVK": "SVK",
            "FRANCE": "FRA", "FRA": "FRA",
            "DENMARK": "DEN", "DEN": "DEN",
            "LATVIA": "LAT", "LAT": "LAT",
            "ITALY": "ITA", "ITA": "ITA",
            "NORWAY": "NOR", "NOR": "NOR",
        }
        code_to_name = {"SUI": "Switzerland", "CZE": "Czechia", "USA": "United States",
                       "CAN": "Canada", "SWE": "Sweden", "FIN": "Finland", "GER": "Germany",
                       "SVK": "Slovakia", "FRA": "France", "DEN": "Denmark", "LAT": "Latvia",
                       "ITA": "Italy", "NOR": "Norway"}

        # Convert all countries to codes
        country_codes = []
        for c in countries:
            code = name_to_code.get(c.upper(), c.upper())
            country_codes.append(code)

        # Determine player's Olympic team based on their NHL team
        # Canadian NHL players play for Canada, American for USA, etc.
        nhl_team_to_olympic = {
            # Canadian teams -> CAN
            "TOR": "CAN", "MTL": "CAN", "OTT": "CAN", "CGY": "CAN", "EDM": "CAN",
            "VAN": "CAN", "WPG": "CAN",
            # US teams - could be USA or other, default to checking name
        }

        # Most North American players: Canadian-born -> CAN, American-born -> USA
        # For simplicity, if query mentions "against X", X is the opponent
        # If two countries mentioned, the one that's NOT CAN/USA is likely the opponent
        # for a North American player

        if len(country_codes) >= 2:
            # If one is CAN/USA and other isn't, the non-CAN/USA is likely opponent
            if "CAN" in country_codes or "USA" in country_codes:
                for code in country_codes:
                    if code not in ("CAN", "USA"):
                        opponent_code = code
                        break
            if not opponent_code:
                # Both are CAN/USA or neither, use second one
                opponent_code = country_codes[1]
        elif len(country_codes) == 1:
            opponent_code = country_codes[0]

        if opponent_code:
            opponent_country = code_to_name.get(opponent_code, opponent_code)

            # Find opponent goalie
            for goalie in olympic_data.get("goalie_leaders", []):
                if goalie["country"] == opponent_code:
                    opponent_goalie = goalie
                    break

        # Calculate expected goals for this game
        base_expected = nhl_gpg * 0.85  # Tournament discount

        goalie_adj = 0.0
        if opponent_goalie:
            sv_pct = opponent_goalie.get("sv", 0.905)
            sv_diff = 0.905 - sv_pct  # League avg is ~.905
            goalie_adj = sv_diff * 0.5  # Each 1% better = -0.5% expected goals
            lines.append(f"**Opponent Goalie:** {opponent_goalie['name']} ({opponent_country})")
            lines.append(f"- Save %: {sv_pct:.3f} | GAA: {opponent_goalie.get('gaa', 0):.2f}")
            if sv_pct > 0.940:
                lines.append(f"- **ELITE GOALIE** - Significantly reduces scoring chances")
            elif sv_pct < 0.900:
                lines.append(f"- **WEAK GOALIE** - Good matchup for scorers")
            lines.append("")

        adjusted_expected = max(0.05, base_expected + goalie_adj)

        # Probability calculation (Poisson)
        prob_goal = 1 - math.exp(-adjusted_expected)

        lines.append(f"**Model Probability:** {prob_goal*100:.1f}% chance to score")
        lines.append(f"- Base (NHL rate adjusted): {base_expected:.3f} expected goals")
        lines.append(f"- Goalie adjustment: {goalie_adj:+.3f}")
        lines.append(f"- Final expected goals: {adjusted_expected:.3f}")
        lines.append("")

        # Value calculation
        if offered_odds:
            if offered_odds > 0:
                implied_prob = 100 / (offered_odds + 100)
            else:
                implied_prob = abs(offered_odds) / (abs(offered_odds) + 100)

            edge = prob_goal - implied_prob

            lines.append(f"**Odds Analysis:**")
            lines.append(f"- Offered odds: **+{offered_odds}** (implied {implied_prob*100:.1f}%)")
            lines.append(f"- Model probability: **{prob_goal*100:.1f}%**")
            lines.append(f"- Edge: **{edge*100:+.1f}%**")
            lines.append("")

            if edge > 0.05:
                ev = (prob_goal * (offered_odds/100)) - (1 - prob_goal)
                lines.append(f"**VERDICT: GOOD VALUE** - {ev*100:.1f}% expected ROI")
                lines.append(f"Recommendation: BET - You have a {edge*100:.1f}% edge over the market")
            elif edge > 0.02:
                lines.append(f"**VERDICT: MARGINAL VALUE** - Small edge of {edge*100:.1f}%")
                lines.append(f"Recommendation: Small bet only, high variance")
            elif edge > -0.02:
                lines.append(f"**VERDICT: FAIR PRICE** - No significant edge either way")
                lines.append(f"Recommendation: PASS - Look for better opportunities")
            else:
                lines.append(f"**VERDICT: NO VALUE** - Market has you beat by {abs(edge)*100:.1f}%")
                lines.append(f"Recommendation: DO NOT BET at these odds")

            # Show breakeven
            if prob_goal >= 0.5:
                breakeven_odds = int(-100 * prob_goal / (1 - prob_goal))
            else:
                breakeven_odds = int(100 * (1 - prob_goal) / prob_goal)
            lines.append(f"\nBreakeven odds: {'+' if breakeven_odds > 0 else ''}{breakeven_odds}")
        else:
            lines.append("**Note:** Specify the offered odds (e.g., '+210') for a complete value analysis")

        return "\n".join(lines)

    async def _fetch_olympic_parlay_analysis(
        self,
        db: AsyncSession,
        classification: dict,
    ) -> str | None:
        """
        Analyze Olympic games for parlay betting opportunities.

        Fetches all upcoming games and identifies best scoring opportunities.
        """
        import math
        from backend.src.ingestion.olympics import (
            get_current_olympic_data,
            predict_olympic_game,
        )

        olympic_data = get_current_olympic_data()
        upcoming_games = olympic_data.get("upcoming_games", [])

        if not upcoming_games:
            return "No upcoming Olympic games found in the schedule."

        lines = []
        lines.append("## Olympic Parlay Analysis\n")
        lines.append("**IMPORTANT:** No bet is ever 'guaranteed'. These are statistical probabilities, not certainties.\n")

        all_predictions = []

        # Get predictions for each upcoming game
        for game in upcoming_games:
            try:
                pred = await predict_olympic_game(
                    db, game["home"], game["away"], game.get("round", "group")
                )

                for player in pred.get("top_scorers", [])[:3]:
                    all_predictions.append({
                        "player": player["player_name"],
                        "country": player.get("country_code") or player.get("country"),
                        "opponent": player["opponent_code"],
                        "prob_goal": player["prob_goal"],
                        "prob_point": player["prob_point"],
                        "confidence": player.get("confidence", "medium"),
                        "game": f"{game['away']} @ {game['home']}",
                    })
            except Exception as e:
                logger.warning("olympic_prediction_failed", game=game, error=str(e))
                continue

        if not all_predictions:
            return "Could not generate predictions for Olympic games."

        # Sort by goal probability
        all_predictions.sort(key=lambda x: x["prob_goal"], reverse=True)

        # Best individual picks
        lines.append("### Top Scoring Probabilities\n")
        lines.append("| Player | Team | vs | P(Goal) | P(Point) | Game |")
        lines.append("|--------|------|-----|---------|----------|------|")

        for pred in all_predictions[:8]:
            lines.append(
                f"| **{pred['player']}** | {pred['country']} | {pred['opponent']} | "
                f"{pred['prob_goal']*100:.0f}% | {pred['prob_point']*100:.0f}% | {pred['game']} |"
            )

        # Suggested parlay (top 3 by probability)
        lines.append("\n### Suggested 3-Leg Parlay (Highest Probability)\n")

        top_3 = all_predictions[:3]
        if len(top_3) >= 3:
            # Calculate combined probability
            combined_prob = 1.0
            for p in top_3:
                combined_prob *= p["prob_goal"]

            lines.append("**Anytime Scorer Parlay:**")
            for i, pred in enumerate(top_3, 1):
                lines.append(f"{i}. **{pred['player']}** ({pred['country']}) to score vs {pred['opponent']} - {pred['prob_goal']*100:.0f}%")

            lines.append(f"\n**Combined Probability:** {combined_prob*100:.1f}%")

            # Estimate fair parlay odds
            if combined_prob > 0:
                if combined_prob >= 0.5:
                    fair_odds = int(-100 * combined_prob / (1 - combined_prob))
                else:
                    fair_odds = int(100 * (1 - combined_prob) / combined_prob)
                lines.append(f"**Fair Odds:** {'+' if fair_odds > 0 else ''}{fair_odds}")

            lines.append("\n**Note:** Actual parlay odds from sportsbooks will be lower due to vig. This parlay has ~{:.0f}% chance of hitting.".format(combined_prob*100))

        # Alternative: Point parlay (higher probability)
        lines.append("\n### Alternative: Point Parlay (Higher Hit Rate)\n")
        top_3_points = sorted(all_predictions[:6], key=lambda x: x["prob_point"], reverse=True)[:3]

        if len(top_3_points) >= 3:
            combined_point_prob = 1.0
            for p in top_3_points:
                combined_point_prob *= p["prob_point"]

            lines.append("**1+ Point Each:**")
            for i, pred in enumerate(top_3_points, 1):
                lines.append(f"{i}. **{pred['player']}** ({pred['country']}) 1+ points vs {pred['opponent']} - {pred['prob_point']*100:.0f}%")

            lines.append(f"\n**Combined Probability:** {combined_point_prob*100:.1f}%")

            # Calculate fair odds and estimated payout for points parlay
            if combined_point_prob > 0:
                if combined_point_prob >= 0.5:
                    point_fair_odds = int(-100 * combined_point_prob / (1 - combined_point_prob))
                else:
                    point_fair_odds = int(100 * (1 - combined_point_prob) / combined_point_prob)
                lines.append(f"**Fair Odds:** {'+' if point_fair_odds > 0 else ''}{point_fair_odds}")

                # Estimate payout (assuming ~10% vig reduction)
                actual_odds_est = int(point_fair_odds * 0.90) if point_fair_odds > 0 else int(point_fair_odds * 1.10)
                if actual_odds_est > 0:
                    payout_per_100 = actual_odds_est
                else:
                    payout_per_100 = int(10000 / abs(actual_odds_est))
                lines.append(f"**Estimated Payout:** ~${100 + payout_per_100} on $100 bet (${payout_per_100} profit)")

        lines.append("\n**Disclaimer:** Past performance doesn't guarantee future results. Bet responsibly.")

        return "\n".join(lines)

    async def _fetch_regression_analysis(
        self,
        db: AsyncSession,
        classification: dict,
    ) -> str | None:
        """Fetch xG regression analysis."""
        from backend.src.agents.regression_tracker import RegressionTracker

        tracker = RegressionTracker(db)
        players = classification.get("players", [])

        if players:
            # Single player regression analysis
            lines = []
            for player_name in players:
                result = await tracker.get_player_regression_analysis(player_name)
                if result:
                    lines.append(f"**{result.player_name}** ({result.team})\n")
                    lines.append(f"- Goals: {result.goals} | xG: {result.xg:.1f} | Differential: {result.differential:+.1f}")
                    lines.append(f"- Shooting %: {result.shooting_pct:.1%} (Expected: {result.expected_shooting_pct:.1%})")
                    lines.append(f"- Regression Type: **{result.regression_type.upper()}**")
                    lines.append(f"- Recommendation: {result.bet_recommendation}")
                    lines.append(f"- Confidence: {result.regression_confidence} ({result.games_played} games)")
                    lines.append("")
            return "\n".join(lines) if lines else None

        # League-wide regression report
        report = await tracker.get_regression_report(top_n=10)

        if not report.positive_regression and not report.negative_regression:
            return "Insufficient data for regression analysis."

        lines = []
        lines.append(f"**xG Regression Report** (Analyzed {report.total_analyzed} players)\n")

        if report.positive_regression:
            lines.append("**POSITIVE REGRESSION CANDIDATES** (Due for more goals)\n")
            lines.append("| Player | Team | Goals | xG | Diff | Recommendation |")
            lines.append("|--------|------|-------|-----|------|----------------|")
            for c in report.positive_regression[:7]:
                lines.append(
                    f"| {c.player_name} | {c.team} | {c.goals} | {c.xg:.1f} | "
                    f"**{c.differential:+.1f}** | {c.bet_recommendation.split('-')[0].strip()} |"
                )

        if report.negative_regression:
            lines.append("\n**NEGATIVE REGRESSION CANDIDATES** (Due for fewer goals)\n")
            lines.append("| Player | Team | Goals | xG | Diff | Recommendation |")
            lines.append("|--------|------|-------|-----|------|----------------|")
            for c in report.negative_regression[:5]:
                lines.append(
                    f"| {c.player_name} | {c.team} | {c.goals} | {c.xg:.1f} | "
                    f"**{c.differential:+.1f}** | {c.bet_recommendation.split('-')[0].strip()} |"
                )

        lines.append("\n**How to use this:**")
        lines.append("- Positive regression candidates are statistically 'due' for more goals")
        lines.append("- The larger the negative differential, the stronger the signal")
        lines.append("- Combine with tonight's edges for best results")

        return "\n".join(lines)

    async def _fetch_olympics_data(
        self,
        db: AsyncSession,
        classification: dict,
    ) -> str | None:
        """Fetch Olympic hockey data for Milano Cortina 2026."""
        from backend.src.ingestion.olympics import (
            get_current_olympic_data,
            predict_olympic_game,
            get_country_code,
            get_country_name,
        )

        data = get_current_olympic_data()
        players = classification.get("players", [])
        countries = classification.get("countries", [])
        is_leaders = classification.get("is_leaders_query", False)
        is_prediction = classification.get("is_prediction_query", False)

        lines = []
        lines.append("**Milano Cortina 2026 Winter Olympics - Men's Hockey**\n")

        # If asking for predictions - handle both two-country and single-country queries
        if is_prediction and len(countries) >= 1:
            # If only one country mentioned, try to find opponent from upcoming games
            if len(countries) == 1:
                country_code = get_country_code(countries[0])
                # Look for upcoming game involving this country
                for game in data.get("upcoming_games", []):
                    if game.get("home") == country_code:
                        countries.append(game.get("away"))
                        break
                    elif game.get("away") == country_code:
                        countries.insert(0, game.get("home"))
                        break
                # If still only one country, default to a common opponent for demo
                if len(countries) == 1:
                    # Default matchups for common queries
                    default_opponents = {"CAN": "CZE", "USA": "GER", "SWE": "LAT", "FIN": "SVK"}
                    opponent = default_opponents.get(country_code, "USA")
                    countries.append(opponent)
                    logger.info("inferred_olympic_opponent", country=country_code, opponent=opponent)

        if is_prediction and len(countries) >= 2:
            try:
                prediction = await predict_olympic_game(
                    db, countries[0], countries[1], "group"
                )
                lines.append(f"### {prediction['game']['home_country']} vs {prediction['game']['away_country']} Predictions\n")

                context = prediction.get("matchup_context", {})
                if context.get("home_goalie") or context.get("away_goalie"):
                    lines.append("**Goalie Matchup (Key Factor - 2x Weight in Olympics):**")
                    if context.get("home_goalie"):
                        hg = context["home_goalie"]
                        sv_pct = hg.get('save_pct') or hg.get('sv') or 0
                        gaa = hg.get('gaa', 0)
                        lines.append(f"- {prediction['game']['home_country']}: **{hg.get('name', 'Unknown')}** ({sv_pct:.3f} SV%, {gaa:.2f} GAA)")
                    if context.get("away_goalie"):
                        ag = context["away_goalie"]
                        sv_pct = ag.get('save_pct') or ag.get('sv') or 0
                        gaa = ag.get('gaa', 0)
                        lines.append(f"- {prediction['game']['away_country']}: **{ag.get('name', 'Unknown')}** ({sv_pct:.3f} SV%, {gaa:.2f} GAA)")
                        # Add warning if goalie has elite stats (explains low probabilities)
                        if sv_pct > 0.940:
                            lines.append(f"  **Elite goalie:** {ag.get('name')} ({sv_pct:.3f} SV%) - significantly reduces goal probabilities")
                    lines.append("")

                # Combine all players and sort by goal probability
                all_players = prediction.get("home_players", []) + prediction.get("away_players", [])
                by_goal = sorted(all_players, key=lambda x: x.get("prob_goal", 0), reverse=True)
                by_point = sorted(all_players, key=lambda x: x.get("prob_point", 0), reverse=True)

                lines.append("**GOAL PROBABILITY RANKINGS (all players):**")
                for i, pred in enumerate(by_goal, 1):
                    prob_pct = pred.get("prob_goal", 0) * 100
                    lines.append(
                        f"{i:2}. [{pred.get('country_code', '?')}] **{pred['player_name']}** - "
                        f"{prob_pct:.1f}% goal ({pred.get('olympic_gp', 0)}GP, {pred.get('olympic_goals', 0)}G)"
                    )

                lines.append("")
                lines.append("**POINT PROBABILITY RANKINGS (all players):**")
                for i, pred in enumerate(by_point, 1):
                    prob_pct = pred.get("prob_point", 0) * 100
                    lines.append(
                        f"{i:2}. [{pred.get('country_code', '?')}] **{pred['player_name']}** - "
                        f"{prob_pct:.1f}% point ({pred.get('olympic_points', 0)}P in {pred.get('olympic_gp', 0)}GP)"
                    )

                lines.append("")
                lines.append("**QUICK REFERENCE:**")
                lines.append(f"- Total players in model: {len(all_players)}")
                lines.append(f"- {prediction['game']['home_country']} players: {len(prediction.get('home_players', []))}")
                lines.append(f"- {prediction['game']['away_country']} players: {len(prediction.get('away_players', []))}")

                lines.append("\n**Model Explanation:**")
                lines.append("Probability = (NHL PPG × 0.45) + (Olympic PPG × 0.20) + goalie adjustment + team strength")
                lines.append("- Higher **Olympic PPG** boosts probability (rewards tournament form)")
                lines.append("- **Elite goalies** (>0.940 SV%) significantly REDUCE goal probabilities")
                lines.append("- A player with high NHL GPG but facing an elite goalie will have lower probability than expected")
                lines.append("")
                lines.append("**Why Olympic Predictions Are Different:**")
                lines.append("- Goalie matchup weighted **2x higher** than NHL model (short tournament = hot goalie dominates)")
                lines.append("- In-tournament form matters more than season stats")
                lines.append("- Elimination games apply pressure coefficients")

                lines.append("\n**IMPORTANT:** Stats available: GP, Goals, Assists, Points, xG, PPG, GPG only.")
                lines.append("TOI (time on ice) is NOT available in this context - do not reference or estimate it.")

                return "\n".join(lines)
            except Exception as e:
                logger.warning("olympic_prediction_failed", error=str(e))

        # If asking about specific player
        if players:
            for player_name in players:
                # Check Olympic scoring leaders
                for leader in data["scoring_leaders"]:
                    if player_name.lower() in leader["name"].lower():
                        lines.append(f"**{leader['name']}** ({leader['country']})")
                        lines.append(f"- Olympic Stats: {leader['g']}G, {leader['a']}A, {leader['pts']}P in {leader['gp']} GP")

                        # Also get NHL stats for comparison
                        result = await db.execute(
                            text("""
                                SELECT p.name, p.team_abbrev, s.goals, s.assists, s.points, s.games_played
                                FROM players p
                                JOIN player_season_stats s ON p.id = s.player_id
                                WHERE p.name ILIKE :name
                                  AND s.season = (SELECT MAX(season) FROM player_season_stats)
                                LIMIT 1
                            """),
                            {"name": f"%{player_name}%"}
                        )
                        row = result.fetchone()
                        if row:
                            lines.append(f"- NHL Stats (2025-26): {row.goals}G, {row.assists}A, {row.points}P in {row.games_played} GP ({row.team_abbrev})")
                        lines.append("")
                        break

                # Check goalies
                for goalie in data["goalie_leaders"]:
                    if player_name.lower() in goalie["name"].lower():
                        lines.append(f"**{goalie['name']}** ({goalie['country']}) - Goalie")
                        lines.append(f"- Olympic Stats: {goalie['w']}W, {goalie['gaa']:.2f} GAA, {goalie['sv']:.3f} SV%")
                        lines.append("")
                        break

            return "\n".join(lines) if len(lines) > 2 else None

        # If asking about specific country
        if countries:
            for country_code in countries:
                for group, teams in data["standings"].items():
                    for team in teams:
                        if team["code"] == country_code.upper() or team["country"].upper() == country_code.upper():
                            lines.append(f"**{team['country']}** (Group {group})")
                            lines.append(f"- Record: {team['w']}W-{team['l']}L ({team['pts']} pts)")
                            lines.append(f"- Goal Diff: {team.get('gf', 0)} GF, {team.get('ga', 0)} GA")

                            # Find players from this country
                            country_players = [p for p in data["scoring_leaders"] if p["country"] == team["code"]]
                            if country_players:
                                lines.append(f"- Top Scorer: {country_players[0]['name']} ({country_players[0]['pts']} pts)")

                            # Find goalie
                            country_goalies = [g for g in data["goalie_leaders"] if g["country"] == team["code"]]
                            if country_goalies:
                                g = country_goalies[0]
                                lines.append(f"- Starting Goalie: {g['name']} ({g['sv']:.3f} SV%)")
                            lines.append("")

            return "\n".join(lines) if len(lines) > 2 else None

        # Default: Show standings and leaders
        lines.append("**Group Standings:**\n")
        for group, teams in data["standings"].items():
            lines.append(f"**Group {group}:**")
            for team in teams:
                lines.append(f"- {team['country']}: {team['w']}W-{team['l']}L ({team['pts']} pts)")
            lines.append("")

        lines.append("**Scoring Leaders:**")
        lines.append("| Player | Country | GP | G | A | Pts |")
        lines.append("|--------|---------|----|----|---|-----|")
        for p in data["scoring_leaders"][:6]:
            lines.append(f"| {p['name']} | {p['country']} | {p['gp']} | {p['g']} | {p['a']} | **{p['pts']}** |")

        lines.append("\n**Goalie Leaders:**")
        lines.append("| Goalie | Country | W | GAA | SV% |")
        lines.append("|--------|---------|---|-----|-----|")
        for g in data["goalie_leaders"][:5]:
            lines.append(f"| {g['name']} | {g['country']} | {g['w']} | {g['gaa']:.2f} | {g['sv']:.3f} |")

        # Show upcoming games if available
        if data.get("upcoming_games"):
            lines.append("\n**Upcoming Games:**")
            for game in data["upcoming_games"][:4]:
                lines.append(f"- {game['away']} @ {game['home']} ({game['round'].title()})")

        return "\n".join(lines)

    async def _fetch_daily_briefing(self, db: AsyncSession) -> str | None:
        """
        Assemble a comprehensive daily briefing covering:
        - Tonight's game schedule
        - Key injury alerts (Out / LTIR / Day-to-Day)
        - Confirmed or expected starting goalies
        - Top 5 scoring picks tonight (model + market odds if available)
        - Top 2 edges/best bets tonight
        """
        from datetime import date as _date
        sections: list[str] = []
        today_str = _date.today().strftime("%A, %B %-d")

        sections.append(f"## Daily Briefing - {today_str}\n")

        # ── 1. Tonight's schedule ──────────────────────────────────────
        try:
            from backend.src.agents.daily_audit import get_todays_games_unified
            schedule_data = await get_todays_games_unified(db)
            games = schedule_data.nhl_games if hasattr(schedule_data, "nhl_games") else (
                schedule_data.get("games", []) if isinstance(schedule_data, dict) else []
            )
            if games:
                sections.append("### Tonight's Games")
                for g in games:
                    home = g.get("home_team") or g.get("home_team_abbrev", "")
                    away = g.get("away_team") or g.get("away_team_abbrev", "")
                    start = g.get("start_time", "TBD")
                    sections.append(f"- {away} @ {home}  ·  {start} ET")
            else:
                sections.append("### Tonight's Games\n- No NHL games scheduled today.")
        except Exception as e:
            logger.warning("briefing_schedule_failed", error=str(e))
            sections.append("### Tonight's Games\n- Schedule unavailable.")

        # ── 2. Key injury alerts (Out / LTIR / Day-to-Day only) ────────
        try:
            from backend.src.ingestion.espn_injuries import get_all_injuries
            injury_data = await get_all_injuries(db)
            priority_statuses = {"Out", "LTIR", "Day-to-Day", "IR", "DTD"}
            alerts: list[str] = []
            for team, players in injury_data.get("injuries_by_team", {}).items():
                for p in players:
                    if p.get("status") in priority_statuses:
                        alerts.append(f"- **{p['player_name']}** ({team}) - {p['status']}: {p.get('description', '')}")
            if alerts:
                sections.append(f"\n### 🚑 Injury Alerts ({len(alerts)} players)")
                sections.extend(alerts[:12])  # cap at 12 to avoid wall of text
                if len(alerts) > 12:
                    sections.append(f"  _...and {len(alerts) - 12} more_")
            else:
                sections.append("\n### 🚑 Injury Alerts\n- No major injuries reported.")
        except Exception as e:
            logger.warning("briefing_injuries_failed", error=str(e))

        # ── 3. Starting goalies for tonight ────────────────────────────
        try:
            result = await db.execute(text("""
                SELECT DISTINCT ON (gs.team_abbrev)
                    gs.team_abbrev, gs.goalie_name, gs.save_pct, gs.games_played
                FROM goalie_stats gs
                JOIN games g ON (
                    g.home_team_abbrev = gs.team_abbrev OR g.away_team_abbrev = gs.team_abbrev
                )
                WHERE g.game_date = CURRENT_DATE
                  AND g.state NOT IN ('OFF', 'FINAL')
                ORDER BY gs.team_abbrev, gs.games_played DESC
            """))
            goalies = result.fetchall()
            if goalies:
                sections.append("\n### 🧤 Expected Starters Tonight")
                for row in goalies:
                    sv_pct = f"{row.save_pct:.3f}" if row.save_pct else "N/A"
                    sections.append(f"- **{row.team_abbrev}**: {row.goalie_name} (SV% {sv_pct})")
        except Exception as e:
            logger.warning("briefing_goalies_failed", error=str(e))

        # ── 4. Top scoring picks tonight (model + market odds) ─────────
        try:
            from backend.src.agents.predictions import PredictionEngine
            from backend.src.agents.odds_value import OddsValueCalculator

            prediction_engine = PredictionEngine()

            # Fetch tonight's games from DB
            games_result = await db.execute(text("""
                SELECT home_team_abbrev, away_team_abbrev
                FROM games
                WHERE game_date = CURRENT_DATE
                  AND state NOT IN ('OFF', 'FINAL')
                LIMIT 10
            """))
            tonight_games = games_result.fetchall()

            if tonight_games:
                # Try to get live odds once for all games
                odds_calculator = OddsValueCalculator(db)
                all_odds_raw, _ = await odds_calculator.get_live_odds()  # dict[game_key -> list[OddsLine]]
                # Build player_name -> best implied_probability from any "anytime_scorer" or "player_goals" market
                market_probs: dict[str, float] = {}
                for lines in (all_odds_raw or {}).values():
                    for line in lines:
                        if "goal" in line.market.lower() or "scorer" in line.market.lower():
                            name_key = line.player_name.lower()
                            # Keep the best (lowest implied prob = truest market price)
                            if name_key not in market_probs or line.implied_probability < market_probs[name_key]:
                                market_probs[name_key] = line.implied_probability

                all_scorers = []
                for game_row in tonight_games:
                    try:
                        matchup = await prediction_engine.get_matchup_prediction(
                            db, game_row.home_team_abbrev, game_row.away_team_abbrev,
                            _date.today(), top_n=8
                        )
                        all_scorers.extend(matchup.top_scorers)
                    except Exception:
                        continue

                all_scorers.sort(key=lambda p: p.prob_goal, reverse=True)
                top_picks = all_scorers[:5]

                if top_picks:
                    sections.append("\n### Top Scoring Picks Tonight")
                    for i, pred in enumerate(top_picks, 1):
                        model_pct = int(pred.prob_goal * 100)
                        matchup_str = f"vs {pred.opponent}" if pred.is_home else f"@ {pred.opponent}"
                        line = f"{i}. **{pred.player_name}** ({pred.team} {matchup_str}) - Model: **{model_pct}%**"

                        # Attach market odds if available
                        mkt = market_probs.get(pred.player_name.lower())
                        if mkt:
                            mkt_pct = int(mkt * 100)
                            edge_pct = model_pct - mkt_pct
                            edge_tag = f" (+{edge_pct}% edge)" if edge_pct >= 5 else ""
                            line += f" | Market: {mkt_pct}%{edge_tag}"

                        if pred.factors:
                            line += f"  _{pred.factors[0]}_"
                        sections.append(line)
        except Exception as e:
            logger.warning("briefing_predictions_failed", error=str(e))

        # ── 5. Top 2 edges / best bets ─────────────────────────────────
        try:
            from backend.src.agents.edge_finder import EdgeFinder
            edge_finder = EdgeFinder()
            edge_report = await edge_finder.find_edges(db)
            top_edges = edge_report.top_edges[:2] if edge_report and edge_report.top_edges else []
            if top_edges:
                sections.append("\n### 💰 Best Bets Tonight")
                for edge in top_edges:
                    grade = edge.edge_grade
                    model_pct = int(edge.prob_goal * 100)
                    sections.append(
                        f"- **{edge.player_name}** ({edge.team} vs {edge.opponent}) "
                        f"Grade: **{grade}** | {model_pct}% goal | {edge.suggested_bet}"
                    )
        except Exception as e:
            logger.warning("briefing_edges_failed", error=str(e))

        if len(sections) <= 2:
            return None

        sections.append("\n---\n_Data from NHL API, ESPN, and MoneyPuck. Refreshed at startup._")
        return "\n".join(sections)

    async def _fetch_recent_results(
        self,
        db: AsyncSession,
        days_offset: int = 1,
    ) -> str | None:
        """
        Fetch completed game results and box score leaders for a past date.
        days_offset=1 → yesterday, 2 → two days ago, etc.
        """
        from datetime import date, timedelta
        from sqlalchemy import text

        target_date = date.today() - timedelta(days=days_offset)
        date_label = "Yesterday" if days_offset == 1 else target_date.strftime("%A, %B %d")

        try:
            # Get completed games for that date
            games_result = await db.execute(
                text("""
                    SELECT home_team_abbrev, away_team_abbrev,
                           home_score, away_score, game_state
                    FROM games
                    WHERE game_date = :d
                    ORDER BY start_time_utc
                """),
                {"d": target_date},
            )
            games = games_result.fetchall()

            if not games:
                return f"No games found for {target_date.strftime('%B %d, %Y')}. Data may not have been ingested yet for that date."

            lines = [f"**{date_label}'s Games - {target_date.strftime('%B %d, %Y')}**\n"]

            for g in games:
                if g.home_score is not None and g.away_score is not None:
                    winner = g.home_team_abbrev if g.home_score > g.away_score else g.away_team_abbrev
                    lines.append(
                        f"**{g.away_team_abbrev} {g.away_score} @ {g.home_team_abbrev} {g.home_score}** "
                        f"- {winner} win"
                    )
                else:
                    lines.append(f"{g.away_team_abbrev} @ {g.home_team_abbrev} - {g.game_state}")

            # Top scorers from box scores
            scorers_result = await db.execute(
                text("""
                    SELECT p.name, gl.team_abbrev, gl.goals, gl.assists, gl.points, gl.shots
                    FROM game_logs gl
                    JOIN players p ON p.id = gl.player_id
                    WHERE gl.game_date = :d AND gl.points > 0
                    ORDER BY gl.points DESC, gl.goals DESC
                    LIMIT 15
                """),
                {"d": target_date},
            )
            scorers = scorers_result.fetchall()

            if scorers:
                lines.append("\n**Top Performers:**")
                for s in scorers:
                    stat_line = []
                    if s.goals:
                        stat_line.append(f"{s.goals}G")
                    if s.assists:
                        stat_line.append(f"{s.assists}A")
                    stat_line.append(f"{s.points}P")
                    if s.shots:
                        stat_line.append(f"{s.shots} SOG")
                    lines.append(f"- {s.name} ({s.team_abbrev}): {', '.join(stat_line)}")
            else:
                lines.append("\n*Box score data not yet ingested for this date. Check back after next startup.*")

            return "\n".join(lines)

        except Exception as e:
            logger.warning("recent_results_fetch_failed", days_offset=days_offset, error=str(e))
            return None

    async def _fetch_todays_schedule(
        self,
        db: AsyncSession,
        classification: dict,
    ) -> str | None:
        """
        Fetch today's game schedule - both NHL and Olympics if active.

        This is the unified source for "what games are today" queries.
        """
        from backend.src.agents.daily_audit import get_todays_games_unified

        try:
            games = await get_todays_games_unified(db)
        except Exception as e:
            logger.warning("schedule_fetch_failed", error=str(e))
            return None

        if games.total_games == 0:
            return f"No games scheduled for today ({games.date.strftime('%B %d, %Y')})."

        lines = []
        lines.append(f"**{games.date.strftime('%A, %B %d, %Y')}**\n")

        # Show Olympics first if active (higher priority during tournament)
        if games.is_olympics_active and games.olympic_games:
            lines.append(f"### Olympic Hockey - Milano Cortina 2026 ({len(games.olympic_games)} games)\n")

            for game in games.olympic_games:
                round_str = f" ({game['round'].title()})" if game.get('round') else ""
                lines.append(f"- **{game['away_team']}** @ **{game['home_team']}**{round_str}")
                if game.get('home_country') and game.get('away_country'):
                    lines.append(f"  _{game['away_country']} vs {game['home_country']}_")

            lines.append("")

        # Show NHL games
        if games.nhl_games:
            lines.append(f"### NHL ({len(games.nhl_games)} games)\n")

            for game in games.nhl_games:
                time_str = ""
                if game.get("start_time"):
                    # Parse and format time, converting from UTC to Eastern
                    try:
                        from datetime import datetime, timezone
                        from zoneinfo import ZoneInfo
                        start_utc = datetime.fromisoformat(game["start_time"].replace("Z", "+00:00"))
                        eastern = ZoneInfo("America/New_York")
                        start_et = start_utc.astimezone(eastern)
                        time_str = f" - {start_et.strftime('%I:%M %p')} ET"
                    except Exception:
                        pass

                state_label = ""
                if game.get("state") == "LIVE":
                    state_label = " - LIVE"
                elif game.get("state") == "FINAL":
                    state_label = " - FINAL"

                lines.append(f"- **{game['away_team']}** @ **{game['home_team']}**{time_str}{state_label}")
                if game.get("venue"):
                    lines.append(f"  _{game['venue']}_")

            lines.append("")

        # Add summary
        if games.is_olympics_active:
            lines.append(f"**Total: {games.total_games} games** ({len(games.olympic_games)} Olympic, {len(games.nhl_games)} NHL)")
            lines.append("\n_Note: During the Olympics, NHL is on break. Olympic games take priority._")
        else:
            lines.append(f"**Total: {games.total_games} NHL games tonight**")

        return "\n".join(lines)

    def _format_season_display(self, season: str | None) -> str:
        """Convert season format from '20232024' to '2023-24' for display."""
        if not season:
            return "Unknown"
        if len(season) == 8:
            return f"{season[:4]}-{season[6:8]}"
        return season

    def _is_followup_query(self, query: str) -> bool:
        """Detect if this is a vague follow-up query that needs previous context."""
        query_lower = query.lower().strip()
        followup_patterns = [
            "tell me more",
            "more details",
            "explain more",
            "what else",
            "go on",
            "continue",
            "elaborate",
            "expand on",
            "more about",
            "more thoughts",
            "what do you think",
            "why is that",
            "how so",
            "can you explain",
            "what about that",
            "more info",
            "keep going",
            "and?",
            "anything else",
            "what's your take",
            # Opinion/challenge responses that reference previous answer
            "seems low",
            "seems high",
            "too low",
            "too high",
            "that's wrong",
            "i disagree",
            "are you sure",
            "really?",
            "why not",
            "what if",
            "but what about",
            "i think",
            "seems off",
            "doesn't seem right",
            "that can't be right",
            "sounds low",
            "sounds high",
        ]
        # Check if query matches any follow-up pattern
        for pattern in followup_patterns:
            if pattern in query_lower:
                return True
        # Also check for very short queries that are likely follow-ups
        if len(query_lower) < 25 and any(word in query_lower for word in ["more", "else", "that", "why", "how", "seems", "think", "sure"]):
            return True
        return False

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

    async def _generate_response(
        self,
        query: str,
        context: str,
        conversation_history: list[dict] | None = None,
        images: list[dict] | None = None,
    ) -> str:
        """Generate the final response using Claude (with optional vision)."""
        # Build message list with conversation history for context
        messages = []

        # Add previous conversation turns (last 10 messages max to avoid token limits)
        if conversation_history:
            for msg in conversation_history[-10:]:
                messages.append({
                    "role": msg["role"],
                    "content": msg["content"],
                })

        # Build the user message text
        user_text = f"""Context from database and knowledge base:

{context}

---

User question: {query}

Provide a helpful, accurate response based on the context above.

IMPORTANT:
- Base your answer ONLY on the context provided above. Do not say you don't have access to data if it's in the context.
- If the context contains scoring predictions, present them clearly with percentages and player names.
- The "Overall Best Bets Tonight" list contains up to 15 ranked players. If a follow-up asks for more picks, draw from players further down that list (e.g., ranks 4-6 for "give me three more").
- If this is a follow-up question referencing previous conversation (e.g., "tell me more", "what about that"), use the conversation history above for context.
- If the user attached an image, analyze it in the context of the hockey question asked.
- Always end your response with a "Sources:" section listing where the data came from, formatted as:

Sources:
- PowerplAI Scoring Model (NHL API game logs, recent form analysis)
- [Any other sources from the context]"""

        # Build the user message content - include images if provided (Claude vision)
        if images:
            content: list = []
            for img in images:
                media_type = img.get("media_type", "image/png")
                # Only pass supported image types to Claude vision
                if media_type in ("image/jpeg", "image/png", "image/gif", "image/webp"):
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": img["data"],
                        },
                    })
            content.append({"type": "text", "text": user_text})
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": user_text})

        message = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=messages,
        )

        # Safely access response content
        if not message.content or len(message.content) == 0:
            logger.error("empty_response_content")
            return "I apologize, but I wasn't able to generate a response. Please try again."

        return message.content[0].text


# Singleton instance
copilot = PowerplAICopilot()
