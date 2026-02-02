"""
Evaluation framework for PowerplAI copilot.

Measures:
1. Factual accuracy - Do stats match the database?
2. Retrieval relevance - Did we pull the right context?
3. Response quality - Is the answer helpful and well-structured?
4. Citation accuracy - Are sources correctly attributed?
"""
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
import anthropic
import structlog

from backend.src.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


@dataclass
class EvalCase:
    """A single evaluation test case."""
    id: str
    query: str
    expected_type: str  # stats_lookup, comparison, etc.
    expected_entities: list[str]  # Players/teams that should be mentioned
    ground_truth: dict | None  # Known correct stats for verification
    tags: list[str]  # For filtering evals


@dataclass
class EvalResult:
    """Result of running an evaluation."""
    case_id: str
    passed: bool
    scores: dict[str, float]  # Individual metric scores
    response: str
    latency_ms: int
    timestamp: datetime
    errors: list[str]


class EvalMetrics:
    """Evaluation metrics calculator."""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def factual_accuracy(
        self,
        response: str,
        ground_truth: dict,
    ) -> tuple[float, list[str]]:
        """
        Check if stats mentioned in response match ground truth.

        Returns:
            (score 0-1, list of errors)
        """
        errors = []

        # Use Claude to extract stats from response
        extraction = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": f"""Extract any hockey statistics mentioned in this response as JSON.
Include player names and their stats (goals, assists, points, xG, etc.)

Response: "{response}"

Return JSON only: {{"stats": [{{"player": "name", "stat": "goals", "value": 10}}]}}"""
            }]
        )

        try:
            extracted = json.loads(extraction.content[0].text)
            mentioned_stats = extracted.get("stats", [])
        except json.JSONDecodeError:
            return 0.5, ["Could not parse extracted stats"]

        # Compare against ground truth
        correct = 0
        total = len(ground_truth)

        for player, expected_stats in ground_truth.items():
            player_mentions = [s for s in mentioned_stats if player.lower() in s.get("player", "").lower()]

            for stat_name, expected_value in expected_stats.items():
                for mention in player_mentions:
                    if mention.get("stat") == stat_name:
                        if abs(mention.get("value", 0) - expected_value) <= 1:  # Allow small rounding
                            correct += 1
                        else:
                            errors.append(
                                f"{player} {stat_name}: expected {expected_value}, got {mention.get('value')}"
                            )
                        break

        score = correct / total if total > 0 else 1.0
        return score, errors

    def retrieval_relevance(
        self,
        query: str,
        retrieved_docs: list[dict],
        expected_entities: list[str],
    ) -> float:
        """
        Score how relevant the retrieved documents are.

        Checks if documents mention expected players/teams.
        """
        if not retrieved_docs:
            return 0.0

        # Check if expected entities appear in retrieved content
        all_content = " ".join([doc.get("content", "") for doc in retrieved_docs]).lower()

        found = sum(1 for entity in expected_entities if entity.lower() in all_content)
        return found / len(expected_entities) if expected_entities else 1.0

    def response_quality(self, query: str, response: str) -> float:
        """
        Use LLM-as-judge to score response quality.

        Criteria: helpful, accurate, well-structured, cites sources.
        """
        judgment = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": f"""Rate this hockey analytics response on a scale of 1-10.

Query: "{query}"
Response: "{response}"

Criteria:
- Directly answers the question
- Uses specific stats/data
- Well-structured and clear
- Cites sources appropriately

