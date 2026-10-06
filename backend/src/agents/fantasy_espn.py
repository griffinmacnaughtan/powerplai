"""
ESPN Fantasy Hockey integration.

ESPN has no official public API. This uses the same read-only endpoints the
fantasy.espn.com web app calls (and the community `espn-api` package wraps):

    https://lm-api-reads.fantasy.espn.com/apis/v3/games/fhl/seasons/{year}/segments/0/leagues/{id}

- Public leagues: league ID only.
- Private leagues: the `espn_s2` and `SWID` cookies from a logged-in browser.
- `year` is the season's END year (2026-27 -> 2027).

Endpoints are undocumented and can change without notice; everything here
fails soft with a clear error instead of crashing the request.

What PowerplAI adds on top of ESPN's own data:
- Expected fantasy points per game under *this league's* scoring settings.
- For players with a game in the window, goals/assists/shots come from our
  matchup-aware prediction model (goalie, pace, home/away, form) instead of
  flat season averages.
- Start/sit lineup for a date and waiver pickups ranked by expected points
  over the next N days (schedule-aware) vs. the weakest rostered player.
"""
from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta

import httpx
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.config import get_settings
from backend.src.ingestion.scheduler import get_current_season

logger = structlog.get_logger()

ESPN_FANTASY_BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fhl"

# Lineup slot IDs (ESPN hockey)
SLOT_NAMES = {
    0: "C", 1: "LW", 2: "RW", 3: "F", 4: "D", 5: "G",
    6: "UTIL", 7: "BN", 8: "IR",
}
BENCH_SLOT = 7
IR_SLOT = 8
GOALIE_SLOT = 5
# Fill specific slots before flex ones so flexible players stay available.
SLOT_FILL_ORDER = [0, 1, 2, 4, 5, 3, 6]

# ESPN stat IDs (hockey) -> short code. From espn-api's hockey constants.
STAT_CODES = {
    "0": "GS", "1": "W", "2": "L", "3": "SA", "4": "GA", "6": "SV", "7": "SO",
    "9": "OTL", "10": "GAA", "11": "SV%", "13": "G", "14": "A", "15": "+/-",
    "17": "PIM", "18": "PPG", "19": "PPA", "20": "SHG", "21": "SHA",
    "22": "GWG", "23": "FOW", "24": "FOL", "28": "HAT", "29": "SOG",
    "31": "HIT", "32": "BLK", "33": "DEF", "34": "GP", "38": "PPP", "39": "SHP",
}
GOALIE_STATS = {"GS", "W", "L", "SA", "GA", "SV", "SO", "OTL", "GAA", "SV%"}
# Rate stats can't be summed per game - excluded from points projection.
RATE_STATS = {"GAA", "SV%"}

# Used when a league scores by categories/roto (no point values). A
# reasonable "standard points" proxy so start/sit still ranks sensibly.
PROXY_POINTS = {
    "G": 2.0, "A": 1.0, "PPP": 0.5, "SHP": 0.5, "SOG": 0.4, "HIT": 0.2,
    "BLK": 0.4, "W": 4.0, "SV": 0.2, "GA": -1.0, "SO": 3.0,
}

# ESPN proTeamId -> NHL abbreviation
ESPN_TEAM_ABBREV = {
    1: "BOS", 2: "BUF", 3: "CGY", 4: "CHI", 5: "DET", 6: "EDM", 7: "CAR",
    8: "LAK", 9: "DAL", 10: "MTL", 11: "NJD", 12: "NYI", 13: "NYR",
    14: "OTT", 15: "PHI", 16: "PIT", 17: "COL", 18: "SJS", 19: "STL",
    20: "TBL", 21: "TOR", 22: "VAN", 23: "WSH", 25: "ANA", 26: "FLA",
    27: "NSH", 28: "WPG", 29: "CBJ", 30: "MIN", 37: "VGK",
    124292: "SEA", 129764: "UTA",
}

# Prior-season games of weight when blending ESPN per-game rates, mirroring
# the prediction engine's early-season shrinkage.
PRIOR_GAMES = 20


