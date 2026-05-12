"""
migrate_add_columns.py
GeneratedDocument 테이블에 새 컬럼 5개를 추가합니다.
이미 컬럼이 있으면 오류 없이 건너뜁니다.
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "templates.db")

NEW_COLUMNS = [
    ("requester_name",       "VARCHAR(100)"),
    ("requester_department", "VARCHAR(100)"),
    ("approval_line_text",   "TEXT"),
    ("approval_line_json",   "TEXT"),
    ("is_active",            "BOOLEAN NOT NULL DEFAULT 1"),
]

def get_existing_columns(cursor, table):
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}

def migrate():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    existing = get_existing_columns(cursor, "generated_documents")
    added = []

    for col_name, col_type in NEW_COLUMNS:
        if col_name in existing:
            print(f"  [skip] {col_name} — 이미 존재합니다.")
        else:
            sql = f"ALTER TABLE generated_documents ADD COLUMN {col_name} {col_type}"
            cursor.execute(sql)
            added.append(col_name)
            print(f"  [added] {col_name}")

    conn.commit()
    conn.close()

    if added:
        print(f"\n마이그레이션 완료: {len(added)}개 컬럼 추가됨 → {added}")
    else:
        print("\n변경 사항 없음 (모든 컬럼이 이미 존재합니다).")

if __name__ == "__main__":
    migrate()
