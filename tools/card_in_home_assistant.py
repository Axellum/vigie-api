"""Try the dashboard card in a real Home Assistant, and take captures of it (the `card` job of the Home Assistant workflow).

A throwaway Home Assistant runs in its official container with `custom_components/vigie` mounted, and the fake instance
replays the answers recorded for Biarritz (``python tools/fake_instance.py --replay tests_ha/fixtures``). This script:

1. finishes the onboarding with a throwaway user, in French, the place in Biarritz;
2. adds the Vigie entry through its config flow, pointed at the fake instance;
3. puts the card on a dashboard of its own, under a banner that says the data are a replay;
4. opens the dashboard in Chromium (Playwright) at a phone width and a wall width, in the dark and the light themes, goes
   through every page of the card by its bar, and saves a capture of each; then opens the card's editor.

With ``--card-from /local/vigie/vigie-card.js`` it also fails when the frontend loaded the card from anywhere else (ADR 0044:
the dashboard resource the integration lists where the www folder is served), and says whether it came compressed.

It fails when the frontend did not load the card, when the card shows no page or an error (then a capture
``echec_<width>_<theme>.png`` and what the card and Home Assistant said are kept), when a page of the card is missing,
when the editor does not draw all its fields, or when the browser logs an error of the card (or any uncaught error while
the editor is open). The other errors of Home Assistant's frontend are printed, with the elements their file names. Standard library and Playwright only; it never talks to anything but the
Home Assistant and the fake instance on this machine.

    python tools/card_in_home_assistant.py --ha http://127.0.0.1:8123 --instance http://127.0.0.1:8099 --out captures
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

PLACE = {"latitude": 43.48, "longitude": -1.56}  # Biarritz, a public place: the place of the recorded answers
WIDTHS = {"phone": (390, 1100), "wall": (1280, 900)}
PAGES = ("home", "details", "alerts", "sea", "normal")
DASHBOARD = "essai-carte"  # a dashboard of its own (the default one is left as it is); no "vigie" in its address, which would pass for the card's
# The spot whose verdicts were recorded ("auto" would pick the Grande Plage, nearer the place, whose verdicts were not).
SPOT = "biarritz-plage-des-corsaires"
# Every `vigie-card` of the page, through the shadow roots of Home Assistant's elements.
FIND_CARDS = """() => {
    const found = [];
    const walk = (root) => root.querySelectorAll('*').forEach((el) => {
        if (el.tagName === 'VIGIE-CARD') found.push(el);
        if (el.shadowRoot) walk(el.shadowRoot);
    });
    walk(document);
    return found;
}"""
BANNER = (
    "**SIMULATION** — réponses de l'API enregistrées le 22/11/2026 et rejouées à l'heure du test "
    "(`tools/fake_instance.py --replay`) : ce n'est pas la météo du jour."
)


def call(url: str, data: Any = None, token: str | None = None, form: bool = False) -> Any:
    """A request to Home Assistant; JSON in, JSON out."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(request, timeout=30) as reply:
        text = reply.read().decode()
    return json.loads(text) if text else None


def wait_for(url: str, seconds: int = 300) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5):
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(2)
    raise SystemExit(f"{url} did not answer within {seconds} s")


def onboard(ha: str) -> dict[str, Any]:
    """The onboarding of a fresh Home Assistant, with a throwaway user; the tokens of that user."""
    client_id = f"{ha}/"
    code = call(f"{ha}/api/onboarding/users", {"client_id": client_id, "name": "Test", "username": "test", "password": "test-only-in-ci", "language": "fr"})
    tokens = call(f"{ha}/auth/token", {"grant_type": "authorization_code", "code": code["auth_code"], "client_id": client_id}, form=True)
    for step in ("core_config", "analytics"):
        call(f"{ha}/api/onboarding/{step}", {}, tokens["access_token"])
    call(f"{ha}/api/onboarding/integration", {"client_id": client_id, "redirect_uri": f"{ha}/?auth_callback=1"}, tokens["access_token"])
    return tokens