class ESPNFantasyError(Exception):
    """Raised for ESPN access problems (bad league ID, private league, API change)."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def current_espn_year() -> int:
    """ESPN keys seasons by end year: 2026-27 is 2027."""
    return int(get_current_season()) + 1


def _normalize_name(name: str) -> str:
    stripped = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return " ".join(stripped.lower().replace(".", "").replace("'", "").split())


# =============================================================================
# ESPN client
# =============================================================================

class ESPNFantasyClient:
    def __init__(
        self,
        league_id: int,
        year: int | None = None,
        espn_s2: str | None = None,
        swid: str | None = None,
    ):
        settings = get_settings()
        self.league_id = league_id
        self.year = year or current_espn_year()
        s2, sw = espn_s2, swid
        # Server-held cookies are the operator's personal ESPN login. Only
        # use them for the operator's own configured league - otherwise any
        # visitor could read any private league that login can see.
        if not (s2 and sw) and settings.espn_league_id and league_id == settings.espn_league_id:
            s2, sw = settings.espn_s2, settings.espn_swid
        self.cookies = {"espn_s2": s2, "SWID": sw} if s2 and sw else None

    @property
    def league_url(self) -> str:
        return f"{ESPN_FANTASY_BASE}/seasons/{self.year}/segments/0/leagues/{self.league_id}"

    async def _get(self, params: dict | list, headers: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=20.0, cookies=self.cookies) as client:
            resp = await client.get(self.league_url, params=params, headers=headers)
        if resp.status_code in (401, 403):
            raise ESPNFantasyError(
                "ESPN denied access - this league is private. Send your espn_s2 "
                "and SWID cookies (from fantasy.espn.com) with the request.",
                status_code=403,
            )
        if resp.status_code == 404:
            raise ESPNFantasyError(
                f"ESPN league {self.league_id} not found for season {self.year}.",
                status_code=404,
            )
        if resp.status_code >= 400:
            raise ESPNFantasyError(f"ESPN returned HTTP {resp.status_code}.")
        try:
            data = resp.json()
        except ValueError:
            raise ESPNFantasyError("ESPN returned a non-JSON response (API may have changed).")
        if isinstance(data, list):  # leagueHistory-style responses come wrapped
            data = data[0] if data else {}
        return data

    async def get_league(self) -> dict:
        params = [("view", v) for v in ("mSettings", "mTeam", "mRoster", "mStatus")]
        return await self._get(params)

    async def get_free_agents(self, size: int = 75, slot_ids: list[int] | None = None) -> list[dict]:
        filters = {
            "players": {
                "filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
                "filterSlotIds": {"value": slot_ids or []},
                "limit": size,
                "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
                "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": "STANDARD"},
            }
        }
        data = await self._get(
            {"view": "kona_player_info"},
            headers={"x-fantasy-filter": json.dumps(filters)},
        )
        return data.get("players", [])


# =============================================================================
# Parsing
# =============================================================================

@dataclass
class LeagueScoring:
    scoring_type: str
    skater_points: dict[str, float]
    goalie_points: dict[str, float]
    is_proxy: bool  # True when the league has no point values (categories/roto)
    categories: list[str] = field(default_factory=list)


@dataclass
class FantasyPlayer:
    espn_id: int
    name: str
    nhl_team: str | None
    position: str  # C/LW/RW/D/G
    eligible_slots: list[int]
    injury_status: str | None
    lineup_slot: int | None = None
    pct_owned: float | None = None
    per_game: dict[str, float] = field(default_factory=dict)  # ESPN-derived rates
    games_sample: int = 0

    @property
    def is_goalie(self) -> bool:
        return self.position == "G"


POSITION_BY_DEFAULT_ID = {1: "C", 2: "LW", 3: "RW", 4: "D", 5: "G"}


def parse_scoring(settings: dict) -> LeagueScoring:
    scoring = settings.get("scoringSettings", {}) or {}
    scoring_type = scoring.get("scoringType", "UNKNOWN")
    skater: dict[str, float] = {}
    goalie: dict[str, float] = {}
    categories: list[str] = []

    for item in scoring.get("scoringItems", []) or []:
        code = STAT_CODES.get(str(item.get("statId")))
        if not code:
            continue
        categories.append(code)
        if code in RATE_STATS:
            continue
        points = float(item.get("points") or 0)
        overrides = item.get("pointsOverrides") or {}
        goalie_pts = float(overrides.get(str(GOALIE_SLOT), overrides.get(GOALIE_SLOT, points)))
        if code in GOALIE_STATS:
            goalie[code] = goalie_pts
        else:
            skater[code] = points
            if overrides:
                goalie[code] = goalie_pts

    is_proxy = "POINTS" not in scoring_type.upper() or not any(skater.values())
    if is_proxy:
        skater = {k: v for k, v in PROXY_POINTS.items() if k not in GOALIE_STATS}
        goalie = {k: v for k, v in PROXY_POINTS.items() if k in GOALIE_STATS}

    return LeagueScoring(
        scoring_type=scoring_type,
        skater_points=skater,
        goalie_points=goalie,
        is_proxy=is_proxy,
        categories=categories,
    )


def _season_totals(stats: list[dict], season_year: int) -> dict[str, float] | None:
    """Actual full-season totals (statSourceId 0, statSplitTypeId 0)."""
    for entry in stats or []:
        if (
            entry.get("seasonId") == season_year
            and entry.get("statSourceId") == 0
            and entry.get("statSplitTypeId") == 0
        ):
            raw = entry.get("stats") or {}
            return {STAT_CODES[k]: float(v) for k, v in raw.items() if k in STAT_CODES}
    return None


def _projection(stats: list[dict], season_year: int) -> dict[str, float] | None:
    """ESPN's preseason projection (statSourceId 1)."""
    for entry in stats or []:
        if entry.get("seasonId") == season_year and entry.get("statSourceId") == 1:
            raw = entry.get("stats") or {}
            return {STAT_CODES[k]: float(v) for k, v in raw.items() if k in STAT_CODES}
    return None


