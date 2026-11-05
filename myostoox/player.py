# -*- coding: utf-8 -*-
"""GStreamer engine helpers for Myostoox (app = window host)."""
from __future__ import annotations

import os
import sys

DEBUG_MYOSTOOX = bool(os.environ.get("MYOSTOOX_DEBUG", "").strip())

try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
except Exception:
    Gst = None

try:
    from gi.repository import GLib
except Exception:
    GLib = None

try:
    gi.require_version("GstVideo", "1.0")
    from gi.repository import GstVideo
except Exception:
    GstVideo = None

try:
    from myostoox.playlist import is_stream, is_cdda, is_dvd, is_bluray, is_special_source
except Exception:
    try:
        from playlist import is_stream, is_cdda, is_dvd, is_bluray, is_special_source
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

try:
    from myostoox.metadata import SUBTITLE_FONT_DESC
except Exception:
    try:
        from metadata import SUBTITLE_FONT_DESC
    except Exception:
        SUBTITLE_FONT_DESC = "DejaVu Sans Bold 12"

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



def gst_set_rank(name, rank):
    return _gst_set_rank(name, rank)

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


def create_playbin(name="playbin"):
    if Gst is None:
        return None
    try:
        return Gst.ElementFactory.make("playbin", name)
    except Exception:
        return None

# --- Engine (app = Myostoox window) ---

def _effective_perf_mode(app):
    cur = getattr(app, "current_path", None)
    if cur and cur == getattr(app, "_hw_fallback_path", None):
        return "software"
    return getattr(app, "performance_mode", "hardware")

def _should_use_custom_pipeline(app, path):
    if not isinstance(path, str): return False
    if is_stream(path) or is_cdda(path) or is_dvd(path) or is_bluray(path): return False
    if not os.path.isfile(path): return False
    if os.path.splitext(path)[1].lower() not in (".mkv", ".webm", ".mka"): return False
    # Uniquement AC3/E-AC3 ou AV1 : multi-pistes audio hétérogènes
    # (AAC/DTS/TrueHD + HEVC) restent sur playbin pour éviter un décodeur unique.
    if app._mkv_has_ac3_family_audio(path): return True
    if app._mkv_has_av1_video(path): return True
    return False

def _configure_playbin_buffers(app, audio_only=False):
    if app.player is None: return
    soft = getattr(app, "performance_mode", "hardware") == "software"
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
        try: app.player.set_property(prop, value)
        except Exception: pass
    try:
        flags = int(app.player.get_property("flags"))
        VIDEO = 1 << 0
        AUDIO = 1 << 1
        TEXT = 1 << 2
        VIS = 1 << 3
        DOWNLOAD = 1 << 7
        if audio_only:
            flags = (flags | AUDIO) & ~VIDEO & ~VIS & ~DOWNLOAD
        else:
            flags = (flags | VIDEO | AUDIO | TEXT) & ~VIS & ~DOWNLOAD
        app.player.set_property("flags", flags)
    except Exception: pass

def _active_pipeline(app):
    if app._custom_pipeline is not None: return app._custom_pipeline
    return app.player

def _detach_equalizer_for_custom(app):
    eq = getattr(app, "equalizer", None)
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
    app._need_playbin_rebind = True
    return eq

def _destroy_custom_pipeline(app):
    pipe = app._custom_pipeline
    if pipe is None: return
    try:
        bus = pipe.get_bus()
        for hid in getattr(app, "_custom_bus_handler_ids", []) or []:
            try: bus.disconnect(hid)
            except Exception: pass
        try: bus.remove_signal_watch()
        except Exception: pass
    except Exception: pass
    app._custom_bus_handler_ids = []
    try:
        pipe.set_state(Gst.State.NULL)
        pipe.get_state(100 * Gst.MSECOND)
    except Exception: pass
    for el in (getattr(app, "spectrum", None), getattr(app, "equalizer", None)):
        if el is None: continue
        try:
            parent = el.get_parent()
            if parent is not None:
                try: el.set_state(Gst.State.NULL)
                except Exception: pass
                parent.remove(el)
        except Exception: pass
    app._custom_pipeline = None
    app._custom_volume = None
    app._custom_audio_linked = False
    app._custom_video_linked = False
    app._custom_sub_linked = False
    app._custom_audio_pads = []
    app._custom_audio_queue = None
    app._custom_audio_index = 0
    app._custom_subtitle_pads = []
    app._custom_subtitle_index = -1
    app._custom_sub_queue = None
    app._custom_sub_overlay = None
    app._custom_ext_sub_src = None
    app._custom_ext_sub_q = None
    app._custom_ext_sub_parse = None
    app._custom_sub_muted = False
    app._need_playbin_rebind = True
    try: app._mkv_probe_cache = {}
    except Exception: pass
    try: GLib.idle_add(lambda: (trim_memory(force=True, full=True), False)[1])
    except Exception: pass
