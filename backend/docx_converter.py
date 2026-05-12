"""
docx_converter.py
- DOCX 바이트를 HTML 문자열로 변환
- 모든 표 셀에 cell_id (t{표}_r{행}_c{열}) 부여
- 분석된 fields 위치에 {{key}} placeholder 삽입
- 최종 단계에서 {{key}} placeholder를 실제 값으로 치환
"""

import re as _re
from io import BytesIO
import mammoth
from bs4 import BeautifulSoup


# DOCX → HTML 변환 시 적용할 기본 스타일
_BASE_STYLE = """
<style>
  .doc-wrap { font-family: 'Malgun Gothic', '맑은 고딕', sans-serif;
              line-height: 1.6; color: #111; padding: 12px; background: #fff; }
  .doc-wrap h1 { font-size: 22px; font-weight: 700; margin: 12px 0 8px; }
  .doc-wrap h2 { font-size: 18px; font-weight: 700; margin: 10px 0 6px; }
  .doc-wrap p  { margin: 6px 0; }
  .doc-wrap table { border-collapse: collapse; width: 100%; margin: 12px 0; }
  .doc-wrap th, .doc-wrap td {
      border: 1px solid #999; padding: 8px; vertical-align: top;
      font-size: 14px;
  }
  .doc-wrap th { background: #f3f4f6; font-weight: 600; }
  /* 미입력 placeholder 강조 (미리보기용) */
  .doc-wrap .ph { color: #2563eb; font-weight: 600; }
</style>
"""


def docx_to_html(file_bytes: bytes) -> str:
    """
    DOCX(바이트) → HTML 문자열로 변환합니다.
    모든 표 셀에 id="t0_r1_c2" 형식의 식별자를 추가합니다.
    """
    result = mammoth.convert_to_html(BytesIO(file_bytes))
    raw_html = result.value or ""

    soup = BeautifulSoup(raw_html, "html.parser")

    for t_idx, table in enumerate(soup.find_all("table")):
        for r_idx, row in enumerate(table.find_all("tr")):
            for c_idx, cell in enumerate(row.find_all(["td", "th"])):
                cid = f"t{t_idx}_r{r_idx}_c{c_idx}"
                cell["id"] = cid
                cell["data-cell-id"] = cid

    body_html = str(soup)
    return f'{_BASE_STYLE}<div class="doc-wrap">{body_html}</div>'


def _ph_span(soup_ref, key: str):
    """{{key}} 를 .ph 클래스 span으로 감싼 태그를 반환합니다."""
    span = soup_ref.new_tag("span", **{"class": "ph"})
    span.string = "{{" + key + "}}"
    return span


def _wrap_template_keys(cell_template: str) -> str:
    """cell_template 안의 {{key}} 를 <span class='ph'>{{key}}</span> 로 치환합니다."""
    def _wrap(m):
        k = m.group(1)
        return f'<span class="ph">{{{{{k}}}}}</span>'
    return _re.sub(r"\{\{(\w+)\}\}", _wrap, cell_template)


def insert_placeholders_into_cells(html: str, fields: list) -> str:
    """
    fields 의 각 항목이 가진 모든 cell_ids 위치에 {{key}} placeholder 를 삽입합니다.

    - cell_ids (배열): 모든 셀에 동일 placeholder 삽입
    - locators (배열): cell_ids 가 없을 때 폴백
    - cell_id (단일): locators 도 없을 때 폴백
    - cell_template 이 있으면 첫 번째 셀에 template 삽입, 나머지 셀에는 {{key}} 단독 삽입
    - 같은 cell_id 는 첫 번째 처리 이후 건너뜁니다 (processed 셋으로 관리)
    """
    soup = BeautifulSoup(html, "html.parser")
    processed: set = set()

    for f in fields or []:
        key = (f.get("key") or "").strip()
        cell_template = (f.get("cell_template") or "").strip()

        if not key and not cell_template:
            continue

        # ── 대상 cell_id 목록 수집 ──
        target_ids: list = []

        raw_cell_ids = f.get("cell_ids") or []
        if isinstance(raw_cell_ids, list) and raw_cell_ids:
            for cid in raw_cell_ids:
                cid = str(cid).strip()
                if cid and cid not in target_ids:
                    target_ids.append(cid)
        else:
            # locators 폴백
            for loc in (f.get("locators") or []):
                if isinstance(loc, dict):
                    cid = str(loc.get("cell_id", "")).strip()
                    if cid and cid not in target_ids:
                        target_ids.append(cid)
            # cell_id 단일 폴백
            single = (f.get("cell_id") or "").strip()
            if single and single not in target_ids:
                target_ids.append(single)

        for idx, cell_id in enumerate(target_ids):
            if cell_id in processed:
                continue

            cell = soup.find(attrs={"id": cell_id})
            if cell is None:
                cell = soup.find(attrs={"data-cell-id": cell_id})
            if cell is None:
                continue

            cell.clear()

            if idx == 0 and cell_template:
                # 첫 번째 셀에 template 내용 삽입
                cell.append(BeautifulSoup(_wrap_template_keys(cell_template), "html.parser"))
            else:
                cell.append(_ph_span(soup, key))

            processed.add(cell_id)

    return str(soup)


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


def replace_placeholders(html_template: str, values: dict) -> str:
    """
    {{key}} placeholder 를 실제 값으로 치환합니다.
    같은 {{key}} 가 여러 위치에 있으면 모두 동일한 값으로 치환됩니다.
    """
    result = html_template
    for key, val in (values or {}).items():
        if val is None:
            val = ""
        if not isinstance(val, str):
            val = str(val)
        safe = _escape_html(val).replace("\n", "<br/>")
        result = result.replace("{{" + key + "}}", safe)
    return result
