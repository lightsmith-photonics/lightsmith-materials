"""Datasets: metadata, data on any grid, exports, comparison, library materials, dispersive models."""

from __future__ import annotations

import builtins
import csv
import io
from typing import Literal

import numpy as np
import yaml
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import rii
from ..dispersion import ModelUnavailable, get_model
from ..models import Dataset, DatasetReference, Material, Reference
from ..optics import AXES, DESCRIPTIONS, QUANTITIES, UNITS, axis_range, build_grid, derived, from_nm, to_json_list, to_nm
from . import queries as Q
from . import schemas as S
from .deps import ApiError, Paging, base_url, brand, paging, session

router = APIRouter(prefix="/v1", tags=["datasets"])

Axis = Literal["wavelength_nm", "wavelength_um", "ev", "wavenumber"]
Format = Literal["json", "csv", "yaml"]
EXPORT_RESPONSES = {
    200: {"content": {"text/csv": {}, "application/yaml": {}}, "description": "JSON by default; CSV or YAML with format="},
    404: {"model": S.Error}, 422: {"model": S.Error},
}
QUANTITY_DOC = ("nk | eps | alpha | skin_depth | all, or a comma-separated list of n, k, eps1, eps2, alpha, "
                "skin_depth")


def _names(quantity: str) -> list[str]:
    if quantity in QUANTITIES:
        return list(QUANTITIES[quantity])
    names = [q.strip() for q in quantity.split(",") if q.strip()]
    bad = [q for q in names if q not in UNITS]
    if bad or not names:
        raise ApiError(422, "invalid_quantity", f"unknown quantity {quantity!r}; use {QUANTITY_DOC}")
    return names


def _format(request: Request, fmt: str | None) -> str:
    if fmt:
        return fmt
    accept = request.headers.get("accept", "")
    if "text/csv" in accept:
        return "csv"
    if "yaml" in accept:
        return "yaml"
    return "json"


def _dataset_or_404(s: Session, ident: str) -> Dataset:
    ds = Q.get_dataset(s, ident)
    if ds is None:
        raise ApiError(404, "not_found", f"no dataset {ident!r} (use an id such as ds_..., or a slug such as "
                                         "au/johnson-christy-1972)")
    return ds


def _version(ds: Dataset, version: int | None):
    v = Q.get_version(ds, version)
    if v is None:
        raise ApiError(404, "not_found", f"dataset {ds.slug} has no version {version}")
    return v


def _ref(ds: Dataset, version: int) -> dict:
    return {"id": ds.public_id, "slug": ds.slug, "name": ds.name, "version": version,
            "material": Q.material_ref(ds.material)}


def _evaluate(ds: Dataset, version: int | None, quantity: str, axis: str, vmin, vmax, points) -> dict:
    ver = _version(ds, version)
    names = _names(quantity)
    disp = Q.dispersion(ver)
    warnings = list(disp.notes)
    lo, hi = disp.range_nm
    try:
        x = build_grid(axis, lo, hi, vmin, vmax, points, disp.sample_nm(), warnings)
    except ValueError as e:
        raise ApiError(422, "invalid_range", str(e)) from None
    wl = to_nm(x, axis)
    n, k = disp.nk(wl)
    vals = derived(wl, n, k, names)
    out = {"dataset": _ref(ds, ver.version), "quantity": quantity, "axis": axis, "axis_unit": AXES[axis],
           "wavelength_nm": to_json_list(wl)}
    if axis != "wavelength_nm":
        out[axis] = to_json_list(x)
    out.update({q: to_json_list(a) for q, a in vals.items()})
    out["units"] = {"wavelength_nm": "nm", axis: AXES[axis], **{q: UNITS[q] for q in names}}
    out["points"] = int(len(x))
    out["range_nm"] = [lo, hi]
    out["warnings"] = warnings
    out["license"] = ds.license_id
    out["citation"] = Q.citation_text(ds)
    return out


def _header(ds: Dataset, payload: dict, base: str, brand_name: str = "Lightsmith Materials") -> list[str]:
    return [f"dataset: {ds.slug} ({ds.public_id}), version {payload['dataset']['version']}",
            f"name: {ds.name}",
            f"material: {ds.material.name}",
            f"license: {ds.license_id}",
            f"cite: {payload['citation']}",
            f"source: {brand_name}, {base}/v1/datasets/{ds.public_id}",
            "conventions: vacuum wavelength; exp(-i omega t), k >= 0 for loss"]


