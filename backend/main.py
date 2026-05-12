"""
main.py — FastAPI 서버
엔드포인트:
  GET   /                                          : 헬스체크
  POST  /api/templates/preview-fields             : DOCX 분석 → preview_html / fields 반환
  POST  /api/templates/save-with-fields           : 사용자가 확정한 fields 로 DB 저장
  GET   /api/templates                            : 활성 양식 목록 (최신순)
  GET   /api/templates/{id}                       : 양식 상세 (html_content + fields)
  POST  /api/education/generate-once              : 자연어 + 영수증 → AI 자동작성 → filled_html
  POST  /api/org-members                          : 조직원 1명 수동 등록
  GET   /api/org-members                          : 조직원 목록 조회 (is_active=True)
  POST  /api/org-members/preview-image            : 조직도 이미지 → AI 추출 미리보기
  POST  /api/org-members/save-preview             : AI 추출 결과 일괄 저장
  POST  /api/approval-rules                       : 결재 규칙 1건 수동 저장
  GET   /api/approval-rules                       : 결재 규칙 목록 조회
  POST  /api/approval-rules/preview-guide         : PDF 지침서 → AI 결재 규칙 후보 추출
  POST  /api/approval-rules/save-preview          : AI 결재 규칙 후보 일괄 저장
  POST  /api/process                              : 결재서류 자동 작성 (AI)
  POST  /api/send-email                           : 작성된 결재서류 이메일 발송
  PATCH /api/generated-documents/{document_id}   : 사용자 편집 HTML DB 저장
  GET   /api/generated-documents                 : 자동작성 문서 목록 조회
  GET   /api/generated-documents/{document_id}   : 자동작성 문서 상세 조회
  DELETE /api/generated-documents/{document_id}  : 자동작성 문서 소프트 삭제
"""

import io
import os
import json
import base64
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from dotenv import load_dotenv

from docx_converter import (
    docx_to_html,
    insert_placeholders_into_cells,
    replace_placeholders,
)
from ai_service import analyze_template, generate_document
from template_utils import (
    normalize_fields,
    convert_template_to_schema,
    validate_template_conversion,
)
from database import engine, get_db, Base
from models import (
    DocumentTemplate,
    OrgMember,
    ApprovalRule,
    ApprovalRuleApprover,
    GeneratedDocument,
)
import claude_service

load_dotenv()

# ── 결재자 필드 → DB 기준 주입 상수 ──────────────────────────────────

# 모든 표준 결재자 필드 키 (AI 생성값 무시하고 DB로 덮어쓰기)
_ALL_APPROVAL_KEYS = [
    "approval_staff_name", "approval_team_leader_name",
    "approval_manager_name", "approval_general_manager_name",
    "approval_director_name", "approval_managing_director_name",
    "approval_senior_managing_director_name", "approval_vice_president_name",
    "approval_ceo_name", "approval_reviewer_name", "approval_approver_name",
    "approver_name", "approval_line",
]

# 직급명 → 결재 필드 key 매핑 (정확히 일치해야만 해당 슬롯에 들어감)
_TITLE_TO_APPROVAL_FIELD: dict = {
    # 담당/실무 레벨 → approval_staff_name
    "담당": "approval_staff_name",
    "사원": "approval_staff_name",
    "주임": "approval_staff_name",
    "대리": "approval_staff_name",
    "팀원": "approval_staff_name",
    "전임": "approval_staff_name",
    # 팀장
    "팀장": "approval_team_leader_name",
    # 과장/차장
    "과장": "approval_manager_name",
    "차장": "approval_manager_name",
    # 부장
    "부장": "approval_general_manager_name",
    # 이사급
    "이사": "approval_director_name",
    "상무이사": "approval_director_name",
    # 상무
    "상무": "approval_managing_director_name",
    # 전무
    "전무": "approval_senior_managing_director_name",
    # 부사장
    "부사장": "approval_vice_president_name",
    # 사장/대표
    "사장": "approval_ceo_name",
    "대표": "approval_ceo_name",
    "대표이사": "approval_ceo_name",
    "회장": "approval_ceo_name",
}


def _build_approval_override(approvers_sorted: list, db) -> dict:
    """
    ApprovalRuleApprover 정렬 목록과 DB 세션을 받아
    표준 결재자 필드 key → 이름 딕셔너리를 반환합니다.

    규칙:
    - member.title 이 _TITLE_TO_APPROVAL_FIELD 에 정확히 일치하는 경우에만 해당 슬롯에 이름 삽입
    - 동일 슬롯에 이미 값이 있으면 먼저 온 결재자 우선 (first-wins)
    - 일치하지 않는 슬롯은 빈 문자열 유지 (AI 추론 방지)
    """
    override = {k: "" for k in _ALL_APPROVAL_KEYS}
    for a in approvers_sorted:
        member = db.query(OrgMember).filter(OrgMember.id == a.approver_member_id).first()
        if not member:
            continue
        title = (member.title or "").strip()
        field_key = _TITLE_TO_APPROVAL_FIELD.get(title)
        if field_key and not override[field_key]:
            override[field_key] = member.name
    return override


