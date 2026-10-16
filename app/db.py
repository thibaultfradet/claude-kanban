from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = DATA_DIR / "logs"
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "kanban.db"
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    # Idempotent migration: model/effort were added after the first release.
    with engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(ticket)"))}
        for col in ("model", "effort"):
            if col not in cols:
                conn.execute(text(f"ALTER TABLE ticket ADD COLUMN {col} TEXT"))


def get_session():
    with Session(engine) as session:
        yield session
