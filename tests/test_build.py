"""Offline tests for the build pipeline.

These run against a hand-written fixture scrape, so they need no network and
no downloaded data.  Run them with:  python3 -m unittest discover tests
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wowdb import query  # noqa: E402
from wowdb.build import build, display_column  # noqa: E402
from wowdb.wago import _label_map  # noqa: E402

ITEM_SPARSE_CSV = """ID,Display_lang,OverallQualityID,ItemLevel,RequiredLevel,InventoryType,Bonding,ExpansionID,Description_lang,Stackable,MaxCount,SellPrice,BuyPrice,ItemSet,StartQuestID,RequiredSkill,Flags_0
19019,"Thunderfury, Blessed Blade of the Windseeker",5,80,60,13,1,0,"Wicked bolts of lightning",1,1,100,500,0,0,0,3
1234,Bag of Nothing,1,1,1,18,0,0,,20,0,1,5,0,0,0,0
5678,"Quest Widget",1,1,1,0,4,0,,1,1,0,0,0,42,0,1
"""

ITEM_CSV = """ID,ClassID,SubclassID,IconFileDataID
19019,2,7,1234
1234,1,0,99
5678,15,0,7
"""

ITEM_CLASS_CSV = """ID,ClassName_lang,ClassID
1,Weapon,2
2,Container,1
3,Miscellaneous,15
"""

ITEM_SUBCLASS_CSV = """DisplayName_lang,VerboseName_lang,ID,ClassID,SubClassID
Sword,One-Handed Swords,1,2,7
Bag,Bag,2,1,0
Junk,Junk,3,15,0
"""

# A table with no ID column and a float, to exercise type inference.
FLOATS_CSV = """Name,Scale,Count
alpha,1.5,10
beta,2.25,20
"""

ITEM_SPARSE_META = {
    "name": "ItemSparse",
    "columns": ["ID", "Display_lang", "OverallQualityID", "ItemLevel"],
    "foreign_keys": {"StartQuestID": ["QuestV2", "ID"], "ItemSet": ["ItemSet", "ID"]},
    "enums": {
        "OverallQualityID": {"1": "Common", "5": "Legendary"},
        "Bonding": {"1": "Bind On Acquire", "4": "Quest Item"},
    },
    "flags": {"Flags_0": {"1": "NO_PICKUP", "2": "CONJURED"}},
}


def write_fixture(root: Path) -> None:
    (root / "csv").mkdir(parents=True)
    (root / "meta").mkdir(parents=True)

    files = {
        "ItemSparse": ITEM_SPARSE_CSV,
        "Item": ITEM_CSV,
        "ItemClass": ITEM_CLASS_CSV,
        "ItemSubClass": ITEM_SUBCLASS_CSV,
        "Floats": FLOATS_CSV,
    }
    for name, content in files.items():
        (root / "csv" / f"{name}.csv").write_text(content, encoding="utf-8")

    (root / "meta" / "ItemSparse.json").write_text(json.dumps(ITEM_SPARSE_META))
    for name in ("Item", "ItemClass", "ItemSubClass", "Floats"):
        (root / "meta" / f"{name}.json").write_text(json.dumps({
            "name": name, "columns": [], "foreign_keys": {}, "enums": {}, "flags": {},
        }))

    (root / "manifest.json").write_text(json.dumps({
        "build": "12.0.7.68887", "product": "wow", "locale": "enUS",
        "finished_at": "2026-07-30 00:00:00", "tables": {},
    }))


class BuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        write_fixture(root / "data")
        cls.db_path = root / "wow.db"
        cls.report = build(root / "data", cls.db_path, log=lambda *a: None)
        cls.connection = sqlite3.connect(f"file:{cls.db_path}?mode=ro", uri=True)
        cls.connection.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()
        cls._tmp.cleanup()

    def one(self, sql, *params):
        return self.connection.execute(sql, params).fetchone()

    # ---------------------------------------------------------------- raw layer

    def test_tables_are_imported(self):
        self.assertEqual(self.report.tables, 5)
        self.assertEqual(self.one("SELECT count(*) FROM ItemSparse")[0], 3)

    def test_types_are_inferred(self):
        types = {
            row["name"]: row["type"]
            for row in self.connection.execute("PRAGMA table_info(Floats)")
        }
        self.assertEqual(types["Name"], "TEXT")
        self.assertEqual(types["Scale"], "REAL")
        self.assertEqual(types["Count"], "INTEGER")

    def test_id_is_the_primary_key(self):
        info = list(self.connection.execute("PRAGMA table_info(ItemSparse)"))
        primary = [row["name"] for row in info if row["pk"]]
        self.assertEqual(primary, ["ID"])

    def test_empty_cells_become_null(self):
        row = self.one("SELECT Description_lang FROM ItemSparse WHERE ID = 1234")
        self.assertIsNone(row[0])

    def test_table_without_id_still_imports(self):
        self.assertEqual(self.one("SELECT count(*) FROM Floats")[0], 2)

    # --------------------------------------------------------------- meta layer

    def test_metadata_records_columns_and_foreign_keys(self):
        row = self.one(
            "SELECT fk_table, fk_column FROM db2_column "
            "WHERE table_name = 'ItemSparse' AND column_name = 'StartQuestID'"
        )
        self.assertEqual((row["fk_table"], row["fk_column"]), ("QuestV2", "ID"))

    def test_enum_labels_are_stored(self):
        row = self.one(
            "SELECT label FROM db2_enum WHERE table_name = 'ItemSparse' "
            "AND column_name = 'OverallQualityID' AND value = 5"
        )
        self.assertEqual(row["label"], "Legendary")

    def test_build_info_records_the_source_build(self):
        info = query.info(self.connection)
        self.assertEqual(info["wow_build"], "12.0.7.68887")
        self.assertEqual(info["locale"], "enUS")

    # -------------------------------------------------------------- readability

    def test_decoded_view_labels_enums(self):
        row = self.one(
            "SELECT OverallQualityID_label, Bonding_label FROM v_ItemSparse "
            "WHERE ID = 19019"
        )
        self.assertEqual(row["OverallQualityID_label"], "Legendary")
        self.assertEqual(row["Bonding_label"], "Bind On Acquire")

    def test_decoded_view_expands_flag_bitmasks(self):
        # Flags_0 = 3 -> both bit 1 and bit 2 are set.
        row = self.one("SELECT Flags_0_flags FROM v_ItemSparse WHERE ID = 19019")
        self.assertEqual(sorted(row[0].split(", ")), ["CONJURED", "NO_PICKUP"])

    def test_curated_item_view_joins_class_names(self):
        row = self.one("SELECT * FROM items WHERE item_id = 19019")
        self.assertEqual(row["name"], "Thunderfury, Blessed Blade of the Windseeker")
        self.assertEqual(row["quality"], "Legendary")
        self.assertEqual(row["class"], "Weapon")
        self.assertEqual(row["subclass"], "Sword")

    def test_views_missing_dependencies_are_skipped_not_failed(self):
        # The fixture has no Spell tables, so 'spells' must be skipped cleanly.
        skipped = dict((name, note) for name, note in self.report.skipped_views)
        self.assertIn("spells", skipped)
        self.assertIn("items", self.report.curated_views)
        row = self.one("SELECT status FROM wowdb_view WHERE view_name = 'spells'")
        self.assertEqual(row["status"], "skipped")

    # ------------------------------------------------------------------ search

    def test_search_finds_text_across_tables(self):
        hits = query.search(self.connection, "Thunderfury")
        self.assertTrue(any(hit["table_name"] == "ItemSparse" for hit in hits))

    def test_search_is_prefix_matched(self):
        self.assertTrue(query.search(self.connection, "Thunder"))

    def test_row_label_resolves_a_display_name(self):
        label = query.row_label(self.connection, "ItemSparse", 19019)
        self.assertEqual(label, "Thunderfury, Blessed Blade of the Windseeker")


class HelperTest(unittest.TestCase):
    def test_display_column_prefers_name_over_description(self):
        self.assertEqual(
            display_column(["ID", "Description_lang", "Name_lang"]), "Name_lang"
        )

    def test_display_column_falls_back_to_any_localised_column(self):
        self.assertEqual(display_column(["ID", "Reward_lang"]), "Reward_lang")

    def test_display_column_may_be_absent(self):
        self.assertIsNone(display_column(["ID", "Flags"]))

    def test_array_columns_are_normalised_to_csv_header_names(self):
        labels = _label_map([{
            "column": "Flags[0]",
            "definitions": [{"value": "1", "name": "FIRST"}],
        }])
        self.assertEqual(labels, {"Flags_0": {1: "FIRST"}})

    def test_conditional_definitions_are_ignored(self):
        labels = _label_map([{
            "column": "Effect",
            "condition": "SpellEffect::Effect",
            "definitions": [{"value": "1", "name": "AMBIGUOUS"}],
        }])
        self.assertEqual(labels, {})

    def test_relation_exists_rejects_unknown_names(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE real_table (id INTEGER)")
        self.assertTrue(query.relation_exists(connection, "real_table"))
        self.assertFalse(query.relation_exists(connection, "'; DROP TABLE x; --"))


if __name__ == "__main__":
    unittest.main()
