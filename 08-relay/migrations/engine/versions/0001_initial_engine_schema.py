"""initial engine schema

Revision ID: 0001
Revises: 
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('workers',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('hostname', sa.Text(), nullable=False),
    sa.Column('pid', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_heartbeat_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('stopped_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workers'))
    )
    op.create_index(op.f('ix_workers_name_started_at'), 'workers', ['name', 'started_at'], unique=False)
    op.create_table('workflow_runs',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('workflow_type', sa.Text(), nullable=False),
    sa.Column('workflow_version', sa.Integer(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('input', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('submission_key', sa.Text(), nullable=True),
    sa.Column('request_hash', sa.Text(), nullable=True),
    sa.Column('batch_id', sa.Text(), nullable=True),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('failure', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(completed_at IS NOT NULL) = (status IN ('succeeded','failed','outcome_unknown','rejected'))", name=op.f('ck_workflow_runs_completed_iff_terminal')),
    sa.CheckConstraint("status IN ('running','awaiting_approval','succeeded','failed','outcome_unknown','rejected')", name=op.f('ck_workflow_runs_status_valid')),
    sa.CheckConstraint('(submission_key IS NULL) = (request_hash IS NULL)', name=op.f('ck_workflow_runs_submission_key_has_hash')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workflow_runs')),
    sa.UniqueConstraint('submission_key', name=op.f('uq_workflow_runs_submission_key'))
    )
    op.create_index(op.f('ix_workflow_runs_batch_id'), 'workflow_runs', ['batch_id'], unique=False)
    op.create_index(op.f('ix_workflow_runs_created_at'), 'workflow_runs', ['created_at'], unique=False)
    op.create_index(op.f('ix_workflow_runs_status_created_at'), 'workflow_runs', ['status', 'created_at'], unique=False)
    op.create_table('step_runs',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('workflow_run_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('input', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('output', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('attempt_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('next_eligible_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('lease_owner', sa.Text(), nullable=True),
    sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('lease_generation', sa.BigInteger(), server_default=sa.text('0'), nullable=False),
    sa.Column('idempotency_key', sa.Text(), nullable=True),
    sa.Column('error_class', sa.Text(), nullable=True),
    sa.Column('error_summary', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'running') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)", name=op.f('ck_step_runs_running_iff_leased')),
    sa.CheckConstraint("error_class IS NULL OR error_class IN ('permanent','transient','ambiguous','lease_expired')", name=op.f('ck_step_runs_error_class_valid')),
    sa.CheckConstraint("status <> 'ready' OR next_eligible_at IS NOT NULL", name=op.f('ck_step_runs_ready_has_eligible_time')),
    sa.CheckConstraint("status IN ('pending','ready','running','awaiting_approval','succeeded','failed','outcome_unknown','rejected','cancelled')", name=op.f('ck_step_runs_status_valid')),
    sa.CheckConstraint('attempt_count >= 0 AND attempt_count <= max_attempts', name=op.f('ck_step_runs_attempts_in_budget')),
    sa.CheckConstraint('lease_generation >= 0', name=op.f('ck_step_runs_generation_non_negative')),
    sa.CheckConstraint('max_attempts >= 1', name=op.f('ck_step_runs_max_attempts_positive')),
    sa.ForeignKeyConstraint(['workflow_run_id'], ['workflow_runs.id'], name=op.f('fk_step_runs_workflow_run_id_workflow_runs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_step_runs')),
    sa.UniqueConstraint('idempotency_key', name=op.f('uq_step_runs_idempotency_key')),
    sa.UniqueConstraint('workflow_run_id', 'name', name=op.f('uq_step_runs_workflow_run_id_name')),
    sa.UniqueConstraint('workflow_run_id', 'position', name=op.f('uq_step_runs_workflow_run_id_position'))
    )
    op.create_index('ix_step_runs_claimable', 'step_runs', ['next_eligible_at', 'id'], unique=False, postgresql_where=sa.text("status = 'ready'"))
    op.create_index('ix_step_runs_leased', 'step_runs', ['lease_expires_at'], unique=False, postgresql_where=sa.text("status = 'running'"))
    op.create_index('uq_step_runs_one_active', 'step_runs', ['workflow_run_id'], unique=True, postgresql_where=sa.text("status IN ('ready','running','awaiting_approval')"))
    op.create_table('step_attempts',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('step_run_id', sa.Uuid(), nullable=False),
    sa.Column('attempt_number', sa.Integer(), nullable=False),
    sa.Column('worker_id', sa.Text(), nullable=False),
    sa.Column('lease_generation', sa.BigInteger(), nullable=False),
    sa.Column('outcome', sa.Text(), server_default=sa.text("'in_progress'"), nullable=False),
    sa.Column('error_class', sa.Text(), nullable=True),
    sa.Column('error_summary', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(outcome = 'in_progress') = (finished_at IS NULL)", name=op.f('ck_step_attempts_finished_iff_closed')),
    sa.CheckConstraint("outcome IN ('in_progress','succeeded','failed','lease_expired','paused_for_approval')", name=op.f('ck_step_attempts_outcome_valid')),
    sa.ForeignKeyConstraint(['step_run_id'], ['step_runs.id'], name=op.f('fk_step_attempts_step_run_id_step_runs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_step_attempts')),
    sa.UniqueConstraint('step_run_id', 'attempt_number', name=op.f('uq_step_attempts_step_run_id_attempt_number')),
    sa.UniqueConstraint('step_run_id', 'lease_generation', name=op.f('uq_step_attempts_step_run_id_lease_generation'))
    )
    op.create_table('workflow_events',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('workflow_run_id', sa.Uuid(), nullable=False),
    sa.Column('step_run_id', sa.Uuid(), nullable=True),
    sa.Column('type', sa.Text(), nullable=False),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['step_run_id'], ['step_runs.id'], name=op.f('fk_workflow_events_step_run_id_step_runs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workflow_run_id'], ['workflow_runs.id'], name=op.f('fk_workflow_events_workflow_run_id_workflow_runs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workflow_events'))
    )
    op.create_index(op.f('ix_workflow_events_type_id'), 'workflow_events', ['type', 'id'], unique=False)
    op.create_index(op.f('ix_workflow_events_workflow_run_id_id'), 'workflow_events', ['workflow_run_id', 'id'], unique=False)

    # --- State machine enforcement (docs/architecture.md §2, relay/engine/states.py) ---
    # These triggers make an illegal transition fail inside PostgreSQL, even if
    # application code has a bug. They also enforce immutability of the pinned
    # workflow version (I9) and monotonic lease generations (I3), and maintain
    # updated_at. tests/integration/test_state_triggers.py checks they agree
    # with relay/engine/states.py.
    op.execute(STEP_TRIGGER)
    op.execute(WORKFLOW_TRIGGER)


STEP_TRIGGER = """
CREATE FUNCTION relay_guard_step_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
           (OLD.status = 'pending'           AND NEW.status IN ('ready', 'cancelled'))
        OR (OLD.status = 'ready'             AND NEW.status IN ('running'))
        OR (OLD.status = 'running'           AND NEW.status IN ('succeeded', 'ready', 'failed',
                                                               'outcome_unknown', 'awaiting_approval'))
        OR (OLD.status = 'awaiting_approval' AND NEW.status IN ('succeeded', 'rejected'))
    ) THEN
        RAISE EXCEPTION 'illegal step transition % -> % (step %)', OLD.status, NEW.status, OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.lease_generation < OLD.lease_generation THEN
        RAISE EXCEPTION 'lease_generation must not decrease (step %)', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.workflow_run_id <> OLD.workflow_run_id OR NEW.name <> OLD.name
       OR NEW.position <> OLD.position THEN
        RAISE EXCEPTION 'step identity is immutable (step %)', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END
