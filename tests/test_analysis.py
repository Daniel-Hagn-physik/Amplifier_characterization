"""Tests fuer Kleinsignalfit, Kompressionspunkt und Kennwertausgabe."""

from __future__ import annotations

import numpy as np
import pytest

from amplifier_characterization import analysis as ana
from amplifier_characterization.measurement import MeasurementError, Point


def make_amplifier(levels, frequencies, gain=10.0, p_sat=5.0, corrected=True):
    """Verstaerker mit weicher Saettigung als Testdatensatz."""
    points = []
    for level in levels:
        for frequency in frequencies:
            linear = level + gain
            p_out = p_sat - np.logaddexp(0.0, p_sat - linear)
            points.append(
                Point(
                    timestamp="t",
                    frequency_hz=float(frequency),
                    p_in_dbm=float(level),
                    p_out_dbm=float(p_out),
                    gain_db=float(p_out - level),
                    p_ref_dbm=float(level) if corrected else None,
                    gain_corr_db=float(p_out - level) if corrected else None,
                    p_amp_dbm=float(p_out) if corrected else None,
                )
            )
    return points


@pytest.fixture
def amplifier():
    return make_amplifier(np.arange(-30.0, 0.1, 1.0), [80e6, 100e6, 120e6])


# -- Bausteine ----------------------------------------------------------
def test_fit_line_recovers_slope_and_intercept():
    slope, intercept = ana.fit_line([0.0, 1.0, 2.0], [1.0, 3.0, 5.0])
    assert slope == pytest.approx(2.0)
    assert intercept == pytest.approx(1.0)


def test_fit_line_needs_two_points():
    with pytest.raises(MeasurementError, match="mindestens 2"):
        ana.fit_line([1.0], [1.0])


def test_linear_region_stops_at_the_knee():
    levels = np.array([-30.0, -20.0, -10.0, 0.0])
    gain = np.array([10.0, 10.0, 9.0, 5.0])
    assert ana.linear_region(levels, gain, tolerance_db=0.25) == (-30.0, -20.0)


def test_linear_region_with_immediate_drop():
    levels = np.array([-30.0, -20.0])
    gain = np.array([10.0, 2.0])
    assert ana.linear_region(levels, gain) == (-30.0, -20.0)


def test_linear_region_uses_everything_when_flat():
    levels = np.array([-30.0, -20.0, -10.0])
    gain = np.array([10.0, 10.0, 10.0])
    assert ana.linear_region(levels, gain) == (-30.0, -10.0)


def test_compression_point_interpolates():
    levels = np.array([-10.0, 0.0, 10.0])
    p_out = np.array([0.0, 10.0, 19.0])  # bei +10 fehlt 1 dB
    point = ana.compression_point(levels, p_out, slope=1.0, intercept=10.0)
    assert point is not None
    assert point[0] == pytest.approx(10.0)
    assert point[1] == pytest.approx(19.0)


def test_compression_point_returns_none_without_compression():
    levels = np.array([-10.0, 0.0])
    p_out = np.array([0.0, 10.0])
    assert ana.compression_point(levels, p_out, 1.0, 10.0) is None


def test_compression_point_returns_none_if_first_level_already_compressed():
    levels = np.array([-10.0, 0.0])
    p_out = np.array([-5.0, 10.0])
    assert ana.compression_point(levels, p_out, 1.0, 10.0) is None


# -- Gesamtauswertung ---------------------------------------------------
def test_analyze_finds_gain_and_compression(amplifier):
    result = ana.analyze(amplifier)
    assert result.gain_mean == pytest.approx(10.0, abs=0.1)
    assert result.gain_ripple == pytest.approx(0.0, abs=1e-6)
    assert result.slope_mean == pytest.approx(1.0, abs=0.02)
    assert result.p1db_in is not None
    assert result.p1db_out == pytest.approx(result.p1db_in + 10.0 - 1.0, abs=0.2)
    assert result.cable_corrected
    assert len(result.results) == 3


def test_analyze_accepts_explicit_linear_range(amplifier):
    result = ana.analyze(amplifier, linear_range=(-30.0, -20.0))
    assert result.linear_low_dbm == -30.0
    assert result.linear_high_dbm == -20.0


def test_analyze_rejects_inverted_range(amplifier):
    with pytest.raises(MeasurementError, match="Ungueltiger Fitbereich"):
        ana.analyze(amplifier, linear_range=(0.0, -30.0))


