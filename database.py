"""SQLAlchemy storage: SQLite for development, PostgreSQL for production."""
import os
import time
import uuid
from datetime import date
from dotenv import load_dotenv
from sqlalchemy import create_engine, event, String, Text, Integer, Date, ForeignKey, CheckConstraint, Index, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, relationship

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
    source_proposal_id: Mapped[str | None] = mapped_column(ForeignKey("task_proposals.id", ondelete="RESTRICT", name="fk_task_source_proposal"), nullable=True, unique=True)
    source_proposal = relationship("TaskProposal", lazy="joined")
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


class Meeting(Base):
    __tablename__ = "meetings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    meeting_date: Mapped[date] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(String(64))
    source_type: Mapped[str] = mapped_column(String(12))
    source_filename: Mapped[str] = mapped_column(String(200), default="")
    source_text: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))
    request_id: Mapped[str] = mapped_column(String(36))
    input_hash: Mapped[str] = mapped_column(String(64))
    extraction_status: Mapped[str] = mapped_column(String(12), default="Draft")
    extraction_attempt: Mapped[int] = mapped_column(Integer, default=0)
    extraction_started_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    __table_args__ = (
        UniqueConstraint("project_id", "request_id", name="uq_meeting_request"),
        CheckConstraint("source_type IN ('notes','transcript')", name="meeting_source_type"),
        CheckConstraint("extraction_status IN ('Draft','Extracting','Ready','Failed')", name="meeting_extraction_status"),
        Index("ix_meetings_project_created", "project_id", "created_at", "id"),
    )


class TaskProposal(Base):
    __tablename__ = "task_proposals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    description: Mapped[str] = mapped_column(Text)
    due_date: Mapped[str] = mapped_column(String(100), default="")
    owner: Mapped[str] = mapped_column(String(120), default="")
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    priority: Mapped[str] = mapped_column(String(10), default="Medium")
    source_quote: Mapped[str] = mapped_column(Text)
    source_start: Mapped[int] = mapped_column(Integer)
    source_end: Mapped[int] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10), default="Pending")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str] = mapped_column(String(36), default=new_id)
    input_hash: Mapped[str] = mapped_column(String(64))
    meeting = relationship("Meeting", lazy="joined")
    __table_args__ = (
        UniqueConstraint("meeting_id", "request_id", name="uq_proposal_request"),
        CheckConstraint("priority IN ('High','Medium','Low')", name="proposal_priority"),
        CheckConstraint("status IN ('Pending','Accepted','Rejected')", name="proposal_status"),
        CheckConstraint("origin IN ('AI','Manual')", name="proposal_origin"),
        CheckConstraint("source_start >= 0 AND source_end > source_start", name="proposal_source_range"),
    )


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