def _csv(header: list[str], columns: dict[str, list]) -> str:
    buf = io.StringIO()
    for h in header:
        buf.write(f"# {h}\n")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns.keys())
    for row in zip(*columns.values()):
        w.writerow(["" if v is None else repr(v) for v in row])
    return buf.getvalue()


def _export(payload: dict, fmt: str, ds: Dataset, base: str, names: list[str], brand_name: str):
    filename = ds.slug.replace("/", "_")
    if fmt == "json":
        return JSONResponse(payload)
    axis = payload["axis"]
    if fmt == "csv":
        cols = {f"{axis}": payload[axis]} if axis != "wavelength_nm" else {}
        cols["wavelength_nm"] = payload["wavelength_nm"]
        cols.update({q: payload[q] for q in names})
        header = _header(ds, payload, base, brand_name) + [f"warning: {w}" for w in payload["warnings"]]
        return PlainTextResponse(_csv(header, cols), media_type="text/csv; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'})
    if names == ["n", "k"] and axis in ("wavelength_nm", "wavelength_um"):
        # refractiveindex.info-style entry, readable by the library's read_refractiveindex_yaml
        rows = [(w / 1000.0, n, k) for w, n, k in zip(payload["wavelength_nm"], payload["n"], payload["k"])
                if w is not None and n is not None and k is not None]
        ver = Q.get_version(ds, payload["dataset"]["version"])
        text = rii.dump_entry(ver.references_html or Q.citation_text(ds), ds.comments, rows, ds.conditions,
                              header=_header(ds, payload, base, brand_name))
    else:
        text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    return PlainTextResponse(text, media_type="application/yaml; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{filename}.yml"'})


# --- list -----------------------------------------------------------------------------

@router.get("/datasets", response_model=S.Page[S.DatasetSummary], summary="Search datasets")
def list_datasets(q: str | None = Query(None, description="Text in dataset name or slug"),
                  material: str | None = Query(None, description="Material id, slug or alias"),
                  category: str | None = None,
                  has_k: bool | None = None,
                  covers_nm: str | None = Query(None, description="'500' or '400,900': datasets covering it"),
                  doi: str | None = None,
                  source: Literal["rii", "submission"] | None = None,
                  p: Paging = Depends(paging), s: Session = Depends(session)):
    stmt = select(Dataset).join(Material, Material.id == Dataset.material_id).where(Dataset.status == "published")
    if material:
        m = Q.get_material(s, material)
        if m is None:
            raise ApiError(404, "not_found", f"no material {material!r}")
        stmt = stmt.where(Dataset.material_id == m.id)
    if category:
        stmt = stmt.where(Material.category.in_([c.strip() for c in category.split(",")]))
    if has_k is not None:
        stmt = stmt.where(Dataset.has_k == has_k)
    if covers_nm:
        try:
            lims = [float(x) for x in covers_nm.split(",")]
        except ValueError:
            raise ApiError(422, "invalid_parameter", "covers_nm must be a number or 'min,max'") from None
        stmt = stmt.where(Dataset.wavelength_min_nm <= min(lims), Dataset.wavelength_max_nm >= max(lims))
    if doi:
        stmt = stmt.where(Dataset.id.in_(select(DatasetReference.dataset_id).join(Reference).where(
            Reference.doi == (rii.normalize_doi(doi) or doi.lower()))))
    if source:
        stmt = stmt.where(Dataset.source == source)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Dataset.name).like(like), Dataset.slug.like(like)))
    total = s.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = s.scalars(stmt.options(*Q.dataset_loader()).order_by(Material.slug, Dataset.position)
                     .limit(p.limit).offset(p.offset)).all()
    return p.envelope([Q.dataset_summary(d) for d in rows], total)


# --- compare (registered before the {dataset_id:path} routes) ------------------------

@router.get("/compare", response_model=S.CompareResponse, responses=EXPORT_RESPONSES,
            summary="Several datasets on a common grid")
