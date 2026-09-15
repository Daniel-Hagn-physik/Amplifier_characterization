"""Plots der S12-Messung (Pegelkennlinien, Frequenzgang, Gain-Karte)."""

from __future__ import annotations

from pathlib import Path
from typing import List, Sequence

import matplotlib

# Agg zeichnet in eine Datei statt in ein Fenster - noetig, damit die Plots
# auch ohne Bildschirm entstehen (Messrechner ueber SSH, Testlauf). force=False
# laesst ein bereits gewaehltes interaktives Backend in Ruhe.
matplotlib.use("Agg", force=False)

# Die Importe stehen bewusst erst hinter matplotlib.use(): pyplot legt beim
# Import das Backend fest. Daher die noqa-Vermerke fuer den Style-Checker.
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.cm import ScalarMappable  # noqa: E402
from matplotlib.colors import Normalize  # noqa: E402

from .measurement import Point, has_reference, to_matrix  # noqa: E402

# Eine einzelne Hue, hell -> dunkel: die Kurvenschar codiert eine Groesse
# (Frequenz bzw. Eingangspegel), keine Kategorien.
SEQUENTIAL = "Blues"
RAMP_RANGE = (0.40, 0.95)
LINE_WIDTH = 1.8
GRID_KWARGS = {"color": "0.85", "linewidth": 0.6}


def _ramp(count: int):
    """Farben einer einzelnen Hue von hell nach dunkel."""
    cmap = plt.get_cmap(SEQUENTIAL)
    if count == 1:
        return [cmap(RAMP_RANGE[1])]
    positions = np.linspace(RAMP_RANGE[0], RAMP_RANGE[1], count)
    return [cmap(position) for position in positions]


def _style(axis, title: str, xlabel: str, ylabel: str) -> None:
    axis.set_title(title, loc="left", fontsize=11)
    axis.set_xlabel(xlabel)
    axis.set_ylabel(ylabel)
    axis.grid(True, **GRID_KWARGS)
    axis.set_axisbelow(True)
    for side in ("top", "right"):
        axis.spines[side].set_visible(False)


def _colorbar(figure, axis, values: np.ndarray, label: str) -> None:
    cmap = plt.get_cmap(SEQUENTIAL)
    mappable = ScalarMappable(
        norm=Normalize(vmin=float(values.min()), vmax=float(values.max())), cmap=cmap
    )
    mappable.set_array([])
    figure.colorbar(mappable, ax=axis, label=label)


def plot_power_transfer(points: Sequence[Point], path: Path | str) -> Path:
    """P_out ueber P_in, eine Kurve je Frequenz (Kompression sichtbar)."""
    frequencies, levels, p_out, _gain = to_matrix(points)
    figure, axis = plt.subplots(figsize=(7.0, 4.6), constrained_layout=True)
    for index, color in enumerate(_ramp(frequencies.size)):
        axis.plot(levels, p_out[:, index], color=color, linewidth=LINE_WIDTH)
    axis.plot(
        levels,
        levels,
        color="0.55",
        linewidth=1.0,
        linestyle="--",
        label="P_out = P_in (0 dB)",
    )
    _style(
        axis,
        "Ausgangs- ueber Eingangsleistung",
        "P_in / dBm (Tracking-Generator)",
        "P_out / dBm",
    )
    axis.legend(frameon=False, fontsize=9)
    if frequencies.size > 1:
        _colorbar(figure, axis, frequencies / 1e6, "Frequenz / MHz")
    figure.savefig(path, dpi=200)
    plt.close(figure)
    return Path(path)


def plot_frequency_response(points: Sequence[Point], path: Path | str) -> Path:
    """P_out ueber der Frequenz, eine Kurve je Eingangspegel."""
    frequencies, levels, p_out, _gain = to_matrix(points)
    figure, axis = plt.subplots(figsize=(7.0, 4.6), constrained_layout=True)
    for index, color in enumerate(_ramp(levels.size)):
        axis.plot(frequencies / 1e6, p_out[index, :], color=color, linewidth=LINE_WIDTH)
    _style(axis, "Frequenzgang", "Frequenz / MHz", "P_out / dBm")
    if levels.size > 1:
        _colorbar(figure, axis, levels, "P_in / dBm")
    figure.savefig(path, dpi=200)
    plt.close(figure)
    return Path(path)


