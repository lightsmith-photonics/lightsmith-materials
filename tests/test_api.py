"""Read API: materials, datasets, data on grids, exports, compare, library material JSON."""

from __future__ import annotations

import csv
import io

import numpy as np
import pytest

from materials_db import rii
from materials_db.optics import HC_EV_NM, Dispersion
from materials_db.photonics import library_module

JC = "au/johnson-christy-1972"


def test_openapi_published(client):
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    for p in ["/v1/materials", "/v1/materials/{material_id}", "/v1/datasets/{dataset_id}",
              "/v1/datasets/{dataset_id}/data", "/v1/compare", "/v1/submissions",
              "/v1/submissions/{submission_id}/review", "/v1/datasets/{dataset_id}/omniscatter"]:
        assert p in paths, p
    body = paths["/v1/submissions"]["post"]["requestBody"]["content"]
    assert {"multipart/form-data", "application/json"} <= set(body)


def test_health(client):
    assert client.get("/healthz").json()["status"] == "ok"


def test_list_and_search_materials(client):
    r = client.get("/v1/materials", params={"q": "gold"}).json()
    assert r["items"][0]["slug"] == "au" and r["total"] >= 1
    assert r["items"][0]["datasets"][0]["is_default"] is True
    r = client.get("/v1/materials", params={"category": "metal"}).json()
    assert {m["slug"] for m in r["items"]} == {"au", "co"}
    r = client.get("/v1/materials", params={"per_page": 2, "page": 2}).json()
    assert (r["limit"], r["offset"], r["page"], len(r["items"]), r["total"]) == (2, 2, 2, 2, 10)
    r = client.get("/v1/materials", params={"category": "plasma"})
    assert r.status_code == 422 and r.json()["error"] == "invalid_category"


@pytest.mark.parametrize("ident", ["au", "gold", "Au"])
def test_material_by_slug_alias(client, ident):
    m = client.get(f"/v1/materials/{ident}").json()
    assert m["slug"] == "au" and m["datasets"][0]["slug"] == JC
    assert client.get(f"/v1/materials/{m['id']}").json()["slug"] == "au"


def test_categories(client):
    body = client.get("/v1/categories").json()
    assert [c["id"] for c in body][:7] == ["semiconductor", "glass_oxide", "polymer", "2d", "metal", "liquid",
                                           "phase_change"]
    cats = {c["id"]: c for c in body}
    assert cats["metal"]["count"] == 2 and cats["gas"]["count"] == 2
    assert (cats["glass_oxide"]["research_count"], cats["glass_oxide"]["supplier_count"]) == (2, 1)
    assert [m["slug"] for m in cats["semiconductor"]["featured"]] == ["linbo3"]
    assert [m["slug"] for m in cats["phase_change"]["featured"]] == ["ge2sb2te5"]


def test_photonics_aliases_tags_and_filters(client):
    assert client.get("/v1/materials/TFLN").json()["slug"] == "linbo3"
    assert client.get("/v1/materials/GST").json()["slug"] == "ge2sb2te5"
    r = client.get("/v1/materials", params={"q": "tfln"}).json()
    assert r["items"][0]["slug"] == "linbo3"
    ln = client.get("/v1/materials/linbo3").json()
    assert ln["category"] == "semiconductor" and ln["anisotropic"]
    assert {"integrated-photonics", "anisotropic"} <= set(ln["tags"])
    r = client.get("/v1/materials", params={"tag": "phase-change"}).json()
    assert [m["slug"] for m in r["items"]] == ["ge2sb2te5"]
    r = client.get("/v1/materials", params={"supplier": "true"}).json()
    assert [m["slug"] for m in r["items"]] == ["schott-n-bk7"]


def test_anisotropic_pair(client):
    o = client.get("/v1/datasets/linbo3/zelmon-1997-o").json()
    assert o["optical_axis"] == "o"
    an = o["anisotropy"]
    assert an["kind"] == "uniaxial" and an["complete"] and set(an["axes"]) == {"o", "e"}
    assert an["axes"]["e"]["slug"] == "linbo3/zelmon-1997-e"
    n_o = client.get("/v1/datasets/linbo3/zelmon-1997-o/data", params={"min": 1550, "max": 1550, "points": 1})
    n_e = client.get(an["axes"]["e"]["slug"].join(["/v1/datasets/", "/data"]), params={"min": 1550, "max": 1550,
                                                                                       "points": 1})
    assert n_o.json()["n"][0] == pytest.approx(2.211, abs=2e-3)  # LiNbO3 n_o at 1550 nm
    assert n_e.json()["n"][0] == pytest.approx(2.138, abs=2e-3)  # n_e
    boyd = client.get("/v1/datasets/aggas2/boyd-1971-o").json()["anisotropy"]
    assert boyd["axes"].keys() == {"o"} and not boyd["complete"]


