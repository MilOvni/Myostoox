# -*- coding: utf-8 -*-
"""Configuration paths and constants for Myostoox."""
from __future__ import annotations

import os

APP_NAME = "Myostoox"
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", APP_NAME)
SOCKET_FILE = os.path.join(CONFIG_DIR, "instance.sock")
LOCK_FILE = os.path.join(CONFIG_DIR, "instance.lock")
PLAYLIST_FILE = os.path.join(CONFIG_DIR, "playlist.json")
RECENT_FILE = os.path.join(CONFIG_DIR, "recent.json")
RECENT_MAX = 15
META_CACHE_FILE = os.path.join(CONFIG_DIR, "meta_cache.json")
META_DISK_MAX = 5000
STATE_FILE = os.path.join(CONFIG_DIR, "state.json")
RESUME_FILE = os.path.join(CONFIG_DIR, "resume.json")
RESUME_MIN_POS_S = 30.0
RESUME_END_MARGIN_S = 30.0
RESUME_MAX_ENTRIES = 200
RESUME_SAVE_INTERVAL_S = 12.0
RESUME_SEEK_MAX_TRIES = 10
RADIOS_FILE = os.path.join(CONFIG_DIR, "radios.json")
TVS_FILE = os.path.join(CONFIG_DIR, "tvs.json")
BOUQUETS_FILE = os.path.join(CONFIG_DIR, "bouquets.json")
EQ_PRESETS_FILE = os.path.join(CONFIG_DIR, "eq_presets.json")
ASSOCIATIONS_FILE = os.path.join(CONFIG_DIR, "associations.json")
DESKTOP_FILE_NAME = f"{APP_NAME.lower()}.desktop"
DESKTOP_FILE_PATH = os.path.join(
    os.path.expanduser("~"), ".local", "share", "applications", DESKTOP_FILE_NAME,
)
AUTOSTART_DIR = os.path.join(os.path.expanduser("~"), ".config", "autostart")
AUTOSTART_DESKTOP_PATH = os.path.join(AUTOSTART_DIR, DESKTOP_FILE_NAME)
WINDOW_PLACEMENTS = (
    "center", "top-left", "top-right", "bottom-left", "bottom-right",
)
WINDOW_PLACEMENT_MARGIN = 48
