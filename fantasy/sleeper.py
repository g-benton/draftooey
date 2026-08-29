from __future__ import annotations

from typing import Any

from .config import Config
from .http import HttpClient
from .store import (
    now,
    players_path,
    read_json,
    sleeper_players_feed_path,
    sleeper_trending_path,
    snapshot_path,
    write_json,
)


BASE_URL = "https://api.sleeper.app/v1"


class SleeperClient:
    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def get(self, path: str) -> Any:
        return self.http.get_json(f"{BASE_URL}{path}")

    def get_json(self, path: str, *, params: dict[str, str] | None = None) -> Any:
        return self.http.get_json(f"{BASE_URL}{path}", params=params)


def normalize_players(data: Any) -> dict[str, dict[str, Any]]:
    """Normalize Sleeper's player map while retaining the provider fields."""
    if not isinstance(data, dict):
        raise ValueError("Sleeper players response must be a JSON object")
    output: dict[str, dict[str, Any]] = {}
    for player_id, player in data.items():
        if not isinstance(player, dict):
            continue
        record = dict(player)
        record.setdefault("player_id", str(player_id))
        output[str(player_id)] = record
    return output


def normalize_trending(data: Any) -> list[dict[str, Any]]:
    """Normalize trending rows into records with a stable player_id field."""
    if not isinstance(data, list):
        raise ValueError("Sleeper trending response must be a JSON array")
    output = []
    for row in data:
        if not isinstance(row, dict):
            continue
        record = dict(row)
        if record.get("player_id") is not None:
            record["player_id"] = str(record["player_id"])
        output.append(record)
    return output


def fetch_players(
    *,
    position: str | None = None,
    active: bool | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Fetch and cache the filtered Sleeper player map.

    ``active`` is omitted when None, matching Sleeper's documented semantics.
    The returned envelope contains both the raw response and normalized map.
    """
    if position is not None and not position.strip():
        raise ValueError("position must not be empty")
    position = position.upper() if position else None
    path = sleeper_players_feed_path(position=position, active=active)
    if path.exists() and not force:
        return read_json(path)
    params: dict[str, str] = {}
    if position:
        params["position"] = position
    if active is not None:
        params["active"] = "true" if active else "false"
    http = HttpClient()
    try:
        raw = SleeperClient(http).get_json("/players/nfl", params=params or None)
    finally:
        http.close()
    normalized = normalize_players(raw)
    payload = {
        "source": "sleeper",
        "kind": "players",
        "fetched_at": now(),
        "metadata": {"sport": "nfl", "position": position, "active": active},
        "raw": raw,
        "players": normalized,
    }
    write_json(path, payload)
    return payload


def fetch_trending(
    trend_type: str,
    *,
    lookback_hours: int = 24,
    limit: int = 25,
    force: bool = False,
) -> dict[str, Any]:
    """Fetch and cache Sleeper's recent adds or drops feed."""
    trend_type = trend_type.lower()
    if trend_type not in {"add", "drop"}:
        raise ValueError("trend_type must be 'add' or 'drop'")
    if lookback_hours < 1:
        raise ValueError("lookback_hours must be positive")
    if limit < 1:
        raise ValueError("limit must be positive")
    path = sleeper_trending_path(trend_type=trend_type, lookback_hours=lookback_hours, limit=limit)
    if path.exists() and not force:
        return read_json(path)
    params = {"lookback_hours": str(lookback_hours), "limit": str(limit)}
    http = HttpClient()
    try:
        raw = SleeperClient(http).get_json(f"/players/nfl/trending/{trend_type}", params=params)
    finally:
        http.close()
    payload = {
        "source": "sleeper",
        "kind": "trending",
        "fetched_at": now(),
        "metadata": {"sport": "nfl", "type": trend_type, "lookback_hours": lookback_hours, "limit": limit},
        "raw": raw,
        "players": normalize_trending(raw),
    }
    write_json(path, payload)
    return payload


def refresh(config: Config, *, include_players: bool = True) -> dict[str, Any]:
    http = HttpClient()
    try:
        client = SleeperClient(http)
        league = client.get(f"/league/{config.league_id}")
        draft = client.get(f"/draft/{league['draft_id']}")
        snapshot = {
            "refreshed_at": now(),
            "config": {"league_id": config.league_id, "user_id": config.user_id, "username": config.username},
            "league": league,
            "draft": draft,
            "picks": client.get(f"/draft/{league['draft_id']}/picks"),
            "rosters": client.get(f"/league/{config.league_id}/rosters"),
            "users": client.get(f"/league/{config.league_id}/users"),
            "nfl_state": client.get("/state/nfl"),
        }
        write_json(snapshot_path(), snapshot)
        if include_players:
            write_json(players_path(), {"refreshed_at": now(), "players": client.get("/players/nfl")})
        return snapshot
    finally:
        http.close()
