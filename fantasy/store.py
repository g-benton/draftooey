from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def read_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"No cached data at {path}. Run `uv run fantasy refresh` first.")
    return json.loads(path.read_text())


def snapshot_path() -> Path:
    return DATA_DIR / "sleeper" / "snapshot.json"


def players_path() -> Path:
    return DATA_DIR / "sleeper" / "players.json"


def sleeper_players_feed_path(*, position: str | None = None, active: bool | None = None) -> Path:
    """Return the cache path for a filtered Sleeper player feed."""
    position_key = (position or "all").lower()
    active_key = "any" if active is None else ("active" if active else "inactive")
    return DATA_DIR / "sleeper" / "feeds" / f"players-{position_key}-{active_key}.json"


def sleeper_trending_path(*, trend_type: str, lookback_hours: int = 24, limit: int = 25) -> Path:
    """Return the cache path for a Sleeper trending feed."""
    return DATA_DIR / "sleeper" / "feeds" / f"trending-{trend_type.lower()}-{lookback_hours}h-{limit}.json"


def ranking_path(source: str, kind: str, *, variant: str | None = None) -> Path:
    directory = DATA_DIR / source
    if variant:
        directory /= variant
    return directory / f"{kind}.json"


def ranking_paths() -> list[Path]:
    """Return primary normalized ranking datasets, excluding raw/legacy variants."""
    paths = []
    for kind in ("ecr", "adp", "editorial"):
        paths.extend(DATA_DIR.glob(f"*/{kind}.json"))
    return sorted(paths)


def request_log_path(source: str = "fantasypros") -> Path:
    return DATA_DIR / source / "request-log.json"


def advice_path() -> Path:
    return DATA_DIR / "session" / "advice.json"


def outlooks_path() -> Path:
    return DATA_DIR / "tinyfish" / "outlooks.json"


def council_root() -> Path:
    return DATA_DIR / "session" / "council"


def council_dir(pick: int) -> Path:
    return council_root() / str(int(pick))