def compare(request: Request,
            datasets: str = Query(description="Comma-separated dataset ids or slugs (up to 12)"),
            quantity: str = Query("nk", description=QUANTITY_DOC),
            axis: Axis = "wavelength_nm",
            min: float | None = Query(None, description="Grid start, in axis units"),
            max: float | None = Query(None, description="Grid end, in axis units"),
            points: int = Query(500, ge=2, le=20000),
            spacing: Literal["linear", "log"] = "linear",
            range: Literal["union", "intersection"] = Query(
                "union", description="Common grid over the union (null outside each dataset) or the overlap"),
            format: Format | None = None,
            s: Session = Depends(session), base: str = Depends(base_url)):
    ids = [x.strip() for x in datasets.split(",") if x.strip()]
    if not ids or len(ids) > 12:
        raise ApiError(422, "invalid_parameter", "give between 1 and 12 datasets")
    names = _names(quantity)
    items = []
    for ident in ids:
        ds = _dataset_or_404(s, ident)
        ver = _version(ds, None)
        items.append((ds, Q.dispersion(ver)))
    ranges = [axis_range(*d.range_nm, axis) for _, d in items]
    if range == "union":
        a, b = np.min([r[0] for r in ranges]), np.max([r[1] for r in ranges])
    else:
        a, b = np.max([r[0] for r in ranges]), np.min([r[1] for r in ranges])
        if a > b:
            raise ApiError(422, "no_overlap", "the datasets have no common range; use range=union")
    a = a if min is None else float(min)
    b = b if max is None else float(max)
    if not 0 < a < b:
        raise ApiError(422, "invalid_range", f"empty or invalid range {a:g}-{b:g} {AXES[axis]}")
    x = np.geomspace(a, b, points) if spacing == "log" else np.linspace(a, b, points)
    wl = to_nm(x, axis)
    out_items = []
    for ds, d in items:
        n, k = d.nk(wl)
        vals = derived(wl, n, k, names)
        data = {"wavelength_nm": to_json_list(wl)}
        if axis != "wavelength_nm":
            data[axis] = to_json_list(x)
        data.update({q: to_json_list(v) for q, v in vals.items()})
        out_items.append({"dataset": Q.dataset_summary(ds), "data": data, "range_nm": list(d.range_nm),
                          "warnings": list(d.notes)})
    lo_nm, hi_nm = sorted(float(v) for v in from_nm([a, b], axis))
    payload = {"quantity": quantity, "axis": axis, "axis_unit": AXES[axis], "range_mode": range,
               "range_nm": [lo_nm, hi_nm], "units": {"wavelength_nm": "nm", axis: AXES[axis],
                                                     **{q: UNITS[q] for q in names}},
               "items": out_items, "warnings": []}
    fmt = _format(request, format)
    if fmt == "csv":
        cols = {axis: to_json_list(x)} if axis != "wavelength_nm" else {}
        cols["wavelength_nm"] = to_json_list(wl)
        for it in out_items:
            for q in names:
                cols[f"{it['dataset']['slug']}:{q}"] = it["data"][q]
        header = [f"comparison of {', '.join(it['dataset']['slug'] for it in out_items)}"]
        header += [f"{it['dataset']['slug']}: {it['dataset']['license']}; cite {it['dataset']['reference']}"
                   for it in out_items]
        return PlainTextResponse(_csv(header, cols), media_type="text/csv; charset=utf-8")
    if fmt == "yaml":
        return PlainTextResponse(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, default=str),
                                 media_type="application/yaml; charset=utf-8")
    return payload


# --- per-dataset sub-resources (before the bare {dataset_id:path} route) -------------

@router.get("/datasets/{dataset_id:path}/data", response_model=S.DataResponse, response_model_exclude_none=True,
            responses=EXPORT_RESPONSES, summary="Dataset values on a grid (JSON, CSV or YAML)")
def dataset_data(request: Request, dataset_id: str,
                 quantity: str = Query("nk", description=QUANTITY_DOC),
                 axis: Axis = Query("wavelength_nm", description="Grid variable; min/max are in its units"),
                 min: float | None = Query(None, description="Lower end of the grid (axis units)"),
                 max: float | None = Query(None, description="Upper end of the grid (axis units)"),
                 points: int | None = Query(None, ge=1, le=20000,
                                            description="Uniform grid in the axis variable; default: native points "
                                                        "(formulas: 400 log-spaced points)"),
                 format: Format | None = Query(None, description="json (default), csv or yaml (yaml with "
                                                                 "quantity=nk is refractiveindex.info format)"),
                 version: int | None = Query(None, ge=1, description="Dataset version (default: current)"),
                 s: Session = Depends(session), base: str = Depends(base_url), brand_name: str = Depends(brand)):
    ds = _dataset_or_404(s, dataset_id)
    payload = _evaluate(ds, version, quantity, axis, min, max, points)
    return _export(payload, _format(request, format), ds, base, _names(quantity), brand_name)


