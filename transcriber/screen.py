"""Screen video recorded alongside the audio, through the desktop's screen-cast portal.

The portal (``org.freedesktop.portal.ScreenCast``) lets the user pick a
monitor or window and hands back a PipeWire stream. GStreamer encodes that
stream to WebM for as long as the audio recorder runs. The finished video is
kept next to the recording as ``<name>.screen.webm``. Its container tag
``SIT_AUDIO_OFFSET`` holds the audio second at which the video's first moment
plays, so a trim of the audio can move the video's start without re-encoding.

Linux only, and optional: without the portal, GStreamer's PipeWire and VP8
plugins or the ``jeepney`` D-Bus library the option is simply not offered.
"""

import json
import logging
import os
import secrets
import shutil
import signal
import subprocess
import threading
import time

from .config import DATA_DIR, SCRATCH_DIR
from .errors import AppError

log = logging.getLogger(__name__)

SCRATCH_VIDEO = SCRATCH_DIR / "current.screen.webm"
# Written when the video starts, so a recording stopped by the watchdog and
# saved later still knows how the video lines up with the audio.
SCRATCH_OFFSET = SCRATCH_DIR / "current.screen.offset"
SCRATCH_LOG = SCRATCH_DIR / "current.screen.log"
# The portal hands one out per approved share. Passing it back next time skips
# the picker, so the user chooses the screen once.
RESTORE_TOKEN = DATA_DIR / ".screencast-token"
OFFSET_TAG = "SIT_AUDIO_OFFSET"

FRAME_RATE = 5
# A keyframe every two seconds: a trim then starts the video on a keyframe at
# most two seconds before the cut instead of re-encoding it.
KEYFRAME_FRAMES = 2 * FRAME_RATE
BITRATE = 1_000_000
PICK_TIMEOUT = 180
STOP_TIMEOUT = 15
GST_ELEMENTS = ("pipewiresrc", "videoconvert", "videorate", "vp8enc", "webmmux")

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"
SOURCE_MONITOR, SOURCE_WINDOW = 1, 2
CURSOR_EMBEDDED = 2
PERSIST_UNTIL_REVOKED = 2


def screen_path_for(wav_path):
    return wav_path.with_name(f"{wav_path.stem}.screen.webm")


_tools_present = None


def _tools_available():
    global _tools_present
    if _tools_present is None:
        try:
            import jeepney  # noqa: F401
        except ImportError:
            _tools_present = False
        else:
            _tools_present = bool(shutil.which("gst-launch-1.0")) and all(
                subprocess.run(["gst-inspect-1.0", "--exists", element]).returncode == 0
                for element in GST_ELEMENTS
            )
    return _tools_present


def available():
    """True when this desktop can share its screen with SIT."""
    if not _tools_available():
        return False
    try:
        portal = _Portal()
    except Exception as exc:  # no session bus, or jeepney cannot reach it
        log.info("Screen recording unavailable: %s", exc)
        return False
    try:
        return bool(portal.property("AvailableSourceTypes") & SOURCE_MONITOR)
    except Exception as exc:  # no portal, or one without ScreenCast
        log.info("Screen recording unavailable: %s", exc)
        return False
    finally:
        portal.close()


class _Portal:
    """A session-bus connection to the screen-cast portal.

    The portal ends a screen-cast session when the connection that opened it
    closes, so one connection lives as long as the recording.
    """

    def __init__(self):
        from jeepney import DBusAddress
        from jeepney.io.blocking import open_dbus_connection

        self.conn = open_dbus_connection(bus="SESSION", enable_fds=True)
        self.address = DBusAddress(PORTAL_PATH, bus_name=PORTAL_BUS, interface=SCREENCAST)

    def close(self):
        self.conn.close()

    def property(self, name):
        from jeepney import Properties
        from jeepney.wrappers import unwrap_msg

        _, value = unwrap_msg(self.conn.send_and_get_reply(Properties(self.address).get(name)))[0]
        return value

    def call(self, method, signature, *args, address=None):
        from jeepney import new_method_call
        from jeepney.wrappers import unwrap_msg

        message = new_method_call(address or self.address, method, signature, args)
        return unwrap_msg(self.conn.send_and_get_reply(message))

    def request(self, method, signature, *args, options, timeout=30):
        """Call a portal method that answers later through a Request object."""
        from jeepney import MatchRule, message_bus
        from jeepney.io.blocking import Proxy

        token = f"sit_{secrets.token_hex(8)}"
        sender = self.conn.unique_name.lstrip(":").replace(".", "_")
        rule = MatchRule(
            type="signal", interface="org.freedesktop.portal.Request", member="Response",
            path=f"{PORTAL_PATH}/request/{sender}/{token}",
        )
        Proxy(message_bus, self.conn).AddMatch(rule)
        with self.conn.filter(rule) as responses:
            self.call(method, signature, *args, {**options, "handle_token": ("s", token)})
            code, results = self.conn.recv_until_filtered(responses, timeout=timeout).body
        return code, {key: value for key, (_, value) in results.items()}


