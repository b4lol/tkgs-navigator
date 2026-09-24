import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from TKGSNavigator.core.bouquets import SPACER, PlanOptions, align, plan_bouquets
from TKGSNavigator.core.lamedb import Service
from TKGSNavigator.core.parser import Channel
from TKGSNavigator.core.storage import (
    BACKUP_DIR,
    BOUQUET,
    INDEX,
    RADIO_INDEX,
    BouquetFile,
    BouquetPlan,
    BouquetStore,
)

KEY = (0x01A40000, 1, 1)


def pair(lcn, sid, name, kind=1, **fields):
    return Channel(lcn, sid, name, **fields), Service(sid, KEY, kind, name)


def names(plan):
    return [file.filename for file in plan.files]


class PlannerTests(unittest.TestCase):
    def test_observed_channels_give_one_lcn_ordered_bouquet(self):
        plan = plan_bouquets([pair(2, 0x66, "B"), pair(1, 0x65, "A")])
        self.assertEqual(names(plan), [BOUQUET])
        self.assertEqual(
            plan.files[0].content,
            b"#NAME TKGS Navigator\n"
            b"#SERVICE 1:0:1:65:1:1:1A40000:0:0:0:\n#DESCRIPTION A\n"
            b"#SERVICE 1:0:1:66:1:1:1A40000:0:0:0:\n#DESCRIPTION B\n",
        )

    def test_hd_and_sd_variants_split_into_main_and_alternate(self):
        matched = [
            pair(1, 0x65, "News HD", hd=True),
            pair(1, 0x67, "News", hd=False),
            pair(2, 0x66, "C", hd=False),
        ]
        plan = plan_bouquets(matched)
        self.assertEqual(names(plan), [BOUQUET, "userbouquet.tkgs_navigator_sd.tv"])
        self.assertIn(b"#DESCRIPTION News HD", plan.files[0].content)
        self.assertIn(b"#DESCRIPTION News\n", plan.files[1].content)
        self.assertEqual([f.services for f in plan.files], [2, 2])
        sd_first = plan_bouquets(matched, PlanOptions(prefer_hd=False))
        self.assertEqual(names(sd_first), [BOUQUET, "userbouquet.tkgs_navigator_hd.tv"])
        self.assertIn(b"#DESCRIPTION News\n", sd_first.files[0].content)

    def test_categories_and_radio_bouquets(self):
        matched = [
            pair(1, 0x65, "A", package=1),
            pair(2, 0x66, "B", package=1),
            pair(3, 0x67, "C", package=42),
            pair(900, 0x70, "Radio A", kind=2, radio=True),
        ]
        self.assertEqual(
            names(plan_bouquets(matched)), [BOUQUET, "userbouquet.tkgs_navigator_radio.radio"]
        )
        plan = plan_bouquets(matched, PlanOptions(categories=True))
        titles = {file.filename: (file.title, file.services) for file in plan.files}
        self.assertEqual(titles["userbouquet.tkgs_navigator_pkg1.tv"], ("TKGS Navigator - News", 2))
        self.assertEqual(
            titles["userbouquet.tkgs_navigator_pkg42.tv"], ("TKGS Navigator - Package 42", 1)
        )
        self.assertEqual(titles[BOUQUET][1], 3)

    def test_alignment_puts_each_channel_at_its_lcn(self):
        rows = align([pair(1, 1, "A"), pair(4, 4, "D"), pair(2500, 5, "E")])
        self.assertEqual([row[0].lcn if row else None for row in rows], [1, None, None, 4, 2500])
        content = plan_bouquets([pair(3, 3, "C")], PlanOptions(align_lcn=True)).files[0].content
        self.assertEqual(content.count(("#SERVICE " + SPACER).encode()), 2)

    def test_nothing_matched_and_invalid_names_are_refused(self):
        with self.assertRaises(ValueError):
            plan_bouquets([])
        for name in ("userbouquet.favourites.tv", "../lamedb", "userbouquet.tkgs_navigator.tv/x"):
            with self.assertRaises(ValueError):
                BouquetFile(name, "T", b"", 0)
        file = BouquetFile(BOUQUET, "T", b"", 0)
        with self.assertRaises(ValueError):
            BouquetPlan((file, file))


class MultiBouquetStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = BouquetStore(self.root)
        self.index = (
            b"#NAME Bouquets (TV)\n"
            b"#SERVICE 1:7:1:0:0:0:0:0:0:0:"
            b'FROM BOUQUET "userbouquet.favourites.tv" ORDER BY bouquet\n'
        )
        (self.root / INDEX).write_bytes(self.index)
        (self.root / "userbouquet.favourites.tv").write_bytes(b"#NAME Favourites\n")
        self.full = plan_bouquets(
            [pair(1, 0x65, "A", package=1), pair(900, 0x70, "R", kind=2, radio=True)],
            PlanOptions(categories=True),
        )

    def read(self, name):
        path = self.root / name
        return path.read_bytes() if path.exists() else None

    def test_apply_links_every_file_and_restore_undoes_all(self):
        ident = self.store.apply(self.full)
        index = self.read(INDEX).decode()
        self.assertIn('"userbouquet.favourites.tv"', index)
        self.assertLess(index.index("favourites"), index.index('"userbouquet.tkgs_navigator.tv"'))
        self.assertIn('"userbouquet.tkgs_navigator_pkg1.tv"', index)
        radio = self.read(RADIO_INDEX).decode()
        self.assertTrue(radio.startswith("#NAME Bouquets (Radio)\n#SERVICE 1:7:2:"))
        self.assertIsNone(self.store.apply(self.full))
        self.store.restore(ident)
        self.assertEqual(self.read(INDEX), self.index)
        self.assertIsNone(self.read(RADIO_INDEX))
        self.assertEqual(
            sorted(p.name for p in self.root.glob("userbouquet.*")), ["userbouquet.favourites.tv"]
        )

    def test_smaller_plan_removes_stale_bouquets_and_links_only(self):
        self.store.apply(self.full)
        self.store.apply(plan_bouquets([pair(1, 0x65, "A", package=1)]))
        self.assertIsNone(self.read("userbouquet.tkgs_navigator_pkg1.tv"))
        self.assertIsNone(self.read("userbouquet.tkgs_navigator_radio.radio"))
        self.assertNotIn(b"pkg1", self.read(INDEX))
        self.assertEqual(self.read(RADIO_INDEX), b"#NAME Bouquets (Radio)\n")
        self.assertEqual(self.read("userbouquet.favourites.tv"), b"#NAME Favourites\n")

    def test_first_places_links_after_the_index_name(self):
        self.store.apply(plan_bouquets([pair(1, 0x65, "A")], PlanOptions(first=True)))
        lines = self.read(INDEX).splitlines()
        self.assertEqual(lines[0], b"#NAME Bouquets (TV)")
        self.assertIn(b'"userbouquet.tkgs_navigator.tv"', lines[1])

    def test_index_failure_rolls_back_every_bouquet(self):
        from TKGSNavigator.core import storage

        real = storage.atomic_write

        def fail_index(path, data):
            if Path(path).name == RADIO_INDEX:
                raise OSError("simulated disk failure")
            return real(path, data)

        with patch.object(storage, "atomic_write", side_effect=fail_index):
            with self.assertRaises(OSError):
                self.store.apply(self.full)
        self.assertEqual(self.read(INDEX), self.index)
        self.assertEqual(
            sorted(p.name for p in self.root.glob("userbouquet.*")), ["userbouquet.favourites.tv"]
        )

    def backup(self, ident, manifest, files):
        folder = self.root / BACKUP_DIR / ident
        folder.mkdir(parents=True)
        for name, content in files.items():
            (folder / name).write_bytes(content)
        (folder / "manifest.json").write_text(json.dumps(manifest))

    def test_schema_one_backup_from_0_3_0_is_restored(self):
        old_index = b"#NAME Bouquets (TV)\n"
        (self.root / BOUQUET).write_bytes(b"#NAME old\n")
        digest = lambda data: hashlib.sha256(data).hexdigest()  # noqa: E731
        self.backup(
            "20260101T000000Z-0123abcd",
            {
                "schema": 1,
                "before": {BOUQUET: None, INDEX: digest(old_index)},
                "after": {BOUQUET: digest(b"#NAME old\n"), INDEX: digest(self.index)},
            },
            {INDEX: old_index},
        )
        self.store.restore("20260101T000000Z-0123abcd")
        self.assertIsNone(self.read(BOUQUET))
        self.assertEqual(self.read(INDEX), old_index)

    def test_manifest_naming_foreign_files_is_refused(self):
        for name in ("lamedb", "../outside.tv", "userbouquet.favourites.tv"):
            ident = "20260101T000000Z-%08x" % abs(hash(name) % 0xFFFFFFFF)
            self.backup(ident, {"schema": 2, "before": {name: None}, "after": {name: None}}, {})
            with self.assertRaises(ValueError):
                self.store.restore(ident)
        self.assertEqual(self.read("userbouquet.favourites.tv"), b"#NAME Favourites\n")


if __name__ == "__main__":
    unittest.main()