Return only a JSON object: {{"score": 7, "reason": "brief explanation"}}"""
            }]
        )

        try:
            result = json.loads(judgment.content[0].text)
            return result.get("score", 5) / 10
        except json.JSONDecodeError:
            return 0.5

    def citation_accuracy(self, response: str, sources: list[dict]) -> float:
        """Check if response properly cites its sources."""
        if not sources:
            return 1.0 if "source" not in response.lower() else 0.5

        # Check for source mentions
        source_names = [s.get("data", "") for s in sources if isinstance(s.get("data"), str)]
        mentioned = sum(1 for name in source_names if name.lower() in response.lower())

        return mentioned / len(source_names) if source_names else 1.0


class EvalRunner:
    """Runs evaluation suites against the copilot."""

    def __init__(self, copilot, db_session):
        self.copilot = copilot
        self.db = db_session
        self.metrics = EvalMetrics()

    async def run_case(self, case: EvalCase) -> EvalResult:
        """Run a single evaluation case."""
        errors = []
        scores = {}

        start = datetime.now()

        # Run the query
        try:
            result = await self.copilot.query(case.query, self.db)
            response = result["response"]
            sources = result["sources"]
        except Exception as e:
            return EvalResult(
                case_id=case.id,
                passed=False,
                scores={},
                response="",
                latency_ms=0,
                timestamp=datetime.now(),
                errors=[str(e)],
            )

        latency_ms = int((datetime.now() - start).total_seconds() * 1000)

        # Calculate metrics
        if case.ground_truth:
            accuracy, accuracy_errors = self.metrics.factual_accuracy(response, case.ground_truth)
            scores["factual_accuracy"] = accuracy
            errors.extend(accuracy_errors)

        scores["retrieval_relevance"] = self.metrics.retrieval_relevance(
            case.query, sources, case.expected_entities
        )
        scores["response_quality"] = self.metrics.response_quality(case.query, response)
        scores["citation_accuracy"] = self.metrics.citation_accuracy(response, sources)

        # Overall pass/fail (configurable threshold)
        avg_score = sum(scores.values()) / len(scores) if scores else 0
        passed = avg_score >= 0.7

        return EvalResult(
            case_id=case.id,
            passed=passed,
            scores=scores,
            response=response,
            latency_ms=latency_ms,
            timestamp=datetime.now(),
            errors=errors,
        )

    async def run_suite(self, cases: list[EvalCase]) -> dict:
        """Run a full evaluation suite."""
        results = []
        for case in cases:
            result = await self.run_case(case)
            results.append(result)
            logger.info(
                "eval_case_complete",
                case_id=case.id,
                passed=result.passed,
                scores=result.scores,
            )

        # Aggregate metrics
        passed = sum(1 for r in results if r.passed)
        avg_scores = {}
        for metric in ["factual_accuracy", "retrieval_relevance", "response_quality", "citation_accuracy"]:
            values = [r.scores.get(metric, 0) for r in results if metric in r.scores]
            avg_scores[metric] = sum(values) / len(values) if values else 0

        return {
            "total": len(cases),
            "passed": passed,
            "pass_rate": passed / len(cases) if cases else 0,
            "avg_scores": avg_scores,
            "avg_latency_ms": sum(r.latency_ms for r in results) / len(results) if results else 0,
            "results": [
                {
                    "case_id": r.case_id,
                    "passed": r.passed,
                    "scores": r.scores,
                    "errors": r.errors,
                }
                for r in results
            ],
        }


# -------------------------------------------------------------------------
# Sample eval cases
# -------------------------------------------------------------------------

SAMPLE_EVAL_CASES = [
    EvalCase(
        id="stats_lookup_1",
        query="How many goals does Connor McDavid have this season?",
        expected_type="stats_lookup",
        expected_entities=["McDavid", "Edmonton", "Oilers"],
        ground_truth={"Connor McDavid": {"goals": 50}},  # Update with real data
        tags=["stats", "skater"],
    ),
    EvalCase(
        id="comparison_1",
        query="Compare Cale Makar vs Quinn Hughes defensively",
        expected_type="comparison",
        expected_entities=["Makar", "Hughes", "Colorado", "Vancouver"],
        ground_truth=None,  # Comparison doesn't have single ground truth
        tags=["comparison", "defense"],
    ),
    EvalCase(
        id="explainer_1",
        query="What is expected goals and why does it matter?",
        expected_type="explainer",
        expected_entities=["xG", "expected goals"],
        ground_truth=None,
        tags=["explainer", "analytics"],
    ),
]
