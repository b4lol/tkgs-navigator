import json
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.helpers import (
    LAMEDB4,
    LAMEDB5,
    LAMEDB_TWO_TRANSPONDERS,
    lcn_record,
    sample_sections,
    section,
    service_record,
)
from TKGSNavigator.core.bouquets import plan_bouquets, render
from TKGSNavigator.core.constants import TKGS_TRANSPONDERS, TuningTarget
from TKGSNavigator.core.crc import crc32_mpeg
from TKGSNavigator.core.dvb import FILTER_PARAMETERS, filter_parameters, ioctl_request
from TKGSNavigator.core.lamedb import Service, ServiceDatabase, Transponder
from TKGSNavigator.core.parser import Channel, parse_channels
from TKGSNavigator.core.sections import Section, SectionFramer, TableCollector
from TKGSNavigator.core.storage import BACKUP_DIR, BOUQUET, INDEX, BouquetStore
from TKGSNavigator.core.text import decode_name
from TKGSNavigator.core.workflow import apply_capture, load_capture, preview, save_capture


class SectionTests(unittest.TestCase):
    def test_crc_known_vector(self):
        self.assertEqual(crc32_mpeg(b"123456789"), 0x0376E6E7)

    def test_crc_corruption_rejected(self):
        raw = bytearray(section(b"example"))
        raw[9] ^= 1
        with self.assertRaises(ValueError):
            Section.parse(raw)

    def test_crc_check_can_be_disabled(self):
        raw = bytearray(section(b"example"))
        raw[9] ^= 1
        collector = TableCollector(check_crc=False)
        self.assertTrue(collector.add(bytes(raw)))

    def test_split_and_coalesced_reads(self):
        samples = sample_sections()
        wire = b"\xff\xff" + b"".join(samples)
        for chunk_size in (1, 3, 13, 8192):
            framer, actual = SectionFramer(), []
            for start in range(0, len(wire), chunk_size):
                actual.extend(framer.feed(wire[start : start + chunk_size]))
            self.assertEqual(actual, samples)
            self.assertEqual(len(framer.pending), 0)

    def test_out_of_order_duplicates(self):
        collector = TableCollector()
        samples = sample_sections()
        collector.add(samples[1])
        collector.add(samples[1])
        self.assertFalse(collector.complete)
        self.assertEqual(collector.missing, [0])
        collector.add(samples[0])
        self.assertTrue(collector.complete)
        self.assertEqual(collector.duplicates, 1)

    def test_version_rollover_and_stale_interleaving(self):
        collector = TableCollector()
        collector.add(section(number=0, last=1, version=31))
        collector.add(section(number=1, last=1, version=0))
        self.assertEqual(collector.missing, [0])
        self.assertFalse(collector.add(section(number=0, last=1, version=31)))
        collector.add(section(number=0, last=1, version=0))
        self.assertTrue(collector.complete)

    def test_other_tables_future_sections_and_inconsistent_last_rejected(self):
        collector = TableCollector()
        collector.add(section(number=0, last=1))
        self.assertFalse(collector.add(section(number=1, last=1, extension=2)))
        self.assertFalse(collector.add(section(number=1, last=1, current=False)))
        self.assertFalse(collector.add(section(number=1, last=2)))
        self.assertEqual(collector.rejected, 3)

    def test_duplicate_conflict_does_not_replace_first_section(self):
        collector = TableCollector()
        raw = section(b"first")
        collector.add(raw)
        collector.add(section(b"second"))
        self.assertEqual(collector.ordered(), [raw])
        self.assertEqual(collector.rejected, 1)

    def test_ioctl_abi(self):
        self.assertEqual(FILTER_PARAMETERS.size, 60)
        expected = bytearray(60)
        expected[0:2] = (8181).to_bytes(2, sys.byteorder)
        expected[2], expected[18] = 0xA7, 0xFF
        expected[56:60] = (5).to_bytes(4, sys.byteorder)
        self.assertEqual(filter_parameters(), bytes(expected))
        self.assertEqual(ioctl_request(43, 60, True, "aarch64"), 0x403C6F2B)
        self.assertEqual(ioctl_request(43, 60, True, "mipsel"), 0x803C6F2B)
        self.assertEqual(ioctl_request(42, machine="mips"), 0x20006F2A)
        self.assertEqual(ioctl_request(42, machine="armv7l"), 0x6F2A)


