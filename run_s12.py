#!/usr/bin/env python3
"""Startskript fuer die S12-Messung.

Beispiele:
    python run_s12.py -c config/default.json
    python run_s12.py -c config/default.json --dry-run --no-prompt
"""

import sys

from amplifier_characterization.cli import main

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
