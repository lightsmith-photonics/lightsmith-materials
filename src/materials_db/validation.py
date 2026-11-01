"""Parsing and validation of contributed datasets.

A contribution is data (CSV, refractiveindex.info-style YAML, or inline JSON
rows) plus metadata. :func:`check_submission` returns the parsed blocks and a
report ``{"errors": [...], "warnings": [...], "stats": {...}}``; any error
blocks the submission, warnings go to the moderator.

Checks: declared units, at least two finite points, positive and strictly
monotonic wavelengths, n > 0, k >= 0 (and Im(eps) >= 0 for permittivity
input), spike/outlier detection, plausibility ranges, a cited source (DOI,
arXiv id or full citation), duplicate DOIs, an allowed license, and a valid
material.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import rii
from .models import CATEGORIES, Dataset, DatasetReference, License, Reference, Submission
from .naming import AXIS_LABELS
from .optics import HC_EV_NM, Dispersion
from .photonics import validate_thermo_optic
from .references import normalize_arxiv

UNIT_ALIASES = {
    "nm": "nm", "nanometer": "nm", "nanometers": "nm", "nanometre": "nm", "nanometres": "nm",
    "um": "um", "µm": "um", "μm": "um", "micron": "um", "microns": "um", "micrometer": "um", "micrometre": "um",
    "m": "m", "mm": "mm",
    "ev": "eV", "cm-1": "cm-1", "cm^-1": "cm-1", "1/cm": "cm-1", "wavenumber": "cm-1",
}
LICENSE_ALIASES = {"cc0": "CC0-1.0", "cc-0": "CC0-1.0", "public domain": "CC0-1.0", "cc-by": "CC-BY-4.0",
                   "cc by": "CC-BY-4.0", "cc-by-4": "CC-BY-4.0", "cc by 4.0": "CC-BY-4.0", "pddl": "PDDL-1.0"}
MAX_ROWS = 200_000
STATES = ("amorphous", "crystalline", "polycrystalline", "liquid", "gas", "solid")


class SubmissionError(ValueError):
    """Raised with a validation report when a submission cannot be accepted."""

    def __init__(self, report: dict):
        super().__init__("; ".join(report["errors"]))
        self.report = report


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"errors": self.errors, "warnings": self.warnings, "stats": self.stats}


def normalize_unit(u: str | None) -> str | None:
    if not u:
        return None
    return UNIT_ALIASES.get(str(u).strip().lower().replace(" ", ""))


def normalize_license(lic: str | None) -> str | None:
    if not lic:
        return None
    s = str(lic).strip()
    return LICENSE_ALIASES.get(s.lower(), s)


def to_um(x: np.ndarray, unit: str) -> np.ndarray:
    with np.errstate(divide="ignore"):
        return {"nm": x / 1000.0, "um": x, "m": x * 1e6, "mm": x * 1000.0, "eV": HC_EV_NM / x / 1000.0,
                "cm-1": 1e4 / x}[unit]


# --- parsing --------------------------------------------------------------------------

_X_NAMES = re.compile(r"^(wavelength|wl|lambda|λ|wave|energy|e|photon[_ ]?energy|wavenumber|nu|freq)", re.I)
_Q_NAMES = {
    "n": "n", "n_real": "n", "nreal": "n", "re(n)": "n", "k": "k", "n_imag": "k", "nimag": "k", "kappa": "k",
    "im(n)": "k", "eps1": "eps1", "e1": "eps1", "eps_real": "eps1", "re(eps)": "eps1", "epsilon1": "eps1",
    "eps2": "eps2", "e2": "eps2", "eps_imag": "eps2", "im(eps)": "eps2", "epsilon2": "eps2",
}


def _unit_in_header(name: str) -> str | None:
    m = re.search(r"[\(\[_ ]\s*(nm|um|µm|μm|ev|cm-1|cm\^-1|1/cm|mm|m)\s*[\)\]]?$", name.strip(), re.I)
    return normalize_unit(m.group(1)) if m else None


def _split(line: str) -> list[str]:
    if "," in line:
        return [c.strip() for c in next(csv.reader([line]))]
    if ";" in line:
        return [c.strip() for c in line.split(";")]
    return line.split()


def parse_table(text: str, columns: list[str] | None, unit: str | None, report: Report) -> dict | None:
    """CSV/TSV/whitespace table -> {"x": array, "unit": unit, "<quantity>": array, ...}."""
    rows, header = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "%", "//")):
            continue
        cells = _split(line)
        try:
            rows.append([float(c) for c in cells])
        except ValueError:
            if header is None and not rows:
                header = cells
                continue
            report.errors.append(f"line {raw[:60]!r}: non-numeric value")
            return None
        if len(rows) > MAX_ROWS:
            report.errors.append(f"too many rows (max {MAX_ROWS})")
            return None
    if not rows:
        report.errors.append("no numeric data rows found")
        return None
    width = {len(r) for r in rows}
    if len(width) != 1:
        report.errors.append(f"rows have different numbers of columns: {sorted(width)}")
        return None
    ncol = width.pop()
    names = columns or header
    if names is not None and len(names) != ncol:
        report.errors.append(f"{len(names)} column names for {ncol} data columns")
        return None
    if names is None:
        names = ["wavelength", "n", "k"][:ncol] if ncol <= 3 else None
        if names is None:
            report.errors.append("cannot guess the meaning of more than 3 columns; declare 'columns'")
            return None
        report.warnings.append(f"no header: columns assumed to be {', '.join(names)}")
    x_name = names[0]
    if not _X_NAMES.match(x_name.strip()) and x_name.strip().lower() not in ("x",):
        report.errors.append(f"first column {x_name!r} must be the wavelength, energy or wavenumber")
        return None
    unit = unit or _unit_in_header(x_name)
    if unit is None:
        report.errors.append("units not declared: give 'wavelength_unit' (nm, um, eV, cm-1) in the metadata "
                             "or put the unit in the header, e.g. 'wavelength_nm' or 'wavelength (um)'")
        return None
    arr = np.array(rows, dtype=float)
    out: dict[str, Any] = {"x": arr[:, 0], "unit": unit}
    for j, name in enumerate(names[1:], start=1):
        key = re.sub(r"\s*[\(\[].*$", "", name.strip().lower())
        q = _Q_NAMES.get(key)
        if q is None:
            report.errors.append(f"unknown column {name!r} (expected n, k, eps1, eps2)")
            return None
        out[q] = arr[:, j]
    return out


def parse_upload(content: bytes, filename: str | None, meta: dict, report: Report) -> list[dict] | None:
    """Uploaded file -> data blocks (wavelengths in µm, sorted ascending)."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        report.errors.append("file is not UTF-8 text")
        return None
    fmt = (meta.get("format") or "").lower()
    name = (filename or "").lower()
    if fmt in ("yaml", "yml") or name.endswith((".yml", ".yaml")):
        if meta.get("wavelength_unit") and normalize_unit(meta["wavelength_unit"]) != "um":
            report.warnings.append("YAML entries use micrometres by definition; 'wavelength_unit' ignored")
        report.stats["units_declared"] = "um (refractiveindex.info YAML format)"
        try:
            doc = rii.parse_yaml(text)
            blocks = rii.entry_blocks(doc if isinstance(doc, dict) else {})
        except (ValueError, KeyError, TypeError) as e:
            report.errors.append(f"YAML: {e}")
            return None
        if isinstance(doc, dict):
            meta.setdefault("comments", doc.get("COMMENTS"))
            if isinstance(doc.get("CONDITIONS"), dict):
                meta.setdefault("conditions", doc["CONDITIONS"])
            if doc.get("REFERENCES") and not (meta.get("reference") or meta.get("doi")):
                refs = rii.split_references(doc["REFERENCES"])
                if refs:
                    meta["doi"] = refs[0]["doi"]
                    meta["reference"] = refs[0]["citation"]
        for b in blocks:
            if "wavelength_um" in b:
                _check_block(b, report, energy_axis=False)
        return blocks if not report.errors else None
    cols = meta.get("columns")
    if isinstance(cols, str):
        cols = {"nk": ["wavelength", "n", "k"], "n": ["wavelength", "n"], "eps": ["wavelength", "eps1", "eps2"]}.get(
            cols.lower(), [c.strip() for c in cols.split(",")])
    unit = normalize_unit(meta.get("wavelength_unit") or meta.get("unit"))
    if (meta.get("wavelength_unit") or meta.get("unit")) and unit is None:
        report.errors.append(f"unknown wavelength unit {meta.get('wavelength_unit') or meta.get('unit')!r}")
        return None
    table = parse_table(text, cols, unit, report)
    if table is None:
        return None
    return table_to_blocks(table, report)


