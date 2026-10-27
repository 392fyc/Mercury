"""Pack generators (#668, #680): each packs/<pack>/build/generate.py --check must pass when the submodules are checked out."""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# pack -> pinned submodule under sources/
GENERATORS = {
    "plan": "claude-plugins-community",
    "subagent-dev": "superpowers",
}


class PackGeneratorsTest(unittest.TestCase):
    def test_generate_check(self):
        for pack, source in GENERATORS.items():
            with self.subTest(pack=pack):
                mod = ROOT / "sources" / source
                if not (mod.is_dir() and any(mod.iterdir())):
                    self.skipTest(f"submodule sources/{source} not initialised (git submodule update --init)")
                script = ROOT / "packs" / pack / "build" / "generate.py"
                run = subprocess.run([sys.executable, "-B", str(script), "--check"],
                                     capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_generator_code_size(self):
        for pack in GENERATORS:
            with self.subTest(pack=pack):
                lines = sum(len(p.read_text(encoding="utf-8").splitlines())
                            for p in (ROOT / "packs" / pack / "build").glob("*.py"))
                self.assertLessEqual(lines, 200)


if __name__ == "__main__":
    unittest.main()