def tabulated(ds: Dataset, version: int | None, vmin: float | None, vmax: float | None,
              points: int | None, base: str, brand_name: str) -> tuple[dict, list[str]]:
    ver = _version(ds, version)
    d = Q.dispersion(ver)
    if not d.has_n:
        raise ApiError(422, "no_n_data", f"{ds.slug} has k data only; simulation materials need n and k")
    lo, hi = d.range_nm
    a, b = (lo if vmin is None else max(lo, vmin)), (hi if vmax is None else min(hi, vmax))
    if a >= b:
        raise ApiError(422, "invalid_range", f"requested range does not overlap {lo:g}-{hi:g} nm")
    wl = np.geomspace(a, b, points) if points else d.sample_nm()
    wl = wl[(wl >= a) & (wl <= b)]
    n, k = d.nk(wl)
    ok = np.isfinite(n) & np.isfinite(k)
    warnings = list(d.notes)
    if not np.all(ok):
        warnings.append(f"{int(np.sum(~ok))} points with non-finite n were dropped")
    mat = {"kind": "tabulated", "wavelength_nm": wl[ok].tolist(), "n_real": n[ok].tolist(),
           "n_imag": k[ok].tolist(), "source": _source_text(ds, ver, base, brand_name)}
    return mat, warnings


def _source_text(ds: Dataset, ver, base: str, brand_name: str) -> str:
    src = (ver.meta or {}).get("source") or {}
    text = (f"{Q.citation_text(ds)}. Dataset {ds.slug} (version {ver.version}, {ds.license_id}), "
            f"{brand_name}, {base}/v1/datasets/{ds.public_id}")
    if src.get("commit"):
        text += f"; original file {src['path']} at database commit {src['commit'][:10]}"
    return text


def _model(s: Session, ds: Dataset, version: int | None, band=None, n_poles=None, drude=None) -> dict:
    ver = _version(ds, version)
    try:
        return get_model(s, ds, ver, Q.dispersion(ver), band=band, n_poles=n_poles, drude=drude)
    except ModelUnavailable as e:
        status, code = (501, "fit_unavailable") if "library" in str(e) else (422, "no_model")
        raise ApiError(status, code, f"no dispersive model for {ds.slug}: {e}") from None
    except ValueError as e:
        raise ApiError(422, "fit_failed", str(e)) from None


def _export_headers(ds: Dataset, version: int | None, warnings: list[str]) -> dict:
    headers = {"X-Dataset-Id": ds.public_id, "X-Dataset-Slug": ds.slug,
               "X-Dataset-Version": str(version or ds.current_version)}
    if warnings:
        headers["X-Warnings"] = " | ".join(warnings).encode("ascii", "replace").decode()
    return headers


def dataset_export(dataset_id: str,
                   model: Literal["tabulated", "drude_lorentz"] = Query(
                       "tabulated", description="tabulated: the data (library Tabulated kind); drude_lorentz: "
                                                "dispersive model for FDTD/FDFD (exact or fitted, see /model)"),
                   min: float | None = Query(None, description="Restrict to wavelengths >= min (nm)"),
                   max: float | None = Query(None, description="Restrict to wavelengths <= max (nm)"),
                   points: int | None = Query(None, ge=2, le=20000,
                                              description="Log-spaced resampling; default native points"),
                   version: int | None = Query(None, ge=1),
                   s: Session = Depends(session), base: str = Depends(base_url), brand_name: str = Depends(brand)):
    ds = _dataset_or_404(s, dataset_id)
    if model == "drude_lorentz":
        m = _model(s, ds, version)
        headers = _export_headers(ds, version, m["notes"])
        headers["X-Model-Method"] = m["method"]
        headers["X-Model-Band-Nm"] = f"{m['band_nm'][0]:g}-{m['band_nm'][1]:g}"
        return JSONResponse(m["material"], headers=headers)
    mat, warnings = tabulated(ds, version, min, max, points, base, brand_name)
    return JSONResponse(mat, headers=_export_headers(ds, version, warnings))


EXPORT_DOC = dict(responses={200: {"model": S.OmniscatterTabulated,
                                   "description": "A library material: Tabulated, or DrudeLorentz with "
                                                  "model=drude_lorentz"},
                             404: {"model": S.Error}, 422: {"model": S.Error}, 501: {"model": S.Error}})
