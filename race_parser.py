#!/usr/bin/env python3
"""Experimental horse-row extraction from positioned JRA PDF text blocks."""
from __future__ import annotations
import argparse, json, re, unicodedata
from pathlib import Path

RACE_RE = re.compile(r"第\s*([0-9０-９]+)\s*競走")
PREFIX_RE = re.compile(r"^([1-8])\s*(1[0-8]|[1-9])")
IDENTITY_RE = re.compile(r"^(.+?)(牡|牝|騸|せ)(\d{1,2})")
TIME_RE = re.compile(r"(?<![0-9])([0-9]{1,2})[:：]([0-9]{2})[.．]([0-9])")

def norm(s):
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", unicodedata.normalize("NFKC", s))

def row_time(raw):
    for m in TIME_RE.finditer(raw):
        token = m.group(1)
        minute = token[-1] if len(token) == 2 else token
        if minute in {"1","2","3","4"}:
            return f"{int(minute)}:{m.group(2)}.{m.group(3)}"
    return None

def grouped_lines(page):
    output = []
    for col in ("left", "right"):
        blocks = sorted((b for b in page.get("blocks", []) if b.get("column") == col),
                        key=lambda b: (b["bbox"][1], b["bbox"][0]))
        groups = []
        for b in blocks:
            y = b["bbox"][1]
            if not groups or abs(y - groups[-1]["y"]) > 1.8:
                groups.append({"column": col, "y": y, "blocks": []})
            groups[-1]["blocks"].append(b)
        for g in groups:
            g["text"] = " ".join(b.get("text", "") for b in sorted(g["blocks"], key=lambda b:b["bbox"][0]))
            output.append(g)
    return sorted(output, key=lambda g:(g["column"],g["y"]))

def parse_row(line, race, page, position):
    raw = norm(line["text"]).strip()
    compact = re.sub(r"\s+", "", raw)
    p = PREFIX_RE.match(compact)
    if not p: return None
    ident = IDENTITY_RE.match(compact[p.end():])
    if not ident: return None
    name = ident.group(1)
    if not name or len(name) > 24: return None
    tm = row_time(raw)
    return {
        "race_number": race, "page": page, "column": line["column"],
        "finish_position": position, "frame": int(p.group(1)),
        "horse_number": int(p.group(2)), "horse_name": name,
        "sex": ident.group(2), "age": int(ident.group(3)),
        "finish_time": tm, "raw_text": raw,
        "confidence": "review" if tm else "incomplete"
    }

def extract(layout):
    races=[]
    for page in layout.get("pages", []):
        active={"left":None,"right":None}
        positions={}
        for line in grouped_lines(page):
            rm=RACE_RE.search(line["text"])
            if rm:
                n=int(unicodedata.normalize("NFKC",rm.group(1)))
                race={"race_number":n,"page":page.get("page_number"),"column":line["column"],"horses":[]}
                races.append(race); active[line["column"]]=race
                continue
            race=active[line["column"]]
            if race is None: continue
            key=(line["column"],race["race_number"])
            pos=positions.get(key,0)+1
            horse=parse_row(line,race["race_number"],page.get("page_number"),pos)
            if horse:
                positions[key]=pos
                race["horses"].append(horse)
        for race in [r for r in races if r["page"]==page.get("page_number")]:
            race["horse_count_extracted"]=len(race["horses"])
            race["rows_needing_review"]=sum(h["confidence"]!="review" for h in race["horses"])
    return {"source":layout.get("source_pdf"),"page_count":layout.get("page_count"),
            "parser_version":"0.2.0",
            "warning":"Experimental extraction; verify every row against the official JRA PDF.",
            "races":races}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input",default="data/pdf_layout.json")
    ap.add_argument("--output",default="data/race_results.json")
    a=ap.parse_args()
    data=json.loads(Path(a.input).read_text(encoding="utf-8"))
    result=extract(data)
    out=Path(a.output); out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Races detected:",len(result["races"]))
    print("Horse rows extracted:",sum(r["horse_count_extracted"] for r in result["races"]))
    print("Rows needing review:",sum(r["rows_needing_review"] for r in result["races"]))
    print("Saved:",out)

if __name__=="__main__": main()
