# -*- coding: utf-8 -*-
"""独显/核显启动：厂商分类、offload 环境变量、Windows 注册表、模式解析。"""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import gpu


class ClassifyTests(unittest.TestCase):
    def test_nvidia(self):
        for name in ("NVIDIA GeForce RTX 4060 Laptop GPU", "GeForce GTX 1650",
                     "Quadro P2000", "nvidia corporation ad107m"):
            self.assertEqual(gpu.classify(name), "nvidia", name)

    def test_amd(self):
        for name in ("AMD Radeon RX 7600M XT", "Radeon 780M Graphics",
                     "ATI Mobility Radeon HD 5470"):
            self.assertEqual(gpu.classify(name), "amd", name)

    def test_intel(self):
        for name in ("Intel(R) Iris(R) Xe Graphics", "Intel UHD Graphics 630",
                     "Intel(R) HD Graphics 4600"):
            self.assertEqual(gpu.classify(name), "intel", name)

    def test_other_and_empty(self):
        self.assertEqual(gpu.classify("Matrox G200"), "other")
        self.assertEqual(gpu.classify(""), "other")
        self.assertEqual(gpu.classify(None), "other")


class NormalizeTests(unittest.TestCase):
    def test_valid_modes(self):
        self.assertEqual(gpu.normalize_mode("discrete"), "discrete")
        self.assertEqual(gpu.normalize_mode(" INTEGRATED "), "integrated")
        self.assertEqual(gpu.normalize_mode("auto"), "auto")

    def test_invalid_falls_back_to_auto(self):
        for bad in ("", None, "dgpu", 42):
            self.assertEqual(gpu.normalize_mode(bad), "auto")


class ResolveModeTests(unittest.TestCase):
    def test_version_setting_wins(self):
        with patch.object(gpu, "normalize_mode", wraps=gpu.normalize_mode):
            from mclauncher.config import CONFIG
            with patch.object(CONFIG, "get", return_value="integrated"):
                self.assertEqual(gpu.resolve_mode({"gpu": "discrete"}), "discrete")

    def test_global_fallback(self):
        from mclauncher.config import CONFIG
        with patch.object(CONFIG, "get", return_value="integrated"):
            self.assertEqual(gpu.resolve_mode({"gpu": ""}), "integrated")
            self.assertEqual(gpu.resolve_mode({}), "integrated")
            self.assertEqual(gpu.resolve_mode(None), "integrated")

    def test_default_auto(self):
        from mclauncher.config import CONFIG
        with patch.object(CONFIG, "get", return_value=""):
            self.assertEqual(gpu.resolve_mode({}), "auto")


class OffloadEnvTests(unittest.TestCase):
    NV = [{"name": "Intel Iris Xe", "vendor": "intel"},
          {"name": "NVIDIA GeForce RTX 4060", "vendor": "nvidia"}]
    AMD = [{"name": "AMD Radeon 780M", "vendor": "amd"},
           {"name": "AMD Radeon RX 7600M", "vendor": "amd"}]

    def test_discrete_nvidia_uses_prime_offload(self):
        env = gpu.offload_env("discrete", self.NV)
        self.assertEqual(env["__NV_PRIME_RENDER_OFFLOAD"], "1")
        self.assertEqual(env["__GLX_VENDOR_LIBRARY_NAME"], "nvidia")
        self.assertEqual(env["__VK_LAYER_NV_optimus"], "NVIDIA_only")

    def test_discrete_mesa_uses_dri_prime(self):
        self.assertEqual(gpu.offload_env("discrete", self.AMD), {"DRI_PRIME": "1"})
        self.assertEqual(gpu.offload_env("discrete", []), {"DRI_PRIME": "1"})

    def test_integrated_disables_offload(self):
        env = gpu.offload_env("integrated", self.NV)
        self.assertEqual(env, {"DRI_PRIME": "0", "__NV_PRIME_RENDER_OFFLOAD": "0"})

    def test_auto_is_empty(self):
        self.assertEqual(gpu.offload_env("auto", self.NV), {})
        self.assertEqual(gpu.offload_env("", self.NV), {})


class LaunchEnvTests(unittest.TestCase):
    def test_auto_returns_nothing(self):
        self.assertEqual(gpu.launch_env("auto"), ({}, ""))
        self.assertEqual(gpu.launch_env(None), ({}, ""))

    def test_linux_discrete(self):
        with patch.object(gpu.utils, "IS_WINDOWS", False), \
                patch.object(gpu.utils, "IS_MAC", False), \
                patch.object(gpu, "list_gpus", return_value=OffloadEnvTests.NV):
            env, note = gpu.launch_env("discrete", "/usr/bin/java")
        self.assertEqual(env["__NV_PRIME_RENDER_OFFLOAD"], "1")
        self.assertIn("独显", note)

    def test_linux_integrated(self):
        with patch.object(gpu.utils, "IS_WINDOWS", False), \
                patch.object(gpu.utils, "IS_MAC", False):
            env, note = gpu.launch_env("integrated", "/usr/bin/java")
        self.assertEqual(env["DRI_PRIME"], "0")
        self.assertIn("核显", note)

    def test_windows_writes_registry_not_env(self):
        with patch.object(gpu.utils, "IS_WINDOWS", True), \
                patch.object(gpu, "apply_windows_preference",
                             return_value="written") as apply:
            env, note = gpu.launch_env("discrete", r"C:\java\bin\javaw.exe")
        self.assertEqual(env, {})
        self.assertEqual(note, "written")
        apply.assert_called_once_with(r"C:\java\bin\javaw.exe", "discrete")

    def test_macos_noop_with_note(self):
        with patch.object(gpu.utils, "IS_WINDOWS", False), \
                patch.object(gpu.utils, "IS_MAC", True):
            env, note = gpu.launch_env("discrete", "/usr/bin/java")
        self.assertEqual(env, {})
        self.assertIn("macOS", note)


