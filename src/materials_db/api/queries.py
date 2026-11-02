"""Lookups and JSON serialisation shared by the routes."""

from __future__ import annotations

import json
from collections import OrderedDict
from functools import lru_cache
from importlib import resources

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, object_session, selectinload

from ..config import RII_CITATION, RII_RAW_URL
from ..models import (CatalogEntry, Dataset, DatasetAlias, DatasetReference, DatasetVersion, ImportRun, License,
                      Material, Reference, Submission)
from ..dispersion import available as available_models, fit_available
from ..naming import anisotropy_kind
from ..optics import Dispersion
from ..references import format_citation

# id -> (name, description, featured slugs shown first when browsing)
CATEGORY_INFO = {
    "semiconductor": ("Semiconductors and photonic platforms",
                      "Si, Ge, III-V and II-VI compounds, and the platform crystals and films of integrated photonics "
                      "(SiN, LiNbO3/TFLN, LiTaO3, AlN), including anisotropic o/e data.",
                      ["si", "si3n4", "linbo3", "inp", "gaas", "ge", "aln", "litao3", "gan", "sic"]),
    "glass_oxide": ("Glasses, oxides and dielectrics",
                    "Optical glasses (research data and supplier catalogs), oxides, fluorides and other dielectric "
                    "crystals and coating materials.",
                    ["sio2", "al2o3", "tio2", "ta2o5", "hfo2", "mgf2", "caf2", "schott-n-bk7", "zno", "nb2o5"]),
    "polymer": ("Polymers and photoresists", "Polymers, photoresists (SU-8, PMMA resists, IP-Dip), adhesives and "
                "organic photovoltaic blends.", ["poly-methyl-methacrylate", "polystyrene", "polycarbonate",
                                                 "microchem-su8-2000", "nanoscribe-ip-dip", "polydimethylsiloxane"]),
    "2d": ("2D materials", "Graphene's relatives: transition-metal dichalcogenides, MXenes and other layered "
           "materials.", ["mos2", "ws2", "wse2", "mose2", "mote2", "ti3c2", "bn"]),
    "metal": ("Metals and conductors", "Elemental metals, alloys, intermetallics and metallic nitrides and "
              "carbides used in plasmonics.", ["au", "ag", "al", "cu", "pt", "ti", "tin", "cr", "w", "ni"]),
    "liquid": ("Liquids", "Water, solvents, immersion oils, liquid crystals and index-matching liquids.",
               ["h2o", "ethanol", "methanol", "acetone", "glycerol", "e7"]),
    "phase_change": ("Phase-change materials", "Materials switched between states, e.g. GST and Sb2Se3 "
                     "(amorphous and crystalline datasets) and VO2 (insulating and metallic).",
                     ["ge2sb2te5", "sb2se3", "sb2s3", "vo2", "gete"]),
    "gas": ("Gases", "Air, noble gases and molecular gases.", ["air", "n2", "ar", "co2", "he"]),
    "biological": ("Biological materials", "Tissue, blood, DNA, silk and buffer solutions.",
                   ["blood", "dna", "pbs"]),
    "other": ("Other", "Carbon allotropes, minerals, soots, small organic solids and metamaterials.",
              ["c", "montmorillonite"]),
}
CATEGORY_NAMES = {k: v[0] for k, v in CATEGORY_INFO.items()}


@lru_cache(maxsize=1)
def library_defaults() -> frozenset[str]:
    """Source keys of the datasets the simulation library uses as material defaults."""
    text = resources.files("materials_db").joinpath("data", "library_names.json").read_text(encoding="utf-8")
    return frozenset(f"rii:{path}" for path, d in json.loads(text)["datasets"].items() if d["default"])


# --- resolution -----------------------------------------------------------------------