def _create_video_sink(app):
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
    soft = getattr(app, "performance_mode", "hardware") == "software"
    # Lateness large : après une longue pause, les 1ères frames sont
    # souvent « late » vs l'horloge → QoS trop strict = saccades 3–5 s.
    lateness = (5 if soft else 3) * Gst.SECOND
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

def _build_mkv_pipeline(app, filepath, force_software=False):
    pipe = Gst.Pipeline.new("mkv-local-pipeline")
    perf_mode = "software" if force_software else app._effective_perf_mode()
    filesrc = Gst.ElementFactory.make("filesrc", "mkv-src")
    demux = Gst.ElementFactory.make("matroskademux", "mkv-demux")
    if not filesrc or not demux: return None
    filesrc.set_property("location", os.path.abspath(filepath))
    pipe.add(filesrc)
    pipe.add(demux)
    if not filesrc.link(demux): return None
    app._custom_sub_lang_list = app._discover_subtitle_languages(filepath)
    try:
        app._custom_audio_label_list = app._discover_audio_track_labels(filepath)
    except Exception:
        app._custom_audio_label_list = []
    app._custom_audio_linked = False
    app._custom_video_linked = False
    app._custom_sub_linked = False
    app._custom_sub_wait_scheduled = False
    app._custom_volume = None
    app._custom_audio_pads = []
    app._custom_audio_queue = None
    app._custom_audio_index = 0
    app._custom_subtitle_pads = []
    app._custom_subtitle_index = -1
    app._custom_sub_muted = False
    app._custom_sub_queue = None
    app._custom_is_av1 = False
    custom_eq = app._detach_equalizer_for_custom()
    video_sink = app._create_video_sink()
    if video_sink is None:
        video_sink = Gst.ElementFactory.make("autovideosink", "mkv-vsink")
    app.video_sink = video_sink
    app._video_sink_is_real = video_sink is not None
    try:
        if video_sink is not None and video_sink.find_property("force-aspect-ratio"):
            video_sink.set_property("force-aspect-ratio", True)
    except Exception: pass
    _n_subs = (getattr(app, "_custom_sub_lang_list", None) or {}).get("__n__")
    need_overlay = (
        _n_subs is None
        or _n_subs > 0
        or bool(getattr(app, "external_subtitles", None))
        or bool(getattr(app, "_preferred_ext_sub_path", None))
    )
    sub_overlay = None
    app._custom_sub_overlay_is_text = True
    if need_overlay:
        sub_overlay = Gst.ElementFactory.make("textoverlay", "mkv-subov")
        if sub_overlay is None:
            sub_overlay = Gst.ElementFactory.make("subtitleoverlay", "mkv-subov")
            app._custom_sub_overlay_is_text = False
    app._custom_sub_overlay = sub_overlay
    if sub_overlay is not None:
        pipe.add(sub_overlay)
        for prop, val in (
            ("shaded-background", False),
            ("draw-shadow", True),
            ("draw-outline", True),
            ("font-desc", SUBTITLE_FONT_DESC),
            ("valignment", 1),
            ("halignment", 1),
            ("line-alignment", 1),
            ("silent", True),
            ("wait-text", False),
            ("deltay", -12),
        ):
            try: sub_overlay.set_property(prop, val)
            except Exception: pass
        for prop, val in (
            ("valignment", "bottom"),
            ("halignment", "center"),
            ("font-desc", SUBTITLE_FONT_DESC),
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
            soft = perf_mode == "software"
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
                    q.set_property("leaky", 2)
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
        mode = perf_mode
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
        mode = perf_mode
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
        mode = perf_mode
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
        if perf_mode == "hardware":
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
        if perf_mode == "hardware":
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
        if perf_mode == "hardware":
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
        if media.startswith("video/") and not app._custom_video_linked:
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
                app._custom_is_av1 = True
            _tune_queue(queue, True, for_av1=(is_av1 or is_h265))
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
                smooth_q = Gst.ElementFactory.make("queue", "mkv-vsmooth")
                if smooth_q is not None:
                    try:
                        smooth_q.set_property(
                            "max-size-buffers",
                            12 if (is_av1 or is_h265) else 6,
                        )
                        smooth_q.set_property("max-size-bytes", 0)
                        smooth_q.set_property("max-size-time", 0)
                        smooth_q.set_property("leaky", 0)
                    except Exception:
                        pass
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
                    if getattr(app, "_custom_sub_overlay_is_text", True):
                        if not tail.link(sub_overlay):
                            app._link_through_video_color(
                                pipe, tail, video_sink
                            )
                        else:
                            app._link_through_video_color(
                                pipe, sub_overlay, video_sink
                            )
                    else:
                        try:
                            vpad = sub_overlay.get_static_pad("video_sink")
                            if vpad is not None:
                                tail.get_static_pad("src").link(vpad)
                                app._link_through_video_color(
                                    pipe, sub_overlay, video_sink
                                )
                            else:
                                app._link_through_video_color(
                                    pipe, tail, video_sink
                                )
                        except Exception:
                            app._link_through_video_color(
                                pipe, tail, video_sink
                            )
                else:
                    app._link_through_video_color(pipe, tail, video_sink)
                app._custom_video_linked = True
                if DEBUG_MYOSTOOX:
                    print(
                    "Myostoox: MKV vidéo (%s)" % label, file=sys.stderr
                )
                try: GLib.idle_add(app._ensure_video_sink_geometry)
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
                            app, "_custom_sub_overlay_is_text", True
                        ):
                            if convert.link(sub_overlay):
                                app._link_through_video_color(
                                    pipe, sub_overlay, video_sink
                                )
                            else:
                                app._link_through_video_color(
                                    pipe, convert, video_sink
                                )
                        elif sub_overlay is not None:
                            try:
                                vpad = sub_overlay.get_static_pad("video_sink")
                                convert.get_static_pad("src").link(vpad)
                                app._link_through_video_color(
                                    pipe, sub_overlay, video_sink
                                )
                            except Exception:
                                app._link_through_video_color(
                                    pipe, convert, video_sink
                                )
                        else:
                            app._link_through_video_color(
                                pipe, convert, video_sink
                            )
                except Exception as e:
                    if DEBUG_MYOSTOOX:
                        print("mkv video:", e, file=sys.stderr)
            decode.connect("pad-added", on_vpad)
            app._custom_video_linked = True
            if DEBUG_MYOSTOOX:
                print("Myostoox: MKV vidéo (decodebin)", file=sys.stderr)
            try: GLib.idle_add(app._ensure_video_sink_geometry)
            except Exception: pass
            return
        if media.startswith("audio/"):
            idx = len(app._custom_audio_pads)
            lang = _pad_language(pad, struct)
            nice = None
            try:
                alist = getattr(app, "_custom_audio_label_list", None) or []
                if idx < len(alist) and alist[idx]:
                    nice = alist[idx]
            except Exception:
                nice = None
            if not nice:
                if lang:
                    nice = "%s — %s%d" % (
                        lang, app.tr("Piste "), idx + 1
                    )
                else:
                    nice = app.tr("Piste ") + str(idx + 1)
            elif lang and lang not in nice:
                nice = "%s — %s" % (lang, nice)
            app._custom_audio_pads.append((pad, nice, media or ""))
            app.audio_tracks = [
                (i, lab) for i, entry in enumerate(app._custom_audio_pads)
                for lab in (entry[1],)
            ]
            if app._custom_audio_linked: return
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
            is_av1_pipe = bool(getattr(app, "_custom_is_av1", False))
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
            app._custom_audio_queue = queue
            app._custom_volume_boost = (
                2.4 if is_eac3 else (1.6 if is_ac3 else 1.0)
            )
            if volume is not None:
                try:
                    vol = float(app.volume_scale.get_value())
                    boost = float(getattr(app, "_custom_volume_boost", 1.0) or 1.0)
                    gain = 0.0 if getattr(app, "is_muted", False) else min(
                        3.5, vol * boost
                    )
                    volume.set_property("volume", gain)
                except Exception: pass
                app._custom_volume = volume
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
            app._custom_audio_linked = True
            app._custom_audio_index = 0
            app.current_audio_index = 0
            if DEBUG_MYOSTOOX:
                print("Myostoox: MKV audio (%s)" % alabel, file=sys.stderr)
            return
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
                lang = (getattr(app, "_custom_sub_lang_list", None) or {}).get(sid)
        lang = app._normalize_lang_code(lang) if lang else None
        sidx = len(app._custom_subtitle_pads)
        if lang:
            nice = lang
        elif is_bitmap_sub:
            nice = "Sub %d (image)" % (sidx + 1)
        else:
            nice = "Sub %d" % (sidx + 1)
        app._custom_subtitle_pads.append((pad, nice, is_text_sub))
        track = {
            "kind": "embedded",
            "index": sidx,
            "path": None,
            "label": nice,
            "text": bool(is_text_sub),
        }
        def _append_track(t=track, si=sidx):
            try:
                app.subtitle_tracks = list(app.subtitle_tracks or [])
                if not any(
                    isinstance(x, dict)
                    and x.get("kind") == "embedded"
                    and x.get("index") == si
                    for x in app.subtitle_tracks
                ):
                    app.subtitle_tracks.append(t)
                    if (
                        getattr(app, "_custom_sub_linked", False)
                        and getattr(app, "_custom_subtitle_index", -1) == si
                        and not getattr(app, "_active_external_sub_path", None)
                    ):
                        app.current_subtitle_index = len(app.subtitle_tracks) - 1
            except Exception: pass
            return False
        GLib.idle_add(_append_track)
        GLib.timeout_add(
            400, app._delayed_subtitle_lang_probe, pad, sidx
        )
        GLib.timeout_add(
            1200, app._delayed_subtitle_lang_probe, pad, sidx
        )
        return
    demux.connect("pad-added", on_pad_added)
    return pipe

