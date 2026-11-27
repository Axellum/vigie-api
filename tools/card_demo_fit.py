"""Check that the home page of the dashboard card fits the card at every width it takes, on the answers of the online demo.

    python tools/build_card_demo.py _site
    python tools/card_demo_fit.py _site --out fit-captures

The demo (`demo/`, put together by `tools/build_card_demo.py`) draws the card of this commit without Home Assistant, in Home
Assistant's font and line height, on answers recorded at three public places: Biarritz (a beach), Chamonix (the mountains, with
a pill of the season: the avalanches) and Tokyo (abroad, the home page of the world). This script serves the built folder on
this machine, opens it in Chromium (Playwright) for each place, in French and in English, and sets the card from 300 to 720 px
wide (the widget of the Windows app is 300 px, a phone 340 to 400, a column of a desktop 339 to 560). The home page fails when:

- it scrolls (the card is 760 px high in a sections view; `_render` draws its home page again until it fits);
- a text or a box goes past its left or right edge (the page hides what goes over, so it is cut off without an ellipsis);
- from 340 px, a text is cut by an ellipsis or two neighbouring columns crowd (`CROWDED` of `tools/card_in_home_assistant.py`).

A capture of the card is kept in `--out` for each failure. The `card` job of the Home Assistant workflow stays the reference
in Home Assistant's own frontend (Biarritz, 390 and 1280 px); this one covers the widths and the places that job does not
(Chamonix scrolled by 13 px from 300 to 380 px wide, and Tokyo's pill went 30 px past the edge, at every width: seen on
2026-11-27). Standard library and Playwright only; nothing leaves this machine.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import sys
import threading
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

# The same rule of a cut or crowded text as the job in a real Home Assistant.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from card_in_home_assistant import CROWDED  # noqa: E402

PLACES = ("biarritz", "chamonix", "tokyo")
LANGS = ("fr", "en")
WIDTHS = (300, 320, 340, 360, 390, 420, 480, 560, 720)
# Narrower, the card may end a long word with an ellipsis (it still fits, and puts nothing past its edges).
STRICT_FROM = 340
# A text or a bordered box of the page past its left or right edge, measured on the text itself (a cell stretched to its
# column, or a label box wider than its text, hides where the text ends). A text its box ends with an ellipsis is not past
# the edge but cut, which the rule of a cut text judges.
PAST_EDGES = r"""(p) => {
  const pr = p.getBoundingClientRect(), out = [], range = document.createRange();
  const past = (b) => b.width > 0 && (b.right > pr.right + 0.5 || b.left < pr.left - 0.5);
  const ellipsis = (t) => {
    for (let e = t.parentElement; e && e !== p; e = e.parentElement) {
      if (getComputedStyle(e).textOverflow === 'ellipsis' && e.scrollWidth > e.clientWidth + 0.5) return true;
    }
    return false;
  };
  const walk = document.createTreeWalker(p, NodeFilter.SHOW_TEXT);
  for (let t = walk.nextNode(); t; t = walk.nextNode()) {
    if (!t.textContent.trim() || ellipsis(t)) continue;
    range.selectNodeContents(t);
    if (past(range.getBoundingClientRect())) out.push('text: ' + t.textContent.trim().slice(0, 30));
  }
  for (const e of p.querySelectorAll('*')) {
    const s = getComputedStyle(e);
    if (!(parseFloat(s.borderLeftWidth) || parseFloat(s.borderRightWidth))) continue;
    if (past(e.getBoundingClientRect())) out.push('box: ' + (e.textContent || e.className).trim().slice(0, 30));
  }
  return out;
}"""


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (the base's name)  the requests of the page are not news
        pass


def serve(folder: Path) -> tuple[http.server.ThreadingHTTPServer, str]:
    """Serve `folder` on a free port of this machine; the server and its address."""
    handler = functools.partial(_Quiet, directory=str(folder))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("site", type=Path, help="the folder written by tools/build_card_demo.py")
    parser.add_argument("--out", type=Path, default=Path("fit-captures"), help="where the captures of the failures go")
    args = parser.parse_args(argv)
    if not (args.site / "vigie-card.js").is_file():
        print(
            f"{args.site} has no vigie-card.js: run python tools/build_card_demo.py {args.site} first", file=sys.stderr
        )
        return 2
    server, base = serve(args.site)
    problems: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for place in PLACES:
                for lang in LANGS:
                    page = browser.new_page(viewport={"width": 1300, "height": 1100})
                    errors: list[str] = []
                    page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                    page.on("console", lambda m, errors=errors: errors.append(m.text) if m.type == "error" else None)
                    stored = json.dumps({"place": place, "theme": "dark", "lang": lang, "width": 480})
                    page.add_init_script(f"localStorage.setItem('vigie-demo', {json.dumps(stored)})")
                    page.goto(f"{base}/index.html")
                    page.wait_for_function(
                        "() => document.querySelector('vigie-card')?.shadowRoot?.querySelector('.page')", timeout=30_000
                    )
                    page.evaluate("() => document.fonts.ready")
                    row = []
                    for width in WIDTHS:
                        page.evaluate(
                            "(w) => document.getElementById('frame').style.setProperty('--card-width', w + 'px')", width
                        )
                        # The card draws itself again when its width changes (its ResizeObserver), then measures its home page.
                        page.wait_for_function(
                            "(w) => document.querySelector('vigie-card')._width === w", arg=width, timeout=10_000
                        )
                        page.wait_for_timeout(150)
                        card = page.locator("vigie-card")
                        shown = card.evaluate("(c) => c._page")
                        found = [] if shown == "home" else [f"the page shown is {shown}, not home"]
                        over = card.locator(".page").evaluate("(p) => p.scrollHeight - p.clientHeight")
                        if over > 0:
                            found.append(f"scrolls by {over} px")
                        found += [f"past the edge, {x}" for x in card.locator(".page").evaluate(PAST_EDGES)]
                        if width >= STRICT_FROM:
                            found += card.locator(".page").evaluate(CROWDED)
                        row.append(f"{width}:{'ok' if not found else 'NO'}")
                        if found:
                            label = f"{place}_{lang}_{width}"
                            problems.append(f"{label}: {'; '.join(found[:4])}")
                            args.out.mkdir(parents=True, exist_ok=True)
                            page.locator("#frame").screenshot(path=str(args.out / f"{label}.png"))
                    print(f"{place} {lang}: {' '.join(row)}")
                    problems += [f"{place}_{lang}: the browser logged {e}" for e in errors]
                    page.close()
            browser.close()
    finally:
        server.shutdown()
    if problems:
        print(f"\nThe home page does not fit the card ({len(problems)}):", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(
        f"\nThe home page fits the card at {len(WIDTHS)} widths, for {len(PLACES)} places, in {len(LANGS)} languages."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