def test_phase_change_states(client):
    m = client.get("/v1/materials/ge2sb2te5").json()
    assert m["category"] == "phase_change"
    states = {d["slug"]: d["state"] for d in m["datasets"]}
    assert states == {"ge2sb2te5/frantz-2024-crystal": "crystalline",
                      "ge2sb2te5/frantz-2024-amorphous": "amorphous"}


def test_thermo_optic(client):
    d = client.get("/v1/datasets/schott-n-bk7/schott").json()
    [to] = d["thermo_optic"]
    assert to["model"] == "schott" and to["coefficients"]["D0"] == pytest.approx(1.86e-6)
    assert 1e-6 < to["dn_dT_at"]["633"] < 4e-6  # N-BK7 dn_abs/dT at 633 nm is about 2.4e-6 /K
    assert client.get(f"/v1/datasets/{JC}").json()["thermo_optic"] is None


def test_exact_sellmeier_model(client):
    d = client.get("/v1/datasets/linbo3/zelmon-1997-e").json()
    assert d["models"] == [{"kind": "drude_lorentz", "method": "exact", "band_nm": [400.0, 5000.0],
                            "url": d["links"]["model"]}]
    m = client.get("/v1/datasets/linbo3/zelmon-1997-e/model").json()
    assert m["method"] == "exact" and m["material"]["kind"] == "drude_lorentz"
    from materials_db.photonics import evaluate_drude_lorentz
    data = client.get("/v1/datasets/linbo3/zelmon-1997-e/data", params={"quantity": "eps", "min": 600,
                                                                       "max": 3000, "points": 3}).json()
    eps = evaluate_drude_lorentz(m["material"], [600.0, 1800.0, 3000.0])
    np.testing.assert_allclose(eps.real, data["eps1"], rtol=1e-10)  # exact, not fitted
    mat = client.get("/v1/datasets/linbo3/zelmon-1997-e/lightsmith", params={"model": "drude_lorentz"})
    assert mat.json() == m["material"] and mat.headers["x-model-method"] == "exact"
    osc = library_module("materials")
    if osc is not None:
        from pydantic import TypeAdapter
        lib = TypeAdapter(osc.Material).validate_python(mat.json())
        np.testing.assert_allclose(lib.eps(np.array([1800.0])).real, eps[1].real, rtol=1e-12)


def test_fitted_model_is_cached(client):
    r = client.get(f"/v1/datasets/{JC}/model")
    if library_module("materials_fit") is None:
        assert r.status_code == 501 and r.json()["error"] == "fit_unavailable"
        return
    m = r.json()
    assert m["method"] == "fit" and m["band_nm"] == [200.0, 1937.0] and m["errors"]["rms_rel_error"] < 0.2
    d = client.get(f"/v1/datasets/{JC}").json()
    assert d["models"][0]["method"] == "fit"
    assert client.get(f"/v1/datasets/{JC}/model").json() == m  # served from the cache


@pytest.mark.parametrize("ident", [JC, "au/jc", "au/johnson"])
def test_dataset_metadata(client, ident):
    d = client.get(f"/v1/datasets/{ident}").json()
    assert d["slug"] == JC and d["license"] == "CC0-1.0" and d["license_info"]["id"] == "CC0-1.0"
    assert d["range_nm"] == [187.9, 1937.0] and d["n_points"] == 49 and d["has_k"]
    src = [r for r in d["references"] if r["role"] == "source"]
    assert src[0]["doi"] == "10.1103/physrevb.6.4370" and src[0]["year"] == 1972
    assert any(r["role"] == "database" for r in d["references"])
    assert d["versions"][0]["version"] == 1 and d["source"]["page"] == "Johnson"
    assert "Johnson" in d["citation"] and "Polyanskiy" in d["citation"]
    assert client.get(f"/v1/datasets/{d['id']}").json()["slug"] == JC


def test_dataset_404(client):
    r = client.get("/v1/datasets/au/nope")
    assert r.status_code == 404 and r.json()["error"] == "not_found"


def test_data_native_points(client):
    d = client.get(f"/v1/datasets/{JC}/data").json()
    assert d["points"] == 49 and len(d["wavelength_nm"]) == 49
    assert d["wavelength_nm"][0] == pytest.approx(187.9) and d["n"][0] == 1.28 and d["k"][0] == 1.188
    assert d["units"]["n"] == "1"


