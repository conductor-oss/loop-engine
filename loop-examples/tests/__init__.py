"""Make the example modules and the loop SDK importable when running
`python3 -m unittest discover` from loop-examples/ (no install required)."""
import os
import sys

_EXAMPLES_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (_EXAMPLES_DIR, os.path.join(os.path.dirname(_EXAMPLES_DIR), "sdk")):
    if path not in sys.path:
        sys.path.insert(0, path)
