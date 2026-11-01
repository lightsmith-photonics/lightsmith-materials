"""photonics metadata: tags, optical axis, state, thermo-optic data, dispersion models

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

JSON = sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), 'postgresql')

# 0001 categories -> 0002 categories
CATEGORY_MAP = {"dielectric": "glass_oxide", "glass": "glass_oxide", "mixture": "other", "organic": "other"}


def upgrade() -> None:
    with op.batch_alter_table('materials', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tags', JSON, nullable=False, server_default='[]'))
    with op.batch_alter_table('datasets', schema=None) as batch_op:
        batch_op.add_column(sa.Column('optical_axis', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('axis_group', sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column('state', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('thermo_optic', JSON, nullable=True))
        batch_op.create_index(batch_op.f('ix_datasets_axis_group'), ['axis_group'], unique=False)
    op.create_table('dispersion_models',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('dataset_id', sa.Integer(), nullable=False),
    sa.Column('dataset_version', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('method', sa.String(length=16), nullable=False),
    sa.Column('material', JSON, nullable=False),
    sa.Column('band_nm', JSON, nullable=False),
    sa.Column('errors', JSON, nullable=True),
    sa.Column('notes', JSON, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dataset_id', 'dataset_version', 'kind')
    )
    with op.batch_alter_table('dispersion_models', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_dispersion_models_dataset_id'), ['dataset_id'], unique=False)
    materials = sa.table('materials', sa.column('category', sa.String))
    for old, new in CATEGORY_MAP.items():
        op.execute(materials.update().where(materials.c.category == old).values(category=new))


def downgrade() -> None:
    with op.batch_alter_table('dispersion_models', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_dispersion_models_dataset_id'))
    op.drop_table('dispersion_models')
    with op.batch_alter_table('datasets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_datasets_axis_group'))
        batch_op.drop_column('thermo_optic')
        batch_op.drop_column('state')
        batch_op.drop_column('axis_group')
        batch_op.drop_column('optical_axis')
    with op.batch_alter_table('materials', schema=None) as batch_op:
        batch_op.drop_column('tags')
