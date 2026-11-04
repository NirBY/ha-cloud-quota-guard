"""Build a complete manual-install archive from the same tracked runtime as HACS."""

import argparse
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/quota_guard"


def build_archive(destination):
    """Include every runtime subdirectory; never package local caches or state."""
    files = sorted(
        p
        for p in COMPONENT.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    )
    expected = {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in files}
    for name in ("README.md", "LICENSE", "CHANGELOG.md", "examples/dashboard.yaml"):
        expected[name] = (ROOT / name).read_bytes()
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for name, content in expected.items():
            archive.writestr(name, content)
    with ZipFile(destination) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(expected):
            raise ValueError("Incomplete release archive")
        for name, content in expected.items():
            if archive.read(name) != content:
                raise ValueError(f"Mismatched release asset: {name}")
    return destination


if __name__ == "__main__":
    version = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))[
        "version"
    ]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / f"ha-cloud-quota-guard-v{version}.zip"
    )
    args = parser.parse_args()
    print(build_archive(args.output))
