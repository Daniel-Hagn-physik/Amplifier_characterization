"""Auswertung einer S12-Messung: Kleinsignalfit, Kompressionspunkt, Kennwerte."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .measurement import MeasurementError, Point, has_reference, to_matrix

DEFAULT_TOLERANCE_DB = 0.25
DEFAULT_COMPRESSION_DB = 1.0

OIP3_OFFSET_DB = 9.6
"""Abstand OIP3 - P1dB fuer eine rein kubische, gedaechtnislose Kennlinie.

Nur eine Faustregel: derselbe Term dritter Ordnung erzeugt Kompression und
Intermodulation, daher der feste Abstand. Reale Verstaerker weichen ab -
gemessen werden kann OIP3 nur mit zwei Toenen.
"""


@dataclass(frozen=True)
class FrequencyResult:
    """Kennwerte bei einer Frequenz."""

    frequency_hz: float
    gain_db: float
    slope: float
    p1db_in_dbm: Optional[float]
    p1db_out_dbm: Optional[float]
    p_out_max_dbm: float

    @property
    def oip3_estimate_dbm(self) -> Optional[float]:
        """Grobe OIP3-Schaetzung aus dem Kompressionspunkt (Faustregel)."""
        if self.p1db_out_dbm is None:
            return None
        return self.p1db_out_dbm + OIP3_OFFSET_DB


@dataclass(frozen=True)
class Analysis:
    """Ergebnis der Auswertung einer kompletten Messung."""

    frequencies_hz: np.ndarray
    levels_dbm: np.ndarray
    p_out_dbm: np.ndarray
    gain_db: np.ndarray
    results: List[FrequencyResult]
    linear_low_dbm: float
    linear_high_dbm: float
    compression_db: float
    cable_corrected: bool

    @property
    def gain_mean(self) -> float:
        return float(np.mean([r.gain_db for r in self.results]))

    @property
    def gain_ripple(self) -> float:
        gains = [r.gain_db for r in self.results]
        return float(max(gains) - min(gains))

    @property
    def slope_mean(self) -> float:
        return float(np.mean([r.slope for r in self.results]))

    def _collect(self, attribute: str) -> List[float]:
        return [
            getattr(r, attribute)
            for r in self.results
            if getattr(r, attribute) is not None
        ]

    @property
    def p1db_in(self) -> Optional[float]:
        values = self._collect("p1db_in_dbm")
        return float(np.mean(values)) if values else None

    @property
    def p1db_out(self) -> Optional[float]:
        values = self._collect("p1db_out_dbm")
        return float(np.mean(values)) if values else None

    @property
    def oip3_estimate(self) -> Optional[float]:
        """Gemittelte OIP3-Schaetzung; None, wenn keine Kompression gefunden."""
        if self.p1db_out is None:
            return None
        return self.p1db_out + OIP3_OFFSET_DB

    @property
    def p_out_max(self) -> float:
        return float(np.mean([r.p_out_max_dbm for r in self.results]))


def fit_line(x: Sequence[float], y: Sequence[float]) -> Tuple[float, float]:
    """Ausgleichsgerade y = slope * x + intercept."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size < 2:
        raise MeasurementError(
            "Fuer den linearen Fit werden mindestens 2 Amplitudenstufen gebraucht."
        )
    slope, intercept = np.polyfit(x, y, 1)
    return float(slope), float(intercept)


def linear_region(
    levels: np.ndarray, gain: np.ndarray, tolerance_db: float = DEFAULT_TOLERANCE_DB
) -> Tuple[float, float]:
    """Bereich, in dem die Verstaerkung noch nicht eingebrochen ist.

    Ausgehend vom kleinsten Pegel, solange die Verstaerkung um weniger als
    ``tolerance_db`` unter ihrem Maximum liegt.
    """
    # Bezug ist das Maximum der gemessenen Verstaerkung, nicht der Wert beim
    # kleinsten Pegel: dort sitzt oft noch Rauschen mit drin.
    threshold = float(np.max(gain)) - tolerance_db
    last = 0
    for index, value in enumerate(gain):
        if value < threshold:
            break
        last = index
    if last == 0:
        # Schon die zweite Stufe liegt unter der Toleranz. Statt aufzugeben
        # werden zwei Punkte genommen - das Minimum fuer eine Gerade. Der Fit
        # ist dann schlecht gestuetzt; wer es besser braucht, gibt den Bereich
        # mit --linear-range von Hand vor.
        last = min(1, levels.size - 1)
    return float(levels[0]), float(levels[last])


