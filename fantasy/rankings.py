from __future__ import annotations

import csv
import io
import json
import os
import re
from typing import Any

from .http import HttpClient
from .store import now, ranking_path, read_json, request_log_path, write_json


FANTASYPROS_BASE = "https://api.fantasypros.com/public/v2/json"
FANTASYPROS_HALF_PPR_URL = "https://www.fantasypros.com/nfl/rankings/half-point-ppr-cheatsheets.php"


def _save(
    source: str,
    kind: str,
    players: list[dict[str, Any]],
    metadata: dict[str, Any],
    *,
    variant: str | None = None,
) -> dict[str, Any]:
    payload = {"source": source, "kind": kind, "fetched_at": now(), "metadata": metadata, "players": players}
    write_json(ranking_path(source, kind, variant=variant), payload)
    return payload


def _record_request(source: str, kind: str, endpoint: str) -> None:
    """Record only completed provider requests; never store credentials."""
    path = request_log_path()
    entries = read_json(path) if path.exists() else []
    entries.append({"source": source, "kind": kind, "endpoint": endpoint, "completed_at": now()})
    write_json(path, entries)


def _embedded_json(html: str, variable: str) -> Any:
    """Decode a JSON value assigned to a named JavaScript variable.

    FantasyPros currently emits valid JSON after ``ecrData =`` and
    ``adpData =``.  ``raw_decode`` avoids brittle greedy regexes and safely
    ignores the rest of the page (including semicolons and script tags).
    """
    match = re.search(rf"\b(?:var|let|const)\s+{re.escape(variable)}\s*=\s*", html)
    if not match:
        raise ValueError(f"FantasyPros page is missing embedded {variable}")
    try:
        value, _ = json.JSONDecoder().raw_decode(html[match.end():])
    except json.JSONDecodeError as exc:
        raise ValueError(f"FantasyPros embedded {variable} is not valid JSON") from exc
    return value


def parse_fantasypros_html(html: str) -> dict[str, Any]:
    """Parse the Half-PPR cheatsheet's embedded ECR and ADP datasets.

    Returns normalized rows suitable for the existing ranking cache.  ECR
    rows retain provider IDs and useful metadata (bye week, tier, range and
    average); ADP rows are joined to ECR by provider ID because the page's
    ``adpData`` array contains only ``player_id`` and ``rank_ecr`` (the latter
    is the provider's field name for the ADP rank in this widget).
    """
    ecr_data = _embedded_json(html, "ecrData")
    adp_data = _embedded_json(html, "adpData")
    if not isinstance(ecr_data, dict) or not isinstance(ecr_data.get("players"), list):
        raise ValueError("FantasyPros ecrData has an unexpected shape")
    if not isinstance(adp_data, list):
        raise ValueError("FantasyPros adpData has an unexpected shape")

    ecr_players: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for item in ecr_data["players"]:
        if not isinstance(item, dict) or not item.get("player_name"):
            continue
        provider_id = item.get("player_id")
        row = {
            "fantasypros_id": provider_id,
            "name": item.get("player_name"),
            "team": item.get("player_team_id"),
            "position": item.get("player_position_id"),
            "bye_week": _number(item.get("player_bye_week")),
            "ecr": _number(item.get("rank_ecr")),
            "ecr_average": _number(item.get("rank_ave")),
            "ecr_min": _number(item.get("rank_min")),
            "ecr_max": _number(item.get("rank_max")),
            "tier": item.get("tier"),
        }
        ecr_players.append(row)
        if provider_id is not None:
            by_id[str(provider_id)] = row

    adp_players: list[dict[str, Any]] = []
    for item in adp_data:
        if not isinstance(item, dict) or item.get("player_id") is None:
            continue
        base = by_id.get(str(item["player_id"]))
        rank = item.get("rank_ecr")
        if rank is None:
            continue
        row = {
            "fantasypros_id": item["player_id"],
            "name": base.get("name") if base else None,
            "team": base.get("team") if base else None,
            "position": base.get("position") if base else None,
            "bye_week": base.get("bye_week") if base else None,
            "adp": _number(rank),
        }
        adp_players.append(row)

    metadata = {
        "url": FANTASYPROS_HALF_PPR_URL,
        "season": _number(ecr_data.get("year")),
        "scoring": ecr_data.get("scoring", "HALF"),
        "last_updated": ecr_data.get("last_updated"),
        "last_updated_ts": ecr_data.get("last_updated_ts"),
        "ecr_count": len(ecr_players),
        "adp_count": len(adp_players),
    }
    return {"ecr": ecr_players, "adp": adp_players, "metadata": metadata}


