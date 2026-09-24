import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from TKGSNavigator.core.dvb import FILTER_PARAMETERS, Cancelled, capture
from TKGSNavigator.core.lamedb import ServiceDatabase
from TKGSNavigator.core.sections import TableCollector
from TKGSNavigator.core.storage import BOUQUET
from TKGSNavigator.core.workflow import preview, save_capture
from tests.helpers import LAMEDB4, sample_sections

SCREEN_MODULE = "TKGSNavigator.ui.screen"


def filter_flags(params):
    return FILTER_PARAMETERS.unpack(params)[-1]


class CaptureTests(unittest.TestCase):
    def test_nonblocking_capture_closes_fd_and_stops_early(self):
        with patch("TKGSNavigator.core.dvb.os.open", return_value=42) as opened, \
             patch("TKGSNavigator.core.dvb.os.close") as closed, \
             patch("TKGSNavigator.core.dvb.fcntl.ioctl") as ioctl, \
             patch("TKGSNavigator.core.dvb.select.select", return_value=([42], [], [])), \
             patch("TKGSNavigator.core.dvb.os.read", return_value=b"".join(sample_sections())) as read, \
             patch("TKGSNavigator.core.dvb.time.monotonic", side_effect=[0, 0, 0.1]):
            events = []
            result = capture("/fake/demux", progress=events.append)
            self.assertTrue(result.complete)
            self.assertEqual(read.call_count, 1)
            self.assertEqual(filter_flags(ioctl.call_args_list[0].args[2]), 5)
            self.assertEqual(events[0]["sections"], 2)
            closed.assert_called_once_with(42)

    def test_incomplete_capture_retries_without_crc(self):
        clock = [0.0]

        def idle_select(readers, writers, errors, interval):
            clock[0] += interval
            return ([], [], [])

        with patch("TKGSNavigator.core.dvb.os.open", return_value=42), \
             patch("TKGSNavigator.core.dvb.os.close"), \
             patch("TKGSNavigator.core.dvb.fcntl.ioctl") as ioctl, \
             patch("TKGSNavigator.core.dvb.select.select", side_effect=idle_select), \
             patch("TKGSNavigator.core.dvb.time.monotonic", side_effect=lambda: clock[0]):
            events = []
            result = capture("/fake/demux", progress=events.append)
            self.assertFalse(result.complete)
            self.assertFalse(result.check_crc)
            flags = [filter_flags(call.args[2]) for call in ioctl.call_args_list if len(call.args) > 2]
            self.assertEqual(flags, [5, 4])
            self.assertFalse(events[-1]["crc"])

    def test_cancellation_closes_fd(self):
        with patch("TKGSNavigator.core.dvb.os.open", return_value=42), \
             patch("TKGSNavigator.core.dvb.os.close") as closed, \
             patch("TKGSNavigator.core.dvb.fcntl.ioctl"):
            with self.assertRaises(Cancelled):
                capture("/fake/demux", cancelled=lambda: True)
            closed.assert_called_once_with(42)

    def test_filter_failure_closes_fd(self):
        with patch("TKGSNavigator.core.dvb.os.open", return_value=42), \
             patch("TKGSNavigator.core.dvb.os.close") as closed, \
             patch("TKGSNavigator.core.dvb.fcntl.ioctl", side_effect=OSError("driver error")):
            with self.assertRaises(OSError):
                capture("/fake/demux")
            closed.assert_called_once_with(42)