def test_data_eps_on_energy_grid(client):
    d = client.get(f"/v1/datasets/{JC}/data",
                   params={"quantity": "eps", "axis": "ev", "min": 1.5, "max": 3, "points": 4}).json()
    assert d["ev"] == pytest.approx([1.5, 2.0, 2.5, 3.0])
    assert d["wavelength_nm"][0] == pytest.approx(HC_EV_NM / 1.5)
    n = client.get(f"/v1/datasets/{JC}/data", params={"wavelength_nm": 1, "min": d["wavelength_nm"][1],
                                                       "max": d["wavelength_nm"][1], "points": 1}).json()
    eps1 = n["n"][0] ** 2 - n["k"][0] ** 2
    assert d["eps1"][1] == pytest.approx(eps1) and d["eps1"][0] < 0 < d["eps2"][0]
    assert "n" not in d


def test_data_all_quantities_and_range_warning(client):
    d = client.get(f"/v1/datasets/{JC}/data", params={"quantity": "all", "min": 100, "max": 300}).json()
    assert {"n", "k", "eps1", "eps2", "alpha", "skin_depth"} <= set(d)
    assert d["wavelength_nm"][0] == pytest.approx(187.9) and "clipped" in d["warnings"][0]
    r = client.get(f"/v1/datasets/{JC}/data", params={"min": 3000, "max": 4000})
    assert r.status_code == 422 and r.json()["error"] == "invalid_range"
    assert client.get(f"/v1/datasets/{JC}/data", params={"quantity": "nonsense"}).status_code == 422


def test_csv_export(client):
    r = client.get(f"/v1/datasets/{JC}/data", params={"format": "csv", "points": 5})
    assert r.headers["content-type"].startswith("text/csv")
    lines = [line for line in r.text.splitlines() if not line.startswith("#")]
    rows = list(csv.reader(io.StringIO("\n".join(lines))))
    assert rows[0] == ["wavelength_nm", "n", "k"] and len(rows) == 6
    assert "license: CC0-1.0" in r.text and "Johnson" in r.text


def test_yaml_export_is_refractiveindex_format(client):
    r = client.get(f"/v1/datasets/{JC}/data", params={"format": "yaml"})
    doc = rii.parse_yaml(r.text)
    blocks = rii.entry_blocks(doc)
    assert "Johnson" in doc["REFERENCES"]
    ref = Dispersion(client_blocks(client))
    wl = np.array(blocks[0]["wavelength_um"]) * 1000
    n, k = Dispersion(blocks).nk(wl)
    n0, k0 = ref.nk(wl)
    np.testing.assert_allclose(n, n0)
    np.testing.assert_allclose(k, k0)
    materials = library_module("materials")
    if materials is None:
        pytest.skip("simulation library not installed")
    tab = materials.read_refractiveindex_yaml(r.text)
    np.testing.assert_allclose(tab.wavelength_nm, wl)


def client_blocks(client):
    text = client.get(f"/v1/datasets/{JC}/source").text
    return rii.entry_blocks(rii.parse_yaml(text))


def test_source_file_verbatim(client):
    from .conftest import FIXTURE
    r = client.get(f"/v1/datasets/{JC}/source")
    assert r.content == (FIXTURE / "database/data/main/Au/nk/Johnson.yml").read_bytes()
    assert client.get(f"/v1/datasets/{JC}/versions").json()[0]["source_file"]["sha256"] == r.headers[
        "x-content-sha256"]


def test_compare_union_and_intersection(client):
    r = client.get("/v1/compare", params={"datasets": f"{JC},co/johnson-christy-1974,sio2/malitson-1965",
                                          "points": 50}).json()
    assert len(r["items"]) == 3 and r["range_mode"] == "union"
    grid = r["items"][0]["data"]["wavelength_nm"]
    assert all(it["data"]["wavelength_nm"] == grid for it in r["items"])
    sio2 = r["items"][2]["data"]
    assert sio2["k"][-1] is None or sio2["k"][-1] == 0  # union beyond a dataset's range is null
    assert r["items"][0]["data"]["n"][-1] is None  # Au ends at 1937 nm, SiO2 goes to 6.7 um
    r = client.get("/v1/compare", params={"datasets": f"{JC},sio2/malitson-1965", "range": "intersection",
                                          "points": 5}).json()
    assert r["range_nm"] == pytest.approx([210.0, 1937.0])
    assert all(v is not None for it in r["items"] for v in it["data"]["n"])
    r = client.get("/v1/compare", params={"datasets": "co-carbon-monoxide/smith-1976,methane/loria-1909",
                                          "range": "intersection"})
    assert r.status_code == 422 and r.json()["error"] == "no_overlap"
    r = client.get("/v1/compare", params={"datasets": f"{JC},sio2/malitson-1965", "format": "csv", "points": 3})
    assert f"{JC}:n" in r.text


