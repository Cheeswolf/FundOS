import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.data_providers import (  # noqa: E402
    CollectedResearchPage,
    OfficialResearchCollectionError,
)
from fundos.domain import Asset  # noqa: E402
from fundos.services import register_research_sources, run_evidence_collection  # noqa: E402
from fundos.storage import Database  # noqa: E402


class FakeCollector:
    source_id = "official"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def collect(self, *, max_items: int):
        if self.fail:
            raise OfficialResearchCollectionError("structure changed")
        return [
            CollectedResearchPage(
                "official",
                "Official release",
                "https://official.example/releases/202607/t20260724_1.html",
                "2026-07-24T00:00:00+08:00",
                ("BOND",),
                "The official source reports a sufficiently detailed factual observation.",
            )
        ][:max_items]


class EvidenceCollectionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Database(Path(self.temporary.name) / "collection.sqlite3")
        self.database.initialize()
        self.database.upsert_assets([Asset("BOND", "Bond", "bond")])
        register_research_sources(
            self.database,
            [{
                "source_id": "official",
                "name": "Official",
                "source_type": "official",
                "allowed_domains": ["official.example"],
                "asset_symbols": ["BOND"],
                "license_note": "Official public information.",
            }],
        )

    def test_records_success_and_duplicate_collection(self) -> None:
        first = run_evidence_collection(
            self.database, collector=FakeCollector(), max_items=1
        )
        second = run_evidence_collection(
            self.database, collector=FakeCollector(), max_items=1
        )
        self.assertEqual((first.imported_count, first.duplicate_count), (1, 0))
        self.assertEqual((second.imported_count, second.duplicate_count), (0, 1))
        self.assertEqual(
            len(self.database.fetch_all("SELECT * FROM evidence_collection_runs")), 2
        )

    def test_records_failed_collection_without_creating_evidence(self) -> None:
        result = run_evidence_collection(
            self.database, collector=FakeCollector(fail=True), max_items=1
        )
        self.assertEqual(result.status, "failed")
        self.assertIn("structure changed", result.error_message)
        self.assertEqual(
            len(self.database.fetch_all("SELECT * FROM raw_research_evidence")), 0
        )


if __name__ == "__main__":
    unittest.main()
