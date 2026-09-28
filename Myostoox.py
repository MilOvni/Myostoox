#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Myostoox - AUDIO/vidéo player
# Copyright (C) 2026 O. Fraisse (MilOvni)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import os
import sys
import math
import random
import cairo
import re
import json
import time
import unicodedata

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GstVideo", "1.0")
try:
    gi.require_version("GstPbutils", "1.0")
except Exception:
    pass
try:
    gi.require_version("PangoCairo", "1.0")
except Exception:
    pass

from gi.repository import Gtk, Gdk, Gst, GdkPixbuf, GLib, Pango, GstVideo, Gio

try:
    from gi.repository import PangoCairo
except Exception:
    PangoCairo = None

try:
    from gi.repository import GstPbutils
except Exception:
    GstPbutils = None


try:
    from mutagen import File as MutagenFile
    from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, ID3NoHeaderError
    from mutagen.flac import FLAC
    from mutagen.oggvorbis import OggVorbis
    from mutagen.oggopus import OggOpus
    from mutagen.mp4 import MP4
except ImportError:
    MutagenFile = None
    ID3 = TIT2 = TPE1 = TALB = TDRC = ID3NoHeaderError = None

import socket
import threading
import subprocess
import fcntl
import errno

APP_NAME = "Myostoox"
DEBUG_MYOSTOOX = False  # True → logs pipeline MKV / diagnostics

# Politique codecs vidéo / AV1
# NVIDIA (nvh264dec, nvh265dec, nvav1dec, …) prioritaire en mode Matériel ;
# repli logiciel (avdec_*, dav1d) si HW indisponible ou en échec.
_AV1_HW_OTHER_NAMES = (
    "vaav1dec",
    "vaapiav1dec",
    "v4l2av1dec",
    "msdkav1dec",
)
_AV1_HW_NVIDIA_NAMES = (
    "nvav1dec",
)
# Compat : ancienne liste complète
_AV1_HW_DECODER_NAMES = _AV1_HW_OTHER_NAMES + _AV1_HW_NVIDIA_NAMES
# Ordre soft : dav1d (si paquet) → libaom → libav
_AV1_SOFT_DECODER_ORDER = ("dav1ddec", "av1dec", "avdec_av1")

# Priorité détection / rang HW (mode Matériel) :
#   1. NVIDIA  2. AMD VA  3. Vulkan  4. Intel VA-API  5. Quick Sync
# Repli logiciel (avdec_*, dav1d) si élément absent ou échec.
_NVIDIA_DECODER_NAMES = (
    "nvh264dec",
    "nvh265dec",
    "nvav1dec",
    "nvvp8dec",
    "nvvp9dec",
    "nvmpeg2videodec",
    "nvmpeg4videodec",
    "nvmpegvideodec",
    "nvv4l2decoder",
    "nvdec",
)

# AMD VA / Intel VA-API / Vulkan / Quick Sync / V4L2
# (rangs plus bas que NVIDIA et que le soft en mode Matériel pour
#  préserver la priorité NVIDIA et éviter les crashs VA-API historiques)
_OTHER_HW_DECODER_NAMES = (
    # AMD / Intel VA (éléments modernes va*)
    "vah264dec",
    "vah265dec",
    "vaav1dec",
    "vavp8dec",
    "vavp9dec",
    "vampeg2dec",
    # VA-API classiques
    "vaapidecodebin",
    "vaapih264dec",
    "vaapih265dec",
    "vaapiav1dec",
    "vaapivp8dec",
    "vaapivp9dec",
    # Vulkan Video
    "vulkanh264dec",
    "vulkanh265dec",
    # Intel Quick Sync
    "qsvh264dec",
    "qsvh265dec",
    "qsvvp9dec",
    # V4L2
    "v4l2h265dec",
    "v4l2h264dec",
    "v4l2slh265dec",
    "v4l2slh264dec",
)

_SOFTWARE_VIDEO_DECODER_NAMES = (
    "avdec_h264",
    "avdec_h265",
    "avdec_hevc",
    "avdec_vp9",
    "avdec_vp8",
    "avdec_av1",
    "avdec_mpeg2video",
    "avdec_mpeg4",
    "dav1ddec",
    "av1dec",
)


def _gst_set_rank(name, rank):
    """Assigne un rang GStreamer ; ignore si l'élément est absent."""
    try:
        factory = Gst.ElementFactory.find(name)
        if factory is not None:
            factory.set_rank(rank)
    except Exception:
        pass


def apply_safe_gstreamer_ranks(prefer_nvidia_av1=False):
    """Rangs audio + AV1 au démarrage (avant toute lecture).

    prefer_nvidia_av1=True (mode Matériel) : nvav1dec prioritaire, soft en repli.
    Sinon : soft uniquement (évite les crashs VA-API AV1 historiques).
    """
    _gst_set_rank("a52dec", Gst.Rank.NONE)
    for n in ("avdec_eac3", "avdec_ac3"):
        _gst_set_rank(n, Gst.Rank.PRIMARY + 100)

    for n in _AV1_HW_OTHER_NAMES:
        _gst_set_rank(n, Gst.Rank.NONE)

    if prefer_nvidia_av1:
        for n in _AV1_HW_NVIDIA_NAMES:
            _gst_set_rank(n, Gst.Rank.PRIMARY + 200)
        soft_ranks = (
            (_AV1_SOFT_DECODER_ORDER[0], Gst.Rank.PRIMARY + 90),
            (_AV1_SOFT_DECODER_ORDER[1], Gst.Rank.PRIMARY + 80),
            (_AV1_SOFT_DECODER_ORDER[2], Gst.Rank.PRIMARY + 70),
            ("av1parse", Gst.Rank.PRIMARY + 50),
        )
    else:
        for n in _AV1_HW_NVIDIA_NAMES:
            _gst_set_rank(n, Gst.Rank.NONE)
        soft_ranks = (
            (_AV1_SOFT_DECODER_ORDER[0], Gst.Rank.PRIMARY + 120),
            (_AV1_SOFT_DECODER_ORDER[1], Gst.Rank.PRIMARY + 110),
            (_AV1_SOFT_DECODER_ORDER[2], Gst.Rank.PRIMARY + 100),
            ("av1parse", Gst.Rank.PRIMARY + 50),
        )
    for n, rank in soft_ranks:
        _gst_set_rank(n, rank)


# Notifications bureau (libnotify) — optionnel sous Lubuntu/XFCE
try:
    gi.require_version("Notify", "0.7")
    from gi.repository import Notify
    if not Notify.is_initted():
        Notify.init(APP_NAME)
except Exception:
    Notify = None

# Répertoire de configuration (partagé : playlist, état, verrou d'instance)
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", APP_NAME)

# Socket Unix + verrou fichier pour l'instance unique.
# Le verrou fcntl est la source de vérité : une seule instance peut le détenir.
# Le socket sert uniquement à transmettre les chemins de fichiers à cette instance.
SOCKET_FILE = os.path.join(CONFIG_DIR, "instance.sock")
LOCK_FILE = os.path.join(CONFIG_DIR, "instance.lock")

PLAYLIST_FILE = os.path.join(
    CONFIG_DIR,
    "playlist.json"
)

RECENT_FILE = os.path.join(CONFIG_DIR, "recent.json")
RECENT_MAX = 15
PATREON_URL = "https://www.patreon.com/MilOvni"

STATE_FILE = os.path.join(CONFIG_DIR, "state.json")
RADIOS_FILE = os.path.join(CONFIG_DIR, "radios.json")
TVS_FILE = os.path.join(CONFIG_DIR, "tvs.json")
BOUQUETS_FILE = os.path.join(CONFIG_DIR, "bouquets.json")
EQ_PRESETS_FILE = os.path.join(CONFIG_DIR, "eq_presets.json")
ASSOCIATIONS_FILE = os.path.join(CONFIG_DIR, "associations.json")

DESKTOP_FILE_NAME = f"{APP_NAME.lower()}.desktop"
DESKTOP_FILE_PATH = os.path.join(
    os.path.expanduser("~"),
    ".local",
    "share",
    "applications",
    DESKTOP_FILE_NAME,
)


AUDIO_EXTENSIONS = {
    ".mp3",
    ".ogg",
    ".oga",
    ".opus",
    ".flac",
    ".wav",
    ".aac",
    ".m4a",
    ".mp4",
    ".mkv",
    ".ac3",
    ".dts",
    ".aiff",
    ".aif",
    ".wv",
    ".wma",
}

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".avi",
    ".mpeg",
    ".mpg",
    ".webm",
}

# Fichiers de sous-titres externes (même dossier que la vidéo)
SUBTITLE_EXTENSIONS = {
    ".srt",
    ".vtt",
    ".ass",
    ".ssa",
    ".sub",
    ".smi",
    ".ttml",
    ".sbv",
}

# Extensions associables + types MIME (xdg-mime)
ASSOCIABLE_FORMATS = [
    (".mp3", ["audio/mpeg", "audio/mp3"]),
    (".flac", ["audio/flac", "audio/x-flac"]),
    (".ogg", ["audio/ogg", "audio/vorbis"]),
    (".oga", ["audio/ogg"]),
    (".opus", ["audio/opus", "audio/ogg"]),
    (".wav", ["audio/wav", "audio/x-wav", "audio/vnd.wave"]),
    (".aac", ["audio/aac", "audio/x-aac"]),
    (".m4a", ["audio/mp4", "audio/x-m4a"]),
    (".wma", ["audio/x-ms-wma", "audio/wma"]),
    (".mp4", ["video/mp4", "audio/mp4"]),
    (".mkv", ["video/x-matroska", "video/mkv"]),
    (".avi", ["video/x-msvideo", "video/avi"]),
    (".mpeg", ["video/mpeg"]),
    (".mpg", ["video/mpeg"]),
    (".webm", ["video/webm", "audio/webm"]),
    (".ac3", ["audio/ac3"]),
    (".dts", ["audio/vnd.dts", "audio/dts"]),
    (".aiff", ["audio/x-aiff", "audio/aiff"]),
    (".aif", ["audio/x-aiff"]),
    (".wv", ["audio/x-wavpack"]),
]

STREAM_SCHEMES = ("http://", "https://", "mms://", "rtsp://", "icy://")


LANGUAGES = (
    "English",
    "Français",
    "Deutsch",
    "Castellano",
    "Italiano",
    "Nederlands",
    "Português",
    "Polski",
    "Svenska",
    "Русский",
)

_TR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "translations.json")
try:
    with open(_TR_FILE, encoding="utf-8") as _f:
        TRANSLATIONS = json.load(_f)
except Exception as exc:
    print("translations.json illisible :", exc, file=sys.stderr)
    TRANSLATIONS = {}

DEFAULT_SHORTCUTS = {
    "play_pause": "space",
    "mute": "m",
    "fullscreen": "F11",
    "fullscreen_alt": "f",
    "seek_back_5": "Left",
    "seek_forward_5": "Right",
    "seek_back_10": "j",
    "seek_forward_10": "l",
    "seek_back_30": "Page_Up",
    "seek_forward_30": "Page_Down",
    "volume_up": "Ctrl+Up",
    "volume_down": "Ctrl+Down",
    "playlist_up": "Up",
    "playlist_down": "Down",
    "delete": "Delete",
    "clear_playlist": "F1",
    "screenshot": "F12",
}

SHORTCUT_LABELS = {
    "play_pause": "Lecture / Pause",
    "mute": "Muet",
    "clear_playlist": "Vider la playlist",
    "fullscreen": "Plein écran",
    "fullscreen_alt": "Plein écran (alt.)",
    "seek_back_5": "Seek −5 s",
    "seek_forward_5": "Seek +5 s",
    "seek_back_10": "Seek −10 s",
    "seek_forward_10": "Seek +10 s",
    "seek_back_30": "Seek −30 s",
    "seek_forward_30": "Seek +30 s",
    "volume_up": "Volume +",
    "volume_down": "Volume −",
    "playlist_up": "Playlist ↑",
    "playlist_down": "Playlist ↓",
    "delete": "Supprimer la sélection",
    "screenshot": "Capture d'écran",
}


THEME_PALETTES = {
    "sombre": {
        "window_bg": "#111315", "window_fg": "#d8dddf",
        "topbar_bg": "#191c1f", "topbar_border": "#303438",
        "btn_bg": "#22262a", "btn_fg": "#cdd3d5", "btn_border": "#383d41",
        "btn_hover": "#30363b", "btn_active_bg": "#3a4d56",
        "btn_active_border": "#5c737e", "time_fg": "#aebbc0",
        "header_bg": "#25292c", "header_fg": "#aeb7bb",
        "tree_bg": "#111315", "tree_fg": "#c7cccf",
        "tree_sel_bg": "#30404a", "tree_sel_fg": "#ffffff",
        "bottom_bg": "#171a1c", "bottom_border": "#303438",
        "cover_bg": "#090b0c", "cover_border": "#353a3e",
        "title_fg": "#dce2e4", "info_fg": "#89959a",
        "status_fg": "#69757a", "menu_fg": "#d8dddf",
        "menu_bg": "#1e2226", "menu_hover": "#30404a",
        "osd_bg": "rgba(0,0,0,0.78)", "osd_fg": "#ffffff",
    },
    "clair": {
        "window_bg": "#f4f5f7", "window_fg": "#2a2e33",
        "topbar_bg": "#e8eaee", "topbar_border": "#cfd3da",
        "btn_bg": "#ffffff", "btn_fg": "#2a2e33", "btn_border": "#c5cad3",
        "btn_hover": "#dfe3ea", "btn_active_bg": "#c5d4e0",
        "btn_active_border": "#7a9bb0", "time_fg": "#5a6570",
        "header_bg": "#eef0f3", "header_fg": "#5a6570",
        "tree_bg": "#fafbfc", "tree_fg": "#2a2e33",
        "tree_sel_bg": "#b8cce0", "tree_sel_fg": "#1a1e22",
        "bottom_bg": "#e8eaee", "bottom_border": "#cfd3da",
        "cover_bg": "#e2e5ea", "cover_border": "#c5cad3",
        "title_fg": "#1f2328", "info_fg": "#5a6570",
        "status_fg": "#7a8490", "menu_fg": "#1f2328",
        "menu_bg": "#ffffff", "menu_hover": "#dfe3ea",
        "osd_bg": "rgba(40,44,52,0.85)", "osd_fg": "#ffffff",
    },
    "sang": {
        "window_bg": "#2a1418", "window_fg": "#ffe8ec",
        "topbar_bg": "#3d1c22", "topbar_border": "#7a3038",
        "btn_bg": "#5a2830", "btn_fg": "#ffe8ec", "btn_border": "#8a4048",
        "btn_hover": "#6e343c", "btn_active_bg": "#a03848",
        "btn_active_border": "#d05060", "time_fg": "#f0c0c8",
        "header_bg": "#3a1e24", "header_fg": "#f0c0c8",
        "tree_bg": "#2a1418", "tree_fg": "#ffe0e4",
        "tree_sel_bg": "#8a303c", "tree_sel_fg": "#ffffff",
        "bottom_bg": "#32181c", "bottom_border": "#7a3038",
        "cover_bg": "#1e1014", "cover_border": "#7a3038",
        "title_fg": "#fff0f2", "info_fg": "#e0a8b0",
        "status_fg": "#c08890", "menu_fg": "#ffe8ec",
        "menu_bg": "#3d1c22", "menu_hover": "#6e343c",
        "osd_bg": "rgba(120,30,40,0.90)", "osd_fg": "#ffffff",
    },
    "nature": {
        "window_bg": "#1e7a3a", "window_fg": "#f2fff4",
        "topbar_bg": "#196b32", "topbar_border": "#145a2a",
        "btn_bg": "#c8efc0", "btn_fg": "#145a2a", "btn_border": "#a8d8a0",
        "btn_hover": "#daf5d4", "btn_active_bg": "#e8ffe4",
        "btn_active_border": "#f4fff2", "time_fg": "#d4f0d8",
        "header_bg": "#1a7034", "header_fg": "#d4f0d8",
        "tree_bg": "#1e7a3a", "tree_fg": "#f0fff2",
        "tree_sel_bg": "#c8efc0", "tree_sel_fg": "#0f3d1c",
        "bottom_bg": "#196b32", "bottom_border": "#145a2a",
        "cover_bg": "#145a2a", "cover_border": "#0f4a22",
        "title_fg": "#ffffff", "info_fg": "#d4f0d8",
        "status_fg": "#b0dcb8", "menu_fg": "#f2fff4",
        "menu_bg": "#196b32", "menu_hover": "#248a44",
        "osd_bg": "rgba(15, 61, 28, 0.92)", "osd_fg": "#e8ffe4",
    },
    "aqua": {
        "window_bg": "#0e1a24", "window_fg": "#e0f0f8",
        "topbar_bg": "#152838", "topbar_border": "#2a5a78",
        "btn_bg": "#1e3a50", "btn_fg": "#e0f0f8", "btn_border": "#3a6a88",
        "btn_hover": "#284a64", "btn_active_bg": "#2a7090",
        "btn_active_border": "#50b0d0", "time_fg": "#90c8e0",
        "header_bg": "#183040", "header_fg": "#90c8e0",
        "tree_bg": "#0e1a24", "tree_fg": "#d8eaf4",
        "tree_sel_bg": "#2a6080", "tree_sel_fg": "#ffffff",
        "bottom_bg": "#122030", "bottom_border": "#2a5a78",
        "cover_bg": "#0a141c", "cover_border": "#2a5a78",
        "title_fg": "#f0f8fc", "info_fg": "#88b8d0",
        "status_fg": "#6a98b0", "menu_fg": "#e0f0f8",
        "menu_bg": "#152838", "menu_hover": "#284a64",
        "osd_bg": "rgba(16,48,72,0.90)", "osd_fg": "#e8f8ff",
    },
    "bigtron": {
        "window_bg": "#0d0704", "window_fg": "#3b4859",
        "topbar_bg": "#ff8a1e", "topbar_border": "#000000",
        "btn_bg": "#150b05", "btn_fg": "#ff9a33", "btn_border": "#000000",
        "btn_hover": "#241207", "btn_active_bg": "#ff8a1e",
        "btn_active_border": "#1c0f04", "time_fg": "#1c0f04",
        "header_bg": "#1c1108", "header_fg": "#ff8a1e",
        "tree_bg": "#0d0704", "tree_fg": "#ffcf99",
        "tree_sel_bg": "#ff8a1e", "tree_sel_fg": "#1c0f04",
        "bottom_bg": "#ff8a1e", "bottom_border": "#000000",
        "cover_bg": "#0d0704", "cover_border": "#000000",
        "title_fg": "#1c0f04", "info_fg": "#3a1f0a",
        "status_fg": "#5c3814", "menu_fg": "#ffcf99",
        "menu_bg": "#150b05", "menu_hover": "#ff8a1e",
        "osd_bg": "rgba(20,10,5,0.92)", "osd_fg": "#ff8a1e",
    },
    "kawai": {
        "window_bg": "#ffe4f0", "window_fg": "#6285cc",
        "topbar_bg": "#ff9ec8", "topbar_border": "#ff5fa3",
        "btn_bg": "#ffc8e0", "btn_fg": "#4bb84d", "btn_border": "#ffffff",
        "btn_hover": "#ffb0d4", "btn_active_bg": "#ff6eb4",
        "btn_active_border": "#e04090", "time_fg": "#4f76c4",
        "header_bg": "#ffd0e8", "header_fg": "#4f76c4",
        "tree_bg": "#fff0f6", "tree_fg": "#4f76c4",
        "tree_sel_bg": "#77e09a", "tree_sel_fg": "#ffffff",
        "bottom_bg": "#ff9ec8", "bottom_border": "#ff5fa3",
        "cover_bg": "#c8f5e0", "cover_border": "#7ed9b0",
        "title_fg": "#ffffff", "info_fg": "#5090c8",
        "status_fg": "#ffffff", "menu_fg": "#4f76c4",
        "menu_bg": "#ffe8f2", "menu_hover": "#77e09a",
        "osd_bg": "rgba(220,60,130,0.90)", "osd_fg": "#ffffff",
    },
    "ciel": {
        "window_bg": "#1a4a9a", "window_fg": "#e8f4ff",
        "topbar_bg": "#2a7fd4", "topbar_border": "#5eb8f0",
        "btn_bg": "#2e6ec0", "btn_fg": "#ffffff", "btn_border": "#5eb8f0",
        "btn_hover": "#3a8ae0", "btn_active_bg": "#4ec8f8",
        "btn_active_border": "#a0e0ff", "time_fg": "#d0ecff",
        "header_bg": "#3a9ad8", "header_fg": "#e8f8ff",
        "tree_bg": "#1e50a8", "tree_fg": "#e8f4ff",
        "tree_sel_bg": "#4ec8f8", "tree_sel_fg": "#0a2850",
        "bottom_bg": "#2a7fd4", "bottom_border": "#5eb8f0",
        "cover_bg": "#163d88", "cover_border": "#5eb8f0",
        "title_fg": "#ffffff", "info_fg": "#a8e5ff",
        "status_fg": "#afdcfa", "menu_fg": "#e8f4ff",
        "menu_bg": "#2458b0", "menu_hover": "#3a8ae0",
        "osd_bg": "rgba(20,60,140,0.92)", "osd_fg": "#ffffff",
    },
}

THEME_LABELS = {
    "aqua": "Aqua",
    "sombre": "Sombre",
    "clair": "Clair",
    "bigtron": "BigTron",
    "sang": "Sang",
    "nature": "Nature",
    "kawai": "Kawai",
    "ciel": "Ciel",
}


# ============================================================
# SPECTRE
# ============================================================

SPECTRUM_BANDS = 12
SPECTRUM_SOURCE_BANDS = 64
SPECTRUM_LOW_CUT = 0.24
SPECTRUM_SPREAD_EXPONENT = 2.25
SPECTRUM_LOW_DB = -79.0
SPECTRUM_HIGH_DB = -4.0
SPECTRUM_VISUAL_FLOOR = 0.00
SPECTRUM_VERTICAL_COMPRESSION = 1.29

SPECTRUM_AGC_ENABLED_DEFAULT = True
SPECTRUM_AGC_CEILING_MIN_DB = -38.0
SPECTRUM_AGC_ATTACK = 0.35
SPECTRUM_AGC_RELEASE = 0.006

SPECTRUM_PEAK_HOLD_SECONDS = 0.30
SPECTRUM_PEAK_RISE = 0.80
SPECTRUM_PEAK_FALL = 0.965

SPECTRUM_REFERENCE_DT = 0.016


# ============================================================
# ÉGALISEUR
# ============================================================

EQ_BANDS = 10
EQ_FREQS = [
    "29 Hz", "59 Hz", "119 Hz", "237 Hz", "474 Hz",
    "947 Hz", "1.9 kHz", "3.8 kHz", "7.5 kHz", "15 kHz"
]
EQ_MIN_DB = -24.0
EQ_MAX_DB = 12.0


# ============================================================
# UTILITAIRES
# ============================================================

def format_time(seconds):

    try:
        seconds = int(seconds)
    except Exception:
        seconds = 0

    seconds = max(0, seconds)

    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)

    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"

    return f"{minutes}:{seconds:02d}"


def is_stream(path):
    if not isinstance(path, str):
        return False
    return path.lower().startswith(STREAM_SCHEMES)


def is_cdda(path):
    return isinstance(path, str) and path.startswith("cdda://")


def is_dvd(path):
    return isinstance(path, str) and path.lower().startswith("dvd://")


def is_bluray(path):
    if not isinstance(path, str):
        return False
    p = path.lower()
    return p.startswith("bluray://") or p.startswith("bd://")


def is_special_source(path):
    """CD / DVD / Blu-ray / stream (URI non-fichier)."""
    return is_cdda(path) or is_stream(path) or is_dvd(path) or is_bluray(path)


def path_to_uri(path):
    if is_special_source(path):
        return path
    return GLib.filename_to_uri(os.path.abspath(path), None)


def atomic_write_json(path, data):
    """Écriture atomique JSON (tmp + replace)."""
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
    if is_special_source(path):
        return True
    return (
        os.path.isfile(path)
        and os.path.splitext(path)[1].lower()
        in AUDIO_EXTENSIONS
    )


def is_pure_audio_file(path):
    """Audio local uniquement (mp3/flac/ogg/wav…).

    Exclut explicitement : vidéo, CD, DVD, Blu-ray, flux radio/TV/bouquets.
    Utilisé pour pitch/tempo.
    """
    if not isinstance(path, str):
        return False
    if is_special_source(path):
        return False
    if is_video_file(path):
        return False
    try:
        return (
            os.path.isfile(path)
            and os.path.splitext(path)[1].lower() in AUDIO_EXTENSIONS
        )
    except Exception:
        return False


def is_video_file(path):
    if not isinstance(path, str) or is_cdda(path) or is_stream(path):
        return False
    if is_dvd(path) or is_bluray(path):
        return True
    return os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS


def find_external_subtitles(video_path):
    """Détecte les fichiers de sous-titres externes associés à une vidéo.

    Règles d'association (même répertoire), par ordre de priorité :
      1. Même nom de base : Film.mp4 → Film.srt / Film.ass
      2. Suffixe langue   : Film.fr.srt, Film.en.ass, Film_fr.srt, Film-en.srt
      3. Contient le base : « Film (2020).fr.srt » si base = « Film (2020) »
      4. Un seul fichier de sous-titres dans le dossier → association par défaut

    Retourne une liste de dicts :
      {"path": str, "lang": str|None, "label": str, "ext": str}
    """
    results = []
    if not isinstance(video_path, str) or is_stream(video_path) or is_cdda(video_path):
        return results
    try:
        if is_dvd(video_path) or is_bluray(video_path):
            return results
    except Exception:
        pass
    if not os.path.isfile(video_path):
        return results

    directory = os.path.dirname(os.path.abspath(video_path))
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    base_lower = base_name.casefold()

    # Normalise séparateurs pour comparaisons souples
    def _norm_token(s):
        s = (s or "").casefold().strip()
        for ch in ("_", "-", " "):
            s = s.replace(ch, ".")
        while ".." in s:
            s = s.replace("..", ".")
        return s.strip(".")

    base_norm = _norm_token(base_name)

    try:
        entries = os.listdir(directory)
    except OSError:
        return results

    all_subs = []  # tous les fichiers sous-titres du dossier
    for name in entries:
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        root, ext = os.path.splitext(name)
        ext_lower = ext.lower()
        if ext_lower not in SUBTITLE_EXTENSIONS:
            continue
        all_subs.append((name, path, root, ext_lower))

    if not all_subs:
        return results

    matched_paths = set()

    def _add(path, lang, ext_lower, priority):
        if path in matched_paths:
            return
        matched_paths.add(path)
        fmt = ext_lower.lstrip(".").upper()
        if lang:
            label = "%s (%s)" % (lang, fmt)
        else:
            label = "%s [%s]" % (fmt, os.path.basename(path))
        results.append({
            "path": path,
            "lang": lang,
            "label": label,
            "ext": ext_lower,
            "_prio": priority,
        })

    # --- Passe 1 : match strict / suffixe langue ---
    for name, path, root, ext_lower in all_subs:
        root_lower = root.casefold()
        lang = None
        prio = 50

        if root_lower == base_lower:
            lang = None
            prio = 0
        elif root_lower.startswith(base_lower + "."):
            suffix = root[len(base_name) + 1 :]
            token = suffix.split(".")[0].strip() if suffix else ""
            lang = token if token else None
            prio = 1
        else:
            # Film_fr.srt / Film-en.srt / Film fr.srt
            root_norm = _norm_token(root)
            if root_norm == base_norm:
                lang = None
                prio = 2
            elif root_norm.startswith(base_norm + "."):
                suffix = root_norm[len(base_norm) + 1 :]
                token = suffix.split(".")[0].strip() if suffix else ""
                lang = token if token else None
                prio = 3
            elif base_norm in root_norm and (
                root_norm.startswith(base_norm)
                or (".%s." % base_norm) in (".%s." % root_norm)
            ):
                # base contenu au début
                rest = root_norm[len(base_norm):].lstrip(".")
                token = rest.split(".")[0] if rest else ""
                lang = token if token else None
                prio = 4
            else:
                continue

        _add(path, lang, ext_lower, prio)

    # --- Passe 2 : un seul .srt/.ass dans le dossier et aucun match → l'associer ---
    if not results and len(all_subs) == 1:
        name, path, root, ext_lower = all_subs[0]
        _add(path, None, ext_lower, 10)

    # --- Passe 3 : plusieurs sous-titres non matchés, mais un seul partage
    #     un long préfixe commun avec le nom vidéo ---
    if not results and len(all_subs) > 1:
        for name, path, root, ext_lower in all_subs:
            root_norm = _norm_token(root)
            # Au moins 6 caractères de préfixe commun
            common = 0
            for a, b in zip(base_norm, root_norm):
                if a == b:
                    common += 1
                else:
                    break
            if common >= 6 and common >= min(len(base_norm), len(root_norm)) * 0.5:
                rest = root_norm[common:].lstrip(".")
                token = rest.split(".")[0] if rest else ""
                _add(path, token or None, ext_lower, 20)

    results.sort(
        key=lambda item: (
            item.get("_prio", 99),
            0 if not item["lang"] else 1,
            (item["lang"] or "").casefold(),
            item["path"].casefold(),
        )
    )
    for item in results:
        item.pop("_prio", None)
    return results


def parse_lrc_content(content):
    """Parse un fichier LRC synchronisé → liste (seconds, text) triée."""
    lines = []
    for raw in content.splitlines():
        raw = raw.strip()
        if not raw or raw.startswith(("[ti:", "[ar:", "[al:", "[by:", "[offset:", "[length:")):
            continue
        tags = list(re.finditer(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]", raw))
        if not tags:
            continue
        last = tags[-1]
        text_part = raw[last.end():].strip()
        if not text_part:
            continue
        for m in tags:
            mm = int(m.group(1))
            ss = int(m.group(2))
            frac = m.group(3) or "0"
            if len(frac) == 1:
                frac = frac + "0"
            cent = int(frac) if frac.isdigit() else 0
            if len(m.group(3) or "") == 3:
                sec = mm * 60 + ss + cent / 1000.0
            else:
                sec = mm * 60 + ss + cent / 100.0
            lines.append((sec, text_part))
    lines.sort(key=lambda x: x[0])
    return lines


def load_lyrics_for_file(filepath):
    """Charge les paroles d'un fichier audio local.

    Priorité : .lrc → .txt → tags embarqués (USLT / LYRICS).
    Retourne {"timed": bool, "lines": [(sec|None, text), ...], "source": str}
    ou None.
    """
    if not isinstance(filepath, str) or not is_pure_audio_file(filepath):
        return None
    if not os.path.isfile(filepath):
        return None

    directory = os.path.dirname(os.path.abspath(filepath))
    base = os.path.splitext(os.path.basename(filepath))[0]

    for ext in (".lrc", ".LRC"):
        lrc_path = os.path.join(directory, base + ext)
        if os.path.isfile(lrc_path):
            try:
                with open(lrc_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                timed = parse_lrc_content(content)
                if timed:
                    return {"timed": True, "lines": timed, "source": "lrc"}
            except Exception as exc:
                print("LRC read:", exc, file=sys.stderr)

    for ext in (".txt", ".TXT"):
        txt_path = os.path.join(directory, base + ext)
        if os.path.isfile(txt_path):
            try:
                with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                plain = [
                    (None, ln.strip())
                    for ln in content.splitlines()
                    if ln.strip()
                ]
                if plain:
                    return {"timed": False, "lines": plain, "source": "txt"}
            except Exception as exc:
                print("TXT lyrics:", exc, file=sys.stderr)

    if MutagenFile is None:
        return None
    try:
        audio = MutagenFile(filepath, easy=False)
        if audio is None:
            return None
        tags = getattr(audio, "tags", None)
        if tags is None:
            return None
        try:
            for key in list(tags.keys()):
                sk = str(key)
                if sk.startswith("USLT") or sk.startswith("©lyr"):
                    frame = tags[key]
                    text = None
                    if hasattr(frame, "text"):
                        text = frame.text
                    elif isinstance(frame, (list, tuple)) and frame:
                        text = frame[0]
                    else:
                        text = str(frame)
                    if text and str(text).strip():
                        plain = [
                            (None, ln.strip())
                            for ln in str(text).splitlines()
                            if ln.strip()
                        ]
                        if plain:
                            return {
                                "timed": False,
                                "lines": plain,
                                "source": "embedded",
                            }
        except Exception:
            pass
        for key in ("LYRICS", "lyrics", "UNSYNCEDLYRICS", "unsyncedlyrics"):
            try:
                if key in tags:
                    val = tags[key]
                    if isinstance(val, (list, tuple)):
                        val = val[0] if val else ""
                    text = str(val).strip()
                    if text:
                        plain = [
                            (None, ln.strip())
                            for ln in text.splitlines()
                            if ln.strip()
                        ]
                        if plain:
                            return {
                                "timed": False,
                                "lines": plain,
                                "source": "embedded",
                            }
            except Exception:
                pass
    except Exception as exc:
        print("embedded lyrics:", exc, file=sys.stderr)
    return None


def get_cdda_track_number(path):
    if not is_cdda(path):
        return None
    try:
        return int(path.split("://", 1)[1].split("#")[0])
    except Exception:
        return None


def get_tag(tags, names, default=""):

    if not tags:
        return default

    for name in names:

        try:
            value = None
            # ID3 : getall est plus fiable que __contains__ sur certaines builds
            if hasattr(tags, "getall"):
                try:
                    frames = tags.getall(name)
                    if frames:
                        value = frames[0]
                except Exception:
                    value = None
            if value is None:
                try:
                    # dict-like (Vorbis, MP4, EasyID3, …)
                    if hasattr(tags, "get"):
                        value = tags.get(name)
                    if value is None and name in tags:
                        value = tags[name]
                except Exception:
                    value = None
            if value is None:
                continue

            if isinstance(value, (list, tuple)):
                if not value:
                    continue
                value = value[0]

            if hasattr(value, "text"):
                value = value.text
                if isinstance(value, (list, tuple)):
                    value = value[0] if value else ""

            if value is not None and not isinstance(value, (str, bytes, int, float)):
                for attr in ("year", "text"):
                    if hasattr(value, attr):
                        try:
                            cand = getattr(value, attr)
                            if isinstance(cand, (list, tuple)):
                                cand = cand[0] if cand else ""
                            if cand is not None and str(cand).strip():
                                value = cand
                                break
                        except Exception:
                            pass

            if value is not None:
                s = str(value).strip()
                if s:
                    return s

        except Exception:
            pass

    return default


def load_external_cover(filename):

    directory = os.path.dirname(filename)

    cover_names = {
        "cover.jpg",
        "cover.jpeg",
        "cover.png",
        "cover.webp",
        "folder.jpg",
        "folder.jpeg",
        "folder.png",
        "folder.webp",
        "front.jpg",
        "front.jpeg",
        "front.png",
        "front.webp",
        "albumart.jpg",
        "albumart.jpeg",
        "albumart.png",
        "album.jpg",
        "album.jpeg",
        "album.png",
        "albumartsmall.jpg",
        "embedded.png",
        "thumb.jpg",
    }

    try:

        for name in os.listdir(directory):

            if name.lower() not in cover_names:
                continue

            path = os.path.join(directory, name)

            if not os.path.isfile(path):
                continue

            try:

                with open(path, "rb") as file:
                    data = file.read()

                if data:
                    return data

            except Exception:
                pass

    except Exception:
        pass

    return None


def load_metadata(filename, load_cover=True):

    if is_cdda(filename):
        track = get_cdda_track_number(filename) or 0
        title = f"Piste {track:02d}"
        return (
            title,
            "CD Audio",
            "CD Audio",
            "",
            0,
            None,
            {
                "format": "CDDA",
                "sample_rate": "44 100 Hz",
                "channels": "2",
                "bitrate": "",
                "bits": "16 bits",
            },
        )

    if is_stream(filename):
        title = filename
        if len(title) > 80:
            title = title[:77] + "…"
        return (
            title,
            "Flux en direct",
            "Streaming",
            "",
            0,
            None,
            {
                "format": "STREAM",
                "sample_rate": "",
                "channels": "",
                "bitrate": "",
                "bits": "",
            },
        )

    title = os.path.splitext(
        os.path.basename(filename)
    )[0]

    artist = ""
    album = ""
    year = ""
    duration = 0
    cover = None

    technical = {
        "format": os.path.splitext(filename)[1].upper().lstrip("."),
        "sample_rate": "",
        "channels": "",
        "bitrate": "",
        "bits": "",
    }

    if MutagenFile is None:
        if load_cover:
            cover = load_external_cover(filename)
        return (
            title,
            artist,
            album,
            year,
            duration,
            cover,
            technical,
        )

    try:
        # --- Passe 1 : tags « easy » (titre / artiste / album / date) ---
        # Plus uniforme entre MP3/FLAC/OGG/MP4 sous mutagen récents.
        try:
            easy = MutagenFile(filename, easy=True)
        except Exception:
            easy = None
        if easy is not None:
            info = getattr(easy, "info", None)
            if info is not None:
                try:
                    duration = float(getattr(info, "length", 0) or 0)
                except Exception:
                    duration = 0
                rate = getattr(info, "sample_rate", None)
                if rate:
                    try:
                        technical["sample_rate"] = (
                            f"{int(rate):,} Hz".replace(",", " ")
                        )
                    except Exception:
                        technical["sample_rate"] = f"{rate} Hz"
                channels = getattr(info, "channels", None)
                if channels:
                    technical["channels"] = str(channels)
                bitrate = getattr(info, "bitrate", None)
                if bitrate:
                    try:
                        technical["bitrate"] = f"{int(bitrate / 1000)} kb/s"
                    except Exception:
                        pass
                bits = getattr(info, "bits_per_sample", None)
                if bits:
                    technical["bits"] = f"{bits} bits"

            def _easy_first(keys):
                for k in keys:
                    try:
                        val = easy.get(k)
                        if not val:
                            continue
                        if isinstance(val, (list, tuple)):
                            val = val[0] if val else ""
                        s = str(val).strip()
                        if s:
                            return s
                    except Exception:
                        continue
                return ""

            title = _easy_first(("title", "TITLE", "TIT2")) or title
            artist = _easy_first(("artist", "ARTIST", "TPE1")) or artist
            album = _easy_first(("album", "ALBUM", "TALB")) or album
            year = _easy_first(
                ("date", "DATE", "year", "YEAR", "TDRC", "TYER")
            ) or year

        # --- Passe 2 : tags bruts (covers + compléments ID3/MP4) ---
        audio = MutagenFile(filename, easy=False)
        if audio is None:
            if load_cover and cover is None:
                cover = load_external_cover(filename)
            if year:
                y = str(year).strip()
                if len(y) >= 4 and y[:4].isdigit():
                    year = y[:4]
            return (
                title,
                artist,
                album,
                year,
                duration,
                cover,
                technical,
            )

        info = getattr(audio, "info", None)
        if info is not None and not duration:
            try:
                duration = float(getattr(info, "length", 0) or 0)
            except Exception:
                pass
            if not technical.get("sample_rate"):
                rate = getattr(info, "sample_rate", None)
                if rate:
                    try:
                        technical["sample_rate"] = (
                            f"{int(rate):,} Hz".replace(",", " ")
                        )
                    except Exception:
                        pass
            if not technical.get("channels"):
                channels = getattr(info, "channels", None)
                if channels:
                    technical["channels"] = str(channels)
            if not technical.get("bitrate"):
                bitrate = getattr(info, "bitrate", None)
                if bitrate:
                    try:
                        technical["bitrate"] = f"{int(bitrate / 1000)} kb/s"
                    except Exception:
                        pass
            if not technical.get("bits"):
                bits = getattr(info, "bits_per_sample", None)
                if bits:
                    technical["bits"] = f"{bits} bits"

        tags = getattr(audio, "tags", None)

        if tags and (not title or title == os.path.splitext(os.path.basename(filename))[0]):
            title = (
                get_tag(tags, ["TIT2", "title", "TITLE", "\xa9nam"])
                or title
            )
        if tags and not artist:
            artist = get_tag(
                tags, ["TPE1", "artist", "ARTIST", "\xa9ART"]
            )
        if tags and not album:
            album = get_tag(
                tags, ["TALB", "album", "ALBUM", "\xa9alb"]
            )
        if tags and not year:
            year = get_tag(
                tags,
                ["TDRC", "TYER", "date", "DATE", "year", "YEAR", "\xa9day"],
            )

        if load_cover:
            # MP4 / M4A
            try:
                if tags is not None and (
                    (MP4 is not None and isinstance(audio, MP4))
                    or "covr" in tags
                ):
                    cov = tags.get("covr") if hasattr(tags, "get") else None
                    if cov is None and "covr" in tags:
                        cov = tags["covr"]
                    if cov:
                        if isinstance(cov, (list, tuple)):
                            cov = cov[0]
                        cover = bytes(cov)
            except Exception:
                pass

            # ID3 APIC
            if cover is None and tags is not None:
                try:
                    apics = None
                    if hasattr(tags, "getall"):
                        try:
                            apics = tags.getall("APIC")
                        except Exception:
                            apics = None
                    if apics:
                        chosen = None
                        for ap in apics:
                            try:
                                # 3 = Cover (front)
                                if int(getattr(ap, "type", -1)) == 3:
                                    chosen = ap
                                    break
                            except Exception:
                                pass
                        if chosen is None:
                            chosen = apics[0]
                        data = getattr(chosen, "data", None)
                        if data:
                            cover = bytes(data)
                    if cover is None:
                        for key in list(tags.keys()):
                            sk = str(key)
                            if not (
                                sk.startswith("APIC")
                                or "PICTURE" in sk.upper()
                                or sk == "covr"
                            ):
                                continue
                            try:
                                frame = tags[key]
                                if isinstance(frame, (list, tuple)) and frame:
                                    frame = frame[0]
                                data = getattr(frame, "data", None)
                                if data is None and isinstance(
                                    frame, (bytes, bytearray, memoryview)
                                ):
                                    data = bytes(frame)
                                if data:
                                    cover = bytes(data)
                                    break
                            except Exception:
                                pass
                except Exception:
                    pass

            # FLAC / Ogg pictures
            if cover is None:
                try:
                    pics = getattr(audio, "pictures", None)
                    if pics:
                        data = getattr(pics[0], "data", None)
                        if data:
                            cover = bytes(data)
                except Exception:
                    pass

            if cover is None:
                cover = load_external_cover(filename)

        if year:
            try:
                y = str(year).strip()
                if len(y) >= 4 and y[:4].isdigit():
                    year = y[:4]
            except Exception:
                pass

    except Exception as exc:

        print(
            f"Lecture métadonnées impossible : "
            f"{filename}: {exc}",
            file=sys.stderr
        )
        if load_cover and cover is None:
            try:
                cover = load_external_cover(filename)
            except Exception:
                pass

    return (
        title,
        artist,
        album,
        year,
        duration,
        cover,
        technical,
    )


# ============================================================
# ÉCHELLE UI (HiDPI / grands écrans sans scale système)
# ============================================================

def detect_ui_scale():
    """Facteur d'échelle *supplémentaire* pour l'UI applicative.

    GTK multiplie déjà les px CSS par monitor.get_scale_factor().
    On n'ajoute un boost que si le système laisse scale=1 sur un
    écran dense / large (cas fréquent sous LXQt / Lubuntu en 4K),
    afin d'éviter une interface illisible hors de la boîte.
    """
    try:
        display = Gdk.Display.get_default()
        if display is None:
            return 1.0
        monitor = None
        try:
            monitor = display.get_primary_monitor()
        except Exception:
            monitor = None
        if monitor is None:
            try:
                if display.get_n_monitors() > 0:
                    monitor = display.get_monitor(0)
            except Exception:
                pass
        if monitor is None:
            return 1.0

        try:
            sys_scale = float(monitor.get_scale_factor() or 1)
        except Exception:
            sys_scale = 1.0
        sys_scale = max(1.0, sys_scale)

        # Le serveur d'affichage scale déjà → ne pas double-scaler
        if sys_scale >= 1.5:
            return 1.0

        geo = monitor.get_geometry()
        width = int(getattr(geo, "width", 0) or 0)
        height = int(getattr(geo, "height", 0) or 0)

        # DPI physique si le moniteur expose la taille en mm
        dpi = None
        try:
            w_mm = monitor.get_width_mm()
            if w_mm and w_mm > 0 and width > 0:
                dpi = (width * 25.4) / float(w_mm)
        except Exception:
            dpi = None

        boost = 1.0
        if dpi is not None and dpi >= 160:
            # Vrai HiDPI non scalé par le DE
            boost = min(2.0, max(1.25, dpi / 110.0))
        elif width >= 3840 or height >= 2160:
            boost = 1.5
        elif width >= 3000 or height >= 1800:
            boost = 1.35
        elif width >= 2560 or height >= 1440:
            boost = 1.2
        else:
            boost = 1.0

        # Arrondi stable (évite 1.333… flottant partout)
        for candidate in (1.0, 1.15, 1.25, 1.35, 1.5, 1.75, 2.0):
            if abs(boost - candidate) < 0.08:
                return candidate
        return round(boost * 20) / 20.0
    except Exception:
        return 1.0


# ============================================================
# APPLICATION
# ============================================================

class Myostoox(Gtk.Window):

    COL_PATH = 0
    COL_TITLE = 1
    COL_ARTIST = 2
    COL_ALBUM = 3
    COL_DURATION_TEXT = 4
    COL_DURATION = 5
    # --- Mini-lecteur : bannière défilante (dessinée en GTK) ---
    _MINI_MARQUEE_HEIGHT = 20           # hauteur de la bande (px)
    _MINI_MARQUEE_FONT = "Sans Bold 9"
    _MINI_MARQUEE_SPEED = 40.0          # vitesse de défilement (px / seconde)
    _MINI_MARQUEE_GAP = 90              # espace entre deux répétitions (px)
    _MINI_MARQUEE_PAD = 10              # marge gauche (px)
    _MINI_MARQUEE_PAUSE_MS = 0       # pause au début de chaque tour ou 0 pour un stop
    _MINI_MARQUEE_ALWAYS_SCROLL = True  # True : défile toujours, même si le titre est court
 
    def __init__(self):

        super().__init__(title=APP_NAME)

        # Scale UI interne (complète le scale factor GDK si absent)
        self.ui_scale = detect_ui_scale()
        try:
            s = float(self.ui_scale) or 1.0
        except Exception:
            s = 1.0
            self.ui_scale = 1.0
        self._ui_s = lambda n, _s=s: max(1, int(round(float(n) * _s)))

        self.set_default_size(self._ui_s(950), self._ui_s(620))
        self.set_position(
            Gtk.WindowPosition.CENTER
        )
        self.set_size_request(self._ui_s(700), self._ui_s(430))

        self.current_path = None
        self.current_duration = 0

        self.is_playing = False
        self.is_paused = False
        self.repeat_mode = False
        self.repeat_track_path = None
        self.shuffle_mode = False
        self.shuffle_queue = []
        self.language = self.load_saved_language()
        self.user_seeking = False

        self.search_text = ""

        self.sort_column = None
        self.sort_ascending = True

        self.spectrum_values = [0.0] * SPECTRUM_BANDS
        self.spectrum_targets = [0.0] * SPECTRUM_BANDS
        self.spectrum_peaks = [0.0] * SPECTRUM_BANDS
        self.spectrum_quiet = [0] * SPECTRUM_BANDS
        self.spectrum_peak_hold = [0.0] * SPECTRUM_BANDS

        self.metadata_cache = {}
        self._missing_cache = {}
        self.spectrum_enabled = True
        self.spectrum_agc_enabled = SPECTRUM_AGC_ENABLED_DEFAULT
        self.recent_files = []
        try:
            self._load_recent_files()
        except Exception:
            self.recent_files = []
        self.spectrum_agc_ceiling = SPECTRUM_HIGH_DB
        self._visualizer_last_tick = None
        self.eq_window = None
        self.eq_scales = []

        # CD Audio
        self.current_is_cdda = False

        # Radios favoris
        self.radios = []
        self.load_radios()

        # Préréglages égaliseur
        self.eq_presets = []
        self.load_eq_presets()

        # Métadonnées ICY des flux
        self.icy_title = ""
        self.icy_artist = ""
        self.current_radio_name = None
        self.current_tv_name = None

        Gst.init(None)

        self.player = Gst.ElementFactory.make(
            "playbin",
            "player"
        )

        if self.player is None:
            raise RuntimeError(
                "Impossible de créer le lecteur GStreamer."
            )

        self.player.set_property(
            "volume",
            0.75
        )

        # Mode performances : "hardware" (défaut) ou "software".
        # Appliqué au démarrage via les rangs GStreamer ; un changement
        # en cours de session nécessite un redémarrage de l'application.
        self.performance_mode = self._load_performance_mode()
        self._hw_fallback_path = None  # chemin pour lequel le soft a déjà été forcé
        self._apply_decoder_performance_mode()
        # a52dec (gst-plugins-ugly) corrompt le tas sur E-AC3 7.1 → forcer avdec_*
        self._apply_safe_audio_decoder_ranks()

        # Pipeline alternatif MKV local (évite bug playbin + double a52dec)
        self._custom_pipeline = None
        self._need_playbin_rebind = False
        self._custom_volume = None
        self._custom_audio_linked = False
        self._custom_video_linked = False
        self._custom_bus_handler_ids = []
        self._custom_audio_pads = []  # [(Gst.Pad, label), ...]
        self._custom_audio_queue = None
        self._custom_audio_index = 0
        self._custom_subtitle_overlay = None
        self._custom_subtitle_pads = []  # [(demux_pad, label), ...]
        self._custom_subtitle_index = -1
        self._custom_sub_linked = False
        self._custom_sub_muted = False
        self._custom_sub_overlay = None

        # Sink vidéo : glimagesink > ximagesink > xvimagesink (évite bug GLAMOR/Xv en 4K).
        self.video_sink = self._create_video_sink()
        if self.video_sink is not None:
            self.player.set_property("video-sink", self.video_sink)

        # Buffers playbin (dimensionnés pour MKV multi-pistes sans underrun).
        self._configure_playbin_buffers()

        self.is_fullscreen = False
        self.subtitles_enabled = True
        self.current_is_video = False
        # Après Stop sur une vidéo/TV : reprendre en mode vidéo au prochain Play
        self._resume_prefer_video = False
        # Mini-lecteur : défilement du titre dans la barre de titre
        self._mini_title_scroll_id = 0
        self._mini_title_text = ""
        self._mini_title_offset = 0
        # Paroles (lyrics) — zone vidéo, fichiers audio locaux uniquement
        self.lyrics_enabled = False
        self.lyrics_data = None
        self.lyrics_current_index = -1
        self.lyrics_mode = False
        # Notifications, mini-lecteur, inhibition veille
        self.desktop_notifications = True
        self._last_notify_key = None
        self._notify_unavailable = False
        self._notify_service_checked = False
        self.mini_player_mode = True
        self._normal_size = None
        self._screensaver_cookie = None
        self._screensaver_cookie_kind = None
        self._screensaver_proxy = None
        self._video_window_handle = None
        self._pending_restore_path = None
        self._restore_handle_tries = 0
        self._fullscreen_ui_state = None  # pour restaurer l'UI après plein écran
        # Sous-titres : pistes unifiées (intégrées + externes)
        # Chaque entrée : {"kind": "embedded"|"external", "index": int|None,
        #                  "path": str|None, "label": str}
        self.subtitle_tracks = []
        self.current_subtitle_index = -1  # index dans subtitle_tracks, ou -1
        self._preferred_custom_sub_index = -1  # piste à brancher au (re)build custom
        self.external_subtitles = []  # fichiers .srt/.vtt/… détectés pour la vidéo courante
        self._active_external_sub_path = None  # chemin du suburi / fichier externe actif
        self._custom_ext_sub_src = None
        self._custom_ext_sub_q = None
        self._custom_ext_sub_parse = None
        self.audio_tracks = []  # liste de (index, language_or_label) — pistes audio (langues)
        self.current_audio_index = -1
        self._size_allocate_timer = 0
        self._seek_in_progress = False
        self._seek_release_timeout_id = 0
        self._playback_generation = 0
        self._instance_lock_fd = None  # descripteur du verrou fcntl (instance primaire)
        self.instance_socket = None
        # Position fiable : évite les retours à 0 transitoires de query_position (MKV)
        self._last_known_position = 0.0
        self._pending_seek_delta = 0.0
        self._updating_position_scale = False
        self._pause_pixbuf = None  # dernière image figée en pause (anti-fantôme)
        self.tvs = []
        self.load_tvs()

        # Bouquets vidéo (playlists M3U type Freebox TV)
        self.bouquets = []
        self.load_bouquets()
        # True tant que la playlist courante est un bouquet (chaînes = flux vidéo)
        self.bouquet_active = False

        # Associations de formats
        self.associated_formats = set()
        self.load_associations()
        try:
            self._ensure_desktop_file()
        except Exception:
            pass

        self.pitch = Gst.ElementFactory.make("pitch", "pitch")
        self.equalizer = Gst.ElementFactory.make(
            "equalizer-10bands",
            "equalizer"
        )

        self.eq_muted = False
        self.eq_saved_gains = []

        # Volume / muet
        self.is_muted = False
        self._volume_before_mute = 0.75
        # Chapitres MKV / DVD : [(start_seconds, title), ...]
        self.chapters = []
        # OSD (uniquement en plein écran, option mémorisable)
        self._osd_timeout_id = 0
        # Curseur auto-masqué en plein écran
        self._cursor_hide_timeout_id = 0
        self._cursor_hidden = False
        # Thème + raccourcis
        self.theme_name = "aqua"
        self.shortcuts = dict(DEFAULT_SHORTCUTS)
        self._css_provider = None
        self._mpris_owner_id = 0
        self._mpris_reg_ids = []

        self.pitch_value = 1.0
        self.tempo_value = 1.0
        self.pitch_window = None
        self.pitch_scale = None
        self.tempo_scale = None
        self._pitch_active = False
        # Colorimétrie vidéo (globale) — OFF par défaut (perf 4K)
        self.colorimetry_enabled = False
        self.color_brightness = 0.0   # -1 .. 1
        self.color_contrast = 1.0     # 0 .. 2
        self.color_saturation = 1.0   # 0 .. 2
        self.color_gamma = 1.0        # 0.3 .. 3
        self.colorimetry_window = None
        self._video_balance = None
        self._video_gamma = None
        self._video_color_bin = None
        self._custom_vbalance = None
        self._custom_vgamma = None
        self.colorimetry_menu_item = None
        self.colorimetry_adjust_item = None
        self.filter_bin = None

        self.spectrum = Gst.ElementFactory.make(
            "spectrum",
            "spectrum"
        )

        if self.spectrum is not None:
            self.spectrum.set_property("bands", SPECTRUM_SOURCE_BANDS)
            self.spectrum.set_property("interval", 20000000)
            self.spectrum.set_property("threshold", -80)
            self.spectrum.set_property("post-messages", True)
            self.spectrum.set_property("message-magnitude", True)
            self.spectrum.set_property("message-phase", False)

        self.rebuild_audio_filter(force=True)

        self.playlist = Gtk.ListStore(
            str, str, str, str, str, float
        )

        self.playlist.set_sort_func(self.COL_TITLE, self.compare_title, None)
        self.playlist.set_sort_func(self.COL_ARTIST, self.compare_artist, None)
        self.playlist.set_sort_func(self.COL_ALBUM, self.compare_album, None)
        self.playlist.set_sort_func(self.COL_DURATION, self.compare_duration, None)

        self._load_ui_prefs()
        self.build_css()
        self.build_interface()

        self.load_playlist()

        self.setup_drag_and_drop()
        self.setup_keyboard()

        self.bus = self.player.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect("message", self.on_gstreamer_message)
        self.bus.enable_sync_message_emission()
        self.bus.connect("sync-message::element", self.on_sync_message)

        # Bus connecté avant restore_state pour intercepter prepare-window-handle
        self.restore_state()
        try:
            if getattr(self, "mini_player_mode", False):
                GLib.idle_add(self._apply_mini_player_mode)
        except Exception:
            pass

        self.connect("destroy", self.on_destroy)

        self._app_closing = False
        self._main_popup_menu = None
        self._playlist_context_menu = None
        self._position_timeout_id = GLib.timeout_add(100, self.update_position)
        self._visualizer_timeout_id = GLib.timeout_add(16, self.update_visualizer)

        # Intégration bureau : MPRIS2 (touches média, shell, Bluetooth…)
        try:
            self._setup_mpris()
        except Exception as exc:
            print("MPRIS2 indisponible :", exc, file=sys.stderr)

    def load_saved_language(self):

        try:
            if os.path.isfile(STATE_FILE):
                with open(STATE_FILE, "r", encoding="utf-8") as file:
                    state = json.load(file)
                language = state.get("language") if isinstance(state, dict) else None
                if language in LANGUAGES:
                    return language
        except Exception:
            pass
        return "English"

    def tr(self, text):

        if self.language == "Français":
            return text
        return TRANSLATIONS.get(self.language, {}).get(text, text)

    def set_language(self, language):

        if language not in LANGUAGES:
            return
        self.language = language
        self.apply_language()
        self.save_state()

    def apply_language(self):

        self.set_title(APP_NAME)

        if hasattr(self, "add_files_button"):
            self.add_files_button.set_tooltip_text(self.tr("Ajouter des fichiers"))
        if hasattr(self, "add_folder_button"):
            self.add_folder_button.set_tooltip_text(self.tr("Ajouter un dossier"))
        if hasattr(self, "previous_button"):
            self.previous_button.set_tooltip_text(self.tr("Morceau précédent"))
        if hasattr(self, "play_button"):
            self.play_button.set_tooltip_text(self.tr("Lecture / Pause"))
        if hasattr(self, "stop_button"):
            self.stop_button.set_tooltip_text(self.tr("Stop"))
        if hasattr(self, "next_button"):
            self.next_button.set_tooltip_text(self.tr("Morceau suivant"))
        if hasattr(self, "shuffle_button"):
            self.shuffle_button.set_tooltip_text(self.tr("Lecture aléatoire activée" if self.shuffle_mode else "Lecture aléatoire"))
        if hasattr(self, "repeat_button"):
            if self.repeat_track_path:
                self.repeat_button.set_tooltip_text(self.tr("Répétition du morceau sélectionné activée"))
            elif self.repeat_mode:
                self.repeat_button.set_tooltip_text(self.tr("Répétition de la playlist activée"))
            else:
                self.repeat_button.set_tooltip_text(self.tr("Répéter la playlist"))

        if hasattr(self, "bluetooth_button"):
            self.bluetooth_button.set_tooltip_text(
                self.tr("Bluetooth")
                + " — "
                + self.tr("Ouvrir les réglages Bluetooth du système")
            )

        if hasattr(self, "search_entry"):
            self.search_entry.set_placeholder_text(self.tr("Rechercher un titre, un artiste, un album…"))
        if hasattr(self, "bottom_title") and self.current_path is None:
            self.bottom_title.set_text(self.tr("Aucun morceau sélectionné"))
        if hasattr(self, "status_label"):
            self.status_label.set_text(self.tr("Prêt"))

        if hasattr(self, "tree"):
            titles = ["Titre", "Artiste", "Album", "Durée"]
            for column, title in zip(self.tree.get_columns(), titles):
                column.set_title(self.tr(title))

    # ========================================================
    # RADIOS FAVORIS
    # ========================================================

    def load_radios(self):

        self.radios = []
        if not os.path.isfile(RADIOS_FILE):
            return

        try:
            with open(RADIOS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "name" in item and "url" in item:
                        self.radios.append({
                            "name": str(item["name"]).strip(),
                            "url": str(item["url"]).strip()
                        })
        except Exception as exc:
            print("Impossible de charger les radios :", exc, file=sys.stderr)

    def save_radios(self):

        try:
            directory = os.path.dirname(RADIOS_FILE)
            os.makedirs(directory, exist_ok=True)
            temporary = RADIOS_FILE + ".tmp"
            with open(temporary, "w", encoding="utf-8") as f:
                json.dump(self.radios, f, ensure_ascii=False, indent=2)
            os.replace(temporary, RADIOS_FILE)
        except Exception as exc:
            print("Impossible de sauvegarder les radios :", exc, file=sys.stderr)

    def find_radio_name(self, url):
        """Retourne le nom de la radio si l'URL correspond à une favorite."""
        if not url:
            return None
        for radio in self.radios:
            if radio["url"] == url:
                return radio["name"]
        # Comparaison plus souple (sans paramètres de query)
        base = url.split("?")[0]
        for radio in self.radios:
            if radio["url"].split("?")[0] == base:
                return radio["name"]
        return None

    def play_radio(self, url):
        """Joue un flux radio sans l'ajouter à la playlist."""
        if not url:
            return
        self.dismiss_bouquet_playlist()
        # Audio uniquement : annuler tout mode vidéo (reprise après Stop, TV…)
        self._prefer_video = False
        self._resume_prefer_video = False
        self.current_tv_name = None
        self.current_radio_name = self.find_radio_name(url)
        self.play_file(url)
        # Forcer la zone audio / spectre (play_file peut encore croire
        # à une reprise vidéo si on venait d'un MKV / TV).
        try:
            self.lyrics_mode = False
            self.lyrics_data = None
            self.lyrics_current_index = -1
            self.show_video_area(False)
        except Exception:
            pass
        self.status_label.set_text(self.tr("Radio en cours"))


    def manage_radios(self, *args):

        dialog = Gtk.Dialog(
            title=self.tr("Gérer les radios"),
            parent=self,
            flags=Gtk.DialogFlags.MODAL
        )
        dialog.set_default_size(self._ui_s(560), self._ui_s(380))
        dialog.add_button(self.tr("Fermer"), Gtk.ResponseType.CLOSE)

        area = dialog.get_content_area()
        area.set_border_width(12)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        area.pack_start(box, True, True, 0)

        store = Gtk.ListStore(str, str)
        for radio in self.radios:
            store.append([radio["name"], radio["url"]])

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        box.pack_start(scroll, True, True, 0)

        tree = Gtk.TreeView(model=store)
        tree.set_headers_visible(True)

        renderer = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn("Nom", renderer, text=0)
        col.set_expand(True)
        tree.append_column(col)

        renderer = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn("URL", renderer, text=1)
        col.set_expand(True)
        tree.append_column(col)

        scroll.add(tree)

        def _sync_radios_from_store():
            self.radios = [
                {"name": row[0], "url": row[1]} for row in store
            ]
            self.save_radios()

        def on_add(*a):
            name, url = self._radio_edit_dialog()
            if name and url:
                store.append([name, url])
                _sync_radios_from_store()

        def on_edit(*a):
            model, it = tree.get_selection().get_selected()
            if it is None:
                return
            old_name = model[it][0]
            old_url = model[it][1]
            name, url = self._radio_edit_dialog(old_name, old_url)
            if name and url:
                model[it][0] = name
                model[it][1] = url
                _sync_radios_from_store()

        def on_remove(*a):
            model, it = tree.get_selection().get_selected()
            if it is None:
                return
            model.remove(it)
            _sync_radios_from_store()

        def on_move(delta):
            model, it = tree.get_selection().get_selected()
            if it is None:
                return
            path = model.get_path(it)
            idx = path.get_indices()[0]
            new_idx = idx + delta
            n = len(model)
            if new_idx < 0 or new_idx >= n:
                return
            other = model.get_iter(Gtk.TreePath.new_from_indices([new_idx]))
            model.swap(it, other)
            tree.get_selection().select_iter(it)
            tree.scroll_to_cell(model.get_path(it), None, False, 0, 0)
            _sync_radios_from_store()

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.pack_start(btn_row, False, False, 0)

        btn_left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.pack_start(btn_left, False, False, 0)

        btn_up = Gtk.Button(label="▲")
        btn_up.set_tooltip_text(self.tr("Monter"))
        btn_up.connect("clicked", lambda *a: on_move(-1))
        btn_left.pack_start(btn_up, False, False, 0)

        btn_down = Gtk.Button(label="▼")
        btn_down.set_tooltip_text(self.tr("Descendre"))
        btn_down.connect("clicked", lambda *a: on_move(1))
        btn_left.pack_start(btn_down, False, False, 0)

        btn_right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_right.set_halign(Gtk.Align.END)
        btn_row.pack_end(btn_right, False, False, 0)

        btn_add = Gtk.Button(label=self.tr("Ajouter"))
        btn_add.connect("clicked", on_add)
        btn_right.pack_start(btn_add, False, False, 0)

        btn_edit = Gtk.Button(label=self.tr("Modifier"))
        btn_edit.connect("clicked", on_edit)
        btn_right.pack_start(btn_edit, False, False, 0)

        btn_remove = Gtk.Button(label=self.tr("Supprimer"))
        btn_remove.connect("clicked", on_remove)
        btn_right.pack_start(btn_remove, False, False, 0)

        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def _radio_edit_dialog(self, name="", url=""):

        d = Gtk.Dialog(
            title=self.tr("Radio"),
            parent=self,
            flags=Gtk.DialogFlags.MODAL
        )
        d.set_default_size(self._ui_s(420), self._ui_s(140))
        d.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        d.add_button(self.tr("Valider"), Gtk.ResponseType.OK)
        d.set_default_response(Gtk.ResponseType.OK)

        area = d.get_content_area()
        area.set_border_width(12)

        grid = Gtk.Grid()
        grid.set_row_spacing(8)
        grid.set_column_spacing(10)
        area.pack_start(grid, True, True, 0)

        grid.attach(Gtk.Label(label=self.tr("Nom :"), xalign=1), 0, 0, 1, 1)
        entry_name = Gtk.Entry()
        entry_name.set_text(name)
        entry_name.set_hexpand(True)
        grid.attach(entry_name, 1, 0, 1, 1)

        grid.attach(Gtk.Label(label=self.tr("URL :"), xalign=1), 0, 1, 1, 1)
        entry_url = Gtk.Entry()
        entry_url.set_text(url)
        entry_url.set_hexpand(True)
        grid.attach(entry_url, 1, 1, 1, 1)

        d.show_all()
        response = d.run()
        new_name = entry_name.get_text().strip()
        new_url = entry_url.get_text().strip()
        d.destroy()

        if response == Gtk.ResponseType.OK and new_name and new_url:
            return new_name, new_url
        return None, None

    # ========================================================
    # TV FAVORIS
    # ========================================================

    def load_tvs(self):

        self.tvs = []
        if not os.path.isfile(TVS_FILE):
            return

        try:
            with open(TVS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "name" in item and "url" in item:
                        self.tvs.append({
                            "name": str(item["name"]).strip(),
                            "url": str(item["url"]).strip()
                        })
        except Exception as exc:
            print("Impossible de charger les TV :", exc, file=sys.stderr)

    def save_tvs(self):

        try:
            directory = os.path.dirname(TVS_FILE)
            os.makedirs(directory, exist_ok=True)
            temporary = TVS_FILE + ".tmp"
            with open(temporary, "w", encoding="utf-8") as f:
                json.dump(self.tvs, f, ensure_ascii=False, indent=2)
            os.replace(temporary, TVS_FILE)
        except Exception as exc:
            print("Impossible de sauvegarder les TV :", exc, file=sys.stderr)

    def find_tv_name(self, url):
        """Retourne le nom de la chaîne TV si l'URL correspond à une favorite."""
        if not url:
            return None
        for tv in self.tvs:
            if tv["url"] == url:
                return tv["name"]
        base = url.split("?")[0]
        for tv in self.tvs:
            if tv["url"].split("?")[0] == base:
                return tv["name"]
        return None

    def play_tv(self, url, name=None):
        """Joue un flux TV (vidéo) sans l'ajouter à la playlist."""
        if not url:
            return
        self.dismiss_bouquet_playlist()
        self._prefer_video = True
        # Nom affiché (menu TV) plutôt que l'URL du flux
        self.current_tv_name = (name or "").strip() or self.find_tv_name(url)
        self.current_radio_name = None
        # Afficher la zone vidéo d'abord pour obtenir un XID valide
        # (sinon prepare-window-handle arrive trop tôt → fenêtre externe).
        try:
            self.show_video_area(True)
        except Exception:
            pass
        self.play_file(url)
        self.status_label.set_text(self.tr("TV en cours"))


    def manage_tvs(self, *args):

        dialog = Gtk.Dialog(
            title=self.tr("Gérer la TV"),
            parent=self,
            flags=Gtk.DialogFlags.MODAL
        )
        dialog.set_default_size(self._ui_s(560), self._ui_s(380))
        dialog.add_button(self.tr("Fermer"), Gtk.ResponseType.CLOSE)

        area = dialog.get_content_area()
        area.set_border_width(12)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        area.pack_start(box, True, True, 0)

        store = Gtk.ListStore(str, str)
        for tv in self.tvs:
            store.append([tv["name"], tv["url"]])

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        box.pack_start(scroll, True, True, 0)

        tree = Gtk.TreeView(model=store)
        tree.set_headers_visible(True)

        renderer = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn("Nom", renderer, text=0)
        col.set_expand(True)
        tree.append_column(col)

        renderer = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn("URL", renderer, text=1)
        col.set_expand(True)
        tree.append_column(col)

        scroll.add(tree)

        def _sync_tvs_from_store():
            self.tvs = [
                {"name": row[0], "url": row[1]} for row in store
            ]
            self.save_tvs()

        def on_add(*a):
            name, url = self._tv_edit_dialog()
            if name and url:
                store.append([name, url])
                _sync_tvs_from_store()

        def on_edit(*a):
            model, it = tree.get_selection().get_selected()
            if it is None:
                return
            old_name = model[it][0]
            old_url = model[it][1]
            name, url = self._tv_edit_dialog(old_name, old_url)
            if name and url:
                model[it][0] = name
                model[it][1] = url
                _sync_tvs_from_store()

        def on_remove(*a):
            model, it = tree.get_selection().get_selected()
            if it is None:
                return
            model.remove(it)
            _sync_tvs_from_store()

        def on_move(delta):
            model, it = tree.get_selection().get_selected()
            if it is None:
                return
            path = model.get_path(it)
            idx = path.get_indices()[0]
            new_idx = idx + delta
            n = len(model)
            if new_idx < 0 or new_idx >= n:
                return
            other = model.get_iter(Gtk.TreePath.new_from_indices([new_idx]))
            model.swap(it, other)
            tree.get_selection().select_iter(it)
            tree.scroll_to_cell(model.get_path(it), None, False, 0, 0)
            _sync_tvs_from_store()

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.pack_start(btn_row, False, False, 0)

        btn_left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.pack_start(btn_left, False, False, 0)

        btn_up = Gtk.Button(label="▲")
        btn_up.set_tooltip_text(self.tr("Monter"))
        btn_up.connect("clicked", lambda *a: on_move(-1))
        btn_left.pack_start(btn_up, False, False, 0)

        btn_down = Gtk.Button(label="▼")
        btn_down.set_tooltip_text(self.tr("Descendre"))
        btn_down.connect("clicked", lambda *a: on_move(1))
        btn_left.pack_start(btn_down, False, False, 0)

        btn_right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_right.set_halign(Gtk.Align.END)
        btn_row.pack_end(btn_right, False, False, 0)

        btn_add = Gtk.Button(label=self.tr("Ajouter"))
        btn_add.connect("clicked", on_add)
        btn_right.pack_start(btn_add, False, False, 0)

        btn_edit = Gtk.Button(label=self.tr("Modifier"))
        btn_edit.connect("clicked", on_edit)
        btn_right.pack_start(btn_edit, False, False, 0)

        btn_remove = Gtk.Button(label=self.tr("Supprimer"))
        btn_remove.connect("clicked", on_remove)
        btn_right.pack_start(btn_remove, False, False, 0)

        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def _tv_edit_dialog(self, name="", url=""):

        d = Gtk.Dialog(
            title=self.tr("TV"),
            parent=self,
            flags=Gtk.DialogFlags.MODAL
        )
        d.set_default_size(self._ui_s(420), self._ui_s(140))
        d.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        d.add_button(self.tr("Valider"), Gtk.ResponseType.OK)
        d.set_default_response(Gtk.ResponseType.OK)

        area = d.get_content_area()
        area.set_border_width(12)

        grid = Gtk.Grid()
        grid.set_row_spacing(8)
        grid.set_column_spacing(10)
        area.pack_start(grid, True, True, 0)

        grid.attach(Gtk.Label(label=self.tr("Nom :"), xalign=1), 0, 0, 1, 1)
        entry_name = Gtk.Entry()
        entry_name.set_text(name)
        entry_name.set_hexpand(True)
        grid.attach(entry_name, 1, 0, 1, 1)

        grid.attach(Gtk.Label(label=self.tr("URL :"), xalign=1), 0, 1, 1, 1)
        entry_url = Gtk.Entry()
        entry_url.set_text(url)
        entry_url.set_hexpand(True)
        grid.attach(entry_url, 1, 1, 1, 1)

        d.show_all()
        response = d.run()
        new_name = entry_name.get_text().strip()
        new_url = entry_url.get_text().strip()
        d.destroy()

        if response == Gtk.ResponseType.OK and new_name and new_url:
            return new_name, new_url
        return None, None

    # ========================================================
    # BOUQUETS VIDÉO (playlists M3U)
    # ========================================================

    def load_bouquets(self):

        self.bouquets = []
        if not os.path.isfile(BOUQUETS_FILE):
            return

        try:
            with open(BOUQUETS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "name" in item and "url" in item:
                        self.bouquets.append({
                            "name": str(item["name"]).strip(),
                            "url": str(item["url"]).strip(),
                        })
        except Exception as exc:
            print("Impossible de charger les bouquets :", exc, file=sys.stderr)

    def save_bouquets(self):

        try:
            directory = os.path.dirname(BOUQUETS_FILE)
            os.makedirs(directory, exist_ok=True)
            temporary = BOUQUETS_FILE + ".tmp"
            with open(temporary, "w", encoding="utf-8") as f:
                json.dump(self.bouquets, f, ensure_ascii=False, indent=2)
            os.replace(temporary, BOUQUETS_FILE)
        except Exception as exc:
            print("Impossible de sauvegarder les bouquets :", exc, file=sys.stderr)

    def manage_bouquets(self, *args):

        dialog = Gtk.Dialog(
            title=self.tr("Gérer les bouquets"),
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
        )
        dialog.set_default_size(self._ui_s(560), self._ui_s(380))
        dialog.add_button(self.tr("Fermer"), Gtk.ResponseType.CLOSE)

        area = dialog.get_content_area()
        area.set_border_width(12)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        area.pack_start(box, True, True, 0)

        store = Gtk.ListStore(str, str)
        for bouquet in self.bouquets:
            store.append([bouquet["name"], bouquet["url"]])

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        box.pack_start(scroll, True, True, 0)

        tree = Gtk.TreeView(model=store)
        tree.set_headers_visible(True)

        renderer = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn("Nom", renderer, text=0)
        col.set_expand(True)
        tree.append_column(col)

        renderer = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn("URL", renderer, text=1)
        col.set_expand(True)
        tree.append_column(col)

        scroll.add(tree)

        def _sync_bouquets_from_store():
            self.bouquets = [
                {"name": row[0], "url": row[1]} for row in store
            ]
            self.save_bouquets()

        def on_add(*a):
            name, url = self._bouquet_edit_dialog()
            if name and url:
                store.append([name, url])
                _sync_bouquets_from_store()

        def on_edit(*a):
            model, it = tree.get_selection().get_selected()
            if it is None:
                return
            old_name = model[it][0]
            old_url = model[it][1]
            name, url = self._bouquet_edit_dialog(old_name, old_url)
            if name and url:
                model[it][0] = name
                model[it][1] = url
                _sync_bouquets_from_store()

        def on_remove(*a):
            model, it = tree.get_selection().get_selected()
            if it is None:
                return
            model.remove(it)
            _sync_bouquets_from_store()

        def on_move(delta):
            model, it = tree.get_selection().get_selected()
            if it is None:
                return
            path = model.get_path(it)
            idx = path.get_indices()[0]
            new_idx = idx + delta
            n = len(model)
            if new_idx < 0 or new_idx >= n:
                return
            other = model.get_iter(Gtk.TreePath.new_from_indices([new_idx]))
            model.swap(it, other)
            tree.get_selection().select_iter(it)
            tree.scroll_to_cell(model.get_path(it), None, False, 0, 0)
            _sync_bouquets_from_store()

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.pack_start(btn_row, False, False, 0)

        btn_left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.pack_start(btn_left, False, False, 0)

        btn_up = Gtk.Button(label="▲")
        btn_up.set_tooltip_text(self.tr("Monter"))
        btn_up.connect("clicked", lambda *a: on_move(-1))
        btn_left.pack_start(btn_up, False, False, 0)

        btn_down = Gtk.Button(label="▼")
        btn_down.set_tooltip_text(self.tr("Descendre"))
        btn_down.connect("clicked", lambda *a: on_move(1))
        btn_left.pack_start(btn_down, False, False, 0)

        btn_right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_right.set_halign(Gtk.Align.END)
        btn_row.pack_end(btn_right, False, False, 0)

        btn_add = Gtk.Button(label=self.tr("Ajouter"))
        btn_add.connect("clicked", on_add)
        btn_right.pack_start(btn_add, False, False, 0)

        btn_edit = Gtk.Button(label=self.tr("Modifier"))
        btn_edit.connect("clicked", on_edit)
        btn_right.pack_start(btn_edit, False, False, 0)

        btn_remove = Gtk.Button(label=self.tr("Supprimer"))
        btn_remove.connect("clicked", on_remove)
        btn_right.pack_start(btn_remove, False, False, 0)

        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def _bouquet_edit_dialog(self, name="", url=""):

        d = Gtk.Dialog(
            title=self.tr("Bouquet"),
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
        )
        d.set_default_size(self._ui_s(480), self._ui_s(160))
        d.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        d.add_button(self.tr("Valider"), Gtk.ResponseType.OK)
        d.set_default_response(Gtk.ResponseType.OK)

        area = d.get_content_area()
        area.set_border_width(12)

        grid = Gtk.Grid()
        grid.set_row_spacing(8)
        grid.set_column_spacing(10)
        area.pack_start(grid, True, True, 0)

        grid.attach(Gtk.Label(label=self.tr("Nom :"), xalign=1), 0, 0, 1, 1)
        entry_name = Gtk.Entry()
        entry_name.set_text(name)
        entry_name.set_hexpand(True)
        grid.attach(entry_name, 1, 0, 1, 1)

        grid.attach(
            Gtk.Label(label=self.tr("URL de la playlist (m3u)") + " :", xalign=1),
            0, 1, 1, 1,
        )
        entry_url = Gtk.Entry()
        entry_url.set_text(url)
        entry_url.set_hexpand(True)
        entry_url.set_placeholder_text(
            "http://mafreebox.freebox.fr/freeboxtv/playlist.m3u"
        )
        grid.attach(entry_url, 1, 1, 1, 1)

        d.show_all()
        response = d.run()
        new_name = entry_name.get_text().strip()
        new_url = entry_url.get_text().strip()
        d.destroy()

        if response == Gtk.ResponseType.OK and new_name and new_url:
            return new_name, new_url
        return None, None

    @staticmethod
    def parse_m3u_playlist(text):
        """Parse une playlist M3U / M3U8 et retourne [(name, url), ...]."""
        channels = []
        if not text:
            return channels

        pending_name = None
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#EXTM3U"):
                continue
            if line.startswith("#EXTINF"):
                # #EXTINF:-1 tvg-id="..." ,Nom de la chaîne
                name = ""
                if "," in line:
                    name = line.split(",", 1)[1].strip()
                pending_name = name or None
                continue
            if line.startswith("#"):
                continue

            url = line
            if not (
                url.startswith("http://")
                or url.startswith("https://")
                or url.startswith("rtsp://")
                or url.startswith("mms://")
                or url.startswith("rtp://")
            ):
                # Chemin relatif éventuel : on ignore si non utilisable
                if "://" not in url:
                    pending_name = None
                    continue

            title = pending_name or url
            if len(title) > 120:
                title = title[:117] + "…"
            channels.append((title, url))
            pending_name = None

        return channels

    def dismiss_bouquet_playlist(self):
        """Si un bouquet vidéo occupe la playlist, la vide avant un autre contenu.

        Appelé dès qu'on ouvre un fichier, une radio, une TV, un CD/DVD, etc.
        """
        if not getattr(self, "bouquet_active", False):
            return

        self.bouquet_active = False
        try:
            self.stop()
        except Exception:
            pass
        self.current_path = None
        self.playlist.clear()
        self.metadata_cache.clear()
        try:
            self.bottom_title.set_text(self.tr("Aucun morceau sélectionné"))
            self.bottom_artist.set_text("")
            self.bottom_album.set_text("")
            self.set_default_cover()
        except Exception:
            pass
        try:
            self.load_playlist()
        except Exception:
            pass

    def load_bouquet(self, m3u_url):
        """Vide la playlist et la remplace par les chaînes du bouquet M3U (async)."""
        if not m3u_url:
            return

        self.status_label.set_text(self.tr("Chargement du bouquet…"))

        def worker():
            try:
                from urllib.request import Request, urlopen
                request = Request(
                    m3u_url,
                    headers={"User-Agent": "Myostoox/1.0"},
                )
                with urlopen(request, timeout=20) as response:
                    raw = response.read()
                GLib.idle_add(self._bouquet_loaded, raw, None)
            except Exception as e:
                GLib.idle_add(self._bouquet_loaded, None, e)

        threading.Thread(target=worker, daemon=True).start()

    def _bouquet_loaded(self, raw, err):
        """Fin du chargement bouquet (thread principal GTK)."""
        if err is not None:
            dialog = Gtk.MessageDialog(
                parent=self,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text=self.tr("Impossible de charger le bouquet"),
            )
            dialog.format_secondary_text(str(err))
            dialog.run()
            dialog.destroy()
            self.status_label.set_text(self.tr("Prêt"))
            return False

        text = None
        for encoding in ("utf-8", "latin-1", "cp1252"):
            try:
                text = raw.decode(encoding)
                break
            except Exception:
                continue
        if text is None:
            text = raw.decode("utf-8", errors="replace")

        channels = self.parse_m3u_playlist(text)
        if not channels:
            dialog = Gtk.MessageDialog(
                parent=self,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text=self.tr("Aucune chaîne trouvée dans la playlist"),
            )
            dialog.run()
            dialog.destroy()
            self.status_label.set_text(self.tr("Prêt"))
            return False

        self.stop()
        self.current_path = None
        self.playlist.clear()
        self.metadata_cache.clear()
        self.bottom_title.set_text(self.tr("Aucun morceau sélectionné"))
        self.bottom_artist.set_text("")
        self.bottom_album.set_text("")
        self.set_default_cover()

        first_url = None
        for title, url in channels:
            self.playlist.append([
                url,
                title,
                self.tr("Bouquet vidéos"),
                "",
                "",
                0.0,
            ])
            if first_url is None:
                first_url = url

        self.bouquet_active = True
        count = len(channels)
        self.status_label.set_text(
            self.tr("Bouquet chargé") + f" — {count}" + self.tr(" chaîne(s) chargée(s)")
        )

        if first_url:
            self._prefer_video = True
            self.play_file(first_url)
        return False


    # ==========================
    # ASSOCIATIONS DE FORMATS
    # ==========================

    def load_associations(self):
        self.associated_formats = set()
        if not os.path.isfile(ASSOCIATIONS_FILE):
            return
        try:
            with open(ASSOCIATIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for ext in data:
                    if isinstance(ext, str) and ext.startswith("."):
                        self.associated_formats.add(ext.lower())
        except Exception as exc:
            print("Impossible de charger les associations :", exc, file=sys.stderr)

    def save_associations(self):
        try:
            directory = os.path.dirname(ASSOCIATIONS_FILE)
            os.makedirs(directory, exist_ok=True)
            temporary = ASSOCIATIONS_FILE + ".tmp"
            with open(temporary, "w", encoding="utf-8") as f:
                json.dump(sorted(self.associated_formats), f, ensure_ascii=False, indent=2)
            os.replace(temporary, ASSOCIATIONS_FILE)
        except Exception as exc:
            print("Impossible de sauvegarder les associations :", exc, file=sys.stderr)

    def _script_path(self):
        return os.path.abspath(__file__)

    def _ensure_desktop_file(self):
        """Crée ou met à jour le fichier .desktop pour les associations MIME."""
        try:
            apps_dir = os.path.dirname(DESKTOP_FILE_PATH)
            os.makedirs(apps_dir, exist_ok=True)

            mime_types = []
            for ext, mimes in ASSOCIABLE_FORMATS:
                if ext in self.associated_formats:
                    for m in mimes:
                        if m not in mime_types:
                            mime_types.append(m)

            # %F = liste de fichiers locaux (tous les formats sélectionnés
            # dans une seule invocation). Chemins quotés pour les espaces.
            script = self._script_path().replace('"', '\\"')
            interpreter = sys.executable.replace('"', '\\"')
            exec_line = f'"{interpreter}" "{script}" %F'
            content = (
                "[Desktop Entry]\n"
                "Type=Application\n"
                f"Name={APP_NAME}\n"
                "Comment=Lecteur audio et vidéo\n"
                f"Exec={exec_line}\n"
                "Icon=myostoox\n"
                "Terminal=false\n"
                "Categories=AudioVideo;Player;Audio;Video;\n"
                f"MimeType={';'.join(mime_types)}\n"
                "StartupNotify=true\n"
                "StartupWMClass=Myostoox\n"
            )
            with open(DESKTOP_FILE_PATH, "w", encoding="utf-8") as f:
                f.write(content)
            try:
                os.chmod(DESKTOP_FILE_PATH, 0o755)
            except Exception:
                pass
            # Met à jour la base des applications
            try:
                subprocess.run(
                    ["update-desktop-database", apps_dir],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except Exception:
                pass
            return True
        except Exception as exc:
            print("Impossible d'écrire le fichier .desktop :", exc, file=sys.stderr)
            return False

    def _xdg_mime_default(self, mime_type, enable=True):
        """Définit (ou tente de retirer) Myostoox comme application par défaut pour un MIME."""
        try:
            if enable:
                subprocess.run(
                    ["xdg-mime", "default", DESKTOP_FILE_NAME, mime_type],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            # Désactiver : on ne peut pas vraiment « dé-associer » via xdg-mime
            # sans connaître l'ancien défaut ; le .desktop mis à jour suffit.
        except Exception as exc:
            print(f"xdg-mime ({mime_type}) :", exc, file=sys.stderr)

    def is_format_associated(self, ext):
        return ext.lower() in self.associated_formats

    def set_format_association(self, ext, enable):
        ext = ext.lower()
        if enable:
            self.associated_formats.add(ext)
        else:
            self.associated_formats.discard(ext)

        self.save_associations()
        self._ensure_desktop_file()

        for format_ext, mimes in ASSOCIABLE_FORMATS:
            if format_ext == ext:
                if enable:
                    for mime in mimes:
                        self._xdg_mime_default(mime, enable=True)
                break

        if enable:
            self.status_label.set_text(
                self.tr("Association activée") + f" ({ext})"
            )
        else:
            self.status_label.set_text(
                self.tr("Association désactivée") + f" ({ext})"
            )

    def toggle_format_association(self, menuitem, ext):
        # Évite les bascules lors de la construction du menu
        if not getattr(self, "_building_assoc_menu", False):
            self.set_format_association(ext, menuitem.get_active())

    def associate_all_formats(self, *args):
        all_associated = all(
            self.is_format_associated(ext)
            for ext, _mimes in ASSOCIABLE_FORMATS
        )

        new_state = not all_associated

        for ext, _mimes in ASSOCIABLE_FORMATS:
            self.set_format_association(ext, new_state)

        if new_state:
            self.status_label.set_text(
                self.tr("Association activée") + " (tous)"
            )
        else:
            self.status_label.set_text(
                self.tr("Association désactivée") + " (tous)"
            )

    # ========================================================
    # PRÉRÉGLAGES ÉGALISEUR
    # ========================================================

    def load_eq_presets(self):

        self.eq_presets = []
        if not os.path.isfile(EQ_PRESETS_FILE):
            return

        try:
            with open(EQ_PRESETS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "name" in item and "gains" in item:
                        name = str(item["name"]).strip()
                        gains = item["gains"]
                        if name and isinstance(gains, list) and len(gains) == EQ_BANDS:
                            try:
                                gains = [float(g) for g in gains]
                                self.eq_presets.append({"name": name, "gains": gains})
                            except Exception:
                                pass
        except Exception as exc:
            print("Impossible de charger les préréglages d'égaliseur :", exc, file=sys.stderr)

    def save_eq_presets(self):

        try:
            directory = os.path.dirname(EQ_PRESETS_FILE)
            os.makedirs(directory, exist_ok=True)
            temporary = EQ_PRESETS_FILE + ".tmp"
            with open(temporary, "w", encoding="utf-8") as f:
                json.dump(self.eq_presets, f, ensure_ascii=False, indent=2)
            os.replace(temporary, EQ_PRESETS_FILE)
        except Exception as exc:
            print("Impossible de sauvegarder les préréglages d'égaliseur :", exc, file=sys.stderr)

    # ========================================================
    # ========================================================
    # CACHE MÉTADONNÉES
    # ========================================================

    def get_metadata(self, filename, load_cover=False):

        if not load_cover and filename in self.metadata_cache:

            data = self.metadata_cache[filename]

            return (
                data["title"],
                data["artist"],
                data["album"],
                data["year"],
                data["duration"],
                None,
                data["technical"],
            )

        title, artist, album, year, duration, cover, technical = load_metadata(
            filename,
            load_cover=load_cover
        )

        self.metadata_cache[filename] = {
            "title": title,
            "artist": artist,
            "album": album,
            "year": year,
            "duration": duration,
            "technical": technical,
        }

        if len(self.metadata_cache) > 100:
            premiere_cle = next(iter(self.metadata_cache))
            del self.metadata_cache[premiere_cle]

        return (
            title,
            artist,
            album,
            year,
            duration,
            cover,
            technical,
        )


    # ========================================================
    # TRI DE LA PLAYLIST
    # ========================================================

    @staticmethod
    def normalize_sort_text(value):

        if value is None:
            return ""

        value = str(value).strip()
        value = unicodedata.normalize("NFKD", value)
        value = "".join(
            char for char in value
            if not unicodedata.combining(char)
        )
        return value.casefold()

    def compare_text_values(self, value1, value2):

        value1 = self.normalize_sort_text(value1)
        value2 = self.normalize_sort_text(value2)

        if value1 < value2:
            return -1
        if value1 > value2:
            return 1
        return 0

    def compare_title(self, model, iterator1, iterator2, data):
        return self.compare_text_values(
            model[iterator1][self.COL_TITLE],
            model[iterator2][self.COL_TITLE]
        )

    def compare_artist(self, model, iterator1, iterator2, data):
        return self.compare_text_values(
            model[iterator1][self.COL_ARTIST],
            model[iterator2][self.COL_ARTIST]
        )

    def compare_album(self, model, iterator1, iterator2, data):
        return self.compare_text_values(
            model[iterator1][self.COL_ALBUM],
            model[iterator2][self.COL_ALBUM]
        )

    def compare_duration(self, model, iterator1, iterator2, data):

        duration1 = float(model[iterator1][self.COL_DURATION] or 0)
        duration2 = float(model[iterator2][self.COL_DURATION] or 0)

        if duration1 < duration2:
            return -1
        if duration1 > duration2:
            return 1
        return 0

    def on_column_clicked(self, column):

        column_id = column.get_sort_column_id()

        if column_id == self.sort_column:
            self.sort_ascending = not self.sort_ascending
        else:
            self.sort_column = column_id
            self.sort_ascending = True

        order = (
            Gtk.SortType.ASCENDING
            if self.sort_ascending
            else Gtk.SortType.DESCENDING
        )

        self.playlist.set_sort_column_id(column_id, order)

        for current_column in self.tree.get_columns():
            current_column.set_sort_indicator(False)

        column.set_sort_indicator(True)
        column.set_sort_order(order)
        self.playlist_filter.refilter()

    # ========================================================
    # INSTANCE UNIQUE
    # ========================================================

    def start_single_instance_server(self):
        """Bind le socket Unix d'instance unique (l'appelant détient déjà le verrou).

        Nettoie un éventuel socket orphelin, puis écoute les connexions
        des invocations secondaires (double-clic depuis le gestionnaire
        de fichiers).
        """
        self.instance_socket = None

        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
        except Exception:
            pass

        # Socket orphelin (crash précédent) : le supprimer pour pouvoir binder
        if os.path.exists(SOCKET_FILE):
            try:
                os.unlink(SOCKET_FILE)
            except Exception:
                pass

        try:
            self.instance_socket = socket.socket(
                socket.AF_UNIX,
                socket.SOCK_STREAM
            )
            # Empêche d'autres utilisateurs d'écrire sur le socket
            old_mask = os.umask(0o077)
            try:
                self.instance_socket.bind(SOCKET_FILE)
            finally:
                os.umask(old_mask)
            self.instance_socket.listen(16)

            thread = threading.Thread(
                target=self.instance_server_loop,
                daemon=True,
                name="myostoox-instance-server",
            )
            thread.start()
            return True
        except Exception as exc:
            print("Serveur d'instance unique indisponible :", exc, file=sys.stderr)
            self.instance_socket = None
            return False

    def instance_server_loop(self):
        """Boucle d'écoute résiliente : une erreur de connexion ne tue pas le serveur."""

        while True:
            sock = getattr(self, "instance_socket", None)
            if sock is None:
                break

            try:
                connection, _ = sock.accept()
            except OSError:
                # Socket fermé à la destruction de l'application
                break
            except Exception as exc:
                print("instance_server accept:", exc, file=sys.stderr)
                continue

            try:
                chunks = []
                connection.settimeout(5.0)
                while True:
                    try:
                        data = connection.recv(1024 * 256)
                    except socket.timeout:
                        break
                    except Exception:
                        break
                    if not data:
                        break
                    chunks.append(data)
                    # Garde-fou : payloads excessifs
                    if sum(len(c) for c in chunks) > 8 * 1024 * 1024:
                        break

                payload = b"".join(chunks).decode("utf-8", errors="ignore")
                files = [
                    line.strip()
                    for line in payload.splitlines()
                    if line.strip()
                ]

                # Toujours remonter à l'UI (même sans fichier : simple activation)
                GLib.idle_add(self.receive_external_files, files)

            except Exception as exc:
                print("instance_server handle:", exc, file=sys.stderr)
            finally:
                try:
                    connection.close()
                except Exception:
                    pass

    def receive_external_files(self, files):
        """Reçoit des fichiers depuis une autre invocation (gestionnaire de fichiers).

        - 1 fichier  → ajout + lecture automatique
        - N fichiers → ajout uniquement, sans démarrer la lecture
        """
        try:
            # Remonter la fenêtre au premier plan
            self.present()
            try:
                self.set_keep_above(True)
                self.set_keep_above(False)
            except Exception:
                pass
            self.present()
        except Exception:
            pass

        if not files:
            return False

        # Ouverture explicite : ne pas laisser une restauration de session
        # en attente reprendre l'ancien média.
        try:
            self.cancel_pending_session_restore()
        except Exception:
            pass

        added = False
        valid_paths = []
        for filename in files:
            if not isinstance(filename, str):
                continue
            # Accepte fichiers locaux, flux et CD
            if (
                is_audio_file(filename)
                or is_video_file(filename)
                or is_stream(filename)
                or is_cdda(filename)
            ):
                self.add_file(filename, save=False)
                valid_paths.append(filename)
                added = True

        if added:
            self.save_playlist()

        # Lecture auto uniquement pour un seul fichier transmis
        if len(valid_paths) == 1:
            self.play_file(valid_paths[0])

        return False

    # ========================================================
    # PLAYLIST PERSISTANTE
    # ========================================================

    def save_playlist(self):
        # Ne jamais écraser la vraie playlist par un bouquet éphémère.
        if getattr(self, "bouquet_active", False):
            return
        try:
            atomic_write_json(PLAYLIST_FILE, self.get_paths())
        except Exception as exc:
            print("Impossible de sauvegarder la playlist :", exc, file=sys.stderr)

    def load_playlist(self):

        if not os.path.isfile(PLAYLIST_FILE):
            return

        try:
            with open(PLAYLIST_FILE, "r", encoding="utf-8") as file:
                paths = json.load(file)

            if not isinstance(paths, list):
                return

            # Conserve tous les chemins (y compris fichiers absents) pour que
            # le marqueur ⚠ puisse s'afficher au démarrage.
            usable = [
                p for p in paths
                if isinstance(p, str) and p.strip()
            ]

            restored = 0

            for filename in usable:
                self.add_file(filename, save=False, scroll_to=False)
                restored += 1

            if restored:
                self.status_label.set_text(f"{restored}" + self.tr(" fichier(s) restauré(s)"))

        except Exception as e:
            print("Impossible de charger la playlist :", e, file=sys.stderr)

    # ========================================================
    # ETAT DE SESSION
    # ========================================================

    def save_state(self):

        try:
            directory = os.path.dirname(STATE_FILE)
            os.makedirs(directory, exist_ok=True)

            eq_gains = []
            if self.eq_muted and self.eq_saved_gains:
                eq_gains = list(self.eq_saved_gains)
            elif self.equalizer is not None:
                for i in range(EQ_BANDS):
                    try:
                        eq_gains.append(self.equalizer.get_property(f"band{i}"))
                    except Exception:
                        eq_gains.append(0.0)
            else:
                eq_gains = [0.0] * EQ_BANDS

            # Ne pas restaurer un flux de bouquet au prochain lancement
            last_path = self.current_path
            if getattr(self, "bouquet_active", False):
                last_path = None

            state = {
                "volume": self.volume_scale.get_value(),
                "last_path": last_path,
                "equalizer": eq_gains,
                "pitch": self.pitch_value,
                "tempo": self.tempo_value,
                "language": self.language,
                "performance_mode": getattr(
                    self, "performance_mode", "hardware"
                ),
                "theme": getattr(self, "theme_name", "sombre"),
                "lyrics_enabled": bool(getattr(self, "lyrics_enabled", False)),
                "mini_player_mode": bool(getattr(self, "mini_player_mode", False)),
                "mini_banner_enabled": bool(getattr(self, "mini_banner_enabled", True)),
                "desktop_notifications": bool(getattr(self, "desktop_notifications", True)),
                "shortcuts": dict(getattr(self, "shortcuts", DEFAULT_SHORTCUTS)),
                "colorimetry_enabled": bool(
                    getattr(self, "colorimetry_enabled", False)
                ),
                "color_brightness": float(getattr(self, "color_brightness", 0.0)),
                "color_contrast": float(getattr(self, "color_contrast", 1.0)),
                "color_saturation": float(getattr(self, "color_saturation", 1.0)),
                "color_gamma": float(getattr(self, "color_gamma", 1.0)),
            }

            temporary_file = STATE_FILE + ".tmp"

            with open(temporary_file, "w", encoding="utf-8") as file:
                json.dump(state, file, ensure_ascii=False, indent=2)

            os.replace(temporary_file, STATE_FILE)

        except Exception as e:
            print("Impossible de sauvegarder l'état :", e, file=sys.stderr)

    def load_state(self):

        if not os.path.isfile(STATE_FILE):
            return None

        try:
            with open(STATE_FILE, "r", encoding="utf-8") as file:
                state = json.load(file)

            if not isinstance(state, dict):
                return None

            return state

        except Exception as e:
            print("Impossible de charger l'état :", e, file=sys.stderr)
            return None

    def restore_state(self):

        state = self.load_state()
        if not state:
            return

        volume = state.get("volume")
        if isinstance(volume, (int, float)):
            volume = max(0.0, min(1.0, volume))
            self.volume_scale.set_value(volume)

        eq_gains = state.get("equalizer")
        if isinstance(eq_gains, list) and self.equalizer is not None:
            for i, gain in enumerate(eq_gains):
                if i >= EQ_BANDS:
                    break
                try:
                    gain = max(EQ_MIN_DB, min(EQ_MAX_DB, float(gain)))
                    self.equalizer.set_property(f"band{i}", gain)
                except Exception:
                    pass

        pitch = state.get("pitch")
        if isinstance(pitch, (int, float)):
            self.pitch_value = max(0.5, min(2.0, float(pitch)))

        tempo = state.get("tempo")
        if isinstance(tempo, (int, float)):
            self.tempo_value = max(0.5, min(2.0, float(tempo)))

        # Évite une lecture « un poil trop rapide » à cause de valeurs
        # flottantes ou d'un rate SoundTouch résiduel.
        try:
            self._snap_pitch_tempo_values()
        except Exception:
            self.pitch_value = 1.0
            self.tempo_value = 1.0

        try:
            if "colorimetry_enabled" in state:
                self.colorimetry_enabled = bool(state.get("colorimetry_enabled"))
            b = state.get("color_brightness")
            if isinstance(b, (int, float)):
                self.color_brightness = max(-1.0, min(1.0, float(b)))
            c = state.get("color_contrast")
            if isinstance(c, (int, float)):
                self.color_contrast = max(0.0, min(2.0, float(c)))
            s = state.get("color_saturation")
            if isinstance(s, (int, float)):
                self.color_saturation = max(0.0, min(2.0, float(s)))
            g = state.get("color_gamma")
            if isinstance(g, (int, float)):
                self.color_gamma = max(0.3, min(3.0, float(g)))
        except Exception:
            pass

        self.rebuild_audio_filter(force=True)
        try:
            self._rebuild_video_color_filter()
        except Exception:
            pass

        last_path = state.get("last_path")

        if last_path and last_path in self.get_paths():
            if is_special_source(last_path) or (
                isinstance(last_path, str) and os.path.isfile(last_path)
            ):
                # Différé : la DrawingArea n'a pas encore de XID au moment
                # de __init__ (avant show_all). Sinon glimagesink ouvre une
                # fenêtre « OpenGL renderer » externe noire au redémarrage.
                self._pending_restore_path = last_path
                GLib.idle_add(self._deferred_restore_paused)

    def cancel_pending_session_restore(self):
        """Annule la restauration différée de last_path.

        À appeler lorsqu'un fichier est ouvert explicitement (CLI / gestionnaire
        de fichiers) pour que l'ancienne session n'écrase pas la nouvelle
        lecture une fois la boucle GTK démarrée.
        """
        self._pending_restore_path = None
        self._restore_handle_tries = 0

    def _deferred_restore_paused(self):
        """Termine la restauration de session une fois la fenêtre mappée."""
        path = getattr(self, "_pending_restore_path", None)
        if not path:
            return False
        # Attendre que la fenêtre principale soit réalisée
        try:
            if not self.get_realized():
                return True  # réessayer au prochain idle
            if not self.get_window():
                return True
        except Exception:
            pass
        # Afficher d'abord la zone vidéo si besoin pour obtenir un XID
        try:
            if (
                is_video_file(path)
                or is_dvd(path)
                or is_bluray(path)
            ):
                self.show_video_area(True)
                try:
                    while Gtk.events_pending():
                        Gtk.main_iteration_do(False)
                except Exception:
                    pass
                self._cache_video_window_handle()
                # Si toujours pas de handle, réessayer un peu plus tard
                if not getattr(self, "_video_window_handle", None):
                    if not hasattr(self, "_restore_handle_tries"):
                        self._restore_handle_tries = 0
                    self._restore_handle_tries += 1
                    if self._restore_handle_tries < 20:
                        return True
        except Exception:
            pass
        self._pending_restore_path = None
        self._restore_handle_tries = 0
        try:
            self.load_file_paused(path)
        except Exception as e:
            print("restauration différée:", e, file=sys.stderr)
        return False

    def load_file_paused(self, filename):

        if not filename:
            return

        try:
            if is_cdda(filename) or is_stream(filename) or is_dvd(filename) or is_bluray(filename):
                uri = filename
            else:
                uri = GLib.filename_to_uri(os.path.abspath(filename), None)

            self._playback_generation += 1

            self.player.set_state(Gst.State.NULL)

            self.current_path = filename
            self.current_duration = 0
            self.current_is_cdda = is_cdda(filename)

            # Après un MKV custom : réattacher sink vidéo + spectre/EQ à playbin
            if getattr(self, "_need_playbin_rebind", False):
                self._need_playbin_rebind = False
                try:
                    self.video_sink = self._create_video_sink()
                    if self.video_sink is not None:
                        self.player.set_property("video-sink", self.video_sink)
                except Exception:
                    pass
                try:
                    self.rebuild_audio_filter(force=True, restart=False)
                except Exception:
                    pass
            self.icy_title = ""
            self.icy_artist = ""
            if is_stream(filename):
                self.current_radio_name = self.find_radio_name(filename)
                # Conserve le nom TV déjà posé par play_tv(), sinon résout via favoris
                if not getattr(self, "current_tv_name", None):
                    self.current_tv_name = self.find_tv_name(filename)
            else:
                self.current_radio_name = None
                self.current_tv_name = None

            self.position_scale.set_range(0, 1)
            self.position_scale.set_value(0)
            self._last_known_position = 0.0
            self._pending_seek_delta = 0.0
            self._seek_in_progress = False
            self.reset_spectrum()

            if is_video_file(filename) or is_dvd(filename):
                self.show_video_area(True)
            else:
                self.show_video_area(False)

            self.set_subtitles_enabled(self.subtitles_enabled)
            self.subtitle_tracks = []
            self.current_subtitle_index = -1
            self.audio_tracks = []
            self.current_audio_index = -1
            self._prepare_external_subtitles_for(filename)
            try:
                self._register_external_subtitle_tracks()
            except Exception:
                pass
            # Aucune piste sous-titre auto-sélectionnée (évite freeze PGS/etc.)
            try:
                self.player.set_property("current-text", -1)
            except Exception:
                pass

            if self.current_is_video:
                self._cache_video_window_handle()

            # Restauration : playbin uniquement (session en pause). Les MKV
            # E-AC3 problématiques utiliseront le pipeline custom au prochain Play.
            self._destroy_custom_pipeline()
            # Handle prêt AVANT PAUSED (sinon glimagesink crée une fenêtre GL externe)
            if self.current_is_video:
                try:
                    while Gtk.events_pending():
                        Gtk.main_iteration_do(False)
                except Exception:
                    pass
                self._cache_video_window_handle()
                self._apply_video_overlay_handle()
            self.player.set_property("uri", uri)
            self.player.set_state(Gst.State.PAUSED)

            self.is_playing = True
            self.is_paused = True
            self.play_button.set_label("▶")
            self.status_label.set_text(self.tr("Session restaurée (en pause)"))

            self.update_information(filename)
            self.select_filename(filename)

        except Exception as e:
            self.status_label.set_text(self.tr("Erreur : ") + str(e))
            print("Erreur restauration de session:", e, file=sys.stderr)

    # ========================================================
    # CSS / THÈMES
    # ========================================================

    def _load_ui_prefs(self):
        """Charge thème, OSD et raccourcis depuis state.json (avant build_css)."""
        try:
            state = self.load_state()
            if not state:
                return
            theme = state.get("theme")
            if theme in THEME_PALETTES:
                self.theme_name = theme
            if "lyrics_enabled" in state:
                self.lyrics_enabled = bool(state.get("lyrics_enabled"))
            if "mini_player_mode" in state:
                self.mini_player_mode = bool(state.get("mini_player_mode"))
            if "mini_banner_enabled" in state:
                self.mini_banner_enabled = bool(state.get("mini_banner_enabled"))
            if "desktop_notifications" in state:
                self.desktop_notifications = bool(
                    state.get("desktop_notifications")
                )
            sc = state.get("shortcuts")
            if isinstance(sc, dict):
                merged = dict(DEFAULT_SHORTCUTS)
                for k, v in sc.items():
                    if k in merged and isinstance(v, str) and v.strip():
                        merged[k] = v.strip()
                self.shortcuts = merged
        except Exception:
            pass

    def _css_from_palette(self, p):
        # Longueurs CSS en px logiques, multipliées par ui_scale si le DE
        # n'applique pas déjà un scale factor (évite UI minuscule en 4K).
        s = getattr(self, "_ui_s", None)
        if s is None:
            try:
                factor = float(getattr(self, "ui_scale", 1.0) or 1.0)
            except Exception:
                factor = 1.0
            s = lambda n, _f=factor: max(1, int(round(float(n) * _f)))
        return f"""
        window {{
            background: {p['window_bg']};
            color: {p['window_fg']};
        }}
        .topbar {{
            background: {p['topbar_bg']};
            border-bottom: {s(1)}px solid {p['topbar_border']};
        }}
        button.control {{
            background-image: none;
            background: {p['btn_bg']};
            color: {p['btn_fg']};
            border: {s(1)}px solid {p['btn_border']};
            border-radius: {s(3)}px;
            padding: {s(3)}px {s(8)}px;
            min-width: {s(27)}px;
            min-height: {s(24)}px;
            font-size: {s(14)}px;
        }}
        button.control label {{
            color: inherit;
            font-size: {s(14)}px;
        }}
        button.symbol-bold,
        button.symbol-bold label {{
            font-weight: bold;
            font-size: {s(15)}px;
        }}
        button.control:hover {{
            background: {p['btn_hover']};
            color: {p['window_fg']};
        }}
        button.active {{
            background: {p['btn_active_bg']};
            color: {p['tree_sel_fg']};
            border-color: {p['btn_active_border']};
        }}
        button.play-control {{
            font-family: Sans;
            font-size: {s(19)}px;
            font-weight: bold;
            padding: 0;
            min-width: {s(34)}px;
            min-height: {s(28)}px;
        }}
        button.play-control label {{
            font-family: Sans;
            font-size: {s(19)}px;
            font-weight: bold;
        }}
        .display-time {{
            color: {p['time_fg']};
            font-family: Monospace;
            font-size: {s(12)}px;
        }}
        label.osd {{
            background-color: {p['osd_bg']};
            color: {p['osd_fg']};
            font-size: {s(20)}px;
            font-weight: bold;
            padding: {s(14)}px {s(24)}px;
            border-radius: {s(10)}px;
        }}
        .fs-controls {{
            background-color: rgba(0, 0, 0, 0.55);
            border-radius: {s(10)}px;
            padding: {s(6)}px {s(10)}px;
        }}
        .fs-ctrl-btn {{
            color: #ffffff;
            background: transparent;
            border: none;
            font-size: {s(16)}px;
            min-width: {s(36)}px;
        }}
        .fs-ctrl-btn:hover {{
            background-color: rgba(255, 255, 255, 0.15);
        }}
        .fs-toast {{
            background-color: rgba(0, 0, 0, 0.65);
            color: #ffffff;
            font-size: {s(14)}px;
            font-weight: bold;
            padding: {s(8)}px {s(14)}px;
            border-radius: {s(8)}px;
        }}
        .fs-time {{
            color: #ffffff;
            font-family: Monospace;
            font-size: {s(13)}px;
            font-weight: bold;
        }}

        .playlist-header {{
            background: {p['header_bg']};
            color: {p['header_fg']};
            font-size: {s(11)}px;
            padding: {s(5)}px;
        }}
        entry.playlist-header {{
            border: none;
            border-radius: 0;
            box-shadow: none;
            background: {p['header_bg']};
            color: {p['header_fg']};
            min-height: {s(34)}px;
        }}
        treeview {{
            background: {p['tree_bg']};
            color: {p['tree_fg']};
            font-size: {s(12)}px;
        }}
        treeview.view:selected {{
            background: {p['tree_sel_bg']};
            color: {p['tree_sel_fg']};
        }}
        /* En-têtes de colonnes (Titre, Artiste, Album, Durée) */
        treeview header button,
        treeview.view header button {{
            background-image: none;
            background: {p['header_bg']};
            color: {p['header_fg']};
            border: none;
            border-bottom: {s(1)}px solid {p['btn_border']};
            border-radius: 0;
            padding: {s(4)}px {s(6)}px;
            box-shadow: none;
            font-weight: bold;
        }}
        treeview header button:hover,
        treeview.view header button:hover {{
            background: {p['btn_hover']};
            color: {p['header_fg']};
        }}
        treeview header button label,
        treeview.view header button label {{
            color: {p['header_fg']};
            font-weight: bold;
        }}
        .bottom {{
            background: {p['bottom_bg']};
            border-top: {s(1)}px solid {p['bottom_border']};
        }}
        .cover {{
            background: {p['cover_bg']};
            border: {s(1)}px solid {p['cover_border']};
        }}
        .song-title {{
            color: {p['title_fg']};
            font-size: {s(18)}px;
            font-weight: bold;
        }}
        .song-info {{
            color: {p['info_fg']};
            font-size: {s(15)}px;
        }}
        .status {{
            color: {p['status_fg']};
            font-size: {s(12)}px;
        }}
        .mini-title {{
            background: {p['bottom_bg']};
            color: {p['title_fg']};
            border-top: {s(1)}px solid {p['bottom_border']};
        }}
        .menu-button {{
            background-image: none;
            background: transparent;
            border: none;
            color: {p['menu_fg']};
        }}
        /* Menus déroulants : fond + texte adaptés à chaque thème */
        menu, .menu, .context-menu {{
            background-color: {p.get('menu_bg', p['topbar_bg'])};
            color: {p['menu_fg']};
            border: {s(1)}px solid {p['btn_border']};
        }}
        menuitem, .menuitem {{
            color: {p['menu_fg']};
            background-color: transparent;
        }}
        menuitem label, menuitem * {{
            color: {p['menu_fg']};
        }}
        menuitem:hover, menuitem:selected, menuitem:hover label {{
            background-color: {p.get('menu_hover', p['btn_hover'])};
            color: {p['window_fg']};
        }}
        menuitem:disabled, menuitem:disabled label {{
            color: {p['status_fg']};
            opacity: 0.7;
        }}
        separator, menuitem separator {{
            background-color: {p['btn_border']};
            border-color: {p['btn_border']};
        }}
        /* Fenêtre « À propos » : couleur fond quel que soit le thème */
        .about-black,
        .about-black box,
        .about-black grid {{
            background-color: #404552;
        }}
        .about-black label {{
            color: #ffffff;
        }}
        /* Corrige le « volet » d'indicateur undershoot/overshoot de
           GtkScrolledWindow (ombre native affichée quand il reste du
           contenu à faire défiler) : notre thème ne le stylait pas,
           il se rendait comme un bloc opaque sur ~2 lignes en bas de
           la playlist tant qu'on n'est pas scrollé jusqu'au bout. */
        scrolledwindow > undershoot.top,
        scrolledwindow > undershoot.bottom,
        scrolledwindow > undershoot.left,
        scrolledwindow > undershoot.right,
        scrolledwindow > overshoot.top,
        scrolledwindow > overshoot.bottom,
        scrolledwindow > overshoot.left,
        scrolledwindow > overshoot.right {{
            background-image: none;
            background-color: transparent;
            box-shadow: none;
            border: none;
        }}
        """.encode("utf-8")


    def build_css(self):
        palette = THEME_PALETTES.get(
            getattr(self, "theme_name", "sombre"),
            THEME_PALETTES["sombre"],
        )
        css = self._css_from_palette(palette)
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        # Retire l'ancien provider si on change de thème à chaud
        screen = Gdk.Screen.get_default()
        if self._css_provider is not None:
            try:
                Gtk.StyleContext.remove_provider_for_screen(
                    screen, self._css_provider
                )
            except Exception:
                pass
        self._css_provider = provider
        Gtk.StyleContext.add_provider_for_screen(
            screen,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def set_theme(self, name):
        if name not in THEME_PALETTES:
            return
        if name == getattr(self, "theme_name", None):
            return
        self.theme_name = name
        self.build_css()
        try:
            self.save_state()
        except Exception:
            pass
        self.status_label.set_text(
            self.tr("Thèmes") + " : " + self.tr(THEME_LABELS.get(name, name))
        )


    # ========================================================
    # INTERFACE
    # ========================================================

    def build_interface(self):

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(main)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        top.get_style_context().add_class("topbar")
        top.set_margin_start(7)
        top.set_margin_end(7)
        top.set_margin_top(6)
        top.set_margin_bottom(6)
        self.top_bar = top
        main.pack_start(top, False, False, 0)
        self._build_mini_title_marquee(main)

        menu = Gtk.Button(label="≡")
        menu.get_style_context().add_class("menu-button")
        menu.connect("clicked", self.show_menu)
        top.pack_start(menu, False, False, 0)

        button = self.make_button("+", self.tr("Ajouter des fichiers"))
        self.add_files_button = button
        button.connect("clicked", self.add_files)
        top.pack_start(button, False, False, 0)

        button = self.make_button("▣", self.tr("Ajouter un dossier"))
        self.add_folder_button = button
        button.connect("clicked", self.add_folder)
        top.pack_start(button, False, False, 0)

        button = self.make_button("◀◀", self.tr("Morceau précédent"))
        self.previous_button = button
        button.connect("clicked", self.previous)
        top.pack_start(button, False, False, 0)

        self.play_button = self.make_button("▶", self.tr("Lecture / Pause"))
        self.play_button.get_style_context().add_class("play-control")
        self.play_button.connect("clicked", self.toggle_play)
        top.pack_start(self.play_button, False, False, 0)

        # Ordre transport : Précédent · Play · Suivant · Stop
        button = self.make_button("▶▶", self.tr("Morceau suivant"))
        self.next_button = button
        button.connect("clicked", self.next)
        top.pack_start(button, False, False, 0)

        button = self.make_button("■", self.tr("Stop"))
        self.stop_button = button
        button.connect("clicked", self.stop)
        top.pack_start(button, False, False, 0)

        # Symboles BMP (⇄ ↻) : s'affichent sans police emoji (Lubuntu/GTK)
        self.shuffle_button = self.make_button("⇄", self.tr("Lecture aléatoire"))
        self.shuffle_button.get_style_context().add_class("symbol-bold")
        self.shuffle_button.connect("clicked", self.toggle_shuffle)
        top.pack_start(self.shuffle_button, False, False, 0)

        self.repeat_button = self.make_button("↻", self.tr("Répéter la playlist"))
        self.repeat_button.get_style_context().add_class("symbol-bold")
        self.repeat_button.connect("clicked", self.toggle_repeat)
        top.pack_start(self.repeat_button, False, False, 0)

        self.position_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 1, 0.01
        )
        self.position_scale.set_draw_value(False)
        self.position_scale.set_size_request(self._ui_s(150), -1)
        self.position_scale.connect("button-press-event", self.seek_press)
        self.position_scale.connect("button-release-event", self.seek_release)
        self.position_scale.connect("value-changed", self.seek_value_changed)
        top.pack_start(self.position_scale, True, True, 5)

        self.time_label = Gtk.Label(label="0:00 / 0:00")
        self.time_label.get_style_context().add_class("display-time")
        top.pack_start(self.time_label, False, False, 3)

        self.mute_button = self.make_button("♪", self.tr("Muet") + " (M)")
        self.mute_button.connect("clicked", self.toggle_mute)
        top.pack_start(self.mute_button, False, False, 0)

        self.volume_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 1, 0.01
        )
        self.volume_scale.set_value(0.75)
        self.volume_scale.set_draw_value(False)
        self.volume_scale.set_size_request(self._ui_s(75), -1)
        self.volume_scale.connect("value-changed", self.volume_changed)
        top.pack_start(self.volume_scale, False, False, 2)

        # Raccourci Bluetooth système — label texte pour hériter de
        # button.control { color: btn_fg } (thèmes sombre/clair/sang/…)
        # Les Gtk.Image "bluetooth" du thème icônes ne suivent pas btn_fg.
        self.bluetooth_button = self.make_button(
            "ᛒ",  # rune Bjarkan (origine du logo Bluetooth) — suit btn_fg
            self.tr("Bluetooth")
            + " — "
            + self.tr("Ouvrir les réglages Bluetooth du système"),
        )
        self.bluetooth_button.connect("clicked", self.open_system_bluetooth)
        top.pack_start(self.bluetooth_button, False, False, 2)

        # Contenu principal : vidéo (si présente) + playlist toujours visible
        self.content_paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        main.pack_start(self.content_paned, True, True, 0)

        # Overlay : zone vidéo + OSD (volume / seek / titre en plein écran)
        self.video_overlay = Gtk.Overlay()
        self.video_overlay.set_no_show_all(True)
        self.video_overlay.hide()

        self.video_area = Gtk.DrawingArea()
        self.video_area.set_size_request(-1, 0)
        self.video_area.set_hexpand(True)
        self.video_area.set_vexpand(True)
        self.video_area.connect("realize", self.on_video_realize)
        self.video_area.connect("size-allocate", self.on_video_size_allocate)
        self.video_area.connect("draw", self.on_video_draw)
        self.video_area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.STRUCTURE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.video_area.connect("button-press-event", self.on_video_button_press)
        self.video_area.connect("motion-notify-event", self.on_video_motion)
        self.video_overlay.add(self.video_area)

        self.osd_label = Gtk.Label(label="")
        self.osd_label.set_halign(Gtk.Align.CENTER)
        self.osd_label.set_valign(Gtk.Align.CENTER)
        self.osd_label.set_no_show_all(True)
        self.osd_label.hide()
        self.osd_label.get_style_context().add_class("osd")
        self.video_overlay.add_overlay(self.osd_label)


        # Toasts coins (plein écran) : plage / capture
        self.fs_toast_tl = Gtk.Label(label="")
        self.fs_toast_tl.set_halign(Gtk.Align.START)
        self.fs_toast_tl.set_valign(Gtk.Align.START)
        self.fs_toast_tl.set_margin_start(18)
        self.fs_toast_tl.set_margin_top(16)
        self.fs_toast_tl.set_no_show_all(True)
        self.fs_toast_tl.hide()
        try:
            self.fs_toast_tl.get_style_context().add_class("fs-toast")
        except Exception:
            pass
        self.video_overlay.add_overlay(self.fs_toast_tl)

        self.fs_toast_tr = Gtk.Label(label="")
        self.fs_toast_tr.set_halign(Gtk.Align.END)
        self.fs_toast_tr.set_valign(Gtk.Align.START)
        self.fs_toast_tr.set_margin_end(18)
        self.fs_toast_tr.set_margin_top(16)
        self.fs_toast_tr.set_no_show_all(True)
        self.fs_toast_tr.hide()
        try:
            self.fs_toast_tr.get_style_context().add_class("fs-toast")
        except Exception:
            pass
        self.video_overlay.add_overlay(self.fs_toast_tr)
        self._fs_toast_tl_timeout = 0
        self._fs_toast_tr_timeout = 0

        # Barre de contrôles plein écran (bas d'écran, au survol)
        self._build_fullscreen_controls()

        # shrink=True : la zone vidéo peut céder de la place pour garantir
        # une playlist visible (au moins ~6 lignes) sous le panneau.
        self.content_paned.pack1(self.video_overlay, True, True)

        self.connect("window-state-event", self.on_window_state_event)
        self.connect("focus-in-event", self.on_window_focus_in)
        self.connect("map-event", self.on_window_map)

        playlist_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        playlist_box.set_margin_start(8)
        playlist_box.set_margin_end(8)
        self.playlist_box = playlist_box
        self.content_paned.pack2(playlist_box, True, True)
        self.content_paned.set_position(0)

        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.search_row = search_row
        playlist_box.pack_start(search_row, False, False, 0)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text(
            self.tr("Rechercher un titre, un artiste, un album…")
        )
        self.search_entry.get_style_context().add_class("playlist-header")
        self.search_entry.connect("search-changed", self.on_search_changed)
        search_row.pack_start(self.search_entry, True, True, 0)

        self.visualizer = Gtk.DrawingArea()
        self.visualizer.set_size_request(self._ui_s(230), self._ui_s(34))
        self.visualizer.connect("draw", self.draw_visualizer)
        search_row.pack_start(self.visualizer, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        # Crucial sous GtkPaned + vidéo : sans cela, le TreeView peut
        # propager sa hauteur « naturelle » (toutes les lignes) et le bas
        # de la playlist est rogné sans pouvoir scroller jusqu'aux nouveaux
        # fichiers.
        try:
            scroll.set_propagate_natural_height(False)
            scroll.set_propagate_natural_width(False)
        except Exception:
            pass
        try:
            # Overlay scrollbars (thèmes récents) : garder une barre utilisable
            scroll.set_overlay_scrolling(False)
        except Exception:
            pass
        playlist_box.pack_start(scroll, True, True, 0)
        self.playlist_scroll = scroll

        self.playlist_filter = self.playlist.filter_new()
        self.playlist_filter.set_visible_func(self.playlist_filter_func)

        self.tree = Gtk.TreeView(model=self.playlist_filter)
        self.tree.set_hexpand(True)
        self.tree.set_vexpand(True)
        try:
            # Évite que le TreeView force une hauteur = somme de toutes les
            # lignes (découpe le bas sous le paned vidéo).
            self.tree.set_vadjustment(scroll.get_vadjustment())
            self.tree.set_hadjustment(scroll.get_hadjustment())
        except Exception:
            pass
        self.create_columns()
        self.tree.connect("row-activated", self.row_activated)
        self.tree.connect("button-press-event", self.on_tree_button_press)
        # Sélection multiple : Ctrl+clic, Maj+clic, Maj+flèches
        self.tree.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        self.tree.get_selection().connect("changed", self.selection_changed)
        scroll.add(self.tree)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        bottom.get_style_context().add_class("bottom")
        bottom.set_border_width(8)
        self.bottom_bar = bottom
        main.pack_start(bottom, False, False, 0)

        self.cover = Gtk.Image()
        self.cover.set_size_request(self._ui_s(105), self._ui_s(105))
        self.cover.get_style_context().add_class("cover")
        self.set_default_cover()
        bottom.pack_end(self.cover, False, False, 0)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        # Décalage à droite du bloc métadonnées (titre, artiste, album, status)
        try:
            info.set_margin_start(self._ui_s(4))
        except Exception:
            info.set_margin_start(14)
        bottom.pack_start(info, True, True, 0)

        self.bottom_title = Gtk.Label(label=self.tr("Aucun morceau sélectionné"))
        self.bottom_title.set_xalign(0)
        self.bottom_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.bottom_title.get_style_context().add_class("song-title")
        info.pack_start(self.bottom_title, False, False, 0)

        self.bottom_artist = Gtk.Label()
        self.bottom_artist.set_xalign(0)
        self.bottom_artist.get_style_context().add_class("song-info")
        info.pack_start(self.bottom_artist, False, False, 0)

        self.bottom_album = Gtk.Label()
        self.bottom_album.set_xalign(0)
        self.bottom_album.get_style_context().add_class("song-info")
        info.pack_start(self.bottom_album, False, False, 0)

        self.status_label = Gtk.Label(label=self.tr("Prêt"))
        self.status_label.set_xalign(0)
        self.status_label.get_style_context().add_class("status")
        info.pack_end(self.status_label, False, False, 0)

    def make_button(self, text, tooltip):

        button = Gtk.Button(label=text)
        button.get_style_context().add_class("control")
        button.set_tooltip_text(tooltip)
        return button

    def create_columns(self):

        definitions = [
            (self.tr("Titre"), self.COL_TITLE, 330),
            (self.tr("Artiste"), self.COL_ARTIST, 190),
            (self.tr("Album"), self.COL_ALBUM, 280),
            (self.tr("Durée"), self.COL_DURATION, 60),
        ]

        for title, sort_id, width in definitions:

            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer)

            if sort_id == self.COL_DURATION:
                column.set_cell_data_func(
                    renderer, self._playlist_cell_data, self.COL_DURATION_TEXT
                )
                renderer.set_property("xalign", 1.0)
            else:
                column.set_cell_data_func(
                    renderer, self._playlist_cell_data, sort_id
                )

            column.set_resizable(True)
            column.set_min_width(50 if sort_id != self.COL_DURATION else 55)
            column.set_fixed_width(width)
            if sort_id == self.COL_DURATION:
                column.set_alignment(1.0)
                renderer.set_property("xpad", 2)
            column.set_sort_column_id(sort_id)
            column.connect("clicked", self.on_column_clicked)
            self.tree.append_column(column)

    def _path_is_missing(self, path):
        if not path or not isinstance(path, str) or is_special_source(path):
            return False
        now = time.monotonic()
        hit = getattr(self, "_missing_cache", {}).get(path)
        if hit and now - hit[1] < 5.0:
            return hit[0]
        missing = not os.path.isfile(path)
        if not hasattr(self, "_missing_cache"):
            self._missing_cache = {}
        self._missing_cache[path] = (missing, now)
        return missing


    def _playlist_cell_data(self, column, cell, model, iterator, col_id):
        """Texte playlist ; grise + préfixe si fichier manquant."""
        try:
            path = model.get_value(iterator, self.COL_PATH)
            missing = self._path_is_missing(path)
            text = model.get_value(iterator, col_id)
            text = "" if text is None else str(text)
            if missing and col_id == self.COL_TITLE:
                text = "⚠ " + text
            cell.set_property("text", text)
            if missing:
                cell.set_property("foreground", "#a06060")
                try:
                    cell.set_property("style", Pango.Style.ITALIC)
                except Exception:
                    pass
            else:
                cell.set_property("foreground-set", False)
                try:
                    cell.set_property("style", Pango.Style.NORMAL)
                except Exception:
                    pass
        except Exception:
            pass

    # ========================================================
    # RECHERCHE
    # ========================================================

    def playlist_filter_func(self, model, iterator, data):

        if not self.search_text:
            return True

        title = (model[iterator][self.COL_TITLE] or "").lower()
        artist = (model[iterator][self.COL_ARTIST] or "").lower()
        album = (model[iterator][self.COL_ALBUM] or "").lower()

        return (
            self.search_text in title
            or self.search_text in artist
            or self.search_text in album
        )

    def on_search_changed(self, entry):

        self.search_text = entry.get_text().strip().lower()
        self.playlist_filter.refilter()

    # ========================================================
    # DRAG & DROP + CLAVIER
    # ========================================================

    def setup_drag_and_drop(self):

        targets = [Gtk.TargetEntry.new("text/uri-list", 0, 0)]
        self.drag_dest_set(Gtk.DestDefaults.ALL, targets, Gdk.DragAction.COPY)
        self.connect("drag-data-received", self.on_drag_data_received)

        # Réordonnancement playlist : suivi souris (sans protocole DnD GTK)
        self._setup_playlist_reorder()

    def on_drag_data_received(self, widget, context, x, y, selection, info, timestamp):
        """Dépôt de fichiers / dossiers / URLs depuis le bureau."""
        try:
            uris = selection.get_uris() or []
        except Exception:
            uris = []
        for uri in uris:
            try:
                path = GLib.filename_from_uri(uri)[0]
            except Exception:
                if is_stream(uri):
                    self.add_file(uri)
                continue
            if os.path.isdir(path):
                self.add_directory(path)
            elif is_audio_file(path) or is_video_file(path):
                self.add_file(path)
        try:
            context.finish(True, False, timestamp)
        except Exception:
            pass

    def _setup_playlist_reorder(self):
        """Glisser-déposer interne de la playlist (ligne unique ou bloc)."""
        if not hasattr(self, "tree") or self.tree is None:
            return
        self._pl_src_child_index = None
        self._pl_src_indices = []
        self._pl_drag_armed = False
        self._pl_start_y = 0.0
        self._pl_suppress_select = False
        try:
            self.tree.add_events(
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.BUTTON_RELEASE_MASK
                | Gdk.EventMask.BUTTON1_MOTION_MASK
            )
            # press géré aussi via on_tree_button_press (ordre des handlers)
            self.tree.connect(
                "button-release-event", self._on_playlist_reorder_release
            )
            self.tree.connect(
                "motion-notify-event", self._on_playlist_reorder_motion
            )
        except Exception as e:
            print("Playlist reorder setup:", e, file=sys.stderr)

    def _filter_path_to_child_index(self, fpath):
        if fpath is None:
            return None
        try:
            cpath = self.playlist_filter.convert_path_to_child_path(fpath)
            if cpath is None:
                return None
            return cpath.get_indices()[0]
        except Exception:
            return None

    def _playlist_collect_selected_indices(self, tree, prefer_idx=None):
        """Indices ListStore de la sélection (pour déplacement en bloc)."""
        indices = []
        try:
            _model, paths = tree.get_selection().get_selected_rows()
            for p in paths:
                ci = self._filter_path_to_child_index(p)
                if ci is not None:
                    indices.append(int(ci))
        except Exception:
            indices = []
        if prefer_idx is not None:
            prefer_idx = int(prefer_idx)
            if prefer_idx not in indices:
                indices = [prefer_idx]
        return sorted(set(indices))

    def _playlist_left_button_press(self, tree, event):
        """Clic gauche : sélection (simple/multi) + préparation du drag.

        Si plusieurs lignes sont sélectionnées et que l'on clique sur l'une
        d'elles sans Ctrl/Maj, on retourne True pour empêcher GTK de réduire
        la sélection (indispensable au glisser-déposer en bloc).
        """
        self._pl_src_child_index = None
        self._pl_src_indices = []
        self._pl_drag_armed = False
        self._pl_start_y = float(event.y)
        self._pl_suppress_select = False

        hit = tree.get_path_at_pos(int(event.x), int(event.y))
        if hit is None:
            return False

        path = hit[0]
        idx = self._filter_path_to_child_index(path)
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        sel = tree.get_selection()

        if ctrl or shift:
            if idx is not None:
                self._pl_src_child_index = int(idx)
                self._pl_src_indices = self._playlist_collect_selected_indices(
                    tree, prefer_idx=idx
                )
            return False

        try:
            _model, paths = sel.get_selected_rows()
            multi = len(paths) > 1
            already = sel.path_is_selected(path)
        except Exception:
            multi = False
            already = False

        if already and multi:
            self._pl_src_child_index = int(idx) if idx is not None else None
            self._pl_src_indices = self._playlist_collect_selected_indices(
                tree, prefer_idx=idx
            )
            self._pl_suppress_select = True
            return True

        if not already:
            try:
                sel.unselect_all()
                sel.select_path(path)
                tree.set_cursor(path)
            except Exception:
                pass

        if idx is not None:
            self._pl_src_child_index = int(idx)
            self._pl_src_indices = [int(idx)]
        return False

    def _on_playlist_reorder_motion(self, tree, event):
        if not (event.state & Gdk.ModifierType.BUTTON1_MASK):
            return False
        if self._pl_src_child_index is None and not self._pl_src_indices:
            return False
        if abs(float(event.y) - self._pl_start_y) <= 8:
            return False
        if not self._pl_drag_armed:
            try:
                self._pl_src_indices = self._playlist_collect_selected_indices(
                    tree, prefer_idx=self._pl_src_child_index
                )
            except Exception:
                pass
            self._pl_drag_armed = True
        return False

    def _on_playlist_reorder_release(self, tree, event):
        if event.button != 1:
            return False

        src = self._pl_src_child_index
        src_indices = list(self._pl_src_indices or [])
        armed = self._pl_drag_armed
        self._pl_src_child_index = None
        self._pl_src_indices = []
        self._pl_drag_armed = False
        self._pl_suppress_select = False

        if not armed:
            return False
        if not src_indices and src is not None:
            src_indices = [int(src)]
        if not src_indices:
            return False

        hit = tree.get_path_at_pos(int(event.x), int(event.y))
        if hit is None:
            return False
        dest = self._filter_path_to_child_index(hit[0])
        if dest is None:
            return False

        # Demi-cellule : insertion avant / après la ligne cible
        try:
            cell_y = hit[3] if len(hit) > 3 else 0
            col = hit[1]
            cell_h = 24
            if col is not None:
                cell_h = max(1, tree.get_background_area(hit[0], col).height)
            if cell_y >= cell_h / 2:
                dest = dest + 1
        except Exception:
            pass

        if len(src_indices) == 1 and dest in (
            src_indices[0],
            src_indices[0] + 1,
        ):
            return False

        GLib.idle_add(self._playlist_move_indices, list(src_indices), int(dest))
        return False

    def _playlist_move_indices(self, src_indices, dest_idx):
        """Déplace un bloc de lignes dans le ListStore (reconstruction atomique)."""
        try:
            n = self.playlist.iter_n_children(None)
            if n <= 1:
                return False

            src_set = {int(i) for i in src_indices if 0 <= int(i) < n}
            src_indices = sorted(src_set)
            if not src_indices:
                return False

            dest_idx = max(0, min(int(dest_idx), n))
            rows = []
            it = self.playlist.get_iter_first()
            while it is not None:
                rows.append([self.playlist.get_value(it, c) for c in range(6)])
                it = self.playlist.iter_next(it)

            moving = [rows[i] for i in src_indices]
            remaining = [rows[i] for i in range(n) if i not in src_set]
            removed_before = sum(1 for i in src_indices if i < dest_idx)
            insert_at = max(0, min(dest_idx - removed_before, len(remaining)))
            new_rows = remaining[:insert_at] + moving + remaining[insert_at:]

            try:
                unsorted = getattr(
                    Gtk, "TREE_SORTABLE_UNSORTED_SORT_COLUMN_ID", -2
                )
                self.playlist.set_sort_column_id(
                    unsorted, Gtk.SortType.ASCENDING
                )
            except Exception:
                pass

            self.playlist.clear()
            for row in new_rows:
                self.playlist.append(row)

            try:
                sel = self.tree.get_selection()
                sel.unselect_all()
                for offset in range(len(moving)):
                    path = Gtk.TreePath.new_from_indices([insert_at + offset])
                    fpath = self.playlist_filter.convert_child_path_to_path(path)
                    if fpath is not None:
                        sel.select_path(fpath)
                first = Gtk.TreePath.new_from_indices([insert_at])
                ffirst = self.playlist_filter.convert_child_path_to_path(first)
                if ffirst is not None:
                    self.tree.scroll_to_cell(ffirst, None, False, 0, 0)
            except Exception:
                pass

            try:
                self.save_playlist()
            except Exception:
                pass
        except Exception as e:
            print("Playlist move:", e, file=sys.stderr)
        return False


    def setup_keyboard(self):
        self.connect("key-press-event", self.on_key_press)

    def _normalize_key_name(self, key):
        if not key:
            return ""
        aliases = {
            "space": "space", "Space": "space",
            "Left": "Left", "Right": "Right", "Up": "Up", "Down": "Down",
            "uparrow": "Up", "downarrow": "Down",
            "KP_Up": "Up", "KP_Down": "Down",
            "Page_Up": "Page_Up", "Page_Down": "Page_Down",
            "Escape": "Escape", "Esc": "Escape",
            "Delete": "Delete", "BackSpace": "BackSpace",
        }
        if key in aliases:
            return aliases[key]
        if len(key) == 1:
            return key.lower()
        if key.startswith("F") and key[1:].isdigit():
            return key.upper() if key[1:] else key
        if key.lower().startswith("f") and key[1:].isdigit():
            return "F" + key[1:]
        return key

    def _event_to_shortcut(self, event):
        key = Gdk.keyval_name(event.keyval)
        if key is None:
            return ""
        key = self._normalize_key_name(key)
        parts = []
        if event.state & Gdk.ModifierType.CONTROL_MASK:
            parts.append("Ctrl")
        if event.state & Gdk.ModifierType.SHIFT_MASK:
            parts.append("Shift")
        if event.state & Gdk.ModifierType.MOD1_MASK:
            parts.append("Alt")
        parts.append(key)
        return "+".join(parts)

    def _shortcut_matches(self, action, event_str, key_name):
        binding = (self.shortcuts or DEFAULT_SHORTCUTS).get(action, "")
        if not binding:
            return False
        binding = binding.strip()
        # Comparaison insensible à la casse (modificateurs inclus dans event_str)
        if binding.lower() == event_str.lower():
            return True
        return False

    def _is_text_input_focused(self):
        """True si le focus est dans un champ de saisie (recherche, dialogues…)."""
        try:
            w = self.get_focus()
        except Exception:
            return False
        if w is None:
            return False
        try:
            # Gtk.SearchEntry hérite de Gtk.Entry
            if isinstance(w, (Gtk.Entry, Gtk.TextView, Gtk.SpinButton)):
                return True
        except Exception:
            pass
        try:
            # Certains thèmes / wrappers : nom de type
            name = type(w).__name__
            if name in ("Entry", "SearchEntry", "TextView", "SpinButton"):
                return True
        except Exception:
            pass
        return False

    def on_key_press(self, widget, event):

        key = Gdk.keyval_name(event.keyval)
        if key is None:
            return False
        key_n = self._normalize_key_name(key)
        event_str = self._event_to_shortcut(event)

        # —— Touches multimédia (toujours actives) ——
        if key in ("XF86AudioPlay", "XF86AudioPause", "AudioPlay", "AudioPause"):
            self.toggle_pause()
            return True
        if key in ("XF86AudioStop", "AudioStop"):
            self.stop()
            return True
        if key in ("XF86AudioNext", "AudioNext"):
            self.next()
            return True
        if key in ("XF86AudioPrev", "AudioPrev"):
            self.previous()
            return True
        if key in ("XF86AudioMute", "AudioMute"):
            self.toggle_mute()
            return True
        if key in ("XF86AudioRaiseVolume", "AudioRaiseVolume"):
            self.adjust_volume(+0.05)
            return True
        if key in ("XF86AudioLowerVolume", "AudioLowerVolume"):
            self.adjust_volume(-0.05)
            return True

        # Échap sort toujours du plein écran
        if key_n in ("Escape", "Esc") and self.is_fullscreen:
            self.toggle_video_fullscreen()
            return True

        # Barre de recherche / Entry / TextView : laisser taper (F, M, J, L, espace…)
        if self._is_text_input_focused():
            return False

        if self._shortcut_matches("play_pause", event_str, key_n):
            self.toggle_play()
            return True
        if self._shortcut_matches("mute", event_str, key_n):
            self.toggle_mute()
            return True
        if (
            self._shortcut_matches("fullscreen", event_str, key_n)
            or self._shortcut_matches("fullscreen_alt", event_str, key_n)
        ):
            if self.current_is_video:
                self.toggle_video_fullscreen()
                return True
        if self._shortcut_matches("screenshot", event_str, key_n) or (
            event_str.lower() in ("ctrl+s",)
        ):
            if self.current_is_video:
                self.capture_screenshot()
                return True
        if self._shortcut_matches("seek_forward_5", event_str, key_n):
            self.seek_relative(5)
            self._osd_seek_feedback(5)
            return True
        if self._shortcut_matches("seek_back_5", event_str, key_n):
            self.seek_relative(-5)
            self._osd_seek_feedback(-5)
            return True
        if self._shortcut_matches("seek_forward_10", event_str, key_n):
            self.seek_relative(10)
            self._osd_seek_feedback(10)
            return True
        if self._shortcut_matches("seek_back_10", event_str, key_n):
            self.seek_relative(-10)
            self._osd_seek_feedback(-10)
            return True
        if self._shortcut_matches("seek_forward_30", event_str, key_n):
            self.seek_relative(30)
            self._osd_seek_feedback(30)
            return True
        if self._shortcut_matches("seek_back_30", event_str, key_n):
            self.seek_relative(-30)
            self._osd_seek_feedback(-30)
            return True
        if self._shortcut_matches("volume_up", event_str, key_n):
            self.adjust_volume(+0.05)
            return True
        if self._shortcut_matches("volume_down", event_str, key_n):
            self.adjust_volume(-0.05)
            return True
        if self._shortcut_matches("playlist_up", event_str, key_n):
            # Maj + flèche : sélection étendue native du TreeView
            if event.state & Gdk.ModifierType.SHIFT_MASK:
                return False
            self.move_playlist_selection(-1)
            return True
        if self._shortcut_matches("playlist_down", event_str, key_n):
            if event.state & Gdk.ModifierType.SHIFT_MASK:
                return False
            self.move_playlist_selection(1)
            return True
        if self._shortcut_matches("delete", event_str, key_n):
            self.remove_selected()
            return True
        if self._shortcut_matches("clear_playlist", event_str, key_n):
            self.clear_playlist()
            return True

        return False

    # ========================================================
    # AJOUT DE FICHIERS / FLUX
    # ========================================================

    def add_files(self, *args):

        dialog = Gtk.FileChooserDialog(
            title=self.tr("Ajouter des fichiers"),
            parent=self,
            action=Gtk.FileChooserAction.OPEN
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )
        dialog.set_select_multiple(True)

        if dialog.run() == Gtk.ResponseType.OK:
            added_paths = []
            for filename in dialog.get_filenames():
                if is_audio_file(filename) or is_video_file(filename):
                    self.add_file(filename, save=False, scroll_to=False)
                    added_paths.append(filename)
            if added_paths:
                self.save_playlist()
                try:
                    GLib.idle_add(self._refresh_playlist_scroll_adjustments)
                    GLib.idle_add(self._scroll_playlist_to_end)
                except Exception:
                    pass
                # Un seul fichier choisi → lecture immédiate (comme ouverture
                # depuis le gestionnaire de fichiers)
                if len(added_paths) == 1:
                    self.play_file(added_paths[0])

        dialog.destroy()

    def add_folder(self, *args):

        dialog = Gtk.FileChooserDialog(
            title=self.tr("Ajouter un dossier"),
            parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )

        if dialog.run() == Gtk.ResponseType.OK:
            self.add_directory(dialog.get_filename())

        dialog.destroy()

    def add_directory(self, directory):
        """Scan récursif en arrière-plan pour ne pas bloquer l'UI."""

        self.status_label.set_text(self.tr("Analyse du dossier…"))

        def worker():
            files = []
            try:
                for root, dirs, names in os.walk(directory):
                    for name in names:
                        path = os.path.join(root, name)
                        if is_audio_file(path) or is_video_file(path):
                            files.append(path)
                files.sort(key=lambda p: p.lower())
            except Exception as e:
                GLib.idle_add(
                    lambda: self.status_label.set_text(str(e)) or False
                )
                return
            GLib.idle_add(self._add_directory_files, files)

        threading.Thread(target=worker, daemon=True, name="myostoox-scan-dir").start()

    def _add_directory_files(self, files):
        """Ajoute les fichiers découverts (thread principal)."""
        for path in files:
            try:
                self.add_file(path, save=False, scroll_to=False)
            except Exception:
                pass
        if files:
            try:
                self.save_playlist()
            except Exception:
                pass
            try:
                GLib.idle_add(self._refresh_playlist_scroll_adjustments)
                GLib.idle_add(self._scroll_playlist_to_end)
            except Exception:
                pass
        self.status_label.set_text(
            f"{len(files)}" + self.tr(" fichier(s) ajouté(s)")
        )
        return False

    def add_file(self, filename, save=True, scroll_to=True):

        # Sortir du mode bouquet dès qu'on ajoute un autre type de média
        self.dismiss_bouquet_playlist()

        title, artist, album, year, duration, cover, technical = \
            self.get_metadata(filename, load_cover=False)

        tree_iter = self.playlist.append([
            filename,
            title,
            artist,
            album,
            format_time(duration),
            float(duration)
        ])

        if save:
            self.save_playlist()

        # Sous paned vidéo, les adjustments ne se mettent pas toujours à jour
        # tout seuls après un append → bas de liste inaccessible.
        if scroll_to:
            try:
                child_path = self.playlist.get_path(tree_iter)
            except Exception:
                child_path = None

            def _after_add():
                try:
                    self._refresh_playlist_scroll_adjustments()
                except Exception:
                    pass
                if child_path is not None:
                    try:
                        self._scroll_playlist_to_path(child_path)
                    except Exception:
                        try:
                            self._scroll_playlist_to_end()
                        except Exception:
                            pass
                return False

            GLib.idle_add(_after_add)

    def open_stream_url(self, *args):

        dialog = Gtk.Dialog(
            title=self.tr("Ouvrir une URL de flux"),
            parent=self,
            flags=Gtk.DialogFlags.MODAL
        )
        dialog.set_default_size(self._ui_s(520), self._ui_s(120))
        dialog.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        dialog.add_button(self.tr("Écouter"), Gtk.ResponseType.OK)

        area = dialog.get_content_area()
        area.set_border_width(12)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        area.pack_start(box, True, True, 0)

        label = Gtk.Label(label=self.tr("Adresse du flux (http://, https://, mms://…)"))
        label.set_xalign(0)
        box.pack_start(label, False, False, 0)

        entry = Gtk.Entry()
        entry.set_placeholder_text("https://exemple.com/stream.mp3")
        entry.set_activates_default(True)
        box.pack_start(entry, False, False, 0)

        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.show_all()

        response = dialog.run()
        url = entry.get_text().strip()
        dialog.destroy()

        if response != Gtk.ResponseType.OK or not url:
            return

        if not is_stream(url):
            error = Gtk.MessageDialog(
                parent=self,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text=self.tr("URL non reconnue")
            )
            error.format_secondary_text(
                self.tr("L'adresse doit commencer par http://, https://, mms:// ou rtsp://")
            )
            error.run()
            error.destroy()
            return

        self.add_file(url, save=True)
        self.play_file(url)
        self.status_label.set_text(self.tr("Flux démarré"))

    # ========================================================
    # CD AUDIO
    # ========================================================

    def list_cd_tracks(self):

        src = Gst.ElementFactory.make("cdiocddasrc", "cdsrc")
        if src is None:
            src = Gst.ElementFactory.make("cdparanoiasrc", "cdsrc")
        if src is None:
            return None

        ret = src.set_state(Gst.State.READY)
        if ret == Gst.StateChangeReturn.FAILURE:
            src.set_state(Gst.State.NULL)
            return None

        src.get_state(Gst.CLOCK_TIME_NONE)

        track_fmt = Gst.Format.get_by_nick("track")
        if track_fmt == Gst.Format.UNDEFINED:
            src.set_state(Gst.State.NULL)
            return None

        ok, n_tracks = src.query_duration(track_fmt)
        if not ok or n_tracks < 1:
            src.set_state(Gst.State.NULL)
            return None

        tracks = []

        for i in range(1, int(n_tracks) + 1):
            src.seek_simple(
                track_fmt,
                Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                i
            )
            src.get_state(Gst.CLOCK_TIME_NONE)

            ok, dur = src.query_duration(Gst.Format.TIME)
            duration = (dur / Gst.SECOND) if ok else 0.0

            uri = f"cdda://{i}"
            title = f"Piste {i:02d}"
            tracks.append((uri, title, duration))

        src.set_state(Gst.State.NULL)
        return tracks

    def add_cd(self, *args):

        self.dismiss_bouquet_playlist()

        self.status_label.set_text(self.tr("Lecture de la table des matières du CD…"))
        while Gtk.events_pending():
            Gtk.main_iteration()

        tracks = self.list_cd_tracks()

        if tracks is None:
            dialog = Gtk.MessageDialog(
                parent=self,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text=self.tr("Aucun CD audio détecté")
            )
            dialog.format_secondary_text(
                self.tr("Vérifiez qu’un CD audio est inséré et que les plugins\ngstreamer1.0-plugins-ugly + libcdio sont installés.")
            )
            dialog.run()
            dialog.destroy()
            self.status_label.set_text(self.tr("Prêt"))
            return

        added = 0
        first_uri = None

        for uri, title, duration in tracks:
            self.playlist.append([
                uri,
                title,
                self.tr("CD Audio"),
                self.tr("CD Audio"),
                format_time(duration),
                float(duration)
            ])
            if first_uri is None:
                first_uri = uri
            added += 1

        self.save_playlist()
        self.status_label.set_text(f"{added}" + self.tr(" piste(s) CD ajoutée(s)"))

        if first_uri:
            self.play_file(first_uri)

    # ========================================================
    # DVD
    # ========================================================

    def list_dvd_titles(self):
        """Liste les titres d'un DVD via dvdreadsrc si disponible.

        Retourne None si aucun lecteur / disque n'est accessible, afin
        d'afficher le même type d'avertissement que pour le CD audio.
        """
        src = Gst.ElementFactory.make("dvdreadsrc", "dvdsrc")
        if src is None:
            return None

        ret = src.set_state(Gst.State.READY)
        if ret == Gst.StateChangeReturn.FAILURE:
            src.set_state(Gst.State.NULL)
            return None

        state_ret, state, _pending = src.get_state(2 * Gst.SECOND)
        if state_ret == Gst.StateChangeReturn.FAILURE or state < Gst.State.READY:
            src.set_state(Gst.State.NULL)
            return None

        title_fmt = Gst.Format.get_by_nick("title")
        if title_fmt == Gst.Format.UNDEFINED:
            src.set_state(Gst.State.NULL)
            # Pas de format « title » : vérifier qu'un périphérique optique existe
            if not any(
                os.path.exists(p)
                for p in ("/dev/dvd", "/dev/sr0", "/dev/cdrom", "/dev/sr1")
            ):
                return None
            return [("dvd://", self.tr("DVD") + " — 1", 0.0)]

        ok, n_titles = src.query_duration(title_fmt)
        if not ok or n_titles < 1:
            src.set_state(Gst.State.NULL)
            return None

        titles = []
        for i in range(1, int(n_titles) + 1):
            duration = 0.0
            try:
                src.seek_simple(
                    title_fmt,
                    Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                    i,
                )
                src.get_state(Gst.CLOCK_TIME_NONE)
                ok_d, dur = src.query_duration(Gst.Format.TIME)
                if ok_d and dur > 0:
                    duration = dur / Gst.SECOND
            except Exception:
                pass

            uri = f"dvd://{i}"
            title = f"{self.tr('DVD')} — {i:02d}"
            titles.append((uri, title, duration))

        src.set_state(Gst.State.NULL)
        return titles if titles else None

    def add_dvd(self, *args):

        self.dismiss_bouquet_playlist()

        self.status_label.set_text(self.tr("Lecture de la table des matières du DVD…"))
        while Gtk.events_pending():
            Gtk.main_iteration()

        titles = self.list_dvd_titles()

        if titles is None:
            dialog = Gtk.MessageDialog(
                parent=self,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text=self.tr("Aucun DVD détecté"),
            )
            dialog.format_secondary_text(
                self.tr(
                    "Vérifiez qu’un DVD est inséré et que les plugins\n"
                    "gstreamer1.0-plugins-ugly + libdvdread sont installés."
                )
            )
            dialog.run()
            dialog.destroy()
            self.status_label.set_text(self.tr("Prêt"))
            return

        added = 0
        first_uri = None

        for uri, title, duration in titles:
            self.playlist.append([
                uri,
                title,
                self.tr("DVD"),
                self.tr("DVD"),
                format_time(duration) if duration > 0 else "",
                float(duration),
            ])
            if first_uri is None:
                first_uri = uri
            added += 1

        self.save_playlist()
        self.status_label.set_text(f"{added}" + self.tr(" titre(s) DVD ajouté(s)"))

        if first_uri:
            self._prefer_video = True
            self.play_file(first_uri)

    def list_bluray_titles(self):
        """Liste les titres Blu-ray disponibles (bluraysrc / bluray://)."""
        # Essai via playbin discovery d'un disque (chemin standard Linux)
        disc_paths = (
            "/dev/sr0",
            "/dev/sr1",
            "/dev/bd",
            "/media/cdrom",
        )
        titles = []
        # bluraysrc si présent
        src = Gst.ElementFactory.make("bluraysrc", "bdsrc")
        if src is not None:
            for dev in disc_paths:
                if not os.path.exists(dev):
                    continue
                try:
                    src.set_property("device", dev)
                except Exception:
                    pass
                try:
                    src.set_state(Gst.State.PAUSED)
                    src.get_state(3 * Gst.SECOND)
                except Exception:
                    try:
                        src.set_state(Gst.State.NULL)
                    except Exception:
                        pass
                    continue
                # Nombre de titres (propriété variable selon plugins)
                n = 1
                for prop in ("title", "titles", "num-titles"):
                    try:
                        # certaines builds exposent le max via autre API
                        pass
                    except Exception:
                        pass
                try:
                    src.set_state(Gst.State.NULL)
                except Exception:
                    pass
                # URI générique titre 0
                titles.append(
                    (f"bluray://{dev}", self.tr("Blu-Ray") + f" — {dev}", 0.0)
                )
                break

        if not titles:
            # Repli : URI bluray:// sans chemin
            titles.append(
                ("bluray://", self.tr("Blu-Ray") + " — 1", 0.0)
            )
        return titles

    def add_bluray(self, *args):

        self.dismiss_bouquet_playlist()

        self.status_label.set_text(
            self.tr("Lecture de la table des matières du Blu-Ray…")
        )
        while Gtk.events_pending():
            Gtk.main_iteration()

        # Vérifie la présence d'un élément bluray
        has_bd = (
            Gst.ElementFactory.find("bluraysrc") is not None
            or Gst.ElementFactory.find("blurayparse") is not None
        )
        if not has_bd:
            dialog = Gtk.MessageDialog(
                parent=self,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text=self.tr("Aucun lecteur Blu-Ray détecté"),
            )
            dialog.format_secondary_text(
                self.tr(
                    "Vérifiez qu’un disque Blu-Ray est inséré et que le "
                    "plugin GStreamer bluray (gstreamer1.0-libav / "
                    "libbluray) est installé."
                )
            )
            dialog.run()
            dialog.destroy()
            self.status_label.set_text(self.tr("Prêt"))
            return

        titles = self.list_bluray_titles()
        if not titles:
            dialog = Gtk.MessageDialog(
                parent=self,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text=self.tr("Aucun Blu-Ray détecté"),
            )
            dialog.run()
            dialog.destroy()
            self.status_label.set_text(self.tr("Prêt"))
            return

        added = 0
        first_uri = None
        for uri, title, duration in titles:
            self.playlist.append([
                uri,
                title,
                self.tr("Blu-Ray"),
                self.tr("Blu-Ray"),
                format_time(duration) if duration > 0 else "",
                float(duration),
            ])
            if first_uri is None:
                first_uri = uri
            added += 1

        self.save_playlist()
        self.status_label.set_text(
            f"{added}" + self.tr(" titre(s) Blu-Ray ajouté(s)")
        )
        if first_uri:
            self._prefer_video = True
            self.play_file(first_uri)

    # ========================================================
    # SÉLECTION & INFORMATIONS
    # ========================================================

    def _clear_now_playing_display(self, reset_time=False):
        """Efface titre / artiste / album / pochette (et optionnellement le temps).

        Utilisé quand la playlist devient vide ou que la piste courante
        est retirée, pour ne pas laisser les métadonnées du dernier fichier
        supprimé dans la zone inférieure.
        """
        try:
            self.bottom_title.set_text(self.tr("Aucun morceau sélectionné"))
        except Exception:
            pass
        try:
            self.bottom_artist.set_text("")
        except Exception:
            pass
        try:
            self.bottom_album.set_text("")
        except Exception:
            pass
        try:
            self.set_default_cover()
        except Exception:
            pass
        if reset_time:
            self.current_duration = 0
            self._last_known_position = 0.0
            try:
                self.position_scale.set_range(0, 1)
                self.position_scale.set_value(0)
            except Exception:
                pass
            try:
                self.time_label.set_text("0:00 / 0:00")
            except Exception:
                pass
        try:
            self._update_mini_title()
        except Exception:
            pass

    def _refresh_bottom_after_selection_cleared(self):
        """Appelé quand la sélection TreeView devient vide (ex. dernière suppression).

        - Si une piste est encore en cours et présente dans la playlist → ses infos.
        - Sinon → zone bas vide (évite le fantôme du dernier fichier retiré).
        """
        path = getattr(self, "current_path", None)
        if path:
            try:
                still_there = path in self.get_paths()
            except Exception:
                still_there = False
            if still_there:
                try:
                    self.update_information(path)
                except Exception:
                    pass
                return
            # current_path orphelin (retiré hors stop) : neutraliser
            self.current_path = None
        self._clear_now_playing_display(reset_time=True)

    def selection_changed(self, selection):

        model, paths = selection.get_selected_rows()
        if not paths:
            # Suppression de la dernière ligne (ou désélection) : ne pas
            # laisser les métadonnées du fichier retiré dans la zone bas.
            self._refresh_bottom_after_selection_cleared()
            return
        # Affiche les infos de la dernière ligne sélectionnée (curseur)
        try:
            cursor_path, _col = self.tree.get_cursor()
        except Exception:
            cursor_path = None
        path = cursor_path if cursor_path is not None else paths[-1]
        try:
            iterator = model.get_iter(path)
        except Exception:
            return
        if iterator is None:
            return
        filename = model[iterator][self.COL_PATH]
        self.update_information(filename)

    def row_activated(self, tree, path, column):

        model = tree.get_model()
        iterator = model.get_iter(path)
        if iterator is None:
            return
        filename = model[iterator][self.COL_PATH]
        # Double-clic → sélection unique de cette ligne
        try:
            sel = tree.get_selection()
            sel.unselect_all()
            sel.select_path(path)
            tree.set_cursor(path)
        except Exception:
            pass
        self.play_file(filename)

    def update_information(self, filename):
        """Met à jour la zone d'info en bas (titre / artiste / album)."""

        def _after_info():
            try:
                self._update_mini_title()
            except Exception:
                pass
            return False

        GLib.idle_add(_after_info)

        # Cas flux / radio / TV
        if is_stream(filename):
            radio_name = self.current_radio_name or self.find_radio_name(filename)
            tv_name = self.current_tv_name or self.find_tv_name(filename)

            if tv_name:
                # Chaîne TV favorite : afficher le nom donné, pas l'URL
                self.bottom_title.set_text(tv_name)
                if self.icy_title:
                    if self.icy_artist:
                        self.bottom_artist.set_text(
                            f"{self.icy_artist} – {self.icy_title}"
                        )
                    else:
                        self.bottom_artist.set_text(self.icy_title)
                else:
                    self.bottom_artist.set_text(self.tr("En direct"))
                self.bottom_album.set_text(self.tr("TV"))
            elif radio_name:
                # Radio favorite connue
                self.bottom_title.set_text(radio_name)
                if self.icy_title:
                    if self.icy_artist:
                        self.bottom_artist.set_text(f"{self.icy_artist} – {self.icy_title}")
                    else:
                        self.bottom_artist.set_text(self.icy_title)
                else:
                    self.bottom_artist.set_text(self.tr("En direct"))
                self.bottom_album.set_text(self.tr("Radio"))
            else:
                # Flux quelconque
                display = filename
                if len(display) > 70:
                    display = display[:67] + "…"
                self.bottom_title.set_text(display)
                if self.icy_title:
                    if self.icy_artist:
                        self.bottom_artist.set_text(f"{self.icy_artist} – {self.icy_title}")
                    else:
                        self.bottom_artist.set_text(self.icy_title)
                else:
                    self.bottom_artist.set_text(self.tr("Flux en direct"))
                self.bottom_album.set_text("")

            self.set_default_cover()
            if filename == self.current_path and self.is_playing and not self.is_paused:
                try:
                    self._notify_track_change(
                        self.bottom_title.get_text(),
                        self.bottom_artist.get_text(),
                        self.bottom_album.get_text(),
                    )
                except Exception:
                    pass
            return

        # Cas normal (fichier ou CD)
        title, artist, album, year, duration, cover, technical = \
            self.get_metadata(filename, load_cover=True)

        if is_cdda(filename):
            track = get_cdda_track_number(filename) or 0
            title = self.tr("Piste ") + f"{track:02d}"
            artist = self.tr("CD Audio")
            album = self.tr("CD Audio")

        self.bottom_title.set_text(title)
        self.bottom_artist.set_text(artist)

        if album and year:
            self.bottom_album.set_text(f"{album} • {year}")
        else:
            self.bottom_album.set_text(album or year)

        if filename == self.current_path and self.is_playing and not self.is_paused:
            try:
                self._notify_track_change(title, artist, album)
            except Exception:
                pass

        if cover:
            self.set_cover(cover)
        else:
            self.set_default_cover()

    def set_default_cover(self):

        try:
            self.cover.set_from_icon_name("audio-x-generic", Gtk.IconSize.DIALOG)
        except Exception:
            self.cover.clear()

    def set_cover(self, data):

        if not data:
            self.set_default_cover()
            return

        try:
            if isinstance(data, memoryview):
                data = data.tobytes()
            elif isinstance(data, bytearray):
                data = bytes(data)
            elif not isinstance(data, bytes):
                data = bytes(data)
        except Exception:
            self.set_default_cover()
            return

        if not data:
            self.set_default_cover()
            return

        pixbuf = None

        # 1) GLib.Bytes + MemoryInputStream (API stable PyGObject / GdkPixbuf 2.42+)
        try:
            gbytes = GLib.Bytes.new(data)
            stream = Gio.MemoryInputStream.new_from_bytes(gbytes)
            pixbuf = GdkPixbuf.Pixbuf.new_from_stream(stream, None)
        except Exception:
            pixbuf = None

        # 2) PixbufLoader (détection auto du format)
        if pixbuf is None:
            try:
                loader = GdkPixbuf.PixbufLoader()
                loader.write(data)
                loader.close()
                pixbuf = loader.get_pixbuf()
            except Exception:
                pixbuf = None

        # 3) Essais typés si la détection magique échoue
        if pixbuf is None:
            # Détection grossière du magic number
            mime_guess = None
            if data[:3] == b"\xff\xd8\xff":
                mime_guess = "image/jpeg"
            elif data[:8] == b"\x89PNG\r\n\x1a\n":
                mime_guess = "image/png"
            elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
                mime_guess = "image/webp"
            elif data[:6] in (b"GIF87a", b"GIF89a"):
                mime_guess = "image/gif"
            mimes = []
            if mime_guess:
                mimes.append(mime_guess)
            for m in ("image/jpeg", "image/png", "image/webp", "image/gif"):
                if m not in mimes:
                    mimes.append(m)
            for mime in mimes:
                try:
                    loader = GdkPixbuf.PixbufLoader.new_with_mime_type(mime)
                    loader.write(data)
                    loader.close()
                    pixbuf = loader.get_pixbuf()
                    if pixbuf is not None:
                        break
                except Exception:
                    pixbuf = None

        if pixbuf is None:
            self.set_default_cover()
            return

        try:
            width = pixbuf.get_width()
            height = pixbuf.get_height()
            if width <= 0 or height <= 0:
                self.set_default_cover()
                return
            size = min(width, height)
            x = max(0, (width - size) // 2)
            y = max(0, (height - size) // 2)
            if size > 0:
                pixbuf = pixbuf.new_subpixbuf(x, y, size, size)
            cs = self._ui_s(105)
            pixbuf = pixbuf.scale_simple(cs, cs, GdkPixbuf.InterpType.BILINEAR)
            self.cover.set_from_pixbuf(pixbuf)
        except Exception as exc:
            print("set_cover:", exc, file=sys.stderr)
            self.set_default_cover()

    # ========================================================
    # SPECTRE
    # ========================================================

    def reset_spectrum(self):

        self.spectrum_values = [0.0] * SPECTRUM_BANDS
        self.spectrum_targets = [0.0] * SPECTRUM_BANDS
        self.spectrum_peaks = [0.0] * SPECTRUM_BANDS
        self.spectrum_quiet = [0] * SPECTRUM_BANDS
        self.spectrum_peak_hold = [0.0] * SPECTRUM_BANDS
        self.spectrum_agc_ceiling = SPECTRUM_AGC_CEILING_MIN_DB

    def _extract_magnitudes(self, structure):

        magnitudes = None

        try:
            ok, value_array = structure.get_list("magnitude")
            if ok and value_array is not None:
                n = value_array.n_values
                magnitudes = []
                for i in range(n):
                    try:
                        magnitudes.append(value_array.get_nth(i))
                    except Exception:
                        break
                if magnitudes:
                    return magnitudes
        except Exception:
            pass

        try:
            raw = structure.get_value("magnitude")
            if raw is not None:
                if isinstance(raw, (list, tuple)):
                    magnitudes = [float(x) for x in raw]
                    if magnitudes:
                        return magnitudes
                if hasattr(raw, "n_values") and hasattr(raw, "get_nth"):
                    magnitudes = [raw.get_nth(i) for i in range(raw.n_values)]
                    if magnitudes:
                        return magnitudes
        except Exception:
            pass

        try:
            s = structure.to_string()
            m = re.search(r"magnitude=\(float\)\{([^}]*)\}", s)
            if m:
                magnitudes = [
                    float(x.strip())
                    for x in m.group(1).split(",")
                    if x.strip()
                ]
                if magnitudes:
                    return magnitudes
        except Exception:
            pass

        return None

    def _build_spectrum_ranges(self, source_count):

        if source_count < 2:
            return []

        low = max(0.0, min(0.90, SPECTRUM_LOW_CUT))
        exponent = max(1.0, SPECTRUM_SPREAD_EXPONENT)
        ranges = []

        for i in range(SPECTRUM_BANDS):
            p0 = i / SPECTRUM_BANDS
            p1 = (i + 1) / SPECTRUM_BANDS
            t0 = p0 ** exponent
            t1 = p1 ** exponent
            pos0 = low + ((1.0 - low) * t0)
            pos1 = low + ((1.0 - low) * t1)
            start = int(source_count * pos0)
            end = int(source_count * pos1)
            start = max(0, min(source_count - 1, start))
            end = max(start + 1, min(source_count, end))
            ranges.append((start, end))

        return ranges

    def process_spectrum(self, structure):

        if not self.spectrum_enabled:
            return

        try:
            magnitudes = self._extract_magnitudes(structure)
            if not magnitudes:
                return

            source_count = min(SPECTRUM_SOURCE_BANDS, len(magnitudes))
            if source_count < 20:
                return

            db_values = []
            for i in range(source_count):
                try:
                    db = float(magnitudes[i])
                except Exception:
                    db = -80.0
                db = max(-80.0, min(0.0, db))
                db_values.append(db)

            ranges = self._build_spectrum_ranges(source_count)
            if len(ranges) != SPECTRUM_BANDS:
                return

            raw = [0.0] * SPECTRUM_BANDS
            band_dbs = [None] * SPECTRUM_BANDS

            if self.spectrum_agc_enabled:
                high_reference = self.spectrum_agc_ceiling
            else:
                high_reference = SPECTRUM_HIGH_DB

            for i, (start, end) in enumerate(ranges):

                segment = db_values[start:end]

                if not segment:
                    continue

                db = max(segment)
                band_dbs[i] = db

                value = (
                    db - SPECTRUM_LOW_DB
                ) / (
                    high_reference
                    -
                    SPECTRUM_LOW_DB
                )

                value = max(
                    0.0,
                    min(
                        1.0,
                        value
                    )
                )

                value = math.pow(
                    value,
                    0.72
                )

                raw[i] = value

            compensation = (
                0.82,
                0.88,
                0.93,
                0.97,
                1.00,
                1.02,
                1.04,
                1.05,
                1.06,
                1.07,
                1.08,
                1.09,
            )

            for i in range(SPECTRUM_BANDS):

                value = (
                    raw[i]
                    *
                    compensation[i]
                )

                value = max(
                    0.0,
                    min(
                        1.0,
                        value
                    )
                )

                if value < 0.030:

                    self.spectrum_quiet[i] += 1

                    if self.spectrum_quiet[i] >= 2:
                        value = 0.0

                else:

                    self.spectrum_quiet[i] = 0

                if value <= SPECTRUM_VISUAL_FLOOR:

                    value = 0.0

                else:

                    value = (
                        value
                        -
                        SPECTRUM_VISUAL_FLOOR
                    ) / (
                        1.0
                        -
                        SPECTRUM_VISUAL_FLOOR
                    )

                value = max(
                    0.0,
                    min(
                        1.0,
                        value
                    )
                )

                if value > 0.0:

                    value = math.pow(
                        value,
                        SPECTRUM_VERTICAL_COMPRESSION
                    )

                value = max(
                    0.0,
                    min(
                        1.0,
                        value
                    )
                )

                self.spectrum_targets[i] = value * (7.0 / 11.0)

            if self.spectrum_agc_enabled:

                observed = [d for d in band_dbs if d is not None]

                if observed:

                    frame_max_db = max(observed)

                    if frame_max_db > self.spectrum_agc_ceiling:

                        self.spectrum_agc_ceiling += (
                            (frame_max_db - self.spectrum_agc_ceiling)
                            * SPECTRUM_AGC_ATTACK
                        )

                    else:

                        self.spectrum_agc_ceiling += (
                            (frame_max_db - self.spectrum_agc_ceiling)
                            * SPECTRUM_AGC_RELEASE
                        )

                    self.spectrum_agc_ceiling = max(
                        SPECTRUM_AGC_CEILING_MIN_DB,
                        min(
                            SPECTRUM_HIGH_DB,
                            self.spectrum_agc_ceiling
                        )
                    )

        except Exception as e:

            print(
                "Erreur spectre:",
                e,
                file=sys.stderr
            )

    # ========================================================
    # ANIMATION DU SPECTRE
    # ========================================================

    def update_visualizer(self):

        if getattr(self, "_app_closing", False):
            return False
        if not self.spectrum_enabled:
            return True
        # En vidéo le FFT est coupé : pas de redraw 60 Hz inutile
        if getattr(self, "current_is_video", False):
            return True

        # Cadence ~60 Hz en audio seul

        now = time.monotonic()

        if self._visualizer_last_tick is None:
            dt = SPECTRUM_REFERENCE_DT
        else:
            dt = now - self._visualizer_last_tick
            dt = max(0.0, min(dt, 0.25))

        self._visualizer_last_tick = now

        time_scale = dt / SPECTRUM_REFERENCE_DT

        for i in range(SPECTRUM_BANDS):

            current = self.spectrum_values[i]
            target = self.spectrum_targets[i]

            difference = (
                target
                -
                current
            )

            if difference > 0:

                if difference > 0.45:
                    speed = 0.92
                elif difference > 0.25:
                    speed = 0.84
                elif difference > 0.10:
                    speed = 0.74
                else:
                    speed = 0.62

            else:

                falling = abs(difference)

                if falling > 0.45:
                    speed = 0.55
                elif falling > 0.25:
                    speed = 0.48
                elif falling > 0.10:
                    speed = 0.40
                else:
                    speed = 0.32

            effective_speed = 1.0 - pow(
                max(0.0, 1.0 - speed),
                time_scale
            )

            current += difference * effective_speed

            if abs(
                target - current
            ) < 0.002:

                current = target

            if (
                target <= 0.0
                and current < 0.008
            ):

                current = 0.0

            current = max(
                0.0,
                min(
                    1.0,
                    current
                )
            )

            self.spectrum_values[i] = current

            peak = self.spectrum_peaks[i]
            hold = self.spectrum_peak_hold[i]

            if current > peak:

                effective_rise = 1.0 - pow(
                    max(0.0, 1.0 - SPECTRUM_PEAK_RISE),
                    time_scale
                )

                peak += (
                    current - peak
                ) * effective_rise

                hold = SPECTRUM_PEAK_HOLD_SECONDS

            elif hold > 0.0:

                hold = max(0.0, hold - dt)

            else:

                peak *= pow(
                    SPECTRUM_PEAK_FALL,
                    time_scale
                )

                if peak < 0.008:
                    peak = 0.0

            self.spectrum_peaks[i] = max(
                0.0,
                min(
                    1.0,
                    peak
                )
            )

            self.spectrum_peak_hold[i] = hold

        if not self.is_playing:

            fade_values = pow(0.64, time_scale)
            fade_targets = pow(0.48, time_scale)
            fade_peaks = pow(0.76, time_scale)

            for i in range(
                SPECTRUM_BANDS
            ):

                self.spectrum_values[i] *= fade_values
                self.spectrum_targets[i] *= fade_targets
                self.spectrum_peaks[i] *= fade_peaks

                if self.spectrum_values[i] < 0.003:
                    self.spectrum_values[i] = 0.0

                if self.spectrum_targets[i] < 0.003:
                    self.spectrum_targets[i] = 0.0

                if self.spectrum_peaks[i] < 0.003:
                    self.spectrum_peaks[i] = 0.0

        state = (tuple(self.spectrum_values), tuple(self.spectrum_peaks))
        if state == getattr(self, "_viz_last_state", None):
            return True
        self._viz_last_state = state
        self.visualizer.queue_draw()
        return True

    # ========================================================
    # DESSIN DU SPECTRE
    # ========================================================

    def draw_visualizer(self, widget, cr):

        allocation = widget.get_allocation()
        width = allocation.width
        height = allocation.height

        if width <= 0 or height <= 0:
            return False

        count = SPECTRUM_BANDS
        try:
            us = float(getattr(self, "ui_scale", 1.0) or 1.0)
        except Exception:
            us = 1.0
        gap = 4.5 * us
        bar_width = 8.0 * us
        total_width = count * bar_width + (count - 1) * gap
        start_x = (width - total_width) / 2.0
        bottom_margin = 4.0 * us
        top_margin = 5.0 * us
        usable_height = max(1.0, height - bottom_margin - top_margin)

        for i in range(count):
            value = max(0.0, min(1.0, self.spectrum_values[i]))
            peak = max(0.0, min(1.0, self.spectrum_peaks[i]))
            minimum_height = 1.0
            bar_height = minimum_height + value * (usable_height - minimum_height)
            x = start_x + i * (bar_width + gap)
            y = height - bottom_margin - bar_height

            pattern = cairo.LinearGradient(0, y, 0, height - bottom_margin)
            pattern.add_color_stop_rgba(0.0, 0.58, 0.88, 1.00, 1.0)
            pattern.add_color_stop_rgba(0.42, 0.31, 0.71, 0.94, 1.0)
            pattern.add_color_stop_rgba(1.0, 0.08, 0.39, 0.65, 1.0)

            cr.rectangle(x, y, bar_width, bar_height)
            cr.set_source(pattern)
            cr.fill()

            cr.set_source_rgba(0.74, 0.95, 1.00, 0.84)
            cr.rectangle(x, y, bar_width, 1.0)
            cr.fill()

            if peak > 0.08:
                peak_y = height - bottom_margin - peak * usable_height
                peak_y = min(peak_y, y)
                cr.set_source_rgba(0.76, 0.95, 1.00, 0.56)
                cr.rectangle(x, peak_y, bar_width, 1.0)
                cr.fill()

        return False

    # ========================================================
    # VIDÉO — sinks, buffers, mémoire 4K
    # ========================================================

    # Compat — rangs réels dans _apply_decoder_performance_mode
    # (NVIDIA d'abord, puis soft en repli).
    _HW_DECODER_NAMES = _NVIDIA_DECODER_NAMES + _OTHER_HW_DECODER_NAMES

    def _load_performance_mode(self):
        """Charge le mode performances depuis state.json (défaut : hardware)."""
        try:
            if os.path.isfile(STATE_FILE):
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
                if isinstance(state, dict):
                    mode = state.get("performance_mode")
                    if mode in ("hardware", "software"):
                        return mode
        except Exception:
            pass
        return "hardware"

    def _apply_decoder_performance_mode(self, force_software=False):
        """Rangs GStreamer : NVIDIA → (repli) logiciel ; optionnellement soft forcé.

        Mode Matériel (défaut) :
          1. Décodeurs NVIDIA (nvh264dec, nvh265dec, nvav1dec, …) — rang max
          2. Décodeurs logiciels (avdec_*, dav1d) — rang secondaire = repli autoplug
          3. AMD VA / Vulkan / Intel VA-API / Quick Sync / V4L2 — rang bas
             (préserve priorité NVIDIA ; soft avant ces HW pour stabilité)

        Mode Logiciel ou force_software=True (échec HW) :
          HW désactivé (Rank.NONE), soft prioritaires.
        """
        mode = getattr(self, "performance_mode", "hardware")
        if force_software:
            mode = "software"

        if mode == "hardware":
            for name in _NVIDIA_DECODER_NAMES:
                _gst_set_rank(name, Gst.Rank.PRIMARY + 200)
            for name in _SOFTWARE_VIDEO_DECODER_NAMES:
                _gst_set_rank(name, Gst.Rank.PRIMARY + 80)
            for name in _OTHER_HW_DECODER_NAMES:
                _gst_set_rank(name, Gst.Rank.PRIMARY + 20)
            try:
                apply_safe_gstreamer_ranks(prefer_nvidia_av1=True)
            except Exception:
                pass
        else:
            for name in _NVIDIA_DECODER_NAMES:
                _gst_set_rank(name, Gst.Rank.NONE)
            for name in _OTHER_HW_DECODER_NAMES:
                _gst_set_rank(name, Gst.Rank.NONE)
            for name in _SOFTWARE_VIDEO_DECODER_NAMES:
                _gst_set_rank(name, Gst.Rank.PRIMARY + 100)
            try:
                apply_safe_gstreamer_ranks(prefer_nvidia_av1=False)
            except Exception:
                pass

    def _promote_av1_software_decoders(self):
        """Compat : politique AV1 selon le mode performances courant."""
        prefer = getattr(self, "performance_mode", "hardware") == "hardware"
        apply_safe_gstreamer_ranks(prefer_nvidia_av1=prefer)

    def _apply_safe_audio_decoder_ranks(self):
        """Évite a52dec sur E-AC3/AC3 (crash heap free(): invalid next size)."""
        try:
            factory = Gst.ElementFactory.find("a52dec")
            if factory is not None:
                factory.set_rank(Gst.Rank.NONE)
        except Exception:
            pass
        for name in ("avdec_eac3", "avdec_ac3"):
            try:
                factory = Gst.ElementFactory.find(name)
                if factory is not None:
                    factory.set_rank(Gst.Rank.PRIMARY + 100)
            except Exception:
                pass
        # Ne plus forcer les rangs AV1 ici : le mode Matériel / Logiciel
        # gère déjà ça via _apply_decoder_performance_mode.

    def set_performance_mode(self, mode):
        """Enregistre le mode performances et prévient si un redémarrage est nécessaire."""
        if mode not in ("hardware", "software"):
            return
        if mode == getattr(self, "performance_mode", None):
            return

        self.performance_mode = mode
        try:
            self.save_state()
        except Exception:
            pass

        # Tente d'appliquer immédiatement (utile au prochain play_file),
        # mais les factories déjà utilisées peuvent rester en cache.
        self._apply_decoder_performance_mode()
        try:
            self._configure_playbin_buffers()
        except Exception:
            pass

        label = (
            self.tr("Matériel") if mode == "hardware" else self.tr("Logiciel")
        )
        self.status_label.set_text(
            self.tr("Performances") + " : " + label
        )

        dialog = Gtk.MessageDialog(
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK,
            text=self.tr("Redémarrage recommandé"),
        )
        dialog.format_secondary_text(
            self.tr(
                "Le mode de décodage vidéo a été modifié.\n"
                "Pour une prise en compte complète, redémarrez l'application."
            )
        )
        dialog.run()
        dialog.destroy()

    def _create_video_sink(self):
        """Choisit le sink vidéo le plus stable pour l'overlay X11 (DrawingArea).

        glimagesink conserve le ratio (force-aspect-ratio) et évite le bug
        GLAMOR/Xv. ximagesink en repli. Le handle X11 est posé AVANT PLAYING
        via _apply_video_overlay_handle pour éviter la fenêtre « OpenGL renderer ».
        """
        candidates = (
            "glimagesink",     # ratio correct + perf, handle posé avant PLAYING
            "ximagesink",      # repli fiable
            "xvimagesink",     # dernier recours (bug connu avec GLAMOR)
            "autovideosink",
        )
        sink = None
        for name in candidates:
            try:
                element = Gst.ElementFactory.make(name, "videosink")
            except Exception:
                element = None
            if element is not None:
                sink = element
                break

        if sink is None:
            return None

        # Mode Logiciel / AV1 soft : lateness large (decode CPU peut retarder).
        soft = getattr(self, "performance_mode", "hardware") == "software"
        # 1.5 s en soft : évite les drops en rafale sur AV1 logiciel
        lateness = (1500 if soft else 500) * Gst.MSECOND
        # Propriétés communes (ignorées silencieusement si absentes)
        props = {
            "force-aspect-ratio": True,
            "sync": True,
            "handle-events": False,
            # Bandes noires gérées par le fond de la DrawingArea
            "draw-borders": False,
            # QoS actif mais tolérant : 20 ms provoquait des freezes MKV
            # (trop de frames considérées « en retard » et jetées en chaîne).
            "qos": True,
            # Large tolérance : trop strict → frames jetées en rafale (vert/sauts)
            "max-lateness": lateness,
        }
        for prop, value in props.items():
            try:
                sink.set_property(prop, value)
            except Exception:
                pass

        # Empêche certains sinks de créer leur propre fenêtre toplevel
        for prop, value in (
            ("force-aspect-ratio", True),
            ("show-preroll-frame", True),
        ):
            try:
                if sink.find_property(prop) is not None:
                    sink.set_property(prop, value)
            except Exception:
                pass

        return sink

    def _configure_playbin_buffers(self):
        """Configure les tampons playbin pour MKV locaux (multi-pistes / 4K).

        Buffer un peu plus généreux en mode Logiciel pour absorber les pics
        CPU ; en Matériel on reste modéré pour limiter la RAM.
        """
        if self.player is None:
            return
        soft = getattr(self, "performance_mode", "hardware") == "software"
        buf_size = (12 if soft else 8) * 1024 * 1024
        buf_dur = (5 if soft else 4) * Gst.SECOND
        for prop, value in (
            ("buffer-size", buf_size),
            ("buffer-duration", buf_dur),
        ):
            try:
                self.player.set_property(prop, value)
            except Exception:
                pass

        # Flags : vidéo + audio + texte, sans visualisation ni download ring.
        try:
            flags = self.player.get_property("flags")
            VIDEO = 1 << 0
            AUDIO = 1 << 1
            TEXT = 1 << 2
            VIS = 1 << 3
            DOWNLOAD = 1 << 7
            # TEXT activé pour sous-titres embarqués MKV
            flags = (flags | VIDEO | AUDIO | TEXT) & ~VIS & ~DOWNLOAD
            self.player.set_property("flags", flags)
        except Exception:
            pass

    def _set_spectrum_active_for_media(self, is_video):
        """Spectre FFT : actif en audio uniquement.

        En vidéo (MKV/MP4/…), les messages spectrum sont coupés pour limiter
        la charge CPU/bus (comportement proche de la version fluide).
        """
        if self.spectrum is None:
            return
        try:
            if is_video:
                # Coupe le FFT pendant la vidéo (MKV inclus)
                self.spectrum.set_property("post-messages", False)
            else:
                self.spectrum.set_property(
                    "post-messages", bool(self.spectrum_enabled)
                )
                if self.spectrum_enabled:
                    self.spectrum.set_property("interval", 20000000)  # 20 ms
        except Exception:
            pass


    def _release_pause_pixbuf(self):
        """Libère l'image de pause (peut peser plusieurs Mo en 4K)."""
        self._pause_pixbuf = None

    def _cache_video_window_handle(self):
        """Met en cache l'identifiant de la DrawingArea pour prepare-window-handle."""
        if self.video_area is None:
            return None
        try:
            if not self.video_area.get_realized():
                self.video_area.realize()
            # S'assurer que la zone est mappée (visible) avant get_xid
            try:
                if not self.video_area.get_mapped():
                    self.video_area.show()
                    while Gtk.events_pending():
                        Gtk.main_iteration_do(False)
            except Exception:
                pass
            window = self.video_area.get_window()
            if window is None:
                return None
            try:
                window.ensure_native()
            except Exception:
                pass
            xid = window.get_xid()
            if xid and xid != 0:
                self._video_window_handle = xid
                return xid
        except Exception as e:
            print("Erreur cache window handle:", e, file=sys.stderr)
        return None

    def _apply_video_overlay_handle(self, sink=None):
        """Lie le sink vidéo au XID de la DrawingArea *avant* PLAYING/PAUSED.

        Ne PAS appeler set_render_rectangle ici : sur certains sinks (surtout
        en plein écran) cela étire / déforme l'image. force-aspect-ratio gère
        le ratio ; le sink occupe la zone client naturellement.
        """
        handle = getattr(self, "_video_window_handle", None)
        if not handle:
            handle = self._cache_video_window_handle()
        if not handle:
            return False
        targets = []
        if sink is not None:
            targets.append(sink)
        vs = getattr(self, "video_sink", None)
        if vs is not None and vs not in targets:
            targets.append(vs)
        ok = False
        for target in targets:
            try:
                if isinstance(target, GstVideo.VideoOverlay):
                    GstVideo.VideoOverlay.set_window_handle(target, handle)
                    ok = True
                elif hasattr(target, "set_window_handle"):
                    target.set_window_handle(handle)
                    ok = True
            except Exception as e:
                print("apply overlay handle:", e, file=sys.stderr)
        return ok

    def on_sync_message(self, bus, message):
        """Répond au message synchrone prepare-window-handle (mécanisme GStreamer standard)."""
        structure = message.get_structure()
        if structure is None:
            return
        if structure.get_name() != "prepare-window-handle":
            return
        handle = self._video_window_handle
        if handle is None:
            handle = self._cache_video_window_handle()
        if handle is None:
            return
        try:
            sink = message.src
            if isinstance(sink, GstVideo.VideoOverlay):
                GstVideo.VideoOverlay.set_window_handle(sink, handle)
            elif hasattr(sink, "set_window_handle"):
                sink.set_window_handle(handle)
            # Aussi sur le sink top-level (bin / glimagesink parent)
            self._apply_video_overlay_handle(sink)
        except Exception as e:
            print("Erreur prepare-window-handle:", e, file=sys.stderr)


    def _nudge_video_redraw(self):
        """Simple redessin (expose) sans état de debounce — après resize/plein écran/focus."""
        if not self.current_is_video or self.video_sink is None:
            return False
        try:
            if isinstance(self.video_sink, GstVideo.VideoOverlay):
                GstVideo.VideoOverlay.expose(self.video_sink)
            if self.video_area is not None:
                self.video_area.queue_draw()
        except Exception:
            pass
        return False

    def on_video_realize(self, widget):
        # Fond opaque noir sur la fenêtre X11 de la zone vidéo : le tracé des
        # bandes noires par le videosink lui-même (draw-borders) est
        # désactivé (voir __init__) car il provoque un liseré parasite /
        # vibrant en bas (et parfois à droite) de l'image sur certaines
        # vidéos basse résolution en plein écran. C'est donc ce fond de
        # fenêtre qui assure désormais, seul et de façon stable, l'affichage
        # des bandes noires autour de l'image.
        try:
            window = widget.get_window()
            if window is not None:
                black = Gdk.RGBA()
                black.red = 0.0
                black.green = 0.0
                black.blue = 0.0
                black.alpha = 1.0
                window.set_background_rgba(black)
        except Exception:
            pass
        self._cache_video_window_handle()

    def on_video_size_allocate(self, widget, allocation):
        # Debounce : le size-allocate tire en rafale pendant un resize
        if not self.current_is_video or self.video_sink is None:
            return
        if self._size_allocate_timer:
            try:
                GLib.source_remove(self._size_allocate_timer)
            except Exception:
                pass
            self._size_allocate_timer = 0

        def _after_resize():
            self._size_allocate_timer = 0
            self._cache_video_window_handle()
            self._apply_video_overlay_handle()
            # force-aspect-ratio doit rester actif (évite stretch plein écran)
            try:
                sink = getattr(self, "video_sink", None)
                if sink is not None and sink.find_property("force-aspect-ratio"):
                    sink.set_property("force-aspect-ratio", True)
            except Exception:
                pass
            self._nudge_video_redraw()
            return False

        self._size_allocate_timer = GLib.timeout_add(150, _after_resize)

    def on_video_draw(self, widget, cr):
        if getattr(self, "lyrics_mode", False):
            self._draw_lyrics(widget, cr)
            return True
        # Pendant la lecture active : laisser le sink X11 dessiner (pas de cairo).
        if self.current_is_video and self.is_playing and not self.is_paused:
            return False
        # En pause : redessine la dernière image capturée (anti-fantôme après
        # masquage / restauration de la fenêtre).
        if self.current_is_video and self.is_playing and self.is_paused:
            allocation = widget.get_allocation()
            cr.set_source_rgb(0.0, 0.0, 0.0)
            cr.rectangle(0, 0, allocation.width, allocation.height)
            cr.fill()
            if self._pause_pixbuf is not None:
                try:
                    pw = self._pause_pixbuf.get_width()
                    ph = self._pause_pixbuf.get_height()
                    if pw > 0 and ph > 0 and allocation.width > 0 and allocation.height > 0:
                        scale = min(
                            allocation.width / pw,
                            allocation.height / ph,
                        )
                        dw = max(1, int(pw * scale))
                        dh = max(1, int(ph * scale))
                        ox = (allocation.width - dw) // 2
                        oy = (allocation.height - dh) // 2
                        pix = self._pause_pixbuf
                        if dw != pw or dh != ph:
                            pix = self._pause_pixbuf.scale_simple(
                                dw, dh, GdkPixbuf.InterpType.BILINEAR
                            )
                        Gdk.cairo_set_source_pixbuf(cr, pix, ox, oy)
                        cr.paint()
                except Exception:
                    pass
            return True
        # Hors vidéo / à l'arrêt : fond noir opaque
        allocation = widget.get_allocation()
        cr.set_source_rgb(0.0, 0.0, 0.0)
        cr.rectangle(0, 0, allocation.width, allocation.height)
        cr.fill()
        return True

    def _capture_pause_frame(self):
        """Capture le dernier frame visible pour l'afficher pendant la pause.

        En 4K une capture pleine résolution coûte ~30 Mo (RGBA) ; on la
        réduit à 1280 px de large max — amplement suffisant pour l'aperçu.
        """
        self._pause_pixbuf = None
        if not self.current_is_video or self.video_area is None:
            return
        try:
            if self.video_sink is not None and isinstance(
                self.video_sink, GstVideo.VideoOverlay
            ):
                try:
                    GstVideo.VideoOverlay.expose(self.video_sink)
                except Exception:
                    pass
            window = self.video_area.get_window()
            if window is None:
                return
            w = self.video_area.get_allocated_width()
            h = self.video_area.get_allocated_height()
            if w <= 0 or h <= 0:
                return
            pixbuf = Gdk.pixbuf_get_from_window(window, 0, 0, w, h)
            if pixbuf is None:
                return
            max_w = 1280
            if w > max_w:
                nh = max(1, int(h * (max_w / float(w))))
                try:
                    pixbuf = pixbuf.scale_simple(
                        max_w, nh, GdkPixbuf.InterpType.BILINEAR
                    )
                except Exception:
                    pass
            self._pause_pixbuf = pixbuf
        except Exception:
            self._pause_pixbuf = None

    def _capture_pause_frame_and_redraw(self):
        self._capture_pause_frame()
        try:
            if self.video_area is not None:
                self.video_area.queue_draw()
        except Exception:
            pass
        return False

    def _refresh_video_surface(self):
        """Redessine après un masquage/restauration de fenêtre."""
        if not self.current_is_video:
            return False
        try:
            self._cache_video_window_handle()
            if self.is_paused and self._pause_pixbuf is not None:
                if self.video_area is not None:
                    self.video_area.queue_draw()
            else:
                self._nudge_video_redraw()
        except Exception:
            pass
        return False

    def on_window_focus_in(self, widget, event):
        if self.current_is_video:
            GLib.timeout_add(50, self._refresh_video_surface)
        return False

    def on_window_map(self, widget, event):
        if self.current_is_video:
            GLib.timeout_add(50, self._refresh_video_surface)
        return False

    def on_video_button_press(self, widget, event):
        if event.type == Gdk.EventType._2BUTTON_PRESS and event.button == 1:
            self.toggle_video_fullscreen()
            return True
        return False

    def on_window_state_event(self, widget, event):
        # Synchronise l'état interne avec le vrai état de la fenêtre
        if event.changed_mask & Gdk.WindowState.FULLSCREEN:
            is_fs = bool(event.new_window_state & Gdk.WindowState.FULLSCREEN)
            self.is_fullscreen = is_fs
            if not is_fs and self._fullscreen_ui_state is not None:
                self._restore_ui_after_fullscreen()
            elif is_fs and (
                self.current_is_video or getattr(self, "lyrics_mode", False)
            ):
                # Ré-applique le masquage UI (vidéo ou paroles).
                self._apply_fullscreen_ui_layout()
                if self.current_is_video:
                    self._nudge_video_redraw()
                elif self.video_area is not None:
                    self.video_area.queue_draw()
        # Restauration après réduction / masquage : ré-affiche le frame de pause
        if event.changed_mask & (
            Gdk.WindowState.ICONIFIED | Gdk.WindowState.WITHDRAWN
        ):
            now_hidden = bool(
                event.new_window_state
                & (Gdk.WindowState.ICONIFIED | Gdk.WindowState.WITHDRAWN)
            )
            if not now_hidden and self.current_is_video:
                GLib.timeout_add(50, self._refresh_video_surface)
        return False

    def toggle_video_fullscreen(self):
        if not (self.current_is_video or getattr(self, "lyrics_mode", False)):
            return
        # S'appuie sur l'état réel de la fenêtre pour éviter les désync
        really_fs = False
        try:
            win = self.get_window()
            if win is not None:
                really_fs = bool(win.get_state() & Gdk.WindowState.FULLSCREEN)
        except Exception:
            really_fs = self.is_fullscreen

        if really_fs or self.is_fullscreen:
            self.unfullscreen()
            # Restauration immédiate (ne pas attendre uniquement window-state-event)
            self._restore_ui_after_fullscreen()
        else:
            self._enter_video_fullscreen()

    def _apply_fullscreen_ui_layout(self):
        """Masque tout sauf la zone vidéo (utilisé en entrée plein écran)."""
        try:
            if hasattr(self, "top_bar"):
                self.top_bar.hide()
            if hasattr(self, "bottom_bar"):
                self.bottom_bar.hide()
            if hasattr(self, "playlist_box"):
                self.playlist_box.hide()
            if hasattr(self, "video_overlay"):
                self.video_overlay.show()
            self.video_area.show()
            self.video_area.set_size_request(-1, -1)
            # Donne tout l'espace à la vidéo
            alloc = self.get_allocation()
            h = max(alloc.height, self.get_allocated_height() or 800)
            self.content_paned.set_position(max(h - 2, 400))
        except Exception as e:
            print("Erreur layout plein écran:", e, file=sys.stderr)

    def _enter_video_fullscreen(self):
        """Plein écran dédié à la vidéo : masque le reste de l'UI."""
        try:
            if self._fullscreen_ui_state is None:
                self._fullscreen_ui_state = {
                    "paned_pos": self.content_paned.get_position(),
                }
            self._apply_fullscreen_ui_layout()
            self.fullscreen()
            self.is_fullscreen = True
            self._cache_video_window_handle()
            self._nudge_video_redraw()
            self._reset_cursor_hide_timer()
        except Exception as e:
            print("Erreur entrée plein écran:", e, file=sys.stderr)

    def _restore_ui_after_fullscreen(self):
        """Restaure l'UI après sortie du plein écran."""
        if self._fullscreen_ui_state is None and not self.is_fullscreen:
            return
        # Empêche la récursion show_video_area(False) → unfullscreen → ici
        self._fullscreen_ui_state = None
        self.is_fullscreen = False
        try:
            if hasattr(self, "top_bar"):
                self.top_bar.show()
            if hasattr(self, "bottom_bar"):
                self.bottom_bar.show()
            if hasattr(self, "playlist_box"):
                self.playlist_box.show()
            if self.current_is_video:
                if hasattr(self, "video_overlay"):
                    self.video_overlay.show()
                self.video_area.show()
                # Réapplique le partage vidéo / playlist (≥ 6 lignes)
                self._apply_video_playlist_split()
                GLib.idle_add(self._apply_video_playlist_split)
            elif getattr(self, "lyrics_mode", False) and getattr(
                self, "lyrics_enabled", False
            ):
                # Ne pas appeler show_video_area(False) : ça masque les paroles
                try:
                    self._refresh_lyrics_for_current()
                except Exception:
                    if hasattr(self, "video_overlay"):
                        self.video_overlay.show()
                    if self.video_area is not None:
                        self.video_area.show()
                        self.video_area.set_size_request(-1, 120)
                        self.video_area.queue_draw()
                    try:
                        self._apply_video_playlist_split()
                        GLib.idle_add(self._apply_video_playlist_split)
                    except Exception:
                        pass
            else:
                self.show_video_area(False)
            self._show_cursor()
            self._hide_fs_controls()
            self._cache_video_window_handle()
            self._nudge_video_redraw()
            # Force un redraw opaque pour éviter le fantôme après sortie FS
            if self.video_area is not None:
                self.video_area.queue_draw()
        except Exception as e:
            print("Erreur restauration UI:", e, file=sys.stderr)
            self._show_cursor()

    def _playlist_min_height_for_rows(self, rows=6):
        """Hauteur minimale (px) pour afficher au moins `rows` lignes de playlist."""
        # ~26 px par ligne TreeView + barre de recherche + en-têtes de colonnes
        s = getattr(self, "_ui_s", None)
        if s is not None:
            row_h = s(26)
            chrome = s(52)
        else:
            row_h = 26
            chrome = 52
        return rows * row_h + chrome

    def _apply_video_playlist_split(self):
        """Répartit le paned : vidéo en haut, au moins 6 lignes de playlist en bas.

        Utilise la hauteur réelle du paned (pas celle de la fenêtre entière),
        sinon la playlist se retrouve écrasée sous la zone vidéo.
        """
        if (
            not (
                getattr(self, "current_is_video", False)
                or getattr(self, "lyrics_mode", False)
            )
            or self.is_fullscreen
        ):
            return False
        if self.content_paned is None:
            return False

        playlist_min = self._playlist_min_height_for_rows(6)
        try:
            if hasattr(self, "playlist_box") and self.playlist_box is not None:
                # Minimum seulement — jamais de hauteur max fixe, sinon le
                # ScrolledWindow ne peut pas utiliser tout l'espace alloué
                # par le paned et le bas de liste devient inaccessible.
                self.playlist_box.set_size_request(-1, playlist_min)
        except Exception:
            pass

        # Hauteur du paned uniquement (hors barres d'outils / infos)
        paned_h = self.content_paned.get_allocated_height()
        if paned_h <= 1:
            # Fallback si le layout n'est pas encore calculé
            win_h = self.get_allocated_height() or 600
            paned_h = max(320, win_h - 160)

        # Laisse au moins playlist_min pour le bas
        video_pos = max(120, paned_h - playlist_min)
        # Sur grande fenêtre : ne pas dépasser ~68 % pour la vidéo
        video_pos = min(video_pos, max(120, int(paned_h * 0.68)))
        # Recale si la contrainte 68 % grignote la playlist
        if paned_h - video_pos < playlist_min:
            video_pos = max(100, paned_h - playlist_min)

        try:
            # size_request minimal seulement : le paned pilote la répartition
            self.video_area.set_size_request(-1, 120)
            self.content_paned.set_position(video_pos)
        except Exception:
            pass
        # Force le recalcul des adjustments du ScrolledWindow (après un
        # resize paned, upper peut rester bloqué sur l'ancienne hauteur).
        try:
            self._refresh_playlist_scroll_adjustments()
        except Exception:
            pass
        return False

    def _refresh_playlist_scroll_adjustments(self):
        """Recalcule les barres de défilement de la playlist.

        Nécessaire après ajout de lignes ou redimensionnement du paned
        vidéo : sinon upper reste trop bas et les derniers fichiers sont
        inaccessibles alors qu'ils sont bien dans le modèle.
        """
        scroll = getattr(self, "playlist_scroll", None)
        tree = getattr(self, "tree", None)
        if scroll is None or tree is None:
            return False
        try:
            tree.queue_resize()
            scroll.queue_resize()
        except Exception:
            pass
        try:
            vadj = scroll.get_vadjustment()
            if vadj is not None:
                # Déclenche la mise à jour upper/page_size
                vadj.changed()
                upper = float(vadj.get_upper() or 0)
                page = float(vadj.get_page_size() or 0)
                value = float(vadj.get_value() or 0)
                # Si on était collé en bas, rester en bas après croissance
                if upper > page and value + page >= upper - 4.0:
                    vadj.set_value(max(0.0, upper - page))
        except Exception:
            pass
        return False

    def _scroll_playlist_to_path(self, child_path):
        """Fait défiler jusqu'à une ligne du ListStore (chemin enfant)."""
        try:
            if child_path is None or getattr(self, "tree", None) is None:
                return False
            fpath = child_path
            try:
                if getattr(self, "playlist_filter", None) is not None:
                    converted = self.playlist_filter.convert_child_path_to_path(
                        child_path
                    )
                    if converted is not None:
                        fpath = converted
            except Exception:
                pass
            if fpath is None:
                return False
            self.tree.scroll_to_cell(fpath, None, True, 1.0, 0.0)
            try:
                self.tree.set_cursor(fpath, None, False)
            except Exception:
                pass
        except Exception:
            pass
        return False

    def _scroll_playlist_to_end(self):
        """Affiche la dernière ligne visible de la playlist filtrée."""
        try:
            filt = getattr(self, "playlist_filter", None)
            if filt is None:
                return False
            n = filt.iter_n_children(None)
            if n <= 0:
                return False
            path = Gtk.TreePath.new_from_indices([n - 1])
            self.tree.scroll_to_cell(path, None, True, 1.0, 0.0)
            self._refresh_playlist_scroll_adjustments()
        except Exception:
            try:
                self._refresh_playlist_scroll_adjustments()
            except Exception:
                pass
        return False

    # ========================================================
    # PAROLES (LYRICS)
    # ========================================================

    def toggle_lyrics(self, menuitem=None):
        if menuitem is not None and hasattr(menuitem, "get_active"):
            self.lyrics_enabled = bool(menuitem.get_active())
        else:
            self.lyrics_enabled = not getattr(self, "lyrics_enabled", False)
        if self.lyrics_enabled:
            self.status_label.set_text(self.tr("Paroles activées"))
            self._refresh_lyrics_for_current()
        else:
            self.status_label.set_text(self.tr("Paroles désactivées"))
            self._hide_lyrics_display()
        try:
            self.save_state()
        except Exception:
            pass

    def _hide_lyrics_display(self):
        was = bool(getattr(self, "lyrics_mode", False))
        self.lyrics_mode = False
        self.lyrics_data = None
        self.lyrics_current_index = -1
        path = getattr(self, "current_path", None)
        real_video = bool(
            path
            and (
                is_video_file(path)
                or is_dvd(path)
                or is_bluray(path)
                or (
                    is_stream(path)
                    and (
                        getattr(self, "bouquet_active", False)
                        or getattr(self, "_prefer_video", False)
                    )
                )
            )
        )
        if was and not real_video:
            try:
                self.show_video_area(False)
            except Exception:
                pass
        try:
            if self.video_area is not None:
                self.video_area.queue_draw()
        except Exception:
            pass

    def _refresh_lyrics_for_current(self):
        path = getattr(self, "current_path", None)
        if not getattr(self, "lyrics_enabled", False):
            self._hide_lyrics_display()
            return
        if not path or not is_pure_audio_file(path):
            self._hide_lyrics_display()
            return
        data = load_lyrics_for_file(path)
        self.lyrics_data = data
        self.lyrics_current_index = -1
        if data is None:
            self.lyrics_mode = False
            try:
                self.show_video_area(False)
            except Exception:
                pass
            self.status_label.set_text(self.tr("Aucune parole trouvée"))
            return
        self.lyrics_mode = True
        try:
            if hasattr(self, "video_overlay"):
                self.video_overlay.show_all()
                self.video_overlay.show()
            if self.video_area is not None:
                self.video_area.show()
                self.video_area.set_size_request(-1, 120)
            self.current_is_video = False
            try:
                self._apply_video_playlist_split()
                GLib.idle_add(self._apply_video_playlist_split)
            except Exception:
                pass
            if hasattr(self, "osd_label"):
                self.osd_label.hide()
        except Exception as e:
            print("lyrics show area:", e, file=sys.stderr)
        try:
            if self.video_area is not None:
                self.video_area.queue_draw()
        except Exception:
            pass
        self._update_lyrics_index(
            getattr(self, "_last_known_position", 0.0) or 0.0
        )

    def _update_lyrics_index(self, position_sec):
        data = getattr(self, "lyrics_data", None)
        if not data or not data.get("timed"):
            return
        lines = data.get("lines") or []
        if not lines:
            return
        idx = -1
        for i, (sec, _txt) in enumerate(lines):
            if sec is not None and sec <= position_sec + 0.05:
                idx = i
            else:
                break
        if idx != self.lyrics_current_index:
            self.lyrics_current_index = idx
            try:
                if self.video_area is not None:
                    self.video_area.queue_draw()
            except Exception:
                pass

    def _draw_lyrics(self, widget, cr):
        allocation = widget.get_allocation()
        w = max(1, allocation.width)
        h = max(1, allocation.height)
        cr.set_source_rgb(0.04, 0.05, 0.07)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        data = getattr(self, "lyrics_data", None)
        if not data or not data.get("lines"):
            self._cairo_center_text(
                cr, w, h, self.tr("Aucune parole trouvée"), 22, (0.55, 0.58, 0.62)
            )
            return

        lines = data["lines"]
        timed = bool(data.get("timed"))
        margin_x = max(24, int(w * 0.08))
        line_h = max(28, int(h * 0.055))
        font_size = max(14, min(32, int(line_h * 0.72)))
        active_size = int(font_size * 1.25)

        if timed:
            cur = getattr(self, "lyrics_current_index", -1)
            center_y = h * 0.45
            for i, (_sec, txt) in enumerate(lines):
                y = center_y + (i - max(0, cur)) * line_h
                if y < -line_h or y > h + line_h:
                    continue
                is_active = i == cur
                if is_active:
                    color = (0.95, 0.97, 1.0)
                    size = active_size
                    weight = cairo.FONT_WEIGHT_BOLD
                else:
                    dist = abs(i - cur) if cur >= 0 else i
                    alpha = max(0.25, 1.0 - dist * 0.18)
                    color = (
                        0.55 * alpha + 0.2,
                        0.58 * alpha + 0.2,
                        0.62 * alpha + 0.22,
                    )
                    size = font_size
                    weight = cairo.FONT_WEIGHT_NORMAL
                self._cairo_draw_line(
                    cr, txt, margin_x, y, w - 2 * margin_x, size, color, weight
                )
        else:
            n = len(lines)
            pos = float(getattr(self, "_last_known_position", 0.0) or 0.0)
            speed = line_h / 3.2
            offset = (pos * speed) % max(line_h, n * line_h + h * 0.5)
            start_y = h * 0.25 - offset
            for i, (_sec, txt) in enumerate(lines):
                y = start_y + i * line_h
                if y < -line_h or y > h + line_h:
                    continue
                mid = h * 0.45
                dist = abs(y - mid) / max(1.0, h * 0.4)
                alpha = max(0.3, 1.0 - dist)
                color = (
                    0.75 * alpha + 0.15,
                    0.78 * alpha + 0.15,
                    0.82 * alpha + 0.18,
                )
                size = font_size if dist > 0.15 else active_size
                weight = (
                    cairo.FONT_WEIGHT_BOLD
                    if dist <= 0.15
                    else cairo.FONT_WEIGHT_NORMAL
                )
                self._cairo_draw_line(
                    cr, txt, margin_x, y, w - 2 * margin_x, size, color, weight
                )

    def _cairo_draw_line(self, cr, text, x, y, max_width, size, color, weight):
        try:
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, weight)
            cr.set_font_size(size)
            cr.set_source_rgb(*color)
            te = cr.text_extents(text)
            draw = text
            if te.width > max_width and max_width > 20:
                while draw and cr.text_extents(draw + "…").width > max_width:
                    draw = draw[:-1]
                draw = draw + "…" if draw != text else text
                te = cr.text_extents(draw)
            tx = x + max(0, (max_width - te.width) / 2.0)
            cr.move_to(tx, y + size * 0.35)
            cr.show_text(draw)
        except Exception:
            pass

    def _cairo_center_text(self, cr, w, h, text, size, color):
        try:
            cr.select_font_face(
                "Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL
            )
            cr.set_font_size(size)
            cr.set_source_rgb(*color)
            te = cr.text_extents(text)
            cr.move_to((w - te.width) / 2.0, (h + te.height) / 2.0)
            cr.show_text(text)
        except Exception:
            pass

    def show_video_area(self, show=True):
        if show:
            self.lyrics_mode = False
            self.lyrics_data = None
            self.lyrics_current_index = -1
            self.current_is_video = True
            self._set_spectrum_active_for_media(True)
            # En plein écran : ne pas réafficher playlist / barres
            if self.is_fullscreen:
                self._apply_fullscreen_ui_layout()
                self._cache_video_window_handle()
                return
            if hasattr(self, "video_overlay"):
                self.video_overlay.show_all()
                self.video_overlay.show()
            self.video_area.show()
            if hasattr(self, "osd_label"):
                self.osd_label.hide()
            # Répartition différée : besoin des allocations GTK réelles
            self._apply_video_playlist_split()
            GLib.idle_add(self._apply_video_playlist_split)
            # Laisse GTK réaliser/mapper la DrawingArea avant le 1er frame
            try:
                while Gtk.events_pending():
                    Gtk.main_iteration_do(False)
            except Exception:
                pass
            self._cache_video_window_handle()
            self._apply_video_overlay_handle()
        else:
            if hasattr(self, "video_overlay"):
                self.video_overlay.hide()
            self.video_area.hide()
            self.video_area.set_size_request(-1, 0)
            try:
                if hasattr(self, "playlist_box") and self.playlist_box is not None:
                    self.playlist_box.set_size_request(-1, -1)
            except Exception:
                pass
            try:
                self.content_paned.set_position(0)
            except Exception:
                pass
            self.current_is_video = False
            self._set_spectrum_active_for_media(False)
            self._release_pause_pixbuf()
            if self.is_fullscreen:
                self.unfullscreen()
                self._restore_ui_after_fullscreen()
        try:
            self._update_colorimetry_menu_sensitivity()
        except Exception:
            pass


    def set_subtitles_enabled(self, enabled):
        """Active/désactive l'affichage des sous-titres.

        Important : on ne modifie JAMAIS playbin.flags en cours de lecture
        (basculer TEXT pendant PLAYING/PAUSED reconfigure le pipeline et
        fige l'image). Le flag TEXT reste allumé en permanence ; on coupe
        l'affichage via current-text = -1 uniquement.
        """
        self.subtitles_enabled = bool(enabled)
        if not enabled:
            self.current_subtitle_index = -1
            if self._custom_pipeline is not None:
                try:
                    self._set_custom_subtitle_active(-1)
                except Exception:
                    pass
            else:
                try:
                    self.player.set_property("current-text", -1)
                except Exception:
                    pass
        # Ne pas toucher à flags ici.

    def _clear_external_suburi(self):
        """Retire le suburi externe du playbin (sans changer l'état de lecture)."""
        try:
            self.player.set_property("suburi", None)
        except Exception:
            try:
                self.player.set_property("suburi", "")
            except Exception:
                pass
        self._active_external_sub_path = None

    def _ensure_playbin_text_flag(self):
        """Garantit le flag TEXT de playbin (sous-titres embarqués + suburi)."""
        try:
            flags = self.player.get_property("flags")
            TEXT = 1 << 2
            if not (flags & TEXT):
                self.player.set_property("flags", flags | TEXT)
        except Exception:
            pass

    def _select_playbin_external_text_track(self):
        """Choisit la piste texte correspondant au suburi externe.

        Après application de suburi, playbin ajoute souvent la piste en
        *dernière* position (après les pistes embarquées). Forcer
        current-text=0 sélectionnait alors une piste embarquée (ou rien).
        """
        try:
            n = int(self.player.get_property("n-text") or 0)
        except Exception:
            n = 0
        if n <= 0:
            try:
                self.player.set_property("current-text", 0)
            except Exception:
                pass
            return False
        # Préférer la dernière piste (suburi ajouté en queue)
        target = n - 1
        try:
            self.player.set_property("current-text", target)
        except Exception:
            try:
                self.player.set_property("current-text", 0)
            except Exception:
                pass
        return False

    def _apply_external_suburi(self, path, restore_position=True):
        """Charge un fichier de sous-titres externe via suburi (playbin).

        suburi n'est pris en compte de façon fiable qu'en READY (ou NULL) :
        un simple PAUSED laissait souvent le fichier listé au menu mais
        jamais affiché. On repasse en READY, on pose suburi, on reprend
        la lecture, puis on sélectionne la bonne piste texte (souvent la
        dernière) et on seek pour resynchroniser les timestamps SRT.
        """
        if not path or not os.path.isfile(path):
            return False

        was_playing = self.is_playing and not self.is_paused
        was_paused = self.is_playing and self.is_paused
        position = float(self._last_known_position or 0.0) if restore_position else 0.0

        try:
            sub_uri = GLib.filename_to_uri(os.path.abspath(path), None)
        except Exception as e:
            print("URI sous-titre externe impossible :", e, file=sys.stderr)
            return False

        # Conserver l'URI média (READY la garde ; NULL l'effacerait)
        media_uri = None
        try:
            media_uri = self.player.get_property("uri")
        except Exception:
            media_uri = None
        if not media_uri and self.current_path:
            try:
                media_uri = path_to_uri(self.current_path)
            except Exception:
                media_uri = None

        try:
            # READY : reconfigure le pads texte sans tout détruire
            self.player.set_state(Gst.State.READY)
            self.player.get_state(int(2 * Gst.SECOND))

            self._ensure_playbin_text_flag()

            if media_uri:
                try:
                    self.player.set_property("uri", media_uri)
                except Exception:
                    pass

            # Reset puis pose du suburi
            try:
                self.player.set_property("suburi", None)
            except Exception:
                try:
                    self.player.set_property("suburi", "")
                except Exception:
                    pass
            try:
                self.player.set_property("suburi", sub_uri)
            except Exception as e:
                print("set suburi:", e, file=sys.stderr)
                return False

            # Encodage courant des .srt européens
            for enc in ("UTF-8", "utf8", "ISO-8859-1", "Windows-1252"):
                try:
                    self.player.set_property("subtitle-encoding", enc)
                    break
                except Exception:
                    pass

            self._active_external_sub_path = path
            self.subtitles_enabled = True

            # Remonter en PAUSED d'abord pour que n-text se peuple
            self.player.set_state(Gst.State.PAUSED)
            self.player.get_state(int(3 * Gst.SECOND))

            # Sélection immédiate + retries (pistes parfois tardives)
            self._select_playbin_external_text_track()

            if was_playing:
                self.player.set_state(Gst.State.PLAYING)
                self.is_playing = True
                self.is_paused = False
                try:
                    self.play_button.set_label("❚❚")
                except Exception:
                    pass
            elif was_paused or self.current_path:
                self.player.set_state(Gst.State.PAUSED)
                self.is_playing = True
                self.is_paused = True
                try:
                    self.play_button.set_label("▶")
                except Exception:
                    pass
            else:
                self.player.set_state(Gst.State.NULL)

            def _after_suburi_ready(retry=0):
                try:
                    self._select_playbin_external_text_track()
                except Exception:
                    pass
                if restore_position and position > 0.05:
                    try:
                        self.player.seek_simple(
                            Gst.Format.TIME,
                            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                            int(position * Gst.SECOND),
                        )
                        self._last_known_position = position
                    except Exception:
                        pass
                # Deuxième tentative si n-text était encore 0
                if retry < 2:
                    try:
                        n = int(self.player.get_property("n-text") or 0)
                    except Exception:
                        n = 0
                    if n <= 0:
                        GLib.timeout_add(
                            400, lambda: _after_suburi_ready(retry + 1) or False
                        )
                return False

            GLib.timeout_add(250, lambda: _after_suburi_ready(0) or False)
            GLib.timeout_add(900, lambda: _after_suburi_ready(1) or False)

            return True
        except Exception as e:
            print("Erreur application suburi :", e, file=sys.stderr)
            return False

    def _prepare_external_subtitles_for(self, filename):
        """Scanne le dossier de la vidéo et prépare un suburi initial si besoin."""
        self.external_subtitles = []
        self._active_external_sub_path = None

        try:
            self.player.set_property("suburi", None)
        except Exception:
            try:
                self.player.set_property("suburi", "")
            except Exception:
                pass

        if not filename or is_stream(filename) or is_cdda(filename):
            return
        try:
            if is_dvd(filename) or is_bluray(filename):
                return
        except Exception:
            pass
        if not (is_video_file(filename) or getattr(self, "current_is_video", False)):
            if not (isinstance(filename, str) and os.path.isfile(filename)):
                return

        try:
            self.external_subtitles = find_external_subtitles(filename) or []
        except Exception as e:
            print("scan sous-titres externes:", e, file=sys.stderr)
            self.external_subtitles = []

        if not self.external_subtitles:
            return

        # Uniquement enregistrer dans le menu — pas d'auto-chargement suburi.
        # L'auto-suburi au démarrage pouvait figer le pipeline (READY) et
        # n'est pas lié à la langue UI ; le choix reste manuel.
        try:
            self._register_external_subtitle_tracks()
        except Exception:
            pass


    def _pick_preferred_external_subtitle(self, external_list):
        """Choisit un sous-titre externe sans tenir compte de la langue de l'UI.

        Ordre neutre : fichier sans code langue, sinon le premier de la liste.
        La langue de l'application ne doit jamais influencer le média (MKV).
        """
        if not external_list:
            return None
        for item in external_list:
            if not item.get("lang"):
                return item
        return external_list[0]


    def _register_external_subtitle_tracks(self):
        """Ajoute les .srt/.ass du dossier aux pistes du menu (playbin ou custom)."""
        active = getattr(self, "_active_external_sub_path", None)
        for item in getattr(self, "external_subtitles", None) or []:
            path = item.get("path")
            if not path:
                continue
            label = item.get("label") or os.path.basename(path)
            if not str(label).endswith("]"):
                label = "%s [%s]" % (label, os.path.basename(path))
            # Évite les doublons
            if any(
                isinstance(t, dict)
                and t.get("kind") == "external"
                and t.get("path") == path
                for t in (self.subtitle_tracks or [])
            ):
                continue
            self.subtitle_tracks.append({
                "kind": "external",
                "index": None,
                "path": path,
                "label": label,
            })
            if active and path == active and self.current_subtitle_index < 0:
                self.current_subtitle_index = len(self.subtitle_tracks) - 1

    def _unlink_custom_text_sink(self):
        """Délie tout ce qui alimente text_sink de l'overlay custom."""
        overlay = getattr(self, "_custom_sub_overlay", None)
        if overlay is None:
            return
        try:
            if getattr(self, "_custom_sub_overlay_is_text", True):
                text_sink = overlay.get_static_pad("text_sink")
            else:
                text_sink = overlay.get_static_pad("subtitle_sink")
        except Exception:
            text_sink = None
        if text_sink is None:
            return
        try:
            peer = text_sink.get_peer()
            if peer is not None:
                peer.unlink(text_sink)
        except Exception:
            pass
        # Ancienne chaîne externe
        for name in ("_custom_ext_sub_src", "_custom_ext_sub_q", "_custom_ext_sub_parse"):
            el = getattr(self, name, None)
            if el is None:
                continue
            try:
                pipe = self._custom_pipeline
                if pipe is not None and el.get_parent() is pipe:
                    el.set_state(Gst.State.NULL)
                    pipe.remove(el)
            except Exception:
                pass
            setattr(self, name, None)

    def _link_external_subtitle_custom(self, path):
        """Branche un fichier .srt/.vtt/.ass sur l'overlay du pipeline MKV custom.

        playbin.suburi est ignoré par le pipeline custom : il faut une chaîne
        filesrc → subparse → textoverlay.
        """
        pipe = getattr(self, "_custom_pipeline", None)
        overlay = getattr(self, "_custom_sub_overlay", None)
        if pipe is None or overlay is None:
            return False
        if not path or not os.path.isfile(path):
            return False

        # Ne pas écraser une piste embarquée déjà choisie sauf si demandé
        self._unlink_custom_text_sink()
        # Aussi délier queue sous-titres demux si présente
        try:
            q = getattr(self, "_custom_sub_queue", None)
            if q is not None:
                qsrc = q.get_static_pad("src")
                if qsrc is not None:
                    peer = qsrc.get_peer()
                    if peer is not None:
                        qsrc.unlink(peer)
        except Exception:
            pass

        filesrc = Gst.ElementFactory.make("filesrc", "mkv-ext-sub-src")
        queue = Gst.ElementFactory.make("queue", "mkv-ext-sub-q")
        parser = Gst.ElementFactory.make("subparse", "mkv-ext-subparse")
        if not filesrc or not queue:
            return False
        try:
            filesrc.set_property("location", os.path.abspath(path))
        except Exception:
            return False
        try:
            queue.set_property("max-size-time", int(2 * Gst.SECOND))
            queue.set_property("max-size-buffers", 20)
        except Exception:
            pass

        try:
            pipe.add(filesrc)
            pipe.add(queue)
            if parser is not None:
                pipe.add(parser)
        except Exception as e:
            print("ext sub add:", e, file=sys.stderr)
            return False

        try:
            if not filesrc.link(queue):
                return False
            text_sink = None
            if getattr(self, "_custom_sub_overlay_is_text", True):
                text_sink = overlay.get_static_pad("text_sink")
                if text_sink is None:
                    try:
                        text_sink = overlay.get_request_pad("text_sink")
                    except Exception:
                        text_sink = None
            else:
                text_sink = overlay.get_static_pad("subtitle_sink")
            if text_sink is None:
                return False
            ok = False
            if parser is not None:
                if queue.link(parser):
                    srcp = parser.get_static_pad("src")
                    if srcp is not None and srcp.link(text_sink) == Gst.PadLinkReturn.OK:
                        ok = True
            if not ok:
                srcp = queue.get_static_pad("src")
                if srcp is not None and srcp.link(text_sink) == Gst.PadLinkReturn.OK:
                    ok = True
            if not ok:
                return False
        except Exception as e:
            print("ext sub link:", e, file=sys.stderr)
            return False

        # Encodage SRT (subparse)
        if parser is not None:
            for enc in ("UTF-8", "utf-8", "ISO-8859-1", "Windows-1252"):
                try:
                    parser.set_property("subtitle-encoding", enc)
                    break
                except Exception:
                    pass

        for el in (filesrc, queue, parser):
            if el is None:
                continue
            try:
                el.sync_state_with_parent()
            except Exception:
                pass
        try:
            # Afficher immédiatement ; wait-text=False évite de bloquer la vidéo
            overlay.set_property("silent", False)
            if overlay.find_property("wait-text") is not None:
                overlay.set_property("wait-text", False)
        except Exception:
            pass

        self._custom_ext_sub_src = filesrc
        self._custom_ext_sub_q = queue
        self._custom_ext_sub_parse = parser
        self._custom_sub_linked = True
        self._active_external_sub_path = path
        self.subtitles_enabled = True
        if DEBUG_MYOSTOOX:
            print(
                "Myostoox: sous-titres externes → %s" % os.path.basename(path),
                file=sys.stderr,
            )
        return True

    def _maybe_attach_external_to_custom(self):
        """Après démarrage MKV custom : enregistre les .srt dans le menu uniquement.

        Plus d'auto-branchement d'un fichier externe (pouvait figer le pipeline).
        L'utilisateur choisit via le menu Sous-titres.
        """
        try:
            self._register_external_subtitle_tracks()
        except Exception:
            pass
        return False




    def _discover_subtitle_languages(self, filepath):
        """Retourne {stream_id: code_langue} pour les pistes sous-titres (Discoverer)."""
        result = {}
        if GstPbutils is None or not filepath:
            return result
        try:
            uri = Gst.filename_to_uri(os.path.abspath(filepath))
        except Exception:
            return result
        try:
            disc = GstPbutils.Discoverer.new(int(6 * Gst.SECOND))
            info = disc.discover_uri(uri)
        except Exception as e:
            print("Discoverer sous-titres:", e, file=sys.stderr)
            return result
        try:
            streams = info.get_subtitle_streams() or []
        except Exception:
            streams = []
        for stream in streams:
            lang = None
            try:
                tags = stream.get_tags()
            except Exception:
                tags = None
            if tags is not None:
                for tkey in (Gst.TAG_LANGUAGE_CODE, Gst.TAG_LANGUAGE_NAME):
                    try:
                        ok, val = tags.get_string(tkey)
                        if ok and val:
                            lang = self._normalize_lang_code(val) or val
                            break
                    except Exception:
                        pass
            if not lang:
                try:
                    if hasattr(stream, "get_language"):
                        raw = stream.get_language()
                        if raw:
                            lang = self._normalize_lang_code(raw) or raw
                except Exception:
                    pass
            sid = None
            try:
                sid = stream.get_stream_id()
            except Exception:
                sid = None
            if sid:
                result[sid] = lang
        return result

    def _normalize_lang_code(self, raw):
        """Normalise un code/nom de langue vers une abréviation courte (fr, en…)."""
        if not raw:
            return None
        s = str(raw).strip()
        if not s or s.lower() in ("und", "unk", "unknown", ""):
            return None
        low = s.casefold()
        # Déjà un code court
        if len(s) <= 3 and s.isalpha():
            # ISO 639-2 → 639-1 fréquent
            map3 = {
                "fre": "fr", "fra": "fr", "eng": "en", "ger": "de", "deu": "de",
                "spa": "es", "ita": "it", "por": "pt", "dut": "nl", "nld": "nl",
                "pol": "pl", "swe": "sv", "rus": "ru", "jpn": "ja", "chi": "zh",
                "zho": "zh", "kor": "ko", "ara": "ar", "hin": "hi", "tur": "tr",
            }
            if low in map3:
                return map3[low]
            return low[:2] if len(low) >= 2 else low
        names = {
            "french": "fr", "français": "fr", "francais": "fr",
            "english": "en", "anglais": "en",
            "german": "de", "deutsch": "de", "allemand": "de",
            "spanish": "es", "español": "es", "espanol": "es", "castellano": "es",
            "italian": "it", "italiano": "it",
            "portuguese": "pt", "portugais": "pt", "português": "pt",
            "dutch": "nl", "nederlands": "nl", "néerlandais": "nl",
            "polish": "pl", "polski": "pl", "polonais": "pl",
            "swedish": "sv", "svenska": "sv", "suédois": "sv",
            "russian": "ru", "русский": "ru", "russe": "ru",
            "japanese": "ja", "japonais": "ja",
            "chinese": "zh", "chinois": "zh",
        }
        if low in names:
            return names[low]
        # Prefixe type "fr-FR"
        if "-" in s or "_" in s:
            return self._normalize_lang_code(s.replace("_", "-").split("-")[0])
        return s[:8]

    def _delayed_subtitle_lang_probe(self, pad, track_index):
        """Relit les tags langue une fois le demux stabilisé."""
        try:
            lang = None
            # 1) Sticky TAG event
            try:
                event = pad.get_sticky_event(Gst.EventType.TAG, 0)
                if event is not None:
                    tags = event.parse_tag()
                    for tkey in (
                        Gst.TAG_LANGUAGE_CODE,
                        Gst.TAG_LANGUAGE_NAME,
                        Gst.TAG_TITLE,
                    ):
                        try:
                            ok, val = tags.get_string(tkey)
                            if ok and val:
                                if tkey == Gst.TAG_TITLE and len(str(val)) > 16:
                                    continue
                                lang = val
                                break
                        except Exception:
                            pass
            except Exception:
                pass
            # 2) Caps / structure du peer
            if not lang:
                try:
                    caps = pad.get_current_caps()
                    if caps is None:
                        caps = pad.query_caps(None)
                    if caps is not None and caps.get_size() > 0:
                        st = caps.get_structure(0)
                        for key in ("language", "language-code", "lang"):
                            if st.has_field(key):
                                ok, val = st.get_string(key)
                                if ok and val and val not in ("und", "unk", ""):
                                    lang = val
                                    break
                except Exception:
                    pass
            # 3) Discoverer, apparié par stream-id du pad (fiable, pas par ordre)
            if not lang:
                try:
                    sid = pad.get_stream_id()
                except Exception:
                    sid = None
                if sid:
                    lang = (getattr(self, "_custom_sub_lang_list", None) or {}).get(sid)
            if lang:
                short = self._normalize_lang_code(lang) or lang
                self._set_subtitle_track_label(track_index, short)
        except Exception:
            pass
        return False

    def _set_subtitle_track_label(self, track_index, lang):


        """Met à jour le label d'une piste sous-titre (tags tardifs)."""
        try:
            for t in self.subtitle_tracks or []:
                if (
                    isinstance(t, dict)
                    and t.get("kind") == "embedded"
                    and t.get("index") == track_index
                ):
                    t["label"] = lang
                    break
            # Rafraîchit aussi la liste custom si présente
            pads = getattr(self, "_custom_subtitle_pads", None) or []
            if 0 <= track_index < len(pads):
                entry = pads[track_index]
                pad = entry[0]
                is_text = entry[2] if len(entry) > 2 else True
                self._custom_subtitle_pads[track_index] = (pad, lang, is_text)
        except Exception:
            pass
        return False

    def refresh_subtitle_tracks(self):

        """Fusionne pistes intégrées (playbin) et fichiers externes détectés."""
        self.subtitle_tracks = []
        embedded_current = -1

        try:
            n_text = self.player.get_property("n-text")
            if n_text is not None and int(n_text) > 0:
                try:
                    cur = self.player.get_property("current-text")
                    embedded_current = cur if cur is not None else -1
                except Exception:
                    embedded_current = -1

                for i in range(int(n_text)):
                    lang = None
                    try:
                        tags = self.player.emit("get-text-tags", i)
                        if tags is not None:
                            ok, value = tags.get_string(Gst.TAG_LANGUAGE_CODE)
                            if ok and value:
                                lang = value
                            if not lang:
                                ok, value = tags.get_string(Gst.TAG_LANGUAGE_NAME)
                                if ok and value:
                                    lang = value
                            if not lang:
                                ok, value = tags.get_string("language-code")
                                if ok and value:
                                    lang = value
                    except Exception:
                        pass
                    label = lang if lang else self.tr("Piste ") + str(i + 1)
                    self.subtitle_tracks.append({
                        "kind": "embedded",
                        "index": i,
                        "path": None,
                        "label": label,
                    })
        except Exception as e:
            print("Erreur lecture pistes sous-titres intégrées:", e, file=sys.stderr)

        # Fichiers externes : affichés même s'ils sont déjà chargés en suburi
        # (étiquette distincte pour les différencier des pistes MKV).
        active_ext = self._active_external_sub_path
        for item in self.external_subtitles:
            path = item["path"]
            label = item["label"]
            # Marqueur discret pour l'origine fichier
            if not label.endswith("]"):
                label = f"{label} [{os.path.basename(path)}]"
            self.subtitle_tracks.append({
                "kind": "external",
                "index": None,
                "path": path,
                "label": label,
            })

        # Détermine l'entrée active dans la liste fusionnée
        self.current_subtitle_index = -1
        if not self.subtitles_enabled:
            return

        if active_ext:
            for i, track in enumerate(self.subtitle_tracks):
                if track["kind"] == "external" and track["path"] == active_ext:
                    self.current_subtitle_index = i
                    return

        if embedded_current >= 0:
            for i, track in enumerate(self.subtitle_tracks):
                if track["kind"] == "embedded" and track["index"] == embedded_current:
                    self.current_subtitle_index = i
                    return

    def _link_custom_subtitle_pad_now(self, pipe, pad, sidx, nice, sub_overlay):
        """Branche un pad sous-titre demux → queue → overlay (une seule fois)."""
        if getattr(self, "_custom_sub_linked", False):
            return False
        if pipe is None or pad is None or sub_overlay is None:
            return False
        queue = Gst.ElementFactory.make("queue", "mkv-squeue")
        if queue is None:
            return False
        try:
            queue.set_property("leaky", 2)
            queue.set_property("max-size-buffers", 50)
            queue.set_property("max-size-time", int(2 * Gst.SECOND))
        except Exception:
            pass
        # subparse gère SRT + SSA/ASS ; ssaparse en repli si disponible
        subparse = Gst.ElementFactory.make("subparse", "mkv-subparse")
        if subparse is None:
            subparse = Gst.ElementFactory.make("ssaparse", "mkv-ssaparse")
        try:
            pipe.add(queue)
            queue.sync_state_with_parent()
            if subparse is not None:
                pipe.add(subparse)
                subparse.sync_state_with_parent()
        except Exception as e:
            print("sub queue add:", e, file=sys.stderr)
            return False
        try:
            if pad.link(queue.get_static_pad("sink")) != Gst.PadLinkReturn.OK:
                return False
        except Exception:
            return False

        text_sink = None
        if getattr(self, "_custom_sub_overlay_is_text", True):
            text_sink = sub_overlay.get_static_pad("text_sink")
            if text_sink is None:
                try:
                    text_sink = sub_overlay.get_request_pad("text_sink")
                except Exception:
                    text_sink = None
        else:
            text_sink = sub_overlay.get_static_pad("subtitle_sink")
            if text_sink is None:
                try:
                    text_sink = sub_overlay.get_request_pad("subtitle_sink")
                except Exception:
                    text_sink = None

        ok = False
        if text_sink is not None:
            if subparse is not None and queue.link(subparse):
                srcp = subparse.get_static_pad("src")
                if (
                    srcp is not None
                    and srcp.link(text_sink) == Gst.PadLinkReturn.OK
                ):
                    ok = True
            if not ok:
                srcp = queue.get_static_pad("src")
                if (
                    srcp is not None
                    and srcp.link(text_sink) == Gst.PadLinkReturn.OK
                ):
                    ok = True
        if not ok:
            return False

        self._custom_sub_linked = True
        self._custom_sub_queue = queue
        self._custom_subtitle_index = sidx
        self._custom_sub_muted = False
        self.current_subtitle_index = 0
        for i, t in enumerate(self.subtitle_tracks or []):
            if (
                isinstance(t, dict)
                and t.get("kind") == "embedded"
                and t.get("index") == sidx
            ):
                self.current_subtitle_index = i
                break
        if DEBUG_MYOSTOOX:
            print(
            "Myostoox: sous-titres overlay GStreamer (%s)" % nice,
            file=sys.stderr,
        )
        return True

    def _link_preferred_custom_subtitle_while_paused(self):
        """Branche la piste demandée uniquement en état PAUSED (avant PLAYING)."""
        pref = getattr(self, "_preferred_custom_sub_index", -1)
        if pref is None or pref < 0:
            return False
        if not getattr(self, "subtitles_enabled", True):
            return False
        pipe = getattr(self, "_custom_pipeline", None)
        overlay = getattr(self, "_custom_sub_overlay", None)
        pads = getattr(self, "_custom_subtitle_pads", None) or []
        if pipe is None or overlay is None or not pads:
            return False
        if pref >= len(pads):
            return False
        entry = pads[pref]
        pad = entry[0]
        nice = entry[1] if len(entry) > 1 else "Sub"
        is_text = entry[2] if len(entry) > 2 else True
        if not is_text:
            return False
        if getattr(self, "_custom_sub_linked", False):
            try:
                overlay.set_property("silent", False)
            except Exception:
                pass
            return True
        ok = self._link_custom_subtitle_pad_now(pipe, pad, pref, nice, overlay)
        if ok:
            try:
                overlay.set_property("silent", False)
            except Exception:
                pass
        return ok

    def _link_best_subtitle_pad_deferred(self):
        """Compat : plus d'auto-lien différé (restait une source de freeze)."""
        self._custom_sub_wait_scheduled = False
        return False


    def _set_custom_subtitle_active(self, pad_index):
        """Bascule la piste sous-titre branchée sur textoverlay/subtitleoverlay."""
        pads = getattr(self, "_custom_subtitle_pads", None) or []
        queue = getattr(self, "_custom_sub_queue", None)
        overlay = getattr(self, "_custom_sub_overlay", None)

        def _text_sink():
            if overlay is None:
                return None
            if getattr(self, "_custom_sub_overlay_is_text", True):
                p = overlay.get_static_pad("text_sink")
                if p is None:
                    try:
                        p = overlay.get_request_pad("text_sink")
                    except Exception:
                        p = None
                return p
            return overlay.get_static_pad("subtitle_sink")

        def _unlink_all():
            # Délie queue ← demux pads
            if queue is not None:
                qsink = queue.get_static_pad("sink")
                if qsink is not None:
                    peer = qsink.get_peer()
                    if peer is not None:
                        try:
                            peer.unlink(qsink)
                        except Exception:
                            pass
            for entry in pads:
                p = entry[0] if entry else None
                if p is None:
                    continue
                try:
                    pr = p.get_peer()
                    if pr is not None:
                        p.unlink(pr)
                except Exception:
                    pass

        if pad_index < 0:
            self._custom_sub_muted = True
            self._custom_subtitle_index = -1
            _unlink_all()
            try:
                self._unlink_custom_text_sink()
            except Exception:
                pass
            try:
                if overlay is not None:
                    overlay.set_property("silent", True)
            except Exception:
                pass
            return

        if pad_index >= len(pads):
            return
        entry = pads[pad_index]
        new_pad = entry[0]
        lab = entry[1] if len(entry) > 1 else "Sub"
        is_text = entry[2] if len(entry) > 2 else True
        if not is_text:
            # PGS / VobSub : textoverlay ne sait pas les décoder → freeze
            self.status_label.set_text(
                self.tr("Sous-titres") + " — image non supportée (PGS/VobSub)"
            )
            return
        try:
            try:
                if overlay is not None:
                    overlay.set_property("silent", False)
            except Exception:
                pass
            _unlink_all()
            if queue is None:
                # Premier branchement (auto-link n'a pas eu lieu) : câbler maintenant
                pipe = getattr(self, "_custom_pipeline", None)
                if pipe is not None and overlay is not None:
                    self._custom_sub_linked = False
                    if self._link_custom_subtitle_pad_now(
                        pipe, new_pad, pad_index, lab, overlay
                    ):
                        self._custom_sub_muted = False
                else:
                    self._custom_subtitle_index = pad_index
                    self._custom_sub_muted = False
                return
            qsink = queue.get_static_pad("sink")
            if qsink is None:
                return
            ret = new_pad.link(qsink)
            if ret != Gst.PadLinkReturn.OK:
                if DEBUG_MYOSTOOX:
                    print(
                    "Myostoox: lien sous-titre pad %d échoué: %s"
                    % (pad_index, ret),
                    file=sys.stderr,
                )
                return
            self._custom_subtitle_index = pad_index
            self._custom_sub_muted = False
            if DEBUG_MYOSTOOX:
                print(
                "Myostoox: sous-titres → %s (pad %d)" % (lab, pad_index),
                file=sys.stderr,
            )
            # Pas de seek ici : un FLUSH après lien sous-titres provoquait
            # une boucle / saccade d'environ 1 s sur MKV.
        except Exception as e:
            print("custom subtitle switch:", e, file=sys.stderr)

    def set_subtitle_track(self, track_list_index):
        """Sélectionne une piste dans subtitle_tracks (-1 = désactiver).

        Playbin : uniquement current-text (jamais de flags / READY / seek).
        Pipeline custom : pause → lien/silent → play, sans seek ni rebuild.
        """
        try:
            # Ignore resélection identique (évite boucles menu RadioMenuItem)
            if (
                track_list_index is not None
                and track_list_index >= 0
                and track_list_index == getattr(self, "current_subtitle_index", -1)
                and getattr(self, "subtitles_enabled", False)
            ):
                return
            if getattr(self, "_subtitle_switch_busy", False):
                return
            self._subtitle_switch_busy = True
            try:
                self._set_subtitle_track_impl(track_list_index)
            finally:
                self._subtitle_switch_busy = False
        except Exception as e:
            self._subtitle_switch_busy = False
            print("Erreur sélection sous-titres:", e, file=sys.stderr)

    def _set_subtitle_track_impl(self, track_list_index):
        try:
            if track_list_index is None or track_list_index < 0:
                self.current_subtitle_index = -1
                self.subtitles_enabled = False
                if self._custom_pipeline is not None:
                    try:
                        ov = getattr(self, "_custom_sub_overlay", None)
                        if ov is not None:
                            ov.set_property("silent", True)
                    except Exception:
                        pass
                    self._custom_subtitle_index = -1
                else:
                    try:
                        self.player.set_property("current-text", -1)
                    except Exception:
                        pass
                self.status_label.set_text(self.tr("Sous-titres désactivés"))
                return

            if track_list_index >= len(self.subtitle_tracks):
                return

            track = self.subtitle_tracks[track_list_index]

            # ---- Pipeline MKV custom (E-AC3) ----
            if (
                self._custom_pipeline is not None
                and track.get("kind") == "embedded"
            ):
                if track.get("text") is False:
                    self.status_label.set_text(
                        self.tr("Sous-titres")
                        + " — image non supportée (PGS/VobSub)"
                    )
                    return
                self.subtitles_enabled = True
                pad_idx = int(track.get("index", 0))
                pipe = self._custom_pipeline
                was_playing = self.is_playing and not self.is_paused
                # Pause → lien (ou silent) → reprise. Aucun seek, pas de rebuild
                # (rebuild+seek provoquait une boucle d'~1 s).
                try:
                    pipe.set_state(Gst.State.PAUSED)
                    pipe.get_state(int(800 * Gst.MSECOND))
                except Exception:
                    pass
                try:
                    ov = getattr(self, "_custom_sub_overlay", None)
                    if ov is not None:
                        try:
                            ov.set_property("wait-text", False)
                        except Exception:
                            pass
                    if (
                        getattr(self, "_custom_sub_linked", False)
                        and getattr(self, "_custom_subtitle_index", -1) == pad_idx
                    ):
                        if ov is not None:
                            ov.set_property("silent", False)
                    else:
                        self._set_custom_subtitle_active(pad_idx)
                        if ov is not None:
                            ov.set_property("silent", False)
                except Exception as e:
                    print("custom sub select:", e, file=sys.stderr)
                if was_playing:
                    try:
                        pipe.set_state(Gst.State.PLAYING)
                    except Exception:
                        pass
                self.current_subtitle_index = track_list_index
                self.status_label.set_text(
                    self.tr("Sous-titres") + " : " + track["label"]
                )
                return

            # ---- Playbin : pistes intégrées ----
            if track.get("kind") == "embedded":
                self.subtitles_enabled = True
                # Retirer un suburi externe sans READY brutal si possible
                if self._active_external_sub_path:
                    try:
                        self._clear_external_suburi()
                    except Exception:
                        pass
                try:
                    self.player.set_property(
                        "current-text", int(track["index"])
                    )
                except Exception as e:
                    print("current-text:", e, file=sys.stderr)
                self.current_subtitle_index = track_list_index
                self.status_label.set_text(
                    self.tr("Sous-titres") + " : " + track["label"]
                )
                return

            # ---- Fichier externe (.srt / .ass) ----
            if track.get("kind") == "external":
                path = track.get("path")
                if not path:
                    return
                if self._custom_pipeline is not None:
                    # Pause → lien → seek (resync SRT) → reprise
                    pipe = self._custom_pipeline
                    was_playing = self.is_playing and not self.is_paused
                    position = float(getattr(self, "_last_known_position", 0) or 0)
                    try:
                        pipe.set_state(Gst.State.PAUSED)
                        pipe.get_state(int(2 * Gst.SECOND))
                    except Exception:
                        pass
                    ok = False
                    try:
                        ok = self._link_external_subtitle_custom(path)
                    except Exception as e:
                        print("ext sub custom:", e, file=sys.stderr)
                    # Seek obligatoire : sinon les timestamps SRT (depuis 0)
                    # sont déjà « en retard » si on n'est pas en début de média.
                    if ok and position > 0.15:
                        try:
                            pipe.seek_simple(
                                Gst.Format.TIME,
                                Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                                int(position * Gst.SECOND),
                            )
                            pipe.get_state(int(2 * Gst.SECOND))
                            self._last_known_position = position
                        except Exception as e:
                            print("ext sub seek:", e, file=sys.stderr)
                    if was_playing:
                        try:
                            pipe.set_state(Gst.State.PLAYING)
                        except Exception:
                            pass
                    if ok:
                        self.current_subtitle_index = track_list_index
                        self.status_label.set_text(
                            self.tr("Sous-titres") + " : " + track["label"]
                        )
                    return
                if self._apply_external_suburi(path):
                    self.current_subtitle_index = track_list_index
                    self.status_label.set_text(
                        self.tr("Sous-titres") + " : " + track["label"]
                    )
                    GLib.timeout_add(250, self._deferred_refresh_subtitles)
                return

        except Exception as e:
            print("Erreur sélection sous-titres:", e, file=sys.stderr)

    def _deferred_refresh_subtitles(self):
        self.refresh_subtitle_tracks()
        return False

    def refresh_audio_tracks(self):
        """Interroge les pistes audio (langues) disponibles dans le flux en cours."""
        self.audio_tracks = []
        self.current_audio_index = -1
        try:
            n_audio = self.player.get_property("n-audio")
            if n_audio is None or n_audio <= 0:
                return
            current = self.player.get_property("current-audio")
            self.current_audio_index = current if current is not None else -1
            for i in range(int(n_audio)):
                lang = None
                try:
                    tags = self.player.emit("get-audio-tags", i)
                    if tags is not None:
                        ok, value = tags.get_string(Gst.TAG_LANGUAGE_CODE)
                        if ok and value:
                            lang = value
                        if not lang:
                            ok, value = tags.get_string(Gst.TAG_LANGUAGE_NAME)
                            if ok and value:
                                lang = value
                        if not lang:
                            ok, value = tags.get_string("language-code")
                            if ok and value:
                                lang = value
                except Exception:
                    pass
                label = lang if lang else self.tr("Piste ") + str(i + 1)
                self.audio_tracks.append((i, label))
        except Exception as e:
            print("Erreur lecture pistes audio:", e, file=sys.stderr)

    def set_audio_track(self, index):
        """Sélectionne la piste audio (playbin ou pipeline MKV custom)."""
        if self._custom_pipeline is not None:
            self._set_custom_audio_track(index)
            return
        try:
            self.player.set_property("current-audio", index)
            self.current_audio_index = index
        except Exception as e:
            print("Erreur piste audio:", e, file=sys.stderr)

    def _set_custom_audio_track(self, index):
        """Rebranche une autre piste audio du demux sur la queue audio."""
        if not self._custom_audio_pads or self._custom_audio_queue is None:
            return
        if index < 0 or index >= len(self._custom_audio_pads):
            return
        if index == self._custom_audio_index:
            return
        try:
            qsink = self._custom_audio_queue.get_static_pad("sink")
            if qsink is None:
                return
            peer = qsink.get_peer()
            if peer is not None:
                peer.unlink(qsink)
            new_pad, label = self._custom_audio_pads[index]
            if new_pad.link(qsink) == Gst.PadLinkReturn.OK:
                self._custom_audio_index = index
                self.current_audio_index = index
                # Flush pour enchaîner proprement
                try:
                    self._custom_pipeline.seek_simple(
                        Gst.Format.TIME,
                        Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                        max(0, int(self._last_known_position * Gst.SECOND)),
                    )
                except Exception:
                    pass
                self.status_label.set_text(
                    self.tr("Piste audio") + " : " + label
                )
            else:
                # Revert
                if peer is not None:
                    peer.link(qsink)
        except Exception as e:
            print("Erreur bascule audio custom:", e, file=sys.stderr)


    def open_system_bluetooth(self, *args):
        """Ouvre le gestionnaire Bluetooth du bureau (raccourci système)."""
        commands = [
            # Blueman (LXQt / Lubuntu fréquent)
            ["blueman-manager"],
            ["blueman-applet"],
            # GNOME
            ["gnome-control-center", "bluetooth"],
            # KDE Plasma
            ["systemsettings5", "kcm_bluetooth"],
            ["systemsettings", "kcm_bluetooth"],
            # GNOME/GTK generic
            ["gnome-bluetooth"],
            # xfce / generic settings
            ["xfce4-settings-manager"],
            # Portals / settings
            ["unity-control-center", "bluetooth"],
        ]
        # URI settings (GNOME/modern)
        uri_cmds = [
            ["xdg-open", "settings://bluetooth"],
            ["xdg-open", "gnome-control-center:bluetooth"],
        ]

        for cmd in commands:
            try:
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self.status_label.set_text(self.tr("Bluetooth"))
                return
            except FileNotFoundError:
                continue
            except Exception:
                continue

        for cmd in uri_cmds:
            try:
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self.status_label.set_text(self.tr("Bluetooth"))
                return
            except Exception:
                continue

        # Dernier recours : page d'aide / message
        try:
            self.status_label.set_text(
                self.tr("Aucun gestionnaire Bluetooth trouvé")
            )
        except Exception:
            pass
        dialog = Gtk.MessageDialog(
            parent=self,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=self.tr("Bluetooth"),
        )
        dialog.format_secondary_text(
            self.tr(
                "Aucun gestionnaire Bluetooth détecté. "
                "Installez blueman ou ouvrez les réglages système."
            )
        )
        dialog.run()
        dialog.destroy()

    def capture_screenshot(self, *args):
        """Capture l'image vidéo courante et propose de l'enregistrer."""
        if not self.current_is_video or self.video_area is None:
            return
        window = self.video_area.get_window()
        if window is None:
            self.status_label.set_text(self.tr("Capture impossible"))
            return
        try:
            width = window.get_width()
            height = window.get_height()
            if width <= 0 or height <= 0:
                return
            pixbuf = Gdk.pixbuf_get_from_window(window, 0, 0, width, height)
            if pixbuf is None:
                self.status_label.set_text(self.tr("Capture impossible"))
                return
        except Exception as e:
            print("Erreur capture:", e, file=sys.stderr)
            self.status_label.set_text(self.tr("Capture impossible"))
            return

        dialog = Gtk.FileChooserDialog(
            title=self.tr("Enregistrer la capture"),
            parent=self,
            action=Gtk.FileChooserAction.SAVE
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.OK
        )
        dialog.set_do_overwrite_confirmation(True)

        # Dossier par défaut : Téléchargements
        try:
            downloads = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
            if downloads:
                dialog.set_current_folder(downloads)
            else:
                dialog.set_current_folder(os.path.expanduser("~/Downloads"))
        except Exception:
            dialog.set_current_folder(os.path.expanduser("~"))

        # Filtres de format
        filter_png = Gtk.FileFilter()
        filter_png.set_name("PNG (*.png)")
        filter_png.add_pattern("*.png")
        dialog.add_filter(filter_png)

        filter_jpg = Gtk.FileFilter()
        filter_jpg.set_name("JPEG (*.jpg)")
        filter_jpg.add_pattern("*.jpg")
        filter_jpg.add_pattern("*.jpeg")
        dialog.add_filter(filter_jpg)

        filter_bmp = Gtk.FileFilter()
        filter_bmp.set_name("BMP (*.bmp)")
        filter_bmp.add_pattern("*.bmp")
        dialog.add_filter(filter_bmp)

        # Nom de fichier par défaut
        from datetime import datetime
        default_name = "capture_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".png"
        dialog.set_current_name(default_name)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            path = dialog.get_filename()
            chosen_filter = dialog.get_filter()
            # Détermine l'extension selon le filtre ou le nom
            ext = os.path.splitext(path)[1].lower()
            if not ext:
                if chosen_filter == filter_jpg:
                    path += ".jpg"
                    ext = ".jpg"
                elif chosen_filter == filter_bmp:
                    path += ".bmp"
                    ext = ".bmp"
                else:
                    path += ".png"
                    ext = ".png"
            try:
                if ext in (".jpg", ".jpeg"):
                    pixbuf.savev(path, "jpeg", ["quality"], ["95"])
                elif ext == ".bmp":
                    pixbuf.savev(path, "bmp", [], [])
                else:
                    pixbuf.savev(path, "png", [], [])
                self.status_label.set_text(self.tr("Capture enregistrée"))
            except Exception as e:
                error = Gtk.MessageDialog(
                    parent=self,
                    flags=Gtk.DialogFlags.MODAL,
                    message_type=Gtk.MessageType.ERROR,
                    buttons=Gtk.ButtonsType.OK,
                    text=self.tr("Impossible d'enregistrer la capture.")
                )
                error.format_secondary_text(str(e))
                error.run()
                error.destroy()
        dialog.destroy()

    # ========================================================
    # LECTURE
    # ========================================================


    # ========================================================
    # PIPELINE MKV LOCAL (sans playbin) — correctif E-AC3 / crash
    # ========================================================

    def _should_use_custom_pipeline(self, path):
        """Pipeline custom pour E-AC3 (crash a52dec) et pour AV1 (av01).

        playbin + VA-API AV1 échoue souvent (élément présent mais GPU sans
        décodage AV1) alors que av1dec/dav1ddec fonctionnent. On force alors
        une chaîne explicite matroskademux → av1parse → av1dec, comme le
        gst-launch qui marche chez l'utilisateur.
        """
        if not isinstance(path, str):
            return False
        if is_stream(path) or is_cdda(path) or is_dvd(path) or is_bluray(path):
            return False
        if not os.path.isfile(path):
            return False
        if os.path.splitext(path)[1].lower() not in (".mkv", ".webm", ".mka"):
            return False
        if self._mkv_has_ac3_family_audio(path):
            return True
        if self._mkv_has_av1_video(path):
            return True
        return False

    def _mkv_probe_header(self, path, size=1024 * 1024):
        """Lit le début d'un fichier Matroska (CodecID en tête EBML/Tracks)."""
        try:
            with open(path, "rb") as f:
                return f.read(size)
        except Exception:
            return b""

    def _mkv_has_ac3_family_audio(self, path):
        """True uniquement pour E-AC3 (A_EAC3) — cas du crash a52dec + playbin.

        L'AC3 classique (A_AC3) est géré par playbin avec a52dec rétrogradé
        et avdec_ac3 ; le pipeline custom ne gère pas bien AC3+H.264.

        Sonde binaire légère uniquement (pas de Discoverer) : Discoverer sur
        le thread UI bloquait 1–3 s et pouvait figer le démarrage MKV.
        """
        data = self._mkv_probe_header(path)
        if not data:
            return False
        if b"A_EAC3" in data or b"A_EAC-3" in data:
            return True
        return False

    def _mkv_has_av1_video(self, path):
        """True si le conteneur annonce une piste vidéo AV1 (V_AV1 / av01)."""
        data = self._mkv_probe_header(path)
        if not data:
            return False
        # Matroska CodecID + ISO-BMFF sample entry parfois présent en métadonnées
        if b"V_AV1" in data or b"V_AV01" in data:
            return True
        if b"av01" in data:
            return True
        return False

    def _demote_hw_av1_decoders(self):
        """Compat : délègue à la politique AV1 unique."""
        apply_safe_gstreamer_ranks()

    def _active_pipeline(self):
        if self._custom_pipeline is not None:
            return self._custom_pipeline
        return self.player

    def _destroy_custom_pipeline(self):
        pipe = self._custom_pipeline
        if pipe is None:
            return
        try:
            bus = pipe.get_bus()
            for hid in getattr(self, "_custom_bus_handler_ids", []) or []:
                try:
                    bus.disconnect(hid)
                except Exception:
                    pass
            try:
                bus.remove_signal_watch()
            except Exception:
                pass
        except Exception:
            pass
        self._custom_bus_handler_ids = []
        for el in (getattr(self, "spectrum", None), getattr(self, "equalizer", None)):
            if el is None:
                continue
            try:
                parent = el.get_parent()
                if parent is not None:
                    try:
                        el.set_state(Gst.State.NULL)
                    except Exception:
                        pass
                    parent.remove(el)
            except Exception:
                pass
        try:
            pipe.set_state(Gst.State.NULL)
            # Court : ne jamais bloquer l'UI 3 s (VAAPI peut coincer)
            pipe.get_state(200 * Gst.MSECOND)
        except Exception:
            pass
        self._custom_pipeline = None
        self._custom_volume = None
        self._custom_audio_linked = False
        self._custom_video_linked = False
        self._custom_sub_linked = False
        self._custom_audio_pads = []
        self._custom_audio_queue = None
        self._custom_audio_index = 0
        self._custom_subtitle_pads = []
        self._custom_subtitle_index = -1
        self._custom_sub_queue = None
        self._custom_sub_overlay = None
        self._custom_sub_muted = False
        self._need_playbin_rebind = True

    def _attach_custom_bus(self, element):
        try:
            bus = element.get_bus()
            bus.add_signal_watch()
            hid1 = bus.connect("message", self.on_gstreamer_message)
            bus.enable_sync_message_emission()
            hid2 = bus.connect("sync-message::element", self.on_sync_message)
            self._custom_bus_handler_ids = [hid1, hid2]
        except Exception as e:
            print("attach bus:", e, file=sys.stderr)

    def _build_mkv_pipeline(self, filepath):
        """Pipeline MKV minimal et stable (E-AC3).

        - Vidéo : h265parse/HW ou decodebin → videoconvert → sink
        - Audio : 1re piste seulement (avdec_eac3) → convert → resample → volume → pulse
        - Pas de spectre/EQ dans ce pipeline (évite unparent depuis le thread streaming)
        - Sous-titres : textoverlay / subtitleoverlay (brûlés dans la vidéo)
        """
        pipe = Gst.Pipeline.new("mkv-local-pipeline")
        filesrc = Gst.ElementFactory.make("filesrc", "mkv-src")
        demux = Gst.ElementFactory.make("matroskademux", "mkv-demux")
        if not filesrc or not demux:
            return None
        filesrc.set_property("location", os.path.abspath(filepath))
        pipe.add(filesrc)
        pipe.add(demux)
        if not filesrc.link(demux):
            return None

        # Langues sous-titres via Discoverer (fiable, hors pad-added)
        self._custom_sub_lang_list = self._discover_subtitle_languages(filepath)

        self._custom_audio_linked = False
        self._custom_video_linked = False
        self._custom_sub_linked = False
        self._custom_sub_wait_scheduled = False
        self._custom_volume = None
        self._custom_audio_pads = []
        self._custom_audio_queue = None
        self._custom_audio_index = 0
        self._custom_subtitle_pads = []
        self._custom_subtitle_index = -1
        self._custom_sub_muted = False
        self._custom_sub_queue = None
        self._custom_is_av1 = False

        video_sink = self._create_video_sink()
        if video_sink is None:
            video_sink = Gst.ElementFactory.make("autovideosink", "mkv-vsink")
        self.video_sink = video_sink
        try:
            if video_sink is not None and video_sink.find_property("force-aspect-ratio"):
                video_sink.set_property("force-aspect-ratio", True)
        except Exception:
            pass

        # Overlay sous-titres dans le flux vidéo (fiable, sans bande GTK)
        sub_overlay = Gst.ElementFactory.make("textoverlay", "mkv-subov")
        self._custom_sub_overlay_is_text = True
        if sub_overlay is None:
            sub_overlay = Gst.ElementFactory.make("subtitleoverlay", "mkv-subov")
            self._custom_sub_overlay_is_text = False
        self._custom_sub_overlay = sub_overlay
        if sub_overlay is not None:
            pipe.add(sub_overlay)
            # GstBaseTextOverlayVAlign: baseline=0, bottom=1, top=2, center=4
            # GstBaseTextOverlayHAlign: left=0, center=1, right=2
            for prop, val in (
                ("shaded-background", False),
                ("draw-shadow", True),
                ("draw-outline", True),
                ("font-desc", "Sans Bold 12"),
                ("valignment", 1),  # bottom (pas 2=top)
                ("halignment", 1),  # center
                ("line-alignment", 1),  # center
                # silent jusqu'à sélection manuelle d'une piste (évite freeze)
                ("silent", True),
                # CRITIQUE : si True, la vidéo attend chaque buffer texte
                # → image bloquée ou boucle d'~1 s dès qu'on active les ST.
                ("wait-text", False),
                ("deltay", -12),  # légère marge au-dessus du bord bas
            ):
                try:
                    sub_overlay.set_property(prop, val)
                except Exception:
                    pass
            # Repli chaînes (certaines builds acceptent les nicks)
            for prop, val in (
                ("valignment", "bottom"),
                ("halignment", "center"),
                ("font-desc", "Sans Bold 12"),
            ):
                try:
                    sub_overlay.set_property(prop, val)
                except Exception:
                    pass
            try:
                # Contour lisible sans rectangle opaque
                sub_overlay.set_property("outline-color", 0xFF000000)
                sub_overlay.set_property("color", 0xFFFFFFFF)
            except Exception:
                pass

        def _tune_queue(q, is_video=False, for_av1=False, for_eac3=False):
            if q is None:
                return
            try:
                # AV1 soft : tampon vidéo modéré (trop profond → starvation
                # audio E-AC3 / crachotements). Audio E-AC3 : file large et
                # non-leaky pour ne pas jeter de samples.
                soft = getattr(self, "performance_mode", "hardware") == "software"
                if is_video:
                    if for_av1:
                        # Compromis fluidité / audio : ~2 s de marge decode
                        max_buf = 120
                        max_bytes = 24 * 1024 * 1024
                        max_time = 2.0
                    elif soft:
                        max_buf = 100
                        max_bytes = 16 * 1024 * 1024
                        max_time = 1.5
                    else:
                        max_buf = 80
                        max_bytes = 12 * 1024 * 1024
                        max_time = 1.0
                else:
                    if for_eac3 or for_av1:
                        max_buf = 200
                        max_bytes = 8 * 1024 * 1024
                        max_time = 3.0
                    else:
                        max_buf = 100
                        max_bytes = 4 * 1024 * 1024
                        max_time = 2.0
                q.set_property("max-size-buffers", max_buf)
                q.set_property("max-size-bytes", max_bytes)
                q.set_property("max-size-time", int(max_time * Gst.SECOND))
                # JAMAIS leaky sur la vidéo.
                # Audio E-AC3 / AV1 : non-leaky (leaky → crachotements puis silence).
                # Autre audio : leaky downstream pour ne pas bloquer le demux.
                if is_video:
                    try:
                        q.set_property("leaky", 0)
                    except Exception:
                        pass
                elif for_eac3 or for_av1:
                    try:
                        q.set_property("leaky", 0)
                    except Exception:
                        pass
                else:
                    try:
                        q.set_property("leaky", 2)  # GST_QUEUE_LEAK_DOWNSTREAM
                    except Exception:
                        try:
                            q.set_property("leaky", True)
                        except Exception:
                            pass
            except Exception:
                pass

        def _pad_language(pad, struct):
            if struct is not None:
                for key in ("language", "language-code", "lang"):
                    try:
                        if struct.has_field(key):
                            ok, val = struct.get_string(key)
                            if ok and val and val not in ("und", "unk", ""):
                                return val
                    except Exception:
                        pass
            try:
                event = pad.get_sticky_event(Gst.EventType.TAG, 0)
                if event is not None:
                    tags = event.parse_tag()
                    for tkey in (Gst.TAG_LANGUAGE_CODE, Gst.TAG_LANGUAGE_NAME):
                        try:
                            ok, val = tags.get_string(tkey)
                            if ok and val and val not in ("und", "unk", ""):
                                return val
                        except Exception:
                            pass
            except Exception:
                pass
            return None

        def _configure_software_decoder(decode):
            """Multi-thread pour décodeurs logiciels (AV1 / H.264 / H.265)."""
            if decode is None:
                return
            try:
                # Utilise tous les cœurs (plafond 16) : AV1 soft en a besoin
                ncpu = max(2, min(16, (os.cpu_count() or 2)))
                for prop in ("max-threads", "n-threads", "threads"):
                    try:
                        if decode.find_property(prop):
                            decode.set_property(prop, ncpu)
                            break
                    except Exception:
                        continue
                # dav1d : frame + tile threads si exposés
                for prop, val in (
                    ("frame-threads", max(1, ncpu // 2)),
                    ("tile-threads", max(1, min(4, ncpu // 2))),
                ):
                    try:
                        if decode.find_property(prop):
                            decode.set_property(prop, val)
                    except Exception:
                        pass
            except Exception:
                pass

        def _configure_videoconvert(convert):
            """Accélère videoconvert (moins de dither / multi-thread)."""
            if convert is None:
                return
            try:
                if convert.find_property("n-threads"):
                    convert.set_property(
                        "n-threads", max(1, min(4, (os.cpu_count() or 2)))
                    )
            except Exception:
                pass
            try:
                # 0 = none — évite le dithering coûteux frame par frame
                if convert.find_property("dither"):
                    convert.set_property("dither", 0)
            except Exception:
                pass

        def _make_h265_chain():
            parse = Gst.ElementFactory.make("h265parse", "mkv-h265parse")
            if parse is None:
                return None
            mode = getattr(self, "performance_mode", "hardware")
            if mode == "hardware":
                # NVIDIA → AMD/Intel VA → Vulkan → Quick Sync → V4L2 → soft
                for dec_name, post_name in (
                    ("nvh265dec", None),
                    ("nvv4l2decoder", None),
                    ("vah265dec", "vapostproc"),
                    ("vaapih265dec", "vaapipostproc"),
                    ("vulkanh265dec", None),
                    ("qsvh265dec", None),
                    ("v4l2h265dec", None),
                ):
                    decode = Gst.ElementFactory.make(dec_name, "mkv-vdec")
                    if decode is None:
                        continue
                    convert = None
                    if post_name:
                        convert = Gst.ElementFactory.make(post_name, "mkv-vpost")
                    if convert is None:
                        convert = Gst.ElementFactory.make(
                            "videoconvert", "mkv-vconv"
                        )
                    if convert is None:
                        continue
                    return parse, decode, convert, dec_name
            decode = Gst.ElementFactory.make("avdec_h265", "mkv-vdec")
            convert = Gst.ElementFactory.make("videoconvert", "mkv-vconv")
            if not decode or not convert:
                return None
            _configure_software_decoder(decode)
            return parse, decode, convert, "avdec_h265"

        def _make_h264_chain():
            """Chaîne H.264 explicite (HW ou SW multi-thread) — plus stable que decodebin seul en 4K."""
            parse = Gst.ElementFactory.make("h264parse", "mkv-h264parse")
            if parse is None:
                return None
            mode = getattr(self, "performance_mode", "hardware")
            if mode == "hardware":
                # NVIDIA → AMD/Intel VA → Vulkan → Quick Sync → V4L2 → soft
                for dec_name, post_name in (
                    ("nvh264dec", None),
                    ("nvv4l2decoder", None),
                    ("vah264dec", "vapostproc"),
                    ("vaapih264dec", "vaapipostproc"),
                    ("vulkanh264dec", None),
                    ("qsvh264dec", None),
                    ("v4l2h264dec", None),
                ):
                    decode = Gst.ElementFactory.make(dec_name, "mkv-vdec")
                    if decode is None:
                        continue
                    convert = None
                    if post_name:
                        convert = Gst.ElementFactory.make(post_name, "mkv-vpost")
                    if convert is None:
                        convert = Gst.ElementFactory.make(
                            "videoconvert", "mkv-vconv"
                        )
                    if convert is None:
                        continue
                    return parse, decode, convert, dec_name
            decode = Gst.ElementFactory.make("avdec_h264", "mkv-vdec")
            convert = Gst.ElementFactory.make("videoconvert", "mkv-vconv")
            if not decode or not convert:
                return None
            _configure_software_decoder(decode)
            return parse, decode, convert, "avdec_h264"

        def _make_av1_chain():
            """AV1 : NVIDIA HW si mode Matériel, sinon / ensuite soft."""
            parse = Gst.ElementFactory.make("av1parse", "mkv-av1parse")
            mode = getattr(self, "performance_mode", "hardware")
            if mode == "hardware":
                # NVIDIA → VA (AMD/Intel) → soft (dav1d / av1dec / avdec_av1)
                for dec_name, post_name in (
                    ("nvav1dec", None),
                    ("vaav1dec", "vapostproc"),
                    ("vaapiav1dec", "vaapipostproc"),
                ):
                    decode = Gst.ElementFactory.make(dec_name, "mkv-vdec")
                    if decode is None:
                        continue
                    convert = None
                    if post_name:
                        convert = Gst.ElementFactory.make(post_name, "mkv-vpost")
                    if convert is None:
                        convert = Gst.ElementFactory.make(
                            "videoconvert", "mkv-vconv"
                        )
                    if convert is None:
                        continue
                    _configure_videoconvert(convert)
                    return parse, decode, convert, dec_name
            for dec_name in _AV1_SOFT_DECODER_ORDER:
                decode = Gst.ElementFactory.make(dec_name, "mkv-vdec")
                if decode is None:
                    continue
                convert = Gst.ElementFactory.make("videoconvert", "mkv-vconv")
                if convert is None:
                    continue
                _configure_software_decoder(decode)
                _configure_videoconvert(convert)
                return parse, decode, convert, dec_name
            return None

        def _make_vp9_chain():
            """VP9 (MKV/WebM)."""
            parse = Gst.ElementFactory.make("vp9parse", "mkv-vp9parse")
            if getattr(self, "performance_mode", "hardware") == "hardware":
                # NVIDIA → AMD/Intel VA → Quick Sync → soft
                for dec_name, post_name in (
                    ("nvvp9dec", None),
                    ("vavp9dec", "vapostproc"),
                    ("vaapivp9dec", "vaapipostproc"),
                    ("qsvvp9dec", None),
                ):
                    decode = Gst.ElementFactory.make(dec_name, "mkv-vdec")
                    if decode is None:
                        continue
                    convert = None
                    if post_name:
                        convert = Gst.ElementFactory.make(post_name, "mkv-vpost")
                    if convert is None:
                        convert = Gst.ElementFactory.make(
                            "videoconvert", "mkv-vconv"
                        )
                    if convert is None:
                        continue
                    return parse, decode, convert, dec_name
            decode = Gst.ElementFactory.make("avdec_vp9", "mkv-vdec")
            convert = Gst.ElementFactory.make("videoconvert", "mkv-vconv")
            if not decode or not convert:
                return None
            _configure_software_decoder(decode)
            return parse, decode, convert, "avdec_vp9"

        def _make_vp8_chain():
            """VP8 (WebM / MKV)."""
            parse = Gst.ElementFactory.make("vp8parse", "mkv-vp8parse")
            if getattr(self, "performance_mode", "hardware") == "hardware":
                for dec_name, post_name in (
                    ("nvvp8dec", None),
                    ("vavp8dec", "vapostproc"),
                    ("vaapivp8dec", "vaapipostproc"),
                ):
                    decode = Gst.ElementFactory.make(dec_name, "mkv-vdec")
                    if decode is None:
                        continue
                    convert = None
                    if post_name:
                        convert = Gst.ElementFactory.make(post_name, "mkv-vpost")
                    if convert is None:
                        convert = Gst.ElementFactory.make(
                            "videoconvert", "mkv-vconv"
                        )
                    if convert is None:
                        continue
                    return parse, decode, convert, dec_name
            decode = Gst.ElementFactory.make("avdec_vp8", "mkv-vdec")
            convert = Gst.ElementFactory.make("videoconvert", "mkv-vconv")
            if not decode or not convert:
                return None
            _configure_software_decoder(decode)
            return parse, decode, convert, "avdec_vp8"

        def _make_mpeg2_chain():
            """MPEG-2 (DVD / MKV)."""
            parse = Gst.ElementFactory.make("mpegvideoparse", "mkv-mpeg2parse")
            if getattr(self, "performance_mode", "hardware") == "hardware":
                for dec_name, post_name in (
                    ("nvmpeg2videodec", None),
                    ("nvmpegvideodec", None),
                    ("vampeg2dec", "vapostproc"),
                ):
                    decode = Gst.ElementFactory.make(dec_name, "mkv-vdec")
                    if decode is None:
                        continue
                    convert = None
                    if post_name:
                        convert = Gst.ElementFactory.make(post_name, "mkv-vpost")
                    if convert is None:
                        convert = Gst.ElementFactory.make(
                            "videoconvert", "mkv-vconv"
                        )
                    if convert is None:
                        continue
                    return parse, decode, convert, dec_name
            decode = Gst.ElementFactory.make("avdec_mpeg2video", "mkv-vdec")
            convert = Gst.ElementFactory.make("videoconvert", "mkv-vconv")
            if not decode or not convert:
                return None
            _configure_software_decoder(decode)
            return parse, decode, convert, "avdec_mpeg2video"

        def on_pad_added(demux_el, pad):
            try:
                caps = pad.get_current_caps()
                if caps is None or caps.is_empty():
                    caps = pad.query_caps(None)
                struct = (
                    caps.get_structure(0) if caps and caps.get_size() > 0 else None
                )
                media = struct.get_name() if struct else ""
            except Exception:
                media = ""

            # ---- Vidéo ----
            if media.startswith("video/") and not self._custom_video_linked:
                if video_sink is None:
                    return
                if "png" in media or "image/" in media:
                    return
                queue = Gst.ElementFactory.make("queue", "mkv-vqueue")
                if not queue:
                    return
                media_l = (media or "").lower()
                is_h265 = "h265" in media_l or "hevc" in media_l
                is_h264 = "h264" in media_l or "avc" in media_l
                is_av1 = "av1" in media_l or "av01" in media_l
                is_vp9 = "vp9" in media_l
                is_vp8 = "vp8" in media_l
                is_mpeg2 = (
                    "mpeg-2" in media_l
                    or "mpeg2" in media_l
                    or media_l in ("video/mpeg", "video/x-mpeg")
                    or ("mpeg" in media_l and "mpeg4" not in media_l and "mpeg-4" not in media_l)
                )
                if is_av1:
                    self._custom_is_av1 = True
                _tune_queue(queue, True, for_av1=is_av1)
                if is_h265:
                    chain = _make_h265_chain()
                elif is_h264:
                    chain = _make_h264_chain()
                elif is_av1:
                    chain = _make_av1_chain()
                elif is_vp9:
                    chain = _make_vp9_chain()
                elif is_vp8:
                    chain = _make_vp8_chain()
                elif is_mpeg2:
                    chain = _make_mpeg2_chain()
                else:
                    chain = None

                if chain is not None:
                    parse, decode, convert, label = chain
                    # parse optionnel (ex. sans av1parse)
                    for el in (queue, parse, decode, convert):
                        if el is None:
                            continue
                        pipe.add(el)
                        el.sync_state_with_parent()
                    # File post-décodage AV1 : absorbe les pics de temps CPU
                    # avant affichage (réduit les micro-saccades).
                    smooth_q = None
                    if is_av1:
                        smooth_q = Gst.ElementFactory.make(
                            "queue", "mkv-vsmooth"
                        )
                        if smooth_q is not None:
                            try:
                                smooth_q.set_property("max-size-buffers", 8)
                                smooth_q.set_property(
                                    "max-size-bytes", 8 * 1024 * 1024
                                )
                                smooth_q.set_property(
                                    "max-size-time", int(0.4 * Gst.SECOND)
                                )
                                smooth_q.set_property("leaky", 0)
                            except Exception:
                                pass
                            pipe.add(smooth_q)
                            smooth_q.sync_state_with_parent()
                    # AV1 soft : ne pas jeter les frames en retard (qos=False)
                    # → lecture un peu plus fluide au prix d'un léger retard.
                    if is_av1 and video_sink is not None:
                        for prop, val in (
                            ("max-lateness", int(2 * Gst.SECOND)),
                            ("qos", False),
                        ):
                            try:
                                video_sink.set_property(prop, val)
                            except Exception:
                                pass
                    if video_sink.get_parent() is None:
                        pipe.add(video_sink)
                        video_sink.sync_state_with_parent()
                    try:
                        pad.link(queue.get_static_pad("sink"))
                    except Exception:
                        return
                    if parse is not None:
                        if not (queue.link(parse) and parse.link(decode)):
                            return
                    else:
                        if not queue.link(decode):
                            return
                    # decode → convert → [smooth queue] → [textoverlay] → sink
                    tail = convert if decode.link(convert) else decode
                    if smooth_q is not None:
                        if tail.link(smooth_q):
                            tail = smooth_q
                    if sub_overlay is not None:
                        if getattr(self, "_custom_sub_overlay_is_text", True):
                            if not tail.link(sub_overlay):
                                self._link_through_video_color(
                                    pipe, tail, video_sink
                                )
                            else:
                                self._link_through_video_color(
                                    pipe, sub_overlay, video_sink
                                )
                        else:
                            try:
                                vpad = sub_overlay.get_static_pad("video_sink")
                                if vpad is not None:
                                    tail.get_static_pad("src").link(vpad)
                                    self._link_through_video_color(
                                        pipe, sub_overlay, video_sink
                                    )
                                else:
                                    self._link_through_video_color(
                                        pipe, tail, video_sink
                                    )
                            except Exception:
                                self._link_through_video_color(
                                    pipe, tail, video_sink
                                )
                    else:
                        self._link_through_video_color(pipe, tail, video_sink)
                    self._custom_video_linked = True
                    if DEBUG_MYOSTOOX:
                        print(
                        "Myostoox: MKV vidéo (%s)" % label, file=sys.stderr
                    )
                    try:
                        GLib.idle_add(self._ensure_video_sink_geometry)
                    except Exception:
                        pass
                    return

                decode = Gst.ElementFactory.make("decodebin", "mkv-vdecode")
                convert = Gst.ElementFactory.make("videoconvert", "mkv-vconv")
                if not decode or not convert:
                    return
                for el in (queue, decode, convert):
                    pipe.add(el)
                    el.sync_state_with_parent()
                if video_sink.get_parent() is None:
                    pipe.add(video_sink)
                    video_sink.sync_state_with_parent()
                try:
                    pad.link(queue.get_static_pad("sink"))
                except Exception:
                    return
                queue.link(decode)

                def on_vpad(dbin, dpad):
                    try:
                        sp = convert.get_static_pad("sink")
                        if sp and not sp.is_linked():
                            dpad.link(sp)
                            if sub_overlay is not None and getattr(
                                self, "_custom_sub_overlay_is_text", True
                            ):
                                if convert.link(sub_overlay):
                                    self._link_through_video_color(
                                        pipe, sub_overlay, video_sink
                                    )
                                else:
                                    self._link_through_video_color(
                                        pipe, convert, video_sink
                                    )
                            elif sub_overlay is not None:
                                try:
                                    vpad = sub_overlay.get_static_pad("video_sink")
                                    convert.get_static_pad("src").link(vpad)
                                    self._link_through_video_color(
                                        pipe, sub_overlay, video_sink
                                    )
                                except Exception:
                                    self._link_through_video_color(
                                        pipe, convert, video_sink
                                    )
                            else:
                                self._link_through_video_color(
                                    pipe, convert, video_sink
                                )
                    except Exception as e:
                        if DEBUG_MYOSTOOX:
                            print("mkv video:", e, file=sys.stderr)

                decode.connect("pad-added", on_vpad)
                self._custom_video_linked = True
                if DEBUG_MYOSTOOX:
                    print("Myostoox: MKV vidéo (decodebin)", file=sys.stderr)
                try:
                    GLib.idle_add(self._ensure_video_sink_geometry)
                except Exception:
                    pass
                return

            # ---- Audio : 1re piste + spectre dédié + gain E-AC3 ----
            if media.startswith("audio/"):
                idx = len(self._custom_audio_pads)
                lang = _pad_language(pad, struct)
                nice = (lang + " — " if lang else "") + ("Piste %d" % (idx + 1))
                self._custom_audio_pads.append((pad, nice))
                self.audio_tracks = [
                    (i, lab) for i, (_, lab) in enumerate(self._custom_audio_pads)
                ]
                if self._custom_audio_linked:
                    return

                queue = Gst.ElementFactory.make("queue", "mkv-aqueue")
                convert = Gst.ElementFactory.make("audioconvert", "mkv-aconv")
                # avdec_eac3 → F32LE non-interleaved : forcer stéréo interleaved float
                # (précision jusqu'au volume, Pulse accepte F32LE).
                acaps = Gst.ElementFactory.make("capsfilter", "mkv-acaps")
                if acaps is not None:
                    try:
                        acaps.set_property(
                            "caps",
                            Gst.Caps.from_string(
                                "audio/x-raw,format=F32LE,channels=2,"
                                "layout=interleaved"
                            ),
                        )
                    except Exception:
                        try:
                            acaps.set_property(
                                "caps",
                                Gst.Caps.from_string(
                                    "audio/x-raw,format=S16LE,channels=2,"
                                    "layout=interleaved"
                                ),
                            )
                        except Exception:
                            acaps = None
                resample = Gst.ElementFactory.make(
                    "audioresample", "mkv-aresample"
                )
                if resample is not None:
                    try:
                        resample.set_property("quality", 8)
                    except Exception:
                        pass
                # Pas de FFT spectrum en pipeline custom vidéo (CPU / bus) :
                # sur AV1+E-AC3 le spectre provoque underruns et crachotements.
                spec = None
                volume = Gst.ElementFactory.make("volume", "mkv-volume")
                sink = Gst.ElementFactory.make("pulsesink", "mkv-asink")
                if sink is None:
                    sink = Gst.ElementFactory.make("autoaudiosink", "mkv-asink")
                decode = None
                alabel = None
                media_a = (media or "").lower()
                is_eac3 = "eac3" in media_a or "e-ac-3" in media_a
                is_ac3 = (not is_eac3) and ("ac3" in media_a)
                is_av1_pipe = bool(getattr(self, "_custom_is_av1", False))
                cands = ()
                if is_eac3:
                    cands = ("avdec_eac3", "avdec_ac3")
                elif is_ac3:
                    cands = ("avdec_ac3", "avdec_eac3")
                for n in cands:
                    decode = Gst.ElementFactory.make(n, "mkv-adec")
                    if decode is not None:
                        # Désactive la compression dynamique (dialnorm E-AC3)
                        for prop_name in (
                            "drc-scale",
                            "drc_scale",
                            "drc",
                        ):
                            try:
                                decode.set_property(prop_name, 0.0)
                                break
                            except Exception:
                                continue
                        alabel = n
                        break
                use_dbin = False
                if decode is None:
                    decode = Gst.ElementFactory.make("decodebin", "mkv-adecode")
                    alabel = "decodebin"
                    use_dbin = True
                if not queue or not decode or not convert or not sink:
                    return
                _tune_queue(
                    queue,
                    False,
                    for_av1=is_av1_pipe,
                    for_eac3=is_eac3 or is_ac3,
                )
                self._custom_audio_queue = queue
                if volume is not None:
                    try:
                        vol = float(self.volume_scale.get_value())
                        # E-AC3 multicanal : gain compensatoire après downmix
                        # DRC off + downmix 5.1 : gain net
                        boost = 2.4 if is_eac3 else (1.6 if is_ac3 else 1.0)
                        gain = 0.0 if getattr(self, "is_muted", False) else min(
                            3.5, vol * boost
                        )
                        volume.set_property("volume", gain)
                    except Exception:
                        pass
                    self._custom_volume = volume
                # Tampon sink plus généreux (E-AC3 + AV1 soft = pics CPU)
                if is_eac3 or is_ac3 or is_av1_pipe:
                    buf_props = (
                        ("buffer-time", 1000000),   # 1 s
                        ("latency-time", 50000),    # 50 ms
                        ("drift-tolerance", 50000),
                    )
                else:
                    buf_props = (
                        ("buffer-time", 400000),
                        ("latency-time", 20000),
                    )
                for prop, val in buf_props:
                    try:
                        sink.set_property(prop, val)
                    except Exception:
                        pass
                try:
                    sink.set_property("sync", True)
                except Exception:
                    pass
                amplify = None
                if is_eac3 or is_ac3:
                    amplify = Gst.ElementFactory.make(
                        "audioamplify", "mkv-aamp"
                    )
                    if amplify is not None:
                        try:
                            # ~+6 dB linéaires après downmix
                            amplify.set_property("amplification", 2.0)
                        except Exception:
                            try:
                                amplify.set_property("amplification", 1.8)
                            except Exception:
                                pass
                elems = [queue, decode, convert]
                if acaps is not None:
                    elems.append(acaps)
                if spec is not None:
                    elems.append(spec)
                elems.append(resample)
                if volume is not None:
                    elems.append(volume)
                if amplify is not None:
                    elems.append(amplify)
                elems.append(sink)
                for el in elems:
                    if el is not None:
                        pipe.add(el)
                        el.sync_state_with_parent()
                try:
                    pad.link(queue.get_static_pad("sink"))
                except Exception:
                    return
                if not queue.link(decode):
                    return

                def finish(from_el):
                    prev = from_el
                    if acaps is not None:
                        if prev.link(acaps):
                            prev = acaps
                    if spec is not None:
                        if prev.link(spec):
                            prev = spec
                    if resample is not None and prev.link(resample):
                        prev = resample
                    if volume is not None and prev.link(volume):
                        prev = volume
                    if amplify is not None and prev.link(amplify):
                        prev = amplify
                    prev.link(sink)

                if use_dbin:
                    def on_apad(dbin, dpad):
                        try:
                            sp = convert.get_static_pad("sink")
                            if sp and not sp.is_linked():
                                dpad.link(sp)
                                finish(convert)
                        except Exception as e:
                            if DEBUG_MYOSTOOX:
                                print("mkv audio:", e, file=sys.stderr)

                    decode.connect("pad-added", on_apad)
                else:
                    if decode.link(convert):
                        finish(convert)
                self._custom_audio_linked = True
                self._custom_audio_index = 0
                self.current_audio_index = 0
                if DEBUG_MYOSTOOX:
                    print("Myostoox: MKV audio (%s)" % alabel, file=sys.stderr)
                return

            # ---- Sous-titres TEXTE uniquement → textoverlay ----
            # Ne JAMAIS brancher PGS / VobSub / DVD subpicture sur textoverlay :
            # cela bloque le pipeline (freeze immédiat). Ces pistes image
            # sont listées mais non auto-branchées (playbin les gère mieux).
            media_l = (media or "").lower()
            is_bitmap_sub = any(
                tok in media_l
                for tok in (
                    "pgs",
                    "hdmv",
                    "vobsub",
                    "dvdsub",
                    "subpicture",
                    "x-dvd",
                    "x-avi-subtitle",
                    "image/",
                )
            )
            # Caps parfois absentes à pad-added : lire le template du pad
            if not media_l:
                try:
                    tmpl = pad.get_pad_template()
                    if tmpl is not None:
                        tcaps = tmpl.get_caps()
                        if tcaps and not tcaps.is_empty():
                            tstruct = tcaps.get_structure(0)
                            if tstruct:
                                media_l = (tstruct.get_name() or "").lower()
                except Exception:
                    pass
            # SSA/ASS Matroska : application/x-ssa, application/x-ass,
            # S_TEXT/ASS, parfois application/x-subtitle ou text/x-raw.
            is_text_sub = (not is_bitmap_sub) and (
                media_l.startswith("text/")
                or media_l.startswith("application/x-subrip")
                or media_l.startswith("application/x-ssa")
                or media_l.startswith("application/x-ass")
                or media_l.startswith("application/x-subtitle")
                or media_l.startswith("subtitle/")
                or media_l in (
                    "application/x-subtitle",
                    "application/x-subtitle-unknown",
                )
                or "subrip" in media_l
                or "ssa" in media_l
                or media_l.endswith("/x-ass")
                or media_l.endswith("/x-ssa")
                or media_l.endswith("/x-raw")
            )
            # Repli : CodecID Matroska dans les tags (S_TEXT/ASS, S_TEXT/SSA…)
            if not is_text_sub and not is_bitmap_sub:
                try:
                    event = pad.get_sticky_event(Gst.EventType.TAG, 0)
                    if event is not None:
                        tags = event.parse_tag()
                        for tkey in (
                            Gst.TAG_CODEC,
                            Gst.TAG_SUBTITLE_CODEC,
                            "codec",
                        ):
                            try:
                                ok, val = tags.get_string(tkey)
                                if not ok or not val:
                                    continue
                                vl = val.lower()
                                if any(
                                    t in vl
                                    for t in (
                                        "ass",
                                        "ssa",
                                        "subrip",
                                        "srt",
                                        "utf8",
                                        "text",
                                    )
                                ):
                                    if not any(
                                        t in vl
                                        for t in ("pgs", "vobsub", "hdmv", "dvd")
                                    ):
                                        is_text_sub = True
                                        break
                            except Exception:
                                continue
                except Exception:
                    pass
            is_sub = is_text_sub or is_bitmap_sub
            if not is_sub:
                return

            lang = _pad_language(pad, struct)
            if not lang:
                try:
                    sid = pad.get_stream_id()
                except Exception:
                    sid = None
                if sid:
                    lang = (getattr(self, "_custom_sub_lang_list", None) or {}).get(sid)
            lang = self._normalize_lang_code(lang) if lang else None
            sidx = len(self._custom_subtitle_pads)
            # Pas de self.tr() ici : callback thread GStreamer (thread-safe)
            if lang:
                nice = lang
            elif is_bitmap_sub:
                nice = "Sub %d (image)" % (sidx + 1)
            else:
                nice = "Sub %d" % (sidx + 1)
            self._custom_subtitle_pads.append((pad, nice, is_text_sub))
            track = {
                "kind": "embedded",
                "index": sidx,
                "path": None,
                "label": nice,
                "text": bool(is_text_sub),
            }
            # Mise à jour liste pistes uniquement via idle (thread UI)
            def _append_track(t=track, si=sidx):
                try:
                    self.subtitle_tracks = list(self.subtitle_tracks or [])
                    if not any(
                        isinstance(x, dict)
                        and x.get("kind") == "embedded"
                        and x.get("index") == si
                        for x in self.subtitle_tracks
                    ):
                        self.subtitle_tracks.append(t)
                except Exception:
                    pass
                return False

            GLib.idle_add(_append_track)

            # Tags souvent absents à pad-added → sondes différées
            GLib.timeout_add(
                400, self._delayed_subtitle_lang_probe, pad, sidx
            )
            GLib.timeout_add(
                1200, self._delayed_subtitle_lang_probe, pad, sidx
            )

            # Pas d'auto-branchement pendant PLAYING/PAUSED : le lien
            # différé figeait le pipeline MKV (toutes langues UI).
            # Activation uniquement via le menu Sous-titres.
            return


        demux.connect("pad-added", on_pad_added)
        return pipe

    def _ensure_video_sink_geometry(self):
        """Allocation non nulle + handle X11 avant négociation vidéo.

        Évite gst_video_center_rect: assertion 'src->h != 0' (VA-API / glimagesink).
        """
        if not getattr(self, "current_is_video", False):
            return
        try:
            if self.video_area is not None:
                # Hauteur mini le temps que GTK alloue (plein écran / paned)
                alloc = self.video_area.get_allocation()
                if alloc.height < 8 or alloc.width < 8:
                    self.video_area.set_size_request(-1, max(180, alloc.height or 180))
                if not self.video_area.get_realized():
                    self.video_area.realize()
                self.video_area.show()
                # Laisse GTK calculer l'allocation réelle
                for _ in range(6):
                    if not Gtk.events_pending():
                        break
                    Gtk.main_iteration_do(False)
        except Exception:
            pass
        try:
            self._cache_video_window_handle()
        except Exception:
            pass

    def _play_file_custom_mkv(self, filename):
        self._destroy_custom_pipeline()
        try:
            self.player.set_state(Gst.State.NULL)
            self.player.get_state(Gst.SECOND)
        except Exception:
            pass
        try:
            self._apply_safe_audio_decoder_ranks()
        except Exception:
            pass
        if DEBUG_MYOSTOOX:
            print(
            "Myostoox: pipeline MKV custom",
            filename,
            getattr(self, "performance_mode", "?"),
            file=sys.stderr,
        )
        if self.current_is_video:
            self._ensure_video_sink_geometry()
        pipe = self._build_mkv_pipeline(filename)
        if pipe is None:
            return False
        self._custom_pipeline = pipe
        self._attach_custom_bus(pipe)
        if self.current_is_video:
            self._ensure_video_sink_geometry()
            # Handle sur le sink custom (pas seulement playbin)
            try:
                sink = getattr(self, "video_sink", None)
                handle = getattr(self, "_video_window_handle", None)
                if sink is not None and handle and isinstance(sink, GstVideo.VideoOverlay):
                    GstVideo.VideoOverlay.set_window_handle(sink, handle)
            except Exception:
                pass
        ret = pipe.set_state(Gst.State.PAUSED)
        if ret == Gst.StateChangeReturn.FAILURE:
            self._destroy_custom_pipeline()
            return False
        try:
            pipe.get_state(3 * Gst.SECOND)
        except Exception:
            pass
        # Laisse le demux exposer les pads sous-titres (pad-added async)
        pref = getattr(self, "_preferred_custom_sub_index", -1)
        if pref is not None and pref >= 0:
            for _ in range(20):
                try:
                    while Gtk.events_pending():
                        Gtk.main_iteration_do(False)
                except Exception:
                    pass
                pads = getattr(self, "_custom_subtitle_pads", None) or []
                if len(pads) > pref:
                    break
                time.sleep(0.025)
        # Branche la piste demandée pendant PAUSED (stable)
        try:
            self._link_preferred_custom_subtitle_while_paused()
        except Exception as e:
            print("preferred sub link:", e, file=sys.stderr)
        if self.current_is_video:
            self._cache_video_window_handle()
            self._apply_video_overlay_handle()
            self._nudge_video_redraw()
        ret = pipe.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self._destroy_custom_pipeline()
            if getattr(self, "performance_mode", "hardware") == "hardware":
                saved = self.performance_mode
                self.performance_mode = "software"
                try:
                    pipe2 = self._build_mkv_pipeline(filename)
                finally:
                    self.performance_mode = saved
                if pipe2 is None:
                    return False
                self._custom_pipeline = pipe2
                self._attach_custom_bus(pipe2)
                if self.current_is_video:
                    self._cache_video_window_handle()
                    self._apply_video_overlay_handle()
                pipe2.set_state(Gst.State.PAUSED)
                try:
                    pipe2.get_state(3 * Gst.SECOND)
                except Exception:
                    pass
                try:
                    self._link_preferred_custom_subtitle_while_paused()
                except Exception:
                    pass
                if self.current_is_video:
                    self._cache_video_window_handle()
                    self._apply_video_overlay_handle()
                if pipe2.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                    self._destroy_custom_pipeline()
                    return False
                return True
            return False
        return True

    def _set_playback_volume(self, value):
        value = max(0.0, min(1.0, float(value)))
        if self._custom_volume is not None:
            try:
                # Même compensation E-AC3 qu'à la construction de la chaîne
                boost = 1.0
                if self._custom_pipeline is not None:
                    boost = 2.4
                self._custom_volume.set_property(
                    "volume", min(3.5, value * boost)
                )
            except Exception:
                pass
        try:
            self.player.set_property("volume", value)
        except Exception:
            pass

    def play_file(self, filename):

        if not filename:
            return

        try:
            if is_cdda(filename) or is_stream(filename) or is_dvd(filename) or is_bluray(filename):
                uri = filename
            else:
                uri = GLib.filename_to_uri(os.path.abspath(filename), None)

            # Nouveau fichier : retente le mode Matériel (NVIDIA) ;
            # si on rejoue le même après repli soft, on garde le soft.
            try:
                if filename != getattr(self, "_hw_fallback_path", None):
                    self._hw_fallback_path = None
                    if getattr(self, "performance_mode", "hardware") == "hardware":
                        self._apply_decoder_performance_mode(force_software=False)
            except Exception:
                pass

            # Invalide tout callback différé encore en attente (vidéo→audio, etc.)
            self._playback_generation += 1
            gen = self._playback_generation

            # Arrêt propre de l'ancien média AVANT de poser current_path
            # (évite qu'un EOS retardé relance l'ancienne piste).
            self._destroy_custom_pipeline()
            try:
                self.player.set_state(Gst.State.NULL)
                self.player.get_state(200 * Gst.MSECOND)
            except Exception:
                pass
            # Purge éventuelle de messages EOS encore en file sur le bus playbin
            try:
                bus = self.player.get_bus()
                if bus is not None:
                    while True:
                        msg = bus.pop_filtered(
                            Gst.MessageType.EOS | Gst.MessageType.ERROR
                        )
                        if msg is None:
                            break
            except Exception:
                pass

            if gen != self._playback_generation:
                return
            self.current_path = filename
            self.current_duration = 0
            self.current_is_cdda = is_cdda(filename)

            # Après un MKV custom : réattacher sink vidéo + spectre/EQ à playbin
            if getattr(self, "_need_playbin_rebind", False):
                self._need_playbin_rebind = False
                try:
                    self.video_sink = self._create_video_sink()
                    if self.video_sink is not None:
                        self.player.set_property("video-sink", self.video_sink)
                except Exception:
                    pass
                try:
                    self.rebuild_audio_filter(force=True, restart=False)
                except Exception:
                    pass

            # Durée initiale depuis les métadonnées (souvent dispo avant GStreamer)
            try:
                _, _, _, _, meta_duration, _, _ = self.get_metadata(filename, load_cover=False)
                if meta_duration and float(meta_duration) > 0:
                    self.current_duration = float(meta_duration)
                    self.position_scale.set_range(0, self.current_duration)
                else:
                    self.position_scale.set_range(0, 1)
            except Exception:
                self.position_scale.set_range(0, 1)

            self.time_label.set_text(
                f"0:00 / {format_time(self.current_duration)}"
                if self.current_duration > 0 else "0:00 / 0:00"
            )

            # Reset ICY + détection radio / TV
            self.icy_title = ""
            self.icy_artist = ""
            if is_stream(filename):
                self.current_radio_name = self.find_radio_name(filename)
                # play_tv() a pu poser current_tv_name juste avant : ne pas l'écraser
                if not getattr(self, "current_tv_name", None):
                    self.current_tv_name = self.find_tv_name(filename)
            else:
                self.current_radio_name = None
                self.current_tv_name = None

            self.position_scale.set_value(0)
            self._last_known_position = 0.0
            self._pending_seek_delta = 0.0
            self._seek_in_progress = False
            self.reset_spectrum()
            self._clear_chapters()

            # Affichage vidéo : fichier vidéo, DVD/BD, TV, bouquet, reprise après Stop
            prefer_video = bool(getattr(self, "_prefer_video", False))
            is_known_radio = bool(
                is_stream(filename)
                and (
                    getattr(self, "current_radio_name", None)
                    or self.find_radio_name(filename)
                )
            )
            # Reprise après Stop vidéo : uniquement pour un média vraiment vidéo/TV
            if not prefer_video and getattr(self, "_resume_prefer_video", False):
                if (
                    is_video_file(filename)
                    or is_dvd(filename)
                    or is_bluray(filename)
                ):
                    prefer_video = True
                elif is_stream(filename) and not is_known_radio:
                    if (
                        getattr(self, "current_tv_name", None)
                        or self.find_tv_name(filename)
                        or getattr(self, "bouquet_active", False)
                    ):
                        prefer_video = True
            if not prefer_video and is_stream(filename) and not is_known_radio:
                if (
                    getattr(self, "current_tv_name", None)
                    or self.find_tv_name(filename)
                    or getattr(self, "bouquet_active", False)
                ):
                    prefer_video = True
            if prefer_video and is_stream(filename) and not getattr(self, "current_tv_name", None):
                try:
                    self.current_tv_name = self.find_tv_name(filename)
                except Exception:
                    pass
            if is_known_radio:
                prefer_video = False
            if (
                is_video_file(filename)
                or is_dvd(filename)
                or is_bluray(filename)
                or prefer_video
                or (getattr(self, "bouquet_active", False) and is_stream(filename) and not is_known_radio)
            ):
                self.lyrics_mode = False
                self.lyrics_data = None
                self.lyrics_current_index = -1
                self.show_video_area(True)
            else:
                if not (
                    getattr(self, "lyrics_enabled", False)
                    and is_pure_audio_file(filename)
                ):
                    self.lyrics_mode = False
                    self.lyrics_data = None
                    self.lyrics_current_index = -1
                    self.show_video_area(False)
            self._prefer_video = False
            self._resume_prefer_video = False

            try:
                self._refresh_lyrics_for_current()
            except Exception as e:
                print("lyrics refresh:", e, file=sys.stderr)

            # Pitch/tempo ne doit jamais rester actif hors audio local
            try:
                want_pitch = (
                    self.pitch is not None
                    and is_pure_audio_file(filename)
                    and (
                        abs(self.pitch_value - 1.0) > 0.001
                        or abs(self.tempo_value - 1.0) > 0.001
                    )
                )
                if want_pitch != bool(getattr(self, "_pitch_active", False)):
                    self.rebuild_audio_filter(force=True, restart=False)
            except Exception:
                pass
            try:
                self._update_pitch_menu_sensitivity()
            except Exception:
                pass
            try:
                self._update_colorimetry_menu_sensitivity()
            except Exception:
                pass
            try:
                # Filtre playbin prêt pour la prochaine vidéo (no-op si audio)
                self._rebuild_video_color_filter(restore_playback=False)
            except Exception:
                pass

            # Sous-titres (intégrés + externes) — aucune piste auto-sélectionnée
            self.set_subtitles_enabled(self.subtitles_enabled)
            self.subtitle_tracks = []
            self.current_subtitle_index = -1
            self._preferred_custom_sub_index = -1
            self.audio_tracks = []
            self.current_audio_index = -1
            self._prepare_external_subtitles_for(filename)
            try:
                self._register_external_subtitle_tracks()
            except Exception:
                pass
            try:
                self.player.set_property("current-text", -1)
            except Exception:
                pass

            # Cache le handle et le pose sur le sink *avant* PLAYING
            # (évite la fenêtre externe « OpenGL renderer »).
            if self.current_is_video:
                try:
                    while Gtk.events_pending():
                        Gtk.main_iteration_do(False)
                except Exception:
                    pass
                self._cache_video_window_handle()
                self._apply_video_overlay_handle()

            used_custom = False
            # Rangs AV1 déjà fixés au démarrage (apply_safe_gstreamer_ranks)

            if self._should_use_custom_pipeline(filename):
                used_custom = self._play_file_custom_mkv(filename)
                if not used_custom:
                    if DEBUG_MYOSTOOX:
                        print(
                            "Myostoox: pipeline MKV custom échoué → playbin",
                            file=sys.stderr,
                        )
                else:
                    # suburi playbin ignoré par le pipeline custom → branchement direct
                    try:
                        self._maybe_attach_external_to_custom()
                    except Exception as e:
                        print("ext sub custom:", e, file=sys.stderr)
                    # Pistes embarquées peuvent arriver en retard (pad-added)
                    try:
                        GLib.timeout_add(1200, self._maybe_attach_external_to_custom)
                    except Exception:
                        pass

            if not used_custom:
                self._destroy_custom_pipeline()
                self.player.set_property("uri", uri)
                # Pipeline unifié audio/vidéo : NULL → PLAYING
                self.player.set_state(Gst.State.PLAYING)

            self.is_playing = True
            try:
                self._update_screensaver_inhibit()
            except Exception:
                pass

            self.is_paused = False
            self.play_button.set_label("❚❚")
            self.status_label.set_text(self.tr("Lecture"))
            try:
                self._announce_track_change(filename)
            except Exception:
                pass
            try:
                self._push_recent_file(filename)
            except Exception:
                pass

            self.update_information(filename)
            self.select_filename(filename)
            self._mpris_notify_playback()

        except Exception as e:
            self.status_label.set_text(self.tr("Erreur : ") + str(e))
            print("Erreur lecture:", e, file=sys.stderr)

    def seek_to_cdda_track(self, track_num):

        fmt = Gst.Format.get_by_nick("track")
        if fmt == Gst.Format.UNDEFINED:
            return False
        try:
            return self.player.seek_simple(
                fmt,
                Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                track_num
            )
        except Exception:
            return False

    def select_filename(self, filename):

        iterator = self.playlist.get_iter_first()
        while iterator is not None:
            if self.playlist[iterator][self.COL_PATH] == filename:
                child_path = self.playlist.get_path(iterator)
                filter_path = self.playlist_filter.convert_child_path_to_path(child_path)
                if filter_path is not None:
                    sel = self.tree.get_selection()
                    blocked = False
                    try:
                        sel.handler_block_by_func(self.selection_changed)
                        blocked = True
                    except Exception:
                        pass
                    try:
                        sel.unselect_all()
                        sel.select_path(filter_path)
                        self.tree.set_cursor(filter_path)
                        self.tree.scroll_to_cell(filter_path, None, True, 0.5, 0)
                    finally:
                        if blocked:
                            try:
                                sel.handler_unblock_by_func(self.selection_changed)
                            except Exception:
                                pass
                return
            iterator = self.playlist.iter_next(iterator)

    def toggle_play(self, *args):
        """Sélection différente → lecture ; sinon pause/reprise via toggle_pause."""
        selection = self.tree.get_selection()
        model, paths = selection.get_selected_rows()
        selected_path = None
        if paths:
            try:
                cursor_path, _ = self.tree.get_cursor()
            except Exception:
                cursor_path = None
            path = cursor_path if cursor_path is not None else paths[0]
            try:
                iterator = model.get_iter(path)
                if iterator is not None:
                    selected_path = model[iterator][self.COL_PATH]
            except Exception:
                selected_path = None

        if selected_path is not None and (
            self.current_path is None or selected_path != self.current_path
        ):
            self.play_file(selected_path)
            return

        if self.current_path is None:
            iterator = self.playlist.get_iter_first()
            if iterator is not None:
                self.play_file(self.playlist[iterator][self.COL_PATH])
            return

        self.toggle_pause()

    def toggle_pause(self, *args):
        """Pause / reprise du média courant (sans changer de piste)."""
        if self.current_path is None:
            return

        if self.is_playing and not self.is_paused:
            self._active_pipeline().set_state(Gst.State.PAUSED)
            self.is_paused = True
            self.play_button.set_label("▶")
            self.status_label.set_text(self.tr("Pause"))
            # Capture le dernier frame pour l'afficher en pause
            # (évite le fantôme après masquage de la fenêtre)
            if self.current_is_video:
                try:
                    if self.video_sink is not None and isinstance(
                        self.video_sink, GstVideo.VideoOverlay
                    ):
                        GstVideo.VideoOverlay.expose(self.video_sink)
                except Exception:
                    pass
                # Capture légèrement différée pour laisser le sink finir de dessiner
                GLib.timeout_add(40, self._capture_pause_frame_and_redraw)
            self.show_osd("❚❚  " + self.tr("Pause"))
            self._mpris_notify_playback()
            try:
                self._update_screensaver_inhibit()
            except Exception:
                pass
        else:
            # Après un Stop le pipeline est à NULL : il faut relancer via play_file
            # pour réafficher la zone vidéo et rattacher l'overlay (sinon fenêtre externe).
            try:
                _ret, state, _pending = self._active_pipeline().get_state(0)
            except Exception:
                state = Gst.State.NULL

            if state == Gst.State.NULL:
                self.play_file(self.current_path)
                return

            # Reprise depuis Pause
            needs_video = (
                is_video_file(self.current_path)
                or is_dvd(self.current_path)
                or is_bluray(self.current_path)
                or getattr(self, "_prefer_video", False)
                or getattr(self, "_resume_prefer_video", False)
                or getattr(self, "current_is_video", False)
                or (
                    is_stream(self.current_path)
                    and (
                        getattr(self, "current_tv_name", None)
                        or self.find_tv_name(self.current_path)
                        or getattr(self, "bouquet_active", False)
                    )
                )
            )
            if needs_video:
                if self.is_fullscreen:
                    # Ne pas appeler show_video_area (réafficherait la playlist)
                    self._apply_fullscreen_ui_layout()
                    self._cache_video_window_handle()
                else:
                    self.show_video_area(True)

            self._active_pipeline().set_state(Gst.State.PLAYING)
            self.is_playing = True
            self.is_paused = False
            self._pause_pixbuf = None
            self.play_button.set_label("❚❚")
            self.status_label.set_text(self.tr("Lecture"))
            if self.current_is_video:
                self._nudge_video_redraw()
            self.show_osd("▶  " + self.tr("Lecture"))
            self._mpris_notify_playback()
            try:
                self._update_screensaver_inhibit()
            except Exception:
                pass

    def stop(self, *args):

        self._playback_generation += 1
        self._cancel_seek_release_timeout()
        self._seek_in_progress = False
        self._pending_seek_delta = 0.0
        self._last_known_position = 0.0
        self._destroy_custom_pipeline()
        try:
            self.player.set_state(Gst.State.NULL)
        except Exception:
            pass
        self.is_playing = False
        self.is_paused = False
        self._pause_pixbuf = None
        self.current_is_cdda = False
        self.icy_title = ""
        self.icy_artist = ""
        self._resume_prefer_video = bool(getattr(self, "current_is_video", False))
        self.current_radio_name = None
        if not self._resume_prefer_video:
            self.current_tv_name = None
        self.subtitle_tracks = []
        self.current_subtitle_index = -1
        self._preferred_custom_sub_index = -1
        self.external_subtitles = []
        self._active_external_sub_path = None
        self._clear_chapters()
        try:
            self.player.set_property("suburi", None)
        except Exception:
            pass
        self.play_button.set_label("▶")
        try:
            self.position_scale.set_value(0)
        except Exception:
            pass
        self.time_label.set_text(
            "0:00 / " + format_time(self.current_duration)
        )
        self.status_label.set_text(self.tr("Arrêt"))
        self.reset_spectrum()
        self.lyrics_mode = False
        self.lyrics_data = None
        self.lyrics_current_index = -1
        try:
            self._inhibit_screensaver(False)
        except Exception:
            pass
        self.show_video_area(False)
        try:
            self._update_pitch_menu_sensitivity()
        except Exception:
            pass
        try:
            self._update_colorimetry_menu_sensitivity()
        except Exception:
            pass
        self._mpris_notify_playback()

    # ========================================================
    # NAVIGATION
    # ========================================================

    def get_paths(self):

        paths = []
        iterator = self.playlist.get_iter_first()
        while iterator is not None:
            paths.append(self.playlist[iterator][self.COL_PATH])
            iterator = self.playlist.iter_next(iterator)
        return paths

    def next(self, *args, auto=False):
        paths = self.get_paths()
        if not paths:
            return

        # Répétition d'un morceau précis : uniquement en EOS (auto=True).
        if auto and self.repeat_track_path and self.repeat_track_path in paths:
            next_path = self.repeat_track_path
        elif self.current_path not in paths:
            next_path = paths[0]
        elif self.shuffle_mode and len(paths) > 1:
            # En mode aléatoire, on utilise une file mélangée afin d'éviter
            # les répétitions immédiates et de parcourir tous les morceaux
            # avant de recommencer un nouveau cycle aléatoire.
            available = [path for path in paths if path != self.current_path]

            # Nettoie la file si la playlist a changé depuis son remplissage.
            self.shuffle_queue = [
                path for path in self.shuffle_queue
                if path in available
            ]

            if not self.shuffle_queue:
                self.shuffle_queue = available[:]
                random.shuffle(self.shuffle_queue)

            next_path = self.shuffle_queue.pop(0)
        else:
            index = paths.index(self.current_path)
            next_index = index + 1

            if next_index >= len(paths):
                if self.repeat_mode:
                    next_index = 0
                else:
                    self.stop()
                    self.status_label.set_text(self.tr("Fin de la playlist"))
                    return

            next_path = paths[next_index]

        if (self.current_is_cdda
                and is_cdda(next_path)
                and self.current_path.startswith("cdda://")
                and next_path.startswith("cdda://")):

            track = get_cdda_track_number(next_path)
            if track is not None and self.seek_to_cdda_track(track):
                self.current_path = next_path
                self.current_duration = 0
                self.reset_spectrum()
                self.update_information(next_path)
                self.select_filename(next_path)
                self.status_label.set_text(self.tr("Lecture"))
                return

        self.play_file(next_path)

    def previous(self, *args):
        paths = self.get_paths()
        if not paths:
            return

        if self.current_path not in paths:
            self.play_file(paths[0])
            return

        index = paths.index(self.current_path)
        previous_index = index - 1
        if previous_index < 0:
            previous_index = len(paths) - 1

        prev_path = paths[previous_index]

        if (self.current_is_cdda
                and is_cdda(prev_path)
                and self.current_path.startswith("cdda://")
                and prev_path.startswith("cdda://")):

            track = get_cdda_track_number(prev_path)
            if track is not None and self.seek_to_cdda_track(track):
                self.current_path = prev_path
                self.current_duration = 0
                self.reset_spectrum()
                self.update_information(prev_path)
                self.select_filename(prev_path)
                self.status_label.set_text(self.tr("Lecture"))
                return

        self.play_file(prev_path)

    def toggle_shuffle(self, button):
        self.shuffle_mode = not self.shuffle_mode
        self.shuffle_queue = []

        if self.shuffle_mode:
            # Prépare immédiatement un premier cycle aléatoire. Le morceau
            # en cours est exclu : il ne sera donc pas relancé par le prochain.
            paths = self.get_paths()
            if self.current_path in paths:
                self.shuffle_queue = [
                    path for path in paths
                    if path != self.current_path
                ]
                random.shuffle(self.shuffle_queue)

            button.get_style_context().add_class("active")
            button.set_tooltip_text(self.tr("Lecture aléatoire activée"))
            self.status_label.set_text(self.tr("Lecture aléatoire activée"))
        else:
            button.get_style_context().remove_class("active")
            button.set_tooltip_text(self.tr("Lecture aléatoire"))
            self.status_label.set_text(self.tr("Lecture aléatoire désactivée"))

    def toggle_repeat(self, button):
        # Trois états : désactivé -> playlist -> morceau sélectionné -> désactivé.
        if self.repeat_track_path is not None:
            self.repeat_track_path = None
            self.repeat_mode = False
            button.get_style_context().remove_class("active")
            button.set_tooltip_text(self.tr("Répéter la playlist"))
            self.status_label.set_text(self.tr("Répétition désactivée"))
            return

        selection = self.tree.get_selection()
        model, paths = selection.get_selected_rows()
        selected_path = None
        if paths:
            try:
                cursor_path, _ = self.tree.get_cursor()
            except Exception:
                cursor_path = None
            path = cursor_path if cursor_path is not None else paths[0]
            try:
                iterator = model.get_iter(path)
                if iterator is not None:
                    selected_path = model[iterator][self.COL_PATH]
            except Exception:
                selected_path = None

        if self.repeat_mode:
            if selected_path is not None:
                self.repeat_mode = False
                self.repeat_track_path = selected_path
                button.get_style_context().add_class("active")
                button.set_tooltip_text(self.tr("Répétition du morceau sélectionné activée"))
                self.status_label.set_text(self.tr("Répétition du morceau sélectionné"))
            else:
                self.repeat_mode = False
                button.get_style_context().remove_class("active")
                button.set_tooltip_text(self.tr("Répéter la playlist"))
                self.status_label.set_text(self.tr("Répétition désactivée"))
            return

        self.repeat_mode = True
        button.get_style_context().add_class("active")
        button.set_tooltip_text(self.tr("Répétition de la playlist activée"))
        self.status_label.set_text(self.tr("Répétition de la playlist"))

    def move_playlist_selection(self, direction):
        model = self.tree.get_model()
        selection = self.tree.get_selection()
        current_path, current_column = self.tree.get_cursor()

        if current_path is None:
            model_sel, paths = selection.get_selected_rows()
            if paths:
                current_path = paths[0]

        if current_path is None:
            target_path = Gtk.TreePath.new_first()
        else:
            target_path = current_path.copy()
            if direction < 0:
                if not target_path.prev():
                    return
            else:
                target_path.next()

        try:
            model.get_iter(target_path)
        except ValueError:
            return

        selection.unselect_all()
        selection.select_path(target_path)
        self.tree.set_cursor(target_path, current_column, False)
        self.tree.scroll_to_cell(target_path, None, True, 0.5, 0)

    # ========================================================
    # BUS GSTREAMER
    # ========================================================

    def on_gstreamer_message(self, bus, message):

        if message.type == Gst.MessageType.EOS:
            # Ignore les EOS d'un pipeline déjà détruit / remplacé
            # (ex. bascule MKV custom → MP4 playbin : l'EOS retardé du MKV
            # relançait la piste suivante = l'ancien fichier).
            try:
                active = self._active_pipeline()
                src = message.src
                if active is None or src is None:
                    return
                if src != active:
                    try:
                        parent = src
                        belongs = False
                        for _ in range(12):
                            if parent is None:
                                break
                            if parent == active:
                                belongs = True
                                break
                            parent = parent.get_parent()
                        if not belongs:
                            return
                    except Exception:
                        return
            except Exception:
                pass
            self.next(auto=True)
            return

        # Table des matières (chapitres MKV / conteneurs)
        if message.type == Gst.MessageType.TOC:
            try:
                toc, _updated = message.parse_toc()
                self._ingest_toc(toc)
            except Exception:
                pass
            return

        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            print("GStreamer :", error, file=sys.stderr)
            if debug:
                print("GStreamer debug:", debug, file=sys.stderr)

            # Mode Matériel + média vidéo : un seul repli logiciel par fichier
            path = getattr(self, "current_path", None)
            can_fallback = (
                getattr(self, "performance_mode", "hardware") == "hardware"
                and path
                and getattr(self, "_hw_fallback_path", None) != path
                and (
                    getattr(self, "current_is_video", False)
                    or is_video_file(path)
                    or is_dvd(path)
                    or is_bluray(path)
                )
            )
            if can_fallback:
                self._hw_fallback_path = path
                print(
                    "Myostoox: échec décodage matériel → repli logiciel",
                    file=sys.stderr,
                )
                try:
                    self.status_label.set_text(
                        self.tr("Repli logiciel…")
                    )
                except Exception:
                    pass
                try:
                    self._apply_decoder_performance_mode(force_software=True)
                except Exception as e:
                    print("fallback ranks:", e, file=sys.stderr)
                def _retry_soft(p=path):
                    try:
                        self.play_file(p)
                    except Exception as e:
                        print("fallback play:", e, file=sys.stderr)
                    return False
                GLib.idle_add(_retry_soft)
                return

            self.status_label.set_text(self.tr("Erreur : ") + str(error))
            self.is_playing = False
            self.is_paused = False
            self.play_button.set_label("▶")
            self.reset_spectrum()
            return

        # ASYNC_DONE : durée + fin de seek utilisateur
        if message.type == Gst.MessageType.ASYNC_DONE:
            self._try_update_duration()
            if self._seek_in_progress:
                self._seek_in_progress = False
                self._cancel_seek_release_timeout()
                try:
                    ok, pos = self.player.query_position(Gst.Format.TIME)
                    if ok and pos >= 0:
                        self._last_known_position = pos / Gst.SECOND
                except Exception:
                    pass
                if self.current_is_video:
                    self._nudge_video_redraw()
                if abs(self._pending_seek_delta) >= 0.001:
                    GLib.idle_add(self._apply_pending_seek_delta)
            return

        if message.type == Gst.MessageType.DURATION_CHANGED:
            self._try_update_duration()
            return

        if message.type == Gst.MessageType.STATE_CHANGED:
            if message.src == self.player:
                _, new_state, _ = message.parse_state_changed()
                if new_state in (Gst.State.PAUSED, Gst.State.PLAYING):
                    self._try_update_duration()
                # Détection des pistes sur l'état réel (PAUSED atteint), plus fiable
                # sur les gros MKV qu'un délai fixe.
                if new_state == Gst.State.PAUSED and self.current_is_video:
                    gen = self._playback_generation
                    def _refresh_tracks(generation=gen):
                        if generation != self._playback_generation:
                            return False
                        self.refresh_subtitle_tracks()
                        self.refresh_audio_tracks()
                        return False
                    GLib.idle_add(_refresh_tracks)
                if new_state == Gst.State.PLAYING and self.current_is_video:
                    self._nudge_video_redraw()
                    # Ne plus modifier les queues en cours de lecture :
                    # cela provoquait des freezes MKV (sous-débit multiqueue).
            return

        # Métadonnées ICY / tags des flux
        if message.type == Gst.MessageType.TAG:
            taglist = message.parse_tag()
            if taglist:
                # Titre
                ok, title = taglist.get_string(Gst.TAG_TITLE)
                if ok and title:
                    self.icy_title = title.strip()
                # Artiste
                ok, artist = taglist.get_string(Gst.TAG_ARTIST)
                if ok and artist:
                    self.icy_artist = artist.strip()

                # Mise à jour de l'affichage si on est sur un flux
                if self.current_path and is_stream(self.current_path):
                    self.update_information(self.current_path)
            return

        if message.type != Gst.MessageType.ELEMENT:
            return

        structure = message.get_structure()
        if structure is None:
            return

        if structure.get_name() != "spectrum":
            return

        self.process_spectrum(structure)

    # ========================================================
    # POSITION & VOLUME
    # ========================================================

    def update_position(self):

        if getattr(self, "_app_closing", False):
            return False
        if not self.is_playing:
            return True
        # En pause pure (sans paroles synchronisées) : pas de query coûteuse
        if self.is_paused and not (
            getattr(self, "lyrics_mode", False)
            and getattr(self, "lyrics_data", None)
            and self.lyrics_data.get("timed")
        ):
            return True

        try:
            ok, position = self._active_pipeline().query_position(Gst.Format.TIME)
            if not ok or position < 0:
                self._try_update_duration()
                return True

            position_seconds = position / Gst.SECOND

            # Pendant un seek : ne pas laisser query_position écraser la
            # cible (sinon KEY_UNIT / latence MKV fait « revenir en arrière »
            # sur les sauts clavier ±5 s).
            if not self._seek_in_progress:
                if position_seconds > 0.05 or self._last_known_position <= 0.05:
                    self._last_known_position = position_seconds

            if getattr(self, "lyrics_mode", False) and getattr(self, "lyrics_data", None):
                try:
                    self._update_lyrics_index(self._last_known_position)
                    if not self.lyrics_data.get("timed") and self.video_area is not None:
                        self.video_area.queue_draw()
                except Exception:
                    pass

            self._try_update_duration()

            display_position = self._last_known_position

            if not self.user_seeking and self.current_duration > 0:
                value = min(max(0.0, display_position), self.current_duration)
                self._updating_position_scale = True
                try:
                    self.position_scale.set_value(value)
                finally:
                    self._updating_position_scale = False

            if self.current_duration <= 0 and is_stream(self.current_path):
                self.time_label.set_text(f"{format_time(display_position)} / ∞")
            else:
                self.time_label.set_text(
                    f"{format_time(display_position)} / {format_time(self.current_duration)}"
                )
            self._sync_fs_transport_ui()

        except Exception:
            pass

        return True

    def seek_press(self, widget, event):
        if event.button == 1:
            self.user_seeking = True
        return False

    def seek_release(self, widget, event):
        if event.button == 1:
            self.user_seeking = False
            try:
                self.seek_to(widget.get_value())
            except Exception as e:
                print("seek_release:", e, file=sys.stderr)
            # Resync les deux barres après le seek OSD / fenêtre
            try:
                self._sync_fs_transport_ui()
            except Exception:
                pass
        return False

    def seek_value_changed(self, scale):
        if self._updating_position_scale:
            return
        value = scale.get_value()
        if self.user_seeking:
            txt = f"{format_time(value)} / {format_time(self.current_duration)}"
            try:
                self.time_label.set_text(txt)
            except Exception:
                pass
            try:
                if getattr(self, "fs_time_label", None) is not None:
                    self.fs_time_label.set_text(txt)
            except Exception:
                pass
            # Pendant un drag sur la barre OSD, tenir aussi position_scale
            # pour que seek_to et le sync restent cohérents
            try:
                if scale is getattr(self, "fs_scale", None):
                    self._updating_position_scale = True
                    try:
                        self.position_scale.set_value(value)
                    finally:
                        self._updating_position_scale = False
            except Exception:
                pass

    def _cancel_seek_release_timeout(self):
        tid = getattr(self, "_seek_release_timeout_id", 0) or 0
        if tid:
            try:
                GLib.source_remove(tid)
            except Exception:
                pass
            self._seek_release_timeout_id = 0

    def seek_to(self, seconds, accurate=False):
        """Seek vers `seconds`.

        accurate=True (flèches) : position exacte (évite snap KEY_UNIT en arrière).
        accurate=False (barre) : KEY_UNIT, plus réactif au scrubbing.
        """
        try:
            if not self.current_path:
                return False

            self._try_update_duration()
            target = max(0.0, float(seconds))
            if self.current_duration > 0:
                target = min(target, self.current_duration)

            flags = (
                Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE
                if accurate
                else Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT
            )

            self._cancel_seek_release_timeout()
            self._seek_in_progress = True

            pipe = self._active_pipeline()
            ns = int(target * Gst.SECOND)
            success = False
            if self._custom_pipeline is not None:
                # seek_simple uniquement. ACCURATE pour autoriser le retour arrière
                # (KEY_UNIT seul refuse souvent les seeks vers le passé sur matroska).
                flags_c = Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE
                was_playing = self.is_playing and not self.is_paused
                try:
                    pipe.set_state(Gst.State.PAUSED)
                    pipe.get_state(int(600 * Gst.MSECOND))
                    success = bool(
                        pipe.seek_simple(Gst.Format.TIME, flags_c, int(ns))
                    )
                    if not success:
                        # Repli KEY_UNIT
                        success = bool(
                            pipe.seek_simple(
                                Gst.Format.TIME,
                                Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                                int(ns),
                            )
                        )
                    pipe.get_state(int(600 * Gst.MSECOND))
                    if was_playing:
                        pipe.set_state(Gst.State.PLAYING)
                except Exception as e:
                    print("seek custom:", e, file=sys.stderr)
                    success = False
                    try:
                        if was_playing:
                            pipe.set_state(Gst.State.PLAYING)
                    except Exception:
                        pass
            else:
                try:
                    success = bool(
                        pipe.seek_simple(Gst.Format.TIME, flags, int(ns))
                    )
                except Exception:
                    success = False
            if not success:
                self._seek_in_progress = False
                return False

            self._last_known_position = target
            self._updating_position_scale = True
            try:
                if self.current_duration > 0:
                    self.position_scale.set_value(target)
            finally:
                self._updating_position_scale = False
            self.time_label.set_text(
                f"{format_time(target)} / {format_time(self.current_duration)}"
            )
            self._sync_fs_transport_ui()

            # Filet si ASYNC_DONE n'arrive pas (certains flux / formats)
            gen = self._playback_generation

            def _release_seek_lock(generation=gen):
                self._seek_release_timeout_id = 0
                if generation != self._playback_generation:
                    return False
                if self._seek_in_progress:
                    self._seek_in_progress = False
                    if abs(self._pending_seek_delta) >= 0.001:
                        self._apply_pending_seek_delta()
                return False

            self._seek_release_timeout_id = GLib.timeout_add(
                250, _release_seek_lock
            )

            if self.is_playing and not self.is_paused:
                GLib.timeout_add(80, self._ensure_playing_after_seek)
                if self._custom_pipeline is not None:
                    GLib.timeout_add(250, self._ensure_playing_after_seek)
            return True
        except Exception:
            self._seek_in_progress = False
            return False


    def _ensure_playing_after_seek(self):

        """Après seek flush : relance PLAYING si le pipeline est resté en PAUSED."""
        try:
            if not self.is_playing or self.is_paused:
                return False
            pipe = self._active_pipeline()
            ret, state, pending = pipe.get_state(100 * Gst.MSECOND)
            if state != Gst.State.PLAYING:
                pipe.set_state(Gst.State.PLAYING)
            if self.current_is_video:
                self._nudge_video_redraw()
        except Exception:
            pass
        return False


    def _apply_pending_seek_delta(self):
        if abs(self._pending_seek_delta) < 0.001:
            return False
        delta = self._pending_seek_delta
        self._pending_seek_delta = 0.0
        base = self._last_known_position
        if self.current_duration > 0:
            base = min(max(0.0, base), self.current_duration)
        self.seek_to(base + delta, accurate=True)
        return False

    def _try_update_duration(self):
        """Récupère la durée GStreamer dès qu'elle devient disponible."""
        try:
            if not self.current_path:
                return False
            ok, duration = self._active_pipeline().query_duration(Gst.Format.TIME)
            if not ok or duration <= 0:
                return False
            duration_seconds = duration / Gst.SECOND
            if duration_seconds <= 0:
                return False

            if self.current_duration <= 0 or abs(duration_seconds - self.current_duration) >= 0.05:
                self.current_duration = duration_seconds
                self.position_scale.set_range(0, duration_seconds)
                self._update_playlist_duration(self.current_path, duration_seconds)

            if not self.user_seeking:
                value = min(self.position_scale.get_value(), duration_seconds)
                self.time_label.set_text(
                    f"{format_time(value)} / {format_time(duration_seconds)}"
                )
            return True
        except Exception:
            return False

    def _update_playlist_duration(self, filename, duration):
        """Met à jour la durée affichée dans la playlist pour un fichier donné."""
        try:
            iterator = self.playlist.get_iter_first()
            while iterator is not None:
                if self.playlist[iterator][self.COL_PATH] == filename:
                    old = float(self.playlist[iterator][self.COL_DURATION] or 0)
                    if old <= 0 or abs(old - duration) > 1.0:
                        self.playlist[iterator][self.COL_DURATION_TEXT] = format_time(duration)
                        self.playlist[iterator][self.COL_DURATION] = float(duration)
                        # Met aussi à jour le cache métadonnées
                        if filename in self.metadata_cache:
                            self.metadata_cache[filename]["duration"] = duration
                    return
                iterator = self.playlist.iter_next(iterator)
        except Exception:
            pass

    def seek_relative(self, seconds):
        """Saut relatif (flèches). Seek ACCURATE pour éviter le retour KEY_UNIT."""
        try:
            if not self.current_path:
                return

            delta = float(seconds)

            def _clamp(t):
                t = max(0.0, t)
                if self.current_duration > 0:
                    t = min(t, self.current_duration)
                return t

            # Seek déjà en cours : recalcule la cible et relance (FLUSH annule l'ancien)
            if self._seek_in_progress:
                target = _clamp(
                    self._last_known_position + self._pending_seek_delta + delta
                )
                self._pending_seek_delta = 0.0
                self.seek_to(target, accurate=True)
                return

            ok, position = self._active_pipeline().query_position(Gst.Format.TIME)
            queried = position / Gst.SECOND if ok and position >= 0 else None
            last = float(self._last_known_position or 0.0)

            if queried is None:
                current = last
            elif abs(queried - last) <= 2.5:
                current = max(last, queried) if delta > 0 else min(last, queried)
            else:
                current = last if last > 0 else queried

            self.seek_to(_clamp(current + delta), accurate=True)
        except Exception:
            pass

    def volume_changed(self, scale):
        value = scale.get_value()
        if not self.is_muted:
            self._set_playback_volume(value)
            self._volume_before_mute = value
        else:
            # L'utilisateur remonte le volume → désactive le muet
            if value > 0.001:
                self.is_muted = False
                self._set_playback_volume(value)
                self._volume_before_mute = value
                self._update_mute_button()
        self._mpris_properties_changed(
            {"Volume": GLib.Variant("d", float(value))}
        )
        self.show_osd(f"♪  {int(round(value * 100))} %")

    def adjust_volume(self, delta):
        """Ajuste le volume (touches média / raccourcis)."""
        try:
            current = self.volume_scale.get_value()
            if self.is_muted and delta > 0:
                self.is_muted = False
                self._update_mute_button()
            new_val = max(0.0, min(1.0, current + float(delta)))
            self.volume_scale.set_value(new_val)
        except Exception:
            pass

    def toggle_mute(self, *args):
        try:
            if not self.is_muted:
                self._volume_before_mute = max(
                    0.01, self.volume_scale.get_value()
                )
                self.is_muted = True
                self._set_playback_volume(0.0)
                self.show_osd("⌀  " + self.tr("Muet"))
            else:
                self.is_muted = False
                vol = self._volume_before_mute or 0.75
                self._set_playback_volume(vol)
                self.volume_scale.handler_block_by_func(self.volume_changed)
                self.volume_scale.set_value(vol)
                self.volume_scale.handler_unblock_by_func(self.volume_changed)
                self.show_osd("♪  " + self.tr("Son activé"))
            self._update_mute_button()
            self._mpris_properties_changed(
                {"Volume": GLib.Variant("d", 0.0 if self.is_muted else float(self.volume_scale.get_value()))}
            )
        except Exception as e:
            print("toggle_mute:", e, file=sys.stderr)

    def _update_mute_button(self):
        if not hasattr(self, "mute_button") or self.mute_button is None:
            return
        if self.is_muted:
            self.mute_button.set_label("⌀")
            self.mute_button.set_tooltip_text(self.tr("Son activé") + " (M)")
        else:
            self.mute_button.set_label("♪")
            self.mute_button.set_tooltip_text(self.tr("Muet") + " (M)")

    # ========================================================
    # CURSEUR PLEIN ÉCRAN (auto-masquage 4 s)
    # ========================================================

    def _build_fullscreen_controls(self):
        """Barre transparente bas d'écran : transport + seek + timer."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.set_halign(Gtk.Align.FILL)
        bar.set_valign(Gtk.Align.END)
        bar.set_margin_start(16)
        bar.set_margin_end(16)
        bar.set_margin_bottom(12)
        bar.set_no_show_all(True)
        bar.hide()
        try:
            bar.get_style_context().add_class("fs-controls")
        except Exception:
            pass

        def _btn(label, cb):
            b = Gtk.Button(label=label)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.connect("clicked", cb)
            try:
                b.get_style_context().add_class("fs-ctrl-btn")
            except Exception:
                pass
            return b

        bar.pack_start(
            _btn("⏮", lambda *a: self.previous()), False, False, 0
        )
        self.fs_play_btn = _btn("❚❚", lambda *a: self.toggle_play())
        bar.pack_start(self.fs_play_btn, False, False, 0)

        def _stop_fs(*_a):
            try:
                if self.is_fullscreen:
                    self.toggle_video_fullscreen()
            except Exception:
                pass
            self.stop()

        bar.pack_start(_btn("■", _stop_fs), False, False, 0)
        bar.pack_start(_btn("⏭", lambda *a: self.next()), False, False, 0)

        self.fs_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 1, 0.1
        )
        self.fs_scale.set_draw_value(False)
        self.fs_scale.set_hexpand(True)
        self.fs_scale.connect(
            "button-press-event", self.seek_press
        )
        self.fs_scale.connect(
            "button-release-event", self.seek_release
        )
        self.fs_scale.connect("value-changed", self.seek_value_changed)
        bar.pack_start(self.fs_scale, True, True, 0)

        self.fs_time_label = Gtk.Label(label="0:00 / 0:00")
        self.fs_time_label.get_style_context().add_class("fs-time")
        self.fs_time_label.set_margin_start(6)
        self.fs_time_label.set_margin_end(6)
        bar.pack_start(self.fs_time_label, False, False, 0)

        # Capture d'écran rapide (BMP → Téléchargements)
        self.fs_shot_btn = Gtk.Button(label="⎙")
        self.fs_shot_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.fs_shot_btn.connect("clicked", self._fs_quick_screenshot)
        try:
            self.fs_shot_btn.get_style_context().add_class("fs-ctrl-btn")
        except Exception:
            pass
        bar.pack_start(self.fs_shot_btn, False, False, 0)

        for child in bar.get_children():
            child.show_all()

        self.fs_controls = bar
        self._fs_controls_visible = False
        self.video_overlay.add_overlay(bar)
        # La barre consomme les events souris pour rester visible au survol
        bar.add_events(
            Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        bar.connect("enter-notify-event", self._on_fs_controls_enter)
        bar.connect("leave-notify-event", self._on_fs_controls_leave)
        bar.connect("motion-notify-event", self._on_fs_controls_motion)


    def _show_fs_toast(self, corner, text, duration_ms=1800):
        """Message bref en coin (tl / tr) en plein écran uniquement."""
        if not getattr(self, "is_fullscreen", False):
            return
        label = (
            getattr(self, "fs_toast_tl", None)
            if corner == "tl"
            else getattr(self, "fs_toast_tr", None)
        )
        if label is None:
            return
        try:
            label.set_text(str(text))
            label.show()
        except Exception:
            return
        attr = (
            "_fs_toast_tl_timeout"
            if corner == "tl"
            else "_fs_toast_tr_timeout"
        )
        tid = getattr(self, attr, 0) or 0
        if tid:
            try:
                GLib.source_remove(tid)
            except Exception:
                pass

        def _hide(a=attr, lab=label):
            setattr(self, a, 0)
            try:
                lab.hide()
            except Exception:
                pass
            return False

        setattr(self, attr, GLib.timeout_add(duration_ms, _hide))

    def _fs_quick_screenshot(self, *args):
        """Capture BMP sans la barre FS : masque l'UI, capture, toast."""
        if not self.current_is_video or self.video_area is None:
            return
        # Masque barre + toasts pour ne pas les graver dans l'image
        try:
            self._hide_fs_controls()
        except Exception:
            pass
        for lab in (
            getattr(self, "fs_toast_tl", None),
            getattr(self, "fs_toast_tr", None),
            getattr(self, "osd_label", None),
        ):
            if lab is not None:
                try:
                    lab.hide()
                except Exception:
                    pass
        # Laisse le compositeur redessiner une frame sans chrome
        while Gtk.events_pending():
            Gtk.main_iteration()
        GLib.timeout_add(60, self._fs_quick_screenshot_do)
        return

    def _fs_quick_screenshot_do(self):
        try:
            window = self.video_area.get_window() if self.video_area else None
            if window is None:
                return False
            width = window.get_width()
            height = window.get_height()
            if width <= 0 or height <= 0:
                return False
            pixbuf = Gdk.pixbuf_get_from_window(window, 0, 0, width, height)
            if pixbuf is None:
                return False
            try:
                downloads = GLib.get_user_special_dir(
                    GLib.UserDirectory.DIRECTORY_DOWNLOAD
                )
            except Exception:
                downloads = None
            if not downloads:
                downloads = os.path.expanduser("~/Téléchargements")
                if not os.path.isdir(downloads):
                    downloads = os.path.expanduser("~/Downloads")
            os.makedirs(downloads, exist_ok=True)
            from datetime import datetime
            name = "capture_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".bmp"
            path = os.path.join(downloads, name)
            pixbuf.savev(path, "bmp", [], [])
            self._show_fs_toast("tr", self.tr("Image capturée !"), 1600)
            self.status_label.set_text(self.tr("Capture enregistrée"))
        except Exception as e:
            print("FS capture:", e, file=sys.stderr)
        return False

    def _announce_track_change(self, path):
        """OSD haut-gauche : nom du fichier au changement de plage (FS)."""
        if not path:
            return
        try:
            if is_stream(path) or is_cdda(path) or is_dvd(path) or is_bluray(path):
                name = path
            else:
                name = os.path.basename(path)
        except Exception:
            name = str(path)
        # Titre playlist si dispo
        try:
            for row in self.playlist:
                if row[self.COL_PATH] == path:
                    t = row[self.COL_TITLE]
                    if t:
                        name = t
                    break
        except Exception:
            pass
        self._show_fs_toast("tl", name, 2000)

    def _on_fs_controls_enter(self, *args):
        self._show_cursor()
        return False

    def _on_fs_controls_leave(self, widget, event):
        # Ne pas masquer pendant un scrub (sinon button-release perdu → seek cassé)
        if getattr(self, "user_seeking", False):
            return False
        if self.is_fullscreen:
            self._hide_fs_controls()
        return False

    def _on_fs_controls_motion(self, *args):
        self._show_cursor()
        return False


    def _sync_fs_transport_ui(self):
        """Met à jour scale / timer / bouton play de la barre plein écran."""
        if not getattr(self, "is_fullscreen", False):
            return
        if (
            not getattr(self, "_fs_controls_visible", False)
            and not getattr(self, "user_seeking", False)
        ):
            return
        try:
            if getattr(self, "fs_play_btn", None) is not None:
                self.fs_play_btn.set_label(
                    "❚❚" if (self.is_playing and not self.is_paused) else "▶"
                )
            if getattr(self, "fs_time_label", None) is not None and getattr(
                self, "time_label", None
            ) is not None:
                self.fs_time_label.set_text(self.time_label.get_text())
            if getattr(self, "fs_scale", None) is not None and getattr(
                self, "position_scale", None
            ) is not None:
                dur = max(1.0, float(self.current_duration) or 1.0)
                self.fs_scale.set_range(0, dur)
                if not getattr(self, "_updating_position_scale", False):
                    self._updating_position_scale = True
                    try:
                        self.fs_scale.set_value(self.position_scale.get_value())
                    finally:
                        self._updating_position_scale = False
        except Exception:
            pass

    def _show_fs_controls(self):
        if not self.is_fullscreen:
            return
        bar = getattr(self, "fs_controls", None)
        if bar is None:
            return
        try:
            # Sync play button / scale / time
            if getattr(self, "fs_play_btn", None) is not None:
                self.fs_play_btn.set_label(
                    "❚❚" if (self.is_playing and not self.is_paused) else "▶"
                )
            if getattr(self, "fs_scale", None) is not None and getattr(
                self, "position_scale", None
            ) is not None:
                try:
                    self.fs_scale.set_range(
                        0, max(1.0, float(self.current_duration) or 1.0)
                    )
                    if not getattr(self, "_updating_position_scale", False):
                        self.fs_scale.set_value(self.position_scale.get_value())
                except Exception:
                    pass
            if getattr(self, "fs_time_label", None) is not None and getattr(
                self, "time_label", None
            ) is not None:
                self.fs_time_label.set_text(self.time_label.get_text())
            bar.show()
            self._fs_controls_visible = True
        except Exception:
            pass

    def _hide_fs_controls(self):
        if getattr(self, "user_seeking", False):
            return
        bar = getattr(self, "fs_controls", None)
        if bar is None:
            return
        try:
            bar.hide()
        except Exception:
            pass
        self._fs_controls_visible = False

    def on_video_motion(self, widget, event):
        if self.is_fullscreen:
            self._reset_cursor_hide_timer()
            # Zone basse (~18 % de la hauteur) → affiche les contrôles
            try:
                h = widget.get_allocated_height() or 1
                if event.y >= h * 0.82:
                    self._show_fs_controls()
                elif getattr(self, "_fs_controls_visible", False):
                    # Hors zone : masque (sauf si souris sur la barre elle-même)
                    self._hide_fs_controls()
            except Exception:
                pass
        return False

    def _reset_cursor_hide_timer(self):
        self._show_cursor()
        if self._cursor_hide_timeout_id:
            try:
                GLib.source_remove(self._cursor_hide_timeout_id)
            except Exception:
                pass
            self._cursor_hide_timeout_id = 0
        if not self.is_fullscreen:
            return

        def _hide():
            self._cursor_hide_timeout_id = 0
            if self.is_fullscreen:
                self._hide_cursor()
            return False

        self._cursor_hide_timeout_id = GLib.timeout_add(4000, _hide)

    def _hide_cursor(self):
        try:
            window = self.get_window()
            if window is None:
                return
            display = window.get_display()
            blank = Gdk.Cursor.new_from_name(display, "none")
            if blank is None:
                blank = Gdk.Cursor.new_for_display(display, Gdk.CursorType.BLANK_CURSOR)
            window.set_cursor(blank)
            self._cursor_hidden = True
        except Exception:
            pass

    def _show_cursor(self):
        try:
            window = self.get_window()
            if window is not None:
                window.set_cursor(None)
            self._cursor_hidden = False
        except Exception:
            pass
        if self._cursor_hide_timeout_id:
            try:
                GLib.source_remove(self._cursor_hide_timeout_id)
            except Exception:
                pass
            self._cursor_hide_timeout_id = 0

    def show_osd(self, text, duration_ms=1600):
        """Ancien OSD central (volume, pause, seek…) — désactivé.

        Les annonces utiles restent les toasts coins en plein écran :
        - haut-gauche : changement de plage (_announce_track_change)
        - haut-droite : confirmation de capture (_fs_quick_screenshot)
        Les appels existants sont conservés pour ne rien casser.
        """
        return

    def _osd_seek_feedback(self, delta):
        sign = "+" if delta >= 0 else ""
        pos = self._last_known_position
        try:
            ok, p = self.player.query_position(Gst.Format.TIME)
            if ok and p >= 0:
                pos = p / Gst.SECOND
        except Exception:
            pass
        total = self.current_duration
        msg = f"{sign}{int(delta)} s"
        if total > 0:
            msg += f"  ·  {format_time(pos)} / {format_time(total)}"
        else:
            msg += f"  ·  {format_time(pos)}"
        self.show_osd(msg)

    # ========================================================
    # CHAPITRES (MKV / DVD)
    # ========================================================

    def _clear_chapters(self):
        self.chapters = []

    def _ingest_toc(self, toc):
        """Remplit self.chapters depuis un Gst.Toc."""
        if toc is None:
            return
        found = []

        def _walk(entry):
            try:
                etype = entry.get_entry_type()
            except Exception:
                etype = None
            start_ns = None
            try:
                ret = entry.get_start_stop_times()
                # Bindings GI : (True, start_ns, stop_ns) le plus courant
                if isinstance(ret, tuple) and len(ret) >= 3 and isinstance(ret[0], bool):
                    if ret[0]:
                        start_ns = int(ret[1])
                elif isinstance(ret, tuple) and len(ret) >= 2 and not isinstance(ret[0], bool):
                    start_ns = int(ret[0])
                elif isinstance(ret, tuple) and len(ret) == 2 and isinstance(ret[0], bool):
                    if ret[0]:
                        start_ns = int(ret[1])
            except Exception:
                start_ns = None
            title = None
            try:
                tags = entry.get_tags()
                if tags is not None:
                    ok, t = tags.get_string(Gst.TAG_TITLE)
                    if ok and t:
                        title = t.strip()
            except Exception:
                pass
            is_chapter = False
            try:
                if etype == Gst.TocEntryType.CHAPTER:
                    is_chapter = True
                elif etype is not None and "CHAPTER" in str(etype).upper():
                    is_chapter = True
            except Exception:
                pass
            if is_chapter and start_ns is not None and start_ns >= 0:
                sec = start_ns / float(Gst.SECOND)
                if title is None:
                    title = f"{self.tr('Chapitre')} {len(found) + 1}"
                found.append((sec, title))
            try:
                for sub in entry.get_sub_entries() or []:
                    _walk(sub)
            except Exception:
                pass

        try:
            for entry in toc.get_entries() or []:
                _walk(entry)
        except Exception:
            pass

        if found:
            found.sort(key=lambda x: x[0])
            self.chapters = found

    def seek_to_chapter(self, index):
        if index < 0 or index >= len(self.chapters):
            return
        start, title = self.chapters[index]
        # Seek précis : les chapitres MKV sont souvent hors keyframe KEY_UNIT
        self.seek_to(start, accurate=True)
        try:
            self._show_fs_toast(
                "tl",
                "⏭  %s  (%s)" % (title, format_time(start)),
                2000,
            )
        except Exception:
            pass

    # ========================================================
    # MPRIS2 (D-Bus)
    # ========================================================

    _MPRIS_XML = """
    <node>
      <interface name="org.mpris.MediaPlayer2">
        <method name="Raise"/>
        <method name="Quit"/>
        <property name="CanQuit" type="b" access="read"/>
        <property name="CanRaise" type="b" access="read"/>
        <property name="HasTrackList" type="b" access="read"/>
        <property name="Identity" type="s" access="read"/>
        <property name="DesktopEntry" type="s" access="read"/>
        <property name="SupportedUriSchemes" type="as" access="read"/>
        <property name="SupportedMimeTypes" type="as" access="read"/>
      </interface>
      <interface name="org.mpris.MediaPlayer2.Player">
        <method name="Next"/>
        <method name="Previous"/>
        <method name="Pause"/>
        <method name="PlayPause"/>
        <method name="Stop"/>
        <method name="Play"/>
        <method name="Seek">
          <arg direction="in" name="Offset" type="x"/>
        </method>
        <method name="SetPosition">
          <arg direction="in" name="TrackId" type="o"/>
          <arg direction="in" name="Position" type="x"/>
        </method>
        <method name="OpenUri">
          <arg direction="in" name="Uri" type="s"/>
        </method>
        <property name="PlaybackStatus" type="s" access="read"/>
        <property name="LoopStatus" type="s" access="readwrite"/>
        <property name="Rate" type="d" access="readwrite"/>
        <property name="Shuffle" type="b" access="readwrite"/>
        <property name="Metadata" type="a{sv}" access="read"/>
        <property name="Volume" type="d" access="readwrite"/>
        <property name="Position" type="x" access="read"/>
        <property name="MinimumRate" type="d" access="read"/>
        <property name="MaximumRate" type="d" access="read"/>
        <property name="CanGoNext" type="b" access="read"/>
        <property name="CanGoPrevious" type="b" access="read"/>
        <property name="CanPlay" type="b" access="read"/>
        <property name="CanPause" type="b" access="read"/>
        <property name="CanSeek" type="b" access="read"/>
        <property name="CanControl" type="b" access="read"/>
        <signal name="Seeked">
          <arg name="Position" type="x"/>
        </signal>
      </interface>
    </node>
    """

    def _setup_mpris(self):
        """Expose org.mpris.MediaPlayer2.Myostoox sur le bus de session."""

        def on_bus_acquired(connection, name):
            try:
                node = Gio.DBusNodeInfo.new_for_xml(self._MPRIS_XML)
                for iface in node.interfaces:
                    reg_id = connection.register_object(
                        "/org/mpris/MediaPlayer2",
                        iface,
                        self._mpris_method_call,
                        self._mpris_get_property,
                        self._mpris_set_property,
                    )
                    self._mpris_reg_ids.append(reg_id)
                self._mpris_connection = connection
            except Exception as e:
                print("MPRIS register:", e, file=sys.stderr)

        def on_name_acquired(connection, name):
            pass

        def on_name_lost(connection, name):
            self._mpris_connection = None

        self._mpris_connection = None
        self._mpris_owner_id = Gio.bus_own_name(
            Gio.BusType.SESSION,
            "org.mpris.MediaPlayer2.Myostoox",
            Gio.BusNameOwnerFlags.NONE,
            on_bus_acquired,
            on_name_acquired,
            on_name_lost,
        )

    def _mpris_method_call(
        self, connection, sender, object_path, interface_name,
        method_name, parameters, invocation
    ):
        try:
            if method_name == "Raise":
                self.present()
            elif method_name == "Quit":
                self.close()
            elif method_name == "Next":
                self.next()
            elif method_name == "Previous":
                self.previous()
            elif method_name == "Pause":
                if self.is_playing and not self.is_paused:
                    self.toggle_pause()
            elif method_name == "Play":
                if not self.is_playing or self.is_paused:
                    self.toggle_pause()
            elif method_name == "PlayPause":
                self.toggle_pause()
            elif method_name == "Stop":
                self.stop()
            elif method_name == "Seek":
                offset = parameters.unpack()[0]  # µs
                self.seek_relative(offset / 1_000_000.0)
            elif method_name == "SetPosition":
                _track, pos = parameters.unpack()
                self.seek_to(pos / 1_000_000.0)
            elif method_name == "OpenUri":
                uri = parameters.unpack()[0]
                path = uri
                if uri.startswith("file://"):
                    try:
                        path = GLib.filename_from_uri(uri)[0]
                    except Exception:
                        path = uri
                if is_audio_file(path) or is_video_file(path) or is_stream(path):
                    self.add_file(path, save=True)
                    self.play_file(path)
            invocation.return_value(None)
        except Exception as e:
            invocation.return_error_literal(
                Gio.dbus_error_quark(),
                Gio.DBusError.FAILED,
                str(e),
            )

    def _mpris_get_property(
        self, connection, sender, object_path, interface_name, property_name
    ):
        try:
            if interface_name == "org.mpris.MediaPlayer2":
                values = {
                    "CanQuit": GLib.Variant("b", True),
                    "CanRaise": GLib.Variant("b", True),
                    "HasTrackList": GLib.Variant("b", False),
                    "Identity": GLib.Variant("s", APP_NAME),
                    "DesktopEntry": GLib.Variant("s", APP_NAME.lower()),
                    "SupportedUriSchemes": GLib.Variant(
                        "as", ["file", "http", "https", "rtsp", "mms"]
                    ),
                    "SupportedMimeTypes": GLib.Variant(
                        "as",
                        [
                            "audio/mpeg", "audio/flac", "audio/ogg",
                            "video/mp4", "video/x-matroska",
                        ],
                    ),
                }
                return values.get(property_name)

            if property_name == "PlaybackStatus":
                if self.is_playing and not self.is_paused:
                    status = "Playing"
                elif self.is_playing and self.is_paused:
                    status = "Paused"
                else:
                    status = "Stopped"
                return GLib.Variant("s", status)
            if property_name == "LoopStatus":
                return GLib.Variant(
                    "s", "Playlist" if self.repeat_mode else "None"
                )
            if property_name == "Rate":
                return GLib.Variant("d", 1.0)
            if property_name == "Shuffle":
                return GLib.Variant("b", bool(self.shuffle_mode))
            if property_name == "Volume":
                vol = 0.0 if self.is_muted else float(
                    self.volume_scale.get_value()
                )
                return GLib.Variant("d", vol)
            if property_name == "Position":
                pos_us = int(self._last_known_position * 1_000_000)
                return GLib.Variant("x", pos_us)
            if property_name == "MinimumRate":
                return GLib.Variant("d", 1.0)
            if property_name == "MaximumRate":
                return GLib.Variant("d", 1.0)
            if property_name in (
                "CanGoNext", "CanGoPrevious", "CanPlay",
                "CanPause", "CanSeek", "CanControl",
            ):
                return GLib.Variant("b", True)
            if property_name == "Metadata":
                return self._mpris_metadata_variant()
        except Exception:
            pass
        return None

    def _mpris_set_property(
        self, connection, sender, object_path, interface_name,
        property_name, value
    ):
        try:
            if property_name == "Volume":
                vol = float(value.unpack())
                vol = max(0.0, min(1.0, vol))
                if vol <= 0.001:
                    if not self.is_muted:
                        self.toggle_mute()
                else:
                    if self.is_muted:
                        self.is_muted = False
                        self._update_mute_button()
                    self.volume_scale.set_value(vol)
            elif property_name == "Shuffle":
                want = bool(value.unpack())
                if want != self.shuffle_mode:
                    self.toggle_shuffle(self.shuffle_button)
            elif property_name == "LoopStatus":
                status = value.unpack()
                if status == "None" and self.repeat_mode:
                    self.toggle_repeat(self.repeat_button)
                elif status == "Playlist" and not self.repeat_mode:
                    self.toggle_repeat(self.repeat_button)
            elif property_name == "Rate":
                pass
            return True
        except Exception:
            return False

    def _mpris_metadata_variant(self):
        meta = {}
        path = self.current_path or ""
        track_id = "/org/mpris/MediaPlayer2/Track/0"
        meta["mpris:trackid"] = GLib.Variant("o", track_id)
        if self.current_duration > 0:
            meta["mpris:length"] = GLib.Variant(
                "x", int(self.current_duration * 1_000_000)
            )
        title = ""
        artist = ""
        album = ""
        try:
            if path and not is_stream(path) and not is_cdda(path):
                t, a, al, _y, _d, _c, _tech = self.get_metadata(
                    path, load_cover=False
                )
                title, artist, album = t or "", a or "", al or ""
            elif path:
                title = (
                    self.icy_title
                    or self.current_tv_name
                    or self.current_radio_name
                    or os.path.basename(path)
                    or path
                )
        except Exception:
            title = os.path.basename(path) if path else ""
        if not title:
            title = APP_NAME
        meta["xesam:title"] = GLib.Variant("s", title)
        if artist:
            meta["xesam:artist"] = GLib.Variant("as", [artist])
        if album:
            meta["xesam:album"] = GLib.Variant("s", album)
        if path:
            try:
                if is_stream(path) or is_cdda(path) or is_dvd(path) or is_bluray(path):
                    uri = path
                else:
                    uri = GLib.filename_to_uri(os.path.abspath(path), None)
                meta["xesam:url"] = GLib.Variant("s", uri)
            except Exception:
                pass
        return GLib.Variant("a{sv}", meta)

    def _mpris_properties_changed(self, props):
        """Notifie le bus des propriétés Player modifiées."""
        conn = getattr(self, "_mpris_connection", None)
        if conn is None or not props:
            return
        try:
            conn.emit_signal(
                None,
                "/org/mpris/MediaPlayer2",
                "org.freedesktop.DBus.Properties",
                "PropertiesChanged",
                GLib.Variant(
                    "(sa{sv}as)",
                    (
                        "org.mpris.MediaPlayer2.Player",
                        props,
                        [],
                    ),
                ),
            )
        except Exception:
            pass

    def _mpris_notify_playback(self):
        if self.is_playing and not self.is_paused:
            status = "Playing"
        elif self.is_playing and self.is_paused:
            status = "Paused"
        else:
            status = "Stopped"
        self._mpris_properties_changed(
            {
                "PlaybackStatus": GLib.Variant("s", status),
                "Metadata": self._mpris_metadata_variant(),
            }
        )

    def show_keyboard_shortcuts(self, *args):
        """Dialogue pour consulter et modifier les raccourcis clavier."""
        dialog = Gtk.Dialog(
            title=self.tr("Raccourcis clavier"),
            parent=self,
            modal=True,
        )
        dialog.set_default_size(self._ui_s(520), self._ui_s(480))
        dialog.add_button(
            self.tr("Réinitialiser les raccourcis"), Gtk.ResponseType.REJECT
        )
        dialog.add_button(self.tr("Fermer"), Gtk.ResponseType.CLOSE)
        # Bouton « ? » à droite : aide rapide (même ligne que Reset / Fermer)
        help_btn = dialog.add_button("?", Gtk.ResponseType.HELP)
        try:
            help_btn.set_tooltip_text(self.tr("Aide raccourcis"))
        except Exception:
            pass

        area = dialog.get_content_area()
        area.set_border_width(12)

        hint = Gtk.Label(
            label=self.tr("Appuyez sur une touche…")
            + " — "
            + self.tr("Action")
        )
        hint.set_xalign(0)
        area.pack_start(hint, False, False, 4)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        area.pack_start(scroll, True, True, 0)

        store = Gtk.ListStore(str, str, str)  # action_id, label, key
        for action_id in DEFAULT_SHORTCUTS.keys():
            label = SHORTCUT_LABELS.get(action_id, action_id)
            key = self.shortcuts.get(action_id, DEFAULT_SHORTCUTS[action_id])
            store.append([action_id, self.tr(label) if label in (
                "Muet", "Lecture / Pause", "Supprimer la sélection", "Capture d'écran",
                "Vider la playlist"
            ) else label, key])

        tree = Gtk.TreeView(model=store)
        tree.set_headers_visible(True)
        renderer = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn(self.tr("Action"), renderer, text=1)
        col.set_expand(True)
        tree.append_column(col)
        renderer_key = Gtk.CellRendererText()
        col_key = Gtk.TreeViewColumn(self.tr("Touche"), renderer_key, text=2)
        col_key.set_min_width(120)
        tree.append_column(col_key)
        scroll.add(tree)

        capture = {"action": None, "iter": None}

        def on_row_activated(tv, path, column):
            it = store.get_iter(path)
            capture["action"] = store[it][0]
            capture["iter"] = it
            hint.set_markup(
                "<b>"
                + GLib.markup_escape_text(self.tr("Appuyez sur une touche…"))
                + "</b>  →  "
                + GLib.markup_escape_text(store[it][1])
            )
            dialog.present()

        tree.connect("row-activated", on_row_activated)

        def on_key(widget, event):
            if capture["action"] is None:
                return False
            key = Gdk.keyval_name(event.keyval)
            if key is None:
                return True
            # Ignore modifiers seuls
            if key in (
                "Control_L", "Control_R", "Shift_L", "Shift_R",
                "Alt_L", "Alt_R", "Meta_L", "Meta_R", "Super_L", "Super_R",
            ):
                return True
            binding = self._event_to_shortcut(event)
            if not binding:
                return True
            store[capture["iter"]][2] = binding
            self.shortcuts[capture["action"]] = binding
            capture["action"] = None
            capture["iter"] = None
            hint.set_text(self.tr("Raccourcis enregistrés"))
            try:
                self.save_state()
            except Exception:
                pass
            return True

        dialog.connect("key-press-event", on_key)

        dialog.show_all()
        while True:
            response = dialog.run()
            if response == Gtk.ResponseType.REJECT:
                self.shortcuts = dict(DEFAULT_SHORTCUTS)
                store.clear()
                for action_id in DEFAULT_SHORTCUTS.keys():
                    label = SHORTCUT_LABELS.get(action_id, action_id)
                    store.append([
                        action_id,
                        label,
                        DEFAULT_SHORTCUTS[action_id],
                    ])
                try:
                    self.save_state()
                except Exception:
                    pass
                hint.set_text(self.tr("Raccourcis enregistrés"))
                continue
            if response == Gtk.ResponseType.HELP:
                # Double-clic sur une ligne → modifier ; Reset restaure les défauts
                try:
                    hint.set_markup(
                        "<i>"
                        + GLib.markup_escape_text(
                            self.tr("Appuyez sur une touche…")
                        )
                        + "</i> — "
                        + GLib.markup_escape_text(
                            self.tr("Réinitialiser les raccourcis")
                        )
                    )
                except Exception:
                    pass
                continue
            break
        dialog.destroy()

    # ========================================================
    # CLIC DROIT
    # ========================================================

    def on_tree_button_press(self, tree, event):
        """Clic gauche → sélection/drag ; clic droit → menu contextuel."""
        if event.button == 1:
            return self._playlist_left_button_press(tree, event)
        if event.button != 3:
            return False

        result = tree.get_path_at_pos(int(event.x), int(event.y))
        if result is None:
            return False

        path = result[0]
        selection = tree.get_selection()
        # Clic droit sur une ligne déjà sélectionnée : conserve la multi-sélection
        if not selection.path_is_selected(path):
            selection.unselect_all()
            selection.select_path(path)
        model = tree.get_model()
        iterator = model.get_iter(path)
        if iterator is None:
            return False

        filename = model[iterator][self.COL_PATH]
        model_sel, sel_paths = selection.get_selected_rows()
        multi = len(sel_paths) > 1

        old = getattr(self, "_playlist_context_menu", None)
        if old is not None:
            try:
                old.popdown()
            except Exception:
                pass
            try:
                old.destroy()
            except Exception:
                pass
            self._playlist_context_menu = None

        menu = Gtk.Menu()
        self._playlist_context_menu = menu

        play = Gtk.MenuItem(label=self.tr("Lire"))
        play.connect("activate", lambda item: self.play_file(filename))
        menu.append(play)

        if not multi:
            metadata = Gtk.MenuItem(label=self.tr("Métadonnées…"))
            metadata.connect("activate", lambda item: self.show_metadata(filename))
            menu.append(metadata)

        menu.append(Gtk.SeparatorMenuItem())

        if multi:
            remove = Gtk.MenuItem(label=self.tr("Supprimer la sélection"))
            remove.connect("activate", lambda item: self.remove_selected())
            menu.append(remove)
        else:
            remove = Gtk.MenuItem(label=self.tr("Supprimer de la playlist"))
            remove.connect("activate", lambda item: self.remove_file(filename))
            menu.append(remove)

        menu.append(Gtk.SeparatorMenuItem())

        clear = Gtk.MenuItem(label=self.tr("Vider la playlist"))
        clear.connect("activate", lambda item: self.clear_playlist())
        menu.append(clear)

        def _ctx_deact(m):
            if getattr(self, "_playlist_context_menu", None) is m:
                self._playlist_context_menu = None
            try:
                GLib.idle_add(m.destroy)
            except Exception:
                try:
                    m.destroy()
                except Exception:
                    pass
            return False

        menu.connect("deactivate", _ctx_deact)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    # ========================================================
    # MÉTADONNÉES
    # ========================================================

    def show_metadata(self, filename):

        title, artist, album, year, duration, cover, technical = \
            self.get_metadata(filename, load_cover=False)

        dialog = Gtk.Dialog(
            title=self.tr("Métadonnées — ") + (os.path.basename(filename) if not (is_cdda(filename) or is_stream(filename)) else filename),
            parent=self,
            flags=Gtk.DialogFlags.MODAL
        )
        dialog.set_default_size(self._ui_s(650), self._ui_s(500))
        dialog.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        dialog.add_button(self.tr("Enregistrer"), Gtk.ResponseType.OK)

        area = dialog.get_content_area()
        area.set_border_width(15)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        area.pack_start(box, True, True, 0)

        frame = Gtk.Frame(label=self.tr("Informations éditables"))
        box.pack_start(frame, False, False, 0)

        grid = Gtk.Grid()
        grid.set_border_width(10)
        grid.set_row_spacing(8)
        grid.set_column_spacing(12)
        frame.add(grid)

        entries = {}
        fields = [
            ("Titre", title),
            ("Artiste", artist),
            ("Album", album),
            (self.tr("Année"), year),
        ]

        for row, (label_text, value) in enumerate(fields):
            label = Gtk.Label(label=self.tr(label_text) + " :")
            label.set_xalign(1)
            entry = Gtk.Entry()
            entry.set_text(value or "")
            entry.set_hexpand(True)
            grid.attach(label, 0, row, 1, 1)
            grid.attach(entry, 1, row, 1, 1)
            entries[label_text] = entry

        tech_frame = Gtk.Frame(label=self.tr("Informations techniques"))
        box.pack_start(tech_frame, False, False, 0)

        tech = Gtk.Grid()
        tech.set_border_width(10)
        tech.set_row_spacing(5)
        tech.set_column_spacing(15)
        tech_frame.add(tech)

        rows = [
            (self.tr("Format"), technical.get("format") or "—"),
            (self.tr("Durée"), format_time(duration) if duration > 0 else "—"),
            (self.tr("Fréquence"), technical.get("sample_rate") or "—"),
            (self.tr("Canaux"), technical.get("channels") or "—"),
            (self.tr("Débit"), technical.get("bitrate") or "—"),
            (self.tr("Résolution"), technical.get("bits") or "—"),
        ]

        for row, (key, value) in enumerate(rows):
            l1 = Gtk.Label(label=self.tr(key) + " :")
            l1.set_xalign(1)
            l2 = Gtk.Label(label=value or "—")
            l2.set_xalign(0)
            tech.attach(l1, 0, row, 1, 1)
            tech.attach(l2, 1, row, 1, 1)

        file_label = Gtk.Label(label=self.tr("Fichier : ") + (os.path.basename(filename) if not (is_cdda(filename) or is_stream(filename)) else filename))
        file_label.set_xalign(0)
        file_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        box.pack_start(file_label, False, False, 0)

        path_label = Gtk.Label(label=self.tr("Chemin : ") + filename)
        path_label.set_xalign(0)
        path_label.set_selectable(True)
        path_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        path_label.get_style_context().add_class("status")
        box.pack_start(path_label, False, False, 0)

        dialog.show_all()
        response = dialog.run()

        if response == Gtk.ResponseType.OK and not (is_cdda(filename) or is_stream(filename)):
            new_title = entries["Titre"].get_text().strip()
            new_artist = entries["Artiste"].get_text().strip()
            new_album = entries["Album"].get_text().strip()
            new_year = entries[self.tr("Année")].get_text().strip()

            try:
                self.save_metadata(filename, new_title, new_artist, new_album, new_year)
                self.refresh_file(filename)
                self.update_information(filename)
                self.status_label.set_text(self.tr("Métadonnées enregistrées"))
            except Exception as e:
                error = Gtk.MessageDialog(
                    parent=self,
                    flags=Gtk.DialogFlags.MODAL,
                    message_type=Gtk.MessageType.ERROR,
                    buttons=Gtk.ButtonsType.OK,
                    text=self.tr("Impossible d'enregistrer les métadonnées.")
                )
                error.format_secondary_text(str(e))
                error.run()
                error.destroy()

        dialog.destroy()

    def save_metadata(self, filename, title, artist, album, year):

        if MutagenFile is None:
            raise RuntimeError(self.tr("Le module Python Mutagen n'est pas installé."))

        ext = os.path.splitext(filename)[1].lower()

        if ext == ".mp3":
            try:
                tags = ID3(filename)
            except ID3NoHeaderError:
                tags = ID3()
            tags.delall("TIT2")
            tags.delall("TPE1")
            tags.delall("TALB")
            tags.delall("TDRC")
            if title:
                tags.add(TIT2(encoding=3, text=title))
            if artist:
                tags.add(TPE1(encoding=3, text=artist))
            if album:
                tags.add(TALB(encoding=3, text=album))
            if year:
                tags.add(TDRC(encoding=3, text=year))
            tags.save(filename)
            return

        if ext == ".flac":
            audio = FLAC(filename)
            self.set_vorbis_tags(audio, title, artist, album, year)
            audio.save()
            return

        if ext in (".ogg", ".oga"):
            audio = MutagenFile(filename)
            if audio is None:
                raise RuntimeError(self.tr("Impossible d'ouvrir ce fichier Ogg."))
            self.set_vorbis_tags(audio, title, artist, album, year)
            audio.save()
            return

        if ext == ".opus":
            audio = OggOpus(filename)
            self.set_vorbis_tags(audio, title, artist, album, year)
            audio.save()
            return

        if ext in (".m4a", ".mp4"):
            audio = MP4(filename)
            if audio.tags is None:
                audio.add_tags()
            tags = audio.tags
            values = {
                "\xa9nam": title,
                "\xa9ART": artist,
                "\xa9alb": album,
                "\xa9day": year,
            }
            for key, value in values.items():
                if value:
                    tags[key] = [value]
                else:
                    tags.pop(key, None)
            audio.save()
            return

        if ext == ".wav":
            audio = MutagenFile(filename)
            if audio is None:
                raise RuntimeError(self.tr("Ce fichier WAV ne peut pas être édité."))
            if audio.tags is None:
                try:
                    audio.add_tags()
                except Exception:
                    raise RuntimeError(self.tr("Impossible d'ajouter des métadonnées à ce WAV."))
            tags = audio.tags
            tags.delall("TIT2")
            tags.delall("TPE1")
            tags.delall("TALB")
            tags.delall("TDRC")
            if title:
                tags.add(TIT2(encoding=3, text=title))
            if artist:
                tags.add(TPE1(encoding=3, text=artist))
            if album:
                tags.add(TALB(encoding=3, text=album))
            if year:
                tags.add(TDRC(encoding=3, text=year))
            audio.save()
            return

        if ext == ".aac":
            raise RuntimeError(
                self.tr(
                    "Un fichier AAC brut (.aac) ne possède généralement pas "
                    "de conteneur permettant cette édition."
                )
            )

        raise RuntimeError(self.tr("Format non pris en charge pour l'édition des métadonnées."))

    def set_vorbis_tags(self, audio, title, artist, album, year):

        if title:
            audio["title"] = [title]
        else:
            audio.pop("title", None)

        if artist:
            audio["artist"] = [artist]
        else:
            audio.pop("artist", None)

        if album:
            audio["album"] = [album]
        else:
            audio.pop("album", None)

        if year:
            audio["date"] = [year]
        else:
            audio.pop("date", None)

    def refresh_file(self, filename):

        if filename in self.metadata_cache:
            del self.metadata_cache[filename]

        iterator = self.playlist.get_iter_first()
        while iterator is not None:
            if self.playlist[iterator][self.COL_PATH] == filename:
                title, artist, album, year, duration, cover, technical = \
                    self.get_metadata(filename, load_cover=False)

                self.playlist[iterator][self.COL_TITLE] = title
                self.playlist[iterator][self.COL_ARTIST] = artist
                self.playlist[iterator][self.COL_ALBUM] = album
                self.playlist[iterator][self.COL_DURATION_TEXT] = format_time(duration)
                self.playlist[iterator][self.COL_DURATION] = float(duration)
                return
            iterator = self.playlist.iter_next(iterator)

    # ========================================================
    # SUPPRESSION
    # ========================================================

    def remove_selected(self):

        selection = self.tree.get_selection()
        model, paths = selection.get_selected_rows()
        if not paths:
            return
        filenames = []
        for path in paths:
            try:
                it = model.get_iter(path)
                if it is not None:
                    filenames.append(model[it][self.COL_PATH])
            except Exception:
                pass
        for filename in filenames:
            self.remove_file(filename)

    def remove_file(self, filename):

        iterator = self.playlist.get_iter_first()
        while iterator is not None:
            if self.playlist[iterator][self.COL_PATH] == filename:
                was_current = (filename == self.current_path)
                if was_current:
                    self.stop()
                    self.current_path = None

                self.playlist.remove(iterator)

                if filename in self.metadata_cache:
                    del self.metadata_cache[filename]

                self.save_playlist()

                # Playlist vide, ou piste en cours retirée : effacer la zone bas
                # (selection_changed peut aussi le faire, mais on garantit ici
                # le cas « dernier fichier supprimé un par un »).
                if was_current or len(self.playlist) == 0:
                    self._clear_now_playing_display(reset_time=(was_current or len(self.playlist) == 0))
                    if len(self.playlist) == 0:
                        try:
                            self.status_label.set_text(self.tr("Playlist vide"))
                        except Exception:
                            pass
                return
            iterator = self.playlist.iter_next(iterator)

    def clear_playlist(self):

        if getattr(self, "bouquet_active", False):
            self.dismiss_bouquet_playlist()
            self.status_label.set_text(self.tr("Playlist vide"))
            return

        self.bouquet_active = False
        self.stop()
        self.current_path = None
        self.current_duration = 0
        self._last_known_position = 0.0
        try:
            self.position_scale.set_range(0, 1)
            self.position_scale.set_value(0)
        except Exception:
            pass
        try:
            self.time_label.set_text("0:00 / 0:00")
        except Exception:
            pass
        self.playlist.clear()
        self.metadata_cache.clear()
        self.save_playlist()

        self._clear_now_playing_display(reset_time=True)
        self.status_label.set_text(self.tr("Playlist vide"))

    # ========================================================
    # ÉGALISEUR GRAPHIQUE
    # ========================================================

    def show_equalizer(self, *args):

        if self.equalizer is None:
            dialog = Gtk.MessageDialog(
                parent=self,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text=self.tr("Égaliseur non disponible")
            )
            dialog.format_secondary_text(
                self.tr("L'élément GStreamer « equalizer-10bands » n'est pas installé.")
            )
            dialog.run()
            dialog.destroy()
            return

        if self.eq_window is not None:
            self.eq_window.present()
            return

        win = Gtk.Window(title=self.tr("Égaliseur graphique"))
        win.set_transient_for(self)
        win.set_default_size(self._ui_s(520), self._ui_s(320))
        win.set_resizable(True)
        win.set_border_width(12)

        self.eq_window = win
        self.eq_scales = []
        self.eq_preset_combo = None

        def on_eq_destroy(*args):
            self.eq_window = None
            self.eq_scales = []
            self.eq_preset_combo = None

        win.connect("destroy", on_eq_destroy)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        win.add(main_box)

        bands_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bands_box.set_homogeneous(True)
        main_box.pack_start(bands_box, True, True, 0)

        for i in range(EQ_BANDS):
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

            try:
                current = self.equalizer.get_property(f"band{i}")
            except Exception:
                current = 0.0

            value_label = Gtk.Label(label=f"{current:+.1f}")
            value_label.set_width_chars(5)
            col.pack_start(value_label, False, False, 0)

            scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.VERTICAL,
                EQ_MIN_DB,
                EQ_MAX_DB,
                0.5
            )
            scale.set_value(current)
            scale.set_draw_value(False)
            scale.set_inverted(True)
            scale.set_size_request(-1, self._ui_s(160))
            scale.set_vexpand(True)

            def make_callback(index, label):
                def callback(s):
                    gain = s.get_value()
                    try:
                        self.equalizer.set_property(f"band{index}", gain)
                    except Exception:
                        pass
                    label.set_text(f"{gain:+.1f}")
                return callback

            scale.connect("value-changed", make_callback(i, value_label))
            col.pack_start(scale, True, True, 0)

            freq_label = Gtk.Label(label=EQ_FREQS[i])
            freq_label.set_xalign(0.5)
            freq_label.get_style_context().add_class("status")
            col.pack_start(freq_label, False, False, 0)

            bands_box.pack_start(col, True, True, 0)
            self.eq_scales.append(scale)

        # Préréglages
        presets_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        main_box.pack_start(presets_box, False, False, 0)

        presets_label = Gtk.Label(label=self.tr("Préréglage :"))
        presets_box.pack_start(presets_label, False, False, 0)

        self.eq_preset_combo = Gtk.ComboBoxText()
        self.eq_preset_combo.set_hexpand(True)
        for preset in self.eq_presets:
            self.eq_preset_combo.append_text(preset["name"])
        presets_box.pack_start(self.eq_preset_combo, True, True, 0)

        apply_preset_btn = Gtk.Button(label=self.tr("Appliquer"))
        apply_preset_btn.connect("clicked", self.apply_eq_preset)
        presets_box.pack_start(apply_preset_btn, False, False, 0)

        save_preset_btn = Gtk.Button(label=self.tr("Enregistrer"))
        save_preset_btn.connect("clicked", self.save_eq_preset)
        presets_box.pack_start(save_preset_btn, False, False, 0)

        delete_preset_btn = Gtk.Button(label=self.tr("Supprimer"))
        delete_preset_btn.connect("clicked", self.delete_eq_preset)
        presets_box.pack_start(delete_preset_btn, False, False, 0)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        button_box.set_halign(Gtk.Align.END)
        main_box.pack_start(button_box, False, False, 0)

        eq_mute_btn = Gtk.Button(label=self.tr("Bypass EQ"))
        eq_mute_btn.connect("clicked", self.toggle_eq_mute)
        button_box.pack_start(eq_mute_btn, False, False, 0)

        reset_btn = Gtk.Button(label=self.tr("Réinitialiser"))
        reset_btn.connect("clicked", self.reset_equalizer)
        button_box.pack_start(reset_btn, False, False, 0)

        close_btn = Gtk.Button(label=self.tr("Fermer"))
        close_btn.connect("clicked", lambda b: win.destroy())
        button_box.pack_start(close_btn, False, False, 0)

        win.show_all()

    def toggle_eq_mute(self, button=None):

        if self.equalizer is None:
            return

        if not self.eq_muted:
            self.eq_saved_gains = []

            for i in range(EQ_BANDS):
                try:
                    gain = self.equalizer.get_property(f"band{i}")
                    self.eq_saved_gains.append(gain)
                    self.equalizer.set_property(f"band{i}", 0.0)
                except Exception:
                    self.eq_saved_gains.append(0.0)
                if i < len(self.eq_scales):
                    try:
                        self.eq_scales[i].set_value(0.0)
                    except Exception:
                        pass

            self.eq_muted = True

            if button is not None:
                button.set_label(self.tr("Réactiver EQ"))

            self.status_label.set_text(self.tr("Égaliseur désactivé"))

        else:
            for i, gain in enumerate(self.eq_saved_gains):
                try:
                    self.equalizer.set_property(f"band{i}", gain)
                except Exception:
                    pass
                if i < len(self.eq_scales):
                    try:
                        self.eq_scales[i].set_value(gain)
                    except Exception:
                        pass

            self.eq_muted = False

            if button is not None:
                button.set_label(self.tr("Bypass EQ"))

            self.status_label.set_text(self.tr("Égaliseur activé"))

    def reset_equalizer(self, *args):

        if self.equalizer is None:
            return

        for i in range(EQ_BANDS):
            try:
                self.equalizer.set_property(f"band{i}", 0.0)
            except Exception:
                pass

            if i < len(self.eq_scales):
                self.eq_scales[i].set_value(0.0)

        self.eq_muted = False
        self.eq_saved_gains = []

        self.status_label.set_text(self.tr("Égaliseur réinitialisé"))

    def apply_eq_preset(self, *args):

        if self.eq_preset_combo is None or self.equalizer is None:
            return

        idx = self.eq_preset_combo.get_active()
        if idx < 0 or idx >= len(self.eq_presets):
            return

        gains = self.eq_presets[idx]["gains"]
        for i in range(EQ_BANDS):
            try:
                gain = max(EQ_MIN_DB, min(EQ_MAX_DB, float(gains[i])))
                self.equalizer.set_property(f"band{i}", gain)
                if i < len(self.eq_scales):
                    self.eq_scales[i].set_value(gain)
            except Exception:
                pass

        self.eq_muted = False
        self.status_label.set_text(self.tr("Préréglage appliqué"))

    def save_eq_preset(self, *args):

        if self.equalizer is None:
            return

        dialog = Gtk.Dialog(
            title=self.tr("Enregistrer le préréglage"),
            parent=self.eq_window if self.eq_window else self,
            flags=Gtk.DialogFlags.MODAL
        )
        dialog.set_default_size(self._ui_s(320), self._ui_s(100))
        dialog.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        dialog.add_button(self.tr("Valider"), Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)

        area = dialog.get_content_area()
        area.set_border_width(12)

        entry = Gtk.Entry()
        entry.set_placeholder_text(self.tr("Nom du préréglage"))
        entry.set_activates_default(True)
        area.pack_start(entry, False, False, 0)

        dialog.show_all()
        response = dialog.run()
        name = entry.get_text().strip()
        dialog.destroy()

        if response != Gtk.ResponseType.OK or not name:
            return

        if self.eq_muted and self.eq_saved_gains:
            gains = list(self.eq_saved_gains)
        else:
            gains = []
            for i in range(EQ_BANDS):
                try:
                    gains.append(float(self.equalizer.get_property(f"band{i}")))
                except Exception:
                    gains.append(0.0)

        # Update if exists
        for i, preset in enumerate(self.eq_presets):
            if preset["name"] == name:
                self.eq_presets[i]["gains"] = gains
                self.save_eq_presets()
                if self.eq_preset_combo is not None:
                    self.eq_preset_combo.set_active(i)
                self.status_label.set_text(self.tr("Préréglage mis à jour"))
                return

        self.eq_presets.append({"name": name, "gains": gains})
        self.save_eq_presets()
        if self.eq_preset_combo is not None:
            self.eq_preset_combo.append_text(name)
            self.eq_preset_combo.set_active(len(self.eq_presets) - 1)
        self.status_label.set_text(self.tr("Préréglage enregistré"))

    def delete_eq_preset(self, *args):

        if self.eq_preset_combo is None:
            return

        idx = self.eq_preset_combo.get_active()
        if idx < 0 or idx >= len(self.eq_presets):
            return

        del self.eq_presets[idx]
        self.save_eq_presets()
        self.eq_preset_combo.remove(idx)
        if len(self.eq_presets) > 0:
            self.eq_preset_combo.set_active(min(idx, len(self.eq_presets) - 1))
        self.status_label.set_text(self.tr("Préréglage supprimé"))

    # ========================================================
    # RECONSTRUCTION DYNAMIQUE DU FILTRE AUDIO
    # ========================================================


    def _snap_pitch_tempo_values(self):
        """Normalise pitch/tempo (évite 1.0001 ou résidus d'état)."""
        try:
            if abs(float(self.pitch_value) - 1.0) < 0.001:
                self.pitch_value = 1.0
            else:
                self.pitch_value = max(0.5, min(2.0, float(self.pitch_value)))
        except Exception:
            self.pitch_value = 1.0
        try:
            if abs(float(self.tempo_value) - 1.0) < 0.001:
                self.tempo_value = 1.0
            else:
                self.tempo_value = max(0.5, min(2.0, float(self.tempo_value)))
        except Exception:
            self.tempo_value = 1.0

    def _configure_pitch_element(self):
        """Applique pitch/tempo avec rate SoundTouch forcé à 1.0 (pas d'accélération)."""
        if self.pitch is None:
            return
        self._snap_pitch_tempo_values()
        for prop, val in (
            ("pitch", float(self.pitch_value)),
            ("tempo", float(self.tempo_value)),
            ("rate", 1.0),
        ):
            try:
                self.pitch.set_property(prop, val)
            except Exception:
                pass

    def rebuild_audio_filter(self, force=False, restart=True):

        # Pitch/tempo : fichiers audio locaux uniquement (pas vidéo / flux / disques)
        need_pitch = (
            self.pitch is not None
            and is_pure_audio_file(getattr(self, "current_path", None))
            and (
                abs(self.pitch_value - 1.0) > 0.001
                or abs(self.tempo_value - 1.0) > 0.001
            )
        )

        if not force and need_pitch == self._pitch_active:
            if need_pitch and self.pitch is not None:
                self._configure_pitch_element()
            return

        was_playing = self.is_playing and not self.is_paused
        was_paused = self.is_paused
        position = 0.0
        try:
            ok, pos = self.player.query_position(Gst.Format.TIME)
            if ok:
                position = pos / Gst.SECOND
        except Exception:
            pass

        self.player.set_state(Gst.State.NULL)

        elements = []

        if self.spectrum is not None:
            elements.append(self.spectrum)

        if need_pitch and self.pitch is not None:
            elements.append(self.pitch)
            self._configure_pitch_element()

        if self.equalizer is not None:
            elements.append(self.equalizer)

        if len(elements) >= 2:
            self.filter_bin = Gst.Bin.new("audio-filter-bin")
            for el in elements:
                parent = el.get_parent()
                if parent is not None:
                    parent.remove(el)
                self.filter_bin.add(el)
            for i in range(len(elements) - 1):
                elements[i].link(elements[i + 1])

            sink_pad = Gst.GhostPad.new(
                "sink",
                elements[0].get_static_pad("sink")
            )
            src_pad = Gst.GhostPad.new(
                "src",
                elements[-1].get_static_pad("src")
            )
            self.filter_bin.add_pad(sink_pad)
            self.filter_bin.add_pad(src_pad)
            self.player.set_property("audio-filter", self.filter_bin)

        elif len(elements) == 1:
            parent = elements[0].get_parent()
            if parent is not None:
                parent.remove(elements[0])
            self.player.set_property("audio-filter", elements[0])
            self.filter_bin = None
        else:
            self.player.set_property("audio-filter", None)
            self.filter_bin = None

        self._pitch_active = need_pitch

        if not restart:
            return

        if self.current_path:
            try:
                uri = path_to_uri(self.current_path)
                self.player.set_property("uri", uri)
            except Exception:
                pass

        if was_playing:
            self.player.set_state(Gst.State.PLAYING)
            self.is_playing = True
            self.is_paused = False
            self.play_button.set_label("❚❚")
        elif was_paused or self.current_path:
            self.player.set_state(Gst.State.PAUSED)
            self.is_playing = True
            self.is_paused = True
            self.play_button.set_label("▶")
        else:
            self.player.set_state(Gst.State.NULL)

        if position > 0.05:
            def _restore_pos():
                self.seek_to(position)
                return False
            GLib.timeout_add(50, _restore_pos)

        # Après reconstruction du filtre audio, redessine l'overlay vidéo
        if self.current_is_video:
            self._nudge_video_redraw()

    # ========================================================
    # COLORIMÉTRIE VIDÉO (globale)
    # ========================================================

    def _colorimetry_allowed(self):
        """Uniquement médias vidéo (fichiers, DVD/BD, flux TV/bouquets)."""
        path = getattr(self, "current_path", None)
        if not path:
            return False
        if is_pure_audio_file(path) or is_cdda(path):
            return False
        if is_video_file(path) or is_dvd(path) or is_bluray(path):
            return True
        if getattr(self, "current_is_video", False):
            return True
        if is_stream(path) and (
            getattr(self, "bouquet_active", False)
            or getattr(self, "_prefer_video", False)
        ):
            return True
        return False

    def _update_colorimetry_menu_sensitivity(self):
        allowed = False
        try:
            allowed = self._colorimetry_allowed()
        except Exception:
            allowed = False
        enabled = bool(getattr(self, "colorimetry_enabled", False))
        item = getattr(self, "colorimetry_menu_item", None)
        if item is not None:
            try:
                item.set_sensitive(allowed)
            except Exception:
                pass
        adj = getattr(self, "colorimetry_adjust_item", None)
        if adj is not None:
            try:
                adj.set_sensitive(allowed and enabled)
            except Exception:
                pass
        if not allowed:
            win = getattr(self, "colorimetry_window", None)
            if win is not None:
                try:
                    win.destroy()
                except Exception:
                    pass

    def _apply_color_props_to(self, balance, gamma):
        if balance is not None:
            for prop, val in (
                ("brightness", float(self.color_brightness)),
                ("contrast", float(self.color_contrast)),
                ("saturation", float(self.color_saturation)),
            ):
                try:
                    balance.set_property(prop, val)
                except Exception:
                    pass
        if gamma is not None:
            try:
                gamma.set_property("gamma", float(self.color_gamma))
            except Exception:
                pass

    def _apply_video_colorimetry(self):
        """Met à jour les éléments colorimétrie actifs (playbin + custom)."""
        self._apply_color_props_to(
            getattr(self, "_video_balance", None),
            getattr(self, "_video_gamma", None),
        )
        self._apply_color_props_to(
            getattr(self, "_custom_vbalance", None),
            getattr(self, "_custom_vgamma", None),
        )

    def _rebuild_video_color_filter(self, restore_playback=True):
        """Attache/retire videobalance+gamma sur playbin de façon sûre.

        Changer video-filter en PLAYING plante souvent (surtout 4K) :
        on passe par NULL, on pose le filtre, puis on reprend position.
        Pipeline custom MKV : ignore playbin (filtres dans le demux).
        """
        if not self.colorimetry_enabled and self._video_color_bin is None:
            return True
        # Pipeline custom : pas de video-filter playbin
        if getattr(self, "_custom_pipeline", None) is not None:
            self._video_balance = None
            self._video_gamma = None
            self._video_color_bin = None
            try:
                self.player.set_property("video-filter", None)
            except Exception:
                pass
            return True

        was_playing = bool(
            getattr(self, "is_playing", False)
            and not getattr(self, "is_paused", False)
        )
        was_paused = bool(getattr(self, "is_paused", False))
        pos = float(getattr(self, "_last_known_position", 0) or 0)
        path = getattr(self, "current_path", None)
        need_restore = bool(
            restore_playback
            and path
            and (was_playing or was_paused)
            and not is_pure_audio_file(path)
        )

        try:
            # Toujours NULL avant de toucher video-filter
            try:
                self.player.set_state(Gst.State.NULL)
                self.player.get_state(int(2 * Gst.SECOND))
            except Exception:
                pass

            self._video_balance = None
            self._video_gamma = None
            self._video_color_bin = None
            try:
                self.player.set_property("video-filter", None)
            except Exception:
                pass

            if not bool(getattr(self, "colorimetry_enabled", False)):
                ok = True
            else:
                ok = False
                vb = Gst.ElementFactory.make("videobalance", "play-vbalance")
                gm = Gst.ElementFactory.make("gamma", "play-vgamma")
                if vb is None and gm is None:
                    print("videobalance/gamma indisponibles", file=sys.stderr)
                    ok = False
                else:
                    elements = [el for el in (vb, gm) if el is not None]
                    self._apply_color_props_to(vb, gm)
                    try:
                        if len(elements) == 1:
                            self.player.set_property("video-filter", elements[0])
                            self._video_balance = vb
                            self._video_gamma = gm
                            self._video_color_bin = elements[0]
                        else:
                            bin_ = Gst.Bin.new("video-color-bin")
                            for el in elements:
                                bin_.add(el)
                            if not elements[0].link(elements[1]):
                                raise RuntimeError("link balance→gamma")
                            bin_.add_pad(
                                Gst.GhostPad.new(
                                    "sink",
                                    elements[0].get_static_pad("sink"),
                                )
                            )
                            bin_.add_pad(
                                Gst.GhostPad.new(
                                    "src",
                                    elements[-1].get_static_pad("src"),
                                )
                            )
                            self.player.set_property("video-filter", bin_)
                            self._video_balance = vb
                            self._video_gamma = gm
                            self._video_color_bin = bin_
                        ok = True
                    except Exception as e:
                        print("video-filter set:", e, file=sys.stderr)
                        try:
                            self.player.set_property("video-filter", None)
                        except Exception:
                            pass
                        self._video_balance = None
                        self._video_gamma = None
                        self._video_color_bin = None
                        ok = False

            if need_restore and path:
                try:
                    if (
                        is_cdda(path)
                        or is_stream(path)
                        or is_dvd(path)
                        or is_bluray(path)
                    ):
                        uri = path
                    else:
                        uri = GLib.filename_to_uri(
                            os.path.abspath(path), None
                        )
                    self.player.set_property("uri", uri)
                except Exception as e:
                    print("color filter re-uri:", e, file=sys.stderr)
                try:
                    self.player.set_state(Gst.State.PAUSED)
                    self.player.get_state(int(3 * Gst.SECOND))
                except Exception:
                    pass
                if pos > 0.4:
                    try:
                        self.player.seek_simple(
                            Gst.Format.TIME,
                            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                            int(pos * Gst.SECOND),
                        )
                        self.player.get_state(int(2 * Gst.SECOND))
                    except Exception:
                        pass
                if was_playing:
                    try:
                        self.player.set_state(Gst.State.PLAYING)
                        self.is_playing = True
                        self.is_paused = False
                        self.play_button.set_label("❚❚")
                    except Exception:
                        pass
                elif was_paused:
                    self.is_playing = True
                    self.is_paused = True
                    self.play_button.set_label("▶")
            return ok
        except Exception as e:
            print("rebuild video color:", e, file=sys.stderr)
            return False

    def _link_through_video_color(self, pipe, prev, sink):
        """Insère videobalance+gamma seulement si colorimétrie activée."""
        if not bool(getattr(self, "colorimetry_enabled", False)):
            self._custom_vbalance = None
            self._custom_vgamma = None
            try:
                return bool(prev.link(sink))
            except Exception:
                return False
        vb = Gst.ElementFactory.make("videobalance", "mkv-vbalance")
        gm = Gst.ElementFactory.make("gamma", "mkv-vgamma")
        self._custom_vbalance = vb
        self._custom_vgamma = gm
        self._apply_color_props_to(vb, gm)
        chain = [el for el in (vb, gm) if el is not None]
        if not chain:
            try:
                return bool(prev.link(sink))
            except Exception:
                return False
        for el in chain:
            pipe.add(el)
            try:
                el.sync_state_with_parent()
            except Exception:
                pass
        try:
            if not prev.link(chain[0]):
                return bool(prev.link(sink))
            for i in range(len(chain) - 1):
                if not chain[i].link(chain[i + 1]):
                    chain[0].link(sink)
                    return True
            return bool(chain[-1].link(sink))
        except Exception as e:
            print("custom color link:", e, file=sys.stderr)
            try:
                return bool(prev.link(sink))
            except Exception:
                return False

    def toggle_colorimetry_enabled(self, menuitem=None):
        """Active/désactive la colorimétrie sans planter le pipeline 4K."""
        if menuitem is not None and hasattr(menuitem, "get_active"):
            want = bool(menuitem.get_active())
        else:
            want = not bool(getattr(self, "colorimetry_enabled", False))

        self.colorimetry_enabled = want
        ok = True

        try:
            custom = getattr(self, "_custom_pipeline", None) is not None
            path = getattr(self, "current_path", None)
            pos = float(getattr(self, "_last_known_position", 0) or 0)
            was_playing = self.is_playing and not self.is_paused
            was_paused = self.is_paused

            if custom and path and self._colorimetry_allowed():
                # Reconstruction propre du pipeline MKV (NULL implicite)
                try:
                    if not self._play_file_custom_mkv(path):
                        ok = False
                    else:
                        if pos > 0.5:
                            GLib.timeout_add(
                                500,
                                lambda p=pos: (self.seek_to(p) or False),
                            )
                        if not was_playing and was_paused:
                            GLib.timeout_add(
                                600,
                                lambda: (
                                    self._active_pipeline().set_state(
                                        Gst.State.PAUSED
                                    )
                                    or False
                                ),
                            )
                except Exception as e:
                    print("colorimetry toggle custom:", e, file=sys.stderr)
                    ok = False
                # playbin filter inutilisé en custom
                try:
                    self._rebuild_video_color_filter(restore_playback=False)
                except Exception:
                    pass
            else:
                # playbin : NULL → filtre → reprise (évite crash 4K)
                ok = bool(self._rebuild_video_color_filter(restore_playback=True))
        except Exception as e:
            print("colorimetry toggle:", e, file=sys.stderr)
            ok = False

        if not ok and want:
            # Échec à l'activation : repasse OFF pour rester stable
            self.colorimetry_enabled = False
            try:
                if menuitem is not None and hasattr(menuitem, "set_active"):
                    menuitem.handler_block_by_func(self.toggle_colorimetry_enabled)
                    menuitem.set_active(False)
                    menuitem.handler_unblock_by_func(self.toggle_colorimetry_enabled)
            except Exception:
                try:
                    if menuitem is not None:
                        menuitem.set_active(False)
                except Exception:
                    pass
            try:
                self._rebuild_video_color_filter(restore_playback=True)
            except Exception:
                pass
            self.status_label.set_text(
                self.tr("Colorimétrie")
                + " — "
                + self.tr("échec, désactivée")
            )
        else:
            self.status_label.set_text(
                self.tr("Colorimétrie activée")
                if self.colorimetry_enabled
                else self.tr("Colorimétrie désactivée")
            )

        try:
            self._update_colorimetry_menu_sensitivity()
        except Exception:
            pass
        try:
            self.save_state()
        except Exception:
            pass
        if not self.colorimetry_enabled:
            win = getattr(self, "colorimetry_window", None)
            if win is not None:
                try:
                    win.destroy()
                except Exception:
                    pass

    def show_colorimetry(self, *args):
        if not self._colorimetry_allowed():
            self.status_label.set_text(
                self.tr("Colorimétrie") + " — " + self.tr("vidéo uniquement")
            )
            return
        if not bool(getattr(self, "colorimetry_enabled", False)):
            self.status_label.set_text(
                self.tr("Colorimétrie désactivée")
            )
            return
        if self.colorimetry_window is not None:
            self.colorimetry_window.present()
            return

        win = Gtk.Window(title=self.tr("Colorimétrie"))
        win.set_transient_for(self)
        win.set_default_size(self._ui_s(400), self._ui_s(220))
        win.set_resizable(False)
        win.set_border_width(14)
        self.colorimetry_window = win

        def on_destroy(*_a):
            self.colorimetry_window = None

        win.connect("destroy", on_destroy)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        win.add(main)

        def _row(label, lo, hi, step, value, on_change):
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lab = Gtk.Label(label=label, xalign=0)
            lab.set_size_request(self._ui_s(110), -1)
            box.pack_start(lab, False, False, 0)
            scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, lo, hi, step
            )
            scale.set_value(value)
            scale.set_hexpand(True)
            scale.set_draw_value(True)
            scale.set_digits(2)
            scale.connect("value-changed", on_change)
            box.pack_start(scale, True, True, 0)
            main.pack_start(box, False, False, 0)
            return scale

        def on_bri(s):
            self.color_brightness = float(s.get_value())
            self._apply_video_colorimetry()

        def on_con(s):
            self.color_contrast = float(s.get_value())
            self._apply_video_colorimetry()

        def on_sat(s):
            self.color_saturation = float(s.get_value())
            self._apply_video_colorimetry()

        def on_gam(s):
            self.color_gamma = float(s.get_value())
            self._apply_video_colorimetry()

        self._color_bri_scale = _row(
            self.tr("Luminosité"), -1.0, 1.0, 0.01,
            self.color_brightness, on_bri,
        )
        self._color_con_scale = _row(
            self.tr("Contraste"), 0.0, 2.0, 0.01,
            self.color_contrast, on_con,
        )
        self._color_sat_scale = _row(
            self.tr("Saturation"), 0.0, 2.0, 0.01,
            self.color_saturation, on_sat,
        )
        self._color_gam_scale = _row(
            self.tr("Gamma"), 0.30, 3.0, 0.01,
            self.color_gamma, on_gam,
        )

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_halign(Gtk.Align.END)
        main.pack_start(btn_box, False, False, 0)

        def on_reset(_b):
            self.color_brightness = 0.0
            self.color_contrast = 1.0
            self.color_saturation = 1.0
            self.color_gamma = 1.0
            for scale, val in (
                (self._color_bri_scale, 0.0),
                (self._color_con_scale, 1.0),
                (self._color_sat_scale, 1.0),
                (self._color_gam_scale, 1.0),
            ):
                try:
                    scale.set_value(val)
                except Exception:
                    pass
            self._apply_video_colorimetry()
            self.status_label.set_text(self.tr("Colorimétrie réinitialisée"))

        reset_btn = Gtk.Button(label=self.tr("Réinitialiser"))
        reset_btn.connect("clicked", on_reset)
        btn_box.pack_start(reset_btn, False, False, 0)
        close_btn = Gtk.Button(label=self.tr("Fermer"))
        close_btn.connect("clicked", lambda b: win.destroy())
        btn_box.pack_start(close_btn, False, False, 0)

        win.show_all()

    # ========================================================
    # PITCH / TEMPO
    # ========================================================

    def show_pitch_tempo(self, *args):

        if not is_pure_audio_file(getattr(self, "current_path", None)):
            self.status_label.set_text(
                self.tr("Pitch / Tempo") + " — " + self.tr("audio uniquement")
            )
            return

        if self.pitch is None:
            dialog = Gtk.MessageDialog(
                parent=self,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text=self.tr("Pitch / Tempo non disponible")
            )
            dialog.format_secondary_text(
                self.tr("L'élément GStreamer « pitch » (plugin SoundTouch) n'est pas installé.") + "\n" +
                self.tr("Installez gstreamer1.0-plugins-bad pour l'activer.")
            )
            dialog.run()
            dialog.destroy()
            return

        if self.pitch_window is not None:
            self.pitch_window.present()
            return

        win = Gtk.Window(title=self.tr("Pitch / Tempo"))
        win.set_transient_for(self)
        win.set_default_size(self._ui_s(380), self._ui_s(150))
        win.set_resizable(False)
        win.set_border_width(14)

        self.pitch_window = win

        def on_pitch_destroy(*args):
            self.pitch_window = None
            self.pitch_scale = None
            self.tempo_scale = None

        win.connect("destroy", on_pitch_destroy)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        win.add(main_box)

        pitch_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        main_box.pack_start(pitch_row, False, False, 0)

        pitch_label = Gtk.Label(label=self.tr("Pitch"))
        pitch_label.set_width_chars(7)
        pitch_label.set_xalign(0)
        pitch_row.pack_start(pitch_label, False, False, 0)

        self.pitch_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0.50, 2.00, 0.01
        )
        self.pitch_scale.set_value(self.pitch_value)
        self.pitch_scale.set_draw_value(True)
        self.pitch_scale.set_value_pos(Gtk.PositionType.RIGHT)
        self.pitch_scale.set_digits(2)
        self.pitch_scale.set_hexpand(True)
        self.pitch_scale.connect("value-changed", self.on_pitch_changed)
        pitch_row.pack_start(self.pitch_scale, True, True, 0)

        tempo_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        main_box.pack_start(tempo_row, False, False, 0)

        tempo_label = Gtk.Label(label=self.tr("Tempo"))
        tempo_label.set_width_chars(7)
        tempo_label.set_xalign(0)
        tempo_row.pack_start(tempo_label, False, False, 0)

        self.tempo_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0.50, 2.00, 0.01
        )
        self.tempo_scale.set_value(self.tempo_value)
        self.tempo_scale.set_draw_value(True)
        self.tempo_scale.set_value_pos(Gtk.PositionType.RIGHT)
        self.tempo_scale.set_digits(2)
        self.tempo_scale.set_hexpand(True)
        self.tempo_scale.connect("value-changed", self.on_tempo_changed)
        tempo_row.pack_start(self.tempo_scale, True, True, 0)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        button_box.set_halign(Gtk.Align.END)
        main_box.pack_start(button_box, False, False, 0)

        reset_btn = Gtk.Button(label=self.tr("Réinitialiser"))
        reset_btn.connect("clicked", self.reset_pitch_tempo)
        button_box.pack_start(reset_btn, False, False, 0)

        close_btn = Gtk.Button(label=self.tr("Fermer"))
        close_btn.connect("clicked", lambda b: win.destroy())
        button_box.pack_start(close_btn, False, False, 0)

        win.show_all()

    def on_pitch_changed(self, scale):

        value = scale.get_value()
        old_need = (
            abs(self.pitch_value - 1.0) > 0.001
            or abs(self.tempo_value - 1.0) > 0.001
        )
        self.pitch_value = value
        new_need = (
            abs(value - 1.0) > 0.001
            or abs(self.tempo_value - 1.0) > 0.001
        )

        # Ne jamais reconstruire le pipeline hors audio local
        # (sinon NULL + seek → vidéo masquée / qui recule)
        if not is_pure_audio_file(getattr(self, "current_path", None)):
            self.status_label.set_text(
                self.tr("Pitch : ")
                + f"{value:.2f}"
                + " — "
                + self.tr("audio uniquement")
            )
            return

        self.rebuild_audio_filter(force=(old_need != new_need))
        self.status_label.set_text(
            self.tr("Pitch : ")
            + f"{value:.2f}"
            + self.tr("  (actif)" if new_need else "  (neutre)")
        )

    def on_tempo_changed(self, scale):

        value = scale.get_value()
        old_need = (
            abs(self.pitch_value - 1.0) > 0.001
            or abs(self.tempo_value - 1.0) > 0.001
        )
        self.tempo_value = value
        new_need = (
            abs(self.pitch_value - 1.0) > 0.001
            or abs(value - 1.0) > 0.001
        )

        if not is_pure_audio_file(getattr(self, "current_path", None)):
            self.status_label.set_text(
                self.tr("Tempo : ")
                + f"{value:.2f}"
                + " — "
                + self.tr("audio uniquement")
            )
            return

        self.rebuild_audio_filter(force=(old_need != new_need))
        self.status_label.set_text(
            self.tr("Tempo : ")
            + f"{value:.2f}"
            + self.tr("  (actif)" if new_need else "  (neutre)")
        )

    def reset_pitch_tempo(self, *args):

        self.pitch_value = 1.0
        self.tempo_value = 1.0

        if self.pitch_scale is not None:
            self.pitch_scale.handler_block_by_func(self.on_pitch_changed)
            self.pitch_scale.set_value(1.0)
            self.pitch_scale.handler_unblock_by_func(self.on_pitch_changed)

        if self.tempo_scale is not None:
            self.tempo_scale.handler_block_by_func(self.on_tempo_changed)
            self.tempo_scale.set_value(1.0)
            self.tempo_scale.handler_unblock_by_func(self.on_tempo_changed)

        if is_pure_audio_file(getattr(self, "current_path", None)):
            self.rebuild_audio_filter(force=True)
        self.status_label.set_text(self.tr("Pitch / Tempo réinitialisés (pipeline léger)"))

    def _update_pitch_menu_sensitivity(self):
        """Grise Pitch/Tempo hors fichiers audio locaux."""
        item = getattr(self, "pitch_menu_item", None)
        if item is None:
            return
        allowed = is_pure_audio_file(getattr(self, "current_path", None))
        try:
            item.set_sensitive(allowed)
        except Exception:
            pass
        # Ferme la fenêtre si on passe sur un média non audio
        if not allowed and getattr(self, "pitch_window", None) is not None:
            try:
                self.pitch_window.destroy()
            except Exception:
                pass

    # ========================================================
    # MENU
    # ========================================================

    # ========================================================
    # NOTIFICATIONS BUREAU
    # ========================================================

    def _notify_track_change(self, title, artist, album=None):
        """Notification libnotify au changement de piste (complément MPRIS)."""
        if not getattr(self, "desktop_notifications", True):
            return
        if Notify is None:
            return
        if getattr(self, "_notify_unavailable", False):
            return
        # LXQt minimal : souvent pas de service org.freedesktop.Notifications
        if not getattr(self, "_notify_service_checked", False):
            self._notify_service_checked = True
            try:
                bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                has = bus.call_sync(
                    "org.freedesktop.DBus",
                    "/org/freedesktop/DBus",
                    "org.freedesktop.DBus",
                    "NameHasOwner",
                    GLib.Variant("(s)", ("org.freedesktop.Notifications",)),
                    GLib.VariantType("(b)"),
                    Gio.DBusCallFlags.NONE,
                    500,
                    None,
                ).unpack()[0]
                if not has:
                    self._notify_unavailable = True
                    return
            except Exception:
                # On tente quand même une fois ci-dessous
                pass
        title = (title or "").strip() or self.tr("Inconnu")
        artist = (artist or "").strip()
        key = (title, artist)
        if key == getattr(self, "_last_notify_key", None):
            return
        self._last_notify_key = key
        body = artist
        if album and str(album).strip():
            body = (body + " — " + str(album).strip()) if body else str(album).strip()
        try:
            n = Notify.Notification.new(title, body or None, "audio-x-generic")
            try:
                n.set_urgency(Notify.Urgency.LOW)
                n.set_timeout(4000)
            except Exception:
                pass
            n.show()
        except Exception:
            # Service absent (LXQt) → silencieux, on n'insiste plus
            self._notify_unavailable = True

    # ========================================================
    # INHIBITION VEILLE (vidéo)
    # ========================================================

    def _get_screensaver_proxy(self):
        """Choisit un backend D-Bus pour inhiber la veille (si disponible).

        Sous Lubuntu/XFCE, org.freedesktop.ScreenSaver est souvent absent :
        on tente aussi SessionManager GNOME et xfce4-power-manager.
        Absence de service = normal, pas une erreur fatale.
        """
        cached = getattr(self, "_screensaver_proxy", None)
        if cached is not None:
            return cached

        candidates = (
            # (kind, bus_name, object_path, interface)
            (
                "fdo",
                "org.freedesktop.ScreenSaver",
                "/org/freedesktop/ScreenSaver",
                "org.freedesktop.ScreenSaver",
            ),
            (
                "fdo",
                "org.freedesktop.ScreenSaver",
                "/ScreenSaver",
                "org.freedesktop.ScreenSaver",
            ),
            (
                "gnome",
                "org.gnome.SessionManager",
                "/org/gnome/SessionManager",
                "org.gnome.SessionManager",
            ),
            (
                "xfce",
                "org.xfce.PowerManager",
                "/org/xfce/PowerManager",
                "org.xfce.Power.Manager",
            ),
            (
                "lxqt",
                "org.freedesktop.PowerManagement",
                "/org/freedesktop/PowerManagement",
                "org.freedesktop.PowerManagement.Inhibit",
            ),
        )
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception:
            self._screensaver_proxy = False
            return False

        for kind, name, obj, iface in candidates:
            try:
                # Vérifie qu'un propriétaire existe (évite ServiceUnknown au call)
                if bus.call_sync(
                    "org.freedesktop.DBus",
                    "/org/freedesktop/DBus",
                    "org.freedesktop.DBus",
                    "NameHasOwner",
                    GLib.Variant("(s)", (name,)),
                    GLib.VariantType("(b)"),
                    Gio.DBusCallFlags.NONE,
                    500,
                    None,
                ).unpack()[0] is not True:
                    continue
                proxy = Gio.DBusProxy.new_sync(
                    bus,
                    Gio.DBusProxyFlags.NONE,
                    None,
                    name,
                    obj,
                    iface,
                    None,
                )
                self._screensaver_proxy = (kind, proxy)
                return self._screensaver_proxy
            except Exception:
                continue

        self._screensaver_proxy = False
        return False

    def _inhibit_screensaver(self, enable):
        """Inhibe la veille pendant la lecture vidéo uniquement.

        Silencieux si aucun service D-Bus n'est disponible (cas fréquent
        sous Lubuntu minimal) — la lecture n'est pas affectée.
        """
        try:
            if not enable:
                cookie = getattr(self, "_screensaver_cookie", None)
                kind_cookie = getattr(self, "_screensaver_cookie_kind", None)
                if cookie is None:
                    return
                proxy_info = self._get_screensaver_proxy()
                self._screensaver_cookie = None
                self._screensaver_cookie_kind = None
                if not proxy_info:
                    return
                kind, proxy = proxy_info
                try:
                    if kind == "gnome":
                        proxy.call_sync(
                            "Uninhibit",
                            GLib.Variant("(u)", (int(cookie),)),
                            Gio.DBusCallFlags.NONE,
                            -1,
                            None,
                        )
                    elif kind == "xfce":
                        pass
                    elif kind == "lxqt":
                        try:
                            proxy.call_sync(
                                "UnInhibit",
                                GLib.Variant("(u)", (int(cookie),)),
                                Gio.DBusCallFlags.NONE,
                                -1,
                                None,
                            )
                        except Exception:
                            try:
                                proxy.call_sync(
                                    "Uninhibit",
                                    GLib.Variant("(u)", (int(cookie),)),
                                    Gio.DBusCallFlags.NONE,
                                    -1,
                                    None,
                                )
                            except Exception:
                                pass
                    else:
                        try:
                            proxy.call_sync(
                                "UnInhibit",
                                GLib.Variant("(u)", (int(cookie),)),
                                Gio.DBusCallFlags.NONE,
                                -1,
                                None,
                            )
                        except Exception:
                            proxy.call_sync(
                                "Uninhibit",
                                GLib.Variant("(u)", (int(cookie),)),
                                Gio.DBusCallFlags.NONE,
                                -1,
                                None,
                            )
                except Exception:
                    pass
                return

            if getattr(self, "_screensaver_cookie", None) is not None:
                return
            if not getattr(self, "current_is_video", False):
                return
            if not (
                getattr(self, "is_playing", False)
                and not getattr(self, "is_paused", False)
            ):
                return
            proxy_info = self._get_screensaver_proxy()
            if not proxy_info:
                return
            kind, proxy = proxy_info
            reason = "Playing video"
            try:
                if kind == "gnome":
                    res = proxy.call_sync(
                        "Inhibit",
                        GLib.Variant("(susu)", (APP_NAME, 0, reason, 8)),
                        Gio.DBusCallFlags.NONE,
                        -1,
                        None,
                    )
                    self._screensaver_cookie = res.unpack()[0]
                    self._screensaver_cookie_kind = kind
                elif kind == "xfce":
                    self._screensaver_proxy = False
                elif kind == "lxqt":
                    try:
                        res = proxy.call_sync(
                            "Inhibit",
                            GLib.Variant("(ss)", (APP_NAME, reason)),
                            Gio.DBusCallFlags.NONE,
                            -1,
                            None,
                        )
                        self._screensaver_cookie = res.unpack()[0]
                        self._screensaver_cookie_kind = kind
                    except Exception:
                        self._screensaver_proxy = False
                else:
                    res = proxy.call_sync(
                        "Inhibit",
                        GLib.Variant("(ss)", (APP_NAME, reason)),
                        Gio.DBusCallFlags.NONE,
                        -1,
                        None,
                    )
                    self._screensaver_cookie = res.unpack()[0]
                    self._screensaver_cookie_kind = kind
            except Exception:
                # Service déclaré mais méthode absente / refusée → on n'insiste pas
                self._screensaver_proxy = False
                self._screensaver_cookie = None
        except Exception:
            pass

    def _update_screensaver_inhibit(self):
        want = (
            getattr(self, "current_is_video", False)
            and getattr(self, "is_playing", False)
            and not getattr(self, "is_paused", False)
        )
        self._inhibit_screensaver(want)

    # ========================================================
    # MINI-LECTEUR
    # ========================================================

    def toggle_mini_player(self, *args):
        self.mini_player_mode = not getattr(self, "mini_player_mode", False)
        self._apply_mini_player_mode()
        try:
            self.save_state()
        except Exception:
            pass

    def toggle_mini_banner(self, *args):
        """Active / désactive la bannière défilante du mini-lecteur."""
        self.mini_banner_enabled = not getattr(self, "mini_banner_enabled", True)
        self._apply_mini_banner_state()
        try:
            self.save_state()
        except Exception:
            pass

    def _apply_mini_banner_state(self):
        """Affiche ou masque la bannière (effet immédiat en mode mini)."""
        if not getattr(self, "mini_player_mode", False):
            return
        area = getattr(self, "mini_title_area", None)
        if area is None:
            return
        try:
            if getattr(self, "mini_banner_enabled", True):
                self.set_title(APP_NAME)
                self._mini_marquee_start()
            else:
                self._stop_mini_title_scroll()
                area.hide()
            self._update_mini_title()
            # La hauteur suit : GTK reprend le minimum requis
            self.resize(max(1, self.get_allocated_width()), 1)
        except Exception as e:
            print("mini banner:", e, file=sys.stderr)

    def _build_mini_title_marquee(self, main):
        """Bande de titre du mini-lecteur (cachée hors mode mini)."""
        area = Gtk.DrawingArea()
        area.set_size_request(-1, self._ui_s(self._MINI_MARQUEE_HEIGHT))
        area.set_hexpand(True)
        area.get_style_context().add_class("mini-title")
        area.connect("draw", self._mini_marquee_draw)
        area.set_no_show_all(True)
        area.hide()
        main.pack_start(area, False, False, 0)
        self.mini_title_area = area

        layout = area.create_pango_layout("")
        try:
            # Taille de police proportionnelle au scale UI
            font_pt = max(8, int(round(9 * float(getattr(self, "ui_scale", 1.0) or 1.0))))
            layout.set_font_description(
                Pango.FontDescription.from_string(f"Sans Bold {font_pt}")
            )
        except Exception:
            try:
                layout.set_font_description(
                    Pango.FontDescription.from_string(self._MINI_MARQUEE_FONT)
                )
            except Exception:
                pass
        self._mini_mq_layout = layout
        self._mini_mq_text = None
        self._mini_mq_text_w = 0
        self._mini_mq_text_h = 0
        self._mini_mq_offset = 0.0
        self._mini_mq_drawn = -1
        self._mini_mq_last_us = None
        self._mini_mq_pause_until = 0
        self._mini_mq_tick_id = 0

    def _mini_marquee_start(self):
        area = getattr(self, "mini_title_area", None)
        if area is None:
            return
        area.show()
        if not self._mini_mq_tick_id:
            self._mini_mq_last_us = None
            self._mini_mq_tick_id = area.add_tick_callback(
                self._mini_marquee_tick
            )

    def _stop_mini_title_scroll(self):
        area = getattr(self, "mini_title_area", None)
        tid = getattr(self, "_mini_mq_tick_id", 0) or 0
        if tid and area is not None:
            try:
                area.remove_tick_callback(tid)
            except Exception:
                pass
        self._mini_mq_tick_id = 0
        self._mini_mq_text = None
        self._mini_mq_offset = 0.0
        self._mini_mq_drawn = -1
        self._mini_mq_last_us = None

    def _mini_marquee_tick(self, widget, frame_clock, *args):
        """Appelé à chaque image (synchronisé avec l'écran) : défilement fluide."""
        if not getattr(self, "mini_player_mode", False):
            self._mini_mq_tick_id = 0
            return False

        now = frame_clock.get_frame_time()  # microsecondes
        last = self._mini_mq_last_us
        self._mini_mq_last_us = now
        if last is None or not self._mini_mq_text:
            return True
        if now < self._mini_mq_pause_until:
            return True

        width = widget.get_allocated_width()
        text_w = self._mini_mq_text_w
        pad = self._ui_s(self._MINI_MARQUEE_PAD)

        # Titre qui tient en entier et défilement permanent désactivé : fixe
        if (not self._MINI_MARQUEE_ALWAYS_SCROLL) and text_w <= width - 2 * pad:
            if self._mini_mq_offset != 0.0:
                self._mini_mq_offset = 0.0
                self._mini_mq_drawn = -1
                widget.queue_draw()
            return True

        period = text_w + self._ui_s(self._MINI_MARQUEE_GAP)
        dt = min(0.1, max(0.0, (now - last) / 1000000.0))
        self._mini_mq_offset += (self._MINI_MARQUEE_SPEED * float(getattr(self, "ui_scale", 1.0) or 1.0)) * dt
        if self._mini_mq_offset >= period:
            # Fin d'un tour : retour à 0 (image identique, donc sans à-coup) + pause
            self._mini_mq_offset = 0.0
            self._mini_mq_pause_until = (
                now + self._MINI_MARQUEE_PAUSE_MS * 1000
            )

        # Position entière : texte net, sans scintillement
        x = int(self._mini_mq_offset)
        if x != self._mini_mq_drawn:
            self._mini_mq_drawn = x
            widget.queue_draw()
        return True

    def _mini_marquee_draw(self, widget, cr):
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        ctx = widget.get_style_context()
        Gtk.render_background(ctx, cr, 0, 0, width, height)
        Gtk.render_frame(ctx, cr, 0, 0, width, height)

        layout = self._mini_mq_layout
        text_w = self._mini_mq_text_w
        if (
            layout is None
            or PangoCairo is None
            or not self._mini_mq_text
            or text_w <= 0
        ):
            return False

        rgba = ctx.get_color(Gtk.StateFlags.NORMAL)
        cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, rgba.alpha)
        y = int(round((height - self._mini_mq_text_h) / 2.0))
        pad = self._ui_s(self._MINI_MARQUEE_PAD)

        def paint(x):
            cr.move_to(x, y)
            PangoCairo.show_layout(cr, layout)

        scrolling = (
            self._MINI_MARQUEE_ALWAYS_SCROLL or text_w > width - 2 * pad
        )
        if not scrolling:
            paint(pad)
            return False

        period = text_w + self._ui_s(self._MINI_MARQUEE_GAP)
        x = pad - int(self._mini_mq_offset)
        while x < width:
            if x + text_w > 0:
                paint(x)
            x += period
        return False

    def _compose_mini_banner_text(self):
        """Texte bannière mini-lecteur : « Artiste — Titre » (fichier, CD, radio, TV)."""

        def _clean(s):
            return (s or "").strip()

        def _join(artist, title):
            artist, title = _clean(artist), _clean(title)
            if artist and title:
                low_t, low_a = title.casefold(), artist.casefold()
                if low_t == low_a or low_t.startswith(low_a + " —") or low_t.startswith(low_a + " -"):
                    return title
                return "%s — %s" % (artist, title)
            return title or artist or ""

        path = getattr(self, "current_path", None)
        empty = self.tr("Aucun morceau sélectionné")

        if path and is_stream(path):
            station = _clean(
                getattr(self, "current_tv_name", None)
                or self.find_tv_name(path)
                or getattr(self, "current_radio_name", None)
                or self.find_radio_name(path)
            )
            icy_title = _clean(getattr(self, "icy_title", None))
            icy_artist = _clean(getattr(self, "icy_artist", None))
            now = _join(icy_artist, icy_title)
            if station and now:
                low_s, low_n = station.casefold(), now.casefold()
                if low_n == low_s or low_n.startswith(low_s + " —") or low_n.startswith(low_s + " -"):
                    return now
                return "%s — %s" % (station, now)
            return station or now or self.tr("En direct")

        if path and is_cdda(path):
            track = get_cdda_track_number(path) or 0
            title = self.tr("Piste ") + "%02d" % track
            artist = ""
            try:
                artist = _clean(self.bottom_artist.get_text())
            except Exception:
                pass
            if not artist or artist == self.tr("CD Audio"):
                try:
                    t, a, *_rest = self.get_metadata(path, load_cover=False)
                    if a:
                        artist = _clean(a)
                    if t and _clean(t) and not _clean(t).startswith("Piste"):
                        title = _clean(t)
                except Exception:
                    pass
            return _join(artist or self.tr("CD Audio"), title)

        title = artist = ""
        try:
            if hasattr(self, "bottom_title"):
                title = _clean(self.bottom_title.get_text())
            if hasattr(self, "bottom_artist"):
                artist = _clean(self.bottom_artist.get_text())
        except Exception:
            pass
        if title == empty:
            title = ""
        if not title and path:
            try:
                title = (
                    os.path.basename(path)
                    if not is_special_source(path)
                    else path
                )
            except Exception:
                title = ""
        return _join(artist, title)

    def _update_mini_title(self):
        """Met à jour le titre du mini-lecteur (bannière ou barre de fenêtre)."""
        if not getattr(self, "mini_player_mode", False):
            self._stop_mini_title_scroll()
            try:
                self.set_title(APP_NAME)
            except Exception:
                pass
            return

        try:
            title = self._compose_mini_banner_text()
        except Exception as exc:
            print("compose mini banner:", exc, file=sys.stderr)
            title = ""

        if not getattr(self, "mini_banner_enabled", True):
            try:
                self.set_title(
                    "%s — %s" % (APP_NAME, title) if title else APP_NAME
                )
            except Exception:
                pass
            return

        # Même texte → pas de reset (les tags ICY radio arrivent souvent)
        if title == getattr(self, "_mini_mq_text", None):
            return

        self._mini_mq_text = title
        layout = getattr(self, "_mini_mq_layout", None)
        text_w = text_h = 0
        if layout is not None:
            try:
                layout.set_text(title or "", -1)
                text_w, text_h = layout.get_pixel_size()
            except Exception:
                pass
        self._mini_mq_text_w = text_w
        self._mini_mq_text_h = text_h
        self._mini_mq_offset = 0.0
        self._mini_mq_drawn = -1
        self._mini_mq_pause_until = (
            GLib.get_monotonic_time() + self._MINI_MARQUEE_PAUSE_MS * 1000
        )
        area = getattr(self, "mini_title_area", None)
        if area is not None:
            area.queue_draw()

    def _apply_mini_player_mode(self):
        mini = bool(getattr(self, "mini_player_mode", False))
        marquee = getattr(self, "mini_title_area", None)
        try:
            if mini:
                if self._normal_size is None:
                    try:
                        self._normal_size = self.get_size()
                    except Exception:
                        self._normal_size = (950, 620)
                try:
                    child = self.get_child()
                    if child is not None:
                        for c in child.get_children():
                            if (
                                c is not getattr(self, "top_bar", None)
                                and c is not marquee
                            ):
                                c.hide()
                except Exception:
                    pass
                self.set_keep_above(True)
                try:
                    # Hauteur libre : GTK prend le minimum (boutons + bande titre)
                    self.set_size_request(self._ui_s(420), -1)
                    self.resize(self._ui_s(520), 1)
                except Exception:
                    pass
                try:
                    self.set_title(APP_NAME)
                except Exception:
                    pass
                try:
                    if getattr(self, "mini_banner_enabled", True):
                        self._mini_marquee_start()
                    elif marquee is not None:
                        marquee.hide()
                    self._update_mini_title()
                except Exception:
                    pass
            else:
                self._stop_mini_title_scroll()
                try:
                    self.set_title(APP_NAME)
                except Exception:
                    pass
                if marquee is not None:
                    try:
                        marquee.hide()
                    except Exception:
                        pass
                try:
                    child = self.get_child()
                    if child is not None:
                        for c in child.get_children():
                            if c is marquee:
                                continue
                            c.show()
                except Exception:
                    pass
                self.set_keep_above(False)
                self.set_size_request(self._ui_s(700), self._ui_s(430))
                if self._normal_size:
                    try:
                        self.resize(*self._normal_size)
                    except Exception:
                        pass
                if getattr(self, "current_is_video", False):
                    try:
                        self.show_video_area(True)
                    except Exception:
                        pass
                elif getattr(self, "lyrics_mode", False):
                    try:
                        self._refresh_lyrics_for_current()
                    except Exception:
                        pass
                else:
                    try:
                        self.show_video_area(False)
                    except Exception:
                        pass
        except Exception as e:
            print("mini player:", e, file=sys.stderr)

    # ========================================================
    # IMPORT / EXPORT PLAYLIST M3U / PLS
    # ========================================================

    def import_playlist_file(self, *args):
        dialog = Gtk.FileChooserDialog(
            title=self.tr("Importer une playlist…"),
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        dialog.add_button(Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        filt = Gtk.FileFilter()
        filt.set_name("M3U / PLS")
        filt.add_pattern("*.m3u")
        filt.add_pattern("*.m3u8")
        filt.add_pattern("*.pls")
        dialog.add_filter(filt)
        resp = dialog.run()
        path = dialog.get_filename()
        dialog.destroy()
        if resp != Gtk.ResponseType.OK or not path:
            return
        try:
            paths = self._parse_playlist_file(path)
        except Exception as e:
            print("import playlist:", e, file=sys.stderr)
            paths = []
        added = 0
        for p in paths:
            if not p:
                continue
            if (
                is_stream(p)
                or is_cdda(p)
                or is_dvd(p)
                or is_bluray(p)
                or os.path.isfile(p)
            ):
                try:
                    self.add_file(p, save=False)
                    added += 1
                except Exception:
                    pass
        if added:
            self.save_playlist()
            self.status_label.set_text(
                f"{added} "
                + self.tr("fichier(s) ajouté(s)")
                + " — "
                + self.tr("Playlist importée")
            )
            try:
                self.tree.queue_draw()
            except Exception:
                pass
        else:
            self.status_label.set_text(
                self.tr("Aucun fichier valide dans la playlist")
            )

    def export_playlist_file(self, *args):
        paths = self.get_paths()
        if not paths:
            self.status_label.set_text(
                self.tr("Aucun fichier valide dans la playlist")
            )
            return
        dialog = Gtk.FileChooserDialog(
            title=self.tr("Exporter la playlist…"),
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        dialog.add_button(Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
        dialog.set_current_name("playlist.m3u")
        dialog.set_do_overwrite_confirmation(True)
        filt = Gtk.FileFilter()
        filt.set_name("M3U")
        filt.add_pattern("*.m3u")
        filt.add_pattern("*.m3u8")
        dialog.add_filter(filt)
        filt2 = Gtk.FileFilter()
        filt2.set_name("PLS")
        filt2.add_pattern("*.pls")
        dialog.add_filter(filt2)
        resp = dialog.run()
        out = dialog.get_filename()
        dialog.destroy()
        if resp != Gtk.ResponseType.OK or not out:
            return
        try:
            low = out.lower()
            if low.endswith(".pls"):
                self._write_pls(out, paths)
            else:
                if not (low.endswith(".m3u") or low.endswith(".m3u8")):
                    out = out + ".m3u"
                self._write_m3u(out, paths)
            self.status_label.set_text(
                self.tr("Playlist exportée")
                + " — "
                + os.path.basename(out)
            )
        except Exception as e:
            print("export playlist:", e, file=sys.stderr)
            self.status_label.set_text(str(e))

    def _parse_playlist_file(self, path):
        """Parse M3U/M3U8/PLS → chemins ou URL."""
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        base = os.path.dirname(os.path.abspath(path))
        low = path.lower()
        results = []
        if low.endswith(".pls"):
            entries = {}
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("[") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                entries[k.strip().lower()] = v.strip()
            for i in range(1, len(entries) + 2):
                key = "file%d" % i
                if key in entries:
                    results.append(entries[key])
        else:
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                results.append(line)
        resolved = []
        for p in results:
            p = p.strip().strip('"')
            if not p:
                continue
            if is_stream(p) or "://" in p:
                resolved.append(p)
            elif os.path.isabs(p):
                resolved.append(p)
            else:
                resolved.append(os.path.normpath(os.path.join(base, p)))
        return resolved

    def _write_m3u(self, path, paths):
        with open(path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for p in paths:
                title = os.path.basename(p)
                try:
                    t, a, *_rest = self.get_metadata(p, load_cover=False)
                    if t:
                        title = t if not a else "%s - %s" % (a, t)
                except Exception:
                    pass
                f.write("#EXTINF:-1,%s\n" % title.replace("\n", " "))
                f.write("%s\n" % p)

    def _write_pls(self, path, paths):
        with open(path, "w", encoding="utf-8") as f:
            f.write("[playlist]\n")
            f.write("NumberOfEntries=%d\n" % len(paths))
            for i, p in enumerate(paths, 1):
                f.write("File%d=%s\n" % (i, p))
                title = os.path.basename(p)
                try:
                    t, a, *_rest = self.get_metadata(p, load_cover=False)
                    if t:
                        title = t if not a else "%s - %s" % (a, t)
                except Exception:
                    pass
                f.write("Title%d=%s\n" % (i, title.replace("\n", " ")))
            f.write("Version=2\n")

    # ========================================================
    # HISTORIQUE RÉCENT
    # ========================================================

    def _load_recent_files(self):
        self.recent_files = []
        try:
            if not os.path.isfile(RECENT_FILE):
                return
            with open(RECENT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self.recent_files = [
                    p for p in data if isinstance(p, str) and p.strip()
                ][:RECENT_MAX]
        except Exception as e:
            print("load recent:", e, file=sys.stderr)
            self.recent_files = []

    def _save_recent_files(self):
        try:
            os.makedirs(os.path.dirname(RECENT_FILE), exist_ok=True)
            tmp = RECENT_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    self.recent_files[:RECENT_MAX], f, ensure_ascii=False, indent=2
                )
            os.replace(tmp, RECENT_FILE)
        except Exception as e:
            print("save recent:", e, file=sys.stderr)

    def _push_recent_file(self, path):
        if not path or not isinstance(path, str):
            return
        if is_stream(path) or is_cdda(path) or is_dvd(path) or is_bluray(path):
            return
        try:
            if os.path.isfile(path):
                path = os.path.abspath(path)
        except Exception:
            pass
        try:
            self.recent_files = [p for p in self.recent_files if p != path]
            self.recent_files.insert(0, path)
            self.recent_files = self.recent_files[:RECENT_MAX]
            self._save_recent_files()
        except Exception:
            pass

    def clear_recent_files(self, *args):
        self.recent_files = []
        self._save_recent_files()
        try:
            self.status_label.set_text(self.tr("Historique vide"))
        except Exception:
            pass

    def _play_recent_file(self, path):
        if not path:
            return
        if not (
            is_stream(path) or is_cdda(path) or is_dvd(path) or is_bluray(path)
        ):
            if not os.path.isfile(path):
                try:
                    self.status_label.set_text(
                        self.tr("Fichier manquant")
                        + " — "
                        + os.path.basename(path)
                    )
                except Exception:
                    pass
                return
        try:
            paths = self.get_paths() if hasattr(self, "get_paths") else []
            if path not in paths:
                self.add_file(path, save=True)
        except Exception:
            pass
        self.play_file(path)


    def show_menu(self, button):

        # Un seul menu principal à la fois (évite fuites / doublons)
        old = getattr(self, "_main_popup_menu", None)
        if old is not None:
            try:
                old.popdown()
            except Exception:
                pass
            try:
                old.destroy()
            except Exception:
                pass
            self._main_popup_menu = None

        menu = Gtk.Menu()
        self._main_popup_menu = menu

        # ---- Radios ----
        radios_menu = Gtk.Menu()
        radios_item = Gtk.MenuItem(label=self.tr("Radios"))
        radios_item.set_submenu(radios_menu)

        if self.radios:
            for radio in self.radios:
                item = Gtk.MenuItem(label=radio["name"])
                item.connect("activate", lambda w, u=radio["url"]: self.play_radio(u))
                radios_menu.append(item)
            radios_menu.append(Gtk.SeparatorMenuItem())

        manage_item = Gtk.MenuItem()
        manage_label = Gtk.Label()
        manage_label.set_markup("<i>" + GLib.markup_escape_text(self.tr("Gérer les radios…")) + "</i>")
        manage_label.set_xalign(0)
        manage_item.add(manage_label)
        manage_item.connect("activate", self.manage_radios)
        radios_menu.append(manage_item)

        menu.append(radios_item)

        # ---- TV ----
        tvs_menu = Gtk.Menu()
        tvs_item = Gtk.MenuItem(label=self.tr("TV"))
        tvs_item.set_submenu(tvs_menu)

        if self.tvs:
            for tv in self.tvs:
                item = Gtk.MenuItem(label=tv["name"])
                item.connect(
                    "activate",
                    lambda w, u=tv["url"], n=tv["name"]: self.play_tv(u, n),
                )
                tvs_menu.append(item)
            tvs_menu.append(Gtk.SeparatorMenuItem())

        manage_tv_item = Gtk.MenuItem()
        manage_tv_label = Gtk.Label()
        manage_tv_label.set_markup("<i>" + GLib.markup_escape_text(self.tr("Gérer la TV…")) + "</i>")
        manage_tv_label.set_xalign(0)
        manage_tv_item.add(manage_tv_label)
        manage_tv_item.connect("activate", self.manage_tvs)
        tvs_menu.append(manage_tv_item)

        menu.append(tvs_item)

        # ---- Bouquet vidéos (playlists M3U) ----
        bouquets_menu = Gtk.Menu()
        bouquets_item = Gtk.MenuItem(label=self.tr("Bouquet vidéos"))
        bouquets_item.set_submenu(bouquets_menu)

        if self.bouquets:
            for bouquet in self.bouquets:
                item = Gtk.MenuItem(label=bouquet["name"])
                item.connect(
                    "activate",
                    lambda w, u=bouquet["url"]: self.load_bouquet(u),
                )
                bouquets_menu.append(item)
            bouquets_menu.append(Gtk.SeparatorMenuItem())

        manage_bouquet_item = Gtk.MenuItem()
        manage_bouquet_label = Gtk.Label()
        manage_bouquet_label.set_markup(
            "<i>"
            + GLib.markup_escape_text(self.tr("Gérer les bouquets…"))
            + "</i>"
        )
        manage_bouquet_label.set_xalign(0)
        manage_bouquet_item.add(manage_bouquet_label)
        manage_bouquet_item.connect("activate", self.manage_bouquets)
        bouquets_menu.append(manage_bouquet_item)

        menu.append(bouquets_item)

        # Séparateur après « Bouquet vidéos »
        menu.append(Gtk.SeparatorMenuItem())

        # Flux / playlists (URL + import/export M3U/PLS)
        flux_menu = Gtk.Menu()
        flux_root = Gtk.MenuItem(label=self.tr("Flux et playlists"))
        flux_root.set_submenu(flux_menu)

        url_item = Gtk.MenuItem(label=self.tr("Ouvrir une URL de flux…"))
        url_item.connect("activate", self.open_stream_url)
        flux_menu.append(url_item)

        import_pl = Gtk.MenuItem(label=self.tr("Importer une playlist…"))
        import_pl.connect("activate", self.import_playlist_file)
        flux_menu.append(import_pl)

        export_pl = Gtk.MenuItem(label=self.tr("Exporter la playlist…"))
        export_pl.connect("activate", self.export_playlist_file)
        flux_menu.append(export_pl)

        menu.append(flux_root)

        cd_item = Gtk.MenuItem(label=self.tr("Lire le CD audio…"))
        cd_item.connect("activate", self.add_cd)
        menu.append(cd_item)

        dvd_item = Gtk.MenuItem(label=self.tr("Lire le DVD…"))
        dvd_item.connect("activate", self.add_dvd)
        menu.append(dvd_item)

        bd_item = Gtk.MenuItem(label=self.tr("Lire le Blu-Ray…"))
        bd_item.connect("activate", self.add_bluray)
        menu.append(bd_item)

        menu.append(Gtk.SeparatorMenuItem())

        mini_menu = Gtk.Menu()
        mini_root = Gtk.MenuItem(label=self.tr("Mini-lecteur"))
        mini_root.set_submenu(mini_menu)

        mini_item = Gtk.CheckMenuItem(label=self.tr("Activer le mini-lecteur"))
        mini_item.set_active(bool(getattr(self, "mini_player_mode", False)))

        def _on_mini_toggled(mi):
            want = mi.get_active()
            if want != bool(getattr(self, "mini_player_mode", False)):
                self.toggle_mini_player()

        mini_item.connect("toggled", _on_mini_toggled)
        mini_menu.append(mini_item)

        mini_banner_item = Gtk.CheckMenuItem(label=self.tr("Bannière défilante"))
        mini_banner_item.set_active(bool(getattr(self, "mini_banner_enabled", True)))

        def _on_mini_banner_toggled(mi):
            want = mi.get_active()
            if want != bool(getattr(self, "mini_banner_enabled", True)):
                self.toggle_mini_banner()

        mini_banner_item.connect("toggled", _on_mini_banner_toggled)
        mini_menu.append(mini_banner_item)

        menu.append(mini_root)

        menu.append(Gtk.SeparatorMenuItem())

        eq_item = Gtk.MenuItem(label=self.tr("Égaliseur…"))
        eq_item.connect("activate", self.show_equalizer)
        menu.append(eq_item)

        pitch_item = Gtk.MenuItem(label=self.tr("Pitch / Tempo…"))
        pitch_item.connect("activate", self.show_pitch_tempo)
        self.pitch_menu_item = pitch_item
        menu.append(pitch_item)
        try:
            self._update_pitch_menu_sensitivity()
        except Exception:
            pass

        spectrum_item = Gtk.CheckMenuItem(label=self.tr("Spectre"))
        spectrum_item.set_active(self.spectrum_enabled)
        spectrum_item.connect("toggled", self.toggle_spectrum)
        menu.append(spectrum_item)

        # Pistes audio — choix de la piste / langue pour les vidéos multi-pistes.
        audio_tracks_menu = Gtk.Menu()
        audio_tracks_root = Gtk.MenuItem(label=self.tr("Pistes audio"))
        audio_tracks_root.set_submenu(audio_tracks_menu)

        if self.audio_tracks:
            audio_group = None
            for idx, label in self.audio_tracks:
                item = Gtk.RadioMenuItem.new_with_label_from_widget(audio_group, label)
                item.set_active(idx == self.current_audio_index)
                item.connect("toggled", lambda m, i=idx: self.set_audio_track(i) if m.get_active() else None)
                audio_tracks_menu.append(item)
                if audio_group is None:
                    audio_group = item
        else:
            empty_audio = Gtk.MenuItem(label=self.tr("Aucune piste disponible"))
            empty_audio.set_sensitive(False)
            audio_tracks_menu.append(empty_audio)

        menu.append(audio_tracks_root)

        # Chapitres (MKV / DVD)
        chapters_menu = Gtk.Menu()
        chapters_root = Gtk.MenuItem(label=self.tr("Chapitres"))
        chapters_root.set_submenu(chapters_menu)
        if self.chapters:
            for i, (start, title) in enumerate(self.chapters):
                label = f"{title}  ({format_time(start)})"
                item = Gtk.MenuItem(label=label)
                item.connect(
                    "activate",
                    lambda w, idx=i: self.seek_to_chapter(idx),
                )
                chapters_menu.append(item)
        else:
            empty_ch = Gtk.MenuItem(label=self.tr("Aucun chapitre"))
            empty_ch.set_sensitive(False)
            chapters_menu.append(empty_ch)
        menu.append(chapters_root)

        # Sous-titres (activation + sélection de langue/piste)
        subtitles_menu = Gtk.Menu()
        subtitles_root = Gtk.MenuItem(label=self.tr("Sous-titres"))
        subtitles_root.set_submenu(subtitles_menu)

        subtitles_item = Gtk.CheckMenuItem(label=self.tr("Activer les sous-titres"))
        subtitles_item.set_active(self.subtitles_enabled)
        subtitles_item.connect("toggled", self.toggle_subtitles)
        subtitles_menu.append(subtitles_item)

        if self.subtitle_tracks:
            subtitles_menu.append(Gtk.SeparatorMenuItem())
            # Option « Aucun »
            # set_active AVANT connect : évite un toggled parasite qui
            # relance le pipeline (freeze) à l'ouverture du menu.
            none_item = Gtk.RadioMenuItem.new_with_label_from_widget(
                None, self.tr("Aucun")
            )
            none_item.set_active(self.current_subtitle_index < 0)
            none_item.connect(
                "toggled",
                lambda m: self.set_subtitle_track(-1) if m.get_active() else None,
            )
            subtitles_menu.append(none_item)
            group = none_item
            for list_idx, track in enumerate(self.subtitle_tracks):
                label = track.get("label") if isinstance(track, dict) else str(track)
                item = Gtk.RadioMenuItem.new_with_label_from_widget(group, label)
                item.set_active(list_idx == self.current_subtitle_index)
                item.connect(
                    "toggled",
                    lambda m, i=list_idx: (
                        self.set_subtitle_track(i) if m.get_active() else None
                    ),
                )
                subtitles_menu.append(item)
        else:
            subtitles_menu.append(Gtk.SeparatorMenuItem())
            empty = Gtk.MenuItem(label=self.tr("Aucune piste disponible"))
            empty.set_sensitive(False)
            subtitles_menu.append(empty)

        menu.append(subtitles_root)

        # Paroles — entre Sous-titres et OSD (audio uniquement)
        lyrics_item = Gtk.CheckMenuItem(label=self.tr("Paroles"))
        lyrics_item.set_active(bool(getattr(self, "lyrics_enabled", False)))
        lyrics_item.connect("toggled", self.toggle_lyrics)
        menu.append(lyrics_item)

        color_check = Gtk.CheckMenuItem(label=self.tr("Colorimétrie"))
        color_check.set_active(bool(getattr(self, "colorimetry_enabled", False)))
        try:
            color_check.set_sensitive(self._colorimetry_allowed())
        except Exception:
            color_check.set_sensitive(False)
        try:
            color_check.set_tooltip_text(
                self.tr(
                    "En mode Logiciel (surtout en 4K), la colorimétrie est "
                    "coûteuse : décodage CPU + filtres vidéo, et le pipeline "
                    "peut être reconstruit. Préférez le mode Matériel."
                )
            )
        except Exception:
            pass
        color_check.connect("toggled", self.toggle_colorimetry_enabled)
        self.colorimetry_menu_item = color_check
        menu.append(color_check)

        color_adj = Gtk.MenuItem(label=self.tr("Ajuster la colorimétrie…"))
        try:
            color_adj.set_sensitive(
                self._colorimetry_allowed()
                and bool(getattr(self, "colorimetry_enabled", False))
            )
        except Exception:
            color_adj.set_sensitive(False)
        try:
            color_adj.set_tooltip_text(
                self.tr(
                    "En mode Logiciel (surtout en 4K), la colorimétrie est "
                    "coûteuse : décodage CPU + filtres vidéo, et le pipeline "
                    "peut être reconstruit. Préférez le mode Matériel."
                )
            )
        except Exception:
            pass
        color_adj.connect("activate", self.show_colorimetry)
        self.colorimetry_adjust_item = color_adj
        menu.append(color_adj)

        menu.append(Gtk.SeparatorMenuItem())

        # ---- Associer les formats ----
        assoc_menu = Gtk.Menu()
        assoc_item = Gtk.MenuItem(label=self.tr("Associer les formats"))
        assoc_item.set_submenu(assoc_menu)

        self._building_assoc_menu = True
        all_item = Gtk.MenuItem(label=self.tr("Tout sélectionner"))
        all_item.connect("activate", self.associate_all_formats)
        assoc_menu.append(all_item)
        assoc_menu.append(Gtk.SeparatorMenuItem())
        for ext, _mimes in ASSOCIABLE_FORMATS:
            label = ext.lstrip(".").upper()
            check = Gtk.CheckMenuItem(label=label)
            check.set_active(self.is_format_associated(ext))
            check.connect("toggled", self.toggle_format_association, ext)
            assoc_menu.append(check)
        self._building_assoc_menu = False

        menu.append(assoc_item)

        # ---- Performances (décodage Logiciel / Matériel) ----
        perf_menu = Gtk.Menu()
        perf_item = Gtk.MenuItem(label=self.tr("Performances"))
        perf_item.set_submenu(perf_menu)

        perf_group = None
        for mode, label_key in (
            ("software", "Logiciel"),
            ("hardware", "Matériel"),
        ):
            item = Gtk.RadioMenuItem.new_with_label_from_widget(
                perf_group, self.tr(label_key)
            )
            item.set_active(
                mode == getattr(self, "performance_mode", "hardware")
            )
            if perf_group is None:
                perf_group = item
            item.connect("toggled", self._performance_menu_toggled, mode)
            perf_menu.append(item)

        menu.append(perf_item)

        menu.append(Gtk.SeparatorMenuItem())

        language_menu = Gtk.Menu()
        language_item = Gtk.MenuItem(label="Language" if self.language == "English" else self.tr("Langue"))
        language_item.set_submenu(language_menu)

        language_group = None
        for language in LANGUAGES:
            item = Gtk.RadioMenuItem.new_with_label_from_widget(language_group, language)
            item.set_active(language == self.language)
            if language_group is None:
                language_group = item
            item.connect("toggled", self._language_menu_toggled, language)
            language_menu.append(item)

        menu.append(language_item)

        # ---- Thèmes ----
        themes_menu = Gtk.Menu()
        themes_item = Gtk.MenuItem(label=self.tr("Thèmes"))
        themes_item.set_submenu(themes_menu)
        theme_group = None
        theme_labels = (
            ("aqua", "Aqua"),
            ("sombre", "Sombre"),
            ("clair", "Clair"),
            ("sang", "Sang"),
            ("bigtron", "BigTron"),
            ("nature", "Nature"),
            ("kawai", "Kawai"),
            ("ciel", "Ciel"),
        )
        for tid, tlabel in theme_labels:
            item = Gtk.RadioMenuItem.new_with_label_from_widget(
                theme_group, self.tr(tlabel)
            )
            item.set_active(tid == getattr(self, "theme_name", "sombre"))
            if theme_group is None:
                theme_group = item
            item.connect(
                "toggled",
                lambda m, name=tid: self.set_theme(name) if m.get_active() else None,
            )
            themes_menu.append(item)
        menu.append(themes_item)

        shortcuts_item = Gtk.MenuItem(label=self.tr("Raccourcis clavier"))
        shortcuts_item.connect("activate", self.show_keyboard_shortcuts)
        menu.append(shortcuts_item)

        recent_menu = Gtk.Menu()
        recent_root = Gtk.MenuItem(label=self.tr("Historique récent"))
        recent_root.set_submenu(recent_menu)
        recents = getattr(self, "recent_files", None) or []
        if recents:
            for rpath in recents:
                try:
                    label = os.path.basename(rpath) or rpath
                    if len(label) > 48:
                        label = label[:45] + "…"
                    if (
                        not is_stream(rpath)
                        and not is_cdda(rpath)
                        and not os.path.isfile(rpath)
                    ):
                        label = "⚠ " + label
                    item = Gtk.MenuItem(label=label)
                    item.set_tooltip_text(rpath)
                    item.connect(
                        "activate",
                        lambda w, p=rpath: self._play_recent_file(p),
                    )
                    recent_menu.append(item)
                except Exception:
                    pass
            recent_menu.append(Gtk.SeparatorMenuItem())
            clear_r = Gtk.MenuItem()
            clear_label = Gtk.Label()
            clear_label.set_markup(
                "<i>" + GLib.markup_escape_text(self.tr("Vider l'historique")) + "</i>"
            )
            clear_label.set_xalign(0)
            clear_r.add(clear_label)
            clear_r.connect("activate", self.clear_recent_files)
            recent_menu.append(clear_r)
        else:
            empty_r = Gtk.MenuItem(label=self.tr("Historique vide"))
            empty_r.set_sensitive(False)
            recent_menu.append(empty_r)
        menu.append(recent_root)

        # Séparateur
        menu.append(Gtk.SeparatorMenuItem())

        about_item = Gtk.MenuItem()
        about_label = Gtk.Label()
        about_label.set_markup("<i>" + GLib.markup_escape_text(self.tr("À propos")) + "</i>")
        about_label.set_xalign(0)
        about_item.add(about_label)
        about_item.connect("activate", self.show_about)
        menu.append(about_item)

        # Séparateur après « À propos »
        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label=self.tr("Quitter"))
        quit_item.connect("activate", lambda item: self.close())
        menu.append(quit_item)

        def _on_deact(m):
            try:
                self._on_menu_deactivate(m)
            except Exception:
                pass
            if getattr(self, "_main_popup_menu", None) is m:
                self._main_popup_menu = None
            # Détruit le menu pour libérer widgets / handlers
            try:
                GLib.idle_add(m.destroy)
            except Exception:
                try:
                    m.destroy()
                except Exception:
                    pass
            return False

        menu.connect("deactivate", _on_deact)

        menu.show_all()
        menu.popup_at_widget(
            button,
            Gdk.Gravity.SOUTH_WEST,
            Gdk.Gravity.NORTH_WEST,
            None
        )

    def _on_menu_deactivate(self, menu):
        """Après fermeture du menu : redessine l'overlay pour effacer le fantôme."""
        if not self.current_is_video:
            return
        self._nudge_video_redraw()
        try:
            if self.video_area is not None and self.is_paused:
                self.video_area.queue_draw()
        except Exception:
            pass

    def _language_menu_toggled(self, menuitem, language):

        if menuitem.get_active() and language != self.language:
            self.set_language(language)

    def _performance_menu_toggled(self, menuitem, mode):

        if menuitem.get_active():
            self.set_performance_mode(mode)

    def toggle_spectrum(self, menuitem):

        self.spectrum_enabled = menuitem.get_active()
        # En vidéo le FFT reste suspendu (économie 4K) ; réactivé en audio.
        self._set_spectrum_active_for_media(self.current_is_video)

        if self.spectrum_enabled:
            self.status_label.set_text(self.tr("Spectre activé"))
        else:
            self.reset_spectrum()
            self.visualizer.queue_draw()
            self.status_label.set_text(self.tr("Spectre désactivé"))


    def toggle_subtitles(self, menuitem):
        self.set_subtitles_enabled(menuitem.get_active())
        if self.subtitles_enabled:
            self.status_label.set_text(self.tr("Sous-titres activés"))
            if self._custom_pipeline is None:
                # playbin choisit sa propre piste : on relit la liste pour que
                # le menu reflète la piste réellement affichée
                GLib.timeout_add(300, self._deferred_refresh_subtitles)
        else:
            self.current_subtitle_index = -1
            self.status_label.set_text(self.tr("Sous-titres désactivés"))

    def _translate_about_dialog_buttons(self, dialog):
        """Traduit les boutons natifs de Gtk.AboutDialog (Crédits / Licence / Fermer)
        dans la langue de l'application (et non celle du système)."""

        def _norm(text):
            return (text or "").replace("_", "").strip().casefold()

        def _native(msgid):
            # Libellé tel que GTK l'a affiché (langue du SYSTÈME, domaine gtk30)
            try:
                return _norm(GLib.dgettext("gtk30", msgid))
            except Exception:
                return ""

        known = {
            "Crédits": {"credits", "crédits", _native("C_redits")},
            "Licence": {"license", "licence", _native("_License")},
            "Fermer":  {"close", "fermer", _native("_Close")},
        }
        for names in known.values():
            names.discard("")

        buttons = []

        def _collect(widget):
            if isinstance(widget, Gtk.Button):
                buttons.append(widget)
            elif isinstance(widget, Gtk.Container):
                for child in widget.get_children():
                    _collect(child)

        _collect(dialog)

        try:
            close_widget = dialog.get_widget_for_response(
                Gtk.ResponseType.DELETE_EVENT
            )
        except Exception:
            close_widget = None

        for btn in buttons:
            try:
                key = "Fermer" if btn is close_widget else next(
                    (k for k, names in known.items()
                     if _norm(btn.get_label()) in names),
                    None,
                )
                if key:
                    btn.set_label(self.tr(key))
            except Exception:
                pass

    def _hide_about_license_button(self, dialog):
        """Masque le bouton « Licence » natif de Gtk.AboutDialog.

        À appeler APRÈS show_all() et AVANT _translate_about_dialog_buttons()
        (qui renomme les boutons et empêcherait de le reconnaître).
        """

        def _norm(text):
            return (text or "").replace("_", "").strip().casefold()

        names = {"license", "licence"}
        for msgid in ("_License", "_Licence"):
            try:
                names.add(_norm(GLib.dgettext("gtk30", msgid)))
            except Exception:
                pass
        names.discard("")

        found = []

        def _walk(widget):
            if isinstance(widget, Gtk.Button):
                if _norm(widget.get_label()) in names:
                    found.append(widget)
                return
            if isinstance(widget, Gtk.Container):
                for child in widget.get_children():
                    _walk(child)

        _walk(dialog)

        # Repli : si le libellé n'a pas été reconnu (locale exotique),
        # prendre le dernier bouton « secondaire » de la barre d'actions
        # (Crédits puis Licence sont les deux boutons secondaires).
        if not found:
            try:
                action = dialog.get_action_area()
                secondary = [
                    c for c in action.get_children()
                    if isinstance(c, Gtk.Button)
                    and action.get_child_secondary(c)
                ]
                if len(secondary) >= 2:
                    found.append(secondary[-1])
            except Exception:
                pass

        for btn in found:
            btn.set_no_show_all(True)
            btn.hide()

    def show_about(self, *args):
        dialog = Gtk.AboutDialog(parent=self)
        dialog.get_style_context().add_class("about-black")
        try:
            logo_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "logo.png"
            )
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(logo_path, 92, 92)
            dialog.set_logo(pixbuf)
        except Exception:
            pass

        dialog.set_program_name(APP_NAME)
        dialog.set_version("1.0")
        dialog.set_comments(
            self.tr("Un lecteur audio complet en GTK3 et GStreamer")
        )
        try:
            dialog.set_copyright("© 2026 O. Fraisse (MilOvni)")
        except Exception:
            pass

        try:
            dialog.add_credit_section(
                self.tr("Créé par"),
                ["O. Fraisse (MilOvni)"],
            )
            dialog.add_credit_section(
                self.tr("Remerciements"),
                [
                    "Dr. Mary D. — "
                    + self.tr("pour avoir restauré mon audition")
                ],
            )
            dialog.add_credit_section(
                self.tr("Inspirations"),
                [
                    "AIMP",
                    "Winamp",
                    "Audacious",
                    "DeaDBeeF",
                    "VLC",
                    "mpv",
                    "SMPlayer",
                ],
            )
            dialog.add_credit_section(
                self.tr("Bibliothèques"),
                [
                    "Python 3 (PSF License)",
                    "GTK 3 / PyGObject (GNU LGPL)",
                    "GStreamer (GNU LGPL)",
                    "GdkPixbuf (GNU LGPL)",
                    "Cairo (GNU LGPL / MPL)",
                    "Pango (GNU LGPL)",
                    "Mutagen (GNU GPL v2)",
                    "libnotify (GNU LGPL)",
                ],
            )
        except Exception:
            pass

        dialog.show_all()
        self._hide_about_license_button(dialog)
        self._translate_about_dialog_buttons(dialog)

        # Lien Patreon « Soutenir… »
        try:
            action = dialog.get_action_area()
            support = Gtk.Label()
            support.set_markup(
                '<a href="%s"><b>%s</b></a>'
                % (
                    GLib.markup_escape_text(PATREON_URL),
                    GLib.markup_escape_text(self.tr("Soutenir…")),
                )
            )
            support.set_use_markup(True)
            support.set_margin_start(6)
            support.set_margin_end(6)
            action.pack_start(support, False, False, 0)
            try:
                children = action.get_children()
                action.reorder_child(support, max(0, len(children) - 2))
            except Exception:
                pass
            support.show()
        except Exception:
            pass

        # Commentaire + blague + lien GPL
        def _find_labels(widget, acc):
            if isinstance(widget, Gtk.Label):
                acc.append(widget)
            if hasattr(widget, "get_children"):
                for child in widget.get_children():
                    _find_labels(child, acc)

        labels = []
        _find_labels(dialog.get_content_area(), labels)
        target = self.tr("Un lecteur audio complet en GTK3 et GStreamer")
        gpl_url = "https://www.gnu.org/licenses/gpl-3.0.html"
        for lab in labels:
            try:
                if lab.get_text().strip().startswith(target.strip()):
                    lab.set_markup(
                        GLib.markup_escape_text(target)
                        + "\n\n<i>"
                        + GLib.markup_escape_text(
                            self.tr("... mais un peu lecteur vidéo aussi.")
                        )
                        + "</i>\n\n\n"
                        + GLib.markup_escape_text(
                            self.tr("Logiciel libre sous")
                        )
                        + ' <a href="%s">GNU GPL v3</a>.'
                        % GLib.markup_escape_text(gpl_url)
                    )
                    break
            except Exception:
                pass

        dialog.set_title(self.tr("À propos de") + " " + APP_NAME)

        dialog.run()
        dialog.destroy()


    # ========================================================
    # FERMETURE
    # ========================================================

    def on_destroy(self, *args):
        try:
            self._inhibit_screensaver(False)
        except Exception:
            pass

        self._app_closing = True

        # Arrête les timers GLib (évite callbacks après destruction)
        try:
            self._stop_mini_title_scroll()
        except Exception:
            pass

        for attr in (
            "_position_timeout_id",
            "_visualizer_timeout_id",
            "_size_allocate_timer",
            "_seek_release_timeout_id",
            "_osd_timeout_id",
            "_cursor_hide_timeout_id",
                        "_fs_toast_tl_timeout",
            "_fs_toast_tr_timeout",
            "_mini_title_scroll_id",
        ):
            tid = getattr(self, attr, 0) or 0
            if tid:
                try:
                    GLib.source_remove(tid)
                except Exception:
                    pass
                try:
                    setattr(self, attr, 0)
                except Exception:
                    pass

        # Menus popup
        for attr in ("_main_popup_menu", "_playlist_context_menu"):
            m = getattr(self, attr, None)
            if m is not None:
                try:
                    m.destroy()
                except Exception:
                    pass
                try:
                    setattr(self, attr, None)
                except Exception:
                    pass

        # Fenêtres auxiliaires
        for attr in ("pitch_window", "eq_window", "colorimetry_window"):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    w.destroy()
                except Exception:
                    pass

        try:
            self.save_playlist()
            self.save_state()
            self.save_radios()
            self.save_tvs()
            self.save_bouquets()
            self.save_associations()
        except Exception as e:
            print("save on destroy:", e, file=sys.stderr)

        # Libère le nom MPRIS2
        try:
            if getattr(self, "_mpris_owner_id", 0):
                Gio.bus_unown_name(self._mpris_owner_id)
                self._mpris_owner_id = 0
        except Exception:
            pass

        try:
            self._destroy_custom_pipeline()
        except Exception:
            pass
        try:
            if getattr(self, "bus", None) is not None:
                try:
                    self.bus.remove_signal_watch()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self.player.set_state(Gst.State.NULL)
            self.player.get_state(200 * Gst.MSECOND)
        except Exception:
            pass
        # Libère le sink GL pour éviter une fenêtre « OpenGL renderer » orpheline
        try:
            self.player.set_property("video-sink", None)
        except Exception:
            pass
        try:
            if self.video_sink is not None:
                self.video_sink.set_state(Gst.State.NULL)
                self.video_sink = None
        except Exception:
            pass

        # Libère références lourdes
        try:
            self._pause_pixbuf = None
            self.reset_spectrum()
        except Exception:
            pass

        # Ne nettoyer le socket / le verrou QUE si nous sommes l'instance primaire.
        # Une invocation secondaire ne doit JAMAIS supprimer le socket de la primaire.
        owns_instance = getattr(self, "_instance_lock_fd", None) is not None

        try:
            sock = getattr(self, "instance_socket", None)
            if sock is not None:
                self.instance_socket = None
                try:
                    sock.close()
                except Exception:
                    pass
        except Exception:
            pass

        if owns_instance:
            try:
                if os.path.exists(SOCKET_FILE):
                    os.unlink(SOCKET_FILE)
            except Exception:
                pass

            lock_fd = self._instance_lock_fd
            self._instance_lock_fd = None
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                lock_fd.close()
            except Exception:
                pass

        Gtk.main_quit()


# ============================================================
# MAIN — instance unique
# ============================================================

def parse_cli_media_files(argv):
    """Extrait les chemins médias depuis les arguments de la ligne de commande.

    Accepte chemins locaux, URIs file://, et conserve l'ordre fourni par
    le gestionnaire de fichiers. Déduplique sans changer l'ordre.
    """
    files = []
    seen = set()

    for argument in argv:
        if not argument or argument.startswith("-"):
            continue

        path = argument

        if path.startswith("file://"):
            try:
                path = GLib.filename_from_uri(path)[0]
            except Exception:
                continue
        elif path.startswith(STREAM_SCHEMES):
            if path not in seen:
                seen.add(path)
                files.append(path)
            continue

        path = os.path.abspath(os.path.expanduser(path))

        if not os.path.isfile(path):
            continue

        if not (is_audio_file(path) or is_video_file(path)):
            continue

        if path in seen:
            continue

        seen.add(path)
        files.append(path)

    return files


def forward_to_existing_instance(files, attempts=1, delay=0.05):
    """Transmet les fichiers à l'instance déjà en cours via le socket Unix.

    Retourne True si la transmission a réussi (l'appelant doit alors quitter
    sans créer de fenêtre).
    """
    if files:
        payload = ("\n".join(files) + "\n").encode("utf-8")
    else:
        # Activation sans fichier : réveille l'instance existante
        payload = b"\n"

    for attempt in range(max(1, attempts)):
        client = None
        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(1.5)
            client.connect(SOCKET_FILE)
            client.sendall(payload)
            try:
                client.shutdown(socket.SHUT_WR)
            except Exception:
                pass
            # Lecture courte éventuelle (ignore) pour laisser le serveur drain
            try:
                client.settimeout(0.2)
                client.recv(16)
            except Exception:
                pass
            client.close()
            return True
        except Exception:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            if attempt + 1 < attempts:
                time.sleep(delay)

    return False


def acquire_instance_lock():
    """Tente d'obtenir le verrou exclusif d'instance unique.

    Retourne le descripteur de fichier ouvert (à conserver ouvert pour
    maintenir le verrou), ou None si une autre instance le détient déjà.
    """
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
    except Exception as exc:
        print("Impossible de créer le répertoire de config :", exc, file=sys.stderr)

    try:
        fd = open(LOCK_FILE, "a+", encoding="utf-8")
    except Exception as exc:
        print("Impossible d'ouvrir le verrou d'instance :", exc, file=sys.stderr)
        return None

    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError) as exc:
        # Verrou déjà détenu → une instance tourne
        if getattr(exc, "errno", None) not in (
            errno.EACCES,
            errno.EAGAIN,
            errno.EWOULDBLOCK,
            None,
        ) and not isinstance(exc, BlockingIOError):
            print("fcntl.flock :", exc, file=sys.stderr)
        try:
            fd.close()
        except Exception:
            pass
        return None

    try:
        fd.seek(0)
        fd.truncate()
        fd.write(str(os.getpid()))
        fd.flush()
    except Exception:
        pass

    return fd


def main():
    """Point d'entrée : une seule instance GUI, fichiers transmis via socket."""

    files = parse_cli_media_files(sys.argv[1:])

    # 1) Une instance écoute déjà ? Lui transmettre les fichiers et quitter.
    if forward_to_existing_instance(files, attempts=4, delay=0.05):
        return

    # 2) Acquérir le verrou exclusif AVANT de créer toute fenêtre GTK.
    lock_fd = acquire_instance_lock()
    if lock_fd is None:
        # Une autre instance détient le verrou : réessayer le transfert
        # (elle est peut-être encore en train de binder le socket).
        if forward_to_existing_instance(files, attempts=60, delay=0.05):
            return
        # Impossible de contacter l'instance existante : ne PAS ouvrir
        # une deuxième fenêtre (respect strict de l'instance unique).
        print(
            "Myostoox : une instance semble déjà active mais ne répond pas. "
            "Abandon pour éviter un doublon.",
            file=sys.stderr,
        )
        return

    # 3) Nous sommes l'instance primaire.
    try:
        GLib.set_prgname("myostoox")
    except Exception:
        pass
    try:
        Gst.init(None)
        apply_safe_gstreamer_ranks()
    except Exception:
        pass

    try:
        logo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
        if os.path.isfile(logo):
            Gtk.Window.set_default_icon_from_file(logo)
    except Exception:
        pass

    window = Myostoox()
    window._instance_lock_fd = lock_fd

    if not window.start_single_instance_server():
        # Cas très rare : bind impossible malgré le verrou.
        # On continue quand même (lecture locale), le verrou empêche
        # d'autres instances de s'ouvrir.
        print(
            "Myostoox : socket d'instance indisponible "
            "(les ouvertures externes pourraient échouer).",
            file=sys.stderr,
        )

    window.show_all()

    if files:
        # Ouverture explicite depuis le gestionnaire de fichiers / CLI :
        # annuler la restauration différée de last_path (sinon l'ancienne
        # vidéo se relance en idle après play_file du nouveau média).
        try:
            window.cancel_pending_session_restore()
        except Exception:
            pass
        for filename in files:
            window.add_file(filename, save=False)
        window.save_playlist()
        # Un seul fichier → lecture auto ; plusieurs → playlist seulement
        if len(files) == 1:
            window.play_file(files[0])

    Gtk.main()


if __name__ == "__main__":

    try:
        main()
    except Exception as e:
        print("Erreur :", e, file=sys.stderr)
        raise