def add_entry(ha: str, instance: str, token: str) -> None:
    """The Vigie entry, through its config flow (the steps a person clicks)."""
    flow = call(f"{ha}/api/config/config_entries/flow", {"handler": "vigie", "show_advanced_options": False}, token)
    result = call(f"{ha}/api/config/config_entries/flow/{flow['flow_id']}", {"name": "Vigie Biarritz", "url": instance, "location": PLACE}, token)
    if result.get("type") != "create_entry":
        raise SystemExit(f"the config flow did not create the entry: {result}")


def ws(page: Page, message: dict[str, Any]) -> Any:
    return page.evaluate("(m) => document.querySelector('home-assistant').hass.callWS(m)", message)


def login(page: Page, ha: str, tokens: dict[str, Any], dark: bool) -> None:
    stored = {
        "access_token": tokens["access_token"],
        "token_type": "Bearer",
        "expires_in": tokens["expires_in"],
        "hassUrl": ha,
        "clientId": f"{ha}/",
        "expires": int(time.time() * 1000) + tokens["expires_in"] * 1000,
        "refresh_token": tokens["refresh_token"],
    }
    theme = {"theme": "default", "dark": dark}
    page.add_init_script(f"localStorage.setItem('hassTokens', {json.dumps(json.dumps(stored))});"
                         f"localStorage.setItem('selectedTheme', {json.dumps(json.dumps(theme))});")


def explain(page: Page, out: Path, label: str, errors: list[str]) -> str:
    """What the page shows when the card shows no page: a capture, and what the card and Home Assistant say."""
    page.screenshot(path=str(out / f"echec_{label}.png"), full_page=True)
    lines = [f"url: {page.url}"]
    lines.append("cards: " + json.dumps(page.evaluate(
        f"() => ({FIND_CARDS})().map((c) => (c.shadowRoot ? c.shadowRoot.textContent : '(no shadow root)').replace(/\\s+/g, ' ').slice(-300))"
    ), ensure_ascii=False))
    for message in ({"type": "vigie/card/data", "spot": SPOT}, {"type": "config_entries/get", "domain": "vigie"}):
        try:
            answer = page.evaluate("(m) => document.querySelector('home-assistant').hass.callWS(m).then((r) => ({ok: r}), (e) => ({error: e}))", message)
        except Exception as err:  # noqa: BLE001 - a diagnosis prints whatever went wrong
            answer = {"failed": str(err)}
        lines.append(f"{message['type']}: {json.dumps(answer, ensure_ascii=False)[:600]}")
    lines.extend(errors)
    return "\n  ".join(lines)


FRAME = re.compile(r"(https?://\S+?\.js):(\d+):(\d+)")


