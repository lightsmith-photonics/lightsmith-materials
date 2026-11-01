"""Evaluating datasets: n, k on any grid and the quantities derived from them.

Conventions match the lightsmith library: vacuum wavelengths in nm, time dependence
exp(-iωt), so absorbing media have k > 0 and Im(ε) > 0. Tabulated data are
linearly interpolated in wavelength, never extrapolated; formulas are evaluated
exactly. Where a ``tabulated k`` block covers less than the n data, k = 0
outside it (as in the library), and :attr:`Dispersion.notes` says so. A few
source entries have k only; their n is reported as null.
"""

from __future__ import annotations

import math

import numpy as np

from . import rii

HC_EV_NM = 1239.841984  # h*c in eV*nm

AXES = {"wavelength_nm": "nm", "wavelength_um": "µm", "ev": "eV", "wavenumber": "cm^-1"}
QUANTITIES = {
    "nk": ("n", "k"),
    "eps": ("eps1", "eps2"),
    "alpha": ("alpha",),
    "skin_depth": ("skin_depth",),
    "all": ("n", "k", "eps1", "eps2", "alpha", "skin_depth"),
}
UNITS = {"n": "1", "k": "1", "eps1": "1", "eps2": "1", "alpha": "cm^-1", "skin_depth": "nm"}
DESCRIPTIONS = {
    "n": "refractive index (real part)",
    "k": "extinction coefficient (imaginary part of the index, >= 0 for loss)",
    "eps1": "real relative permittivity, n^2 - k^2",
    "eps2": "imaginary relative permittivity, 2nk",
    "alpha": "intensity absorption coefficient, 4*pi*k/lambda",
    "skin_depth": "field 1/e decay length, lambda/(2*pi*k); null where k = 0",
}
FORMULA_SAMPLES = 400  # same as lightsmith.materials._FORMULA_SAMPLES
MAX_POINTS = 20000


def to_nm(x, axis: str) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    if axis == "wavelength_nm":
        return x
    if axis == "wavelength_um":
        return x * 1000.0
    with np.errstate(divide="ignore"):
        if axis == "ev":
            return HC_EV_NM / x
        if axis == "wavenumber":
            return 1e7 / x
    raise ValueError(f"unknown axis {axis!r}; use one of {', '.join(AXES)}")


def from_nm(nm, axis: str) -> np.ndarray:
    if axis == "wavelength_um":
        return np.asarray(nm, dtype=float) / 1000.0
    return to_nm(nm, axis)  # nm, eV and cm^-1 conversions are their own inverses


def _clean_table(wl, v) -> tuple[np.ndarray, np.ndarray]:
    arr = np.column_stack([np.asarray(wl, float), np.asarray(v, float)])
    arr = arr[np.argsort(arr[:, 0], kind="stable")]
    keep = np.concatenate([[True], np.diff(arr[:, 0]) > 0])
    return arr[keep, 0], arr[keep, 1]


class Dispersion:
    """n(λ), k(λ) from the stored blocks of a dataset version.

    Tables are kept in nm (source µm × 1000, the same conversion as the library),
    so evaluating at the native points returns the published values exactly."""

    def __init__(self, blocks: list[dict]):
        self.n_tab = self.k_tab = None  # (wavelength_nm, values), sorted, unique
        self._k_um = None
        self.formula = None  # (type, coefficients, lo_um, hi_um)
        for b in blocks:
            t = b["type"]
            if t in ("tabulated nk", "tabulated n"):
                wl, n = _clean_table(b["wavelength_um"], b["n"])
                self.n_tab = (wl * 1000.0, n)
            if t in ("tabulated nk", "tabulated k"):
                wl, k = _clean_table(b["wavelength_um"], b["k"])
                self.k_tab, self._k_um = (wl * 1000.0, k), wl
            elif t.startswith("formula"):
                self.formula = (int(b["formula"]), np.asarray(b["coefficients"], float), *map(float, b["range_um"]))
            elif t != "tabulated n":
                raise ValueError(f"unsupported block type {t!r}")
        if self.formula is None and self.n_tab is None and self.k_tab is None:
            raise ValueError("dataset has no n or k data")
        self.notes: list[str] = []
        lo, hi = self.range_nm
        if not self.has_n:
            self.notes.append("the source gives k only; n is not available")
        elif self.k_tab is not None and (self.k_tab[0][0] > lo or self.k_tab[0][-1] < hi):
            self.notes.append(f"k data cover {self.k_tab[0][0]:g}-{self.k_tab[0][-1]:g} nm only; "
                              f"k = 0 elsewhere in the n range {lo:g}-{hi:g} nm")

    @property
    def range_nm(self) -> tuple[float, float]:
        if self.formula is not None:
            return self.formula[2] * 1000.0, self.formula[3] * 1000.0
        tab = self.n_tab if self.n_tab is not None else self.k_tab
        return float(tab[0][0]), float(tab[0][-1])

    @property
    def has_k(self) -> bool:
        return self.k_tab is not None and bool(np.any(self.k_tab[1] > 0))

    @property
    def has_n(self) -> bool:
        return self.formula is not None or self.n_tab is not None

    @property
    def is_formula(self) -> bool:
        return self.formula is not None

    def native_nm(self) -> np.ndarray | None:
        """The tabulated wavelengths (nm), or None for formula data."""
        if self.formula is not None:
            return None
        return (self.n_tab if self.n_tab is not None else self.k_tab)[0]

    def sample_nm(self) -> np.ndarray:
        """Default sampling, identical to the library's: native points, or for a formula a
        log-spaced grid plus the k points inside its range."""
        if self.formula is None:
            return self.native_nm()
        _, _, lo, hi = self.formula
        lam = np.geomspace(lo, hi, FORMULA_SAMPLES)
        if self._k_um is not None:
            inside = self._k_um[(self._k_um > lo) & (self._k_um < hi)]
            lam = np.unique(np.concatenate([lam, inside]))
        return lam * 1000.0

    def nk(self, wavelength_nm) -> tuple[np.ndarray, np.ndarray]:
        """n and k at the given vacuum wavelengths; NaN outside the data range."""
        wl = np.asarray(wavelength_nm, dtype=float)
        lo, hi = self.range_nm
        tol = 1e-9 * max(abs(hi), 1.0)  # round-off from unit conversions at the range ends
        inside = (wl >= lo - tol) & (wl <= hi + tol)
        wl_c = np.clip(wl, lo, hi)
        if self.formula is not None:
            num, coeffs, _, _ = self.formula
            n = np.real(rii.formula_n(num, coeffs, wl_c / 1000.0))
        elif self.n_tab is not None:
            n = np.interp(wl_c, *self.n_tab)
        else:
            n = np.full_like(wl_c, np.nan)
        if self.k_tab is None:
            k = np.zeros_like(wl_c)
        else:
            k = np.interp(wl_c, *self.k_tab, left=0.0, right=0.0)
        return np.where(inside, n, np.nan), np.where(inside, k, np.nan)


