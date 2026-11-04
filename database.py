"""SQLAlchemy storage: SQLite for development, PostgreSQL for production."""
import os
import time
import uuid
from dotenv import load_dotenv
from sqlalchemy import create_engine, event, String, Text, Integer, ForeignKey, CheckConstraint, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

load_dotenv()


def new_id():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    google_sub: Mapped[str] = mapped_column(String(255), unique=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(100))


class Membership(Base):
    __tablename__ = "memberships"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(10))
    __table_args__ = (CheckConstraint("role IN ('Admin','Member','Viewer')", name="membership_role"), Index("ix_memberships_user", "user_id"))


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))


class Task(Base):
    __tablename__ = "project_tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    description: Mapped[str] = mapped_column(Text)
    due_date: Mapped[str] = mapped_column(String(100), default="")
    owner: Mapped[str] = mapped_column(String(120), default="")
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    priority: Mapped[str] = mapped_column(String(10), default="Medium")
    status: Mapped[str] = mapped_column(String(20), default="To Do")
    __table_args__ = (CheckConstraint("priority IN ('High','Medium','Low')", name="task_priority"), CheckConstraint("status IN ('To Do','In Progress','Done')", name="task_status"), Index("ix_tasks_project_id", "project_id", "id"))


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[int] = mapped_column(Integer, index=True)


class OAuthFlow(Base):
    __tablename__ = "oauth_flows"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    browser_hash: Mapped[str] = mapped_column(String(64))
    nonce: Mapped[str] = mapped_column(String(64))
    verifier: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[int] = mapped_column(Integer, index=True)


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(254))
    role: Mapped[str] = mapped_column(String(10))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[int] = mapped_column(Integer, index=True)
    __table_args__ = (CheckConstraint("role IN ('Admin','Member','Viewer')", name="invite_role"),)


class RateBucket(Base):
    __tablename__ = "rate_buckets"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window: Mapped[int] = mapped_column(Integer, primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[int] = mapped_column(Integer, index=True)


def build_engine(url):
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    options = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False, "timeout": 15}
    else:
        options.update(pool_size=5, max_overflow=5, pool_timeout=10, connect_args={"connect_timeout": 10, "options": "-c statement_timeout=15000"})
    engine = create_engine(url, **options)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def sqlite_setup(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
    return engine


engine = build_engine(os.getenv("DATABASE_URL", "sqlite:///./taskiq.db"))
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as db:
        yield db
