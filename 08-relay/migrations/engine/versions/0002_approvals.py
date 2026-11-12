"""approvals: one human decision per paused step (Stage 3)

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("step_run_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("proposed_output", postgresql.JSONB(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.Text(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('pending','approved','rejected')", name=op.f("ck_approvals_status_valid")),
        sa.CheckConstraint("(status = 'pending') = (decided_at IS NULL)", name=op.f("ck_approvals_decided_iff_not_pending")),
        sa.CheckConstraint("(status = 'pending') = (decided_by IS NULL)", name=op.f("ck_approvals_decider_iff_decided")),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], name=op.f("fk_approvals_workflow_run_id_workflow_runs"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["step_run_id"], ["step_runs.id"], name=op.f("fk_approvals_step_run_id_step_runs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approvals")),
        # A step pauses at most once: it leaves awaiting_approval only to a terminal state.
        sa.UniqueConstraint("step_run_id", name=op.f("uq_approvals_step_run_id")),
    )
    op.create_index("ix_approvals_pending", "approvals", ["requested_at"], postgresql_where=sa.text("status = 'pending'"))
    op.create_index(op.f("ix_approvals_workflow_run_id"), "approvals", ["workflow_run_id"])
    # A decision is final: pending -> approved | rejected, never back, never changed.
    op.execute(APPROVAL_TRIGGER)


APPROVAL_TRIGGER = """
CREATE FUNCTION relay_guard_approval_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status <> 'pending' THEN
        RAISE EXCEPTION 'approval % is already % and cannot change', OLD.id, OLD.status
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.workflow_run_id <> OLD.workflow_run_id OR NEW.step_run_id <> OLD.step_run_id
       OR NEW.reason <> OLD.reason OR NEW.proposed_output <> OLD.proposed_output THEN
        RAISE EXCEPTION 'approval request is immutable (approval %)', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER approvals_guard BEFORE UPDATE ON approvals
    FOR EACH ROW EXECUTE FUNCTION relay_guard_approval_update();
"""


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS approvals_guard ON approvals")
    op.execute("DROP FUNCTION IF EXISTS relay_guard_approval_update()")
    op.drop_index(op.f("ix_approvals_workflow_run_id"), table_name="approvals")
    op.drop_index("ix_approvals_pending", table_name="approvals", postgresql_where=sa.text("status = 'pending'"))
    op.drop_table("approvals")
