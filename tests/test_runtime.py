import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.helpers import LAMEDB4, sample_sections
from TKGSNavigator.core.dvb import FILTER_PARAMETERS, Cancelled, capture
from TKGSNavigator.core.sections import TableCollector
from TKGSNavigator.core.storage import BOUQUET
from TKGSNavigator.core.workflow import save_capture

SCREEN_MODULE = "TKGSNavigator.ui.screen"


def filter_flags(params):
    return FILTER_PARAMETERS.unpack(params)[-1]


class CaptureTests(unittest.TestCase):
    def test_nonblocking_capture_closes_fd_and_stops_early(self):
        with patch("TKGSNavigator.core.dvb.os.open", return_value=42), patch(
            "TKGSNavigator.core.dvb.os.close"
        ) as closed, patch("TKGSNavigator.core.dvb.fcntl.ioctl") as ioctl, patch(
            "TKGSNavigator.core.dvb.select.select", return_value=([42], [], [])
        ), patch(
            "TKGSNavigator.core.dvb.os.read", return_value=b"".join(sample_sections())
        ) as read, patch("TKGSNavigator.core.dvb.time.monotonic", side_effect=[0, 0, 0.1]):
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

        with patch("TKGSNavigator.core.dvb.os.open", return_value=42), patch(
            "TKGSNavigator.core.dvb.os.close"
        ), patch("TKGSNavigator.core.dvb.fcntl.ioctl") as ioctl, patch(
            "TKGSNavigator.core.dvb.select.select", side_effect=idle_select
        ), patch("TKGSNavigator.core.dvb.time.monotonic", side_effect=lambda: clock[0]):
            events = []
            result = capture("/fake/demux", progress=events.append)
            self.assertFalse(result.complete)
            self.assertFalse(result.check_crc)
            flags = [
                filter_flags(call.args[2]) for call in ioctl.call_args_list if len(call.args) > 2
            ]
            self.assertEqual(flags, [5, 4])
            self.assertFalse(events[-1]["crc"])

    def test_silent_transponder_retries_without_crc_then_gives_up(self):
        clock = [0.0]

        def idle_select(readers, writers, errors, interval):
            clock[0] += interval
            return ([], [], [])

        with patch("TKGSNavigator.core.dvb.os.open", return_value=42), patch(
            "TKGSNavigator.core.dvb.os.close"
        ) as closed, patch("TKGSNavigator.core.dvb.fcntl.ioctl") as ioctl, patch(
            "TKGSNavigator.core.dvb.select.select", side_effect=idle_select
        ), patch("TKGSNavigator.core.dvb.time.monotonic", side_effect=lambda: clock[0]):
            events = []
            result = capture("/fake/demux", progress=events.append, idle_timeout=20)
            self.assertEqual(result.parts, {})
            self.assertFalse(result.check_crc)
            self.assertTrue(20 <= clock[0] < 31)
            self.assertEqual(events[-1]["sections"], 0)
            self.assertEqual(
                [filter_flags(call.args[2]) for call in ioctl.call_args_list if len(call.args) > 2],
                [5, 4],
            )
            closed.assert_called_once_with(42)

    def test_first_demux_with_tkgs_data_wins_and_others_close(self):
        wire = b"".join(sample_sections())
        reads = {41: iter([]), 42: iter([wire])}

        def read(fd, size):
            try:
                return next(reads[fd])
            except StopIteration:
                raise BlockingIOError(11, "again") from None

        opened = iter([41, 42, 43])
        with patch("TKGSNavigator.core.dvb.os.open", side_effect=lambda *a: next(opened)), patch(
            "TKGSNavigator.core.dvb.os.close"
        ) as closed, patch("TKGSNavigator.core.dvb.fcntl.ioctl") as ioctl, patch(
            "TKGSNavigator.core.dvb.select.select", return_value=([41, 42], [], [])
        ), patch("TKGSNavigator.core.dvb.os.read", side_effect=read), patch(
            "TKGSNavigator.core.dvb.time.monotonic", return_value=0.0
        ):

            def ioctl_effect(fd, request, *params):
                if fd == 43 and params:
                    raise OSError("demux busy")

            ioctl.side_effect = ioctl_effect
            events = []
            result = capture(
                [
                    "/dev/dvb/adapter0/demux0",
                    "/dev/dvb/adapter0/demux1",
                    "/dev/dvb/adapter0/demux2",
                ],
                progress=events.append,
            )
        self.assertTrue(result.complete)
        self.assertEqual(result.device, "/dev/dvb/adapter0/demux1")
        self.assertEqual(events[-1]["device"], "/dev/dvb/adapter0/demux1")
        self.assertEqual(sorted(call.args[0] for call in closed.call_args_list), [41, 42, 43])

    def test_no_openable_demux_raises(self):
        with patch("TKGSNavigator.core.dvb.os.open", side_effect=OSError("no device")):
            with self.assertRaises(OSError):
                capture(["/dev/dvb/adapter0/demux0", "/dev/dvb/adapter0/demux1"])
        with self.assertRaises(ValueError):
            capture([])

    def test_table_completed_after_fallback_deadline_keeps_crc(self):
        clock = iter([0.0, 0.0, 30.0])
        with patch("TKGSNavigator.core.dvb.os.open", return_value=42), patch(
            "TKGSNavigator.core.dvb.os.close"
        ), patch("TKGSNavigator.core.dvb.fcntl.ioctl"), patch(
            "TKGSNavigator.core.dvb.select.select", return_value=([42], [], [])
        ), patch("TKGSNavigator.core.dvb.os.read", return_value=b"".join(sample_sections())), patch(
            "TKGSNavigator.core.dvb.time.monotonic", side_effect=lambda: next(clock, 30.0)
        ):
            result = capture("/fake/demux")
            self.assertTrue(result.complete)
            self.assertTrue(result.check_crc)

    def test_invalid_idle_timeout_rejected(self):
        with self.assertRaises(ValueError):
            capture("/fake/demux", timeout=10, idle_timeout=20)

    def test_cancellation_closes_fd(self):
        with patch("TKGSNavigator.core.dvb.os.open", return_value=42), patch(
            "TKGSNavigator.core.dvb.os.close"
        ) as closed, patch("TKGSNavigator.core.dvb.fcntl.ioctl"):
            with self.assertRaises(Cancelled):
                capture("/fake/demux", cancelled=lambda: True)
            closed.assert_called_once_with(42)

    def test_filter_failure_closes_fd(self):
        with patch("TKGSNavigator.core.dvb.os.open", return_value=42), patch(
            "TKGSNavigator.core.dvb.os.close"
        ) as closed, patch(
            "TKGSNavigator.core.dvb.fcntl.ioctl", side_effect=OSError("driver error")
        ):
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
            command = [
                sys.executable,
                worker,
                "scan",
                "--capture",
                str(folder / "record.json"),
                "--lamedb",
                str(folder / "lamedb"),
            ]
            result = subprocess.run(command, capture_output=True, text=True, cwd=str(folder))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["can_apply"])
            self.assertFalse((folder / "bouquets.tv").exists())
            result = subprocess.run(
                [
                    sys.executable,
                    worker,
                    "apply",
                    "--capture",
                    str(folder / "record.json"),
                    "--config-dir",
                    str(folder),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            event = json.loads(result.stdout)
            self.assertEqual(event["event"], "applied")
            self.assertTrue((folder / BOUQUET).exists())
            result = subprocess.run(
                [
                    sys.executable,
                    worker,
                    "restore",
                    "--backup",
                    event["backup"],
                    "--config-dir",
                    str(folder),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((folder / BOUQUET).exists())


class TranslationTests(unittest.TestCase):
    def test_plugin_catalog_first_then_enigma2_catalog(self):
        from TKGSNavigator.ui import i18n

        with patch.object(i18n.gettext, "dgettext", return_value="Kapat") as plugin, patch.object(
            i18n.gettext, "gettext", return_value="unused"
        ):
            self.assertEqual(i18n._("Close"), "Kapat")
            plugin.assert_called_once_with(i18n.DOMAIN, "Close")
        with patch.object(
            i18n.gettext, "dgettext", side_effect=lambda domain, text: text
        ), patch.object(i18n.gettext, "gettext", return_value="Schließen"):
            self.assertEqual(i18n._("Close"), "Schließen")

    def test_template_lists_every_ui_message(self):
        root = Path(__file__).resolve().parents[1] / "TKGSNavigator"
        messages = set()
        for path in [root / "plugin.py"] + sorted((root / "ui").glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) == "_"
                    and node.args
                ):
                    messages.add(node.args[0].value)
        self.assertTrue(messages)
        self.assertEqual(messages - template_messages(root / "locale" / "TKGSNavigator.pot"), set())


def template_messages(path):
    messages, current = set(), None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("msgid "):
            current = [ast.literal_eval(line[6:])]
        elif line.startswith('"') and current is not None:
            current.append(ast.literal_eval(line))
        else:
            if current is not None and line.startswith("msgstr"):
                messages.add("".join(current))
            current = None
    return messages - {""}


if __name__ == "__main__":
    unittest.main()
