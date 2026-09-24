import importlib
import json
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

from tests.helpers import LAMEDB4, LAMEDB_TWO_TRANSPONDERS, sample_sections
from TKGSNavigator.core.lamedb import ServiceDatabase
from TKGSNavigator.core.sections import TableCollector
from TKGSNavigator.core.workflow import preview

UI_MODULES = ("screen", "controller", "config", "checks", "skin", "auto")


class Widget:
    def __init__(self, value=None, **kwargs):
        self.text = value
        self.list = value if isinstance(value, list) else []
        self.instance = self
        self.moveUp, self.moveDown = -1, 1

    def setText(self, value):
        self.text = value

    def setList(self, value):
        self.list = value

    def setRange(self, value):
        pass

    def setValue(self, value):
        self.value = value

    def handleKey(self, key):
        self.key = key

    def moveSelection(self, value):
        self.movement = value


class FakeScreen:
    def __init__(self, session):
        self.session = session
        self.widgets = {}
        self.onClose = []
        self.onFirstExecBegin = []
        self.closed = False

    def __setitem__(self, name, value):
        self.widgets[name] = value

    def __getitem__(self, name):
        return self.widgets[name]

    def close(self):
        self.closed = True
        for callback in self.onClose:
            callback()


class Setting:
    def __init__(self, default=None, **kwargs):
        self.value = default

    def save(self):
        pass


class Container:
    def __init__(self):
        self.dataAvail, self.appClosed = [], []
        self.command = None
        self.killed = False

    def execute(self, command):
        self.command = command
        return 0

    def kill(self):
        self.killed = True
        for callback in self.appClosed[:]:
            callback(137)


class Timer:
    def __init__(self):
        self.callback = []
        self.running = False
        self.interval = None

    def start(self, interval, *args):
        self.running, self.interval = True, interval

    def stop(self):
        self.running = False


class Reference:
    def __init__(self, text):
        self.text = text

    def toString(self):
        return self.text

    def toCompareString(self):
        return self.text.split("::")[0] + ":" if "::" in self.text else self.text


class Navigation:
    def __init__(self):
        self.current = Reference("original-service")
        self.recordings = []
        self.locked = True
        self.demux = None
        self.stopped = 0
        self.RecordTimer = types.SimpleNamespace(getNextRecordingTime=lambda: -1)

    def getCurrentlyPlayingServiceReference(self):
        return self.current

    def playService(self, ref):
        self.current = ref
        return 0

    def stopService(self):
        self.stopped += 1
        self.current = None

    def getRecordings(self):
        return self.recordings

    def getCurrentService(self):
        return types.SimpleNamespace(
            frontendInfo=lambda: types.SimpleNamespace(
                getFrontendStatus=lambda: {"tuner_locked": self.locked}
            ),
            stream=lambda: types.SimpleNamespace(getStreamingData=lambda: {"demux": self.demux}),
        )


class Session:
    def __init__(self, nav):
        self.nav = nav
        self.opened = []
        self.questions = []

    def open(self, screen, *args):
        self.opened.append(screen)

    def openWithCallback(self, callback, screen, *args):
        self.questions.append((callback, screen, args))


class SatelliteParameters:
    FEC_Auto, Inversion_Unknown, System_DVB_S, Modulation_QPSK = 0, 2, 0, 1
    RollOff_alpha_0_35, Pilot_Unknown = 0, 2


class FrontendParameters:
    def setDVBS(self, parameters):
        self.satellite = parameters


def result_event(collector=None):
    if collector is None:
        collector = TableCollector()
        for raw in sample_sections():
            collector.add(raw)
    return dict(event="result", **preview(collector, ServiceDatabase.parse(LAMEDB4)))


