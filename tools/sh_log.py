"""
sh_log.py - lightweight progress + logging for the SquadHeight exporters.
=========================================================================

Why this exists
---------------
A headless export runs the Unreal commandlet with the engine streaming its
*entire* log to the console (``-stdout -FullStdOutLogOutput``): thousands of
asset / shader / streaming lines that bury the handful a human actually wants.
This module gives the export scripts two clean, SEPARATE channels instead:

  * CONSOLE  - short plain-English "which phase is running and how far along",
               plus one in-place progress bar. Written straight to the
               process's real stdout file descriptor (``os.write(1, ...)``),
               which BYPASSES the Unreal Python plugin's redirection of
               ``sys.stdout`` to the engine log. That is what lets these lines
               show on the .bat console even though the runners no longer pass
               ``-stdout`` (so the engine firehose stays off the console).
  * LOG FILE - every line (the console lines AND verbose ``detail`` lines)
               appended to ``<output>/logs/squadheight_<date>.log`` - the file
               you open to troubleshoot a run. The full engine log still lands
               in the project's ``Saved/Logs`` exactly as before.

When ``unreal`` is importable, ``detail``/milestone lines are ALSO mirrored to
``unreal.log`` so the engine-side ``Saved/Logs`` keeps full context right next
to any engine error.

Pure standard library; safe to import outside the editor (offline tools and
the self-test stub ``unreal`` away). Set ``SQUADHEIGHT_VERBOSE=1`` to also echo
the verbose ``detail`` lines to the console while a run is happening.

Console text is kept strictly ASCII (no box-drawing / Unicode) so it renders
correctly under the default Windows console code page.
"""

import os
import sys
import time

try:
    import unreal  # only present when running inside the editor
except Exception:  # pragma: no cover - exercised only outside the editor
    unreal = None


def _utc_stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def _clock():
    return time.strftime("%H:%M:%S", time.localtime())


