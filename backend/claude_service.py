"""
claude_service.py
- 조직도/인사표/주소록 이미지에서 조직원 정보를 추출하는 AI 서비스
- PDF 지침서에서 결재 규칙 후보를 추출하는 AI 서비스
- 결재서류 자동 작성용 데이터를 추출하는 AI 서비스
"""

import os
import json
from typing import List, Dict, Any

import anthropic
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

_client = anthropic.Anthropic(api_key=API_KEY) if API_KEY else None

_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_SONNET_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5").strip() or "claude-sonnet-4-5"


# ──────────────────────────────────────────────────────────────
# 공통 유틸: JSON 파싱
# ──────────────────────────────────────────────────────────────

def _extract_json_array(text: str) -> List[Dict[str, Any]]:
    """AI 응답 텍스트에서 JSON 배열을 추출합니다."""
    if not text:
        raise ValueError("AI 응답이 비어있습니다.")
    s = text.strip()

    if s.startswith("```"):
        first_nl = s.find("\n")
        last_fence = s.rfind("```")
        if first_nl != -1 and last_fence > first_nl:
            s = s[first_nl + 1:last_fence].strip()

    try:
        data = json.loads(s)
        if isinstance(data, list):
            return data
    except Exception:
        pass

    start = s.find("[")
    end = s.rfind("]")
    if start != -1 and end > start:
        try:
            data = json.loads(s[start:end + 1])
            if isinstance(data, list):
                return data
        except Exception:
            pass

    raise ValueError("AI 응답에서 JSON 배열을 찾을 수 없습니다: " + s[:200])


def _extract_json_object(text: str) -> Dict[str, Any]:
    """AI 응답 텍스트에서 JSON 객체를 추출합니다."""
    if not text:
        raise ValueError("AI 응답이 비어있습니다.")
    s = text.strip()

    if s.startswith("```"):
        first_nl = s.find("\n")
        last_fence = s.rfind("```")
        if first_nl != -1 and last_fence > first_nl:
            s = s[first_nl + 1:last_fence].strip()

    try:
        data = json.loads(s)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(s[start:end + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    raise ValueError("AI 응답에서 JSON 객체를 찾을 수 없습니다: " + s[:200])


# ──────────────────────────────────────────────────────────────
# 조직도 이미지 분석
# ──────────────────────────────────────────────────────────────

_SYSTEM_ORG = """\
당신은 회사 조직도/인사표/주소록 표 이미지를 읽어 조직원 정보를 JSON 배열로 구조화하는 전문가입니다.

규칙:
1) JSON 배열만 출력합니다. 설명, 마크다운, 코드블록 출력 금지.
2) 각 행은 name, department, title, email 을 포함합니다.
3) 헤더 행은 제외합니다.
4) 보이지 않는 값은 빈 문자열("")로 둡니다.
5) 추측하지 않습니다.
6) 이름/직급/이메일이 모두 비어 있는 행은 제외합니다.

출력 예시:
[
  {"name": "홍길동", "department": "영업팀", "title": "팀장", "email": "hong@company.com"}
]
"""


def preview_org_members_from_image(image_b64: str, image_mime: str) -> List[Dict[str, Any]]:
    """
    조직도/인사표/주소록 이미지(base64)를 받아 조직원 목록 JSON 배열을 반환합니다.
    각 항목: {"name": str, "department": str, "title": str, "email": str}
    """
    if _client is None:
        raise RuntimeError("ANTHROPIC_API_KEY 가 설정되지 않았습니다.")

    resp = _client.messages.create(
        model=_HAIKU_MODEL,
        max_tokens=4096,
        system=_SYSTEM_ORG,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": image_mime,
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "이 이미지에서 조직원 목록을 JSON 배열로 추출해주세요. "
                            "JSON 배열만 출력하세요."
                        ),
                    },
                ],
            }
        ],
    )

    text = resp.content[0].text if resp.content else ""
    return _extract_json_array(text)


# ──────────────────────────────────────────────────────────────
# PDF 지침서 → 결재 규칙 후보 추출
# ──────────────────────────────────────────────────────────────

_SYSTEM_GUIDE = """\
당신은 회사 결재 지침서에서 결재 규칙을 추출하는 전문가입니다.

규칙:
1) JSON 배열만 출력합니다. 설명, 마크다운, 코드블록 출력 금지.
2) 제공된 templates 목록의 id만 template_id로 사용합니다.
3) 제공된 members 목록의 id만 approver_member_id로 사용합니다.
4) 지침서에서 근거가 명확한 규칙만 생성합니다. 애매하면 생성하지 않습니다.
5) approval_type은 반드시 "결재", "합의", "최종승인", "참조" 중 하나만 사용합니다.
6) 각 규칙의 approvers는 1명 이상이어야 합니다.
7) approval_order는 1부터 시작하는 정수이며, 같은 규칙 안에서 중복 금지.

출력 형식:
[
  {
    "rule_name": "규칙명",
    "rule_text": "규칙 설명 (한 문장 이상)",
    "template_id": 1,
    "source_excerpt": "지침서에서 발췌한 근거 원문",
    "approvers": [
      {"approver_member_id": 3, "approval_order": 1, "approval_type": "결재"},
      {"approver_member_id": 5, "approval_order": 2, "approval_type": "최종승인"}
    ]
  }
]
"""


