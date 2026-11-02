"""The public website: server-rendered pages over the same database as the API.

URLs (all stable; every material and dataset has exactly one canonical URL):

* ``/``: home, search box, shelves and categories. API clients that do not ask for HTML still
  get the service description JSON here, as before.
* ``/browse``, ``/browse/{shelf}``, ``/browse/{shelf}/{book}``: the source catalog, shelf → book → page.
* ``/materials/{material}``: a material (a book of the catalog) with all its pages.
* ``/materials/{material}/{dataset}``: a dataset (a page of the catalog), the canonical page.
* ``/categories/{id}``, ``/search?q=``, ``/about``, ``/sitemap.xml``, ``/robots.txt``.

Redirects (301) keep other addresses working: refractiveindex.info-style ``/?shelf=&book=&page=``,
``/browse/{shelf}/{book}/{page}``, dataset and material aliases and public IDs (``/datasets/ds_…``).
"""

from __future__ import annotations

import hashlib
import math
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import __version__
from ..config import RII_CITATION, RII_DOI, RII_REPOSITORY, Settings
from ..dispersion import available as available_models
from ..importer import _library_names
from ..models import CATEGORIES, CatalogEntry, Dataset, DatasetReference, ImportRun, Material, Reference
from ..api import queries as Q
from ..api.deps import Paging, session, settings as get_settings
from ..api.routes_datasets import list_datasets, tabulated
from ..api.routes_materials import categories as api_categories, list_materials
from . import content as C

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.filters.update(num=C.num, sanitize=C.sanitize, plain=C.plain, paragraphs=C.paragraphs,
                             um_range=C.um_range, urlquote=lambda s: quote(str(s), safe=""))
templates.env.globals.update(categories=Q.CATEGORY_NAMES, rii_citation=RII_CITATION, rii_doi=RII_DOI,
                             rii_repository=RII_REPOSITORY)

router = APIRouter(include_in_schema=False)

HTML_CACHE = "public, max-age=300, stale-while-revalidate=86400"
SECTION_TEXT = {
    "Research data": "Optical constants from the research literature, grouped as in the source database.",
    "Supplier data": "Datasheet values published by glass, crystal and polymer suppliers.",
    "Selected data": "Hand-picked shortcuts to commonly used datasets.",
}
FORMULA_TEXT = {1: "Sellmeier", 2: "Sellmeier (C in µm²)", 3: "Polynomial", 4: "RefractiveIndex.INFO",
                5: "Cauchy", 6: "Gases", 7: "Herzberger", 8: "Retro", 9: "Exotic"}


@lru_cache(maxsize=None)
def _static_version() -> str:
    h = hashlib.sha256()
    for p in sorted((HERE / "static").rglob("*")):
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()[:10]


def static_url(path: str) -> str:
    return f"/static/{path}?v={_static_version()}"


templates.env.globals["static_url"] = static_url


def _render(request: Request, template: str, status: int = 200, **ctx) -> HTMLResponse:
    cfg: Settings = request.app.state.settings
    base = cfg.public_base_url
    ctx.setdefault("canonical", None)
    resp = templates.TemplateResponse(request, template, {
        "cfg": cfg, "base": base, "brand": cfg.brand_name, "library": cfg.library_name, "version": __version__,
        **ctx}, status_code=status)
    resp.headers["Cache-Control"] = HTML_CACHE if status == 200 else "no-store"
    return resp


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=301, headers={"Cache-Control": "public, max-age=86400"})


def _not_found(request: Request, what: str) -> HTMLResponse:
    return _render(request, "404.html", status=404, what=what, title="Not found")


def wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept or "application/xhtml" in accept


# --- URL helpers --------------------------------------------------------------------------

def material_url(m: Material) -> str:
    return f"/materials/{m.slug}"


def dataset_url(ds: Dataset) -> str:
    return f"/materials/{ds.slug}"


def book_url(shelf: str, book: str) -> str:
    return f"/browse/{quote(shelf, safe='')}/{quote(book, safe='')}"


templates.env.globals.update(material_url=material_url, dataset_url=dataset_url, book_url=book_url)


