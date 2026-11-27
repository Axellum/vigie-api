"""Put the online demo of the Home Assistant card together in a folder, as GitHub Pages serves it (`.github/workflows/pages.yml`).

    python tools/build_card_demo.py _site

The page, its script and style and the recorded answers come from `demo/`; the card itself is the integration's file
(`custom_components/vigie/frontend/vigie-card.js`), copied as it is, so the demo always shows the card of the commit it is built
from; the icon is the integration's.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
CARD = ROOT / "custom_components" / "vigie" / "frontend" / "vigie-card.js"
ICON = ROOT / "custom_components" / "vigie" / "brand" / "icon.png"
PAGE_FILES = ("index.html", "demo.js", "demo.css")


def build(out: Path) -> list[Path]:
    """Write the site into `out` (emptied first); the files written."""
    data = sorted((DEMO / "data").glob("*.json"))
    if not data:
        raise SystemExit("demo/data has no recorded answer: run tools/record_card_demo.py")
    if out.exists():
        shutil.rmtree(out)
    (out / "data").mkdir(parents=True)
    written = []
    for name in PAGE_FILES:
        written.append(Path(shutil.copy2(DEMO / name, out / name)))
    for path in data:
        written.append(Path(shutil.copy2(path, out / "data" / path.name)))
    written.append(Path(shutil.copy2(CARD, out / "vigie-card.js")))
    written.append(Path(shutil.copy2(ICON, out / "icon.png")))
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("out", type=Path, help="the folder to write (emptied first)")
    out = parser.parse_args().out
    for path in build(out):
        print(path.relative_to(out).as_posix(), path.stat().st_size)


if __name__ == "__main__":
    main()
