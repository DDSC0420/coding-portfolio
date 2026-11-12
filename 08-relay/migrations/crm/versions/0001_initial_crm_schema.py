"""initial crm schema

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
    op.create_table('contacts',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('email', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('company', sa.Text(), nullable=False),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('revision', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('email = lower(email)', name=op.f('ck_contacts_email_lowercase')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_contacts')),
    sa.UniqueConstraint('email', name=op.f('uq_contacts_email'))
    )
    op.create_table('idempotency_keys',
    sa.Column('key', sa.Text(), nullable=False),
    sa.Column('request_hash', sa.Text(), nullable=False),
    sa.Column('response_status', sa.Integer(), nullable=True),
    sa.Column('response_body', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('(response_status IS NULL) = (completed_at IS NULL)', name=op.f('ck_idempotency_keys_response_iff_completed')),
    sa.PrimaryKeyConstraint('key', name=op.f('pk_idempotency_keys'))
    )
    op.create_table('mutations',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('idempotency_key', sa.Text(), nullable=False),
    sa.Column('contact_id', sa.Uuid(), nullable=False),
    sa.Column('operation', sa.Text(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('applied_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("operation IN ('created','updated')", name=op.f('ck_mutations_operation_valid')),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_mutations_contact_id_contacts')),
    sa.ForeignKeyConstraint(['idempotency_key'], ['idempotency_keys.key'], name=op.f('fk_mutations_idempotency_key_idempotency_keys')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_mutations')),
    sa.UniqueConstraint('idempotency_key', name=op.f('uq_mutations_idempotency_key'))
    )
    op.create_index(op.f('ix_mutations_contact_id'), 'mutations', ['contact_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_mutations_contact_id'), table_name='mutations')
    op.drop_table('mutations')
    op.drop_table('idempotency_keys')
    op.drop_table('contacts')
