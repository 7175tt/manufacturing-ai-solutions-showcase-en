#!/usr/bin/env python3
"""Export and translate the two Chinese source spreadsheets for the English site."""

from __future__ import annotations

import csv
import io
import json
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CACHE_PATH = DATA_DIR / "translation-cache.json"

SOLUTION_SHEET_ID = "1ludLzvtDeuOkWQa4tGBcQ3ThM_FNmR9esSOvE_C2Obs"
SOLUTION_TAB = "解決方案資料"
TOOL_SHEET_ID = "18Hq6sUrweHmm_08AcFPBnQ8XypXJ7CM6"
TOOL_TABS = [
    "人員及知識管理",
    "環境及設備管理",
    "原物料管理",
    "生產與品質管理",
    "行銷管理",
    "能源與碳排管理",
    "綜合或其他",
]

CATEGORY_MAP = {
    "人員及知識管理": "People & Knowledge Management",
    "環境及設備管理": "Environment & Equipment Management",
    "原物料管理": "Materials Management",
    "生產與品質管理": "Production & Quality Management",
    "行銷管理": "Marketing Management",
    "能源與碳排管理": "Energy & Carbon Management",
    "綜合或其他": "Integrated & Other Solutions",
}

TOOL_HEADER_MAP = {
    "編號": "No.",
    "來源": "Source",
    "供應商名稱": "Supplier",
    "工具名稱": "Tool Name",
    "工具類別": "Tool Category",
    "價格": "Price",
    "工具簡介(功能、特色、適用情境)": "Tool Overview (Functions, Features, Use Cases)",
    "聯絡人": "Contact",
    "聯絡資訊": "Contact Information",
}

SOLUTION_PRESERVE_FIELDS = {"id", "contact_email", "contact_phone", "url"}
SOLUTION_LIST_FIELDS = {
    "tech_tags",
    "pain_tags",
    "deployment_tags",
    "lead_time_tags",
    "api_tags",
    "data_need_tags",
    "equipment_tags",
    "integration_tags",
    "industry_tags",
    "sessions",
}
TOOL_PRESERVE_FIELDS = {"編號", "聯絡資訊"}

ZH_RE = re.compile(r"[\u3400-\u9fff]")
PROTECTED_TOKEN_RE = re.compile(
    r"https?://[^\s，。；、）)]+|www\.[^\s，。；、）)]+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
    re.I,
)


def fetch_csv(sheet_id: str, tab: str) -> list[dict[str, str]]:
    url = (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?"
        f"sheet={urllib.parse.quote(tab)}&tqx=out:csv"
    )
    with urllib.request.urlopen(url, timeout=60) as response:
        text = response.read().decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    headers = [h.strip() for h in rows[0]]
    keep = [i for i, header in enumerate(headers) if header]
    return [
        {headers[i]: (row[i].strip() if i < len(row) else "") for i in keep}
        for row in rows[1:]
        if any((row[i].strip() if i < len(row) else "") for i in keep)
    ]