class EnigmaTestCase(unittest.TestCase):
    """Imports the UI package against minimal fakes of the Enigma2 modules it uses."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "lamedb").write_text(LAMEDB4, encoding="utf-8")
        self.standby = types.SimpleNamespace(inStandby=None)
        modules = {}

        def module(name, **values):
            result = types.ModuleType(name)
            result.__dict__.update(values)
            modules[name] = result

        module("Components")
        module("Screens")
        module("Components.ActionMap", NumberActionMap=lambda *args: None)
        module("Components.ConfigList", ConfigList=Widget)
        for name in ("Label", "MenuList", "ProgressBar"):
            module("Components." + name, **{name: Widget})
        module(
            "Components.config",
            config=types.SimpleNamespace(plugins=types.SimpleNamespace()),
            configfile=types.SimpleNamespace(save=lambda: None),
            ConfigSubsection=types.SimpleNamespace,
            ConfigInteger=Setting,
            ConfigSelection=Setting,
            ConfigYesNo=Setting,
            ConfigText=Setting,
            getConfigListEntry=lambda *args: args,
            KEY_LEFT=0,
            KEY_RIGHT=1,
            KEY_0=10,
        )
        module("Screens.Screen", Screen=FakeScreen)
        module("Screens.MessageBox", MessageBox=types.SimpleNamespace(TYPE_YESNO=0))
        module("Screens.ScanSetup", ScanSetup="ScanSetup")
        module("Screens.ServiceScan", ServiceScan="ServiceScan")
        module(
            "Components.NimManager",
            nimmanager=types.SimpleNamespace(
                getNimListOfType=lambda kind: [1],
                getSatListForNim=lambda slot: [(192, "Astra"), (420, "Türksat")],
            ),
        )
        self.saved_servicelist = []
        modules["Screens.Standby"] = self.standby
        size = types.SimpleNamespace(width=lambda: 1280, height=lambda: 720)
        module(
            "enigma",
            eConsoleAppContainer=Container,
            eTimer=Timer,
            eServiceReference=Reference,
            eDVBDB=types.SimpleNamespace(
                getInstance=lambda: types.SimpleNamespace(
                    reloadBouquets=lambda: None,
                    saveServicelist=lambda: self.saved_servicelist.append(True),
                )
            ),
            eDVBFrontendParametersSatellite=SatelliteParameters,
            eDVBFrontendParameters=FrontendParameters,
            eComponentScan=types.SimpleNamespace(scanNetworkSearch=0x10),
            getDesktop=lambda index: types.SimpleNamespace(size=lambda: size),
        )
        modules["Screens"].Standby = self.standby
        self.modules = modules
        patcher = patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in UI_MODULES:
            sys.modules.pop("TKGSNavigator.ui." + name, None)
            self.addCleanup(sys.modules.pop, "TKGSNavigator.ui." + name, None)
        self.controller_module = importlib.import_module("TKGSNavigator.ui.controller")
        self.controller_module.CONFIG_DIR = self.root
        self.config_module = importlib.import_module("TKGSNavigator.ui.config")
        self.devices = ["/dev/dvb/adapter0/demux0", "/dev/dvb/adapter0/demux3"]
        self.config_module.list_demuxes = lambda: list(self.devices)
        self.nav = Navigation()
        self.session = Session(self.nav)


class ScreenTests(EnigmaTestCase):
    def setUp(self):
        super().setUp()
        self.module = importlib.import_module("TKGSNavigator.ui.screen")
        self.screen = self.module.NavigatorScreen(self.session)
        self.controller = self.screen.controller
        self.cfg = self.screen.cfg
        self.addCleanup(self.controller.close)

    def lamedb(self, text):
        (self.root / "lamedb").write_text(text, encoding="utf-8")

    def send(self, event):
        wire = (json.dumps(event) + "\n").encode("ascii")
        for offset in range(0, len(wire), 7):
            self.controller.receive(wire[offset : offset + 7])

    def scan_to_result(self, event=None):
        self.screen.start_scan()
        self.controller.check_lock()
        self.send(event or result_event())
        self.controller.finished(0)

    def test_only_one_user_setting_is_shown(self):
        self.assertEqual([label for label, _ in self.screen["config"].list], ["Automatic updates"])
        self.assertTrue(self.cfg.automatic.value)

    def test_opening_scans_in_automatic_mode_only(self):
        self.assertEqual(self.nav.current.toString(), "original-service")
        self.assertFalse(self.controller.playback_changed)
        for callback in self.screen.onFirstExecBegin:
            callback()
        self.assertEqual(self.controller.state, "tuning")
        self.controller.cancel()
        self.cfg.automatic.value = False
        for callback in self.screen.onFirstExecBegin:
            callback()
        self.assertEqual(self.controller.state, "idle")

    def test_clean_result_is_applied_and_its_transponder_learned(self):
        self.scan_to_result()
        self.assertEqual(self.nav.current.toString(), "original-service")
        self.assertEqual(len(self.screen["channels"].list), 2)
        self.assertEqual(self.cfg.learned.value, "12380:V:27500")
        self.assertIn(" apply ", self.controller.container.command)
        self.send({"event": "applied", "backup": "20260101T000000Z-0123abcd"})
        self.controller.finished(0)
        self.assertEqual(self.screen["status"].text, "The channel list was updated.")
        self.screen.restore()
        self.assertIn("--backup 20260101T000000Z-0123abcd", self.controller.container.command)

    def test_manual_mode_and_warnings_stop_at_the_preview(self):
        self.cfg.automatic.value = False
        self.scan_to_result()
        self.assertNotIn(" apply ", self.controller.container.command)
        self.assertIn("Press Yellow", self.screen["status"].text)
        self.screen.apply()
        self.assertIn(" apply ", self.controller.container.command)

    def test_plan_follows_the_image_numbering_mode(self):
        self.screen.start_scan()
        self.controller.check_lock()
        command = self.controller.container.command
        self.assertIn("--prefer hd --categories --align-lcn --bouquet-first", command)
        self.controller.cancel()
        config = self.modules["Components.config"].config
        config.usage = types.SimpleNamespace(
            alternative_number_mode=types.SimpleNamespace(value=True)
        )
        self.screen.start_scan()
        self.controller.check_lock()
        self.assertNotIn("--bouquet-first", self.controller.container.command)

    def test_every_demux_is_offered_with_the_detected_one_first(self):
        self.nav.demux = 3
        self.screen.start_scan()
        self.controller.check_lock()
        self.assertIn(
            "--device /dev/dvb/adapter0/demux3 --device /dev/dvb/adapter0/demux0",
            self.controller.container.command,
        )

    def test_already_playing_target_service_is_not_an_error(self):
        # enigma2 refuses to replay the running service ("Ignore request to play
        # already running service") and returns 1; that must not fail the tune.
        # The playing reference carries the resolved name after "::".
        self.lamedb(LAMEDB4)
        self.nav.current = Reference("1:0:19:65:1:1:1A40000:0:0:0::Sample News HD")
        real_play = self.nav.playService

        def playService(ref):
            if ref.toCompareString() == self.nav.current.toCompareString():
                return 1
            return real_play(ref)

        self.nav.playService = playService
        self.screen.start_scan()
        self.assertEqual(self.controller.state, "tuning")
        self.controller.check_lock()
        self.assertIn("scan", self.controller.container.command)

    def test_no_demux_fails_after_lock_and_restores_playback(self):
        self.devices.clear()
        self.screen.start_scan()
        self.controller.check_lock()
        self.assertEqual(self.controller.state, "idle")
        self.assertIn("No DVB demux", self.screen["status"].text)
        self.assertEqual(self.nav.current.toString(), "original-service")

    def test_learned_transponder_is_tried_first(self):
        self.lamedb(LAMEDB_TWO_TRANSPONDERS)
        self.cfg.learned.value = "12423:H:30000"
        self.screen.start_scan()
        self.assertIn("12423 H 30000", self.screen["status"].text)

    def test_deep_search_covers_other_transponders_and_reports_not_found(self):
        self.lamedb(LAMEDB4.replace("12380000:27500000:1", "11054000:30000000:1"))
        self.screen.start_scan()
        self.assertIn(
            "Searching for the TKGS table (1/1): 11054 V 30000", self.screen["status"].text
        )
        self.controller.check_lock()
        self.assertIn("--idle-timeout 8", self.controller.container.command)
        self.send(result_event(TableCollector()))
        self.controller.finished(2)
        self.assertEqual(self.controller.state, "idle")
        self.assertIn("No TKGS table was found on 1 transponders", self.screen["status"].text)

    def test_empty_lamedb_runs_the_receivers_channel_search_then_scans(self):
        self.lamedb(LAMEDB4.replace(":420:", ":192:"))
        self.screen.start_scan()
        callback, screen, args = self.session.questions[0]
        self.assertEqual(screen, "ServiceScan")
        (scan,) = args[0]
        self.assertEqual((scan["flags"], scan["feid"]), (0x10, 1))
        # eComponentScan.addInitial takes the typed parameters object directly.
        self.assertTrue(all(isinstance(t, SatelliteParameters) for t in scan["transponders"]))
        self.assertEqual(
            [(t.frequency, t.polarisation) for t in scan["transponders"]],
            [(12380000, 1), (12423000, 0)],
        )
        self.lamedb(LAMEDB4)
        callback()
        self.assertEqual(self.saved_servicelist, [True])
        self.assertEqual(self.controller.state, "tuning")

    def test_channel_search_runs_once_then_explains(self):
        self.lamedb(LAMEDB4.replace(":420:", ":192:"))
        self.screen.start_scan()
        self.session.questions[0][0]()
        self.assertEqual(len(self.session.questions), 1)
        self.assertIn("No Türksat 42.0°E channels", self.screen["status"].text)

    def test_manual_mode_asks_before_searching(self):
        self.cfg.automatic.value = False
        self.lamedb(LAMEDB4.replace(":420:", ":192:"))
        self.screen.start_scan()
        callback, screen, args = self.session.questions[0]
        self.assertIn("channel search", args[0])
        callback(True)
        self.assertEqual(self.session.opened, ["ScanSetup"])

    def test_many_missing_channels_trigger_one_search_before_applying(self):
        self.lamedb(LAMEDB4.replace("0066:01a40000:0001:0001:1:0", "0066:01a40000:0001:0001:2:0"))
        partial = dict(result_event(), skipped=[{"lcn": 2, "name": "B", "reason": "missing"}])
        self.scan_to_result(partial)
        self.assertNotIn(" apply ", self.controller.container.command)
        callback, screen, _ = self.session.questions[0]
        self.assertEqual(screen, "ServiceScan")
        callback()
        self.controller.check_lock()
        self.send(partial)
        self.controller.finished(0)
        self.assertIn(" apply ", self.controller.container.command)

    def test_cancel_tuning_restores_playback(self):
        self.screen.start_scan()
        self.screen.cancel()
        self.assertEqual(self.nav.current.toString(), "original-service")
        self.assertEqual(self.controller.state, "idle")
        self.assertFalse(self.screen.closed)

    def test_cancel_worker_restores_playback(self):
        self.screen.start_scan()
        self.controller.check_lock()
        self.screen.cancel()
        self.assertTrue(self.controller.container.killed)
        self.assertEqual(self.nav.current.toString(), "original-service")
        self.assertIsNone(self.controller.report)
        self.assertIn("cancelled", self.screen["status"].text)

    def test_red_key_closes_when_idle(self):
        self.screen.cancel()
        self.assertTrue(self.screen.closed)

    def test_recording_blocks_scan(self):
        self.nav.recordings = [object()]
        self.screen.start_scan()
        self.assertEqual(self.controller.state, "idle")
        self.assertEqual(self.nav.current.toString(), "original-service")

    def test_missing_tuner_for_42e_blocks_scan(self):
        nims = types.SimpleNamespace(
            getNimListOfType=lambda kind: [0], getSatListForNim=lambda slot: [(192, "Astra")]
        )
        fake = {"Components.NimManager": types.SimpleNamespace(nimmanager=nims)}
        with patch.dict(sys.modules, fake):
            self.screen.start_scan()
        self.assertEqual(self.controller.state, "idle")
        self.assertIn("42.0°E", self.screen["status"].text)

    def test_lock_timeout_tries_next_transponder_then_restores(self):
        self.lamedb(LAMEDB_TWO_TRANSPONDERS)
        self.screen.start_scan()
        first = self.controller.target_reference
        self.nav.locked = False
        self.controller.deadline = 0
        self.controller.check_lock()
        self.assertEqual(self.controller.state, "tuning")
        self.assertNotEqual(self.controller.target_reference, first)
        self.assertIn("12423 H 30000", self.screen["status"].text)
        self.controller.deadline = 0
        self.controller.check_lock()
        self.assertEqual(self.controller.state, "idle")
        self.assertEqual(self.nav.current.toString(), "original-service")

    def test_empty_transponder_retunes(self):
        self.lamedb(LAMEDB_TWO_TRANSPONDERS)
        self.screen.start_scan()
        self.controller.check_lock()
        self.assertIn("--idle-timeout 20", self.controller.container.command)
        self.send(result_event(TableCollector()))
        self.controller.finished(2)
        self.assertEqual(self.controller.state, "tuning")
        self.assertIn("No TKGS data", self.screen["status"].text)

    def test_no_original_service_stops_after_cancel(self):
        self.nav.current = None
        self.screen.start_scan()
        self.screen.cancel()
        self.assertIsNone(self.nav.current)

    def test_apply_cannot_be_interrupted_by_red_key(self):
        self.controller.state = "apply"
        self.screen.cancel()
        self.assertFalse(self.controller.container.killed)
        self.assertFalse(self.screen.closed)

    def test_failed_process_start_restores_playback(self):
        self.screen.start_scan()
        self.controller.container.execute = lambda command: -1
        self.controller.check_lock()
        self.assertEqual(self.controller.state, "idle")
        self.assertEqual(self.nav.current.toString(), "original-service")

    def test_skin_is_valid_xml(self):
        from xml.etree.ElementTree import fromstring

        self.assertEqual(fromstring(self.screen.skin).tag, "screen")


class AutoUpdaterTests(EnigmaTestCase):
    def setUp(self):
        super().setUp()
        self.module = importlib.import_module("TKGSNavigator.ui.auto")
        self.now = time.mktime((2026, 9, 24, 6, 0, 0, 0, 0, -1))
        self.updater = self.module.AutoUpdater(self.session, clock=lambda: self.now)
        self.addCleanup(self.updater.stop)
        self.cfg = self.updater.cfg
        self.cfg.last_auto_update.value = int(self.now) - 2 * 86400
        self.standby.inStandby = object()
        self.nav.current = None

    def run_scan(self, event):
        self.updater.tick()
        controller = self.updater.controller
        controller.check_lock()
        controller.receive((json.dumps(event) + "\n").encode("ascii"))
        controller.finished(0)
        return controller

    def test_due_after_the_hour_once_per_day(self):
        at = lambda h, d=24: time.mktime((2026, 9, d, h, 0, 0, 0, 0, -1))  # noqa: E731
        self.assertFalse(self.module.due(at(4), 5, at(5, 23)))
        self.assertTrue(self.module.due(at(6), 5, at(5, 23)))
        self.assertFalse(self.module.due(at(6), 5, at(5)))
        self.assertTrue(self.module.due(at(6), 5, 0))

    def test_nothing_happens_unless_automatic_due_and_in_standby(self):
        for change in (
            lambda: setattr(self.cfg.automatic, "value", False),
            lambda: setattr(self.standby, "inStandby", None),
            lambda: setattr(self.nav, "recordings", [object()]),
            lambda: setattr(self.nav.RecordTimer, "getNextRecordingTime", lambda: self.now + 600),
            lambda: setattr(self.cfg.last_auto_update, "value", int(self.now)),
        ):
            self.setUp()
            change()
            self.updater.tick()
            self.assertIsNone(self.updater.controller)

    def test_clean_preview_is_applied_and_transponder_learned(self):
        controller = self.run_scan(result_event())
        self.assertIn(" apply ", controller.container.command)
        self.assertEqual(self.cfg.learned.value, "12380:V:27500")
        controller.receive(b'{"event": "applied", "backup": "20260101T000000Z-0123abcd"}\n')
        controller.finished(0)
        self.assertIsNone(self.updater.controller)
        self.assertEqual(self.cfg.last_auto_update.value, int(self.now))
        self.assertEqual(self.nav.stopped, 1)

    def test_preview_with_warnings_is_not_applied(self):
        partial = TableCollector()
        partial.add(sample_sections()[0])
        controller = self.run_scan(result_event(partial))
        self.assertNotIn(" apply ", controller.container.command)
        self.assertEqual(self.cfg.last_auto_update.value, int(self.now))

    def test_missing_transponders_never_start_a_search_in_standby(self):
        (self.root / "lamedb").write_text(LAMEDB4.replace(":420:", ":192:"), encoding="utf-8")
        self.updater.tick()
        self.assertIsNone(self.updater.controller)
        self.assertEqual(self.session.questions, [])

    def test_leaving_standby_cancels_without_touching_playback(self):
        self.updater.tick()
        controller = self.updater.controller
        controller.check_lock()
        self.standby.inStandby = None
        self.updater.tick()
        self.assertTrue(controller.container.killed)
        self.assertEqual(self.nav.stopped, 0)
        self.assertIsNone(self.updater.controller)
        self.assertEqual(self.cfg.last_auto_update.value, int(self.now) - 2 * 86400)


if __name__ == "__main__":
    unittest.main()
