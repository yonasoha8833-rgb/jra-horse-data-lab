from html.parser import HTMLParser
from urllib.request import Request, urlopen
from urllib.parse import urljoin
from datetime import datetime, timezone
from pathlib import Path
import json

YEAR = datetime.now(timezone.utc).year
PAGE = f"https://www.jra.go.jp/datafile/seiseki/report/{YEAR}.html"
OUT = Path("data/catalog.json")

class Parser(HTMLParser):
    def __init__(self):
        super().__init__(); self.href=None; self.parts=[]; self.links=[]
    def handle_starttag(self, tag, attrs):
        if tag=="a": self.href=dict(attrs).get("href"); self.parts=[]
    def handle_data(self, data):
        if self.href is not None and data.strip(): self.parts.append(data.strip())
    def handle_endtag(self, tag):
        if tag=="a" and self.href is not None:
            self.links.append((self.href," ".join(self.parts))); self.href=None; self.parts=[]

def main():
    req=Request(PAGE,headers={"User-Agent":"JRA-HORSE-DATA-LAB/0.1"})
    with urlopen(req,timeout=30) as r: html=r.read().decode("utf-8",errors="replace")
    p=Parser(); p.feed(html); items=[]
    for href,label in p.links:
        url=urljoin(PAGE,href)
        if ".pdf" in url.lower() and not any(w in label for w in ("発売票数","票数","払戻")):
            items.append({"label":label or "JRA公式成績PDF","url":url,"year":YEAR})
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps({"source":PAGE,"checked_at":datetime.now(timezone.utc).isoformat(),
      "notice":"PDFリンク一覧のみ。馬別データ解析は未実装。","items":items},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"Collected {len(items)} links")
if __name__=="__main__": main()
