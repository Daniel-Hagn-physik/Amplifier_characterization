#!/usr/bin/env python3
"""THRU-Referenzmessung: die beiden Messkabel OHNE Verstaerker vermessen.

Einmal vor der ersten Verstaerkermessung ausfuehren - und immer dann wieder,
wenn Kabel gewechselt oder die Messparameter geaendert wurden. Das Ergebnis
landet in <datum>_<name>_thru und wird von run_s12.py automatisch gefunden.

    python run_thru.py
    python run_thru.py -c config/default.json
"""

import sys

from amplifier_characterization.cli import main_thru

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main_thru(sys.argv[1:]))
