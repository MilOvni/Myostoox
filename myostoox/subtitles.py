# -*- coding: utf-8 -*-
"""Subtitle and audio-track helpers for Myostoox (app = window host)."""
from __future__ import annotations

import os
import sys
from collections import OrderedDict

DEBUG_MYOSTOOX = bool(os.environ.get("MYOSTOOX_DEBUG", "").strip())

try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst, GLib
except Exception:
    Gst = None
    GLib = None

try:
    gi.require_version("GstPbutils", "1.0")
    from gi.repository import GstPbutils
except Exception:
    GstPbutils = None

try:
    from myostoox.playlist import (
        is_stream, is_cdda, is_dvd, is_bluray, is_video_file,
        is_audio_file, is_special_source,
    )
except Exception:
    try:
        from playlist import (
            is_stream, is_cdda, is_dvd, is_bluray, is_video_file,
            is_audio_file, is_special_source,
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
        def is_audio_file(path):
            if is_special_source(path):
                return True
            return (
                os.path.isfile(path)
                and os.path.splitext(path)[1].lower() in {
                    ".mp3", ".ogg", ".oga", ".opus", ".flac", ".wav", ".aac", ".m4a",
                    ".ac3", ".dts", ".aiff", ".aif", ".wv", ".wma",
                }
            )

try:
    from myostoox.metadata import SUBTITLE_EXTENSIONS, SUBTITLE_FONT_DESC
except Exception:
    try:
        from metadata import SUBTITLE_EXTENSIONS, SUBTITLE_FONT_DESC
    except Exception:
        SUBTITLE_FONT_DESC = "DejaVu Sans Bold 12"
        SUBTITLE_EXTENSIONS = {
            ".srt", ".vtt", ".ass", ".ssa", ".sub", ".smi", ".ttml", ".sbv",
        }

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
    all_subs = []
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
    if not results and len(all_subs) == 1:
        name, path, root, ext_lower = all_subs[0]
        _add(path, None, ext_lower, 10)
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

def guess_subtitle_encoding(path):
    """Devine l'encodage d'un fichier de sous-titres (UTF-8 / UTF-16 / CP1252)."""
    limit = 256 * 1024
    try:
        with open(path, "rb") as f:
            data = f.read(limit)
    except Exception:
        return "UTF-8"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "UTF-16"
    if data.startswith(b"\xef\xbb\xbf"):
        return "UTF-8"
    try:
        data.decode("utf-8")
        return "UTF-8"
    except UnicodeDecodeError as exc:
        if len(data) >= limit and exc.start >= len(data) - 4:
            return "UTF-8"
    return "CP1252"

def _discover_subtitle_languages(app, filepath):
    result = {}
    if GstPbutils is None or not filepath: return result
    try:
        st = os.stat(filepath)
        cache_key = (os.path.abspath(filepath), int(st.st_mtime), int(st.st_size))
    except Exception:
        cache_key = None
    cache = getattr(app, "_sub_lang_cache", None)
    if cache is None:
        app._sub_lang_cache = {}
        cache = app._sub_lang_cache
    if cache_key is not None and cache_key in cache:
        try: return dict(cache[cache_key])
        except Exception: pass
    try:
        uri = GLib.filename_to_uri(os.path.abspath(filepath), None)
    except Exception:
        return result
    try:
        disc = app._get_discoverer()
        if disc is None:
            return result
        info = disc.discover_uri(uri)
    except Exception as e:
        print("Discoverer sous-titres:", e, file=sys.stderr)
        if cache_key is not None:
            try:
                cache[cache_key] = {}
            except Exception:
                pass
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
                        lang = app._normalize_lang_code(val) or val
                        break
                except Exception: pass
        if not lang:
            try:
                if hasattr(stream, "get_language"):
                    raw = stream.get_language()
                    if raw:
                        lang = app._normalize_lang_code(raw) or raw
            except Exception: pass
        sid = None
        try:
            sid = stream.get_stream_id()
        except Exception:
            sid = None
        if sid:
            result[sid] = lang
    result["__n__"] = len(streams)
    if cache_key is not None:
        try:
            if not isinstance(cache, OrderedDict):
                app._sub_lang_cache = OrderedDict(cache)
                cache = app._sub_lang_cache
            cache[cache_key] = dict(result)
            try:
                cache.move_to_end(cache_key)
            except Exception:
                pass
            while len(cache) > PROBE_CACHE_MAX:
                try:
                    cache.popitem(last=False)
                except Exception:
                    break
        except Exception: pass
    return result

def _discover_audio_track_labels(app, filepath):
    labels = []
    if GstPbutils is None or not filepath:
        return labels
    try:
        st = os.stat(filepath)
        cache_key = ("a", os.path.abspath(filepath), int(st.st_mtime), int(st.st_size))
    except Exception:
        cache_key = None
    cache = getattr(app, "_audio_label_cache", None)
    if cache is None:
        app._audio_label_cache = OrderedDict()
        cache = app._audio_label_cache
    if cache_key is not None and cache_key in cache:
        try:
            return list(cache[cache_key])
        except Exception:
            pass
    try:
        uri = GLib.filename_to_uri(os.path.abspath(filepath), None)
    except Exception:
        return labels
    try:
        disc = app._get_discoverer()
        if disc is None:
            return labels
        info = disc.discover_uri(uri)
    except Exception as e:
        print("Discoverer audio:", e, file=sys.stderr)
        return labels
    try:
        streams = info.get_audio_streams() or []
    except Exception:
        streams = []
    for i, stream in enumerate(streams):
        lang = None
        title = None
        try:
            tags = stream.get_tags()
        except Exception:
            tags = None
        if tags is not None:
            for tkey in (Gst.TAG_LANGUAGE_CODE, Gst.TAG_LANGUAGE_NAME):
                try:
                    ok, val = tags.get_string(tkey)
                    if ok and val and val not in ("und", "unk", ""):
                        lang = app._normalize_lang_code(val) or val
                        if lang:
                            break
                except Exception:
                    pass
            try:
                ok, val = tags.get_string(Gst.TAG_TITLE)
                if ok and val and val.strip():
                    title = val.strip()
            except Exception:
                pass
            if not title:
                try:
                    ok, val = tags.get_string("title")
                    if ok and val and val.strip():
                        title = val.strip()
                except Exception:
                    pass
        if not lang:
            try:
                if hasattr(stream, "get_language"):
                    raw = stream.get_language()
                    if raw and raw not in ("und", "unk", ""):
                        lang = app._normalize_lang_code(raw) or raw
            except Exception:
                pass
        parts = []
        if lang:
            parts.append(lang)
        if title and title not in parts:
            parts.append(title)
        if parts:
            labels.append(" — ".join(parts))
        else:
            labels.append(app.tr("Piste ") + str(i + 1))
    if cache_key is not None and labels:
        try:
            cache[cache_key] = list(labels)
            try:
                cache.move_to_end(cache_key)
            except Exception:
                pass
            while len(cache) > PROBE_CACHE_MAX:
                try:
                    cache.popitem(last=False)
                except Exception:
                    break
        except Exception:
            pass
    return labels


def _disable_all_subtitles(app):
    """Coupe sous-titres intégrés, externes et overlay (playbin + custom)."""
    app.subtitles_enabled = False
    app.current_subtitle_index = -1
    if app._custom_pipeline is not None:
        try:
            app._set_custom_subtitle_active(-1)
        except Exception:
            pass
        try:
            ov = getattr(app, "_custom_sub_overlay", None)
            if ov is not None:
                try:
                    ov.set_property("silent", True)
                except Exception:
                    pass
                try:
                    ov.set_property("text", "")
                except Exception:
                    pass
        except Exception:
            pass
        try:
            app._unlink_custom_text_sink()
        except Exception:
            pass
        app._active_external_sub_path = None
        app._custom_subtitle_index = -1
        app._custom_sub_muted = True
    else:
        try:
            app.player.set_property("current-text", -1)
        except Exception:
            pass
        try:
            app._clear_external_suburi()
        except Exception:
            pass
        try:
            if app.player.find_property("subtitle-font-desc"):
                app.player.set_property(
                    "subtitle-font-desc", SUBTITLE_FONT_DESC
                )
        except Exception:
            pass

def _prepare_external_subtitles_for(app, filename):
    app.external_subtitles = []
    app._active_external_sub_path = None
    try:
        app.player.set_property("suburi", None)
    except Exception:
        try: app.player.set_property("suburi", "")
        except Exception: pass
    if not filename or is_stream(filename) or is_cdda(filename): return
    try:
        if is_dvd(filename) or is_bluray(filename): return
    except Exception: pass
    if not (is_video_file(filename) or getattr(app, "current_is_video", False)):
        if not (isinstance(filename, str) and os.path.isfile(filename)): return
    try:
        app.external_subtitles = find_external_subtitles(filename) or []
    except Exception as e:
        print("scan sous-titres externes:", e, file=sys.stderr)
        app.external_subtitles = []
    if not app.external_subtitles: return
    try: app._register_external_subtitle_tracks()
    except Exception: pass
def _register_external_subtitle_tracks(app):
    active = getattr(app, "_active_external_sub_path", None)
    for item in getattr(app, "external_subtitles", None) or []:
        path = item.get("path")
        if not path: continue
        label = item.get("label") or os.path.basename(path)
        if not str(label).endswith("]"):
            label = "%s [%s]" % (label, os.path.basename(path))
        if any(
            isinstance(t, dict)
            and t.get("kind") == "external"
            and t.get("path") == path
            for t in (app.subtitle_tracks or [])
        ):
            continue
        app.subtitle_tracks.append({
            "kind": "external",
            "index": None,
            "path": path,
            "label": label,
        })
        if active and path == active and app.current_subtitle_index < 0:
            app.current_subtitle_index = len(app.subtitle_tracks) - 1

def refresh_subtitle_tracks(app):
    app.subtitle_tracks = []
    embedded_current = -1
    try:
        n_text = app.player.get_property("n-text")
        if n_text is not None and int(n_text) > 0:
            try:
                cur = app.player.get_property("current-text")
                embedded_current = cur if cur is not None else -1
            except Exception:
                embedded_current = -1
            for i in range(int(n_text)):
                lang = None
                try:
                    tags = app.player.emit("get-text-tags", i)
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
                label = lang if lang else app.tr("Piste ") + str(i + 1)
                app.subtitle_tracks.append({
                    "kind": "embedded",
                    "index": i,
                    "path": None,
                    "label": label,
                })
    except Exception as e:
        print("Erreur lecture pistes sous-titres intégrées:", e, file=sys.stderr)
    active_ext = app._active_external_sub_path
    for item in app.external_subtitles:
        path = item["path"]
        label = item["label"]
        if not label.endswith("]"):
            label = f"{label} [{os.path.basename(path)}]"
        app.subtitle_tracks.append({
            "kind": "external",
            "index": None,
            "path": path,
            "label": label,
        })
    app.current_subtitle_index = -1
    if not app.subtitles_enabled: return
    if active_ext:
        for i, track in enumerate(app.subtitle_tracks):
            if track["kind"] == "external" and track["path"] == active_ext:
                app.current_subtitle_index = i
                return
    if embedded_current >= 0:
        for i, track in enumerate(app.subtitle_tracks):
            if track["kind"] == "embedded" and track["index"] == embedded_current:
                app.current_subtitle_index = i
                return

def refresh_audio_tracks(app):
    app.audio_tracks = []
    app.current_audio_index = -1
    try:
        n_audio = app.player.get_property("n-audio")
        if n_audio is None or n_audio <= 0: return
        current = app.player.get_property("current-audio")
        app.current_audio_index = current if current is not None else -1
        for i in range(int(n_audio)):
            lang = None
            title = None
            try:
                tags = app.player.emit("get-audio-tags", i)
                if tags is not None:
                    ok, value = tags.get_string(Gst.TAG_LANGUAGE_CODE)
                    if ok and value and value not in ("und", "unk"):
                        lang = value
                    if not lang:
                        ok, value = tags.get_string(Gst.TAG_LANGUAGE_NAME)
                        if ok and value and value not in ("und", "unk"):
                            lang = value
                    if not lang:
                        ok, value = tags.get_string("language-code")
                        if ok and value and value not in ("und", "unk"):
                            lang = value
                    try:
                        ok, value = tags.get_string(Gst.TAG_TITLE)
                        if ok and value and value.strip():
                            title = value.strip()
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                if lang:
                    lang = app._normalize_lang_code(lang) or lang
            except Exception:
                pass
            parts = []
            if lang:
                parts.append(lang)
            if title and title not in parts:
                parts.append(title)
            if parts:
                label = " — ".join(parts)
            else:
                label = app.tr("Piste ") + str(i + 1)
            app.audio_tracks.append((i, label))
    except Exception as e:
        print("Erreur lecture pistes audio:", e, file=sys.stderr)

def set_subtitle_track(app, track_list_index):
    try:
        if (
            track_list_index is not None
            and track_list_index >= 0
            and track_list_index == getattr(app, "current_subtitle_index", -1)
            and getattr(app, "subtitles_enabled", False)
        ):
            return
        if getattr(app, "_subtitle_switch_busy", False): return
        app._subtitle_switch_busy = True
        try:
            app._set_subtitle_track_impl(track_list_index)
        finally:
            app._subtitle_switch_busy = False
    except Exception as e:
        app._subtitle_switch_busy = False
        print("Erreur sélection sous-titres:", e, file=sys.stderr)

def _set_subtitle_track_impl(app, track_list_index):
    try:
        if track_list_index is None or track_list_index < 0:
            try:
                app._disable_all_subtitles()
            except Exception:
                pass
            try:
                app.status_label.set_text(app.tr("Sous-titres désactivés"))
            except Exception:
                pass
            return
        if track_list_index >= len(app.subtitle_tracks): return
        track = app.subtitle_tracks[track_list_index]
        if (
            app._custom_pipeline is not None
            and track.get("kind") == "embedded"
        ):
            if track.get("text") is False:
                app.status_label.set_text(
                    app.tr("Sous-titres")
                    + " — image non supportée (PGS/VobSub)"
                )
                return
            app.subtitles_enabled = True
            pad_idx = int(track.get("index", 0))
            pipe = app._custom_pipeline
            was_playing = app.is_playing and not app.is_paused
            try:
                pipe.set_state(Gst.State.PAUSED)
                pipe.get_state(int(800 * Gst.MSECOND))
            except Exception: pass
            try:
                ov = getattr(app, "_custom_sub_overlay", None)
                if ov is not None:
                    try:
                        ov.set_property("wait-text", False)
                    except Exception:
                        pass
                    try:
                        ov.set_property("silent", True)
                    except Exception:
                        pass
                    try:
                        if ov.find_property("text") is not None:
                            ov.set_property("text", "")
                    except Exception:
                        pass
                if (
                    getattr(app, "_custom_sub_linked", False)
                    and getattr(app, "_custom_subtitle_index", -1) == pad_idx
                    and getattr(app, "_custom_ext_sub_src", None) is None
                ):
                    if ov is not None:
                        ov.set_property("silent", False)
                else:
                    app._set_custom_subtitle_active(pad_idx)
                    if ov is not None:
                        ov.set_property("silent", False)
            except Exception as e:
                print("custom sub select:", e, file=sys.stderr)
            # Flush obligatoire : sinon les nouveaux ST n'apparaissent qu'au prochain seek
            try:
                app._flush_pipeline_at_position(pipe)
            except Exception:
                pass
            if was_playing:
                try:
                    pipe.set_state(Gst.State.PLAYING)
                except Exception:
                    pass
            app.current_subtitle_index = track_list_index
            app.status_label.set_text(
                app.tr("Sous-titres") + " : " + track["label"]
            )
            return
        if track.get("kind") == "embedded":
            app.subtitles_enabled = True
            if app._active_external_sub_path:
                try:
                    app._clear_external_suburi()
                except Exception:
                    pass
            try:
                app.player.set_property(
                    "current-text", int(track["index"])
                )
            except Exception as e:
                print("current-text:", e, file=sys.stderr)
            # Ne pas lancer de seek FLUSH lors du changement de piste
            # playbin : il peut bloquer les threads de streaming.
            if DEBUG_MYOSTOOX:
                print(
                    "Myostoox: seek FLUSH ignoré après changement "
                    "de piste playbin",
                    file=sys.stderr,
                )
            app.current_subtitle_index = track_list_index
            app.status_label.set_text(
                app.tr("Sous-titres") + " : " + track["label"]
            )
            return
        if track.get("kind") == "external":
            path = track.get("path")
            if not path: return
            if app._custom_pipeline is not None:
                pipe = app._custom_pipeline
                was_playing = app.is_playing and not app.is_paused
                position = float(getattr(app, "_last_known_position", 0) or 0)
                try:
                    pipe.set_state(Gst.State.PAUSED)
                    pipe.get_state(int(2 * Gst.SECOND))
                except Exception: pass
                ok = False
                try:
                    ok = app._link_external_subtitle_custom(path)
                except Exception as e:
                    print("ext sub custom:", e, file=sys.stderr)
                try:
                    app._flush_pipeline_at_position(pipe, position if position > 0.15 else None)
                    if position > 0.15:
                        app._last_known_position = position
                except Exception as e:
                    print("ext sub seek:", e, file=sys.stderr)
                if was_playing:
                    try: pipe.set_state(Gst.State.PLAYING)
                    except Exception: pass
                if ok:
                    app.current_subtitle_index = track_list_index
                    app.status_label.set_text(
                        app.tr("Sous-titres") + " : " + track["label"]
                    )
                return
            if app._apply_external_suburi(path):
                app.current_subtitle_index = track_list_index
                app.status_label.set_text(
                    app.tr("Sous-titres") + " : " + track["label"]
                )
                GLib.timeout_add(250, app._deferred_refresh_subtitles)
            return
    except Exception as e:
        print("Erreur sélection sous-titres:", e, file=sys.stderr)

def set_subtitles_enabled(app, enabled):
    app.subtitles_enabled = bool(enabled)
    if not enabled:
        try:
            app._disable_all_subtitles()
        except Exception as e:
            print("disable subs:", e, file=sys.stderr)
def set_audio_track(app, index):
    if app._custom_pipeline is not None:
        app._set_custom_audio_track(index)
        return
    try:
        app.player.set_property("current-audio", index)
        app.current_audio_index = index
    except Exception as e:
        print("Erreur piste audio:", e, file=sys.stderr)

def _set_custom_audio_track(app, index):
    if not app._custom_audio_pads or app._custom_audio_queue is None:
        return
    if index < 0 or index >= len(app._custom_audio_pads):
        return
    if index == getattr(app, "_custom_audio_index", -1):
        return
    new_pad, label, new_media = app._custom_audio_entry(index)
    if new_pad is None:
        return
    cur = getattr(app, "_custom_audio_index", 0) or 0
    _, _, old_media = app._custom_audio_entry(cur)
    same_family = (
        app._custom_audio_media_family(old_media)
        == app._custom_audio_media_family(new_media)
        and bool(app._custom_audio_media_family(new_media))
    )
    # Codecs différents (ex. E-AC3 → AAC/DTS) : relinker sur le même
    # décodeur casse le son et peut figer la vidéo → reconstruction.
    if not same_family:
        app._restart_custom_with_audio_index(index)
        try:
            app.status_label.set_text(
                app.tr("Piste audio") + " : " + (label or str(index + 1))
            )
        except Exception:
            pass
        return
    try:
        qsink = app._custom_audio_queue.get_static_pad("sink")
        if qsink is None:
            return
        peer = qsink.get_peer()
        if peer is not None:
            peer.unlink(qsink)
        if new_pad.link(qsink) == Gst.PadLinkReturn.OK:
            app._custom_audio_index = index
            app.current_audio_index = index
            # Seek doux : éviter KEY_UNIT qui peut figer l'image
            try:
                pipe = app._custom_pipeline
                pos_ns = max(0, int(float(getattr(app, "_last_known_position", 0.0) or 0.0) * Gst.SECOND))
                if pipe is not None and pos_ns > 0:
                    app._seek_with_rate(
                        pipe,
                        Gst.SeekFlags.FLUSH,
                        pos_ns,
                    )
            except Exception:
                pass
            try:
                app.status_label.set_text(
                    app.tr("Piste audio") + " : " + (label or str(index + 1))
                )
            except Exception:
                pass
        else:
            if peer is not None:
                try:
                    peer.link(qsink)
                except Exception:
                    pass
            # Échec de lien → reconstruction complète
            app._restart_custom_with_audio_index(index)
    except Exception as e:
        print("Erreur bascule audio custom:", e, file=sys.stderr)
        try:
            app._restart_custom_with_audio_index(index)
        except Exception:
            pass

def _find_subtitle_list_index(app, kind, index=None, path=None):
    for i, t in enumerate(app.subtitle_tracks or []):
        if not isinstance(t, dict) or t.get("kind") != kind:
            continue
        if kind == "embedded" and t.get("index") == index:
            return i
        if kind == "external" and t.get("path") == path:
            return i
    return -1
