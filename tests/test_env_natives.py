# -*- coding: utf-8 -*-
"""HMCL「高级设置」同款疑难杂症区：环境变量 / 系统 GLFW-OpenAL / 自定义 natives。"""
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import launcher, utils
from mclauncher import launch_flow as lf
from mclauncher import version_settings as vs
from mclauncher.installer import extract_natives
from mclauncher.version_ops import export_launch_bat


class ParseEnvVarsTests(unittest.TestCase):
    def test_basic_pairs(self):
        self.assertEqual(lf.parse_env_vars("A=1 B=two"), {"A": "1", "B": "two"})

    def test_quoted_value_with_spaces(self):
        out = lf.parse_env_vars('A=1 "B=hello world"')
        self.assertEqual(out, {"A": "1", "B": "hello world"})

    def test_token_without_equals_is_empty_value(self):
        # HMCL 同款：没有 = 的词视为空值变量
        self.assertEqual(lf.parse_env_vars("MANGOHUD X=1"), {"MANGOHUD": "", "X": "1"})

    def test_empty_and_none(self):
        self.assertEqual(lf.parse_env_vars(""), {})
        self.assertEqual(lf.parse_env_vars(None), {})
        self.assertEqual(lf.parse_env_vars("   "), {})

    def test_later_duplicate_wins(self):
        self.assertEqual(lf.parse_env_vars("A=1 A=2"), {"A": "2"})

    def test_value_keeps_extra_equals(self):
        self.assertEqual(lf.parse_env_vars("OPTS=a=b,c=d"), {"OPTS": "a=b,c=d"})


class LoaderEnvFlagsTests(unittest.TestCase):
    def _flags(self, *names):
        return lf.loader_env_flags({"libraries": [{"name": n} for n in names]})

    def test_fabric(self):
        self.assertEqual(self._flags("net.fabricmc:fabric-loader:0.16.9"), ["INST_FABRIC"])

    def test_forge_and_optifine(self):
        flags = self._flags("net.minecraftforge:forge:1.20.1-47.3.0",
                            "optifine:OptiFine:1.20.1_HD_U_I6")
        self.assertIn("INST_FORGE", flags)
        self.assertIn("INST_OPTIFINE", flags)

    def test_cleanroom_not_marked_as_plain_forge(self):
        flags = self._flags("com.cleanroommc:cleanroom:0.3.0")
        self.assertIn("INST_CLEANROOM", flags)
        self.assertNotIn("INST_FORGE", flags)

    def test_vanilla_has_none(self):
        self.assertEqual(self._flags("org.lwjgl:lwjgl:3.3.3"), [])

    def test_none_resolved(self):
        self.assertEqual(lf.loader_env_flags(None), [])


class _EnvFakeInstance:
    def __init__(self, root: Path):
        self.path = root
        self.name = "t"

    def versions_dir(self):
        return self.path / "versions"


