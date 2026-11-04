"""Immutable meeting sources and reviewed task proposals.

Revision ID: 0002_meetings
Revises: 0001_phase1
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_meetings"
down_revision = "0001_phase1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("meetings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("meeting_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("source_type", sa.String(12), nullable=False),
        sa.Column("source_filename", sa.String(200), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("extraction_status", sa.String(12), nullable=False),
        sa.Column("extraction_attempt", sa.Integer(), nullable=False),
        sa.Column("extraction_started_at", sa.Integer(), nullable=True),
        sa.UniqueConstraint("project_id", "request_id", name="uq_meeting_request"),
        sa.CheckConstraint("source_type IN ('notes','transcript')", name="meeting_source_type"),
        sa.CheckConstraint("extraction_status IN ('Draft','Extracting','Ready','Failed')", name="meeting_extraction_status"),
    )
    op.create_index("ix_meetings_project_id", "meetings", ["project_id"])
    op.create_index("ix_meetings_project_created", "meetings", ["project_id", "created_at", "id"])
    op.create_table("task_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("meeting_id", sa.String(36), sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("due_date", sa.String(100), nullable=False),
        sa.Column("owner", sa.String(120), nullable=False),
        sa.Column("assignee_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("priority", sa.String(10), nullable=False),
        sa.Column("source_quote", sa.Text(), nullable=False),
        sa.Column("source_start", sa.Integer(), nullable=False),
        sa.Column("source_end", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("reviewed_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", sa.Integer(), nullable=True),
        sa.Column("decision_hash", sa.String(64), nullable=True),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("meeting_id", "request_id", name="uq_proposal_request"),
        sa.CheckConstraint("priority IN ('High','Medium','Low')", name="proposal_priority"),
        sa.CheckConstraint("status IN ('Pending','Accepted','Rejected')", name="proposal_status"),
        sa.CheckConstraint("origin IN ('AI','Manual')", name="proposal_origin"),
        sa.CheckConstraint("source_start >= 0 AND source_end > source_start", name="proposal_source_range"),
    )
    op.create_index("ix_task_proposals_meeting_id", "task_proposals", ["meeting_id"])
    with op.batch_alter_table("project_tasks") as batch:
        batch.add_column(sa.Column("source_proposal_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_task_source_proposal", "task_proposals", ["source_proposal_id"], ["id"], ondelete="RESTRICT")
        batch.create_unique_constraint("uq_task_source_proposal", ["source_proposal_id"])


def downgrade():
    with op.batch_alter_table("project_tasks") as batch:
        batch.drop_constraint("uq_task_source_proposal", type_="unique")
        batch.drop_constraint("fk_task_source_proposal", type_="foreignkey")
        batch.drop_column("source_proposal_id")
    op.drop_index("ix_task_proposals_meeting_id", table_name="task_proposals")
    op.drop_table("task_proposals")
    op.drop_index("ix_meetings_project_created", table_name="meetings")
    op.drop_index("ix_meetings_project_id", table_name="meetings")
    op.drop_table("meetings")