def test_analyze_rejects_too_narrow_range(amplifier):
    with pytest.raises(MeasurementError, match="weniger als"):
        ana.analyze(amplifier, linear_range=(-30.4, -29.6))


def test_analyze_needs_two_levels():
    points = make_amplifier([-30.0], [80e6])
    with pytest.raises(MeasurementError, match="mindestens 2 Amplitudenstufen"):
        ana.analyze(points)


def test_analyze_without_compression_in_range():
    points = make_amplifier(np.arange(-30.0, -19.9, 1.0), [80e6], p_sat=40.0)
    result = ana.analyze(points)
    assert result.p1db_in is None
    assert result.p1db_out is None
    assert "nicht erreicht" in "\n".join(ana.summary_lines(result))


def test_analyze_marks_missing_cable_correction():
    points = make_amplifier(np.arange(-30.0, 0.1, 5.0), [80e6], corrected=False)
    result = ana.analyze(points)
    assert not result.cable_corrected
    assert "NEIN" in "\n".join(ana.summary_lines(result))


def test_summary_lines_report_the_key_figures(amplifier):
    text = "\n".join(ana.summary_lines(ana.analyze(amplifier)))
    for expected in ("Kleinsignalverstaerkung", "Steigung des Fits",
                     "Kompression", "Welligkeit"):
        assert expected in text


# -- Dateien ------------------------------------------------------------
def test_write_summary_creates_markdown(tmp_path, amplifier):
    path = ana.write_summary(ana.analyze(amplifier), tmp_path / "s.md", "quelle.csv")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Auswertung")
    assert "quelle.csv" in text


def test_write_summary_warns_without_reference(tmp_path):
    points = make_amplifier(np.arange(-30.0, 0.1, 5.0), [80e6], corrected=False)
    path = ana.write_summary(ana.analyze(points), tmp_path / "s.md")
    assert "Achtung" in path.read_text(encoding="utf-8")


def test_write_compression_csv_has_one_row_per_frequency(tmp_path, amplifier):
    import csv as csv_module

    path = ana.write_compression_csv(ana.analyze(amplifier), tmp_path / "c.csv")
    rows = list(csv_module.DictReader(path.open(encoding="utf-8")))
    assert len(rows) == 3
    assert rows[0]["p1db_in_dbm"]


def test_write_compression_csv_leaves_missing_values_empty(tmp_path):
    import csv as csv_module

    points = make_amplifier(np.arange(-30.0, -19.9, 1.0), [80e6], p_sat=40.0)
    path = ana.write_compression_csv(ana.analyze(points), tmp_path / "c.csv")
    rows = list(csv_module.DictReader(path.open(encoding="utf-8")))
    assert rows[0]["p1db_in_dbm"] == ""


# -- OIP3-Schaetzung ----------------------------------------------------
def test_oip3_estimate_follows_the_rule_of_thumb(amplifier):
    result = ana.analyze(amplifier)
    assert result.oip3_estimate == pytest.approx(
        result.p1db_out + ana.OIP3_OFFSET_DB
    )
    assert result.results[0].oip3_estimate_dbm == pytest.approx(
        result.results[0].p1db_out_dbm + ana.OIP3_OFFSET_DB
    )


def test_oip3_estimate_is_none_without_compression():
    points = make_amplifier(np.arange(-30.0, -19.9, 1.0), [80e6], p_sat=40.0)
    result = ana.analyze(points)
    assert result.oip3_estimate is None
    assert result.results[0].oip3_estimate_dbm is None
    assert "OIP3" not in "\n".join(ana.summary_lines(result))


def test_summary_marks_oip3_as_an_estimate(amplifier):
    text = "\n".join(ana.summary_lines(ana.analyze(amplifier)))
    assert "nur geschaetzt" in text
    assert "keine Messung" in text


def test_written_summary_explains_the_oip3_limitation(tmp_path, amplifier):
    path = ana.write_summary(ana.analyze(amplifier), tmp_path / "s.md")
    text = path.read_text(encoding="utf-8")
    assert "zwei Toene" in text


def test_compression_csv_contains_the_oip3_column(tmp_path, amplifier):
    import csv as csv_module

    path = ana.write_compression_csv(ana.analyze(amplifier), tmp_path / "c.csv")
    rows = list(csv_module.DictReader(path.open(encoding="utf-8")))
    assert rows[0]["oip3_estimate_dbm"]
