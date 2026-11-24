// The card in Node, for tests_ha/test_card.py: a browser without a browser (just what the card touches), then the card itself,
// whose module defines its elements. Arguments: the card's file, a recorded answer of `vigie/card/data`. Prints what each case
// drew, as one line of JSON.
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

class El {
  constructor() { this.style = {}; this.innerHTML = ''; }
  addEventListener() {}
  querySelector() { return this._child || (this._child = new El()); }
  querySelectorAll() { return []; }
}
globalThis.HTMLElement = class extends El {
  attachShadow() { this.shadowRoot = new El(); return this.shadowRoot; }
  getBoundingClientRect() { return { width: 420 }; }
  get isConnected() { return false; }
};
const defined = {};
globalThis.customElements = { get: (tag) => defined[tag], define: (tag, cls) => { defined[tag] = cls; } };
globalThis.window = globalThis;
globalThis.document = { createElement: () => new El() };
const stored = new Map();
globalThis.localStorage = { getItem: (k) => (stored.has(k) ? stored.get(k) : null), setItem: (k, v) => stored.set(k, String(v)) };

const [card, data] = process.argv.slice(2);
const answer = JSON.parse(await readFile(data, 'utf8'));
await import(pathToFileURL(card).href);
const Card = customElements.get('vigie-card');
const drawn = (c) => c.shadowRoot.querySelector('.root').innerHTML;
const hass = (reply) => ({ language: 'fr', locale: { language: 'fr' }, themes: { darkMode: true }, states: {}, connection: null, callWS: reply });
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const out = {};

// 1. A first card: "loading" until Home Assistant answers.
const first = new Card();
first.setConfig({ spot: 'auto' });
out.first_before = drawn(first);
let asked = 0;
first.hass = hass(async () => { asked += 1; return answer; });
await tick();
out.first_after = drawn(first);
out.asked = asked;

// 2. A second card of the same request, in the same page: drawn at once, before it has Home Assistant.
const second = new Card();
second.setConfig({ spot: 'auto' });
out.second_before = drawn(second);

// 3. Kept in the browser's storage too, for the next visit.
out.stored_age_ms = Date.now() - JSON.parse(stored.get('vigie-card:last:|auto')).at;

// 4. Another spot: not the first one's answer.
const other = new Card();
other.setConfig({ spot: 'none' });
out.other_spot_before = drawn(other);

// 5. A request that fails shows its error, not the answer kept.
const failing = new Card();
failing.setConfig({ spot: 'auto' });
failing.hass = hass(async () => { throw { code: 'not_found' }; });
await tick();
out.failed_after = drawn(failing);

// 6. An answer older than half an hour is not drawn.
stored.set('vigie-card:last:old|auto', JSON.stringify({ at: Date.now() - 31 * 60 * 1000, data: answer }));
const old = new Card();
old.setConfig({ spot: 'auto', entry_id: 'old' });
out.old_before = drawn(old);

console.log(JSON.stringify(out));
