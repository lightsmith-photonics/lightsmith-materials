"""The website: pages, redirects, crawler files, library snippets, and parity of the browser code with the API."""

from __future__ import annotations

import json
from html import unescape
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from materials_db.optics import Dispersion
from materials_db.photonics import library_module
from materials_db.site import content as C

HTML = {"Accept": "text/html"}
JC = "au/johnson-christy-1972"
STATIC = Path(C.__file__).parent / "static"


def page(client, path, status=200):
    r = client.get(path, headers=HTML, follow_redirects=False)
    assert r.status_code == status, (path, r.status_code, r.text[:500])
    return r


def test_home_is_html_for_browsers_and_json_for_clients(client):
    r = page(client, "/")
    assert "text/html" in r.headers["content-type"] and "Refractive index" in r.text
    assert '<link rel="canonical" href="http://testserver/">' in r.text
    assert client.get("/").json()["api"] == "/v1"  # API clients keep the service description


@pytest.mark.parametrize("path,target", [
    ("/?shelf=main&book=Au&page=Johnson", f"/materials/{JC}"),        # refractiveindex.info-style address
    ("/?shelf=main&book=Au", "/materials/au"),
    ("/?shelf=main", "/browse/main"),
    ("/browse/main/Au", "/materials/au"),
    ("/browse/main/Au/Johnson", f"/materials/{JC}"),
    ("/browse/glass/fused_silica/Malitson", "/materials/sio2/malitson-1965"),  # a re-listing goes to the home page
    ("/browse/popular_glass/BK7/SCHOTT", None),
    ("/materials/gold", "/materials/au"),                               # material alias
    ("/materials/Au", "/materials/au"),
    ("/materials/au/johnson", f"/materials/{JC}"),                      # dataset alias
])
def test_redirects(client, path, target):
    r = page(client, path, 301)
    if target:
        assert r.headers["location"] == target
    else:
        assert r.headers["location"].startswith("/materials/schott-n-bk7/")


def test_public_ids_redirect(client):
    ds = client.get(f"/v1/datasets/{JC}").json()
    assert page(client, f"/datasets/{ds['id']}", 301).headers["location"] == f"/materials/{JC}"
    assert page(client, f"/materials/{ds['id']}", 301).headers["location"] == f"/materials/{JC}"


def test_browse_pages(client):
    r = page(client, "/browse")
    assert "MAIN" in r.text and "/browse/specs" in r.text
    r = page(client, "/browse/main")
    assert 'href="/materials/au"' in r.text and "Au - Gold" in r.text  # books link to materials, dividers kept
    r = page(client, "/browse/specs/SCHOTT-optical")  # a supplier book lists its pages
    assert "N-BK7" in r.text and "BK (Borosilicate crown)" in r.text
    page(client, "/browse/nope", 404)


def test_dataset_page_content(client):
    r = page(client, f"/materials/{JC}")
    html = unescape(r.text)
    assert f'<link rel="canonical" href="http://testserver/materials/{JC}">' in html
    assert "Johnson and Christy 1972: n,k 0.188–1.937 µm" in html
    assert "Optical constants of the noble metals" in html            # references, as published
    assert 'href="https://doi.org/10.1103/PhysRevB.6.4370"' in html
    assert "<td>0.1879</td><td>1.28</td><td>1.188</td>" in html          # the published rows, verbatim
    assert "Room temperature" in html                                  # comments
    for fmt in ("csv", "txt", "yaml"):
        assert f"/data?format={fmt}" in html
    assert "/source" in html                                           # full database record
    assert 'application/ld+json' in html and '"@type":"Dataset"' in html
    # library funnel: bundled material, pinned download, API, simulation, links
    assert 'ls.LibraryMaterial(name="Au", dataset="Johnson-Christy")' in html
    assert 'ls.fetch_refractiveindex("main", "Au", "Johnson", ref="c5c2f188e848453def5970e347399d653df2ffc2")' in html
    assert "ls.Tabulated.model_validate" in html and "ls.Simulation(" in html
    assert "docs.lightsmith.dev" in html and "spec_url=" in html
    # calculator values rendered on the server (the d line, 587.6 nm, as on the source site)
    n, k = Dispersion(_blocks(client, JC)).nk(np.array([587.6]))
    assert f'data-out="n">{C.num(n[0])}<' in html and f'data-out="k">{C.num(k[0])}<' in html


def _blocks(client, slug):
    ds = client.get(f"/v1/datasets/{slug}").json()
    with client.app.state.db.session() as s:
        from materials_db.api import queries as Q
        return Q.get_version(Q.get_dataset(s, ds["id"]), None).blocks