def _blend_per_game(player_stats: list[dict], year: int) -> tuple[dict[str, float], int]:
    """
    Per-game rates from ESPN stats: this season's actuals shrunk toward last
    season (or ESPN's projection for rookies) until PRIOR_GAMES are played.
    """
    cur = _season_totals(player_stats, year) or {}
    prior = _season_totals(player_stats, year - 1)
    if not prior or not prior.get("GP"):
        prior = _projection(player_stats, year)

    cur_gp = cur.get("GP", 0.0)
    prior_gp = (prior or {}).get("GP", 0.0)
    weight = max(0.0, PRIOR_GAMES - cur_gp) if prior_gp else 0.0
    if cur_gp + weight == 0:
        return {}, 0

    codes = set(cur) | set(prior or {})
    rates = {}
    for code in codes - {"GP"} - RATE_STATS:
        prior_rate = (prior or {}).get(code, 0.0) / prior_gp if prior_gp else 0.0
        rates[code] = (cur.get(code, 0.0) + prior_rate * weight) / (cur_gp + weight)
    return rates, int(cur_gp)


def parse_player(entry: dict, year: int) -> FantasyPlayer:
    # Roster entries nest the player under playerPoolEntry; free-agent
    # listings put it at the top level.
    pool = entry.get("playerPoolEntry") or entry
    player = pool.get("player") or {}
    per_game, sample = _blend_per_game(player.get("stats") or [], year)
    ownership = player.get("ownership") or {}
    status = player.get("injuryStatus")
    return FantasyPlayer(
        espn_id=player.get("id") or pool.get("id"),
        name=player.get("fullName", "Unknown"),
        nhl_team=ESPN_TEAM_ABBREV.get(player.get("proTeamId")),
        position=POSITION_BY_DEFAULT_ID.get(player.get("defaultPositionId"), "F"),
        eligible_slots=[s for s in player.get("eligibleSlots") or [] if s in SLOT_NAMES],
        injury_status=status if status and status != "ACTIVE" else None,
        lineup_slot=entry.get("lineupSlotId"),
        pct_owned=ownership.get("percentOwned"),
        per_game=per_game,
        games_sample=sample,
    )


def parse_league(data: dict, year: int) -> dict:
    settings = data.get("settings", {}) or {}
    members = {m.get("id"): m for m in data.get("members", []) or []}
    teams = []
    for t in data.get("teams", []) or []:
        owner_ids = t.get("owners") or []
        owner = members.get(owner_ids[0]) if owner_ids else None
        name = t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}".strip()
        roster = [parse_player(e, year) for e in (t.get("roster") or {}).get("entries", [])]
        teams.append({
            "team_id": t.get("id"),
            "name": name,
            "abbrev": t.get("abbrev"),
            "owner": f"{owner.get('firstName', '')} {owner.get('lastName', '')}".strip() if owner else None,
            "record": (t.get("record") or {}).get("overall"),
            "roster": roster,
        })

    slot_counts = {
        int(k): v for k, v in ((settings.get("rosterSettings") or {}).get("lineupSlotCounts") or {}).items()
        if int(k) in SLOT_NAMES and v
    }
    return {
        "league_id": data.get("id"),
        "name": settings.get("name"),
        "year": year,
        "size": settings.get("size") or len(teams),
        "scoring": parse_scoring(settings),
        "lineup_slot_counts": slot_counts,
        "current_matchup_period": (data.get("status") or {}).get("currentMatchupPeriod"),
        "teams": teams,
    }