router.add_api_route("/datasets/{dataset_id:path}/lightsmith", dataset_export, methods=["GET"], **EXPORT_DOC,
                     summary="The dataset as a Lightsmith library material (Tabulated or DrudeLorentz)")
router.add_api_route("/datasets/{dataset_id:path}/omniscatter", dataset_export, methods=["GET"], **EXPORT_DOC,
                     deprecated=True, summary="Alias of /lightsmith, kept for existing clients")


@router.get("/datasets/{dataset_id:path}/model",
            summary="Dispersive model for FDTD/FDFD: exact Sellmeier rewrite or Drude-Lorentz fit",
            responses={404: {"model": S.Error}, 422: {"model": S.Error}, 501: {"model": S.Error}})
def dataset_model(dataset_id: str, version: int | None = Query(None, ge=1), s: Session = Depends(session)):
    """Sellmeier datasets (formulas 1 and 2) are rewritten exactly as undamped Lorentz poles. Other
    datasets are fitted once over their range clipped to 200 nm - 20 um with the library's
    ``fit_drude_lorentz``, and the fit is cached; ``method`` says which. Custom bands: ``/fit/drude-lorentz``."""
    ds = _dataset_or_404(s, dataset_id)
    m = _model(s, ds, version)
    return {"dataset": _ref(ds, version or ds.current_version), **m}


@router.get("/datasets/{dataset_id:path}/fit/drude-lorentz",
            summary="Fit a passive Drude-Lorentz model over a chosen band (needs the library)",
            responses={422: {"model": S.Error}, 501: {"model": S.Error}})
def dataset_fit(dataset_id: str, min: float | None = Query(None, description="Band start (nm)"),
                max: float | None = Query(None, description="Band end (nm)"),
                n_poles: int | None = Query(None, ge=0, le=12, description="Default: chosen automatically"),
                drude: bool | None = Query(None, description="Default: added when Re(eps) < 0 in the band"),
                version: int | None = Query(None, ge=1), s: Session = Depends(session)):
    ds = _dataset_or_404(s, dataset_id)
    ver = _version(ds, version)
    lo, hi = Q.dispersion(ver).range_nm
    band = (lo if min is None else builtins.max(lo, min), hi if max is None else builtins.min(hi, max))
    m = _model(s, ds, version, band=band, n_poles=n_poles, drude=drude)
    return {"dataset": _ref(ds, version or ds.current_version), "material": m["material"], "fit": m["errors"],
            "band_nm": m["band_nm"], "warnings": m["notes"]}


@router.get("/datasets/{dataset_id:path}/source", summary="The original file, verbatim",
            responses={200: {"content": {"application/yaml": {}, "text/csv": {}}}, 404: {"model": S.Error}})
def dataset_source(dataset_id: str, version: int | None = Query(None, ge=1), s: Session = Depends(session)):
    ds = _dataset_or_404(s, dataset_id)
    ver = _version(ds, version)
    sf = ver.source_file
    if sf is None:
        raise ApiError(404, "not_found", "no source file stored for this version")
    return Response(sf.content, media_type=sf.media_type,
                    headers={"Content-Disposition": f'attachment; filename="{sf.filename}"',
                             "X-Content-SHA256": sf.sha256})


@router.get("/datasets/{dataset_id:path}/versions", response_model=list[S.VersionOut],
            summary="Version history")
def dataset_versions(dataset_id: str, s: Session = Depends(session)):
    ds = _dataset_or_404(s, dataset_id)
    return [Q.version_out(v) for v in ds.versions]


@router.get("/datasets/{dataset_id:path}", response_model=S.DatasetDetail,
            responses={404: {"model": S.Error}}, summary="Dataset metadata, references and license")
def get_dataset(dataset_id: str, s: Session = Depends(session), base: str = Depends(base_url),
                brand_name: str = Depends(brand)):
    return Q.dataset_detail(_dataset_or_404(s, dataset_id), base, brand_name)


@router.get("/quantities", summary="Quantities and axes the data endpoints understand", tags=["meta"])
def quantities():
    return {"quantities": {q: {"unit": UNITS[q], "description": DESCRIPTIONS[q]} for q in UNITS},
            "groups": {k: list(v) for k, v in QUANTITIES.items()}, "axes": AXES}