def test_formula_page(client):
    html = page(client, "/materials/sio2/malitson-1965").text
    assert '<math display="block"' in html and "<mn>0.6961663</mn>" in html and "<mn>9.896161</mn>" in html
    assert "Sellmeier" in html and "Fused silica, 20 °C" in html
    assert "Abbe number" in html
    # the dispersion parameter of fused silica at 1.55 µm is about 22 ps/(nm km)
    d = C.derived_at(Dispersion(_blocks(client, "sio2/malitson-1965")), 1.55)
    assert 20 < d["D"] < 24 and -32 < d["gvd"] < -24 and 1.46 < d["ng"] < 1.47


def test_k_only_dataset_page(client):
    html = page(client, "/materials/baf2/bosomworth-1967-5k").text
    assert "k only" in html  # no simulation snippet, explained instead
    assert "ls.Tabulated" not in html


def test_supplier_and_anisotropic_pages(client):
    html = page(client, "/materials/schott-n-bk7").text
    assert "N-BK7" in html and "SCHOTT" in html
    ds = client.get("/v1/datasets", params={"material": "schott-n-bk7"}).json()["items"][0]
    html = page(client, f"/materials/{ds['slug']}").text
    assert "Properties" in html and "glass code" in html
    html = page(client, "/materials/linbo3/zelmon-1997-o").text
    assert "Uniaxial crystal" in html and 'href="/materials/linbo3/zelmon-1997-e"' in html


def test_material_page(client):
    html = page(client, "/materials/au").text
    assert "Au (Gold)" in html and "is a noble metal" in html  # about.yml text
    assert 'href="https://en.wikipedia.org/wiki/Gold"' in html       # about.yml links
    assert f'href="/materials/{JC}"' in html and "data-compare" in html
    assert client.get("/v1/materials/au").json()["external_links"][0]["text"] == "Gold - Wikipedia"


def test_category_and_search(client):
    assert 'href="/materials/au"' in page(client, "/categories/metal").text
    page(client, "/categories/nope", 404)
    r = page(client, "/search?q=gold")
    assert 'href="/materials/au"' in r.text and r.headers["x-robots-tag"].startswith("noindex")
    assert "Johnson" in page(client, "/search?q=christy").text  # references are searched too


def test_not_found_is_html_for_pages_json_for_api(client):
    assert "Not found" in page(client, "/materials/unobtainium", 404).text
    assert "Not found" in page(client, "/no/such/page", 404).text
    r = client.get("/v1/materials/unobtainium", headers=HTML)
    assert r.status_code == 404 and r.json()["error"] == "not_found"


def test_sitemap_and_robots(client):
    xml = client.get("/sitemap.xml").text
    locs = re.findall(r"<loc>(.*?)</loc>", xml)
    assert f"http://testserver/materials/{JC}" in locs and "http://testserver/materials/au" in locs
    assert "http://testserver/browse/specs/SCHOTT-optical" in locs
    assert "http://testserver/browse/main/Au" not in locs  # redirects are not listed
    assert len(locs) == len(set(locs))
    robots = client.get("/robots.txt").text
    assert "Sitemap: http://testserver/sitemap.xml" in robots and "Disallow: /v1/" in robots


def test_static_assets_are_cacheable(client):
    r = client.get("/static/site.css")
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    assert re.search(r'/static/site\.js\?v=[0-9a-f]{10}', page(client, "/").text)


def test_txt_export(client):
    r = client.get(f"/v1/datasets/{JC}/data", params={"format": "txt"})
    assert r.headers["content-type"].startswith("text/plain")
    lines = [x for x in r.text.splitlines() if not x.startswith("#")]
    assert lines[0] == "wavelength_nm\tn\tk" and lines[1].split("\t")[1] == "1.28"


def test_studio_spec(client):
    r = client.get(f"/materials/{JC}/studio.json")
    spec = r.json()
    assert r.headers["access-control-allow-origin"] == "*"
    mat = spec["particles"][0]["material"]
    assert mat["kind"] == "tabulated" and len(mat["wavelength_nm"]) == 49
    lo, hi = spec["wavelengths"]["start_nm"], spec["wavelengths"]["stop_nm"]
    assert 187.9 <= lo < hi <= 1937
    lib = library_module("spec")
    if lib is None:
        pytest.skip("simulation library not installed")
    lib.Simulation.model_validate(spec)