def get_material(session: Session, ident: str) -> Material | None:
    ident = ident.strip()
    m = session.scalar(select(Material).where(or_(Material.public_id == ident, Material.slug == ident.lower())))
    if m is None and ident:
        # exact alias or formula, case-insensitive (e.g. "gold", "Au")
        low = ident.lower()
        for cand in session.scalars(select(Material).where(Material.search_text.like(f"%|{low}|%")).limit(200)):
            if low in (a.lower() for a in [*(cand.aliases or []), cand.formula or "", cand.name]):
                return cand
    return m


def get_dataset(session: Session, ident: str) -> Dataset | None:
    ident = ident.strip().strip("/")
    ds = session.scalar(select(Dataset).where(or_(Dataset.public_id == ident, Dataset.slug == ident.lower())))
    if ds is None:
        alias = session.get(DatasetAlias, ident.lower())
        ds = alias.dataset if alias else None
    return ds


def get_version(ds: Dataset, version: int | None) -> DatasetVersion | None:
    want = version or ds.current_version
    return next((v for v in ds.versions if v.version == want), None)


_DISPERSIONS: OrderedDict[int, Dispersion] = OrderedDict()


def dispersion(version: DatasetVersion) -> Dispersion:
    """Dispersion of an (immutable) dataset version, cached."""
    d = _DISPERSIONS.get(version.id)
    if d is None:
        d = Dispersion(version.blocks)
        _DISPERSIONS[version.id] = d
        if len(_DISPERSIONS) > 512:
            _DISPERSIONS.popitem(last=False)
    else:
        _DISPERSIONS.move_to_end(version.id)
    return d


# --- serialisation --------------------------------------------------------------------

def material_ref(m: Material) -> dict:
    return {"id": m.public_id, "slug": m.slug, "name": m.name, "formula": m.formula, "category": m.category}


