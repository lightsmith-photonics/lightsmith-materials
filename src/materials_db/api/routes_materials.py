"""Materials, categories, catalog, references, licenses, stats."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, case, func, or_, select
from sqlalchemy.orm import Session

from ..models import CATEGORIES, CatalogEntry, Dataset, DatasetReference, License, Material, Reference
from ..rii import normalize_doi
from . import queries as Q
from . import schemas as S
from .deps import ApiError, Paging, base_url, brand, paging, session

router = APIRouter(prefix="/v1", tags=["materials"])


@router.get("/materials", response_model=S.Page[S.MaterialSummary],
            summary="Search materials")
def list_materials(q: str | None = Query(None, description="Name, formula, alias or slug (substring match)"),
                   category: str | None = Query(None, description="One category or a comma-separated list"),
                   tag: str | None = Query(None, description="integrated-photonics, phase-change, anisotropic, "
                                                             "photoresist or supplier-datasheet"),
                   supplier: bool | None = Query(None, description="false: research data only; true: supplier "
                                                                   "datasheets only"),
                   p: Paging = Depends(paging), s: Session = Depends(session)):
    stmt = select(Material)
    if tag:
        stmt = stmt.where(func.lower(func.cast(Material.tags, String)).like(f'%"{tag.strip().lower()}"%'))
    if supplier is not None:
        stmt = stmt.where(Material.manufacturer.is_not(None) if supplier else Material.manufacturer.is_(None))
    if category:
        cats = [c.strip().lower() for c in category.split(",") if c.strip()]
        bad = [c for c in cats if c not in CATEGORIES]
        if bad:
            raise ApiError(422, "invalid_category", f"unknown category {bad[0]!r}", {"categories": CATEGORIES})
        stmt = stmt.where(Material.category.in_(cats))
    order = [Material.name]
    if q and q.strip():
        ql = q.strip().lower()
        stmt = stmt.where(Material.search_text.like(f"%{ql}%"))
        rank = case((Material.slug == ql, 0), (func.lower(Material.formula) == ql, 1),
                    (Material.search_text.like(f"%|{ql}|%"), 2),  # a whole name or alias, e.g. "silicon"
                    (func.lower(Material.name).like(f"{ql}%"), 3), (Material.search_text.like(f"%|{ql}%"), 4),
                    else_=5)
        # supplier glass catalogs are numerous; list research materials first
        order = [rank, case((Material.manufacturer.is_(None), 0), else_=1), Material.name]
    total = s.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = s.scalars(stmt.options(Q.material_loader()).order_by(*order).limit(p.limit).offset(p.offset)).all()
    return p.envelope([Q.material_summary(m) for m in rows], total)


@router.get("/materials/{material_id}", response_model=S.MaterialDetail,
            summary="A material with its datasets", responses={404: {"model": S.Error}})
def get_material(material_id: str, s: Session = Depends(session), base: str = Depends(base_url),
                 brand_name: str = Depends(brand)):
    m = Q.get_material(s, material_id)
    if m is None:
        raise ApiError(404, "not_found", f"no material {material_id!r} (use an id, slug or exact alias)")
    return Q.material_detail(s, m, base, brand_name)


@router.get("/categories", response_model=list[S.Category],
            summary="Material categories for browsing, with counts and featured materials")
def categories(s: Session = Depends(session)):
    counts: dict[str, list[int]] = {}
    for cat, is_supplier, n in s.execute(select(Material.category, Material.manufacturer.is_not(None), func.count())
                                         .group_by(Material.category, Material.manufacturer.is_not(None))):
        counts.setdefault(cat, [0, 0])[1 if is_supplier else 0] += n
    featured_slugs = [slug for c in CATEGORIES for slug in Q.CATEGORY_INFO[c][2]]
    found = {m.slug: m for m in s.scalars(select(Material).where(Material.slug.in_(featured_slugs)))}
    out = []
    for c in CATEGORIES:
        name, description, featured = Q.CATEGORY_INFO[c]
        research, supplier = counts.get(c, [0, 0])
        out.append({"id": c, "name": name, "description": description, "count": research + supplier,
                    "research_count": research, "supplier_count": supplier,
                    "featured": [Q.material_ref(found[x]) for x in featured if x in found]})
    return out


@router.get("/catalog", summary="The imported catalog tree (shelves, books, pages)")
def catalog(shelf: str | None = Query(None, description="Return the books and pages of one shelf"),
            s: Session = Depends(session)):
    if shelf is None:
        rows = s.execute(select(CatalogEntry.shelf, CatalogEntry.shelf_name, CatalogEntry.section,
                                func.count(), func.min(CatalogEntry.position))
                         .group_by(CatalogEntry.shelf, CatalogEntry.shelf_name, CatalogEntry.section)
                         .order_by(func.min(CatalogEntry.position))).all()
        return {"source": "refractiveindex.info", "shelves": [
            {"shelf": r[0], "name": r[1], "section": r[2], "pages": r[3]} for r in rows]}
    rows = s.execute(select(CatalogEntry, Dataset.public_id, Dataset.slug, Material.public_id, Material.slug)
                     .outerjoin(Dataset, Dataset.id == CatalogEntry.dataset_id)
                     .outerjoin(Material, Material.id == CatalogEntry.material_id)
                     .where(CatalogEntry.shelf == shelf).order_by(CatalogEntry.position)).all()
    if not rows:
        raise ApiError(404, "not_found", f"no shelf {shelf!r}")
    books: dict[str, dict] = {}
    for c, ds_id, ds_slug, mat_id, mat_slug in rows:
        b = books.setdefault(c.book, {"book": c.book, "name": c.book_name, "group": c.book_group, "pages": []})
        b["pages"].append({"page": c.page, "name": c.page_name, "group": c.page_group, "dataset_id": ds_id,
                           "dataset_slug": ds_slug, "material_id": mat_id, "material_slug": mat_slug})
    first = rows[0][0]
    return {"shelf": shelf, "name": first.shelf_name, "section": first.section, "books": list(books.values())}


@router.get("/references", response_model=S.Page[S.ReferenceOut], summary="Search references")
def list_references(doi: str | None = None, q: str | None = Query(None, description="Text in citation or title"),
                    p: Paging = Depends(paging), s: Session = Depends(session)):
    stmt = select(Reference)
    if doi:
        stmt = stmt.where(Reference.doi == (normalize_doi(doi) or doi.lower()))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Reference.citation).like(like), func.lower(Reference.title).like(like)))
    total = s.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = s.scalars(stmt.order_by(Reference.year.desc(), Reference.id).limit(p.limit).offset(p.offset)).all()
    return p.envelope([Q.reference_out(r) for r in rows], total)


@router.get("/references/{reference_id:path}", summary="A reference and the datasets citing it")
def get_reference(reference_id: str, s: Session = Depends(session)):
    ref = s.scalar(select(Reference).where(or_(Reference.public_id == reference_id,
                                                Reference.doi == normalize_doi(reference_id))))
    if ref is None:
        raise ApiError(404, "not_found", f"no reference {reference_id!r}")
    ds = s.scalars(select(Dataset).join(DatasetReference, DatasetReference.dataset_id == Dataset.id)
                   .where(DatasetReference.reference_id == ref.id, Dataset.status == "published")
                   .options(*Q.dataset_loader()).order_by(Dataset.slug).limit(500)).all()
    return {**Q.reference_out(ref), "doi_metadata": ref.doi_metadata,
            "datasets": [Q.dataset_summary(d) for d in ds]}


@router.get("/licenses", response_model=list[S.License], summary="Licenses and which ones contributions may use")
def licenses(s: Session = Depends(session)):
    return [{**Q.license_out(x), "allowed_for_contributions": x.allowed_for_contributions}
            for x in s.scalars(select(License).order_by(License.id))]


@router.get("/stats", summary="Database statistics and the imported snapshot")
def stats(s: Session = Depends(session)):
    return Q.database_stats(s)
