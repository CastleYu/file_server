"""Shared filesystem locations for source, assets, and runtime data."""

from __future__ import annotations

import sys
from pathlib import Path


def _app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


APP_ROOT = _app_root()
ASSETS_DIR = APP_ROOT / "assets"
ICONS_DIR = ASSETS_DIR / "icons"
CERTS_DIR = APP_ROOT / "certs"
LOGS_DIR = APP_ROOT / "logs"
ROOT_DIR = APP_ROOT / "root"
CONFIG_PATH = APP_ROOT / "ftp_users.json"


def icon_path(name: str) -> str:
    return str(ICONS_DIR / name)