def _book_materials(s: Session, shelf: str, books: list[str]) -> dict[str, Material]:
    """Books of a research shelf that are a material of their own (keyed ``rii:{shelf}/{book}``)."""
    if shelf == "specs":
        return {}
    keys = {f"rii:{shelf}/{b}": b for b in books}
    return {keys[m.source_key]: m for m in s.scalars(select(Material).where(Material.source_key.in_(list(keys))))}


def _book_target(s: Session, shelf: str, book: str) -> str:
    m = _book_materials(s, shelf, [book]).get(book)
    return material_url(m) if m else book_url(shelf, book)


def _snapshot(s: Session) -> dict | None:
    run = s.scalar(select(ImportRun).where(ImportRun.status == "ok").order_by(ImportRun.id.desc()))
    return {"commit": run.commit, "imported_at": run.finished_at} if run else None


def _shelves(s: Session) -> list[dict]:
    rows = s.execute(select(CatalogEntry.shelf, CatalogEntry.shelf_name, CatalogEntry.section,
                            func.count(), func.count(func.distinct(CatalogEntry.book)), func.min(CatalogEntry.position))
                     .group_by(CatalogEntry.shelf, CatalogEntry.shelf_name, CatalogEntry.section)
                     .order_by(func.min(CatalogEntry.position))).all()
    sections: dict[str, dict] = {}
    for shelf, name, section, pages, books, _ in rows:
        title, _, subtitle = (name or shelf).partition(" - ")
        sec = sections.setdefault(section or "", {"name": section, "text": SECTION_TEXT.get(section or ""),
                                                  "shelves": []})
        sec["shelves"].append({"shelf": shelf, "name": name, "title": title.strip(), "subtitle": subtitle.strip(),
                               "pages": pages, "books": books})
    return list(sections.values())


# --- home, about, search ------------------------------------------------------------------

@router.get("/")
def home(request: Request, shelf: str | None = None, book: str | None = None, page: str | None = None,
         s: Session = Depends(session), cfg: Settings = Depends(get_settings)):
    # refractiveindex.info-style addresses: /?shelf=main&book=Au&page=Johnson
    if shelf:
        if book and page:
            return browse_page(request, shelf, book, page, s)
        if book:
            return _redirect(_book_target(s, shelf, book))
        return _redirect(f"/browse/{quote(shelf, safe='')}")
    if not wants_html(request):
        return {"service": cfg.brand_name, "version": __version__, "openapi": "/openapi.json",
                "docs": "/docs", "api": "/v1", "website": "/"}
    stats = Q.database_stats(s)
    cats = [c for c in api_categories(s) if c["count"]]
    featured = [("Gold", "au"), ("Silicon", "si"), ("Fused silica", "sio2"), ("Silicon nitride", "si3n4"),
                ("Lithium niobate", "linbo3"), ("Water", "h2o"), ("Silver", "ag"), ("Titanium dioxide", "tio2")]
    have = {m.slug for m in s.scalars(select(Material).where(Material.slug.in_([x[1] for x in featured])))}
    return _render(request, "home.html", stats=stats, sections=_shelves(s), cats=cats,
                   featured=[f for f in featured if f[1] in have], snapshot=_snapshot(s), canonical="/",
                   description=(f"Refractive index n, extinction coefficient k and permittivity of "
                                f"{stats['materials']:,} materials ({stats['datasets']:,} datasets) with plots, "
                                f"formulas, references and a calculator. Load any dataset in the {cfg.library_name} "
                                "photonics library."))


@router.get("/about")
def about(request: Request, s: Session = Depends(session)):
    return _render(request, "about.html", stats=Q.database_stats(s), snapshot=_snapshot(s), canonical="/about",
                   title="About", description="Where the data come from, how to cite them, and the API.")