def rows_to_blocks(rows: list, report: Report) -> list[dict] | None:
    """Inline JSON rows ``[[wavelength_nm, n, k], ...]`` (or ``[wavelength_nm, n]``)."""
    try:
        arr = np.array(rows, dtype=float)
    except (TypeError, ValueError):
        report.errors.append("data must be a list of [wavelength_nm, n, k] rows of numbers")
        return None
    if arr.ndim != 2 or arr.shape[1] not in (2, 3):
        report.errors.append("data must be a list of [wavelength_nm, n, k] (or [wavelength_nm, n]) rows")
        return None
    if len(arr) > MAX_ROWS:
        report.errors.append(f"too many rows (max {MAX_ROWS})")
        return None
    table = {"x": arr[:, 0], "unit": "nm", "n": arr[:, 1]}
    if arr.shape[1] == 3:
        table["k"] = arr[:, 2]
    return table_to_blocks(table, report)


def table_to_blocks(t: dict, report: Report) -> list[dict] | None:
    unit = t["unit"]
    report.stats["units_declared"] = unit
    x = np.asarray(t["x"], float)
    if "eps1" in t or "eps2" in t:
        if not ("eps1" in t and "eps2" in t):
            report.errors.append("permittivity input needs both eps1 and eps2 columns")
            return None
        e1, e2 = np.asarray(t["eps1"], float), np.asarray(t["eps2"], float)
        if np.any(e2 < 0):
            report.errors.append(f"Im(eps) < 0 at {int(np.sum(e2 < 0))} points (exp(-iwt) convention: loss is "
                                 "Im(eps) > 0; conjugate data that use exp(+iwt))")
            return None
        mod = np.hypot(e1, e2)
        t = {"n": np.sqrt((mod + e1) / 2), "k": np.sqrt(np.maximum(mod - e1, 0) / 2)}
        report.stats["converted_from"] = "permittivity"
    if "n" not in t and "k" not in t:
        report.errors.append("no n, k or permittivity columns")
        return None
    wl = to_um(x, unit)
    energy_axis = unit in ("eV", "cm-1")
    block: dict[str, Any] = {"wavelength_um": wl}
    for q in ("n", "k"):
        if q in t:
            block[q] = np.asarray(t[q], float)
    block["type"] = "tabulated " + "".join(q for q in ("n", "k") if q in block)
    if not _check_block(block, report, energy_axis=energy_axis):
        return None
    order = np.argsort(block["wavelength_um"])
    out = {"type": block["type"], "wavelength_um": block["wavelength_um"][order].tolist()}
    for q in ("n", "k"):
        if q in block:
            out[q] = np.asarray(block[q])[order].tolist()
    return [out]


