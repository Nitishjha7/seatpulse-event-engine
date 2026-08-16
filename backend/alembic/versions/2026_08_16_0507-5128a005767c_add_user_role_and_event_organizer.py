"""add user role and event organizer

Revision ID: 5128a005767c
Revises: 39470c52944a
Create Date: 2026-08-16 05:07:56.660894
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '5128a005767c'
down_revision: Union[str, None] = '39470c52944a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('events', sa.Column('organizer_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_events_organizer_id'), 'events', ['organizer_id'], unique=False)
    # Constraint ka naam khud diya hai — autogenerate `None` chhod deta hai,
    # aur bina naam ke downgrade me use drop nahi kar sakte.
    op.create_foreign_key(
        'fk_events_organizer_id_users', 'events', 'users',
        ['organizer_id'], ['id'], ondelete='SET NULL',
    )

    # ⚠️ server_default zaroori hai — table me pehle se 500 users hain aur
    # NOT NULL column bina default ke add karne par Postgres poochta hai
    # "purani rows me kya daalun?" -> error.
    # Column ban jaane ke baad default hata dete hain: value ab application
    # (SQLAlchemy model) set karta hai.
    op.add_column(
        'users',
        sa.Column('role', sa.String(length=16), nullable=False, server_default='attendee'),
    )
    op.alter_column('users', 'role', server_default=None)

    op.create_index(op.f('ix_users_role'), 'users', ['role'], unique=False)
    op.create_check_constraint(
        'ck_user_role', 'users', "role IN ('attendee', 'organizer', 'admin')"
    )


def downgrade() -> None:
    op.drop_constraint('ck_user_role', 'users', type_='check')
    op.drop_index(op.f('ix_users_role'), table_name='users')
    op.drop_column('users', 'role')
    op.drop_constraint('fk_events_organizer_id_users', 'events', type_='foreignkey')
    op.drop_index(op.f('ix_events_organizer_id'), table_name='events')
    op.drop_column('events', 'organizer_id')