@router.get("/search")
def search(request: Request, q: str = Query("", max_length=200), category: str | None = None,
           s: Session = Depends(session)):
    q = q.strip()
    cat = category if category in CATEGORIES else None
    materials = datasets = refs = None
    if q or cat:
        materials = list_materials(q=q or None, category=cat, tag=None, supplier=None, p=Paging(60, 0), s=s)
    if q:
        datasets = list_datasets(q=q, material=None, category=cat, has_k=None, covers_nm=None, doi=None, source=None,
                                 p=Paging(40, 0), s=s)
        like = f"%{q.lower()}%"
        refs = s.scalars(select(Reference).where(func.lower(Reference.citation).like(like))
                         .order_by(Reference.year.desc().nulls_last()).limit(20)).all()
        ref_ds = {}
        for r in refs:
            ref_ds[r.id] = s.scalars(select(Dataset).join(DatasetReference)
                                     .where(DatasetReference.reference_id == r.id, Dataset.status == "published")
                                     .order_by(Dataset.slug).limit(6)).all()
        refs = [(r, ref_ds[r.id]) for r in refs if ref_ds[r.id]]
    resp = _render(request, "search.html", q=q, category=cat, materials=materials, datasets=datasets, refs=refs,
                   title=f"Search: {q}" if q else "Search", canonical="/search")
    resp.headers["X-Robots-Tag"] = "noindex, follow"
    return resp


# --- catalog ------------------------------------------------------------------------------

@router.get("/browse")
def browse(request: Request, s: Session = Depends(session)):
    return _render(request, "browse.html", sections=_shelves(s), canonical="/browse", title="Browse the database",
                   description="All shelves of the database: research data, supplier datasheets and selections.")


@router.get("/browse/{shelf}")
def browse_shelf(request: Request, shelf: str, s: Session = Depends(session)):
    rows = s.execute(select(CatalogEntry.book, CatalogEntry.book_name, CatalogEntry.book_group,
                            CatalogEntry.shelf_name, CatalogEntry.section, func.count(),
                            func.min(CatalogEntry.position))
                     .where(CatalogEntry.shelf == shelf)
                     .group_by(CatalogEntry.book, CatalogEntry.book_name, CatalogEntry.book_group,
                               CatalogEntry.shelf_name, CatalogEntry.section)
                     .order_by(func.min(CatalogEntry.position))).all()
    if not rows:
        return _not_found(request, f"shelf “{shelf}”")
    mats = _book_materials(s, shelf, [r[0] for r in rows])
    groups: list[dict] = []
    for book, name, group, *_rest in rows:
        if not groups or groups[-1]["name"] != group:
            groups.append({"name": group, "books": []})
        m = mats.get(book)
        groups[-1]["books"].append({"book": book, "name": name or book, "pages": _rest[2],
                                    "url": material_url(m) if m else book_url(shelf, book),
                                    "category": m.category if m else None})
    shelf_name = rows[0][3] or shelf
    return _render(request, "shelf.html", shelf=shelf, shelf_name=shelf_name, section=rows[0][4], groups=groups,
                   books=len(rows), canonical=f"/browse/{quote(shelf, safe='')}", title=C.plain(shelf_name),
                   description=f"{C.plain(shelf_name)}: {len(rows)} books in the optical constants database.")


def _pages_of_book(s: Session, shelf: str, book: str, page: str | None = None) -> list[tuple[CatalogEntry, Dataset]]:
    stmt = (select(CatalogEntry, Dataset).join(Dataset, Dataset.id == CatalogEntry.dataset_id)
            .where(CatalogEntry.shelf == shelf, CatalogEntry.book == book).order_by(CatalogEntry.position))
    if page is not None:
        stmt = stmt.where(CatalogEntry.page == page)
    return [tuple(r) for r in s.execute(stmt)]


@router.get("/browse/{shelf}/{book}")
def browse_book(request: Request, shelf: str, book: str, s: Session = Depends(session)):
    m = _book_materials(s, shelf, [book]).get(book)
    if m is not None:
        return _redirect(material_url(m))
    pages = _pages_of_book(s, shelf, book)
    if not pages:
        return _not_found(request, f"book “{book}” on shelf “{shelf}”")
    first = pages[0][0]
    groups = _group_pages(pages)
    return _render(request, "book.html", shelf=shelf, shelf_name=first.shelf_name, book=book,
                   book_name=first.book_name or book, groups=groups, count=len(pages),
                   canonical=book_url(shelf, book), title=C.plain(first.book_name or book),
                   description=f"{C.plain(first.book_name or book)} ({C.plain(first.shelf_name)}): "
                               f"{len(pages)} datasets with refractive index and extinction coefficient.")