class ParserTests(unittest.TestCase):
    def test_cross_section_names_and_lcns(self):
        result = parse_channels(sample_sections()[::-1])
        self.assertEqual(
            [(c.lcn, c.sid, c.name) for c in result.channels],
            [(1, 101, "Sample News HD"), (2, 102, "Sample Culture")],
        )

    def test_name_encodings_and_control_injection(self):
        self.assertEqual(decode_name("Çığ ŞÖLEN".encode("iso-8859-9")), "Çığ ŞÖLEN")
        self.assertEqual(decode_name(b"\x15test\n#SERVICE\x00evil"), "test #SERVICE evil")
        self.assertEqual(decode_name(b"\x10\x00\x09" + "Çığ".encode("iso-8859-9")), "Çığ")
        self.assertEqual(decode_name(b"\x15\xff"), "")

    def test_conflicting_lcn_does_not_guess(self):
        payload = (
            service_record(101, "A")
            + service_record(102, "B")
            + lcn_record(1, 101)
            + lcn_record(1, 102)
        )
        result = parse_channels([section(payload)])
        self.assertEqual(result.channels, [])
        self.assertTrue(result.warnings)

    def test_conflicting_service_names(self):
        payload = service_record(101, "A") + service_record(101, "B") + lcn_record(1, 101)
        result = parse_channels([section(payload)])
        self.assertEqual(result.channels, [])
        self.assertTrue(result.warnings)

    def test_duplicate_sid_keeps_first_lcn(self):
        payload = service_record(101, "A") + lcn_record(3, 101) + lcn_record(1, 101)
        self.assertEqual([c.lcn for c in parse_channels([section(payload)]).channels], [1])

    def test_truncated_descriptor_rejected(self):
        payload = service_record(101, "Sample")[:-2]
        result = parse_channels([section(payload), section(lcn_record(1, 101))])
        self.assertEqual(result.channels, [])

    def test_deterministic_malformed_input_corpus(self):
        rng = random.Random(924)
        for size in range(512):
            payload = bytes(rng.randrange(256) for _ in range(size))
            parse_channels([section(payload)])


class DatabaseTests(unittest.TestCase):
    def test_all_zero_placeholder_service_is_skipped(self):
        # Stock images can leave a stray all-zero record in the services section;
        # enigma2 skips such lines without consuming the following name line.
        text = LAMEDB4.replace("services\n", "services\n0000:00000000:0000:0000:0:0:0\n", 1)
        database = ServiceDatabase.parse(text)
        self.assertEqual(database.services, ServiceDatabase.parse(LAMEDB4).services)
        text5 = LAMEDB5.replace("s:0065", 's:0000:00000000:0000:0000:0:0,""\ns:0065', 1)
        self.assertEqual(
            ServiceDatabase.parse(text5).services, ServiceDatabase.parse(LAMEDB5).services
        )

    def test_v4_v5_equivalence_and_decimal_service_type(self):
        four, five = ServiceDatabase.parse(LAMEDB4), ServiceDatabase.parse(LAMEDB5)
        self.assertEqual(four.services, five.services)
        self.assertEqual(four.transponders, five.transponders)
        self.assertTrue(four.services[0].reference.startswith("1:0:19:65:"))

    def test_frequency_comparison_is_numeric(self):
        database = ServiceDatabase.parse(LAMEDB4)
        self.assertEqual(database.tuning_service(12380, "V", 27500).sid, 101)
        with self.assertRaises(ValueError):
            database.tuning_service(1238, "V", 2750)

    def test_wrong_satellite_excluded_and_ambiguity_skipped(self):
        database = ServiceDatabase.parse(LAMEDB4)
        key = (0x130000, 2, 2)
        database.transponders[key] = Transponder(key, 12380000, 27500000, 1, 192)
        services = database.services + [Service(101, key, 1, "Wrong satellite")]
        database = ServiceDatabase(database.transponders, services)
        channels = [Channel(1, 101, "A")]
        self.assertEqual(len(database.match(channels)[0]), 1)
        database.transponders[key] = Transponder(key, 12380000, 27500000, 1, 420)
        matched, skipped = database.match(channels)
        self.assertEqual(matched, [])
        self.assertEqual(skipped[0]["reason"], "ambiguous")

    def test_tuning_candidates_keep_order_skip_missing_and_duplicates(self):
        database = ServiceDatabase.parse(LAMEDB_TWO_TRANSPONDERS)
        targets = (TuningTarget(11000, "V", 27500), TKGS_TRANSPONDERS[1]) + TKGS_TRANSPONDERS
        found = database.tuning_candidates(targets)
        self.assertEqual(
            [(target.frequency, service.sid) for target, service in found],
            [(12423, 0x67), (12380, 0x65)],
        )
        self.assertEqual(
            ServiceDatabase.parse(LAMEDB4).tuning_candidates([TuningTarget(11000, "V", 27500)]), []
        )

    def test_v5_quoted_name(self):
        db = ServiceDatabase.parse(LAMEDB5.replace('"Sample Culture"', '"Culture, Arts"'))
        self.assertEqual(db.services[1].name, "Culture, Arts")

    def test_malformed_database_rejected(self):
        for text in ("bad", "eDVB services /4/\nservices\n0065:01a40000:1:1:1:0\nname"):
            with self.assertRaises(ValueError):
                ServiceDatabase.parse(text)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = BouquetStore(self.root)
        self.db = ServiceDatabase.parse(LAMEDB4)
        self.matched = self.db.match(parse_channels(sample_sections()).channels)[0]
        self.plan = plan_bouquets(self.matched)
        self.original = b"#NAME My bouquets\n#SERVICE unrelated\n"
        (self.root / INDEX).write_bytes(self.original)

    def test_apply_backup_restore_and_idempotency(self):
        ident = self.store.apply(self.plan)
        self.assertTrue(ident)
        self.assertIsNone(self.store.apply(self.plan))
        self.assertIn(b"#SERVICE unrelated", (self.root / INDEX).read_bytes())
        self.assertIn(b"1:0:19:65:", (self.root / BOUQUET).read_bytes())
        self.store.restore(ident)
        self.assertEqual((self.root / INDEX).read_bytes(), self.original)
        self.assertFalse((self.root / BOUQUET).exists())

    def test_empty_results_preserve_files(self):
        with self.assertRaises(ValueError):
            self.store.apply(plan_bouquets([]))
        self.assertEqual((self.root / INDEX).read_bytes(), self.original)
        self.assertFalse((self.root / BOUQUET).exists())

    def test_failure_on_second_file_rolls_back(self):
        from TKGSNavigator.core import storage

        real = storage.atomic_write
        failed = [False]

        def fail_once(path, data):
            if Path(path) == self.root / INDEX and not failed[0]:
                failed[0] = True
                raise OSError("simulated disk failure")
            return real(path, data)

        with patch.object(storage, "atomic_write", side_effect=fail_once):
            with self.assertRaises(OSError):
                self.store.apply(self.plan)
        self.assertEqual((self.root / INDEX).read_bytes(), self.original)
        self.assertFalse((self.root / BOUQUET).exists())

    def test_restore_refuses_external_changes(self):
        ident = self.store.apply(self.plan)
        (self.root / INDEX).write_bytes(b"someone else's new channels")
        with self.assertRaises(ValueError):
            self.store.restore(ident)
        self.assertEqual((self.root / INDEX).read_bytes(), b"someone else's new channels")

    def test_backup_tamper_is_detected(self):
        ident = self.store.apply(self.plan)
        (self.root / BACKUP_DIR / ident / INDEX).write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            self.store.restore(ident)

    def test_newline_in_name_does_not_inject_service(self):
        rendered = render("T", [(Channel(1, 101, "A\n#SERVICE evil"), self.db.services[0])])
        self.assertEqual(rendered.count(b"\n#SERVICE "), 1)

    def test_symlink_and_backup_traversal_refused(self):
        (self.root / BOUQUET).symlink_to(self.root / INDEX)
        with self.assertRaises(ValueError):
            self.store.apply(self.plan)
        with self.assertRaises(ValueError):
            self.store.restore("../../outside")


