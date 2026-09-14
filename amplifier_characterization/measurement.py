"""Ablauf der S12-Messung: Amplituden- und Frequenzraster abfahren, CSV schreiben."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence

import numpy as np

from .config import Config
from .instrument import InstrumentError

CSV_FIELDS = [
    "timestamp",
    "frequency_hz",
    "p_in_dbm",
    "p_out_dbm",
    "gain_db",
]


class MeasurementError(RuntimeError):
    """Fehler waehrend der Messung."""


@dataclass(frozen=True)
class Point:
    """Ein Messpunkt der S12-Matrix."""

    timestamp: str
    frequency_hz: float
    p_in_dbm: float
    p_out_dbm: float
    gain_db: float


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def run_measurement(
    analyzer,
    config: Config,
    progress: Optional[Callable[[str], None]] = None,
    clock: Callable[[], str] = _now,
) -> List[Point]:
    """Faehrt pro Amplitudenstufe einen vollen Frequenzsweep und interpoliert.

    Fuer jede Stufe des Tracking-Generators wird ein kompletter Trace gelesen
    und auf das in der Config gewuenschte Frequenzraster interpoliert.
    """
    report = progress or (lambda _message: None)
    frequencies = config.frequency_grid
    levels = config.amplitude_grid

    analyzer.configure(config.instrument, float(frequencies[0]), float(frequencies[-1]))
    for entry in getattr(analyzer, "rejected", []):
        report(f"  Hinweis: Geraet hat abgelehnt: {entry}")
    axis = np.asarray(analyzer.frequency_axis(), dtype=float)
    if axis.size < 2:
        raise MeasurementError("Das Geraet meldet eine unbrauchbare Frequenzachse.")

    limits = getattr(analyzer, "generator_limits", lambda: None)()
    if limits is not None:
        low, high = limits
        outside = [
            float(level)
            for level in levels
            if level < low - 1e-9 or level > high + 1e-9
        ]
        if outside:
            raise MeasurementError(
                f"{len(outside)} Amplitudenstufen liegen ausserhalb des "
                f"Generatorbereichs ({low:+.2f} ... {high:+.2f} dBm), z.B. "
                f"{outside[0]:+.2f} dBm. amplitude.start_dbm / stop_dbm anpassen."
            )

    points: List[Point] = []
    analyzer.generator(True)
    try:
        for index, level in enumerate(levels, start=1):
            analyzer.set_generator_level(float(level))
            analyzer.sweep(config.instrument.settle_s)
            trace = np.asarray(analyzer.read_trace(), dtype=float)
            if trace.size != axis.size:
                raise MeasurementError(
                    f"Trace-Laenge {trace.size} passt nicht zur Frequenzachse "
                    f"({axis.size} Punkte)."
                )
            values = np.interp(frequencies, axis, trace)
            stamp = clock()
            for frequency, p_out in zip(frequencies, values):
                points.append(
                    Point(
                        timestamp=stamp,
                        frequency_hz=float(frequency),
                        p_in_dbm=float(level),
                        p_out_dbm=float(p_out),
                        gain_db=float(p_out) - float(level),
                    )
                )
            report(
                f"  [{index}/{len(levels)}] P_in = {level:+7.2f} dBm  ->  "
                f"P_out {values.min():+7.2f} ... {values.max():+7.2f} dBm"
            )
    finally:
        try:
            analyzer.generator(False)
        except InstrumentError:  # Generator-Abschaltung darf die Daten nicht kosten
            report("  Warnung: Tracking-Generator konnte nicht abgeschaltet werden.")
    return points


def write_csv(points: Sequence[Point], path: Path | str) -> Path:
    """Schreibt die Messpunkte als CSV (eine Zeile pro Frequenz/Pegel-Kombination)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for point in points:
            writer.writerow(asdict(point))
    return path


def read_csv(path: Path | str) -> List[Point]:
    """Liest eine zuvor geschriebene CSV wieder ein (z.B. zum Nachplotten)."""
    path = Path(path)
    points: List[Point] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            points.append(
                Point(
                    timestamp=row["timestamp"],
                    frequency_hz=float(row["frequency_hz"]),
                    p_in_dbm=float(row["p_in_dbm"]),
                    p_out_dbm=float(row["p_out_dbm"]),
                    gain_db=float(row["gain_db"]),
                )
            )
    return points


def to_matrix(points: Iterable[Point]):
    """Sortiert die Punkte in Raster zurueck: (frequenzen, pegel, p_out, gain)."""
    points = list(points)
    if not points:
        raise MeasurementError("Keine Messpunkte vorhanden.")
    frequencies = np.array(sorted({p.frequency_hz for p in points}))
    levels = np.array(sorted({p.p_in_dbm for p in points}))
    p_out = np.full((levels.size, frequencies.size), np.nan)
    freq_index = {value: i for i, value in enumerate(frequencies)}
    level_index = {value: i for i, value in enumerate(levels)}
    for point in points:
        p_out[level_index[point.p_in_dbm], freq_index[point.frequency_hz]] = point.p_out_dbm
    gain = p_out - levels[:, None]
    return frequencies, levels, p_out, gain