class _Capture:
    def __init__(self, portal, session, node, fd):
        self.portal, self.session, self.node, self.fd = portal, session, node, fd
        self.proc = None

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            from jeepney import DBusAddress

            session = DBusAddress(self.session, bus_name=PORTAL_BUS, interface="org.freedesktop.portal.Session")
            self.portal.call("Close", "", address=session)
        except Exception:  # closing the connection ends the session anyway
            pass
        self.portal.close()


_lock = threading.Lock()
_capture = None


def _saved_token():
    try:
        return RESTORE_TOKEN.read_text().strip() or None
    except OSError:
        return None


def choose():
    """Ask the portal for a screen to record. Blocks while the user picks one.

    Nothing is recorded yet: the video starts with :func:`begin`, once the
    audio is running, so the picker's wait does not end up in the video.
    """
    global _capture
    if not forget_leftovers():
        raise AppError("Screen recording is already running.")
    try:
        portal = _Portal()
    except Exception as exc:
        raise AppError(f"Could not reach the desktop's screen sharing: {exc}") from exc
    try:
        code, results = portal.request(
            "CreateSession", "a{sv}",
            options={"session_handle_token": ("s", f"sit_{secrets.token_hex(8)}")},
        )
        if code != 0:
            raise AppError("The desktop refused to start screen sharing.")
        session = results["session_handle"]
        sources = {
            "types": ("u", portal.property("AvailableSourceTypes") & (SOURCE_MONITOR | SOURCE_WINDOW)),
            "multiple": ("b", False),
        }
        if portal.property("AvailableCursorModes") & CURSOR_EMBEDDED:
            sources["cursor_mode"] = ("u", CURSOR_EMBEDDED)
        if portal.property("version") >= 4:
            sources["persist_mode"] = ("u", PERSIST_UNTIL_REVOKED)
            token = _saved_token()
            if token:
                sources["restore_token"] = ("s", token)
        code, _ = portal.request("SelectSources", "oa{sv}", session, options=sources)
        if code != 0:
            raise AppError("The desktop refused to share the screen.")
        code, results = portal.request("Start", "osa{sv}", session, "", options={}, timeout=PICK_TIMEOUT)
        if code != 0:
            raise AppError("Screen sharing was cancelled.")
        if results.get("restore_token"):
            RESTORE_TOKEN.write_text(results["restore_token"])
        node = results["streams"][0][0]
        fd = portal.call("OpenPipeWireRemote", "oa{sv}", session, {})[0].to_raw_fd()
    except AppError:
        portal.close()
        raise
    except TimeoutError as exc:
        portal.close()
        raise AppError("No screen was chosen in time.") from exc
    except Exception as exc:
        portal.close()
        raise AppError(f"Could not start screen sharing: {exc}") from exc
    with _lock:
        _capture = _Capture(portal, session, node, fd)


def begin(audio_started_at):
    """Start encoding the chosen screen. ``audio_started_at`` is the audio's wall-clock start."""
    with _lock:
        capture = _capture
        if capture is None:
            return
        SCRATCH_VIDEO.unlink(missing_ok=True)
        with SCRATCH_LOG.open("w") as errors:
            capture.proc = subprocess.Popen(
                [
                    "gst-launch-1.0", "-e", "-q",
                    "pipewiresrc", f"fd={capture.fd}", f"path={capture.node}",
                    "do-timestamp=true", "keepalive-time=1000",
                    "!", "videoconvert", "!", "videorate",
                    "!", f"video/x-raw,framerate={FRAME_RATE}/1",
                    "!", "vp8enc", "deadline=1", "cpu-used=8", "end-usage=vbr",
                    f"target-bitrate={BITRATE}", f"keyframe-max-dist={KEYFRAME_FRAMES}", "threads=2",
                    "!", "webmmux",
                    "!", "filesink", f'location="{SCRATCH_VIDEO}"',
                ],
                pass_fds=(capture.fd,), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=errors,
            )
        started = time.time()
        os.close(capture.fd)
        capture.fd = None
        try:
            # A pipeline that cannot negotiate with the stream quits at once.
            capture.proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            SCRATCH_OFFSET.write_text(json.dumps(round(started - audio_started_at, 3)))
            return
    detail = _log_tail()
    discard()
    raise AppError(f"Screen recording failed to start: {detail or 'GStreamer quit.'}")


def stop():
    """Finish the running video, if any. The file stays in scratch until saved."""
    global _capture
    with _lock:
        capture, _capture = _capture, None
    if capture is None:
        return
    proc = capture.proc
    if proc is not None and proc.poll() is None:
        # -e turns the interrupt into end-of-stream, so the WebM gets its index.
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    if proc is not None and proc.returncode not in (0, None):
        log.warning("Screen recording ended with an error: %s", _log_tail())
    capture.close()


def _remove_scratch():
    for path in (SCRATCH_VIDEO, SCRATCH_OFFSET, SCRATCH_LOG):
        path.unlink(missing_ok=True)


def forget_leftovers():
    """Drop a video left from an earlier, unsaved take. False while one is running."""
    with _lock:
        if _capture is not None:
            return False
        _remove_scratch()
    return True


def discard():
    """Stop any capture and forget its video."""
    stop()
    _remove_scratch()


def _log_tail():
    try:
        return SCRATCH_LOG.read_text(errors="replace").strip()[-500:]
    except OSError:
        return ""


def store(wav_path):
    """Move the finished scratch video next to ``wav_path``. Returns its path or None."""
    try:
        if not SCRATCH_VIDEO.is_file() or SCRATCH_VIDEO.stat().st_size == 0:
            return None
        lead = float(json.loads(SCRATCH_OFFSET.read_text()))
        target = screen_path_for(wav_path)
        _remux(SCRATCH_VIDEO, target, lead)
        return target
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        log.warning("The screen video could not be saved: %s", exc)
        return None
    finally:
        discard()


def _remux(source, target, lead, cut=None, length=None):
    """Copy a WebM without re-encoding, rewriting its index and offset tag."""
    tmp = target.with_name(f".{target.name}.tmp")
    command = ["ffmpeg", "-y", "-v", "error"]
    if cut is not None:
        command += ["-ss", f"{cut:.3f}"]
    command += ["-i", str(source)]
    if length is not None:
        command += ["-t", f"{length:.3f}"]
    command += ["-map", "0:v", "-c", "copy", "-metadata", f"{OFFSET_TAG}={lead:.3f}", "-f", "webm", str(tmp)]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def describe(video_path):
    """``(lead, duration)`` of a stored screen video, in seconds."""
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", f"format=duration:format_tags={OFFSET_TAG}",
            "-of", "json", str(video_path),
        ],
        check=True, capture_output=True, text=True,
    )
    fmt = json.loads(probe.stdout)["format"]
    tags = {key.upper(): value for key, value in (fmt.get("tags") or {}).items()}
    return float(tags.get(OFFSET_TAG, 0)), float(fmt.get("duration", 0))


def _keyframes(video_path):
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", str(video_path),
        ],
        check=True, capture_output=True, text=True,
    )
    keys = []
    for line in probe.stdout.splitlines():
        time_text, _, flags = line.partition(",")
        if "K" in flags and time_text not in ("", "N/A"):
            keys.append(float(time_text))
    return keys


def trim(video_path, start, end):
    """Follow an audio trim to ``start..end`` seconds. None when there is no video.

    The video is cut on the last keyframe before the new start, not re-encoded.
    Its offset tag absorbs the difference, so it stays in step with the audio.
    A video that lay entirely in the removed parts is deleted.
    """
    if not video_path.is_file():
        return None
    try:
        lead, duration = describe(video_path)
        first, last = start - lead, end - lead
        if last <= 0 or first >= duration:
            video_path.unlink()
            return False
        cut = max((key for key in _keyframes(video_path) if key <= max(first, 0.0)), default=0.0)
        _remux(video_path, video_path, cut + lead - start, cut=cut, length=last - cut)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise AppError(f"The audio was trimmed, but the screen video could not follow: {exc}") from exc
    return True
