from __future__ import annotations

import math
from collections import Counter
from typing import Any

from .board import (
    available_players,
    enrich_with_rankings,
    needs,
    normalize_name,
    player_index,
    roster_players,
    taken_ids,
    user_roster,
)
from .store import DATA_DIR, now, read_json, snapshot_path


POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")


def _flock_lookup() -> dict[str, dict[str, Any]]:
    path = DATA_DIR / "flock" / "overall_rankings.json"
    if not path.exists():
        return {}
    output = {}
    for row in read_json(path).get("rankings", []):
        key = normalize_name(row.get("player"))
        if key:
            output[key] = row
    return output


def proposal_player_details(names: list[str]) -> dict[str, dict[str, Any]]:
    """Resolve assistant-authored player names to the same evidence as the board."""
    snapshot = read_json(snapshot_path())
    available = enrich_with_rankings(available_players(snapshot))
    flock = _flock_lookup()
    catalog = player_index()
    wanted = {normalize_name(name): name for name in names}
    output: dict[str, dict[str, Any]] = {}
    for player in available:
        key = normalize_name(player.get("name"))
        if key not in wanted:
            continue
        sleeper = catalog.get(str(player["player_id"]), {})
        flock_row = flock.get(key, {})
        output[wanted[key]] = {
            **player,
            "age": sleeper.get("age"),
            "years_exp": sleeper.get("years_exp"),
            "flock_rank": flock_row.get("rank"),
            "flock_tier": flock_row.get("tier"),
        }
    return output


