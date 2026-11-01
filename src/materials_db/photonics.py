"""Photonics metadata: thermo-optic coefficients and dispersive (FDTD/FDFD-ready) models.

Dispersive models use the library's ``drude_lorentz`` material format:
eps(w) = eps_inf - wp^2/(w^2 + i g w) + sum_j d_j w0_j^2 / (w0_j^2 - w^2 - i G_j w), energies in eV.

A Sellmeier formula is exactly such a model with undamped poles:
B lam^2 / (lam^2 - C^2) = B w0^2 / (w0^2 - w^2) with w0 = hc / C. :func:`sellmeier_as_drude_lorentz`
rewrites refractiveindex.info formulas 1 and 2 that way, losslessly. Other datasets can be fitted
with the library's ``materials_fit.fit_drude_lorentz`` (see :func:`library_module`).
"""

from __future__ import annotations

import importlib
import math
from types import ModuleType

import numpy as np

from .optics import HC_EV_NM, Dispersion
from .rii import floats

# Python packages that provide the library (the product is being renamed; both names are tried)
LIBRARY_PACKAGES = ("lightsmith", "omniscatter")
REFERENCE_WAVELENGTHS_NM = (633.0, 1310.0, 1550.0)


def library_module(name: str) -> ModuleType | None:
    """``<library>.<name>`` (e.g. ``materials_fit``) from the first installed library package, or None."""
    for pkg in LIBRARY_PACKAGES:
        try:
            return importlib.import_module(f"{pkg}.{name}")
        except ImportError:
            continue
    return None


# --- thermo-optic coefficients ----------------------------------------------------------

def _num(v) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _wavelength_nm(v) -> float | None:
    """Source wavelengths are in µm; a few are written in metres (e.g. 0.6e-6)."""
    x = _num(v)
    if x is None or x <= 0:
        return None
    return x * 1e9 if x < 1e-3 else x * 1000.0


def schott_dn_dT(disp: Dispersion, coeffs: list[float], wavelength_nm: float) -> float | None:
    """Absolute dn/dT (1/K) at the reference temperature from the Schott formula
    dn/dT = (n^2 - 1)/(2n) * (D0 + E0 / (lam^2 - lam_TK^2)), lam in µm."""
    d0, _d1, _d2, e0, _e1, ltk = (coeffs + [0.0] * 6)[:6]
    n, _ = disp.nk([wavelength_nm])
    n = float(n[0])
    lam = wavelength_nm / 1000.0
    if not math.isfinite(n) or n <= 0 or abs(lam**2 - ltk**2) < 1e-12:
        return None
    return (n * n - 1) / (2 * n) * (d0 + e0 / (lam**2 - ltk**2))


def thermo_optic_from_source(properties: dict | None, disp: Dispersion, temperature_k: float | None) -> list[dict] | None:
    """Normalised thermo-optic data from a source entry's PROPERTIES.thermal_dispersion.

    Each item has ``dn_dT`` (1/K) or ``model`` + ``coefficients`` (+ ``dn_dT_at`` evaluated at
    633/1310/1550 nm where the data cover them), and optionally ``wavelength_nm``,
    ``wavelength_range_nm``, ``temperature_k``, ``direction``."""
    out = []
    for b in (properties or {}).get("thermal_dispersion") or []:
        if not isinstance(b, dict):
            continue
        typ = str(b.get("type", "")).strip()
        item: dict = {}
        if typ == "formula A":
            c = floats(b.get("coefficients") or "")
            if len(c) < 6:
                continue
            item = {"model": "schott", "coefficients": dict(zip(("D0", "D1", "D2", "E0", "E1", "lambda_tk_um"), c)),
                    "reference_temperature_k": temperature_k or 293.15,
                    "description": "Schott formula: dn_abs/dT = (n^2-1)/(2n) * (D0 + 2 D1 dT + 3 D2 dT^2 + "
                                   "(E0 + 2 E1 dT)/(lam^2 - lam_TK^2)), lam in um"}
            lo, hi = disp.range_nm
            at = {f"{w:g}": schott_dn_dT(disp, c, w) for w in REFERENCE_WAVELENGTHS_NM if lo <= w <= hi}
            item["dn_dT_at"] = {k: v for k, v in at.items() if v is not None}
        elif typ in ("constant", "dn/dT"):
            v = _num(b.get("value"))
            if v is None:
                continue
            item = {"dn_dT": v}
        else:
            continue
        if b.get("wavelength") is not None and _wavelength_nm(b["wavelength"]):
            item["wavelength_nm"] = _wavelength_nm(b["wavelength"])
        if b.get("wavelength_range"):
            r = [x * 1000.0 for x in floats(b["wavelength_range"])[:2]]
            if len(r) == 2:
                item["wavelength_range_nm"] = r
        if _num(b.get("temperature")) is not None:
            item["temperature_k"] = _num(b["temperature"])
        if b.get("direction"):
            item["direction"] = str(b["direction"])
        item["origin"] = "source"
        out.append(item)
    return out or None