def derived(wavelength_nm, n, k, names) -> dict[str, np.ndarray]:
    wl = np.asarray(wavelength_nm, float)
    out = {}
    with np.errstate(divide="ignore", invalid="ignore"):
        for q in names:
            if q == "n":
                out[q] = n
            elif q == "k":
                out[q] = k
            elif q == "eps1":
                out[q] = n**2 - k**2
            elif q == "eps2":
                out[q] = 2 * n * k
            elif q == "alpha":
                out[q] = 4 * math.pi * k / (wl * 1e-7)
            elif q == "skin_depth":
                out[q] = np.where(k > 0, wl / (2 * math.pi * k), np.nan)
            else:
                raise ValueError(f"unknown quantity {q!r}")
    return out


def axis_range(lo_nm: float, hi_nm: float, axis: str) -> tuple[float, float]:
    a, b = from_nm([lo_nm, hi_nm], axis)
    return (float(min(a, b)), float(max(a, b)))


def build_grid(axis: str, data_lo_nm: float, data_hi_nm: float, vmin: float | None, vmax: float | None,
               points: int | None, native_nm: np.ndarray | None,
               warnings: list[str]) -> np.ndarray:
    """Grid in axis units, ascending, restricted to the data range.

    With ``points``: uniform in the axis variable. Without: the native tabulated
    points, or a log-spaced grid of FORMULA_SAMPLES points for formulas."""
    lo, hi = axis_range(data_lo_nm, data_hi_nm, axis)
    unit = AXES[axis]
    req_lo = lo if vmin is None else float(vmin)
    req_hi = hi if vmax is None else float(vmax)
    if req_lo > req_hi:
        raise ValueError(f"min ({req_lo:g}) is larger than max ({req_hi:g})")
    if req_lo < lo or req_hi > hi:
        warnings.append(f"requested {req_lo:g}-{req_hi:g} {unit} clipped to the data range {lo:g}-{hi:g} {unit}; "
                        "no extrapolation is done")
    a, b = max(req_lo, lo), min(req_hi, hi)
    if a > b:
        raise ValueError(f"requested range {req_lo:g}-{req_hi:g} {unit} does not overlap the data "
                         f"range {lo:g}-{hi:g} {unit}")
    if points is not None:
        if not 1 <= points <= MAX_POINTS:
            raise ValueError(f"points must be between 1 and {MAX_POINTS}")
        return np.array([a]) if points == 1 else np.linspace(a, b, points)
    if native_nm is not None:
        x = np.sort(from_nm(native_nm, axis))
        return x[(x >= a) & (x <= b)]
    return np.geomspace(a, b, FORMULA_SAMPLES) if a > 0 else np.linspace(a, b, FORMULA_SAMPLES)


def to_json_list(a) -> list[float | None]:
    """Floats for JSON: NaN and infinities become null."""
    return [float(v) if math.isfinite(v) else None for v in np.asarray(a, dtype=float).tolist()]
