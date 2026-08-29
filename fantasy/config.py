from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Config:
    league_id: str
    user_id: str
    username: str


def load_config(path: Path = Path("sleeper.yaml")) -> Config:
    if not path.exists():
        raise FileNotFoundError(f"Missing configuration file: {path}")
    raw = yaml.safe_load(path.read_text())
    if isinstance(raw, list):
        raw = {key: value for item in raw for key, value in item.items()}
    if not isinstance(raw, dict):
        raise ValueError("sleeper.yaml must be a mapping or a list of one-key mappings")
    required = ("league", "user_id", "username")
    missing = [key for key in required if not raw.get(key)]
    if missing:
        raise ValueError(f"sleeper.yaml is missing: {', '.join(missing)}")
    return Config(str(raw["league"]), str(raw["user_id"]), str(raw["username"]))