def _map(
    frequencies: np.ndarray,
    levels: np.ndarray,
    values: np.ndarray,
    path: Path | str,
    title: str,
    colorbar_label: str,
) -> Path:
    """Gemeinsames Geruest der 2D-Karten ueber Frequenz und Eingangspegel."""
    figure, axis = plt.subplots(figsize=(7.0, 4.6), constrained_layout=True)
    mesh = axis.pcolormesh(
        frequencies / 1e6, levels, values, cmap=SEQUENTIAL, shading="nearest"
    )
    _style(axis, title, "Frequenz / MHz", "P_in / dBm")
    axis.grid(False)
    figure.colorbar(mesh, ax=axis, label=colorbar_label)
    figure.savefig(path, dpi=200)
    plt.close(figure)
    return Path(path)


def plot_gain_map(points: Sequence[Point], path: Path | str) -> Path:
    """Verstaerkung als Karte ueber Frequenz und Eingangspegel."""
    frequencies, levels, _p_out, gain = to_matrix(points)
    title = "Verstaerkung S12"
    title += " (kabelkorrigiert)" if has_reference(points) else " (inkl. Kabeldaempfung)"
    return _map(frequencies, levels, gain, path, title, "Gain / dB")


def plot_power_map(points: Sequence[Point], path: Path | str) -> Path:
    """Ausgangsleistung als Karte ueber Frequenz und Eingangspegel."""
    frequencies, levels, p_out, _gain = to_matrix(points)
    plane = "Verstaerkerausgang" if has_reference(points) else "Analyzer-Eingang"
    return _map(
        frequencies,
        levels,
        p_out,
        path,
        f"Ausgangsleistung ueber Frequenz und Eingangspegel ({plane})",
        "P_out / dBm",
    )


