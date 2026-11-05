# -*- coding: utf-8 -*-
"""Playlist helpers, media-type detection and path utilities for Myostoox."""
from __future__ import annotations

import os
import sys
import json

try:
    from gi.repository import GLib
except Exception:
    GLib = None

STREAM_SCHEMES = ("http://", "https://", "mms://", "rtsp://", "icy://")

AUDIO_EXTENSIONS = {
    ".mp3", ".ogg", ".oga", ".opus", ".flac", ".wav", ".aac", ".m4a",
    ".mp4", ".mkv", ".ac3", ".dts", ".aiff", ".aif", ".wv", ".wma",
}
VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mpeg", ".mpg", ".webm",
}

def is_stream(path):
    if not isinstance(path, str): return False
    return path.lower().startswith(STREAM_SCHEMES)

def is_cdda(path):
    return isinstance(path, str) and path.startswith("cdda://")

def is_dvd(path):
    return isinstance(path, str) and path.lower().startswith("dvd://")

def is_bluray(path):
    if not isinstance(path, str): return False
    p = path.lower()
    return p.startswith("bluray://") or p.startswith("bd://")

def is_special_source(path):
    return is_cdda(path) or is_stream(path) or is_dvd(path) or is_bluray(path)

def path_to_uri(path):
    if is_special_source(path): return path
    return GLib.filename_to_uri(os.path.abspath(path), None)

def atomic_write_json(path, data):
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    except Exception as exc:
        print("Écriture impossible :", path, exc, file=sys.stderr)

def is_audio_file(path):
    if is_special_source(path): return True
    return (
        os.path.isfile(path)
        and os.path.splitext(path)[1].lower()
        in AUDIO_EXTENSIONS
    )

def is_pure_audio_file(path):
    if not isinstance(path, str): return False
    if is_special_source(path): return False
    if is_video_file(path): return False
    try:
        return (
            os.path.isfile(path)
            and os.path.splitext(path)[1].lower() in AUDIO_EXTENSIONS
        )
    except Exception:
        return False

def is_video_file(path):
    if not isinstance(path, str) or is_cdda(path) or is_stream(path): return False
    if is_dvd(path) or is_bluray(path): return True
    return os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS
