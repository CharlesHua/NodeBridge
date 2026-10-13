import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts import build_portable


class PortableBuildTests(unittest.TestCase):
    def test_rebuild_leaves_existing_data_untouched_and_excludes_it_from_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "dist" / "NodeBridge" / "data"
            data.mkdir(parents=True)
            (data / "sites.json").write_text("private", encoding="utf-8")
            (root / "LICENSE").write_text("license", encoding="utf-8")

            def fake_build(args, **_kwargs):
                bundle = Path(args[args.index("--distpath") + 1]) / "NodeBridge"
                self.assertIn("portable-builds", Path(args[args.index("--workpath") + 1]).parts)
                bundle.mkdir(parents=True, exist_ok=True)
                (bundle / "NodeBridge.exe").write_bytes(b"exe")
                _kwargs["stdout"].write("路径：西湖大学\\NodeBridge\n".encode("utf-8"))
                self.assertEqual(_kwargs["env"]["PYTHONIOENCODING"], "utf-8")
                self.assertEqual(_kwargs["stderr"], subprocess.STDOUT)

            with patch.object(build_portable, "ROOT", root), \
                 patch.object(build_portable.subprocess, "run", side_effect=fake_build):
                build_portable.main()

            self.assertEqual((data / "sites.json").read_text(encoding="utf-8"), "private")
            self.assertIn("路径：西湖大学", (root / "dist" / "build_portable_latest.log").read_text(encoding="utf-8"))
            with zipfile.ZipFile(root / "dist" / "NodeBridge-portable.zip") as archive:
                self.assertIn("NodeBridge/NodeBridge.exe", archive.namelist())
                self.assertNotIn("NodeBridge/data/sites.json", archive.namelist())

    def test_build_failure_restores_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "dist" / "NodeBridge" / "data"
            data.mkdir(parents=True)
            (data / "sites.json").write_text("private", encoding="utf-8")
            with patch.object(build_portable, "ROOT", root), \
                 patch.object(build_portable.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "build")):
                with self.assertRaises(subprocess.CalledProcessError):
                    build_portable.main()
            self.assertEqual((data / "sites.json").read_text(encoding="utf-8"), "private")
