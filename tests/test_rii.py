"""The ported refractiveindex.info parser, formulas, references and naming."""

from __future__ import annotations

import numpy as np
import pytest

from materials_db import rii
from materials_db.importer import Page, dataset_part, read_catalog
from materials_db.optics import Dispersion
from materials_db.photonics import library_module

from .conftest import FIXTURE

DATA = FIXTURE / "database" / "data"
ENTRIES = sorted(p for p in DATA.rglob("*.yml") if p.name != "about.yml")


def test_tabulated_blocks_are_lossless():
    text = (DATA / "main/Au/nk/Johnson.yml").read_text(encoding="utf-8")
    [block] = rii.entry_blocks(rii.parse_yaml(text))
    rows = [line.split() for line in text.split("data: |")[1].strip().splitlines()]
    assert block["type"] == "tabulated nk"
    assert block["wavelength_um"] == [float(r[0]) for r in rows]
    assert block["n"] == [float(r[1]) for r in rows]
    assert block["k"] == [float(r[2]) for r in rows]


def test_formula_sellmeier_fused_silica():
    blocks = rii.entry_blocks(rii.parse_yaml((DATA / "main/SiO2/nk/Malitson.yml").read_text(encoding="utf-8")))
    assert blocks[0]["type"] == "formula 1"
    n, k = Dispersion(blocks).nk([587.6])
    assert n[0] == pytest.approx(1.4585, abs=2e-4)  # n_d of fused silica
    assert k[0] == 0


def test_formula_with_tabulated_k():
    blocks = rii.entry_blocks(rii.parse_yaml((DATA / "specs/schott/optical/N-BK7.yml").read_text(encoding="utf-8")))
    assert rii.data_type(blocks) == "formula 2 + tabulated k"
    n, k = Dispersion(blocks).nk([587.6])
    assert n[0] == pytest.approx(1.5168, abs=1e-4)  # catalog n_d of N-BK7
    assert 0 < k[0] < 1e-6


def test_k_only_entry():
    blocks = rii.entry_blocks(rii.parse_yaml((DATA / "main/BaF2/nk/Bosomworth-5K.yml").read_text(encoding="utf-8")))
    d = Dispersion(blocks)
    assert not d.has_n and d.has_k
    n, k = d.nk([60_000.0])
    assert np.isnan(n[0]) and k[0] > 0


@pytest.mark.parametrize("num", range(1, 10))
def test_all_formula_types_evaluate(num):
    c = [0.5, 0.6, 0.07, 0.4, 0.1, 0.9, 9.0, 0.1, 0.2]
    n = rii.formula_n(num, c, np.array([0.5, 1.0]))
    assert n.shape == (2,)


def test_split_references_numbered_list():
    doc = rii.parse_yaml((DATA / "main/AgGaS2/nk/Boyd-o.yml").read_text(encoding="utf-8"))
    refs = rii.split_references(doc["REFERENCES"])
    assert [r["doi"] for r in refs] == ["10.1109/jqe.1971.1076588", "10.1364/ao.15.0305_1"]
    assert refs[0]["year"] == 1971 and refs[1]["year"] == 1976
    assert refs[0]["citation"].startswith("G. D. Boyd")
    assert refs[0]["title"].startswith("Linear and nonlinear optical properties")
    assert all(r["role"] == "source" for r in refs)


def test_split_references_data_source_mentions():
    html = ('A. B. Author. Title. <a href="https://doi.org/10.1/x"><i>J.</i> <b>1</b>, 2 (2020)</a><br>'
            '(fit of data from <a href="https://doi.org/10.1007/BF01">Smith-1931</a> and '
            '<a href="https://doi.org/10.1364/AO.12">Jones-1973</a>)')
    refs = rii.split_references(html)
    assert [r["role"] for r in refs] == ["source", "related", "related"]
    assert [r["citation"] for r in refs[1:]] == ["Smith-1931", "Jones-1973"]


@pytest.mark.parametrize("doi, out", [
    ("https://doi.org/10.1103/PhysRevB.6.4370", "10.1103/physrevb.6.4370"),
    ("doi:10.1364/AO.37.005271.", "10.1364/ao.37.005271"),
    ("10.1016/j.solmat.2008.06.009", "10.1016/j.solmat.2008.06.009"),
    ("not a doi", None),
])
def test_normalize_doi(doi, out):
    assert rii.normalize_doi(doi) == out


def _page(page, name):
    return Page("main", None, None, "X", None, None, page, name, None, "x", 0)


@pytest.mark.parametrize("page, name, part", [
    ("Johnson", "Johnson and Christy 1972: n,k 0.188–1.937 µm", "johnson-christy-1972"),
    ("Ferrera-298K", "Ferrera et al. 2019: n,k 0.245–1.45 µm; 298 K", "ferrera-2019-298k"),
    ("Green-2008", "Green 2008: n,k 0.25–1.45 µm", "green-2008"),
    ("Rakic-LD", "Rakić et al. 1998: Lorentz-Drude model; n,k", "rakic-1998-ld"),
    ("N-BK7", "N-BK7", "n-bk7"),
])
def test_dataset_slug_part(page, name, part):
    assert dataset_part(_page(page, name)) == part


def test_catalog_reader_keeps_duplicates_and_sections():
    pages = read_catalog((FIXTURE / "database" / "catalog-nk.yml").read_text(encoding="utf-8"))
    assert len(pages) == 15
    bk7 = [p for p in pages if p.shelf == "specs"]
    assert [p.listing for p in bk7] == [0, 1]
    assert pages[-1].section == "Selected data" and pages[-1].shelf == "popular_glass"
    assert next(p for p in pages if p.book == "Au").page_group == "Experimental data: bulk, thick film"


@pytest.mark.parametrize("path", ENTRIES, ids=lambda p: p.relative_to(DATA).as_posix())
def test_matches_lightsmith_library(path):
    """Same n, k as the library's reference implementation, on the library's own sampling."""
    materials = library_module("materials")
    if materials is None:
        pytest.skip("simulation library not installed")
    blocks = rii.entry_blocks(rii.parse_yaml(path.read_text(encoding="utf-8")))
    d = Dispersion(blocks)
    if not d.has_n:
        with pytest.raises(ValueError):
            materials.read_refractiveindex_yaml(path)
        return
    lib = materials.read_refractiveindex_yaml(path)
    wl = d.sample_nm()
    np.testing.assert_allclose(wl, lib.wavelength_nm, rtol=1e-12)
    n, k = d.nk(wl)
    np.testing.assert_allclose(n, lib.n_real, rtol=1e-12)
    np.testing.assert_allclose(k, lib.n_imag, rtol=1e-12, atol=1e-300)