class GameEnvTests(unittest.TestCase):
    def test_nothing_to_add_returns_none(self):
        self.assertIsNone(lf.game_env({}, None))
        self.assertIsNone(lf.game_env({"env_vars": {}}, {}))

    def test_user_vars_merged_over_environ(self):
        env = lf.game_env({"env_vars": {"PYMCL_TEST_VAR": "42"}})
        self.assertIsNotNone(env)
        self.assertEqual(env["PYMCL_TEST_VAR"], "42")
        # 继承启动器已有环境
        for key in ("PATH", "Path"):
            if key in os.environ:
                self.assertEqual(env[key], os.environ[key])

    def test_user_vars_override_gpu_env(self):
        # HMCL putAll 在最后：用户显式写的变量优先级最高
        env = lf.game_env({"env_vars": {"LIBGL_ALWAYS_SOFTWARE": "0"}},
                          {"LIBGL_ALWAYS_SOFTWARE": "1", "OTHER": "x"})
        self.assertEqual(env["LIBGL_ALWAYS_SOFTWARE"], "0")
        self.assertEqual(env["OTHER"], "x")

    def test_inst_vars_present(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inst = _EnvFakeInstance(root)
            resolved = {"libraries": [{"name": "net.fabricmc:fabric-loader:0.16.9"}]}
            env = lf.game_env({}, None, instance=inst, version_id="1.20.1",
                              java_exe="/usr/bin/java", game_dir=root / "gd",
                              resolved=resolved)
            self.assertEqual(env["INST_NAME"], "1.20.1")
            self.assertEqual(env["INST_ID"], "1.20.1")
            self.assertEqual(env["INST_DIR"], str(root / "versions" / "1.20.1"))
            self.assertEqual(env["INST_MC_DIR"], str(root / "gd"))
            self.assertEqual(env["INST_JAVA"], "/usr/bin/java")
            self.assertEqual(env["INST_FABRIC"], "1")

    def test_user_vars_override_inst_vars(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _EnvFakeInstance(Path(td))
            env = lf.game_env({"env_vars": {"INST_NAME": "custom"}}, None,
                              instance=inst, version_id="1.20.1")
            self.assertEqual(env["INST_NAME"], "custom")


class _VsFakeInstance:
    def __init__(self, root: Path):
        self._root = root
        self.path = root

    def versions_dir(self) -> Path:
        return self._root / "versions"


class VersionSettingsRoundtripTests(unittest.TestCase):
    def test_defaults_present(self):
        for key, default in (("env_vars", ""), ("use_system_glfw", False),
                             ("use_system_openal", False), ("natives_dir", "")):
            self.assertEqual(vs.DEFAULTS.get(key), default)

    def test_roundtrip_and_prepare(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "versions" / "1.20.1").mkdir(parents=True)
            inst = _VsFakeInstance(root)
            vs.save(inst, "1.20.1", {
                "env_vars": "MANGOHUD=1 \"MSG=hello world\"",
                "use_system_glfw": True,
                "use_system_openal": True,
                "natives_dir": "  /opt/natives  ",
            })
            data = vs.load(inst, "1.20.1")
            self.assertEqual(data["env_vars"], "MANGOHUD=1 \"MSG=hello world\"")
            self.assertTrue(data["use_system_glfw"])
            self.assertTrue(data["use_system_openal"])
            prep = lf.prepare(inst, "1.20.1")
            self.assertEqual(prep["env_vars"], {"MANGOHUD": "1", "MSG": "hello world"})
            self.assertTrue(prep["use_system_glfw"])
            self.assertTrue(prep["use_system_openal"])
            self.assertEqual(prep["natives_dir"], "/opt/natives")

    def test_prepare_defaults_empty(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "versions" / "1.20.1").mkdir(parents=True)
            prep = lf.prepare(_VsFakeInstance(root), "1.20.1")
            self.assertEqual(prep["env_vars"], {})
            self.assertFalse(prep["use_system_glfw"])
            self.assertFalse(prep["use_system_openal"])
            self.assertEqual(prep["natives_dir"], "")


class _NativesFakeInstance:
    def __init__(self, root: Path):
        self.path = root
        self.name = "t"

    def versions_dir(self):
        return self.path / "versions"

    def libraries_dir(self):
        return self.path / "libraries"

    def natives_dir(self, version_id, _resolved=None):
        return self.path / "versions" / version_id / "natives"

    def version_json(self, vid):
        return None


def _make_natives_fixture(root: Path):
    """一个带 lwjgl/glfw/openal 三个库文件的 natives jar + resolved json。"""
    jar_rel = "org/lwjgl/lwjgl/3.3.3/lwjgl-3.3.3-natives-test.jar"
    jar_path = root / "libraries" / jar_rel
    jar_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(jar_path, "w") as zf:
        zf.writestr("liblwjgl.so", b"lwjgl")
        zf.writestr("libglfw.so", b"glfw")
        zf.writestr("libopenal.so", b"openal")
    resolved = {
        "id": "1.20.1",
        "libraries": [{
            "name": "org.lwjgl:lwjgl:3.3.3",
            "natives": {utils.OS_NAME: "natives-test"},
            "downloads": {"classifiers": {"natives-test": {"path": jar_rel}}},
        }],
    }
    (root / "versions" / "1.20.1").mkdir(parents=True, exist_ok=True)
    return resolved


class ExtractNativesSkipTests(unittest.TestCase):
    def test_default_extracts_all(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            resolved = _make_natives_fixture(root)
            inst = _NativesFakeInstance(root)
            ndir = extract_natives(inst, resolved, "1.20.1")
            names = sorted(p.name for p in ndir.iterdir())
            self.assertEqual(names, ["libglfw.so", "liblwjgl.so", "libopenal.so"])

    def test_skip_glfw_and_openal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            resolved = _make_natives_fixture(root)
            inst = _NativesFakeInstance(root)
            ndir = extract_natives(inst, resolved, "1.20.1",
                                   skip_glfw=True, skip_openal=True)
            names = sorted(p.name for p in ndir.iterdir())
            self.assertEqual(names, ["liblwjgl.so"])

    def test_toggle_purges_previously_extracted(self):
        # 上次启动解压出的捆绑库必须清掉，否则 java.library.path 仍会优先加载
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            resolved = _make_natives_fixture(root)
            inst = _NativesFakeInstance(root)
            extract_natives(inst, resolved, "1.20.1")
            ndir = extract_natives(inst, resolved, "1.20.1", skip_glfw=True)
            names = sorted(p.name for p in ndir.iterdir())
            self.assertEqual(names, ["liblwjgl.so", "libopenal.so"])

    def test_toggle_back_restores_bundled(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            resolved = _make_natives_fixture(root)
            inst = _NativesFakeInstance(root)
            extract_natives(inst, resolved, "1.20.1", skip_glfw=True, skip_openal=True)
            ndir = extract_natives(inst, resolved, "1.20.1")
            names = sorted(p.name for p in ndir.iterdir())
            self.assertEqual(names, ["libglfw.so", "liblwjgl.so", "libopenal.so"])


MODERN_JSON = {
    "id": "1.20.1",
    "mainClass": "net.minecraft.client.main.Main",
    "assetIndex": {"id": "5"},
    "arguments": {"jvm": [], "game": ["--username", "${auth_player_name}"]},
}


class _CmdFakeInstance:
    def __init__(self, root: Path):
        self.path = root
        self.name = "t"

    def version_json(self, vid):
        p = self.path / "versions" / vid / f"{vid}.json"
        if not p.is_file():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def versions_dir(self):
        return self.path / "versions"

    def assets_dir(self):
        return self.path / "assets"

    def libraries_dir(self):
        return self.path / "libraries"

    def natives_dir(self, version_id, _resolved=None):
        return self.path / "versions" / version_id / "natives"


class BuildCommandNativesTests(unittest.TestCase):
    def _build(self, root: Path, **kwargs):
        vid = MODERN_JSON["id"]
        vdir = root / "versions" / vid
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / f"{vid}.json").write_text(json.dumps(MODERN_JSON), encoding="utf-8")
        (vdir / f"{vid}.jar").write_bytes(b"PK\x03\x04fakejar")
        inst = _CmdFakeInstance(root)
        props = {"name": "Steve", "uuid": "a" * 32, "token": "0",
                 "user_type": "legacy", "xuid": ""}
        with patch.object(launcher.java_mod, "java_usable_for", return_value=True), \
             patch.object(launcher.java_mod, "get_java_major", return_value=17):
            return launcher.build_launch_command(
                inst, vid, props, sys.executable, memory_mb=1024, **kwargs)

    def test_custom_natives_dir_used_in_library_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            custom = root / "my-natives"
            custom.mkdir()
            cmd, ndir, _v, _g = self._build(root, natives_dir_override=str(custom))
            self.assertEqual(Path(ndir), custom)
            self.assertIn(f"-Djava.library.path={custom}", cmd)

    def test_missing_custom_natives_dir_raises(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaises(launcher.LaunchError):
                self._build(root, natives_dir_override=str(root / "nope"))

    def test_skip_flags_forwarded_to_extract(self):
        seen = {}

        def fake_extract(instance, resolved, version_id, skip_glfw=False, skip_openal=False):
            seen["glfw"] = skip_glfw
            seen["openal"] = skip_openal
            d = instance.natives_dir(version_id)
            d.mkdir(parents=True, exist_ok=True)
            return d

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(launcher, "extract_natives", side_effect=fake_extract):
                self._build(root, use_system_glfw=True, use_system_openal=True)
        self.assertTrue(seen["glfw"])
        self.assertTrue(seen["openal"])


class ExportBatEnvTests(unittest.TestCase):
    def test_env_lines_written(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "launch.bat"
            path = export_launch_bat(dest, ["java", "-jar", "x.jar"], td,
                                     env={"MANGOHUD": "1", "MSG": "hello world"})
            text = Path(path).read_text(encoding="utf-8")
            self.assertIn('set "MANGOHUD=1"', text)
            self.assertIn('set "MSG=hello world"', text)
            # set 行必须在启动命令之前
            self.assertLess(text.index('set "MANGOHUD=1"'), text.index("java -jar"))

    def test_no_env_no_set_lines(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "launch.bat"
            path = export_launch_bat(dest, ["java"], td)
            self.assertNotIn('set "', Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