def _group_pages(pages: list[tuple[CatalogEntry, Dataset]]) -> list[dict]:
    groups: list[dict] = []
    for c, ds in pages:
        if not groups or groups[-1]["name"] != c.page_group:
            groups.append({"name": c.page_group, "pages": []})
        groups[-1]["pages"].append({"entry": c, "ds": ds, "name": c.page_name or c.page})
    return groups


@router.get("/browse/{shelf}/{book}/{page}")
def browse_page(request: Request, shelf: str, book: str, page: str, s: Session = Depends(session)):
    pages = _pages_of_book(s, shelf, book, page)
    if not pages:
        return _not_found(request, f"page “{page}” in book “{book}” on shelf “{shelf}”")
    return _redirect(dataset_url(pages[0][1]))


@router.get("/categories/{category}")
def category_page(request: Request, category: str, supplier: bool = False, page: int = Query(1, ge=1),
                  s: Session = Depends(session)):
    if category not in CATEGORIES:
        return _not_found(request, f"category “{category}”")
    info = Q.CATEGORY_INFO[category]
    per = 120
    res = list_materials(q=None, category=category, tag=None, supplier=supplier, p=Paging(per, (page - 1) * per), s=s)
    other = s.scalar(select(func.count()).where(Material.category == category,
                                                Material.manufacturer.is_not(None) if not supplier
                                                else Material.manufacturer.is_(None)))
    groups: list[dict] = []
    for m in res["items"]:
        key = m["group"] or ""
        if not groups or groups[-1]["name"] != key:
            groups.append({"name": key, "items": []})
        groups[-1]["items"].append(m)
    if not supplier:
        groups.sort(key=lambda g: (g["name"] == "", g["name"].lower()))
    canonical = f"/categories/{category}" + ("?supplier=true" if supplier else "") + (
        f"{'&' if supplier else '?'}page={page}" if page > 1 else "")
    return _render(request, "category.html", category=category, info=info, res=res, groups=groups,
                   supplier=supplier, other=other, page=page, pages=max(1, math.ceil(res["total"] / per)),
                   canonical=canonical, title=info[0], description=info[1])


# --- materials and datasets --------------------------------------------------------------------

@router.get("/datasets/{ident:path}")
def dataset_by_id(request: Request, ident: str, s: Session = Depends(session)):
    ds = Q.get_dataset(s, ident)
    if ds is None or ds.status != "published":
        return _not_found(request, f"dataset “{ident}”")
    return _redirect(dataset_url(ds))


@router.get("/materials/{slug:path}")
def material_or_dataset(request: Request, slug: str, s: Session = Depends(session),
                        cfg: Settings = Depends(get_settings)):
    slug = slug.strip("/")
    if slug.endswith("/studio.json"):
        return _studio_spec(request, slug.removesuffix("/studio.json"), s, cfg)
    if "/" not in slug:
        m = Q.get_material(s, slug)
        if m is None:
            ds = Q.get_dataset(s, slug)  # a dataset public ID
            if ds is not None and ds.status == "published":
                return _redirect(dataset_url(ds))
            return _not_found(request, f"material “{slug}”")
        if m.slug != slug:
            return _redirect(material_url(m))
        return _material_page(request, s, cfg, m)
    ds = Q.get_dataset(s, slug)
    if ds is None or ds.status != "published":
        return _not_found(request, f"dataset “{slug}”")
    if ds.slug != slug:
        return _redirect(dataset_url(ds))
    return _dataset_page(request, s, cfg, ds)


