"""
models.py — SQLAlchemy ORM 모델
- DocumentTemplate      (테이블명: document_templates)
- OrgMember             (테이블명: org_members)
- ApprovalRule          (테이블명: approval_rules)
- ApprovalRuleApprover  (테이블명: approval_rule_approvers)
- GeneratedDocument     (테이블명: generated_documents)
"""

from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, JSON,
    ForeignKey, UniqueConstraint,
)
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from database import Base


class DocumentTemplate(Base):
    __tablename__ = "document_templates"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)

    template_name = Column(String(255), nullable=False, index=True)
    file_name = Column(String(255), nullable=True)
    html_content = Column(Text, nullable=False)
    description = Column(Text, nullable=True)

    template_schema = Column(JSON, nullable=True)
    placeholder_keys = Column(JSON, nullable=True)
    template_fields = Column(JSON, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self):
        return f"<DocumentTemplate id={self.id} name={self.template_name!r}>"


class OrgMember(Base):
    __tablename__ = "org_members"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    title = Column(String(100), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    department = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self):
        return f"<OrgMember id={self.id} name={self.name!r} email={self.email!r}>"


class ApprovalRule(Base):
    """결재 규칙 — 양식 1개에 대한 결재 순서·결재자를 정의"""
    __tablename__ = "approval_rules"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    rule_name = Column(String(255), nullable=False)
    rule_text = Column(Text, nullable=False)
    template_id = Column(Integer, ForeignKey("document_templates.id"), nullable=False, index=True)

    source_type = Column(String(50), nullable=False, default="manual")
    source_name = Column(String(255), nullable=True)
    source_excerpt = Column(Text, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    approvers = relationship(
        "ApprovalRuleApprover",
        back_populates="rule",
        cascade="all, delete-orphan",
        order_by="ApprovalRuleApprover.approval_order",
    )

    def __repr__(self):
        return f"<ApprovalRule id={self.id} name={self.rule_name!r}>"


class ApprovalRuleApprover(Base):
    """결재선 구성원 — 한 결재 규칙 안에서 순서·유형을 정의"""
    __tablename__ = "approval_rule_approvers"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    rule_id = Column(Integer, ForeignKey("approval_rules.id"), nullable=False, index=True)
    approver_member_id = Column(Integer, ForeignKey("org_members.id"), nullable=False)
    approval_order = Column(Integer, nullable=False)
    approval_type = Column(String(50), nullable=False, default="결재")

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    rule = relationship("ApprovalRule", back_populates="approvers")
    member = relationship("OrgMember")

    __table_args__ = (
        UniqueConstraint("rule_id", "approver_member_id", name="uq_rule_approver_member"),
        UniqueConstraint("rule_id", "approval_order", name="uq_rule_approver_order"),
    )

    def __repr__(self):
        return (
            f"<ApprovalRuleApprover rule_id={self.rule_id} "
            f"member_id={self.approver_member_id} order={self.approval_order}>"
        )


class GeneratedDocument(Base):
    """AI가 자동 작성한 결재서류 결과 저장"""
    __tablename__ = "generated_documents"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)

    # 연결 정보
    template_id = Column(Integer, ForeignKey("document_templates.id"), nullable=False, index=True)
    applied_rule_id = Column(Integer, ForeignKey("approval_rules.id"), nullable=True)
    recipient_member_id = Column(Integer, ForeignKey("org_members.id"), nullable=True)

    # 문서 기본 정보
    document_title = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    receipt_file_name = Column(String(500), nullable=True)
    receipt_content_type = Column(String(100), nullable=True)

    # AI 작성 결과
    extracted_json = Column(JSON, nullable=True)    # AI가 추출한 JSON 데이터
    html_content = Column(Text, nullable=False)     # 사용자가 편집 가능한 최종 HTML

    # 수신자 정보 (1차 결재자)
    recipient_name = Column(String(100), nullable=True)
    recipient_email = Column(String(255), nullable=True)
    recipient_title = Column(String(100), nullable=True)

    # 작성자 정보 (영수증/기안자)
    requester_name = Column(String(100), nullable=True)
    requester_department = Column(String(100), nullable=True)

    # 결재선 정보 (DB 기반으로 구성)
    approval_line_text = Column(Text, nullable=True)   # 사람이 읽을 수 있는 결재선 문자열
    approval_line_json = Column(JSON, nullable=True)   # 결재선 구조화 데이터

    # 발송 상태
    is_sent = Column(Boolean, default=False, nullable=False)

    # 활성 상태 (소프트 삭제)
    is_active = Column(Boolean, default=True, nullable=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self):
        return f"<GeneratedDocument id={self.id} title={self.document_title!r}>"
