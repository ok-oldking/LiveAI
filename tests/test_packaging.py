from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_launcher_profiles_can_run_the_deployed_source(self) -> None:
        config = yaml.safe_load((ROOT / "pyappify.yml").read_text(encoding="utf-8"))
        deployed = (ROOT / "deploy.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(config["profiles"]), 2)
        for profile in config["profiles"]:
            with self.subTest(profile=profile["name"]):
                self.assertIn(profile["main_script"], deployed)
                self.assertIn(profile["requirements"], deployed)
                self.assertIn(profile["PYTHONPATH"], deployed)
                self.assertEqual(profile["requires_python"], "3.12")
                self.assertTrue(profile["use_pythonw"])
                self.assertTrue((ROOT / profile["main_script"]).is_file())
                self.assertTrue((ROOT / profile["requirements"]).is_file())
                self.assertTrue((ROOT / profile["PYTHONPATH"] / "live_gpt/app.py").is_file())
        for path in deployed:
            self.assertTrue((ROOT / path).exists(), f"Missing deploy entry: {path}")
        self.assertTrue((ROOT / config["icon"]).is_file())
        for extension in ("png", "ico"):
            self.assertEqual((ROOT / f"icons/icon.{extension}").read_bytes(),
                             (ROOT / f"src/live_gpt/assets/app-icon.{extension}").read_bytes())

    def test_no_deps_runtime_lock_includes_required_transitive_dependencies(self) -> None:
        lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        locked = {}
        for line in lines:
            if line.strip() and not line.startswith("#"):
                requirement = Requirement(line)
                specifiers = list(requirement.specifier)
                self.assertEqual(len(specifiers), 1)
                self.assertEqual(specifiers[0].operator, "==")
                name = canonicalize_name(requirement.name)
                self.assertNotIn(name, locked)
                locked[name] = specifiers[0].version
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        for source in project["project"]["dependencies"]:
            requirement = Requirement(source)
            self.assertIn(locked[canonicalize_name(requirement.name)], requirement.specifier)
        for name, version in locked.items():
            with self.subTest(package=name):
                distribution = importlib.metadata.distribution(name)
                self.assertEqual(distribution.version, version)
                for source in distribution.requires or []:
                    dependency = Requirement(source)
                    if dependency.marker is not None and not dependency.marker.evaluate({"extra": ""}):
                        continue
                    dependency_name = canonicalize_name(dependency.name)
                    self.assertIn(dependency_name, locked, f"Missing dependency of {name}: {source}")
                    self.assertIn(locked[dependency_name], dependency.specifier)

    def test_staged_update_package_loads_ui_and_entry_point(self) -> None:
        config = yaml.safe_load((ROOT / "pyappify.yml").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            staged = Path(directory)
            for entry in (ROOT / "deploy.txt").read_text(encoding="utf-8").splitlines():
                source, destination = ROOT / entry, staged / entry
                if source.is_dir():
                    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
            code = """
import json
import runpy
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
import live_gpt.app as app
from live_gpt.pet import default_pet_path

root = Path.cwd().resolve()
assert Path(app.__file__).resolve().is_relative_to(root)
assert app.ICON_PATH.is_file()
assert default_pet_path().resolve().is_relative_to(root)
application = QApplication([])
window = app.OverlayWindow()
assert not window.pet.sheet.isNull()
window.close()
with patch.object(app, 'main', return_value=0) as main:
    try:
        runpy.run_path('main.py', run_name='__main__')
    except SystemExit as exit:
        assert exit.code == 0
    main.assert_called_once_with()
print(json.dumps({'source': str(Path(app.__file__).resolve())}))
"""
            result = subprocess.run(
                [sys.executable, "-c", code], cwd=staged,
                env={**os.environ, "PYTHONPATH": config["profiles"][0]["PYTHONPATH"],
                     "PYTHONNOUSERSITE": "1", "QT_QPA_PLATFORM": "offscreen"},
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertTrue(Path(payload["source"]).samefile(staged / "src/live_gpt/app.py"))


if __name__ == "__main__":
    unittest.main()