class WorkflowTests(unittest.TestCase):
    def test_partial_table_never_applied(self):
        collector = TableCollector()
        collector.add(sample_sections()[0])
        report = preview(collector, ServiceDatabase.parse(LAMEDB4))
        self.assertFalse(report["can_apply"])

    def test_conflicting_sid_warns_but_manual_apply_stays_available(self):
        # Per-channel skips are informational: the conflicting SID is dropped,
        # the rest of the complete table must still be applicable by hand.
        # Automatic application stays strict and checks warnings separately.
        conflict = section(
            service_record(101, "Sample News HD")
            + service_record(101, "Renamed News")
            + service_record(102, "Sample Culture"),
            0,
            1,
        )
        collector = TableCollector()
        collector.add(conflict)
        collector.add(section(lcn_record(1, 101) + lcn_record(2, 102), 1, 1))
        report = preview(collector, ServiceDatabase.parse(LAMEDB4))
        self.assertTrue(any("Conflicting" in warning for warning in report["warnings"]))
        self.assertTrue(report["can_apply"])

    def test_crc_disabled_capture_survives_preview_and_reload(self):
        samples = [bytearray(raw) for raw in sample_sections()]
        samples[1][-1] ^= 1
        collector = TableCollector(check_crc=False)
        for raw in samples:
            collector.add(bytes(raw))
        report = preview(collector, ServiceDatabase.parse(LAMEDB4))
        self.assertTrue(report["can_apply"])
        self.assertFalse(report["crc_checked"])
        self.assertTrue(any("CRC" in warning for warning in report["warnings"]))
        with tempfile.TemporaryDirectory() as tmp:
            save_capture(Path(tmp) / "capture.json", collector)
            loaded = load_capture(Path(tmp) / "capture.json")
            self.assertTrue(loaded.complete)
            self.assertFalse(loaded.check_crc)

    def test_roundtrip_capture_and_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lamedb").write_text(LAMEDB4, encoding="utf-8")
            collector = TableCollector()
            for raw in sample_sections():
                collector.add(raw)
            save_capture(root / "capture.json", collector)
            loaded = load_capture(root / "capture.json")
            self.assertEqual(loaded.ordered(), collector.ordered())
            report = apply_capture(root / "capture.json", root)
            self.assertEqual(len(report["matched"]), 2)
            self.assertTrue((root / BOUQUET).exists())

    def test_bad_capture_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capture.json"
            for document in (
                {"schema": 2, "sections": []},
                {"schema": 1, "sections": ["not base64!"]},
                {"schema": 1, "sections": ["x" * 6000]},
            ):
                path.write_text(json.dumps(document))
                with self.assertRaises(ValueError):
                    load_capture(path)


if __name__ == "__main__":
    unittest.main()