def _play_file_custom_mkv(app, filename):
    app._destroy_custom_pipeline()
    try:
        app.player.set_state(Gst.State.NULL)
        app.player.get_state(Gst.SECOND)
    except Exception: pass
    try: app._apply_safe_audio_decoder_ranks()
    except Exception: pass
    if DEBUG_MYOSTOOX:
        print(
        "Myostoox: pipeline MKV custom",
        filename,
        getattr(app, "performance_mode", "?"),
        file=sys.stderr,
    )
    if app.current_is_video:
        app._ensure_video_sink_geometry()
    pipe = app._build_mkv_pipeline(filename)
    if pipe is None: return False
    app._custom_pipeline = pipe
    app._attach_custom_bus(pipe)
    if app.current_is_video:
        app._ensure_video_sink_geometry()
        try:
            sink = getattr(app, "video_sink", None)
            handle = getattr(app, "_video_window_handle", None)
            if sink is not None and handle and isinstance(sink, GstVideo.VideoOverlay):
                GstVideo.VideoOverlay.set_window_handle(sink, handle)
        except Exception: pass
    ret = pipe.set_state(Gst.State.PAUSED)
    if ret == Gst.StateChangeReturn.FAILURE:
        app._destroy_custom_pipeline()
        return False
    try: pipe.get_state(3 * Gst.SECOND)
    except Exception: pass
    if app.current_is_video:
        app._cache_video_window_handle()
        app._apply_video_overlay_handle()
        app._nudge_video_redraw()
    ret = pipe.set_state(Gst.State.PLAYING)
    if ret == Gst.StateChangeReturn.FAILURE:
        app._destroy_custom_pipeline()
        if app._effective_perf_mode() == "hardware":
            pipe2 = app._build_mkv_pipeline(filename, force_software=True)
            if pipe2 is None: return False
            app._custom_pipeline = pipe2
            app._attach_custom_bus(pipe2)
            if app.current_is_video:
                app._cache_video_window_handle()
                app._apply_video_overlay_handle()
            pipe2.set_state(Gst.State.PAUSED)
            try: pipe2.get_state(3 * Gst.SECOND)
            except Exception: pass
            if app.current_is_video:
                app._cache_video_window_handle()
                app._apply_video_overlay_handle()
            if pipe2.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                app._destroy_custom_pipeline()
                return False
            return True
        return False
    return True

