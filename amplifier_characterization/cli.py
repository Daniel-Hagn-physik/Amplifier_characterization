"""Kommandozeile: Config laden, THRU-Hinweis zeigen, messen, speichern, plotten."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from . import __version__
from .config import (
    Config,
    ConfigError,
    describe,
    load_config,
    make_output_directory,
)
from .dialog import prompt_thru_calibration
from .instrument import (
    FPC1500,
    InstrumentError,
    SimulatedFPC1500,
    list_resources,
    probe_tcp,
    strip_block_header,
)
from .measurement import MeasurementError, Point, run_measurement, write_csv
from .plotting import plot_all

DEFAULT_CONFIG = "config/default.json"

DIAGNOSTIC_PORTS: tuple = (
    (5025, "SCPI Raw Socket"),
    (111, "VXI-11 Portmapper"),
    (4880, "HiSLIP"),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amplifier-characterization",
        description=(
            "S12-Messung (Ausgangsleistung ueber Eingangsleistung und Frequenz) "
            "mit dem Rohde & Schwarz FPC1500."
        ),
    )
    parser.add_argument(
        "-c",
        "--config",
        default=DEFAULT_CONFIG,
        help=f"Pfad zur JSON-Konfiguration (Standard: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Ueberschreibt output.directory aus der Config (Wurzelverzeichnis).",
    )
    parser.add_argument(
        "--resource",
        default=None,
        help="Ueberschreibt die VISA-Adresse aus der Config.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simuliertes Geraet statt echter Hardware (zum Testen des Ablaufs).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Nur die Verbindung zum Geraet pruefen (*IDN?) und beenden.",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Geraete-Selbsttest: alle SCPI-Kommandos und einen Sweep pruefen.",
    )
    parser.add_argument(
        "--dump-commands",
        metavar="DATEI",
        nargs="?",
        const="scpi_commands.txt",
        default=None,
        help="Befehlsliste des Geraets (SYST:HELP:HEAD?) in eine Datei schreiben.",
    )
    parser.add_argument(
        "--list-resources",
        action="store_true",
        help="Alle sichtbaren VISA-Ressourcen auflisten und beenden.",
    )
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="Ueberspringt den THRU-Kalibrierungshinweis.",
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="THRU-Hinweis in der Konsole statt als Fenster.",
    )
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Nur CSV schreiben, keine Plots erzeugen.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def apply_overrides(config: Config, args: argparse.Namespace) -> Config:
    """Baut die Config mit den CLI-Overrides neu auf."""
    import dataclasses

    if args.output is not None:
        config = dataclasses.replace(
            config, output=dataclasses.replace(config.output, directory=args.output)
        )
    if args.resource is not None:
        config = dataclasses.replace(
            config,
            instrument=dataclasses.replace(config.instrument, resource=args.resource),
        )
    return config


def make_analyzer(config: Config, dry_run: bool):
    """Erzeugt echtes oder simuliertes Geraet."""
    if dry_run:
        return SimulatedFPC1500(
            gain_db=config.simulation.gain_db,
            p_sat_dbm=config.simulation.p_sat_dbm,
            rolloff_db_per_ghz=config.simulation.rolloff_db_per_ghz,
            points=config.simulation.points,
            noise_floor_dbm=config.simulation.noise_floor_dbm,
        )
    return FPC1500(config.instrument.resource, timeout_ms=config.instrument.timeout_ms)


def host_of(resource: str) -> str:
    """Zieht den Hostnamen bzw. die IP aus einer VISA-Adresse."""
    parts = [part for part in resource.split("::") if part]
    return parts[1] if len(parts) > 1 else resource


def diagnose_network(
    resource: str,
    printer: Callable[[str], None],
    probe: Optional[Callable[[str, int], bool]] = None,
) -> None:
    """Sagt, ob das Geraet ueberhaupt im Netz antwortet - und auf welchem Port."""
    probe = probe or probe_tcp
    host = host_of(resource)
    printer(f"  Netzwerktest zu {host}:")
    reachable = []
    for port, label in DIAGNOSTIC_PORTS:
        answered = probe(host, port)
        if answered:
            reachable.append(port)
        printer(
            f"    Port {port:>5} ({label}): "
            f"{'antwortet' if answered else 'keine Antwort'}"
        )
    if reachable:
        printer("  -> Das Geraet ist erreichbar, nur der Resource-String passt nicht.")
        if 5025 in reachable:
            printer(f"     Versuch: --resource TCPIP0::{host}::5025::SOCKET")
        if 4880 in reachable:
            printer(f"     Versuch: --resource TCPIP0::{host}::hislip0::INSTR")
    else:
        printer("  -> Keine Antwort von dieser Adresse.")
        printer("     Bei DHCP kann sich die IP geaendert haben: am FPC1500")
        printer("     SETUP > Instrument Setup > Network die aktuelle IP ablesen")
        printer("     und mit --resource oder in der Config eintragen.")


def show_resources(
    printer: Callable[[str], None],
    lister: Optional[Callable[[], List[str]]] = None,
) -> int:
    """Gibt aus, was das VISA-Backend an Geraeten sieht."""
    try:
        resources = (lister or list_resources)()
    except InstrumentError as exc:
        printer(f"VISA-Fehler: {exc}")
        return 1
    if not resources:
        printer("Keine VISA-Ressourcen gefunden.")
        return 1
    printer("Gefundene VISA-Ressourcen:")
    for resource in resources:
        printer(f"  {resource}")
    return 0


INTERESTING_PREFIXES = ("SOUR", "OUTP", "GEN", "POW", "TG")


def dump_commands(
    config: Config, path: str, printer: Callable[[str], None]
) -> int:
    """Fragt die Befehlsliste des Geraets ab und legt sie als Datei ab.

    Viele R&S-Geraete beantworten ``SYST:HELP:HEAD?`` mit allen implementierten
    Kommandos - damit muss man die richtige Schreibweise nicht raten.
    """
    device = FPC1500(
        config.instrument.resource,
        timeout_ms=max(config.instrument.timeout_ms, 60000),
    )
    try:
        device.open()
        printer(f"  Geraet: {device.identify()}")
        printer("  Frage SYST:HELP:HEAD? ab (kann eine Weile dauern) ...")
        answer = strip_block_header(device.query("SYST:HELP:HEAD?"))
    except InstrumentError as exc:
        printer(f"  FEHLER: {exc}")
        printer("  Das Geraet unterstuetzt SYST:HELP:HEAD? offenbar nicht.")
        return 1
    finally:
        device.close()

    commands = [line.strip() for line in answer.replace(",", "\n").splitlines()]
    commands = [line for line in commands if line]
    target = Path(path)
    target.write_text("\n".join(commands) + "\n", encoding="utf-8")
    printer(f"  {len(commands)} Kommandos geschrieben nach {target}")
    printer("  Treffer zur Signalquelle:")
    hits = [
        command
        for command in commands
        if any(prefix in command.upper() for prefix in INTERESTING_PREFIXES)
    ]
    for command in hits[:60]:
        printer(f"    {command}")
    if not hits:
        printer("    (keine)")
    return 0


def probe_instrument(
    config: Config, dry_run: bool, printer: Callable[[str], None]
) -> int:
    """Faehrt den kompletten Messaufbau einmal durch - ohne die eigentliche Messung.

    Prueft, ob das Geraet alle verwendeten SCPI-Kommandos akzeptiert, wie viele
    Sweep-Punkte es liefert und ob ein Trace lesbar ist.
    """
    frequencies = config.frequency_grid
    analyzer = make_analyzer(config, dry_run)
    try:
        analyzer.open()
        printer(f"  Geraet            : {analyzer.identify()}")
        printer("  SCPI-Kommandos    :")
        analyzer.configure(
            config.instrument,
            float(frequencies[0]),
            float(frequencies[-1]),
            progress=lambda command: printer(f"    -> {command}"),
        )
        for entry in getattr(analyzer, "rejected", []):
            printer(f"  Abgelehnt         : {entry}")
        axis = analyzer.frequency_axis()
        printer(
            f"  Sweep-Punkte      : {axis.size} "
            f"({axis[0] / 1e6:.3f} - {axis[-1] / 1e6:.3f} MHz, "
            f"{(axis[1] - axis[0]) / 1e3:.1f} kHz Raster)"
        )
        step = lambda command: printer(f"    -> {command}")
        printer("  Signalquelle      :")
        analyzer.generator(True, progress=step)
        limits = analyzer.generator_limits()
        level = float(config.amplitude_grid[0])
        if limits is None:
            printer("    Pegelbereich: vom Geraet nicht abfragbar")
        else:
            printer(f"    Pegelbereich: {limits[0]:+.2f} ... {limits[1]:+.2f} dBm")
            level = min(max(level, limits[0]), limits[1])
        analyzer.set_generator_level(level, progress=step)
        printer(f"    ein/aus  : {analyzer.generator_state_template}")
        printer(f"    Pegel    : {analyzer.generator_level_template}")
        if limits is not None:
            outside = [
                value
                for value in config.amplitude_grid
                if value < limits[0] - 1e-9 or value > limits[1] + 1e-9
            ]
            if outside:
                printer(
                    f"  WARNUNG           : {len(outside)} der {len(config.amplitude_grid)} "
                    f"Amplitudenstufen liegen ausserhalb "
                    f"({outside[0]:+.2f} ... {outside[-1]:+.2f} dBm)"
                )
        analyzer.sweep(config.instrument.settle_s)
        trace = analyzer.read_trace()
        printer(
            f"  Trace             : {trace.size} Werte, "
            f"{trace.min():+.2f} ... {trace.max():+.2f} dBm"
        )
        analyzer.generator(False)
        errors = analyzer.check_errors()
        if errors:
            printer("  Abgelehnte Kommandos:")
            for entry in errors:
                printer(f"    {entry}")
            return 1
        printer("  Alle SCPI-Kommandos akzeptiert - bereit fuer die Messung.")
        return 0
    except InstrumentError as exc:
        printer(f"  FEHLER: {exc}")
        return 1
    finally:
        analyzer.close()


def check_connection(
    config: Config, dry_run: bool, printer: Callable[[str], None]
) -> int:
    """Fragt nur *IDN? ab - schneller Test, ob das Geraet erreichbar ist."""
    analyzer = make_analyzer(config, dry_run)
    printer(f"Verbindungstest zu {config.instrument.resource} ...")
    try:
        analyzer.open()
        printer(f"  OK - Geraet meldet: {analyzer.identify()}")
        return 0
    except InstrumentError as exc:
        printer(f"  FEHLER: {exc}")
        diagnose_network(config.instrument.resource, printer)
        return 1
    finally:
        analyzer.close()


def save_run(
    config: Config,
    points: Sequence[Point],
    directory: Path,
    make_plots: bool,
    printer: Callable[[str], None],
) -> List[Path]:
    """Schreibt CSV, Config-Kopie und optional die Plots."""
    written: List[Path] = [write_csv(points, directory / "s12_measurement.csv")]
    config_copy = directory / "config_used.json"
    config_copy.write_text(
        json.dumps(
            {
                "instrument": vars(config.instrument),
                "frequency": vars(config.frequency),
                "amplitude": vars(config.amplitude),
                "output": vars(config.output),
                "measurement": vars(config.measurement),
                "simulation": vars(config.simulation),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    written.append(config_copy)
    if make_plots:
        written.extend(plot_all(points, directory))
    for path in written:
        printer(f"  geschrieben: {path}")
    return written


def main(
    argv: Optional[Sequence[str]] = None,
    printer: Callable[[str], None] = print,
    today: Optional[date] = None,
) -> int:
    """Einstiegspunkt. Gibt 0 bei Erfolg, 1 bei erwarteten Fehlern zurueck."""
    args = build_parser().parse_args(argv)
    try:
        config = apply_overrides(load_config(args.config), args)
    except ConfigError as exc:
        printer(f"Konfigurationsfehler: {exc}")
        return 1

    printer("S12-Messung FPC1500")
    for line in describe(config):
        printer("  " + line)

    if args.list_resources:
        return show_resources(printer)

    if args.dump_commands:
        return dump_commands(config, args.dump_commands, printer)

    if args.check:
        return check_connection(config, args.dry_run, printer)

    if args.probe:
        return probe_instrument(config, args.dry_run, printer)

    if not (args.no_prompt or config.measurement.skip_thru_prompt):
        prompt_thru_calibration(
            config.measurement.thru_message,
            use_gui=not args.no_gui,
            printer=printer,
        )

    analyzer = make_analyzer(config, args.dry_run)
    try:
        analyzer.open()
        printer(f"  Geraet: {analyzer.identify()}")
        points = run_measurement(analyzer, config, progress=printer)
    except (InstrumentError, MeasurementError) as exc:
        printer(f"Messfehler: {exc}")
        return 1
    finally:
        analyzer.close()

    directory = make_output_directory(config, today)
    save_run(config, points, directory, not args.no_plot, printer)
    printer(f"Fertig: {len(points)} Messpunkte in {directory}")
    return 0
