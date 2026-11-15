"""Collect JRA official result PDFs and normalize meeting metadata."""
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from urllib.parse import urljoin
from datetime import datetime
from pathlib import Path
import json
import re

YEAR = datetime.now().year
PAGE = f"https://www.jra.go.jp/datafile/seiseki/report/{YEAR}.html"
OUT = Path("data/catalog.json")
TRACKS = ("札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉")


def parse_metadata(label, context="", year=YEAR):
    """Extract fields explicitly present in the link label or nearby page context."""
    text = f"{context} {label}".strip()
    metadata = {}
    date_match = re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if date_match:
        month, day = map(int, date_match.groups())
        try:
            metadata["date"] = datetime(year, month, day).date().isoformat()
        except ValueError:
            pass
    meeting = re.search(r"(\d+)回", label)
    day_match = re.search(r"(\d+)日(?:\s|$|（)", label)
    track = next((name for name in TRACKS if name in label), None)
    if track:
        metadata["track"] = track
    if meeting:
        metadata["meeting_number"] = int(meeting.group(1))
    if day_match:
        metadata["meeting_day"] = int(day_match.group(1))
    return metadata


def merge_catalog_items(items):
    """Merge duplicate PDF URLs while preserving metadata found in either listing."""
    merged = {}
    for item in items:
        url = item["url"]
        if url not in merged:
            merged[url] = dict(item)
            continue
        current = merged[url]
        for key, value in item.items():
            if value not in (None, "") and current.get(key) in (None, ""):
                current[key] = value
        current["labels"] = sorted(set(current.get("labels", [current.get("label", "")])) |
                                   set(item.get("labels", [item.get("label", "")])))
    return list(merged.values())


class Parser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.href = None
        self.parts = []
        self.links = []
        self.context_parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.parts = []

    def handle_data(self, data):
        value = " ".join(data.split())
        if not value:
            return
        if self.href is not None:
            self.parts.append(value)
        else:
            self.context_parts.append(value)

    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            context = " ".join(self.context_parts[-12:])
            self.links.append((self.href, " ".join(self.parts), context))
            self.href = None
            self.parts = []


def main():
    req = Request(PAGE, headers={"User-Agent": "JRA-HORSE-DATA-LAB/0.3"})
    with urlopen(req, timeout=30) as response:
        raw = response.read()
        charset = response.headers.get_content_charset()

    if not charset:
        match = re.search(br'charset=["\']?([a-zA-Z0-9_-]+)', raw[:5000], re.I)
        charset = match.group(1).decode() if match else "cp932"
    try:
        html = raw.decode(charset)
    except (UnicodeDecodeError, LookupError):
        html = raw.decode("cp932", errors="replace")

    parser = Parser()
    parser.feed(html)
    items = []
    for href, label, context in parser.links:
        url = urljoin(PAGE, href)
        lower_url = url.lower()
        if ".pdf" not in lower_url:
            continue
        if "-hyo-" in lower_url or "票数" in label or "発売票数" in label:
            continue
        metadata = parse_metadata(label, context, YEAR)
        item = {"label": label or "JRA公式成績PDF", "url": url, "year": YEAR}
        item.update(metadata)
        items.append(item)

    items = merge_catalog_items(items)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source": PAGE,
        "checked_at": datetime.now().astimezone().isoformat(),
        "notice": "JRA公式成績PDFリンク一覧。開催情報は公式ページに明記された範囲で付与。",
        "items": items
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Collected {len(items)} unique result PDF links")


if __name__ == "__main__":
    main()
