"""Contributions: auth, validation, moderation, publication."""

from __future__ import annotations

import json

import numpy as np
import pytest

from materials_db.validation import spikes

from .conftest import SECRET, auth

WL = np.linspace(400, 800, 41)
GOOD_ROWS = [[float(w), 1.5 + 1e4 / w**2, 0.01] for w in WL]
BASE = {"material_id": "au", "dataset_name": "Lovelace and Turing 2025", "doi": "10.5555/test.1",
        "license": "CC-BY-4.0"}


def submit_json(client, body=None, headers=None, **over):
    payload = {**BASE, "data": GOOD_ROWS, **(body or {}), **over}
    return client.post("/v1/submissions", json=payload, headers=headers or auth())


def submit_file(client, content: str, filename: str, meta: dict, headers=None):
    return client.post("/v1/submissions", files={"file": (filename, content.encode(), "text/plain")},
                       data={"metadata": json.dumps(meta)}, headers=headers or auth())


def test_auth_required(client):
    assert client.post("/v1/submissions", json={}).status_code == 401
    bad = {"Authorization": "Bearer not-a-jwt"}
    r = client.post("/v1/submissions", json={}, headers=bad)
    assert r.status_code == 401 and r.json()["error"] == "unauthorized"
    from materials_db.auth import make_token
    wrong_aud = {"Authorization": f"Bearer {make_token(SECRET, sub='x', audience='compute')}"}
    assert client.post("/v1/submissions", json={}, headers=wrong_aud).status_code == 401


def test_json_submission_web_shape(client, resolver):
    r = submit_json(client)
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["status"] == "pending" and s["kind"] == "new_dataset" and s["material_id"]
    assert s["submitted_by"]["sub"] == "u1" and s["license"] == "CC-BY-4.0"
    assert s["data_summary"]["points"] == 41 and s["data"][0][0] == pytest.approx(400)
    assert s["reference_info"]["title"] == "A measured optical constant"  # DOI metadata fetched
    assert resolver.calls == ["10.5555/test.1"]


def test_multipart_csv_with_units_in_header(client):
    csv = "# my data\nwavelength (um),n,k\n" + "\n".join(f"{w / 1000},{n},{k}" for w, n, k in GOOD_ROWS)
    r = submit_file(client, csv, "data.csv", BASE)
    assert r.status_code == 201, r.text
    assert r.json()["data_summary"]["range_nm"] == pytest.approx([400, 800])


def test_multipart_permittivity_in_ev(client):
    ev = np.linspace(1.6, 3.0, 15)
    csv = "energy_eV,eps1,eps2\n" + "\n".join(f"{e},{-10 + e},{1.0}" for e in ev)
    r = submit_file(client, csv, "eps.csv", BASE)
    assert r.status_code == 201, r.text
    v = r.json()["validation"]
    assert v["stats"]["converted_from"] == "permittivity" and not any("descending" in w for w in v["warnings"])


def test_multipart_yaml(client):
    from .conftest import FIXTURE
    text = (FIXTURE / "database/data/main/Co/nk/Johnson.yml").read_text(encoding="utf-8")
    r = submit_file(client, text, "co.yml", {"material_id": "co", "license": "CC0-1.0",
                                             "doi": "10.1103/PhysRevB.9.5056"})
    assert r.status_code == 201, r.text
    assert r.json()["data_summary"]["type"] == "tabulated nk"


@pytest.mark.parametrize("change, message", [
    ({"data": [[400, 1.5, -0.1], [500, 1.5, 0.1]]}, "k must be >= 0"),
    ({"data": [[400, 1.5, 0.1], [600, 1.5, 0.1], [500, 1.5, 0.1]]}, "not monotonic"),
    ({"data": [[400, 1.5, 0.1], [400, 1.5, 0.1]]}, "duplicate wavelengths"),
    ({"data": [[400, 1.5, 0.1]]}, "at least two"),
    ({"data": [[400, -1.5, 0.1], [500, 1.5, 0.1]]}, "n must be positive"),
    ({"doi": None}, "must cite its source"),
    ({"doi": "nonsense"}, "not a valid DOI"),
    ({"license": "CC-BY-NC-4.0"}, "is not accepted"),
    ({"material_id": "unobtainium"}, "not found"),
    ({"material_id": None, "material_name": "Unobtainium", "category": "plasma"}, "unknown category"),
])
def test_validation_errors(client, change, message):
    r = submit_json(client, change)
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "validation_failed"
    assert any(message in e for e in body["detail"]["validation"]["errors"]), body


def test_units_must_be_declared(client):
    csv = "x,n,k\n" + "\n".join(f"{w},{n},{k}" for w, n, k in GOOD_ROWS)
    r = submit_file(client, csv, "data.csv", BASE)
    assert r.status_code == 422 and "units not declared" in r.text
    r = submit_file(client, csv, "data.csv", {**BASE, "wavelength_unit": "nm"})
    assert r.status_code == 201


def test_duplicate_doi_and_outlier_warnings(client):
    rows = [list(r) for r in GOOD_ROWS]
    rows[20][1] = 9.0  # a spike
    r = submit_json(client, {"doi": "10.1103/PhysRevB.6.4370", "data": rows})
    assert r.status_code == 201
    warnings = " ".join(r.json()["validation"]["warnings"])
    assert "possible duplicate" in warnings and "au/johnson-christy-1972" in warnings
    assert "possible outliers in n at 600 nm" in warnings
    assert r.json()["validation"]["stats"]["duplicate_doi"] is True