$$;
CREATE TRIGGER step_runs_guard BEFORE UPDATE ON step_runs
    FOR EACH ROW EXECUTE FUNCTION relay_guard_step_update();
"""

WORKFLOW_TRIGGER = """
CREATE FUNCTION relay_guard_workflow_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
           (OLD.status = 'running'           AND NEW.status IN ('awaiting_approval', 'succeeded',
                                                               'failed', 'outcome_unknown'))
        OR (OLD.status = 'awaiting_approval' AND NEW.status IN ('running', 'rejected'))
    ) THEN
        RAISE EXCEPTION 'illegal workflow transition % -> % (run %)', OLD.status, NEW.status, OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.workflow_type <> OLD.workflow_type OR NEW.workflow_version <> OLD.workflow_version
       OR NEW.input <> OLD.input THEN
        RAISE EXCEPTION 'workflow type, version and input are immutable (run %)', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END
$$;
CREATE TRIGGER workflow_runs_guard BEFORE UPDATE ON workflow_runs
    FOR EACH ROW EXECUTE FUNCTION relay_guard_workflow_update();
"""


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS workflow_runs_guard ON workflow_runs")
    op.execute("DROP FUNCTION IF EXISTS relay_guard_workflow_update()")
    op.execute("DROP TRIGGER IF EXISTS step_runs_guard ON step_runs")
    op.execute("DROP FUNCTION IF EXISTS relay_guard_step_update()")
    op.drop_index(op.f('ix_workflow_events_workflow_run_id_id'), table_name='workflow_events')
    op.drop_index(op.f('ix_workflow_events_type_id'), table_name='workflow_events')
    op.drop_table('workflow_events')
    op.drop_table('step_attempts')
    op.drop_index('uq_step_runs_one_active', table_name='step_runs', postgresql_where=sa.text("status IN ('ready','running','awaiting_approval')"))
    op.drop_index('ix_step_runs_leased', table_name='step_runs', postgresql_where=sa.text("status = 'running'"))
    op.drop_index('ix_step_runs_claimable', table_name='step_runs', postgresql_where=sa.text("status = 'ready'"))
    op.drop_table('step_runs')
    op.drop_index(op.f('ix_workflow_runs_status_created_at'), table_name='workflow_runs')
    op.drop_index(op.f('ix_workflow_runs_created_at'), table_name='workflow_runs')
    op.drop_index(op.f('ix_workflow_runs_batch_id'), table_name='workflow_runs')
    op.drop_table('workflow_runs')
    op.drop_index(op.f('ix_workers_name_started_at'), table_name='workers')
    op.drop_table('workers')
