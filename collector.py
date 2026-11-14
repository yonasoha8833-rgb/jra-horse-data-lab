

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

class Parser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.href = None
        self.parts = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.parts = []

    def handle_data(self, data):
        if self.href is not None and data.strip():
            self.parts.append(data.strip())

    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            self.links.append((self.href, " ".join(self.parts)))
            self.href = None
            self.parts = []

def main():
    req = Request(
        PAGE,
        headers={"User-Agent": "JRA-HORSE-DATA-LAB/0.2"}
    )
    with urlopen(req, timeout=30) as r:
        raw = r.read()
        charset = r.headers.get_content_charset()

    if not charset:
        match = re.search(
            br'charset=["\']?([a-zA-Z0-9_-]+)',
            raw[:5000],
            re.I
        )
        charset = match.group(1).decode() if match else "cp932"

    try:
        html = raw.decode(charset)
    except (UnicodeDecodeError, LookupError):
        html = raw.decode("cp932", errors="replace")

    p = Parser()
    p.feed(html)
    items = []

    for href, label in p.links:
        url = urljoin(PAGE, href)
        lower_url = url.lower()
        lower_label = label.lower()

        if ".pdf" not in lower_url:
            continue
        if "-hyo-" in lower_url or "票数" in label or "発売票数" in label:
            continue

        items.append({
            "label": label or "JRA公式成績PDF",
            "url": url,
            "year": YEAR
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({
            "source": PAGE,
            "checked_at": datetime.now().astimezone().isoformat(),
            "notice": "公式成績PDFリンク一覧。馬別解析は未実装。",
            "items": items
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )
    print(f"Collected {len(items)} links")

if __name__ == "__main__":
    main()