def test_snippets_use_the_real_library_api(client):
    materials = library_module("materials")
    if materials is None:
        pytest.skip("simulation library not installed")
    import lightsmith as ls
    # what the "This API" snippet does, without the network
    tab = ls.Tabulated.model_validate(client.get(f"/v1/datasets/{JC}/lightsmith").json())
    assert tab.range_nm == (187.9, 1937.0)
    lib = ls.LibraryMaterial(name="Au", dataset="Johnson-Christy")
    assert np.allclose(lib.eps(np.array([633.0])), tab.eps(np.array([633.0])))
    sim = ls.Simulation(particles=[ls.Particle(shape=ls.Sphere(radius_nm=50), material=tab)], medium_index=1.0,
                        wavelengths=ls.WavelengthRange(start_nm=400, stop_nm=1000, num=5))
    assert len(ls.solve(sim).q_ext) == 5
    dl = client.get("/v1/datasets/sio2/malitson-1965/lightsmith", params={"model": "drude_lorentz"}).json()
    assert ls.DrudeLorentz.model_validate(dl).eps(np.array([1550.0])).real[0] > 2
    # every fetch_refractiveindex snippet passes the library's own name check
    for name in ("Johnson", "CH4 - methane", "Walling-α"):
        assert materials._SAFE_NAME.match(name) and C._SAFE_NAME.match(name)


def test_formula_mathml_all_types():
    for n, coeffs in {1: [0, 0.69, 0.068, 0.4, 0.11], 2: [0.1, 1.2, -0.01], 3: [2.1, -0.01, 2, 0.02, -2],
                      4: [2.2, 0.1, 2, 0.2, 2, 0, 1, 1, 1, -0.01, 2], 5: [1.5, 0.004, -2], 6: [0, 0.05, 238, 0.0016, 57],
                      7: [1.5, 0.01, 0.001, -0.002, 1e-5, 0], 8: [0.3, 0.1, 0.02, -0.001], 9: [2, 0.1, 0.03, 0.2, 5, 1]}.items():
        m = C.formula_mathml(n, coeffs)
        assert m.startswith('<math display="block"') and m.count("<math") == 1 and "<mi>n</mi>" in m


def test_sanitize_keeps_source_markup_and_drops_the_rest():
    s = C.sanitize('<a href="https://doi.org/10.1/x" onclick="x()"><i>J</i> <b>6</b></a><script>alert(1)</script>'
                   '<a href="javascript:alert(1)">bad</a> SiO<sub>2</sub><img src=x onerror=y>')
    assert s == '<a href="https://doi.org/10.1/x" rel="noopener nofollow"><i>J</i> <b>6</b></a>alert(1)bad SiO<sub>2</sub>'


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_browser_code_matches_the_api(client):
    """dataset.js evaluates n, k and the derived quantities exactly as the server does."""
    with client.app.state.db.session() as s:
        from sqlalchemy import select

        from materials_db.models import Dataset
        slugs = list(s.scalars(select(Dataset.slug)))
    cases = []
    for slug in slugs:
        disp = Dispersion(_blocks(client, slug))
        lo, hi = disp.range_nm
        wls = [float(w) for w in np.geomspace(lo * 1.0001, hi * 0.9999, 7) / 1000]
        expect = [{q: (v if v is None or np.isfinite(v) else None) for q, v in C.derived_at(disp, w).items()}
                  for w in wls]
        cases.append({"slug": slug, "plot": C.plot_payload(disp), "wl": wls, "expect": expect})
    script = r"""
const fs = require("fs");
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const src = fs.readFileSync(process.argv[1], "utf8");
const out = [];
for (const c of cases) {
  const LS = {$: () => null, $$: () => [], palette: () => [], fmt: String, chart: () => ({update() {}})};
  global.window = {LS};
  global.document = {getElementById: () => ({textContent: JSON.stringify(c.plot)}), querySelector: () => null,
                     addEventListener() {}};
  new Function("window", "document", src)(global.window, global.document);
  out.push(c.wl.map((w) => LS.derivedAt(w)));
}
process.stdout.write(JSON.stringify(out, (k, v) => (typeof v === "number" && !isFinite(v) ? null : v)));
"""
    res = subprocess.run(["node", "-e", script, str(STATIC / "dataset.js")], input=json.dumps(cases),
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    names = {"n": "n", "k": "k", "eps1": "eps1", "eps2": "eps2", "alpha": "alpha", "skin_depth_nm": "skin",
             "dn_dl": "dndl", "ng": "ng", "gvd": "gvd", "D": "D"}
    for case, rows in zip(cases, got):
        for want, have in zip(case["expect"], rows):
            for py, js in names.items():
                a, b = want.get(py), have.get(js)
                if a is None or (isinstance(a, float) and not np.isfinite(a)):
                    assert b is None, (case["slug"], py, a, b)
                else:
                    # finite-difference derivatives amplify last-digit differences between numpy and JS
                    rtol = {"dn_dl": 1e-7, "ng": 1e-9, "gvd": 1e-6, "D": 1e-6}.get(py, 1e-12)
                    assert b is not None and np.isclose(a, b, rtol=rtol, atol=1e-12), (case["slug"], py, a, b)
