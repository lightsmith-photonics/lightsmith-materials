"""Response and request models (they define the published OpenAPI schema)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")
Num = float | None


class Out(BaseModel):
    model_config = ConfigDict(extra="allow")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int
    page: int = Field(description="1-based page number (page/per_page are aliases of offset/limit)")
    per_page: int


class Error(BaseModel):
    error: str
    message: str
    detail: Any = None


class MaterialRef(Out):
    id: str
    slug: str
    name: str
    formula: str | None
    category: str


class Category(Out):
    id: str
    name: str
    description: str
    count: int
    research_count: int = Field(description="Materials that are not supplier datasheets")
    supplier_count: int
    featured: list[MaterialRef] = Field(description="Materials to show first when browsing the category")


class License(Out):
    id: str
    name: str
    url: str
    requires_attribution: bool
    share_alike: bool


class ReferenceOut(Out):
    id: str
    doi: str | None
    arxiv: str | None
    url: str | None
    title: str | None
    authors: list[dict[str, Any]]
    journal: str | None
    year: int | None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    publisher: str | None = None
    citation: str = Field(description="Citation text as given by the source")
    formatted: str = Field(description="Citation built from DOI metadata when available")
    metadata_source: str
    role: str | None = Field(None, description="source | related | database (on dataset pages)")


class DatasetBrief(Out):
    id: str = Field(description="Stable public ID, e.g. ds_1a2b3c4d5e6f")
    slug: str = Field(description="Stable slug, e.g. au/johnson-christy-1972")
    name: str
    type: str = Field(description="tabulated nk | tabulated n | formula N | combinations joined with ' + '")
    range_nm: list[float] = Field(description="[min, max] vacuum wavelength in nm")
    has_k: bool
    year: int | None
    doi: str | None
    reference: str | None
    is_default: bool
    optical_axis: str | None = Field(None, description="Crystal axis for anisotropic data (o, e, alpha, ...)")
    state: str | None = Field(None, description="amorphous, crystalline, liquid, ... when known")


class DatasetSummary(DatasetBrief):
    material_id: str
    material: MaterialRef
    data_type: str
    n_points: int | None
    temperature_k: float | None
    group: str | None
    authors: str | None
    license: str
    comments: str | None
    status: str
    current_version: int
    source_url: str | None


class VersionOut(Out):
    version: int
    created_at: datetime
    change_note: str | None
    source_commit: str | None
    content_hash: str
    submission_id: str | None
    contributor: dict | None
    source_file: dict | None


class DatasetDetail(DatasetSummary):
    conditions: dict[str, Any]
    sample_notes: str | None
    measurement_method: str | None
    quality_flags: list[str]
    anisotropy: dict[str, Any] | None = Field(
        None, description="{kind: uniaxial|biaxial, axis, axes: {axis: dataset id}} for axis-resolved data")
    thermo_optic: list[dict[str, Any]] | None = Field(None, description="dn/dT data (1/K) or a Schott model")
    models: list[dict[str, Any]] = Field(description="Dispersive models available for this version")
    properties: Any = None
    references: list[ReferenceOut]
    references_html: str | None
    license_info: License
    source: dict[str, Any]
    versions: list[VersionOut]
    aliases: list[str]
    citation: str = Field(description="What to cite when using this dataset")
    links: dict[str, str]
    created_at: datetime
    updated_at: datetime


class MaterialSummary(Out):
    id: str = Field(description="Stable public ID, e.g. mat_1a2b3c4d5e6f")
    slug: str
    name: str
    name_html: str | None
    formula: str | None
    category: str
    tags: list[str] = Field(description="integrated-photonics, phase-change, anisotropic, photoresist, "
                                        "supplier-datasheet")
    anisotropic: bool
    group: str | None
    manufacturer: str | None
    aliases: list[str]
    description: str | None
    dataset_count: int
    range_nm: list[float] | None
    default_dataset_id: str | None
    datasets: list[DatasetBrief] = []


class MaterialDetail(MaterialSummary):
    datasets: list[DatasetSummary] = []
    source: dict[str, Any]
    catalog: list[dict[str, Any]]
    links: dict[str, str]
    external_links: list[dict[str, str]] = Field(
        [], description="Further reading on the material from the source database ({url, text})")
    created_at: datetime
    updated_at: datetime


class DataResponse(Out):
    """Arrays are named after the axis and the quantities; non-finite values are null."""

    dataset: dict[str, Any]
    quantity: str
    axis: str
    axis_unit: str
    wavelength_nm: list[Num]
    wavelength_um: list[Num] | None = None
    ev: list[Num] | None = None
    wavenumber: list[Num] | None = None
    n: list[Num] | None = None
    k: list[Num] | None = None
    eps1: list[Num] | None = None
    eps2: list[Num] | None = None
    alpha: list[Num] | None = None
    skin_depth: list[Num] | None = None
    units: dict[str, str]
    points: int
    range_nm: list[float]
    warnings: list[str]
    license: str
    citation: str


class CompareItem(Out):
    dataset: DatasetSummary
    data: dict[str, list[Num]]
    range_nm: list[float]
    warnings: list[str]


class CompareResponse(Out):
    quantity: str
    axis: str
    axis_unit: str
    range_mode: Literal["union", "intersection"]
    range_nm: list[float]
    units: dict[str, str]
    items: list[CompareItem]
    warnings: list[str]


class OmniscatterTabulated(BaseModel):
    """A library ``Tabulated`` material, ready to drop into a Simulation (``?model=drude_lorentz``
    returns the library's ``drude_lorentz`` kind instead)."""

    kind: Literal["tabulated"] = "tabulated"
    wavelength_nm: list[float]
    n_real: list[float]
    n_imag: list[float]
    source: str


class SubmissionInput(BaseModel):
    """JSON form of a submission (the multipart form takes the same fields in ``metadata``)."""

    model_config = ConfigDict(extra="forbid")

    material_id: str | None = Field(None, description="Existing material (id, slug or alias)")
    material_name: str | None = Field(None, description="Name of a new material")
    formula: str | None = None
    category: str | None = Field(None, description="For a new material: metal, semiconductor, dielectric, 2d, ...")
    aliases: list[str] | None = None
    dataset_name: str | None = None
    target_dataset_id: str | None = Field(None, description="Submit a new version of this dataset")
    reference: str | None = Field(None, description="Full citation (required unless doi or arxiv is given)")
    doi: str | None = None
    arxiv: str | None = None
    url: str | None = None
    license: str = Field(description="SPDX id, e.g. CC0-1.0 or CC-BY-4.0")
    comments: str | None = None
    temperature_k: float | None = None
    conditions: dict[str, Any] | None = None
    measurement_method: str | None = None
    sample_notes: str | None = None
    change_note: str | None = None
    optical_axis: str | None = Field(None, description="o, e, alpha, beta, gamma, x, y, z, a, b, c for anisotropic data")
    axis_partner_id: str | None = Field(None, description="Dataset with another axis of the same measurement")
    state: str | None = Field(None, description="amorphous, crystalline, polycrystalline, liquid, gas, solid")
    thermo_optic: list[dict[str, Any]] | None = Field(
        None, description="[{dn_dT (1/K), wavelength_nm?, temperature_k?, direction?}]")
    data: list[list[float]] = Field(description="Rows [wavelength_nm, n, k] or [wavelength_nm, n]")


class ReviewInput(BaseModel):
    decision: Literal["approve", "reject"]
    notes: str | None = None
    note: str | None = Field(None, description="Alias of notes")


class SubmissionOut(Out):
    id: str
    status: Literal["pending", "approved", "rejected"]
    kind: str
    material_id: str | None
    material: MaterialRef | None
    proposed_material: dict[str, Any] | None
    target_dataset_id: str | None
    material_name: str | None
    category: str | None
    dataset_name: str | None
    reference: str | None
    doi: str | None
    license: str
    comments: str | None
    metadata: dict[str, Any]
    validation: dict[str, Any]
    reference_info: ReferenceOut | None
    submitted_by: dict[str, Any]
    reviewer: dict[str, Any] | None
    review_note: str | None
    result_dataset_id: str | None
    result_dataset_slug: str | None
    file: dict[str, Any]
    data_summary: dict[str, Any]
    data: list[list[float | None]] | None = None
    events: list[dict[str, Any]]
    created_at: datetime
    reviewed_at: datetime | None
