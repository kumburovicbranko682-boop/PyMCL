from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mclauncher.launch_flow import apply_wrapper


class ApplyWrapperTests(unittest.TestCase):
    CMD = ["/usr/bin/java", "-Xmx4G", "net.minecraft.client.main.Main"]

    def test_empty_wrapper_is_noop(self):
        self.assertEqual(apply_wrapper(self.CMD, ""), self.CMD)
        self.assertEqual(apply_wrapper(self.CMD, "   "), self.CMD)
        self.assertEqual(apply_wrapper(self.CMD, None), self.CMD)

    def test_single_word_wrapper(self):
        out = apply_wrapper(self.CMD, "gamemoderun")
        self.assertEqual(out, ["gamemoderun"] + self.CMD)

    def test_wrapper_with_args(self):
        out = apply_wrapper(self.CMD, "nice -n 10")
        self.assertEqual(out, ["nice", "-n", "10"] + self.CMD)

    def test_quoted_wrapper_path(self):
        out = apply_wrapper(self.CMD, '"C:\\Program Files\\wrap.exe" --fast')
        self.assertEqual(out[0], "C:\\Program Files\\wrap.exe")
        self.assertEqual(out[1], "--fast")
        self.assertEqual(out[2:], self.CMD)

    def test_does_not_mutate_input(self):
        cmd = list(self.CMD)
        apply_wrapper(cmd, "wrap")
        self.assertEqual(cmd, self.CMD)


class VersionSettingsWrapperTests(unittest.TestCase):
    def test_wrapper_roundtrip(self):
        from mclauncher import version_settings as vs

        class FakeInstance:
            def __init__(self, root: Path):
                self._root = root

            def versions_dir(self) -> Path:
                return self._root

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "1.20.1").mkdir()
            inst = FakeInstance(root)
            self.assertEqual(vs.load(inst, "1.20.1").get("wrapper"), "")
            vs.save(inst, "1.20.1", {"wrapper": "prime-run"})
            self.assertEqual(vs.load(inst, "1.20.1").get("wrapper"), "prime-run")

    def test_prepare_exposes_wrapper(self):
        import mclauncher.launch_flow as lf
        from mclauncher import version_settings as vs

        class FakeInstance:
            def __init__(self, root: Path):
                self._root = root
                self.path = root

            def versions_dir(self) -> Path:
                return self._root / "versions"

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "versions" / "1.20.1").mkdir(parents=True)
            inst = FakeInstance(root)
            vs.save(inst, "1.20.1", {"wrapper": "  gamemoderun  "})
            prep = lf.prepare(inst, "1.20.1")
            self.assertEqual(prep.get("wrapper"), "gamemoderun")


if __name__ == "__main__":
    unittest.main()
