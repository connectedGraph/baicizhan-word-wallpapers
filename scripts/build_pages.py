"""Build the static GitHub Pages artifact into dist/."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PAGE = ROOT / "index.html"
SOURCE_DATA = ROOT / "海报数据"
DIST = ROOT / "dist"
DIST_DATA = DIST / "海报数据"


def main() -> None:
    if not SOURCE_PAGE.is_file():
        raise SystemExit(f"Missing page source: {SOURCE_PAGE}")
    manifest_path = SOURCE_DATA / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"Missing manifest: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files")
    if not isinstance(files, list) or not all(isinstance(name, str) for name in files):
        raise SystemExit("manifest.json must contain a string array at files")

    # dist is a generated artifact; only this project-local directory is replaced.
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST_DATA.mkdir(parents=True)

    shutil.copy2(SOURCE_PAGE, DIST / "index.html")
    shutil.copy2(manifest_path, DIST_DATA / "manifest.json")

    missing = []
    for name in files:
        source = SOURCE_DATA / name
        if not source.is_file():
            missing.append(name)
            continue
        shutil.copy2(source, DIST_DATA / name)
    if missing:
        raise SystemExit(f"Manifest references missing images: {', '.join(missing[:5])}")

    # Prevent GitHub Pages from applying Jekyll transforms to the static artifact.
    (DIST / ".nojekyll").write_text("", encoding="utf-8")
    build_info = {
        "imageCount": len(files),
        "source": "海报数据/manifest.json",
    }
    (DIST / "build.json").write_text(
        json.dumps(build_info, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Built {len(files)} images into {DIST}")


if __name__ == "__main__":
    main()
