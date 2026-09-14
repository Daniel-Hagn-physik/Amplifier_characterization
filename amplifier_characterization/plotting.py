"""Plots der S12-Messung (Pegelkennlinien, Frequenzgang, Gain-Karte)."""

from __future__ import annotations

from pathlib import Path
from typing import List, Sequence

import matplotlib

matplotlib.use("Agg", force=False)

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.cm import ScalarMappable  # noqa: E402
from matplotlib.colors import Normalize  # noqa: E402

from .measurement import Point, to_matrix  # noqa: E402

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


def plot_gain_map(points: Sequence[Point], path: Path | str) -> Path:
    """Verstaerkung als Karte ueber Frequenz und Eingangspegel."""
    frequencies, levels, _p_out, gain = to_matrix(points)
    figure, axis = plt.subplots(figsize=(7.0, 4.6), constrained_layout=True)
    mesh = axis.pcolormesh(
        frequencies / 1e6,
        levels,
        gain,
        cmap=SEQUENTIAL,
        shading="nearest",
    )
    _style(axis, "Verstaerkung S12", "Frequenz / MHz", "P_in / dBm")
    axis.grid(False)
    figure.colorbar(mesh, ax=axis, label="Gain / dB")
    figure.savefig(path, dpi=200)
    plt.close(figure)
    return Path(path)


def plot_all(points: Sequence[Point], directory: Path | str, stem: str = "s12") -> List[Path]:
    """Erzeugt alle drei Standardplots im Ausgabeverzeichnis."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    return [
        plot_power_transfer(points, directory / f"{stem}_power_transfer.png"),
        plot_frequency_response(points, directory / f"{stem}_frequency_response.png"),
        plot_gain_map(points, directory / f"{stem}_gain_map.png"),
    ]