def _check_block(b: dict, report: Report, energy_axis: bool) -> bool:
    wl = np.asarray(b["wavelength_um"], float)
    label = b.get("type", "data")
    n0 = len(report.errors)
    if len(wl) < 2:
        report.errors.append(f"{label}: at least two data points are needed")
        return False
    arrays = {"wavelength": wl, **{q: np.asarray(b[q], float) for q in ("n", "k") if q in b}}
    for name, a in arrays.items():
        if not np.all(np.isfinite(a)):
            report.errors.append(f"{label}: non-finite {name} values at rows {_rows(~np.isfinite(a))}")
    if report.errors[n0:]:
        return False
    if np.any(wl <= 0):
        report.errors.append(f"{label}: wavelengths must be positive (rows {_rows(wl <= 0)})")
    d = np.diff(wl)
    if np.any(d == 0):
        report.errors.append(f"{label}: duplicate wavelengths at rows {_rows(np.concatenate([[False], d == 0]))}")
    elif not (np.all(d > 0) or np.all(d < 0)):
        report.errors.append(f"{label}: wavelengths are not monotonic (first reversal at row "
                             f"{int(np.argmax(np.sign(d) != np.sign(d[0]))) + 2})")
    elif np.all(d < 0) and not energy_axis:
        report.warnings.append(f"{label}: wavelengths are descending; the data were sorted")
    if "n" in arrays and np.any(arrays["n"] <= 0):
        report.errors.append(f"{label}: n must be positive (rows {_rows(arrays['n'] <= 0)})")
    if "k" in arrays and np.any(arrays["k"] < 0):
        report.errors.append(f"{label}: k must be >= 0 (rows {_rows(arrays['k'] < 0)}); the convention is "
                             "exp(-iwt), where absorption means k > 0")
    if report.errors[n0:]:
        return False
    order = np.argsort(wl)
    for q in ("n", "k"):
        if q in arrays:
            idx = spikes(arrays[q][order])
            if idx:
                report.warnings.append(f"{label}: possible outliers in {q} at {', '.join(f'{wl[order][i] * 1000:g} nm' for i in idx[:10])}"
                                       + (f" (+{len(idx) - 10} more)" if len(idx) > 10 else ""))
    if "n" in arrays and np.max(arrays["n"]) > 20:
        report.warnings.append(f"{label}: n up to {np.max(arrays['n']):g} is unusually large; check units/columns")
    if "k" in arrays and np.max(arrays["k"]) > 500:
        report.warnings.append(f"{label}: k up to {np.max(arrays['k']):g} is unusually large; check units/columns")
    lo, hi = wl.min() * 1000, wl.max() * 1000
    if lo < 1 or hi > 1e7:
        report.warnings.append(f"{label}: wavelength range {lo:g}-{hi:g} nm is unusual; check the unit")
    report.stats.update({"points": int(len(wl)), "range_nm": [float(lo), float(hi)]})
    return True


