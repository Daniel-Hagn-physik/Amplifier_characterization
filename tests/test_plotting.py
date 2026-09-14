"""Tests fuer die Plot-Erzeugung (nur Existenz und Aufrufwege, Backend Agg)."""

from __future__ import annotations

import pytest

from amplifier_characterization import plotting
from amplifier_characterization.measurement import Point


def make_points(frequencies, levels, gain=10.0):
    return [
        Point(
            timestamp="2026-09-14T10:00:00",
            frequency_hz=frequency,
            p_in_dbm=level,
            p_out_dbm=level + gain,
            gain_db=gain,
        )
        for level in levels
        for frequency in frequencies
    ]


@pytest.fixture
def points():
    return make_points([1e6, 3e6, 5e6], [-20.0, -10.0, 0.0])


def test_ramp_single_entry_uses_dark_end():
    assert len(plotting._ramp(1)) == 1


def test_ramp_spans_light_to_dark():
    colors = plotting._ramp(4)
    assert len(colors) == 4
    assert sum(colors[0][:3]) > sum(colors[-1][:3])


def test_plot_power_transfer(tmp_path, points):
    path = plotting.plot_power_transfer(points, tmp_path / "transfer.png")
    assert path.stat().st_size > 0


def test_plot_frequency_response(tmp_path, points):
    path = plotting.plot_frequency_response(points, tmp_path / "response.png")
    assert path.stat().st_size > 0


def test_plot_gain_map(tmp_path, points):
    path = plotting.plot_gain_map(points, tmp_path / "gain.png")
    assert path.stat().st_size > 0


def test_plots_without_colorbar_for_single_values(tmp_path):
    single = make_points([1e6], [-10.0, -5.0])
    assert plotting.plot_power_transfer(single, tmp_path / "a.png").exists()
    single_level = make_points([1e6, 2e6], [-10.0])
    assert plotting.plot_frequency_response(single_level, tmp_path / "b.png").exists()


def test_plot_all_writes_three_files(tmp_path, points):
    paths = plotting.plot_all(points, tmp_path / "run", stem="m")
    assert [path.name for path in paths] == [
        "m_power_transfer.png",
        "m_frequency_response.png",
        "m_gain_map.png",
    ]
    assert all(path.exists() for path in paths)
