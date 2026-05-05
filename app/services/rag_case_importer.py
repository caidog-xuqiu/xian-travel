from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List
from xml.etree import ElementTree as ET
from zipfile import ZipFile

_XLSX_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_DEFAULT_CITY = "西安"

_HEADER_ALIASES = {
    "case_id": "case_id",
    "用户需求": "user_query",
    "需求": "user_query",
    "适合区域": "scene_context",
    "适合场景": "scene_context",
    "可用时长": "available_hours_text",
    "同行人": "companion_text",
    "目的": "route_tags_text",
    "路线标签": "route_tags_text",
    "路线": "route_text",
    "推荐理由": "recommendation_reason",
}

_TAG_RULES = [
    ("约会", "dating"),
    ("对象", "dating"),
    ("夜景", "night_view"),
    ("夜游", "night_view"),
    ("拍照", "photo"),
    ("打卡", "photo"),
    ("吃饭", "meal"),
    ("餐", "meal"),
    ("烧烤", "bbq"),
    ("烤肉", "bbq"),
    ("夜宵", "late_food"),
    ("父母", "parents"),
    ("低强度", "low_walk"),
    ("少走", "low_walk"),
    ("步行不要太多", "low_walk"),
    ("朋友", "friends"),
    ("热闹", "lively"),
    ("公园", "park"),
    ("散步", "walk"),
    ("自然", "nature"),
    ("雨", "rainy"),
    ("室内", "indoor"),
    ("商场", "mall"),
    ("博物馆", "museum"),
    ("文博", "museum"),
    ("半天", "short_time"),
    ("3小时", "short_time"),
]


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\u3000", " ").strip()


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _cell_text(cell: ET.Element, shared_strings: List[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return _clean_text("".join(node.text or "" for node in cell.findall(".//m:t", _XLSX_NS)))
    value = cell.find("m:v", _XLSX_NS)
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        try:
            return _clean_text(shared_strings[int(value.text)])
        except (ValueError, IndexError):
            return _clean_text(value.text)
    return _clean_text(value.text)


def _load_shared_strings(zip_file: ZipFile) -> List[str]:
    if "xl/sharedStrings.xml" not in zip_file.namelist():
        return []
    root = ET.fromstring(zip_file.read("xl/sharedStrings.xml"))
    values: List[str] = []
    for item in root.findall("m:si", _XLSX_NS):
        values.append("".join(node.text or "" for node in item.findall(".//m:t", _XLSX_NS)))
    return values


def read_xlsx_rows(path: str | Path, *, sheet_index: int = 1) -> List[Dict[str, str]]:
    """Read the simple route-case workbook without openpyxl/pandas dependencies."""

    workbook_path = Path(path)
    if not workbook_path.exists():
        raise FileNotFoundError(workbook_path)
    with ZipFile(workbook_path) as zip_file:
        shared_strings = _load_shared_strings(zip_file)
        sheet_path = f"xl/worksheets/sheet{sheet_index}.xml"
        root = ET.fromstring(zip_file.read(sheet_path))
        matrix: List[List[str]] = []
        max_col = 0
        for row in root.findall(".//m:sheetData/m:row", _XLSX_NS):
            cells: Dict[str, str] = {}
            for cell in row.findall("m:c", _XLSX_NS):
                ref = cell.attrib.get("r", "")
                col = re.sub(r"\d+", "", ref)
                if not col:
                    continue
                cells[col] = _cell_text(cell, shared_strings)
                max_col = max(max_col, _column_index(col))
            if cells:
                matrix.append([cells.get(_column_name(i), "") for i in range(1, max_col + 1)])
    if not matrix:
        return []
    headers = [_clean_text(value) for value in matrix[0]]
    rows: List[Dict[str, str]] = []
    for raw_row in matrix[1:]:
        row = {headers[i]: _clean_text(raw_row[i]) if i < len(raw_row) else "" for i in range(len(headers))}
        if any(row.values()):
            rows.append(row)
    return rows


def _column_index(name: str) -> int:
    result = 0
    for char in name:
        result = result * 26 + (ord(char.upper()) - 64)
    return result


def _normalize_case_id(value: str, index: int) -> str:
    text = _clean_text(value)
    if not text:
        return f"case_{index:03d}"
    if text.startswith("case_"):
        return text
    digits = re.sub(r"\D+", "", text)
    if digits:
        return f"case_{int(digits):03d}"
    return f"case_{index:03d}"


def _split_label_field(value: str) -> List[str]:
    labels: List[str] = []
    for chunk in re.split(r"[/,，、\s]+", _clean_text(value)):
        item = chunk.strip()
        if not item or "->" in item or "→" in item:
            continue
        # Keep this field tag-like; long user phrases belong in text_for_rag, not metadata tags.
        if len(item) <= 12:
            labels.append(item)
    return labels


def _split_tags(*label_parts: str, inference_text: str = "") -> List[str]:
    tags: List[str] = []
    for part in label_parts:
        tags.extend(_split_label_field(part))
    raw = " ".join([*(label_parts or []), inference_text])
    for needle, tag in _TAG_RULES:
        if needle in raw and tag not in tags:
            tags.append(tag)
    return list(dict.fromkeys(tags))


def _split_route_stops(route_text: str) -> List[Dict[str, Any]]:
    parts = [part.strip() for part in re.split(r"\s*(?:->|→|—>|-＞|到|，|,|；|;)\s*", route_text or "") if part.strip()]
    return [{"order": idx, "name": name} for idx, name in enumerate(parts, start=1)]


def _canonical_row(row: Dict[str, str]) -> Dict[str, str]:
    canonical: Dict[str, str] = {}
    for key, value in row.items():
        mapped = _HEADER_ALIASES.get(_clean_text(key))
        if mapped:
            canonical[mapped] = _clean_text(value)
    return canonical


def normalize_route_case(row: Dict[str, str], *, index: int, source_file: str | None = None) -> Dict[str, Any]:
    canonical = _canonical_row(row)
    user_query = canonical.get("user_query", "")
    route_text = canonical.get("route_text", "")
    route_tags = _split_tags(
        canonical.get("scene_context", ""),
        canonical.get("route_tags_text", ""),
        canonical.get("companion_text", ""),
        inference_text=" ".join(
            [
                user_query,
                route_text,
                canonical.get("recommendation_reason", ""),
            ]
        ),
    )
    stops = _split_route_stops(route_text)
    case_id = _normalize_case_id(canonical.get("case_id", ""), index)
    scene_context = canonical.get("scene_context", "")
    text_for_rag = build_route_case_text(
        case_id=case_id,
        user_query=user_query,
        scene_context=scene_context,
        available_hours_text=canonical.get("available_hours_text", ""),
        companion_text=canonical.get("companion_text", ""),
        route_tags=route_tags,
        route_text=route_text,
        recommendation_reason=canonical.get("recommendation_reason", ""),
    )
    return {
        "id": case_id,
        "source_type": "route_case_seed",
        "city": _DEFAULT_CITY,
        "user_query": user_query,
        "scene_context": scene_context,
        "available_hours_text": canonical.get("available_hours_text", ""),
        "companion_text": canonical.get("companion_text", ""),
        "route_tags": route_tags,
        "route_text": route_text,
        "route_stops": stops,
        "recommendation_reason": canonical.get("recommendation_reason", ""),
        "text_for_rag": text_for_rag,
        "metadata": {
            "case_id": case_id,
            "city": _DEFAULT_CITY,
            "source_type": "route_case_seed",
            "scene_context": scene_context,
            "available_hours_text": canonical.get("available_hours_text", ""),
            "companion_text": canonical.get("companion_text", ""),
            "route_tags": route_tags,
            "stop_count": len(stops),
            "source_file": source_file or "",
        },
    }


def build_route_case_text(
    *,
    case_id: str,
    user_query: str,
    scene_context: str,
    available_hours_text: str,
    companion_text: str,
    route_tags: Iterable[str],
    route_text: str,
    recommendation_reason: str,
) -> str:
    tags = "、".join(route_tags)
    return "\n".join(
        line
        for line in [
            f"案例ID：{case_id}",
            f"用户需求：{user_query}",
            f"适合场景：{scene_context}",
            f"可用时长：{available_hours_text}",
            f"同行人：{companion_text}",
            f"路线标签：{tags}",
            f"推荐路线：{route_text}",
            f"推荐理由：{recommendation_reason}",
        ]
        if line.split("：", 1)[-1].strip()
    )


def load_route_cases_from_xlsx(path: str | Path) -> List[Dict[str, Any]]:
    rows = read_xlsx_rows(path)
    return [normalize_route_case(row, index=idx, source_file=Path(path).name) for idx, row in enumerate(rows, start=1)]


def write_jsonl(cases: Iterable[Dict[str, Any]], output_path: str | Path) -> int:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as fp:
        for case in cases:
            fp.write(json.dumps(case, ensure_ascii=False) + "\n")
            count += 1
    return count


def read_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as fp:
        for line in fp:
            text = line.strip()
            if text:
                output.append(json.loads(text))
    return output

