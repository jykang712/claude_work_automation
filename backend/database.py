"""
database.py
- .env 의 DATABASE_URL 을 읽어 SQLAlchemy engine / SessionLocal / Base 생성
- 기본값은 SQLite (templates.db) 로 설정 → 별도 DB 설치 없이 바로 사용 가능
- get_db: FastAPI Depends 용 DB 세션 헬퍼
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

# 기본은 SQLite. 운영시에는 .env 에 PostgreSQL/MySQL URL 을 넣으면 됨.
# 예) postgresql+psycopg2://user:pass@localhost:5432/mydb
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./templates.db")

# SQLite 일 때만 필요한 옵션
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI 의 Depends 용 DB 세션 제너레이터"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()