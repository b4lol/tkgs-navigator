import importlib
import json
from pathlib import Path
import sys
import tempfile
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
            getConfigListEntry=lambda *args: args,
            KEY_LEFT=0,
            KEY_RIGHT=1,
            KEY_0=10,
        )
        module("Screens.Screen", Screen=FakeScreen)
        module("Screens.MessageBox", MessageBox=types.SimpleNamespace(TYPE_YESNO=0))
        module("Screens.ScanSetup", ScanSetup="ScanSetup")
        modules["Screens.Standby"] = self.standby
        size = types.SimpleNamespace(width=lambda: 1280, height=lambda: 720)
        module(
            "enigma",
            eConsoleAppContainer=Container,
            eTimer=Timer,
            eServiceReference=Reference,
            eDVBDB=types.SimpleNamespace(
                getInstance=lambda: types.SimpleNamespace(reloadBouquets=lambda: None)
            ),
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
        self.devices = {"/dev/dvb/adapter0/demux0", "/dev/dvb/adapter0/demux3"}
        self.config_module.Path = lambda path: types.SimpleNamespace(
            exists=lambda: str(path) in self.devices
        )
        self.nav = Navigation()
        self.session = Session(self.nav)


class ScreenTests(EnigmaTestCase):
    def setUp(self):
        super().setUp()
        self.module = importlib.import_module("TKGSNavigator.ui.screen")
        self.screen = self.module.NavigatorScreen(self.session)
        self.controller = self.screen.controller
        self.addCleanup(self.controller.close)

    def use_two_transponders(self):
        (self.root / "lamedb").write_text(LAMEDB_TWO_TRANSPONDERS, encoding="utf-8")

    def send(self, event):
        wire = (json.dumps(event) + "\n").encode("ascii")
        for offset in range(0, len(wire), 7):
            self.controller.receive(wire[offset : offset + 7])

    def test_opening_screen_does_not_interrupt_playback(self):
        self.assertEqual(self.nav.current.toString(), "original-service")
        self.assertFalse(self.controller.playback_changed)

    def test_tune_capture_partial_json_and_restore_original(self):
        self.screen.start_scan()
        self.assertEqual(self.controller.state, "tuning")
        self.assertEqual(self.screen["red"].text, "Cancel")
        self.controller.check_lock()
        self.assertEqual(self.controller.state, "scan")
        self.send(result_event())
        self.controller.finished(0)
        self.assertEqual(self.nav.current.toString(), "original-service")
        self.assertEqual(len(self.screen["channels"].list), 2)
        self.assertTrue(self.controller.report["can_apply"])
        self.assertIn("1 bouquets", self.screen["metrics"].text)
        self.assertEqual(self.screen["red"].text, "Close")

    def test_plan_settings_reach_scan_and_apply(self):
        self.screen.cfg.categories.value = True
        self.screen.cfg.prefer.value = "sd"
        self.screen.start_scan()
        self.controller.check_lock()
        self.assertIn("--prefer sd --categories", self.controller.container.command)
        self.send(result_event())
        self.controller.finished(0)
        self.screen.apply()
        command = self.controller.container.command
        self.assertIn(" apply ", command)
        self.assertIn("--prefer sd --categories", command)

    def test_demux_follows_the_playing_service_unless_manual(self):
        self.nav.demux = 3
        self.screen.start_scan()
        self.controller.check_lock()
        self.assertIn("/dev/dvb/adapter0/demux3", self.controller.container.command)
        self.controller.cancel()
        self.screen.cfg.demux_mode.value = "manual"
        self.screen.start_scan()
        self.controller.check_lock()
        self.assertIn("/dev/dvb/adapter0/demux0", self.controller.container.command)

    def test_missing_demux_fails_after_lock_and_restores_playback(self):
        self.devices.clear()
        self.screen.start_scan()
        self.controller.check_lock()
        self.assertEqual(self.controller.state, "idle")
        self.assertIn("demux0", self.screen["status"].text)
        self.assertEqual(self.nav.current.toString(), "original-service")

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
        with patch.dict(
            sys.modules, {"Components.NimManager": types.SimpleNamespace(nimmanager=nims)}
        ):
            self.screen.start_scan()
        self.assertEqual(self.controller.state, "idle")
        self.assertIn("42.0°E", self.screen["status"].text)

    def test_missing_transponders_offer_channel_search(self):
        (self.root / "lamedb").write_text(LAMEDB4.replace("12380000", "11000000"), encoding="utf-8")
        self.screen.start_scan()
        self.assertEqual(self.controller.state, "idle")
        callback, _, args = self.session.questions[0]
        self.assertIn("channel search", args[0])
        callback(False)
        self.assertEqual(self.session.opened, [])
        callback(True)
        self.assertEqual(self.session.opened, ["ScanSetup"])

    def test_lock_timeout_restores_playback(self):
        self.screen.start_scan()
        self.nav.locked = False
        self.controller.deadline = 0
        self.controller.check_lock()
        self.assertEqual(self.controller.state, "idle")
        self.assertEqual(self.nav.current.toString(), "original-service")

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

    def test_lock_timeout_tries_next_transponder(self):
        self.use_two_transponders()
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

    def test_empty_transponder_retunes_then_reports(self):
        self.use_two_transponders()
        self.screen.start_scan()
        self.controller.check_lock()
        self.assertIn("--idle-timeout", self.controller.container.command)
        empty = result_event(TableCollector())
        self.send(empty)
        self.controller.finished(2)
        self.assertEqual(self.controller.state, "tuning")
        self.assertIn("No TKGS data", self.screen["status"].text)
        self.controller.check_lock()
        self.send(empty)
        self.controller.finished(2)
        self.assertEqual(self.controller.state, "idle")
        self.assertEqual(self.nav.current.toString(), "original-service")

    def test_configured_transponder_missing_falls_back_to_known_one(self):
        self.screen.cfg.frequency.value = 11000
        self.screen.start_scan()
        self.assertEqual(self.controller.state, "tuning")
        self.assertIn("12380 V 27500", self.screen["status"].text)

    def test_apply_and_undo_report_and_track_the_backup(self):
        self.screen.start_scan()
        self.controller.check_lock()
        self.send(result_event())
        self.controller.finished(0)
        self.screen.apply()
        self.send({"event": "applied", "backup": "20260101T000000Z-0123abcd"})
        self.controller.finished(0)
        self.assertEqual(self.screen["status"].text, "The channel list was updated.")
        self.screen.restore()
        self.assertIn("--backup 20260101T000000Z-0123abcd", self.controller.container.command)

    def test_skin_is_valid_xml(self):
        from xml.etree.ElementTree import fromstring

        self.assertEqual(fromstring(self.screen.skin).tag, "screen")


if __name__ == "__main__":
    unittest.main()
