"""Database schema.

A *material* has *datasets*; each dataset has an immutable history of
*dataset versions* that hold the numbers (lossless float64, wavelengths in µm
as in the source files) and point at the verbatim *source file*. Datasets cite
*references* (many-to-many) and carry one *license*. Community uploads arrive
as *submissions* and become datasets or new versions when a moderator approves
them. *Catalog entries* keep the full shelf/book/page tree of imported
databases, including shelves that only re-list datasets.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text,
                        UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")

CATEGORIES = ("semiconductor", "glass_oxide", "polymer", "2d", "metal", "liquid", "phase_change", "gas",
              "biological", "other")
SUBMISSION_STATES = ("pending", "approved", "rejected")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONType, list[Any]: JSONType}


class License(Base):
    __tablename__ = "licenses"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # SPDX identifier
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(500))
    requires_attribution: Mapped[bool] = mapped_column(Boolean, default=False)
    share_alike: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_for_contributions: Mapped[bool] = mapped_column(Boolean, default=False)


class Contributor(Base):
    __tablename__ = "contributors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(200), unique=True)  # JWT "sub" from the web platform
    name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(50), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True)
    slug: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(300))
    name_html: Mapped[str | None] = mapped_column(String(500))
    formula: Mapped[str | None] = mapped_column(String(200))
    aliases: Mapped[list[Any]] = mapped_column(default=list)
    category: Mapped[str] = mapped_column(String(32), index=True)
    tags: Mapped[list[Any]] = mapped_column(default=list)  # integrated-photonics, phase-change, anisotropic, ...
    group: Mapped[str | None] = mapped_column(String(200))  # e.g. catalog divider "Alcohols", "Optical glass"
    manufacturer: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    external_links: Mapped[list[Any]] = mapped_column(default=list)  # [{"url", "text"}] from the source's about.yml
    search_text: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32))  # "rii" | "community"
    source_key: Mapped[str | None] = mapped_column(String(500), unique=True)  # e.g. "rii:main/Au"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    datasets: Mapped[list[Dataset]] = relationship(back_populates="material", order_by="Dataset.position")


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True)
    slug: Mapped[str] = mapped_column(String(300), unique=True)  # "<material slug>/<dataset part>"
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), index=True)
    name: Mapped[str] = mapped_column(String(500))
    page: Mapped[str | None] = mapped_column(String(200))  # source page key, e.g. "Johnson"
    group: Mapped[str | None] = mapped_column(String(200))  # e.g. "Experimental data: thin film"
    position: Mapped[int] = mapped_column(Integer, default=0)
    data_type: Mapped[str] = mapped_column(String(100))  # "tabulated nk" | "tabulated n" | "formula 2" | ...
    has_k: Mapped[bool] = mapped_column(Boolean, default=False)
    wavelength_min_nm: Mapped[float] = mapped_column(Float)
    wavelength_max_nm: Mapped[float] = mapped_column(Float)
    n_points: Mapped[int | None] = mapped_column(Integer)
    temperature_k: Mapped[float | None] = mapped_column(Float)
    optical_axis: Mapped[str | None] = mapped_column(String(16))  # o | e | alpha | beta | gamma | x | y | z
    axis_group: Mapped[str | None] = mapped_column(String(300), index=True)  # same key = axes of one measurement
    state: Mapped[str | None] = mapped_column(String(32))  # amorphous | crystalline | liquid | ...
    thermo_optic: Mapped[list[Any] | None] = mapped_column(JSONType, nullable=True)
    conditions: Mapped[dict[str, Any]] = mapped_column(default=dict)
    measurement_method: Mapped[str | None] = mapped_column(String(300))
    sample_notes: Mapped[str | None] = mapped_column(Text)
    comments: Mapped[str | None] = mapped_column(Text)
    quality_flags: Mapped[list[Any]] = mapped_column(default=list)
    license_id: Mapped[str] = mapped_column(ForeignKey("licenses.id"))
    status: Mapped[str] = mapped_column(String(32), default="published")  # published | withdrawn
    source: Mapped[str] = mapped_column(String(32))  # "rii" | "submission"
    source_key: Mapped[str | None] = mapped_column(String(500), unique=True)  # e.g. "rii:main/Au/nk/Johnson.yml"
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    material: Mapped[Material] = relationship(back_populates="datasets")
    license: Mapped[License] = relationship()
    versions: Mapped[list[DatasetVersion]] = relationship(
        back_populates="dataset", order_by="DatasetVersion.version", cascade="all, delete-orphan")
    reference_links: Mapped[list[DatasetReference]] = relationship(
        back_populates="dataset", order_by="DatasetReference.position", cascade="all, delete-orphan")
    aliases: Mapped[list[DatasetAlias]] = relationship(back_populates="dataset", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_datasets_range", "wavelength_min_nm", "wavelength_max_nm"),)

    @property
    def references(self) -> list[Reference]:
        return [link.reference for link in self.reference_links]


class DatasetAlias(Base):
    """Alternative slugs that resolve to a dataset (old slugs, library names such as ``au/jc``)."""

    __tablename__ = "dataset_aliases"

    slug: Mapped[str] = mapped_column(String(300), primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)

    dataset: Mapped[Dataset] = relationship(back_populates="aliases")


class SourceFile(Base):
    """Verbatim original file (YAML from the source database, or an uploaded CSV/YAML)."""

    __tablename__ = "source_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    filename: Mapped[str] = mapped_column(String(300))
    path: Mapped[str | None] = mapped_column(String(500))  # path inside the source repository
    origin_url: Mapped[str | None] = mapped_column(String(1000))
    media_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DatasetVersion(Base):
    """One immutable revision of a dataset's numbers and metadata.

    ``blocks`` mirrors the source structure, e.g.
    ``[{"type": "tabulated nk", "wavelength_um": [...], "n": [...], "k": [...]},
    {"type": "formula 2", "coefficients": [...], "range_um": [lo, hi]}]``.
    Values are float64 as parsed from the source text."""

    __tablename__ = "dataset_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    blocks: Mapped[list[Any]] = mapped_column()
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)  # name, comments, conditions, properties, ...
    references_html: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    source_file_id: Mapped[int | None] = mapped_column(ForeignKey("source_files.id"))
    source_commit: Mapped[str | None] = mapped_column(String(64))
    change_note: Mapped[str | None] = mapped_column(Text)
    contributor_id: Mapped[int | None] = mapped_column(ForeignKey("contributors.id"))
    submission_id: Mapped[int | None] = mapped_column(ForeignKey("submissions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    dataset: Mapped[Dataset] = relationship(back_populates="versions")
    source_file: Mapped[SourceFile | None] = relationship()
    contributor: Mapped[Contributor | None] = relationship()

    __table_args__ = (UniqueConstraint("dataset_id", "version"),)


class Reference(Base):
    __tablename__ = "bib_references"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True)
    doi: Mapped[str | None] = mapped_column(String(300), unique=True)  # lower case, no "https://doi.org/"
    arxiv: Mapped[str | None] = mapped_column(String(100), index=True)
    url: Mapped[str | None] = mapped_column(String(1000))
    title: Mapped[str | None] = mapped_column(Text)
    authors: Mapped[list[Any]] = mapped_column(default=list)  # [{"given": "P. B.", "family": "Johnson"}]
    journal: Mapped[str | None] = mapped_column(String(500))
    year: Mapped[int | None] = mapped_column(Integer)
    volume: Mapped[str | None] = mapped_column(String(50))
    issue: Mapped[str | None] = mapped_column(String(50))
    pages: Mapped[str | None] = mapped_column(String(100))
    publisher: Mapped[str | None] = mapped_column(String(300))
    citation: Mapped[str] = mapped_column(Text)  # plain-text citation as given by the source
    citation_html: Mapped[str | None] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64), unique=True)
    metadata_source: Mapped[str] = mapped_column(String(32), default="source-text")  # | crossref | datacite | user
    doi_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    doi_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DatasetReference(Base):
    __tablename__ = "dataset_references"

    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), primary_key=True)
    reference_id: Mapped[int] = mapped_column(ForeignKey("bib_references.id"), primary_key=True, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    role: Mapped[str] = mapped_column(String(32), default="source")  # source | related | database

    dataset: Mapped[Dataset] = relationship(back_populates="reference_links")
    reference: Mapped[Reference] = relationship()


class DispersionModel(Base):
    """A dispersive model of a dataset version, in a library material format (e.g. drude_lorentz).

    ``method`` is ``exact`` (a lossless rewrite of a source formula, e.g. Sellmeier as undamped
    Lorentz poles) or ``fit`` (``lightsmith.materials_fit.fit_drude_lorentz`` over ``band_nm``)."""

    __tablename__ = "dispersion_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)
    dataset_version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    method: Mapped[str] = mapped_column(String(16))
    material: Mapped[dict[str, Any]] = mapped_column()
    band_nm: Mapped[list[Any]] = mapped_column()
    errors: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    notes: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("dataset_id", "dataset_version", "kind"),)


class CatalogEntry(Base):
    """One page of an imported database catalog (keeps shelves, books, dividers and order)."""

    __tablename__ = "catalog_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    shelf: Mapped[str] = mapped_column(String(100))
    shelf_name: Mapped[str | None] = mapped_column(String(300))
    section: Mapped[str | None] = mapped_column(String(300))  # top-level divider ("Research data", ...)
    book: Mapped[str] = mapped_column(String(200))
    book_name: Mapped[str | None] = mapped_column(String(500))
    book_group: Mapped[str | None] = mapped_column(String(300))  # divider above the book
    page: Mapped[str] = mapped_column(String(200))
    page_name: Mapped[str | None] = mapped_column(String(500))
    page_group: Mapped[str | None] = mapped_column(String(300))  # divider above the page
    listing: Mapped[int] = mapped_column(Integer, default=0)  # n-th listing of the same page in its book
    data_path: Mapped[str] = mapped_column(String(500))
    position: Mapped[int] = mapped_column(Integer)
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("datasets.id"), index=True)
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id"), index=True)

    __table_args__ = (UniqueConstraint("source", "shelf", "book", "page", "listing"),)


class ImportRun(Base):
    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    commit: Mapped[str | None] = mapped_column(String(64))
    archive_sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="running")
    stats: Mapped[dict[str, Any]] = mapped_column(default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True)
    contributor_id: Mapped[int] = mapped_column(ForeignKey("contributors.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    kind: Mapped[str] = mapped_column(String(32))  # new_dataset | new_version
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id"))
    proposed_material: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    target_dataset_id: Mapped[int | None] = mapped_column(ForeignKey("datasets.id"))
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)  # metadata as submitted
    blocks: Mapped[list[Any]] = mapped_column(default=list)  # parsed, normalised data
    validation: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"errors": [], "warnings": [], "stats": {}}
    license_id: Mapped[str] = mapped_column(ForeignKey("licenses.id"))
    reference_id: Mapped[int | None] = mapped_column(ForeignKey("bib_references.id"))
    source_file_id: Mapped[int] = mapped_column(ForeignKey("source_files.id"))
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("contributors.id"))
    moderation_notes: Mapped[str | None] = mapped_column(Text)
    result_dataset_id: Mapped[int | None] = mapped_column(ForeignKey("datasets.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    contributor: Mapped[Contributor] = relationship(foreign_keys=[contributor_id])
    reviewer: Mapped[Contributor | None] = relationship(foreign_keys=[reviewer_id])
    material: Mapped[Material | None] = relationship()
    target_dataset: Mapped[Dataset | None] = relationship(foreign_keys=[target_dataset_id])
    result_dataset: Mapped[Dataset | None] = relationship(foreign_keys=[result_dataset_id])
    reference: Mapped[Reference | None] = relationship()
    source_file: Mapped[SourceFile] = relationship()
    license: Mapped[License] = relationship()
    events: Mapped[list[SubmissionEvent]] = relationship(
        back_populates="submission", order_by="SubmissionEvent.id", cascade="all, delete-orphan")


class SubmissionEvent(Base):
    """Moderation history of a submission (submitted, approved, rejected, commented)."""

    __tablename__ = "submission_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("contributors.id"))
    action: Mapped[str] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    submission: Mapped[Submission] = relationship(back_populates="events")
    actor: Mapped[Contributor] = relationship()
