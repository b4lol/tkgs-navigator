import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.helpers import LAMEDB4, sample_sections, section
from tests.helpers import record as record_bytes
from TKGSNavigator.core.crc import crc32_mpeg
from TKGSNavigator.core.dvb import FILTER_PARAMETERS, filter_parameters, record
from TKGSNavigator.core.inspect import inspect_sections
from TKGSNavigator.core.lamedb import ServiceDatabase
from TKGSNavigator.core.sections import SectionFramer
from TKGSNavigator.core.workflow import load_capture, save_recording

ROOT = Path(__file__).resolve().parents[1]


def other_table():
    body = bytes([0xA8, 0x30, 0x05]) + b"abcde"  # short-form private section, table 0xA8
    return body


def record_layout_subtable(extension):
    payload = record_bytes(0x65, 1, "News HD", flags=0x03) + record_bytes(0x66, 2, "Culture")
    return [section(payload, 0, 0, extension=extension)]


def recording():
    return sample_sections() + record_layout_subtable(7) + [other_table()]


class RecorderTests(unittest.TestCase):
    def test_framer_without_table_id_accepts_every_table(self):
        wire = b"".join(recording())
        self.assertEqual(SectionFramer(None).feed(wire), recording())
        self.assertEqual(SectionFramer().feed(wire), sample_sections() + record_layout_subtable(7))

    def test_filter_for_all_tables_has_no_table_match(self):
        pid, value, mask, _, _, flags = FILTER_PARAMETERS.unpack(filter_parameters(False, None))
        self.assertEqual((pid, value, mask, flags), (8181, bytes(16), bytes(16), 4))

    def test_record_keeps_distinct_sections_until_time_is_up(self):
        clock = [0.0]

        def ready(readers, writers, errors, interval):
            clock[0] += 1
            return ([42], [], [])

        chunks = iter([b"".join(recording()), b"".join(recording())])

        def read(fd, size):
            try:
                return next(chunks)
            except StopIteration:
                raise BlockingIOError(11, "again") from None

        with patch("TKGSNavigator.core.dvb.os.open", return_value=42), patch(
            "TKGSNavigator.core.dvb.os.close"
        ) as closed, patch("TKGSNavigator.core.dvb.fcntl.ioctl") as ioctl, patch(
            "TKGSNavigator.core.dvb.select.select", side_effect=ready
        ), patch("TKGSNavigator.core.dvb.os.read", side_effect=read), patch(
            "TKGSNavigator.core.dvb.time.monotonic", side_effect=lambda: clock[0]
        ):
            sections = record("/fake/demux", seconds=5, all_tables=True)
        self.assertEqual(sections, recording())
        self.assertGreaterEqual(clock[0], 5)
        params = [call.args[2] for call in ioctl.call_args_list if len(call.args) > 2]
        self.assertEqual(FILTER_PARAMETERS.unpack(params[0])[-1], 4)
        closed.assert_called_once_with(42)

    def test_record_rejects_out_of_range_length(self):
        with self.assertRaises(ValueError):
            record("/fake/demux", seconds=0)


class RecordingFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "recording.json"
        save_recording(self.path, recording(), 90, True)

    def test_best_or_requested_subtable_is_loaded(self):
        self.assertEqual(load_capture(self.path).ordered(), sample_sections())
        self.assertEqual(load_capture(self.path, 7).ordered(), record_layout_subtable(7))
        with self.assertRaises(ValueError):
            load_capture(self.path, 99)

    def test_complete_subtable_beats_a_larger_incomplete_one(self):
        partial = [section(b"x", number, 5, extension=2) for number in range(4)]
        save_recording(self.path, partial + record_layout_subtable(9), 90, False)
        self.assertEqual(load_capture(self.path).ordered(), record_layout_subtable(9))

    def test_inspection_describes_each_table_and_layout(self):
        report = inspect_sections(recording(), ServiceDatabase.parse(LAMEDB4))
        tables = {(t["table_id"], t["extension"]): t for t in report["tables"]}
        self.assertEqual(set(tables), {("0xA7", 1), ("0xA7", 7), ("0xA8", None)})
        observed = tables[("0xA7", 1)]
        self.assertTrue(observed["complete"])
        self.assertEqual(observed["observed_layout"]["channels"], 2)
        self.assertFalse(observed["record_layout"]["parsed"])
        records = tables[("0xA7", 7)]["record_layout"]
        self.assertEqual((records["records"], records["lamedb_hit_rate"]), (2, 1.0))
        self.assertEqual(records["flags"], {"0x03": 1, "0x02": 1})
        self.assertEqual(tables[("0xA8", None)]["sections"], 1)

    def test_inspection_counts_crc_failures(self):
        broken = bytearray(sample_sections()[0])
        broken[-1] ^= 1
        table = inspect_sections([bytes(broken)])["tables"][0]
        self.assertEqual((table["crc_ok"], table["crc_failed"]), (0, 1))
        self.assertNotEqual(crc32_mpeg(bytes(broken)), 0)

    def test_worker_inspects_and_scans_a_recording(self):
        worker = str(ROOT / "TKGSNavigator/worker.py")
        (Path(self.temp.name) / "lamedb").write_text(LAMEDB4, encoding="utf-8")
        lamedb = str(Path(self.temp.name) / "lamedb")
        result = subprocess.run(
            [sys.executable, worker, "inspect", "--capture", str(self.path), "--lamedb", lamedb],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["event"], "inspection")
        result = subprocess.run(
            [
                sys.executable,
                worker,
                "scan",
                "--capture",
                str(self.path),
                "--extension",
                "7",
                "--lamedb",
                lamedb,
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["layout"]["layout"], "records")

    def test_worker_applies_category_bouquets_and_restores(self):
        worker = str(ROOT / "TKGSNavigator/worker.py")
        folder = Path(self.temp.name)
        (folder / "lamedb").write_text(LAMEDB4, encoding="utf-8")
        common = ["--capture", str(self.path), "--config-dir", str(folder), "--extension", "7"]
        result = subprocess.run(
            [sys.executable, worker, "apply", *common, "--categories", "--bouquet-first"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        event = json.loads(result.stdout)
        files = [item["file"] for item in event["bouquets"]]
        self.assertEqual(
            files, ["userbouquet.tkgs_navigator.tv", "userbouquet.tkgs_navigator_pkg0.tv"]
        )
        self.assertTrue(all((folder / name).exists() for name in files))
        self.assertIn(b"tkgs_navigator.tv", (folder / "bouquets.tv").read_bytes().splitlines()[1])
        result = subprocess.run(
            [
                sys.executable,
                worker,
                "restore",
                "--config-dir",
                str(folder),
                "--backup",
                event["backup"],
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any((folder / name).exists() for name in files))


if __name__ == "__main__":
    unittest.main()
