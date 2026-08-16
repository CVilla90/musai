from sqlmodel import create_engine, Session, SQLModel
from musai.config import settings

_SQLITE = settings.database_url.startswith("sqlite")

_connect_args = {"check_same_thread": False} if _SQLITE else {}

# expire_on_commit=False keeps ORM objects usable after session.close()
# so that Jinja2 templates can render them after the with-block exits.
_session_kwargs: dict = {"expire_on_commit": False}

# App engine — full read/write (cockpit + local runner)
# On Replit, DATABASE_URL is injected automatically for the native Postgres.
engine = create_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=not _SQLITE,
    connect_args=_connect_args,
)

# Read-only engine — SUSAI process only.
# On Postgres (prod): connects as susai_ro with restricted grants.
# On SQLite (local dev): same file, role enforcement skipped.
ro_engine = create_engine(
    settings.database_url_readonly,
    echo=False,
    pool_pre_ping=not _SQLITE,
    connect_args=_connect_args,
)


def init_db() -> None:
    """Create all tables if they don't exist (SQLite dev only; prod uses Alembic)."""
    import musai.models  # noqa: F401 — ensure metadata is populated
    if _SQLITE:
        SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine, **_session_kwargs) as session:
        yield session


def get_ro_session():
    with Session(ro_engine, **_session_kwargs) as session:
        yield session
