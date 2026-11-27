"""Check that the home and Sea pages of the dashboard card fit the card at every width it takes, on the answers of the online demo.

    python tools/build_card_demo.py _site
    python tools/card_demo_fit.py _site --out fit-captures

The demo (`demo/`, put together by `tools/build_card_demo.py`) draws the card of this commit without Home Assistant, in Home
Assistant's font and line height, on answers recorded at three public places: Biarritz (a beach), Chamonix (the mountains, with
a pill of the season: the avalanches) and Tokyo (abroad, the home page of the world). This script serves the built folder on
this machine, opens it in Chromium (Playwright) for each place, in French and in English, and sets the card from 300 to 720 px
wide (the widget of the Windows app is 300 px, a phone 340 to 400, a column of a desktop 339 to 560). The home page fails when:

- it scrolls (the card is 760 px high in a sections view; `_render` draws its home page again until it fits);
- a text or a box goes past its left or right edge (the page hides what goes over, so it is cut off without an ellipsis);
- a text of a block of columns (the hours, the days) goes under its bottom (the gusts of the days did, on 2026-11-27);
- from 340 px, a text is cut by an ellipsis or two neighbouring columns crowd (`CROWDED` of `tools/card_in_home_assistant.py`).

A place whose card has beaches (Biarritz) then shows its Sea page, each beach in its tab, from 340 to 720 px, judged the same way,
and a text cut by its ellipsis measured by a Range, to the fraction of a pixel (`CUT`). With two beaches, the row of their tabs
made it scroll on a phone's card, and words of its tiles were cut (seen on 2026-11-27).

Home Assistant draws its cards in Roboto, and so does the demo: it serves the files of Home Assistant's frontend
(`demo/fonts/roboto/`), a Roboto installed on the machine first, as Home Assistant does. Without either, a browser draws the card
in the machine's own font (Segoe UI on Windows, DejaVu Sans on Linux), wider, which cuts what Home Assistant does not. The script
says which font it measured in, and whether the demo served it; `--need-roboto` (the CI, which installs Ubuntu's) fails without
Roboto. A capture of the card is kept in `--out` for each failure. The `card` job of the Home Assistant workflow stays the reference
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
# The Sea page is measured from a phone's card up (the widget of the Windows app, 300 px, shows the home page only).
SEA_FROM = 340
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
  // A block of columns (the hours, the days) holds what it says: a text under its bottom crosses the ring of today's column.
  for (const cols of p.querySelectorAll('.cols')) {
    const bottom = cols.getBoundingClientRect().bottom, inside = document.createTreeWalker(cols, NodeFilter.SHOW_TEXT);
    for (let t = inside.nextNode(); t; t = inside.nextNode()) {
      if (!t.textContent.trim()) continue;
      range.selectNodeContents(t);
      const b = range.getBoundingClientRect();
      if (b.height > 0 && b.bottom > bottom + 0.5) out.push('under its columns: ' + t.textContent.trim().slice(0, 30));
    }
  }
  return out;
}"""


# A text that ends in an ellipsis on the Sea page: its own width, measured by a Range, is wider than its box, even by a fraction
# of a pixel (`scrollWidth` is rounded, and missed "pas tous les jours" cut by half a pixel, its ellipsis shown: 2026-11-27).
CUT = r"""(p) => {
  const out = [], range = document.createRange();
  for (const e of p.querySelectorAll('*')) {
    const s = getComputedStyle(e);
    if (s.textOverflow !== 'ellipsis' || s.overflowX === 'visible' || !e.textContent.trim()) continue;
    range.selectNodeContents(e);
    const box = e.getBoundingClientRect().width - parseFloat(s.borderLeftWidth) - parseFloat(s.borderRightWidth)
      - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight);
    const w = range.getBoundingClientRect().width;
    if (w > box + 0.05) out.push(`cut by ${(w - box).toFixed(1)} px: ` + e.textContent.trim().slice(0, 30));
  }
  return out;
}"""


# Whether the page draws in Roboto, installed on this machine or served by the demo and loaded: a text in "Roboto, monospace" as
# wide as in monospace alone was drawn in the fallback. Run on the demo page, whose fonts are loaded before the card is drawn.
HAS_ROBOTO = r"""() => {
  const c = document.createElement('canvas').getContext('2d'), s = 'mmmmmmmmmmlllliiiiWW 0123456789';
  c.font = '40px monospace'; const mono = c.measureText(s).width;
  c.font = '40px Roboto, monospace'; return Math.abs(c.measureText(s).width - mono) > 0.5;
}"""


def set_width(page: Any, width: int) -> None:
    """Set the card's width, and wait until it has drawn itself again at it (its ResizeObserver), then measured its page."""
    page.evaluate("(w) => document.getElementById('frame').style.setProperty('--card-width', w + 'px')", width)
    page.wait_for_function("(w) => document.querySelector('vigie-card')._width === w", arg=width, timeout=10_000)
    page.wait_for_timeout(150)