def _authors_text(ref: Reference | None) -> str | None:
    if ref is None or not ref.authors:
        return None
    names = [" ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("literal", "") for a in ref.authors]
    return ", ".join(n for n in names if n) or None


def primary_reference(ds: Dataset) -> Reference | None:
    for link in ds.reference_links:
        if link.role == "source":
            return link.reference
    return None


def default_dataset(m: Material) -> Dataset | None:
    live = [d for d in m.datasets if d.status == "published"]
    lib = library_defaults()
    return next((d for d in live if d.source_key in lib), live[0] if live else None)


def dataset_brief(ds: Dataset, default_id: int | None = None) -> dict:
    if default_id is None:
        default = default_dataset(ds.material)
        default_id = default.id if default else None
    ref = primary_reference(ds)
    return {
        "id": ds.public_id, "slug": ds.slug, "name": ds.name, "type": ds.data_type,
        "range_nm": [ds.wavelength_min_nm, ds.wavelength_max_nm], "has_k": ds.has_k,
        "year": ref.year if ref else None, "doi": ref.doi if ref else None,
        "reference": format_citation(ref) if ref else None, "is_default": ds.id == default_id,
        "optical_axis": ds.optical_axis, "state": ds.state,
    }


def dataset_summary(ds: Dataset, default_id: int | None = None) -> dict:
    ref = primary_reference(ds)
    src = (ds.versions[-1].meta or {}).get("source") if ds.versions else None
    return {
        **dataset_brief(ds, default_id),
        "material_id": ds.material.public_id, "material": material_ref(ds.material),
        "data_type": ds.data_type, "n_points": ds.n_points, "temperature_k": ds.temperature_k,
        "group": ds.group, "authors": _authors_text(ref), "license": ds.license_id, "comments": ds.comments,
        "status": ds.status, "current_version": ds.current_version,
        "source_url": (src or {}).get("url"),
    }


def reference_out(ref: Reference, role: str | None = None) -> dict:
    return {
        "id": ref.public_id, "doi": ref.doi, "arxiv": ref.arxiv, "url": ref.url, "title": ref.title,
        "authors": ref.authors or [], "journal": ref.journal, "year": ref.year, "volume": ref.volume,
        "issue": ref.issue, "pages": ref.pages, "publisher": ref.publisher, "citation": ref.citation,
        "formatted": format_citation(ref), "metadata_source": ref.metadata_source, "role": role,
    }


def license_out(lic: License) -> dict:
    return {"id": lic.id, "name": lic.name, "url": lic.url, "requires_attribution": lic.requires_attribution,
            "share_alike": lic.share_alike}


def version_out(v: DatasetVersion) -> dict:
    sf = v.source_file
    return {
        "version": v.version, "created_at": v.created_at, "change_note": v.change_note,
        "source_commit": v.source_commit, "content_hash": v.content_hash,
        "submission_id": v.meta.get("submission") if v.meta else None,
        "contributor": {"name": v.contributor.name} if v.contributor else None,
        "source_file": {"sha256": sf.sha256, "filename": sf.filename, "size": sf.size, "media_type": sf.media_type,
                        "path": sf.path, "origin_url": sf.origin_url} if sf else None,
    }


def citation_text(ds: Dataset) -> str:
    """What to cite when using the dataset: its source(s), then the compiling database."""
    parts = [format_citation(link.reference) for link in ds.reference_links if link.role == "source"]
    if ds.source == "rii":
        parts.append(f"Data from the refractiveindex.info database ({RII_CITATION})")
    return "; ".join(parts) or "No primary reference recorded"


def anisotropy(session: Session, ds: Dataset) -> dict | None:
    """The other axes of an anisotropic measurement (datasets sharing ``axis_group``)."""
    if not ds.optical_axis or not ds.axis_group:
        return None
    rows = session.execute(select(Dataset.optical_axis, Dataset.public_id, Dataset.slug)
                           .where(Dataset.axis_group == ds.axis_group, Dataset.status == "published")
                           .order_by(Dataset.position)).all()
    axes = {ax: {"id": pid, "slug": slug} for ax, pid, slug in rows if ax}
    return {"kind": anisotropy_kind(set(axes)), "axis": ds.optical_axis, "axes": axes,
            "complete": set(axes) in ({"o", "e"}, {"alpha", "beta", "gamma"}, {"x", "y", "z"}, {"a", "b", "c"})}


def dataset_detail(ds: Dataset, base_url: str, brand: str = "Lightsmith Materials") -> dict:
    session = object_session(ds)
    default = default_dataset(ds.material)
    cur = get_version(ds, None)
    src = dict((cur.meta or {}).get("source") or {}) if cur else {}
    if src and src.get("commit"):
        src["raw_url"] = RII_RAW_URL.format(commit=src["commit"], path=src["path"].removeprefix("database/"))
    api = f"{base_url}/v1/datasets/{ds.public_id}"
    return {
        **dataset_summary(ds, default.id if default else None),
        "conditions": ds.conditions or {}, "sample_notes": ds.sample_notes,
        "measurement_method": ds.measurement_method, "quality_flags": ds.quality_flags or [],
        "anisotropy": anisotropy(session, ds), "thermo_optic": ds.thermo_optic,
        "models": [{**m, "url": f"{api}/model"} for m in available_models(session, ds, cur)] if cur else [],
        "model_fit_available": fit_available(),
        "properties": (cur.meta or {}).get("properties") if cur else None,
        "references": [reference_out(link.reference, link.role) for link in ds.reference_links],
        "references_html": cur.references_html if cur else None,
        "license_info": license_out(ds.license),
        "source": src or {"database": f"{brand} community submission"},
        "versions": [version_out(v) for v in ds.versions],
        "aliases": [a.slug for a in ds.aliases],
        "citation": citation_text(ds),
        "links": {"self": api, "data": f"{api}/data", "lightsmith": f"{api}/lightsmith", "model": f"{api}/model",
                  "source_file": f"{api}/source", "versions": f"{api}/versions",
                  "material": f"{base_url}/v1/materials/{ds.material.public_id}"},
        "created_at": ds.created_at, "updated_at": ds.updated_at,
    }


def _short(text: str | None, limit: int = 280) -> str | None:
    if not text or len(text) <= limit:
        return text
    cut = text[:limit]
    end = cut.rfind(". ")
    return (cut[: end + 1] if end > 80 else cut.rsplit(" ", 1)[0] + "…")


def material_summary(m: Material, detail: bool = False) -> dict:
    """List form: shortened description, brief datasets. ``detail=True``: full description, no datasets."""
    live = [d for d in m.datasets if d.status == "published"]
    default = default_dataset(m)
    did = default.id if default else None
    out = {
        "id": m.public_id, "slug": m.slug, "name": m.name, "name_html": m.name_html, "formula": m.formula,
        "category": m.category, "tags": m.tags or [], "anisotropic": "anisotropic" in (m.tags or []),
        "group": m.group, "manufacturer": m.manufacturer, "aliases": m.aliases or [],
        "description": m.description if detail else _short(m.description),
        "dataset_count": len(live),
        "range_nm": [min(d.wavelength_min_nm for d in live), max(d.wavelength_max_nm for d in live)] if live else None,
        "default_dataset_id": default.public_id if default else None,
    }
    if not detail:
        out["datasets"] = [dataset_brief(d, did) for d in live]
    return out


def material_detail(session: Session, m: Material, base_url: str, brand: str = "Lightsmith Materials") -> dict:
    default = default_dataset(m)
    did = default.id if default else None
    out = material_summary(m, detail=True)
    out["datasets"] = [dataset_summary(d, did) for d in m.datasets if d.status == "published"]
    out["source"] = ({"database": "refractiveindex.info", "key": m.source_key.removeprefix("rii:")}
                     if m.source == "rii" else {"database": f"{brand} community"})
    out["catalog"] = [
        {"shelf": c.shelf, "book": c.book, "book_name": c.book_name, "page": c.page, "page_name": c.page_name}
        for c in session.scalars(select(CatalogEntry).where(CatalogEntry.material_id == m.id)
                                 .order_by(CatalogEntry.position))]
    out["links"] = {"self": f"{base_url}/v1/materials/{m.public_id}"}
    out["external_links"] = m.external_links or []
    out["created_at"], out["updated_at"] = m.created_at, m.updated_at
    return out


def dataset_loader():
    return (selectinload(Dataset.reference_links).selectinload(DatasetReference.reference),
            selectinload(Dataset.versions), selectinload(Dataset.material))


def material_loader():
    return selectinload(Material.datasets).options(
        selectinload(Dataset.reference_links).selectinload(DatasetReference.reference),
        selectinload(Dataset.versions))


def database_stats(session: Session) -> dict:
    run = session.scalar(select(ImportRun).where(ImportRun.status == "ok").order_by(ImportRun.id.desc()))
    return {
        "materials": session.scalar(select(func.count()).select_from(Material)),
        "datasets": session.scalar(select(func.count()).where(Dataset.status == "published")),
        "datasets_withdrawn": session.scalar(select(func.count()).where(Dataset.status == "withdrawn")),
        "dataset_versions": session.scalar(select(func.count()).select_from(DatasetVersion)),
        "references": session.scalar(select(func.count()).select_from(Reference)),
        "references_with_doi": session.scalar(select(func.count()).where(Reference.doi.is_not(None))),
        "references_with_doi_metadata": session.scalar(
            select(func.count()).where(Reference.metadata_source.in_(("crossref", "datacite")))),
        "submissions": {s: n for s, n in session.execute(
            select(Submission.status, func.count()).group_by(Submission.status))},
        "categories": {c: n for c, n in session.execute(
            select(Material.category, func.count()).group_by(Material.category).order_by(Material.category))},
        "source_snapshot": {"database": "refractiveindex.info", "commit": run.commit,
                            "imported_at": run.finished_at} if run else None,
    }
