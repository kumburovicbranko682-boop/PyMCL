# -*- coding: utf-8 -*-
"""原理图管理（HMCL 原理图管理界面同款）：列表 / 元数据 / 导入 / 删除。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import nbt, schematics  # noqa: E402
from mclauncher.config import CONFIG  # noqa: E402


def _litematic_bytes(name="小别墅", author="Steve", x=5, y=4, z=6,
                     blocks=120, regions=1) -> bytes:
    root = (nbt.TAG_COMPOUND, {
        "MinecraftDataVersion": (nbt.TAG_INT, 3465),
        "Version": (nbt.TAG_INT, 6),
        "Metadata": (nbt.TAG_COMPOUND, {
            "Name": (nbt.TAG_STRING, name),
            "Author": (nbt.TAG_STRING, author),
            "EnclosingSize": (nbt.TAG_COMPOUND, {
                "x": (nbt.TAG_INT, x), "y": (nbt.TAG_INT, y), "z": (nbt.TAG_INT, z),
            }),
            "TotalBlocks": (nbt.TAG_INT, blocks),
            "RegionCount": (nbt.TAG_INT, regions),
        }),
    })
    return nbt.dumps_typed("", root, compress=True)


def _schem_bytes(w=3, h=2, ln=4) -> bytes:
    root = (nbt.TAG_COMPOUND, {
        "Width": (nbt.TAG_SHORT, w),
        "Height": (nbt.TAG_SHORT, h),
        "Length": (nbt.TAG_SHORT, ln),
    })
    return nbt.dumps_typed("Schematic", root, compress=True)


def _vanilla_nbt_bytes() -> bytes:
    root = (nbt.TAG_COMPOUND, {
        "size": (nbt.TAG_LIST, (nbt.TAG_INT, [7, 8, 9])),
        "author": (nbt.TAG_STRING, "Alex"),
        "DataVersion": (nbt.TAG_INT, 3465),
    })
    return nbt.dumps_typed("", root, compress=True)


class _Isolated(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        patcher = patch.object(type(CONFIG), "instances_dir",
                               new_callable=PropertyMock,
                               return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        from mclauncher.instances import Instance
        (self.root / "inst").mkdir(parents=True)
        self.inst = Instance("inst")
        self.fold = Path(self.inst.path) / "schematics"
        self.fold.mkdir()


class ListTests(_Isolated):
    def test_empty_when_no_folder(self):
        import shutil
        shutil.rmtree(self.fold)
        self.assertEqual(schematics.list_schematics(self.inst), [])

    def test_litematic_metadata(self):
        (self.fold / "house.litematic").write_bytes(_litematic_bytes())
        rows = schematics.list_schematics(self.inst)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["title"], "小别墅")
        self.assertEqual(r["author"], "Steve")
        self.assertEqual(r["size"], "5×4×6")
        self.assertEqual(r["blocks"], 120)
        self.assertEqual(r["format"], "Litematica")

    def test_worldedit_schem_dimensions(self):
        (self.fold / "wall.schem").write_bytes(_schem_bytes())
        r = schematics.list_schematics(self.inst)[0]
        self.assertEqual(r["size"], "3×2×4")
        self.assertEqual(r["format"], "WorldEdit (Sponge)")

    def test_vanilla_structure_nbt(self):
        (self.fold / "farm.nbt").write_bytes(_vanilla_nbt_bytes())
        r = schematics.list_schematics(self.inst)[0]
        self.assertEqual(r["size"], "7×8×9")
        self.assertEqual(r["author"], "Alex")
        self.assertEqual(r["format"], "结构方块")

    def test_corrupt_file_listed_without_meta(self):
        (self.fold / "broken.litematic").write_bytes(b"not nbt at all")
        r = schematics.list_schematics(self.inst)[0]
        self.assertEqual(r["title"], "")
        self.assertEqual(r["name"], "broken.litematic")

    def test_unrelated_files_skipped(self):
        (self.fold / "readme.txt").write_text("x")
        (self.fold / "wall.schem").write_bytes(_schem_bytes())
        rows = schematics.list_schematics(self.inst)
        self.assertEqual([r["name"] for r in rows], ["wall.schem"])


class ImportDeleteTests(_Isolated):
    def test_import_copies_and_dedupes(self):
        src = self.root / "外部.litematic"
        src.write_bytes(_litematic_bytes())
        added = schematics.import_schematics(self.inst, [str(src)])
        self.assertEqual(added, ["外部.litematic"])
        # 再导入同名 → 自动加序号，不覆盖
        added2 = schematics.import_schematics(self.inst, [str(src)])
        self.assertEqual(added2, ["外部 (2).litematic"])
        self.assertTrue((self.fold / "外部 (2).litematic").is_file())

    def test_import_rejects_unknown_ext(self):
        src = self.root / "b.zip"
        src.write_bytes(b"x")
        with self.assertRaises(schematics.SchematicError):
            schematics.import_schematics(self.inst, [str(src)])

    def test_delete_and_traversal_guard(self):
        (self.fold / "a.schem").write_bytes(_schem_bytes())
        schematics.delete_schematic(self.inst, "a.schem")
        self.assertFalse((self.fold / "a.schem").exists())
        with self.assertRaises(schematics.SchematicError):
            schematics.delete_schematic(self.inst, "../../evil")

    def test_delete_missing_raises(self):
        with self.assertRaises(schematics.SchematicError):
            schematics.delete_schematic(self.inst, "nope.schem")


class FacadeParityTests(unittest.TestCase):
    METHODS = ("list_schematics", "import_schematics", "delete_schematic",
               "open_schematics_folder")

    def test_signatures_match(self):
        import inspect
        from app.backend import BackendAPI as QtBackend
        from bridge.api import BackendAPI
        for name in self.METHODS:
            qt = getattr(QtBackend, name, None)
            br = getattr(BackendAPI, name, None)
            self.assertIsNotNone(qt, f"QtBackend 缺 {name}")
            self.assertIsNotNone(br, f"BackendAPI 缺 {name}")
            self.assertEqual(inspect.signature(qt), inspect.signature(br),
                             f"{name} 签名不一致")


if __name__ == "__main__":
    unittest.main()