# The Sea page drawn for the beach of rank `i` (its answer of `vigie/card/beach` in, its head drawn), not the page of before.
SHOWN_BEACH = r"""(i) => {
  const c = document.querySelector('vigie-card');
  return (c._beachIdx || 0) === i && c._beach?.spot?.id === c._config.beaches[i] && !!c.shadowRoot.querySelector('.page .bh');
}"""


def show_beach(page: Any, i: int) -> None:
    """Show the beach of rank `i` on the Sea page: its tab when there are several."""
    tabs = page.locator("vigie-card").locator(".sch")
    if tabs.count() > i:
        tabs.nth(i).click()
    page.wait_for_function(SHOWN_BEACH, arg=i, timeout=10_000)
    page.wait_for_timeout(150)


def judge(
    page: Any, width: int, label: str, found: list[str], out: Path, problems: list[str], by_range: bool = False
) -> str:
    """The page the card shows at this width: `found` and what scrolls, goes over its edges or is cut, kept in `problems`
    with a capture in `out`. `by_range`: a text cut by its ellipsis is measured by a Range (`CUT`)."""
    card = page.locator("vigie-card")
    over = card.locator(".page").evaluate("(p) => p.scrollHeight - p.clientHeight")
    if over > 0:
        found.append(f"scrolls by {over} px")
    edges = card.locator(".page").evaluate(PAST_EDGES)
    found += [x if x.startswith("under") else f"past the edge, {x}" for x in edges]
    if width >= STRICT_FROM:
        found += card.locator(".page").evaluate(CROWDED)
        if by_range:
            found += card.locator(".page").evaluate(CUT)
    if found:
        problems.append(f"{label}: {'; '.join(found[:4])}")
        out.mkdir(parents=True, exist_ok=True)
        page.locator("#frame").screenshot(path=str(out / f"{label}.png"))
    return "ok" if not found else "NO"


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
    parser.add_argument(
        "--need-roboto", action="store_true", help="fail when Roboto, Home Assistant's font, is missing"
    )
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
            probe = browser.new_page()
            served: list[str] = []
            probe.on(
                "response",
                lambda r: served.append(r.url.rsplit("/", 1)[-1]) if "/fonts/" in r.url and r.ok else None,
            )
            probe.goto(f"{base}/index.html")
            probe.wait_for_function(
                "() => document.querySelector('vigie-card')?.shadowRoot?.querySelector('.page')", timeout=30_000
            )
            probe.evaluate("() => document.fonts.ready")
            roboto = probe.evaluate(HAS_ROBOTO)
            probe.close()
            if not roboto:
                note = (
                    "Roboto, the font of Home Assistant, is neither on this machine nor served by the demo "
                    "(demo/fonts/roboto/): the card is measured in this machine's own font"
                )
                if args.need_roboto:
                    print(note, file=sys.stderr)
                    browser.close()
                    return 2
                print(f"({note})")
            elif served:
                print(
                    f"(the card is measured in Roboto, the font of Home Assistant, served by the demo: {', '.join(sorted(served))})"
                )
            else:
                print("(the card is measured in Roboto, the font of Home Assistant, installed on this machine)")
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
                    card = page.locator("vigie-card")
                    for width in WIDTHS:
                        set_width(page, width)
                        shown = card.evaluate("(c) => c._page")
                        found = [] if shown == "home" else [f"the page shown is {shown}, not home"]
                        row.append(
                            f"{width}:{judge(page, width, f'{place}_{lang}_{width}', found, args.out, problems)}"
                        )
                    print(f"{place} {lang}: {' '.join(row)}")
                    # The Sea page of each beach the card is set to (one tab each), on a phone's card and wider.
                    beaches = card.evaluate("(c) => (c._config.beaches || []).length")
                    if beaches:
                        card.evaluate("(c) => c._go('sea')")
                        row = []
                        for width in (w for w in WIDTHS if w >= SEA_FROM):
                            set_width(page, width)
                            marks = []
                            for i in range(beaches):
                                show_beach(page, i)
                                found = [] if card.evaluate("(c) => c._page") == "sea" else ["not the Sea page"]
                                marks.append(
                                    judge(
                                        page,
                                        width,
                                        f"{place}_{lang}_sea{i + 1}_{width}",
                                        found,
                                        args.out,
                                        problems,
                                        by_range=True,
                                    )
                                )
                            row.append(f"{width}:{'/'.join(marks)}")
                        print(f"{place} {lang} sea ({beaches} beaches): {' '.join(row)}")
                    problems += [f"{place}_{lang}: the browser logged {e}" for e in errors]
                    page.close()
            browser.close()
    finally:
        server.shutdown()
    if problems:
        print(f"\nA page does not fit the card ({len(problems)}):", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(
        f"\nThe home page fits the card at {len(WIDTHS)} widths, for {len(PLACES)} places, in {len(LANGS)} languages;"
        " so does the Sea page of each beach of the demo, from a phone's card up."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