# =============================================================================
# Projection (ESPN rates + PowerplAI matchup model)
# =============================================================================

async def _games_by_team(db: AsyncSession, start: date, end: date) -> dict[str, list[dict]]:
    result = await db.execute(
        text("""
            SELECT game_date, home_team_abbrev, away_team_abbrev
            FROM games
            WHERE game_date BETWEEN :start AND :end
              AND game_type IN (2, 3)
            ORDER BY game_date
        """),
        {"start": start, "end": end},
    )
    schedule: dict[str, list[dict]] = {}
    for row in result.fetchall():
        schedule.setdefault(row.home_team_abbrev, []).append(
            {"date": row.game_date, "opponent": row.away_team_abbrev, "is_home": True}
        )
        schedule.setdefault(row.away_team_abbrev, []).append(
            {"date": row.game_date, "opponent": row.home_team_abbrev, "is_home": False}
        )
    return schedule


async def _match_players(db: AsyncSession, players: list[FantasyPlayer]) -> dict[int, dict]:
    """Map ESPN players to PowerplAI player ids by normalized name (+ team when ambiguous)."""
    if not players:
        return {}
    result = await db.execute(text("SELECT id, name, team_abbrev FROM players"))
    by_name: dict[str, list] = {}
    for row in result.fetchall():
        by_name.setdefault(_normalize_name(row.name), []).append(row)

    matched = {}
    for p in players:
        candidates = by_name.get(_normalize_name(p.name), [])
        if len(candidates) > 1 and p.nhl_team:
            same_team = [c for c in candidates if c.team_abbrev == p.nhl_team]
            candidates = same_team or candidates
        if candidates:
            matched[p.espn_id] = {"player_id": candidates[0].id, "name": candidates[0].name}
    return matched


def _points(rates: dict[str, float], weights: dict[str, float]) -> float:
    return sum(rates.get(code, 0.0) * pts for code, pts in weights.items())


async def project_players(
    db: AsyncSession,
    players: list[FantasyPlayer],
    scoring: LeagueScoring,
    start: date,
    days: int = 1,
    use_model: bool = True,
) -> list[dict]:
    """
    Expected fantasy points for each player over [start, start + days).

    Per game: ESPN per-game rates under league scoring. With use_model, a
    skater who has a game in the window and maps to a PowerplAI player gets
    G/A/SOG from the prediction model for that specific matchup. (Waiver
    scans over 75 players x a week of games skip the model to stay fast.)
    """
    from backend.src.agents.predictions import PredictionEngine

    end = start + timedelta(days=days - 1)
    schedule = await _games_by_team(db, start, end)
    matches = await _match_players(db, players)
    engine = PredictionEngine(db)

    projections = []
    for p in players:
        weights = scoring.goalie_points if p.is_goalie else scoring.skater_points
        baseline_fp = _points(p.per_game, weights)
        games = schedule.get(p.nhl_team or "", [])
        match = matches.get(p.espn_id)

        game_rows = []
        for g in games:
            fp = baseline_fp
            model = None
            if use_model and match and not p.is_goalie:
                try:
                    home = p.nhl_team if g["is_home"] else g["opponent"]
                    away = g["opponent"] if g["is_home"] else p.nhl_team
                    ctx = await engine._get_matchup_context(db, home, away)
                    pred = await engine._calculate_player_prediction(
                        db, match["player_id"], match["name"], p.nhl_team, g["opponent"],
                        g["is_home"], g["date"], matchup_context=ctx,
                    )
                except Exception as e:  # model gaps shouldn't break the lineup
                    logger.warning("fantasy_prediction_failed", player=p.name, error=str(e))
                    pred = None
                if pred:
                    rates = dict(p.per_game)
                    rates["G"] = pred.expected_goals
                    rates["A"] = pred.expected_assists
                    if pred.expected_shots:
                        rates["SOG"] = pred.expected_shots
                    fp = _points(rates, weights)
                    model = {
                        "prob_goal": pred.prob_goal,
                        "prob_point": pred.prob_point,
                        "confidence": pred.confidence,
                    }
            game_rows.append({
                "date": g["date"].isoformat(),
                "opponent": g["opponent"],
                "is_home": g["is_home"],
                "expected_fp": round(fp, 2),
                "model": model,
            })

        injured = p.injury_status in {"OUT", "INJURY_RESERVE", "SUSPENSION"}
        total = 0.0 if injured else sum(r["expected_fp"] for r in game_rows)
        projections.append({
            "espn_id": p.espn_id,
            "name": p.name,
            "nhl_team": p.nhl_team,
            "position": p.position,
            "eligible_slots": [SLOT_NAMES[s] for s in p.eligible_slots if s not in (BENCH_SLOT, IR_SLOT)],
            "lineup_slot": SLOT_NAMES.get(p.lineup_slot) if p.lineup_slot is not None else None,
            "injury_status": p.injury_status,
            "pct_owned": round(p.pct_owned, 1) if p.pct_owned is not None else None,
            "fp_per_game": round(baseline_fp, 2),
            "games": len(game_rows),
            "expected_fp": round(total, 2),
            "game_detail": game_rows,
            "matched_to_model": bool(match),
            "current_season_gp": p.games_sample,
            "_slots": p.eligible_slots,
        })
    return projections


