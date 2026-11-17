"""Conservative parser and fetcher for JRA official HTML race-result pages.

Runner-level estimated last 3F and corner positions are kept separate from
race-level lap and closing-section times. Only HTTPS JRA result-page URLs are
accepted by the network fetcher; missing values are never inferred.
"""
from __future__ import annotations

from datetime import date
from html.parser import HTMLParser
import re
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from typing import Any

JRA_RESULT_HOSTS = {"www.jra.go.jp", "sp.jra.jp"}
MAX_HTML_BYTES = 5 * 1024 * 1024
TRACKS = ("札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉")
RESULT_STATUS_RE = re.compile(r"(競走中止|失格|中止|取消|除外)")
TIME_RE = re.compile(r"(?<![0-9])([0-9]{1,2}:[0-9]{2}[.．][0-9])")
NUMBER_RE = re.compile(r"[0-9０-９]+")


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _ascii_digits(value: str) -> str:
    return value.translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def _seconds(value: str | None) -> float | None:
    if not value:
        return None
    match = TIME_RE.search(_ascii_digits(value).replace("．", "."))
    if not match:
        return None
    minutes, seconds, tenth = re.fullmatch(r"(\d{1,2}):(\d{2})\.(\d)", match.group(1)).groups()
    if int(seconds) >= 60:
        return None
    return int(minutes) * 60 + int(seconds) + int(tenth) / 10


class _Cell:
    def __init__(self) -> None:
        self.text: list[str] = []
        self.alt: list[str] = []


