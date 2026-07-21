import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from fundos.api import create_app  # noqa: E402
from fundos.domain import PortfolioProduct  # noqa: E402


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.temporary_directory.name) / "api.sqlite3")
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.temporary_directory.cleanup()

    def test_health(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_lists_and_gets_product(self) -> None:
        self.app.state.database.create_product(
            PortfolioProduct("P1", "Test Portfolio", "BM", datetime.now())
        )
        listing = self.client.get("/products")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()[0]["product_id"], "P1")
        detail = self.client.get("/products/P1")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["product"]["name"], "Test Portfolio")

    def test_missing_resources_return_404(self) -> None:
        self.assertEqual(self.client.get("/products/missing").status_code, 404)
        self.assertEqual(self.client.get("/workflows/missing").status_code, 404)


if __name__ == "__main__":
    unittest.main()

