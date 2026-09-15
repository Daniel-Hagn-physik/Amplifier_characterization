"""Kommandozeile der Auswertung: CSV auswaehlen, Kennwerte und Plots erzeugen."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable, Optional, Sequence

from .analysis import (
    DEFAULT_COMPRESSION_DB,
    DEFAULT_TOLERANCE_DB,
    analyze,
    summary_lines,
    write_compression_csv,
    write_summary,
)
from .config import ConfigError, load_config
from .dialog import ask_for_csv
from .measurement import MeasurementError, read_csv
from .plotting import plot_analysis

DEFAULT_CONFIG = "config/default.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="analyze",
        description=(
            "Wertet eine S12-Messung aus: Kleinsignalfit, Verstaerkung, "
            "Kompressionspunkt und Plots. Ohne Dateiangabe oeffnet sich ein "
            "Dateiauswahl-Fenster."
        ),
    )
    parser.add_argument(
        "csv",
        nargs="?",
        default=None,
        help="Messdatei (s12_measurement.csv). Fehlt sie, wird danach gefragt.",
    )
    parser.add_argument(
        "-c", "--config", default=DEFAULT_CONFIG,
        help="Config, aus der das Startverzeichnis der Auswahl kommt.",
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="Zielverzeichnis fuer Plots und Kennwerte (Standard: neben der CSV).",
    )
    parser.add_argument(
        "--compression-db", type=float, default=DEFAULT_COMPRESSION_DB,
        help="Kompressionsmass in dB (Standard: 1.0).",
    )
    parser.add_argument(
        "--tolerance-db", type=float, default=DEFAULT_TOLERANCE_DB,
        help=(
            "Wie weit die Verstaerkung im Fitbereich abfallen darf "
            f"(Standard: {DEFAULT_TOLERANCE_DB})."
        ),
    )
    parser.add_argument(
        "--linear-range", type=float, nargs=2, metavar=("LOW", "HIGH"), default=None,
        help="Fitbereich in dBm fest vorgeben statt automatisch zu bestimmen.",
    )
    parser.add_argument(
        "--no-plot", action="store_true", help="Nur Kennwerte, keine Plots."
    )
    return parser


def start_directory(config_path: str) -> str:
    """Verzeichnis, in dem die Dateiauswahl startet.

    Die Config wird hier nur nach dem Ablageort gefragt. Ist sie fehlerhaft
    oder gar nicht da, faellt die Auswahl auf das aktuelle Verzeichnis zurueck -
    eine Auswertung schon vorhandener Messdaten soll nicht daran scheitern.
    """
    try:
        config = load_config(config_path)
    except ConfigError:
        return "."
    directory = Path(config.output.directory).expanduser()
    return str(directory) if directory.is_dir() else "."


def main(
    argv: Optional[Sequence[str]] = None,
    printer: Callable[[str], None] = print,
    chooser=None,
) -> int:
    """Einstiegspunkt der Auswertung."""
    args = build_parser().parse_args(argv)

    if args.csv:
        source = Path(args.csv)
    else:
        selected = ask_for_csv(
            start_directory(args.config),
            "S12-Messdatei auswaehlen",
            chooser=chooser,
            printer=printer,
        )
        if not selected:
            printer("Keine Datei ausgewaehlt - abgebrochen.")
            return 1
        source = Path(selected)

    if not source.is_file():
        printer(f"Datei nicht gefunden: {source}")
        return 1

    printer(f"Auswertung von {source}")
    try:
        points = read_csv(source)
    except (OSError, KeyError, ValueError) as exc:
        printer(f"Messdatei nicht lesbar: {exc}")
        return 1

    try:
        analysis = analyze(
            points,
            tolerance_db=args.tolerance_db,
            compression_db=args.compression_db,
            linear_range=tuple(args.linear_range) if args.linear_range else None,
        )
    except MeasurementError as exc:
        printer(f"Auswertung nicht moeglich: {exc}")
        return 1

    printer("")
    for line in summary_lines(analysis):
        printer("  " + line)
    printer("")
    if not analysis.cable_corrected:
        printer("  ACHTUNG: keine THRU-Referenz in dieser Messung -")
        printer("  die Verstaerkung enthaelt noch die Kabeldaempfung.")
        printer("")

    # Ohne -o landen Kennwerte und Plots neben der CSV. Damit bleibt alles zu
    # einer Messung in einem Ordner beisammen und die Auswertung laesst sich
    # mit anderen Parametern wiederholen, ohne die Zuordnung zu verlieren.
    directory = Path(args.output) if args.output else source.parent
    directory.mkdir(parents=True, exist_ok=True)
    written = [
        write_summary(analysis, directory / "analysis_summary.md", str(source)),
        write_compression_csv(analysis, directory / "analysis_per_frequency.csv"),
    ]
    if not args.no_plot:
        written += plot_analysis(analysis, points, directory)
    for path in written:
        printer(f"  geschrieben: {path}")
    return 0
