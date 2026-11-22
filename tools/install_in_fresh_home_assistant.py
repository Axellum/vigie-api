"""Install the integration in a Home Assistant that never ran, the way docs/home-assistant.md says, through Home Assistant's
own screens (the `install` job of the Home Assistant workflow).

The job copies `custom_components/vigie` from the files of the commit (`git archive`: what HACS or a copy by hand puts in the
configuration folder, not the work tree) into the configuration folder of a Home Assistant that never ran, starts it in its
official container (the "restart" of the documentation: a new custom integration is only found at start), and runs the fake
instance that replays the answers recorded for Biarritz (``python tools/fake_instance.py --replay tests_ha/fixtures``). This
script then:

1. finishes the onboarding with a throwaway user, the home in Biarritz (what the map of the onboarding sets);
2. follows "Install" and "Configure": Settings, Devices & services, Add integration, searches "Vigie", fills the form with the
   address of the fake instance instead of the public one (the name and the place left as suggested), Submit, Finish;
3. checks that the entities the documentation lists were made, and that none is unavailable;
4. adds the card the way a person does, with no resource to add and no YAML: on an empty dashboard, Edit, Add card, "Vigie"
   in the card picker, Save, Done; then checks that the card shows its first page;
5. fails on any error the integration wrote in Home Assistant's log, and on any error of the card in the browser.

What it does not do: run HACS (it asks for a GitHub account; its part is the copy of the folder), and make the dashboard
(made empty by Home Assistant's API; its card is added through the screens). A capture of every step is kept in --out
(``NN_<step>.png``); on a failure, ``echec.png`` and what the open dialogs said. Standard library and Playwright only; it never
talks to anything but the Home Assistant and the fake instance on this machine.

    python tools/install_in_fresh_home_assistant.py --ha http://127.0.0.1:8123 --instance http://127.0.0.1:8099 --out captures
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

sys.path.insert(0, str(Path(__file__).resolve().parent))
from card_in_home_assistant import PLACE, login, onboard, wait_for, ws  # noqa: E402 - the card job's own helpers

DASHBOARD = "essai-installation"  # no "vigie" in its address, which would pass for the card's
PUBLIC_INSTANCE = "https://vigie.auxfilsdesidees.fr"  # what the form suggests (docs/home-assistant.md, "Configure")
CONNECTED = "() => document.querySelector('home-assistant')?.hass?.connected === true"
# What the documentation lists for one entry with the option "Weather of the place and dashboard card", on by default:
# maximum level, strongest earthquake, rain in the next hour, strongest gust, next high tide; warning active; vigilance and
# earthquake events; the weather of the place.
EXPECTED = {"sensor": 5, "binary_sensor": 1, "event": 2, "weather": 1}
# The dialogs open on the screen, with their text, through the shadow roots of Home Assistant's elements.
OPEN_DIALOGS = r"""() => {
    const found = [];
    const walk = (root) => root.querySelectorAll('*').forEach((el) => {
        const tag = el.tagName.toLowerCase();
        if (/^(dialog-|hui-dialog-|step-flow-)/.test(tag)) {
            found.push(tag + ': ' + (el.shadowRoot || el).textContent.replace(/\s+/g, ' ').trim().slice(0, 500));
        }
        if (el.shadowRoot) walk(el.shadowRoot);
    });
    walk(document);
    return found;
}"""


class Stuck(Exception):
    """A step of the documentation that could not be done."""


class Steps:
    """Numbered captures of the steps, in the order they were done."""

    def __init__(self, page: Page, out: Path) -> None:
        self.page, self.out, self.number = page, out, 0

    def shot(self, what: str) -> None:
        self.number += 1
        self.page.screenshot(path=str(self.out / f"{self.number:02d}_{what}.png"))


def button(page: Page, scope: str, name: str) -> Any:
    """The button named ``name`` (the whole label, any case) inside the element ``scope`` (a dialog: its buttons are in its footer)."""
    return page.locator(scope).get_by_role("button", name=re.compile(rf"^\s*{name}\s*$", re.I)).first


def add_the_integration(page: Page, ha: str, instance: str, steps: Steps) -> list[str]:
    """Settings, Devices & services, Add integration, "Vigie", the form, Submit, Finish."""
    page.goto(f"{ha}/config/integrations/dashboard")
    page.wait_for_function(CONNECTED, timeout=60_000)
    page.get_by_role("button", name=re.compile(r"add integration", re.I)).first.click(timeout=60_000)
    dialog = page.locator("dialog-add-integration")
    try:
        dialog.locator("input").first.fill("Vigie", timeout=30_000)
        page.wait_for_timeout(1000)
        steps.shot("integration_cherchee")
        dialog.get_by_text("Vigie", exact=True).first.click(timeout=30_000)
    except PlaywrightTimeout as err:
        raise Stuck(f'"Vigie" is not found by the search of Add integration ({err.message.splitlines()[0]})') from err
    form = page.locator("step-flow-form")
    # the one address field of the form (a URL text selector; its label is not tied to its input for Playwright)
    address = form.locator('input[type="url"]')
    try:
        address.first.wait_for(timeout=30_000)
    except PlaywrightTimeout as err:
        raise Stuck(f"the form of the integration has no address field ({err.message.splitlines()[0]})") from err
    if address.count() != 1:
        raise Stuck(f"the form of the integration has {address.count()} address fields, one was expected")
    suggested = address.input_value()
    print(f"Suggested by the form: the address {suggested!r}")
    problems = [] if suggested == PUBLIC_INSTANCE else [f"the form suggests {suggested!r}, the documentation says {PUBLIC_INSTANCE}"]
    address.fill(instance)
    page.wait_for_timeout(500)
    steps.shot("formulaire")
    button(page, "dialog-data-entry-flow", "submit").click(timeout=30_000)
    try:
        page.locator("step-flow-create-entry").wait_for(timeout=60_000)
    except PlaywrightTimeout as err:
        raise Stuck("the form did not create the entry") from err
    page.wait_for_timeout(500)
    steps.shot("entree_creee")
    button(page, "dialog-data-entry-flow", "(skip and )?finish").click(timeout=30_000)  # "Skip and finish" while no area is picked
    page.locator("dialog-data-entry-flow").wait_for(state="detached", timeout=30_000)
    return problems


def check_the_entities(page: Page) -> list[str]:
    """The entities of the entry: those the documentation lists, none unavailable. The problems, if any."""
    entries = ws(page, {"type": "config_entries/get", "domain": "vigie"})
    if len(entries) != 1 or entries[0]["state"] != "loaded":
        return [f"one loaded entry was expected: {entries}"]
    entry = entries[0]
    print(f"Entry: {entry['title']!r}, {entry['state']}")
    ids = sorted(e["entity_id"] for e in ws(page, {"type": "config/entity_registry/list"}) if e.get("config_entry_id") == entry["entry_id"])
    script = "(ids) => ids.map((id) => [id, document.querySelector('home-assistant').hass.states[id]?.state ?? null])"
    try:  # the weather routes are read a moment after the entry is loaded
        page.wait_for_function(f"(ids) => ({script})(ids).every(([, s]) => s !== null && s !== 'unavailable')", arg=ids, timeout=60_000)
    except PlaywrightTimeout:
        pass  # said below, entity by entity
    states = dict(page.evaluate(script, ids))
    for entity_id, state in states.items():
        print(f"  {entity_id}: {state}")
    problems = [f"{entity_id} is {state}" for entity_id, state in states.items() if state in (None, "unavailable")]
    found = dict(Counter(entity_id.split(".")[0] for entity_id in ids))
    if found != EXPECTED:
        problems.append(f"entities by platform: {found}, the documentation lists {EXPECTED}")
    return problems


def add_the_card(page: Page, ha: str, steps: Steps) -> list[str]:
    """On an empty dashboard: Edit, Add card, "Vigie" in the card picker, Save, Done; the card shows its first page."""
    ws(page, {"type": "lovelace/dashboards/create", "url_path": DASHBOARD, "title": "Essai", "icon": "mdi:test-tube",
              "show_in_sidebar": True, "require_admin": False})
    ws(page, {"type": "lovelace/config/save", "url_path": DASHBOARD, "config": {"title": "Essai", "views": [{"title": "Essai", "path": "essai", "cards": []}]}})
    page.goto(f"{ha}/{DASHBOARD}/essai?edit=1")
    page.wait_for_function(CONNECTED, timeout=60_000)
    try:
        page.get_by_role("button", name=re.compile(r"add card", re.I)).first.click(timeout=60_000)
        picker = page.locator("hui-card-picker")
        picker.locator("input").first.fill("Vigie", timeout=30_000)
        page.wait_for_timeout(1500)
        steps.shot("carte_cherchee")
        picker.get_by_text("Vigie", exact=True).first.click(timeout=30_000)
    except PlaywrightTimeout as err:
        raise Stuck(f'"Vigie" is not offered by the card picker ({err.message.splitlines()[0]})') from err
    try:
        page.locator("hui-dialog-edit-card vigie-card .page").first.wait_for(timeout=60_000)
    except PlaywrightTimeout as err:
        raise Stuck("the editor of the card shows no preview of the card") from err
    page.wait_for_timeout(1000)
    steps.shot("carte_apercu")
    button(page, "hui-dialog-edit-card", "save").click(timeout=30_000)
    page.locator("hui-dialog-edit-card").wait_for(state="detached", timeout=30_000)
    page.get_by_role("button", name=re.compile(r"^\s*done\s*$", re.I)).first.click(timeout=30_000)
    try:
        page.locator("vigie-card .page").first.wait_for(timeout=60_000)
    except PlaywrightTimeout as err:
        raise Stuck("the card saved on the dashboard shows no page") from err
    page.wait_for_timeout(1500)
    steps.shot("tableau_de_bord")
    saved = ws(page, {"type": "lovelace/config", "url_path": DASHBOARD})["views"][0]["cards"]
    print(f"Saved by the screens: {saved}")
    return [] if saved == [{"type": "custom:vigie-card", "spot": "auto"}] else [f"the dashboard holds {saved}, the card's own suggestion was expected"]


def errors_of_the_integration(page: Page) -> list[str]:
    """What the integration wrote in Home Assistant's log: its errors are problems, its warnings are printed."""
    problems = []
    for item in ws(page, {"type": "system_log/list"}):
        if "vigie" not in item.get("name", ""):
            continue
        line = f"{item['level']} {item['name']}: {' | '.join(item.get('message') or [])}"
        if item["level"] in ("ERROR", "CRITICAL"):
            problems.append(f"Home Assistant's log: {line}")
        else:
            print(f"(log) {line}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--ha", default="http://127.0.0.1:8123")
    parser.add_argument("--instance", default="http://127.0.0.1:8099")
    parser.add_argument("--out", type=Path, default=Path("captures"))
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    wait_for(f"{args.ha}/api/onboarding")  # answers without a user while the onboarding is not done
    tokens = onboard(args.ha)
    problems: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US", timezone_id="Europe/Paris")
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda err: errors.append(f"page error: {err.message} {err.stack or ''}"))
        page.on("console", lambda msg: errors.append(f"console: {msg.text}") if msg.type == "error" else None)
        login(page, args.ha, tokens, dark=False)
        steps = Steps(page, args.out)
        try:
            page.goto(f"{args.ha}/profile")
            page.wait_for_function(CONNECTED, timeout=60_000)
            ws(page, {"type": "config/core/update", **PLACE, "time_zone": "Europe/Paris", "country": "FR", "unit_system": "metric",
                      "currency": "EUR", "location_name": "Biarritz"})
            problems += add_the_integration(page, args.ha, args.instance, steps)
            problems += check_the_entities(page)
            page.goto(f"{args.ha}/config/integrations/integration/vigie")
            page.wait_for_function(CONNECTED, timeout=60_000)
            page.wait_for_timeout(1500)
            steps.shot("integration_installee")
            problems += add_the_card(page, args.ha, steps)
            problems += errors_of_the_integration(page)
        except (Stuck, PlaywrightTimeout) as err:
            page.screenshot(path=str(args.out / "echec.png"), full_page=True)
            dialogs = page.evaluate(OPEN_DIALOGS)
            problems.append(f"{err if isinstance(err, Stuck) else err.message.splitlines()[0]}\n  url: {page.url}\n  " + "\n  ".join(dialogs or ["(no dialog open)"]))
        ours = [error for error in errors if "vigie" in error.lower()]
        problems += [f"browser: {error}" for error in ours]
        for error in errors:
            if error not in ours:
                print(f"(not the card) browser: {error[:300]}")
        context.close()
        browser.close()
    if problems:
        print("PROBLEMS:\n" + "\n".join(problems), file=sys.stderr)
        return 1
    print("Installed as the documentation says: the integration through its form, its entities, the card through the card picker; no error.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
