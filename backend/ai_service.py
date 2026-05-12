"""
ai_service.py
- Anthropic Claude API 를 사용하여:
  1) HTML 양식에서 입력 필드 후보를 추출 (analyze_template)
  2) 사용자 설명 + 영수증 이미지로부터 각 필드 값을 자동 작성 (generate_document)
"""

import os
import json
from typing import List, Dict, Any, Tuple
from dotenv import load_dotenv
import anthropic

load_dotenv()

API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5").strip() or "claude-sonnet-4-5"

if not API_KEY:
    print("[경고] ANTHROPIC_API_KEY 가 비어있습니다. backend/.env 파일을 확인하세요.")

_client = anthropic.Anthropic(api_key=API_KEY) if API_KEY else None


SYSTEM_ANALYZE = """\
당신은 한국 기업의 다양한 결재서류 양식(전자결재 폼)을 분석하는 전문가입니다.
제공된 HTML(원본 DOCX를 변환한 것)에서 '사용자가 자동으로 채워야 할 입력 필드'를 식별합니다.
각 셀에는 id="t{표번호}_r{행번호}_c{열번호}" 속성이 있으며, 이것이 cell_id입니다.

=== 1단계: 문서 종류 추론 ===
HTML의 제목, 첫 행, 섹션명을 보고 문서 종류를 먼저 추론하세요.
예: 견적서, 품의서, 지출결의서, 구매요청서, 출장신청서, 출장복명서, 휴가신청서, 계약검토서, 일반결재문서

=== 2단계: 셀 역할 분류 — placeholder 삽입 대상 선별 ===
다음 역할의 셀에만 placeholder를 삽입하세요:
  ✅ input_cell: 빈 입력 칸
  ✅ approval_input_cell: 결재자 이름/서명 입력 칸 (결재 라벨 옆/아래 빈 칸)
  ✅ amount_cell: 금액 입력 칸
  ✅ date_cell: 날짜 입력 칸
  ✅ line_item_cell: 품목/내역 반복 행

다음 역할의 셀에는 절대 placeholder를 삽입하지 마세요:
  ❌ label_cell: "출장 목적", "신청자명" 등 항목 라벨
  ❌ document_title: 문서 제목 텍스트
  ❌ section_title: 섹션 구분 제목
  ❌ header_cell: 표 열 헤더
  ❌ approval_label_cell: "담당", "팀장", "과장", "부장", "이사" 등 결재란 라벨
  ❌ fixed_text_cell: "아래와 같이 신청합니다" 등 고정 안내 문구

=== 3단계: 날짜 분리 규칙 (매우 중요) ===
셀 내용이 "20　년　월　일", "년　월　일", "____년 ____월 ____일" 등 년/월/일이
하나의 셀에 혼합된 경우, 반드시 3개의 별도 field로 분리하세요.

규칙:
- 년도 field: cell_id + cell_template 부여 (셀 전체 패턴 정의)
- 월/일 field: cell_id와 cell_ids는 빈 배열 [] (cell_template의 년도 field가 처리)

예시 — 셀 t0_r5_c1에 "20　년　월　일" 내용이 있을 때:
  {"label":"신청일 년","key":"request_date_year","cell_id":"t0_r5_c1","cell_ids":["t0_r5_c1"],
   "cell_template":"{{request_date_year}}년 {{request_date_month}}월 {{request_date_day}}일"},
  {"label":"신청일 월","key":"request_date_month","cell_id":"","cell_ids":[],"cell_template":""},
  {"label":"신청일 일","key":"request_date_day","cell_id":"","cell_ids":[],"cell_template":""}

기간(시작일~종료일)도 동일하게 처리:
  start_date_year/month/day + end_date_year/month/day 로 분리

=== 4단계: Key 생성 규칙 ===
영문 snake_case 사용. 라벨+상위헤더+섹션명+문서유형을 종합해 의미 있는 key 생성.
field_1, value_1 같은 무의미한 key는 최후의 수단으로만 사용.

== 공통 라벨 → Key 매핑 ==
문서기본: 문서번호→document_number, 기안일/작성일→draft_date, 신청일→request_date
작성자: 기안자/신청자/청구인→requester, 부서/소속→department, 직급/직위→position, 성명/이름→name
금액: 금액→amount, 합계/총액→total_amount, 단가→unit_price, 수량→quantity
거래처: 업체명/회사명→company_name, 담당자→contact_person, 주소→address
품목: 품목→item_name, 내역→item_description, 규격→specification, 단위→unit

== 결재란 Key 매핑 (반드시 준수, 예외 없음) ==
결재란 라벨 셀 자체가 아니라 그 옆/아래 빈 입력 셀에 아래 key를 부여하세요:
  담당 → approval_staff_name
  팀장 → approval_team_leader_name
  과장 → approval_manager_name
  차장 → approval_manager_name (과장과 동일 슬롯)
  부장 → approval_general_manager_name
  이사 → approval_director_name
  상무 → approval_managing_director_name
  전무 → approval_senior_managing_director_name
  부사장 → approval_vice_president_name
  대표/사장/대표이사 → approval_ceo_name
  검토 → approval_reviewer_name
  승인 → approval_approver_name

== 문서 유형별 Key prefix ==
출장 관련 문서에만: trip_ (trip_purpose, trip_destination 등)
견적서: quote_ (quote_date, quote_total_amount 등)
구매요청서: purchase_
지출결의서: expense_
품의서: proposal_

=== 5단계: 동일 Key 다중 셀 연결 ===
같은 결재자(또는 같은 데이터)가 문서 상단과 하단에 반복되는 경우,
cell_ids 배열에 모든 셀 ID를 나열하세요. cell_id는 cell_ids[0]으로 설정.

예: approval_manager_name이 t0_r1_c3과 t2_r0_c3 두 곳에 있으면:
  "cell_id":"t0_r1_c3", "cell_ids":["t0_r1_c3","t2_r0_c3"]

=== 출력 형식 ===
반드시 JSON만 출력하세요. 설명, 마크다운, 코드블록 금지.

{
  "document_type": "문서종류",
  "fields": [
    {
      "label": "라벨명",
      "key": "snake_case_key",
      "type": "string",
      "required": false,
      "description": "AI 작성 힌트 (결재자 필드면 '결재규칙 기반 자동 주입')",
      "cell_id": "t0_r1_c2",
      "cell_ids": ["t0_r1_c2"],
      "cell_template": ""
    }
  ]
}

type은 string | number | date | textarea 중 하나.
"""


