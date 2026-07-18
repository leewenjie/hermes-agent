"""Configuration gate for the legacy hosted-runtime scaffold API."""
from __future__ import annotations

from hermes_cli.config import cfg_get, load_config

API_PREFIX = "/api/hosted/runtime"


def enabled() -> bool:
    """Return whether the legacy hosted-runtime bridge is explicitly enabled."""
    try:
        config = load_config() or {}
    except Exception:
        return False
    return cfg_get(
        config,
        "hosted_runtime_bridge",
        "enabled",
        default=False,
    ) is True


def matches_path(path: str) -> bool:
    """Return whether *path* belongs to the legacy bridge namespace."""
    return path == API_PREFIX or path.startswith(API_PREFIX + "/")
