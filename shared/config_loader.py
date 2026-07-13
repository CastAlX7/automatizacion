"""Carga la configuración por entorno desde config/{env}.json.

Uso:
    from shared.config_loader import load_config
    cfg = load_config()           # lee APP_ENV de .env (default: dev)
    cfg = load_config("staging")  # fuerza entorno
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

_cache: dict[str, dict[str, Any]] = {}


def load_config(env: str | None = None) -> dict[str, Any]:
    env = env or os.getenv("APP_ENV", "dev")
    if env in _cache:
        return _cache[env]

    config_path = CONFIG_DIR / f"{env}.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)

    _cache[env] = cfg
    return cfg


def get_feature_flag(flag: str, env: str | None = None) -> bool:
    cfg = load_config(env)
    return cfg.get("feature_flags", {}).get(flag, False)


def get_model(env: str | None = None) -> str:
    cfg = load_config(env)
    return cfg.get("model", os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"))


def get_eval_thresholds(env: str | None = None) -> dict[str, Any]:
    cfg = load_config(env)
    return cfg.get("evaluation", {})
