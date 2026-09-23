"""Regression tests for the generated claim-review schema patches."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

EXTENSION_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EXTENSION_ROOT / "tools"))

import split_schema_sql  # noqa: E402


class SchemaSqlSplitterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tables, cls.indexes = split_schema_sql.expected_schema()

    def test_mysql_splits_five_tables_without_losing_options_or_prefixes(self) -> None:
        aggregate = (EXTENSION_ROOT / "sql/mysql/tables-generated.sql").read_text(
            encoding="utf-8"
        )
        patches = split_schema_sql.parse_generated_sql(
            "mysql", aggregate, self.tables, self.indexes
        )

        self.assertEqual(list(patches), list(self.tables))
        self.assertIn("CREATE TABLE /*_*/wikikg_snapshot", patches["wikikg_snapshot"])
        self.assertIn("/*$wgDBTableOptions*/;", patches["wikikg_snapshot"])
        self.assertIn("INDEX wikikg_snapshot_created", patches["wikikg_snapshot"])

    def test_sqlite_indexes_stay_with_their_table_patches(self) -> None:
        aggregate = (EXTENSION_ROOT / "sql/sqlite/tables-generated.sql").read_text(
            encoding="utf-8"
        )
        patches = split_schema_sql.parse_generated_sql(
            "sqlite", aggregate, self.tables, self.indexes
        )

        self.assertIn("CREATE INDEX wikikg_snapshot_created", patches["wikikg_snapshot"])
        self.assertIn("ON /*_*/wikikg_snapshot", patches["wikikg_snapshot"])
        self.assertIn("CREATE INDEX wikikg_evidence_revision", patches["wikikg_evidence"])
        self.assertIn(
            "CREATE INDEX wikikg_review_reviewer_timestamp",
            patches["wikikg_review_event"],
        )
        self.assertNotIn("CREATE INDEX", patches["wikikg_source_page"])

    def test_unexpected_sql_statement_is_rejected(self) -> None:
        aggregate = (EXTENSION_ROOT / "sql/mysql/tables-generated.sql").read_text(
            encoding="utf-8"
        )

        with self.assertRaisesRegex(ValueError, "unexpected SQL"):
            split_schema_sql.parse_generated_sql(
                "mysql", aggregate + "\nDROP TABLE wikikg_claim;\n", self.tables, self.indexes
            )

    def test_aggregate_table_order_must_match_schema_order(self) -> None:
        aggregate = (EXTENSION_ROOT / "sql/mysql/tables-generated.sql").read_text(
            encoding="utf-8"
        )
        table_number = 0

        def swap_source_and_claim(match: re.Match[str]) -> str:
            nonlocal table_number
            table_number += 1
            table = match.group("table")
            if table_number == 2:
                table = "wikikg_claim"
            elif table_number == 3:
                table = "wikikg_source_page"
            return f"CREATE TABLE /*_*/{table}"

        reordered = re.sub(
            r"(?m)^CREATE TABLE /\*_\*/(?P<table>[A-Za-z_][A-Za-z0-9_]*)",
            swap_source_and_claim,
            aggregate,
        )

        with self.assertRaisesRegex(ValueError, "order/content"):
            split_schema_sql.parse_generated_sql(
                "mysql", reordered, self.tables, self.indexes
            )


if __name__ == "__main__":
    unittest.main()
