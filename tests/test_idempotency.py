import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.api.main import _idempotent  # noqa: E402
from fundos.storage import Database  # noqa: E402


class IdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Database(
            Path(self.temporary_directory.name) / "idempotency.sqlite3"
        )
        self.database.initialize()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_rolls_back_business_write_when_action_fails(self) -> None:
        def failing_action(connection):
            connection.execute(
                """
                INSERT INTO portfolio_products (
                    product_id, name, benchmark_symbol, created_at
                ) VALUES ('P1', 'Portfolio', 'BM', '2026-07-26T00:00:00')
                """
            )
            raise RuntimeError("simulated process failure")

        with self.assertRaisesRegex(RuntimeError, "simulated process failure"):
            _idempotent(
                self.database,
                key="create-P1",
                operation="create_product",
                payload={"product_id": "P1"},
                action=failing_action,
            )

        self.assertEqual(
            self.database.fetch_all(
                "SELECT * FROM portfolio_products WHERE product_id = 'P1'"
            ),
            [],
        )
        self.assertEqual(
            self.database.fetch_all(
                "SELECT * FROM idempotency_records WHERE idempotency_key = 'create-P1'"
            ),
            [],
        )

    def test_concurrent_retries_execute_business_action_once(self) -> None:
        action_count = 0
        count_lock = threading.Lock()

        def action(connection):
            nonlocal action_count
            with count_lock:
                action_count += 1
            connection.execute(
                """
                INSERT INTO portfolio_products (
                    product_id, name, benchmark_symbol, created_at
                ) VALUES ('P1', 'Portfolio', 'BM', '2026-07-26T00:00:00')
                """
            )
            return {"product_id": "P1"}

        def request():
            return _idempotent(
                self.database,
                key="create-P1",
                operation="create_product",
                payload={"product_id": "P1"},
                action=action,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: request(), range(2)))

        self.assertEqual(results, [{"product_id": "P1"}, {"product_id": "P1"}])
        self.assertEqual(action_count, 1)
        self.assertEqual(
            len(self.database.fetch_all("SELECT * FROM portfolio_products")),
            1,
        )


if __name__ == "__main__":
    unittest.main()