def validate_thermo_optic(items) -> tuple[list[dict] | None, list[str]]:
    """Contributed thermo-optic data: a list of {dn_dT, wavelength_nm?, temperature_k?, direction?}."""
    if items in (None, [], {}):
        return None, []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return None, ["thermo_optic must be a list of {dn_dT, wavelength_nm, temperature_k}"]
    out, errors = [], []
    for i, it in enumerate(items):
        v = _num(it.get("dn_dT")) if isinstance(it, dict) else None
        if v is None:
            errors.append(f"thermo_optic[{i}]: dn_dT (1/K) is required")
            continue
        if abs(v) > 1e-2:
            errors.append(f"thermo_optic[{i}]: |dn_dT| = {abs(v):g} /K is implausible (expected < 1e-2)")
            continue
        item = {"dn_dT": v, "origin": "contributor"}
        for key in ("wavelength_nm", "temperature_k"):
            if it.get(key) is not None:
                x = _num(it[key])
                if x is None or x <= 0:
                    errors.append(f"thermo_optic[{i}]: {key} must be a positive number")
                else:
                    item[key] = x
        if it.get("direction"):
            item["direction"] = str(it["direction"])
        out.append(item)
    return (out or None), errors


# --- dispersive models -------------------------------------------------------------------

def sellmeier_as_drude_lorentz(blocks: list[dict]) -> tuple[dict, list[str]] | None:
    """Exact ``drude_lorentz`` rewrite of a Sellmeier (formula 1 or 2) dataset, or None.

    Only for datasets whose n comes from the formula; any tabulated k is lossy information the
    undamped model cannot carry, so it is reported in the notes."""
    f = [b for b in blocks if b["type"].startswith("formula")]
    if len(f) != 1 or f[0]["formula"] not in (1, 2):
        return None
    b = f[0]
    c = list(b["coefficients"]) + [0.0] * max(0, 17 - len(b["coefficients"]))
    eps_inf = 1.0 + c[0]
    poles = []
    for i in range(1, len(c) - 1, 2):
        strength, cc = c[i], c[i + 1]
        if strength == 0:
            continue
        lam0_sq = cc**2 if b["formula"] == 1 else cc  # um^2
        if lam0_sq < 0:
            return None  # not a physical resonance; no Lorentz form
        if lam0_sq == 0:
            eps_inf += strength  # B lam^2/lam^2
            continue
        poles.append({"delta_eps": strength, "omega0_ev": HC_EV_NM / (1000.0 * math.sqrt(lam0_sq)), "gamma_ev": 0.0})
    notes = [f"exact rewrite of Sellmeier formula {b['formula']} as undamped Lorentz poles; valid over "
             f"{b['range_um'][0] * 1000:g}-{b['range_um'][1] * 1000:g} nm like the source formula"]
    if any(x["type"] in ("tabulated k", "tabulated nk") for x in blocks):
        k_blocks = [x for x in blocks if "k" in x]
        if any(v > 0 for x in k_blocks for v in x["k"]):
            notes.append("the dataset's tabulated k is not represented (the model is lossless)")
    if eps_inf < 1.0:
        notes.append(f"eps_inf = {eps_inf:g} < 1")
    model = {"kind": "drude_lorentz", "eps_inf": eps_inf, "wp_ev": 0.0, "gamma_ev": 0.0, "poles": poles}
    return model, notes


def default_fit_band(disp: Dispersion) -> tuple[float, float]:
    """Band fitted by default: the data range clipped to 200 nm - 20 um (where photonics solvers work)."""
    lo, hi = disp.range_nm
    return max(lo, 200.0), min(hi, 20_000.0)


def evaluate_drude_lorentz(model: dict, wavelength_nm) -> np.ndarray:
    """eps(lambda) of a drude_lorentz model dict (no library needed)."""
    w = HC_EV_NM / np.asarray(wavelength_nm, dtype=float)
    e = model.get("eps_inf", 1.0) + 0j * w
    if model.get("wp_ev", 0) > 0:
        e = e - model["wp_ev"] ** 2 / (w**2 + 1j * model.get("gamma_ev", 0.0) * w)
    for p in model.get("poles", []):
        e = e + p["delta_eps"] * p["omega0_ev"] ** 2 / (p["omega0_ev"] ** 2 - w**2 - 1j * p["gamma_ev"] * w)
    return e
