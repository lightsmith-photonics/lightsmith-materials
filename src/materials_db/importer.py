"""Import of the refractiveindex.info database (CC0) at a pinned commit.

Reads ``database/catalog-nk.yml`` and every data file it lists, straight from
the repository zip archive (nothing is extracted or executed) or from a
checkout. Re-running is safe: records are keyed by their source path, and a
dataset only gets a new version when its file content changed.

Mapping:
- every book of the shelves main, organic, glass and other is a material;
  on the supplier shelf ("specs") every page is a material (one glass type);
- every distinct data file is one dataset, owned by the first material that
  lists it; later listings (e.g. "fused silica" re-listing SiO2/Malitson, the
  "3d" and "popular_glass" shelves) become catalog entries pointing at it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

import httpx
import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import rii
from .config import RII_ARCHIVE_URL, RII_CITATION, RII_DOI, RII_PAGE_URL, RII_RAW_URL, RII_REPOSITORY
from .models import (CatalogEntry, Dataset, DatasetAlias, DatasetReference, DatasetVersion, ImportRun, Material,
                     Reference, SourceFile)
from .naming import (EXTRA_ALIASES, categorize, formula_from_name, material_state, optical_axis, search_text, slugify,
                     stable_id, tags_for)
from .optics import Dispersion
from .photonics import thermo_optic_from_source
from .references import DoiResolver, apply_metadata, get_or_create
from .seed import ensure_licenses

log = logging.getLogger(__name__)

MATERIAL_SHELVES = ("main", "organic", "glass", "other", "specs")
MAX_FILE_BYTES = 50_000_000
_SAFE_REF = re.compile(r"^[A-Za-z0-9._\-]+$")


# --- sources ------------------------------------------------------------------------

class ArchiveSource:
    """The repository zip from GitHub; only ``database/**.yml`` members are ever read."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.zip = zipfile.ZipFile(self.path)
        cats = [n for n in self.zip.namelist() if n.endswith("database/catalog-nk.yml")]
        if not cats:
            raise ValueError(f"{path} does not look like the refractiveindex.info database archive")
        self.prefix = cats[0][: -len("catalog-nk.yml")]
        self.names = set(self.zip.namelist())
        self.sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def read(self, rel: str) -> bytes | None:
        name = self.prefix + rel
        if not rel.endswith(".yml") or name not in self.names:
            return None
        info = self.zip.getinfo(name)
        if info.file_size > MAX_FILE_BYTES:
            raise ValueError(f"{rel}: file too large ({info.file_size} bytes)")
        return self.zip.read(name)


class DirectorySource:
    """A checkout of the repository (or its ``database`` directory)."""

    def __init__(self, path: Path):
        root = Path(path)
        self.root = root / "database" if (root / "database" / "catalog-nk.yml").is_file() else root
        if not (self.root / "catalog-nk.yml").is_file():
            raise ValueError(f"no catalog-nk.yml under {path}")
        self.sha256 = None

    def read(self, rel: str) -> bytes | None:
        p = (self.root / rel).resolve()
        if not rel.endswith(".yml") or self.root.resolve() not in p.parents or not p.is_file():
            return None
        return p.read_bytes()


def download_archive(commit: str, dest_dir: Path, timeout: float = 120.0) -> Path:
    """Download the repository zip at ``commit`` (cached in ``dest_dir``)."""
    if not _SAFE_REF.match(commit):
        raise ValueError(f"invalid commit {commit!r}")
    dest = Path(dest_dir) / f"refractiveindex.info-database-{commit}.zip"
    if dest.is_file() and zipfile.is_zipfile(dest):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    with httpx.stream("GET", RII_ARCHIVE_URL.format(commit=commit), timeout=timeout, follow_redirects=True) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    tmp.replace(dest)
    return dest


def open_source(path: Path):
    path = Path(path)
    return DirectorySource(path) if path.is_dir() else ArchiveSource(path)


# --- catalog ------------------------------------------------------------------------

@dataclass
class Page:
    shelf: str
    shelf_name: str | None
    section: str | None
    book: str
    book_name: str | None
    book_group: str | None
    page: str
    page_name: str | None
    page_group: str | None
    data_path: str
    position: int
    listing: int = 0


def read_catalog(text: str) -> list[Page]:
    pages: list[Page] = []
    seen: dict[tuple, int] = {}
    section = None
    for top in rii.parse_yaml(text):
        if "DIVIDER" in top:
            section = top["DIVIDER"]
            continue
        shelf, shelf_name, book_group = str(top["SHELF"]), top.get("name"), None
        for b in top.get("content") or []:
            if "DIVIDER" in b:
                book_group = b["DIVIDER"]
                continue
            page_group = None
            for p in b.get("content") or []:
                if "DIVIDER" in p:
                    page_group = p["DIVIDER"]
                    continue
                path = re.sub(r"/+", "/", str(p["data"]).strip())
                key = (shelf, str(b["BOOK"]), str(p["PAGE"]))
                seen[key] = seen.get(key, -1) + 1
                pages.append(Page(shelf, shelf_name, section, key[1], b.get("name"), book_group,
                                  key[2], p.get("name"), page_group, path, len(pages), seen[key]))
    return pages


# --- naming of datasets -------------------------------------------------------------

_YEAR = re.compile(r"^(.*?)\s*\b((?:1[5-9]|20)\d\d)[a-z]?\b")


def dataset_part(page: Page) -> str:
    """Slug part from the catalog page name, e.g. "Johnson and Christy 1972: ..." -> "johnson-christy-1972";
    distinguishing bits of the page key are kept ("Ferrera-298K" -> "ferrera-2019-298k")."""
    page_slug = slugify(page.page)
    head = rii.strip_html(page.page_name or "").split(":")[0]
    m = _YEAR.match(head)
    if not m or not m.group(1).strip():
        return page_slug
    authors = re.sub(r"\bet al\.?|\band\b|&|,", " ", m.group(1))
    author_slug = slugify(authors, max_len=40)
    year = m.group(2)
    a_tokens = set(author_slug.split("-"))
    rest = [t for t in page_slug.split("-") if t not in a_tokens and t != year]
    return "-".join([author_slug, year, *rest])


def _library_names() -> dict:
    text = resources.files("materials_db").joinpath("data", "library_names.json").read_text(encoding="utf-8")
    return json.loads(text)["datasets"]


# --- import -------------------------------------------------------------------------

@dataclass
class Stats:
    commit: str | None = None
    catalog_pages: int = 0
    data_files: int = 0
    materials_created: int = 0
    materials_total: int = 0
    datasets_created: int = 0
    datasets_updated: int = 0
    datasets_unchanged: int = 0
    datasets_withdrawn: int = 0
    datasets_total: int = 0
    references_total: int = 0
    references_with_doi: int = 0
    datasets_without_primary_reference: int = 0
    catalog_entries: int = 0
    failures: list[dict] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    by_data_type: dict = field(default_factory=dict)
    by_category: dict = field(default_factory=dict)
    doi_lookups: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _content_hash(blocks: list, meta: dict) -> str:
    return hashlib.sha256(json.dumps([blocks, meta], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _source_file(session: Session, content: bytes, rel: str, commit: str) -> SourceFile:
    sha = hashlib.sha256(content).hexdigest()
    sf = session.scalar(select(SourceFile).where(SourceFile.sha256 == sha))
    if sf is None:
        sf = SourceFile(sha256=sha, filename=rel.rsplit("/", 1)[-1], path=f"database/data/{rel}",
                        origin_url=RII_RAW_URL.format(commit=commit, path=f"data/{rel}"),
                        media_type="application/yaml", size=len(content), content=content)
        session.add(sf)
        session.flush()
    return sf


def _temperature(cond: dict) -> float | None:
    for key in ("temperature", "temerature"):
        try:
            return float(cond[key])
        except (KeyError, TypeError, ValueError):
            continue
    return None


def import_rii(session: Session, source, commit: str, *, limit: int | None = None,
               resolver: DoiResolver | None = None, progress=None) -> Stats:
    """Import (or update to) the database snapshot ``commit`` from ``source``."""
    ensure_licenses(session)
    stats = Stats(commit=commit)
    run = ImportRun(source="rii", commit=commit, archive_sha256=getattr(source, "sha256", None))
    session.add(run)
    session.flush()

    pages = read_catalog(source.read("catalog-nk.yml").decode("utf-8"))
    stats.catalog_pages = len(pages)
    library = _library_names()

    # home page of each data file: first listing on a material shelf
    home: dict[str, Page] = {}
    for p in pages:
        if p.shelf in MATERIAL_SHELVES and p.data_path not in home:
            home[p.data_path] = p
    paths = list(home)[:limit] if limit else list(home)
    stats.data_files = len(home)

    # materials -------------------------------------------------------------------
    def mat_key(p: Page) -> str:
        return f"rii:specs/{p.book}/{p.page}" if p.shelf == "specs" else f"rii:{p.shelf}/{p.book}"

    taken = set(session.scalars(select(Material.slug)))
    materials: dict[str, Material] = {}
    book_pages: dict[str, list[Page]] = {}
    for path in paths:
        book_pages.setdefault(mat_key(home[path]), []).append(home[path])
    about_cache: dict[str, dict] = {}

    def about(dirpath: str) -> dict:
        if dirpath not in about_cache:
            raw = source.read(f"data/{dirpath}/about.yml")
            try:
                about_cache[dirpath] = rii.parse_yaml(raw.decode("utf-8")) if raw else {}
            except ValueError:
                about_cache[dirpath] = {}
        return about_cache[dirpath]

    for key, plist in book_pages.items():
        p0 = plist[0]
        mat = session.scalar(select(Material).where(Material.source_key == key))
        if p0.shelf == "specs":
            manufacturer = p0.book.split("-")[0]
            formula, common = None, None
            name = f"{rii.strip_html(p0.page_name or p0.page)} ({manufacturer})"
            name_html = None
            info = about(p0.data_path.split("/")[0] + "/" + p0.data_path.split("/")[1])
            base = slugify(f"{manufacturer}-{p0.page}")
            group = rii.strip_html(p0.book_name or "")
            aliases = [p0.page]
        else:
            manufacturer = None
            formula, common = formula_from_name(p0.book_name, p0.book)
            name = rii.strip_html(p0.book_name or p0.book)
            name_html = p0.book_name
            info = about(p0.data_path.split("/nk/")[0])
            base = slugify(p0.book)
            group = p0.book_group
            aliases = [p0.book, *([formula] if formula else []), *([common] if common else [])]
        aliases += [str(x) for x in (info.get("NAMES") or []) if x]
        for lib in (library.get(p.data_path) for p in plist):
            if lib:
                aliases += [lib["material"], *lib["material_aliases"]]
        aliases += EXTRA_ALIASES.get(key, [])
        aliases = list(dict.fromkeys(a for a in aliases if a))
        category = categorize(p0.shelf, p0.book, formula, p0.book_group, [p.page_group or "" for p in plist])
        description = str(info.get("ABOUT") or "").strip() or None
        if mat is None:
            slug = base
            if slug in taken:
                slug = f"{base}-{slugify(common or p0.shelf)}"
            i = 2
            while slug in taken:
                slug, i = f"{base}-{i}", i + 1
            taken.add(slug)
            mat = Material(public_id=stable_id("mat", key), slug=slug, source="rii", source_key=key)
            session.add(mat)
            stats.materials_created += 1
        mat.name, mat.name_html, mat.formula, mat.category = name, name_html, formula, category
        mat.group, mat.manufacturer, mat.description = group, manufacturer, description
        mat.external_links = [{"url": str(x["url"]), "text": str(x.get("text") or x["url"])}
                              for x in (info.get("LINKS") or []) if isinstance(x, dict) and x.get("url")]
        mat.aliases = aliases
        mat.tags = tags_for(p0.shelf, p0.book, formula, p0.book_group)
        mat.search_text = search_text(mat.slug, name, formula, manufacturer, *aliases)
        materials[key] = mat
        stats.by_category[category] = stats.by_category.get(category, 0) + 1
    session.flush()

    # the database itself, cited by every imported dataset
    db_ref = get_or_create(session, citation=f"{RII_CITATION} (refractiveindex.info database, {RII_REPOSITORY})",
                           doi=RII_DOI, year=2024,
                           title="Refractiveindex.info database of optical constants",
                           authors_text="M. N. Polyanskiy")

    # datasets --------------------------------------------------------------------
    datasets_by_path: dict[str, Dataset] = {}
    used_slugs = set(session.scalars(select(Dataset.slug))) | set(session.scalars(select(DatasetAlias.slug)))
    for i, path in enumerate(paths):
        p = home[path]
        if progress and i % 200 == 0:
            progress(i, len(paths))
        raw = source.read(f"data/{path}")
        if raw is None:
            stats.missing_files.append(path)
            continue
        try:
            text = raw.decode("utf-8")
            doc = rii.parse_yaml(text)
            blocks = rii.entry_blocks(doc)
            disp = Dispersion(blocks)
        except (ValueError, KeyError, TypeError, UnicodeDecodeError) as e:
            stats.failures.append({"path": path, "error": str(e)})
            continue
        mat = materials[mat_key(p)]
        cond = doc.get("CONDITIONS") if isinstance(doc.get("CONDITIONS"), dict) else {}
        flags = quality_flags(disp)
        refs = rii.split_references(doc.get("REFERENCES"))
        if not any(r["role"] == "source" for r in refs):
            flags.append("no primary reference in the source file")
        meta = {
            "name": rii.strip_html(p.page_name or p.page), "comments": doc.get("COMMENTS"),
            "conditions": cond, "properties": doc.get("PROPERTIES"),
            "source": {"database": "refractiveindex.info", "shelf": p.shelf, "book": p.book, "page": p.page,
                       "path": f"database/data/{path}", "commit": commit,
                       "url": RII_PAGE_URL.format(shelf=p.shelf, book=p.book, page=p.page)},
        }
        chash = _content_hash(blocks, {k: meta[k] for k in ("comments", "conditions", "properties")}
                              | {"references": doc.get("REFERENCES")})
        skey = f"rii:{path}"
        ds = session.scalar(select(Dataset).where(Dataset.source_key == skey))
        if ds is not None and ds.versions and ds.versions[-1].content_hash == chash:
            stats.datasets_unchanged += 1
            _describe(ds, mat, p, doc, blocks, disp, cond, flags, mat_key(p))  # metadata rules may have improved
            datasets_by_path[path] = ds
            stats.by_data_type[ds.data_type] = stats.by_data_type.get(ds.data_type, 0) + 1
            continue
        sf = _source_file(session, raw, path, commit)
        if ds is None:
            part = slugify(mat.manufacturer or "datasheet") if p.shelf == "specs" else dataset_part(p)
            slug, j = f"{mat.slug}/{part}", 2
            while slug in used_slugs:
                slug, j = f"{mat.slug}/{part}-{j}", j + 1
            used_slugs.add(slug)
            ds = Dataset(public_id=stable_id("ds", skey), slug=slug, material=mat, source="rii", source_key=skey,
                         license_id="CC0-1.0", current_version=0)
            session.add(ds)
            for alias in [f"{mat.slug}/{slugify(p.page)}",
                          *(f"{mat.slug}/{slugify(a)}" for a in _lib_aliases(library.get(path)))]:
                if alias not in used_slugs:
                    used_slugs.add(alias)
                    ds.aliases.append(DatasetAlias(slug=alias))
            stats.datasets_created += 1
        else:
            stats.datasets_updated += 1
        _describe(ds, mat, p, doc, blocks, disp, cond, flags, mat_key(p))
        ds.current_version += 1
        ds.versions.append(DatasetVersion(version=ds.current_version, blocks=blocks, meta=meta,
                                          references_html=doc.get("REFERENCES"), content_hash=chash,
                                          source_file_id=sf.id, source_commit=commit,
                                          change_note=f"imported from refractiveindex.info database at {commit[:10]}"))
        ds.reference_links.clear()
        session.flush()
        seen = set()
        for pos, r in enumerate(refs):
            ref = get_or_create(session, citation=r["citation"], doi=r["doi"], arxiv=r["arxiv"], url=r["url"],
                                citation_html=r["citation_html"], year=r["year"], title=r.get("title"),
                                authors_text=r.get("authors_text"))
            if ref.id not in seen:
                seen.add(ref.id)
                ds.reference_links.append(DatasetReference(reference=ref, position=pos, role=r["role"]))
        if db_ref.id not in seen:
            ds.reference_links.append(DatasetReference(reference=db_ref, position=len(refs), role="database"))
        datasets_by_path[path] = ds
        stats.by_data_type[ds.data_type] = stats.by_data_type.get(ds.data_type, 0) + 1
    session.flush()

    # materials with axis-resolved datasets
    for mat in materials.values():
        if any(d.optical_axis for d in mat.datasets) and "anisotropic" not in mat.tags:
            mat.tags = [*mat.tags, "anisotropic"]

    # withdrawn upstream (only decided on a full import)
    if not limit:
        for ds in session.scalars(select(Dataset).where(Dataset.source == "rii", Dataset.status == "published")):
            if ds.source_key.removeprefix("rii:") not in home:
                ds.status = "withdrawn"
                stats.datasets_withdrawn += 1

    # catalog entries -------------------------------------------------------------
    existing = {(c.shelf, c.book, c.page, c.listing): c
                for c in session.scalars(select(CatalogEntry).where(CatalogEntry.source == "rii"))}
    for p in pages:
        ds = datasets_by_path.get(p.data_path)
        if ds is None and limit:
            continue
        c = existing.get((p.shelf, p.book, p.page, p.listing))
        if c is None:
            c = CatalogEntry(source="rii", shelf=p.shelf, book=p.book, page=p.page, listing=p.listing)
            session.add(c)
        c.shelf_name, c.section, c.book_name, c.book_group = p.shelf_name, p.section, p.book_name, p.book_group
        c.page_name, c.page_group, c.data_path, c.position = p.page_name, p.page_group, p.data_path, p.position
        c.dataset_id = ds.id if ds else None
        c.material_id = ds.material_id if ds else None
        stats.catalog_entries += 1
    # books that only re-list other materials' data lend their names as aliases
    for p in pages:
        ds = datasets_by_path.get(p.data_path)
        if ds is None or p.shelf not in ("glass", "other", "organic") or mat_key(p) in materials:
            continue
        alias = rii.strip_html(p.book_name or p.book)
        if alias not in ds.material.aliases:
            ds.material.aliases = [*ds.material.aliases, alias]
            ds.material.search_text = search_text(*ds.material.search_text.split("|"), alias)
    session.flush()

    # DOI metadata ----------------------------------------------------------------
    if resolver is not None:
        stats.doi_lookups = enrich_references(session, resolver)

    stats.materials_total = session.scalar(select(func.count()).select_from(Material))
    stats.datasets_total = session.scalar(select(func.count()).where(Dataset.status == "published"))
    stats.references_total = session.scalar(select(func.count()).select_from(Reference))
    stats.references_with_doi = session.scalar(select(func.count()).where(Reference.doi.is_not(None)))
    stats.datasets_without_primary_reference = sum(
        1 for ds in datasets_by_path.values() if "no primary reference in the source file" in ds.quality_flags)
    run.status = "partial" if limit else "ok"
    run.stats = stats.as_dict()
    run.finished_at = datetime.now(timezone.utc)
    return stats


def _describe(ds: Dataset, mat: Material, p: Page, doc: dict, blocks: list, disp: Dispersion, cond: dict,
              flags: list[str], material_key: str) -> None:
    """Metadata derived from the source entry (refreshed on every import, versions or not)."""
    lo, hi = disp.range_nm
    ds.material = mat
    ds.name, ds.page, ds.group, ds.position = rii.strip_html(p.page_name or p.page), p.page, p.page_group, p.position
    ds.data_type, ds.has_k = rii.data_type(blocks), disp.has_k
    ds.wavelength_min_nm, ds.wavelength_max_nm = lo, hi
    ds.n_points = None if disp.is_formula else int(len(disp.native_nm()))
    ds.temperature_k, ds.conditions = _temperature(cond), cond
    ds.comments = doc.get("COMMENTS")
    ds.sample_notes = ", ".join(f"{k}: {v}" for k, v in cond.items() if k not in ("temperature", "temerature")
                                and isinstance(v, (str, int, float))) or None
    ds.quality_flags = flags
    axis, base = optical_axis(cond, p.page)
    ds.optical_axis = axis
    if axis is None:
        ds.axis_group = None
    elif p.shelf == "specs":  # one material per page: siblings are pages of the same supplier book
        ds.axis_group = f"{material_key.rsplit('/', 1)[0]}/{base}"
    else:
        ds.axis_group = f"{material_key}/{base}"
    ds.state = material_state(p.page, p.page_name, p.page_group, doc.get("COMMENTS"))
    ds.thermo_optic = thermo_optic_from_source(doc.get("PROPERTIES"), disp, ds.temperature_k)
    ds.status = "published"


def quality_flags(disp: Dispersion) -> list[str]:
    """Problems in the source data worth showing next to it (the data are kept as published)."""
    flags = []
    n, k = disp.nk(disp.sample_nm())
    if disp.has_n and not np.all(np.isfinite(n)):
        flags.append("formula gives non-finite n in part of its stated wavelength range")
    with np.errstate(invalid="ignore"):
        if disp.has_n and np.any(n <= 0):
            flags.append("n <= 0 at some wavelengths in the source data")
        if np.any(k < 0):
            flags.append("k < 0 at some wavelengths in the source data (gain, or a sign convention issue)")
    return flags + disp.notes


def _lib_aliases(lib: dict | None) -> list[str]:
    return [lib["dataset"], *lib["aliases"]] if lib else []


def enrich_references(session: Session, resolver: DoiResolver, *, refresh: bool = False,
                      progress=None) -> dict:
    """Fill title, authors, journal, year ... from DOI metadata for references that have a DOI."""
    q = select(Reference).where(Reference.doi.is_not(None))
    if not refresh:
        q = q.where(Reference.doi_checked_at.is_(None))
    refs = list(session.scalars(q))
    out = {"checked": 0, "found": 0, "not_found": 0, "errors": 0}
    for i, ref in enumerate(refs):
        if progress and i % 50 == 0:
            progress(i, len(refs))
        try:
            msg = resolver.resolve(ref.doi)
        except (httpx.HTTPError, ValueError) as e:
            log.warning("DOI lookup failed for %s: %s", ref.doi, e)
            out["errors"] += 1
            continue
        apply_metadata(ref, msg)
        out["checked"] += 1
        out["found" if msg else "not_found"] += 1
        if i % 100 == 0:
            session.flush()
    return out