def fmt_duration(seconds):
    """'4m54s', '12s', '1h03m' - compact human duration for log lines."""
    seconds = int(round(seconds))
    if seconds < 60:
        return "%ds" % seconds
    if seconds < 3600:
        return "%dm%02ds" % (seconds // 60, seconds % 60)
    return "%dh%02dm" % (seconds // 3600, (seconds % 3600) // 60)


def fmt_count(n):
    """'16.7M', '4.1k', '320' - compact human count for log lines."""
    if n >= 1_000_000:
        return "%.1fM" % (n / 1_000_000.0)
    if n >= 1_000:
        return "%.1fk" % (n / 1_000.0)
    return str(int(n))


class Reporter(object):
    """
    Two-channel reporter. Methods, by where the line goes:

        phase(msg)  - console + file + unreal   (a new top-level stage)
        step(msg)   - console + file + unreal   (a sub-step under a phase)
        detail(msg) - file + unreal only        (verbose; console only if
                                                  verbose=True)
        warn(msg)   - console + file + unreal
        error(msg)  - console + file + unreal
        progress(i, n, suffix) - in-place bar on the console; coarse
                                  breadcrumbs to the file. Never touches unreal
                                  (it would flood Saved/Logs).
    """

    def __init__(self, log_path=None, verbose=False):
        self.log_path = None
        self.verbose = verbose
        self._fh = None
        # In-place progress-bar bookkeeping.
        self._bar_active = False
        self._bar_len = 0
        self._last_bar = 0.0
        self._last_file_progress = 0.0
        # Is stdout a real terminal? When the .bat output is redirected to a
        # file, \r in-place updates would just produce noise, so we fall back
        # to occasional full lines instead.
        try:
            self._tty = os.isatty(1)
        except Exception:
            self._tty = False
        if log_path:
            self._open_log(log_path)

    # ------------------------------------------------------------------ file
    def _open_log(self, path):
        try:
            d = os.path.dirname(path)
            if d and not os.path.isdir(d):
                os.makedirs(d)
            # Append: a multi-process batch (the .bat relaunch loop, one editor
            # per map) keeps adding to the same day's file instead of leaving
            # 25 fragments. Runs are sequential, so no concurrent writers.
            self._fh = open(path, "a", encoding="utf-8")
            self.log_path = path
        except Exception:
            self._fh = None

    def _to_file(self, text):
        if self._fh is not None:
            try:
                self._fh.write(_utc_stamp() + "  " + text + "\n")
                self._fh.flush()
            except Exception:
                pass

    def rule(self):
        """Write a separator to the file only (marks a new session)."""
        self._to_file("=" * 70)

    # --------------------------------------------------------------- console
    def _to_console(self, text, newline=True):
        """
        Write to the real stdout fd, bypassing unreal's sys.stdout redirect.
        Falls back to the original interpreter stdout if fd 1 is unusable.
        """
        payload = text + ("\n" if newline else "")
        try:
            os.write(1, payload.encode("utf-8", "replace"))
            return
        except Exception:
            pass
        try:
            s = sys.__stdout__
            if s is not None:
                s.write(payload)
                s.flush()
        except Exception:
            pass

    def _break_bar(self):
        """End any in-place progress line so the next line starts fresh."""
        if self._bar_active:
            self._to_console("", newline=True)
            self._bar_active = False
            self._bar_len = 0

    def _mirror(self, msg, error=False, warning=False):
        if unreal is None:
            return
        try:
            if error:
                unreal.log_error("[SquadHeight] " + msg)
            elif warning:
                unreal.log_warning("[SquadHeight] " + msg)
            else:
                unreal.log("[SquadHeight] " + msg)
        except Exception:
            pass

    # ------------------------------------------------------------------ API
    def phase(self, msg):
        self._break_bar()
        self._to_console("[%s] ==> %s" % (_clock(), msg))
        self._to_file("==> " + msg)
        self._mirror(msg)

    def step(self, msg):
        self._break_bar()
        self._to_console("[%s]     %s" % (_clock(), msg))
        self._to_file("    " + msg)
        self._mirror("  " + msg)

    def detail(self, msg):
        self._to_file("    . " + msg)
        if self.verbose:
            self._break_bar()
            self._to_console("[%s]     . %s" % (_clock(), msg))
        self._mirror(msg)

    def warn(self, msg):
        self._break_bar()
        self._to_console("[%s] !   %s" % (_clock(), msg))
        self._to_file("WARNING: " + msg)
        self._mirror(msg, warning=True)

    def error(self, msg):
        self._break_bar()
        self._to_console("[%s] XX  %s" % (_clock(), msg))
        self._to_file("ERROR: " + msg)
        self._mirror(msg, error=True)

    def progress(self, current, total, suffix="", min_interval=0.25):
        """
        Update the in-place console progress bar (throttled to min_interval),
        and drop a coarse breadcrumb into the log file every few seconds so a
        post-mortem can still see how the scan paced itself.
        """
        if total <= 0:
            return
        frac = current / float(total)
        if frac < 0.0:
            frac = 0.0
        elif frac > 1.0:
            frac = 1.0
        now = time.time()
        is_last = current >= total
        pct = int(frac * 100 + 0.5)

        if self._tty:
            if is_last or (now - self._last_bar) >= min_interval:
                width = 24
                filled = int(width * frac + 0.5)
                bar = "#" * filled + "-" * (width - filled)
                line = "    [%s] %3d%%  %s" % (bar, pct, suffix)
                pad = self._bar_len - len(line)
                self._to_console(
                    "\r" + line + (" " * pad if pad > 0 else ""), newline=False)
                self._bar_active = True
                self._bar_len = len(line)
                self._last_bar = now
        else:
            # Not a terminal (redirected): periodic full lines, no \r.
            if is_last or (now - self._last_bar) >= 5.0:
                self._to_console("    %3d%%  %s" % (pct, suffix))
                self._last_bar = now

        if is_last or (now - self._last_file_progress) >= 5.0:
            self._to_file("progress %3d%%  %s" % (pct, suffix))
            self._last_file_progress = now

        if is_last:
            self._break_bar()

    def end_progress(self):
        self._break_bar()

    def close(self):
        self._break_bar()
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None


# ============================================================================
# Module-level singleton: the export scripts share one reporter so batch and
# per-map logging land in the same session file.
# ============================================================================
_GLOBAL = None


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _session_log_path(output_root):
    return os.path.join(
        output_root, "logs",
        "squadheight_%s.log" % time.strftime("%Y%m%d", time.gmtime()))


def _verbose_env():
    return bool(os.environ.get("SQUADHEIGHT_VERBOSE"))


def get():
    """Return the active reporter, lazily creating one with a default file."""
    global _GLOBAL
    if _GLOBAL is None:
        _GLOBAL = Reporter(log_path=_session_log_path(
            os.path.join(_repo_root(), "output")), verbose=_verbose_env())
    return _GLOBAL


def start_session(output_root, title=None):
    """
    (Re)point the shared reporter at <output_root>/logs/ and announce it.
    Call this once at the top of a batch / standalone export.
    """
    global _GLOBAL
    path = _session_log_path(output_root)
    if _GLOBAL is None or _GLOBAL.log_path != path:
        if _GLOBAL is not None:
            _GLOBAL.close()
        _GLOBAL = Reporter(log_path=path, verbose=_verbose_env())
    _GLOBAL.rule()
    if title:
        _GLOBAL.phase(title)
    _GLOBAL.step("Detailed log: %s" % path)
    return _GLOBAL


def ensure_session(output_root, title=None):
    """Start a session only if one is not already active (then reuse it)."""
    if _GLOBAL is None or _GLOBAL.log_path is None:
        return start_session(output_root, title)
    return _GLOBAL
