"""
template_utils.py
- convert_template_to_schema : 사용자가 확정한 fields 기준으로 최종 html_template / json_schema / placeholder_keys 생성
- validate_template_conversion : html_template 안에 placeholder 가 모두 들어있는지 검증
"""

from typing import List, Dict, Any, Tuple


def normalize_fields(fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """프론트에서 넘어온 fields 를 안전한 형태로 정리합니다."""
    cleaned = []
    seen_keys = set()
    for f in fields or []:
        if not isinstance(f, dict):
            continue
        key = str(f.get("key", "")).strip()
        if not key:
            continue
        if key in seen_keys:
            i = 2
            while f"{key}_{i}" in seen_keys:
                i += 1
            key = f"{key}_{i}"
        seen_keys.add(key)

        # cell_ids 정규화
        raw_cell_ids = f.get("cell_ids") or []
        if not isinstance(raw_cell_ids, list):
            raw_cell_ids = []
        single_cid = str(f.get("cell_id") or "").strip()
        if not raw_cell_ids and single_cid:
            raw_cell_ids = [single_cid]
        cell_ids = [str(c).strip() for c in raw_cell_ids if str(c).strip()]

        # locators 정규화
        raw_locators = f.get("locators") or []
        if not isinstance(raw_locators, list):
            raw_locators = []
        if not raw_locators and cell_ids:
            raw_locators = [{"type": "cell", "cell_id": c} for c in cell_ids]

        cleaned.append({
            "label": str(f.get("label") or key).strip(),
            "key": key,
            "type": (str(f.get("type") or "string").strip()) or "string",
            "required": bool(f.get("required", False)),
            "description": str(f.get("description") or "").strip(),
            "cell_id": cell_ids[0] if cell_ids else "",
            "cell_ids": cell_ids,
            "locators": raw_locators,
            "cell_template": str(f.get("cell_template") or "").strip(),
        })
    return cleaned


def convert_template_to_schema(
    html_template: str,
    fields: List[Dict[str, Any]],
    template_name: str,
) -> Tuple[str, Dict[str, Any], List[str]]:
    """
    사용자가 확정한 fields 기준으로
    (final_html_template, json_schema, placeholder_keys) 를 반환합니다.

    html_template 은 이미 {{key}} 가 삽입된 상태라고 가정합니다.
    """
    placeholder_keys = [f["key"] for f in fields if f.get("key")]

    properties: Dict[str, Any] = {}
    required: List[str] = []
    for f in fields:
        ftype = (f.get("type") or "string").lower()
        json_type = "number" if ftype == "number" else "string"
        prop: Dict[str, Any] = {
            "type": json_type,
            "title": f.get("label") or f["key"],
        }
        if f.get("description"):
            prop["description"] = f["description"]
        if ftype == "date":
            prop["format"] = "date"
        if ftype == "textarea":
            prop["x-display"] = "textarea"
        properties[f["key"]] = prop
        if f.get("required"):
            required.append(f["key"])

    json_schema = {
        "type": "object",
        "title": template_name,
        "properties": properties,
        "required": required,
    }
    return html_template, json_schema, placeholder_keys


def validate_template_conversion(
    html_template: str,
    placeholder_keys: List[str],
) -> Tuple[bool, str]:
    """html_template 안에 placeholder_keys 의 {{key}} 가 모두 존재하는지 확인합니다."""
    if not html_template:
        return False, "html_template 이 비어있습니다."
    missing = []
    for key in placeholder_keys:
        if "{{" + key + "}}" not in html_template:
            missing.append(key)
    if missing:
        return False, f"HTML 안에서 다음 placeholder 를 찾을 수 없습니다: {missing}"
    return True, "OK"