def optimize_lineup(projections: list[dict], slot_counts: dict[int, int]) -> dict:
    """
    Fill active slots to maximize expected points for the day.

    Greedy: highest projection first, into its most specific open slot
    (C/LW/RW/D/G before F before UTIL). For hockey's slot structure this
    matches the exact optimum in practice and is easy to explain.
    """
    open_slots = {s: n for s, n in slot_counts.items() if s not in (BENCH_SLOT, IR_SLOT)}
    starters, bench = [], []
    for p in sorted(projections, key=lambda x: x["expected_fp"], reverse=True):
        slot = None
        if p["games"] > 0 and p["expected_fp"] > 0:
            for s in SLOT_FILL_ORDER:
                if open_slots.get(s, 0) > 0 and s in p["_slots"]:
                    slot = s
                    break
        if slot is None:
            bench.append({**p, "recommended_slot": "BN"})
        else:
            open_slots[slot] -= 1
            starters.append({**p, "recommended_slot": SLOT_NAMES[slot]})

    def is_active(slot: str | None) -> bool:
        return slot not in (None, "BN", "IR")

    changes = [
        {
            "player": p["name"],
            "action": "start" if p["recommended_slot"] != "BN" else "bench",
            "from": p["lineup_slot"],
            "to": p["recommended_slot"],
        }
        for p in starters + bench
        if p["lineup_slot"] != "IR"
        and is_active(p["lineup_slot"]) != (p["recommended_slot"] != "BN")
    ]
    return {
        "starters": [_public(p) for p in starters],
        "bench": [_public(p) for p in bench],
        "unfilled_slots": {SLOT_NAMES[s]: n for s, n in open_slots.items() if n > 0},
        "expected_fp": round(sum(p["expected_fp"] for p in starters), 2),
        "changes": changes,
    }


def _public(p: dict) -> dict:
    return {k: v for k, v in p.items() if not k.startswith("_")}


# =============================================================================
# High-level operations used by the API
# =============================================================================

def _resolve_league_id(league_id: int | None) -> int:
    resolved = league_id or get_settings().espn_league_id
    if not resolved:
        raise ESPNFantasyError("Pass league_id or set ESPN_LEAGUE_ID.", status_code=400)
    return resolved


def _find_team(league: dict, team_id: int) -> dict:
    for t in league["teams"]:
        if t["team_id"] == team_id:
            return t
    raise ESPNFantasyError(f"Team {team_id} not found in league.", status_code=404)


def _scoring_summary(scoring: LeagueScoring) -> dict:
    return {
        "type": scoring.scoring_type,
        "uses_proxy_points": scoring.is_proxy,
        "skater_points": scoring.skater_points,
        "goalie_points": scoring.goalie_points,
        "categories": scoring.categories,
    }


