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
import gc
from collections import OrderedDict, deque
from functools import lru_cache
try:
    import ctypes
    _libc = ctypes.CDLL("libc.so.6")
    try: _libc.mallopt(-8, 2)
    except Exception: pass
except Exception:
    _libc = None

def trim_memory(force=False):
    now = time.monotonic()
    last = getattr(trim_memory, "_last", 0.0)
    if not force and (now - last) < 30.0: return
    trim_memory._last = now
    try: gc.collect()
    except Exception: pass
    try:
        if _libc is not None:
            _libc.malloc_trim(0)
    except Exception: pass
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GstVideo", "1.0")
try: gi.require_version("GstPbutils", "1.0")
except Exception: pass
try: gi.require_version("PangoCairo", "1.0")
except Exception: pass
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
METADATA_CACHE_MAX = 1500
COVER_DECODE_MAX_PX = 256
COVER_PIXBUF_CACHE_MAX = 24
EXTERNAL_COVER_MAX_BYTES = 8 * 1024 * 1024
VISUALIZER_INTERVAL_MS = 16
FS_BAR_ALPHA = 0.55
PLAYBACK_RATES = (0.50, 0.75, 1.0, 1.25, 1.5, 1.75)
_AV1_HW_OTHER_NAMES = (
    "vaav1dec",
    "vaapiav1dec",
    "v4l2av1dec",
    "msdkav1dec",
)
_AV1_HW_NVIDIA_NAMES = (
    "nvav1dec",
)
_AV1_HW_DECODER_NAMES = _AV1_HW_OTHER_NAMES + _AV1_HW_NVIDIA_NAMES
_AV1_SOFT_DECODER_ORDER = ("dav1ddec", "av1dec", "avdec_av1")
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
_OTHER_HW_DECODER_NAMES = (
    "vah264dec",
    "vah265dec",
    "vaav1dec",
    "vavp8dec",
    "vavp9dec",
    "vampeg2dec",
    "vaapidecodebin",
    "vaapih264dec",
    "vaapih265dec",
    "vaapiav1dec",
    "vaapivp8dec",
    "vaapivp9dec",
    "vulkanh264dec",
    "vulkanh265dec",
    "qsvh264dec",
    "qsvh265dec",
    "qsvvp9dec",
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
    try:
        factory = Gst.ElementFactory.find(name)
        if factory is not None:
            factory.set_rank(rank)
    except Exception: pass

def apply_safe_gstreamer_ranks(prefer_nvidia_av1=False):
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
try:
    gi.require_version("Notify", "0.7")
    from gi.repository import Notify
    if not Notify.is_initted():
        Notify.init(APP_NAME)
except Exception:
    Notify = None
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", APP_NAME)
SOCKET_FILE = os.path.join(CONFIG_DIR, "instance.sock")
LOCK_FILE = os.path.join(CONFIG_DIR, "instance.lock")
PLAYLIST_FILE = os.path.join(
    CONFIG_DIR,
    "playlist.json"
)
RECENT_FILE = os.path.join(CONFIG_DIR, "recent.json")
RECENT_MAX = 15
RESUME_FILE = os.path.join(CONFIG_DIR, "resume.json")
RESUME_MAX = 200
RESUME_MIN_DURATION = 120.0
RESUME_MIN_POS = 30.0
RESUME_END_MARGIN = 45.0
RESUME_REWIND = 3.0
RESUME_SAVE_INTERVAL = 15.0
META_CACHE_FILE = os.path.join(CONFIG_DIR, "meta_cache.json")
META_DISK_MAX = 5000
PATREON_URL = "https://www.patreon.com/MilOvni"
STATE_FILE = os.path.join(CONFIG_DIR, "state.json")
RADIOS_FILE = os.path.join(CONFIG_DIR, "radios.json")
TVS_FILE = os.path.join(CONFIG_DIR, "tvs.json")
BOUQUETS_FILE = os.path.join(CONFIG_DIR, "bouquets.json")
EQ_PRESETS_FILE = os.path.join(CONFIG_DIR, "eq_presets.json")
_DEFAULT_EQ_PRESETS_Z = bytes.fromhex(
    "78da8d933d6f14410c86ffca74dbec8dfc39b6291389820ad1462956e884222e3960434110ff9d993dee7203bb122e465adb63fb79c77bf773789a1e"
    "f7c39b34dc7efb313f4f87614cc3a7e9e169aebe3bcc14ea6ae18e458b798c0932fc397698192d30c0144514025b04d98d9c894b84108a8e09b386b2"
    "a911a9302b8d89b3bb7030145667c79a45b9f750bbd83570a91d76b58510203a0b6328a9fafdaf31bda2bc3d4ccf1dc7ebccddc11626c5594518d4ac"
    "ac252d475fff669ae774733cce7d17c9e20848459dc85c6c4c96adb368e0bdcbcbd62c15b474d6aeef285367284b26972a5248703170b7934e801e6e"
    "2c6846554f773fb9af320b78ebdfb5971ef7f650791fbe7edfffa5a90709687d2e0a448ed2d854c9b05676871ac2fdae3d6c6de99d056f6ccf4601c8"
    "b48c7a9e1b71598dcec0161f01296311d5d2266a0bd4d8cf4228aef97ade77d3cb4b87ba3e3fe44e462bd24ac3458f2a8e14da7cdc9adabe2e215a4a"
    "d225ad054aac97a45af2ccd88017d0ab165cc53b5dbdbaa9a03de787e3c7cfe9fdf14bc74a6de36985974ebfc2bf8fbeb9bc57ab57a56e2c2b73732b"
    "fb9fc3dfff0626ec0224"
)
DEFAULT_EQ_PRESETS = eval(__import__('zlib').decompress(_DEFAULT_EQ_PRESETS_Z).decode())

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
_ASSOCIABLE_FORMATS_Z = bytes.fromhex(
    "78da7592c10ec2200c865fc51b9a4c3de8db2c1eba8d1132718b38f4f1a5a31dc5c5db3efe8fd2c2eabd3ab9e9a2aa5dad60eeec787693361157baa8"
    "dba1da45adbf432b3c42a2cf7161524763849988208ccfc6fa2cc28fc8c1347b9924dc7a6f08424bb4b654607874686bde09c534500e0379167785e2"
    "76aed2c3903b71d28b913fa625eecdad6a2a52ab603bfd531281ad21082b1684d773f4c35292764683a709b694fdf2955d14d6e3b529ced72647ff92"
    "b76e9c8808793624eea4957f131205dd4bbe283e475aa105042e61fbbeb84b5ae09a4859dd9adc7228a2f8f813b4434c6f5f53a7e327"
)
ASSOCIABLE_FORMATS = eval(__import__('zlib').decompress(_ASSOCIABLE_FORMATS_Z).decode())

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
    "quit": "Alt+q",
    "factory_reset": "Ctrl+Alt+x",
    "playlist_menu": "Ctrl+p",
    "focus_search": "Ctrl+r",
    "toggle_recording": "Ctrl+s",
    "toggle_playlist_only": "Ctrl+n",
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
    "quit": "Quitter",
    "factory_reset": "Quitter et réinitialiser",
    "playlist_menu": "Menu playlist",
    "focus_search": "Champ de recherche",
    "toggle_recording": "Enregistrement",
    "toggle_playlist_only": "Sans lecteur",
}
_THEME_PALETTES_Z = bytes.fromhex(
    "78da959adb72e3360c865f2533bd483ba3e9f028417d990ec44336d3246e1d6ff7a2d3772f654b040f506ac7570b8b14007e067f82fbcff3e7e97d39"
    "87e7df9efe79fef1fae14f3f7e5f5ed2bf9e7f92526a699f87a7dd1e6f760fdefbb8da2fa73f173ce7e767e964653f9d7d385fbfd3421b0deb77cbe5"
    "631fa0941a15eec66d76e7bdf6363f594c01da1bb97ff1edf4374d3dea65b7a3bbbcfe1df6376834de8eed7734a775939ec2d5e5d7f7b0bb8061599c"
    "58addf02a647b3bf56cdca15767a7e5aae1e5cce21f4e9bb5af7f026e75cccd6cff0967d154618acbed9c6c4ebdf358ad3e5727acf6f98244a57dab9"
    "84bb3553fb10318b45b8c25c8cb01af5968bcb5b76d8bba08259cdaf1ff1b45b619eed7c75f6f38297ef9fbb7d9c273b5dedefe1e37b4fccd5babb1f"
    "5600b2b55cd13d13a74fbf3d7d7e59f067315c3fbf4ef0cbfe6d9da27fd7c0def0f5cce01c4db471ea7156a882d63dce010286c0e3ec6282141b9c8b"
    "65229c69f60667671d7adde3ec63d001799c9df52688639c279c9745b4385b1cedc4e01c421451f738d3f325ce11e3125d8b7391bc06e7059c0b82c3"
    "39419b56bec79912dee34c09af704e68da802cce94df0a67199556d0e24c21d7384f0866162dce344789332d7f8333ad6883b311833183558968b05f"
    "10fd891f2f0cd00aa591d0031d63caa4eb81d65eba5bde7ba0274c05a3adcf34a0009a666feb73f534853f8654891c0f34a6971a3806da0b2bc60ee8"
    "289c70d003ad57b04c0f343d5f024de92b814ee1895bb96b81863545ee91faac9584a3fa4c09af8096410a6958a0694405747ab388aa053a08848501"
    "da09801e685ad312685ad006685ad10668a9c4a013d46b8d9ec517447f249f78cd115298c830add250c3698e71d1074c4b6351b5455a853958d9301d"
    "44744539a729103ca0608a34c6548a79a603a4749a63a6d33e146f0b5632ed4d149e615ae29476f29e697abed21c397d15d322bfb161da41888e2dd2"
    "6967f035bb6dc27ba629e135d3ad99468868702b490dd3db0faa649a42ae995e84770b744c67622acd91dd6f985606d0188e693b3c8d727852303c25"
    "aa554bf5b6dc2bd5f8d77764981641e2ad2cd54c27ea44048669ab4003cfb4428b535ba765d06845cff4367b5ba77144809e690506c703a655a270fe"
    "4278d8a42a7d57a7e7547503233c24ac0aaf679a9e2f99a6f4954cfb2416225ba7158e02c4433a5a29a105cf3425bcd6d1ebe6c1eb681a51339d56e3"
    "a69f2a1d0d0b78a64e8f386ff5bb649ad6b4623af3d2329d57b4657a1c0c0c9362cb74021ab632bdbcbe5ccea70f8e692f26c1303daa490253a76384"
    "b439f34c8beb5fcbf48a94edb4c78cac98aea7287fd5694b9a78a6c9259e69e952b64dcb34591ba69d940218ed4181974ce7f4d5dac3c579e6986e66"
    "29c574f6a766ba88ae639ab255339d9deaeb741e518be9fcf292e924c3a260ce86d66990a6d71e7bd035d3fbf2374c535c0dd3497a4831d881abd1db"
    "a015e937fcb884338bb45913c0b53ba20b1353a6fd325bf300d2c24a23da7687f6de8df800d2226d9852f148934b3cd2c2ec1e944893b5463ad53894"
    "4c992e022f91cee92b914658c5378774334b293db23f4d99a6e8ee463a3b752fd2f4f212e994f42019e991a48d2be444177489342d7f8334c5d5206d"
    "871bd41cd2dba06bc3e3fcfdf35be05a1e624ca709ee8468fcb617d548db058d9b1f411a257427c429a46d1c1e403aedbfa081479a5c3a403a7b5021"
    "9dad0dd261dfe99b9607055e229dd35722bdc0ba0d734837b39448539e2aa48be8ee463a3b7537d2f9e525d21277d158234df2a5449a82ae90ce3337"
    "48535c0dd2302833e891457a1bb422fde2f12d3240a763aaf10cd01a35dcd6b5911d36ca30f2400382eda4b4f71e1cf43d3c508201ba9e82c20f4b18"
    "fd410f8f5c3a90d26847032dd014600db407af1c733c2c1252f6f044287b725d78fdf1d00984831e1e48c1c80e8aae039ab255014d217440d3885a76"
    "e497d73dbc3d716d0f6fb2630734055df5f0b2fb0dd09489be87a761d0bc94dea65b89f678be7ce34a34ac1f4648afdd1e866867d6d3de23425ac942"
    "2fe4838411563c52a2d7be9438e84a67970e8434ee2156423a5b1b216df637d544178197253aa7af243a00b6dc2eec2c771c0e8be8ee2ed1d9a9bb85"
    "744e4649b4c1bdd4b7874359b2db055d09e9bcfc2dd139aeae893798f4493c9b96e76dccca733c873421d7c14b27e3db0d530df4ac8cb59e031ac5f8"
    "08d049fea26cbb1dde2c008f680e9d34878423a077978e80de43ac81deadcdad21aaada5db029d032f3b78347705740a902fd1f52c7589de666a81ce"
    "d1dd0b344d753fd0fb881a688d8a05daa26180de832e81a6e5ef80dee36a4f86e355472b56746ca3b66ec77b78c1838e47fa191939f65c9ba4a22cd3"
    "c5039cd5ec1e29d4742d465c2f6201f748a14ebb5ad0075d3c72e980eb1c62c575b636853a28a3b0e7ba08bce49ae62ebb78c28fdef1b72dd52c25d7"
    "79a69aeb22babbb9ce53ddcd751e51773c8c325cc703473386ee363c075d15eabcfc0dd714575ba861e55af15c6fa356aeffc01ff8ca5d88c7606e9a"
    "beede181758eebe1cdc11df4a563923aa8bb0bf1bd9f5b106d96058c6788aeafcfcb8ecf228eee5a621cc3f245c363d53973d79736711a1dd3c38bd1"
    "8bc0f4f0e8f94a4caf176f1dd1cdb305d1d314c4fc90f4a084f74453c22ba21d441b047f7f18fcbc883bef5aac98b72bd39ae8e692bb0bbabe100f10"
    "39a229136da54e3c8fa954ebffbd4074afe18d151f0667e6fa30396222f35f96144ed11ff4f06c582076e2230fa8dad225b9d55540f974213e10301c"
    "a86993963c7e71258e229dc463777d984e76376b7b253ea36790de9afcddf5a12d75739fbc0669f2b56f78a422c29c0f8b8c744853c2eb223d6a0fbc"
    "9aa61177208d10eccd5a238dd1bb88bdf8d883aec487b1e5b50cb7a27d5b7a25da8883c6f48de87fff03044a1d83"
)
import zlib as _zlib_themes
THEME_PALETTES = eval(_zlib_themes.decompress(_THEME_PALETTES_Z).decode("utf-8"))

THEME_LABELS = {
    "aqua": "Aqua",
    "sombre": "Sombre",
    "clair": "Clair",
    "sang": "Sang",
    "nature": "Nature",
    "kawai": "Kawai",
    "ciel": "Ciel",
    "bigtron": "BigTron",
    "lantern": "Lantern",
    "crusher": "Crusher",
    "gdalf": "Gdalf",
    "darth": "Darth",
    "fremen": "Fremen",
    "bigmegatron": "BigMegatron",
}
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
SPECTRUM_SYNC_OFFSET_MIN_MS = -500
SPECTRUM_SYNC_OFFSET_MAX_MS = 1500
SPECTRUM_FALLBACK_DELAY_S = 0.25   # si le message n'a pas de running-time
SPECTRUM_QUEUE_MAX = 400
SPECTRUM_MAX_LEAD_S = 5.0      # frame plus en avance → périmée (autre timeline)
SPECTRUM_BROKEN_AFTER = 40     # N frames périmées d'affilée → repli murale
SPECTRUM_DEBUG = bool(os.environ.get("MYOSTOOX_SPECTRUM_DEBUG", "").strip())
EQ_BANDS = 10
EQ_FREQS = [
    "29 Hz", "59 Hz", "119 Hz", "237 Hz", "474 Hz",
    "947 Hz", "1.9 kHz", "3.8 kHz", "7.5 kHz", "15 kHz"
]
EQ_MIN_DB = -24.0
EQ_MAX_DB = 12.0
REPLAYGAIN_MODES = ("off", "track", "album", "auto")
CROSSFADE_MAX_S = 12.0
SLEEP_PRESETS_MIN = (15, 30, 45, 60, 90)

def format_time(seconds):
    try:
        seconds = int(seconds)
    except Exception:
        seconds = 0
    seconds = max(0, seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours: return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"

def format_video_resolution(width, height):
    try:
        return "%d × %d" % (int(width), int(height))
    except Exception:
        return "—"

def format_video_fps(fps):
    try:
        fps = float(fps)
    except Exception:
        return None
    if fps <= 0: return None
    if abs(fps - round(fps)) < 0.005: return "%d fps" % int(round(fps))
    return ("%.3f" % fps).rstrip("0").rstrip(".") + " fps"

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

def find_external_subtitles(video_path):
    results = []
    if not isinstance(video_path, str) or is_stream(video_path) or is_cdda(video_path): return results
    try:
        if is_dvd(video_path) or is_bluray(video_path): return results
    except Exception: pass
    if not os.path.isfile(video_path): return results
    directory = os.path.dirname(os.path.abspath(video_path))
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    base_lower = base_name.casefold()

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
        if not os.path.isfile(path): continue
        root, ext = os.path.splitext(name)
        ext_lower = ext.lower()
        if ext_lower not in SUBTITLE_EXTENSIONS: continue
        all_subs.append((name, path, root, ext_lower))
    if not all_subs: return results
    matched_paths = set()

    def _add(path, lang, ext_lower, priority):
        if path in matched_paths: return
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
    if not results and len(all_subs) > 1:
        for name, path, root, ext_lower in all_subs:
            root_norm = _norm_token(root)
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
    lines = []
    for raw in content.splitlines():
        raw = raw.strip()
        if not raw or raw.startswith(("[ti:", "[ar:", "[al:", "[by:", "[offset:", "[length:")): continue
        tags = list(re.finditer(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]", raw))
        if not tags: continue
        last = tags[-1]
        text_part = raw[last.end():].strip()
        if not text_part: continue
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
    if not isinstance(filepath, str) or not is_pure_audio_file(filepath): return None
    if not os.path.isfile(filepath): return None
    directory = os.path.dirname(os.path.abspath(filepath))
    base = os.path.splitext(os.path.basename(filepath))[0]
    for ext in (".lrc", ".LRC"):
        lrc_path = os.path.join(directory, base + ext)
        if os.path.isfile(lrc_path):
            try:
                with open(lrc_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                timed = parse_lrc_content(content)
                if timed: return {"timed": True, "lines": timed, "source": "lrc"}
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
                if plain: return {"timed": False, "lines": plain, "source": "txt"}
            except Exception as exc:
                print("TXT lyrics:", exc, file=sys.stderr)
    if MutagenFile is None: return None
    try:
        audio = MutagenFile(filepath, easy=False)
        if audio is None: return None
        tags = getattr(audio, "tags", None)
        if tags is None: return None
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
                        if plain: return {
                                "timed": False,
                                "lines": plain,
                                "source": "embedded",
                            }
        except Exception: pass
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
                        if plain: return {
                                "timed": False,
                                "lines": plain,
                                "source": "embedded",
                            }
            except Exception: pass
    except Exception as exc:
        print("embedded lyrics:", exc, file=sys.stderr)
    return None

def get_cdda_track_number(path):
    if not is_cdda(path): return None
    try:
        return int(path.split("://", 1)[1].split("#")[0])
    except Exception:
        return None

def get_tag(tags, names, default=""):
    if not tags: return default
    for name in names:
        try:
            value = None
            if hasattr(tags, "getall"):
                try:
                    frames = tags.getall(name)
                    if frames:
                        value = frames[0]
                except Exception:
                    value = None
            if value is None:
                try:
                    if hasattr(tags, "get"):
                        value = tags.get(name)
                    if value is None and name in tags:
                        value = tags[name]
                except Exception:
                    value = None
            if value is None: continue
            if isinstance(value, (list, tuple)):
                if not value: continue
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
                        except Exception: pass
            if value is not None:
                s = str(value).strip()
                if s: return s
        except Exception: pass
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
            if name.lower() not in cover_names: continue
            path = os.path.join(directory, name)
            if not os.path.isfile(path): continue
            try:
                try:
                    sz = os.path.getsize(path)
                    if sz <= 0 or sz > EXTERNAL_COVER_MAX_BYTES: continue
                except Exception: pass
                with open(path, "rb") as file:
                    data = file.read(EXTERNAL_COVER_MAX_BYTES + 1)
                if data and len(data) <= EXTERNAL_COVER_MAX_BYTES: return data
            except Exception: pass
    except Exception: pass
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
                    try: technical["bitrate"] = f"{int(bitrate / 1000)} kb/s"
                    except Exception: pass
                bits = getattr(info, "bits_per_sample", None)
                if bits:
                    technical["bits"] = f"{bits} bits"
            def _easy_first(keys):
                for k in keys:
                    try:
                        val = easy.get(k)
                        if not val: continue
                        if isinstance(val, (list, tuple)):
                            val = val[0] if val else ""
                        s = str(val).strip()
                        if s: return s
                    except Exception:
                        continue
                return ""
            title = _easy_first(("title", "TITLE", "TIT2")) or title
            artist = _easy_first(("artist", "ARTIST", "TPE1")) or artist
            album = _easy_first(("album", "ALBUM", "TALB")) or album
            year = _easy_first(
                ("date", "DATE", "year", "YEAR", "TDRC", "TYER")
            ) or year
        # --- Passe 2 : tags bruts seulement si cover ou champs incomplets ---
        need_raw = bool(load_cover) or not (title and artist)
        if not need_raw:
            if year:
                try:
                    y = str(year).strip()
                    if len(y) >= 4 and y[:4].isdigit():
                        year = y[:4]
                except Exception: pass
            return (
                title,
                artist,
                album,
                year,
                duration,
                cover,
                technical,
            )
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
            try: duration = float(getattr(info, "length", 0) or 0)
            except Exception: pass
            if not technical.get("sample_rate"):
                rate = getattr(info, "sample_rate", None)
                if rate:
                    try:
                        technical["sample_rate"] = (
                            f"{int(rate):,} Hz".replace(",", " ")
                        )
                    except Exception: pass
            if not technical.get("channels"):
                channels = getattr(info, "channels", None)
                if channels:
                    technical["channels"] = str(channels)
            if not technical.get("bitrate"):
                bitrate = getattr(info, "bitrate", None)
                if bitrate:
                    try: technical["bitrate"] = f"{int(bitrate / 1000)} kb/s"
                    except Exception: pass
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
            except Exception: pass
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
                                if int(getattr(ap, "type", -1)) == 3:
                                    chosen = ap
                                    break
                            except Exception: pass
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
                            except Exception: pass
                except Exception: pass
            if cover is None:
                try:
                    pics = getattr(audio, "pictures", None)
                    if pics:
                        data = getattr(pics[0], "data", None)
                        if data:
                            cover = bytes(data)
                except Exception: pass
            if cover is None:
                cover = load_external_cover(filename)
        if year:
            try:
                y = str(year).strip()
                if len(y) >= 4 and y[:4].isdigit():
                    year = y[:4]
            except Exception: pass
    except Exception as exc:
        print(
            f"Lecture métadonnées impossible : "
            f"{filename}: {exc}",
            file=sys.stderr
        )
        if load_cover and cover is None:
            try: cover = load_external_cover(filename)
            except Exception: pass
    return (
        title,
        artist,
        album,
        year,
        duration,
        cover,
        technical,
    )

def detect_ui_scale():
    try:
        display = Gdk.Display.get_default()
        if display is None: return 1.0
        monitor = None
        try:
            monitor = display.get_primary_monitor()
        except Exception:
            monitor = None
        if monitor is None:
            try:
                if display.get_n_monitors() > 0:
                    monitor = display.get_monitor(0)
            except Exception: pass
        if monitor is None: return 1.0
        try:
            sys_scale = float(monitor.get_scale_factor() or 1)
        except Exception:
            sys_scale = 1.0
        sys_scale = max(1.0, sys_scale)
        if sys_scale >= 1.5: return 1.0
        geo = monitor.get_geometry()
        width = int(getattr(geo, "width", 0) or 0)
        height = int(getattr(geo, "height", 0) or 0)
        dpi = None
        try:
            w_mm = monitor.get_width_mm()
            if w_mm and w_mm > 0 and width > 0:
                dpi = (width * 25.4) / float(w_mm)
        except Exception:
            dpi = None
        boost = 1.0
        if dpi is not None and dpi >= 160:
            boost = min(2.0, max(1.25, dpi / 110.0))
        elif width >= 3840 or height >= 2160:
            boost = 1.5
        elif width >= 3000 or height >= 1800:
            boost = 1.35
        elif width >= 2560 or height >= 1440:
            boost = 1.2
        else:
            boost = 1.0
        for candidate in (1.0, 1.15, 1.25, 1.35, 1.5, 1.75, 2.0):
            if abs(boost - candidate) < 0.08: return candidate
        return round(boost * 20) / 20.0
    except Exception:
        return 1.0

def _memlog_tick():
    try:
        import tracemalloc
        lines = []
        try:
            with open("/proc/self/status", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith(("VmRSS:", "RssAnon:", "RssFile:", "RssShmem:")):
                        lines.append(line.strip())
        except Exception: pass
        try:
            if not tracemalloc.is_tracing():
                tracemalloc.start(15)
            snap = tracemalloc.take_snapshot()
            top = snap.statistics("lineno")[:5]
            lines.append("tracemalloc top:")
            for s in top:
                lines.append("  " + str(s))
        except Exception: pass
        try:
            w = getattr(_memlog_tick, "window", None)
            if w is not None:
                lines.append(
                    "cache=%s missing=%s playlist=%s gc=%s"
                    % (
                        len(getattr(w, "metadata_cache", {}) or {}),
                        len(getattr(w, "_missing_cache", {}) or {}),
                        len(w.playlist) if getattr(w, "playlist", None) is not None else 0,
                        gc.get_count(),
                    )
                )
        except Exception: pass
        print("[memlog]", " | ".join(lines), flush=True)
    except Exception as exc:
        print("[memlog] error:", exc, file=sys.stderr)
    return True

_CSS_TPL_Z = bytes.fromhex(
    "78dad51c5d6fdb38ecbdbfc2c01eb61d6c234dd32eeb80fd837bbac7c361b0632531ceb1025b5dd715fdefa70f7fe883a2e524ed767787b611298aa4"
    "4889a4987b2ceb823e46cfcf5779b6f977d7d087bab88f9e1fe5f0b77cf76dd3b62f5f3460b2a1156d00947efcf8f7fb0eb4ddbdff87035e5eae5246"
    "8f79d638cb705c05e164246e4e9b8234494e19a3078ed07eb8fef872fc11b5b42a0b035f220ef437fb861e485234d9ce5a843559dd1eb386d40c10c3"
    "844a9af735ad89f8f02369f71997a31be0cbe40f9cad3addd09a35b432174aca43b623fd644bc89cd5bd849a9ac4e856971b1458ce1da5ed34d46445"
    "f9d0ca093762c297ab63561465bd1b87c41f6b053b9475f258166c2fa1cb4fe3e89e94bb3d53c32b35bce5e2256df9932876ba5157fc2acb89548212"
    "a9acf7a42919325f99455a67354d0eb4209166163af7d73df74b9bfb05ff9035bbb2167f41143925137b14d13383efb39c3590ed39f1e0ab3f8f55f6"
    "54952de396fa237691da0d5750450a1d304c5140efa273f9776c1254e50a3284eb356808d777a0212cd4a86b804bff0efb4c06268d5010da4ba684bc"
    "05ad7d090bb906855ccee3c427d05223fea816cd6955a8c3ca3407d610125f899fdf4bf20800ad73463b4004143857e47077b078158d2c78df928a6c"
    "18292656e668bed505683cfe03dd615a48e3841e4fa4f6e9c0b59b080dc7c098b54bc67e58fab9f51c76f77bfa9d80f797389e25d0d68373097614b3"
    "0d2bbf131f29050d506be786d675d24f376e478f0b49d1b7d9a1ac9eeeffe2d7a0a38ccf3e23064f2b79eface09b656dab1571a29338d2d45b14d039"
    "e1b2ba4659351736aec680853dc7024aa6dd64153f08e4af744f9bf227c7caaa7e845b182bf95f822a7d605559132452917322cecbc36eefd2b40003"
    "69633812c2f0b8a8ed861d8b9558df14100e11fd1846b064865dd2ac5bf65499f0f14ef45e408e224c3d593b0dde8863b0a274b0e7e895980228d181"
    "0d7ab421d1b1a1bb86b4ad4fb5c3048f92b76555612a76e1bf42c1bdd67425750119105d40d8a3a5c19b05cd6979a04c1a607f6c252b4433441cc207"
    "373ee846a1106d228dc076a153bc0cefad431cc845928a6c99c6ea9d19820966bd501e5b6bb0cf064ce557ce8e8b2996efee1f0eb993752c4e3205a5"
    "7f7593066f9776f1a23caa3bf8455b79f046d34086e160f380efb43e94ea0c440c255626b6f8021e98bd4abde7a58b708237bb0962e896598a33376c"
    "427d9e3809da285c7e643fcd4362dca8b52f3fb9190e8fb4285b197ab0f240c6d45505587cc8889abb40e44f5a53ee961b82e40af2be4f698b44cc1c"
    "08447562148cd4978ba9e06bb44d3db470a5d762fe74dbf6214a7b4699043ddf8095758eef7a866db658c38375568f9bf26e2bfff9e2e75277046b6b"
    "eea0cbfde60e5c12b2d94eee6697671f96b7b77134fe58a43c4b1888309ab5cc3f77c1f1d57fe99d98658906974af03d5f0f1af46ef9da90d3b47573"
    "ed491bbfc112d92187db93ac00dc9e6f53f3f44d01d15a228ca8f949078293da6bd7ccc63c4e92063905ce51e47ebba4601e0ff2ca6b5d3d372b5cba"
    "54cbe6077f896d5c0396b6246b36fba4e1e93984f10aa785ad5f603bf409b384bddfd2cd431bcf99d1d73c664d12c2150d3dbeb17ab4f20d58529f5d"
    "165aba6521d84e50702a201338d1d748293484d6800c5fabf2847d66cd4b1c3db39dfc99cb9f941f65257b7af9e8d5ef94a49a394cb3198a3ce0f5e6"
    "3983b49a125e90036de2d4821d6c0b01487c03851447da04e2f7d817dff0c9b79d542fe9079c819731c2b40ba35f7b9dc1d23cf6ea29f94ed9d6c54a"
    "c25635389e806b76357500ce2f2ae34bcf74066452907360f367b9d6efefafafebdf675f6cbf5cc4373ba4cee572c277d52f5f3bc0e9ae0bd7c1c358"
    "31caeb56fd67a1d51e03a9e105d0f904fd257881305183b750fc698fb72a0e3f95ace78b31d6d820311414150342992a8e9d259d56debb3e555e6f51"
    "4c13092f8b418827f0e1797a3c41fd731657ef6317b1dd804d479368d7ab3dbea99770cf38563ad95fc7008cda25ab88165d0dd961973e293662330c"
    "348133ba998672861de7990a877c1169ec42fb9c16667967287bde794adb60bd0ad64a5fdf467473c2ebbfa90aefe2eaa518df18abcfca570f73aa73"
    "4ad720d71262f5dcf1bb1dde970ed96ab8f3694402acfb1922dbe199545b5aef1269cc4e715e3370ab78b1c64a949262596fa945500c79e88db5c3b4"
    "65195359b736550d4e5652527eb494a330f81ef8443d696f0ea47e48e678b5bfacaef125a86af62c3ec651dafd9269ec0f96888fbe2245ba23ec8322"
    "c3a58e23b3f9f3e38b77b5f0e6c88eaf929143c79bf8d3da3f83345a9cd3c8758e1a0d9fff40a86af3bad3659837e6de918932baf984eed47123d5a7"
    "9d3e1f279a8f86c58ab2cdf2ca58bf1f024f1ad3dabbf8ff7e917e521721e1baca18d5248c86318f30520a8ee436316910bb2d07ecec1b7ced98d59c"
    "fdafe3d2b11a1ac37a00a605e95f27987eb790ff9cf1266bdd4f33c4d33b98f4de5747e6fe22f34b6e62c0f223af61ef6e32f12f96fa750151763c26"
    "45995574175fa9dfda90bf8839b49483254c13ea3576e4b4807bc7e77386b6ba6be46473b03eb06bcac21cc10b28272f9d607573a0edfee24df960b4"
    "b69eea0ed425080b03b066ce25487753d19604dd8bd09580b6ef4f3460e83a59e21dfaebe90e7d4cf2955f72c4bfe737a15af60edd1fa883c8a7b6b3"
    "fa9b2ff0c509db69c303275b6f6ff9158f95dd4811e4554842630b13d6d40cd086aa0cb88585f632875b629f4fc5e02852cf0d343bcf72f6f30a8473"
    "f1ee7a681134b777f3c85352f93133b778d8d0434ec5d76aa616bfa0cb58275d4e8ba7b36f54a3d16a017a53d7b3ed0c0dd11704338bc3dee5a79bbc"
    "1dd24303b79f210405e80f7757f8ff3485df0437854f9582effc7bafb77a234a47b1e0967277a9b1897c7a5b7ef786f2e0965444a97d5f2f82e2eff1"
    "fd9d1a73cfed37be80165fa56f173575ffee05b5f0ffc6fbe77959585d74ff3c7df5afba83fa9bc7f8c61103589ec75d5f7e8fd40026db76a0b5c35e"
    "73e1a9a14fb7b0d09e5bd189b442a2b1d778a985b946ddeaf257c5e51f6431b1a61d23e4cc985ac4fbb47a59f743de534fb3b1377b510d76de996f6e"
    "21092c9e246139e25419ed94fc2928650b606a5e6e77c147dcc6fc921c5a073cef19374855af53cce8b3d818ce6dbf467ed8f0d571fef94db2dff0f8"
    "1a4fd515cbf3f42929e63c6249f28aa3c7c6a75e49da882881c3af0babc5eaf676e950b4ab8ac317589c3e0cbe299c1c69da3da54cfcbf45621441d5"
    "b3711cf1fd4b1ca3e9bfe76ba10805618c8c702f1f238a878d11a17173bd1945eda96f1bfc074e8e3124"
)

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
        try: self._init_rgba_visual()
        except Exception: pass
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
        try:
            self._load_tr_table(self.language)
        except Exception:
            self._tr_table = TRANSLATIONS.get(self.language, {}) or {}
        self.user_seeking = False
        self._seek_user_moved = False
        self._seek_press_value = None
        self.search_text = ""
        self.sort_column = None
        self.sort_ascending = True
        self.spectrum_values = [0.0] * SPECTRUM_BANDS
        self.spectrum_targets = [0.0] * SPECTRUM_BANDS
        self.spectrum_peaks = [0.0] * SPECTRUM_BANDS
        self.spectrum_quiet = [0] * SPECTRUM_BANDS
        self.spectrum_peak_hold = [0.0] * SPECTRUM_BANDS
        self._spectrum_delay_queue = deque()
        self.spectrum_sync_offset_ms = 0  # trim utilisateur (Bluetooth, etc.)
        self.spectrum_sync_window = None
        self._spectrum_stale_count = 0
        self._spectrum_use_fallback = False
        self._spectrum_epoch = 0          # incrémenté à chaque changement de média
        self._spectrum_ok_count = 0       # frames cohérentes (sortie du fallback)
        self.metadata_cache = OrderedDict()
        self._disk_meta_cache = {}
        self._disk_meta_dirty = False
        self._disk_meta_save_id = 0
        try:
            self._load_disk_meta_cache()
        except Exception:
            self._disk_meta_cache = {}
        self._missing_cache = {}
        self._cover_pixbuf_cache = OrderedDict()  # path → GdkPixbuf réduit
        self._selection_info_timeout_id = 0
        self._pending_selection_path = None
        self._info_generation = 0
        self._transparency_css_timeout_id = 0
        self._spectrum_mag_method = None
        self._mkv_probe_cache = {}
        self._spectrum_in_filter = True
        self._sub_lang_cache = {}
        self._video_probe_cache = {}
        self.spectrum_enabled = True
        self.spectrum_agc_enabled = SPECTRUM_AGC_ENABLED_DEFAULT
        self._window_obscured = False  # iconified / withdrawn → pause spectre
        self._visualizer_idle_ticks = 0
        self.recent_files = []
        try:
            self._load_recent_files()
        except Exception:
            self.recent_files = []
        self.resume_prompt_enabled = True
        self._resume_data = {}
        self._resume_prompt_open = False
        self._resume_last_tick = 0.0
        self._resume_reset = False
        try:
            self._load_resume_data()
        except Exception:
            self._resume_data = {}
        self.spectrum_agc_ceiling = SPECTRUM_HIGH_DB
        self._visualizer_last_tick = None
        self.eq_window = None
        self.eq_scales = []
        self.current_is_cdda = False
        self.radios = []
        self.load_radios()
        self.eq_presets = []
        self.load_eq_presets()
        self.icy_title = ""
        self.icy_artist = ""
        self.current_radio_name = None
        self.current_tv_name = None
        self._recording = False
        self._record_pipeline = None
        self._record_temp_path = None
        self._record_bus_watch_id = 0
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
        self.performance_mode = self._load_performance_mode()
        self._hw_fallback_path = None  # chemin pour lequel le soft a déjà été forcé
        self._apply_decoder_performance_mode()
        self._apply_safe_audio_decoder_ranks()
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
        self.video_sink = None
        self._video_sink_is_real = False
        try:
            _vfake = Gst.ElementFactory.make("fakesink", "video_sink_placeholder")
            if _vfake is not None:
                try: _vfake.set_property("sync", False)
                except Exception: pass
                try: _vfake.set_property("async", False)
                except Exception: pass
                self.player.set_property("video-sink", _vfake)
                self.video_sink = _vfake
        except Exception: pass
        self._configure_playbin_buffers()
        self.is_fullscreen = False
        self.subtitles_enabled = True
        self.current_is_video = False
        self._resume_prefer_video = False
        self._mini_title_scroll_id = 0
        self._mini_title_text = ""
        self._mini_title_offset = 0
        self.lyrics_enabled = False
        self.lyrics_data = None
        self.lyrics_current_index = -1
        self.lyrics_mode = False
        self.desktop_notifications = True
        self.window_borders = True
        self.window_layer = "normal"
        self.playlist_transparency = False
        self.playlist_transparency_opacity = 0.35  # 0=invisible, 1=opaque
        self.show_metadata_panel = True  # panneau bas (jaquette + métadonnées)
        self.spectrum_bar_color = (0.31, 0.71, 0.94)  # RGB 0..1
        self.spectrum_style = "pixel"
        self.playlist_only_mode = False
        self.playlist_only_maximized = False
        self._playlist_only_prev_size = None
        self._playlist_only_prev_layer = None
        self.playback_rate = 1.0
        self.speed_window = None
        self.nano_player_mode = False
        self.nano_banner_enabled = False
        self.nano_vertical = False
        self.nano_playlist_enabled = False
        self.nano_playlist_side = "right"  # left | right (mode vertical)
        self._last_notify_key = None
        self._notify_unavailable = False
        self._notify_service_checked = False
        self.mini_player_mode = False
        self._normal_size = None
        self._screensaver_cookie = None
        self._screensaver_cookie_kind = None
        self._screensaver_proxy = None
        self._video_window_handle = None
        self._pending_restore_path = None
        self._restore_handle_tries = 0
        self._fullscreen_ui_state = None  # pour restaurer l'UI après plein écran
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
        self._last_known_position = 0.0
        self._pending_seek_delta = 0.0
        self._updating_position_scale = False
        self._pause_pixbuf = None  # dernière image figée en pause (anti-fantôme)
        self.tvs = []
        self.load_tvs()
        self.bouquets = []
        self.load_bouquets()
        self.bouquet_active = False
        self.associated_formats = set()
        self.load_associations()
        try: self._ensure_desktop_file()
        except Exception: pass
        self.pitch = None
        self.equalizer = Gst.ElementFactory.make(
            "equalizer-10bands",
            "equalizer"
        )
        self.scaletempo = Gst.ElementFactory.make("scaletempo", "scaletempo")
        self.tempo_conv = (
            Gst.ElementFactory.make("audioconvert", "tempo-conv")
            if self.scaletempo is not None else None
        )
        self.eq_muted = False
        self.eq_saved_gains = []
        self.is_muted = False
        self._volume_before_mute = 0.75
        # --- ReplayGain / normalisation ---
        self.replaygain_mode = "off"          # off | track | album | auto
        self.rgvolume = None
        try:
            self.rgvolume = Gst.ElementFactory.make("rgvolume", "rgvolume")
        except Exception:
            self.rgvolume = None
        if self.rgvolume is not None:
            try:
                self.rgvolume.set_property("album-mode", False)
                self.rgvolume.set_property("pre-amp", 0.0)
                self.rgvolume.set_property("fallback-gain", -6.0)
            except Exception: pass
        # --- Crossfade / gapless ---
        self.crossfade_seconds = 0.0          # 0 = off
        self.gapless_enabled = False
        self._crossfade_active = False
        self._crossfade_timeout_id = 0
        # --- Sleep timer ---
        self.sleep_timer_id = 0
        self.sleep_timer_end = 0.0            # monotonic deadline
        self.sleep_timer_minutes = 0
        self.sleep_end_of_track = False
        # --- Sortie audio (Pulse / PipeWire sink name, "" = auto) ---
        self.audio_sink_name = ""
        self._audio_sink_element = None
        # --- Scrobbling (ListenBrainz / Last.fm) ---
        self.scrobble_enabled = False
        self.scrobble_service = "listenbrainz"  # listenbrainz | lastfm
        self.scrobble_token = ""                 # LB token or Last.fm session key
        self.scrobble_username = ""
        self.scrobble_api_key = ""               # Last.fm only
        self.scrobble_api_secret = ""
        self._scrobble_sent_for = None           # path already scrobbled this play
        self._scrobble_started_at = 0.0
        self._nowplaying_sent_for = None
        # --- File d'attente « Lire ensuite » ---
        self.play_next_queue = []                # list of paths, FIFO front = next
        self.chapters = []
        self._osd_timeout_id = 0
        self._cursor_hide_timeout_id = 0
        self._cursor_hidden = False
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
        self.colorimetry_check_item = None
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
        self.restore_state()
        try:
            if not bool(getattr(self, "window_borders", True)):
                self.set_decorated(False)
        except Exception: pass
        try: self._update_undecorated_move_hint()
        except Exception: pass
        try: self._apply_playlist_transparency()
        except Exception: pass
        try: self._apply_metadata_panel_visibility()
        except Exception: pass
        try:
            if getattr(self, "nano_player_mode", False) or getattr(self, "mini_player_mode", False):
                GLib.idle_add(self._apply_compact_player_mode)
            try: GLib.idle_add(self._apply_window_layer)
            except Exception: pass
        except Exception: pass
        self.connect("destroy", self.on_destroy)
        self._app_closing = False
        self._main_popup_menu = None
        self._playlist_context_menu = None
        self._position_timeout_id = GLib.timeout_add(100, self.update_position)
        self._visualizer_timeout_id = GLib.timeout_add(VISUALIZER_INTERVAL_MS, self.update_visualizer)
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
                if language in LANGUAGES: return language
        except Exception: pass
        return "English"

    def tr(self, text):
        if self.language == "Français": return text
        table = getattr(self, "_tr_table", None)
        if table is None:
            table = TRANSLATIONS.get(self.language, {})
        return table.get(text, text)

    def _load_tr_table(self, language):
        if language == "Français":
            self._tr_table = {}
            return
        table = TRANSLATIONS.get(language)
        if isinstance(table, dict):
            self._tr_table = table
        else:
            self._tr_table = {}

    def set_language(self, language):
        if language not in LANGUAGES: return
        self.language = language
        try:
            self._load_tr_table(language)
        except Exception:
            self._tr_table = TRANSLATIONS.get(language, {}) or {}
        self.apply_language()
        self.save_state()

    def apply_language(self):
        self.set_title(APP_NAME)
        if hasattr(self, "add_files_button"):
            self.add_files_button.set_tooltip_text(self.tr("Ajout des fichiers"))
        if hasattr(self, "record_button"):
            if getattr(self, "_recording", False):
                self.record_button.set_tooltip_text(self.tr("Arrêter l'enregistrement"))
            else:
                self.record_button.set_tooltip_text(self.tr("Enregistrement"))
        if hasattr(self, "previous_button"):
            self.previous_button.set_tooltip_text(self.tr("Morceau précédent"))
        if hasattr(self, "play_button"):
            self.play_button.set_tooltip_text(self.tr("Lecture / Pause"))
        if hasattr(self, "stop_button"):
            self.stop_button.set_tooltip_text(self.tr("Stop"))
        if hasattr(self, "next_button"):
            self.next_button.set_tooltip_text(self.tr("Morceau suivant"))
        if hasattr(self, "shuffle_button"):
            self.shuffle_button.set_tooltip_text(
                self.tr(
                    "Lecture aléatoire activée"
                    if self.shuffle_mode
                    else "Lecture aléatoire"
                )
            )
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
        try: self._update_undecorated_move_hint()
        except Exception: pass
    def load_radios(self):
        self.radios = []
        if not os.path.isfile(RADIOS_FILE): return
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
        if not url: return None
        for radio in self.radios:
            if radio["url"] == url: return radio["name"]
        base = url.split("?")[0]
        for radio in self.radios:
            if radio["url"].split("?")[0] == base: return radio["name"]
        return None

    def play_radio(self, url):
        if not url: return
        self.dismiss_bouquet_playlist()
        self._prefer_video = False
        self._resume_prefer_video = False
        self.current_tv_name = None
        self.current_radio_name = self.find_radio_name(url)
        self.play_file(url)
        try:
            self.lyrics_mode = False
            self.lyrics_data = None
            self.lyrics_current_index = -1
            self.show_video_area(False)
        except Exception: pass
        self.status_label.set_text(self.tr("Radio en cours"))

    def manage_radios(self, *args):
        dialog = Gtk.Dialog(
            title=self.tr("Gérer les radios"),
            parent=self,
            flags=Gtk.DialogFlags.MODAL
        )
        dialog.set_default_size(self._ui_s(560), self._ui_s(380))
        dialog.add_button(self.tr("Fermer"), Gtk.ResponseType.CLOSE)
        self._style_app_dialog(dialog)
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
            if it is None: return
            old_name = model[it][0]
            old_url = model[it][1]
            name, url = self._radio_edit_dialog(old_name, old_url)
            if name and url:
                model[it][0] = name
                model[it][1] = url
                _sync_radios_from_store()
        def on_remove(*a):
            model, it = tree.get_selection().get_selected()
            if it is None: return
            model.remove(it)
            _sync_radios_from_store()
        def on_move(delta):
            model, it = tree.get_selection().get_selected()
            if it is None: return
            path = model.get_path(it)
            idx = path.get_indices()[0]
            new_idx = idx + delta
            n = len(model)
            if new_idx < 0 or new_idx >= n: return
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
        if response == Gtk.ResponseType.OK and new_name and new_url: return new_name, new_url
        return None, None

    def load_tvs(self):
        self.tvs = []
        if not os.path.isfile(TVS_FILE): return
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
        if not url: return None
        for tv in self.tvs:
            if tv["url"] == url: return tv["name"]
        base = url.split("?")[0]
        for tv in self.tvs:
            if tv["url"].split("?")[0] == base: return tv["name"]
        return None

    def play_tv(self, url, name=None):
        if not url: return
        self.dismiss_bouquet_playlist()
        self._prefer_video = True
        self.current_tv_name = (name or "").strip() or self.find_tv_name(url)
        self.current_radio_name = None
        try: self.show_video_area(True)
        except Exception: pass
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
            if it is None: return
            old_name = model[it][0]
            old_url = model[it][1]
            name, url = self._tv_edit_dialog(old_name, old_url)
            if name and url:
                model[it][0] = name
                model[it][1] = url
                _sync_tvs_from_store()
        def on_remove(*a):
            model, it = tree.get_selection().get_selected()
            if it is None: return
            model.remove(it)
            _sync_tvs_from_store()
        def on_move(delta):
            model, it = tree.get_selection().get_selected()
            if it is None: return
            path = model.get_path(it)
            idx = path.get_indices()[0]
            new_idx = idx + delta
            n = len(model)
            if new_idx < 0 or new_idx >= n: return
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
        if response == Gtk.ResponseType.OK and new_name and new_url: return new_name, new_url
        return None, None

    def load_bouquets(self):
        self.bouquets = []
        if not os.path.isfile(BOUQUETS_FILE): return
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
        self._style_app_dialog(dialog)
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
            if it is None: return
            old_name = model[it][0]
            old_url = model[it][1]
            name, url = self._bouquet_edit_dialog(old_name, old_url)
            if name and url:
                model[it][0] = name
                model[it][1] = url
                _sync_bouquets_from_store()
        def on_remove(*a):
            model, it = tree.get_selection().get_selected()
            if it is None: return
            model.remove(it)
            _sync_bouquets_from_store()
        def on_move(delta):
            model, it = tree.get_selection().get_selected()
            if it is None: return
            path = model.get_path(it)
            idx = path.get_indices()[0]
            new_idx = idx + delta
            n = len(model)
            if new_idx < 0 or new_idx >= n: return
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
        if response == Gtk.ResponseType.OK and new_name and new_url: return new_name, new_url
        return None, None
    @staticmethod
    def parse_m3u_playlist(text):
        channels = []
        if not text: return channels
        pending_name = None
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line: continue
            if line.startswith("#EXTM3U"): continue
            if line.startswith("#EXTINF"):
                name = ""
                if "," in line:
                    name = line.split(",", 1)[1].strip()
                pending_name = name or None
                continue
            if line.startswith("#"): continue
            url = line
            if not (
                url.startswith("http://")
                or url.startswith("https://")
                or url.startswith("rtsp://")
                or url.startswith("mms://")
                or url.startswith("rtp://")
            ):
                if "://" not in url:
                    pending_name = None
                    continue
            title = pending_name or url
            if len(title) > 120:
                title = title[:117] + "…"
            channels.append((title, url))
            pending_name = None
        return channels

    def _clear_playlist_search(self):
        self.search_text = ""
        entry = getattr(self, "search_entry", None)
        if entry is None: return
        try:
            entry.handler_block_by_func(self.on_search_changed)
        except Exception:
            try: entry.set_text("")
            except Exception: pass
            return
        try: entry.set_text("")
        except Exception: pass
        try: entry.handler_unblock_by_func(self.on_search_changed)
        except Exception: pass
    def dismiss_bouquet_playlist(self):
        if not getattr(self, "bouquet_active", False): return
        self.bouquet_active = False
        try: self.stop()
        except Exception: pass
        self.current_path = None
        try:
            self._clear_playlist_search()
        except Exception:
            self.search_text = ""
        try:
            self._playlist_views_detach_clear_reattach(clear_cache=True)
        except Exception:
            try: self.playlist.clear()
            except Exception: pass
            try: self.metadata_cache.clear()
            except Exception: pass
            try:
                tree = getattr(self, "tree", None)
                flt = getattr(self, "playlist_filter", None)
                if tree is not None and flt is not None:
                    tree.set_model(flt)
                    flt.refilter()
            except Exception: pass
        try:
            self.bottom_title.set_text(self.tr("Aucun morceau sélectionné"))
            self.bottom_artist.set_text("")
            self.bottom_album.set_text("")
            self.set_default_cover()
        except Exception: pass
        try:
            self._restore_playlist_from_disk(quiet=True)
        except Exception as e:
            print("dismiss bouquet restore:", e, file=sys.stderr)
        try: self._refresh_playlist_scroll_adjustments()
        except Exception: pass
        try:
            tree = getattr(self, "tree", None)
            if tree is not None:
                tree.queue_draw()
        except Exception: pass

    def load_bouquet(self, m3u_url):
        if not m3u_url: return
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
        try:
            self._clear_playlist_search()
        except Exception:
            self.search_text = ""
        try:
            self._playlist_views_detach_clear_reattach(clear_cache=True)
        except Exception:
            try: self.playlist.clear()
            except Exception: pass
            try: self.metadata_cache.clear()
            except Exception: pass
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
        try:
            if getattr(self, "playlist_filter", None) is not None:
                self.playlist_filter.refilter()
        except Exception: pass
        try: self._refresh_playlist_scroll_adjustments()
        except Exception: pass
        count = len(channels)
        self.status_label.set_text(
            self.tr("Bouquet chargé") + f" — {count}" + self.tr(" chaîne(s) chargée(s)")
        )
        if first_url:
            self._prefer_video = True
            self.play_file(first_url)
        return False

    def load_associations(self):
        self.associated_formats = set()
        if not os.path.isfile(ASSOCIATIONS_FILE): return
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
        try:
            apps_dir = os.path.dirname(DESKTOP_FILE_PATH)
            os.makedirs(apps_dir, exist_ok=True)
            mime_types = []
            for ext, mimes in ASSOCIABLE_FORMATS:
                if ext in self.associated_formats:
                    for m in mimes:
                        if m not in mime_types:
                            mime_types.append(m)
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
            try: os.chmod(DESKTOP_FILE_PATH, 0o755)
            except Exception: pass
            try:
                subprocess.run(
                    ["update-desktop-database", apps_dir],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except Exception: pass
            return True
        except Exception as exc:
            print("Impossible d'écrire le fichier .desktop :", exc, file=sys.stderr)
            return False

    def _xdg_mime_default(self, mime_type, enable=True):
        try:
            if enable:
                subprocess.run(
                    ["xdg-mime", "default", DESKTOP_FILE_NAME, mime_type],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
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

    def load_eq_presets(self):
        self.eq_presets = []
        if not os.path.isfile(EQ_PRESETS_FILE):
            try:
                self.eq_presets = [
                    {"name": p["name"], "gains": list(p["gains"])}
                    for p in DEFAULT_EQ_PRESETS
                ]
                self.save_eq_presets()
            except Exception as e:
                print("init eq presets:", e, file=sys.stderr)
                self.eq_presets = [
                    {"name": p["name"], "gains": list(p["gains"])}
                    for p in DEFAULT_EQ_PRESETS
                ]
            return
        try:
            with open(EQ_PRESETS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    if not isinstance(item, dict): continue
                    name = item.get("name")
                    gains = item.get("gains")
                    if isinstance(name, str) and isinstance(gains, list) and len(gains) >= EQ_BANDS:
                        try:
                            self.eq_presets.append({
                                "name": name,
                                "gains": [float(gains[i]) for i in range(EQ_BANDS)],
                            })
                        except Exception: pass
        except Exception as e:
            print("load eq presets:", e, file=sys.stderr)
            if not self.eq_presets:
                self.eq_presets = [
                    {"name": p["name"], "gains": list(p["gains"])}
                    for p in DEFAULT_EQ_PRESETS
                ]

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

    def get_metadata(self, filename, load_cover=False):
        if not load_cover and filename in self.metadata_cache:
            try: self.metadata_cache.move_to_end(filename)
            except Exception: pass
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
        if not load_cover:
            try:
                hit = self._disk_meta_lookup(filename)
            except Exception:
                hit = None
            if hit is not None:
                self.metadata_cache[filename] = hit
                try: self.metadata_cache.move_to_end(filename)
                except Exception: pass
                while len(self.metadata_cache) > METADATA_CACHE_MAX:
                    try:
                        self.metadata_cache.popitem(last=False)
                    except Exception:
                        break
                return (
                    hit["title"],
                    hit["artist"],
                    hit["album"],
                    hit["year"],
                    hit["duration"],
                    None,
                    hit.get("technical") or {},
                )
        title, artist, album, year, duration, cover, technical = load_metadata(
            filename,
            load_cover=load_cover
        )
        entry = {
            "title": title,
            "artist": artist,
            "album": album,
            "year": year,
            "duration": duration,
            "technical": technical,
        }
        self.metadata_cache[filename] = entry
        try: self.metadata_cache.move_to_end(filename)
        except Exception: pass
        while len(self.metadata_cache) > METADATA_CACHE_MAX:
            try:
                self.metadata_cache.popitem(last=False)
            except Exception:
                break
        if not load_cover:
            try: self._disk_meta_store(filename, entry)
            except Exception: pass
        return (
            title,
            artist,
            album,
            year,
            duration,
            cover,
            technical,
        )

    def _load_disk_meta_cache(self):
        self._disk_meta_cache = {}
        if not os.path.isfile(META_CACHE_FILE): return
        try:
            with open(META_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._disk_meta_cache = data
        except Exception as exc:
            print("meta_cache load:", exc, file=sys.stderr)
            self._disk_meta_cache = {}

    def _schedule_disk_meta_save(self):
        self._disk_meta_dirty = True
        tid = getattr(self, "_disk_meta_save_id", 0) or 0
        if tid: return
        def _do():
            self._disk_meta_save_id = 0
            try: self._save_disk_meta_cache()
            except Exception: pass
            return False
        try:
            self._disk_meta_save_id = GLib.timeout_add_seconds(8, _do)
        except Exception:
            try: self._save_disk_meta_cache()
            except Exception: pass
    def _save_disk_meta_cache(self):
        if not getattr(self, "_disk_meta_dirty", False): return
        cache = getattr(self, "_disk_meta_cache", None)
        if not isinstance(cache, dict): return
        try:
            if len(cache) > META_DISK_MAX:
                keys = list(cache.keys())
                for k in keys[: max(0, len(keys) - META_DISK_MAX)]:
                    cache.pop(k, None)
            os.makedirs(CONFIG_DIR, exist_ok=True)
            tmp = META_CACHE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, META_CACHE_FILE)
            self._disk_meta_dirty = False
        except Exception as exc:
            print("meta_cache save:", exc, file=sys.stderr)

    def _disk_meta_lookup(self, filename):
        if not filename or is_stream(filename) or is_cdda(filename): return None
        if is_dvd(filename) or is_bluray(filename): return None
        try:
            st = os.stat(filename)
        except Exception:
            return None
        cache = getattr(self, "_disk_meta_cache", None) or {}
        rec = cache.get(filename)
        if not isinstance(rec, dict): return None
        try:
            if int(rec.get("m", -1)) != int(st.st_mtime) or int(rec.get("s", -1)) != int(st.st_size): return None
        except Exception:
            return None
        return {
            "title": rec.get("title") or os.path.splitext(os.path.basename(filename))[0],
            "artist": rec.get("artist") or "",
            "album": rec.get("album") or "",
            "year": rec.get("year") or "",
            "duration": float(rec.get("duration") or 0),
            "technical": rec.get("technical") or {},
        }

    def _disk_meta_store(self, filename, entry):
        if not filename or is_stream(filename) or is_cdda(filename): return
        if is_dvd(filename) or is_bluray(filename): return
        try:
            st = os.stat(filename)
        except Exception:
            return
        cache = getattr(self, "_disk_meta_cache", None)
        if cache is None:
            self._disk_meta_cache = {}
            cache = self._disk_meta_cache
        cache[filename] = {
            "m": int(st.st_mtime),
            "s": int(st.st_size),
            "title": entry.get("title") or "",
            "artist": entry.get("artist") or "",
            "album": entry.get("album") or "",
            "year": entry.get("year") or "",
            "duration": float(entry.get("duration") or 0),
            "technical": entry.get("technical") or {},
        }
        self._schedule_disk_meta_save()
    @staticmethod
    @lru_cache(maxsize=4096)
    def normalize_sort_text(value):
        if value is None: return ""
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
        if value1 < value2: return -1
        if value1 > value2: return 1
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
        if duration1 < duration2: return -1
        if duration1 > duration2: return 1
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

    def start_single_instance_server(self):
        self.instance_socket = None
        try: os.makedirs(CONFIG_DIR, exist_ok=True)
        except Exception: pass
        if os.path.exists(SOCKET_FILE):
            try: os.unlink(SOCKET_FILE)
            except Exception: pass
        try:
            self.instance_socket = socket.socket(
                socket.AF_UNIX,
                socket.SOCK_STREAM
            )
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
        while True:
            sock = getattr(self, "instance_socket", None)
            if sock is None: break
            try:
                connection, _ = sock.accept()
            except OSError:
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
                    if not data: break
                    chunks.append(data)
                    if sum(len(c) for c in chunks) > 8 * 1024 * 1024: break
                payload = b"".join(chunks).decode("utf-8", errors="ignore")
                files = [
                    line.strip()
                    for line in payload.splitlines()
                    if line.strip()
                ]
                GLib.idle_add(self.receive_external_files, files)
            except Exception as exc:
                print("instance_server handle:", exc, file=sys.stderr)
            finally:
                try: connection.close()
                except Exception: pass
    def receive_external_files(self, files):
        try:
            self.present()
            try:
                self.set_keep_above(True)
                self.set_keep_above(False)
            except Exception: pass
            self.present()
            try: GLib.timeout_add(1500, self._apply_window_layer)
            except Exception: pass
        except Exception: pass
        if not files: return False
        try: self.cancel_pending_session_restore()
        except Exception: pass
        added = False
        valid_paths = []
        for filename in files:
            if not isinstance(filename, str): continue
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
        if len(valid_paths) == 1:
            self.play_file(valid_paths[0])
        return False

    def save_playlist(self):
        if getattr(self, "bouquet_active", False): return
        try:
            atomic_write_json(PLAYLIST_FILE, self.get_paths())
        except Exception as exc:
            print("Impossible de sauvegarder la playlist :", exc, file=sys.stderr)

    def load_playlist(self):
        try:
            restored = self._restore_playlist_from_disk(quiet=False)
        except Exception as e:
            print("Impossible de charger la playlist :", e, file=sys.stderr)
            restored = 0
        if restored:
            try:
                self.status_label.set_text(
                    f"{restored}" + self.tr(" fichier(s) restauré(s)")
                )
            except Exception: pass

    def _restore_playlist_from_disk(self, quiet=True):
        if not os.path.isfile(PLAYLIST_FILE): return 0
        try:
            with open(PLAYLIST_FILE, "r", encoding="utf-8") as file:
                paths = json.load(file)
        except Exception as e:
            print("Impossible de lire la playlist :", e, file=sys.stderr)
            return 0
        if not isinstance(paths, list): return 0
        usable = [
            p for p in paths
            if isinstance(p, str) and p.strip()
        ]
        restored = 0
        prev_flag = getattr(self, "_restoring_playlist", False)
        self._restoring_playlist = True
        try:
            for filename in usable:
                try:
                    self._playlist_append_path(filename)
                    restored += 1
                except Exception as exc:
                    print("restore path:", filename, exc, file=sys.stderr)
        finally:
            self._restoring_playlist = prev_flag
        if not quiet and restored:
            try:
                self.status_label.set_text(
                    f"{restored}" + self.tr(" fichier(s) restauré(s)")
                )
            except Exception: pass
        return restored

    def _playlist_append_path(self, filename):
        title, artist, album, year, duration, cover, technical = \
            self.get_metadata(filename, load_cover=False)
        return self.playlist.append([
            filename,
            title,
            artist,
            album,
            format_time(duration),
            float(duration),
        ])

    def _playlist_views_detach_clear_reattach(self, clear_cache=True):
        tree = getattr(self, "tree", None)
        nano = getattr(self, "nano_playlist_tree", None)
        flt = getattr(self, "playlist_filter", None)
        for t in (tree, nano):
            if t is not None:
                try: t.set_model(None)
                except Exception: pass
        try: self.playlist.clear()
        except Exception: pass
        if clear_cache:
            try: self.metadata_cache.clear()
            except Exception: pass
            try:
                if isinstance(getattr(self, "_missing_cache", None), dict):
                    self._missing_cache.clear()
            except Exception: pass
            try:
                cache = getattr(self, "_cover_pixbuf_cache", None)
                if cache is not None and hasattr(cache, "clear"):
                    cache.clear()
            except Exception: pass
        if tree is not None:
            try:
                tree.set_model(flt if flt is not None else self.playlist)
            except Exception as e:
                print("rebind tree:", e, file=sys.stderr)
        if nano is not None:
            try: nano.set_model(self.playlist)
            except Exception: pass
        try:
            if flt is not None:
                flt.refilter()
        except Exception: pass
        try:
            sc = getattr(self, "playlist_scroll", None)
            if sc is not None:
                sc.get_vadjustment().set_value(0)
                sc.get_hadjustment().set_value(0)
        except Exception: pass

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
            last_path = self.current_path
            if getattr(self, "bouquet_active", False):
                last_path = None
            state = {
                "volume": self.volume_scale.get_value(),
                "replaygain_mode": str(getattr(self, "replaygain_mode", "off") or "off"),
                "crossfade_seconds": float(getattr(self, "crossfade_seconds", 0.0) or 0.0),
                "gapless_enabled": bool(getattr(self, "gapless_enabled", False)),
                "audio_sink_name": str(getattr(self, "audio_sink_name", "") or ""),
                "scrobble_enabled": bool(getattr(self, "scrobble_enabled", False)),
                "scrobble_service": str(getattr(self, "scrobble_service", "listenbrainz") or "listenbrainz"),
                "scrobble_token": str(getattr(self, "scrobble_token", "") or ""),
                "scrobble_username": str(getattr(self, "scrobble_username", "") or ""),
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
                "window_borders": bool(getattr(self, "window_borders", True)),
                "window_layer": str(getattr(self, "window_layer", "normal") or "normal"),
                "playlist_transparency": bool(getattr(self, "playlist_transparency", False)),
                "playlist_transparency_opacity": float(
                    getattr(self, "playlist_transparency_opacity", 0.35)
                ),
                "show_metadata_panel": bool(getattr(self, "show_metadata_panel", True)),
                "spectrum_bar_color": list(getattr(self, "spectrum_bar_color", (0.31, 0.71, 0.94))),
                "spectrum_style": str(getattr(self, "spectrum_style", "pixel") or "pixel"),
                "spectrum_sync_offset_ms": int(
                    getattr(self, "spectrum_sync_offset_ms", 0) or 0
                ),
                "playlist_only_mode": bool(getattr(self, "playlist_only_mode", False)),
                "playlist_only_maximized": bool(getattr(self, "playlist_only_maximized", False)),
                "playback_rate": float(getattr(self, "playback_rate", 1.0) or 1.0),
                "resume_prompt_enabled": bool(
                    getattr(self, "resume_prompt_enabled", True)
                ),
                "nano_player_mode": bool(getattr(self, "nano_player_mode", False)),
                "nano_banner_enabled": bool(getattr(self, "nano_banner_enabled", False)),
                "nano_vertical": bool(getattr(self, "nano_vertical", False)),
                "nano_playlist_enabled": bool(
                    getattr(self, "nano_playlist_enabled", False)
                ),
                "nano_playlist_side": str(
                    getattr(self, "nano_playlist_side", "right") or "right"
                ),
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
        if not os.path.isfile(STATE_FILE): return None
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as file:
                state = json.load(file)
            if not isinstance(state, dict): return None
            return state
        except Exception as e:
            print("Impossible de charger l'état :", e, file=sys.stderr)
            return None

    def restore_state(self):
        state = self.load_state()
        if not state: return
        volume = state.get("volume")
        if isinstance(volume, (int, float)):
            volume = max(0.0, min(1.0, volume))
            self.volume_scale.set_value(volume)
        try:
            rg = state.get("replaygain_mode")
            if isinstance(rg, str) and rg in ("off", "track", "album", "auto"):
                self.replaygain_mode = rg
                if rg != "off":
                    GLib.idle_add(lambda: (self.rebuild_audio_filter(force=True), False)[1])
        except Exception: pass
        try:
            cf = state.get("crossfade_seconds")
            if isinstance(cf, (int, float)):
                self.crossfade_seconds = max(0.0, min(CROSSFADE_MAX_S, float(cf)))
        except Exception: pass
        try: self.gapless_enabled = bool(state.get("gapless_enabled", False))
        except Exception: pass
        try:
            asn = state.get("audio_sink_name")
            if isinstance(asn, str):
                self.audio_sink_name = asn
                if asn:
                    GLib.idle_add(lambda: self._apply_audio_sink(asn) or False)
        except Exception: pass
        try:
            self.scrobble_enabled = bool(state.get("scrobble_enabled", False))
            svc = state.get("scrobble_service")
            if isinstance(svc, str) and svc in ("listenbrainz", "lastfm"):
                self.scrobble_service = svc
            tok = state.get("scrobble_token")
            if isinstance(tok, str):
                self.scrobble_token = tok
            user = state.get("scrobble_username")
            if isinstance(user, str):
                self.scrobble_username = user
        except Exception: pass
        eq_gains = state.get("equalizer")
        if isinstance(eq_gains, list) and self.equalizer is not None:
            for i, gain in enumerate(eq_gains):
                if i >= EQ_BANDS: break
                try:
                    gain = max(EQ_MIN_DB, min(EQ_MAX_DB, float(gain)))
                    self.equalizer.set_property(f"band{i}", gain)
                except Exception: pass
        pitch = state.get("pitch")
        if isinstance(pitch, (int, float)):
            self.pitch_value = max(0.5, min(2.0, float(pitch)))
        tempo = state.get("tempo")
        if isinstance(tempo, (int, float)):
            self.tempo_value = max(0.5, min(2.0, float(tempo)))
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
        except Exception: pass
        self.rebuild_audio_filter(force=True)
        try: self._rebuild_video_color_filter()
        except Exception: pass
        last_path = state.get("last_path")
        if last_path and last_path in self.get_paths():
            if is_special_source(last_path) or (
                isinstance(last_path, str) and os.path.isfile(last_path)
            ):
                self._pending_restore_path = last_path
                GLib.idle_add(self._deferred_restore_paused)

    def cancel_pending_session_restore(self):
        self._pending_restore_path = None
        self._restore_handle_tries = 0

    def _deferred_restore_paused(self):
        path = getattr(self, "_pending_restore_path", None)
        if not path: return False
        try:
            if not self.get_realized(): return True  # réessayer au prochain idle
            if not self.get_window(): return True
        except Exception: pass
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
                except Exception: pass
                self._cache_video_window_handle()
                if not getattr(self, "_video_window_handle", None):
                    if not hasattr(self, "_restore_handle_tries"):
                        self._restore_handle_tries = 0
                    self._restore_handle_tries += 1
                    if self._restore_handle_tries < 20: return True
        except Exception: pass
        self._pending_restore_path = None
        self._restore_handle_tries = 0
        try:
            self.load_file_paused(path)
        except Exception as e:
            print("restauration différée:", e, file=sys.stderr)
        return False

    def load_file_paused(self, filename):
        if not filename: return
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
            if getattr(self, "_need_playbin_rebind", False):
                self._need_playbin_rebind = False
                try:
                    self._video_sink_is_real = False
                    self._ensure_video_sink()
                except Exception: pass
                try: self.rebuild_audio_filter(force=True, restart=False)
                except Exception: pass
            self.icy_title = ""
            self.icy_artist = ""
            if is_stream(filename):
                self.current_radio_name = self.find_radio_name(filename)
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
            try: self.rebuild_audio_filter(force=True, restart=False)
            except Exception: pass
            self.set_subtitles_enabled(self.subtitles_enabled)
            self.subtitle_tracks = []
            self.current_subtitle_index = -1
            self.audio_tracks = []
            self.current_audio_index = -1
            self._prepare_external_subtitles_for(filename)
            try: self._register_external_subtitle_tracks()
            except Exception: pass
            try: self.player.set_property("current-text", -1)
            except Exception: pass
            if self.current_is_video:
                self._cache_video_window_handle()
            self._destroy_custom_pipeline()
            if self.current_is_video:
                try:
                    while Gtk.events_pending():
                        Gtk.main_iteration_do(False)
                except Exception: pass
                self._cache_video_window_handle()
                self._apply_video_overlay_handle()
            self.player.set_property("uri", uri)
            self.player.set_state(Gst.State.PAUSED)
            self.is_playing = True
            self.is_paused = True
            self.play_button.set_label("▶")
            self.status_label.set_text(self.tr("Session restaurée (en pause)"))
            try:
                if (
                    getattr(self, "resume_prompt_enabled", True)
                    and self._resume_eligible(filename)
                ):
                    p = self._resume_lookup(filename)
                    if p is not None:
                        self._resume_apply_later(
                            filename, max(0.0, p - RESUME_REWIND)
                        )
            except Exception: pass
            self.update_information(filename)
            self.select_filename(filename)
        except Exception as e:
            self.status_label.set_text(self.tr("Erreur : ") + str(e))
            print("Erreur restauration de session:", e, file=sys.stderr)

    def _load_ui_prefs(self):
        try:
            state = self.load_state()
            if not state: return
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
            if "window_borders" in state:
                self.window_borders = bool(state.get("window_borders"))
            if "window_layer" in state:
                layer = str(state.get("window_layer") or "normal").lower()
                if layer not in ("normal", "below"):
                    layer = "normal"
                self.window_layer = layer
            if "playlist_transparency" in state:
                self.playlist_transparency = bool(
                    state.get("playlist_transparency")
                )
            if "show_metadata_panel" in state:
                self.show_metadata_panel = bool(state.get("show_metadata_panel"))
            if "playlist_transparency_opacity" in state:
                try:
                    op = float(state.get("playlist_transparency_opacity"))
                    self.playlist_transparency_opacity = max(0.0, min(1.0, op))
                except Exception: pass
            if "nano_player_mode" in state:
                self.nano_player_mode = bool(state.get("nano_player_mode"))
            if "nano_banner_enabled" in state:
                self.nano_banner_enabled = bool(state.get("nano_banner_enabled"))
            if "nano_vertical" in state:
                self.nano_vertical = bool(state.get("nano_vertical"))
            if "nano_playlist_enabled" in state:
                self.nano_playlist_enabled = bool(state.get("nano_playlist_enabled"))
            if "nano_playlist_side" in state:
                side = str(state.get("nano_playlist_side") or "right").lower()
                self.nano_playlist_side = "left" if side == "left" else "right"
            sc_col = state.get("spectrum_bar_color")
            if isinstance(sc_col, (list, tuple)) and len(sc_col) >= 3:
                try:
                    self.spectrum_bar_color = (
                        float(sc_col[0]),
                        float(sc_col[1]),
                        float(sc_col[2]),
                    )
                except Exception: pass
            if "spectrum_style" in state:
                st = str(state.get("spectrum_style") or "pixel").lower()
                if st not in ("bars", "oscilloscope", "pixel"):
                    st = "pixel"
                self.spectrum_style = st
            if "spectrum_sync_offset_ms" in state:
                try:
                    v = int(round(float(state.get("spectrum_sync_offset_ms") or 0)))
                    self.spectrum_sync_offset_ms = max(
                        SPECTRUM_SYNC_OFFSET_MIN_MS,
                        min(SPECTRUM_SYNC_OFFSET_MAX_MS, v),
                    )
                except Exception:
                    self.spectrum_sync_offset_ms = 0
            if "playlist_only_mode" in state:
                self.playlist_only_mode = bool(state.get("playlist_only_mode"))
            if "playlist_only_maximized" in state:
                self.playlist_only_maximized = bool(
                    state.get("playlist_only_maximized")
                )
            if "playback_rate" in state:
                try:
                    r = float(state.get("playback_rate") or 1.0)
                    allowed = PLAYBACK_RATES
                    self.playback_rate = min(allowed, key=lambda x: abs(x - r))
                except Exception:
                    self.playback_rate = 1.0
            if "resume_prompt_enabled" in state:
                self.resume_prompt_enabled = bool(
                    state.get("resume_prompt_enabled")
                )
            sc = state.get("shortcuts")
            if isinstance(sc, dict):
                merged = dict(DEFAULT_SHORTCUTS)
                for k, v in sc.items():
                    if k in merged and isinstance(v, str) and v.strip():
                        merged[k] = v.strip()
                self.shortcuts = merged
        except Exception: pass

    def _css_from_palette(self, p):
        s = getattr(self, "_ui_s", None)
        if s is None:
            try:
                factor = float(getattr(self, "ui_scale", 1.0) or 1.0)
            except Exception:
                factor = 1.0
            s = lambda n, _f=factor: max(1, int(round(float(n) * _f)))
        _menu_sep_overrides = {
            "sang": "#9a4850",
            "bigtron": "#a85a18",
            "lantern": "#1a7a3c",
            "crusher": "#3a7088",
            "darth": "#8a2838",
        }
        try:
            _tn = str(getattr(self, "theme_name", "") or "").lower()
        except Exception:
            _tn = ""
        menu_sep = _menu_sep_overrides.get(_tn) or p.get("btn_border") or "#555555"
        def _hex_rgb(hx, fallback=(17, 19, 21)):
            try:
                h = (hx or "").lstrip("#")
                if len(h) >= 6: return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            except Exception: pass
            return fallback
        tr, tg, tb = _hex_rgb(p.get("tree_bg"))
        try:
            topacity = float(getattr(self, "playlist_transparency_opacity", 0.35))
        except Exception:
            topacity = 0.35
        topacity = max(0.0, min(1.0, topacity))
        try:
            win_transp = bool(getattr(self, "playlist_transparency", False))
        except Exception:
            win_transp = False
        if win_transp:
            window_bg_css = "transparent"
            entry_header_bg_css = "transparent"
        else:
            window_bg_css = p['window_bg']
            entry_header_bg_css = p['header_bg']
        _tn = (getattr(self, "theme_name", "") or "").lower()
        if _tn == "sang":
            scale_fill, scale_trough = "#c41e3a", "#3a1018"
            scale_thumb, scale_thumb_hover = "#e04050", "#ff6070"
        elif _tn == "nature":
            scale_fill, scale_trough = "#f0e6c8", "#2a4a30"
            scale_thumb, scale_thumb_hover = "#fff5dc", "#ffffff"
        elif _tn == "gdalf":
            scale_fill, scale_trough = "#ffffff", "#6a6558"
            scale_thumb, scale_thumb_hover = "#f5f1e6", "#ffffff"
        elif _tn == "darth":
            scale_fill, scale_trough = "#8a929c", "#1a1010"
            scale_thumb, scale_thumb_hover = "#b0b8c0", "#d0d6dc"
        elif _tn == "fremen":
            scale_fill, scale_trough = "#c4a06a", "#2a2014"
            scale_thumb, scale_thumb_hover = "#d4b888", "#e8d4b0"
        else:
            scale_fill, scale_trough = "#2f80ed", "#1a2a3a"
            scale_thumb, scale_thumb_hover = "#4d9fff", "#7ab8ff"
        slider_color = (
            p.get("btn_active_border")
            or p.get("tree_sel_bg")
            or p.get("title_fg")
            or "#5c737e"
        )
        slider_hover = p.get("title_fg") or p.get("btn_fg") or slider_color
        _theme = getattr(self, "theme_name", "") or ""
        _accent_scroll_themes = (
            "bigtron",
            "lantern",
            "crusher",
            "darth",
            "fremen",
            "bigmegatron",
        )
        _bb = (p.get("btn_border") or "").lower()
        if _theme in _accent_scroll_themes or _bb in ("#000000", "#000", "black"):
            scroll_slider = (
                p.get("tree_sel_bg")
                or p.get("topbar_bg")
                or p.get("btn_fg")
                or slider_color
            )
            scroll_slider_hover = p.get("btn_fg") or p.get("header_fg") or scroll_slider
            scroll_trough = p.get("btn_bg") or p.get("header_bg") or p.get("tree_bg")
        else:
            scroll_slider = p.get("btn_border") or slider_color
            scroll_slider_hover = p.get("btn_hover") or scroll_slider
            scroll_trough = p.get("header_bg") or p.get("tree_bg")
        import zlib as _z
        _tpl = _z.decompress(_CSS_TPL_Z).decode("utf-8")
        # Le template utilise les locales (window_bg_css, scale_fill, p, s, …)
        return eval("f" + repr(_tpl), dict(locals())).encode("utf-8")

    def build_css(self):
        palette = THEME_PALETTES.get(
            getattr(self, "theme_name", "sombre"),
            THEME_PALETTES["sombre"],
        )
        css = self._css_from_palette(palette)
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        screen = Gdk.Screen.get_default()
        if self._css_provider is not None:
            try:
                Gtk.StyleContext.remove_provider_for_screen(
                    screen, self._css_provider
                )
            except Exception: pass
        self._css_provider = provider
        Gtk.StyleContext.add_provider_for_screen(
            screen,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_USER,
        )

    def set_theme(self, name):
        if name not in THEME_PALETTES: return
        if name == getattr(self, "theme_name", None): return
        self.theme_name = name
        self.build_css()
        try: self.save_state()
        except Exception: pass
        self.status_label.set_text(
            self.tr("Thèmes") + " : " + self.tr(THEME_LABELS.get(name, name))
        )

    def _translate_dialog_action_buttons(self, dialog):
        if dialog is None: return
        def _norm(text):
            return (text or "").replace("_", "").strip().casefold()
        known = {
            "ok": self.tr("Valider"),
            "OK": self.tr("Valider"),
            "gtk-ok": self.tr("Valider"),
            "validate": self.tr("Valider"),
            "valider": self.tr("Valider"),
            "apply": self.tr("Appliquer"),
            "appliquer": self.tr("Appliquer"),
            "cancel": self.tr("Annuler"),
            "annuler": self.tr("Annuler"),
            "gtk-cancel": self.tr("Annuler"),
            "close": self.tr("Fermer"),
            "fermer": self.tr("Fermer"),
            "gtk-close": self.tr("Fermer"),
            "open": self.tr("Ouvrir"),
            "ouvrir": self.tr("Ouvrir"),
            "gtk-open": self.tr("Ouvrir"),
            "öffnen": self.tr("Ouvrir"),
            "abrir": self.tr("Ouvrir"),
            "apri": self.tr("Ouvrir"),
            "openen": self.tr("Ouvrir"),
            "otwórz": self.tr("Ouvrir"),
            "öppna": self.tr("Ouvrir"),
            "открыть": self.tr("Ouvrir"),
            "save": self.tr("Enregistrer"),
            "enregistrer": self.tr("Enregistrer"),
            "select": self.tr("Valider"),
            "sélectionner": self.tr("Valider"),
            "select color": self.tr("Valider"),
            "sélectionner la couleur": self.tr("Valider"),
            "select colour": self.tr("Valider"),
        }
        for msgid, our in (
            ("_OK", "Valider"),
            ("_Cancel", "Annuler"),
            ("_Close", "Fermer"),
            ("_Open", "Ouvrir"),
            ("_Save", "Enregistrer"),
            ("Select", "Valider"),
            ("_Select", "Valider"),
        ):
            try:
                native = GLib.dgettext("gtk30", msgid)
                if native:
                    known[_norm(native)] = self.tr(our)
            except Exception: pass
        buttons = []
        def _walk(w):
            if isinstance(w, Gtk.Button):
                buttons.append(w)
            elif isinstance(w, Gtk.Container):
                for c in w.get_children():
                    _walk(c)
        try: _walk(dialog)
        except Exception: pass
        try:
            area = dialog.get_action_area()
            if area is not None:
                _walk(area)
        except Exception: pass
        seen = set()
        for btn in buttons:
            if id(btn) in seen: continue
            seen.add(id(btn))
            try:
                lab = btn.get_label()
            except Exception:
                lab = None
            if not lab: continue
            mapped = known.get(_norm(lab))
            if mapped:
                try: btn.set_label(mapped)
                except Exception: pass
            try:
                resp = dialog.get_response_for_widget(btn)
                nlab = _norm(lab)
                if resp == Gtk.ResponseType.OK:
                    if nlab in (
                        "open", "ouvrir", "gtk-open", "öffnen", "abrir",
                        "apri", "openen", "otwórz", "öppna", "открыть",
                    ) or nlab.startswith("open") or "ouvrir" in nlab:
                        btn.set_label(self.tr("Ouvrir"))
                    elif nlab in (
                        "ok", "gtk-ok", "select", "select color", "select colour",
                        "sélectionner", "sélectionner la couleur", "valider",
                    ):
                        btn.set_label(self.tr("Valider"))
                    elif nlab in ("save", "enregistrer", "gtk-save"):
                        btn.set_label(self.tr("Enregistrer"))
                elif resp == Gtk.ResponseType.CANCEL:
                    btn.set_label(self.tr("Annuler"))
                elif resp == Gtk.ResponseType.CLOSE:
                    btn.set_label(self.tr("Fermer"))
                elif resp == Gtk.ResponseType.ACCEPT:
                    if nlab in ("open", "ouvrir", "gtk-open") or "open" in nlab:
                        btn.set_label(self.tr("Ouvrir"))
            except Exception: pass

    def _style_app_dialog(self, widget):
        if widget is None: return
        if getattr(widget, "_app_dialog_chrome", False): return
        try: widget._app_dialog_chrome = True
        except Exception: pass
        try: widget.get_style_context().add_class("app-dialog")
        except Exception: pass
        try: widget.set_decorated(False)
        except Exception: pass
        try: widget.set_border_width(0)
        except Exception: pass
        title = ""
        try:
            title = widget.get_title() or ""
        except Exception:
            title = ""
        header = Gtk.EventBox()
        try: header.get_style_context().add_class("app-dialog-header")
        except Exception: pass
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        try:
            header_box.set_margin_start(self._ui_s(6))
            header_box.set_margin_end(self._ui_s(4))
            header_box.set_margin_top(self._ui_s(2))
            header_box.set_margin_bottom(self._ui_s(2))
        except Exception: pass
        header.add(header_box)
        title_lbl = Gtk.Label(label=title)
        title_lbl.set_xalign(0)
        title_lbl.set_hexpand(True)
        title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        try: title_lbl.get_style_context().add_class("app-dialog-title")
        except Exception: pass
        header_box.pack_start(title_lbl, True, True, 0)
        close_btn = Gtk.Button(label="×")
        try: close_btn.get_style_context().add_class("app-dialog-close")
        except Exception: pass
        close_btn.set_relief(Gtk.ReliefStyle.NONE)
        close_btn.set_can_focus(False)
        try: close_btn.set_tooltip_text(self.tr("Fermer"))
        except Exception: pass
        def _close(_btn=None):
            try:
                if isinstance(widget, Gtk.Dialog):
                    widget.response(Gtk.ResponseType.CLOSE)
                    return
            except Exception: pass
            try: widget.destroy()
            except Exception: pass
        close_btn.connect("clicked", _close)
        header_box.pack_end(close_btn, False, False, 0)
        def _on_header_press(_w, event):
            try:
                if event.type != Gdk.EventType.BUTTON_PRESS: return False
                if int(getattr(event, "button", 0) or 0) not in (1, 2, 3): return False
                widget.begin_move_drag(
                    int(event.button),
                    int(event.x_root),
                    int(event.y_root),
                    int(event.time),
                )
                return True
            except Exception:
                return False
        try:
            header.add_events(
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.BUTTON1_MOTION_MASK
            )
            header.connect("button-press-event", _on_header_press)
        except Exception: pass
        try:
            if isinstance(widget, Gtk.Dialog):
                area = widget.get_content_area()
                if area is not None:
                    try: area.get_style_context().add_class("app-dialog")
                    except Exception: pass
                    try: area.set_border_width(0)
                    except Exception: pass
                    area.pack_start(header, False, False, 0)
                    area.reorder_child(header, 0)
                    try:
                        for child in area.get_children():
                            if child is header: continue
                            try:
                                child.set_margin_start(self._ui_s(10))
                                child.set_margin_end(self._ui_s(10))
                                child.set_margin_top(self._ui_s(8))
                                child.set_margin_bottom(self._ui_s(8))
                            except Exception: pass
                    except Exception: pass
                try:
                    action = widget.get_action_area()
                    if action is not None:
                        action.get_style_context().add_class("app-dialog")
                        try:
                            action.set_margin_start(self._ui_s(8))
                            action.set_margin_end(self._ui_s(8))
                            action.set_margin_bottom(self._ui_s(8))
                        except Exception: pass
                except Exception: pass
            else:
                child = None
                try:
                    child = widget.get_child()
                except Exception:
                    child = None
                if child is not None:
                    try:
                        widget.remove(child)
                    except Exception:
                        child = None
                shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                try: shell.get_style_context().add_class("app-dialog")
                except Exception: pass
                shell.pack_start(header, False, False, 0)
                if child is not None:
                    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                    try: body.get_style_context().add_class("app-dialog-body")
                    except Exception: pass
                    try:
                        body.set_border_width(0)
                        child.set_margin_start(self._ui_s(4))
                        child.set_margin_end(self._ui_s(4))
                        child.set_margin_bottom(self._ui_s(4))
                    except Exception: pass
                    body.pack_start(child, True, True, 0)
                    shell.pack_start(body, True, True, 0)
                widget.add(shell)
        except Exception as exc:
            print("app dialog chrome:", exc, file=sys.stderr)
        try: header.show_all()
        except Exception: pass
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
        top_wrap = Gtk.EventBox()
        try: top_wrap.get_style_context().add_class("chrome-drag")
        except Exception: pass
        try: top_wrap.set_above_child(False)
        except Exception: pass
        top_wrap.add(top)
        top_wrap.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON1_MOTION_MASK
            | Gdk.EventMask.BUTTON3_MOTION_MASK
        )
        top_wrap.connect("button-press-event", self._on_chrome_press_for_move)
        self.top_bar_wrap = top_wrap
        main.pack_start(top_wrap, False, False, 0)
        self._build_mini_title_marquee(main)
        self._build_nano_playlist_panel(main)
        try: self._update_undecorated_move_hint()
        except Exception: pass
        menu = Gtk.Button(label="≡")
        menu.get_style_context().add_class("menu-button")
        menu.connect("clicked", self.show_menu)
        top.pack_start(menu, False, False, 0)
        button = self.make_button("+", self.tr("Ajout des fichiers"))
        self.add_files_button = button
        button.get_style_context().add_class("add-control")
        button.connect("clicked", self._on_add_button_clicked)
        top.pack_start(button, False, False, 0)
        button = self.make_button("●", self.tr("Enregistrement"))
        self.record_button = button
        button.connect("clicked", self.toggle_recording)
        top.pack_start(button, False, False, 0)
        try: self._update_record_button_state()
        except Exception: pass
        button = self.make_button("◀◀", self.tr("Morceau précédent"))
        self.previous_button = button
        button.connect("clicked", self.previous)
        top.pack_start(button, False, False, 0)
        self.play_button = self.make_button("▶", self.tr("Lecture / Pause"))
        self.play_button.get_style_context().add_class("play-control")
        self.play_button.connect("clicked", self.toggle_play)
        top.pack_start(self.play_button, False, False, 0)
        button = self.make_button("▶▶", self.tr("Morceau suivant"))
        self.next_button = button
        button.connect("clicked", self.next)
        top.pack_start(button, False, False, 0)
        button = self.make_button("■", self.tr("Stop"))
        self.stop_button = button
        button.connect("clicked", self.stop)
        top.pack_start(button, False, False, 0)
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
        self.time_label = Gtk.Label(label="0:00 • 0:00")
        self.time_label.get_style_context().add_class("display-time")
        try:
            self.time_label.set_events(
                self.time_label.get_events() | Gdk.EventMask.BUTTON_PRESS_MASK
            )
            self.time_label.connect(
                "button-press-event", self._on_chrome_press_for_move
            )
        except Exception: pass
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
        self.bluetooth_button = self.make_button(
            "ᛒ",  # rune Bjarkan (origine du logo Bluetooth) — suit btn_fg
            self.tr("Bluetooth")
            + " — "
            + self.tr("Ouvrir les réglages Bluetooth du système"),
        )
        self.bluetooth_button.connect("clicked", self.open_system_bluetooth)
        top.pack_start(self.bluetooth_button, False, False, 2)
        self.content_paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        main.pack_start(self.content_paned, True, True, 0)
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
        self.fs_toast_tl = Gtk.Label(label="")
        self.fs_toast_tl.set_halign(Gtk.Align.START)
        self.fs_toast_tl.set_valign(Gtk.Align.START)
        self.fs_toast_tl.set_margin_start(18)
        self.fs_toast_tl.set_margin_top(16)
        self.fs_toast_tl.set_no_show_all(True)
        self.fs_toast_tl.hide()
        try: self.fs_toast_tl.get_style_context().add_class("fs-toast")
        except Exception: pass
        self.video_overlay.add_overlay(self.fs_toast_tl)
        self.fs_toast_tr = Gtk.Label(label="")
        self.fs_toast_tr.set_halign(Gtk.Align.END)
        self.fs_toast_tr.set_valign(Gtk.Align.START)
        self.fs_toast_tr.set_margin_end(18)
        self.fs_toast_tr.set_margin_top(16)
        self.fs_toast_tr.set_no_show_all(True)
        self.fs_toast_tr.hide()
        try: self.fs_toast_tr.get_style_context().add_class("fs-toast")
        except Exception: pass
        self.video_overlay.add_overlay(self.fs_toast_tr)
        self._fs_toast_tl_timeout = 0
        self._fs_toast_tr_timeout = 0
        self._build_fullscreen_controls()
        self.content_paned.pack1(self.video_overlay, True, True)
        self.connect("window-state-event", self.on_window_state_event)
        self.connect("configure-event", self._on_main_configure_for_fs_popup)
        self.connect("focus-in-event", self.on_window_focus_in)
        self.connect("map-event", self.on_window_map)
        playlist_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        playlist_box.set_margin_start(8)
        playlist_box.set_margin_end(8)
        try: playlist_box.get_style_context().add_class("playlist-box")
        except Exception: pass
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
        self.playlist_scroll = scroll
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        try: scroll.get_style_context().add_class("playlist-scroll")
        except Exception: pass
        try:
            scroll.set_propagate_natural_height(False)
            scroll.set_propagate_natural_width(False)
        except Exception: pass
        try: scroll.set_overlay_scrolling(False)
        except Exception: pass
        playlist_box.pack_start(scroll, True, True, 0)
        self.playlist_scroll = scroll
        self.playlist_filter = self.playlist.filter_new()
        self.playlist_filter.set_visible_func(self.playlist_filter_func)
        self.tree = Gtk.TreeView(model=self.playlist_filter)
        self.tree.set_hexpand(True)
        self.tree.set_vexpand(True)
        try:
            self.tree.set_vadjustment(scroll.get_vadjustment())
            self.tree.set_hadjustment(scroll.get_hadjustment())
        except Exception: pass
        self.create_columns()
        self.tree.connect("row-activated", self.row_activated)
        self.tree.connect("button-press-event", self.on_tree_button_press)
        self.tree.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        self.tree.get_selection().connect("changed", self.selection_changed)
        scroll.add(self.tree)
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        bottom.get_style_context().add_class("bottom")
        bottom.set_border_width(8)
        self.bottom_bar = bottom
        bottom_wrap = Gtk.EventBox()
        try: bottom_wrap.get_style_context().add_class("chrome-drag")
        except Exception: pass
        try: bottom_wrap.set_above_child(False)
        except Exception: pass
        bottom_wrap.add(bottom)
        bottom_wrap.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON1_MOTION_MASK
            | Gdk.EventMask.BUTTON3_MOTION_MASK
        )
        bottom_wrap.connect("button-press-event", self._on_chrome_press_for_move)
        self.bottom_bar_wrap = bottom_wrap
        main.pack_start(bottom_wrap, False, False, 0)
        self.cover = Gtk.Image()
        self.cover.set_size_request(self._ui_s(105), self._ui_s(105))
        self.cover.get_style_context().add_class("cover")
        self.set_default_cover()
        bottom.pack_end(self.cover, False, False, 0)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        try:
            info.set_margin_start(self._ui_s(4))
        except Exception:
            info.set_margin_start(14)
        try:
            info.set_margin_top(self._ui_s(4))
        except Exception:
            info.set_margin_top(4)
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
        for _lbl in (self.bottom_title, self.bottom_artist, self.bottom_album):
            try:
                _lbl.set_events(
                    _lbl.get_events() | Gdk.EventMask.BUTTON_PRESS_MASK
                )
                _lbl.connect("button-press-event", self._on_chrome_press_for_move)
            except Exception: pass
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

    def _refresh_playlist_current_style(self):
        try:
            if getattr(self, "tree", None) is not None:
                self.tree.queue_draw()
        except Exception: pass
        try:
            nt = getattr(self, "nano_playlist_tree", None)
            if nt is not None and nt.get_visible():
                nt.queue_draw()
        except Exception: pass

    def _path_is_missing(self, path):
        if not path or not isinstance(path, str) or is_special_source(path): return False
        now = time.monotonic()
        hit = getattr(self, "_missing_cache", {}).get(path)
        if hit and now - hit[1] < 5.0: return hit[0]
        missing = not os.path.isfile(path)
        if not hasattr(self, "_missing_cache") or self._missing_cache is None:
            self._missing_cache = {}
        self._missing_cache[path] = (missing, now)
        if len(self._missing_cache) > 1500:
            try:
                drop = len(self._missing_cache) // 4
                for _ in range(drop):
                    self._missing_cache.pop(next(iter(self._missing_cache)))
            except Exception:
                self._missing_cache.clear()
        return missing

    def _playlist_cell_data(self, column, cell, model, iterator, col_id):
        try:
            path = model.get_value(iterator, self.COL_PATH)
            missing = self._path_is_missing(path)
            text = model.get_value(iterator, col_id)
            text = "" if text is None else str(text)
            if missing and col_id == self.COL_TITLE:
                text = "⚠ " + text
            cell.set_property("text", text)
            is_current = (
                path
                and path == getattr(self, "current_path", None)
                and bool(getattr(self, "is_playing", False) or getattr(self, "is_paused", False))
            )
            try:
                cell.set_property(
                    "weight",
                    Pango.Weight.BOLD if is_current else Pango.Weight.NORMAL,
                )
            except Exception:
                try:
                    cell.set_property(
                        "weight",
                        700 if is_current else 400,
                    )
                except Exception: pass
            if missing:
                cell.set_property("foreground", "#a06060")
                try: cell.set_property("style", Pango.Style.ITALIC)
                except Exception: pass
            else:
                cell.set_property("foreground-set", False)
                try: cell.set_property("style", Pango.Style.NORMAL)
                except Exception: pass
        except Exception: pass

    def playlist_filter_func(self, model, iterator, data):
        if not self.search_text: return True
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

    def setup_drag_and_drop(self):
        targets = [Gtk.TargetEntry.new("text/uri-list", 0, 0)]
        self.drag_dest_set(Gtk.DestDefaults.ALL, targets, Gdk.DragAction.COPY)
        self.connect("drag-data-received", self.on_drag_data_received)
        self._setup_playlist_reorder()

    def on_drag_data_received(self, widget, context, x, y, selection, info, timestamp):
        try:
            self._clear_playlist_search()
        except Exception:
            self.search_text = ""
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
        try: context.finish(True, False, timestamp)
        except Exception: pass
    def _setup_playlist_reorder(self):
        if not hasattr(self, "tree") or self.tree is None: return
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
            self.tree.connect(
                "button-release-event", self._on_playlist_reorder_release
            )
            self.tree.connect(
                "motion-notify-event", self._on_playlist_reorder_motion
            )
        except Exception as e:
            print("Playlist reorder setup:", e, file=sys.stderr)

    def _filter_path_to_child_index(self, fpath):
        if fpath is None: return None
        try:
            cpath = self.playlist_filter.convert_path_to_child_path(fpath)
            if cpath is None: return None
            return cpath.get_indices()[0]
        except Exception:
            return None

    def _playlist_collect_selected_indices(self, tree, prefer_idx=None):
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
        self._pl_src_child_index = None
        self._pl_src_indices = []
        self._pl_drag_armed = False
        self._pl_start_y = float(event.y)
        self._pl_suppress_select = False
        hit = tree.get_path_at_pos(int(event.x), int(event.y))
        if hit is None: return False
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
            except Exception: pass
        if idx is not None:
            self._pl_src_child_index = int(idx)
            self._pl_src_indices = [int(idx)]
        return False

    def _on_playlist_reorder_motion(self, tree, event):
        if not (event.state & Gdk.ModifierType.BUTTON1_MASK): return False
        if self._pl_src_child_index is None and not self._pl_src_indices: return False
        if abs(float(event.y) - self._pl_start_y) <= 8: return False
        if not self._pl_drag_armed:
            try:
                self._pl_src_indices = self._playlist_collect_selected_indices(
                    tree, prefer_idx=self._pl_src_child_index
                )
            except Exception: pass
            self._pl_drag_armed = True
        return False

    def _on_playlist_reorder_release(self, tree, event):
        if event.button != 1: return False
        src = self._pl_src_child_index
        src_indices = list(self._pl_src_indices or [])
        armed = self._pl_drag_armed
        self._pl_src_child_index = None
        self._pl_src_indices = []
        self._pl_drag_armed = False
        self._pl_suppress_select = False
        if not armed: return False
        if not src_indices and src is not None:
            src_indices = [int(src)]
        if not src_indices: return False
        hit = tree.get_path_at_pos(int(event.x), int(event.y))
        if hit is None: return False
        dest = self._filter_path_to_child_index(hit[0])
        if dest is None: return False
        try:
            cell_y = hit[3] if len(hit) > 3 else 0
            col = hit[1]
            cell_h = 24
            if col is not None:
                cell_h = max(1, tree.get_background_area(hit[0], col).height)
            if cell_y >= cell_h / 2:
                dest = dest + 1
        except Exception: pass
        if len(src_indices) == 1 and dest in (
            src_indices[0],
            src_indices[0] + 1,
        ):
            return False
        GLib.idle_add(self._playlist_move_indices, list(src_indices), int(dest))
        return False

    def _playlist_move_indices(self, src_indices, dest_idx):
        try:
            n = self.playlist.iter_n_children(None)
            if n <= 1: return False
            src_set = {int(i) for i in src_indices if 0 <= int(i) < n}
            src_indices = sorted(src_set)
            if not src_indices: return False
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
            except Exception: pass
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
            except Exception: pass
            try: self.save_playlist()
            except Exception: pass
        except Exception as e:
            print("Playlist move:", e, file=sys.stderr)
        return False

    def setup_keyboard(self):
        self.connect("key-press-event", self.on_key_press)

    def _normalize_key_name(self, key):
        if not key: return ""
        aliases = {
            "space": "space", "Space": "space",
            "Left": "Left", "Right": "Right", "Up": "Up", "Down": "Down",
            "uparrow": "Up", "downarrow": "Down",
            "KP_Up": "Up", "KP_Down": "Down",
            "Page_Up": "Page_Up", "Page_Down": "Page_Down",
            "Escape": "Escape", "Esc": "Escape",
            "Delete": "Delete", "BackSpace": "BackSpace",
        }
        if key in aliases: return aliases[key]
        if len(key) == 1: return key.lower()
        if key.startswith("F") and key[1:].isdigit(): return key.upper() if key[1:] else key
        if key.lower().startswith("f") and key[1:].isdigit(): return "F" + key[1:]
        return key

    def _event_to_shortcut(self, event):
        try:
            keyval = Gdk.keyval_to_lower(event.keyval)
        except Exception:
            keyval = event.keyval
        key = Gdk.keyval_name(keyval)
        if key is None:
            key = Gdk.keyval_name(event.keyval)
        if key is None: return ""
        key = self._normalize_key_name(key)
        parts = []
        state = int(getattr(event, "state", 0) or 0)
        if state & Gdk.ModifierType.CONTROL_MASK:
            parts.append("Ctrl")
        if state & Gdk.ModifierType.SHIFT_MASK:
            if not (len(key) == 1 and key.isalpha()):
                parts.append("Shift")
        alt_mask = int(Gdk.ModifierType.MOD1_MASK)
        try: alt_mask |= int(Gdk.ModifierType.META_MASK)
        except Exception: pass
        if state & alt_mask:
            parts.append("Alt")
        parts.append(key)
        return "+".join(parts)

    def _shortcut_matches(self, action, event_str, key_name):
        binding = (self.shortcuts or DEFAULT_SHORTCUTS).get(action, "")
        if not binding:
            binding = DEFAULT_SHORTCUTS.get(action, "")
        if not binding: return False
        binding = binding.strip()
        if binding.lower() == (event_str or "").lower(): return True
        return False

    def _is_text_input_focused(self):
        try:
            w = self.get_focus()
        except Exception:
            return False
        if w is None: return False
        try:
            if isinstance(w, (Gtk.Entry, Gtk.TextView, Gtk.SpinButton)): return True
        except Exception: pass
        try:
            name = type(w).__name__
            if name in ("Entry", "SearchEntry", "TextView", "SpinButton"): return True
        except Exception: pass
        return False

    def on_key_press(self, widget, event):
        key = Gdk.keyval_name(event.keyval)
        if key is None: return False
        key_n = self._normalize_key_name(key)
        event_str = self._event_to_shortcut(event)
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
        if key_n in ("Escape", "Esc") and self.is_fullscreen:
            self.toggle_video_fullscreen()
            return True
        is_quit = self._shortcut_matches("quit", event_str, key_n) or (
            event_str.lower() in ("alt+q",)
        )
        is_reset = self._shortcut_matches("factory_reset", event_str, key_n) or (
            event_str.lower() in ("ctrl+alt+x",)
        )
        is_pl_menu = self._shortcut_matches("playlist_menu", event_str, key_n) or (
            event_str.lower() in ("ctrl+p",)
        )
        is_focus_search = self._shortcut_matches("focus_search", event_str, key_n) or (
            event_str.lower() in ("ctrl+r",)
        )
        is_toggle_rec = self._shortcut_matches("toggle_recording", event_str, key_n) or (
            event_str.lower() in ("ctrl+s",)
        )
        is_toggle_po = self._shortcut_matches("toggle_playlist_only", event_str, key_n) or (
            event_str.lower() in ("ctrl+n",)
        )
        if is_quit:
            try:
                self.close()
            except Exception:
                try: Gtk.main_quit()
                except Exception: pass
            return True
        if is_reset:
            self.factory_reset_and_quit()
            return True
        if is_pl_menu:
            self._popup_playlist_menu_from_shortcut()
            return True
        if is_focus_search:
            self.focus_search_entry()
            return True
        if is_toggle_rec:
            self.toggle_recording()
            return True
        if is_toggle_po:
            self.set_playlist_only_mode(
                not bool(getattr(self, "playlist_only_mode", False))
            )
            return True
        if self._is_text_input_focused(): return False
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
        if self._shortcut_matches("screenshot", event_str, key_n):
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
            if event.state & Gdk.ModifierType.SHIFT_MASK: return False
            self.move_playlist_selection(-1)
            return True
        if self._shortcut_matches("playlist_down", event_str, key_n):
            if event.state & Gdk.ModifierType.SHIFT_MASK: return False
            self.move_playlist_selection(1)
            return True
        if self._shortcut_matches("delete", event_str, key_n):
            self.remove_selected()
            return True
        if self._shortcut_matches("clear_playlist", event_str, key_n):
            self.clear_playlist()
            return True
        if self._shortcut_matches("quit", event_str, key_n):
            self.close()
            return True
        if self._shortcut_matches("factory_reset", event_str, key_n):
            self.factory_reset_and_quit()
            return True
        if self._shortcut_matches("playlist_menu", event_str, key_n):
            self._popup_playlist_menu_from_shortcut()
            return True
        if self._shortcut_matches("focus_search", event_str, key_n):
            self.focus_search_entry()
            return True
        if self._shortcut_matches("toggle_recording", event_str, key_n):
            self.toggle_recording()
            return True
        if self._shortcut_matches("toggle_playlist_only", event_str, key_n):
            self.set_playlist_only_mode(
                not bool(getattr(self, "playlist_only_mode", False))
            )
            return True
        return False

    def focus_search_entry(self):
        entry = getattr(self, "search_entry", None)
        if entry is None: return
        try:
            row = getattr(self, "search_row", None)
            if row is not None:
                row.show()
            entry.show()
        except Exception: pass
        try:
            entry.grab_focus()
            try: entry.select_region(0, -1)
            except Exception: pass
        except Exception as exc:
            print("focus_search:", exc, file=sys.stderr)

    def _on_add_button_clicked(self, button):
        self._popup_add_media_menu(button)

    def add_files(self, *args):
        self._add_files_dialog()

    def add_folder(self, *args):
        self._add_folder_dialog()

    def _popup_add_media_menu(self, widget=None):
        old = getattr(self, "_add_media_menu", None)
        if old is not None:
            try: old.popdown()
            except Exception: pass
            try: old.destroy()
            except Exception: pass
            self._add_media_menu = None
        menu = Gtk.Menu()
        self._add_media_menu = menu
        item_files = Gtk.MenuItem(label=self.tr("Fichiers…"))
        item_files.connect(
            "activate", lambda *a: self._add_files_dialog()
        )
        menu.append(item_files)
        item_folder = Gtk.MenuItem(label=self.tr("Dossier…"))
        item_folder.connect(
            "activate", lambda *a: self._add_folder_dialog()
        )
        menu.append(item_folder)
        def _on_deactivate(m):
            if getattr(self, "_add_media_menu", None) is m:
                self._add_media_menu = None
            try:
                GLib.idle_add(m.destroy)
            except Exception:
                try: m.destroy()
                except Exception: pass
            return False
        menu.connect("deactivate", _on_deactivate)
        menu.show_all()
        try:
            if widget is not None:
                menu.popup_at_widget(
                    widget,
                    Gdk.Gravity.SOUTH_WEST,
                    Gdk.Gravity.NORTH_WEST,
                    None,
                )
            else:
                menu.popup_at_pointer(None)
        except Exception:
            try:
                menu.popup(None, None, None, None, 0, Gtk.get_current_event_time())
            except Exception as exc:
                print("add media menu:", exc, file=sys.stderr)

    def _add_files_dialog(self, *args):
        dialog = Gtk.FileChooserDialog(
            title=self.tr("Ajouter des fichiers"),
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_button(self.tr("Annuler"), Gtk.ResponseType.CANCEL)
        dialog.add_button(self.tr("Ouvrir"), Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.set_select_multiple(True)
        try: dialog.set_local_only(False)
        except Exception: pass
        try:
            filt_media = Gtk.FileFilter()
            filt_media.set_name(self.tr("Fichiers multimédias"))
            for ext in sorted(AUDIO_EXTENSIONS | VIDEO_EXTENSIONS):
                filt_media.add_pattern("*" + ext)
                filt_media.add_pattern("*" + ext.upper())
            dialog.add_filter(filt_media)
            filt_all = Gtk.FileFilter()
            filt_all.set_name(self.tr("Tous les fichiers"))
            filt_all.add_pattern("*")
            dialog.add_filter(filt_all)
            dialog.set_filter(filt_media)
        except Exception: pass
        try: self._translate_dialog_action_buttons(dialog)
        except Exception: pass
        paths = []
        try:
            if dialog.run() == Gtk.ResponseType.OK:
                paths = list(dialog.get_filenames() or [])
        finally:
            dialog.destroy()
        if paths:
            self._ingest_media_paths(paths)

    def _add_folder_dialog(self, *args):
        dialog = Gtk.FileChooserDialog(
            title=self.tr("Ajouter un dossier"),
            parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dialog.add_button(self.tr("Annuler"), Gtk.ResponseType.CANCEL)
        dialog.add_button(self.tr("Ouvrir"), Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        try: self._translate_dialog_action_buttons(dialog)
        except Exception: pass
        folder = None
        try:
            if dialog.run() == Gtk.ResponseType.OK:
                folder = dialog.get_filename()
        finally:
            dialog.destroy()
        if folder:
            self.add_directory(folder)

    def _ingest_media_paths(self, paths):
        try:
            self._clear_playlist_search()
        except Exception:
            self.search_text = ""
        added_files = []
        directories = []
        for raw in paths:
            if not raw: continue
            try:
                p = (
                    os.path.abspath(os.path.expanduser(raw))
                    if not is_stream(raw)
                    else raw
                )
            except Exception:
                p = raw
            try:
                if is_stream(p):
                    self.add_file(p, save=False, scroll_to=False)
                    added_files.append(p)
                elif os.path.isdir(p):
                    directories.append(p)
                elif is_audio_file(p) or is_video_file(p):
                    self.add_file(p, save=False, scroll_to=False)
                    added_files.append(p)
            except Exception: pass
        for directory in directories:
            try: self.add_directory(directory)
            except Exception: pass
        if added_files:
            try: self.save_playlist()
            except Exception: pass
            try:
                GLib.idle_add(self._refresh_playlist_scroll_adjustments)
                GLib.idle_add(self._scroll_playlist_to_end)
            except Exception: pass
            if len(added_files) == 1 and not directories:
                try: self.play_file(added_files[0])
                except Exception: pass
    def _update_record_button_state(self):
        btn = getattr(self, "record_button", None)
        if btn is None: return
        try:
            can = bool(
                self.current_path
                and is_stream(self.current_path)
                and (self.is_playing or self.is_paused or self._recording)
            )
            btn.set_sensitive(can or self._recording)
        except Exception: pass
        try:
            if self._recording:
                btn.set_label("◼")
                btn.set_tooltip_text(self.tr("Arrêter l'enregistrement"))
                try: btn.get_style_context().add_class("recording")
                except Exception: pass
            else:
                btn.set_label("●")
                btn.set_tooltip_text(self.tr("Enregistrement"))
                try: btn.get_style_context().remove_class("recording")
                except Exception: pass
        except Exception: pass

    def toggle_recording(self, *args):
        if getattr(self, "_recording", False):
            self._stop_recording(show_save_dialog=True)
        else:
            self._start_recording()

    def _start_recording(self):
        path = getattr(self, "current_path", None)
        if not path or not is_stream(path):
            try:
                self.status_label.set_text(
                    self.tr("Enregistrement disponible uniquement pour les flux")
                )
            except Exception: pass
            return
        if not (self.is_playing or self.is_paused):
            try:
                self.status_label.set_text(
                    self.tr("Lancez d'abord la lecture du flux")
                )
            except Exception: pass
            return
        if self._recording: return
        try: self._destroy_record_pipeline()
        except Exception: pass
        import tempfile
        try:
            cache_root = GLib.get_user_cache_dir()
            rec_dir = os.path.join(cache_root or os.path.expanduser("~/.cache"), APP_NAME)
            os.makedirs(rec_dir, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                prefix="myostoox_rec_", suffix=".mp3", dir=rec_dir
            )
            os.close(fd)
        except Exception as exc:
            print("record temp:", exc, file=sys.stderr)
            try: self.status_label.set_text(self.tr("Enregistrement impossible"))
            except Exception: pass
            return
        self._record_temp_path = tmp
        uri = path if is_stream(path) else path_to_uri(path)
        audio_bin = None
        try:
            convert = Gst.ElementFactory.make("audioconvert", "rec_convert")
            resample = Gst.ElementFactory.make("audioresample", "rec_resample")
            queue = Gst.ElementFactory.make("queue", "rec_queue")
            enc = Gst.ElementFactory.make("lamemp3enc", "rec_enc")
            sink = Gst.ElementFactory.make("filesink", "rec_sink")
            if not all((convert, resample, queue, enc, sink)):
                missing = []
                if convert is None:
                    missing.append("audioconvert")
                if resample is None:
                    missing.append("audioresample")
                if queue is None:
                    missing.append("queue")
                if enc is None:
                    missing.append("lamemp3enc")
                if sink is None:
                    missing.append("filesink")
                raise RuntimeError(
                    "éléments manquants : " + ", ".join(missing)
                )
            try:
                queue.set_property("max-size-buffers", 0)
                queue.set_property("max-size-time", 3 * Gst.SECOND)
                queue.set_property("max-size-bytes", 4 * 1024 * 1024)
                queue.set_property("leaky", 0)  # pas de drop silencieux
            except Exception: pass
            try:
                enc.set_property("target", 1)
            except Exception:
                try: enc.set_property("target", "bitrate")
                except Exception: pass
            try: enc.set_property("bitrate", 320)
            except Exception: pass
            try: enc.set_property("cbr", True)
            except Exception: pass
            sink.set_property("location", tmp)
            try: sink.set_property("sync", False)
            except Exception: pass
            try: sink.set_property("async", False)
            except Exception: pass
            audio_bin = Gst.Bin.new("rec_audio_bin")
            for el in (convert, resample, queue, enc, sink):
                audio_bin.add(el)
            if not convert.link(resample):
                raise RuntimeError("link convert→resample")
            if not resample.link(queue):
                raise RuntimeError("link resample→queue")
            if not queue.link(enc):
                raise RuntimeError("link queue→enc")
            if not enc.link(sink):
                raise RuntimeError("link enc→sink")
            sink_pad = convert.get_static_pad("sink")
            if sink_pad is None:
                raise RuntimeError("pad sink convert")
            ghost = Gst.GhostPad.new("sink", sink_pad)
            if not audio_bin.add_pad(ghost):
                raise RuntimeError("ghost pad")
        except Exception as exc:
            print("record audio bin:", exc, file=sys.stderr)
            audio_bin = None
        if audio_bin is None:
            try:
                self.status_label.set_text(
                    self.tr("Enregistrement impossible")
                    + " (lamemp3enc ?)"
                )
            except Exception: pass
            try: os.unlink(tmp)
            except Exception: pass
            self._record_temp_path = None
            return
        try:
            rec = Gst.ElementFactory.make("playbin", "recorder")
            if rec is None:
                raise RuntimeError("playbin recorder introuvable")
            rec.set_property("uri", uri)
            try:
                flags = int(rec.get_property("flags"))
                flags = (flags | 2)   # AUDIO
                flags = flags & ~1   # pas de VIDEO
                flags = flags & ~4   # pas de sous-titres
                flags = flags & ~8   # pas de visualisation
                rec.set_property("flags", flags)
            except Exception: pass
            try:
                vfake = Gst.ElementFactory.make("fakesink", "rec_vfake")
                if vfake is not None:
                    try: vfake.set_property("sync", False)
                    except Exception: pass
                    try: vfake.set_property("async", False)
                    except Exception: pass
                    rec.set_property("video-sink", vfake)
            except Exception: pass
            rec.set_property("audio-sink", audio_bin)
            try: rec.set_property("volume", 1.0)
            except Exception: pass
            try: rec.set_property("mute", False)
            except Exception: pass
            self._record_pipeline = rec
        except Exception as exc:
            print("record playbin:", exc, file=sys.stderr)
            try: os.unlink(tmp)
            except Exception: pass
            self._record_temp_path = None
            self._record_pipeline = None
            try: self.status_label.set_text(self.tr("Enregistrement impossible"))
            except Exception: pass
            return
        try:
            bus = self._record_pipeline.get_bus()
            bus.add_signal_watch()
            self._record_bus_watch_id = bus.connect(
                "message", self._on_record_bus_message
            )
        except Exception as exc:
            print("record bus:", exc, file=sys.stderr)
        ret = self._record_pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            try: self._destroy_record_pipeline()
            except Exception: pass
            try:
                if self._record_temp_path and os.path.exists(self._record_temp_path):
                    os.unlink(self._record_temp_path)
            except Exception: pass
            self._record_temp_path = None
            try: self.status_label.set_text(self.tr("Enregistrement impossible"))
            except Exception: pass
            return
        self._recording = True
        self._update_record_button_state()
        try: self.status_label.set_text(self.tr("Enregistrement en cours…"))
        except Exception: pass
    def _on_record_bus_message(self, bus, message):
        try:
            t = message.type
        except Exception:
            return
        if t == Gst.MessageType.ERROR:
            err, dbg = message.parse_error()
            print("record error:", err, dbg, file=sys.stderr)
            GLib.idle_add(self._on_record_failed, str(err) if err else "")
        elif t == Gst.MessageType.EOS:
            GLib.idle_add(self._on_record_eos)
        return

    def _on_record_failed(self, err_msg=""):
        if not getattr(self, "_recording", False): return False
        self._recording = False
        try: self._destroy_record_pipeline()
        except Exception: pass
        try:
            self.status_label.set_text(
                self.tr("Enregistrement impossible")
                + ((" — " + err_msg) if err_msg else "")
            )
        except Exception: pass
        self._update_record_button_state()
        return False

    def _on_record_eos(self):
        if getattr(self, "_recording", False):
            self._stop_recording(show_save_dialog=True)
        return False

    def _destroy_record_pipeline(self):
        pipe = getattr(self, "_record_pipeline", None)
        self._record_pipeline = None
        wid = getattr(self, "_record_bus_watch_id", 0) or 0
        self._record_bus_watch_id = 0
        if pipe is not None:
            try:
                bus = pipe.get_bus()
                if wid:
                    try: bus.disconnect(wid)
                    except Exception: pass
                try: bus.remove_signal_watch()
                except Exception: pass
            except Exception: pass
            try:
                pipe.set_state(Gst.State.NULL)
                pipe.get_state(500 * Gst.MSECOND)
            except Exception: pass

    def _stop_recording(self, show_save_dialog=True):
        if not getattr(self, "_recording", False) and not getattr(
            self, "_record_pipeline", None
        ):
            return
        self._recording = False
        tmp = getattr(self, "_record_temp_path", None)
        self._record_temp_path = None
        pipe = getattr(self, "_record_pipeline", None)
        if pipe is not None:
            try:
                pipe.send_event(Gst.Event.new_eos())
                bus = pipe.get_bus()
                if bus is not None:
                    bus.timed_pop_filtered(
                        5 * Gst.SECOND,
                        Gst.MessageType.EOS | Gst.MessageType.ERROR,
                    )
            except Exception as exc:
                print("record EOS:", exc, file=sys.stderr)
            try:
                pipe.set_state(Gst.State.NULL)
                pipe.get_state(2 * Gst.SECOND)
            except Exception: pass
            try: self._destroy_record_pipeline()
            except Exception: pass
        self._update_record_button_state()
        if not show_save_dialog:
            try:
                if tmp and os.path.exists(tmp):
                    os.unlink(tmp)
            except Exception: pass
            return
        size = 0
        try:
            if tmp and os.path.isfile(tmp):
                size = os.path.getsize(tmp)
        except Exception:
            size = 0
        if not tmp or not os.path.isfile(tmp) or size < 256:
            try:
                if tmp and os.path.exists(tmp):
                    os.unlink(tmp)
            except Exception: pass
            try: self.status_label.set_text(self.tr("Enregistrement vide ou impossible"))
            except Exception: pass
            return
        self._prompt_save_recording(tmp)

    def _prompt_save_recording(self, temp_path):
        from datetime import datetime
        default_name = "Audio_" + datetime.now().strftime("%H-%M_%d-%m-%Y") + ".mp3"
        dialog = Gtk.FileChooserDialog(
            title=self.tr("Enregistrer l'audio"),
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            self.tr("Enregistrer"), Gtk.ResponseType.OK,
        )
        dialog.set_do_overwrite_confirmation(True)
        try: self._style_app_dialog(dialog)
        except Exception: pass
        try: self._translate_dialog_action_buttons(dialog)
        except Exception: pass
        try:
            downloads = GLib.get_user_special_dir(
                GLib.UserDirectory.DIRECTORY_DOWNLOAD
            )
            if downloads:
                dialog.set_current_folder(downloads)
            else:
                dialog.set_current_folder(os.path.expanduser("~/Downloads"))
        except Exception:
            try:
                fr = os.path.expanduser("~/Téléchargements")
                if os.path.isdir(fr):
                    dialog.set_current_folder(fr)
                else:
                    dialog.set_current_folder(os.path.expanduser("~/Downloads"))
            except Exception:
                dialog.set_current_folder(os.path.expanduser("~"))
        filt = Gtk.FileFilter()
        filt.set_name("MP3 (*.mp3)")
        filt.add_pattern("*.mp3")
        dialog.add_filter(filt)
        dialog.set_current_name(default_name)
        dest = None
        try:
            if dialog.run() == Gtk.ResponseType.OK:
                dest = dialog.get_filename()
        finally:
            dialog.destroy()
        if not dest:
            try: os.unlink(temp_path)
            except Exception: pass
            try: self.status_label.set_text(self.tr("Enregistrement annulé"))
            except Exception: pass
            return
        if not dest.lower().endswith(".mp3"):
            dest = dest + ".mp3"
        try:
            import shutil
            shutil.move(temp_path, dest)
            try:
                self.status_label.set_text(
                    self.tr("Enregistrement sauvegardé") + " — " + os.path.basename(dest)
                )
            except Exception: pass
        except Exception as exc:
            print("save recording:", exc, file=sys.stderr)
            try:
                import shutil
                shutil.copy2(temp_path, dest)
                os.unlink(temp_path)
                self.status_label.set_text(
                    self.tr("Enregistrement sauvegardé") + " — " + os.path.basename(dest)
                )
            except Exception as exc2:
                print("save recording copy:", exc2, file=sys.stderr)
                try: self.status_label.set_text(self.tr("Impossible d'enregistrer l'audio."))
                except Exception: pass
                try:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
                except Exception: pass

    def add_directory(self, directory):
        try:
            self._clear_playlist_search()
        except Exception:
            self.search_text = ""
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
        for path in files:
            try: self.add_file(path, save=False, scroll_to=False)
            except Exception: pass
        if files:
            try: self.save_playlist()
            except Exception: pass
            try:
                GLib.idle_add(self._refresh_playlist_scroll_adjustments)
                GLib.idle_add(self._scroll_playlist_to_end)
            except Exception: pass
        self.status_label.set_text(
            f"{len(files)}" + self.tr(" fichier(s) ajouté(s)")
        )
        return False

    def add_file(self, filename, save=True, scroll_to=True):
        if not getattr(self, "_restoring_playlist", False):
            try: self.dismiss_bouquet_playlist()
            except Exception: pass
        tree_iter = self._playlist_append_path(filename)
        if save and not getattr(self, "bouquet_active", False):
            self.save_playlist()
        try:
            if getattr(self, "playlist_filter", None) is not None:
                self.playlist_filter.refilter()
        except Exception: pass
        try:
            tree = getattr(self, "tree", None)
            if tree is not None:
                tree.queue_draw()
        except Exception: pass
        if scroll_to:
            try:
                child_path = self.playlist.get_path(tree_iter)
            except Exception:
                child_path = None
            def _after_add():
                try: self._refresh_playlist_scroll_adjustments()
                except Exception: pass
                if child_path is not None:
                    try:
                        self._scroll_playlist_to_path(child_path)
                    except Exception:
                        try: self._scroll_playlist_to_end()
                        except Exception: pass
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
        if response != Gtk.ResponseType.OK or not url: return
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

    def list_cd_tracks(self):
        src = Gst.ElementFactory.make("cdiocddasrc", "cdsrc")
        if src is None:
            src = Gst.ElementFactory.make("cdparanoiasrc", "cdsrc")
        if src is None: return None
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
                self.tr(
                    "Vérifiez qu’un CD audio est inséré et que les plugins\n"
                    "gstreamer1.0-plugins-ugly + libcdio sont installés."
                )
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

    def list_dvd_titles(self):
        src = Gst.ElementFactory.make("dvdreadsrc", "dvdsrc")
        if src is None: return None
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
            except Exception: pass
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
        disc_paths = (
            "/dev/sr0",
            "/dev/sr1",
            "/dev/bd",
            "/media/cdrom",
        )
        titles = []
        src = Gst.ElementFactory.make("bluraysrc", "bdsrc")
        if src is not None:
            for dev in disc_paths:
                if not os.path.exists(dev): continue
                try: src.set_property("device", dev)
                except Exception: pass
                try:
                    src.set_state(Gst.State.PAUSED)
                    src.get_state(3 * Gst.SECOND)
                except Exception:
                    try: src.set_state(Gst.State.NULL)
                    except Exception: pass
                    continue
                n = 1
                for prop in ("title", "titles", "num-titles"):
                    try: pass
                    except Exception: pass
                try: src.set_state(Gst.State.NULL)
                except Exception: pass
                titles.append(
                    (f"bluray://{dev}", self.tr("Blu-Ray") + f" — {dev}", 0.0)
                )
                break
        if not titles:
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

    def _clear_now_playing_display(self, reset_time=False):
        try: self.bottom_title.set_text(self.tr("Aucun morceau sélectionné"))
        except Exception: pass
        try: self.bottom_artist.set_text("")
        except Exception: pass
        try: self.bottom_album.set_text("")
        except Exception: pass
        try: self.set_default_cover()
        except Exception: pass
        if reset_time:
            self.current_duration = 0
            self._last_known_position = 0.0
            try:
                self.position_scale.set_range(0, 1)
                self.position_scale.set_value(0)
            except Exception: pass
            try: self.time_label.set_text("0:00 • 0:00")
            except Exception: pass
        try: self._update_mini_title()
        except Exception: pass
        try: self._update_nano_tooltip()
        except Exception: pass
    def _refresh_bottom_after_selection_cleared(self):
        path = getattr(self, "current_path", None)
        if path:
            try:
                still_there = path in self.get_paths()
            except Exception:
                still_there = False
            if still_there:
                try: self.update_information(path)
                except Exception: pass
                return
            self.current_path = None
        self._clear_now_playing_display(reset_time=True)

    def selection_changed(self, selection):
        model, paths = selection.get_selected_rows()
        if not paths:
            self._refresh_bottom_after_selection_cleared()
            return
        try:
            cursor_path, _col = self.tree.get_cursor()
        except Exception:
            cursor_path = None
        path = cursor_path if cursor_path is not None else paths[-1]
        try:
            iterator = model.get_iter(path)
        except Exception:
            return
        if iterator is None: return
        filename = model[iterator][self.COL_PATH]
        self._pending_selection_path = filename
        try:
            self._info_generation = int(getattr(self, "_info_generation", 0)) + 1
        except Exception:
            self._info_generation = 1
        try:
            cache = getattr(self, "_cover_pixbuf_cache", None) or {}
            if filename in cache:
                self.cover.set_from_pixbuf(cache[filename])
        except Exception: pass
        tid = getattr(self, "_selection_info_timeout_id", 0) or 0
        if tid:
            try: GLib.source_remove(tid)
            except Exception: pass
        self._selection_info_timeout_id = GLib.timeout_add(
            120, self._selection_info_timeout
        )

    def _selection_info_timeout(self):
        self._selection_info_timeout_id = 0
        filename = getattr(self, "_pending_selection_path", None)
        self._pending_selection_path = None
        if filename:
            try: self.update_information(filename)
            except Exception: pass
        return False

    def row_activated(self, tree, path, column):
        model = tree.get_model()
        iterator = model.get_iter(path)
        if iterator is None: return
        filename = model[iterator][self.COL_PATH]
        try:
            sel = tree.get_selection()
            sel.unselect_all()
            sel.select_path(path)
            tree.set_cursor(path)
        except Exception: pass
        self.play_file(filename)

    def update_information(self, filename):
        gen = int(getattr(self, "_info_generation", 0))
        def _after_info():
            try: self._update_mini_title()
            except Exception: pass
            return False
        GLib.idle_add(_after_info)
        if is_stream(filename):
            radio_name = self.current_radio_name or self.find_radio_name(filename)
            tv_name = self.current_tv_name or self.find_tv_name(filename)
            if tv_name:
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
            if gen == int(getattr(self, "_info_generation", 0)):
                self.set_default_cover()
            if filename == self.current_path and self.is_playing and not self.is_paused:
                try:
                    self._notify_track_change(
                        self.bottom_title.get_text(),
                        self.bottom_artist.get_text(),
                        self.bottom_album.get_text(),
                    )
                except Exception: pass
            try: self._update_nano_tooltip()
            except Exception: pass
            return
        try:
            cache = getattr(self, "_cover_pixbuf_cache", None) or {}
            if filename in cache and gen == int(getattr(self, "_info_generation", 0)):
                self.cover.set_from_pixbuf(cache[filename])
        except Exception: pass
        title, artist, album, year, duration, cover, technical = \
            self.get_metadata(filename, load_cover=True)
        if gen != int(getattr(self, "_info_generation", 0)): return
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
            try: self._notify_track_change(title, artist, album)
            except Exception: pass
        if cover:
            self.set_cover(cover, for_path=filename)
        else:
            self.set_default_cover()
        try: self._update_nano_tooltip()
        except Exception: pass
    def set_default_cover(self):
        try:
            self.cover.set_from_icon_name("audio-x-generic", Gtk.IconSize.DIALOG)
        except Exception:
            self.cover.clear()

    def set_cover(self, data, for_path=None):
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
        if for_path:
            try:
                cache = getattr(self, "_cover_pixbuf_cache", None)
                if cache is not None and for_path in cache:
                    try: cache.move_to_end(for_path)
                    except Exception: pass
                    try:
                        self.cover.set_from_pixbuf(cache[for_path])
                        return
                    except Exception: pass
            except Exception: pass
        pixbuf = None
        try:
            target_px = max(64, int(COVER_DECODE_MAX_PX))
        except Exception:
            target_px = 256
        try:
            gbytes = GLib.Bytes.new(data)
            stream = Gio.MemoryInputStream.new_from_bytes(gbytes)
            pixbuf = GdkPixbuf.Pixbuf.new_from_stream_at_scale(
                stream, target_px, target_px, True, None
            )
        except Exception:
            pixbuf = None
        if pixbuf is None:
            try:
                loader = GdkPixbuf.PixbufLoader()
                loader.write(data)
                loader.close()
                pixbuf = loader.get_pixbuf()
                if pixbuf is not None:
                    w, h = pixbuf.get_width(), pixbuf.get_height()
                    if max(w, h) > target_px:
                        sc = float(target_px) / float(max(w, h))
                        pixbuf = pixbuf.scale_simple(
                            max(1, int(w * sc)),
                            max(1, int(h * sc)),
                            GdkPixbuf.InterpType.BILINEAR,
                        )
            except Exception:
                pixbuf = None
        if pixbuf is None:
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
                    if pixbuf is not None: break
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
            try:
                if for_path:
                    if not hasattr(self, "_cover_pixbuf_cache") or self._cover_pixbuf_cache is None:
                        self._cover_pixbuf_cache = OrderedDict()
                    self._cover_pixbuf_cache[for_path] = pixbuf
                    try: self._cover_pixbuf_cache.move_to_end(for_path)
                    except Exception: pass
                    while len(self._cover_pixbuf_cache) > COVER_PIXBUF_CACHE_MAX:
                        try:
                            self._cover_pixbuf_cache.popitem(last=False)
                        except Exception:
                            break
            except Exception: pass
            try:
                if data is not None and len(data) > 512 * 1024:
                    GLib.idle_add(lambda: (trim_memory(force=True), False)[1])
            except Exception: pass
        except Exception as exc:
            print("set_cover:", exc, file=sys.stderr)
            self.set_default_cover()

    def reset_spectrum(self):
        self.spectrum_values = [0.0] * SPECTRUM_BANDS
        self.spectrum_targets = [0.0] * SPECTRUM_BANDS
        self.spectrum_peaks = [0.0] * SPECTRUM_BANDS
        self.spectrum_quiet = [0] * SPECTRUM_BANDS
        self.spectrum_peak_hold = [0.0] * SPECTRUM_BANDS
        self.spectrum_agc_ceiling = SPECTRUM_AGC_CEILING_MIN_DB
        try:
            q = getattr(self, "_spectrum_delay_queue", None)
            if q is not None:
                q.clear()
        except Exception:
            self._spectrum_delay_queue = deque()
        self._spectrum_stale_count = 0
        self._spectrum_use_fallback = False
        self._spectrum_ok_count = 0
        try:
            self._spectrum_epoch = int(getattr(self, "_spectrum_epoch", 0) or 0) + 1
        except Exception:
            self._spectrum_epoch = 1

    def _extract_magnitudes(self, structure):
        method = getattr(self, "_spectrum_mag_method", None)
        def _via_list():
            ok, value_array = structure.get_list("magnitude")
            if not ok or value_array is None: return None
            n = value_array.n_values
            magnitudes = []
            for i in range(n):
                try:
                    magnitudes.append(value_array.get_nth(i))
                except Exception:
                    break
            return magnitudes or None
        def _via_value():
            raw = structure.get_value("magnitude")
            if raw is None: return None
            if isinstance(raw, (list, tuple)):
                magnitudes = [float(x) for x in raw]
                return magnitudes or None
            if hasattr(raw, "n_values") and hasattr(raw, "get_nth"):
                magnitudes = [raw.get_nth(i) for i in range(raw.n_values)]
                return magnitudes or None
            return None
        def _via_regex():
            s = structure.to_string()
            m = re.search(r"magnitude=\(float\)\{([^}]*)\}", s)
            if not m: return None
            magnitudes = [
                float(x.strip())
                for x in m.group(1).split(",")
                if x.strip()
            ]
            return magnitudes or None
        order = []
        if method == "list":
            order = [_via_list, _via_value, _via_regex]
        elif method == "value":
            order = [_via_value, _via_list, _via_regex]
        elif method == "regex":
            order = [_via_regex, _via_list, _via_value]
        else:
            order = [_via_list, _via_value, _via_regex]
        names = {_via_list: "list", _via_value: "value", _via_regex: "regex"}
        for fn in order:
            try:
                magnitudes = fn()
                if magnitudes:
                    self._spectrum_mag_method = names[fn]
                    return magnitudes
            except Exception: pass
        return None

    def _build_spectrum_ranges(self, source_count):
        if source_count < 2: return []
        cached = getattr(self, "_spectrum_ranges_cache", None)
        if isinstance(cached, tuple) and cached[0] == source_count: return cached[1]
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
        self._spectrum_ranges_cache = (source_count, ranges)
        return ranges

    def _spectrum_message_running_time(self, structure):
        if structure is None: return None
        try:
            ok, val = structure.get_uint64("running-time")
            if ok: return int(val)
        except Exception: pass
        try:
            val = structure.get_value("running-time")
            if val is not None: return int(val)
        except Exception: pass
        for key in ("stream-time", "timestamp"):
            try:
                ok, val = structure.get_uint64(key)
                if ok: return int(val)
            except Exception: pass
            try:
                val = structure.get_value(key)
                if val is not None: return int(val)
            except Exception: pass
        try:
            s = structure.to_string()
            m = re.search(r"running-time=\(guint64\)(\d+)", s)
            if m: return int(m.group(1))
        except Exception: pass
        return None

    def _spectrum_playback_running_time_ns(self):
        try:
            pipe = self._active_pipeline()
        except Exception:
            pipe = getattr(self, "player", None)
        if pipe is None: return None
        try:
            _ret, state, _pending = pipe.get_state(0)
            if state != Gst.State.PLAYING: return None
        except Exception:
            return None
        try:
            ok, pos = pipe.query_position(Gst.Format.TIME)
            if ok and pos is not None and int(pos) >= 0: return int(pos)
        except Exception: pass
        try:
            clock = pipe.get_clock()
            if clock is None: return None
            base = pipe.get_base_time()
            now = clock.get_time()
            if (
                base == Gst.CLOCK_TIME_NONE
                or now == Gst.CLOCK_TIME_NONE
                or now < base
            ):
                return None
            return int(now - base)
        except Exception:
            return None

    def _spectrum_enqueue(self, rt_ns, targets):
        q = getattr(self, "_spectrum_delay_queue", None)
        if q is None:
            q = self._spectrum_delay_queue = deque()
        epoch = int(getattr(self, "_spectrum_epoch", 0) or 0)
        if (
            rt_ns is not None
            and q
            and q[-1][0] is not None
            and rt_ns + 50_000_000 < q[-1][0]  # redescend de >50 ms
        ):
            q.clear()
            self._spectrum_stale_count = 0
            self._spectrum_use_fallback = False
            self._spectrum_ok_count = 0
        try:
            mono_ns = time.monotonic_ns()
        except Exception:
            mono_ns = int(time.monotonic() * 1e9)
        q.append((rt_ns, mono_ns, list(targets), epoch))
        while len(q) > SPECTRUM_QUEUE_MAX:
            q.popleft()

    def _flush_spectrum_delay(self):
        q = getattr(self, "_spectrum_delay_queue", None)
        if not q: return
        if getattr(self, "is_paused", False) or not getattr(self, "is_playing", False): return
        try:
            offset_ns = int(getattr(self, "spectrum_sync_offset_ms", 0) or 0) * 1000000
        except Exception:
            offset_ns = 0
        cur_epoch = int(getattr(self, "_spectrum_epoch", 0) or 0)
        use_fb = bool(getattr(self, "_spectrum_use_fallback", False))
        probe_rt = self._spectrum_playback_running_time_ns()
        now_rt = None if use_fb else probe_rt
        try:
            mono_now = time.monotonic_ns()
        except Exception:
            mono_now = int(time.monotonic() * 1e9)
        fb_ns = int(SPECTRUM_FALLBACK_DELAY_S * 1e9)
        max_lead_ns = int(SPECTRUM_MAX_LEAD_S * 1e9)
        max_skew_ns = int(3.0 * 1e9)
        applied = None
        while q:
            item = q[0]
            if len(item) >= 4:
                rt_ns, mono_ns, targets, epoch = item[0], item[1], item[2], item[3]
            else:
                rt_ns, mono_ns, targets = item[0], item[1], item[2]
                epoch = cur_epoch
            if epoch != cur_epoch:
                q.popleft()
                continue
            if use_fb and rt_ns is not None and probe_rt is not None:
                skew = abs((rt_ns + offset_ns) - probe_rt)
                if skew <= max_skew_ns and (rt_ns + offset_ns) - probe_rt <= max_lead_ns:
                    self._spectrum_ok_count = (
                        getattr(self, "_spectrum_ok_count", 0) + 1
                    )
                    if self._spectrum_ok_count >= 12:
                        self._spectrum_use_fallback = False
                        use_fb = False
                        now_rt = probe_rt
                        self._spectrum_stale_count = 0
                        if SPECTRUM_DEBUG or DEBUG_MYOSTOOX:
                            print(
                                "[spectrum-sync] retour synchro position",
                                file=sys.stderr,
                            )
                else:
                    self._spectrum_ok_count = 0
            if rt_ns is None or now_rt is None:
                due = (mono_ns + fb_ns + offset_ns) <= mono_now
            else:
                lead = rt_ns + offset_ns - now_rt
                if lead > max_lead_ns:
                    q.popleft()
                    self._spectrum_stale_count = (
                        getattr(self, "_spectrum_stale_count", 0) + 1
                    )
                    self._spectrum_ok_count = 0
                    if self._spectrum_stale_count >= SPECTRUM_BROKEN_AFTER:
                        self._spectrum_use_fallback = True
                        use_fb = True
                        if SPECTRUM_DEBUG or DEBUG_MYOSTOOX:
                            print(
                                "[spectrum-sync] timeline incohérente "
                                "→ repli horloge murale",
                                file=sys.stderr,
                            )
                        now_rt = None
                        continue
                    continue
                lag = now_rt - (rt_ns + offset_ns)
                if lag > max_skew_ns:
                    q.popleft()
                    self._spectrum_stale_count = (
                        getattr(self, "_spectrum_stale_count", 0) + 1
                    )
                    self._spectrum_ok_count = 0
                    if self._spectrum_stale_count >= SPECTRUM_BROKEN_AFTER:
                        self._spectrum_use_fallback = True
                        use_fb = True
                        now_rt = None
                    continue
                self._spectrum_stale_count = 0
                due = (rt_ns + offset_ns) <= now_rt
            if not due: break
            applied = q.popleft()
        if applied is not None:
            targets = applied[2]
            for i in range(min(SPECTRUM_BANDS, len(targets))):
                self.spectrum_targets[i] = targets[i]
        if SPECTRUM_DEBUG or DEBUG_MYOSTOOX:
            t = time.monotonic()
            if t - getattr(self, "_spectrum_dbg_t", 0.0) > 1.0:
                self._spectrum_dbg_t = t
                head_rt = q[0][0] if q else None
                print(
                    "[spectrum-sync] now_rt=%s head_rt=%s file=%d "
                    "fallback=%s stale=%d epoch=%d"
                    % (
                        "None" if now_rt is None else "%.0f ms" % (now_rt / 1e6),
                        "None" if head_rt is None else "%.0f ms" % (head_rt / 1e6),
                        len(q),
                        use_fb or now_rt is None,
                        getattr(self, "_spectrum_stale_count", 0),
                        cur_epoch,
                    ),
                    file=sys.stderr,
                )

    def process_spectrum(self, structure):
        if not self.spectrum_enabled: return
        if bool(getattr(self, "_window_obscured", False)): return
        if bool(getattr(self, "current_is_video", False)): return
        if getattr(self, "is_paused", False): return
        try:
            magnitudes = self._extract_magnitudes(structure)
            if not magnitudes: return
            source_count = min(SPECTRUM_SOURCE_BANDS, len(magnitudes))
            if source_count < 20: return
            db_values = []
            for i in range(source_count):
                try:
                    db = float(magnitudes[i])
                except Exception:
                    db = -80.0
                db = max(-80.0, min(0.0, db))
                db_values.append(db)
            ranges = self._build_spectrum_ranges(source_count)
            if len(ranges) != SPECTRUM_BANDS: return
            raw = [0.0] * SPECTRUM_BANDS
            band_dbs = [None] * SPECTRUM_BANDS
            if self.spectrum_agc_enabled:
                high_reference = self.spectrum_agc_ceiling
            else:
                high_reference = SPECTRUM_HIGH_DB
            for i, (start, end) in enumerate(ranges):
                segment = db_values[start:end]
                if not segment: continue
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
                raw[i] = value * (7.0 / 11.0)  # réutilise raw comme cibles finales
            try:
                self._spectrum_enqueue(
                    self._spectrum_message_running_time(structure), raw
                )
            except Exception:
                for i in range(SPECTRUM_BANDS):
                    self.spectrum_targets[i] = raw[i]
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

    def update_visualizer(self):
        if getattr(self, "_app_closing", False):
            self._visualizer_timeout_id = 0
            return False
        if not self._visualizer_should_animate():
            try:
                if all(v <= 0.001 for v in self.spectrum_values) and all(
                    t <= 0.001 for t in self.spectrum_targets
                ):
                    self._visualizer_timeout_id = 0
                    return False
            except Exception:
                self._visualizer_timeout_id = 0
                return False
        if not getattr(self, "is_playing", False):
            try:
                if all(v <= 0.001 for v in self.spectrum_values) and all(
                    t <= 0.001 for t in self.spectrum_targets
                ):
                    self._visualizer_timeout_id = 0
                    return False
            except Exception: pass
        try: self._flush_spectrum_delay()
        except Exception: pass
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
        if state == getattr(self, "_viz_last_state", None): return True
        self._viz_last_state = state
        self.visualizer.queue_draw()
        return True

    def _spectrum_base_color(self):
        try:
            br, bg, bb = getattr(self, "spectrum_bar_color", (0.31, 0.71, 0.94))
            return float(br), float(bg), float(bb)
        except Exception:
            return 0.31, 0.71, 0.94

    def _draw_spectrum_bars(self, cr, width, height, us):
        count = SPECTRUM_BANDS
        gap = 4.5 * us
        bar_width = 8.0 * us
        total_width = count * bar_width + (count - 1) * gap
        start_x = (width - total_width) / 2.0
        bottom_margin = 4.0 * us
        top_margin = 5.0 * us
        usable_height = max(1.0, height - bottom_margin - top_margin)
        br, bg, bb = self._spectrum_base_color()
        lr = min(1.0, br * 0.55 + 0.45)
        lg = min(1.0, bg * 0.55 + 0.45)
        lb = min(1.0, bb * 0.55 + 0.45)
        dr = max(0.0, br * 0.35)
        dg = max(0.0, bg * 0.35)
        db = max(0.0, bb * 0.35)
        for i in range(count):
            value = max(0.0, min(1.0, self.spectrum_values[i]))
            peak = max(0.0, min(1.0, self.spectrum_peaks[i]))
            minimum_height = 1.0
            bar_height = minimum_height + value * (usable_height - minimum_height)
            x = start_x + i * (bar_width + gap)
            y = height - bottom_margin - bar_height
            pattern = cairo.LinearGradient(0, y, 0, height - bottom_margin)
            pattern.add_color_stop_rgba(0.0, lr, lg, lb, 1.0)
            pattern.add_color_stop_rgba(0.42, br, bg, bb, 1.0)
            pattern.add_color_stop_rgba(1.0, dr, dg, db, 1.0)
            cr.rectangle(x, y, bar_width, bar_height)
            cr.set_source(pattern)
            cr.fill()
            cr.set_source_rgba(lr, lg, lb, 0.84)
            cr.rectangle(x, y, bar_width, 1.0)
            cr.fill()
            if peak > 0.08:
                peak_y = height - bottom_margin - peak * usable_height
                peak_y = min(peak_y, y)
                cr.set_source_rgba(lr, lg, lb, 0.56)
                cr.rectangle(x, peak_y, bar_width, 1.0)
                cr.fill()

    def _draw_spectrum_oscilloscope(self, cr, width, height, us):
        br, bg, bb = self._spectrum_base_color()
        lr = min(1.0, br * 0.55 + 0.45)
        lg = min(1.0, bg * 0.55 + 0.45)
        lb = min(1.0, bb * 0.55 + 0.45)
        bottom_margin = 3.0 * us
        top_margin = 3.0 * us
        usable_height = max(1.0, height - bottom_margin - top_margin)
        mid_y = top_margin + usable_height * 0.5
        count = SPECTRUM_BANDS
        vals = [
            max(0.0, min(1.0, self.spectrum_values[i]))
            for i in range(count)
        ]
        samples = max(48, int(width))
        points = []
        for s in range(samples):
            t = s / max(1, samples - 1)
            fidx = t * (count - 1)
            i0 = int(fidx)
            i1 = min(count - 1, i0 + 1)
            frac = fidx - i0
            v = vals[i0] * (1.0 - frac) + vals[i1] * frac
            amp = (v - 0.5) * 1.6
            amp = max(-0.95, min(0.95, amp))
            x = t * (width - 1)
            y = mid_y - amp * (usable_height * 0.48)
            points.append((x, y))
        if len(points) < 2: return
        cr.move_to(points[0][0], mid_y)
        for x, y in points:
            cr.line_to(x, y)
        cr.line_to(points[-1][0], mid_y)
        cr.close_path()
        fill = cairo.LinearGradient(0, top_margin, 0, height - bottom_margin)
        fill.add_color_stop_rgba(0.0, lr, lg, lb, 0.55)
        fill.add_color_stop_rgba(0.5, br, bg, bb, 0.28)
        fill.add_color_stop_rgba(1.0, br, bg, bb, 0.05)
        cr.set_source(fill)
        cr.fill()
        cr.set_line_width(max(1.2, 1.6 * us))
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.move_to(points[0][0], points[0][1])
        for x, y in points[1:]:
            cr.line_to(x, y)
        stroke = cairo.LinearGradient(0, 0, width, 0)
        stroke.add_color_stop_rgba(0.0, lr, lg, lb, 0.95)
        stroke.add_color_stop_rgba(0.5, br, bg, bb, 1.0)
        stroke.add_color_stop_rgba(1.0, lr, lg, lb, 0.95)
        cr.set_source(stroke)
        cr.stroke()
        cr.set_source_rgba(br, bg, bb, 0.18)
        cr.set_line_width(1.0)
        cr.move_to(0, mid_y)
        cr.line_to(width, mid_y)
        cr.stroke()

    def _draw_spectrum_pixel(self, cr, width, height, us):
        pixel_cols = 36
        gap = max(1.0, 1.2 * us)
        bar_width = max(2.0, (width - (pixel_cols - 1) * gap) / float(pixel_cols))
        total_width = pixel_cols * bar_width + (pixel_cols - 1) * gap
        start_x = (width - total_width) / 2.0
        bottom_margin = 2.0 * us
        top_margin = 2.0 * us
        usable_height = max(1.0, height - bottom_margin - top_margin)
        cell = max(2.0, 3.0 * us)
        br, bg, bb = self._spectrum_base_color()
        lr = min(1.0, br * 0.55 + 0.45)
        lg = min(1.0, bg * 0.55 + 0.45)
        lb = min(1.0, bb * 0.55 + 0.45)
        base_count = SPECTRUM_BANDS
        for col in range(pixel_cols):
            t = col / max(1, pixel_cols - 1)
            fidx = t * (base_count - 1)
            i0 = int(fidx)
            i1 = min(base_count - 1, i0 + 1)
            frac = fidx - i0
            value = (
                max(0.0, min(1.0, self.spectrum_values[i0])) * (1.0 - frac)
                + max(0.0, min(1.0, self.spectrum_values[i1])) * frac
            )
            cascade = 0.08 * math.sin(col * 0.7 + time.time() * 3.5)
            value = max(0.0, min(1.0, value + cascade * 0.15))
            n_cells = max(1, int(round(value * (usable_height / cell))))
            x = start_x + col * (bar_width + gap)
            for c in range(n_cells):
                y = height - bottom_margin - (c + 1) * cell
                k = c / max(1, n_cells - 1)
                r = lr * (1.0 - k) + br * k
                g = lg * (1.0 - k) + bg * k
                b = lb * (1.0 - k) + bb * k
                alpha = 0.55 + 0.45 * (1.0 - abs(k - 0.35))
                cr.set_source_rgba(r, g, b, alpha)
                cr.rectangle(x, y + 0.5, bar_width, max(1.0, cell - 1.0))
                cr.fill()

    def draw_visualizer(self, widget, cr):
        allocation = widget.get_allocation()
        width = allocation.width
        height = allocation.height
        if width <= 0 or height <= 0: return False
        try:
            us = float(getattr(self, "ui_scale", 1.0) or 1.0)
        except Exception:
            us = 1.0
        style = str(getattr(self, "spectrum_style", "pixel") or "pixel").lower()
        if style in ("oscilloscope", "pixel"):
            if not self._spectrum_style_allowed():
                style = "bars"
        if style == "oscilloscope":
            self._draw_spectrum_oscilloscope(cr, width, height, us)
        elif style == "pixel":
            self._draw_spectrum_pixel(cr, width, height, us)
        else:
            self._draw_spectrum_bars(cr, width, height, us)
        return False

    def _spectrum_style_allowed(self):
        try:
            if getattr(self, "current_is_video", False):
                path = getattr(self, "current_path", None)
                if is_cdda(path) or is_stream(path):
                    if getattr(self, "current_tv_name", None): return False
                    return True
                return False
            return True
        except Exception:
            return True

    def set_spectrum_style(self, style):
        style = str(style or "bars").lower()
        if style not in ("bars", "oscilloscope", "pixel"):
            style = "bars"
        if style in ("oscilloscope", "pixel") and not self._spectrum_style_allowed():
            style = "bars"
        self.spectrum_style = style
        try: self.save_state()
        except Exception: pass
        try:
            if getattr(self, "visualizer", None) is not None:
                self.visualizer.queue_draw()
        except Exception: pass
        labels = {
            "bars": self.tr("Barres"),
            "oscilloscope": self.tr("Oscilloscope"),
            "pixel": self.tr("Pixel"),
        }
        try:
            self.status_label.set_text(
                self.tr("Spectre") + " : " + labels.get(style, style)
            )
        except Exception: pass

    def _load_performance_mode(self):
        try:
            if os.path.isfile(STATE_FILE):
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
                if isinstance(state, dict):
                    mode = state.get("performance_mode")
                    if mode in ("hardware", "software"): return mode
        except Exception: pass
        return "hardware"

    def _apply_decoder_performance_mode(self, force_software=False):
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
            try: apply_safe_gstreamer_ranks(prefer_nvidia_av1=True)
            except Exception: pass
        else:
            for name in _NVIDIA_DECODER_NAMES:
                _gst_set_rank(name, Gst.Rank.NONE)
            for name in _OTHER_HW_DECODER_NAMES:
                _gst_set_rank(name, Gst.Rank.NONE)
            for name in _SOFTWARE_VIDEO_DECODER_NAMES:
                _gst_set_rank(name, Gst.Rank.PRIMARY + 100)
            try: apply_safe_gstreamer_ranks(prefer_nvidia_av1=False)
            except Exception: pass
    def _promote_av1_software_decoders(self):
        prefer = getattr(self, "performance_mode", "hardware") == "hardware"
        apply_safe_gstreamer_ranks(prefer_nvidia_av1=prefer)

    def _apply_safe_audio_decoder_ranks(self):
        try:
            factory = Gst.ElementFactory.find("a52dec")
            if factory is not None:
                factory.set_rank(Gst.Rank.NONE)
        except Exception: pass
        for name in ("avdec_eac3", "avdec_ac3"):
            try:
                factory = Gst.ElementFactory.find(name)
                if factory is not None:
                    factory.set_rank(Gst.Rank.PRIMARY + 100)
            except Exception: pass

    def set_performance_mode(self, mode):
        if mode not in ("hardware", "software"): return
        if mode == getattr(self, "performance_mode", None): return
        self.performance_mode = mode
        try: self.save_state()
        except Exception: pass
        self._apply_decoder_performance_mode()
        try: self._configure_playbin_buffers()
        except Exception: pass
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
        if sink is None: return None
        soft = getattr(self, "performance_mode", "hardware") == "software"
        lateness = (1500 if soft else 500) * Gst.MSECOND
        props = {
            "force-aspect-ratio": True,
            "sync": True,
            "handle-events": False,
            "draw-borders": False,
            "qos": True,
            "max-lateness": lateness,
        }
        for prop, value in props.items():
            try: sink.set_property(prop, value)
            except Exception: pass
        for prop, value in (
            ("force-aspect-ratio", True),
            ("show-preroll-frame", True),
        ):
            try:
                if sink.find_property(prop) is not None:
                    sink.set_property(prop, value)
            except Exception: pass
        return sink

    def _ensure_video_sink(self):
        if getattr(self, "_video_sink_is_real", False) and self.video_sink is not None: return self.video_sink
        try:
            sink = self._create_video_sink()
        except Exception:
            sink = None
        if sink is None: return self.video_sink
        self.video_sink = sink
        self._video_sink_is_real = True
        try:
            if self.player is not None:
                self.player.set_property("video-sink", sink)
        except Exception: pass
        return sink

    def _release_real_video_sink(self):
        if not getattr(self, "_video_sink_is_real", False): return
        try:
            fake = Gst.ElementFactory.make("fakesink", "video_sink_placeholder")
            if fake is None: return
            try: fake.set_property("sync", False)
            except Exception: pass
            try: fake.set_property("async", False)
            except Exception: pass
            if self.player is not None:
                self.player.set_property("video-sink", fake)
            self.video_sink = fake
            self._video_sink_is_real = False
            try: self._pause_pixbuf = None
            except Exception: pass
        except Exception as exc:
            print("release video sink:", exc, file=sys.stderr)

    def _configure_playbin_buffers(self, audio_only=False):
        if self.player is None: return
        soft = getattr(self, "performance_mode", "hardware") == "software"
        if audio_only:
            buf_size = 2 * 1024 * 1024
            buf_dur = 2 * Gst.SECOND
        else:
            buf_size = (12 if soft else 8) * 1024 * 1024
            buf_dur = (5 if soft else 4) * Gst.SECOND
        for prop, value in (
            ("buffer-size", buf_size),
            ("buffer-duration", buf_dur),
        ):
            try: self.player.set_property(prop, value)
            except Exception: pass
        try:
            flags = int(self.player.get_property("flags"))
            VIDEO = 1 << 0
            AUDIO = 1 << 1
            TEXT = 1 << 2
            VIS = 1 << 3
            DOWNLOAD = 1 << 7
            if audio_only:
                flags = (flags | AUDIO) & ~VIDEO & ~VIS & ~DOWNLOAD
            else:
                flags = (flags | VIDEO | AUDIO | TEXT) & ~VIS & ~DOWNLOAD
            self.player.set_property("flags", flags)
        except Exception: pass

    def _set_spectrum_active_for_media(self, is_video):
        try:
            if is_video is not None: pass
            self._sync_spectrum_activity()
        except Exception: pass

    def _spectrum_messages_wanted(self):
        if self.spectrum is None: return False
        if not bool(getattr(self, "spectrum_enabled", True)): return False
        if bool(getattr(self, "current_is_video", False)): return False
        if bool(getattr(self, "_window_obscured", False)): return False
        if bool(getattr(self, "_app_closing", False)): return False
        if not bool(getattr(self, "is_playing", False)): return False
        if bool(getattr(self, "is_paused", False)): return False
        return True

    def _visualizer_should_animate(self):
        if bool(getattr(self, "_app_closing", False)): return False
        if not bool(getattr(self, "spectrum_enabled", True)): return False
        if bool(getattr(self, "current_is_video", False)): return False
        if bool(getattr(self, "_window_obscured", False)): return False
        viz = getattr(self, "visualizer", None)
        if viz is not None:
            try:
                if not viz.get_mapped(): return False
            except Exception: pass
        return True

    def _sync_spectrum_activity(self):
        if self.spectrum is None: return
        want = self._spectrum_messages_wanted()
        try:
            self.spectrum.set_property("post-messages", bool(want))
            if want:
                self.spectrum.set_property("interval", 20000000)  # 20 ms
        except Exception: pass
        try: self._ensure_visualizer_timer()
        except Exception: pass
    def _ensure_visualizer_timer(self):
        tid = getattr(self, "_visualizer_timeout_id", 0) or 0
        need = self._visualizer_should_animate()
        if not need:
            try:
                vals = getattr(self, "spectrum_values", None) or []
                tars = getattr(self, "spectrum_targets", None) or []
                if any(v > 0.001 for v in vals) or any(t > 0.001 for t in tars):
                    need = True
            except Exception: pass
        if need and not tid:
            try:
                self._visualizer_timeout_id = GLib.timeout_add(
                    VISUALIZER_INTERVAL_MS, self.update_visualizer
                )
            except Exception:
                self._visualizer_timeout_id = 0

    def _release_pause_pixbuf(self):
        self._pause_pixbuf = None

    def _cache_video_window_handle(self):
        if self.video_area is None: return None
        try:
            if not self.video_area.get_realized():
                self.video_area.realize()
            try:
                if not self.video_area.get_mapped():
                    self.video_area.show()
                    while Gtk.events_pending():
                        Gtk.main_iteration_do(False)
            except Exception: pass
            window = self.video_area.get_window()
            if window is None: return None
            try: window.ensure_native()
            except Exception: pass
            xid = window.get_xid()
            if xid and xid != 0:
                self._video_window_handle = xid
                return xid
        except Exception as e:
            print("Erreur cache window handle:", e, file=sys.stderr)
        return None

    def _apply_video_overlay_handle(self, sink=None):
        handle = getattr(self, "_video_window_handle", None)
        if not handle:
            handle = self._cache_video_window_handle()
        if not handle: return False
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
        try:
            if not GstVideo.is_video_overlay_prepare_window_handle_message(message): return
        except Exception:
            structure = message.get_structure()
            if structure is None: return
            if structure.get_name() != "prepare-window-handle": return
        handle = self._video_window_handle
        if handle is None:
            handle = self._cache_video_window_handle()
        if handle is None: return
        try:
            sink = message.src
            if isinstance(sink, GstVideo.VideoOverlay):
                GstVideo.VideoOverlay.set_window_handle(sink, handle)
            elif hasattr(sink, "set_window_handle"):
                sink.set_window_handle(handle)
            self._apply_video_overlay_handle(sink)
        except Exception as e:
            print("Erreur prepare-window-handle:", e, file=sys.stderr)

    def _nudge_video_redraw(self):
        if not self.current_is_video or self.video_sink is None: return False
        try:
            if isinstance(self.video_sink, GstVideo.VideoOverlay):
                GstVideo.VideoOverlay.expose(self.video_sink)
            if self.video_area is not None:
                self.video_area.queue_draw()
        except Exception: pass
        return False

    def on_video_realize(self, widget):
        try:
            window = widget.get_window()
            if window is not None:
                black = Gdk.RGBA()
                black.red = 0.0
                black.green = 0.0
                black.blue = 0.0
                black.alpha = 1.0
                window.set_background_rgba(black)
        except Exception: pass
        self._cache_video_window_handle()

    def on_video_size_allocate(self, widget, allocation):
        if getattr(self, "_fs_controls_visible", False):
            try: GLib.idle_add(self._position_fs_popup)
            except Exception: pass
        if not self.current_is_video or self.video_sink is None: return
        if self._size_allocate_timer:
            try: GLib.source_remove(self._size_allocate_timer)
            except Exception: pass
            self._size_allocate_timer = 0
        def _after_resize():
            self._size_allocate_timer = 0
            self._cache_video_window_handle()
            self._apply_video_overlay_handle()
            try:
                sink = getattr(self, "video_sink", None)
                if sink is not None and sink.find_property("force-aspect-ratio"):
                    sink.set_property("force-aspect-ratio", True)
            except Exception: pass
            self._nudge_video_redraw()
            return False
        self._size_allocate_timer = GLib.timeout_add(150, _after_resize)

    def on_video_draw(self, widget, cr):
        if getattr(self, "lyrics_mode", False):
            self._draw_lyrics(widget, cr)
            return True
        if self.current_is_video and self.is_playing and not self.is_paused: return False
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
                except Exception: pass
            return True
        allocation = widget.get_allocation()
        cr.set_source_rgb(0.0, 0.0, 0.0)
        cr.rectangle(0, 0, allocation.width, allocation.height)
        cr.fill()
        return True

    def _capture_pause_frame(self):
        self._pause_pixbuf = None
        if not self.current_is_video or self.video_area is None: return
        try:
            if self.video_sink is not None and isinstance(
                self.video_sink, GstVideo.VideoOverlay
            ):
                try: GstVideo.VideoOverlay.expose(self.video_sink)
                except Exception: pass
            window = self.video_area.get_window()
            if window is None: return
            w = self.video_area.get_allocated_width()
            h = self.video_area.get_allocated_height()
            if w <= 0 or h <= 0: return
            pixbuf = Gdk.pixbuf_get_from_window(window, 0, 0, w, h)
            if pixbuf is None: return
            max_w = 960
            if w > max_w:
                nh = max(1, int(h * (max_w / float(w))))
                try:
                    pixbuf = pixbuf.scale_simple(
                        max_w, nh, GdkPixbuf.InterpType.BILINEAR
                    )
                except Exception: pass
            self._pause_pixbuf = pixbuf
        except Exception:
            self._pause_pixbuf = None

    def _capture_pause_frame_and_redraw(self):
        self._capture_pause_frame()
        try:
            if self.video_area is not None:
                self.video_area.queue_draw()
        except Exception: pass
        return False

    def _refresh_video_surface(self):
        if not self.current_is_video: return False
        try:
            self._cache_video_window_handle()
            if self.is_paused and self._pause_pixbuf is not None:
                if self.video_area is not None:
                    self.video_area.queue_draw()
            else:
                self._nudge_video_redraw()
        except Exception: pass
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
        if event.changed_mask & Gdk.WindowState.FULLSCREEN:
            is_fs = bool(event.new_window_state & Gdk.WindowState.FULLSCREEN)
            self.is_fullscreen = is_fs
            if not is_fs and self._fullscreen_ui_state is not None:
                self._restore_ui_after_fullscreen()
            elif is_fs and (
                self.current_is_video or getattr(self, "lyrics_mode", False)
            ):
                self._apply_fullscreen_ui_layout()
                if self.current_is_video:
                    self._nudge_video_redraw()
                elif self.video_area is not None:
                    self.video_area.queue_draw()
        if event.changed_mask & (
            Gdk.WindowState.ICONIFIED | Gdk.WindowState.WITHDRAWN
        ):
            now_hidden = bool(
                event.new_window_state
                & (Gdk.WindowState.ICONIFIED | Gdk.WindowState.WITHDRAWN)
            )
            self._window_obscured = now_hidden
            try: self._sync_spectrum_activity()
            except Exception: pass
            if not now_hidden and self.current_is_video:
                GLib.timeout_add(50, self._refresh_video_surface)
        return False

    def toggle_video_fullscreen(self):
        if not (self.current_is_video or getattr(self, "lyrics_mode", False)): return
        really_fs = False
        try:
            win = self.get_window()
            if win is not None:
                really_fs = bool(win.get_state() & Gdk.WindowState.FULLSCREEN)
        except Exception:
            really_fs = self.is_fullscreen
        if really_fs or self.is_fullscreen:
            self.unfullscreen()
            self._restore_ui_after_fullscreen()
        else:
            self._enter_video_fullscreen()

    def _apply_fullscreen_ui_layout(self):
        try:
            for attr in ("top_bar_wrap", "top_bar", "bottom_bar_wrap", "bottom_bar"):
                w = getattr(self, attr, None)
                if w is not None:
                    try: w.hide()
                    except Exception: pass
            if hasattr(self, "playlist_box"):
                self.playlist_box.hide()
            if hasattr(self, "video_overlay"):
                self.video_overlay.show()
            self.video_area.show()
            self.video_area.set_size_request(-1, -1)
            alloc = self.get_allocation()
            h = max(alloc.height, self.get_allocated_height() or 800)
            self.content_paned.set_position(max(h - 2, 400))
        except Exception as e:
            print("Erreur layout plein écran:", e, file=sys.stderr)

    def _enter_video_fullscreen(self):
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
        if self._fullscreen_ui_state is None and not self.is_fullscreen: return
        self._fullscreen_ui_state = None
        self.is_fullscreen = False
        try:
            compact = bool(getattr(self, "mini_player_mode", False)) or bool(
                getattr(self, "nano_player_mode", False)
            )
            if compact:
                try: self._apply_compact_player_mode()
                except Exception: pass
                self._show_cursor()
                self._hide_fs_controls()
                return
            playlist_only = bool(getattr(self, "playlist_only_mode", False))
            for attr in ("top_bar_wrap", "top_bar"):
                w = getattr(self, attr, None)
                if w is not None:
                    try:
                        if playlist_only:
                            w.hide()
                        else:
                            w.show()
                    except Exception: pass
            if playlist_only:
                for attr in ("bottom_bar_wrap", "bottom_bar"):
                    w = getattr(self, attr, None)
                    if w is not None:
                        try: w.hide()
                        except Exception: pass
            else:
                try:
                    self._apply_metadata_panel_visibility()
                except Exception:
                    bar = getattr(self, "bottom_bar_wrap", None) or getattr(
                        self, "bottom_bar", None
                    )
                    if bar is not None:
                        if bool(getattr(self, "show_metadata_panel", True)):
                            bar.show()
                        else:
                            bar.hide()
            if hasattr(self, "playlist_box"):
                self.playlist_box.show()
            if self.current_is_video:
                if hasattr(self, "video_overlay"):
                    self.video_overlay.show()
                self.video_area.show()
                if playlist_only:
                    try:
                        self._apply_playlist_only_mode()
                    except Exception:
                        self._apply_video_playlist_split()
                        GLib.idle_add(self._apply_video_playlist_split)
                else:
                    self._apply_video_playlist_split()
                    GLib.idle_add(self._apply_video_playlist_split)
                    try: self._apply_metadata_panel_visibility()
                    except Exception: pass
                    def _re_show_meta():
                        try: self._apply_metadata_panel_visibility()
                        except Exception: pass
                        return False
                    GLib.idle_add(_re_show_meta)
            elif getattr(self, "lyrics_mode", False) and getattr(
                self, "lyrics_enabled", False
            ):
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
                    except Exception: pass
                if playlist_only:
                    try: self._apply_playlist_only_mode()
                    except Exception: pass
            else:
                self.show_video_area(False)
                if playlist_only:
                    try: self._apply_playlist_only_mode()
                    except Exception: pass
            if playlist_only:
                def _re_apply_po():
                    try:
                        if bool(getattr(self, "playlist_only_mode", False)):
                            self._apply_playlist_only_mode()
                    except Exception: pass
                    return False
                try: GLib.idle_add(_re_apply_po)
                except Exception: pass
            self._show_cursor()
            self._hide_fs_controls()
            self._cache_video_window_handle()
            self._nudge_video_redraw()
            if self.video_area is not None:
                self.video_area.queue_draw()
        except Exception as e:
            print("Erreur restauration UI:", e, file=sys.stderr)
            self._show_cursor()

    def _playlist_min_height_for_rows(self, rows=6):
        s = getattr(self, "_ui_s", None)
        if s is not None:
            row_h = s(26)
            chrome = s(52)
        else:
            row_h = 26
            chrome = 52
        return rows * row_h + chrome

    def _apply_video_playlist_split(self):
        if (
            not (
                getattr(self, "current_is_video", False)
                or getattr(self, "lyrics_mode", False)
            )
            or self.is_fullscreen
        ):
            return False
        if self.content_paned is None: return False
        playlist_min = self._playlist_min_height_for_rows(6)
        try:
            if hasattr(self, "playlist_box") and self.playlist_box is not None:
                self.playlist_box.set_size_request(-1, playlist_min)
        except Exception: pass
        paned_h = self.content_paned.get_allocated_height()
        if paned_h <= 1:
            win_h = self.get_allocated_height() or 600
            paned_h = max(320, win_h - 160)
        video_pos = max(120, paned_h - playlist_min)
        video_pos = min(video_pos, max(120, int(paned_h * 0.68)))
        if paned_h - video_pos < playlist_min:
            video_pos = max(100, paned_h - playlist_min)
        try:
            self.video_area.set_size_request(-1, 120)
            self.content_paned.set_position(video_pos)
        except Exception: pass
        try: self._refresh_playlist_scroll_adjustments()
        except Exception: pass
        return False

    def _refresh_playlist_scroll_adjustments(self):
        scroll = getattr(self, "playlist_scroll", None)
        tree = getattr(self, "tree", None)
        if scroll is None or tree is None: return False
        try:
            tree.queue_resize()
            scroll.queue_resize()
        except Exception: pass
        try:
            vadj = scroll.get_vadjustment()
            if vadj is not None:
                vadj.changed()
                upper = float(vadj.get_upper() or 0)
                page = float(vadj.get_page_size() or 0)
                value = float(vadj.get_value() or 0)
                if upper > page and value + page >= upper - 4.0:
                    vadj.set_value(max(0.0, upper - page))
        except Exception: pass
        return False

    def _scroll_playlist_to_path(self, child_path):
        try:
            if child_path is None or getattr(self, "tree", None) is None: return False
            fpath = child_path
            try:
                if getattr(self, "playlist_filter", None) is not None:
                    converted = self.playlist_filter.convert_child_path_to_path(
                        child_path
                    )
                    if converted is not None:
                        fpath = converted
            except Exception: pass
            if fpath is None: return False
            self.tree.scroll_to_cell(fpath, None, True, 1.0, 0.0)
            try: self.tree.set_cursor(fpath, None, False)
            except Exception: pass
        except Exception: pass
        return False

    def _scroll_playlist_to_end(self):
        try:
            filt = getattr(self, "playlist_filter", None)
            if filt is None: return False
            n = filt.iter_n_children(None)
            if n <= 0: return False
            path = Gtk.TreePath.new_from_indices([n - 1])
            self.tree.scroll_to_cell(path, None, True, 1.0, 0.0)
            self._refresh_playlist_scroll_adjustments()
        except Exception:
            try: self._refresh_playlist_scroll_adjustments()
            except Exception: pass
        return False

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
        try: self.save_state()
        except Exception: pass
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
            try: self.show_video_area(False)
            except Exception: pass
        try:
            if self.video_area is not None:
                self.video_area.queue_draw()
        except Exception: pass

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
            try: self.show_video_area(False)
            except Exception: pass
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
            if bool(getattr(self, "playlist_only_mode", False)):
                try:
                    self._apply_playlist_only_mode()
                except Exception:
                    try:
                        self._apply_video_playlist_split()
                        GLib.idle_add(self._apply_video_playlist_split)
                    except Exception: pass
            else:
                try:
                    self._apply_video_playlist_split()
                    GLib.idle_add(self._apply_video_playlist_split)
                except Exception: pass
            if hasattr(self, "osd_label"):
                self.osd_label.hide()
        except Exception as e:
            print("lyrics show area:", e, file=sys.stderr)
        try:
            if self.video_area is not None:
                self.video_area.queue_draw()
        except Exception: pass
        self._update_lyrics_index(
            getattr(self, "_last_known_position", 0.0) or 0.0
        )

    def _update_lyrics_index(self, position_sec):
        data = getattr(self, "lyrics_data", None)
        if not data or not data.get("timed"): return
        lines = data.get("lines") or []
        if not lines: return
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
            except Exception: pass

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
                if y < -line_h or y > h + line_h: continue
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
                if y < -line_h or y > h + line_h: continue
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
        except Exception: pass

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
        except Exception: pass

    def show_video_area(self, show=True):
        if show:
            self.lyrics_mode = False
            self.lyrics_data = None
            self.lyrics_current_index = -1
            self.current_is_video = True
            self._set_spectrum_active_for_media(True)
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
            self._apply_video_playlist_split()
            GLib.idle_add(self._apply_video_playlist_split)
            try:
                while Gtk.events_pending():
                    Gtk.main_iteration_do(False)
            except Exception: pass
            self._cache_video_window_handle()
            self._apply_video_overlay_handle()
            try:
                if bool(getattr(self, "playlist_only_mode", False)):
                    for attr in ("top_bar_wrap", "top_bar", "bottom_bar_wrap", "bottom_bar"):
                        w = getattr(self, attr, None)
                        if w is not None:
                            try: w.hide()
                            except Exception: pass
            except Exception: pass
        else:
            try: self._hide_fs_controls()
            except Exception: pass
            if hasattr(self, "video_overlay"):
                self.video_overlay.hide()
            self.video_area.hide()
            self.video_area.set_size_request(-1, 0)
            try:
                if hasattr(self, "playlist_box") and self.playlist_box is not None:
                    self.playlist_box.set_size_request(-1, -1)
            except Exception: pass
            try: self.content_paned.set_position(0)
            except Exception: pass
            self.current_is_video = False
            self._set_spectrum_active_for_media(False)
            self._release_pause_pixbuf()
            if self.is_fullscreen:
                self.unfullscreen()
                self._restore_ui_after_fullscreen()
        try: self._update_colorimetry_menu_sensitivity()
        except Exception: pass
    def set_subtitles_enabled(self, enabled):
        self.subtitles_enabled = bool(enabled)
        if not enabled:
            self.current_subtitle_index = -1
            if self._custom_pipeline is not None:
                try: self._set_custom_subtitle_active(-1)
                except Exception: pass
            else:
                try: self.player.set_property("current-text", -1)
                except Exception: pass
    def _clear_external_suburi(self):
        try:
            self.player.set_property("suburi", None)
        except Exception:
            try: self.player.set_property("suburi", "")
            except Exception: pass
        self._active_external_sub_path = None

    def _ensure_playbin_text_flag(self):
        try:
            flags = self.player.get_property("flags")
            TEXT = 1 << 2
            if not (flags & TEXT):
                self.player.set_property("flags", flags | TEXT)
        except Exception: pass

    def _select_playbin_external_text_track(self):
        try:
            n = int(self.player.get_property("n-text") or 0)
        except Exception:
            n = 0
        if n <= 0:
            try: self.player.set_property("current-text", 0)
            except Exception: pass
            return False
        target = n - 1
        try:
            self.player.set_property("current-text", target)
        except Exception:
            try: self.player.set_property("current-text", 0)
            except Exception: pass
        return False

    def _apply_external_suburi(self, path, restore_position=True):
        if not path or not os.path.isfile(path): return False
        was_playing = self.is_playing and not self.is_paused
        was_paused = self.is_playing and self.is_paused
        position = float(self._last_known_position or 0.0) if restore_position else 0.0
        try:
            sub_uri = GLib.filename_to_uri(os.path.abspath(path), None)
        except Exception as e:
            print("URI sous-titre externe impossible :", e, file=sys.stderr)
            return False
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
            self.player.set_state(Gst.State.READY)
            self.player.get_state(int(2 * Gst.SECOND))
            self._ensure_playbin_text_flag()
            if media_uri:
                try: self.player.set_property("uri", media_uri)
                except Exception: pass
            try:
                self.player.set_property("suburi", None)
            except Exception:
                try: self.player.set_property("suburi", "")
                except Exception: pass
            try:
                self.player.set_property("suburi", sub_uri)
            except Exception as e:
                print("set suburi:", e, file=sys.stderr)
                return False
            for enc in ("UTF-8", "utf8", "ISO-8859-1", "Windows-1252"):
                try:
                    self.player.set_property("subtitle-encoding", enc)
                    break
                except Exception: pass
            self._active_external_sub_path = path
            self.subtitles_enabled = True
            self.player.set_state(Gst.State.PAUSED)
            self.player.get_state(int(3 * Gst.SECOND))
            self._select_playbin_external_text_track()
            if was_playing:
                self.player.set_state(Gst.State.PLAYING)
                self.is_playing = True
                self.is_paused = False
                try: self.play_button.set_label("❚❚")
                except Exception: pass
            elif was_paused or self.current_path:
                self.player.set_state(Gst.State.PAUSED)
                self.is_playing = True
                self.is_paused = True
                try: self.play_button.set_label("▶")
                except Exception: pass
            else:
                self.player.set_state(Gst.State.NULL)
            def _after_suburi_ready(retry=0):
                try: self._select_playbin_external_text_track()
                except Exception: pass
                if restore_position and position > 0.05:
                    try:
                        self.player.seek_simple(
                            Gst.Format.TIME,
                            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                            int(position * Gst.SECOND),
                        )
                        self._last_known_position = position
                    except Exception: pass
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
        self.external_subtitles = []
        self._active_external_sub_path = None
        try:
            self.player.set_property("suburi", None)
        except Exception:
            try: self.player.set_property("suburi", "")
            except Exception: pass
        if not filename or is_stream(filename) or is_cdda(filename): return
        try:
            if is_dvd(filename) or is_bluray(filename): return
        except Exception: pass
        if not (is_video_file(filename) or getattr(self, "current_is_video", False)):
            if not (isinstance(filename, str) and os.path.isfile(filename)): return
        try:
            self.external_subtitles = find_external_subtitles(filename) or []
        except Exception as e:
            print("scan sous-titres externes:", e, file=sys.stderr)
            self.external_subtitles = []
        if not self.external_subtitles: return
        try: self._register_external_subtitle_tracks()
        except Exception: pass
    def _pick_preferred_external_subtitle(self, external_list):
        if not external_list: return None
        for item in external_list:
            if not item.get("lang"): return item
        return external_list[0]

    def _register_external_subtitle_tracks(self):
        active = getattr(self, "_active_external_sub_path", None)
        for item in getattr(self, "external_subtitles", None) or []:
            path = item.get("path")
            if not path: continue
            label = item.get("label") or os.path.basename(path)
            if not str(label).endswith("]"):
                label = "%s [%s]" % (label, os.path.basename(path))
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
        overlay = getattr(self, "_custom_sub_overlay", None)
        if overlay is None: return
        try:
            if getattr(self, "_custom_sub_overlay_is_text", True):
                text_sink = overlay.get_static_pad("text_sink")
            else:
                text_sink = overlay.get_static_pad("subtitle_sink")
        except Exception:
            text_sink = None
        if text_sink is None: return
        try:
            peer = text_sink.get_peer()
            if peer is not None:
                peer.unlink(text_sink)
        except Exception: pass
        for name in ("_custom_ext_sub_src", "_custom_ext_sub_q", "_custom_ext_sub_parse"):
            el = getattr(self, name, None)
            if el is None: continue
            try:
                pipe = self._custom_pipeline
                if pipe is not None and el.get_parent() is pipe:
                    el.set_state(Gst.State.NULL)
                    pipe.remove(el)
            except Exception: pass
            setattr(self, name, None)

    def _link_external_subtitle_custom(self, path):
        pipe = getattr(self, "_custom_pipeline", None)
        overlay = getattr(self, "_custom_sub_overlay", None)
        if pipe is None or overlay is None: return False
        if not path or not os.path.isfile(path): return False
        self._unlink_custom_text_sink()
        try:
            q = getattr(self, "_custom_sub_queue", None)
            if q is not None:
                qsrc = q.get_static_pad("src")
                if qsrc is not None:
                    peer = qsrc.get_peer()
                    if peer is not None:
                        qsrc.unlink(peer)
        except Exception: pass
        filesrc = Gst.ElementFactory.make("filesrc", "mkv-ext-sub-src")
        queue = Gst.ElementFactory.make("queue", "mkv-ext-sub-q")
        parser = Gst.ElementFactory.make("subparse", "mkv-ext-subparse")
        if not filesrc or not queue: return False
        try:
            filesrc.set_property("location", os.path.abspath(path))
        except Exception:
            return False
        try:
            queue.set_property("max-size-time", int(2 * Gst.SECOND))
            queue.set_property("max-size-buffers", 20)
        except Exception: pass
        try:
            pipe.add(filesrc)
            pipe.add(queue)
            if parser is not None:
                pipe.add(parser)
        except Exception as e:
            print("ext sub add:", e, file=sys.stderr)
            return False
        try:
            if not filesrc.link(queue): return False
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
            if text_sink is None: return False
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
            if not ok: return False
        except Exception as e:
            print("ext sub link:", e, file=sys.stderr)
            return False
        if parser is not None:
            for enc in ("UTF-8", "utf-8", "ISO-8859-1", "Windows-1252"):
                try:
                    parser.set_property("subtitle-encoding", enc)
                    break
                except Exception: pass
        for el in (filesrc, queue, parser):
            if el is None: continue
            try: el.sync_state_with_parent()
            except Exception: pass
        try:
            overlay.set_property("silent", False)
            if overlay.find_property("wait-text") is not None:
                overlay.set_property("wait-text", False)
        except Exception: pass
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
        try: self._register_external_subtitle_tracks()
        except Exception: pass
        return False

    def _discover_subtitle_languages(self, filepath):
        result = {}
        if GstPbutils is None or not filepath: return result
        try:
            st = os.stat(filepath)
            cache_key = (os.path.abspath(filepath), int(st.st_mtime), int(st.st_size))
        except Exception:
            cache_key = None
        cache = getattr(self, "_sub_lang_cache", None)
        if cache is None:
            self._sub_lang_cache = {}
            cache = self._sub_lang_cache
        if cache_key is not None and cache_key in cache:
            try: return dict(cache[cache_key])
            except Exception: pass
        try:
            uri = Gst.filename_to_uri(os.path.abspath(filepath))
        except Exception:
            return result
        try:
            disc = GstPbutils.Discoverer.new(int(2.5 * Gst.SECOND))
            info = disc.discover_uri(uri)
        except Exception as e:
            print("Discoverer sous-titres:", e, file=sys.stderr)
            if cache_key is not None:
                cache[cache_key] = {}
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
                    except Exception: pass
            if not lang:
                try:
                    if hasattr(stream, "get_language"):
                        raw = stream.get_language()
                        if raw:
                            lang = self._normalize_lang_code(raw) or raw
                except Exception: pass
            sid = None
            try:
                sid = stream.get_stream_id()
            except Exception:
                sid = None
            if sid:
                result[sid] = lang
        if cache_key is not None:
            try:
                cache[cache_key] = dict(result)
                while len(cache) > 12:
                    cache.pop(next(iter(cache)))
            except Exception: pass
        return result

    def _probe_video_info(self, filepath):
        if GstPbutils is None or not isinstance(filepath, str): return None
        try:
            if is_special_source(filepath) or not os.path.isfile(filepath): return None
        except Exception:
            return None
        try:
            st = os.stat(filepath)
            key = (os.path.abspath(filepath), int(st.st_mtime), int(st.st_size))
        except Exception:
            return None
        cache = getattr(self, "_video_probe_cache", None)
        if cache is None:
            self._video_probe_cache = {}
            cache = self._video_probe_cache
        if key in cache: return cache[key] or None
        try:
            uri = GLib.filename_to_uri(os.path.abspath(filepath), None)
            disc = GstPbutils.Discoverer.new(4 * Gst.SECOND)
            info = disc.discover_uri(uri)
        except Exception as exc:
            print("probe vidéo:", exc, file=sys.stderr)
            return None  # pas de mise en cache d'un échec
        try:
            res = info.get_result()
            if res in (
                GstPbutils.DiscovererResult.TIMEOUT,
                GstPbutils.DiscovererResult.ERROR,
            ):
                return None
        except Exception: pass
        result = {}
        try:
            streams = info.get_video_streams() or []
        except Exception:
            streams = []
        for stream in streams:
            try:
                try:
                    if stream.is_image(): continue
                except Exception: pass
                w = int(stream.get_width())
                h = int(stream.get_height())
                if w <= 0 or h <= 0: continue
                fps = None
                try:
                    num = int(stream.get_framerate_num())
                    den = int(stream.get_framerate_denom())
                    if num > 0 and den > 0:
                        fps = num / float(den)
                except Exception:
                    fps = None
                result = {"width": w, "height": h, "fps": fps}
                break
            except Exception:
                continue
        try:
            cache[key] = result  # {} = « pas de vidéo », mis en cache aussi
            while len(cache) > 16:
                cache.pop(next(iter(cache)))
        except Exception: pass
        return result or None

    def _normalize_lang_code(self, raw):
        if not raw: return None
        s = str(raw).strip()
        if not s or s.lower() in ("und", "unk", "unknown", ""): return None
        low = s.casefold()
        if len(s) <= 3 and s.isalpha():
            map3 = {
                "fre": "fr", "fra": "fr", "eng": "en", "ger": "de", "deu": "de",
                "spa": "es", "ita": "it", "por": "pt", "dut": "nl", "nld": "nl",
                "pol": "pl", "swe": "sv", "rus": "ru", "jpn": "ja", "chi": "zh",
                "zho": "zh", "kor": "ko", "ara": "ar", "hin": "hi", "tur": "tr",
            }
            if low in map3: return map3[low]
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
        if low in names: return names[low]
        if "-" in s or "_" in s: return self._normalize_lang_code(s.replace("_", "-").split("-")[0])
        return s[:8]

    def _delayed_subtitle_lang_probe(self, pad, track_index):
        try:
            lang = None
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
                                if tkey == Gst.TAG_TITLE and len(str(val)) > 16: continue
                                lang = val
                                break
                        except Exception: pass
            except Exception: pass
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
                except Exception: pass
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
        except Exception: pass
        return False

    def _set_subtitle_track_label(self, track_index, lang):
        try:
            for t in self.subtitle_tracks or []:
                if (
                    isinstance(t, dict)
                    and t.get("kind") == "embedded"
                    and t.get("index") == track_index
                ):
                    t["label"] = lang
                    break
            pads = getattr(self, "_custom_subtitle_pads", None) or []
            if 0 <= track_index < len(pads):
                entry = pads[track_index]
                pad = entry[0]
                is_text = entry[2] if len(entry) > 2 else True
                self._custom_subtitle_pads[track_index] = (pad, lang, is_text)
        except Exception: pass
        return False

    def refresh_subtitle_tracks(self):
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
                    except Exception: pass
                    label = lang if lang else self.tr("Piste ") + str(i + 1)
                    self.subtitle_tracks.append({
                        "kind": "embedded",
                        "index": i,
                        "path": None,
                        "label": label,
                    })
        except Exception as e:
            print("Erreur lecture pistes sous-titres intégrées:", e, file=sys.stderr)
        active_ext = self._active_external_sub_path
        for item in self.external_subtitles:
            path = item["path"]
            label = item["label"]
            if not label.endswith("]"):
                label = f"{label} [{os.path.basename(path)}]"
            self.subtitle_tracks.append({
                "kind": "external",
                "index": None,
                "path": path,
                "label": label,
            })
        self.current_subtitle_index = -1
        if not self.subtitles_enabled: return
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
        if getattr(self, "_custom_sub_linked", False): return False
        if pipe is None or pad is None or sub_overlay is None: return False
        queue = Gst.ElementFactory.make("queue", "mkv-squeue")
        if queue is None: return False
        try:
            queue.set_property("leaky", 2)
            queue.set_property("max-size-buffers", 50)
            queue.set_property("max-size-time", int(2 * Gst.SECOND))
        except Exception: pass
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
            if pad.link(queue.get_static_pad("sink")) != Gst.PadLinkReturn.OK: return False
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
        if not ok: return False
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
        pref = getattr(self, "_preferred_custom_sub_index", -1)
        if pref is None or pref < 0: return False
        if not getattr(self, "subtitles_enabled", True): return False
        pipe = getattr(self, "_custom_pipeline", None)
        overlay = getattr(self, "_custom_sub_overlay", None)
        pads = getattr(self, "_custom_subtitle_pads", None) or []
        if pipe is None or overlay is None or not pads: return False
        if pref >= len(pads): return False
        entry = pads[pref]
        pad = entry[0]
        nice = entry[1] if len(entry) > 1 else "Sub"
        is_text = entry[2] if len(entry) > 2 else True
        if not is_text: return False
        if getattr(self, "_custom_sub_linked", False):
            try: overlay.set_property("silent", False)
            except Exception: pass
            return True
        ok = self._link_custom_subtitle_pad_now(pipe, pad, pref, nice, overlay)
        if ok:
            try: overlay.set_property("silent", False)
            except Exception: pass
        return ok

    def _link_best_subtitle_pad_deferred(self):
        self._custom_sub_wait_scheduled = False
        return False

    def _set_custom_subtitle_active(self, pad_index):
        pads = getattr(self, "_custom_subtitle_pads", None) or []
        queue = getattr(self, "_custom_sub_queue", None)
        overlay = getattr(self, "_custom_sub_overlay", None)
        def _text_sink():
            if overlay is None: return None
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
            if queue is not None:
                qsink = queue.get_static_pad("sink")
                if qsink is not None:
                    peer = qsink.get_peer()
                    if peer is not None:
                        try: peer.unlink(qsink)
                        except Exception: pass
            for entry in pads:
                p = entry[0] if entry else None
                if p is None: continue
                try:
                    pr = p.get_peer()
                    if pr is not None:
                        p.unlink(pr)
                except Exception: pass
        if pad_index < 0:
            self._custom_sub_muted = True
            self._custom_subtitle_index = -1
            _unlink_all()
            try: self._unlink_custom_text_sink()
            except Exception: pass
            try:
                if overlay is not None:
                    overlay.set_property("silent", True)
            except Exception: pass
            return
        if pad_index >= len(pads): return
        entry = pads[pad_index]
        new_pad = entry[0]
        lab = entry[1] if len(entry) > 1 else "Sub"
        is_text = entry[2] if len(entry) > 2 else True
        if not is_text:
            self.status_label.set_text(
                self.tr("Sous-titres") + " — image non supportée (PGS/VobSub)"
            )
            return
        try:
            try:
                if overlay is not None:
                    overlay.set_property("silent", False)
            except Exception: pass
            _unlink_all()
            if queue is None:
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
            if qsink is None: return
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
        except Exception as e:
            print("custom subtitle switch:", e, file=sys.stderr)

    def set_subtitle_track(self, track_list_index):
        try:
            if (
                track_list_index is not None
                and track_list_index >= 0
                and track_list_index == getattr(self, "current_subtitle_index", -1)
                and getattr(self, "subtitles_enabled", False)
            ):
                return
            if getattr(self, "_subtitle_switch_busy", False): return
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
                    except Exception: pass
                    self._custom_subtitle_index = -1
                else:
                    try: self.player.set_property("current-text", -1)
                    except Exception: pass
                self.status_label.set_text(self.tr("Sous-titres désactivés"))
                return
            if track_list_index >= len(self.subtitle_tracks): return
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
                try:
                    pipe.set_state(Gst.State.PAUSED)
                    pipe.get_state(int(800 * Gst.MSECOND))
                except Exception: pass
                try:
                    ov = getattr(self, "_custom_sub_overlay", None)
                    if ov is not None:
                        try: ov.set_property("wait-text", False)
                        except Exception: pass
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
                    try: pipe.set_state(Gst.State.PLAYING)
                    except Exception: pass
                self.current_subtitle_index = track_list_index
                self.status_label.set_text(
                    self.tr("Sous-titres") + " : " + track["label"]
                )
                return
            # ---- Playbin : pistes intégrées ----
            if track.get("kind") == "embedded":
                self.subtitles_enabled = True
                if self._active_external_sub_path:
                    try: self._clear_external_suburi()
                    except Exception: pass
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
            if track.get("kind") == "external":
                path = track.get("path")
                if not path: return
                if self._custom_pipeline is not None:
                    pipe = self._custom_pipeline
                    was_playing = self.is_playing and not self.is_paused
                    position = float(getattr(self, "_last_known_position", 0) or 0)
                    try:
                        pipe.set_state(Gst.State.PAUSED)
                        pipe.get_state(int(2 * Gst.SECOND))
                    except Exception: pass
                    ok = False
                    try:
                        ok = self._link_external_subtitle_custom(path)
                    except Exception as e:
                        print("ext sub custom:", e, file=sys.stderr)
                    if ok and position > 0.15:
                        try:
                            self._seek_with_rate(
                                pipe,
                                Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                                int(position * Gst.SECOND),
                            )
                            pipe.get_state(int(2 * Gst.SECOND))
                            self._last_known_position = position
                        except Exception as e:
                            print("ext sub seek:", e, file=sys.stderr)
                    if was_playing:
                        try: pipe.set_state(Gst.State.PLAYING)
                        except Exception: pass
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
        self.audio_tracks = []
        self.current_audio_index = -1
        try:
            n_audio = self.player.get_property("n-audio")
            if n_audio is None or n_audio <= 0: return
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
                except Exception: pass
                label = lang if lang else self.tr("Piste ") + str(i + 1)
                self.audio_tracks.append((i, label))
        except Exception as e:
            print("Erreur lecture pistes audio:", e, file=sys.stderr)

    def set_audio_track(self, index):
        if self._custom_pipeline is not None:
            self._set_custom_audio_track(index)
            return
        try:
            self.player.set_property("current-audio", index)
            self.current_audio_index = index
        except Exception as e:
            print("Erreur piste audio:", e, file=sys.stderr)

    def _set_custom_audio_track(self, index):
        if not self._custom_audio_pads or self._custom_audio_queue is None: return
        if index < 0 or index >= len(self._custom_audio_pads): return
        if index == self._custom_audio_index: return
        try:
            qsink = self._custom_audio_queue.get_static_pad("sink")
            if qsink is None: return
            peer = qsink.get_peer()
            if peer is not None:
                peer.unlink(qsink)
            new_pad, label = self._custom_audio_pads[index]
            if new_pad.link(qsink) == Gst.PadLinkReturn.OK:
                self._custom_audio_index = index
                self.current_audio_index = index
                try:
                    self._seek_with_rate(
                        self._custom_pipeline,
                        Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                        max(0, int(self._last_known_position * Gst.SECOND)),
                    )
                except Exception: pass
                self.status_label.set_text(
                    self.tr("Piste audio") + " : " + label
                )
            else:
                if peer is not None:
                    peer.link(qsink)
        except Exception as e:
            print("Erreur bascule audio custom:", e, file=sys.stderr)

    def open_system_bluetooth(self, *args):
        commands = [
            ["blueman-manager"],
            ["blueman-applet"],
            ["gnome-control-center", "bluetooth"],
            ["systemsettings5", "kcm_bluetooth"],
            ["systemsettings", "kcm_bluetooth"],
            ["gnome-bluetooth"],
            ["xfce4-settings-manager"],
            ["unity-control-center", "bluetooth"],
        ]
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
        try:
            self.status_label.set_text(
                self.tr("Aucun gestionnaire Bluetooth trouvé")
            )
        except Exception: pass
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
        if not self.current_is_video or self.video_area is None: return
        window = self.video_area.get_window()
        if window is None:
            self.status_label.set_text(self.tr("Capture impossible"))
            return
        try:
            width = window.get_width()
            height = window.get_height()
            if width <= 0 or height <= 0: return
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
        try:
            downloads = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
            if downloads:
                dialog.set_current_folder(downloads)
            else:
                dialog.set_current_folder(os.path.expanduser("~/Downloads"))
        except Exception:
            dialog.set_current_folder(os.path.expanduser("~"))
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
        from datetime import datetime
        default_name = "capture_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".png"
        dialog.set_current_name(default_name)
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            path = dialog.get_filename()
            chosen_filter = dialog.get_filter()
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

    def _should_use_custom_pipeline(self, path):
        if not isinstance(path, str): return False
        if is_stream(path) or is_cdda(path) or is_dvd(path) or is_bluray(path): return False
        if not os.path.isfile(path): return False
        if os.path.splitext(path)[1].lower() not in (".mkv", ".webm", ".mka"): return False
        if self._mkv_has_ac3_family_audio(path): return True
        if self._mkv_has_av1_video(path): return True
        return False

    def _mkv_probe_flags(self, path, size=512 * 1024):
        try:
            st = os.stat(path)
            key = (os.path.abspath(path), int(st.st_mtime), int(st.st_size))
        except Exception:
            key = None
        cache = getattr(self, "_mkv_probe_cache", None)
        if cache is None:
            self._mkv_probe_cache = {}
            cache = self._mkv_probe_cache
        if key is not None and key in cache: return cache[key]
        flags = {"ac3": False, "av1": False}
        data = b""
        try:
            with open(path, "rb") as f:
                data = f.read(size)
            if data:
                flags["ac3"] = (b"A_EAC3" in data) or (b"A_EAC-3" in data)
                flags["av1"] = (
                    (b"V_AV1" in data)
                    or (b"V_AV01" in data)
                    or (b"av01" in data)
                )
        except Exception: pass
        finally:
            data = None
        if key is not None:
            cache[key] = flags
            while len(cache) > 8:
                try:
                    cache.pop(next(iter(cache)))
                except Exception:
                    break
        return flags

    def _mkv_probe_header(self, path, size=512 * 1024):
        try:
            with open(path, "rb") as f:
                return f.read(size)
        except Exception:
            return b""

    def _mkv_has_ac3_family_audio(self, path):
        try:
            return bool(self._mkv_probe_flags(path).get("ac3"))
        except Exception:
            return False

    def _mkv_has_av1_video(self, path):
        try:
            return bool(self._mkv_probe_flags(path).get("av1"))
        except Exception:
            return False

    def _demote_hw_av1_decoders(self):
        apply_safe_gstreamer_ranks()

    def _active_pipeline(self):
        if self._custom_pipeline is not None: return self._custom_pipeline
        return self.player

    def _detach_equalizer_for_custom(self):
        eq = getattr(self, "equalizer", None)
        if eq is None: return None
        try:
            parent = eq.get_parent()
            if parent is not None:
                try: eq.set_state(Gst.State.NULL)
                except Exception: pass
                parent.remove(eq)
        except Exception as e:
            print("detach equalizer:", e, file=sys.stderr)
            return None
        self._need_playbin_rebind = True
        return eq

    def _destroy_custom_pipeline(self):
        pipe = self._custom_pipeline
        if pipe is None: return
        try:
            bus = pipe.get_bus()
            for hid in getattr(self, "_custom_bus_handler_ids", []) or []:
                try: bus.disconnect(hid)
                except Exception: pass
            try: bus.remove_signal_watch()
            except Exception: pass
        except Exception: pass
        self._custom_bus_handler_ids = []
        try:
            pipe.set_state(Gst.State.NULL)
            pipe.get_state(200 * Gst.MSECOND)
        except Exception: pass
        for el in (getattr(self, "spectrum", None), getattr(self, "equalizer", None)):
            if el is None: continue
            try:
                parent = el.get_parent()
                if parent is not None:
                    try: el.set_state(Gst.State.NULL)
                    except Exception: pass
                    parent.remove(el)
            except Exception: pass
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
        try: self._mkv_probe_cache = {}
        except Exception: pass
        try: GLib.idle_add(lambda: (trim_memory(force=True), False)[1])
        except Exception: pass
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
        pipe = Gst.Pipeline.new("mkv-local-pipeline")
        filesrc = Gst.ElementFactory.make("filesrc", "mkv-src")
        demux = Gst.ElementFactory.make("matroskademux", "mkv-demux")
        if not filesrc or not demux: return None
        filesrc.set_property("location", os.path.abspath(filepath))
        pipe.add(filesrc)
        pipe.add(demux)
        if not filesrc.link(demux): return None
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
        custom_eq = self._detach_equalizer_for_custom()
        video_sink = self._create_video_sink()
        if video_sink is None:
            video_sink = Gst.ElementFactory.make("autovideosink", "mkv-vsink")
        self.video_sink = video_sink
        self._video_sink_is_real = video_sink is not None
        try:
            if video_sink is not None and video_sink.find_property("force-aspect-ratio"):
                video_sink.set_property("force-aspect-ratio", True)
        except Exception: pass
        sub_overlay = Gst.ElementFactory.make("textoverlay", "mkv-subov")
        self._custom_sub_overlay_is_text = True
        if sub_overlay is None:
            sub_overlay = Gst.ElementFactory.make("subtitleoverlay", "mkv-subov")
            self._custom_sub_overlay_is_text = False
        self._custom_sub_overlay = sub_overlay
        if sub_overlay is not None:
            pipe.add(sub_overlay)
            for prop, val in (
                ("shaded-background", False),
                ("draw-shadow", True),
                ("draw-outline", True),
                ("font-desc", "Sans Bold 12"),
                ("valignment", 1),  # bottom (pas 2=top)
                ("halignment", 1),  # center
                ("line-alignment", 1),  # center
                ("silent", True),
                ("wait-text", False),
                ("deltay", -12),  # légère marge au-dessus du bord bas
            ):
                try: sub_overlay.set_property(prop, val)
                except Exception: pass
            for prop, val in (
                ("valignment", "bottom"),
                ("halignment", "center"),
                ("font-desc", "Sans Bold 12"),
            ):
                try: sub_overlay.set_property(prop, val)
                except Exception: pass
            try:
                sub_overlay.set_property("outline-color", 0xFF000000)
                sub_overlay.set_property("color", 0xFFFFFFFF)
            except Exception: pass
        def _tune_queue(q, is_video=False, for_av1=False, for_eac3=False):
            if q is None: return
            try:
                soft = getattr(self, "performance_mode", "hardware") == "software"
                if is_video:
                    if for_av1:
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
                if is_video:
                    try: q.set_property("leaky", 0)
                    except Exception: pass
                elif for_eac3 or for_av1:
                    try: q.set_property("leaky", 0)
                    except Exception: pass
                else:
                    try:
                        q.set_property("leaky", 2)  # GST_QUEUE_LEAK_DOWNSTREAM
                    except Exception:
                        try: q.set_property("leaky", True)
                        except Exception: pass
            except Exception: pass
        def _pad_language(pad, struct):
            if struct is not None:
                for key in ("language", "language-code", "lang"):
                    try:
                        if struct.has_field(key):
                            ok, val = struct.get_string(key)
                            if ok and val and val not in ("und", "unk", ""): return val
                    except Exception: pass
            try:
                event = pad.get_sticky_event(Gst.EventType.TAG, 0)
                if event is not None:
                    tags = event.parse_tag()
                    for tkey in (Gst.TAG_LANGUAGE_CODE, Gst.TAG_LANGUAGE_NAME):
                        try:
                            ok, val = tags.get_string(tkey)
                            if ok and val and val not in ("und", "unk", ""): return val
                        except Exception: pass
            except Exception: pass
            return None
        def _configure_software_decoder(decode):
            if decode is None: return
            try:
                ncpu = max(2, min(8, (os.cpu_count() or 2)))
                for prop in ("max-threads", "n-threads", "threads"):
                    try:
                        if decode.find_property(prop):
                            decode.set_property(prop, ncpu)
                            break
                    except Exception:
                        continue
                try:
                    if decode.find_property("max-frame-delay") is not None:
                        decode.set_property("max-frame-delay", 2)
                except Exception: pass
                for prop, val in (
                    ("frame-threads", max(1, ncpu // 2)),
                    ("tile-threads", max(1, min(4, ncpu // 2))),
                ):
                    try:
                        if decode.find_property(prop):
                            decode.set_property(prop, val)
                    except Exception: pass
            except Exception: pass
        def _configure_videoconvert(convert):
            if convert is None: return
            try:
                if convert.find_property("n-threads"):
                    convert.set_property(
                        "n-threads", max(1, min(4, (os.cpu_count() or 2)))
                    )
            except Exception: pass
            try:
                if convert.find_property("dither"):
                    convert.set_property("dither", 0)
            except Exception: pass
        def _sink_wants_gl():
            try:
                sink = video_sink
                if sink is None: return False
                fac = sink.get_factory()
                name = (fac.get_name() if fac is not None else "") or ""
                return "gl" in name.lower()
            except Exception:
                return False
        def _make_gl_convert_bin():
            try:
                upload = Gst.ElementFactory.make("glupload", "mkv-glupload")
                gconv = Gst.ElementFactory.make("glcolorconvert", "mkv-glcconv")
            except Exception:
                return None
            if upload is None or gconv is None: return None
            try:
                b = Gst.Bin.new("mkv-glconvert-bin")
                b.add(upload)
                b.add(gconv)
                if not upload.link(gconv): return None
                sinkpad = upload.get_static_pad("sink")
                srcpad = gconv.get_static_pad("src")
                if sinkpad is None or srcpad is None: return None
                gp_sink = Gst.GhostPad.new("sink", sinkpad)
                gp_src = Gst.GhostPad.new("src", srcpad)
                if gp_sink is None or gp_src is None: return None
                try:
                    gp_sink.set_active(True)
                    gp_src.set_active(True)
                except Exception: pass
                if not b.add_pad(gp_sink) or not b.add_pad(gp_src): return None
                return b
            except Exception as exc:
                if DEBUG_MYOSTOOX:
                    print("gl convert bin:", exc, file=sys.stderr)
                return None
        def _make_video_convert_element(preferred=None):
            if preferred is not None: return preferred
            if _sink_wants_gl():
                glbin = _make_gl_convert_bin()
                if glbin is not None:
                    if DEBUG_MYOSTOOX:
                        print(
                            "Myostoox: MKV convert GL (glupload+glcolorconvert)",
                            file=sys.stderr,
                        )
                    return glbin
            convert = Gst.ElementFactory.make("videoconvert", "mkv-vconv")
            _configure_videoconvert(convert)
            return convert
        def _make_h265_chain():
            parse = Gst.ElementFactory.make("h265parse", "mkv-h265parse")
            if parse is None: return None
            mode = getattr(self, "performance_mode", "hardware")
            if mode == "hardware":
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
                    if decode is None: continue
                    convert = None
                    if post_name:
                        convert = Gst.ElementFactory.make(post_name, "mkv-vpost")
                    convert = _make_video_convert_element(preferred=convert)
                    if convert is None: continue
                    return parse, decode, convert, dec_name
            decode = Gst.ElementFactory.make("avdec_h265", "mkv-vdec")
            convert = _make_video_convert_element()
            if not decode or not convert: return None
            _configure_software_decoder(decode)
            return parse, decode, convert, "avdec_h265"
        def _make_h264_chain():
            parse = Gst.ElementFactory.make("h264parse", "mkv-h264parse")
            if parse is None: return None
            mode = getattr(self, "performance_mode", "hardware")
            if mode == "hardware":
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
                    if decode is None: continue
                    convert = None
                    if post_name:
                        convert = Gst.ElementFactory.make(post_name, "mkv-vpost")
                    convert = _make_video_convert_element(preferred=convert)
                    if convert is None: continue
                    return parse, decode, convert, dec_name
            decode = Gst.ElementFactory.make("avdec_h264", "mkv-vdec")
            convert = _make_video_convert_element()
            if not decode or not convert: return None
            _configure_software_decoder(decode)
            return parse, decode, convert, "avdec_h264"
        def _make_av1_chain():
            parse = Gst.ElementFactory.make("av1parse", "mkv-av1parse")
            mode = getattr(self, "performance_mode", "hardware")
            if mode == "hardware":
                for dec_name, post_name in (
                    ("nvav1dec", None),
                    ("vaav1dec", "vapostproc"),
                    ("vaapiav1dec", "vaapipostproc"),
                ):
                    decode = Gst.ElementFactory.make(dec_name, "mkv-vdec")
                    if decode is None: continue
                    convert = None
                    if post_name:
                        convert = Gst.ElementFactory.make(post_name, "mkv-vpost")
                    convert = _make_video_convert_element(preferred=convert)
                    if convert is None: continue
                    return parse, decode, convert, dec_name
            for dec_name in _AV1_SOFT_DECODER_ORDER:
                decode = Gst.ElementFactory.make(dec_name, "mkv-vdec")
                if decode is None: continue
                convert = _make_video_convert_element()
                if convert is None: continue
                _configure_software_decoder(decode)
                return parse, decode, convert, dec_name
            return None
        def _make_vp9_chain():
            parse = Gst.ElementFactory.make("vp9parse", "mkv-vp9parse")
            if getattr(self, "performance_mode", "hardware") == "hardware":
                for dec_name, post_name in (
                    ("nvvp9dec", None),
                    ("vavp9dec", "vapostproc"),
                    ("vaapivp9dec", "vaapipostproc"),
                    ("qsvvp9dec", None),
                ):
                    decode = Gst.ElementFactory.make(dec_name, "mkv-vdec")
                    if decode is None: continue
                    convert = None
                    if post_name:
                        convert = Gst.ElementFactory.make(post_name, "mkv-vpost")
                    convert = _make_video_convert_element(preferred=convert)
                    if convert is None: continue
                    return parse, decode, convert, dec_name
            decode = Gst.ElementFactory.make("avdec_vp9", "mkv-vdec")
            convert = _make_video_convert_element()
            if not decode or not convert: return None
            _configure_software_decoder(decode)
            return parse, decode, convert, "avdec_vp9"
        def _make_vp8_chain():
            parse = Gst.ElementFactory.make("vp8parse", "mkv-vp8parse")
            if getattr(self, "performance_mode", "hardware") == "hardware":
                for dec_name, post_name in (
                    ("nvvp8dec", None),
                    ("vavp8dec", "vapostproc"),
                    ("vaapivp8dec", "vaapipostproc"),
                ):
                    decode = Gst.ElementFactory.make(dec_name, "mkv-vdec")
                    if decode is None: continue
                    convert = None
                    if post_name:
                        convert = Gst.ElementFactory.make(post_name, "mkv-vpost")
                    convert = _make_video_convert_element(preferred=convert)
                    if convert is None: continue
                    return parse, decode, convert, dec_name
            decode = Gst.ElementFactory.make("avdec_vp8", "mkv-vdec")
            convert = _make_video_convert_element()
            if not decode or not convert: return None
            _configure_software_decoder(decode)
            return parse, decode, convert, "avdec_vp8"
        def _make_mpeg2_chain():
            parse = Gst.ElementFactory.make("mpegvideoparse", "mkv-mpeg2parse")
            if getattr(self, "performance_mode", "hardware") == "hardware":
                for dec_name, post_name in (
                    ("nvmpeg2videodec", None),
                    ("nvmpegvideodec", None),
                    ("vampeg2dec", "vapostproc"),
                ):
                    decode = Gst.ElementFactory.make(dec_name, "mkv-vdec")
                    if decode is None: continue
                    convert = None
                    if post_name:
                        convert = Gst.ElementFactory.make(post_name, "mkv-vpost")
                    convert = _make_video_convert_element(preferred=convert)
                    if convert is None: continue
                    return parse, decode, convert, dec_name
            decode = Gst.ElementFactory.make("avdec_mpeg2video", "mkv-vdec")
            convert = _make_video_convert_element()
            if not decode or not convert: return None
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
            if media.startswith("video/") and not self._custom_video_linked:
                if video_sink is None: return
                if "png" in media or "image/" in media: return
                queue = Gst.ElementFactory.make("queue", "mkv-vqueue")
                if not queue: return
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
                    for el in (queue, parse, decode, convert):
                        if el is None: continue
                        pipe.add(el)
                        el.sync_state_with_parent()
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
                            except Exception: pass
                            pipe.add(smooth_q)
                            smooth_q.sync_state_with_parent()
                    if is_av1 and video_sink is not None:
                        for prop, val in (
                            ("max-lateness", int(2 * Gst.SECOND)),
                            ("qos", False),
                        ):
                            try: video_sink.set_property(prop, val)
                            except Exception: pass
                    if video_sink.get_parent() is None:
                        pipe.add(video_sink)
                        video_sink.sync_state_with_parent()
                    try:
                        pad.link(queue.get_static_pad("sink"))
                    except Exception:
                        return
                    if parse is not None:
                        if not (queue.link(parse) and parse.link(decode)): return
                    else:
                        if not queue.link(decode): return
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
                    try: GLib.idle_add(self._ensure_video_sink_geometry)
                    except Exception: pass
                    return
                decode = Gst.ElementFactory.make("decodebin", "mkv-vdecode")
                convert = _make_video_convert_element()
                if not decode or not convert: return
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
                try: GLib.idle_add(self._ensure_video_sink_geometry)
                except Exception: pass
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
                if self._custom_audio_linked: return
                queue = Gst.ElementFactory.make("queue", "mkv-aqueue")
                convert = Gst.ElementFactory.make("audioconvert", "mkv-aconv")
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
                tempo = Gst.ElementFactory.make("scaletempo", "mkv-atempo")
                if resample is not None:
                    try: resample.set_property("quality", 8)
                    except Exception: pass
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
                if not queue or not decode or not convert or not sink: return
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
                        boost = 2.4 if is_eac3 else (1.6 if is_ac3 else 1.0)
                        gain = 0.0 if getattr(self, "is_muted", False) else min(
                            3.5, vol * boost
                        )
                        volume.set_property("volume", gain)
                    except Exception: pass
                    self._custom_volume = volume
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
                    try: sink.set_property(prop, val)
                    except Exception: pass
                try: sink.set_property("sync", True)
                except Exception: pass
                amplify = None
                if is_eac3 or is_ac3:
                    amplify = Gst.ElementFactory.make(
                        "audioamplify", "mkv-aamp"
                    )
                    if amplify is not None:
                        try:
                            amplify.set_property("amplification", 2.0)
                        except Exception:
                            try: amplify.set_property("amplification", 1.8)
                            except Exception: pass
                eq_el = custom_eq
                try:
                    if eq_el is not None and eq_el.get_parent() is not None:
                        eq_el = None
                except Exception:
                    eq_el = None
                elems = [queue, decode, convert]
                if acaps is not None:
                    elems.append(acaps)
                if tempo is not None:
                    elems.append(tempo)
                if spec is not None:
                    elems.append(spec)
                if eq_el is not None:
                    elems.append(eq_el)
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
                if not queue.link(decode): return
                def finish(from_el):
                    prev = from_el
                    if acaps is not None:
                        if prev.link(acaps):
                            prev = acaps
                    if tempo is not None:
                        if prev.link(tempo):
                            prev = tempo
                    if spec is not None:
                        if prev.link(spec):
                            prev = spec
                    if eq_el is not None:
                        if prev.link(eq_el):
                            prev = eq_el
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
            if not media_l:
                try:
                    tmpl = pad.get_pad_template()
                    if tmpl is not None:
                        tcaps = tmpl.get_caps()
                        if tcaps and not tcaps.is_empty():
                            tstruct = tcaps.get_structure(0)
                            if tstruct:
                                media_l = (tstruct.get_name() or "").lower()
                except Exception: pass
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
                                if not ok or not val: continue
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
                except Exception: pass
            is_sub = is_text_sub or is_bitmap_sub
            if not is_sub: return
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
                except Exception: pass
                return False
            GLib.idle_add(_append_track)
            GLib.timeout_add(
                400, self._delayed_subtitle_lang_probe, pad, sidx
            )
            GLib.timeout_add(
                1200, self._delayed_subtitle_lang_probe, pad, sidx
            )
            return
        demux.connect("pad-added", on_pad_added)
        return pipe

    def _ensure_video_sink_geometry(self):
        if not getattr(self, "current_is_video", False): return
        try:
            if self.video_area is not None:
                alloc = self.video_area.get_allocation()
                if alloc.height < 8 or alloc.width < 8:
                    self.video_area.set_size_request(-1, max(180, alloc.height or 180))
                if not self.video_area.get_realized():
                    self.video_area.realize()
                self.video_area.show()
                for _ in range(6):
                    if not Gtk.events_pending(): break
                    Gtk.main_iteration_do(False)
        except Exception: pass
        try: self._cache_video_window_handle()
        except Exception: pass
    def _play_file_custom_mkv(self, filename):
        self._destroy_custom_pipeline()
        try:
            self.player.set_state(Gst.State.NULL)
            self.player.get_state(Gst.SECOND)
        except Exception: pass
        try: self._apply_safe_audio_decoder_ranks()
        except Exception: pass
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
        if pipe is None: return False
        self._custom_pipeline = pipe
        self._attach_custom_bus(pipe)
        if self.current_is_video:
            self._ensure_video_sink_geometry()
            try:
                sink = getattr(self, "video_sink", None)
                handle = getattr(self, "_video_window_handle", None)
                if sink is not None and handle and isinstance(sink, GstVideo.VideoOverlay):
                    GstVideo.VideoOverlay.set_window_handle(sink, handle)
            except Exception: pass
        ret = pipe.set_state(Gst.State.PAUSED)
        if ret == Gst.StateChangeReturn.FAILURE:
            self._destroy_custom_pipeline()
            return False
        try: pipe.get_state(3 * Gst.SECOND)
        except Exception: pass
        pref = getattr(self, "_preferred_custom_sub_index", -1)
        if pref is not None and pref >= 0:
            for _ in range(20):
                try:
                    while Gtk.events_pending():
                        Gtk.main_iteration_do(False)
                except Exception: pass
                pads = getattr(self, "_custom_subtitle_pads", None) or []
                if len(pads) > pref: break
                time.sleep(0.025)
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
                if pipe2 is None: return False
                self._custom_pipeline = pipe2
                self._attach_custom_bus(pipe2)
                if self.current_is_video:
                    self._cache_video_window_handle()
                    self._apply_video_overlay_handle()
                pipe2.set_state(Gst.State.PAUSED)
                try: pipe2.get_state(3 * Gst.SECOND)
                except Exception: pass
                try: self._link_preferred_custom_subtitle_while_paused()
                except Exception: pass
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
                boost = 1.0
                if self._custom_pipeline is not None:
                    boost = 2.4
                self._custom_volume.set_property(
                    "volume", min(3.5, value * boost)
                )
            except Exception: pass
        try: self.player.set_property("volume", value)
        except Exception: pass
    def play_file(self, filename, resume=True):
        if not filename: return
        if getattr(self, "_resume_prompt_open", False): return
        start_at = 0.0
        try:
            self._resume_save_current()
            try:
                if (
                    isinstance(filename, str)
                    and not is_special_source(filename)
                    and os.path.isfile(filename)
                ):
                    filename = os.path.abspath(filename)
            except Exception: pass
            if (
                resume
                and getattr(self, "resume_prompt_enabled", True)
                and self._resume_eligible(filename)
            ):
                start_at = self._resume_ask(filename)
        except Exception as exc:
            print("resume:", exc, file=sys.stderr)
            start_at = 0.0
        try:
            if getattr(self, "_recording", False):
                if filename != getattr(self, "current_path", None):
                    self._stop_recording(show_save_dialog=True)
        except Exception: pass
        try:
            if is_cdda(filename) or is_stream(filename) or is_dvd(filename) or is_bluray(filename):
                uri = filename
            else:
                uri = GLib.filename_to_uri(os.path.abspath(filename), None)
            try:
                if filename != getattr(self, "_hw_fallback_path", None):
                    self._hw_fallback_path = None
                    if getattr(self, "performance_mode", "hardware") == "hardware":
                        self._apply_decoder_performance_mode(force_software=False)
            except Exception: pass
            self._playback_generation += 1
            gen = self._playback_generation
            self._destroy_custom_pipeline()
            try:
                self.player.set_state(Gst.State.NULL)
                self.player.get_state(200 * Gst.MSECOND)
            except Exception: pass
            try:
                bus = self.player.get_bus()
                if bus is not None:
                    while True:
                        msg = bus.pop_filtered(
                            Gst.MessageType.EOS | Gst.MessageType.ERROR
                        )
                        if msg is None: break
            except Exception: pass
            if gen != self._playback_generation: return
            self.current_path = filename
            self._refresh_playlist_current_style()
            self.current_duration = 0
            try: self._scrobble_on_track_start(filename)
            except Exception: pass
            self.current_is_cdda = is_cdda(filename)
            if getattr(self, "_need_playbin_rebind", False):
                self._need_playbin_rebind = False
                try:
                    self._video_sink_is_real = False
                    self._ensure_video_sink()
                except Exception: pass
                try: self.rebuild_audio_filter(force=True, restart=False)
                except Exception: pass
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
                f"0:00 • {format_time(self.current_duration)}"
                if self.current_duration > 0 else "0:00 • 0:00"
            )
            self.icy_title = ""
            self.icy_artist = ""
            if is_stream(filename):
                self.current_radio_name = self.find_radio_name(filename)
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
            prefer_video = bool(getattr(self, "_prefer_video", False))
            is_known_radio = bool(
                is_stream(filename)
                and (
                    getattr(self, "current_radio_name", None)
                    or self.find_radio_name(filename)
                )
            )
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
                try: self.current_tv_name = self.find_tv_name(filename)
                except Exception: pass
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
            try:
                want_pitch = (
                    self.pitch is not None
                    and is_pure_audio_file(filename)
                    and (
                        abs(self.pitch_value - 1.0) > 0.001
                        or abs(self.tempo_value - 1.0) > 0.001
                    )
                )
                want_spectrum = not bool(getattr(self, "current_is_video", False))
                need_rebuild = (
                    want_pitch != bool(getattr(self, "_pitch_active", False))
                    or want_spectrum != bool(getattr(self, "_spectrum_in_filter", True))
                )
                if need_rebuild:
                    self.rebuild_audio_filter(force=True, restart=False)
            except Exception: pass
            try: self._update_pitch_menu_sensitivity()
            except Exception: pass
            try: self._update_colorimetry_menu_sensitivity()
            except Exception: pass
            try: self._rebuild_video_color_filter(restore_playback=False)
            except Exception: pass
            self.set_subtitles_enabled(self.subtitles_enabled)
            self.subtitle_tracks = []
            self.current_subtitle_index = -1
            self._preferred_custom_sub_index = -1
            self.audio_tracks = []
            self.current_audio_index = -1
            self._prepare_external_subtitles_for(filename)
            try: self._register_external_subtitle_tracks()
            except Exception: pass
            try: self.player.set_property("current-text", -1)
            except Exception: pass
            if self.current_is_video:
                try:
                    while Gtk.events_pending():
                        Gtk.main_iteration_do(False)
                except Exception: pass
                self._cache_video_window_handle()
                self._apply_video_overlay_handle()
            used_custom = False
            if self._should_use_custom_pipeline(filename):
                used_custom = self._play_file_custom_mkv(filename)
                if not used_custom:
                    if DEBUG_MYOSTOOX:
                        print(
                            "Myostoox: pipeline MKV custom échoué → playbin",
                            file=sys.stderr,
                        )
                else:
                    try:
                        self._maybe_attach_external_to_custom()
                    except Exception as e:
                        print("ext sub custom:", e, file=sys.stderr)
                    try: GLib.timeout_add(1200, self._maybe_attach_external_to_custom)
                    except Exception: pass
            if not used_custom:
                self._destroy_custom_pipeline()
                if getattr(self, "_need_playbin_rebind", False):
                    self._need_playbin_rebind = False
                    try:
                        self._video_sink_is_real = False
                        self._ensure_video_sink()
                    except Exception: pass
                    try: self.rebuild_audio_filter(force=True, restart=False)
                    except Exception: pass
                try:
                    audio_only = not bool(getattr(self, "current_is_video", False))
                    self._configure_playbin_buffers(audio_only=audio_only)
                except Exception: pass
                if getattr(self, "current_is_video", False):
                    try: self._ensure_video_sink()
                    except Exception: pass
                else:
                    try: self._release_real_video_sink()
                    except Exception: pass
                self.player.set_property("uri", uri)
                self.player.set_state(Gst.State.PLAYING)
            self.is_playing = True
            try: self._update_screensaver_inhibit()
            except Exception: pass
            try: self._update_record_button_state()
            except Exception: pass
            self.is_paused = False
            try: self._sync_spectrum_activity()
            except Exception: pass
            self.play_button.set_label("❚❚")
            self.status_label.set_text(self.tr("Lecture"))
            try: self._announce_track_change(filename)
            except Exception: pass
            try: self._push_recent_file(filename)
            except Exception: pass
            self.update_information(filename)
            self.select_filename(filename)
            self._mpris_notify_playback()
            if start_at > 0.0:
                try: self._resume_apply_later(filename, start_at)
                except Exception: pass
            try:
                if bool(getattr(self, "playlist_only_mode", False)):
                    self._apply_playlist_only_mode()
            except Exception: pass
            try:
                if (
                    abs(float(getattr(self, "playback_rate", 1.0) or 1.0) - 1.0) > 0.001
                    and self._is_speed_media(filename)
                ):
                    GLib.timeout_add(350, self._apply_playback_rate_idle)
            except Exception: pass
        except Exception as e:
            self.status_label.set_text(self.tr("Erreur : ") + str(e))
            print("Erreur lecture:", e, file=sys.stderr)

    def seek_to_cdda_track(self, track_num):
        fmt = Gst.Format.get_by_nick("track")
        if fmt == Gst.Format.UNDEFINED: return False
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
                    except Exception: pass
                    try:
                        sel.unselect_all()
                        sel.select_path(filter_path)
                        self.tree.set_cursor(filter_path)
                        self.tree.scroll_to_cell(filter_path, None, True, 0.5, 0)
                    finally:
                        if blocked:
                            try: sel.handler_unblock_by_func(self.selection_changed)
                            except Exception: pass
                return
            iterator = self.playlist.iter_next(iterator)

    def toggle_play(self, *args):
        if getattr(self, "is_playing", False) and getattr(self, "current_path", None):
            self.toggle_pause()
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
        if selected_path is not None:
            self.play_file(selected_path)
            return
        if self.current_path is not None:
            self.toggle_pause()
            return
        iterator = self.playlist.get_iter_first()
        if iterator is not None:
            self.play_file(self.playlist[iterator][self.COL_PATH])

    def toggle_pause(self, *args):
        if self.current_path is None: return
        if self.is_playing and not self.is_paused:
            self._active_pipeline().set_state(Gst.State.PAUSED)
            self.is_paused = True
            try: self._sync_spectrum_activity()
            except Exception: pass
            self.play_button.set_label("▶")
            self.status_label.set_text(self.tr("Pause"))
            if self.current_is_video:
                try:
                    if self.video_sink is not None and isinstance(
                        self.video_sink, GstVideo.VideoOverlay
                    ):
                        GstVideo.VideoOverlay.expose(self.video_sink)
                except Exception: pass
                GLib.timeout_add(40, self._capture_pause_frame_and_redraw)
            self.show_osd("❚❚  " + self.tr("Pause"))
            self._mpris_notify_playback()
            try: self._update_screensaver_inhibit()
            except Exception: pass
        else:
            try:
                _ret, state, _pending = self._active_pipeline().get_state(0)
            except Exception:
                state = Gst.State.NULL
            if state == Gst.State.NULL:
                self.play_file(self.current_path, resume=False)
                return
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
                    self._apply_fullscreen_ui_layout()
                    self._cache_video_window_handle()
                else:
                    self.show_video_area(True)
            self._active_pipeline().set_state(Gst.State.PLAYING)
            self.is_playing = True
            self.is_paused = False
            self._pause_pixbuf = None
            try: self._sync_spectrum_activity()
            except Exception: pass
            self.play_button.set_label("❚❚")
            self.status_label.set_text(self.tr("Lecture"))
            if self.current_is_video:
                self._nudge_video_redraw()
            self.show_osd("▶  " + self.tr("Lecture"))
            self._mpris_notify_playback()
            try: self._update_screensaver_inhibit()
            except Exception: pass
    def stop(self, *args):
        try: self._resume_save_current()
        except Exception: pass
        self._playback_generation += 1
        self._cancel_seek_release_timeout()
        self._seek_in_progress = False
        self._pending_seek_delta = 0.0
        self._last_known_position = 0.0
        try:
            if getattr(self, "_recording", False):
                self._stop_recording(show_save_dialog=True)
        except Exception: pass
        self._destroy_custom_pipeline()
        try: self.player.set_state(Gst.State.NULL)
        except Exception: pass
        self.is_playing = False
        self.is_paused = False
        self._pause_pixbuf = None
        try: self._sync_spectrum_activity()
        except Exception: pass
        try:
            n = len(getattr(self, "spectrum_values", []) or [])
            if n:
                self.spectrum_values = [0.0] * n
                self.spectrum_targets = [0.0] * n
                self.spectrum_peaks = [0.0] * n
        except Exception: pass
        try: self._refresh_playlist_current_style()
        except Exception: pass
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
        try: self.player.set_property("suburi", None)
        except Exception: pass
        self.play_button.set_label("▶")
        try: self.position_scale.set_value(0)
        except Exception: pass
        self.time_label.set_text(
            "0:00 • " + format_time(self.current_duration)
        )
        self.status_label.set_text(self.tr("Arrêt"))
        self.reset_spectrum()
        self.lyrics_mode = False
        self.lyrics_data = None
        self.lyrics_current_index = -1
        try: self._inhibit_screensaver(False)
        except Exception: pass
        self.show_video_area(False)
        try: self._update_pitch_menu_sensitivity()
        except Exception: pass
        try: self._update_colorimetry_menu_sensitivity()
        except Exception: pass
        try: self._update_record_button_state()
        except Exception: pass
        self._mpris_notify_playback()
        try: GLib.idle_add(lambda: (trim_memory(force=True), False)[1])
        except Exception: pass
    def get_paths(self):
        paths = []
        iterator = self.playlist.get_iter_first()
        while iterator is not None:
            paths.append(self.playlist[iterator][self.COL_PATH])
            iterator = self.playlist.iter_next(iterator)
        return paths

    def next(self, *args, auto=False):
        try:
            if self._next_with_play_next_queue(auto=auto): return
        except Exception: pass
        paths = self.get_paths()
        if not paths: return
        if auto and self.repeat_track_path and self.repeat_track_path in paths:
            next_path = self.repeat_track_path
        elif self.current_path not in paths:
            next_path = paths[0]
        elif self.shuffle_mode and len(paths) > 1:
            available = [path for path in paths if path != self.current_path]
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
        self.play_file(next_path, resume=not auto)

    def previous(self, *args):
        paths = self.get_paths()
        if not paths: return
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

    def _active_playlist_tree(self):
        try:
            if (
                getattr(self, "nano_player_mode", False)
                and getattr(self, "nano_playlist_enabled", False)
            ):
                nt = getattr(self, "nano_playlist_tree", None)
                if nt is not None: return nt
        except Exception: pass
        return getattr(self, "tree", None)

    def move_playlist_selection(self, direction):
        tree = self._active_playlist_tree()
        if tree is None: return
        model = tree.get_model()
        if model is None: return
        selection = tree.get_selection()
        try:
            current_path, current_column = tree.get_cursor()
        except Exception:
            current_path, current_column = None, None
        if current_path is None:
            try:
                model_sel, paths = selection.get_selected_rows()
                if paths:
                    current_path = paths[0]
            except Exception: pass
        if current_path is None:
            target_path = Gtk.TreePath.new_first()
        else:
            target_path = current_path.copy()
            if direction < 0:
                if not target_path.prev(): return
            else:
                target_path.next()
        try:
            model.get_iter(target_path)
        except (ValueError, TypeError):
            return
        try:
            selection.unselect_all()
            selection.select_path(target_path)
            tree.set_cursor(target_path, current_column, False)
            tree.scroll_to_cell(target_path, None, True, 0.5, 0)
        except Exception: pass

    def on_gstreamer_message(self, bus, message):
        if message.type == Gst.MessageType.EOS:
            try:
                active = self._active_pipeline()
                src = message.src
                if active is None or src is None: return
                if src != active:
                    try:
                        parent = src
                        belongs = False
                        for _ in range(12):
                            if parent is None: break
                            if parent == active:
                                belongs = True
                                break
                            parent = parent.get_parent()
                        if not belongs: return
                    except Exception:
                        return
            except Exception: pass
            try: self._resume_forget(self.current_path)
            except Exception: pass
            if getattr(self, "sleep_end_of_track", False):
                self.sleep_end_of_track = False
                try:
                    self.toggle_pause()
                except Exception:
                    try:
                        self.player.set_state(Gst.State.PAUSED)
                        self.is_paused = True
                        self.play_button.set_label("▶")
                    except Exception: pass
                try: self.status_label.set_text(self.tr("Minuteur : arrêt"))
                except Exception: pass
                return
            cf = float(getattr(self, "crossfade_seconds", 0.0) or 0.0)
            if cf >= 0.15 and self.is_playing and not self.is_paused:
                def _after_fade():
                    self.next(auto=True)
                self._start_crossfade_out(then_callback=_after_fade)
                return
            self.next(auto=True)
            return
        if message.type == Gst.MessageType.TOC:
            try:
                toc, _updated = message.parse_toc()
                self._ingest_toc(toc)
            except Exception: pass
            return
        if message.type == Gst.MessageType.STATE_CHANGED:
            try:
                if message.src == self._active_pipeline():
                    _old, new_state, _pending = message.parse_state_changed()
                    if new_state == Gst.State.PLAYING:
                        self._probe_video_size_for_colorimetry()
            except Exception: pass
        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            print("GStreamer :", error, file=sys.stderr)
            if debug:
                print("GStreamer debug:", debug, file=sys.stderr)
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
                except Exception: pass
                try:
                    self._apply_decoder_performance_mode(force_software=True)
                except Exception as e:
                    print("fallback ranks:", e, file=sys.stderr)
                def _retry_soft(p=path):
                    try:
                        self.play_file(p, resume=False)
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
        if message.type == Gst.MessageType.ASYNC_DONE:
            self._try_update_duration()
            if self._seek_in_progress:
                self._seek_in_progress = False
                self._cancel_seek_release_timeout()
                try:
                    ok, pos = self.player.query_position(Gst.Format.TIME)
                    if ok and pos >= 0:
                        self._last_known_position = pos / Gst.SECOND
                except Exception: pass
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
                if new_state == Gst.State.PAUSED and self.current_is_video:
                    gen = self._playback_generation
                    def _refresh_tracks(generation=gen):
                        if generation != self._playback_generation: return False
                        self.refresh_subtitle_tracks()
                        self.refresh_audio_tracks()
                        return False
                    GLib.idle_add(_refresh_tracks)
                if new_state == Gst.State.PLAYING and self.current_is_video:
                    self._nudge_video_redraw()
            return
        if message.type == Gst.MessageType.TAG:
            taglist = message.parse_tag()
            if taglist:
                ok, title = taglist.get_string(Gst.TAG_TITLE)
                if ok and title:
                    self.icy_title = title.strip()
                ok, artist = taglist.get_string(Gst.TAG_ARTIST)
                if ok and artist:
                    self.icy_artist = artist.strip()
                if self.current_path and is_stream(self.current_path):
                    self.update_information(self.current_path)
                try:
                    if hasattr(Gst, "TAG_VIDEO_WIDTH"):
                        ok_w, vw = taglist.get_uint(Gst.TAG_VIDEO_WIDTH)
                    else:
                        ok_w, vw = False, 0
                except Exception:
                    ok_w, vw = False, 0
                try:
                    if hasattr(Gst, "TAG_VIDEO_HEIGHT"):
                        ok_h, vh = taglist.get_uint(Gst.TAG_VIDEO_HEIGHT)
                    else:
                        ok_h, vh = False, 0
                except Exception:
                    ok_h, vh = False, 0
                if not ok_w:
                    try:
                        ok_w, vw = taglist.get_int("video-width")
                    except Exception:
                        ok_w = False
                if not ok_h:
                    try:
                        ok_h, vh = taglist.get_int("video-height")
                    except Exception:
                        ok_h = False
                if ok_w or ok_h:
                    try:
                        self._maybe_disable_colorimetry_for_4k(
                            vw if ok_w else 0, vh if ok_h else 0
                        )
                    except Exception: pass
            return
        if message.type != Gst.MessageType.ELEMENT: return
        structure = message.get_structure()
        if structure is None: return
        if structure.get_name() != "spectrum": return
        self.process_spectrum(structure)

    def update_position(self):
        if getattr(self, "_app_closing", False): return False
        if not self.is_playing: return True
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
            if not self._seek_in_progress:
                if position_seconds > 0.05 or self._last_known_position <= 0.05:
                    self._last_known_position = position_seconds
            if getattr(self, "lyrics_mode", False) and getattr(self, "lyrics_data", None):
                try:
                    self._update_lyrics_index(self._last_known_position)
                    if not self.lyrics_data.get("timed") and self.video_area is not None:
                        self.video_area.queue_draw()
                except Exception: pass
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
                self.time_label.set_text(f"{format_time(display_position)} • ∞")
            else:
                self.time_label.set_text(
                    f"{format_time(display_position)} • {format_time(self.current_duration)}"
                )
            self._sync_fs_transport_ui()
            try: self._resume_tick()
            except Exception: pass
            try:
                self._scrobble_maybe_submit(
                    getattr(self, "current_path", None),
                    display_position,
                    getattr(self, "current_duration", 0) or 0,
                )
            except Exception: pass
        except Exception: pass
        return True

    def seek_press(self, widget, event):
        if event.button == 1:
            self.user_seeking = True
            self._seek_user_moved = False
            try:
                self._seek_press_value = float(widget.get_value())
            except Exception:
                self._seek_press_value = None
        return False

    def seek_release(self, widget, event):
        if event.button == 1:
            was_seeking = self.user_seeking
            moved = bool(getattr(self, "_seek_user_moved", False))
            press_value = getattr(self, "_seek_press_value", None)
            self.user_seeking = False
            self._seek_user_moved = False
            self._seek_press_value = None
            if not was_seeking: return False
            try:
                value = float(widget.get_value())
                changed = moved or (
                    press_value is not None and abs(value - press_value) > 0.05
                )
                has_duration = float(self.current_duration or 0) > 0
                if changed and has_duration:
                    self.seek_to(value)
            except Exception as e:
                print("seek_release:", e, file=sys.stderr)
            try: self._sync_fs_transport_ui()
            except Exception: pass
        return False

    def seek_value_changed(self, scale):
        if self._updating_position_scale: return
        value = scale.get_value()
        if self.user_seeking:
            self._seek_user_moved = True
            txt = f"{format_time(value)} • {format_time(self.current_duration)}"
            try: self.time_label.set_text(txt)
            except Exception: pass
            try:
                if getattr(self, "fs_time_label", None) is not None:
                    self.fs_time_label.set_text(txt)
            except Exception: pass
            try:
                if scale is getattr(self, "fs_scale", None):
                    self._updating_position_scale = True
                    try:
                        self.position_scale.set_value(value)
                    finally:
                        self._updating_position_scale = False
            except Exception: pass

    def _cancel_seek_release_timeout(self):
        tid = getattr(self, "_seek_release_timeout_id", 0) or 0
        if tid:
            try: GLib.source_remove(tid)
            except Exception: pass
            self._seek_release_timeout_id = 0

    def seek_to(self, seconds, accurate=False):
        try:
            if not self.current_path: return False
            self._try_update_duration()
            target = max(0.0, float(seconds))
            if self.current_duration > 0:
                target = min(target, self.current_duration)
            flags = (
                Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE
                if accurate
                else Gst.SeekFlags.FLUSH
                | Gst.SeekFlags.KEY_UNIT
                | Gst.SeekFlags.SNAP_NEAREST
            )
            self._cancel_seek_release_timeout()
            self._seek_in_progress = True
            pipe = self._active_pipeline()
            ns = int(target * Gst.SECOND)
            success = False
            if self._custom_pipeline is not None:
                flags_c = Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE
                was_playing = self.is_playing and not self.is_paused
                try:
                    pipe.set_state(Gst.State.PAUSED)
                    pipe.get_state(int(600 * Gst.MSECOND))
                    success = bool(self._seek_with_rate(pipe, flags_c, ns))
                    if not success:
                        success = bool(
                            self._seek_with_rate(
                                pipe,
                                Gst.SeekFlags.FLUSH
                                | Gst.SeekFlags.KEY_UNIT
                                | Gst.SeekFlags.SNAP_NEAREST,
                                ns,
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
                    except Exception: pass
            else:
                try:
                    success = bool(self._seek_with_rate(pipe, flags, ns))
                except Exception:
                    success = False
            if not success:
                self._seek_in_progress = False
                return False
            self._last_known_position = target
            try:
                q = getattr(self, "_spectrum_delay_queue", None)
                if q is not None:
                    q.clear()
            except Exception: pass
            self._updating_position_scale = True
            try:
                if self.current_duration > 0:
                    self.position_scale.set_value(target)
            finally:
                self._updating_position_scale = False
            self.time_label.set_text(
                f"{format_time(target)} • {format_time(self.current_duration)}"
            )
            self._sync_fs_transport_ui()
            gen = self._playback_generation
            def _release_seek_lock(generation=gen):
                self._seek_release_timeout_id = 0
                if generation != self._playback_generation: return False
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
        try:
            if not self.is_playing or self.is_paused: return False
            pipe = self._active_pipeline()
            ret, state, pending = pipe.get_state(100 * Gst.MSECOND)
            if state != Gst.State.PLAYING:
                pipe.set_state(Gst.State.PLAYING)
            if self.current_is_video:
                self._nudge_video_redraw()
        except Exception: pass
        return False

    def _apply_pending_seek_delta(self):
        if abs(self._pending_seek_delta) < 0.001: return False
        delta = self._pending_seek_delta
        self._pending_seek_delta = 0.0
        base = self._last_known_position
        if self.current_duration > 0:
            base = min(max(0.0, base), self.current_duration)
        self.seek_to(base + delta, accurate=True)
        return False

    def _try_update_duration(self):
        try:
            if not self.current_path: return False
            ok, duration = self._active_pipeline().query_duration(Gst.Format.TIME)
            if not ok or duration <= 0: return False
            duration_seconds = duration / Gst.SECOND
            if duration_seconds <= 0: return False
            if self.current_duration <= 0 or abs(duration_seconds - self.current_duration) >= 0.05:
                self.current_duration = duration_seconds
                self.position_scale.set_range(0, duration_seconds)
                self._update_playlist_duration(self.current_path, duration_seconds)
            if not self.user_seeking:
                value = min(self.position_scale.get_value(), duration_seconds)
                self.time_label.set_text(
                    f"{format_time(value)} • {format_time(duration_seconds)}"
                )
            return True
        except Exception:
            return False

    def _update_playlist_duration(self, filename, duration):
        try:
            iterator = self.playlist.get_iter_first()
            while iterator is not None:
                if self.playlist[iterator][self.COL_PATH] == filename:
                    old = float(self.playlist[iterator][self.COL_DURATION] or 0)
                    if old <= 0 or abs(old - duration) > 1.0:
                        self.playlist[iterator][self.COL_DURATION_TEXT] = format_time(duration)
                        self.playlist[iterator][self.COL_DURATION] = float(duration)
                        if filename in self.metadata_cache:
                            self.metadata_cache[filename]["duration"] = duration
                    return
                iterator = self.playlist.iter_next(iterator)
        except Exception: pass

    def seek_relative(self, seconds):
        try:
            if not self.current_path: return
            delta = float(seconds)
            def _clamp(t):
                t = max(0.0, t)
                if self.current_duration > 0:
                    t = min(t, self.current_duration)
                return t
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
        except Exception: pass

    def volume_changed(self, scale):
        value = scale.get_value()
        if not self.is_muted:
            self._set_playback_volume(value)
            self._volume_before_mute = value
        else:
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
        try:
            current = self.volume_scale.get_value()
            if self.is_muted and delta > 0:
                self.is_muted = False
                self._update_mute_button()
            new_val = max(0.0, min(1.0, current + float(delta)))
            self.volume_scale.set_value(new_val)
        except Exception: pass

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
        if not hasattr(self, "mute_button") or self.mute_button is None: return
        if self.is_muted:
            self.mute_button.set_label("⌀")
            self.mute_button.set_tooltip_text(self.tr("Son activé") + " (M)")
        else:
            self.mute_button.set_label("♪")
            self.mute_button.set_tooltip_text(self.tr("Muet") + " (M)")

    def _build_fullscreen_controls(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        try: bar.get_style_context().add_class("fs-controls")
        except Exception: pass
        def _btn(label, cb):
            b = Gtk.Button(label=label)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.connect("clicked", cb)
            try: b.get_style_context().add_class("fs-ctrl-btn")
            except Exception: pass
            return b
        self.fs_play_btn = _btn(
            "▶",
            lambda *a: self.toggle_play(),
        )
        bar.pack_start(self.fs_play_btn, False, False, 0)
        bar.pack_start(_btn("⏮", lambda *a: self.previous()), False, False, 0)
        def _stop_fs(*_a):
            try:
                if self.is_fullscreen:
                    self.toggle_video_fullscreen()
            except Exception: pass
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
        self.fs_time_label = Gtk.Label(label="0:00 • 0:00")
        self.fs_time_label.get_style_context().add_class("fs-time")
        self.fs_time_label.set_margin_start(6)
        self.fs_time_label.set_margin_end(6)
        bar.pack_start(self.fs_time_label, False, False, 0)
        self.fs_shot_btn = Gtk.Button(label="⎙")
        self.fs_shot_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.fs_shot_btn.connect("clicked", self._fs_quick_screenshot)
        try: self.fs_shot_btn.get_style_context().add_class("fs-ctrl-btn")
        except Exception: pass
        bar.pack_start(self.fs_shot_btn, False, False, 0)
        for child in bar.get_children():
            child.show_all()
        popup = Gtk.Window(type=Gtk.WindowType.POPUP)
        popup.set_transient_for(self)
        popup.set_accept_focus(False)
        popup.set_focus_on_map(False)
        popup.set_decorated(False)
        popup.set_skip_taskbar_hint(True)
        popup.set_skip_pager_hint(True)
        popup.set_app_paintable(True)
        try:
            visual = popup.get_screen().get_rgba_visual()
            if visual is not None:
                popup.set_visual(visual)
        except Exception: pass
        popup.connect("draw", self._on_fs_popup_draw)
        popup.add(bar)
        bar.show()
        popup.add_events(
            Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        popup.connect("enter-notify-event", self._on_fs_controls_enter)
        popup.connect("leave-notify-event", self._on_fs_controls_leave)
        popup.connect("motion-notify-event", self._on_fs_controls_motion)
        self.fs_controls = popup
        self._fs_controls_visible = False
        self._fs_popup_geom = None

    def _show_fs_toast(self, corner, text, duration_ms=1800):
        if not getattr(self, "is_fullscreen", False): return
        label = (
            getattr(self, "fs_toast_tl", None)
            if corner == "tl"
            else getattr(self, "fs_toast_tr", None)
        )
        if label is None: return
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
            try: GLib.source_remove(tid)
            except Exception: pass
        def _hide(a=attr, lab=label):
            setattr(self, a, 0)
            try: lab.hide()
            except Exception: pass
            return False
        setattr(self, attr, GLib.timeout_add(duration_ms, _hide))

    def _fs_quick_screenshot(self, *args):
        if not self.current_is_video or self.video_area is None: return
        try: self._hide_fs_controls()
        except Exception: pass
        for lab in (
            getattr(self, "fs_toast_tl", None),
            getattr(self, "fs_toast_tr", None),
            getattr(self, "osd_label", None),
        ):
            if lab is not None:
                try: lab.hide()
                except Exception: pass
        while Gtk.events_pending():
            Gtk.main_iteration()
        GLib.timeout_add(60, self._fs_quick_screenshot_do)
        return

    def _fs_quick_screenshot_do(self):
        try:
            window = self.video_area.get_window() if self.video_area else None
            if window is None: return False
            width = window.get_width()
            height = window.get_height()
            if width <= 0 or height <= 0: return False
            pixbuf = Gdk.pixbuf_get_from_window(window, 0, 0, width, height)
            if pixbuf is None: return False
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
        if not path: return
        try:
            if is_stream(path) or is_cdda(path) or is_dvd(path) or is_bluray(path):
                name = path
            else:
                name = os.path.basename(path)
        except Exception:
            name = str(path)
        try:
            for row in self.playlist:
                if row[self.COL_PATH] == path:
                    t = row[self.COL_TITLE]
                    if t:
                        name = t
                    break
        except Exception: pass
        self._show_fs_toast("tl", name, 2000)

    def _on_fs_controls_enter(self, *args):
        self._show_cursor()
        return False

    def _on_fs_controls_leave(self, widget, event):
        try:
            if getattr(event, "detail", None) == Gdk.NotifyType.INFERIOR: return False
        except Exception: pass
        if getattr(self, "user_seeking", False): return False
        if self._fs_osd_nav_allowed():
            self._hide_fs_controls()
        return False

    def _on_fs_controls_motion(self, *args):
        self._show_cursor()
        return False

    def _sync_fs_transport_ui(self):
        if not self._fs_osd_nav_allowed(): return
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
            if (
                getattr(self, "fs_scale", None) is not None
                and getattr(self, "position_scale", None) is not None
                and not getattr(self, "user_seeking", False)
            ):
                dur = max(1.0, float(self.current_duration) or 1.0)
                self._updating_position_scale = True
                try:
                    self.fs_scale.set_range(0, dur)
                    self.fs_scale.set_value(self.position_scale.get_value())
                finally:
                    self._updating_position_scale = False
        except Exception: pass

    def _fs_osd_nav_allowed(self):
        if bool(getattr(self, "is_fullscreen", False)): return True
        if (
            bool(getattr(self, "playlist_only_mode", False))
            and bool(getattr(self, "current_is_video", False))
        ):
            return True
        return False

    def _on_fs_popup_draw(self, widget, cr):
        try:
            w = widget.get_allocated_width()
            h = widget.get_allocated_height()
            alpha = 1.0
            try:
                scr = widget.get_screen()
                if (
                    scr is not None
                    and scr.is_composited()
                    and scr.get_rgba_visual() is not None
                ):
                    alpha = float(FS_BAR_ALPHA)
            except Exception: pass
            cr.save()
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_rgba(0, 0, 0, 0)
            cr.paint()
            cr.restore()
            r = float(self._ui_s(10))
            if w > 2 * r and h > 2 * r:
                cr.new_sub_path()
                cr.arc(w - r, r, r, -math.pi / 2, 0)
                cr.arc(w - r, h - r, r, 0, math.pi / 2)
                cr.arc(r, h - r, r, math.pi / 2, math.pi)
                cr.arc(r, r, r, math.pi, 3 * math.pi / 2)
                cr.close_path()
            else:
                cr.rectangle(0, 0, w, h)
            cr.set_source_rgba(0, 0, 0, alpha)
            cr.fill()
        except Exception: pass
        return False

    def _position_fs_popup(self):
        try:
            popup = getattr(self, "fs_controls", None)
            va = getattr(self, "video_area", None)
            if popup is None or va is None: return False
            gw = va.get_window()
            if gw is None: return False
            res = gw.get_origin()
            ox, oy = res[-2], res[-1]
            vw = va.get_allocated_width()
            vh = va.get_allocated_height()
            if vw <= 1 or vh <= 1: return False
            margin = self._ui_s(16)
            bottom = self._ui_s(12)
            width = max(self._ui_s(240), vw - 2 * margin)
            popup.set_size_request(width, -1)
            _min_h, nat_h = popup.get_preferred_height()
            h = max(1, int(nat_h))
            x = int(ox + (vw - width) / 2)
            y = int(oy + vh - h - bottom)
            geom = (x, y, width, h)
            if geom == getattr(self, "_fs_popup_geom", None) and popup.get_visible(): return False
            self._fs_popup_geom = geom
            popup.resize(width, h)
            popup.move(x, y)
        except Exception as e:
            print("fs popup position:", e, file=sys.stderr)
        return False

    def _on_main_configure_for_fs_popup(self, widget, event):
        if getattr(self, "_fs_controls_visible", False):
            try: GLib.idle_add(self._position_fs_popup)
            except Exception: pass
        return False

    def _show_fs_controls(self):
        if not self._fs_osd_nav_allowed(): return
        bar = getattr(self, "fs_controls", None)
        if bar is None: return
        try:
            if getattr(self, "fs_play_btn", None) is not None:
                self.fs_play_btn.set_label(
                    "❚❚" if (self.is_playing and not self.is_paused) else "▶"
                )
            if (
                getattr(self, "fs_scale", None) is not None
                and getattr(self, "position_scale", None) is not None
                and not getattr(self, "user_seeking", False)
            ):
                self._updating_position_scale = True
                try:
                    self.fs_scale.set_range(
                        0, max(1.0, float(self.current_duration) or 1.0)
                    )
                    self.fs_scale.set_value(self.position_scale.get_value())
                except Exception: pass
                finally:
                    self._updating_position_scale = False
            if getattr(self, "fs_time_label", None) is not None and getattr(
                self, "time_label", None
            ) is not None:
                self.fs_time_label.set_text(self.time_label.get_text())
            self._position_fs_popup()
            bar.show()
            self._fs_controls_visible = True
        except Exception: pass

    def _hide_fs_controls(self):
        if getattr(self, "user_seeking", False): return
        bar = getattr(self, "fs_controls", None)
        if bar is None: return
        try: bar.hide()
        except Exception: pass
        self._fs_controls_visible = False

    def on_video_motion(self, widget, event):
        if self._fs_osd_nav_allowed():
            if self.is_fullscreen:
                self._reset_cursor_hide_timer()
            try:
                h = widget.get_allocated_height() or 1
                if event.y >= h * 0.82:
                    self._show_fs_controls()
                elif getattr(self, "_fs_controls_visible", False):
                    self._hide_fs_controls()
            except Exception: pass
        return False

    def _reset_cursor_hide_timer(self):
        self._show_cursor()
        if self._cursor_hide_timeout_id:
            try: GLib.source_remove(self._cursor_hide_timeout_id)
            except Exception: pass
            self._cursor_hide_timeout_id = 0
        if not self.is_fullscreen: return
        def _hide():
            self._cursor_hide_timeout_id = 0
            if self.is_fullscreen:
                self._hide_cursor()
            return False
        self._cursor_hide_timeout_id = GLib.timeout_add(4000, _hide)

    def _hide_cursor(self):
        try:
            window = self.get_window()
            if window is None: return
            display = window.get_display()
            blank = Gdk.Cursor.new_from_name(display, "none")
            if blank is None:
                blank = Gdk.Cursor.new_for_display(display, Gdk.CursorType.BLANK_CURSOR)
            window.set_cursor(blank)
            self._cursor_hidden = True
        except Exception: pass

    def _show_cursor(self):
        try:
            window = self.get_window()
            if window is not None:
                window.set_cursor(None)
            self._cursor_hidden = False
        except Exception: pass
        if self._cursor_hide_timeout_id:
            try: GLib.source_remove(self._cursor_hide_timeout_id)
            except Exception: pass
            self._cursor_hide_timeout_id = 0

    def show_osd(self, text, duration_ms=1600):
        return

    def _osd_seek_feedback(self, delta):
        sign = "+" if delta >= 0 else ""
        pos = self._last_known_position
        try:
            ok, p = self.player.query_position(Gst.Format.TIME)
            if ok and p >= 0:
                pos = p / Gst.SECOND
        except Exception: pass
        total = self.current_duration
        msg = f"{sign}{int(delta)} s"
        if total > 0:
            msg += f"  ·  {format_time(pos)} • {format_time(total)}"
        else:
            msg += f"  ·  {format_time(pos)}"
        self.show_osd(msg)

    def _clear_chapters(self):
        self.chapters = []

    def _ingest_toc(self, toc):
        if toc is None: return
        found = []
        def _walk(entry):
            try:
                etype = entry.get_entry_type()
            except Exception:
                etype = None
            start_ns = None
            try:
                ret = entry.get_start_stop_times()
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
            except Exception: pass
            is_chapter = False
            try:
                if etype == Gst.TocEntryType.CHAPTER:
                    is_chapter = True
                elif etype is not None and "CHAPTER" in str(etype).upper():
                    is_chapter = True
            except Exception: pass
            if is_chapter and start_ns is not None and start_ns >= 0:
                sec = start_ns / float(Gst.SECOND)
                if title is None:
                    title = f"{self.tr('Chapitre')} {len(found) + 1}"
                found.append((sec, title))
            try:
                for sub in entry.get_sub_entries() or []:
                    _walk(sub)
            except Exception: pass
        try:
            for entry in toc.get_entries() or []:
                _walk(entry)
        except Exception: pass
        if found:
            found.sort(key=lambda x: x[0])
            self.chapters = found

    def seek_to_chapter(self, index):
        if index < 0 or index >= len(self.chapters): return
        start, title = self.chapters[index]
        self.seek_to(start, accurate=True)
        try:
            self._show_fs_toast(
                "tl",
                "⏭  %s  (%s)" % (title, format_time(start)),
                2000,
            )
        except Exception: pass
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
                offset = parameters.unpack()[0]
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
            if property_name == "LoopStatus": return GLib.Variant(
                    "s", "Playlist" if self.repeat_mode else "None"
                )
            if property_name == "Rate": return GLib.Variant("d", 1.0)
            if property_name == "Shuffle": return GLib.Variant("b", bool(self.shuffle_mode))
            if property_name == "Volume":
                vol = 0.0 if self.is_muted else float(
                    self.volume_scale.get_value()
                )
                return GLib.Variant("d", vol)
            if property_name == "Position":
                pos_us = int(self._last_known_position * 1_000_000)
                return GLib.Variant("x", pos_us)
            if property_name == "MinimumRate": return GLib.Variant("d", 1.0)
            if property_name == "MaximumRate": return GLib.Variant("d", 1.0)
            if property_name in (
                "CanGoNext", "CanGoPrevious", "CanPlay",
                "CanPause", "CanSeek", "CanControl",
            ):
                return GLib.Variant("b", True)
            if property_name == "Metadata": return self._mpris_metadata_variant()
        except Exception: pass
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
            except Exception: pass
        return GLib.Variant("a{sv}", meta)

    def _mpris_properties_changed(self, props):
        conn = getattr(self, "_mpris_connection", None)
        if conn is None or not props: return
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
        except Exception: pass

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
        help_btn = dialog.add_button("?", Gtk.ResponseType.HELP)
        try: help_btn.set_tooltip_text(self.tr("Aide raccourcis"))
        except Exception: pass
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
        store = Gtk.ListStore(str, str, str)
        for action_id in DEFAULT_SHORTCUTS.keys():
            label = SHORTCUT_LABELS.get(action_id, action_id)
            key = self.shortcuts.get(action_id, DEFAULT_SHORTCUTS[action_id])
            store.append([action_id, self.tr(label), key])
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
            if capture["action"] is None: return False
            key = Gdk.keyval_name(event.keyval)
            if key is None: return True
            if key in (
                "Control_L", "Control_R", "Shift_L", "Shift_R",
                "Alt_L", "Alt_R", "Meta_L", "Meta_R", "Super_L", "Super_R",
            ):
                return True
            binding = self._event_to_shortcut(event)
            if not binding: return True
            store[capture["iter"]][2] = binding
            self.shortcuts[capture["action"]] = binding
            capture["action"] = None
            capture["iter"] = None
            hint.set_text(self.tr("Raccourcis enregistrés"))
            try: self.save_state()
            except Exception: pass
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
                        self.tr(label),
                        DEFAULT_SHORTCUTS[action_id],
                    ])
                try: self.save_state()
                except Exception: pass
                hint.set_text(self.tr("Raccourcis enregistrés"))
                continue
            if response == Gtk.ResponseType.HELP:
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
                except Exception: pass
                continue
            break
        dialog.destroy()

    def on_tree_button_press(self, tree, event):
        if event.button == 1: return self._playlist_left_button_press(tree, event)
        if event.button != 3: return False
        result = tree.get_path_at_pos(int(event.x), int(event.y))
        if result is None: return False
        path = result[0]
        selection = tree.get_selection()
        if not selection.path_is_selected(path):
            selection.unselect_all()
            selection.select_path(path)
        model = tree.get_model()
        iterator = model.get_iter(path)
        if iterator is None: return False
        filename = model[iterator][self.COL_PATH]
        model_sel, sel_paths = selection.get_selected_rows()
        multi = len(sel_paths) > 1
        menu = self._build_playlist_context_menu(
            filename=filename, multi=multi
        )
        if menu is None: return False
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def show_metadata(self, filename):
        title, artist, album, year, duration, cover, technical = \
            self.get_metadata(filename, load_cover=False)
        dialog = Gtk.Dialog(
            title=self.tr("Métadonnées — ") + (
                os.path.basename(filename)
                if not (is_cdda(filename) or is_stream(filename))
                else filename
            ),
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
            ("Format", technical.get("format") or "—"),
            ("Durée", format_time(duration) if duration > 0 else "—"),
            ("Fréquence", technical.get("sample_rate") or "—"),
            ("Canaux", technical.get("channels") or "—"),
            ("Débit", technical.get("bitrate") or "—"),
            ("Profondeur", technical.get("bits") or "—"),
        ]
        for row, (key, value) in enumerate(rows):
            l1 = Gtk.Label(label=self.tr(key) + " :")
            l1.set_xalign(1)
            l2 = Gtk.Label(label=value or "—")
            l2.set_xalign(0)
            tech.attach(l1, 0, row, 1, 1)
            tech.attach(l2, 1, row, 1, 1)
        video_widgets = []
        res_label = fps_label = None
        try:
            probe_ok = (
                GstPbutils is not None
                and not is_special_source(filename)
                and is_video_file(filename)
                and os.path.isfile(filename)
            )
        except Exception:
            probe_ok = False
        if probe_ok:
            base_row = len(rows)
            lines = []
            for offset, key in enumerate(("Résolution", "Fréquence d'images")):
                l1 = Gtk.Label(label=self.tr(key) + " :")
                l1.set_xalign(1)
                l2 = Gtk.Label(label="…")
                l2.set_xalign(0)
                tech.attach(l1, 0, base_row + offset, 1, 1)
                tech.attach(l2, 1, base_row + offset, 1, 1)
                video_widgets.extend([l1, l2])
                lines.append(l2)
            res_label, fps_label = lines
        file_label = Gtk.Label(
            label=self.tr("Fichier : ") + (
                os.path.basename(filename)
                if not (is_cdda(filename) or is_stream(filename))
                else filename
            )
        )
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
        if res_label is not None:
            alive = {"ok": True}
            def _mark_dead(*_a):
                alive["ok"] = False
            try: dialog.connect("destroy", _mark_dead)
            except Exception: pass
            def _apply_video_info(info):
                if not alive.get("ok"): return False
                try:
                    if not info:
                        for w in video_widgets:
                            try: w.hide()
                            except Exception: pass
                        return False
                    res_label.set_text(
                        format_video_resolution(
                            info.get("width"), info.get("height")
                        )
                    )
                    res_fps = format_video_fps(info.get("fps"))
                    fps_label.set_text(
                        res_fps if res_fps else self.tr("Variable")
                    )
                except Exception as exc:
                    print("apply video info:", exc, file=sys.stderr)
                return False
            def _video_worker():
                try:
                    info = self._probe_video_info(filename)
                except Exception:
                    info = None
                try: GLib.idle_add(_apply_video_info, info)
                except Exception: pass
            try:
                threading.Thread(
                    target=_video_worker,
                    daemon=True,
                    name="myostoox-probe-video",
                ).start()
            except Exception as exc:
                print("probe video thread:", exc, file=sys.stderr)
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

    def remove_selected(self):
        selection = self.tree.get_selection()
        model, paths = selection.get_selected_rows()
        if not paths: return
        filenames = []
        for path in paths:
            try:
                it = model.get_iter(path)
                if it is not None:
                    filenames.append(model[it][self.COL_PATH])
            except Exception: pass
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
                try:
                    if filename in self.metadata_cache:
                        del self.metadata_cache[filename]
                except Exception: pass
                try:
                    if getattr(self, "_missing_cache", None) is not None:
                        self._missing_cache.pop(filename, None)
                except Exception: pass
                try:
                    if getattr(self, "_cover_pixbuf_cache", None) is not None:
                        self._cover_pixbuf_cache.pop(filename, None)
                except Exception: pass
                self.save_playlist()
                if was_current or len(self.playlist) == 0:
                    self._clear_now_playing_display(reset_time=(was_current or len(self.playlist) == 0))
                    if len(self.playlist) == 0:
                        try: self.status_label.set_text(self.tr("Playlist vide"))
                        except Exception: pass
                return
            iterator = self.playlist.iter_next(iterator)

    def clear_playlist(self):
        if getattr(self, "bouquet_active", False):
            self.dismiss_bouquet_playlist()
            self.status_label.set_text(self.tr("Playlist vide"))
            return
        self.bouquet_active = False
        try: self.stop()
        except Exception: pass
        self.current_path = None
        self.current_duration = 0
        self._last_known_position = 0.0
        try:
            self.position_scale.set_range(0, 1)
            self.position_scale.set_value(0)
        except Exception: pass
        try: self.time_label.set_text("0:00 • 0:00")
        except Exception: pass
        try:
            self._clear_playlist_search()
        except Exception:
            self.search_text = ""
        try:
            self._playlist_views_detach_clear_reattach(clear_cache=True)
        except Exception as e:
            print("clear_playlist:", e, file=sys.stderr)
        try: self.save_playlist()
        except Exception: pass
        try: self._clear_now_playing_display(reset_time=True)
        except Exception: pass
        try: self.status_label.set_text(self.tr("Playlist vide"))
        except Exception: pass
        try: GLib.idle_add(lambda: (trim_memory(force=True), False)[1])
        except Exception: pass
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
                    try: self.equalizer.set_property(f"band{index}", gain)
                    except Exception: pass
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
        self._style_app_dialog(win)
        win.show_all()

    def toggle_eq_mute(self, button=None):
        if self.equalizer is None: return
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
                    try: self.eq_scales[i].set_value(0.0)
                    except Exception: pass
            self.eq_muted = True
            if button is not None:
                button.set_label(self.tr("Réactiver EQ"))
            self.status_label.set_text(self.tr("Égaliseur désactivé"))
        else:
            for i, gain in enumerate(self.eq_saved_gains):
                try: self.equalizer.set_property(f"band{i}", gain)
                except Exception: pass
                if i < len(self.eq_scales):
                    try: self.eq_scales[i].set_value(gain)
                    except Exception: pass
            self.eq_muted = False
            if button is not None:
                button.set_label(self.tr("Bypass EQ"))
            self.status_label.set_text(self.tr("Égaliseur activé"))

    def reset_equalizer(self, *args):
        if self.equalizer is None: return
        for i in range(EQ_BANDS):
            try: self.equalizer.set_property(f"band{i}", 0.0)
            except Exception: pass
            if i < len(self.eq_scales):
                self.eq_scales[i].set_value(0.0)
        self.eq_muted = False
        self.eq_saved_gains = []
        self.status_label.set_text(self.tr("Égaliseur réinitialisé"))

    def apply_eq_preset(self, *args):
        if self.eq_preset_combo is None or self.equalizer is None: return
        idx = self.eq_preset_combo.get_active()
        if idx < 0 or idx >= len(self.eq_presets): return
        gains = self.eq_presets[idx]["gains"]
        for i in range(EQ_BANDS):
            try:
                gain = max(EQ_MIN_DB, min(EQ_MAX_DB, float(gains[i])))
                self.equalizer.set_property(f"band{i}", gain)
                if i < len(self.eq_scales):
                    self.eq_scales[i].set_value(gain)
            except Exception: pass
        self.eq_muted = False
        self.status_label.set_text(self.tr("Préréglage appliqué"))

    def save_eq_preset(self, *args):
        if self.equalizer is None: return
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
        if response != Gtk.ResponseType.OK or not name: return
        if self.eq_muted and self.eq_saved_gains:
            gains = list(self.eq_saved_gains)
        else:
            gains = []
            for i in range(EQ_BANDS):
                try:
                    gains.append(float(self.equalizer.get_property(f"band{i}")))
                except Exception:
                    gains.append(0.0)
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
        if self.eq_preset_combo is None: return
        idx = self.eq_preset_combo.get_active()
        if idx < 0 or idx >= len(self.eq_presets): return
        del self.eq_presets[idx]
        self.save_eq_presets()
        self.eq_preset_combo.remove(idx)
        if len(self.eq_presets) > 0:
            self.eq_preset_combo.set_active(min(idx, len(self.eq_presets) - 1))
        self.status_label.set_text(self.tr("Préréglage supprimé"))

    def _snap_pitch_tempo_values(self):
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
        if self.pitch is None:
            try:
                self.pitch = Gst.ElementFactory.make("pitch", "pitch")
            except Exception:
                self.pitch = None
        if self.pitch is None: return
        self._snap_pitch_tempo_values()
        for prop, val in (
            ("pitch", float(self.pitch_value)),
            ("tempo", float(self.tempo_value)),
            ("rate", 1.0),
        ):
            try: self.pitch.set_property(prop, val)
            except Exception: pass
    def rebuild_audio_filter(self, force=False, restart=True):
        if getattr(self, "_custom_pipeline", None) is not None: return
        want_pitch = (
            is_pure_audio_file(getattr(self, "current_path", None))
            and (
                abs(self.pitch_value - 1.0) > 0.001
                or abs(self.tempo_value - 1.0) > 0.001
            )
        )
        if want_pitch and self.pitch is None:
            try:
                self.pitch = Gst.ElementFactory.make("pitch", "pitch")
            except Exception:
                self.pitch = None
        need_pitch = want_pitch and self.pitch is not None
        include_spectrum = (
            self.spectrum is not None
            and not bool(getattr(self, "current_is_video", False))
        )
        spectrum_active = include_spectrum and bool(
            getattr(self, "spectrum_enabled", True)
        )
        want_tempo = (
            getattr(self, "scaletempo", None) is not None
            and getattr(self, "tempo_conv", None) is not None
            and bool(getattr(self, "current_is_video", False))
        )
        tempo_active = bool(getattr(self, "_tempo_in_filter", False))
        if (
            not force
            and need_pitch == self._pitch_active
            and spectrum_active == getattr(self, "_spectrum_in_filter", True)
            and want_tempo == tempo_active
        ):
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
        except Exception: pass
        self.player.set_state(Gst.State.NULL)
        elements = []
        if include_spectrum:
            elements.append(self.spectrum)
            self._spectrum_in_filter = True
        else:
            self._spectrum_in_filter = False
        include_tempo = (
            self.scaletempo is not None
            and self.tempo_conv is not None
            and bool(getattr(self, "current_is_video", False))
        )
        if include_tempo:
            elements.append(self.tempo_conv)
            elements.append(self.scaletempo)
        if need_pitch and self.pitch is not None:
            elements.append(self.pitch)
            self._configure_pitch_element()
        if self.equalizer is not None:
            elements.append(self.equalizer)
        if (
            self.rgvolume is not None
            and str(getattr(self, "replaygain_mode", "off") or "off") != "off"
        ):
            try:
                parent = self.rgvolume.get_parent()
                if parent is not None:
                    parent.remove(self.rgvolume)
            except Exception: pass
            mode = str(getattr(self, "replaygain_mode", "off") or "off")
            try:
                self.rgvolume.set_property(
                    "album-mode", mode == "album"
                )
            except Exception: pass
            elements.append(self.rgvolume)
        self._tempo_in_filter = bool(
            getattr(self, "scaletempo", None) is not None
            and self.scaletempo in elements
        )
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
        if not restart: return
        if self.current_path:
            try:
                uri = path_to_uri(self.current_path)
                self.player.set_property("uri", uri)
            except Exception: pass
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
        if self.current_is_video:
            self._nudge_video_redraw()

    def _probe_video_size_for_colorimetry(self):
        try:
            pipe = self._active_pipeline()
            if pipe is None: return
            ok, w = pipe.query_duration(Gst.Format.TIME)
        except Exception: pass
        width = height = 0
        try:
            pipe = self._active_pipeline()
            if pipe is None: return
            try:
                pad = pipe.emit("get-video-pad", 0)
            except Exception:
                pad = None
            if pad is None:
                try:
                    pad = pipe.get_static_pad("src")
                except Exception:
                    pad = None
            caps = None
            if pad is not None:
                caps = pad.get_current_caps()
                if caps is None:
                    caps = pad.query_caps(None)
            if caps is not None and not caps.is_empty():
                st = caps.get_structure(0)
                if st is not None:
                    ok, width = st.get_int("width")
                    if not ok:
                        width = 0
                    ok, height = st.get_int("height")
                    if not ok:
                        height = 0
        except Exception:
            width = height = 0
        if width or height:
            self._maybe_disable_colorimetry_for_4k(width, height)

    def _maybe_disable_colorimetry_for_4k(self, width=None, height=None):
        try:
            w = int(width or 0)
            h = int(height or 0)
        except Exception:
            w = h = 0
        if w <= 0 and h <= 0: return
        is_4k = (w >= 3840) or (h >= 2160)
        if not is_4k: return
        if not bool(getattr(self, "colorimetry_enabled", False)): return
        self.colorimetry_enabled = False
        try: self._apply_video_colorimetry()
        except Exception: pass
        try: self._update_colorimetry_menu_sensitivity()
        except Exception: pass
        try:
            item = getattr(self, "colorimetry_check_item", None) or getattr(
                self, "colorimetry_menu_item", None
            )
            if item is not None and hasattr(item, "set_active"):
                try:
                    item.handler_block_by_func(self.toggle_colorimetry_enabled)
                    item.set_active(False)
                    item.handler_unblock_by_func(self.toggle_colorimetry_enabled)
                except Exception:
                    item.set_active(False)
        except Exception: pass
        try:
            self.status_label.set_text(
                self.tr("Colorimétrie") + " — 4K"
            )
        except Exception: pass
        try: self.save_state()
        except Exception: pass
    def _colorimetry_allowed(self):
        path = getattr(self, "current_path", None)
        if not path: return False
        if is_pure_audio_file(path) or is_cdda(path): return False
        if is_video_file(path) or is_dvd(path) or is_bluray(path): return True
        if getattr(self, "current_is_video", False): return True
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
        for attr in (
            "colorimetry_menu_item",
            "colorimetry_check_item",
            "colorimetry_adjust_item",
        ):
            item = getattr(self, attr, None)
            if item is None: continue
            try:
                if attr == "colorimetry_adjust_item":
                    item.set_sensitive(allowed and enabled)
                else:
                    item.set_sensitive(allowed)
            except Exception: pass
        check = getattr(self, "colorimetry_check_item", None)
        if check is not None:
            try:
                if bool(check.get_active()) != enabled:
                    check.handler_block_by_func(self.toggle_colorimetry_enabled)
                    check.set_active(enabled)
                    check.handler_unblock_by_func(self.toggle_colorimetry_enabled)
            except Exception:
                try: check.set_active(enabled)
                except Exception: pass
        if not allowed:
            win = getattr(self, "colorimetry_window", None)
            if win is not None:
                try: win.destroy()
                except Exception: pass
    def _apply_color_props_to(self, balance, gamma):
        if balance is not None:
            for prop, val in (
                ("brightness", float(self.color_brightness)),
                ("contrast", float(self.color_contrast)),
                ("saturation", float(self.color_saturation)),
            ):
                try: balance.set_property(prop, val)
                except Exception: pass
        if gamma is not None:
            try: gamma.set_property("gamma", float(self.color_gamma))
            except Exception: pass
    def _apply_video_colorimetry(self):
        self._apply_color_props_to(
            getattr(self, "_video_balance", None),
            getattr(self, "_video_gamma", None),
        )
        self._apply_color_props_to(
            getattr(self, "_custom_vbalance", None),
            getattr(self, "_custom_vgamma", None),
        )

    def _rebuild_video_color_filter(self, restore_playback=True):
        if not self.colorimetry_enabled and self._video_color_bin is None: return True
        if getattr(self, "_custom_pipeline", None) is not None:
            self._video_balance = None
            self._video_gamma = None
            self._video_color_bin = None
            try: self.player.set_property("video-filter", None)
            except Exception: pass
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
            try:
                self.player.set_state(Gst.State.NULL)
                self.player.get_state(int(2 * Gst.SECOND))
            except Exception: pass
            self._video_balance = None
            self._video_gamma = None
            self._video_color_bin = None
            try: self.player.set_property("video-filter", None)
            except Exception: pass
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
                        try: self.player.set_property("video-filter", None)
                        except Exception: pass
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
                except Exception: pass
                if pos > 0.4:
                    try:
                        self.player.seek_simple(
                            Gst.Format.TIME,
                            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                            int(pos * Gst.SECOND),
                        )
                        self.player.get_state(int(2 * Gst.SECOND))
                    except Exception: pass
                if was_playing:
                    try:
                        self.player.set_state(Gst.State.PLAYING)
                        self.is_playing = True
                        self.is_paused = False
                        self.play_button.set_label("❚❚")
                    except Exception: pass
                elif was_paused:
                    self.is_playing = True
                    self.is_paused = True
                    self.play_button.set_label("▶")
            return ok
        except Exception as e:
            print("rebuild video color:", e, file=sys.stderr)
            return False

    def _link_through_video_color(self, pipe, prev, sink):
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
            try: el.sync_state_with_parent()
            except Exception: pass
        try:
            if not prev.link(chain[0]): return bool(prev.link(sink))
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
                try: self._rebuild_video_color_filter(restore_playback=False)
                except Exception: pass
            else:
                ok = bool(self._rebuild_video_color_filter(restore_playback=True))
        except Exception as e:
            print("colorimetry toggle:", e, file=sys.stderr)
            ok = False
        if not ok and want:
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
                except Exception: pass
            try: self._rebuild_video_color_filter(restore_playback=True)
            except Exception: pass
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
        try: self._update_colorimetry_menu_sensitivity()
        except Exception: pass
        try: self.save_state()
        except Exception: pass
        if not self.colorimetry_enabled:
            win = getattr(self, "colorimetry_window", None)
            if win is not None:
                try: win.destroy()
                except Exception: pass
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
        win.set_default_size(self._ui_s(400), self._ui_s(260))
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
            float(getattr(self, "color_brightness", 0.0)), on_bri,
        )
        self._color_con_scale = _row(
            self.tr("Contraste"), 0.0, 2.0, 0.01,
            float(getattr(self, "color_contrast", 1.0)), on_con,
        )
        self._color_sat_scale = _row(
            self.tr("Saturation"), 0.0, 2.0, 0.01,
            float(getattr(self, "color_saturation", 1.0)), on_sat,
        )
        self._color_gam_scale = _row(
            self.tr("Gamma"), 0.30, 3.0, 0.01,
            float(getattr(self, "color_gamma", 1.0)), on_gam,
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
                try: scale.set_value(val)
                except Exception: pass
            self._apply_video_colorimetry()
            self.status_label.set_text(self.tr("Colorimétrie réinitialisée"))
        reset_btn = Gtk.Button(label=self.tr("Réinitialiser"))
        reset_btn.connect("clicked", on_reset)
        btn_box.pack_start(reset_btn, False, False, 0)
        close_btn = Gtk.Button(label=self.tr("Fermer"))
        close_btn.connect("clicked", lambda b: win.destroy())
        btn_box.pack_start(close_btn, False, False, 0)
        try: self._style_app_dialog(win)
        except Exception: pass
        win.show_all()

    def _is_speed_media(self, path=None):
        path = path if path is not None else getattr(self, "current_path", None)
        if not isinstance(path, str) or not path: return False
        try:
            if is_stream(path): return False
        except Exception: pass
        try:
            if is_video_file(path) or is_dvd(path) or is_bluray(path): return True
        except Exception: pass
        return False

    def _seek_with_rate(self, pipe, flags, ns):
        rate = 1.0
        try:
            if self._is_speed_media():
                rate = float(getattr(self, "playback_rate", 1.0) or 1.0)
        except Exception:
            rate = 1.0
        if rate <= 0.01:
            rate = 1.0
        return bool(
            pipe.seek(
                rate,
                Gst.Format.TIME,
                flags,
                Gst.SeekType.SET,
                int(ns),
                Gst.SeekType.NONE,
                -1,
            )
        )

    def _apply_playback_rate_idle(self):
        try:
            self._apply_playback_rate()
        except Exception as e:
            print("playback rate idle:", e, file=sys.stderr)
        return False

    def _apply_playback_rate(self):
        rate = float(getattr(self, "playback_rate", 1.0) or 1.0)
        if rate <= 0.01:
            rate = 1.0
            self.playback_rate = rate
        pipe = None
        try:
            pipe = self._active_pipeline()
        except Exception:
            pipe = None
        if pipe is None: return False
        pos_ns = 0
        try:
            ok, pos = pipe.query_position(Gst.Format.TIME)
            if ok and pos is not None and pos >= 0:
                pos_ns = int(pos)
            else:
                pos_ns = int(float(getattr(self, "_last_known_position", 0.0) or 0.0) * Gst.SECOND)
        except Exception:
            try:
                pos_ns = int(float(getattr(self, "_last_known_position", 0.0) or 0.0) * Gst.SECOND)
            except Exception:
                pos_ns = 0
        flags = Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE
        try:
            success = bool(
                pipe.seek(
                    rate,
                    Gst.Format.TIME,
                    flags,
                    Gst.SeekType.SET,
                    int(pos_ns),
                    Gst.SeekType.NONE,
                    -1,
                )
            )
        except Exception as e:
            print("playback rate seek:", e, file=sys.stderr)
            success = False
        if success and self.is_playing and not self.is_paused:
            try: pipe.set_state(Gst.State.PLAYING)
            except Exception: pass
        return success

    def set_playback_rate(self, rate):
        try:
            rate = float(rate)
        except Exception:
            rate = 1.0
        allowed = PLAYBACK_RATES
        rate = min(allowed, key=lambda x: abs(x - rate))
        self.playback_rate = rate
        if self._is_speed_media():
            try:
                self._apply_playback_rate()
            except Exception as e:
                print("set_playback_rate:", e, file=sys.stderr)
        try:
            self.status_label.set_text(
                self.tr("Vitesse") + " : " + ("x " + ("%.2f" % rate).rstrip("0").rstrip("."))
            )
        except Exception: pass
        try: self.save_state()
        except Exception: pass
    def show_speed(self, *args):
        if not self._is_speed_media():
            try:
                self.status_label.set_text(
                    self.tr("Vitesse") + " — " + self.tr("vidéo uniquement")
                )
            except Exception: pass
            return
        if getattr(self, "speed_window", None) is not None:
            try: self.speed_window.present()
            except Exception: pass
            return
        win = Gtk.Window(title=self.tr("Vitesse"))
        win.set_transient_for(self)
        win.set_default_size(self._ui_s(320), self._ui_s(260))
        win.set_resizable(False)
        win.set_border_width(0)
        self.speed_window = win
        def on_speed_destroy(*a):
            self.speed_window = None
        win.connect("destroy", on_speed_destroy)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        win.add(outer)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        try:
            content.set_margin_start(self._ui_s(14))
            content.set_margin_end(self._ui_s(14))
            content.set_margin_top(self._ui_s(10))
            content.set_margin_bottom(self._ui_s(12))
        except Exception: pass
        outer.pack_start(content, True, True, 0)
        info = Gtk.Label(
            label=self.tr("Vitesse de lecture")
        )
        info.set_xalign(0)
        try: info.get_style_context().add_class("status")
        except Exception: pass
        content.pack_start(info, False, False, 0)
        rates = PLAYBACK_RATES
        cur = float(getattr(self, "playback_rate", 1.0) or 1.0)
        group = None
        def _rate_label(r):
            s = ("%.2f" % float(r)).rstrip("0").rstrip(".")
            lab = "x " + s
            if abs(float(r) - 1.0) < 0.001:
                lab = "x 1.0  (%s)" % self.tr("normal")
            return lab
        for r in rates:
            label = _rate_label(r)
            item = Gtk.RadioButton.new_with_label_from_widget(group, label)
            if group is None:
                group = item
            item.set_active(abs(cur - r) < 0.001)
            def _on_toggled(btn, rate=r):
                if btn.get_active():
                    self.set_playback_rate(rate)
            item.connect("toggled", _on_toggled)
            content.pack_start(item, False, False, 0)
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content.pack_start(btn_row, False, False, 0)
        close_btn = Gtk.Button(label=self.tr("Fermer"))
        close_btn.connect("clicked", lambda b: win.destroy())
        btn_row.pack_end(close_btn, False, False, 0)
        try: self._style_app_dialog(win)
        except Exception: pass
        win.show_all()

    def show_pitch_tempo(self, *args):
        if not is_pure_audio_file(getattr(self, "current_path", None)):
            self.status_label.set_text(
                self.tr("Pitch / Tempo") + " — " + self.tr("audio uniquement")
            )
            return
        if self.pitch is None:
            try:
                self.pitch = Gst.ElementFactory.make("pitch", "pitch")
            except Exception:
                self.pitch = None
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
        self._style_app_dialog(win)
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
        item = getattr(self, "pitch_menu_item", None)
        if item is None: return
        allowed = is_pure_audio_file(getattr(self, "current_path", None))
        try: item.set_sensitive(allowed)
        except Exception: pass
        if not allowed and getattr(self, "pitch_window", None) is not None:
            try: self.pitch_window.destroy()
            except Exception: pass
        try: self._update_speed_menu_sensitivity()
        except Exception: pass
    def _update_speed_menu_sensitivity(self):
        item = getattr(self, "speed_menu_item", None)
        allowed = False
        try:
            allowed = self._is_speed_media()
        except Exception:
            allowed = False
        if item is not None:
            try: item.set_sensitive(allowed)
            except Exception: pass
        if not allowed and getattr(self, "speed_window", None) is not None:
            try: self.speed_window.destroy()
            except Exception: pass
    def _notify_track_change(self, title, artist, album=None):
        if not getattr(self, "desktop_notifications", True): return
        if Notify is None: return
        if getattr(self, "_notify_unavailable", False): return
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
            except Exception: pass
        title = (title or "").strip() or self.tr("Inconnu")
        artist = (artist or "").strip()
        key = (title, artist)
        if key == getattr(self, "_last_notify_key", None): return
        self._last_notify_key = key
        body = artist
        if album and str(album).strip():
            body = (body + " — " + str(album).strip()) if body else str(album).strip()
        try:
            n = Notify.Notification.new(title, body or None, "audio-x-generic")
            try:
                n.set_urgency(Notify.Urgency.LOW)
                n.set_timeout(4000)
            except Exception: pass
            n.show()
        except Exception:
            self._notify_unavailable = True

    def _get_screensaver_proxy(self):
        cached = getattr(self, "_screensaver_proxy", None)
        if cached is not None: return cached
        candidates = (
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
        try:
            if not enable:
                cookie = getattr(self, "_screensaver_cookie", None)
                kind_cookie = getattr(self, "_screensaver_cookie_kind", None)
                if cookie is None: return
                proxy_info = self._get_screensaver_proxy()
                self._screensaver_cookie = None
                self._screensaver_cookie_kind = None
                if not proxy_info: return
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
                            except Exception: pass
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
                except Exception: pass
                return
            if getattr(self, "_screensaver_cookie", None) is not None: return
            if not getattr(self, "current_is_video", False): return
            if not (
                getattr(self, "is_playing", False)
                and not getattr(self, "is_paused", False)
            ):
                return
            proxy_info = self._get_screensaver_proxy()
            if not proxy_info: return
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
                self._screensaver_proxy = False
                self._screensaver_cookie = None
        except Exception: pass

    def _update_screensaver_inhibit(self):
        want = (
            getattr(self, "current_is_video", False)
            and getattr(self, "is_playing", False)
            and not getattr(self, "is_paused", False)
        )
        self._inhibit_screensaver(want)

    def toggle_mini_player(self, *args):
        want = not bool(getattr(self, "mini_player_mode", False))
        if want:
            self.nano_player_mode = False
            self.mini_player_mode = True
        else:
            self.mini_player_mode = False
            self.nano_player_mode = False
        self._apply_compact_player_mode()
        try: self.save_state()
        except Exception: pass
    def toggle_mini_banner(self, *args):
        self.mini_banner_enabled = not getattr(self, "mini_banner_enabled", True)
        self._apply_mini_banner_state()
        try: self.save_state()
        except Exception: pass
    def _apply_mini_banner_state(self):
        if not getattr(self, "mini_player_mode", False): return
        area = getattr(self, "mini_title_area", None)
        if area is None: return
        try:
            if getattr(self, "mini_banner_enabled", True):
                self.set_title(APP_NAME)
                try:
                    area.set_no_show_all(False)
                    area.set_size_request(
                        -1, self._ui_s(self._MINI_MARQUEE_HEIGHT)
                    )
                except Exception: pass
                self._mini_marquee_start()
            else:
                self._stop_mini_title_scroll()
                try:
                    area.hide()
                    area.set_no_show_all(True)
                except Exception: pass
            self._update_mini_title()
            try: self._resize_mini_window()
            except Exception: pass
        except Exception as e:
            print("mini banner:", e, file=sys.stderr)

    def _build_mini_title_marquee(self, main):
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
            font_pt = max(8, int(round(9 * float(getattr(self, "ui_scale", 1.0) or 1.0))))
            layout.set_font_description(
                Pango.FontDescription.from_string(f"Sans Bold {font_pt}")
            )
        except Exception:
            try:
                layout.set_font_description(
                    Pango.FontDescription.from_string(self._MINI_MARQUEE_FONT)
                )
            except Exception: pass
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
        if area is None: return
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
            try: area.remove_tick_callback(tid)
            except Exception: pass
        self._mini_mq_tick_id = 0
        self._mini_mq_text = None
        self._mini_mq_offset = 0.0
        self._mini_mq_drawn = -1
        self._mini_mq_last_us = None

    def _mini_marquee_tick(self, widget, frame_clock, *args):
        if not getattr(self, "mini_player_mode", False):
            self._mini_mq_tick_id = 0
            return False
        now = frame_clock.get_frame_time()  # microsecondes
        last = self._mini_mq_last_us
        self._mini_mq_last_us = now
        if last is None or not self._mini_mq_text: return True
        if now < self._mini_mq_pause_until: return True
        width = widget.get_allocated_width()
        text_w = self._mini_mq_text_w
        pad = self._ui_s(self._MINI_MARQUEE_PAD)
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
            self._mini_mq_offset = 0.0
            self._mini_mq_pause_until = (
                now + self._MINI_MARQUEE_PAUSE_MS * 1000
            )
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
        def _clean(s):
            return (s or "").strip()
        def _join(artist, title):
            artist, title = _clean(artist), _clean(title)
            if artist and title:
                low_t, low_a = title.casefold(), artist.casefold()
                if low_t == low_a or low_t.startswith(low_a + " —") or low_t.startswith(low_a + " -"): return title
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
                if low_n == low_s or low_n.startswith(low_s + " —") or low_n.startswith(low_s + " -"): return now
                return "%s — %s" % (station, now)
            return station or now or self.tr("En direct")
        if path and is_cdda(path):
            track = get_cdda_track_number(path) or 0
            title = self.tr("Piste ") + "%02d" % track
            artist = ""
            try: artist = _clean(self.bottom_artist.get_text())
            except Exception: pass
            if not artist or artist == self.tr("CD Audio"):
                try:
                    t, a, *_rest = self.get_metadata(path, load_cover=False)
                    if a:
                        artist = _clean(a)
                    if t and _clean(t) and not _clean(t).startswith("Piste"):
                        title = _clean(t)
                except Exception: pass
            return _join(artist or self.tr("CD Audio"), title)
        title = artist = ""
        try:
            if hasattr(self, "bottom_title"):
                title = _clean(self.bottom_title.get_text())
            if hasattr(self, "bottom_artist"):
                artist = _clean(self.bottom_artist.get_text())
        except Exception: pass
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
        if not getattr(self, "mini_player_mode", False):
            self._stop_mini_title_scroll()
            try: self.set_title(APP_NAME)
            except Exception: pass
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
            except Exception: pass
            return
        if title == getattr(self, "_mini_mq_text", None): return
        self._mini_mq_text = title
        layout = getattr(self, "_mini_mq_layout", None)
        text_w = text_h = 0
        if layout is not None:
            try:
                layout.set_text(title or "", -1)
                text_w, text_h = layout.get_pixel_size()
            except Exception: pass
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
        try: self._update_nano_tooltip()
        except Exception: pass
    def _apply_mini_player_mode(self):
        if getattr(self, "mini_player_mode", False) and getattr(self, "nano_player_mode", False):
            self.nano_player_mode = False
        self._apply_compact_player_mode()

    def toggle_nano_player(self, *args):
        want = not bool(getattr(self, "nano_player_mode", False))
        if want:
            self.mini_player_mode = False
            self.nano_player_mode = True
            self.nano_banner_enabled = False
        else:
            self.nano_player_mode = False
            self.mini_player_mode = False
        self._apply_compact_player_mode()
        try: self.save_state()
        except Exception: pass
    def toggle_nano_banner(self, *args):
        self.nano_banner_enabled = not bool(getattr(self, "nano_banner_enabled", False))
        if getattr(self, "nano_player_mode", False):
            self._apply_nano_banner_state()
        try: self.save_state()
        except Exception: pass
    def toggle_nano_vertical(self, *args):
        self.nano_vertical = not bool(getattr(self, "nano_vertical", False))
        if getattr(self, "nano_player_mode", False):
            self._apply_compact_player_mode()
        try: self.save_state()
        except Exception: pass
    def toggle_nano_playlist(self, *args):
        self.nano_playlist_enabled = not bool(
            getattr(self, "nano_playlist_enabled", False)
        )
        if getattr(self, "nano_player_mode", False):
            self._apply_compact_player_mode()
        try: self.save_state()
        except Exception: pass
    def set_nano_playlist_side(self, side):
        side = "left" if str(side).lower() == "left" else "right"
        if getattr(self, "nano_playlist_side", "right") == side: return
        self.nano_playlist_side = side
        if getattr(self, "nano_player_mode", False) and getattr(
            self, "nano_playlist_enabled", False
        ):
            self._apply_compact_player_mode()
        try: self.save_state()
        except Exception: pass
    def _build_nano_playlist_panel(self, main):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        try:
            box.get_style_context().add_class("nano-playlist-box")
            box.get_style_context().add_class("playlist-box")
        except Exception: pass
        box.set_no_show_all(True)
        box.hide()
        self.nano_playlist_box = box
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        try: scroll.set_overlay_scrolling(False)
        except Exception: pass
        try:
            scroll.get_style_context().add_class("playlist-scroll")
            scroll.get_style_context().add_class("nano-playlist-scroll")
        except Exception: pass
        scroll.set_no_show_all(True)
        self.nano_playlist_scroll = scroll
        tree = Gtk.TreeView(model=self.playlist)
        tree.set_headers_visible(False)
        tree.set_enable_search(False)
        tree.set_no_show_all(True)
        try: tree.get_selection().set_mode(Gtk.SelectionMode.SINGLE)
        except Exception: pass
        try: tree.get_style_context().add_class("nano-playlist-tree")
        except Exception: pass
        for col_id in (self.COL_TITLE, self.COL_ARTIST):
            col = Gtk.TreeViewColumn()
            cell = Gtk.CellRendererText()
            try: cell.set_property("ellipsize", Pango.EllipsizeMode.END)
            except Exception: pass
            col.pack_start(cell, True)
            col.set_cell_data_func(cell, self._playlist_cell_data, col_id)
            col.set_expand(True)
            tree.append_column(col)
        tree.set_can_focus(True)
        tree.connect("row-activated", self._nano_playlist_row_activated)
        tree.connect("key-press-event", self._on_nano_playlist_key_press)
        self.nano_playlist_tree = tree
        scroll.add(tree)
        box.pack_start(scroll, True, True, 0)
        main.pack_start(box, False, False, 0)

    def _nano_playlist_row_activated(self, tree, path, column):
        try:
            if path is None or tree is None: return
            model = tree.get_model()
            if model is None: return
            try:
                it = model.get_iter(path)
            except Exception:
                return
            if it is None: return
            filename = model.get_value(it, self.COL_PATH)
            if not filename or not isinstance(filename, str): return
            self.play_file(filename)
        except Exception as e:
            print("nano playlist activate:", e, file=sys.stderr)

    def _on_nano_playlist_key_press(self, tree, event):
        try:
            key = Gdk.keyval_name(Gdk.keyval_to_lower(event.keyval)) or ""
        except Exception:
            key = ""
        if key in ("up", "kp_up"):
            self.move_playlist_selection(-1)
            return True
        if key in ("down", "kp_down"):
            self.move_playlist_selection(1)
            return True
        if key in ("return", "kp_enter"):
            try:
                model, paths = tree.get_selection().get_selected_rows()
                if paths and model is not None:
                    it = model.get_iter(paths[0])
                    filename = model.get_value(it, self.COL_PATH)
                    if filename:
                        self.play_file(filename)
                        return True
            except Exception: pass
        return False

    def _hide_nano_playlist_panel(self):
        box = getattr(self, "nano_playlist_box", None)
        if box is None: return
        for w in (
            box,
            getattr(self, "nano_playlist_scroll", None),
            getattr(self, "nano_playlist_tree", None),
        ):
            if w is None: continue
            try: w.set_no_show_all(True)
            except Exception: pass
            try: w.hide()
            except Exception: pass
        try: box.set_size_request(-1, 0)
        except Exception: pass
    def _apply_nano_playlist_layout(self):
        box = getattr(self, "nano_playlist_box", None)
        if box is None: return
        nano = bool(getattr(self, "nano_player_mode", False))
        enabled = bool(nano and getattr(self, "nano_playlist_enabled", False))
        main = None
        try:
            main = self.get_child()
        except Exception:
            main = None
        if not enabled:
            self._hide_nano_playlist_panel()
            try:
                if main is not None:
                    main.set_orientation(Gtk.Orientation.VERTICAL)
            except Exception: pass
            return
        row_h = max(16, self._ui_s(20))
        pl_h = row_h * 8
        vertical = bool(getattr(self, "nano_vertical", False))
        side = getattr(self, "nano_playlist_side", "right") or "right"
        top_wrap = getattr(self, "top_bar_wrap", None) or getattr(self, "top_bar", None)
        try:
            box.set_no_show_all(False)
            scroll = getattr(self, "nano_playlist_scroll", None)
            tree = getattr(self, "nano_playlist_tree", None)
            for w in (scroll, tree):
                if w is not None:
                    w.set_no_show_all(False)
            if vertical:
                pl_w = self._ui_s(180)
                box.set_size_request(pl_w, pl_h)
                if scroll is not None:
                    scroll.set_size_request(pl_w, pl_h)
            else:
                box.set_size_request(-1, pl_h)
                if scroll is not None:
                    scroll.set_size_request(-1, pl_h)
            try:
                box.set_hexpand(False)
                box.set_vexpand(False)
            except Exception: pass
            box.show_all()
            try:
                tree = getattr(self, "nano_playlist_tree", None)
                if tree is not None:
                    try:
                        cur = getattr(self, "current_path", None)
                        if cur and tree.get_model() is not None:
                            model = tree.get_model()
                            it = model.get_iter_first()
                            while it is not None:
                                if model.get_value(it, self.COL_PATH) == cur:
                                    path = model.get_path(it)
                                    tree.get_selection().unselect_all()
                                    tree.get_selection().select_path(path)
                                    tree.set_cursor(path, None, False)
                                    tree.scroll_to_cell(path, None, True, 0.5, 0)
                                    break
                                it = model.iter_next(it)
                    except Exception: pass
                    tree.grab_focus()
            except Exception: pass
        except Exception as e:
            print("nano playlist show:", e, file=sys.stderr)
            return
        if main is None: return
        try:
            if vertical:
                main.set_orientation(Gtk.Orientation.HORIZONTAL)
                if side == "left":
                    main.reorder_child(box, 0)
                    if top_wrap is not None:
                        main.reorder_child(top_wrap, 1)
                else:
                    if top_wrap is not None:
                        main.reorder_child(top_wrap, 0)
                    main.reorder_child(box, 1)
            else:
                main.set_orientation(Gtk.Orientation.VERTICAL)
                if top_wrap is not None:
                    main.reorder_child(top_wrap, 0)
                main.reorder_child(box, 1)
        except Exception as e:
            print("nano playlist reorder:", e, file=sys.stderr)

    def _queue_resize_recursive(self, widget):
        if widget is None: return
        try: widget.queue_resize()
        except Exception: pass
        try:
            if isinstance(widget, Gtk.Container):
                for child in widget.get_children():
                    self._queue_resize_recursive(child)
        except Exception: pass

    def _resize_nano_window_deferred(self):
        if getattr(self, "nano_player_mode", False):
            self._resize_nano_window(_deferred=True)
        return False

    def _resize_nano_window(self, _deferred=False):
        if not bool(getattr(self, "nano_player_mode", False)): return
        pl = bool(getattr(self, "nano_playlist_enabled", False))
        vertical = bool(getattr(self, "nano_vertical", False))
        row_h = max(16, self._ui_s(18))
        pl_h = row_h * 8
        pl_w = self._ui_s(180)
        top = getattr(self, "top_bar", None)
        measure = getattr(self, "top_bar_wrap", None) or top
        btn_w, btn_h = self._ui_s(210), self._ui_s(28)
        try:
            if measure is not None:
                self._queue_resize_recursive(measure)
                _min, nat = measure.get_preferred_size()
                nw = int(getattr(nat, "width", 0) or 0)
                nh = int(getattr(nat, "height", 0) or 0)
                if nw > 0:
                    btn_w = nw
                if nh > 0:
                    btn_h = nh
        except Exception: pass
        if vertical:
            btn_w = max(btn_w, self._ui_s(32))
            btn_h = max(btn_h, self._ui_s(180))
        else:
            btn_w = max(btn_w, self._ui_s(200))
            btn_h = max(btn_h, self._ui_s(26))
        banner_h = 0
        try:
            if bool(getattr(self, "nano_banner_enabled", False)):
                banner_h = max(12, self._ui_s(10))
        except Exception:
            banner_h = 0
        if pl:
            if vertical:
                w = btn_w + pl_w
                h = max(pl_h, btn_h) + banner_h
            else:
                w = max(btn_w, pl_w)
                h = btn_h + pl_h + banner_h
        else:
            w, h = btn_w, btn_h + banner_h
        w = max(1, int(w))
        h = max(1, int(h))
        try: self._apply_compact_fixed_geometry(w, h)
        except Exception: pass
        if not _deferred:
            try: GLib.timeout_add(150, self._resize_nano_window_deferred)
            except Exception: pass
    def _apply_compact_fixed_geometry(self, width, height):
        try:
            if self.is_maximized():
                self.unmaximize()
        except Exception: pass
        try:
            if bool(getattr(self, "is_fullscreen", False)):
                try: self.unfullscreen()
                except Exception: pass
        except Exception: pass
        try: self.set_resizable(False)
        except Exception: pass
        w = max(1, int(width))
        h = max(1, int(height))
        try: self.set_size_request(1, 1)
        except Exception: pass
        try: self.resize(w, h)
        except Exception: pass
        try: self.set_size_request(w, h)
        except Exception: pass
    def _resize_mini_window(self):
        if not bool(getattr(self, "mini_player_mode", False)): return
        if bool(getattr(self, "nano_player_mode", False)): return
        marquee = getattr(self, "mini_title_area", None)
        top = getattr(self, "top_bar", None)
        top_wrap = getattr(self, "top_bar_wrap", None) or top
        banner_on = bool(getattr(self, "mini_banner_enabled", True))
        if marquee is not None:
            try:
                if banner_on:
                    marquee.set_no_show_all(False)
                    marquee.set_size_request(
                        -1, self._ui_s(self._MINI_MARQUEE_HEIGHT)
                    )
                    marquee.show()
                else:
                    self._stop_mini_title_scroll()
                    marquee.hide()
                    marquee.set_no_show_all(True)
            except Exception: pass
        mini_w = self._ui_s(520)
        btn_h = self._ui_s(40)
        banner_h = 0
        try:
            measure = top_wrap or top
            if measure is not None:
                self._queue_resize_recursive(measure)
                _min, nat = measure.get_preferred_size()
                nw = int(getattr(nat, "width", 0) or 0)
                nh = int(getattr(nat, "height", 0) or 0)
                if nw > 0:
                    mini_w = max(mini_w, nw)
                if nh > 0:
                    btn_h = nh
        except Exception: pass
        if banner_on and marquee is not None:
            try:
                self._queue_resize_recursive(marquee)
                _min, nat = marquee.get_preferred_size()
                mh = int(getattr(nat, "height", 0) or 0)
                if mh > 0:
                    banner_h = mh
                else:
                    banner_h = self._ui_s(self._MINI_MARQUEE_HEIGHT)
            except Exception:
                banner_h = self._ui_s(self._MINI_MARQUEE_HEIGHT)
        mini_h = max(1, int(btn_h) + int(banner_h))
        try: self._apply_compact_fixed_geometry(mini_w, mini_h)
        except Exception: pass
    def _apply_nano_banner_state(self):
        if not getattr(self, "nano_player_mode", False): return
        area = getattr(self, "mini_title_area", None)
        if area is None: return
        try:
            if getattr(self, "nano_banner_enabled", False):
                self.set_title(APP_NAME)
                try:
                    h = max(12, self._ui_s(10))
                    area.set_size_request(-1, h)
                except Exception: pass
                self._mini_marquee_start()
            else:
                self._stop_mini_title_scroll()
                area.hide()
            self._update_mini_title()
            try: self._resize_nano_window()
            except Exception: pass
        except Exception as e:
            print("nano banner:", e, file=sys.stderr)

    def _nano_tooltip_text(self):
        try:
            title = self._compose_mini_banner_text() or self.tr("Aucun morceau sélectionné")
        except Exception:
            title = self.tr("Aucun morceau sélectionné")
        lines = [title]
        path = getattr(self, "current_path", None)
        if path and not is_stream(path) and not is_cdda(path):
            try:
                t, a, al, y, dur, _c, tech = self.get_metadata(path, load_cover=False)
                if a:
                    lines.append(a)
                if al:
                    lines.append(al)
                if y:
                    lines.append(str(y))
                if dur:
                    lines.append(format_time(dur))
                fmt = (tech or {}).get("format") if isinstance(tech, dict) else None
                if fmt:
                    lines.append(str(fmt))
            except Exception: pass
        elif path and is_stream(path):
            try:
                icy = (getattr(self, "icy_title", "") or "").strip()
                if icy and icy not in title:
                    lines.append(icy)
            except Exception: pass
        out = []
        seen = set()
        for ln in lines:
            s = (ln or "").strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return "\n".join(out) if out else APP_NAME

    def _update_nano_tooltip(self):
        if not getattr(self, "nano_player_mode", False):
            try: self.set_tooltip_text(None)
            except Exception: pass
            for attr in (
                "top_bar", "top_bar_wrap", "nano_playlist_box",
                "nano_playlist_tree", "nano_playlist_scroll",
            ):
                w = getattr(self, attr, None)
                if w is not None:
                    try: w.set_tooltip_text(None)
                    except Exception: pass
            return
        text = None
        try:
            text = self._nano_tooltip_text()
        except Exception:
            text = APP_NAME
        for attr in (
            None,
            "top_bar",
            "top_bar_wrap",
            "nano_playlist_box",
            "nano_playlist_tree",
            "nano_playlist_scroll",
        ):
            w = self if attr is None else getattr(self, attr, None)
            if w is None: continue
            try: w.set_tooltip_text(text)
            except Exception: pass
    def _nano_chrome_widgets(self):
        names = (
            "position_scale",
            "time_label",
            "mute_button",
            "volume_scale",
            "bluetooth_button",
        )
        out = []
        for n in names:
            w = getattr(self, n, None)
            if w is not None:
                out.append(w)
        return out

    def _apply_nano_chrome(self, active):
        for w in self._nano_chrome_widgets():
            try:
                if active:
                    w.hide()
                else:
                    w.show()
            except Exception: pass
        marquee = getattr(self, "mini_title_area", None)
        if active and marquee is not None:
            try:
                self._stop_mini_title_scroll()
                marquee.hide()
            except Exception: pass

    def _restore_normal_ui_widgets(self):
        for attr in (
            "content_paned",
            "playlist_box",
            "playlist_scroll",
            "tree",
            "search_row",
            "search_entry",
            "visualizer",
            "video_overlay",
            "video_area",
            "bottom_bar_wrap",
            "bottom_bar",
            "top_bar_wrap",
            "top_bar",
            "position_scale",
            "time_label",
            "mute_button",
            "volume_scale",
            "bluetooth_button",
        ):
            w = getattr(self, attr, None)
            if w is None: continue
            try: w.set_no_show_all(False)
            except Exception: pass
        try:
            main = self.get_child()
            if main is not None:
                main.set_orientation(Gtk.Orientation.VERTICAL)
        except Exception: pass
        top = getattr(self, "top_bar", None)
        try:
            if top is not None:
                top.set_orientation(Gtk.Orientation.HORIZONTAL)
        except Exception: pass
        for attr in ("top_bar_wrap", "top_bar", "content_paned"):
            w = getattr(self, attr, None)
            if w is not None:
                try: w.show()
                except Exception: pass
        for attr in ("playlist_box", "playlist_scroll", "search_row", "search_entry", "tree"):
            w = getattr(self, attr, None)
            if w is not None:
                try: w.show()
                except Exception: pass
        try:
            tree = getattr(self, "tree", None)
            if tree is not None:
                tree.set_headers_visible(
                    not bool(getattr(self, "playlist_only_mode", False))
                )
        except Exception: pass
        try:
            vis = getattr(self, "visualizer", None)
            if vis is not None:
                vis.show()
        except Exception: pass
        self._apply_nano_chrome(False)
        try: self._apply_metadata_panel_visibility()
        except Exception: pass
        try:
            if getattr(self, "current_is_video", False):
                self.show_video_area(True)
            elif getattr(self, "lyrics_mode", False):
                self._refresh_lyrics_for_current()
            else:
                self.show_video_area(False)
        except Exception: pass
        try: self._hide_nano_playlist_panel()
        except Exception: pass
        marquee = getattr(self, "mini_title_area", None)
        if marquee is not None:
            try:
                self._stop_mini_title_scroll()
                marquee.hide()
            except Exception: pass
        try:
            if bool(getattr(self, "playlist_only_mode", False)):
                self._apply_playlist_only_mode()
        except Exception: pass

    def _apply_compact_player_mode(self):
        if getattr(self, "nano_player_mode", False) and getattr(
            self, "mini_player_mode", False
        ):
            self.mini_player_mode = False
        nano = bool(getattr(self, "nano_player_mode", False))
        mini = bool(getattr(self, "mini_player_mode", False)) and not nano
        marquee = getattr(self, "mini_title_area", None)
        top = getattr(self, "top_bar", None)
        top_wrap = getattr(self, "top_bar_wrap", None) or top
        try:
            if nano or mini:
                if self._normal_size is None:
                    try:
                        self._normal_size = self.get_size()
                    except Exception:
                        self._normal_size = (950, 620)
                try:
                    child = self.get_child()
                    nano_pl = getattr(self, "nano_playlist_box", None)
                    if child is not None:
                        for c in child.get_children():
                            if c is top_wrap or c is top:
                                try: c.show()
                                except Exception: pass
                            elif c is marquee:
                                continue
                            elif c is nano_pl:
                                continue
                            else:
                                try: c.hide()
                                except Exception: pass
                except Exception: pass
                try: self._apply_window_layer()
                except Exception: pass
                try: self.set_title(APP_NAME)
                except Exception: pass
                if nano:
                    try: self.get_style_context().add_class("nano-mode")
                    except Exception: pass
                    try:
                        if top is not None:
                            if getattr(self, "nano_vertical", False):
                                top.set_orientation(Gtk.Orientation.VERTICAL)
                            else:
                                top.set_orientation(Gtk.Orientation.HORIZONTAL)
                    except Exception: pass
                    self._apply_nano_chrome(True)
                    for attr in (
                        "content_paned",
                        "bottom_bar_wrap",
                        "bottom_bar",
                        "video_overlay",
                    ):
                        w = getattr(self, attr, None)
                        if w is not None:
                            try: w.hide()
                            except Exception: pass
                    if marquee is not None:
                        try:
                            self._stop_mini_title_scroll()
                            marquee.hide()
                        except Exception: pass
                    try:
                        self._apply_nano_playlist_layout()
                    except Exception as e:
                        print("nano playlist layout:", e, file=sys.stderr)
                    try: self._resize_nano_window()
                    except Exception: pass
                    try: self._update_nano_tooltip()
                    except Exception: pass
                else:
                    try: self.get_style_context().remove_class("nano-mode")
                    except Exception: pass
                    try:
                        if top is not None:
                            top.set_orientation(Gtk.Orientation.HORIZONTAL)
                    except Exception: pass
                    self._apply_nano_chrome(False)
                    try: self._hide_nano_playlist_panel()
                    except Exception: pass
                    try:
                        if marquee is not None:
                            if getattr(self, "mini_banner_enabled", True):
                                marquee.set_no_show_all(False)
                                marquee.set_size_request(
                                    -1, self._ui_s(self._MINI_MARQUEE_HEIGHT)
                                )
                                self._mini_marquee_start()
                            else:
                                self._stop_mini_title_scroll()
                                marquee.hide()
                                marquee.set_no_show_all(True)
                        self._update_mini_title()
                    except Exception: pass
                    try: self._resize_mini_window()
                    except Exception: pass
                    try: self.set_tooltip_text(None)
                    except Exception: pass
            else:
                try: self.get_style_context().remove_class("nano-mode")
                except Exception: pass
                try: self.set_title(APP_NAME)
                except Exception: pass
                try: self.set_tooltip_text(None)
                except Exception: pass
                try:
                    self._restore_normal_ui_widgets()
                except Exception as e:
                    print("restore normal ui:", e, file=sys.stderr)
                try: self._apply_window_layer()
                except Exception: pass
                try: self.set_resizable(True)
                except Exception: pass
                try: self.set_size_request(self._ui_s(700), self._ui_s(430))
                except Exception: pass
                if self._normal_size:
                    try:
                        self.resize(
                            int(self._normal_size[0]), int(self._normal_size[1])
                        )
                    except Exception: pass
                self._normal_size = None
        except Exception as e:
            print("compact player:", e, file=sys.stderr)

    def show_transparency_settings(self, *args):
        dialog = Gtk.Dialog(
            title=self.tr("Réglages de transparence"),
            parent=self,
            flags=0,
        )
        dialog.add_button(Gtk.STOCK_CLOSE, Gtk.ResponseType.CLOSE)
        self._style_app_dialog(dialog)
        try: dialog.set_default_size(self._ui_s(360), self._ui_s(140))
        except Exception: pass
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)
        label = Gtk.Label(
            label=self.tr("Opacité du fond de la playlist"),
            xalign=0,
        )
        box.pack_start(label, False, False, 0)
        adj = Gtk.Adjustment(
            value=float(getattr(self, "playlist_transparency_opacity", 0.35)) * 100.0,
            lower=0.0,
            upper=100.0,
            step_increment=1.0,
            page_increment=5.0,
        )
        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
        scale.set_digits(0)
        scale.set_hexpand(True)
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        def _on_change(sc):
            try:
                self.playlist_transparency_opacity = max(
                    0.0, min(1.0, float(sc.get_value()) / 100.0)
                )
            except Exception:
                return
            tid = getattr(self, "_transparency_css_timeout_id", 0) or 0
            if tid:
                try: GLib.source_remove(tid)
                except Exception: pass
            def _apply_debounced():
                self._transparency_css_timeout_id = 0
                try: self.build_css()
                except Exception: pass
                if getattr(self, "playlist_transparency", False):
                    try: self._apply_playlist_transparency()
                    except Exception: pass
                try: self.save_state()
                except Exception: pass
                return False
            self._transparency_css_timeout_id = GLib.timeout_add(120, _apply_debounced)
        scale.connect("value-changed", _on_change)
        box.pack_start(scale, False, False, 0)
        hint = Gtk.Label(
            label=self.tr("0 % = transparent, 100 % = opaque")
            + "\n"
            + self.tr(
                "Nécessite la composition XFCE (Peaufinage des fenêtres). "
                "Compton/Picom optionnel."
            ),
            xalign=0,
        )
        try: hint.get_style_context().add_class("status")
        except Exception: pass
        box.pack_start(hint, False, False, 0)
        dialog.show_all()
        dialog.run()
        dialog.destroy()

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
        if resp != Gtk.ResponseType.OK or not path: return
        try:
            paths = self._parse_playlist_file(path)
        except Exception as e:
            print("import playlist:", e, file=sys.stderr)
            paths = []
        added = 0
        for p in paths:
            if not p: continue
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
                except Exception: pass
        if added:
            self.save_playlist()
            self.status_label.set_text(
                f"{added} "
                + self.tr("fichier(s) ajouté(s)")
                + " — "
                + self.tr("Playlist importée")
            )
            try: self.tree.queue_draw()
            except Exception: pass
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
        if resp != Gtk.ResponseType.OK or not out: return
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
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        base = os.path.dirname(os.path.abspath(path))
        low = path.lower()
        results = []
        if low.endswith(".pls"):
            entries = {}
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("[") or "=" not in line: continue
                k, v = line.split("=", 1)
                entries[k.strip().lower()] = v.strip()
            for i in range(1, len(entries) + 2):
                key = "file%d" % i
                if key in entries:
                    results.append(entries[key])
        else:
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"): continue
                results.append(line)
        resolved = []
        for p in results:
            p = p.strip().strip('"')
            if not p: continue
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
                except Exception: pass
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
                except Exception: pass
                f.write("Title%d=%s\n" % (i, title.replace("\n", " ")))
            f.write("Version=2\n")

    def _load_recent_files(self):
        self.recent_files = []
        try:
            if not os.path.isfile(RECENT_FILE): return
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
        if not path or not isinstance(path, str): return
        if is_stream(path) or is_cdda(path) or is_dvd(path) or is_bluray(path): return
        try:
            if os.path.isfile(path):
                path = os.path.abspath(path)
        except Exception: pass
        try:
            self.recent_files = [p for p in self.recent_files if p != path]
            self.recent_files.insert(0, path)
            self.recent_files = self.recent_files[:RECENT_MAX]
            self._save_recent_files()
        except Exception: pass

    def clear_recent_files(self, *args):
        self.recent_files = []
        self._save_recent_files()
        try: self.status_label.set_text(self.tr("Historique vide"))
        except Exception: pass
    def _resume_key(self, path):
        try:
            return os.path.abspath(path)
        except Exception:
            return path

    def _resume_eligible(self, path):
        try:
            return (
                isinstance(path, str)
                and not is_special_source(path)
                and is_video_file(path)
                and os.path.isfile(path)
            )
        except Exception:
            return False

    def _load_resume_data(self):
        self._resume_data = {}
        if not os.path.isfile(RESUME_FILE): return
        try:
            with open(RESUME_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._resume_data = {
                    k: v
                    for k, v in data.items()
                    if isinstance(k, str) and isinstance(v, dict)
                }
        except Exception as exc:
            print("load resume:", exc, file=sys.stderr)

    def _save_resume_data(self):
        if getattr(self, "_resume_reset", False): return
        data = self._resume_data
        if len(data) > RESUME_MAX:
            keep = sorted(
                data.items(),
                key=lambda kv: float(kv[1].get("t", 0) or 0),
                reverse=True,
            )[:RESUME_MAX]
            self._resume_data = data = dict(keep)
        try:
            atomic_write_json(RESUME_FILE, data)
        except Exception as exc:
            print("save resume:", exc, file=sys.stderr)

    def _resume_forget(self, path):
        try:
            if self._resume_data.pop(self._resume_key(path), None) is not None:
                self._save_resume_data()
        except Exception: pass

    def _resume_lookup(self, path):
        rec = self._resume_data.get(self._resume_key(path))
        if not isinstance(rec, dict): return None
        try:
            st = os.stat(path)
            if (
                int(rec.get("m", -1)) != int(st.st_mtime)
                or int(rec.get("s", -1)) != int(st.st_size)
            ):
                raise ValueError("fichier modifié")
            pos = float(rec.get("pos", 0))
            dur = float(rec.get("dur", 0))
        except Exception:
            self._resume_forget(path)
            return None
        if pos < RESUME_MIN_POS or (dur > 0 and pos > dur - RESUME_END_MARGIN):
            self._resume_forget(path)
            return None
        return pos

    def _resume_save_current(self, force=False):
        if getattr(self, "_resume_reset", False): return
        if not getattr(self, "resume_prompt_enabled", True): return
        path = getattr(self, "current_path", None)
        if not self._resume_eligible(path): return
        if not force and not self.is_playing: return
        pos = float(getattr(self, "_last_known_position", 0.0) or 0.0)
        dur = float(getattr(self, "current_duration", 0.0) or 0.0)
        if dur <= 0:
            try:
                adj = self.position_scale.get_adjustment()
                dur = float(adj.get_upper() or 0.0)
            except Exception: pass
        if dur > 0 and dur < RESUME_MIN_DURATION: return
        if dur > 0 and pos > dur - RESUME_END_MARGIN:
            self._resume_forget(path)
            return
        if pos < RESUME_MIN_POS: return
        try:
            st = os.stat(path)
            path = os.path.abspath(path)
        except OSError:
            return
        key = self._resume_key(path)
        self._resume_data.pop(key, None)
        self._resume_data[key] = {
            "pos": pos,
            "dur": dur,
            "m": int(st.st_mtime),
            "s": int(st.st_size),
            "t": time.time(),
        }
        self._save_resume_data()

    def _resume_tick(self):
        now = time.monotonic()
        if now - getattr(self, "_resume_last_tick", 0.0) < RESUME_SAVE_INTERVAL: return
        self._resume_last_tick = now
        if self.is_playing and not self.is_paused:
            self._resume_save_current()

    def _resume_ask(self, path):
        if not getattr(self, "resume_prompt_enabled", True): return 0.0
        pos = self._resume_lookup(path)
        if pos is None: return 0.0
        self._resume_prompt_open = True
        resp = Gtk.ResponseType.NONE
        try:
            try:
                if self.is_playing and not self.is_paused:
                    self._active_pipeline().set_state(Gst.State.PAUSED)
            except Exception: pass
            dialog = Gtk.Dialog(
                title=self.tr("Reprendre la lecture ?"),
                parent=self,
                flags=Gtk.DialogFlags.MODAL,
            )
            dialog.add_button(self.tr("Recommencer"), Gtk.ResponseType.REJECT)
            dialog.add_button(self.tr("Reprendre"), Gtk.ResponseType.ACCEPT)
            dialog.set_default_response(Gtk.ResponseType.ACCEPT)
            try: self._style_app_dialog(dialog)
            except Exception: pass
            area = dialog.get_content_area()
            area.set_border_width(12)
            name = Gtk.Label(label=os.path.basename(path))
            name.set_xalign(0)
            name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            area.pack_start(name, False, False, 4)
            ask = Gtk.Label(
                label=self.tr("Reprendre à %s ?") % format_time(pos)
            )
            ask.set_xalign(0)
            area.pack_start(ask, False, False, 4)
            dialog.show_all()
            resp = dialog.run()
            dialog.destroy()
        finally:
            self._resume_prompt_open = False
        if resp == Gtk.ResponseType.ACCEPT: return max(0.0, pos - RESUME_REWIND)
        if resp == Gtk.ResponseType.REJECT:
            self._resume_forget(path)
        return 0.0

    def _resume_apply_later(self, path, pos):
        gen = self._playback_generation
        tries = {"n": 0}
        def _try():
            if gen != self._playback_generation or self.current_path != path: return False
            tries["n"] += 1
            try:
                _r, state, _p = self._active_pipeline().get_state(0)
                ready = state in (Gst.State.PAUSED, Gst.State.PLAYING)
            except Exception:
                ready = False
            if ready:
                try: self._try_update_duration()
                except Exception: pass
                try:
                    self.seek_to(pos)
                    return False
                except Exception: pass
            return tries["n"] < 40
        GLib.timeout_add(150, _try)

    def toggle_resume_prompt(self, menuitem):
        self.resume_prompt_enabled = bool(menuitem.get_active())
        try: self.save_state()
        except Exception: pass
        try:
            self.status_label.set_text(
                self.tr("Reprise de lecture activée")
                if self.resume_prompt_enabled
                else self.tr("Reprise de lecture désactivée")
            )
        except Exception: pass

    def clear_resume_data(self, *args):
        self._resume_data = {}
        self._save_resume_data()
        try: self.status_label.set_text(self.tr("Positions mémorisées effacées"))
        except Exception: pass
    def _play_recent_file(self, path):
        if not path: return
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
                except Exception: pass
                return
        try:
            paths = self.get_paths() if hasattr(self, "get_paths") else []
            if path not in paths:
                self.add_file(path, save=True)
        except Exception: pass
        self.play_file(path)

    def show_menu(self, button):
        old = getattr(self, "_main_popup_menu", None)
        if old is not None:
            try: old.popdown()
            except Exception: pass
            try: old.destroy()
            except Exception: pass
            self._main_popup_menu = None
        menu = Gtk.Menu()
        self._main_popup_menu = menu
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
        menu.append(Gtk.SeparatorMenuItem())
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
        discs_root = Gtk.MenuItem(label=self.tr("Disques"))
        discs_menu = Gtk.Menu()
        discs_root.set_submenu(discs_menu)
        cd_item = Gtk.MenuItem(label=self.tr("Lire le CD audio…"))
        cd_item.connect("activate", self.add_cd)
        discs_menu.append(cd_item)
        dvd_item = Gtk.MenuItem(label=self.tr("Lire le DVD…"))
        dvd_item.connect("activate", self.add_dvd)
        discs_menu.append(dvd_item)
        bd_item = Gtk.MenuItem(label=self.tr("Lire le Blu-Ray…"))
        bd_item.connect("activate", self.add_bluray)
        discs_menu.append(bd_item)
        menu.append(discs_root)
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
        nano_menu = Gtk.Menu()
        nano_root = Gtk.MenuItem(label=self.tr("Nano-lecteur"))
        nano_root.set_submenu(nano_menu)
        nano_item = Gtk.CheckMenuItem(label=self.tr("Activer le nano-lecteur"))
        nano_item.set_active(bool(getattr(self, "nano_player_mode", False)))
        def _on_nano_toggled(mi):
            want = mi.get_active()
            if want != bool(getattr(self, "nano_player_mode", False)):
                self.toggle_nano_player()
        nano_item.connect("toggled", _on_nano_toggled)
        nano_menu.append(nano_item)
        nano_playlist_item = Gtk.CheckMenuItem(label=self.tr("Playlist"))
        nano_playlist_item.set_active(
            bool(getattr(self, "nano_playlist_enabled", False))
        )
        def _on_nano_playlist_toggled(mi):
            want = mi.get_active()
            if want != bool(getattr(self, "nano_playlist_enabled", False)):
                self.toggle_nano_playlist()
        nano_playlist_item.connect("toggled", _on_nano_playlist_toggled)
        nano_menu.append(nano_playlist_item)
        nano_vertical_menu = Gtk.Menu()
        nano_vertical_root = Gtk.MenuItem(label=self.tr("Vertical"))
        nano_vertical_root.set_submenu(nano_vertical_menu)
        nano_vertical_item = Gtk.CheckMenuItem(label=self.tr("Activer le mode vertical"))
        nano_vertical_item.set_active(bool(getattr(self, "nano_vertical", False)))
        def _on_nano_vertical_toggled(mi):
            want = mi.get_active()
            if want != bool(getattr(self, "nano_vertical", False)):
                self.toggle_nano_vertical()
        nano_vertical_item.connect("toggled", _on_nano_vertical_toggled)
        nano_vertical_menu.append(nano_vertical_item)
        nano_vertical_menu.append(Gtk.SeparatorMenuItem())
        side = getattr(self, "nano_playlist_side", "right") or "right"
        radio_right = Gtk.RadioMenuItem.new_with_label(
            None, self.tr("Playlist droite")
        )
        radio_left = Gtk.RadioMenuItem.new_with_label_from_widget(
            radio_right, self.tr("Playlist gauche")
        )
        radio_right.set_active(side != "left")
        radio_left.set_active(side == "left")
        def _on_side_right(mi):
            if mi.get_active():
                self.set_nano_playlist_side("right")
        def _on_side_left(mi):
            if mi.get_active():
                self.set_nano_playlist_side("left")
        radio_right.connect("toggled", _on_side_right)
        radio_left.connect("toggled", _on_side_left)
        nano_vertical_menu.append(radio_right)
        nano_vertical_menu.append(radio_left)
        nano_menu.append(nano_vertical_root)
        menu.append(nano_root)
        menu.append(Gtk.SeparatorMenuItem())
        # --- Options audio (Égaliseur / Pitch en tête) ---
        audio_opts_root = Gtk.MenuItem(label=self.tr("Options audio"))
        try:
            audio_opts_root.set_submenu(self._build_audio_options_submenu())
        except Exception as e:
            print("audio options menu:", e, file=sys.stderr)
        menu.append(audio_opts_root)
        try:
            menu.append(self._build_spectrum_menu_item())
        except Exception as e:
            print("spectrum menu:", e, file=sys.stderr)
        try:
            for _mi in self._build_media_track_menu_items():
                menu.append(_mi)
        except Exception as e:
            print("tracks menu:", e, file=sys.stderr)
        lyrics_item = Gtk.CheckMenuItem(label=self.tr("Paroles"))
        lyrics_item.set_active(bool(getattr(self, "lyrics_enabled", False)))
        lyrics_item.connect("toggled", self.toggle_lyrics)
        menu.append(lyrics_item)
        try:
            menu.append(self._build_colorimetry_menu_item())
        except Exception as e:
            print("colorimetry menu:", e, file=sys.stderr)
        menu.append(Gtk.SeparatorMenuItem())
        # ---- Apparence (partagé avec le menu contextuel playlist) ----
        try:
            appearance_item = self._build_appearance_menu_item()
            if appearance_item is not None:
                menu.append(appearance_item)
        except Exception as e:
            print("appearance menu:", e, file=sys.stderr)
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
                except Exception: pass
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
        resume_menu = Gtk.Menu()
        resume_root = Gtk.MenuItem(label=self.tr("Reprise de lecture"))
        resume_root.set_submenu(resume_menu)
        resume_check = Gtk.CheckMenuItem(label=self.tr("Activer"))
        resume_check.set_active(
            bool(getattr(self, "resume_prompt_enabled", True))
        )
        resume_check.connect("toggled", self.toggle_resume_prompt)
        resume_menu.append(resume_check)
        resume_clear = Gtk.MenuItem(
            label=self.tr("Effacer les positions mémorisées")
        )
        resume_clear.set_sensitive(bool(getattr(self, "_resume_data", None)))
        resume_clear.connect("activate", self.clear_resume_data)
        resume_menu.append(resume_clear)
        menu.append(resume_root)
        menu.append(Gtk.SeparatorMenuItem())
        about_item = Gtk.MenuItem()
        about_label = Gtk.Label()
        about_label.set_markup("<i>" + GLib.markup_escape_text(self.tr("À propos")) + "</i>")
        about_label.set_xalign(0)
        about_item.add(about_label)
        about_item.connect("activate", self.show_about)
        menu.append(about_item)
        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label=self.tr("Quitter"))
        quit_item.connect("activate", lambda item: self.close())
        menu.append(quit_item)
        def _on_deact(m):
            try: self._on_menu_deactivate(m)
            except Exception: pass
            if getattr(self, "_main_popup_menu", None) is m:
                self._main_popup_menu = None
            try:
                GLib.idle_add(m.destroy)
            except Exception:
                try: m.destroy()
                except Exception: pass
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
        if not self.current_is_video: return
        self._nudge_video_redraw()
        try:
            if self.video_area is not None and self.is_paused:
                self.video_area.queue_draw()
        except Exception: pass

    def _language_menu_toggled(self, menuitem, language):
        if menuitem.get_active() and language != self.language:
            self.set_language(language)

    def _performance_menu_toggled(self, menuitem, mode):
        if menuitem.get_active():
            self.set_performance_mode(mode)

    def _menu_item(self, label, cb, *cb_args, check=False, active=False):
        if check:
            item = Gtk.CheckMenuItem(label=self.tr(label) if isinstance(label, str) else label)
            item.set_active(bool(active))
        else:
            item = Gtk.MenuItem(label=self.tr(label) if isinstance(label, str) else label)
        if cb is not None:
            if cb_args:
                item.connect("activate" if not check else "toggled", cb, *cb_args)
            else:
                item.connect("activate" if not check else "toggled", cb)
        return item

    def _build_spectrum_menu_item(self):
        root = Gtk.MenuItem(label=self.tr("Spectre"))
        sub = Gtk.Menu()
        root.set_submenu(sub)
        it = Gtk.CheckMenuItem(label=self.tr("Spectre"))
        it.set_active(bool(getattr(self, "spectrum_enabled", True)))
        it.connect("toggled", self.toggle_spectrum)
        sub.append(it)
        c = Gtk.MenuItem(label=self.tr("Couleurs"))
        c.connect("activate", self.show_spectrum_colors)
        sub.append(c)
        s = Gtk.MenuItem(label=self.tr("Synchronisation…"))
        s.connect("activate", self.show_spectrum_sync)
        sub.append(s)
        sub.append(Gtk.SeparatorMenuItem())
        try:
            style_allowed = self._spectrum_style_allowed()
        except Exception:
            style_allowed = True
        cur = str(getattr(self, "spectrum_style", "pixel") or "pixel").lower()
        group = None
        for sid, slabel in (("bars", "Barres"), ("oscilloscope", "Oscilloscope"), ("pixel", "Pixel")):
            item = Gtk.RadioMenuItem.new_with_label_from_widget(group, self.tr(slabel))
            item.set_active(sid == cur)
            if group is None:
                group = item
            if sid != "bars" and not style_allowed:
                item.set_sensitive(False)
            item.connect("toggled", lambda m, name=sid: self.set_spectrum_style(name) if m.get_active() else None)
            sub.append(item)
        return root

    def _build_media_track_menu_items(self):
        items = []
        # Audio
        audio_menu = Gtk.Menu()
        audio_root = Gtk.MenuItem(label=self.tr("Pistes audio"))
        audio_root.set_submenu(audio_menu)
        if self.audio_tracks:
            group = None
            for idx, label in self.audio_tracks:
                item = Gtk.RadioMenuItem.new_with_label_from_widget(group, label)
                item.set_active(idx == self.current_audio_index)
                item.connect("toggled", lambda m, i=idx: self.set_audio_track(i) if m.get_active() else None)
                audio_menu.append(item)
                if group is None:
                    group = item
        else:
            empty = Gtk.MenuItem(label=self.tr("Aucune piste disponible"))
            empty.set_sensitive(False)
            audio_menu.append(empty)
        items.append(audio_root)
        # Chapters
        ch_menu = Gtk.Menu()
        ch_root = Gtk.MenuItem(label=self.tr("Chapitres"))
        ch_root.set_submenu(ch_menu)
        if self.chapters:
            for i, (start, title) in enumerate(self.chapters):
                lab = f"{title}  ({format_time(start)})"
                item = Gtk.MenuItem(label=lab)
                item.connect("activate", lambda w, idx=i: self.seek_to_chapter(idx))
                ch_menu.append(item)
        else:
            empty = Gtk.MenuItem(label=self.tr("Aucun chapitre"))
            empty.set_sensitive(False)
            ch_menu.append(empty)
        items.append(ch_root)
        # Subtitles
        sub_menu = Gtk.Menu()
        sub_root = Gtk.MenuItem(label=self.tr("Sous-titres"))
        sub_root.set_submenu(sub_menu)
        sub_on = Gtk.CheckMenuItem(label=self.tr("Activer les sous-titres"))
        sub_on.set_active(bool(getattr(self, "subtitles_enabled", False)))
        sub_on.connect("toggled", self.toggle_subtitles)
        sub_menu.append(sub_on)
        if self.subtitle_tracks:
            sub_menu.append(Gtk.SeparatorMenuItem())
            none_item = Gtk.RadioMenuItem.new_with_label_from_widget(None, self.tr("Aucun"))
            none_item.set_active(self.current_subtitle_index < 0)
            none_item.connect("toggled", lambda m: self.set_subtitle_track(-1) if m.get_active() else None)
            sub_menu.append(none_item)
            group = none_item
            for list_idx, track in enumerate(self.subtitle_tracks):
                label = track.get("label") if isinstance(track, dict) else str(track)
                item = Gtk.RadioMenuItem.new_with_label_from_widget(group, label)
                item.set_active(list_idx == self.current_subtitle_index)
                item.connect("toggled", lambda m, i=list_idx: self.set_subtitle_track(i) if m.get_active() else None)
                sub_menu.append(item)
        else:
            empty = Gtk.MenuItem(label=self.tr("Aucune piste disponible"))
            empty.set_sensitive(False)
            sub_menu.append(empty)
        items.append(sub_root)
        return items

    def _build_colorimetry_menu_item(self):
        color_root = Gtk.MenuItem(label=self.tr("Colorimétrie"))
        color_sub = Gtk.Menu()
        color_check = Gtk.CheckMenuItem(label=self.tr("Activer"))
        color_check.set_active(bool(getattr(self, "colorimetry_enabled", False)))
        try:
            color_check.set_sensitive(self._colorimetry_allowed())
        except Exception:
            color_check.set_sensitive(False)
        color_check.connect("toggled", self.toggle_colorimetry_enabled)
        color_sub.append(color_check)
        color_adj = Gtk.MenuItem(label=self.tr("Ajuster la colorimétrie…"))
        try:
            color_adj.set_sensitive(
                self._colorimetry_allowed() and bool(getattr(self, "colorimetry_enabled", False))
            )
        except Exception:
            color_adj.set_sensitive(False)
        try:
            color_adj.set_tooltip_text(self.tr(
                "En mode Logiciel (surtout en 4K), la colorimétrie est "
                "coûteuse : décodage CPU + filtres vidéo, et le pipeline "
                "peut être reconstruit. Préférez le mode Matériel."
            ))
        except Exception: pass
        color_adj.connect("activate", self.show_colorimetry)
        color_sub.append(color_adj)
        color_root.set_submenu(color_sub)
        self.colorimetry_menu_item = color_root
        self.colorimetry_check_item = color_check
        self.colorimetry_adjust_item = color_adj
        return color_root

    def _build_audio_options_submenu(self):
        audio_opts = Gtk.Menu()
        eq_item = Gtk.MenuItem(label=self.tr("Égaliseur…"))
        eq_item.connect("activate", self.show_equalizer)
        audio_opts.append(eq_item)
        pitch_item = Gtk.MenuItem(label=self.tr("Pitch / Tempo…"))
        pitch_item.connect("activate", self.show_pitch_tempo)
        self.pitch_menu_item = pitch_item
        try: self._update_pitch_menu_sensitivity()
        except Exception: pass
        audio_opts.append(pitch_item)
        speed_item = Gtk.MenuItem(label=self.tr("Vitesse…"))
        speed_item.connect("activate", self.show_speed)
        self.speed_menu_item = speed_item
        try: speed_item.set_sensitive(self._is_speed_media())
        except Exception: pass
        audio_opts.append(speed_item)
        audio_opts.append(Gtk.SeparatorMenuItem())
        rg_root = Gtk.MenuItem(label=self.tr("Normalisation"))
        rg_sub = Gtk.Menu()
        rg_root.set_submenu(rg_sub)
        rg_group = None
        cur_rg = str(getattr(self, "replaygain_mode", "off") or "off")
        for mid, mlab in (
            ("off", "Désactivé"),
            ("track", "Piste"),
            ("album", "Album"),
            ("auto", "Auto"),
        ):
            item = Gtk.RadioMenuItem.new_with_label_from_widget(
                rg_group, self.tr(mlab)
            )
            item.set_active(mid == cur_rg)
            if rg_group is None:
                rg_group = item
            item.connect(
                "toggled",
                lambda m, mode=mid: (
                    self.set_replaygain_mode(mode) if m.get_active() else None
                ),
            )
            rg_sub.append(item)
        audio_opts.append(rg_root)
        cf_item = Gtk.MenuItem(label=self.tr("Crossfade / Gapless…"))
        cf_item.connect("activate", self.show_crossfade_dialog)
        audio_opts.append(cf_item)
        sleep_item = Gtk.MenuItem(label=self.tr("Minuteur d'arrêt…"))
        sleep_item.connect("activate", self.show_sleep_timer_dialog)
        audio_opts.append(sleep_item)
        audio_dev_item = Gtk.MenuItem(label=self.tr("Sortie audio…"))
        audio_dev_item.connect("activate", self.show_audio_device_dialog)
        audio_opts.append(audio_dev_item)
        audio_opts.append(Gtk.SeparatorMenuItem())
        scrobble_item = Gtk.MenuItem(label=self.tr("Scrobbling…"))
        scrobble_item.connect("activate", self.show_scrobbling_dialog)
        audio_opts.append(scrobble_item)
        return audio_opts

    def _build_appearance_menu_item(self):
        appearance_menu = Gtk.Menu()
        appearance_item = Gtk.MenuItem(label=self.tr("Apparence"))
        appearance_item.set_submenu(appearance_menu)
        # ---- Langue (en tête) ----
        language_menu = Gtk.Menu()
        language_item = Gtk.MenuItem(
            label="Language" if self.language == "English" else self.tr("Langue")
        )
        language_item.set_submenu(language_menu)
        language_group = None
        for language in LANGUAGES:
            item = Gtk.RadioMenuItem.new_with_label_from_widget(
                language_group, language
            )
            item.set_active(language == self.language)
            if language_group is None:
                language_group = item
            item.connect("toggled", self._language_menu_toggled, language)
            language_menu.append(item)
        appearance_menu.append(language_item)
        # ---- Thèmes (en tête) ----
        themes_menu = Gtk.Menu()
        themes_item = Gtk.MenuItem(label=self.tr("Thèmes"))
        themes_item.set_submenu(themes_menu)
        theme_group = None
        theme_labels = (
            ("aqua", "Aqua"),
            ("sombre", "Sombre"),
            ("clair", "Clair"),
            ("sang", "Sang"),
            ("nature", "Nature"),
            ("kawai", "Kawai"),
            ("ciel", "Ciel"),
            ("bigtron", "BigTron"),
            ("lantern", "Lantern"),
            ("crusher", "Crusher"),
            ("gdalf", "Gdalf"),
            ("darth", "Darth"),
            ("fremen", "Fremen"),
            ("bigmegatron", "BigMegatron"),
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
        appearance_menu.append(themes_item)
        appearance_menu.append(Gtk.SeparatorMenuItem())
        borders_item = Gtk.CheckMenuItem(label=self.tr("Bordures"))
        borders_item.set_active(bool(getattr(self, "window_borders", True)))
        borders_item.connect("toggled", self.toggle_window_borders)
        appearance_menu.append(borders_item)
        layer_menu = Gtk.Menu()
        layer_root = Gtk.MenuItem(label=self.tr("Plan de la fenêtre"))
        layer_root.set_submenu(layer_menu)
        cur_layer = str(getattr(self, "window_layer", "normal") or "normal")
        if cur_layer == "above":
            cur_layer = "normal"
        radio_normal = Gtk.RadioMenuItem.new_with_label(
            None, self.tr("Plan normal")
        )
        radio_below = Gtk.RadioMenuItem.new_with_label_from_widget(
            radio_normal, self.tr("Toujours en arrière-plan")
        )
        radio_normal.set_active(cur_layer != "below")
        radio_below.set_active(cur_layer == "below")
        def _on_layer_normal(mi):
            if mi.get_active():
                if getattr(self, "playlist_only_maximized", False): return
                self.set_window_layer("normal")
        def _on_layer_below(mi):
            if mi.get_active():
                self.set_window_layer("below")
        radio_normal.connect("toggled", _on_layer_normal)
        radio_below.connect("toggled", _on_layer_below)
        if getattr(self, "playlist_only_maximized", False):
            radio_normal.set_sensitive(False)
            radio_below.set_active(True)
        layer_menu.append(radio_normal)
        layer_menu.append(radio_below)
        appearance_menu.append(layer_root)
        playlist_only_root = Gtk.MenuItem(label=self.tr("Sans lecteur"))
        playlist_only_sub = Gtk.Menu()
        playlist_only_check = Gtk.CheckMenuItem(label=self.tr("Activer"))
        playlist_only_check.set_active(
            bool(getattr(self, "playlist_only_mode", False))
        )
        playlist_only_check.connect(
            "toggled", self._on_playlist_only_menu_toggled
        )
        playlist_only_sub.append(playlist_only_check)
        playlist_only_max = Gtk.CheckMenuItem(label=self.tr("Maximiser"))
        playlist_only_max.set_active(
            bool(getattr(self, "playlist_only_maximized", False))
        )
        playlist_only_max.set_sensitive(
            bool(getattr(self, "playlist_only_mode", False))
        )
        playlist_only_max.connect(
            "toggled", self._on_playlist_only_maximize_toggled
        )
        playlist_only_sub.append(playlist_only_max)
        playlist_only_root.set_submenu(playlist_only_sub)
        appearance_menu.append(playlist_only_root)
        transparency_root = Gtk.MenuItem(label=self.tr("Transparence"))
        transparency_sub = Gtk.Menu()
        transparency_check = Gtk.CheckMenuItem(label=self.tr("Activer"))
        transparency_check.set_active(
            bool(getattr(self, "playlist_transparency", False))
        )
        transparency_check.connect("toggled", self._on_transparency_menu_toggled)
        transparency_sub.append(transparency_check)
        transparency_settings = Gtk.MenuItem(label=self.tr("Réglages…"))
        transparency_settings.connect("activate", self.show_transparency_settings)
        transparency_sub.append(transparency_settings)
        transparency_root.set_submenu(transparency_sub)
        appearance_menu.append(transparency_root)
        metadata_item = Gtk.CheckMenuItem(label=self.tr("Métadonnées"))
        metadata_item.set_active(bool(getattr(self, "show_metadata_panel", True)))
        metadata_item.connect("toggled", self._on_metadata_panel_menu_toggled)
        appearance_menu.append(metadata_item)
        notifications_item = Gtk.CheckMenuItem(label=self.tr("Notifications"))
        notifications_item.set_active(
            bool(getattr(self, "desktop_notifications", True))
        )
        notifications_item.connect("toggled", self.toggle_desktop_notifications)
        appearance_menu.append(notifications_item)
        reset_appearance = Gtk.MenuItem(label=self.tr("Réinitialisation"))
        reset_appearance.connect("activate", self.reset_appearance_defaults)
        appearance_menu.append(reset_appearance)
        return appearance_item

    def reset_appearance_defaults(self, *args):
        try:
            if getattr(self, "playlist_only_maximized", False):
                self.set_playlist_only_maximized(False)
        except Exception: pass
        try:
            self.mini_player_mode = False
            self.nano_player_mode = False
            self.playlist_only_mode = False
            self.playlist_only_maximized = False
        except Exception: pass
        self.window_borders = True
        self.window_layer = "normal"
        self.playlist_transparency = False
        self.playlist_transparency_opacity = 0.35
        self.show_metadata_panel = True
        self.desktop_notifications = True
        self.mini_banner_enabled = True
        self.nano_banner_enabled = False
        try: self.set_decorated(True)
        except Exception: pass
        try: self.set_window_layer("normal")
        except Exception: pass
        try: self._apply_playlist_transparency()
        except Exception: pass
        try: self._apply_metadata_panel_visibility()
        except Exception: pass
        try: self._update_undecorated_move_hint()
        except Exception: pass
        try: self._apply_compact_player_mode()
        except Exception: pass
        try: self._apply_playlist_only_mode()
        except Exception: pass
        try:
            if hasattr(self, "set_playlist_only_maximized"):
                self.set_playlist_only_maximized(False)
        except Exception: pass
        try: self.save_state()
        except Exception: pass
        try: self.status_label.set_text(self.tr("Réinitialisation…"))
        except Exception: pass
        def _msg():
            try: self.status_label.set_text(self.tr("Apparence réinitialisée"))
            except Exception: pass
            return False
        try: GLib.timeout_add(600, _msg)
        except Exception: pass
    def _on_playlist_only_menu_toggled(self, menuitem):
        try:
            enabled = bool(menuitem.get_active())
        except Exception:
            return
        if enabled == bool(getattr(self, "playlist_only_mode", False)): return
        self.set_playlist_only_mode(enabled)

    def _on_playlist_only_maximize_toggled(self, menuitem):
        try:
            enabled = bool(menuitem.get_active())
        except Exception:
            return
        if not bool(getattr(self, "playlist_only_mode", False)): return
        if enabled == bool(getattr(self, "playlist_only_maximized", False)): return
        self.set_playlist_only_maximized(enabled)

    def set_playlist_only_mode(self, enabled):
        enabled = bool(enabled)
        self.playlist_only_mode = enabled
        if not enabled:
            if getattr(self, "playlist_only_maximized", False):
                self.set_playlist_only_maximized(False)
            if not bool(getattr(self, "is_fullscreen", False)):
                try: self._hide_fs_controls()
                except Exception: pass
        try:
            self._apply_playlist_only_mode()
        except Exception as e:
            print("playlist_only:", e, file=sys.stderr)
        try: self.save_state()
        except Exception: pass
        try:
            self.status_label.set_text(
                self.tr("Sans lecteur activé")
                if enabled
                else self.tr("Sans lecteur désactivé")
            )
        except Exception: pass

    def set_playlist_only_maximized(self, enabled):
        enabled = bool(enabled)
        if enabled and not bool(getattr(self, "playlist_only_mode", False)): return
        prev = bool(getattr(self, "playlist_only_maximized", False))
        self.playlist_only_maximized = enabled
        try:
            if enabled and not prev:
                try:
                    self._playlist_only_prev_pos = self.get_position()
                except Exception:
                    self._playlist_only_prev_pos = None
                try:
                    self._playlist_only_prev_size = self.get_size()
                except Exception:
                    self._playlist_only_prev_size = None
                self._playlist_only_prev_layer = str(
                    getattr(self, "window_layer", "normal") or "normal"
                )
                self.set_window_layer("below")
                try:
                    screen = self.get_screen()
                    mon = screen.get_monitor_at_window(self.get_window()) if self.get_window() else 0
                    geo = screen.get_monitor_geometry(mon)
                    self.resize(geo.width, geo.height)
                    self.move(geo.x, geo.y)
                except Exception:
                    try: self.maximize()
                    except Exception: pass
            elif not enabled and prev:
                prev_layer = getattr(self, "_playlist_only_prev_layer", None) or "normal"
                if prev_layer == "above":
                    prev_layer = "normal"
                self.set_window_layer(prev_layer)
                try: self.unmaximize()
                except Exception: pass
                prev_size = getattr(self, "_playlist_only_prev_size", None)
                if prev_size and len(prev_size) == 2:
                    try: self.resize(int(prev_size[0]), int(prev_size[1]))
                    except Exception: pass
                prev_pos = getattr(self, "_playlist_only_prev_pos", None)
                if prev_pos and len(prev_pos) == 2:
                    try: self.move(int(prev_pos[0]), int(prev_pos[1]))
                    except Exception: pass
        except Exception as e:
            print("playlist_only maximize:", e, file=sys.stderr)
        try: self.save_state()
        except Exception: pass
    def _apply_playlist_only_mode(self):
        enabled = bool(getattr(self, "playlist_only_mode", False))
        compact = bool(getattr(self, "mini_player_mode", False)) or bool(
            getattr(self, "nano_player_mode", False)
        )
        if compact: return
        for attr in ("top_bar_wrap", "top_bar"):
            w = getattr(self, attr, None)
            if w is None: continue
            try:
                if enabled:
                    w.hide()
                else:
                    w.show()
            except Exception: pass
        is_vid = bool(getattr(self, "current_is_video", False))
        lyrics_on = bool(getattr(self, "lyrics_mode", False)) and bool(
            getattr(self, "lyrics_enabled", False)
        )
        show_media_area = is_vid or lyrics_on
        for attr in ("video_overlay", "video_area"):
            w = getattr(self, attr, None)
            if w is None: continue
            try:
                if enabled and not show_media_area:
                    w.hide()
                elif show_media_area:
                    w.show()
                    if attr == "video_overlay":
                        try: w.show_all()
                        except Exception: pass
                    if lyrics_on and attr == "video_area":
                        try: w.set_size_request(-1, 120)
                        except Exception: pass
                else:
                    w.hide()
            except Exception: pass
        if enabled and is_vid:
            try:
                self._cache_video_window_handle()
                self._apply_video_overlay_handle()
            except Exception: pass
        paned = getattr(self, "content_paned", None)
        if paned is not None:
            try:
                paned.set_no_show_all(False)
                paned.show()
                if enabled and not show_media_area:
                    paned.set_position(0)
                elif show_media_area:
                    try:
                        alloc = paned.get_allocation()
                        h = int(getattr(alloc, "height", 0) or 0)
                        if h > 80:
                            if enabled:
                                if lyrics_on and not is_vid:
                                    paned.set_position(max(80, h // 2))
                                else:
                                    paned.set_position(max(80, (h * 2) // 3))
                            else:
                                paned.set_position(max(0, h // 3))
                        else:
                            self._apply_video_playlist_split()
                    except Exception:
                        try: self._apply_video_playlist_split()
                        except Exception: pass
                else:
                    try:
                        alloc = paned.get_allocation()
                        h = int(getattr(alloc, "height", 0) or 0)
                        if h > 80:
                            paned.set_position(max(0, h // 3))
                    except Exception: pass
            except Exception: pass
        for attr in ("tree",):
            tree = getattr(self, attr, None)
            if tree is None: continue
            try: tree.set_headers_visible(not enabled)
            except Exception: pass
        for attr in ("search_row", "search_entry", "visualizer"):
            w = getattr(self, attr, None)
            if w is None: continue
            try: w.show()
            except Exception: pass
        if enabled:
            for attr in ("bottom_bar_wrap", "bottom_bar"):
                w = getattr(self, attr, None)
                if w is not None:
                    try: w.hide()
                    except Exception: pass
        else:
            try: self._apply_metadata_panel_visibility()
            except Exception: pass
        try: self.queue_resize()
        except Exception: pass
    def _build_playlist_context_menu(self, filename=None, multi=False):
        old = getattr(self, "_playlist_context_menu", None)
        if old is not None:
            try: old.popdown()
            except Exception: pass
            try: old.destroy()
            except Exception: pass
            self._playlist_context_menu = None
        menu = Gtk.Menu()
        self._playlist_context_menu = menu
        add_files = Gtk.MenuItem(label=self.tr("Ajouter des fichiers"))
        add_files.connect("activate", lambda item: self._add_files_dialog())
        menu.append(add_files)
        add_folder = Gtk.MenuItem(label=self.tr("Ajouter un dossier"))
        add_folder.connect("activate", lambda item: self._add_folder_dialog())
        menu.append(add_folder)
        menu.append(Gtk.SeparatorMenuItem())
        if filename:
            play = Gtk.MenuItem(label=self.tr("Lire"))
            play.connect(
                "activate", lambda item, p=filename: self.play_file(p)
            )
            menu.append(play)
            if not multi:
                metadata = Gtk.MenuItem(label=self.tr("Métadonnées…"))
                metadata.connect(
                    "activate",
                    lambda item, p=filename: self.show_metadata(p),
                )
                menu.append(metadata)
            menu.append(Gtk.SeparatorMenuItem())
        radios_root = Gtk.MenuItem(label=self.tr("Radios"))
        radios_sub = Gtk.Menu()
        radios_root.set_submenu(radios_sub)
        radios = getattr(self, "radios", None) or []
        if radios:
            for radio in radios:
                try:
                    name = radio.get("name") or radio.get("url") or "?"
                    url = radio.get("url") or ""
                    item = Gtk.MenuItem(label=name)
                    if url:
                        item.connect(
                            "activate",
                            lambda w, u=url: self.play_radio(u),
                        )
                    radios_sub.append(item)
                except Exception: pass
            radios_sub.append(Gtk.SeparatorMenuItem())
        manage_r = Gtk.MenuItem(label=self.tr("Gérer les radios…"))
        manage_r.connect("activate", self.manage_radios)
        radios_sub.append(manage_r)
        menu.append(radios_root)
        tvs_root = Gtk.MenuItem(label=self.tr("TV"))
        tvs_sub = Gtk.Menu()
        tvs_root.set_submenu(tvs_sub)
        tvs = getattr(self, "tvs", None) or []
        if tvs:
            for tv in tvs:
                try:
                    name = tv.get("name") or tv.get("url") or "?"
                    url = tv.get("url") or ""
                    item = Gtk.MenuItem(label=name)
                    if url:
                        item.connect(
                            "activate",
                            lambda w, u=url, n=name: self.play_tv(u, n),
                        )
                    tvs_sub.append(item)
                except Exception: pass
            tvs_sub.append(Gtk.SeparatorMenuItem())
        manage_tv = Gtk.MenuItem(label=self.tr("Gérer la TV…"))
        manage_tv.connect("activate", self.manage_tvs)
        tvs_sub.append(manage_tv)
        menu.append(tvs_root)
        bouquets_root = Gtk.MenuItem(label=self.tr("Bouquet vidéos"))
        bouquets_sub = Gtk.Menu()
        bouquets_root.set_submenu(bouquets_sub)
        bouquets = getattr(self, "bouquets", None) or []
        if bouquets:
            for bouquet in bouquets:
                try:
                    name = bouquet.get("name") or bouquet.get("url") or "?"
                    url = bouquet.get("url") or ""
                    item = Gtk.MenuItem(label=name)
                    if url:
                        item.connect(
                            "activate",
                            lambda w, u=url: self.load_bouquet(u),
                        )
                    bouquets_sub.append(item)
                except Exception: pass
            bouquets_sub.append(Gtk.SeparatorMenuItem())
        manage_bq = Gtk.MenuItem(label=self.tr("Gérer les bouquets…"))
        manage_bq.connect("activate", self.manage_bouquets)
        bouquets_sub.append(manage_bq)
        menu.append(bouquets_root)
        menu.append(Gtk.SeparatorMenuItem())
        # --- Options audio (Égaliseur / Pitch en tête) ---
        audio_opts_root = Gtk.MenuItem(label=self.tr("Options audio"))
        try:
            audio_opts_root.set_submenu(self._build_audio_options_submenu())
        except Exception as e:
            print("audio options menu:", e, file=sys.stderr)
        menu.append(audio_opts_root)
        try:
            menu.append(self._build_spectrum_menu_item())
        except Exception as e:
            print("spectrum menu:", e, file=sys.stderr)
        menu.append(Gtk.SeparatorMenuItem())
        try:
            for _mi in self._build_media_track_menu_items():
                menu.append(_mi)
        except Exception as e:
            print("tracks menu:", e, file=sys.stderr)
        menu.append(Gtk.SeparatorMenuItem())
        try:
            appearance_item = self._build_appearance_menu_item()
            if appearance_item is not None:
                menu.append(appearance_item)
        except Exception as e:
            print("playlist ctx appearance:", e, file=sys.stderr)
        menu.append(Gtk.SeparatorMenuItem())
        if filename:
            if not multi:
                play_next_item = Gtk.MenuItem(label=self.tr("Lire ensuite"))
                play_next_item.connect(
                    "activate",
                    lambda item, p=filename: self.play_next_add(p),
                )
                menu.append(play_next_item)
                menu.append(Gtk.SeparatorMenuItem())
            if multi:
                remove = Gtk.MenuItem(label=self.tr("Supprimer la sélection"))
                remove.connect("activate", lambda item: self.remove_selected())
                menu.append(remove)
            else:
                remove = Gtk.MenuItem(label=self.tr("Supprimer de la playlist"))
                remove.connect(
                    "activate",
                    lambda item, p=filename: self.remove_file(p),
                )
                menu.append(remove)
        clear = Gtk.MenuItem(label=self.tr("Vider la playlist"))
        clear.connect("activate", lambda item: self.clear_playlist())
        menu.append(clear)
        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label=self.tr("Quitter"))
        quit_item.connect("activate", lambda item: self.close())
        menu.append(quit_item)
        def _ctx_deact(m):
            if getattr(self, "_playlist_context_menu", None) is m:
                self._playlist_context_menu = None
            try:
                GLib.idle_add(m.destroy)
            except Exception:
                try: m.destroy()
                except Exception: pass
            return False
        menu.connect("deactivate", _ctx_deact)
        return menu

    def _popup_playlist_menu_from_shortcut(self):
        tree = None
        if getattr(self, "nano_player_mode", False) and getattr(
            self, "nano_playlist_enabled", False
        ):
            tree = getattr(self, "nano_playlist_tree", None)
        if tree is None:
            tree = getattr(self, "tree", None)
        filename = None
        multi = False
        try:
            if tree is not None:
                model = tree.get_model()
                selection = tree.get_selection()
                paths = []
                try:
                    _m, paths = selection.get_selected_rows()
                except Exception:
                    paths = []
                multi = len(paths) > 1
                if paths:
                    try:
                        it = model.get_iter(paths[0])
                        filename = model[it][self.COL_PATH]
                    except Exception:
                        filename = None
                elif model is not None:
                    try:
                        it = model.get_iter_first()
                        if it is not None:
                            filename = model[it][self.COL_PATH]
                            selection.unselect_all()
                            selection.select_iter(it)
                    except Exception: pass
        except Exception: pass
        menu = self._build_playlist_context_menu(
            filename=filename, multi=multi
        )
        if menu is None: return
        menu.show_all()
        try:
            menu.popup_at_pointer(None)
        except Exception:
            try:
                target = tree if tree is not None else self
                menu.popup_at_widget(
                    target,
                    Gdk.Gravity.CENTER,
                    Gdk.Gravity.NORTH_WEST,
                    None,
                )
            except Exception:
                try:
                    menu.popup(
                        None, None, None, None, 0, Gtk.get_current_event_time()
                    )
                except Exception as e:
                    print("playlist menu shortcut:", e, file=sys.stderr)
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    def show_scrobbling_dialog(self, *args):
        dialog = Gtk.Dialog(
            title=self.tr("Scrobbling"),
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
        )
        dialog.add_button(self.tr("Annuler"), Gtk.ResponseType.CANCEL)
        dialog.add_button(self.tr("Enregistrer"), Gtk.ResponseType.OK)
        dialog.set_default_size(self._ui_s(460), self._ui_s(320))
        dialog.set_border_width(0)
        box = dialog.get_content_area()
        box.set_spacing(8)
        try:
            box.set_margin_start(self._ui_s(14))
            box.set_margin_end(self._ui_s(14))
            box.set_margin_top(self._ui_s(10))
            box.set_margin_bottom(self._ui_s(8))
        except Exception:
            box.set_border_width(12)
        enable = Gtk.CheckButton(label=self.tr("Activer le scrobbling"))
        enable.set_active(bool(getattr(self, "scrobble_enabled", False)))
        box.pack_start(enable, False, False, 0)
        svc_lab = Gtk.Label(label=self.tr("Service :"), xalign=0)
        box.pack_start(svc_lab, False, False, 0)
        svc_store = Gtk.ListStore(str, str)
        svc_store.append(["listenbrainz", "ListenBrainz"])
        svc_store.append(["lastfm", "Last.fm"])
        svc_combo = Gtk.ComboBox.new_with_model(svc_store)
        cell = Gtk.CellRendererText()
        svc_combo.pack_start(cell, True)
        svc_combo.add_attribute(cell, "text", 1)
        cur = str(getattr(self, "scrobble_service", "listenbrainz") or "listenbrainz")
        svc_combo.set_active(0 if cur != "lastfm" else 1)
        box.pack_start(svc_combo, False, False, 0)
        user_lab = Gtk.Label(label=self.tr("Nom d'utilisateur :"), xalign=0)
        box.pack_start(user_lab, False, False, 0)
        user_entry = Gtk.Entry()
        user_entry.set_text(str(getattr(self, "scrobble_username", "") or ""))
        box.pack_start(user_entry, False, False, 0)
        tok_lab = Gtk.Label(
            label=self.tr("Jeton / Session key :"),
            xalign=0,
        )
        box.pack_start(tok_lab, False, False, 0)
        tok_entry = Gtk.Entry()
        tok_entry.set_visibility(False)
        tok_entry.set_text(str(getattr(self, "scrobble_token", "") or ""))
        box.pack_start(tok_entry, False, False, 0)
        hint = Gtk.Label(
            label=self.tr(
                "ListenBrainz : créez un jeton sur listenbrainz.org (Settings → Music). "
                "Last.fm : utilisez une session key (API). "
                "Le scrobble est envoyé après ~50 % du titre ou 4 minutes."
            ),
            xalign=0,
        )
        hint.set_line_wrap(True)
        try: hint.get_style_context().add_class("status")
        except Exception: pass
        box.pack_start(hint, False, False, 0)
        try: self._style_app_dialog(dialog)
        except Exception: pass
        try: self._translate_dialog_action_buttons(dialog)
        except Exception: pass
        dialog.show_all()
        resp = dialog.run()
        if resp == Gtk.ResponseType.OK:
            self.scrobble_enabled = enable.get_active()
            it = svc_combo.get_active_iter()
            if it is not None:
                self.scrobble_service = svc_store[it][0]
            self.scrobble_username = user_entry.get_text().strip()
            self.scrobble_token = tok_entry.get_text().strip()
            try: self.save_state()
            except Exception: pass
            try:
                self.status_label.set_text(
                    self.tr("Scrobbling activé")
                    if self.scrobble_enabled
                    else self.tr("Scrobbling désactivé")
                )
            except Exception: pass
        dialog.destroy()

    def _scrobble_payload(self, path):
        if not path or is_stream(path) or is_cdda(path): return None
        try:
            title, artist, album, year, duration, cover, tech = self.get_metadata(
                path, load_cover=False
            )
        except Exception:
            title = artist = album = ""
            duration = 0
        title = (title or "").strip() or os.path.splitext(os.path.basename(path))[0]
        artist = (artist or "").strip() or "Unknown Artist"
        album = (album or "").strip()
        return artist, title, album, float(duration or 0)

    def _scrobble_send_async(self, kind, artist, title, album, duration=0):
        if not getattr(self, "scrobble_enabled", False): return
        token = (getattr(self, "scrobble_token", "") or "").strip()
        if not token: return
        service = str(getattr(self, "scrobble_service", "listenbrainz") or "listenbrainz")
        def _worker():
            try:
                import urllib.request
                import urllib.error
                import urllib.parse
                import json as _json
                if service == "listenbrainz":
                    payload = {
                        "listen_type": "playing_now" if kind == "nowplaying" else "single",
                        "payload": [{
                            "track_metadata": {
                                "artist_name": artist,
                                "track_name": title,
                                "release_name": album or None,
                            }
                        }],
                    }
                    if kind == "scrobble":
                        payload["payload"][0]["listened_at"] = int(time.time())
                    if not album:
                        payload["payload"][0]["track_metadata"].pop("release_name", None)
                    data = _json.dumps(payload).encode("utf-8")
                    req = urllib.request.Request(
                        "https://api.listenbrainz.org/1/submit-listens",
                        data=data,
                        headers={
                            "Authorization": "Token " + token,
                            "Content-Type": "application/json",
                        },
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        resp.read()
                else:
                    api_key = (getattr(self, "scrobble_api_key", "") or "").strip()
                    api_secret = (getattr(self, "scrobble_api_secret", "") or "").strip()
                    if not api_key or not api_secret: return
                    import hashlib
                    method = (
                        "track.updateNowPlaying"
                        if kind == "nowplaying"
                        else "track.scrobble"
                    )
                    params = {
                        "method": method,
                        "artist": artist,
                        "track": title,
                        "api_key": api_key,
                        "sk": token,
                    }
                    if album:
                        params["album"] = album
                    if kind == "scrobble":
                        params["timestamp"] = str(int(time.time()))
                    sig_str = "".join(
                        k + params[k] for k in sorted(params.keys())
                    ) + api_secret
                    params["api_sig"] = hashlib.md5(sig_str.encode("utf-8")).hexdigest()
                    params["format"] = "json"
                    data = urllib.parse.urlencode(params).encode("utf-8")
                    req = urllib.request.Request(
                        "https://ws.audioscrobbler.com/2.0/",
                        data=data,
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        resp.read()
            except Exception as exc:
                print("scrobble:", exc, file=sys.stderr)
        try:
            threading.Thread(target=_worker, daemon=True).start()
        except Exception as e:
            print("scrobble thread:", e, file=sys.stderr)

    def _scrobble_on_track_start(self, path):
        self._scrobble_sent_for = None
        self._nowplaying_sent_for = None
        self._scrobble_started_at = time.monotonic()
        if not getattr(self, "scrobble_enabled", False): return
        info = self._scrobble_payload(path)
        if not info: return
        artist, title, album, duration = info
        self._nowplaying_sent_for = path
        self._scrobble_send_async("nowplaying", artist, title, album, duration)

    def _scrobble_maybe_submit(self, path, position, duration):
        if not getattr(self, "scrobble_enabled", False): return
        if path is None or path == getattr(self, "_scrobble_sent_for", None): return
        if is_stream(path) or is_cdda(path): return
        try:
            pos = float(position or 0)
            dur = float(duration or 0)
        except Exception:
            return
        elapsed = time.monotonic() - float(getattr(self, "_scrobble_started_at", 0) or 0)
        ok = False
        if dur >= 30 and pos >= dur * 0.5:
            ok = True
        if elapsed >= 240:
            ok = True
        if not ok: return
        info = self._scrobble_payload(path)
        if not info: return
        artist, title, album, _d = info
        self._scrobble_sent_for = path
        self._scrobble_send_async("scrobble", artist, title, album, dur)
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    def play_next_add(self, path):
        if not path or not isinstance(path, str): return
        q = getattr(self, "play_next_queue", None)
        if q is None:
            self.play_next_queue = []
            q = self.play_next_queue
        if q and q[0] == path: return
        q.insert(0, path)  # front = next to play (LIFO of "play next" clicks = last wins as immediate next)
        try:
            base = os.path.basename(path)
            self.status_label.set_text(
                self.tr("Lire ensuite") + " : " + base
            )
        except Exception: pass

    def play_next_pop(self):
        q = getattr(self, "play_next_queue", None) or []
        if not q: return None
        path = q.pop(0)
        self.play_next_queue = q
        return path

    def _next_with_play_next_queue(self, auto=True):
        path = self.play_next_pop()
        if path:
            try:
                found = False
                for row in self.playlist:
                    if row[self.COL_PATH] == path:
                        found = True
                        break
                if not found:
                    self.add_file(path, save=False)
            except Exception: pass
            self.play_file(path)
            return True
        return False
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    def set_replaygain_mode(self, mode):
        mode = str(mode or "off").lower()
        if mode not in ("off", "track", "album", "auto"):
            mode = "off"
        if mode == getattr(self, "replaygain_mode", "off"): return
        self.replaygain_mode = mode
        try:
            self.rebuild_audio_filter(force=True)
        except Exception as e:
            print("replaygain rebuild:", e, file=sys.stderr)
        try: self.save_state()
        except Exception: pass
        labels = {
            "off": self.tr("Normalisation désactivée"),
            "track": self.tr("Normalisation piste"),
            "album": self.tr("Normalisation album"),
            "auto": self.tr("Normalisation auto"),
        }
        try: self.status_label.set_text(labels.get(mode, mode))
        except Exception: pass
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    def set_crossfade_seconds(self, seconds):
        try:
            seconds = float(seconds)
        except Exception:
            seconds = 0.0
        seconds = max(0.0, min(CROSSFADE_MAX_S, seconds))
        self.crossfade_seconds = seconds
        try: self.save_state()
        except Exception: pass
        try:
            if seconds <= 0.01:
                self.status_label.set_text(self.tr("Crossfade désactivé"))
            else:
                self.status_label.set_text(
                    self.tr("Crossfade") + " : %.1f s" % seconds
                )
        except Exception: pass

    def set_gapless_enabled(self, enabled):
        self.gapless_enabled = bool(enabled)
        try: self.save_state()
        except Exception: pass
        try:
            self.status_label.set_text(
                self.tr("Gapless activé") if self.gapless_enabled
                else self.tr("Gapless désactivé")
            )
        except Exception: pass

    def _start_crossfade_out(self, then_callback=None):
        if self._crossfade_timeout_id:
            try: GLib.source_remove(self._crossfade_timeout_id)
            except Exception: pass
            self._crossfade_timeout_id = 0
        secs = float(getattr(self, "crossfade_seconds", 0.0) or 0.0)
        if secs < 0.15 or not self.is_playing:
            if then_callback:
                then_callback()
            return
        self._crossfade_active = True
        steps = max(8, int(secs * 20))
        step_ms = max(20, int((secs * 1000) / steps))
        try:
            start_vol = float(self.volume_scale.get_value())
        except Exception:
            start_vol = 0.75
        state = {"i": 0, "start": start_vol}
        def _tick():
            if not getattr(self, "_crossfade_active", False): return False
            state["i"] += 1
            frac = state["i"] / float(steps)
            if frac >= 1.0:
                try: self.player.set_property("volume", 0.0)
                except Exception: pass
                self._crossfade_active = False
                self._crossfade_timeout_id = 0
                if then_callback:
                    then_callback()
                def _restore():
                    try:
                        self.player.set_property("volume", state["start"])
                        self.volume_scale.set_value(state["start"])
                    except Exception: pass
                    return False
                GLib.timeout_add(80, _restore)
                return False
            v = state["start"] * (1.0 - frac)
            try: self.player.set_property("volume", max(0.0, v))
            except Exception: pass
            return True
        self._crossfade_timeout_id = GLib.timeout_add(step_ms, _tick)
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    def cancel_sleep_timer(self):
        if getattr(self, "sleep_timer_id", 0):
            try: GLib.source_remove(self.sleep_timer_id)
            except Exception: pass
        self.sleep_timer_id = 0
        self.sleep_timer_end = 0.0
        self.sleep_timer_minutes = 0
        self.sleep_end_of_track = False
        try: self.status_label.set_text(self.tr("Minuteur annulé"))
        except Exception: pass
    def set_sleep_timer(self, minutes=0, end_of_track=False):
        self.cancel_sleep_timer()
        self.sleep_end_of_track = bool(end_of_track)
        if end_of_track:
            self.sleep_timer_minutes = 0
            try: self.status_label.set_text(self.tr("Arrêt à la fin du titre"))
            except Exception: pass
            return
        try:
            minutes = int(minutes)
        except Exception:
            minutes = 0
        if minutes <= 0: return
        self.sleep_timer_minutes = minutes
        self.sleep_timer_end = time.monotonic() + minutes * 60.0
        def _fire():
            self.sleep_timer_id = 0
            self.sleep_timer_end = 0.0
            self.sleep_timer_minutes = 0
            try:
                self.toggle_pause()
            except Exception:
                try:
                    self.player.set_state(Gst.State.PAUSED)
                    self.is_paused = True
                    self.play_button.set_label("▶")
                except Exception: pass
            try: self.status_label.set_text(self.tr("Minuteur : arrêt"))
            except Exception: pass
            return False
        self.sleep_timer_id = GLib.timeout_add_seconds(minutes * 60, _fire)
        try:
            self.status_label.set_text(
                self.tr("Minuteur") + " : %d min" % minutes
            )
        except Exception: pass

    def show_sleep_timer_dialog(self, *args):
        dialog = Gtk.Dialog(
            title=self.tr("Minuteur d'arrêt"),
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
        )
        dialog.add_button(self.tr("Annuler"), Gtk.ResponseType.CANCEL)
        dialog.add_button(self.tr("Appliquer"), Gtk.ResponseType.OK)
        dialog.set_default_size(self._ui_s(360), self._ui_s(240))
        dialog.set_border_width(0)
        box = dialog.get_content_area()
        box.set_spacing(8)
        try:
            box.set_margin_start(self._ui_s(14))
            box.set_margin_end(self._ui_s(14))
            box.set_margin_top(self._ui_s(10))
            box.set_margin_bottom(self._ui_s(8))
        except Exception:
            box.set_border_width(12)
        info = Gtk.Label(
            label=self.tr("Arrêter la lecture après :"),
            xalign=0,
        )
        try: info.get_style_context().add_class("status")
        except Exception: pass
        box.pack_start(info, False, False, 0)
        store = Gtk.ListStore(str, int)
        store.append([self.tr("Désactiver"), -1])
        for m in SLEEP_PRESETS_MIN:
            store.append(["%d min" % m, m])
        store.append([self.tr("Fin du titre en cours"), 0])
        combo = Gtk.ComboBox.new_with_model(store)
        cell = Gtk.CellRendererText()
        combo.pack_start(cell, True)
        combo.add_attribute(cell, "text", 0)
        if self.sleep_end_of_track:
            combo.set_active(len(SLEEP_PRESETS_MIN) + 1)
        elif self.sleep_timer_minutes > 0:
            try:
                idx = 1 + list(SLEEP_PRESETS_MIN).index(self.sleep_timer_minutes)
                combo.set_active(idx)
            except Exception:
                combo.set_active(0)
        else:
            combo.set_active(0)
        box.pack_start(combo, False, False, 0)
        remaining = Gtk.Label(xalign=0)
        try: remaining.get_style_context().add_class("status")
        except Exception: pass
        if self.sleep_timer_end > 0:
            left = max(0, int(self.sleep_timer_end - time.monotonic()))
            remaining.set_text(
                self.tr("Temps restant") + " : %d:%02d" % (left // 60, left % 60)
            )
        elif self.sleep_end_of_track:
            remaining.set_text(self.tr("Arrêt à la fin du titre"))
        else:
            remaining.set_text("")
        box.pack_start(remaining, False, False, 0)
        try: self._style_app_dialog(dialog)
        except Exception: pass
        try: self._translate_dialog_action_buttons(dialog)
        except Exception: pass
        dialog.show_all()
        resp = dialog.run()
        if resp == Gtk.ResponseType.OK:
            it = combo.get_active_iter()
            if it is not None:
                minutes = store[it][1]
                if minutes < 0:
                    self.cancel_sleep_timer()
                elif minutes == 0:
                    self.set_sleep_timer(end_of_track=True)
                else:
                    self.set_sleep_timer(minutes=minutes)
        dialog.destroy()

    def _list_pulse_sinks(self):
        sinks = [("", self.tr("Automatique (défaut)"))]
        try:
            out = subprocess.check_output(
                ["pactl", "list", "short", "sinks"],
                stderr=subprocess.DEVNULL,
                timeout=2.5,
                universal_newlines=True,
            )
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    name = parts[1].strip()
                    if name:
                        sinks.append((name, name))
        except Exception: pass
        if len(sinks) == 1:
            try:
                out = subprocess.check_output(
                    ["pactl", "list", "sinks"],
                    stderr=subprocess.DEVNULL,
                    timeout=3.0,
                    universal_newlines=True,
                )
                name = desc = None
                for line in out.splitlines():
                    line = line.strip()
                    if line.startswith("Name:"):
                        name = line.split(":", 1)[1].strip()
                    elif line.startswith("Description:"):
                        desc = line.split(":", 1)[1].strip()
                        if name:
                            sinks.append((name, desc or name))
                            name = desc = None
            except Exception: pass
        return sinks

    def _apply_audio_sink(self, sink_name):
        sink_name = (sink_name or "").strip()
        self.audio_sink_name = sink_name
        try:
            if sink_name:
                sink = Gst.ElementFactory.make("pulsesink", "user_asink")
                if sink is None:
                    sink = Gst.ElementFactory.make("pipewiresink", "user_asink")
                if sink is not None:
                    try:
                        sink.set_property("device", sink_name)
                    except Exception:
                        try: sink.set_property("sink", sink_name)
                        except Exception: pass
                    self.player.set_property("audio-sink", sink)
                    self._audio_sink_element = sink
                else:
                    self.player.set_property("audio-sink", None)
                    self._audio_sink_element = None
            else:
                self.player.set_property("audio-sink", None)
                self._audio_sink_element = None
        except Exception as e:
            print("audio sink:", e, file=sys.stderr)
        try: self.save_state()
        except Exception: pass
        return False

    def show_audio_device_dialog(self, *args):
        dialog = Gtk.Dialog(
            title=self.tr("Sortie audio"),
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
        )
        dialog.add_button(self.tr("Annuler"), Gtk.ResponseType.CANCEL)
        dialog.add_button(self.tr("Appliquer"), Gtk.ResponseType.OK)
        dialog.set_default_size(self._ui_s(420), self._ui_s(280))
        dialog.set_border_width(0)
        box = dialog.get_content_area()
        box.set_spacing(8)
        try:
            box.set_margin_start(self._ui_s(14))
            box.set_margin_end(self._ui_s(14))
            box.set_margin_top(self._ui_s(10))
            box.set_margin_bottom(self._ui_s(8))
        except Exception:
            box.set_border_width(12)
        lab = Gtk.Label(
            label=self.tr("Périphérique de sortie :"),
            xalign=0,
        )
        box.pack_start(lab, False, False, 0)
        sinks = self._list_pulse_sinks()
        store = Gtk.ListStore(str, str)  # name, label
        active = 0
        for i, (name, label) in enumerate(sinks):
            store.append([name, label])
            if name == getattr(self, "audio_sink_name", ""):
                active = i
        combo = Gtk.ComboBox.new_with_model(store)
        cell = Gtk.CellRendererText()
        combo.pack_start(cell, True)
        combo.add_attribute(cell, "text", 1)
        combo.set_active(active)
        box.pack_start(combo, False, False, 0)
        hint = Gtk.Label(
            label=self.tr(
                "Nécessite PulseAudio ou PipeWire (pactl). "
                "Le changement peut nécessiter une reprise de lecture."
            ),
            xalign=0,
        )
        hint.set_line_wrap(True)
        try: hint.get_style_context().add_class("status")
        except Exception: pass
        box.pack_start(hint, False, False, 0)
        try: self._style_app_dialog(dialog)
        except Exception: pass
        try: self._translate_dialog_action_buttons(dialog)
        except Exception: pass
        dialog.show_all()
        resp = dialog.run()
        if resp == Gtk.ResponseType.OK:
            it = combo.get_active_iter()
            if it is not None:
                name = store[it][0]
                was_playing = self.is_playing and not self.is_paused
                pos = 0.0
                try:
                    ok, p = self.player.query_position(Gst.Format.TIME)
                    if ok:
                        pos = p / Gst.SECOND
                except Exception: pass
                self.player.set_state(Gst.State.NULL)
                self._apply_audio_sink(name)
                if self.current_path:
                    try:
                        self.player.set_property(
                            "uri", path_to_uri(self.current_path)
                        )
                    except Exception: pass
                    if was_playing:
                        self.player.set_state(Gst.State.PLAYING)
                    else:
                        self.player.set_state(Gst.State.PAUSED)
                    if pos > 0.05:
                        GLib.timeout_add(80, lambda: (self.seek_to(pos), False)[1])
                try:
                    self.status_label.set_text(
                        self.tr("Sortie audio") + " : " + (name or self.tr("Automatique"))
                    )
                except Exception: pass
        dialog.destroy()

    def show_crossfade_dialog(self, *args):
        dialog = Gtk.Dialog(
            title=self.tr("Crossfade / Gapless"),
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
        )
        dialog.add_button(self.tr("Fermer"), Gtk.ResponseType.CLOSE)
        dialog.set_default_size(self._ui_s(400), self._ui_s(240))
        dialog.set_border_width(0)
        box = dialog.get_content_area()
        box.set_spacing(10)
        try:
            box.set_margin_start(self._ui_s(14))
            box.set_margin_end(self._ui_s(14))
            box.set_margin_top(self._ui_s(10))
            box.set_margin_bottom(self._ui_s(8))
        except Exception:
            box.set_border_width(12)
        gap = Gtk.CheckButton(label=self.tr("Lecture gapless (sans silence)"))
        gap.set_active(bool(getattr(self, "gapless_enabled", False)))
        gap.connect(
            "toggled",
            lambda b: self.set_gapless_enabled(b.get_active()),
        )
        box.pack_start(gap, False, False, 0)
        lab = Gtk.Label(
            label=self.tr("Durée du crossfade (0 = désactivé) :"),
            xalign=0,
        )
        box.pack_start(lab, False, False, 0)
        scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0.0, CROSSFADE_MAX_S, 0.5
        )
        scale.set_digits(1)
        scale.set_value(float(getattr(self, "crossfade_seconds", 0.0) or 0.0))
        scale.set_draw_value(True)
        scale.add_mark(0, Gtk.PositionType.BOTTOM, "0")
        scale.add_mark(3, Gtk.PositionType.BOTTOM, "3 s")
        scale.add_mark(6, Gtk.PositionType.BOTTOM, "6 s")
        def _on_cf(s):
            self.set_crossfade_seconds(s.get_value())
        scale.connect("value-changed", _on_cf)
        box.pack_start(scale, False, False, 0)
        hint = Gtk.Label(
            label=self.tr(
                "Le crossfade effectue un fondu du volume avant le titre suivant. "
                "Le gapless tente d'enchaîner sans trou (fichiers locaux)."
            ),
            xalign=0,
        )
        hint.set_line_wrap(True)
        box.pack_start(hint, False, False, 0)
        try: self._style_app_dialog(dialog)
        except Exception: pass
        try: self._translate_dialog_action_buttons(dialog)
        except Exception: pass
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def factory_reset_and_quit(self):
        try:
            try: self.player.set_state(Gst.State.NULL)
            except Exception: pass
            try: self._destroy_custom_pipeline()
            except Exception: pass
            for path in (
                STATE_FILE,
                EQ_PRESETS_FILE,
                ASSOCIATIONS_FILE,
                RESUME_FILE,
                os.path.join(CONFIG_DIR, "instance.lock"),
                os.path.join(CONFIG_DIR, "instance.sock"),
            ):
                try:
                    if os.path.exists(path):
                        os.unlink(path)
                except Exception: pass
            self.shortcuts = dict(DEFAULT_SHORTCUTS)
            self.theme_name = "sombre"
            self.spectrum_style = "pixel"
            self.spectrum_bar_color = (0.31, 0.71, 0.94)
            self.spectrum_sync_offset_ms = 0
            self.replaygain_mode = "off"
            self.crossfade_seconds = 0.0
            self.gapless_enabled = False
            self.audio_sink_name = ""
            self.scrobble_enabled = False
            self.scrobble_token = ""
            self.scrobble_username = ""
            self.play_next_queue = []
            try: self.cancel_sleep_timer()
            except Exception: pass
            self.playlist_only_mode = False
            self.playlist_only_maximized = False
            self.playback_rate = 1.0
            self.resume_prompt_enabled = True
            self._resume_data = {}
            self._resume_reset = True
            self.window_layer = "normal"
            self.window_borders = True
            self.playlist_transparency = False
            self.show_metadata_panel = True
            self.desktop_notifications = True
            self.performance_mode = "hardware"
            try: self.status_label.set_text(self.tr("Réinitialisation…"))
            except Exception: pass
        except Exception as e:
            print("factory reset:", e, file=sys.stderr)
        try:
            self.close()
        except Exception:
            try: Gtk.main_quit()
            except Exception: pass
    def set_window_layer(self, layer):
        layer = str(layer or "normal").lower()
        if layer not in ("normal", "above", "below"):
            layer = "normal"
        self.window_layer = layer
        try: self._apply_window_layer()
        except Exception: pass
        try: self.save_state()
        except Exception: pass
        try:
            labels = {
                "above": self.tr("Toujours au premier plan"),
                "below": self.tr("Toujours en arrière-plan"),
                "normal": self.tr("Plan normal"),
            }
            self.status_label.set_text(labels.get(layer, labels["normal"]))
        except Exception: pass

    def _apply_window_layer(self):
        layer = str(getattr(self, "window_layer", "normal") or "normal").lower()
        try:
            if layer == "above":
                self.set_keep_below(False)
                self.set_keep_above(True)
            elif layer == "below":
                self.set_keep_above(False)
                self.set_keep_below(True)
            else:
                self.set_keep_above(False)
                self.set_keep_below(False)
        except Exception as e:
            print("window layer:", e, file=sys.stderr)
        return False

    def toggle_window_borders(self, menuitem):
        enabled = bool(menuitem.get_active())
        self.window_borders = enabled
        try:
            self.set_decorated(enabled)
        except Exception as e:
            print("window borders:", e, file=sys.stderr)
        try: self._update_undecorated_move_hint()
        except Exception: pass
        try: self.save_state()
        except Exception: pass
        try:
            self.status_label.set_text(
                self.tr("Bordures activées")
                if enabled
                else self.tr("Bordures désactivées")
            )
        except Exception: pass

    def _update_undecorated_move_hint(self):
        tip = None
        if not bool(getattr(self, "window_borders", True)):
            tip = self.tr(
                "Cliquer ici pour déplacer la fenêtre (clic gauche ou droit)"
            )
        for attr in (
            "top_bar_wrap",
            "top_bar",
            "time_label",
            "bottom_bar_wrap",
            "bottom_bar",
            "bottom_title",
            "bottom_artist",
            "bottom_album",
        ):
            w = getattr(self, attr, None)
            if w is None: continue
            try: w.set_tooltip_text(tip)
            except Exception: pass
    def _on_chrome_press_for_move(self, widget, event):
        if bool(getattr(self, "window_borders", True)): return False
        try:
            if event.type != Gdk.EventType.BUTTON_PRESS: return False
        except Exception:
            return False
        if int(getattr(event, "button", 0) or 0) not in (1, 2, 3): return False
        try:
            if isinstance(widget, (Gtk.Button, Gtk.Scale, Gtk.Entry, Gtk.Switch)): return False
        except Exception: pass
        try:
            self.begin_move_drag(
                int(event.button),
                int(event.x_root),
                int(event.y_root),
                int(event.time),
            )
            return True
        except Exception as e:
            print("begin_move_drag:", e, file=sys.stderr)
            return False

    def _on_transparency_menu_toggled(self, menuitem):
        try:
            enabled = bool(menuitem.get_active())
        except Exception:
            return
        if enabled == bool(getattr(self, "playlist_transparency", False)): return
        self.toggle_playlist_transparency(enabled)

    def _on_metadata_panel_menu_toggled(self, menuitem):
        try:
            enabled = bool(menuitem.get_active())
        except Exception:
            return
        if enabled == bool(getattr(self, "show_metadata_panel", True)): return
        self.show_metadata_panel = enabled
        self._apply_metadata_panel_visibility()
        try: self.save_state()
        except Exception: pass
        try:
            self.status_label.set_text(
                self.tr("Métadonnées activées")
                if enabled
                else self.tr("Métadonnées désactivées")
            )
        except Exception: pass

    def _apply_metadata_panel_visibility(self):
        wrap = getattr(self, "bottom_bar_wrap", None)
        bar = getattr(self, "bottom_bar", None)
        if wrap is None and bar is None: return
        compact = bool(getattr(self, "mini_player_mode", False)) or bool(
            getattr(self, "nano_player_mode", False)
        )
        try:
            if compact: return
            show = bool(getattr(self, "show_metadata_panel", True))
            for w in (wrap, bar):
                if w is None: continue
                if show:
                    w.show()
                else:
                    w.hide()
            if show and wrap is not None:
                try: wrap.show_all()
                except Exception: pass
        except Exception: pass

    def toggle_playlist_transparency(self, menuitem=None):
        if isinstance(menuitem, bool):
            enabled = menuitem
        elif menuitem is not None and hasattr(menuitem, "get_active"):
            enabled = bool(menuitem.get_active())
        else:
            enabled = not bool(getattr(self, "playlist_transparency", False))
        if enabled == bool(getattr(self, "playlist_transparency", False)):
            try: self._apply_playlist_transparency()
            except Exception: pass
            return
        self.playlist_transparency = enabled
        self._apply_playlist_transparency()
        try: self.save_state()
        except Exception: pass
        try:
            self.status_label.set_text(
                self.tr("Transparence activée")
                if enabled
                else self.tr("Transparence désactivée")
            )
        except Exception: pass

    def _init_rgba_visual(self):
        if getattr(self, "_rgba_visual_ready", False): return bool(getattr(self, "_rgba_visual_ok", False))
        self._rgba_visual_ready = True
        self._rgba_visual_ok = False
        try:
            screen = None
            try:
                screen = self.get_screen()
            except Exception:
                screen = None
            if screen is None:
                screen = Gdk.Screen.get_default()
            if screen is None: return False
            visual = None
            try:
                if screen.is_composited():
                    visual = screen.get_rgba_visual()
            except Exception:
                visual = None
            if visual is None:
                try:
                    visual = screen.get_rgba_visual()
                except Exception:
                    visual = None
            if visual is not None:
                self.set_visual(visual)
                self._rgba_visual_ok = True
        except Exception as e:
            print("init rgba visual:", e, file=sys.stderr)
            self._rgba_visual_ok = False
        return self._rgba_visual_ok

    def _on_window_draw_transparent(self, widget, cr):
        if not getattr(self, "playlist_transparency", False): return False
        try:
            cr.save()
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.0)
            cr.paint()
            cr.restore()
        except Exception: pass
        return False

    def _playlist_rgba(self):
        rgba = Gdk.RGBA()
        try:
            palette = THEME_PALETTES.get(
                getattr(self, "theme_name", "sombre"),
                THEME_PALETTES.get("sombre", {}),
            )
            hx = (palette.get("tree_bg") or "#111315").lstrip("#")
            rgba.red = int(hx[0:2], 16) / 255.0
            rgba.green = int(hx[2:4], 16) / 255.0
            rgba.blue = int(hx[4:6], 16) / 255.0
        except Exception:
            rgba.red, rgba.green, rgba.blue = 0.07, 0.07, 0.08
        try:
            a = float(getattr(self, "playlist_transparency_opacity", 0.35))
        except Exception:
            a = 0.35
        rgba.alpha = max(0.0, min(1.0, a))
        return rgba

    def _set_widget_bg_alpha(self, widget, rgba_or_none, *, preserve_selection=False):
        if widget is None: return
        try:
            if preserve_selection:
                flags = (
                    Gtk.StateFlags.NORMAL,
                    Gtk.StateFlags.ACTIVE,
                    Gtk.StateFlags.PRELIGHT,
                    Gtk.StateFlags.FOCUSED,
                    Gtk.StateFlags.BACKDROP,
                )
            else:
                flags = (
                    Gtk.StateFlags.NORMAL,
                    Gtk.StateFlags.ACTIVE,
                    Gtk.StateFlags.PRELIGHT,
                    Gtk.StateFlags.SELECTED,
                    Gtk.StateFlags.FOCUSED,
                    Gtk.StateFlags.BACKDROP,
                )
        except Exception:
            flags = (Gtk.StateFlags.NORMAL,)
        for st in flags:
            try: widget.override_background_color(st, rgba_or_none)
            except Exception: pass
        if preserve_selection:
            try: widget.override_background_color(Gtk.StateFlags.SELECTED, None)
            except Exception: pass
            try:
                widget.override_background_color(
                    Gtk.StateFlags.SELECTED | Gtk.StateFlags.FOCUSED, None
                )
            except Exception: pass

    def _transparency_widget_chain(self):
        widgets = []
        for attr in (
            "tree",
            "playlist_box",
            "playlist_scroll",
            "content_paned",
            "search_entry",
            "search_row",
            "nano_playlist_box",
            "nano_playlist_scroll",
            "nano_playlist_tree",
        ):
            w = getattr(self, attr, None)
            if w is not None and w not in widgets:
                widgets.append(w)
        try:
            tree = getattr(self, "tree", None)
            if tree is not None:
                parent = tree.get_parent()
                hops = 0
                while parent is not None and hops < 8:
                    if parent not in widgets:
                        widgets.append(parent)
                    if parent is getattr(self, "content_paned", None): break
                    try:
                        parent = parent.get_parent()
                    except Exception:
                        break
                    hops += 1
        except Exception: pass
        try:
            child = self.get_child()
            if child is not None and child not in widgets:
                widgets.append(child)
        except Exception: pass
        return widgets

    def _apply_playlist_transparency(self):
        enabled = bool(getattr(self, "playlist_transparency", False))
        try: self._init_rgba_visual()
        except Exception: pass
        try:
            self.set_app_paintable(bool(enabled and getattr(self, "_rgba_visual_ok", False)))
        except Exception:
            try: self.set_app_paintable(enabled)
            except Exception: pass
        if not getattr(self, "_transparency_draw_connected", False):
            try:
                self.connect("draw", self._on_window_draw_transparent)
                self._transparency_draw_connected = True
            except Exception: pass
        try: self.build_css()
        except Exception: pass
        rgba = self._playlist_rgba() if enabled else None
        clear = Gdk.RGBA(red=0, green=0, blue=0, alpha=0) if enabled else None
        for w in self._transparency_widget_chain():
            try:
                ctx = w.get_style_context()
                if enabled:
                    ctx.add_class("playlist-transparent")
                else:
                    ctx.remove_class("playlist-transparent")
                    ctx.remove_class("content-transparent")
            except Exception: pass
            try:
                if not enabled:
                    self._set_widget_bg_alpha(w, None)
                elif w is getattr(self, "tree", None):
                    self._set_widget_bg_alpha(w, rgba, preserve_selection=True)
                    try:
                        palette = THEME_PALETTES.get(
                            getattr(self, "theme_name", "sombre"),
                            THEME_PALETTES.get("sombre", {}),
                        )
                        sel = Gdk.RGBA()
                        hx = (palette.get("tree_sel_bg") or "#30404a").lstrip("#")
                        sel.red = int(hx[0:2], 16) / 255.0
                        sel.green = int(hx[2:4], 16) / 255.0
                        sel.blue = int(hx[4:6], 16) / 255.0
                        sel.alpha = 1.0
                        w.override_background_color(Gtk.StateFlags.SELECTED, sel)
                        w.override_background_color(
                            Gtk.StateFlags.SELECTED | Gtk.StateFlags.FOCUSED, sel
                        )
                        fg = Gdk.RGBA()
                        hx2 = (palette.get("tree_sel_fg") or "#ffffff").lstrip("#")
                        fg.red = int(hx2[0:2], 16) / 255.0
                        fg.green = int(hx2[2:4], 16) / 255.0
                        fg.blue = int(hx2[4:6], 16) / 255.0
                        fg.alpha = 1.0
                        w.override_color(Gtk.StateFlags.SELECTED, fg)
                        w.override_color(
                            Gtk.StateFlags.SELECTED | Gtk.StateFlags.FOCUSED, fg
                        )
                    except Exception: pass
                elif w is getattr(self, "search_entry", None) or w is getattr(self, "search_row", None):
                    self._set_widget_bg_alpha(w, clear)
                    try:
                        for st in (
                            Gtk.StateFlags.NORMAL,
                            Gtk.StateFlags.ACTIVE,
                            Gtk.StateFlags.PRELIGHT,
                            Gtk.StateFlags.SELECTED,
                            Gtk.StateFlags.FOCUSED,
                            Gtk.StateFlags.BACKDROP,
                            Gtk.StateFlags.INSENSITIVE,
                        ):
                            w.override_background_color(st, clear)
                    except Exception: pass
                elif w is getattr(self, "playlist_scroll", None) or w is getattr(self, "playlist_box", None):
                    self._set_widget_bg_alpha(w, rgba)
                    try:
                        if w is getattr(self, "playlist_scroll", None):
                            child = w.get_child()
                            if child is not None:
                                self._set_widget_bg_alpha(child, rgba)
                    except Exception: pass
                else:
                    self._set_widget_bg_alpha(w, clear)
            except Exception: pass
        for attr in ("top_bar", "bottom_bar"):
            w = getattr(self, attr, None)
            if w is None: continue
            try: self._set_widget_bg_alpha(w, None)
            except Exception: pass
        try: self.queue_draw()
        except Exception: pass
        for attr in ("tree", "playlist_scroll", "playlist_box", "content_paned"):
            w = getattr(self, attr, None)
            if w is None: continue
            try: w.queue_draw()
            except Exception: pass
    def toggle_desktop_notifications(self, menuitem):
        enabled = bool(menuitem.get_active())
        self.desktop_notifications = enabled
        try: self.save_state()
        except Exception: pass
        try:
            self.status_label.set_text(
                self.tr("Notifications activées")
                if enabled
                else self.tr("Notifications désactivées")
            )
        except Exception: pass

    def show_spectrum_colors(self, *args):
        dialog = Gtk.ColorChooserDialog(
            title=self.tr("Couleur des barres"),
            parent=self,
        )
        try: dialog.set_use_alpha(False)
        except Exception: pass
        try:
            r, g, b = getattr(self, "spectrum_bar_color", (0.31, 0.71, 0.94))
            rgba = Gdk.RGBA()
            rgba.red = float(r)
            rgba.green = float(g)
            rgba.blue = float(b)
            rgba.alpha = 1.0
            dialog.set_rgba(rgba)
        except Exception: pass
        try: self._style_app_dialog(dialog)
        except Exception: pass
        try: self._translate_dialog_action_buttons(dialog)
        except Exception: pass
        try: dialog.show_all()
        except Exception: pass
        try: self._translate_color_chooser_labels(dialog)
        except Exception: pass
        resp = dialog.run()
        if resp == Gtk.ResponseType.OK:
            try:
                rgba = dialog.get_rgba()
                self.spectrum_bar_color = (
                    float(rgba.red),
                    float(rgba.green),
                    float(rgba.blue),
                )
                try: self.save_state()
                except Exception: pass
                try:
                    if getattr(self, "visualizer", None) is not None:
                        self.visualizer.queue_draw()
                except Exception: pass
                try: self.status_label.set_text(self.tr("Couleur du spectre"))
                except Exception: pass
            except Exception as e:
                print("spectrum color:", e, file=sys.stderr)
        dialog.destroy()

    def _translate_color_chooser_labels(self, dialog):
        if dialog is None: return
        def _norm(text):
            return (text or "").replace("_", "").strip().casefold()
        targets = {
            "personnalisée": "Personnalisée",
            "personnalisee": "Personnalisée",
            "custom": "Personnalisée",
            "palette": "Palette",
            "color": "Couleur",
            "couleur": "Couleur",
            "color name": "Nom de la couleur",
            "nom de la couleur": "Nom de la couleur",
            "hue": "Teinte",
            "teinte": "Teinte",
            "saturation": "Saturation",
            "value": "Valeur",
            "valeur": "Valeur",
            "red": "Rouge",
            "rouge": "Rouge",
            "green": "Vert",
            "vert": "Vert",
            "blue": "Bleu",
            "bleu": "Bleu",
        }
        for msgid, our_key in (
            ("Custom", "Personnalisée"),
            ("_Custom", "Personnalisée"),
            ("Palette", "Palette"),
            ("_Palette", "Palette"),
            ("Color Name", "Nom de la couleur"),
            ("Hue", "Teinte"),
            ("Saturation", "Saturation"),
            ("Value", "Valeur"),
            ("Red", "Rouge"),
            ("Green", "Vert"),
            ("Blue", "Bleu"),
        ):
            try:
                native = GLib.dgettext("gtk30", msgid)
                if native:
                    targets[_norm(native)] = our_key
            except Exception: pass
            targets[_norm(msgid)] = our_key
        def _apply_label(widget):
            try:
                if isinstance(widget, Gtk.Label):
                    raw = widget.get_text() or ""
                    key = targets.get(_norm(raw))
                    if key:
                        widget.set_text(self.tr(key))
                        return
                    try:
                        m = widget.get_label() or ""
                        key2 = targets.get(_norm(m))
                        if key2:
                            widget.set_label(self.tr(key2))
                    except Exception: pass
                elif isinstance(widget, Gtk.Button):
                    lab = widget.get_label() or ""
                    key = targets.get(_norm(lab))
                    if key:
                        widget.set_label(self.tr(key))
            except Exception: pass
        def _walk(w):
            if w is None: return
            _apply_label(w)
            try:
                if isinstance(w, Gtk.Notebook):
                    n = w.get_n_pages()
                    for i in range(n):
                        tab = w.get_tab_label(w.get_nth_page(i))
                        if tab is not None:
                            _walk(tab)
            except Exception: pass
            try:
                if isinstance(w, Gtk.Container):
                    for c in w.get_children():
                        _walk(c)
            except Exception: pass
        try: _walk(dialog)
        except Exception: pass
        def _deferred():
            try: _walk(dialog)
            except Exception: pass
            return False
        try: GLib.idle_add(_deferred)
        except Exception: pass
    def show_spectrum_sync(self, *args):
        if getattr(self, "spectrum_sync_window", None) is not None:
            try: self.spectrum_sync_window.present()
            except Exception: pass
            return
        win = Gtk.Window(title=self.tr("Synchro spectre"))
        win.set_transient_for(self)
        win.set_default_size(self._ui_s(420), self._ui_s(200))
        win.set_resizable(False)
        win.set_border_width(14)
        self.spectrum_sync_window = win
        def _on_destroy(*_a):
            self.spectrum_sync_window = None
            try: self.save_state()
            except Exception: pass
        win.connect("destroy", _on_destroy)
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        win.add(main)
        info = Gtk.Label(
            label=self.tr(
                "Positif : retarde le spectre. Négatif : l'avance."
            ),
            xalign=0,
        )
        info.set_line_wrap(True)
        try: info.get_style_context().add_class("status")
        except Exception: pass
        main.pack_start(info, False, False, 0)
        hint = Gtk.Label(
            label=self.tr(
                "La synchronisation suit l'horloge audio du pipeline. "
                "Utilisez ce curseur pour un écart résiduel (ex. Bluetooth)."
            ),
            xalign=0,
        )
        hint.set_line_wrap(True)
        try: hint.get_style_context().add_class("status")
        except Exception: pass
        main.pack_start(hint, False, False, 0)
        scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL,
            SPECTRUM_SYNC_OFFSET_MIN_MS,
            SPECTRUM_SYNC_OFFSET_MAX_MS,
            10,
        )
        scale.set_digits(0)
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        scale.set_hexpand(True)
        scale.set_value(int(getattr(self, "spectrum_sync_offset_ms", 0) or 0))
        scale.add_mark(0, Gtk.PositionType.BOTTOM, "0 ms")
        def _on_change(s):
            try:
                self.spectrum_sync_offset_ms = int(round(s.get_value()))
            except Exception:
                self.spectrum_sync_offset_ms = 0
            try:
                self.status_label.set_text(
                    self.tr("Synchro spectre")
                    + " : %+d ms" % self.spectrum_sync_offset_ms
                )
            except Exception: pass
        scale.connect("value-changed", _on_change)
        main.pack_start(scale, False, False, 0)
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_halign(Gtk.Align.END)
        main.pack_start(btn_box, False, False, 0)
        reset_btn = Gtk.Button(label=self.tr("Réinitialiser"))
        reset_btn.connect("clicked", lambda b: scale.set_value(0))
        btn_box.pack_start(reset_btn, False, False, 0)
        close_btn = Gtk.Button(label=self.tr("Fermer"))
        close_btn.connect("clicked", lambda b: win.destroy())
        btn_box.pack_start(close_btn, False, False, 0)
        try: self._style_app_dialog(win)
        except Exception: pass
        win.show_all()

    def toggle_spectrum(self, menuitem):
        self.spectrum_enabled = menuitem.get_active()
        try:
            self._sync_spectrum_activity()
        except Exception:
            self._set_spectrum_active_for_media(self.current_is_video)
        if self.spectrum_enabled:
            self.status_label.set_text(self.tr("Spectre activé"))
        else:
            self.reset_spectrum()
            try:
                if getattr(self, "visualizer", None) is not None:
                    self.visualizer.queue_draw()
            except Exception: pass
            self.status_label.set_text(self.tr("Spectre désactivé"))

    def toggle_subtitles(self, menuitem):
        self.set_subtitles_enabled(menuitem.get_active())
        if self.subtitles_enabled:
            self.status_label.set_text(self.tr("Sous-titres activés"))
            if self._custom_pipeline is None:
                GLib.timeout_add(300, self._deferred_refresh_subtitles)
        else:
            self.current_subtitle_index = -1
            self.status_label.set_text(self.tr("Sous-titres désactivés"))

    def _translate_about_dialog_buttons(self, dialog):
        def _norm(text):
            return (text or "").replace("_", "").strip().casefold()
        def _native(msgid):
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
            except Exception: pass

    def _hide_about_license_button(self, dialog):
        def _norm(text):
            return (text or "").replace("_", "").strip().casefold()
        names = {"license", "licence"}
        for msgid in ("_License", "_Licence"):
            try: names.add(_norm(GLib.dgettext("gtk30", msgid)))
            except Exception: pass
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
            except Exception: pass
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
        except Exception: pass
        dialog.set_program_name(APP_NAME)
        dialog.set_version("1.4.1")
        dialog.set_comments(
            self.tr("Un lecteur audio complet en GTK3 et GStreamer")
        )
        try: dialog.set_copyright("© 2026 O. Fraisse (MilOvni)")
        except Exception: pass
        try:
            dialog.add_credit_section(
                self.tr("Créé par"),
                [
                    "O. Fraisse (MilOvni)",
                    self.tr("avec l'assistance de Grok (IA)"),
                ],
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
        except Exception: pass
        dialog.show_all()
        self._hide_about_license_button(dialog)
        self._translate_about_dialog_buttons(dialog)
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
            except Exception: pass
            support.show()
        except Exception: pass
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
            except Exception: pass
        dialog.set_title(self.tr("À propos de") + " " + APP_NAME)
        dialog.run()
        dialog.destroy()

    def on_destroy(self, *args):
        try: self._inhibit_screensaver(False)
        except Exception: pass
        self._app_closing = True
        try: self._resume_save_current(force=True)
        except Exception: pass
        try:
            if getattr(self, "_recording", False):
                self._stop_recording(show_save_dialog=True)
        except Exception: pass
        try: self._destroy_record_pipeline()
        except Exception: pass
        try: self._stop_mini_title_scroll()
        except Exception: pass
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
            "_disk_meta_save_id",
            "_selection_info_timeout_id",
            "_transparency_css_timeout_id",
        ):
            tid = getattr(self, attr, 0) or 0
            if tid:
                try: GLib.source_remove(tid)
                except Exception: pass
                try: setattr(self, attr, 0)
                except Exception: pass
        for attr in ("_main_popup_menu", "_playlist_context_menu"):
            m = getattr(self, attr, None)
            if m is not None:
                try: m.destroy()
                except Exception: pass
                try: setattr(self, attr, None)
                except Exception: pass
        for attr in (
            "pitch_window",
            "eq_window",
            "colorimetry_window",
            "speed_window",
            "spectrum_sync_window",
            "fs_controls",
        ):
            w = getattr(self, attr, None)
            if w is not None:
                try: w.destroy()
                except Exception: pass
        try:
            self.save_playlist()
            self.save_state()
            self.save_radios()
            self.save_tvs()
            self.save_bouquets()
            self.save_associations()
            self._disk_meta_dirty = True
            self._save_disk_meta_cache()
        except Exception as e:
            print("save on destroy:", e, file=sys.stderr)
        try: trim_memory(force=True)
        except Exception: pass
        try:
            if getattr(self, "_mpris_owner_id", 0):
                Gio.bus_unown_name(self._mpris_owner_id)
                self._mpris_owner_id = 0
        except Exception: pass
        try: self._destroy_custom_pipeline()
        except Exception: pass
        try:
            if getattr(self, "bus", None) is not None:
                try: self.bus.remove_signal_watch()
                except Exception: pass
        except Exception: pass
        try:
            self.player.set_state(Gst.State.NULL)
            self.player.get_state(200 * Gst.MSECOND)
        except Exception: pass
        try: self.player.set_property("video-sink", None)
        except Exception: pass
        try:
            if self.video_sink is not None:
                self.video_sink.set_state(Gst.State.NULL)
                self.video_sink = None
        except Exception: pass
        try:
            self._pause_pixbuf = None
            self.reset_spectrum()
        except Exception: pass
        owns_instance = getattr(self, "_instance_lock_fd", None) is not None
        try:
            sock = getattr(self, "instance_socket", None)
            if sock is not None:
                self.instance_socket = None
                try: sock.close()
                except Exception: pass
        except Exception: pass
        if owns_instance:
            try:
                if os.path.exists(SOCKET_FILE):
                    os.unlink(SOCKET_FILE)
            except Exception: pass
            lock_fd = self._instance_lock_fd
            self._instance_lock_fd = None
            try: fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            except Exception: pass
            try: lock_fd.close()
            except Exception: pass
        Gtk.main_quit()

def parse_cli_media_files(argv):
    files = []
    seen = set()
    for argument in argv:
        if not argument or argument.startswith("-"): continue
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
        if not os.path.isfile(path): continue
        if not (is_audio_file(path) or is_video_file(path)): continue
        if path in seen: continue
        seen.add(path)
        files.append(path)
    return files

def forward_to_existing_instance(files, attempts=1, delay=0.05):
    if files:
        payload = ("\n".join(files) + "\n").encode("utf-8")
    else:
        payload = b"\n"
    for attempt in range(max(1, attempts)):
        client = None
        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(1.5)
            client.connect(SOCKET_FILE)
            client.sendall(payload)
            try: client.shutdown(socket.SHUT_WR)
            except Exception: pass
            try:
                client.settimeout(0.2)
                client.recv(16)
            except Exception: pass
            client.close()
            return True
        except Exception:
            if client is not None:
                try: client.close()
                except Exception: pass
            if attempt + 1 < attempts:
                time.sleep(delay)
    return False

def acquire_instance_lock():
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
        if getattr(exc, "errno", None) not in (
            errno.EACCES,
            errno.EAGAIN,
            errno.EWOULDBLOCK,
            None,
        ) and not isinstance(exc, BlockingIOError):
            print("fcntl.flock :", exc, file=sys.stderr)
        try: fd.close()
        except Exception: pass
        return None
    try:
        fd.seek(0)
        fd.truncate()
        fd.write(str(os.getpid()))
        fd.flush()
    except Exception: pass
    return fd

def main():
    files = parse_cli_media_files(sys.argv[1:])
    if forward_to_existing_instance(files, attempts=4, delay=0.05): return
    lock_fd = acquire_instance_lock()
    if lock_fd is None:
        if forward_to_existing_instance(files, attempts=60, delay=0.05): return
        print(
            "Myostoox : une instance semble déjà active mais ne répond pas. "
            "Abandon pour éviter un doublon.",
            file=sys.stderr,
        )
        return
    try: GLib.set_prgname("myostoox")
    except Exception: pass
    try:
        Gst.init(None)
        apply_safe_gstreamer_ranks()
    except Exception: pass
    try:
        logo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
        if os.path.isfile(logo):
            Gtk.Window.set_default_icon_from_file(logo)
    except Exception: pass
    window = Myostoox()
    window._instance_lock_fd = lock_fd
    try:
        if os.environ.get("MYOSTOOX_MEMLOG", "").strip() in ("1", "true", "yes"):
            _memlog_tick.window = window
            GLib.timeout_add_seconds(30, _memlog_tick)
            print("[memlog] enabled (every 30s)", flush=True)
    except Exception: pass
    try:
        cache_root = GLib.get_user_cache_dir()
        rec_dir = os.path.join(cache_root or os.path.expanduser("~/.cache"), APP_NAME)
        if os.path.isdir(rec_dir):
            for name in os.listdir(rec_dir):
                if name.startswith("myostoox_rec_") and name.endswith(".mp3"):
                    try: os.unlink(os.path.join(rec_dir, name))
                    except Exception: pass
    except Exception: pass
    if not window.start_single_instance_server():
        print(
            "Myostoox : socket d'instance indisponible "
            "(les ouvertures externes pourraient échouer).",
            file=sys.stderr,
        )
    window.show_all()
    try: window._hide_nano_playlist_panel()
    except Exception: pass
    try: window._apply_window_layer()
    except Exception: pass
    try: window._apply_playlist_only_mode()
    except Exception: pass
    try:
        if getattr(window, "playlist_only_maximized", False) and getattr(
            window, "playlist_only_mode", False
        ):
            window.set_playlist_only_maximized(True)
    except Exception: pass
    if files:
        try: window.cancel_pending_session_restore()
        except Exception: pass
        for filename in files:
            window.add_file(filename, save=False)
        window.save_playlist()
        if len(files) == 1:
            def _cli_play(p=files[0]):
                try:
                    window.play_file(p)
                except Exception as exc:
                    print("cli play:", exc, file=sys.stderr)
                return False
            GLib.idle_add(_cli_play)
    Gtk.main()
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Erreur :", e, file=sys.stderr)
        raise