def _number(value: Any) -> int | float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def fetch_fantasypros_html(*, force: bool = False, url: str = FANTASYPROS_HALF_PPR_URL) -> dict[str, Any]:
    """Fetch and cache both Half-PPR FantasyPros datasets with one request."""
    ecr_path = ranking_path("fantasypros", "ecr")
    adp_path = ranking_path("fantasypros", "adp")
    if ecr_path.exists() and adp_path.exists() and not force:
        return {"ecr": read_json(ecr_path), "adp": read_json(adp_path)}
    http = HttpClient()
    try:
        parsed = parse_fantasypros_html(http.get_text(url))
    finally:
        http.close()
    metadata = {**parsed["metadata"], "url": url}
    ecr = _save("fantasypros", "ecr", parsed["ecr"], metadata)
    adp = _save("fantasypros", "adp", parsed["adp"], metadata)
    _record_request("fantasypros", "page", url)
    return {"ecr": ecr, "adp": adp}


def fetch_fantasypros(*, kind: str, season: int, scoring: str = "HALF", api_key: str | None = None, force: bool = False) -> dict[str, Any]:
    if kind not in {"ecr", "adp"}:
        raise ValueError("kind must be 'ecr' or 'adp'")
    cached_path = ranking_path("fantasypros", kind, variant="api")
    if cached_path.exists() and not force:
        return read_json(cached_path)
    api_key = api_key or os.getenv("FANTASYPROS_API_KEY")
    if not api_key:
        raise ValueError("Set FANTASYPROS_API_KEY before fetching FantasyPros rankings")
    # The live API currently rejects ``ALL`` for NFL consensus rankings even
    # though its documentation lists it. ``OP`` is its Superflex board; use
    # fetch_fantasypros_html for the standard overall Half-PPR board.
    params = {"position": "OP", "scoring": scoring}
    if kind == "adp":
        params["type"] = "ADP"
    http = HttpClient()
    try:
        data = http.get_json(
            f"{FANTASYPROS_BASE}/nfl/{season}/consensus-rankings",
            params=params,
            headers={"x-api-key": api_key},
        )
    finally:
        http.close()
    _record_request("fantasypros", kind, f"/nfl/{season}/consensus-rankings")
    players = []
    for item in data.get("players", []):
        players.append({
            "name": item.get("player_name"),
            "team": item.get("player_team_id"),
            "position": item.get("player_position_id"),
            "ecr": item.get("rank_ecr"),
            "adp": item.get("rank_ave") if kind == "adp" else item.get("adp"),
            "tier": item.get("tier"),
        })
    return _save(
        "fantasypros",
        kind,
        players,
        {"season": season, "scoring": scoring, "last_updated": data.get("last_updated"), "transport": "api"},
        variant="api",
    )


def import_csv(*, source: str, kind: str, url: str, name_column: str = "name", rank_column: str = "rank", position_column: str = "position") -> dict[str, Any]:
    if kind not in {"ecr", "adp"}:
        raise ValueError("kind must be 'ecr' or 'adp'")
    http = HttpClient()
    try:
        text = http.get_text(url)
    finally:
        http.close()
    reader = csv.DictReader(io.StringIO(text))
    players = []
    for row in reader:
        name = row.get(name_column)
        if not name:
            continue
        value = row.get(rank_column)
        try:
            value = float(value) if value else None
        except ValueError:
            value = None
        players.append({"name": name, "position": row.get(position_column), kind: value})
    return _save(source, kind, players, {"url": url, "columns": {"name": name_column, "rank": rank_column, "position": position_column}})
