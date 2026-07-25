import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.data_providers import (  # noqa: E402
    OfficialResearchCollectionError,
    OfficialResearchCollector,
)


class OfficialResearchCollectorTests(unittest.TestCase):
    def test_collects_matching_official_article(self) -> None:
        index_url = "https://official.example/releases/"
        article_url = "https://official.example/releases/202607/t20260724_1.html"
        pages = {
            index_url: b"""
                <html><a href="./202607/t20260724_1.html">Official release</a>
                <a href="/navigation.html">Navigation</a></html>
            """,
            article_url: """
                <html><h1>Official release title</h1>
                <div class="TRS_Editor">
                The official publication reports measured output, prices, and dates
                using a clearly stated statistical methodology for the current period.
                </div></html>
            """.encode(),
        }
        collector = OfficialResearchCollector(
            source_id="official",
            index_url=index_url,
            article_url_pattern=r"/releases/20[0-9]{4}/t20[0-9]+_[0-9]+\.html$",
            content_markers=("trs_editor",),
            asset_symbols=("EQUITY",),
            fetch=pages.__getitem__,
        )
        results = collector.collect(max_items=2)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Official release title")
        self.assertEqual(results[0].published_at, "2026-07-24T00:00:00+08:00")
        self.assertIn("statistical methodology", results[0].content)

    def test_fails_when_page_structure_has_no_main_content(self) -> None:
        index_url = "https://official.example/releases/"
        article_url = "https://official.example/releases/202607/t20260724_1.html"
        pages = {
            index_url: b'<a href="./202607/t20260724_1.html">Release</a>',
            article_url: b"<html><h1>Release</h1><nav>Only navigation</nav></html>",
        }
        collector = OfficialResearchCollector(
            source_id="official",
            index_url=index_url,
            article_url_pattern=r"/releases/20[0-9]{4}/t20[0-9]+_[0-9]+\.html$",
            content_markers=("trs_editor",),
            asset_symbols=("EQUITY",),
            fetch=pages.__getitem__,
        )
        with self.assertRaisesRegex(OfficialResearchCollectionError, "main content"):
            collector.collect()


if __name__ == "__main__":
    unittest.main()
