// The online demo of the Home Assistant card: the card of the integration (`vigie-card.js`, copied next to this page when it is
// published), drawn without Home Assistant. The page plays the part of Home Assistant for it, as the widget page of vigie-web
// does: a `hass` object with the language and the theme, whose `callWS` answers the card's command `vigie/card/data` with an
// answer recorded by `tools/record_card_demo.py` (`data/<place>.json`). The card's clock starts at the time of that record
// and runs from there, so "now", "the next hours" and "the rain in the hour" read as they did then. Nothing is asked of a
// Vigie instance from this page.

const PLACES = [
  { id: 'biarritz', fr: 'Biarritz · plage', en: 'Biarritz · beach' },
  { id: 'chamonix', fr: 'Chamonix · montagne', en: 'Chamonix · mountains' },
  { id: 'tokyo', fr: 'Tokyo · monde', en: 'Tokyo · world' },
];
const PAGES = ['home', 'details', 'alerts', 'sea', 'normal'];
const CARD_HEIGHT = 760; // the fixed size of the card in a sections view: 12 rows of the grid

// ---- the card's clock: the time of the record, running ----------------------------------------------------------------------
const RealDate = Date;
let shiftMs = 0;
class DemoDate extends RealDate {
  constructor(...args) {
    if (args.length === 0) super(RealDate.now() - shiftMs);
    else super(...args);
  }
  static now() {
    return RealDate.now() - shiftMs;
  }
}
globalThis.Date = DemoDate;

// ---- what the viewer chose (kept for the next visit, when the browser allows it) ---------------------------------------------
const stored = (() => {
  try {
    return JSON.parse(localStorage.getItem('vigie-demo') || '{}');
  } catch {
    return {};
  }
})();
const prefersDark = globalThis.matchMedia?.('(prefers-color-scheme: dark)').matches ?? true;
const state = {
  place: PLACES.some((p) => p.id === stored.place) ? stored.place : 'biarritz',
  theme: stored.theme === 'light' || stored.theme === 'dark' ? stored.theme : prefersDark ? 'dark' : 'light',
  lang: stored.lang === 'fr' || stored.lang === 'en' ? stored.lang : (navigator.language || 'fr').toLowerCase().startsWith('fr') ? 'fr' : 'en',
  width: [360, 480, 720].includes(stored.width) ? stored.width : 480,
};
const keep = () => {
  try {
    localStorage.setItem('vigie-demo', JSON.stringify(state));
  } catch {
    /* private window, blocked storage: the choice is simply not kept */
  }
};

const records = new Map();
async function record(id) {
  if (!records.has(id)) {
    records.set(
      id,
      fetch(`data/${id}.json`).then((r) => {
        if (!r.ok) throw new Error(`data/${id}.json: HTTP ${r.status}`);
        return r.json();
      })
    );
  }
  return records.get(id);
}

/** What `vigie/card/data` answers, in the language of the page (only the warnings' texts depend on it). */
function answerOf(rec, lang) {
  const answer = structuredClone(rec.answer);
  answer.language = lang;
  if (answer.warnings) answer.warnings.vigilances = rec.vigilances?.[lang] ?? answer.warnings.vigilances;
  return answer;
}

// The record the card is shown: `callWS` reads it when the card asks, so a card set to another place reads the new one.
let current = null;

function fakeHass() {
  return {
    language: state.lang,
    locale: { language: state.lang },
    themes: { darkMode: state.theme === 'dark' },
    states: {},
    connection: null,
    async callWS(message) {
      if (!current) throw { code: 'not_found' };
      if (message?.type === 'vigie/card/data') return answerOf(current, state.lang);
      if (message?.type === 'vigie/card/entries') return [{ entry_id: current.answer.entry_id, title: current.place.name }];
      if (message?.type === 'vigie/card/spots') return current.answer.spot ? [current.answer.spot] : [];
      throw { code: 'unknown_command' };
    },
  };
}

function configOf(rec) {
  return {
    type: 'custom:vigie-card',
    entry_id: rec.answer.entry_id,
    name: rec.place.name,
    pages: PAGES,
    spot: rec.place.spot,
    idle: 'none',
  };
}

