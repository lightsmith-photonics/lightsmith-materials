"""References: de-duplication and DOI metadata (Crossref, then doi.org content negotiation)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Reference
from .naming import stable_id
from .rii import normalize_doi

log = logging.getLogger(__name__)

CROSSREF_URL = "https://api.crossref.org/works/{doi}"
DOI_URL = "https://doi.org/{doi}"
_ARXIV_ID = re.compile(r"^(\d{4}\.\d{4,5}|[a-z\-]+(\.[A-Z]{2})?/\d{7})(v\d+)?$", re.I)


def normalize_arxiv(a: str | None) -> str | None:
    if not a:
        return None
    a = re.sub(r"^(https?://)?arxiv\.org/(abs|pdf)/", "", str(a).strip(), flags=re.I).removeprefix("arXiv:")
    a = a.removesuffix(".pdf")
    return a if _ARXIV_ID.match(a) else None


def text_hash(doi: str | None, citation: str) -> str:
    key = f"doi:{doi}" if doi else "text:" + re.sub(r"\s+", " ", citation.strip().lower())
    return hashlib.sha256(key.encode()).hexdigest()


def _authors_from_text(s: str | None) -> list[dict]:
    if not s:
        return []
    parts = [p.strip() for p in re.split(r",\s*|\s+and\s+", s) if p.strip() and p.strip() != "et al."]
    out = []
    for p in parts:
        bits = p.split()
        out.append({"given": " ".join(bits[:-1]) or None, "family": bits[-1]} if bits else {"literal": p})
    return out


def get_or_create(session: Session, *, citation: str, doi: str | None = None, arxiv: str | None = None,
                  url: str | None = None, citation_html: str | None = None, year: int | None = None,
                  title: str | None = None, authors_text: str | None = None,
                  metadata_source: str = "source-text") -> Reference:
    doi = normalize_doi(doi)
    arxiv = normalize_arxiv(arxiv)
    if doi is None and arxiv:
        doi = f"10.48550/arxiv.{arxiv.lower()}" if "/" not in arxiv else None
    ref = None
    if doi:
        ref = session.scalar(select(Reference).where(Reference.doi == doi))
    h = text_hash(doi, citation)
    if ref is None:
        ref = session.scalar(select(Reference).where(Reference.text_hash == h))
    if ref is None:
        ref = Reference(public_id=stable_id("ref", h), doi=doi, arxiv=arxiv, citation=citation,
                        citation_html=citation_html, text_hash=h, url=url or (f"https://doi.org/{doi}" if doi else None),
                        year=year, title=title, authors=_authors_from_text(authors_text),
                        metadata_source=metadata_source)
        session.add(ref)
        session.flush()
    else:
        # a fuller citation for a DOI first seen as a bare link
        if len(citation) > len(ref.citation or "") and ref.metadata_source == "source-text":
            ref.citation, ref.citation_html = citation, citation_html or ref.citation_html
        ref.year = ref.year or year
        ref.title = ref.title or title
        ref.arxiv = ref.arxiv or arxiv
        if not ref.authors and authors_text:
            ref.authors = _authors_from_text(authors_text)
    return ref


# --- DOI metadata -----------------------------------------------------------------

class DoiResolver:
    """Fetches DOI metadata with an on-disk cache (also caches "not found").

    Crossref first (api.crossref.org), then doi.org CSL-JSON content negotiation,
    which also covers DataCite DOIs such as arXiv's 10.48550/*."""

    def __init__(self, cache_dir: Path | None, mailto: str | None = None, timeout: float = 15.0,
                 min_interval: float = 0.2, client: httpx.Client | None = None):
        self.cache_dir = cache_dir
        ua = "lightsmith-materials/0.1 (DOI metadata for an open optical-constants database)"
        if mailto:
            ua += f" mailto:{mailto}"
        self.client = client or httpx.Client(timeout=timeout, headers={"User-Agent": ua}, follow_redirects=True)
        self.min_interval = min_interval
        self._last = 0.0

    def _cache_path(self, doi: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / f"{hashlib.sha1(doi.encode()).hexdigest()}.json"

    def _get(self, url: str, headers: dict | None = None) -> httpx.Response:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        r = self.client.get(url, headers=headers)
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", "5") or 5))
            r = self.client.get(url, headers=headers)
        return r

    def resolve(self, doi: str) -> dict | None:
        """CSL-like metadata dict, or None when the DOI is unknown. Raises on network failure."""
        doi = normalize_doi(doi) or doi
        path = self._cache_path(doi)
        if path is not None and path.is_file():
            cached = json.loads(path.read_text(encoding="utf-8"))
            return cached.get("message")
        quoted = urllib.parse.quote(doi, safe="/()")
        msg, source = None, None
        r = self._get(CROSSREF_URL.format(doi=quoted))
        if r.status_code == 200:
            msg, source = r.json().get("message"), "crossref"
        elif r.status_code == 404:
            r2 = self._get(DOI_URL.format(doi=quoted), headers={"Accept": "application/vnd.citationstyles.csl+json"})
            if r2.status_code == 200 and "json" in r2.headers.get("content-type", ""):
                msg, source = r2.json(), "datacite"
            elif r2.status_code not in (404, 406):
                r2.raise_for_status()
        else:
            r.raise_for_status()
        if msg is not None:
            msg = _trim(msg)
            msg["_source"] = source
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"doi": doi, "message": msg}, ensure_ascii=False), encoding="utf-8")
            except OSError as e:  # e.g. a read-only container filesystem: the cache is only an optimisation
                log.warning("cannot write DOI cache %s: %s", path, e)
        return msg


