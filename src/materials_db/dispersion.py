"""Dispersive models of datasets: exact rewrites, cached fits, fits on demand."""

from __future__ import annotations

import numpy as np
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import Dataset, DatasetVersion, DispersionModel
from .optics import Dispersion
from .photonics import default_fit_band, library_module, sellmeier_as_drude_lorentz

KIND = "drude_lorentz"


class ModelUnavailable(Exception):
    """No model exists and none can be fitted here (``reason`` explains)."""


def fit_available() -> bool:
    return library_module("materials_fit") is not None


def stored_model(session: Session, ds: Dataset, version: int) -> DispersionModel | None:
    return session.scalar(select(DispersionModel).where(DispersionModel.dataset_id == ds.id,
                                                        DispersionModel.dataset_version == version,
                                                        DispersionModel.kind == KIND))


def available(session: Session, ds: Dataset, ver: DatasetVersion) -> list[dict]:
    """Models that exist without fitting (exact rewrites and cached fits)."""
    out = []
    exact = sellmeier_as_drude_lorentz(ver.blocks)
    if exact is not None:
        lo, hi = ds.wavelength_min_nm, ds.wavelength_max_nm
        out.append({"kind": KIND, "method": "exact", "band_nm": [lo, hi]})
    m = stored_model(session, ds, ver.version)
    if m is not None:
        out.append({"kind": m.kind, "method": m.method, "band_nm": m.band_nm, "errors": m.errors})
    return out


def tabulated_samples(disp: Dispersion, lo: float, hi: float) -> np.ndarray:
    wl = disp.sample_nm()
    wl = wl[(wl >= lo) & (wl <= hi)]
    if len(wl) < 8:  # sparse tables: add points so the fit is constrained across the band
        wl = np.unique(np.concatenate([wl, np.geomspace(lo, hi, 64)]))
    return wl


def fit(disp: Dispersion, band: tuple[float, float], n_poles: int | None = None,
        drude: bool | None = None) -> tuple[dict, dict, list[str]]:
    """(drude_lorentz material, fit errors, notes) from the library's fitter."""
    mod = library_module("materials_fit")
    if mod is None:
        raise ModelUnavailable("fitting needs the Lightsmith/omniscatter library (materials_fit) on the server")
    if not disp.has_n:
        raise ModelUnavailable("the dataset has k data only")
    lo, hi = band
    wl = tabulated_samples(disp, lo, hi)
    n, k = disp.nk(wl)
    ok = np.isfinite(n) & np.isfinite(k)
    if ok.sum() < 2:
        raise ModelUnavailable("fewer than two usable data points in the band")
    material = {"kind": "tabulated", "wavelength_nm": wl[ok].tolist(), "n_real": n[ok].tolist(),
                "n_imag": np.maximum(k[ok], 0.0).tolist(), "source": "fit input"}
    notes = []
    if np.any(k[ok] < 0):
        notes.append("negative k in the data was clipped to 0 before fitting (a passive model cannot fit gain)")
    model, info = mod.fit_drude_lorentz(material, material["wavelength_nm"], n_poles, drude=drude, return_info=True)
    errors = {k_: v for k_, v in info.to_dict().items() if k_ not in ("model",)}
    return model.model_dump(), errors, notes


def get_model(session: Session, ds: Dataset, ver: DatasetVersion, disp: Dispersion, *,
              band: tuple[float, float] | None = None, n_poles: int | None = None, drude: bool | None = None,
              allow_fit: bool = True) -> dict:
    """The best dispersive model: exact rewrite > cached default fit > new fit (cached when it uses defaults)."""
    if band is None and n_poles is None and drude is None:
        exact = sellmeier_as_drude_lorentz(ver.blocks)
        if exact is not None:
            material, notes = exact
            return {"kind": KIND, "method": "exact", "material": material,
                    "band_nm": [ds.wavelength_min_nm, ds.wavelength_max_nm], "errors": None, "notes": notes}
        m = stored_model(session, ds, ver.version)
        if m is not None:
            return {"kind": m.kind, "method": m.method, "material": m.material, "band_nm": m.band_nm,
                    "errors": m.errors, "notes": m.notes}
    if not allow_fit:
        raise ModelUnavailable("no stored model")
    default = band is None and n_poles is None and drude is None
    lo, hi = band or default_fit_band(disp)
    if lo >= hi:
        raise ModelUnavailable(f"empty fit band {lo:g}-{hi:g} nm")
    material, errors, notes = fit(disp, (lo, hi), n_poles, drude)
    notes = [f"fitted over {lo:g}-{hi:g} nm; use only inside this band", *notes]
    if default:
        try:
            with session.begin_nested():  # a concurrent request may have cached the same fit
                session.add(DispersionModel(dataset_id=ds.id, dataset_version=ver.version, kind=KIND, method="fit",
                                            material=material, band_nm=[lo, hi], errors=errors, notes=notes))
        except IntegrityError:
            pass
    return {"kind": KIND, "method": "fit", "material": material, "band_nm": [lo, hi], "errors": errors,
            "notes": notes}