def preview_approval_rules_from_guide(
    guide_text: str,
    templates: List[Dict[str, Any]],
    members: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    지침서 텍스트와 양식·조직원 목록을 받아 결재 규칙 후보 JSON 배열을 반환합니다.
    """
    if _client is None:
        raise RuntimeError("ANTHROPIC_API_KEY 가 설정되지 않았습니다.")

    templates_json = json.dumps(templates, ensure_ascii=False)
    members_json = json.dumps(members, ensure_ascii=False)
    guide_excerpt = guide_text[:8000]

    user_msg = f"""다음은 회사 결재 지침서 원문입니다:

===== 지침서 시작 =====
{guide_excerpt}
===== 지침서 끝 =====

사용 가능한 결재서류 양식 목록 (이 목록의 id만 사용하세요):
{templates_json}

사용 가능한 조직원 목록 (이 목록의 id만 사용하세요):
{members_json}

위 지침서에서 결재 규칙 후보를 JSON 배열로 추출해주세요.
JSON 배열만 출력하세요."""

    resp = _client.messages.create(
        model=_SONNET_MODEL,
        max_tokens=4096,
        system=_SYSTEM_GUIDE,
        messages=[{"role": "user", "content": user_msg}],
    )

    text = resp.content[0].text if resp.content else ""
    return _extract_json_array(text)


# ──────────────────────────────────────────────────────────────
# 결재서류 자동 작성
# ──────────────────────────────────────────────────────────────

_SYSTEM_EXPENSE = """\
당신은 회사 결재서류를 자동으로 작성하는 AI 어시스턴트입니다.

역할:
사용자의 자연어 설명, 증빙 이미지, 결재 규칙, 결재선 정보를 참고하여
결재서류에 필요한 데이터를 JSON 객체로 생성합니다.

반드시 지켜야 할 규칙:
1) 제공된 JSON Schema의 properties에 정의된 필드만 사용합니다.
2) Schema에 없는 필드는 절대 추가하지 않습니다.
3) 증빙 이미지가 여러 장이면 모두 꼼꼼히 확인합니다.
4) 자연어 설명과 이미지 내용이 충돌하면 이미지 정보를 우선합니다.
5) number 타입 필드는 반드시 숫자(정수 또는 소수)로 반환합니다. 쉼표·원화기호·단위 금지.
6) 날짜 year 필드는 4자리 숫자 문자열(예: "2025"), month는 숫자(예: "5"), day는 숫자(예: "11").
7) 값을 알 수 없거나 이미지·설명에 없으면 빈 문자열("")을 사용합니다.
8) JSON 객체만 출력합니다. 설명문, 마크다운, 코드블록 출력 절대 금지.

[결재자 필드 규칙 — 매우 중요]
아래 필드들은 반드시 빈 문자열("")로만 출력하세요.
AI가 임의로 이름을 추론하거나 채우면 절대 안 됩니다.
결재자 값은 DB 결재규칙 기준으로 시스템이 별도로 주입합니다.

빈 문자열로 두어야 할 필드:
- approval_staff_name
- approval_team_leader_name
- approval_manager_name
- approval_general_manager_name
- approval_director_name
- approval_managing_director_name
- approval_senior_managing_director_name
- approval_vice_president_name
- approval_ceo_name
- approval_reviewer_name
- approval_approver_name
- approver_name
- approval_line
"""


def extract_expense_data(
    template_html: str,
    template_name: str,
    template_schema: dict,
    description: str,
    receipt_images: list | None = None,
    approval_rules: str = "",
    org_chart: dict | None = None,
) -> Dict[str, Any]:
    """
    결재서류 자동 작성용 데이터를 Claude API로 추출합니다.
    JSON Schema의 properties에 맞는 JSON 객체를 반환합니다.

    Parameters
    ----------
    template_html   : 결재서류 템플릿 HTML (참고용)
    template_name   : 템플릿 이름 (예: "출장계")
    template_schema : JSON Schema 객체 (properties에 필드 정의)
    description     : 사용자 자연어 설명
    receipt_images  : base64 이미지 목록 [{"media_type": ..., "data": ...}, ...]
    approval_rules  : 결재 규칙·결재선 텍스트 (DB에서 구성)
    org_chart       : 조직도 정보 (선택)

    Returns
    -------
    dict — JSON Schema properties에 맞는 데이터 객체
    """
    if _client is None:
        raise RuntimeError("ANTHROPIC_API_KEY 가 설정되지 않았습니다.")

    schema_json = json.dumps(template_schema or {}, ensure_ascii=False, indent=2)

    # 사용자 메시지 content 구성
    content: list = []

    # 증빙 이미지를 먼저 추가 (Claude가 이미지를 먼저 보게)
    for img in (receipt_images or []):
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": img["media_type"],
                "data": img["data"],
            },
        })

    # 텍스트 지시사항 구성
    text_parts = [
        f"[문서 유형]\n{template_name}",
        f"[JSON Schema — 이 필드만 사용하고, 없는 필드는 추가하지 마세요]\n{schema_json}",
    ]

    if approval_rules:
        text_parts.append(f"[결재 규칙 및 결재선 — 결재자 관련 필드는 이 정보를 기준으로 작성]\n{approval_rules}")

    if description:
        text_parts.append(f"[작성 요청 내용]\n{description}")

    text_parts.append(
        "위 정보와 첨부된 증빙 이미지를 참고하여 결재서류 데이터를 JSON 객체로 작성해주세요.\n"
        "JSON 객체만 출력하세요."
    )

    content.append({
        "type": "text",
        "text": "\n\n".join(text_parts),
    })

    resp = _client.messages.create(
        model=_SONNET_MODEL,
        max_tokens=4096,
        system=_SYSTEM_EXPENSE,
        messages=[{"role": "user", "content": content}],
    )

    text = resp.content[0].text if resp.content else ""
    return _extract_json_object(text)