def _material_entries(s: Session, m: Material) -> list[tuple[CatalogEntry | None, Dataset]]:
    """The pages of a material's book, in catalog order (including pages that re-list other materials'
    data, as the source does), then its datasets that are not in the catalog (community submissions)."""
    key = (m.source_key or "").removeprefix("rii:")
    rows: list[tuple[CatalogEntry | None, Dataset]] = []
    if m.source == "rii" and key.count("/") >= 1:
        parts = key.split("/")
        stmt = select(CatalogEntry, Dataset).join(Dataset, Dataset.id == CatalogEntry.dataset_id).where(
            CatalogEntry.shelf == parts[0], CatalogEntry.book == parts[1], Dataset.status == "published")
        if parts[0] == "specs" and len(parts) > 2:
            stmt = stmt.where(CatalogEntry.page == "/".join(parts[2:]))
        rows = [tuple(r) for r in s.execute(stmt.order_by(CatalogEntry.position))]
    seen = {ds.id for _, ds in rows}
    rows += [(None, d) for d in m.datasets if d.status == "published" and d.id not in seen]
    return rows


def _material_groups(m: Material, entries: list[tuple[CatalogEntry | None, Dataset]],
                     default: Dataset | None) -> list[dict]:
    groups: list[dict] = []
    for c, ds in entries:
        g = c.page_group if c else "Community contributions"
        if not groups or groups[-1]["name"] != g:
            groups.append({"name": g, "pages": []})
        groups[-1]["pages"].append({"entry": c, "ds": ds, "name": (c.page_name if c else ds.name) or ds.name,
                                    "is_default": default is not None and ds.id == default.id,
                                    "foreign": ds.material_id != m.id})
    return groups


def _library_bits(s: Session, cfg: Settings, ds: Dataset, label: str) -> tuple[list[dict], str | None]:
    """The library snippets for a dataset, and the Studio link that opens a sphere made of it."""
    m = ds.material
    ver = Q.get_version(ds, None)
    disp = Q.dispersion(ver)
    src = dict((ver.meta or {}).get("source") or {}) if ds.source == "rii" else {}
    lib = _library_names().get((ds.source_key or "").removeprefix("rii:"))
    snippet_list = C.snippets(ds_slug=ds.slug, ds_public_id=ds.public_id, label=label, source=src, library=lib,
                              has_n=disp.has_n, range_nm=disp.range_nm, var=C.py_identifier(m.formula, m.slug),
                              base_url=cfg.public_base_url, has_model=bool(available_models(s, ds, ver)),
                              docs_url=cfg.docs_url)
    studio = None
    if disp.has_n:
        spec_url = f"{cfg.public_base_url}{dataset_url(ds)}/studio.json"
        studio = f"{cfg.studio_url}/?spec_url={quote(spec_url, safe='')}&name={quote(C.plain(m.name) + ' sphere')}"
    return snippet_list, studio


def _material_page(request: Request, s: Session, cfg: Settings, m: Material) -> HTMLResponse:
    entries = _material_entries(s, m)
    default = Q.default_dataset(m)
    groups = _material_groups(m, entries, default)
    snippet_list, studio = _library_bits(s, cfg, default, f"{C.plain(m.name)}: {default.name}") if default else ([], None)
    listed = s.execute(select(CatalogEntry.shelf, CatalogEntry.shelf_name, CatalogEntry.book,
                              CatalogEntry.book_name).where(CatalogEntry.material_id == m.id)
                       .group_by(CatalogEntry.shelf, CatalogEntry.shelf_name, CatalogEntry.book,
                                 CatalogEntry.book_name)).all()
    key = (m.source_key or "").removeprefix("rii:").split("/")
    home_shelf = key[0] if m.source == "rii" else None
    home_shelf_name = s.scalar(select(CatalogEntry.shelf_name).where(CatalogEntry.shelf == home_shelf).limit(1))
    others = [r for r in listed if (r[0], r[2]) != (key[0], key[1] if len(key) > 1 else None)]
    lib = _library_names()
    bundled = sorted({v["material"] for k, v in lib.items() if any(d.source_key == f"rii:{k}" for d in m.datasets)})
    # plot the default dataset and the first datasets of other sources (one per author/year)
    plotted = sorted((e for e in entries if e[1].data_type != "tabulated k"),
                     key=lambda e: default is None or e[1].id != default.id)
    compare, sources = [], set()
    for c, ds in plotted:
        source = C.plain((c.page_name if c else None) or ds.name).split(":")[0]
        if source not in sources:
            sources.add(source)
            compare.append(ds.public_id)
    lo = min((ds.wavelength_min_nm for _, ds in entries), default=None)
    hi = max((ds.wavelength_max_nm for _, ds in entries), default=None)
    title = C.plain(m.name_html or m.name)
    desc = C.summary(m.description) if m.description else (
        f"Refractive index of {title}: {len(entries)} datasets"
        + (f", {C.um_range(lo, hi)}" if lo else "") + ". Plots, data, formulas, references and Python code.")
    return _render(request, "material.html", m=m, groups=groups, entries=entries, default=default,
                   snippets=snippet_list, studio=studio, material_about=C.paragraphs(m.description),
                   others=others, home_shelf=home_shelf, home_shelf_name=home_shelf_name, bundled=bundled,
                   compare_ids=compare[:5], lo=lo, hi=hi, canonical=material_url(m),
                   title=f"Refractive index of {title}", description=desc,
                   var=C.py_identifier(m.formula, m.slug),
                   aliases=[a for a in (m.aliases or []) if C.plain(a).lower() not in (title.lower(), m.slug)])