def compression_point(
    levels: np.ndarray,
    p_out: np.ndarray,
    slope: float,
    intercept: float,
    compression_db: float = DEFAULT_COMPRESSION_DB,
) -> Optional[Tuple[float, float]]:
    """Punkt, an dem die Kennlinie um ``compression_db`` unter den Fit faellt.

    Zwischen den beiden benachbarten Messpunkten wird linear interpoliert; ist
    die Kompression im gemessenen Bereich nie erreicht, wird None geliefert.
    """
    ideal = slope * levels + intercept
    deviation = ideal - p_out
    hits = np.where(deviation >= compression_db)[0]
    # hits[0] == 0 heisst: schon der kleinste gemessene Pegel liegt unter dem
    # Fit. Dann ist die Kompression nicht erfasst, sondern liegt unterhalb des
    # Messbereichs - ein Wert waere Extrapolation und wird nicht geliefert.
    if hits.size == 0 or hits[0] == 0:
        return None
    index = int(hits[0])
    x0, x1 = float(levels[index - 1]), float(levels[index])
    d0, d1 = float(deviation[index - 1]), float(deviation[index])
    if d1 == d0:  # pragma: no cover - durch die Suche ausgeschlossen
        return None
    p_in = x0 + (compression_db - d0) * (x1 - x0) / (d1 - d0)
    return p_in, slope * p_in + intercept - compression_db


def analyze(
    points: Sequence[Point],
    tolerance_db: float = DEFAULT_TOLERANCE_DB,
    compression_db: float = DEFAULT_COMPRESSION_DB,
    linear_range: Optional[Tuple[float, float]] = None,
) -> Analysis:
    """Wertet eine Messung aus: Fit im Kleinsignalbereich und Kompressionspunkt."""
    frequencies, levels, p_out, gain = to_matrix(points)
    if levels.size < 2:
        raise MeasurementError(
            "Fuer die Auswertung werden mindestens 2 Amplitudenstufen gebraucht."
        )
    if linear_range is None:
        low, high = linear_region(levels, gain.mean(axis=1), tolerance_db)
    else:
        low, high = float(linear_range[0]), float(linear_range[1])
        if high <= low:
            raise MeasurementError(
                f"Ungueltiger Fitbereich: {low} bis {high} dBm."
            )
    mask = (levels >= low - 1e-9) & (levels <= high + 1e-9)
    if int(mask.sum()) < 2:
        raise MeasurementError(
            f"Im Fitbereich {low:+.1f} ... {high:+.1f} dBm liegen weniger als "
            "2 Amplitudenstufen."
        )

    results: List[FrequencyResult] = []
    for column, frequency in enumerate(frequencies):
        slope, intercept = fit_line(levels[mask], p_out[mask, column])
        point = compression_point(
            levels, p_out[:, column], slope, intercept, compression_db
        )
        results.append(
            FrequencyResult(
                frequency_hz=float(frequency),
                gain_db=float(np.mean(gain[mask, column])),
                slope=slope,
                p1db_in_dbm=None if point is None else point[0],
                p1db_out_dbm=None if point is None else point[1],
                # Letzte Zeile = hoechster tatsaechlich gemessener Pegel.
                # to_matrix sortiert die Stufen aufsteigend, und verworfene
                # oder abgebrochene Stufen stehen gar nicht erst drin.
                p_out_max_dbm=float(p_out[-1, column]),
            )
        )
    return Analysis(
        frequencies_hz=frequencies,
        levels_dbm=levels,
        p_out_dbm=p_out,
        gain_db=gain,
        results=results,
        linear_low_dbm=low,
        linear_high_dbm=high,
        compression_db=compression_db,
        cable_corrected=has_reference(points),
    )