SYSTEM_GENERATE = """\
당신은 한국 기업의 결재서류 자동작성 어시스턴트입니다.
사용자의 자연어 설명, 첨부 영수증/이미지, 양식 필드 목록을 보고 각 필드의 값을 자연스럽게 채워주세요.

규칙:
1) 반드시 JSON 객체 하나만 출력합니다. 설명 문장, 코드 펜스, 추가 텍스트 모두 금지.
2) 출력 객체의 각 키는 입력으로 받은 fields 의 'key' 값과 정확히 일치해야 합니다.
3) 값은 한국어로 자연스럽게 작성합니다.
4) 날짜 year 필드는 4자리 숫자(예: "2025"), month는 숫자(예: "5"), day는 숫자(예: "11").
5) 금액은 콤마 포함 문자열(예: "152,000")이 가능합니다.
6) 정보가 부족하면 합리적인 추정을 하되, 도저히 알 수 없으면 빈 문자열("")로 둡니다.
7) 영수증 이미지가 있으면 금액/일자/상호명 등을 적극 활용하세요.
"""


def _extract_json(text: str) -> dict:
    """모델 응답에서 JSON 객체만 안전하게 추출합니다."""
    if not text:
        raise ValueError("AI 응답이 비어있습니다.")
    s = text.strip()
    if s.startswith("```"):
        first_nl = s.find("\n")
        last_fence = s.rfind("```")
        if first_nl != -1 and last_fence != -1 and last_fence > first_nl:
            s = s[first_nl + 1:last_fence].strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(s[start:end + 1])
    raise ValueError("AI 응답에서 JSON 을 찾을 수 없습니다: " + s[:200])


def analyze_template(html: str) -> Tuple[List[Dict[str, Any]], str]:
    """
    HTML 양식에서 입력 필드 후보 리스트와 문서 종류를 반환합니다.
    Returns: (fields, document_type)
    """
    if _client is None:
        raise RuntimeError("ANTHROPIC_API_KEY 가 설정되지 않았습니다.")

    user_msg = (
        "다음 HTML 양식을 분석하여 입력 필드를 JSON 으로 출력하세요. "
        "JSON 외에는 어떤 글자도 출력하지 마세요.\n\n"
        f"{html}"
    )

    resp = _client.messages.create(
        model=MODEL,
        max_tokens=8192,
        system=SYSTEM_ANALYZE,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = resp.content[0].text if resp.content else ""
    data = _extract_json(text)

    document_type = str(data.get("document_type") or "일반결재문서").strip()
    fields_raw = data.get("fields", [])
    if not isinstance(fields_raw, list):
        return [], document_type

    cleaned = []
    for f in fields_raw:
        if not isinstance(f, dict):
            continue
        key = str(f.get("key", "")).strip()
        if not key:
            continue

        # cell_ids 정규화
        raw_cell_ids = f.get("cell_ids") or []
        if not isinstance(raw_cell_ids, list):
            raw_cell_ids = []
        single_cid = str(f.get("cell_id") or "").strip()
        if not raw_cell_ids and single_cid:
            raw_cell_ids = [single_cid]
        cell_ids = [str(c).strip() for c in raw_cell_ids if str(c).strip()]

        cleaned.append({
            "label": str(f.get("label", "")).strip(),
            "key": key,
            "type": str(f.get("type", "string")).strip() or "string",
            "required": bool(f.get("required", False)),
            "description": str(f.get("description", "")).strip(),
            "cell_id": cell_ids[0] if cell_ids else "",
            "cell_ids": cell_ids,
            "cell_template": str(f.get("cell_template", "")).strip(),
        })
    return cleaned, document_type


def generate_document(
    fields: List[Dict[str, Any]],
    description: str,
    images: List[Dict[str, str]],
) -> Dict[str, Any]:
    """
    fields + 자연어 설명 + 영수증 이미지(base64) 를 받아 자동작성 결과 JSON 을 반환합니다.
    """
    if _client is None:
        raise RuntimeError("ANTHROPIC_API_KEY 가 설정되지 않았습니다.")

    field_brief = json.dumps(fields, ensure_ascii=False, indent=2)
    text_block = (
        "아래는 채워야 하는 결재서류의 필드 목록입니다:\n"
        f"{field_brief}\n\n"
        "사용자 설명:\n"
        f"{description or '(없음)'}\n\n"
        "위 정보와 첨부 이미지를 종합하여 각 field key 에 대응하는 값을 JSON 으로만 출력하세요."
    )

    content: List[Dict[str, Any]] = [{"type": "text", "text": text_block}]

    for img in images or []:
        media = img.get("media_type", "image/jpeg")
        b64 = img.get("data")
        if not b64:
            continue
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media, "data": b64},
        })

    resp = _client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_GENERATE,
        messages=[{"role": "user", "content": content}],
    )
    text = resp.content[0].text if resp.content else ""
    data = _extract_json(text)
    if not isinstance(data, dict):
        return {}
    return data
