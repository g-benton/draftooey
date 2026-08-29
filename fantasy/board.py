from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process

from .store import players_path, ranking_paths, read_json, snapshot_path


FANTASY_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}


def normalize_name(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower().replace("dst", "def"))


def player_index() -> dict[str, dict[str, Any]]:
    return read_json(players_path())["players"]


def user_roster(snapshot: dict[str, Any]) -> dict[str, Any]:
    user_id = snapshot["config"]["user_id"]
    return next(roster for roster in snapshot["rosters"] if roster["owner_id"] == user_id)


def taken_ids(snapshot: dict[str, Any]) -> set[str]:
    return {str(pick["player_id"]) for pick in snapshot["picks"] if pick.get("player_id")}


def roster_players(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    players = player_index()
    roster = user_roster(snapshot)
    drafted = {
        str(pick["player_id"])
        for pick in snapshot["picks"]
        if pick.get("roster_id") == roster["roster_id"] and pick.get("player_id")
    }
    return [players[player_id] for player_id in drafted if player_id in players]


def available_players(snapshot: dict[str, Any], *, position: str | None = None) -> list[dict[str, Any]]:
    taken = taken_ids(snapshot)
    output = []
    for player_id, player in player_index().items():
        if player_id in taken or player.get("position") not in FANTASY_POSITIONS:
            continue
        if position and player.get("position") != position.upper():
            continue
        # Sleeper retains historical/free-agent records. A current NFL team is
        # the most useful baseline when no external rankings are cached.
        if not player.get("team") or player.get("status") not in {"Active", None}:
            continue
        output.append({"player_id": player_id, "name": player.get("full_name"), "position": player.get("position"), "team": player.get("team"), "injury": player.get("injury_status")})
    return output


def rankings() -> list[dict[str, Any]]:
    return [read_json(path) for path in ranking_paths()]


def enrich_with_rankings(players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources = rankings()
    lookup: dict[str, dict[str, Any]] = {}
    for source in sources:
        for ranked in source["players"]:
            key = normalize_name(ranked.get("name"))
            if key:
                values = {
                    key_: value
                    for key_, value in ranked.items()
                    if value is not None and key_ not in {"name", "position", "team", "player_id"}
                }
                if ranked.get("fantasypros_id") is not None:
                    values["fantasypros_id"] = ranked["fantasypros_id"]
                elif source.get("source") == "fantasypros" and ranked.get("player_id") is not None:
                    # Older caches called the provider ID ``player_id``. Keep
                    # Sleeper's ID canonical and retain this under its source.
                    values["fantasypros_id"] = ranked["player_id"]
                lookup.setdefault(key, {}).update(values)
    choices = list(lookup)
    for player in players:
        key = normalize_name(player["name"])
        values = lookup.get(key)
        if not values and choices:
            match = process.extractOne(key, choices, scorer=fuzz.ratio, score_cutoff=94)
            values = lookup.get(match[0]) if match else None
        if values:
            player.update(values)
    return players


def needs(snapshot: dict[str, Any]) -> dict[str, int]:
    roster = roster_players(snapshot)
    counts = Counter(player.get("position") for player in roster)
    starters = Counter(snapshot["league"]["roster_positions"])
    fixed_positions = ("QB", "RB", "WR", "TE", "K", "DEF")
    output = {position: max(0, starters[position] - counts[position]) for position in fixed_positions if starters[position]}
    flex_eligible = counts["RB"] + counts["WR"] + counts["TE"]
    fixed_flex_eligible = starters["RB"] + starters["WR"] + starters["TE"]
    output["FLEX"] = max(0, starters["FLEX"] - max(0, flex_eligible - fixed_flex_eligible))
    return output