def _user_turns(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    draft = snapshot["draft"]
    teams = int(draft.get("settings", {}).get("teams") or snapshot["league"].get("total_rosters") or 14)
    rounds = int(draft.get("settings", {}).get("rounds") or 16)
    user_id = snapshot["config"]["user_id"]
    slot = int(draft.get("draft_order", {}).get(user_id) or 1)
    roster_id = user_roster(snapshot)["roster_id"]
    catalog = player_index()
    selected = {
        int(pick["pick_no"]): pick
        for pick in snapshot["picks"]
        if pick.get("roster_id") == roster_id and pick.get("pick_no") is not None
    }
    slots = []
    for round_no in range(1, rounds + 1):
        pick = (round_no - 1) * teams + (slot if round_no % 2 else teams - slot + 1)
        selection = selected.get(pick)
        player = catalog.get(str(selection.get("player_id")), {}) if selection else {}
        slots.append(
            {
                "pick": pick,
                "round": round_no,
                "player_id": str(selection["player_id"]) if selection and selection.get("player_id") else None,
                "player_name": player.get("full_name") or (selection or {}).get("metadata", {}).get("first_name"),
                "keeper": bool(selection and selection.get("is_keeper")),
            }
        )
    turns: list[dict[str, Any]] = []
    for item in slots:
        if turns and item["pick"] == turns[-1]["slots"][-1]["pick"] + 1:
            turns[-1]["slots"].append(item)
        else:
            turns.append({"slots": [item]})
    for index, turn in enumerate(turns, start=1):
        turn["turn"] = index
    return turns


def _upcoming_turns(snapshot: dict[str, Any], count: int = 4) -> list[dict[str, Any]]:
    current = int(snapshot["league"].get("metadata", {}).get("current_pick_no") or 1)
    return [turn for turn in _user_turns(snapshot) if turn["slots"][-1]["pick"] >= current][:count]


def _candidate_score(player: dict[str, Any], gaps: dict[str, int], current_pick: int) -> tuple[float, dict[str, float]]:
    ecr = float(player.get("ecr") or player.get("adp") or 300)
    adp = float(player.get("adp") or ecr)
    position = player.get("position") or ""
    ranking = max(0.0, 1.0 - (ecr - 1.0) / 180.0)
    starter_gap = float(gaps.get(position, 0))
    flex_gap = float(gaps.get("FLEX", 0)) if position in {"RB", "WR", "TE"} else 0.0
    roster_fit = min(1.0, 0.25 + starter_gap * 0.35 + flex_gap * 0.15)
    value = max(0.0, min(1.0, 0.5 + (current_pick - adp) / 80.0))
    risk = 0.35 if player.get("injury") else 0.0
    total = 0.64 * ranking + 0.25 * roster_fit + 0.11 * value - 0.12 * risk
    factors = {
        "ranking": round(ranking, 3),
        "roster_fit": round(roster_fit, 3),
        "value": round(value, 3),
        "risk": round(risk, 3),
    }
    return total, factors


def build_assessment(*, position: str | None = None, limit: int = 5) -> dict[str, Any]:
    snapshot = read_json(snapshot_path())
    gaps = needs(snapshot)
    current_pick = int(snapshot["league"].get("metadata", {}).get("current_pick_no") or 1)
    available = enrich_with_rankings(available_players(snapshot, position=position))
    flock = _flock_lookup()
    catalog = player_index()

    scored = []
    for player in available:
        if player.get("ecr") is None and player.get("adp") is None:
            continue
        score, factors = _candidate_score(player, gaps, current_pick)
        sleeper = catalog.get(str(player["player_id"]), {})
        flock_row = flock.get(normalize_name(player.get("name")), {})
        scored.append(
            {
                **player,
                "age": sleeper.get("age"),
                "years_exp": sleeper.get("years_exp"),
                "flock_rank": flock_row.get("rank"),
                "flock_tier": flock_row.get("tier"),
                "score": score,
                "factors": factors,
            }
        )
    scored.sort(key=lambda item: (-item["score"], item.get("ecr") or 9999, item.get("name") or ""))
    candidates = scored[:limit]

    # A softmax makes the displayed recommendation shares comparable and
    # guarantees one meaningful denominator across the shortlist.
    weights = [math.exp((item["score"] - max((row["score"] for row in candidates), default=0.0)) * 5.0) for item in candidates]
    total_weight = sum(weights) or 1.0
    shares = [round(weight / total_weight * 100) for weight in weights]
    if shares:
        shares[0] += 100 - sum(shares)
    for candidate, share in zip(candidates, shares, strict=True):
        candidate["share"] = share
        candidate["score"] = round(candidate["score"], 3)

    upcoming_turns = _upcoming_turns(snapshot)
    decision_turn = upcoming_turns[0] if upcoming_turns else None
    pair_plans: list[dict[str, Any]] = []
    if decision_turn and len(decision_turn["slots"]) == 2 and decision_turn["slots"][0]["pick"] - current_pick <= 1:
        open_slots = [slot for slot in decision_turn["slots"] if not slot.get("player_id")]
        locked = [slot for slot in decision_turn["slots"] if slot.get("player_id")]
        plan_scores: list[tuple[float, list[dict[str, Any]]]] = []
        pool = scored[:12]
        if len(open_slots) == 2:
            for first_index, first in enumerate(pool):
                for second in pool[first_index + 1 :]:
                    diversity = 0.04 if first.get("position") != second.get("position") else 0.0
                    combined = float(first["score"]) + float(second["score"]) + diversity
                    plan_scores.append((combined, [first, second]))
        elif len(open_slots) == 1:
            locked_player = {
                "name": locked[0].get("player_name") or "LOCKED PICK",
                "position": "KEEPER" if locked[0].get("keeper") else "SELECTED",
                "player_id": locked[0].get("player_id"),
            }
            for candidate in pool:
                plan_scores.append((float(candidate["score"]), [locked_player, candidate]))
        plan_scores.sort(key=lambda item: item[0], reverse=True)
        chosen = plan_scores[:3]
        pair_weights = [math.exp((score - chosen[0][0]) * 4.0) for score, _ in chosen] if chosen else []
        pair_total = sum(pair_weights) or 1.0
        pair_shares = [round(weight / pair_total * 100) for weight in pair_weights]
        if pair_shares:
            pair_shares[0] += 100 - sum(pair_shares)
        for (score, players), share in zip(chosen, pair_shares, strict=True):
            pair_plans.append(
                {
                    "players": [
                        {
                            "player_id": player.get("player_id"),
                            "name": player.get("name"),
                            "position": player.get("position"),
                            "team": player.get("team"),
                        }
                        for player in players
                    ],
                    "share": share,
                    "score": round(score, 3),
                }
            )

    roster = roster_players(snapshot)
    counts = Counter(player.get("position") for player in roster)
    our_roster = user_roster(snapshot)
    return {
        "schema_version": 1,
        "generated_at": now(),
        "position_filter": position,
        "league": snapshot["league"].get("name"),
        "draft_status": snapshot["draft"].get("status"),
        "current_pick": current_pick,
        "picks_made": len(taken_ids(snapshot)),
        "our_roster_id": our_roster.get("roster_id"),
        "next_user_picks": [slot["pick"] for turn in upcoming_turns for slot in turn["slots"]][:4],
        "upcoming_turns": upcoming_turns,
        "pair_plans": pair_plans,
        "needs": gaps,
        "roster_counts": dict(counts),
        "roster": [
            {
                "name": player.get("full_name"),
                "position": player.get("position"),
                "team": player.get("team"),
            }
            for player in sorted(roster, key=lambda item: (item.get("position") or "", item.get("full_name") or ""))
        ],
        "candidates": candidates,
    }
