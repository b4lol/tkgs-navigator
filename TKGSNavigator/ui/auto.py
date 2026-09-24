"""Optional daily update in standby, off by default.

Runs only when "Daily automatic update" is on, the receiver is in standby and no recording
is running or due soon; otherwise it retries a few minutes later. The preview is applied
only when "Apply updates automatically" is also on and the preview is complete, applicable
and free of warnings; every apply is backed up and can be undone from the plugin screen.
Leaving standby cancels a running scan without touching the playback standby restores.
"""

import time

from Components.config import configfile
from enigma import eTimer

from . import controller as scan
from .checks import scan_candidates
from .config import device_resolver, plan_arguments, settings
from .signals import bind_signal, unbind_signal

IDLE_INTERVAL_MS = 5 * 60 * 1000
BUSY_INTERVAL_MS = 5 * 1000
RECORDING_MARGIN = 30 * 60


def due(now, hour, last_run):
    """True once the most recent occurrence of hour:00 local time is newer than last_run."""
    local = time.localtime(now)
    today = time.mktime(local[:3] + (hour, 0, 0, 0, 0, -1))
    latest = today if now >= today else today - 24 * 3600
    return last_run < latest


def in_standby():
    try:
        import Screens.Standby

        return Screens.Standby.inStandby is not None
    except Exception:
        return False


def recording_soon(session, now):
    if session.nav.getRecordings():
        return True
    try:
        upcoming = session.nav.RecordTimer.getNextRecordingTime()
    except Exception:
        return False
    return upcoming is not None and 0 < upcoming - now < RECORDING_MARGIN


class AutoUpdater:
    def __init__(self, session, clock=time.time):
        self.session = session
        self.clock = clock
        self.cfg = settings()
        self.controller = None
        self.result = None
        self.timer = eTimer()
        name = "callback" if hasattr(self.timer, "callback") else "timeout"
        self.binding = bind_signal(self.timer, name, self.tick)

    def start(self):
        self.timer.start(IDLE_INTERVAL_MS, False)

    def stop(self):
        self.timer.stop()
        unbind_signal(self.binding)
        self._release()

    def tick(self):
        now = self.clock()
        if self.controller is not None:
            if not in_standby():
                self.controller.release_playback()
                if not self.controller.cancel():
                    self._release()
            return
        if not self.cfg.auto_update.value:
            return
        if not due(now, self.cfg.auto_hour.value, self.cfg.last_auto_update.value):
            return
        if not in_standby() or recording_soon(self.session, now):
            return
        self.run()

    def run(self):
        self.controller = scan.ScanController(self.session, self, scan.CONFIG_DIR)
        try:
            candidates = scan_candidates(self.session, self.controller.lamedb, self.cfg)
        except ValueError:
            self._done()
            return
        self.timer.start(BUSY_INTERVAL_MS, False)
        self.controller.start(
            candidates,
            self.cfg.timeout.value,
            device_resolver(self.cfg, self.session, scan.playing_demux),
            plan_arguments(self.cfg),
        )

    # Controller listener -------------------------------------------------------------

    def status(self, message):
        pass

    def progress(self, event):
        pass

    def finished(self, operation, event, code):
        self.result = event
        if event.get("event") == "cancelled":
            # Interrupted by leaving standby: not done, try again at the next standby.
            self._release()
            self.timer.start(IDLE_INTERVAL_MS, False)
            return
        if (
            operation == "scan"
            and event.get("event") == "result"
            and self.cfg.auto_apply.value
            and event.get("can_apply")
            and not event.get("warnings")
            and in_standby()
            and self.controller.apply()
        ):
            return
        self._done()

    def _done(self):
        self.cfg.last_auto_update.value = int(self.clock())
        self.cfg.last_auto_update.save()
        configfile.save()
        self._release()
        self.timer.start(IDLE_INTERVAL_MS, False)

    def _release(self):
        if self.controller is not None:
            controller, self.controller = self.controller, None
            controller.close()