def plot_all(points: Sequence[Point], directory: Path | str, stem: str = "s12") -> List[Path]:
    """Erzeugt alle drei Standardplots im Ausgabeverzeichnis."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    return [
        plot_power_transfer(points, directory / f"{stem}_power_transfer.png"),
        plot_frequency_response(points, directory / f"{stem}_frequency_response.png"),
        plot_gain_map(points, directory / f"{stem}_gain_map.png"),
        plot_power_map(points, directory / f"{stem}_power_map.png"),
    ]


# -- Auswertungsplots ---------------------------------------------------
ACCENT = "#1f5fa9"
BAND_ALPHA = 0.18
REFERENCE_COLOR = "0.45"
MARKER_SIZE = 9


def _band(axis, x, matrix, label: str) -> None:
    """Mittelwert ueber die Frequenz plus Min/Max-Band.

    Das Band ist der volle Streubereich ueber die Frequenz, keine
    Standardabweichung: bei wenigen Frequenzpunkten ist die Spanne
    ehrlicher und zeigt sofort, ob eine Frequenz aus der Reihe faellt.
    """
    axis.fill_between(
        x, matrix.min(axis=1), matrix.max(axis=1),
        color=ACCENT, alpha=BAND_ALPHA, linewidth=0,
        label="Streuung ueber die Frequenz",
    )
    axis.plot(x, matrix.mean(axis=1), color=ACCENT, linewidth=LINE_WIDTH, label=label)


def plot_transfer_with_fit(analysis, path: Path | str) -> Path:
    """Kennlinie mit extrapoliertem Kleinsignalfit und Kompressionspunkt."""
    levels = analysis.levels_dbm
    figure, axis = plt.subplots(figsize=(7.0, 4.6), constrained_layout=True)
    _band(axis, levels, analysis.p_out_dbm, "Messung (Mittel)")

    # Achsenabschnitt aus dem Kleinsignalbereich, nicht aus dem Endpunkt:
    # die gestrichelte Gerade soll zeigen, wo der Verstaerker ohne Kompression
    # landen wuerde. Aus den obersten Pegeln gerechnet laege sie zu tief und
    # die Kompression waere im Bild kaum noch zu sehen.
    slope = analysis.slope_mean
    mask = (levels >= analysis.linear_low_dbm - 1e-9) & (
        levels <= analysis.linear_high_dbm + 1e-9
    )
    intercept = float(
        np.mean(analysis.p_out_dbm[mask].mean(axis=1) - slope * levels[mask])
    )
    axis.plot(
        levels, slope * levels + intercept,
        color=REFERENCE_COLOR, linewidth=1.2, linestyle="--",
        label=f"Linearer Fit ({analysis.gain_mean:+.2f} dB)",
    )

    if analysis.p1db_in is not None:
        axis.plot(
            [analysis.p1db_in], [analysis.p1db_out],
            marker="o", markersize=MARKER_SIZE, color=ACCENT,
            markeredgecolor="white", markeredgewidth=1.5, linestyle="none",
            label=f"{analysis.compression_db:.0f} dB Kompression",
        )
        axis.annotate(
            f"P$_{{1dB}}$: {analysis.p1db_in:+.1f} / {analysis.p1db_out:+.1f} dBm",
            xy=(analysis.p1db_in, analysis.p1db_out),
            xytext=(-10, 14), textcoords="offset points",
            ha="right", fontsize=9, color="0.25",
        )
    _style(
        axis,
        "Kennlinie mit Kleinsignalfit",
        "P_in / dBm",
        "P_out / dBm",
    )
    axis.legend(frameon=False, fontsize=9, loc="upper left")
    figure.savefig(path, dpi=200)
    plt.close(figure)
    return Path(path)


def plot_gain_compression(analysis, path: Path | str) -> Path:
    """Verstaerkung ueber dem Eingangspegel - zeigt den Einbruch direkt."""
    levels = analysis.levels_dbm
    figure, axis = plt.subplots(figsize=(7.0, 4.6), constrained_layout=True)
    _band(axis, levels, analysis.gain_db, "Verstaerkung (Mittel)")
    axis.axhline(
        analysis.gain_mean, color=REFERENCE_COLOR, linewidth=1.2, linestyle="--",
        label=f"Kleinsignal {analysis.gain_mean:+.2f} dB",
    )
    axis.axhline(
        analysis.gain_mean - analysis.compression_db,
        color=REFERENCE_COLOR, linewidth=1.0, linestyle=":",
        label=f"-{analysis.compression_db:.0f} dB",
    )
    if analysis.p1db_in is not None:
        axis.plot(
            [analysis.p1db_in], [analysis.gain_mean - analysis.compression_db],
            marker="o", markersize=MARKER_SIZE, color=ACCENT,
            markeredgecolor="white", markeredgewidth=1.5, linestyle="none",
        )
        axis.annotate(
            f"{analysis.p1db_in:+.1f} dBm",
            xy=(analysis.p1db_in, analysis.gain_mean - analysis.compression_db),
            xytext=(-8, -16), textcoords="offset points",
            ha="right", fontsize=9, color="0.25",
        )
    _style(axis, "Kompression", "P_in / dBm", "Verstaerkung / dB")
    axis.legend(frameon=False, fontsize=9, loc="lower left")
    figure.savefig(path, dpi=200)
    plt.close(figure)
    return Path(path)


def plot_gain_vs_frequency(analysis, path: Path | str) -> Path:
    """Kleinsignalverstaerkung und Kompressionspunkt ueber der Frequenz."""
    megahertz = analysis.frequencies_hz / 1e6
    gains = np.array([r.gain_db for r in analysis.results])
    figure, (top, bottom) = plt.subplots(
        2, 1, figsize=(7.0, 6.0), sharex=True, constrained_layout=True
    )
    top.plot(megahertz, gains, color=ACCENT, linewidth=LINE_WIDTH)
    _style(top, "Kleinsignalverstaerkung", "", "Verstaerkung / dB")

    valid = [
        (r.frequency_hz / 1e6, r.p1db_out_dbm)
        for r in analysis.results
        if r.p1db_out_dbm is not None
    ]
    if valid:
        x, y = zip(*valid)
        bottom.plot(x, y, color=ACCENT, linewidth=LINE_WIDTH)
    else:
        bottom.text(
            0.5, 0.5, "Kompression im gemessenen Bereich nicht erreicht",
            transform=bottom.transAxes, ha="center", va="center",
            fontsize=10, color="0.35",
        )
    _style(
        bottom,
        f"Ausgangsleistung bei {analysis.compression_db:.0f} dB Kompression",
        "Frequenz / MHz",
        "P_out / dBm",
    )
    figure.savefig(path, dpi=200)
    plt.close(figure)
    return Path(path)


def plot_analysis(analysis, points, directory: Path | str) -> List[Path]:
    """Alle Auswertungsplots inklusive der 2D-Karte."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    return [
        plot_transfer_with_fit(analysis, directory / "analysis_transfer.png"),
        plot_gain_compression(analysis, directory / "analysis_compression.png"),
        plot_gain_vs_frequency(analysis, directory / "analysis_frequency.png"),
        plot_gain_map(points, directory / "analysis_gain_map.png"),
        plot_power_map(points, directory / "analysis_power_map.png"),
    ]
