"""Discover official JRA graded-race result PDFs from annual replay listings."""
from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

BASE = "https://www.jra.go.jp/datafile/seiseki/replay/"
OUTPUT = Path("data/replay_catalog.json")


class _ListingParser(HTMLParser):
    def __init__(self, year: int):
        super().__init__(convert_charrefs=True)
        self.year = year
        self.rows: list[dict] = []
        self._row = None
        self._cell = None
        self._anchor = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self._row = {"cells": [], "links": []}
        elif self._row is not None and tag in {"td", "th"}:
            self._cell = {"text": ""}
        elif self._row is not None and tag == "a":
            self._anchor = {"href": attrs.get("href"), "text": ""}

    def handle_data(self, data):
        if self._cell is not None:
            self._cell["text"] += data
        if self._anchor is not None:
            self._anchor["text"] += data

    def handle_endtag(self, tag):
        if tag == "a" and self._anchor is not None:
            self._row["links"].append(self._anchor)
            self._anchor = None
        elif tag in {"td", "th"} and self._cell is not None:
            self._row["cells"].append(" ".join(self._cell["text"].split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None



class _HrefParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for key in ("href", "src", "data"):
            value = attrs.get(key)
            if value:
                self.hrefs.append(value)


def find_pdf_link(html: str, page_url: str) -> str | None:
    """Find a PDF linked from a JRA result page, including relative URLs."""
    parser = _HrefParser()
    parser.feed(html)
    for href in parser.hrefs:
        if re.search(r"\\.pdf(?:$|[?#])", href, re.I):
            return urljoin(page_url, href)
    return None


def fetch_result_pdf_url(result_url: str) -> str | None:
    """Resolve a result-page link to its underlying PDF, or return direct PDF URL."""
    if re.search(r"\\.pdf(?:$|[?#])", result_url, re.I):
        return result_url
    request = Request(result_url, headers={"User-Agent": "JRA-Horse-Data-Lab/1.0"})
    with urlopen(request, timeout=30) as response:
        content_type = response.headers.get("Content-Type", "").lower()
        body = response.read()
        final_url = response.geturl()
    if "pdf" in content_type or body.startswith(b"%PDF-"):
        return final_url
    return find_pdf_link(body.decode("utf-8", errors="replace"), final_url)

def parse_listing_html(html: str, *, year: int, listing_url: str) -> list[dict]:
    parser = _ListingParser(year)
    parser.feed(html)
    items = []
    for row in parser.rows:
        cells = row["cells"]
        if len(cells) < 5:
            continue
        date_match = re.search(r"(\d{1,2})月(\d{1,2})日", cells[0])
        if not date_match:
            continue
        month, day = map(int, date_match.groups())
        try:
            race_date = date(year, month, day).isoformat()
        except ValueError:
            continue
        race_name = cells[1]
        track = cells[2]
        course = cells[4]
        course_match = re.search(r"(芝|ダ|障)\s*([0-9,，]+)\s*メートル", course)
        if not race_name or track not in ("札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"):
            continue
        metadata = {
            "date": race_date,
            "race_name": race_name,
            "track": track,
            "surface": ({"ダ": "ダート", "障": "障害"}.get(course_match.group(1), "芝")
                        if course_match else None),
            "distance": (int(course_match.group(2).replace(",", "").replace("，", ""))
                         if course_match else None),
        }
        for link in row["links"]:
            href = link.get("href") or ""
            if not href:
                continue
            result_url = urljoin(listing_url, href)
            item = {
                "label": f"{race_date} {race_name} {track}",
                "result_url": result_url,
                "year": year,
                "race_metadata": metadata,
            }
            if re.search(r"\\.pdf(?:$|[?#])", href, re.I):
                item["url"] = result_url
            items.append(item)
    return items


def fetch_year(year: int) -> list[dict]:
    if year < 2002:
        raise ValueError("JRA graded-race results are listed from 2002 onward")
    page = "jyusyo.html" if year == date.today().year else f"{year}/jyusyo.html"
    listing_url = urljoin(BASE, page)
    request = Request(listing_url, headers={"User-Agent": "JRA-Horse-Data-Lab/1.0"})
    with urlopen(request, timeout=30) as response:
        html = response.read().decode("utf-8", errors="replace")
    items = parse_listing_html(html, year=year, listing_url=listing_url)
    resolved = []
    for item in items:
        pdf_url = item.get("url") or fetch_result_pdf_url(item["result_url"])
        if not pdf_url:
            print(f"WARNING: no PDF link found: {item['result_url']}")
            continue
        item["url"] = pdf_url
        resolved.append(item)
    return resolved


def build_catalog(years: list[int]) -> dict:
    items_by_url = {}
    for year in sorted(set(years)):
        for item in fetch_year(year):
            items_by_url[item["url"]] = item
    items = sorted(items_by_url.values(), key=lambda item: (item["date"] if "date" in item else item["race_metadata"]["date"], item["url"]))
    return {
        "source": BASE,
        "years": sorted(set(years)),
        "item_count": len(items),
        "items": items,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-year", type=int, default=date.today().year)
    parser.add_argument("--to-year", type=int, default=date.today().year)
    args = parser.parse_args()
    if args.from_year < 2002 or args.to_year < args.from_year:
        raise SystemExit("year range must be 2002 or later and from-year <= to-year")
    catalog = build_catalog(list(range(args.from_year, args.to_year + 1)))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {catalog['item_count']} official replay PDFs to {OUTPUT}")


if __name__ == "__main__":
    main()
