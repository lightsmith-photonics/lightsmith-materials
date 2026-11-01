"""refractiveindex.info-format YAML: parsing, dispersion formulas and export.

The parser and formulas are ported from ``lightsmith.materials`` (that module
is the reference implementation; keep the two in step). This copy differs in
what it returns: the raw *blocks* of an entry, losslessly, instead of a
resampled n, k table, so the database keeps exactly what the source said.

Units follow the source format: wavelengths in micrometres.
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
from typing import Any

import numpy as np

FORMULA_NAMES = {
    1: "Sellmeier", 2: "Sellmeier-2", 3: "Polynomial", 4: "RefractiveIndex.INFO", 5: "Cauchy",
    6: "Gases", 7: "Herzberger", 8: "Retro", 9: "Exotic",
}


def yaml_scalar(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        q = s[0]
        s = s[1:-1]
        s = s.replace("''", "'") if q == "'" else s.replace('\\"', '"')
    return s


def parse_yaml(text: str) -> Any:
    """Parse the YAML subset used by refractiveindex.info files (catalogs and entries).

    Supports nested mappings, block literals (``|``), lists of mappings and
    plain/quoted scalars, which covers every file in the database."""
    toks: list[list] = []
    for raw in text.replace("\t", "    ").splitlines():
        s = raw.strip()
        if s and not s.startswith("#"):
            toks.append([len(raw) - len(raw.lstrip(" ")), s])

    def node(i: int, indent: int):
        if toks[i][1].startswith("- ") or toks[i][1] == "-":
            out = []
            while i < len(toks) and toks[i][0] == indent and toks[i][1].startswith("-"):
                rest = toks[i][1][1:].strip()
                if not rest:
                    item, i = node(i + 1, toks[i + 1][0])
                else:
                    toks[i] = [indent + 2, rest]
                    item, i = node(i, indent + 2) if ":" in rest else (yaml_scalar(rest), i + 1)
                out.append(item)
            return out, i
        out: dict[str, Any] = {}
        while i < len(toks) and toks[i][0] == indent:
            key, sep, rest = toks[i][1].partition(":")
            if not sep:
                raise ValueError(f"unparseable YAML line: {toks[i][1]!r}")
            rest, i = rest.strip(), i + 1
            if rest[:1] in ("|", ">"):
                lines = []
                while i < len(toks) and toks[i][0] > indent:
                    lines.append(toks[i][1])
                    i += 1
                out[key.strip()] = ("\n" if rest[0] == "|" else " ").join(lines)
            elif rest:
                out[key.strip()] = yaml_scalar(rest)
            elif i < len(toks) and (toks[i][0] > indent or (toks[i][0] == indent and toks[i][1].startswith("-"))):
                out[key.strip()], i = node(i, toks[i][0])
            else:
                out[key.strip()] = None
        return out, i

    if not toks:
        raise ValueError("empty YAML document")
    doc, _ = node(0, toks[0][0])
    return doc


def floats(s) -> list[float]:
    return [float(x) for x in str(s).replace(",", " ").split()]


def formula_n(num: int, c, lam) -> np.ndarray:
    """Refractive index n(lam) for refractiveindex.info formula types 1-9 (lam in µm)."""
    c = np.asarray(c, dtype=float)
    lam = np.asarray(lam, dtype=float)
    c = np.concatenate([c, np.zeros(max(0, 17 - len(c)))])
    l2 = lam**2
    pairs = range(1, len(c) - 1, 2)
    with np.errstate(all="ignore"):
        if num == 1:  # Sellmeier
            n2 = 1 + c[0] + sum(c[i] * l2 / (l2 - c[i + 1] ** 2) for i in pairs if c[i] != 0)
        elif num == 2:  # Sellmeier-2
            n2 = 1 + c[0] + sum(c[i] * l2 / (l2 - c[i + 1]) for i in pairs if c[i] != 0)
        elif num == 3:  # polynomial
            n2 = c[0] + sum(c[i] * lam ** c[i + 1] for i in pairs if c[i] != 0)
        elif num == 4:  # RefractiveIndex.INFO
            n2 = c[0] + 0 * lam
            if c[1] != 0:
                n2 = n2 + c[1] * lam ** c[2] / (l2 - c[3] ** c[4])
            if c[5] != 0:
                n2 = n2 + c[5] * lam ** c[6] / (l2 - c[7] ** c[8])
            n2 = n2 + sum(c[i] * lam ** c[i + 1] for i in range(9, len(c) - 1, 2) if c[i] != 0)
        elif num == 5:  # Cauchy
            return c[0] + sum(c[i] * lam ** c[i + 1] for i in pairs if c[i] != 0) + 0 * lam
        elif num == 6:  # gases
            return 1 + c[0] + sum(c[i] / (c[i + 1] - lam**-2) for i in pairs if c[i] != 0) + 0 * lam
        elif num == 7:  # Herzberger
            d = 1 / (l2 - 0.028)
            return c[0] + c[1] * d + c[2] * d**2 + c[3] * l2 + c[4] * l2**2 + c[5] * l2**3
        elif num == 8:  # Retro
            x = c[0] + c[1] * l2 / (l2 - c[2]) + c[3] * l2
            n2 = (1 + 2 * x) / (1 - x)
        elif num == 9:  # Exotic
            n2 = c[0] + c[1] / (l2 - c[2]) + c[3] * (lam - c[4]) / ((lam - c[4]) ** 2 + c[5])
        else:
            raise ValueError(f"unsupported refractiveindex.info formula type {num}")
        return np.sqrt(n2 + 0 * lam)


def entry_blocks(doc: dict) -> list[dict]:
    """The DATA blocks of a parsed entry, as stored in ``DatasetVersion.blocks``.

    Rows are kept in source order and at full float64 precision."""
    blocks = []
    for entry in doc.get("DATA") or []:
        typ = " ".join(str(entry.get("type", "")).split())
        kind = typ.split()
        if kind[:1] == ["tabulated"]:
            what = kind[1] if len(kind) > 1 else "nk"
            if what not in ("nk", "n", "k"):
                raise ValueError(f"unsupported data type {typ!r}")
            rows = [floats(r) for r in str(entry.get("data") or "").splitlines() if r.strip()]
            width = 3 if what == "nk" else 2
            bad = [r for r in rows if len(r) != width]
            if bad:
                raise ValueError(f"{typ}: expected {width} columns, got row {bad[0]}")
            if not rows:
                raise ValueError(f"{typ}: no data rows")
            block: dict[str, Any] = {"type": f"tabulated {what}", "wavelength_um": [r[0] for r in rows]}
            if what in ("nk", "n"):
                block["n"] = [r[1] for r in rows]
            if what == "nk":
                block["k"] = [r[2] for r in rows]
            if what == "k":
                block["k"] = [r[1] for r in rows]
            blocks.append(block)
        elif kind[:1] == ["formula"]:
            num = int(kind[1])
            if num not in FORMULA_NAMES:
                raise ValueError(f"unsupported formula type {num}")
            rng = floats(entry.get("wavelength_range") or entry.get("range") or "")
            if len(rng) < 2:
                raise ValueError("formula without wavelength_range")
            blocks.append({"type": f"formula {num}", "formula": num, "coefficients": floats(entry["coefficients"]),
                           "range_um": rng[:2]})
        else:
            raise ValueError(f"unsupported data type {typ!r}")
    if not blocks:
        raise ValueError("entry has no DATA blocks")
    return blocks


def data_type(blocks: list[dict]) -> str:
    return " + ".join(b["type"] for b in blocks)


# --- references -----------------------------------------------------------------

def strip_html(s: str | None) -> str:
    s = re.sub(r"<br\s*/?>", "; ", s or "")
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    return re.sub(r"\s+", " ", s).strip(" ;")


_DOI_RE = re.compile(r"doi\.org/([^\"'<>\s]+)", re.I)
_ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/[0-9]{7})", re.I)
_HREF_RE = re.compile(r"href=[\"']([^\"']+)[\"']", re.I)
_YEAR_RE = re.compile(r"\((1[5-9]\d\d|20\d\d)\)")


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = urllib.parse.unquote(str(doi)).strip()
    d = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", d, flags=re.I).removeprefix("doi:").strip()
    d = d.rstrip(".,;)")
    return d.lower() if re.match(r"^10\.\d{4,9}/\S+$", d) else None


def split_references(refs_html: str | None) -> list[dict]:
    """Split a REFERENCES block into citations.

    Each result has ``citation`` (plain text), ``citation_html``, ``doi``,
    ``arxiv``, ``url``, ``year`` and, for the usual three-line layout
    (authors / title / journal link), ``authors_text`` and ``title``.
    Chunks that only mention linked data sources ("fit of data from X and Y")
    yield one ``related`` entry per DOI; plain notes are skipped."""
    out: list[dict] = []
    if not refs_html:
        return out
    for chunk in re.split(r"<br\s*/?>", refs_html):
        chunk = chunk.strip()
        text = strip_html(chunk)
        text = re.sub(r"^\d+\)\s*", "", text)
        if not text:
            continue
        dois = [d for d in (normalize_doi(x) for x in _DOI_RE.findall(chunk)) if d]
        arx = _ARXIV_RE.findall(chunk)
        hrefs = _HREF_RE.findall(chunk)
        year_m = _YEAR_RE.findall(chunk)
        lines = [strip_html(x) for x in chunk.splitlines() if strip_html(x)]
        lines = [re.sub(r"^\d+\)\s*", "", x) for x in lines]
        full = bool(year_m) or len(lines) >= 3 or (len(dois) <= 1 and len(text) > 40)
        if len(dois) > 1 and not full:
            # "fit of data from <a doi>Smith-1931</a> and <a doi>Jones-1973</a>"
            for m in re.finditer(r"<a[^>]*doi\.org/([^\"'<>\s]+)[^>]*>(.*?)</a>", chunk, re.I | re.S):
                d = normalize_doi(m.group(1))
                if d:
                    out.append({"citation": strip_html(m.group(2)) or d, "citation_html": m.group(0), "doi": d,
                                "arxiv": None, "url": f"https://doi.org/{d}", "year": None, "role": "related"})
            continue
        if not (dois or arx or hrefs or year_m):
            continue  # a note such as "* Ref. 2 provides a dispersion formula based on data from Ref. 1"
        item = {"citation": text, "citation_html": chunk, "doi": dois[0] if dois else None,
                "arxiv": arx[0] if arx else None, "url": hrefs[0] if hrefs else None,
                "year": int(year_m[-1]) if year_m else None, "role": "source"}
        if len(lines) >= 3:
            item["authors_text"] = lines[0].rstrip(".")
            item["title"] = lines[1].rstrip(".")
        out.append(item)
        for d in dois[1:]:  # extra DOIs in a full citation (e.g. an erratum)
            out.append({"citation": f"doi:{d}", "citation_html": None, "doi": d, "arxiv": None,
                        "url": f"https://doi.org/{d}", "year": None, "role": "related"})
    return out


# --- export -----------------------------------------------------------------------

def _block_literal(text: str, indent: int) -> str:
    pad = " " * indent
    return "|\n" + "\n".join(pad + line for line in text.splitlines()) + "\n"


def dump_entry(references_html: str | None, comments: str | None, rows: list[tuple[float, float, float]],
               conditions: dict | None = None, header: list[str] | None = None) -> str:
    """refractiveindex.info-style YAML with one ``tabulated nk`` block (wavelengths in µm)."""
    out = [f"# {h}\n" for h in header or []]
    if references_html:
        out.append("REFERENCES: " + _block_literal(references_html, 4))
    if comments:
        out.append("COMMENTS: " + _block_literal(comments, 4))
    out.append("DATA:\n  - type: tabulated nk\n    data: |\n")
    out.extend(f"        {w!r} {n!r} {k!r}\n" for w, n, k in rows)
    simple = {k: v for k, v in (conditions or {}).items() if isinstance(v, (int, float, str)) and v != ""}
    if simple:
        out.append("CONDITIONS:\n")
        out.extend(f"    {k}: {json.dumps(v, ensure_ascii=False) if isinstance(v, str) else v}\n"
                   for k, v in simple.items())
    return "".join(out)