class _FakeWinreg(types.ModuleType):
    HKEY_CURRENT_USER = "HKCU"
    REG_SZ = 1

    def __init__(self):
        super().__init__("winreg")
        self.set_calls = []
        self.deleted = []
        self.missing = set()

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def CreateKey(self, hive, path):
        self.path = (hive, path)
        return self._Key()

    def SetValueEx(self, key, name, reserved, kind, value):
        self.set_calls.append((name, kind, value))

    def DeleteValue(self, key, name):
        if name in self.missing:
            raise FileNotFoundError(name)
        self.deleted.append(name)


class WindowsRegistryTests(unittest.TestCase):
    def _with_fake_winreg(self, mode, exe="/opt/java/bin/java", missing=False):
        fake = _FakeWinreg()
        if missing:
            fake.missing.add(str(Path(exe).resolve()))
        with patch.dict(sys.modules, {"winreg": fake}):
            note = gpu.apply_windows_preference(exe, mode)
        return fake, note

    def test_discrete_sets_high_performance(self):
        fake, note = self._with_fake_winreg("discrete")
        self.assertEqual(len(fake.set_calls), 1)
        name, _kind, value = fake.set_calls[0]
        self.assertEqual(value, "GpuPreference=2;")
        self.assertTrue(name.endswith("java"))
        self.assertIn("高性能", note)
        self.assertEqual(fake.path[1], r"Software\Microsoft\DirectX\UserGpuPreferences")

    def test_integrated_sets_power_saving(self):
        fake, _ = self._with_fake_winreg("integrated")
        self.assertEqual(fake.set_calls[0][2], "GpuPreference=1;")

    def test_auto_deletes_value(self):
        fake, note = self._with_fake_winreg("auto")
        self.assertEqual(fake.set_calls, [])
        self.assertEqual(len(fake.deleted), 1)
        self.assertEqual(note, "")

    def test_auto_ignores_missing_value(self):
        fake, note = self._with_fake_winreg("auto", missing=True)
        self.assertEqual(fake.deleted, [])
        self.assertEqual(note, "")

    def test_registry_failure_does_not_raise(self):
        fake = _FakeWinreg()

        def boom(*a, **kw):
            raise OSError("access denied")

        fake.CreateKey = boom
        with patch.dict(sys.modules, {"winreg": fake}):
            note = gpu.apply_windows_preference("/opt/java/bin/java", "discrete")
        self.assertIn("失败", note)

    def test_empty_exe_is_noop(self):
        self.assertEqual(gpu.apply_windows_preference("", "discrete"), "")


class ListGpusTests(unittest.TestCase):
    def test_reuses_sysinfo_probe(self):
        rows = [{"name": "NVIDIA GeForce RTX 4060"}, {"name": ""}, None,
                {"name": "Intel UHD Graphics"}]
        with patch("mclauncher.sysinfo._gpu_info", return_value=rows):
            gpus = gpu.list_gpus()
        self.assertEqual(gpus, [
            {"name": "NVIDIA GeForce RTX 4060", "vendor": "nvidia"},
            {"name": "Intel UHD Graphics", "vendor": "intel"},
        ])
        self.assertTrue(gpu.has_dual_gpu(gpus))
        self.assertFalse(gpu.has_dual_gpu(gpus[:1]))

    def test_probe_failure_returns_empty(self):
        with patch("mclauncher.sysinfo._gpu_info", side_effect=RuntimeError):
            self.assertEqual(gpu.list_gpus(), [])


class WiringTests(unittest.TestCase):
    def test_version_settings_default(self):
        from mclauncher import version_settings
        self.assertIn("gpu", version_settings.DEFAULTS)
        self.assertEqual(version_settings.DEFAULTS["gpu"], "")

    def test_config_default(self):
        from mclauncher.config import DEFAULT_CONFIG
        self.assertEqual(DEFAULT_CONFIG.get("gpu_mode"), "auto")

    def test_launch_flow_carries_gpu_mode(self):
        import inspect
        from mclauncher import launch_flow
        src = inspect.getsource(launch_flow.prepare)
        self.assertIn("gpu_mode", src)
        self.assertIn("resolve_mode", src)

    def test_facades_inject_launch_env(self):
        root = Path(__file__).resolve().parents[1]
        for rel in ("app/backend.py", "bridge/api.py"):
            src = (root / rel).read_text(encoding="utf-8")
            self.assertIn("gpu_mod.launch_env", src, rel)
            self.assertIn('"gpu_mode"', src, rel)


if __name__ == "__main__":
    unittest.main()