def _dataset_page(request: Request, s: Session, cfg: Settings, ds: Dataset) -> HTMLResponse:
    m = ds.material
    ver = Q.get_version(ds, None)
    disp = Q.dispersion(ver)
    detail = Q.dataset_detail(ds, cfg.public_base_url, cfg.brand_name)
    src = detail["source"] if ds.source == "rii" else {}
    entries = _material_entries(s, m)
    home_entry = s.scalar(select(CatalogEntry).where(CatalogEntry.dataset_id == ds.id)
                          .order_by(CatalogEntry.shelf.in_(("main", "organic", "glass", "other", "specs")).desc(),
                                    CatalogEntry.position).limit(1))
    listed = s.scalars(select(CatalogEntry).where(CatalogEntry.dataset_id == ds.id)
                       .order_by(CatalogEntry.position)).all()
    lo, hi = disp.range_nm
    wl0 = C.default_wavelength_um(lo, hi)
    calc = C.derived_at(disp, wl0)
    rows, published = C.table_rows(ver.blocks, disp)
    conditions, properties = C.source_order(ver.source_file.content if ver.source_file else None,
                                            detail["conditions"], detail["properties"])
    lib = _library_names().get((ds.source_key or "").removeprefix("rii:"))
    page_label = C.plain(home_entry.page_name if home_entry and home_entry.page_name else ds.name)
    snippet_list, studio = _library_bits(s, cfg, ds, f"{C.plain(m.name)}: {page_label}")
    title_name = C.plain(m.name_html or m.name)
    kinds = "n,k" if disp.has_n and ds.has_k else ("n" if disp.has_n else "k")
    description = (f"Refractive index of {title_name} ({page_label}): {kinds} {C.um_range(lo, hi)}. "
                   "Interactive plots, data table, dispersion formula, references, calculator and Python code.")
    jsonld = _dataset_jsonld(ds, detail, cfg, title_name, page_label, lo, hi)
    return _render(request, "dataset.html", ds=ds, m=m, ver=ver, disp=disp, detail=detail, src=src,
                   entries=entries, home_entry=home_entry, listed=listed, lo=lo, hi=hi, wl0=wl0, calc=calc,
                   abbe=C.abbe_number(disp), rows=rows, published=published, formula=C.formula_info(ver.blocks),
                   formula_text=FORMULA_TEXT, plot=C.json_script(C.plot_payload(disp)), snippets=snippet_list,
                   library_entry=lib, studio=studio, page_label=page_label, kinds=kinds,
                   groups=_material_groups(m, entries, Q.default_dataset(m)), current=ds.id,
                   show_k=disp.k_tab is not None, conditions=conditions, properties=properties,
                   canonical=dataset_url(ds), title=f"{title_name}: {page_label}", description=description,
                   jsonld=C.json_script(jsonld), api=f"/v1/datasets/{ds.public_id}",
                   material_about=C.paragraphs(m.description))


