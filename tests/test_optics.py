"""Derived quantities, axes and grids."""

from __future__ import annotations

import math

import numpy as np
import pytest

from materials_db.optics import HC_EV_NM, Dispersion, build_grid, derived, from_nm, to_json_list, to_nm

BLOCKS = [{"type": "tabulated nk", "wavelength_um": [0.4, 0.5, 0.6], "n": [1.0, 2.0, 3.0], "k": [0.0, 1.0, 2.0]}]


def test_derived_quantities():
    wl, n, k = np.array([500.0]), np.array([2.0]), np.array([1.0])
    out = derived(wl, n, k, ["eps1", "eps2", "alpha", "skin_depth"])
    assert out["eps1"][0] == pytest.approx(3.0)  # n^2 - k^2
    assert out["eps2"][0] == pytest.approx(4.0)  # 2nk
    assert out["alpha"][0] == pytest.approx(4 * math.pi / 500e-7)  # cm^-1
    assert out["skin_depth"][0] == pytest.approx(500 / (2 * math.pi))
    assert np.isnan(derived(wl, n, np.array([0.0]), ["skin_depth"])["skin_depth"][0])


@pytest.mark.parametrize("axis", ["wavelength_nm", "wavelength_um", "ev", "wavenumber"])
def test_axes_round_trip(axis):
    wl = np.array([250.0, 633.0, 10_000.0])
    np.testing.assert_allclose(to_nm(from_nm(wl, axis), axis), wl)


def test_axis_values():
    assert from_nm([HC_EV_NM], "ev")[0] == pytest.approx(1.0)
    assert from_nm([1000.0], "wavenumber")[0] == pytest.approx(10_000.0)
    assert from_nm([1000.0], "wavelength_um")[0] == pytest.approx(1.0)


def test_interpolation_and_no_extrapolation():
    d = Dispersion(BLOCKS)
    n, k = d.nk([450.0, 399.0, 601.0, 600.0])
    assert n[0] == pytest.approx(1.5) and k[0] == pytest.approx(0.5)
    assert np.isnan(n[1]) and np.isnan(n[2])
    assert n[3] == 3.0


def test_partial_k_is_zero_outside_and_noted():
    d = Dispersion([{"type": "tabulated n", "wavelength_um": [0.4, 0.8], "n": [1.5, 1.4]},
                    {"type": "tabulated k", "wavelength_um": [0.5, 0.6], "k": [0.1, 0.2]}])
    _, k = d.nk([450.0, 550.0])
    assert k[0] == 0 and k[1] == pytest.approx(0.15)
    assert any("k data cover" in note for note in d.notes)


def test_grid_native_points_in_range():
    w = []
    x = build_grid("wavelength_nm", 400, 600, 450, None, None, np.array([400.0, 500.0, 600.0]), w)
    assert x.tolist() == [500.0, 600.0] and not w


def test_grid_uniform_in_energy_and_clip_warning():
    w = []
    x = build_grid("ev", 400, 600, 1.0, 3.0, 5, None, w)
    assert x[0] == pytest.approx(HC_EV_NM / 600) and x[-1] == pytest.approx(3.0)  # clipped below, kept above
    np.testing.assert_allclose(np.diff(x), np.diff(x)[0])
    assert "clipped" in w[0]


def test_grid_rejects_disjoint_range():
    with pytest.raises(ValueError, match="does not overlap"):
        build_grid("wavelength_nm", 400, 600, 700, 800, None, None, [])


def test_json_list_nulls():
    assert to_json_list([1.0, float("nan"), float("inf")]) == [1.0, None, None]