// ---- the page's own words ---------------------------------------------------------------------------------------------------
function texts() {
  document.documentElement.lang = state.lang;
  document.documentElement.dataset.theme = state.theme;
  for (const el of document.querySelectorAll('[data-fr]')) el.textContent = el.dataset[state.lang];
  for (const group of document.querySelectorAll('[data-label-fr]')) {
    const label = state.lang === 'fr' ? group.dataset.labelFr : group.dataset.labelEn;
    group.querySelector('.label').textContent = label;
    group.setAttribute('aria-label', label);
  }
  document.querySelector('.controls').setAttribute('aria-label', state.lang === 'fr' ? 'Réglages' : 'Settings');
  for (const button of document.querySelectorAll('#places button')) {
    const place = PLACES.find((p) => p.id === button.dataset.value);
    button.textContent = place[state.lang];
  }
}

function pressed() {
  const mark = (id, value) => {
    for (const b of document.querySelectorAll(`#${id} button`)) b.setAttribute('aria-pressed', String(b.dataset.value === String(value)));
  };
  mark('places', state.place);
  mark('themes', state.theme);
  mark('languages', state.lang);
  mark('widths', state.width);
}

const NBSP = ' ';

function recordedLine(rec) {
  // In the time of Paris, where the answers were recorded; French writes the hour "13 h 44".
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat(state.lang === 'fr' ? 'fr-FR' : 'en-GB', {
      timeZone: 'Europe/Paris',
      day: 'numeric',
      month: 'long',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    })
      .formatToParts(new RealDate(rec.recorded_utc * 1000))
      .map((p) => [p.type, p.value])
  );
  document.getElementById('recorded').textContent =
    state.lang === 'fr'
      ? `Réponses enregistrées le ${parts.day} ${parts.month} ${parts.year} à ${Number(parts.hour)}${NBSP}h${NBSP}${parts.minute}, heure de Paris. L’horloge de la carte part de cet instant${NBSP}: rien n’est en direct.`
      : `Answers recorded on ${parts.day} ${parts.month} ${parts.year} at ${parts.hour}:${parts.minute}, Paris time. The card's clock starts at that moment: nothing is live.`;
}

function yaml(config) {
  const lines = [
    'type: custom:vigie-card',
    `name: ${config.name}`,
    `pages: [${config.pages.join(', ')}]`,
    `spot: ${config.spot}`,
    `idle: ${config.idle}`,
  ];
  document.getElementById('yaml').textContent = lines.join('\n');
}

// ---- the card -------------------------------------------------------------------------------------------------------------
const frame = document.getElementById('frame');
let card = null;

async function draw({ placeChanged = false } = {}) {
  texts();
  pressed();
  keep();
  frame.style.setProperty('--card-width', `${state.width}px`);
  frame.style.setProperty('--card-height', `${CARD_HEIGHT}px`);
  let rec;
  try {
    rec = await record(state.place);
  } catch (err) {
    console.error(err);
    document.getElementById('failed').hidden = false;
    return;
  }
  recordedLine(rec);
  const config = configOf(rec);
  yaml(config);
  if (!card) return;
  if (placeChanged || current === null) {
    // A few seconds after the record, as when one opens a dashboard just after Home Assistant read the instance.
    shiftMs = RealDate.now() - (rec.recorded_utc * 1000 + 5_000);
    current = rec;
    // As Home Assistant does: the configuration first, then `hass`; a new configuration makes the card ask for its data.
    card.setConfig(config);
  }
  card.hass = fakeHass();
}

function wire(id, key, parse = (v) => v) {
  document.getElementById(id).addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (!button) return;
    const value = parse(button.dataset.value);
    if (state[key] === value) return;
    state[key] = value;
    draw({ placeChanged: key === 'place' });
  });
}

const placesBox = document.getElementById('places');
for (const place of PLACES) {
  const button = document.createElement('button');
  button.type = 'button';
  button.dataset.value = place.id;
  placesBox.append(button);
}
wire('places', 'place');
wire('themes', 'theme');
wire('languages', 'lang');
wire('widths', 'width', Number);

draw();
import('./vigie-card.js')
  .then(() => customElements.whenDefined('vigie-card'))
  .then(() => {
    card = document.createElement('vigie-card');
    frame.append(card);
    return draw({ placeChanged: true });
  })
  .catch((err) => {
    console.error(err);
    document.getElementById('failed').hidden = false;
  });