def _rows(mask: np.ndarray) -> str:
    idx = np.flatnonzero(mask)[:10] + 1
    return ", ".join(map(str, idx)) + (" ..." if np.count_nonzero(mask) > 10 else "")


def spikes(y: np.ndarray, window: int = 3, threshold: float = 8.0) -> list[int]:
    """Indices of isolated points far from the median of their neighbours (robust z-score)."""
    y = np.asarray(y, float)
    if len(y) < 2 * window + 1:
        return []
    res = np.empty_like(y)
    for i in range(len(y)):
        nb = np.concatenate([y[max(0, i - window):i], y[i + 1:i + 1 + window]])
        res[i] = y[i] - np.median(nb)
    mad = 1.4826 * np.median(np.abs(res - np.median(res)))
    scale = mad + 1e-3 * np.median(np.abs(y)) + 1e-12
    out = []
    for i in np.flatnonzero(np.abs(res) > threshold * scale):
        nb = np.concatenate([y[max(0, i - window):i], y[i + 1:i + 1 + window]])
        if abs(res[i]) > 0.1 * max(np.max(np.abs(nb)), 1e-6):  # ignore tiny wiggles on small values
            out.append(int(i))
    return out


# --- metadata checks ------------------------------------------------------------------

def check_metadata(session: Session, meta: dict, report: Report, *, material, target: Dataset | None) -> None:
    doi = meta.get("doi")
    arxiv = meta.get("arxiv")
    citation = (meta.get("reference") or meta.get("citation") or "").strip()
    if doi and rii.normalize_doi(doi) is None:
        report.errors.append(f"{doi!r} is not a valid DOI (expected 10.xxxx/...)")
    if arxiv and normalize_arxiv(arxiv) is None:
        report.errors.append(f"{arxiv!r} is not a valid arXiv identifier")
    if not (doi or arxiv or len(citation) >= 10):
        report.errors.append("every dataset must cite its source: give a DOI, an arXiv id, or a full citation "
                             "('reference')")
    lic = normalize_license(meta.get("license"))
    if not lic:
        report.errors.append("a license is required (e.g. CC0-1.0 or CC-BY-4.0)")
    else:
        row = session.get(License, lic)
        allowed = [x.id for x in session.scalars(select(License).where(License.allowed_for_contributions))]
        if row is None or not row.allowed_for_contributions:
            report.errors.append(f"license {meta.get('license')!r} is not accepted; use one of {', '.join(allowed)}")
        else:
            meta["license"] = lic
    if target is None and material is None:
        name = (meta.get("material_name") or "").strip()
        cat = (meta.get("category") or "").strip().lower()
        if not name:
            report.errors.append("give material_id (an existing material) or material_name for a new material")
        if cat and cat not in CATEGORIES:
            report.errors.append(f"unknown category {cat!r}; use one of {', '.join(CATEGORIES)}")
        elif not cat:
            report.warnings.append("no category given for the new material; 'other' will be used")
    if not (meta.get("dataset_name") or meta.get("name")) and target is None:
        report.warnings.append("no dataset_name given; one will be derived from the reference")
    if meta.get("optical_axis") is not None:
        ax = str(meta["optical_axis"]).strip().lower()
        if ax not in AXIS_LABELS:
            report.errors.append(f"optical_axis must be one of {', '.join(AXIS_LABELS)}")
        else:
            meta["optical_axis"] = ax
    if meta.get("state") is not None and meta["state"] not in STATES:
        report.errors.append(f"state must be one of {', '.join(STATES)}")
    if meta.get("thermo_optic") is not None:
        to, errs = validate_thermo_optic(meta["thermo_optic"])
        report.errors.extend(errs)
        meta["thermo_optic"] = to
    if meta.get("temperature_k") is not None:
        try:
            t = float(meta["temperature_k"])
            if not 0 < t < 5000:
                report.errors.append(f"temperature_k {t:g} is out of range")
        except (TypeError, ValueError):
            report.errors.append("temperature_k must be a number (kelvin)")
    nd = rii.normalize_doi(doi) if doi else None
    if nd:
        hits = session.execute(
            select(Dataset.slug, Dataset.material_id).join(DatasetReference, DatasetReference.dataset_id == Dataset.id)
            .join(Reference, Reference.id == DatasetReference.reference_id)
            .where(Reference.doi == nd, Dataset.status == "published")).all()
        mat_id = target.material_id if target is not None else (material.id if material is not None else None)
        same = [s for s, m in hits if m == mat_id]
        other = [s for s, m in hits if m != mat_id]
        if same:
            report.warnings.append(f"possible duplicate: DOI {nd} is already cited by {', '.join(same[:5])} "
                                   "for this material")
        if other:
            report.warnings.append(f"DOI {nd} is also cited by {', '.join(other[:5])}")
        pending = session.scalars(select(Submission.public_id).join(Reference, Reference.id == Submission.reference_id)
                                  .where(Reference.doi == nd, Submission.status == "pending")).all()
        if pending:
            report.warnings.append(f"pending submissions with the same DOI: {', '.join(pending[:5])}")
        report.stats["duplicate_doi"] = bool(same)


def summarize_blocks(blocks: list[dict], report: Report) -> None:
    try:
        d = Dispersion(blocks)
    except ValueError as e:
        report.errors.append(str(e))
        return
    lo, hi = d.range_nm
    report.stats.update({"range_nm": [lo, hi], "has_k": d.has_k, "data_type": rii.data_type(blocks)})
    if d.is_formula:
        n, _ = d.nk(d.sample_nm())
        if not np.all(np.isfinite(n)):
            report.errors.append("formula gives non-finite n inside its stated wavelength range")
    report.warnings.extend(d.notes)