def load_cache() -> dict[str, str]:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict[str, str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def should_translate(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text and ZH_RE.search(text))


def request_translation(text: str) -> str:
    query = urllib.parse.urlencode(
        {
            "client": "gtx",
            "sl": "zh-TW",
            "tl": "en",
            "dt": "t",
            "q": text,
        }
    )
    url = f"https://translate.googleapis.com/translate_a/single?{query}"
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
            translated = "".join(part[0] for part in payload[0] if part and part[0])
            return translated.strip() or text
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Translation failed after retries: {text[:80]}") from last_error


def split_translation_chunks(text: str, limit: int = 1200) -> list[str]:
    parts = re.split(r"(?<=[。！？；\n])", text)
    chunks: list[str] = []
    current = ""
    for part in parts:
        if len(current) + len(part) <= limit:
            current += part
            continue
        if current.strip():
            chunks.append(current.strip())
        while len(part) > limit:
            chunks.append(part[:limit])
            part = part[limit:]
        current = part
    if current.strip():
        chunks.append(current.strip())
    return chunks or [text]


def request_translation_chunked(text: str) -> str:
    protected: list[str] = []

    def replace_token(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"XPROTECTEDTOKEN{len(protected) - 1}X"

    safe_text = PROTECTED_TOKEN_RE.sub(replace_token, text)
    translated = "\n".join(
        request_translation(chunk) for chunk in split_translation_chunks(safe_text)
    )
    for index, original in enumerate(protected):
        translated = translated.replace(f"XPROTECTEDTOKEN{index}X", original)
        translated = translated.replace(f"X PROTECTED TOKEN {index} X", original)
    return translated


def parse_list(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            pass
    return [item.strip() for item in re.split(r"\s*[|｜]\s*", text) if item.strip()]


def collect_translation_strings(
    solution_rows: list[dict[str, str]],
    tool_rows: dict[str, list[dict[str, str]]],
) -> set[str]:
    values: set[str] = set()
    for row in solution_rows:
        for field, value in row.items():
            if field in SOLUTION_PRESERVE_FIELDS:
                continue
            if field in SOLUTION_LIST_FIELDS:
                values.update(item for item in parse_list(value) if should_translate(item))
            elif should_translate(value):
                values.add(value)
    for tab, rows in tool_rows.items():
        values.add(tab)
        for row in rows:
            for field, value in row.items():
                if field not in TOOL_PRESERVE_FIELDS and should_translate(value):
                    values.add(value)
    return values


def translate_missing(strings: set[str], cache: dict[str, str]) -> None:
    missing = sorted(text for text in strings if text not in cache)
    print(f"Unique strings: {len(strings)}; translating: {len(missing)}")
    if not missing:
        return
    completed = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(request_translation_chunked, text): text for text in missing}
        for future in as_completed(futures):
            source = futures[future]
            cache[source] = future.result()
            completed += 1
            if completed % 100 == 0:
                print(f"Translated {completed}/{len(missing)}")
                save_cache(cache)
    save_cache(cache)


def repair_incomplete_translations(strings: set[str], cache: dict[str, str]) -> None:
    incomplete = sorted(text for text in strings if ZH_RE.search(cache.get(text, "")))
    print(f"Repairing translations with remaining Chinese text: {len(incomplete)}")
    if not incomplete:
        return
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(request_translation_chunked, text): text for text in incomplete}
        for future in as_completed(futures):
            source = futures[future]
            cache[source] = future.result()
    save_cache(cache)


def tr(value: str, cache: dict[str, str]) -> str:
    text = str(value or "").strip()
    return cache.get(text, text)


def translate_solution_rows(
    rows: list[dict[str, str]], cache: dict[str, str]
) -> list[dict[str, str]]:
    translated: list[dict[str, str]] = []
    for row in rows:
        item: dict[str, str] = {}
        for field, value in row.items():
            if field in SOLUTION_PRESERVE_FIELDS:
                item[field] = value
            elif field in SOLUTION_LIST_FIELDS:
                item[field] = " | ".join(tr(part, cache) for part in parse_list(value))
            else:
                item[field] = tr(value, cache)
        translated.append(item)
    return translated


def translate_tool_rows(
    rows_by_tab: dict[str, list[dict[str, str]]], cache: dict[str, str]
) -> tuple[dict[str, list[dict[str, str]]], list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    flattened: list[dict[str, str]] = []
    for tab, rows in rows_by_tab.items():
        category = CATEGORY_MAP[tab]
        translated_rows: list[dict[str, str]] = []
        for row in rows:
            item = {"Category": category}
            for field, value in row.items():
                english_header = TOOL_HEADER_MAP[field]
                item[english_header] = value if field in TOOL_PRESERVE_FIELDS else tr(value, cache)
            translated_rows.append(item)
            flattened.append(item)
        grouped[category] = translated_rows
    return grouped, flattened


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print("Fetching source spreadsheets...")
    solution_rows = fetch_csv(SOLUTION_SHEET_ID, SOLUTION_TAB)
    tool_rows = {tab: fetch_csv(TOOL_SHEET_ID, tab) for tab in TOOL_TABS}

    cache = load_cache()
    strings = collect_translation_strings(solution_rows, tool_rows)
    translate_missing(strings, cache)
    repair_incomplete_translations(strings, cache)

    solutions_en = translate_solution_rows(solution_rows, cache)
    tools_grouped_en, tools_flat_en = translate_tool_rows(tool_rows, cache)

    solution_fields = list(solution_rows[0].keys())
    tool_fields = [
        "Category",
        "No.",
        "Source",
        "Supplier",
        "Tool Name",
        "Tool Category",
        "Price",
        "Tool Overview (Functions, Features, Use Cases)",
        "Contact",
        "Contact Information",
    ]

    (DATA_DIR / "solutions-en.json").write_text(
        json.dumps(solutions_en, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (DATA_DIR / "industry-tools-en.json").write_text(
        json.dumps(tools_grouped_en, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (DATA_DIR / "site-data-en.js").write_text(
        "window.ENGLISH_SOLUTION_DATA = "
        + json.dumps(solutions_en, ensure_ascii=False)
        + ";\nwindow.ENGLISH_TOOL_DATA = "
        + json.dumps(tools_grouped_en, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )
    write_csv(DATA_DIR / "solutions-en.csv", solutions_en, solution_fields)
    write_csv(DATA_DIR / "industry-tools-en.csv", tools_flat_en, tool_fields)

    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "solution_source": f"https://docs.google.com/spreadsheets/d/{SOLUTION_SHEET_ID}/edit",
        "tool_source": f"https://docs.google.com/spreadsheets/d/{TOOL_SHEET_ID}/edit",
        "solution_count": len(solutions_en),
        "tool_count": len(tools_flat_en),
        "tool_categories": {name: len(rows) for name, rows in tools_grouped_en.items()},
    }
    (DATA_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