def near(page: Page, error: str) -> str:
    """The custom elements named in the file of Home Assistant's frontend an error points at (the files are minified: the
    names of the elements are what tells which one it was), and the code around the place."""
    match = FRAME.search(error)
    if not match:
        return ""
    url, line, column = match.group(1), int(match.group(2)), int(match.group(3))
    try:
        source = page.evaluate("(u) => fetch(u).then((r) => r.text())", url).split("\n")[line - 1]
    except Exception:  # noqa: BLE001 - a diagnosis that cannot read the file says nothing more
        return ""
    window = source if len(source) < 30_000 else source[max(0, column - 6000) : column + 2000]
    names = sorted(set(re.findall(r"[\"'`]((?:ha|hui|state|more-info|dialog|entity)-[a-z0-9-]+)[\"'`]", window)))
    return f"\n    near: {', '.join(names[:20]) or 'no element name'}\n    code: {source[max(0, column - 200) : column + 60]!r}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--ha", default="http://127.0.0.1:8123")
    parser.add_argument("--instance", default="http://127.0.0.1:8099")
    parser.add_argument("--out", type=Path, default=Path("captures"))
    parser.add_argument("--card-from", help="the path the frontend must load the card from, e.g. /local/vigie/vigie-card.js")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    wait_for(f"{args.ha}/api/onboarding")  # answers without a user while the onboarding is not done
    tokens = onboard(args.ha)
    token = tokens["access_token"]
    add_entry(args.ha, args.instance, token)
    problems: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for dark in (True, False):
            for name, (width, height) in WIDTHS.items():
                context = browser.new_context(viewport={"width": width, "height": height}, locale="fr-FR", timezone_id="Europe/Paris")
                page = context.new_page()
                errors: list[str] = []
                phase = ["dashboard"]  # or "editor": what was on the screen when an error came
                # the stack names the file an uncaught error came from (`/vigie/vigie-card.js` for the card)
                page.on("pageerror", lambda err, errors=errors, phase=phase: errors.append(f"[{phase[0]}] page error: {err.message} {err.stack or ''}"))
                page.on("console", lambda msg, errors=errors, phase=phase: errors.append(f"[{phase[0]}] console: {msg.text}") if msg.type == "error" else None)
                # a console error "Failed to load resource" does not say which: the answer does
                page.on("response", lambda r, errors=errors, phase=phase: errors.append(f"[{phase[0]}] HTTP {r.status}: {r.url}") if r.status >= 400 else None)
                loaded: list[tuple[str, str]] = []  # where the card's file came from, and its encoding
                page.on("response", lambda r, loaded=loaded: loaded.append((urllib.parse.urlsplit(r.url).path, r.headers.get("content-encoding", "none"))) if r.url.split("?")[0].endswith("/vigie-card.js") else None)
                login(page, args.ha, tokens, dark)
                connected = "() => document.querySelector('home-assistant')?.hass?.connected"
                if dark and name == "phone":  # once: the place, the language, and the dashboard
                    page.goto(f"{args.ha}/profile")
                    page.wait_for_function(connected, timeout=60_000)
                    ws(page, {"type": "config/core/update", **PLACE, "time_zone": "Europe/Paris", "country": "FR", "language": "fr",
                              "unit_system": "metric", "currency": "EUR", "location_name": "Biarritz"})
                    ws(page, {"type": "frontend/set_user_data", "key": "language", "value": {
                        "language": "fr", "number_format": "language", "time_format": "language", "date_format": "language",
                        "first_weekday": "language", "time_zone": "local"}})
                    ws(page, {"type": "lovelace/dashboards/create", "url_path": DASHBOARD, "title": "Vigie", "icon": "mdi:eye",
                              "show_in_sidebar": True, "require_admin": False})
                    ws(page, {"type": "lovelace/config/save", "url_path": DASHBOARD, "config": {"title": "Vigie", "views": [{
                        "title": "Vigie", "path": "carte", "panel": True,
                        "cards": [{"type": "vertical-stack", "cards": [{"type": "markdown", "content": BANNER}, {"type": "custom:vigie-card", "spot": SPOT}]}],
                    }]}})
                page.goto(f"{args.ha}/{DASHBOARD}/carte")
                page.wait_for_function(connected, timeout=60_000)
                theme = "sombre" if dark else "clair"
                served = page.evaluate("() => !!customElements.get('vigie-card') && (window.customCards || []).some((c) => c.type === 'vigie-card')")
                if not served:
                    problems.append(f"{name}: the frontend did not load the card")
                    context.close()
                    continue
                print(f"({name}/{theme}: the card came from {loaded})")  # a new browser context each time: never from a cache
                if args.card_from and not any(path == args.card_from for path, _ in loaded):
                    problems.append(f"{name}/{theme}: the card came from {loaded}, not from {args.card_from}")
                card = page.locator("vigie-card")
                try:
                    card.locator(".page").wait_for(timeout=60_000)
                except PlaywrightTimeout:
                    problems.append(f"{name}/{theme}: the card shows no page\n  {explain(page, args.out, f'{name}_{theme}', errors)}")
                    context.close()
                    continue
                # Home Assistant applies the theme when its list of themes arrives, and skips it when its configuration has not
                # arrived yet (`_applyTheme` of the frontend's themes-mixin.ts): `darkMode` then stays unset until the next change
                # of theme or of the browser's colour scheme. A change of the colour scheme makes it apply the theme asked (the
                # stored `selectedTheme`, whose `dark` is explicit) once more; the scheme ends as the theme asked.
                dark_mode = "() => document.querySelector('home-assistant')?.hass?.themes?.darkMode"
                if page.evaluate(dark_mode) is None:
                    print(f"({name}/{theme}: Home Assistant had not applied its theme; it is applied again)")
                for scheme in ("light", "dark") if dark else ("dark", "light"):
                    page.emulate_media(color_scheme=scheme)
                try:
                    page.wait_for_function(f"(d) => ({dark_mode})() === d", arg=dark, timeout=30_000)
                except PlaywrightTimeout:
                    pass  # said just below
                dark_seen = page.evaluate("() => document.querySelector('home-assistant').hass.themes.darkMode")
                if dark_seen is not dark:
                    problems.append(f"{name}: dark mode is {dark_seen}, {dark} was asked")
                # the whole screen once Home Assistant has finished drawing it: its launch screen gone, the banner drawn
                try:
                    page.wait_for_function("() => !document.getElementById('ha-launch-screen')", timeout=30_000)
                    page.get_by_text("SIMULATION").first.wait_for(timeout=30_000)
                except PlaywrightTimeout:
                    print(f"({name}/{theme}: Home Assistant had not finished drawing the screen after 30 s)")
                page.wait_for_timeout(1000)
                page.screenshot(path=str(args.out / f"{name}_{theme}_tableau.png"))
                shown = card.locator(".nav button").evaluate_all("(buttons) => buttons.map((b) => b.dataset.go)")
                missing = [p for p in PAGES if p not in shown]
                if missing:
                    problems.append(f"{name}: pages missing from the bar: {missing}")
                for page_id in shown:
                    card.locator(f'.nav button[data-go="{page_id}"]').click()
                    card.locator(f'.nav button[data-go="{page_id}"][aria-selected="true"]').wait_for()
                    text = card.locator(".page").inner_text()
                    if not text.strip() or "ne répondent pas" in text:
                        problems.append(f"{name}/{page_id}: empty or in error: {text[:120]!r}")
                    card.screenshot(path=str(args.out / f"{name}_{theme}_{page_id}.png"))
                if dark and name == "wall":  # the editor of the card, in Home Assistant's own form
                    phase[0] = "editor"
                    page.set_viewport_size({"width": width, "height": 1500})  # the whole editor, down to its last field
                    page.evaluate("""async () => {
                        const holder = document.createElement('div');
                        holder.id = 'editeur';
                        holder.style.cssText = 'position:fixed;inset:20px auto auto 20px;width:520px;padding:16px;z-index:9999;' +
                          'background:var(--card-background-color,#1c1c1c);border-radius:12px';
                        const editor = document.createElement('vigie-card-editor');
                        editor.setConfig({ type: 'custom:vigie-card', spot: 'auto' });
                        editor.hass = document.querySelector('home-assistant').hass;
                        holder.appendChild(editor);
                        // Inside Home Assistant's own tree, where its dialogs open: its pickers take their words from a
                        // context the `home-assistant` element provides (under <body> the entity picker throws).
                        document.querySelector('home-assistant').shadowRoot.appendChild(holder);
                    }""")
                    page.locator("vigie-card-editor ha-form").wait_for(timeout=30_000)
                    try:  # its last field, the weather entity of the home, in Home Assistant's own entity picker
                        page.locator("vigie-card-editor ha-entity-picker").wait_for(timeout=30_000)
                    except PlaywrightTimeout:
                        problems.append(f"{name}/{theme}: the editor does not draw its field of the weather entity")
                    page.wait_for_timeout(1500)
                    page.locator("#editeur").screenshot(path=str(args.out / "wall_sombre_editeur.png"))
                # Home Assistant logs errors of its own in a container without hardware (Bluetooth, USB...): only the
                # card's fail the run, and any uncaught error while only the editor of the card changed the screen; the
                # others are printed.
                ours = [e for e in errors if "vigie" in e.lower() or e.startswith("[editor] page error")]
                problems.extend(f"{name}/{theme}: {error}{near(page, error) if 'page error' in error else ''}" for error in ours)
                for error in errors:
                    if error not in ours:
                        print(f"(not the card) {name}/{theme}: {error}{near(page, error) if 'page error' in error else ''}")
                context.close()
        browser.close()
    print("\n".join(sorted(path.name for path in args.out.glob("*.png"))))
    if problems:
        print("PROBLEMS:\n" + "\n".join(problems), file=sys.stderr)
        return 1
    print("The card loads, shows its five pages and logs no error, at both widths and in both themes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