class _TableParser(HTMLParser):
    """Collect table rows without depending on JRA's CSS class names."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[_Cell]] = []
        self.text_parts: list[str] = []
        self._row: list[_Cell] | None = None
        self._cell: _Cell | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs = dict(attrs)
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = _Cell()
        elif tag == "img" and self._cell is not None:
            alt = (attrs.get("alt") or "").strip()
            if alt:
                self._cell.alt.append(alt)
        elif tag in {"br", "li", "p"}:
            if self._cell is not None:
                self._cell.text.append(" ")

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)
        if self._cell is not None:
            self._cell.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._cell is not None:
                self._row.append(self._cell)
                self._cell = None
            self.rows.append(self._row)
            self._row = None


def _column(headers: list[str], *names: str) -> int | None:
    for index, header in enumerate(headers):
        normalized = _normalize(header)
        if any(name in normalized for name in names):
            return index
    return None


def _cell_text(row: list[_Cell], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return " ".join(" ".join(row[index].text).split())


def _cell_alt(row: list[_Cell], index: int | None) -> list[str]:
    if index is None or index >= len(row):
        return []
    return row[index].alt


def _race_metadata(page_text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    date_match = re.search(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日", page_text)
    if date_match:
        try:
            result["date"] = date(*map(int, date_match.groups())).isoformat()
        except ValueError:
            pass

    meeting_match = re.search(r"\d+回\s*(" + "|".join(TRACKS) + r")\s*\d+日", page_text)
    if meeting_match:
        result["track"] = meeting_match.group(1)
    else:
        result["track"] = next((track for track in TRACKS if track in page_text), None)

    race_match = re.search(r"(\d{1,2})\s*レース", page_text)
    if race_match and 1 <= int(race_match.group(1)) <= 12:
        result["race_number"] = int(race_match.group(1))

    course_match = re.search(
        r"コース\s*[：:]\s*([0-9０-９,，]+)\s*メートル\s*[（(]\s*(芝|ダート|障害|ダ)",
        page_text,
    )
    if not course_match:
        course_match = re.search(
            r"(芝|ダート|障害|ダ)\s*([0-9０-９,，]+)\s*メートル",
            page_text,
        )
        if course_match:
            surface, distance = course_match.groups()
        else:
            surface = distance = None
    else:
        distance, surface = course_match.groups()
    if surface:
        result["surface"] = {"ダ": "ダート"}.get(surface, surface)
    if distance:
        try:
            parsed_distance = int(_ascii_digits(distance).replace(",", "").replace("，", ""))
            if 1000 <= parsed_distance <= 5000:
                result["distance"] = parsed_distance
        except ValueError:
            pass

    going_match = re.search(r"(?:芝|ダート|ダ|障害)\s*(良|稍重|重|不良)", page_text)
    if going_match:
        result["going"] = going_match.group(1)

    class_match = re.search(r"((?:2歳|3歳|4歳|3歳以上|4歳以上)[^\n]{0,35}?(?:新馬|未勝利|1勝クラス|2勝クラス|3勝クラス|オープン))", page_text)
    if class_match:
        result["race_class"] = " ".join(class_match.group(1).split())

    lap_match = re.search(
        r"ハロンタイム\s*[|｜:：]?\s*([0-9０-９]+[.．][0-9０-９]+(?:\s*[-―ー－]\s*[0-9０-９]+[.．][0-9０-９]+)+)",
        page_text,
    )
    if lap_match:
        laps = [
            float(_ascii_digits(value).replace("．", "."))
            for value in re.findall(r"[0-9０-９]+[.．][0-9０-９]+", lap_match.group(1))
        ]
        if all(5.0 <= value <= 30.0 for value in laps):
            result["race_lap_times"] = laps

    closing_match = re.search(
        r"上り\s*[|｜:：]?\s*4F\s*([0-9０-９]+[.．][0-9０-９]+)\s*[-―ー－]\s*3F\s*([0-9０-９]+[.．][0-9０-９]+)",
        page_text,
    )
    if closing_match:
        last4, last3 = (
            float(_ascii_digits(value).replace("．", "."))
            for value in closing_match.groups()
        )
        if 20.0 <= last4 <= 90.0 and 15.0 <= last3 <= 70.0:
            result["race_last4f"] = last4
            result["race_last3f"] = last3
    return result


def parse_result_html(html: str, *, source_url: str = "") -> list[dict[str, Any]]:
    """Parse all runner rows from one JRA official race-result HTML page."""
    if not isinstance(html, str) or not html.strip():
        return []
    parser = _TableParser()
    parser.feed(html)
    page_text = " ".join(parser.text_parts)
    metadata = _race_metadata(page_text)

    for table_rows in _split_tables(parser.rows):
        header_index = None
        headers: list[str] = []
        for index, row in enumerate(table_rows):
            candidate = [" ".join(cell.text).strip() for cell in row]
            normalized = [_normalize(value) for value in candidate]
            if (
                _column(normalized, "着順") is not None
                and _column(normalized, "馬番") is not None
                and _column(normalized, "馬名") is not None
                and _column(normalized, "推定上り", "推定上がり") is not None
            ):
                header_index = index
                headers = normalized
                break
        if header_index is None:
            continue

        columns = {
            "finish": _column(headers, "着順"),
            "frame": _column(headers, "枠"),
            "horse_number": _column(headers, "馬番"),
            "horse_name": _column(headers, "馬名"),
            "sex_age": _column(headers, "性齢"),
            "weight_carried": _column(headers, "負担重量"),
            "jockey": _column(headers, "騎手名"),
            "finish_time": _column(headers, "タイム"),
            "margin": _column(headers, "着差"),
            "corner": _column(headers, "コーナー通過順位", "通過順位"),
            "individual_last3f": _column(headers, "推定上り", "推定上がり"),
            "body_weight": _column(headers, "馬体重"),
            "trainer": _column(headers, "調教師名"),
            "popularity": _column(headers, "単勝人気", "人気"),
        }
        records: list[dict[str, Any]] = []
        for row in table_rows[header_index + 1:]:
            number_text = _cell_text(row, columns["horse_number"])
            name_text = _cell_text(row, columns["horse_name"])
            if not number_text or not name_text:
                continue
            number_match = NUMBER_RE.search(_ascii_digits(number_text))
            if not number_match:
                continue
            horse_number = int(number_match.group())
            if not 1 <= horse_number <= 18:
                continue

            finish_text = _cell_text(row, columns["finish"])
            status_match = RESULT_STATUS_RE.search(finish_text)
            finish_match = NUMBER_RE.search(_ascii_digits(finish_text))
            finish = int(finish_match.group()) if finish_match and not status_match else None
            if finish is not None and not 1 <= finish <= 18:
                finish = None

            time_text = _cell_text(row, columns["finish_time"])
            time_match = TIME_RE.search(_ascii_digits(time_text).replace("．", "."))
            finish_time = time_match.group(1).replace("．", ".") if time_match else None
            finish_seconds = _seconds(finish_time)

            last3f_text = _cell_text(row, columns["individual_last3f"])
            last3f_match = re.search(r"[0-9０-９]{2,3}[.．][0-9０-９]", last3f_text)
            individual_last3f = (
                float(_ascii_digits(last3f_match.group()).replace("．", "."))
                if last3f_match else None
            )
            if individual_last3f is not None and not 20.0 <= individual_last3f <= 60.0:
                individual_last3f = None

            corner_text = _cell_text(row, columns["corner"])
            corner_numbers = [int(_ascii_digits(value)) for value in NUMBER_RE.findall(corner_text)]
            corner_numbers = [value for value in corner_numbers if 1 <= value <= 18]
            corner_positions = "-".join(map(str, corner_numbers[:4])) if 2 <= len(corner_numbers) <= 4 else None

            sex_age_text = _cell_text(row, columns["sex_age"])
            sex_match = re.search(r"(牡|牝|セ|せん)", sex_age_text)
            age_match = NUMBER_RE.search(_ascii_digits(sex_age_text))
            weight_text = _cell_text(row, columns["weight_carried"])
            weight_match = re.search(r"[0-9０-９]{2,3}(?:[.．][0-9０-９])?", weight_text)
            body_text = _cell_text(row, columns["body_weight"])
            body_match = re.search(r"([0-9０-９]{3})\s*[（(]\s*([+＋-−]?[0-9０-９]+)\s*[）)]", body_text)
            popularity_text = _cell_text(row, columns["popularity"])
            popularity_match = NUMBER_RE.search(_ascii_digits(popularity_text))
            horse_name = re.sub(r"\s+", "", name_text)
            horse_name = horse_name.replace("ブリンカー着用", "").strip()

            records.append({
                **metadata,
                "frame": int(_ascii_digits(_cell_text(row, columns["frame"]))) if _cell_text(row, columns["frame"]).isdigit() else None,
                "horse_number": horse_number,
                "horse_name": horse_name,
                "sex": sex_match.group(1) if sex_match else None,
                "age": int(_ascii_digits(age_match.group())) if age_match else None,
                "weight_carried": float(_ascii_digits(weight_match.group()).replace("．", ".")) if weight_match else None,
                "jockey": _cell_text(row, columns["jockey"]) or None,
                "finish": finish,
                "result_status": status_match.group(1) if status_match else None,
                "finish_time": finish_time,
                "finish_time_seconds": finish_seconds,
                "margin": _cell_text(row, columns["margin"]) or None,
                "corner_positions": corner_positions,
                "corner_3": corner_numbers[0] if len(corner_numbers) >= 1 else None,
                "corner_4": corner_numbers[1] if len(corner_numbers) >= 2 else None,
                "individual_last3f": individual_last3f,
                "body_weight": int(body_match.group(1)) if body_match else None,
                "body_weight_change": int(_ascii_digits(body_match.group(2)).replace("＋", "+").replace("−", "-")) if body_match else None,
                "trainer": _cell_text(row, columns["trainer"]) or None,
                "popularity": int(_ascii_digits(popularity_match.group())) if popularity_match else None,
                "blinkers": any("ブリンカー" in alt for alt in _cell_alt(row, columns["horse_name"])),
                "field_size": None,
                "source_url": source_url,
                "source_type": "jra_official_result_html",
                "extraction_status": "parsed_official_html",
            })
        if not records:
            continue
        numbers = [row["horse_number"] for row in records]
        warnings = []
        if len(numbers) != len(set(numbers)):
            warnings.append("duplicate_horse_number")
        field_size = len(records)
        for record in records:
            record["field_size"] = field_size
            record["race_validation_warnings"] = warnings.copy()
        return records
    return []


def _split_tables(rows: list[list[_Cell]]) -> list[list[list[_Cell]]]:
    """Split collected rows at table header candidates; nested table markup is rare."""
    tables: list[list[list[_Cell]]] = []
    current: list[list[_Cell]] = []
    for row in rows:
        if current and not row:
            if current:
                tables.append(current)
            current = []
        current.append(row)
    if current:
        tables.append(current)
    return tables


def read_limited_html(response, max_bytes: int = MAX_HTML_BYTES) -> bytes:
    """Read a response with a hard byte cap, including streams without a length."""
    raw_length = response.headers.get("Content-Length")
    if raw_length is not None:
        try:
            length = int(raw_length)
        except (TypeError, ValueError):
            raise ValueError("Invalid Content-Length header")
        if length < 0:
            raise ValueError("Invalid negative Content-Length header")
        if length > max_bytes:
            raise ValueError(f"HTML exceeds size limit ({max_bytes} bytes)")
    body = response.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError(f"HTML exceeds size limit ({max_bytes} bytes)")
    return body


def _validate_result_url(url: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in JRA_RESULT_HOSTS
        or parsed.path != "/JRADB/accessS.html"
        or not parse_qs(parsed.query).get("CNAME")
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("Only HTTPS JRA official accessS result-page URLs are allowed")


def fetch_result_html(url: str) -> str:
    """Fetch a size-limited official JRA HTML result page and return decoded text."""
    _validate_result_url(url)
    request = Request(url, headers={"User-Agent": "JRA-Horse-Data-Lab/1.0"})
    with urlopen(request, timeout=30) as response:
        final_url = response.geturl()
        _validate_result_url(final_url)
        content_type = response.headers.get("Content-Type", "").lower()
        if content_type and "html" not in content_type:
            raise ValueError("JRA result response is not HTML")
        body = read_limited_html(response)
        charset_match = re.search(r"charset=([a-zA-Z0-9._-]+)", content_type)
        charset = charset_match.group(1) if charset_match else "utf-8"
        try:
            return body.decode(charset, errors="replace")
        except LookupError:
            return body.decode("utf-8", errors="replace")


def fetch_and_parse_result(url: str) -> list[dict[str, Any]]:
    """Fetch and parse one official JRA result page."""
    return parse_result_html(fetch_result_html(url), source_url=url)
