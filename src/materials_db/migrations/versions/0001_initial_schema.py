"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-11-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('contributors',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('external_id', sa.String(length=200), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=True),
    sa.Column('role', sa.String(length=50), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('external_id')
    )
    op.create_table('import_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('commit', sa.String(length=64), nullable=True),
    sa.Column('archive_sha256', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('stats', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('licenses',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('url', sa.String(length=500), nullable=False),
    sa.Column('requires_attribution', sa.Boolean(), nullable=False),
    sa.Column('share_alike', sa.Boolean(), nullable=False),
    sa.Column('allowed_for_contributions', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('materials',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('public_id', sa.String(length=32), nullable=False),
    sa.Column('slug', sa.String(length=200), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('name_html', sa.String(length=500), nullable=True),
    sa.Column('formula', sa.String(length=200), nullable=True),
    sa.Column('aliases', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('category', sa.String(length=32), nullable=False),
    sa.Column('group', sa.String(length=200), nullable=True),
    sa.Column('manufacturer', sa.String(length=200), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('search_text', sa.Text(), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('source_key', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id'),
    sa.UniqueConstraint('slug'),
    sa.UniqueConstraint('source_key')
    )
    with op.batch_alter_table('materials', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_materials_category'), ['category'], unique=False)

    op.create_table('bib_references',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('public_id', sa.String(length=32), nullable=False),
    sa.Column('doi', sa.String(length=300), nullable=True),
    sa.Column('arxiv', sa.String(length=100), nullable=True),
    sa.Column('url', sa.String(length=1000), nullable=True),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('authors', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('journal', sa.String(length=500), nullable=True),
    sa.Column('year', sa.Integer(), nullable=True),
    sa.Column('volume', sa.String(length=50), nullable=True),
    sa.Column('issue', sa.String(length=50), nullable=True),
    sa.Column('pages', sa.String(length=100), nullable=True),
    sa.Column('publisher', sa.String(length=300), nullable=True),
    sa.Column('citation', sa.Text(), nullable=False),
    sa.Column('citation_html', sa.Text(), nullable=True),
    sa.Column('text_hash', sa.String(length=64), nullable=False),
    sa.Column('metadata_source', sa.String(length=32), nullable=False),
    sa.Column('doi_metadata', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('doi_checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('doi'),
    sa.UniqueConstraint('public_id'),
    sa.UniqueConstraint('text_hash')
    )
    with op.batch_alter_table('bib_references', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_bib_references_arxiv'), ['arxiv'], unique=False)

    op.create_table('source_files',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('filename', sa.String(length=300), nullable=False),
    sa.Column('path', sa.String(length=500), nullable=True),
    sa.Column('origin_url', sa.String(length=1000), nullable=True),
    sa.Column('media_type', sa.String(length=100), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('content', sa.LargeBinary(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('sha256')
    )
    op.create_table('datasets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('public_id', sa.String(length=32), nullable=False),
    sa.Column('slug', sa.String(length=300), nullable=False),
    sa.Column('material_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=500), nullable=False),
    sa.Column('page', sa.String(length=200), nullable=True),
    sa.Column('group', sa.String(length=200), nullable=True),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('data_type', sa.String(length=100), nullable=False),
    sa.Column('has_k', sa.Boolean(), nullable=False),
    sa.Column('wavelength_min_nm', sa.Float(), nullable=False),
    sa.Column('wavelength_max_nm', sa.Float(), nullable=False),
    sa.Column('n_points', sa.Integer(), nullable=True),
    sa.Column('temperature_k', sa.Float(), nullable=True),
    sa.Column('conditions', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('measurement_method', sa.String(length=300), nullable=True),
    sa.Column('sample_notes', sa.Text(), nullable=True),
    sa.Column('comments', sa.Text(), nullable=True),
    sa.Column('quality_flags', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('license_id', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('source_key', sa.String(length=500), nullable=True),
    sa.Column('current_version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['license_id'], ['licenses.id'], ),
    sa.ForeignKeyConstraint(['material_id'], ['materials.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id'),
    sa.UniqueConstraint('slug'),
    sa.UniqueConstraint('source_key')
    )
    with op.batch_alter_table('datasets', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_datasets_material_id'), ['material_id'], unique=False)
        batch_op.create_index('ix_datasets_range', ['wavelength_min_nm', 'wavelength_max_nm'], unique=False)

    op.create_table('catalog_entries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('shelf', sa.String(length=100), nullable=False),
    sa.Column('shelf_name', sa.String(length=300), nullable=True),
    sa.Column('section', sa.String(length=300), nullable=True),
    sa.Column('book', sa.String(length=200), nullable=False),
    sa.Column('book_name', sa.String(length=500), nullable=True),
    sa.Column('book_group', sa.String(length=300), nullable=True),
    sa.Column('page', sa.String(length=200), nullable=False),
    sa.Column('page_name', sa.String(length=500), nullable=True),
    sa.Column('page_group', sa.String(length=300), nullable=True),
    sa.Column('listing', sa.Integer(), nullable=False),
    sa.Column('data_path', sa.String(length=500), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('dataset_id', sa.Integer(), nullable=True),
    sa.Column('material_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ),
    sa.ForeignKeyConstraint(['material_id'], ['materials.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source', 'shelf', 'book', 'page', 'listing')
    )
    with op.batch_alter_table('catalog_entries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_catalog_entries_dataset_id'), ['dataset_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_catalog_entries_material_id'), ['material_id'], unique=False)

    op.create_table('dataset_aliases',
    sa.Column('slug', sa.String(length=300), nullable=False),
    sa.Column('dataset_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ),
    sa.PrimaryKeyConstraint('slug')
    )
    with op.batch_alter_table('dataset_aliases', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_dataset_aliases_dataset_id'), ['dataset_id'], unique=False)

    op.create_table('dataset_references',
    sa.Column('dataset_id', sa.Integer(), nullable=False),
    sa.Column('reference_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ),
    sa.ForeignKeyConstraint(['reference_id'], ['bib_references.id'], ),
    sa.PrimaryKeyConstraint('dataset_id', 'reference_id')
    )
    with op.batch_alter_table('dataset_references', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_dataset_references_reference_id'), ['reference_id'], unique=False)

    op.create_table('submissions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('public_id', sa.String(length=32), nullable=False),
    sa.Column('contributor_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('material_id', sa.Integer(), nullable=True),
    sa.Column('proposed_material', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('target_dataset_id', sa.Integer(), nullable=True),
    sa.Column('meta', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('blocks', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('validation', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('license_id', sa.String(length=64), nullable=False),
    sa.Column('reference_id', sa.Integer(), nullable=True),
    sa.Column('source_file_id', sa.Integer(), nullable=False),
    sa.Column('reviewer_id', sa.Integer(), nullable=True),
    sa.Column('moderation_notes', sa.Text(), nullable=True),
    sa.Column('result_dataset_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['contributor_id'], ['contributors.id'], ),
    sa.ForeignKeyConstraint(['license_id'], ['licenses.id'], ),
    sa.ForeignKeyConstraint(['material_id'], ['materials.id'], ),
    sa.ForeignKeyConstraint(['reference_id'], ['bib_references.id'], ),
    sa.ForeignKeyConstraint(['result_dataset_id'], ['datasets.id'], ),
    sa.ForeignKeyConstraint(['reviewer_id'], ['contributors.id'], ),
    sa.ForeignKeyConstraint(['source_file_id'], ['source_files.id'], ),
    sa.ForeignKeyConstraint(['target_dataset_id'], ['datasets.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id')
    )
    with op.batch_alter_table('submissions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_submissions_contributor_id'), ['contributor_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_submissions_status'), ['status'], unique=False)

    op.create_table('dataset_versions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('dataset_id', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('blocks', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('meta', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('references_html', sa.Text(), nullable=True),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('source_file_id', sa.Integer(), nullable=True),
    sa.Column('source_commit', sa.String(length=64), nullable=True),
    sa.Column('change_note', sa.Text(), nullable=True),
    sa.Column('contributor_id', sa.Integer(), nullable=True),
    sa.Column('submission_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['contributor_id'], ['contributors.id'], ),
    sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ),
    sa.ForeignKeyConstraint(['source_file_id'], ['source_files.id'], ),
    sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dataset_id', 'version')
    )
    with op.batch_alter_table('dataset_versions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_dataset_versions_dataset_id'), ['dataset_id'], unique=False)

    op.create_table('submission_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('submission_id', sa.Integer(), nullable=False),
    sa.Column('actor_id', sa.Integer(), nullable=False),
    sa.Column('action', sa.String(length=32), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['contributors.id'], ),
    sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('submission_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_submission_events_submission_id'), ['submission_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('submission_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_submission_events_submission_id'))

    op.drop_table('submission_events')
    with op.batch_alter_table('dataset_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_dataset_versions_dataset_id'))

    op.drop_table('dataset_versions')
    with op.batch_alter_table('submissions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_submissions_status'))
        batch_op.drop_index(batch_op.f('ix_submissions_contributor_id'))

    op.drop_table('submissions')
    with op.batch_alter_table('dataset_references', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_dataset_references_reference_id'))

    op.drop_table('dataset_references')
    with op.batch_alter_table('dataset_aliases', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_dataset_aliases_dataset_id'))

    op.drop_table('dataset_aliases')
    with op.batch_alter_table('catalog_entries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_catalog_entries_material_id'))
        batch_op.drop_index(batch_op.f('ix_catalog_entries_dataset_id'))

    op.drop_table('catalog_entries')
    with op.batch_alter_table('datasets', schema=None) as batch_op:
        batch_op.drop_index('ix_datasets_range')
        batch_op.drop_index(batch_op.f('ix_datasets_material_id'))

    op.drop_table('datasets')
    op.drop_table('source_files')
    with op.batch_alter_table('bib_references', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_bib_references_arxiv'))

    op.drop_table('bib_references')
    with op.batch_alter_table('materials', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_materials_category'))

    op.drop_table('materials')
    op.drop_table('licenses')
    op.drop_table('import_runs')
    op.drop_table('contributors')
