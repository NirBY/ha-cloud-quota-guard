"""Check the distributable metadata, assets, and bundled card."""

import json
from pathlib import Path

import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/quota_guard"
manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8-sig"))
hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8-sig"))
assert manifest["domain"] == "quota_guard"
assert manifest["name"] == hacs["name"] == "Cloud Quota Guard"
assert manifest["codeowners"] == ["@NirBY"]
assert manifest["documentation"] == "https://github.com/NirBY/ha-cloud-quota-guard"
assert manifest["issue_tracker"].endswith("/ha-cloud-quota-guard/issues")
assert len(list((ROOT / "custom_components").iterdir())) == 1
assert (COMPONENT / "frontend/quota-guard-card.js").is_file()
assert (ROOT / "LICENSE").is_file()
for filename, size in [("icon.png", 256), ("icon@2x.png", 512)]:
    with Image.open(COMPONENT / "brand" / filename) as icon:
        assert icon.format == "PNG" and icon.size == (size, size)
        assert icon.mode == "RGBA"
strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8-sig"))
services = yaml.safe_load((COMPONENT / "services.yaml").read_text(encoding="utf-8-sig"))
assert set(services) == set(strings["services"]) == {"configure", "report"}
for service, data in services.items():
    assert set(data["fields"]) == set(strings["services"][service]["fields"])
for file in COMPONENT.rglob("*.json"):
    json.loads(file.read_text(encoding="utf-8-sig"))
print(
    "Package metadata, action translations, bundled frontend and brand icons verified"
)

# A stale generated bundle must fail validation rather than ship mixed languages.
catalog = json.loads((COMPONENT / "languages/he.json").read_text(encoding="utf-8"))
card = (COMPONENT / "frontend/quota-guard-card.js").read_text(encoding="utf-8")
assert (
    "const HE_TEXT="
    + json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
    + ";"
    in card
)