_KEEP = ("DOI", "URL", "type", "title", "author", "container-title", "short-container-title", "issued",
         "published-print", "published-online", "volume", "issue", "page", "article-number", "publisher")


def _trim(msg: dict) -> dict:
    out = {k: msg[k] for k in _KEEP if k in msg}
    if "author" in out:
        out["author"] = [{k: a[k] for k in ("given", "family", "name", "literal", "ORCID") if k in a}
                         for a in out["author"][:100]]
    return out


def _first(v):
    return (v[0] if v else None) if isinstance(v, list) else v


def _year(msg: dict) -> int | None:
    for key in ("issued", "published-print", "published-online"):
        parts = (msg.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            return int(parts[0][0])
    return None


def apply_metadata(ref: Reference, msg: dict | None) -> None:
    ref.doi_checked_at = datetime.now(timezone.utc)
    if not msg:
        return
    ref.doi_metadata = msg
    ref.metadata_source = msg.get("_source", "crossref")
    ref.title = _first(msg.get("title")) or ref.title
    authors = [{"given": a.get("given"), "family": a.get("family") or a.get("name") or a.get("literal")}
               for a in msg.get("author") or []]
    if authors:
        ref.authors = authors
    ref.journal = _first(msg.get("container-title")) or ref.journal
    ref.year = _year(msg) or ref.year
    ref.volume = str(msg["volume"]) if msg.get("volume") else ref.volume
    ref.issue = str(msg["issue"]) if msg.get("issue") else ref.issue
    ref.pages = str(msg.get("page") or msg.get("article-number") or "") or ref.pages
    ref.publisher = msg.get("publisher") or ref.publisher


def format_citation(ref: Reference) -> str:
    """Citation built from DOI metadata; the source's own text when there is none."""
    if ref.metadata_source not in ("crossref", "datacite") or not (ref.title and ref.authors):
        return ref.citation
    names = [" ".join(x for x in (a.get("given"), a.get("family")) if x) for a in ref.authors]
    who = names[0] if len(names) == 1 else (", ".join(names[:-1]) + " and " + names[-1] if len(names) <= 5
                                            else f"{names[0]} et al.")
    tail = ", ".join(x for x in (ref.journal, ref.volume and str(ref.volume), ref.pages) if x)
    s = f"{who}. {ref.title}. {tail}" + (f" ({ref.year})" if ref.year else "")
    return s + (f", doi:{ref.doi}" if ref.doi else "")