class CliTests(unittest.TestCase):
    def test_standalone_worker_scan_is_read_only_then_explicit_apply(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "lamedb").write_text(LAMEDB4, encoding="utf-8")
            collector = TableCollector()
            for raw in sample_sections():
                collector.add(raw)
            save_capture(folder / "record.json", collector)
            worker = str(root / "TKGSNavigator/worker.py")
            command = [sys.executable, worker, "scan", "--capture", str(folder / "record.json"),
                       "--lamedb", str(folder / "lamedb")]
            result = subprocess.run(command, capture_output=True, text=True, cwd=str(folder))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["can_apply"])
            self.assertFalse((folder / "bouquets.tv").exists())
            result = subprocess.run([sys.executable, worker, "apply", "--capture", str(folder / "record.json"),
                                     "--config-dir", str(folder)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            event = json.loads(result.stdout)
            self.assertEqual(event["event"], "applied")
            self.assertTrue((folder / BOUQUET).exists())
            result = subprocess.run([sys.executable, worker, "restore", "--backup", event["backup"],
                                     "--config-dir", str(folder)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((folder / BOUQUET).exists())


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

    def __setitem__(self, name, value):
        self.widgets[name] = value

    def __getitem__(self, name):
        return self.widgets[name]

    def close(self):
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

    def start(self, *args):
        self.running = True

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

    def getCurrentlyPlayingServiceReference(self):
        return self.current

    def playService(self, ref):
        self.current = ref
        return 0

    def stopService(self):
        self.current = None

    def getRecordings(self):
        return self.recordings

    def getCurrentService(self):
        return types.SimpleNamespace(frontendInfo=lambda: types.SimpleNamespace(
            getFrontendStatus=lambda: {"tuner_locked": self.locked}))


class ScreenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
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
        module("Components.config", config=types.SimpleNamespace(plugins=types.SimpleNamespace()),
               configfile=types.SimpleNamespace(save=lambda: None), ConfigSubsection=types.SimpleNamespace,
               ConfigInteger=Setting, ConfigSelection=Setting, getConfigListEntry=lambda *args: args,
               KEY_LEFT=0, KEY_RIGHT=1, KEY_0=10)
        module("Screens.Screen", Screen=FakeScreen)
        size = types.SimpleNamespace(width=lambda: 1280, height=lambda: 720)
        module("enigma", eConsoleAppContainer=Container, eTimer=Timer, eServiceReference=Reference,
               eDVBDB=types.SimpleNamespace(getInstance=lambda: types.SimpleNamespace(reloadBouquets=lambda: None)),
               getDesktop=lambda index: types.SimpleNamespace(size=lambda: size))
        self.patch = patch.dict(sys.modules, modules)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        for name in (SCREEN_MODULE, "TKGSNavigator.ui.config", "TKGSNavigator.ui.skin"):
            sys.modules.pop(name, None)
        self.module = importlib.import_module(SCREEN_MODULE)
        self.addCleanup(lambda: sys.modules.pop(SCREEN_MODULE, None))
        self.module.CONFIG_DIR = Path(self.temp.name)
        self.module.LAMEDB = Path(self.temp.name) / "lamedb"
        (Path(self.temp.name) / "lamedb").write_text(LAMEDB4, encoding="utf-8")
        self.nav = Navigation()
        self.screen = self.module.NavigatorScreen(types.SimpleNamespace(nav=self.nav))
        self.addCleanup(self.screen.cleanup)

    def start(self):
        with patch.object(Path, "exists", return_value=True):
            self.screen.start_scan()

    def test_opening_screen_does_not_interrupt_playback(self):
        self.assertEqual(self.nav.current.toString(), "original-service")
        self.assertFalse(self.screen.playback_changed)

    def test_tune_capture_partial_json_and_restore_original(self):
        self.start()
        self.assertEqual(self.screen.state, "tuning")
        self.screen.check_lock()
        self.assertEqual(self.screen.state, "scan")
        collector = TableCollector()
        for raw in sample_sections():
            collector.add(raw)
        event = dict(event="result", **preview(collector, ServiceDatabase.parse(LAMEDB4)))
        wire = (json.dumps(event) + "\n").encode("ascii")
        for offset in range(0, len(wire), 7):
            self.screen.receive(wire[offset:offset + 7])
        self.screen.finished(0)
        self.assertEqual(self.nav.current.toString(), "original-service")
        self.assertEqual(len(self.screen["channels"].list), 2)
        self.assertTrue(self.screen.report["can_apply"])

    def test_cancel_tuning_restores_playback(self):
        self.start()
        self.screen.cancel()
        self.assertEqual(self.nav.current.toString(), "original-service")
        self.assertEqual(self.screen.state, "idle")

    def test_cancel_worker_restores_playback(self):
        self.start()
        self.screen.check_lock()
        self.screen.cancel()
        self.assertTrue(self.screen.container.killed)
        self.assertEqual(self.nav.current.toString(), "original-service")
        self.assertIsNone(self.screen.report)

    def test_recording_blocks_scan(self):
        self.nav.recordings = [object()]
        self.start()
        self.assertEqual(self.screen.state, "idle")
        self.assertEqual(self.nav.current.toString(), "original-service")

    def test_lock_timeout_restores_playback(self):
        self.start()
        self.nav.locked = False
        self.screen.deadline = 0
        self.screen.check_lock()
        self.assertEqual(self.screen.state, "idle")
        self.assertEqual(self.nav.current.toString(), "original-service")

    def test_no_original_service_stops_after_cancel(self):
        self.nav.current = None
        self.start()
        self.screen.cancel()
        self.assertIsNone(self.nav.current)

    def test_apply_cannot_be_interrupted_by_red_key(self):
        self.screen.state = "apply"
        self.screen.cancel()
        self.assertFalse(self.screen.container.killed)
        self.assertFalse(self.screen.closed)

    def test_failed_process_start_restores_playback(self):
        self.start()
        self.screen.container.execute = lambda command: -1
        self.screen.check_lock()
        self.assertEqual(self.screen.state, "idle")
        self.assertEqual(self.nav.current.toString(), "original-service")

    def test_skin_is_valid_xml(self):
        from xml.etree.ElementTree import fromstring
        self.assertEqual(fromstring(self.screen.skin).tag, "screen")


if __name__ == "__main__":
    unittest.main()