def test_spike_detector_ignores_smooth_resonances():
    x = np.linspace(-5, 5, 101)
    assert spikes(1 / (1 + x**2)) == []
    y = np.ones(50)
    y[25] = 3
    assert spikes(y) == [25]


def test_listing_visibility(client):
    submit_json(client, headers=auth(sub="alice"))
    submit_json(client, headers=auth(sub="bob"))
    mine = client.get("/v1/submissions", headers=auth(sub="alice")).json()
    assert mine["total"] == 1 and mine["items"][0]["submitted_by"]["sub"] == "alice"
    allp = client.get("/v1/submissions", params={"status": "pending"}, headers=auth("moderator", "mod")).json()
    assert allp["total"] == 2
    sid = mine["items"][0]["id"]
    assert client.get(f"/v1/submissions/{sid}", headers=auth(sub="bob")).status_code == 404
    assert client.get(f"/v1/submissions/{sid}/file", headers=auth(sub="alice")).status_code == 200


def test_review_approve_publishes_dataset(client):
    sid = submit_json(client).json()["id"]
    assert client.post(f"/v1/submissions/{sid}/review", json={"decision": "approve"},
                       headers=auth()).status_code == 403
    r = client.post(f"/v1/submissions/{sid}/review", json={"decision": "approve", "note": "looks good"},
                    headers=auth("moderator", "mod", "Mod"))
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["status"] == "approved" and s["review_note"] == "looks good" and s["reviewer"]["sub"] == "mod"
    assert [e["action"] for e in s["events"]] == ["submitted", "approved"]
    slug = s["result_dataset_slug"]
    assert slug == "au/lovelace-2025"
    d = client.get(f"/v1/datasets/{slug}").json()
    assert d["license"] == "CC-BY-4.0" and d["source"]["database"].startswith("Lightsmith")
    assert d["references"][0]["doi"] == "10.5555/test.1" and d["versions"][0]["submission_id"] == sid
    data = client.get(f"/v1/datasets/{slug}/data").json()
    assert data["wavelength_nm"] == pytest.approx(WL) and data["k"][0] == 0.01
    assert client.get("/v1/materials/au").json()["dataset_count"] == 2
    again = client.post(f"/v1/submissions/{sid}/review", json={"decision": "reject"}, headers=auth("moderator"))
    assert again.status_code == 409


def test_new_material_and_new_version(client):
    sid = submit_json(client, {"material_id": None, "material_name": "Unobtainium", "formula": "Ub2O3",
                               "category": "glass_oxide", "doi": "10.5555/test.2"}).json()["id"]
    s = client.post(f"/v1/submissions/{sid}/review", json={"decision": "approve"},
                    headers=auth("admin", "root")).json()
    m = client.get("/v1/materials/ub2o3").json()
    assert m["name"] == "Unobtainium" and m["category"] == "glass_oxide"
    ds = s["result_dataset_id"]
    rows = [[w, n + 0.01, k] for w, n, k in GOOD_ROWS]
    sid2 = submit_json(client, {"material_id": None, "target_dataset_id": ds, "data": rows,
                                "change_note": "recalibrated", "doi": "10.5555/test.2"}).json()["id"]
    s2 = client.post(f"/v1/submissions/{sid2}/review", json={"decision": "approve"},
                     headers=auth("moderator")).json()
    assert s2["kind"] == "new_version" and s2["result_dataset_id"] == ds
    versions = client.get(f"/v1/datasets/{ds}/versions").json()
    assert [v["version"] for v in versions] == [1, 2] and versions[1]["change_note"] == "recalibrated"
    v1 = client.get(f"/v1/datasets/{ds}/data", params={"version": 1}).json()["n"][0]
    v2 = client.get(f"/v1/datasets/{ds}/data").json()["n"][0]
    assert v2 == pytest.approx(v1 + 0.01)


def test_anisotropic_thermo_optic_submission(client):
    partner = "linbo3/zelmon-1997-o"
    sid = submit_json(client, {"material_id": "linbo3", "optical_axis": "E", "axis_partner_id": partner,
                               "state": "crystalline", "doi": "10.5555/test.3",
                               "thermo_optic": [{"dn_dT": 3.3e-5, "wavelength_nm": 1550}]}).json()["id"]
    s = client.post(f"/v1/submissions/{sid}/review", json={"decision": "approve"}, headers=auth("moderator")).json()
    d = client.get(f"/v1/datasets/{s['result_dataset_id']}").json()
    assert d["optical_axis"] == "e" and d["state"] == "crystalline"
    assert d["thermo_optic"] == [{"dn_dT": 3.3e-5, "origin": "contributor", "wavelength_nm": 1550.0}]
    assert d["anisotropy"]["axes"]["o"]["slug"] == partner
    bad = submit_json(client, {"optical_axis": "q", "thermo_optic": [{"dn_dT": 1.0}]})
    errors = " ".join(bad.json()["detail"]["validation"]["errors"])
    assert "optical_axis must be one of" in errors and "implausible" in errors


def test_reject(client):
    sid = submit_json(client).json()["id"]
    r = client.post(f"/v1/submissions/{sid}/review", json={"decision": "reject", "notes": "no raw data"},
                    headers=auth("moderator")).json()
    assert r["status"] == "rejected" and r["result_dataset_id"] is None and r["review_note"] == "no raw data"


def test_auth_not_configured(client):
    client.app.state.settings = client.app.state.settings.__class__(
        **{**client.app.state.settings.__dict__, "jwt_secret": None})
    r = submit_json(client)
    assert r.status_code == 503 and r.json()["error"] == "auth_not_configured"
