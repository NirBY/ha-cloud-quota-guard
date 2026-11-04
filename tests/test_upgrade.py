"""A HACS-style component replacement must preserve separate HA state."""

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "quota_package", ROOT / "scripts/package_release.py"
)
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


class UpgradeTests(unittest.TestCase):
    def test_complete_runtime_survives_component_replacement_without_losing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            installed = root / "config/custom_components/quota_guard"
            installed.mkdir(parents=True)
            (installed / "frontend").mkdir()
            (installed / "frontend/quota-guard-card.js").write_text("old bundle")
            storage = root / "config/.storage"
            storage.mkdir()
            fixtures = {
                "quota_guard": {
                    "metrics": {
                        "custom_usage": {
                            "config": {"name": "My usage"},
                            "points": [{"used": 7}],
                        }
                    }
                },
                "lovelace.dashboard_service_quotas": {
                    "views": [
                        {
                            "cards": [
                                {"type": "custom:quota-guard-card", "language": "he"}
                            ]
                        }
                    ]
                },
            }
            for name, data in fixtures.items():
                (storage / name).write_text(json.dumps(data), encoding="utf-8")
            before = {p.name: p.read_bytes() for p in storage.iterdir()}
            archive_path = packager.build_archive(root / "release.zip")
            shutil.rmtree(installed)
            with ZipFile(archive_path) as archive:
                for name in archive.namelist():
                    if name.startswith("custom_components/quota_guard/"):
                        archive.extract(name, root / "config")
            for source in (ROOT / "custom_components/quota_guard").rglob("*"):
                if (
                    not source.is_file()
                    or "__pycache__" in source.parts
                    or source.suffix == ".pyc"
                ):
                    continue
                self.assertEqual(
                    source.read_bytes(),
                    (
                        installed
                        / source.relative_to(ROOT / "custom_components/quota_guard")
                    ).read_bytes(),
                )
            self.assertEqual(
                before, {p.name: p.read_bytes() for p in storage.iterdir()}
            )
            card = (installed / "frontend/quota-guard-card.js").read_text(
                encoding="utf-8"
            )
            for feature in (
                "usageTile",
                "usageDetail",
                "related automations",
                "HE_TEXT",
                "registeredCard",
            ):
                self.assertIn(feature, card)
