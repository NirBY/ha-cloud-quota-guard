"""Relations expose direct dependencies without changing automation state."""

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace

path = (
    Path(__file__).resolve().parents[1]
    / "custom_components/quota_guard/relationships.py"
)
spec = importlib.util.spec_from_file_location("quota_relationships", path)
relationships = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relationships)


class RelationshipTests(unittest.TestCase):
    def test_entity_and_device_matches_exclude_unrelated_automation(self):
        def automation(name, entities=(), devices=()):
            return SimpleNamespace(
                entity_id="automation." + name,
                name=name,
                unique_id=name,
                referenced_entities=entities,
                referenced_devices=devices,
            )

        result = relationships.related_automations(
            {"github": {"sensor.github"}},
            {"tuya": {"device_tuya"}},
            [
                automation("repo", ["sensor.github"]),
                automation("lights", devices=["device_tuya"]),
                automation("other", ["sensor.other"]),
            ],
        )
        self.assertEqual(result["github"][0]["automation_id"], "repo")
        self.assertEqual(result["tuya"][0]["devices"], ["device_tuya"])
        self.assertNotIn("other", str(result))
