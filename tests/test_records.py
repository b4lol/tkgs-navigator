import random
import unittest

from tests.helpers import LAMEDB4, record, sample_sections, section
from TKGSNavigator.core.lamedb import ServiceDatabase
from TKGSNavigator.core.parser import Channel
from TKGSNavigator.core.records import (
    FLAG_HD,
    FLAG_RADIO,
    LayoutMismatch,
    parse_record_payload,
    parse_record_sections,
)
from TKGSNavigator.core.sections import TableCollector
from TKGSNavigator.core.workflow import parse_table, preview


def collector_of(*payloads):
    collector = TableCollector()
    for number, payload in enumerate(payloads):
        collector.add(section(payload, number, len(payloads) - 1))
    return collector


class RecordLayoutTests(unittest.TestCase):
    def test_records_carry_every_field(self):
        payload = record(0x65, 1, "News HD", flags=FLAG_HD | 0x02, package=1) + record(
            0x66, 2, "Culture", package=4
        )
        first, second = parse_record_payload(payload)
        self.assertEqual((first.lcn, first.sid, first.tsid, first.onid), (1, 0x65, 1, 1))
        self.assertEqual((first.hd, first.fta, first.radio, first.package), (True, True, False, 1))
        self.assertEqual((second.name, second.hd, second.package), ("Culture", False, 4))

    def test_layout_is_rejected_on_any_misfit(self):
        good = record(0x65, 1, "News")
        for payload in (
            good + b"\x00",
            good[:-1],
            record(0x65, 1, "News", flags=0x10),
            record(0, 1, "News"),
            record(0x65, 0, "News"),
            b"",
        ):
            with self.assertRaises(LayoutMismatch):
                parse_record_payload(payload)

    def test_observed_layout_fixtures_are_never_taken_for_records(self):
        with self.assertRaises(LayoutMismatch):
            parse_record_sections(sample_sections())

    def test_random_payloads_only_raise_layout_mismatch(self):
        rng = random.Random(7)
        for size in range(400):
            try:
                parse_record_payload(bytes(rng.randrange(256) for _ in range(size)))
            except LayoutMismatch:
                pass

    def test_hd_sd_variants_kept_duplicates_and_conflicts_resolved(self):
        payload = (
            record(0x65, 1, "News HD", flags=FLAG_HD)
            + record(0x67, 1, "News")
            + record(0x66, 5, "Culture")
            + record(0x66, 3, "Culture")
            + record(0x68, 9, "A")
            + record(0x69, 9, "B")
        )
        result = parse_record_sections([section(payload)])
        self.assertEqual(
            [(c.lcn, c.sid) for c in result.channels], [(1, 0x65), (1, 0x67), (3, 0x66)]
        )
        self.assertEqual(result.warnings, ["LCN 9 points to multiple services; skipped."])


class LayoutSelectionTests(unittest.TestCase):
    def test_records_chosen_when_keys_exist_in_lamedb(self):
        collector = collector_of(
            record(0x65, 1, "News HD", flags=FLAG_HD), record(0x66, 2, "Culture")
        )
        report = preview(collector, ServiceDatabase.parse(LAMEDB4))
        self.assertEqual(report["layout"]["layout"], "records")
        self.assertEqual(report["layout"]["lamedb_hit_rate"], 1.0)
        self.assertEqual([item["lcn"] for item in report["matched"]], [1, 2])
        self.assertTrue(report["can_apply"])

    def test_records_ignored_when_keys_are_unknown(self):
        collector = collector_of(record(0x65, 1, "News", tsid=9, onid=9))
        _, evidence = parse_table(collector, ServiceDatabase.parse(LAMEDB4))
        self.assertEqual(evidence["layout"], "observed")
        self.assertTrue(evidence["records_parsed"])
        self.assertEqual(evidence["lamedb_hit_rate"], 0.0)

    def test_observed_capture_reports_why_records_were_rejected(self):
        collector = TableCollector()
        for raw in sample_sections():
            collector.add(raw)
        report = preview(collector, ServiceDatabase.parse(LAMEDB4))
        self.assertEqual(report["layout"]["layout"], "observed")
        self.assertIn("records_rejected", report["layout"])
        self.assertTrue(report["can_apply"])


class FullKeyMatchTests(unittest.TestCase):
    def test_tsid_and_onid_narrow_the_match(self):
        database = ServiceDatabase.parse(LAMEDB4)
        self.assertEqual(len(database.candidates(Channel(1, 0x65, "N", tsid=1, onid=1))), 1)
        self.assertEqual(database.candidates(Channel(1, 0x65, "N", tsid=2, onid=1)), [])
        self.assertEqual(database.candidates(Channel(1, 0x65, "N", onid=7)), [])

    def test_radio_channels_match_radio_types_only(self):
        database = ServiceDatabase.parse(
            LAMEDB4.replace("0066:01a40000:0001:0001:1:0", "0066:01a40000:0001:0001:2:0")
        )
        self.assertEqual(len(database.candidates(Channel(2, 0x66, "R", radio=True))), 1)
        self.assertEqual(database.candidates(Channel(2, 0x66, "R")), [])
        self.assertEqual(database.candidates(Channel(1, 0x65, "N", radio=True)), [])
        self.assertTrue(FLAG_RADIO)


if __name__ == "__main__":
    unittest.main()
