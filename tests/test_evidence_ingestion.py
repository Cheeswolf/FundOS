import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.domain import Asset  # noqa: E402
from fundos.services import import_raw_research_evidence, register_research_sources  # noqa: E402
from fundos.storage import Database  # noqa: E402


class EvidenceIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary_directory.name) / "evidence.sqlite3")
        self.database.initialize()
        self.database.upsert_assets(
            [Asset("BOND", "Bond", "bond"), Asset("CASH", "Cash", "cash")]
        )
        register_research_sources(
            self.database,
            [
                {
                    "source_id": "central-bank",
                    "name": "Central Bank",
                    "source_type": "official",
                    "allowed_domains": ["official.example"],
                    "asset_symbols": ["BOND", "CASH"],
                    "license_note": "Official public information; verify reuse terms.",
                }
            ],
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def item(self) -> dict:
        return {
            "source_id": "central-bank",
            "title": "Official operation notice",
            "url": "https://data.official.example/releases/1",
            "published_at": "2026-07-24T09:00:00+08:00",
            "asset_symbols": ["BOND", "CASH"],
            "content": "The official release reports a 100 unit operation on the stated date.",
        }

    def test_imports_pending_immutable_evidence_and_deduplicates(self) -> None:
        retrieved = datetime(2026, 7, 24, 2, 0, tzinfo=timezone.utc)
        first = import_raw_research_evidence(
            self.database, self.item(), retrieved_at=retrieved
        )
        second = import_raw_research_evidence(
            self.database, self.item(), retrieved_at=retrieved
        )
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.raw_evidence_id, second.raw_evidence_id)
        row = self.database.fetch_all("SELECT * FROM raw_research_evidence")[0]
        self.assertEqual(row["review_status"], "pending")
        self.assertEqual(json.loads(row["asset_symbols"]), ["BOND", "CASH"])
        self.assertEqual(len(row["content_sha256"]), 64)

    def test_rejects_url_outside_source_allowlist(self) -> None:
        item = self.item()
        item["url"] = "https://lookalike.example/releases/1"
        with self.assertRaisesRegex(ValueError, "outside the source allowlist"):
            import_raw_research_evidence(
                self.database,
                item,
                retrieved_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
            )

    def test_rejects_assets_outside_source_coverage(self) -> None:
        item = self.item()
        item["asset_symbols"].append("GOLD")
        with self.assertRaisesRegex(ValueError, "outside the registered source coverage"):
            import_raw_research_evidence(
                self.database,
                item,
                retrieved_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
            )


if __name__ == "__main__":
    unittest.main()
