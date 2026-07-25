from __future__ import annotations

import re
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Callable, Iterable, Mapping
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import certifi


class OfficialResearchCollectionError(RuntimeError):
    pass


Fetch = Callable[[str], bytes]


def verified_fetch(url: str) -> bytes:
    context = ssl.create_default_context(cafile=certifi.where())
    request = Request(url, headers={"User-Agent": "FundOS/0.1 research-evidence-collector"})
    with urlopen(request, timeout=30, context=context) as response:  # noqa: S310
        return response.read()


@dataclass(frozen=True, slots=True)
class CollectedResearchPage:
    source_id: str
    title: str
    url: str
    published_at: str
    asset_symbols: tuple[str, ...]
    content: str


@dataclass(frozen=True, slots=True)
class OfficialResearchCollector:
    source_id: str
    index_url: str
    article_url_pattern: str
    content_markers: tuple[str, ...]
    asset_symbols: tuple[str, ...]
    fetch: Fetch = verified_fetch

    @classmethod
    def from_mapping(
        cls,
        configuration: Mapping[str, object],
        *,
        fetch: Fetch = verified_fetch,
    ) -> "OfficialResearchCollector":
        return cls(
            source_id=str(configuration["source_id"]),
            index_url=str(configuration["index_url"]),
            article_url_pattern=str(configuration["article_url_pattern"]),
            content_markers=tuple(str(item) for item in configuration["content_markers"]),
            asset_symbols=tuple(str(item) for item in configuration["asset_symbols"]),
            fetch=fetch,
        )

    def collect(self, *, max_items: int = 5) -> list[CollectedResearchPage]:
        if max_items < 1:
            raise ValueError("collector max items must be positive")
        try:
            index_html = _decode_html(self.fetch(self.index_url))
        except (OSError, ValueError) as error:
            raise OfficialResearchCollectionError(
                f"{self.source_id} index request failed: {error}"
            ) from error
        parser = _LinkParser()
        parser.feed(index_html)
        pattern = re.compile(self.article_url_pattern)
        links: list[tuple[str, str]] = []
        seen: set[str] = set()
        for href, text in parser.links:
            url = urljoin(self.index_url, href)
            if url in seen or not pattern.search(url):
                continue
            seen.add(url)
            links.append((url, _normalize_text(text)))
            if len(links) >= max_items:
                break
        if not links:
            raise OfficialResearchCollectionError(
                f"{self.source_id} index contained no matching article links"
            )

        results: list[CollectedResearchPage] = []
        for url, link_title in links:
            try:
                article_html = _decode_html(self.fetch(url))
                article = _ArticleParser(self.content_markers)
                article.feed(article_html)
                title = article.title
                if not title or len(title) > 200 or "var title" in title.lower():
                    title = link_title
                content = _normalize_text(article.content)
                if not title or len(content) < 40:
                    raise ValueError("article title or main content is missing")
                date_match = re.search(r"(?:/|t)(20\d{2})(\d{2})(\d{2})", url)
                if date_match is None:
                    raise ValueError("article URL does not contain a publication date")
                published = datetime(
                    int(date_match.group(1)),
                    int(date_match.group(2)),
                    int(date_match.group(3)),
                    tzinfo=timezone(timedelta(hours=8)),
                )
                results.append(
                    CollectedResearchPage(
                        self.source_id,
                        title,
                        url,
                        published.isoformat(),
                        self.asset_symbols,
                        content[:12000],
                    )
                )
            except (OSError, ValueError) as error:
                raise OfficialResearchCollectionError(
                    f"{self.source_id} article parse failed for {url}: {error}"
                ) from error
        return results


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._text)))
            self._href = None
            self._text = []


class _ArticleParser(HTMLParser):
    def __init__(self, markers: Iterable[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.markers = tuple(item.lower() for item in markers)
        self._capture_depth = 0
        self._heading_depth = 0
        self._ignored_depth = 0
        self._content: list[str] = []
        self._title: list[str] = []

    @property
    def content(self) -> str:
        return " ".join(self._content)

    @property
    def title(self) -> str:
        return _normalize_text(" ".join(self._title))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        attributes = {key.lower(): (value or "").lower() for key, value in attrs}
        marker_text = f"{attributes.get('id', '')} {attributes.get('class', '')}"
        if self._capture_depth:
            self._capture_depth += 1
        elif any(marker in marker_text for marker in self.markers):
            self._capture_depth = 1
        if tag.lower() == "h1":
            self._heading_depth = 1
        elif self._heading_depth:
            self._heading_depth += 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._capture_depth:
            self._content.append(data)
        if self._heading_depth:
            self._title.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if self._capture_depth:
            self._capture_depth -= 1
        if self._heading_depth:
            self._heading_depth -= 1


def _decode_html(raw: bytes) -> str:
    head = raw[:2048].decode("ascii", errors="ignore")
    match = re.search(r"charset\s*=\s*[\"']?([A-Za-z0-9_-]+)", head, re.I)
    candidates = [match.group(1)] if match else []
    candidates.extend(["utf-8", "gb18030"])
    for encoding in candidates:
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    raise ValueError("official page is not decodable as UTF-8 or GB18030")


def _normalize_text(value: str) -> str:
    return " ".join(value.replace("\u3000", " ").replace("\xa0", " ").split())
