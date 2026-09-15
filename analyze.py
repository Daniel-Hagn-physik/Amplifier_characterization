#!/usr/bin/env python3
"""Auswertung einer S12-Messung - getrennt von der Messung.

Ohne Argument oeffnet sich ein Dateiauswahl-Fenster, in dem die gewuenschte
s12_measurement.csv ausgewaehlt wird. Ergebnis: Kennwerte (Kleinsignal-
verstaerkung, linearer Fit, 1-dB-Kompressionspunkt) und vier Plots, die neben
der CSV abgelegt werden.

    python analyze.py
    python analyze.py results/2026-09-15_amp1/s12_measurement.csv
    python analyze.py --linear-range -30 -12 --compression-db 3
"""

import sys

from amplifier_characterization.analysis_cli import main

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