# DB 테이블 자동 생성 (이미 있으면 무시)
Base.metadata.create_all(bind=engine)

FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")

app = FastAPI(title="AI 결재서류 자동작성", version="1.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        FRONTEND_ORIGIN,
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://192.168.219.175:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==============================================================
# 공통 유틸
# ==============================================================
def _validate_docx(filename: str | None):
    name = (filename or "").lower()
    if name.endswith(".hwp") or name.endswith(".hwpx"):
        raise HTTPException(
            status_code=400,
            detail="HWP/HWPX 파일은 지원하지 않습니다. DOCX 파일을 업로드 해주세요.",
        )
    if not name.endswith(".docx"):
        raise HTTPException(
            status_code=400,
            detail="DOCX 파일만 업로드 가능합니다.",
        )


def _build_json_schema(template_name: str, fields: list) -> dict:
    properties = {}
    required = []
    for f in fields:
        ftype = f.get("type", "string")
        json_type = "number" if ftype == "number" else "string"
        properties[f["key"]] = {
            "type": json_type,
            "title": f.get("label", f["key"]),
        }
        if f.get("required"):
            required.append(f["key"])
    return {
        "type": "object",
        "title": template_name,
        "properties": properties,
        "required": required,
    }


def _to_dict_summary(t: DocumentTemplate) -> dict:
    return {
        "id": t.id,
        "template_name": t.template_name,
        "file_name": t.file_name,
        "description": t.description,
        "placeholder_keys": t.placeholder_keys or [],
        "is_active": t.is_active,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


def _to_dict_detail(t: DocumentTemplate) -> dict:
    base = _to_dict_summary(t)
    base.update({
        "html_content": t.html_content,
        "template_schema": t.template_schema,
        "fields": t.template_fields or [],
    })
    return base


# ==============================================================
# 헬스체크
# ==============================================================
@app.get("/")
async def root():
    return {"status": "ok", "service": "AI 결재서류 자동작성"}


# ==============================================================
# [1] 양식 분석 — preview-fields
# ==============================================================
@app.post("/api/templates/preview-fields")
async def preview_fields(
    template: UploadFile = File(...),
    template_name: str = Form(...),
    description: str = Form(""),
):
    _validate_docx(template.filename)

    raw = await template.read()
    if not raw:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")

    try:
        base_html = docx_to_html(raw)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DOCX 변환 실패: {e}")

    try:
        fields, document_type = analyze_template(base_html)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 분석 실패: {e}")

    html_template = insert_placeholders_into_cells(base_html, fields)
    json_schema = _build_json_schema(template_name, fields)

    # 각 셀의 원본 텍스트를 추출해 프론트에 전달 (cell_template 작성 참고용)
    from bs4 import BeautifulSoup as _BS
    _soup = _BS(base_html, "html.parser")
    cell_texts: dict = {}
    for _cell in _soup.find_all(attrs={"data-cell-id": True}):
        cid = _cell.get("data-cell-id")
        txt = _cell.get_text(separator=" ").strip()
        if cid and txt:
            cell_texts[cid] = txt

    return {
        "status": "ok",
        "document_type": document_type,
        "html_template": html_template,
        "preview_html": html_template,
        "fields": fields,
        "json_schema": json_schema,
        "placeholder_keys": [f["key"] for f in fields],
        "cell_texts": cell_texts,
    }


# ==============================================================
# [2] 양식 저장 — save-with-fields
# ==============================================================
@app.post("/api/templates/save-with-fields")
async def save_template_with_fields(
    template: UploadFile = File(...),
    template_name: str = Form(...),
    description: str = Form(""),
    fields: str = Form(...),
    db: Session = Depends(get_db),
):
    _validate_docx(template.filename)

    if not template_name.strip():
        raise HTTPException(status_code=400, detail="template_name 이 비어있습니다.")

    raw = await template.read()
    if not raw:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")

    try:
        confirmed_raw = json.loads(fields)
    except Exception:
        raise HTTPException(status_code=400, detail="fields 는 JSON 문자열이어야 합니다.")
    if not isinstance(confirmed_raw, list):
        raise HTTPException(status_code=400, detail="fields 는 배열이어야 합니다.")

    confirmed_fields = normalize_fields(confirmed_raw)

    try:
        base_html = docx_to_html(raw)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DOCX 변환 실패: {e}")

    html_with_ph = insert_placeholders_into_cells(base_html, confirmed_fields)
    final_html, json_schema, placeholder_keys = convert_template_to_schema(
        html_with_ph, confirmed_fields, template_name
    )

    # 실제로 HTML 안에 {{key}} 가 존재하는 키만 placeholder_keys 로 확정
    # (cell_id 없이 cell_template 으로만 포함된 키도 자동 감지)
    import re as _re2
    keys_in_html = set(_re2.findall(r"\{\{(\w+)\}\}", final_html))
    placeholder_keys = [k for k in placeholder_keys if k in keys_in_html]

    # 검증: cell_id 또는 cell_template 에 포함되었어야 할 키가 HTML 에 없으면 경고
    keys_to_validate = []
    all_template_keys: set = set()
    for f in confirmed_fields:
        ct = (f.get("cell_template") or "").strip()
        if ct:
            all_template_keys.update(_re2.findall(r"\{\{(\w+)\}\}", ct))
        # cell_ids 배열 또는 단일 cell_id 가 있으면 검증 대상
        has_cell = bool(f.get("cell_ids") or f.get("cell_id"))
        if has_cell and f["key"] not in keys_to_validate:
            keys_to_validate.append(f["key"])
    # cell_template 에서 참조된 키도 검증 대상에 포함
    for f in confirmed_fields:
        if f["key"] in all_template_keys and f["key"] not in keys_to_validate:
            keys_to_validate.append(f["key"])

    ok, msg = validate_template_conversion(final_html, keys_to_validate)
    if not ok:
        raise HTTPException(status_code=400, detail=f"템플릿 검증 실패: {msg}")

    obj = DocumentTemplate(
        template_name=template_name.strip(),
        file_name=template.filename,
        html_content=final_html,
        description=(description or "").strip() or None,
        template_schema=json_schema,
        placeholder_keys=placeholder_keys,
        template_fields=confirmed_fields,
        is_active=True,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)

    return {
        "status": "ok",
        **_to_dict_summary(obj),
    }


# ==============================================================
# [3] 양식 목록
# ==============================================================
@app.get("/api/templates")
async def list_templates(db: Session = Depends(get_db)):
    rows = (
        db.query(DocumentTemplate)
        .filter(DocumentTemplate.is_active == True)  # noqa: E712
        .order_by(DocumentTemplate.created_at.desc())
        .all()
    )
    return {
        "status": "ok",
        "templates": [_to_dict_summary(r) for r in rows],
    }


# ==============================================================
# [4] 양식 상세
# ==============================================================
@app.get("/api/templates/{template_id}")
async def get_template_detail(template_id: int, db: Session = Depends(get_db)):
    row = (
        db.query(DocumentTemplate)
        .filter(DocumentTemplate.id == template_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="템플릿을 찾을 수 없습니다.")
    return {
        "status": "ok",
        **_to_dict_detail(row),
    }


# ==============================================================
# [5] AI 자동작성 — generate-once (교육용 페이지 전용)
# ==============================================================
@app.post("/api/education/generate-once")
async def generate_once(
    template_name: str = Form(...),
    html_template: str = Form(...),
    fields: str = Form(...),
    description: str = Form(""),
    receipts: List[UploadFile] = File(default=[]),
):
    try:
        fields_obj = json.loads(fields)
    except Exception:
        raise HTTPException(status_code=400, detail="fields 는 JSON 문자열이어야 합니다.")

    if not isinstance(fields_obj, list):
        raise HTTPException(status_code=400, detail="fields 는 배열이어야 합니다.")

    images = []
    for up in receipts or []:
        data = await up.read()
        if not data:
            continue
        ct = (up.content_type or "image/jpeg").lower()
        if ct not in ("image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"):
            ct = "image/jpeg"
        if ct == "image/jpg":
            ct = "image/jpeg"
        b64 = base64.b64encode(data).decode("utf-8")
        images.append({"media_type": ct, "data": b64})

    try:
        expense_data = generate_document(fields_obj, description or "", images)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 자동작성 실패: {e}")

    filled_html = replace_placeholders(html_template, expense_data)

    return {
        "status": "ok",
        "expense_data": expense_data,
        "filled_html": filled_html,
    }


# ==============================================================
# [6] 조직원 수동 등록
# ==============================================================
@app.post("/api/org-members")
async def create_org_member(body: dict, db: Session = Depends(get_db)):
    name = (body.get("name") or "").strip()
    title = (body.get("title") or "").strip()
    email = (body.get("email") or "").strip()
    department = (body.get("department") or "").strip()

    if not name:
        raise HTTPException(status_code=400, detail="name 은 필수입니다.")
    if not title:
        raise HTTPException(status_code=400, detail="title 은 필수입니다.")
    if not email:
        raise HTTPException(status_code=400, detail="email 은 필수입니다.")

    existing = (
        db.query(OrgMember)
        .filter(OrgMember.email == email, OrgMember.is_active == True)  # noqa: E712
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail=f"이미 등록된 이메일입니다: {email}")

    obj = OrgMember(
        name=name,
        title=title,
        email=email,
        department=department or None,
        is_active=True,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)

    return {"status": "ok", "id": obj.id}


# ==============================================================
# [7] 조직원 목록 조회
# ==============================================================
@app.get("/api/org-members")
async def list_org_members(db: Session = Depends(get_db)):
    rows = (
        db.query(OrgMember)
        .filter(OrgMember.is_active == True)  # noqa: E712
        .order_by(OrgMember.id.asc())
        .all()
    )
    return [
        {
            "id": r.id,
            "name": r.name,
            "department": r.department or "",
            "title": r.title,
            "email": r.email,
        }
        for r in rows
    ]


# ==============================================================
# [8] 조직도 이미지 AI 분석 미리보기
# ==============================================================
@app.post("/api/org-members/preview-image")
async def preview_org_image(org_image: UploadFile = File(...)):
    ct = (org_image.content_type or "").lower()
    if not ct.startswith("image/"):
        raise HTTPException(status_code=400, detail="이미지 파일만 업로드 가능합니다.")

    raw = await org_image.read()
    if not raw:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")

    image_mime = ct if ct in (
        "image/jpeg", "image/png", "image/gif", "image/webp"
    ) else "image/jpeg"
    image_b64 = base64.b64encode(raw).decode("utf-8")

    try:
        rows = claude_service.preview_org_members_from_image(image_b64, image_mime)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 분석 실패: {e}")

    cleaned = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        title = str(row.get("title") or "").strip()
        email = str(row.get("email") or "").strip()
        department = str(row.get("department") or "").strip()
        if not name and not title and not email:
            continue
        cleaned.append({
            "name": name,
            "department": department,
            "title": title,
            "email": email,
        })

    return {
        "status": "ok",
        "rows": cleaned,
        "parsed_count": len(cleaned),
        "file_name": org_image.filename or "",
    }


# ==============================================================
# [9] AI 추출 결과 일괄 저장
# ==============================================================
@app.post("/api/org-members/save-preview")
async def save_preview_org_members(body: dict, db: Session = Depends(get_db)):
    rows = body.get("rows")
    if not rows or not isinstance(rows, list) or len(rows) == 0:
        raise HTTPException(status_code=400, detail="rows 가 비어있습니다.")

    existing_emails = {
        r.email
        for r in db.query(OrgMember.email)
        .filter(OrgMember.is_active == True)  # noqa: E712
        .all()
    }

    saved = []
    skipped = []
    seen_in_batch: set[str] = set()

    for row in rows:
        if not isinstance(row, dict):
            skipped.append(row)
            continue

        name = str(row.get("name") or "").strip()
        title = str(row.get("title") or "").strip()
        email = str(row.get("email") or "").strip()
        department = str(row.get("department") or "").strip()

        if not name or not title or not email:
            skipped.append(row)
            continue

        if email in existing_emails or email in seen_in_batch:
            skipped.append(row)
            continue

        seen_in_batch.add(email)
        obj = OrgMember(
            name=name,
            title=title,
            email=email,
            department=department or None,
            is_active=True,
        )
        db.add(obj)
        saved.append({"name": name, "department": department, "title": title, "email": email})

    db.commit()

    return {
        "status": "ok",
        "saved_count": len(saved),
        "skipped_count": len(skipped),
        "saved": saved,
        "skipped": skipped,
    }


# ==============================================================
# 결재 규칙 공통 저장 함수
# ==============================================================
def _persist_approval_rule(
    db: Session,
    payload: dict,
    source_type: str,
    source_name: str | None = None,
) -> ApprovalRule:
    rule_name = (payload.get("rule_name") or "").strip()
    rule_text = (payload.get("rule_text") or "").strip()
    template_id = payload.get("template_id")
    approvers_raw = payload.get("approvers") or []
    source_excerpt = payload.get("source_excerpt") or None

    if not rule_name:
        raise HTTPException(status_code=400, detail="rule_name 은 필수입니다.")
    if not rule_text:
        raise HTTPException(status_code=400, detail="rule_text 는 필수입니다.")
    if not template_id:
        raise HTTPException(status_code=400, detail="template_id 는 필수입니다.")
    if not approvers_raw or len(approvers_raw) == 0:
        raise HTTPException(status_code=400, detail="approvers 는 1명 이상이어야 합니다.")

    template = (
        db.query(DocumentTemplate)
        .filter(
            DocumentTemplate.id == template_id,
            DocumentTemplate.is_active == True,  # noqa: E712
        )
        .first()
    )
    if not template:
        raise HTTPException(
            status_code=400,
            detail=f"template_id={template_id} 가 존재하지 않거나 비활성입니다.",
        )

    seen_orders: list[int] = []
    seen_members: list[int] = []
    for a in approvers_raw:
        mid = a.get("approver_member_id")
        order = a.get("approval_order")

        if mid is None or order is None:
            raise HTTPException(status_code=400, detail="approver_member_id 와 approval_order 는 필수입니다.")

        mid = int(mid)
        order = int(order)

        if order in seen_orders:
            raise HTTPException(status_code=400, detail=f"approval_order={order} 가 중복입니다.")
        seen_orders.append(order)

        if mid in seen_members:
            raise HTTPException(status_code=400, detail=f"approver_member_id={mid} 가 중복입니다.")
        seen_members.append(mid)

        member = (
            db.query(OrgMember)
            .filter(OrgMember.id == mid, OrgMember.is_active == True)  # noqa: E712
            .first()
        )
        if not member:
            raise HTTPException(
                status_code=400,
                detail=f"approver_member_id={mid} 가 존재하지 않거나 비활성입니다.",
            )

    rule = ApprovalRule(
        rule_name=rule_name,
        rule_text=rule_text,
        template_id=int(template_id),
        source_type=source_type,
        source_name=source_name,
        source_excerpt=source_excerpt,
        is_active=True,
    )
    db.add(rule)
    db.flush()

    for a in approvers_raw:
        approver = ApprovalRuleApprover(
            rule_id=rule.id,
            approver_member_id=int(a["approver_member_id"]),
            approval_order=int(a["approval_order"]),
            approval_type=a.get("approval_type") or "결재",
        )
        db.add(approver)

    db.commit()
    db.refresh(rule)
    return rule


# ==============================================================
# [10] 결재 규칙 수동 저장
# ==============================================================
@app.post("/api/approval-rules")
async def create_approval_rule(body: dict, db: Session = Depends(get_db)):
    rule = _persist_approval_rule(db, body, source_type="manual", source_name=None)
    return {"status": "ok", "id": rule.id}


# ==============================================================
# [11] 결재 규칙 목록 조회
# ==============================================================
@app.get("/api/approval-rules")
async def list_approval_rules(db: Session = Depends(get_db)):
    rules = (
        db.query(ApprovalRule)
        .filter(ApprovalRule.is_active == True)  # noqa: E712
        .order_by(ApprovalRule.created_at.desc())
        .all()
    )

    result = []
    for rule in rules:
        template = db.query(DocumentTemplate).filter(DocumentTemplate.id == rule.template_id).first()
        template_name = template.template_name if template else ""

        approvers_data = []
        for a in sorted(rule.approvers, key=lambda x: x.approval_order):
            member = db.query(OrgMember).filter(OrgMember.id == a.approver_member_id).first()
            approvers_data.append({
                "approver_member_id": a.approver_member_id,
                "approval_order": a.approval_order,
                "approval_type": a.approval_type,
                "approver_name": member.name if member else "",
                "approver_title": member.title if member else "",
                "approver_email": member.email if member else "",
            })

        result.append({
            "id": rule.id,
            "rule_name": rule.rule_name,
            "rule_text": rule.rule_text,
            "template_id": rule.template_id,
            "template_name": template_name,
            "source_type": rule.source_type,
            "source_name": rule.source_name,
            "source_excerpt": rule.source_excerpt,
            "approvers": approvers_data,
        })

    return result


# ==============================================================
# [12] PDF 지침서 AI 분석 미리보기
# ==============================================================
@app.post("/api/approval-rules/preview-guide")
async def preview_approval_rules_by_guide(
    guide_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    filename = (guide_file.filename or "").lower()
    if not filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF 파일만 업로드 가능합니다.")

    raw = await guide_file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")

    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        pages_text = []
        for page in reader.pages:
            text = page.extract_text() or ""
            pages_text.append(text)
        guide_text = "\n".join(pages_text).strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF 텍스트 추출 실패: {e}")

    if not guide_text:
        raise HTTPException(status_code=400, detail="PDF에서 텍스트를 추출할 수 없습니다. 스캔 이미지 PDF는 지원하지 않습니다.")

    templates = (
        db.query(DocumentTemplate)
        .filter(DocumentTemplate.is_active == True)  # noqa: E712
        .all()
    )
    members = (
        db.query(OrgMember)
        .filter(OrgMember.is_active == True)  # noqa: E712
        .all()
    )

    if not templates:
        raise HTTPException(status_code=400, detail="등록된 결재서류 양식이 없습니다. 먼저 양식을 등록하세요.")
    if not members:
        raise HTTPException(status_code=400, detail="등록된 조직원이 없습니다. 먼저 조직원을 등록하세요.")

    templates_for_ai = [{"id": t.id, "template_name": t.template_name} for t in templates]
    members_for_ai = [
        {"id": m.id, "name": m.name, "title": m.title, "department": m.department or ""}
        for m in members
    ]

    try:
        rules = claude_service.preview_approval_rules_from_guide(
            guide_text, templates_for_ai, members_for_ai
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 분석 실패: {e}")

    return {
        "status": "ok",
        "rules": rules,
        "parsed_rule_count": len(rules),
        "source_name": guide_file.filename or "",
    }


# ==============================================================
# [13] AI 결재 규칙 후보 일괄 저장
# ==============================================================
@app.post("/api/approval-rules/save-preview")
async def save_preview_approval_rules(body: dict, db: Session = Depends(get_db)):
    source_name = body.get("source_name") or None
    rules_raw = body.get("rules") or []

    if not rules_raw or not isinstance(rules_raw, list):
        raise HTTPException(status_code=400, detail="rules 가 비어있습니다.")

    results = []
    for payload in rules_raw:
        rule = _persist_approval_rule(
            db,
            payload,
            source_type="guide",
            source_name=source_name,
        )
        results.append({"id": rule.id})

    return {
        "status": "ok",
        "saved_rule_count": len(results),
        "results": results,
    }


# ==============================================================
# [14] 결재서류 자동 작성 — /api/process
# ==============================================================
@app.post("/api/process")
async def process_document(
    template_id: str = Form(...),
    description: str = Form(""),
    requester_name: str = Form(""),
    requester_department: str = Form(""),
    receipts: List[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
):
    """
    저장된 템플릿 + 결재 규칙을 기반으로 AI가 결재서류를 자동 작성합니다.
    결과를 GeneratedDocument에 저장하고 filled_html과 수신자 정보를 반환합니다.
    """
    # ── 1. 템플릿 조회 ──
    try:
        tid = int(template_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="template_id 는 정수여야 합니다.")

    template = (
        db.query(DocumentTemplate)
        .filter(DocumentTemplate.id == tid, DocumentTemplate.is_active == True)  # noqa: E712
        .first()
    )
    if not template:
        raise HTTPException(status_code=404, detail="템플릿을 찾을 수 없습니다.")

    # ── 2. 결재 규칙 조회 (최신 활성 규칙 1개) ──
    rule = (
        db.query(ApprovalRule)
        .filter(
            ApprovalRule.template_id == tid,
            ApprovalRule.is_active == True,  # noqa: E712
        )
        .order_by(ApprovalRule.created_at.desc())
        .first()
    )
    if not rule:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{template.template_name}' 양식에 등록된 결재 규칙이 없습니다. "
                "먼저 [양식 규칙 관리] 페이지에서 결재 규칙을 등록하세요."
            ),
        )

    # ── 3. 결재자 정렬 & 1차 결재자를 수신자로 지정 ──
    approvers_sorted = sorted(rule.approvers, key=lambda x: x.approval_order)

    recipient_name = ""
    recipient_email = ""
    recipient_title = ""
    recipient_member_id = None

    if approvers_sorted:
        first = approvers_sorted[0]
        m = db.query(OrgMember).filter(OrgMember.id == first.approver_member_id).first()
        if m:
            recipient_name = m.name
            recipient_email = m.email
            recipient_title = m.title
            recipient_member_id = m.id

    # ── 4. 결재선 문자열 구성 (AI 프롬프트에 포함) ──
    lines = [
        f"결재 규칙명: {rule.rule_name}",
        f"규칙 설명: {rule.rule_text}",
        "결재선:",
    ]
    for a in approvers_sorted:
        m = db.query(OrgMember).filter(OrgMember.id == a.approver_member_id).first()
        if m:
            dept = f", {m.department}" if m.department else ""
            lines.append(f"  {a.approval_order}차 {a.approval_type}: {m.name} ({m.title}{dept})")
    approval_rules_text = "\n".join(lines)

    # ── 5. 영수증 이미지 base64 변환 ──
    images: list = []
    receipt_names: list[str] = []
    for up in receipts or []:
        data = await up.read()
        if not data:
            continue
        ct = (up.content_type or "image/jpeg").lower()
        if ct not in ("image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"):
            ct = "image/jpeg"
        if ct == "image/jpg":
            ct = "image/jpeg"
        b64 = base64.b64encode(data).decode("utf-8")
        images.append({"media_type": ct, "data": b64})
        if up.filename:
            receipt_names.append(up.filename)

    # ── 6. AI 자동 작성 ──
    template_schema = template.template_schema or {}
    try:
        expense_data = claude_service.extract_expense_data(
            template_html=template.html_content,
            template_name=template.template_name,
            template_schema=template_schema,
            description=description or "",
            receipt_images=images or None,
            approval_rules=approval_rules_text,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 자동작성 실패: {e}")

    # ── 7. 숫자 타입 필드 정규화 ──
    props = template_schema.get("properties", {})
    for key, val in expense_data.items():
        if key in props and props[key].get("type") == "number":
            try:
                if isinstance(val, str):
                    cleaned = (
                        val.replace(",", "")
                           .replace("원", "")
                           .replace("₩", "")
                           .replace(" ", "")
                    )
                    expense_data[key] = (
                        float(cleaned) if "." in cleaned else int(cleaned)
                    )
            except (ValueError, TypeError):
                pass  # 변환 실패 시 원래 값 유지

    # ── 7.5. 결재자 필드 DB 기준 강제 덮어쓰기 (AI 생성값 무시) ──
    approval_override = _build_approval_override(approvers_sorted, db)
    expense_data.update(approval_override)

    # ── 8. 플레이스홀더 치환 → filled_html 생성 ──
    filled_html = replace_placeholders(template.html_content, expense_data)

    # ── 9. 작성자(기안자) 정보 추출 ──
    # 프론트엔드에서 명시적으로 전달한 값 우선, 없으면 AI 추출값 사용
    requester_name = (requester_name.strip() or
        expense_data.get("requester") or
        expense_data.get("employee_name") or
        expense_data.get("draft_person") or
        expense_data.get("name") or
        ""
    )
    requester_department = (requester_department.strip() or
        expense_data.get("department") or
        expense_data.get("draft_department") or
        expense_data.get("employee_department") or
        ""
    )

    # ── 10. 결재선 문자열/JSON 구성 (DB 기반) ──
    approval_line_parts = []
    approval_line_json_list = []
    for a in approvers_sorted:
        m = db.query(OrgMember).filter(OrgMember.id == a.approver_member_id).first()
        if m:
            dept = f" ({m.department})" if m.department else ""
            approval_line_parts.append(
                f"{a.approval_order}차 {a.approval_type}: {m.name} {m.title}{dept}"
            )
            approval_line_json_list.append({
                "order": a.approval_order,
                "type": a.approval_type,
                "name": m.name,
                "title": m.title,
                "department": m.department or "",
                "email": m.email,
            })
    approval_line_text = " → ".join(approval_line_parts)

    # ── 11. GeneratedDocument 저장 ──
    doc = GeneratedDocument(
        template_id=template.id,
        applied_rule_id=rule.id,
        recipient_member_id=recipient_member_id,
        document_title=template.template_name,
        description=(description or "").strip() or None,
        receipt_file_name=", ".join(receipt_names) if receipt_names else None,
        extracted_json=expense_data,
        html_content=filled_html,
        recipient_name=recipient_name,
        recipient_email=recipient_email,
        recipient_title=recipient_title,
        is_sent=False,
        requester_name=requester_name or None,
        requester_department=requester_department or None,
        approval_line_text=approval_line_text or None,
        approval_line_json=approval_line_json_list or None,
        is_active=True,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    return {
        "document_id": doc.id,
        "expense_data": expense_data,
        "filled_html": filled_html,
        "recipient_name": recipient_name,
        "recipient_title": recipient_title,
        "recipient_email": recipient_email,
        "template_name": template.template_name,
        "file_name": template.file_name or "",
        "requester_name": requester_name,
        "requester_department": requester_department,
        "approval_line_text": approval_line_text,
    }


# ==============================================================
# [15] 이메일 발송 — /api/send-email
# ==============================================================
@app.post("/api/send-email")
async def send_email_endpoint(body: dict, db: Session = Depends(get_db)):
    """
    작성된 결재서류를 이메일로 발송합니다.
    filled_html은 사용자가 편집한 최신 HTML을 프론트에서 전달해야 합니다.
    SMTP 설정이 없으면 DB만 is_sent=True 로 갱신하고 성공을 반환합니다.
    """
    document_id = body.get("document_id")
    to_email = (body.get("to_email") or "").strip()
    to_name = (body.get("to_name") or "").strip()
    requester = (body.get("requester") or "").strip()
    template_name = (body.get("template_name") or "결재서류").strip()
    filled_html = body.get("filled_html") or ""

    if not document_id:
        raise HTTPException(status_code=400, detail="document_id 는 필수입니다.")
    if not to_email:
        raise HTTPException(status_code=400, detail="to_email 은 필수입니다.")

    doc = db.query(GeneratedDocument).filter(
        GeneratedDocument.id == int(document_id)
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")

    # ── SMTP 설정 확인 ──
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587") or "587")
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_pass = os.getenv("SMTP_PASS", "").strip()
    smtp_from = os.getenv("SMTP_FROM", smtp_user).strip() or smtp_user

    smtp_configured = bool(smtp_host and smtp_user and smtp_pass)
    email_sent = False

    if smtp_configured:
        try:
            requester_line = f"<p><strong>요청자:</strong> {requester}</p>" if requester else ""
            html_body = f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><title>{template_name} 결재 요청</title></head>
<body style="font-family:sans-serif;max-width:800px;margin:0 auto;padding:20px;">
  <h2 style="color:#1e40af;">[결재 요청] {template_name}</h2>
  <p>{to_name} 님, 아래 결재서류를 검토해 주세요.</p>
  {requester_line}
  <hr style="margin:20px 0;">
  {filled_html}
</body></html>"""

            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[결재 요청] {template_name}"
            msg["From"] = smtp_from
            msg["To"] = to_email
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
                server.ehlo()
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_from, [to_email], msg.as_string())

            email_sent = True

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"이메일 발송 실패: {e}")
    else:
        # SMTP 미설정 시 DB만 업데이트하고 성공 반환
        email_sent = False

    # ── is_sent 업데이트 ──
    doc.is_sent = True
    db.commit()

    return {
        "status": "ok",
        "email_sent": email_sent,
        "smtp_configured": smtp_configured,
        "message": (
            "이메일이 발송되었습니다." if email_sent
            else "SMTP 설정이 없어 이메일을 발송하지 않았습니다. .env에 SMTP 설정을 추가하면 실제로 발송됩니다."
        ),
    }


# ==============================================================
# [16] 편집된 HTML 저장 — PATCH /api/generated-documents/{document_id}
# ==============================================================
@app.patch("/api/generated-documents/{document_id}")
async def update_generated_document(
    document_id: int,
    body: dict,
    db: Session = Depends(get_db),
):
    """
    사용자가 미리보기에서 편집한 HTML을 DB에 반영합니다.
    """
    doc = db.query(GeneratedDocument).filter(
        GeneratedDocument.id == document_id
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")

    html_content = body.get("html_content")
    extracted_json = body.get("extracted_json")

    if html_content is not None:
        doc.html_content = html_content
    if extracted_json is not None:
        doc.extracted_json = extracted_json

    db.commit()

    return {"status": "ok", "document_id": document_id}


# ==============================================================
# [17] 자동작성 문서 목록 조회 — GET /api/generated-documents
# ==============================================================
@app.get("/api/generated-documents")
async def list_generated_documents(db: Session = Depends(get_db)):
    """
    is_active=True 인 문서 목록을 최신순으로 반환합니다.
    """
    docs = (
        db.query(GeneratedDocument)
        .filter(GeneratedDocument.is_active == True)  # noqa: E712
        .order_by(GeneratedDocument.created_at.desc())
        .all()
    )

    result = []
    for doc in docs:
        template = db.query(DocumentTemplate).filter(DocumentTemplate.id == doc.template_id).first()
        result.append({
            "id": doc.id,
            "document_title": doc.document_title or "",
            "template_id": doc.template_id,
            "template_name": template.template_name if template else "",
            "requester_name": doc.requester_name or "",
            "requester_department": doc.requester_department or "",
            "approval_line_text": doc.approval_line_text or "",
            "recipient_name": doc.recipient_name or "",
            "recipient_email": doc.recipient_email or "",
            "recipient_title": doc.recipient_title or "",
            "is_sent": doc.is_sent,
            "description": doc.description or "",
            "receipt_file_name": doc.receipt_file_name or "",
            "created_at": doc.created_at.isoformat() if doc.created_at else "",
            "updated_at": doc.updated_at.isoformat() if doc.updated_at else "",
        })

    total = len(result)
    sent_count = sum(1 for d in result if d["is_sent"])
    return {
        "documents": result,
        "total": total,
        "sent_count": sent_count,
        "unsent_count": total - sent_count,
    }


# ==============================================================
# [18] 자동작성 문서 상세 조회 — GET /api/generated-documents/{document_id}
# ==============================================================
@app.get("/api/generated-documents/{document_id}")
async def get_generated_document(document_id: int, db: Session = Depends(get_db)):
    """
    문서 상세 정보와 html_content, extracted_json, approval_line_json 을 반환합니다.
    """
    doc = db.query(GeneratedDocument).filter(
        GeneratedDocument.id == document_id,
        GeneratedDocument.is_active == True,  # noqa: E712
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")

    template = db.query(DocumentTemplate).filter(DocumentTemplate.id == doc.template_id).first()

    return {
        "id": doc.id,
        "document_title": doc.document_title or "",
        "template_id": doc.template_id,
        "template_name": template.template_name if template else "",
        "requester_name": doc.requester_name or "",
        "requester_department": doc.requester_department or "",
        "approval_line_text": doc.approval_line_text or "",
        "approval_line_json": doc.approval_line_json or [],
        "recipient_name": doc.recipient_name or "",
        "recipient_email": doc.recipient_email or "",
        "recipient_title": doc.recipient_title or "",
        "is_sent": doc.is_sent,
        "description": doc.description or "",
        "receipt_file_name": doc.receipt_file_name or "",
        "extracted_json": doc.extracted_json or {},
        "html_content": doc.html_content or "",
        "created_at": doc.created_at.isoformat() if doc.created_at else "",
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else "",
    }


# ==============================================================
# [19] 자동작성 문서 소프트 삭제 — DELETE /api/generated-documents/{document_id}
# ==============================================================
@app.delete("/api/generated-documents/{document_id}")
async def delete_generated_document(document_id: int, db: Session = Depends(get_db)):
    """
    is_active = False 로 설정하여 소프트 삭제합니다.
    """
    doc = db.query(GeneratedDocument).filter(
        GeneratedDocument.id == document_id,
        GeneratedDocument.is_active == True,  # noqa: E712
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")

    doc.is_active = False
    db.commit()

    return {"status": "ok", "document_id": document_id, "message": "문서가 삭제되었습니다."}
