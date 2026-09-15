#!/usr/bin/env python3
"""S12-Messung des Verstaerkers.

Vorher einmalig die Kabelreferenz aufnehmen (siehe run_thru.py) - sie wird
hier automatisch gefunden und abgezogen, sodass die Kabeldaempfung nicht im
Ergebnis landet.

    python run_s12.py
    python run_s12.py --reference results/2026-09-15_amp1_thru/s12_measurement.csv
    python run_s12.py --dry-run --no-prompt
"""

import sys

from amplifier_characterization.cli import main

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