def summary_lines(analysis: Analysis) -> List[str]:
    """Kennwerte als lesbare Zeilen."""
    lines = [
        f"Frequenzbereich        : {analysis.frequencies_hz[0] / 1e6:.3f} - "
        f"{analysis.frequencies_hz[-1] / 1e6:.3f} MHz "
        f"({analysis.frequencies_hz.size} Punkte)",
        f"Eingangspegel          : {analysis.levels_dbm[0]:+.1f} ... "
        f"{analysis.levels_dbm[-1]:+.1f} dBm ({analysis.levels_dbm.size} Stufen)",
        "Kabelkorrektur         : "
        + (
            "ja (THRU-Referenz, Werte auf Verstaerkerebene)"
            if analysis.cable_corrected
            else "NEIN (Rohwerte am Analyzer-Eingang)"
        ),
        f"Fitbereich (linear)    : {analysis.linear_low_dbm:+.1f} ... "
        f"{analysis.linear_high_dbm:+.1f} dBm",
        f"Kleinsignalverstaerkung: {analysis.gain_mean:+.2f} dB",
        f"Welligkeit ueber f     : {analysis.gain_ripple:.2f} dB",
        f"Steigung des Fits      : {analysis.slope_mean:.4f} "
        "(1.0000 = ideal linear)",
    ]
    if analysis.p1db_in is None:
        lines.append(
            f"{analysis.compression_db:.0f}-dB-Kompression     : im gemessenen "
            "Bereich nicht erreicht"
        )
    else:
        spread = [
            r.p1db_in_dbm for r in analysis.results if r.p1db_in_dbm is not None
        ]
        lines.append(
            f"{analysis.compression_db:.0f}-dB-Kompression     : "
            f"P_in {analysis.p1db_in:+.2f} dBm / P_out {analysis.p1db_out:+.2f} dBm "
            f"(Streuung ueber f: {float(np.std(spread)):.2f} dB)"
        )
    if analysis.oip3_estimate is not None:
        lines.append(
            f"OIP3 (nur geschaetzt)  : {analysis.oip3_estimate:+.2f} dBm "
            f"= P1dB + {OIP3_OFFSET_DB:.1f} dB - Faustregel, keine Messung"
        )
    lines.append(
        f"P_out bei {analysis.levels_dbm[-1]:+.1f} dBm    : "
        f"{analysis.p_out_max:+.2f} dBm "
        f"({10 ** (analysis.p_out_max / 10):.1f} mW)"
    )
    return lines


def write_summary(analysis: Analysis, path: Path | str, title: str = "") -> Path:
    """Schreibt die Kennwerte als Markdown."""
    path = Path(path)
    body = ["# Auswertung S12-Messung", ""]
    if title:
        body += [f"Quelle: `{title}`", ""]
    body += ["```"] + summary_lines(analysis) + ["```", ""]
    body += [
        "> Der OIP3-Wert ist **geschaetzt**, nicht gemessen: OIP3 = P1dB + "
        f"{OIP3_OFFSET_DB:.1f} dB gilt fuer eine rein kubische Kennlinie.",
        "> Eine echte Messung braucht zwei Toene gleichzeitig - der FPC1500",
        "> hat nur eine Signalquelle.",
        "",
    ]
    if not analysis.cable_corrected:
        body += [
            "> **Achtung:** Diese Messung enthaelt keine THRU-Referenz. Die",
            "> angegebene Verstaerkung enthaelt noch die Kabeldaempfung.",
            "",
        ]
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def write_compression_csv(analysis: Analysis, path: Path | str) -> Path:
    """Schreibt die Kennwerte je Frequenz als CSV."""
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["frequency_hz", "gain_db", "slope", "p1db_in_dbm",
             "p1db_out_dbm", "oip3_estimate_dbm", "p_out_max_dbm"]
        )
        for result in analysis.results:
            writer.writerow(
                [
                    f"{result.frequency_hz:.1f}",
                    f"{result.gain_db:.4f}",
                    f"{result.slope:.6f}",
                    "" if result.p1db_in_dbm is None else f"{result.p1db_in_dbm:.4f}",
                    "" if result.p1db_out_dbm is None else f"{result.p1db_out_dbm:.4f}",
                    ""
                    if result.oip3_estimate_dbm is None
                    else f"{result.oip3_estimate_dbm:.4f}",
                    f"{result.p_out_max_dbm:.4f}",
                ]
            )
    return path
