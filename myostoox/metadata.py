# -*- coding: utf-8 -*-
"""Metadata, covers and lyrics helpers for Myostoox."""
from __future__ import annotations

import os
import sys
import re
from typing import Any, Optional

try:
    from gi.repository import GLib, GdkPixbuf
except Exception:
    GLib = None
    GdkPixbuf = None

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
    FLAC = OggVorbis = OggOpus = MP4 = None

try:
    from myostoox.playlist import (
        is_stream, is_cdda, is_dvd, is_bluray, is_special_source, is_video_file,
    )
except Exception:
    try:
        from playlist import (
            is_stream, is_cdda, is_dvd, is_bluray, is_special_source, is_video_file,
        )
    except Exception:
        def is_stream(path):
            return isinstance(path, str) and path.lower().startswith(
                ("http://", "https://", "mms://", "rtsp://", "icy://")
            )
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
            return is_cdda(path) or is_stream(path) or is_dvd(path) or is_bluray(path)
        def is_video_file(path):
            if not isinstance(path, str) or is_cdda(path) or is_stream(path):
                return False
            if is_dvd(path) or is_bluray(path):
                return True
            return os.path.splitext(path)[1].lower() in {
                ".mp4", ".mkv", ".avi", ".mpeg", ".mpg", ".webm"
            }

COVER_DECODE_MAX_PX = 256
COVER_PIXBUF_CACHE_MAX = 24
EXTERNAL_COVER_MAX_BYTES = 8 * 1024 * 1024
METADATA_CACHE_MAX = 1500
SUBTITLE_FONT_DESC = "DejaVu Sans Bold 12"
SUBTITLE_EXTENSIONS = {
    ".srt", ".vtt", ".ass", ".ssa", ".sub", ".smi", ".ttml", ".sbv",
}

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