def _dataset_jsonld(ds: Dataset, detail: dict, cfg: Settings, name: str, label: str, lo: float, hi: float) -> dict:
    """schema.org/Dataset, so search engines (and dataset search) can index each page."""
    api = f"{cfg.public_base_url}/v1/datasets/{ds.public_id}/data"
    out = {
        "@context": "https://schema.org", "@type": "Dataset",
        "name": f"Refractive index of {name}: {label}",
        "description": (f"Optical constants ({ds.data_type}) of {name} from {C.um_range(lo, hi)}. "
                        + (C.plain(ds.comments) + ". " if ds.comments else "") + detail["citation"])[:5000],
        "url": f"{cfg.public_base_url}{dataset_url(ds)}", "identifier": ds.public_id,
        "license": detail["license_info"]["url"], "isAccessibleForFree": True,
        "keywords": ["refractive index", "extinction coefficient", "optical constants", "permittivity", name],
        "variableMeasured": ["refractive index n"] + (["extinction coefficient k"] if ds.has_k else []),
        "distribution": [{"@type": "DataDownload", "encodingFormat": "text/csv", "contentUrl": f"{api}?format=csv"},
                         {"@type": "DataDownload", "encodingFormat": "application/yaml",
                          "contentUrl": f"{api}?format=yaml"}],
        "includedInDataCatalog": {"@type": "DataCatalog", "name": cfg.brand_name, "url": cfg.public_base_url},
        "isBasedOn": detail["source"].get("url") or None,
        "citation": [r["formatted"] for r in detail["references"] if r["role"] == "source"] or None,
    }
    return {k: v for k, v in out.items() if v}


def _studio_spec(request: Request, slug: str, s: Session, cfg: Settings) -> Response:
    ds = Q.get_dataset(s, slug)
    if ds is None or ds.status != "published":
        return JSONResponse({"error": "not_found", "message": f"no dataset {slug!r}"}, status_code=404)
    mat, _ = tabulated(ds, None, None, None, None, cfg.public_base_url, cfg.brand_name)
    spec = C.studio_spec(mat, Q.dispersion(Q.get_version(ds, None)).range_nm)
    # Studio fetches this cross-origin (?spec_url=); the data are public.
    return JSONResponse(spec, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": HTML_CACHE})


# --- crawlers -----------------------------------------------------------------------------

@router.get("/robots.txt")
def robots(cfg: Settings = Depends(get_settings)):
    return PlainTextResponse(f"User-agent: *\nAllow: /\nDisallow: /v1/\nDisallow: /search\n\n"
                             f"Sitemap: {cfg.public_base_url}/sitemap.xml\n")


@router.get("/sitemap.xml")
def sitemap(s: Session = Depends(session), cfg: Settings = Depends(get_settings)):
    base = cfg.public_base_url
    urls: list[tuple[str, str | None]] = [("/", None), ("/browse", None), ("/about", None)]
    urls += [(f"/categories/{c}", None) for c in CATEGORIES]
    shelves = s.execute(select(CatalogEntry.shelf, CatalogEntry.book).distinct()).all()
    for shelf in sorted({r[0] for r in shelves}):
        urls.append((f"/browse/{quote(shelf, safe='')}", None))
    mat_keys = set(s.scalars(select(Material.source_key).where(Material.source_key.is_not(None))))
    for shelf, book in sorted(shelves):
        if f"rii:{shelf}/{book}" not in mat_keys:
            urls.append((book_url(shelf, book), None))
    for slug, updated in s.execute(select(Material.slug, Material.updated_at).order_by(Material.slug)):
        urls.append((f"/materials/{slug}", updated.date().isoformat() if updated else None))
    for slug, updated in s.execute(select(Dataset.slug, Dataset.updated_at).where(Dataset.status == "published")
                                   .order_by(Dataset.slug)):
        urls.append((f"/materials/{slug}", updated.date().isoformat() if updated else None))
    body = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, lastmod in urls:
        loc = (base + path).replace("&", "&amp;")
        body.append(f"<url><loc>{loc}</loc>" + (f"<lastmod>{lastmod}</lastmod>" if lastmod else "") + "</url>")
    body.append("</urlset>")
    return Response("\n".join(body), media_type="application/xml",
                    headers={"Cache-Control": "public, max-age=3600"})
