"""Upstream adapters (#668): each generate.py --check must pass when the submodules are checked out."""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADAPTERS = {
    "html-plan": "claude-plugins-community",
    "superpowers": "superpowers",
}


class UpstreamAdaptersTest(unittest.TestCase):
    def test_generate_check(self):
        for adapter, module in ADAPTERS.items():
            with self.subTest(adapter=adapter):
                mod = ROOT / "modules" / module
                if not (mod.is_dir() and any(mod.iterdir())):
                    self.skipTest(f"submodule modules/{module} not initialised (git submodule update --init)")
                script = ROOT / "adapters" / adapter / "generate.py"
                run = subprocess.run([sys.executable, "-B", str(script), "--check"],
                                     capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_adapter_code_size(self):
        for adapter in ADAPTERS:
            with self.subTest(adapter=adapter):
                lines = sum(len(p.read_text(encoding="utf-8").splitlines())
                            for p in (ROOT / "adapters" / adapter).glob("*.py"))
                self.assertLessEqual(lines, 200)


if __name__ == "__main__":
    unittest.main()
