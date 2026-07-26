import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.storage.data_migration import dependency_order, rows_digest  # noqa: E402


class DataMigrationTests(unittest.TestCase):
    def test_orders_parent_tables_before_children(self) -> None:
        dependencies = {
            "weights": {"versions", "assets"},
            "versions": {"products"},
            "assets": set(),
            "products": set(),
        }

        ordered = dependency_order(dependencies)

        self.assertLess(ordered.index("products"), ordered.index("versions"))
        self.assertLess(ordered.index("versions"), ordered.index("weights"))
        self.assertLess(ordered.index("assets"), ordered.index("weights"))

    def test_dependency_order_is_deterministic_for_independent_tables(self) -> None:
        self.assertEqual(
            dependency_order({"zeta": set(), "alpha": set()}),
            ("alpha", "zeta"),
        )

    def test_rejects_cyclic_dependencies(self) -> None:
        with self.assertRaisesRegex(ValueError, "cyclic"):
            dependency_order({"first": {"second"}, "second": {"first"}})

    def test_digest_is_order_independent_and_content_sensitive(self) -> None:
        first = rows_digest([{"id": 1, "value": "甲"}, {"id": 2, "value": "乙"}])
        reordered = rows_digest(
            [{"value": "乙", "id": 2}, {"value": "甲", "id": 1}]
        )
        changed = rows_digest([{"id": 1, "value": "甲"}])

        self.assertEqual(first, reordered)
        self.assertNotEqual(first, changed)


if __name__ == "__main__":
    unittest.main()
