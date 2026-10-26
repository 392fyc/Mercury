"""Put the installed dot-link pack's script directories on sys.path so the tests import the shipped code.

The pack is installed at .mercury/packs/dot-link by `python tools/mercury.py apply --target .`.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / ".mercury" / "packs" / "dot-link"
SCRIPTS = PACK / "scripts"
GATEWAY = SCRIPTS / "agents_gateway"
for _p in (str(GATEWAY), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
