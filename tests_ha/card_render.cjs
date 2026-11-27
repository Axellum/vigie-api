// Draws pages of the dashboard card outside a browser, for tests_ha/test_card_season.py and test_card_rain.py: the card file runs in a bare context
// with the few names of a browser it needs when it loads, and the pages come out as strings of HTML.
// Usage: node card_render.cjs <vigie-card.js>, the input on stdin: {lang, width, now_ms, data, config}, `config` the options
// of the card (optional). Also gives the icon the card chooses for each WMO weather code, by day and by night.
'use strict';
const fs = require('fs');
const vm = require('vm');

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = { console, HTMLElement: class {}, customElements: { get: () => undefined, define: () => {} } };
context.window = context;
vm.createContext(context);
vm.runInContext(`Date.now = () => ${Number(input.now_ms)};`, context);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context, { filename: 'vigie-card.js' });
context.__input = input;
const out = vm.runInContext(`(() => {
  const i = __input;
  const c = makeContext(i.lang, true, i.width, i.data, { type: 'custom:vigie-card', _pages: ['home', 'details', 'alerts', 'sea', 'normal'], ...(i.config || {}) });
  return JSON.stringify({
    items: seasonItems(c).map((it) => ({ id: it.id, tone: it.tone })),
    nowcast: nowcastFirst(c),
    home: pageHome(c),
    alerts: pageAlerts(c),
    codes: Object.fromEntries(Array.from({ length: 100 }, (_, k) => [k, [atNight(wmoKind(k), true), atNight(wmoKind(k), false)]])),
  });
})()`, context);
process.stdout.write(out);
