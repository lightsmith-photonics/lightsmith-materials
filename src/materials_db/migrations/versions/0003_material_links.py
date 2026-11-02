"""material external links (the LINKS of the source database's about.yml)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

JSON = sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    with op.batch_alter_table('materials', schema=None) as batch_op:
        batch_op.add_column(sa.Column('external_links', JSON, nullable=False, server_default='[]'))


def downgrade() -> None:
    with op.batch_alter_table('materials', schema=None) as batch_op:
        batch_op.drop_column('external_links')