async def get_league_summary(
    league_id: int | None = None,
    year: int | None = None,
    credentials: tuple[str, str] | None = None,
) -> dict:
    client = ESPNFantasyClient(_resolve_league_id(league_id), year, *(credentials or (None, None)))
    league = parse_league(await client.get_league(), client.year)
    return {
        "league_id": league["league_id"],
        "name": league["name"],
        "year": league["year"],
        "size": league["size"],
        "scoring": _scoring_summary(league["scoring"]),
        "lineup_slots": {SLOT_NAMES[s]: n for s, n in league["lineup_slot_counts"].items()},
        "current_matchup_period": league["current_matchup_period"],
        "teams": [
            {
                "team_id": t["team_id"],
                "name": t["name"],
                "abbrev": t["abbrev"],
                "owner": t["owner"],
                "record": t["record"],
                "roster_size": len(t["roster"]),
            }
            for t in league["teams"]
        ],
    }


async def get_lineup_recommendation(
    db: AsyncSession,
    team_id: int,
    league_id: int | None = None,
    game_date: date | None = None,
    year: int | None = None,
    credentials: tuple[str, str] | None = None,
) -> dict:
    client = ESPNFantasyClient(_resolve_league_id(league_id), year, *(credentials or (None, None)))
    league = parse_league(await client.get_league(), client.year)
    team = _find_team(league, team_id)
    target = game_date or date.today()

    projections = await project_players(db, team["roster"], league["scoring"], target, days=1)
    lineup = optimize_lineup(projections, league["lineup_slot_counts"])
    return {
        "league": league["name"],
        "team": team["name"],
        "date": target.isoformat(),
        "scoring": _scoring_summary(league["scoring"]),
        **lineup,
        "notes": _notes(league["scoring"]),
    }


async def get_waiver_recommendations(
    db: AsyncSession,
    team_id: int,
    league_id: int | None = None,
    days: int = 7,
    limit: int = 10,
    year: int | None = None,
    credentials: tuple[str, str] | None = None,
) -> dict:
    """
    Rank free agents by expected points over the next `days`, and compare each
    to the weakest droppable player on the user's roster at a shared position.
    """
    client = ESPNFantasyClient(_resolve_league_id(league_id), year, *(credentials or (None, None)))
    league = parse_league(await client.get_league(), client.year)
    team = _find_team(league, team_id)
    start = date.today()

    fa_raw = await client.get_free_agents(size=75)
    free_agents = [parse_player(e, client.year) for e in fa_raw]
    fa_proj = await project_players(db, free_agents, league["scoring"], start, days=days, use_model=False)
    roster_proj = await project_players(db, team["roster"], league["scoring"], start, days=days, use_model=False)
    droppable = [p for p in roster_proj if p["lineup_slot"] != "IR"]

    def shares_position(a: dict, b: dict) -> bool:
        if (a["position"] == "G") != (b["position"] == "G"):
            return False
        if a["position"] == "G":
            return True
        specific = {0, 1, 2, 4}  # C/LW/RW/D - ignore F/UTIL flex slots
        return bool(set(a["_slots"]) & set(b["_slots"]) & specific)

    suggestions = []
    for fa in sorted(fa_proj, key=lambda x: x["expected_fp"], reverse=True):
        if fa["injury_status"]:
            continue
        same_pos = [r for r in droppable if shares_position(r, fa)]
        if not same_pos:
            continue
        worst = min(same_pos, key=lambda r: r["expected_fp"])
        gain = fa["expected_fp"] - worst["expected_fp"]
        if gain <= 0:
            continue
        suggestions.append({
            "add": _public(fa),
            "drop": _public(worst),
            "expected_gain_fp": round(gain, 2),
        })
        if len(suggestions) >= limit:
            break

    return {
        "league": league["name"],
        "team": team["name"],
        "window": {"start": start.isoformat(), "days": days},
        "scoring": _scoring_summary(league["scoring"]),
        "suggestions": suggestions,
        "notes": _notes(league["scoring"]) + [
            "Drop candidates are matched by position; check keeper/long-term value before dropping.",
        ],
    }


def _notes(scoring: LeagueScoring) -> list[str]:
    notes = [
        "Goalie starts aren't confirmed until morning skate; goalie projections assume they start.",
    ]
    if scoring.is_proxy:
        notes.append(
            f"League scores by {scoring.scoring_type.replace('_', ' ').lower()}; "
            "rankings use a standard points proxy across your categories."
        )
    return notes