@pytest.mark.parametrize("endpoint", ["lightsmith", "omniscatter"])
def test_library_material(client, endpoint):
    r = client.get(f"/v1/datasets/{JC}/{endpoint}")
    m = r.json()
    assert set(m) == {"kind", "wavelength_nm", "n_real", "n_imag", "source"} and m["kind"] == "tabulated"
    assert len(m["wavelength_nm"]) == 49 and "Johnson" in m["source"] and r.headers["x-dataset-slug"] == JC
    assert "Lightsmith Materials" in m["source"]
    f = client.get("/v1/datasets/sio2/malitson-1965/lightsmith").json()
    assert len(f["wavelength_nm"]) == 400  # formula sampled like the library does
    r = client.get("/v1/datasets/baf2/bosomworth-1967-5k/lightsmith")
    assert r.status_code == 422 and r.json()["error"] == "no_n_data"
    materials = library_module("materials")
    if materials is None:
        pytest.skip("simulation library not installed")
    tab = materials.Tabulated.model_validate(m)
    assert tab.eps(np.array([600.0]))[0].real < 0


def test_library_material_validates(client):
    materials = library_module("materials")
    if materials is None:
        pytest.skip("simulation library not installed")
    m = client.get(f"/v1/datasets/{JC}/lightsmith").json()
    from pydantic import TypeAdapter
    mat = TypeAdapter(materials.Material).validate_python(m)
    assert mat.eps(np.array([520.0])).imag[0] > 0


def test_fit_endpoint(client):
    r = client.get(f"/v1/datasets/{JC}/fit/drude-lorentz", params={"min": 500, "max": 900})
    if library_module("materials_fit") is None:
        assert r.status_code == 501
        return
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["material"]["kind"] == "drude_lorentz" and body["fit"]["rms_rel_error"] < 0.1


def test_datasets_list_filters(client):
    r = client.get("/v1/datasets", params={"covers_nm": "500,600", "has_k": True}).json()
    assert {d["slug"] for d in r["items"]} == {JC, "co/johnson-christy-1974", "schott-n-bk7/schott",
                                               "ge2sb2te5/frantz-2024-crystal", "ge2sb2te5/frantz-2024-amorphous"}
    r = client.get("/v1/datasets", params={"doi": "10.1103/PhysRevB.6.4370"}).json()
    assert [d["slug"] for d in r["items"]] == [JC]
    r = client.get("/v1/datasets", params={"material": "sio2"}).json()
    assert r["total"] == 1


def test_references_catalog_stats(client):
    r = client.get("/v1/references", params={"doi": "10.1103/PhysRevB.6.4370"}).json()
    assert r["total"] == 1
    ref = client.get(f"/v1/references/{r['items'][0]['id']}").json()
    assert ref["datasets"][0]["slug"] == JC
    shelves = client.get("/v1/catalog").json()["shelves"]
    assert client.get("/healthz").json()["schema"] == "ok"
    assert [s["shelf"] for s in shelves] == ["main", "organic", "glass", "specs", "popular_glass"]
    glass = client.get("/v1/catalog", params={"shelf": "glass"}).json()
    assert glass["books"][0]["pages"][0]["dataset_slug"] == "sio2/malitson-1965"
    st = client.get("/v1/stats").json()
    assert st["materials"] == 10 and st["datasets"] == 12 and st["source_snapshot"]["commit"].startswith("c5c2f18")
    lic = {x["id"]: x for x in client.get("/v1/licenses").json()}
    assert lic["CC0-1.0"]["allowed_for_contributions"] and not lic["CC-BY-SA-4.0"]["allowed_for_contributions"]


@pytest.mark.parametrize("origin, allowed", [("http://localhost:3000", True), ("http://localhost:5173", True),
                                             ("https://evil.example", False)])
def test_cors(client, origin, allowed):
    r = client.options("/v1/materials", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
    assert (r.headers.get("access-control-allow-origin") == origin) is allowed


def test_search_ranks_whole_alias_first(client):
    r = client.get("/v1/materials", params={"q": "silica"}).json()
    assert r["items"][0]["slug"] == "sio2"
    r = client.get("/v1/references/10.1103/PhysRevB.6.4370")
    assert r.status_code == 200 and r.json()["datasets"][0]["slug"] == JC
