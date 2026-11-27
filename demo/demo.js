// The online demo of the Home Assistant card: the card of the integration (`vigie-card.js`, copied next to this page when it is
// published), drawn without Home Assistant. The page plays the part of Home Assistant for it, as the widget page of vigie-web
// does: a `hass` object with the language and the theme, whose `callWS` answers the card's commands `vigie/card/data`,
// `vigie/card/mountain` (a station of the Mountain page), `vigie/card/beach` (a beach of the Sea page) and `vigie/card/spots`
// (the spots near the place) with answers recorded by `tools/record_card_demo.py`
// (`data/<place>.json`). The card's clock starts at the time of that record
// and runs from there, so "now", "the next hours" and "the rain in the hour" read as they did then. Nothing is asked of a
// Vigie instance from this page.

const PLACES = [
  { id: 'biarritz', fr: 'Biarritz · plage', en: 'Biarritz · beach' },
  { id: 'chamonix', fr: 'Chamonix · montagne', en: 'Chamonix · mountains' },
  {
    id: 'tokyo',
    fr: 'Tokyo · monde',
    en: 'Tokyo · world',
    // What the card does outside France, said where a visitor would otherwise miss the weather.
    note: {
      fr: 'Hors de France, l’Accueil montre la météo d’une entité météo de Home Assistant (option « Hors de France : entité météo de la maison ») ; sans elle, comme ici, il montre les alertes, les séismes et la marée. Les pages Détails et Normal ? viennent de Météo-France : la carte les cache ici.',
      en: 'Outside France, the home page shows the weather of a Home Assistant weather entity (the option “Outside France: weather entity of the home”); without one, as here, it shows the warnings, the earthquakes and the tide. The Details and Normal? pages come from Météo-France: the card hides them here.',
    },
  },
];
const PAGES = ['home', 'details', 'alerts', 'sea', 'mountain', 'normal'];
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
      if (message?.type === 'vigie/card/spots') {
        if (current.spots?.length) return current.spots;
        return current.answer.spot ? [current.answer.spot] : [];
      }
      if (message?.type === 'vigie/card/beach') {
        // A beach of the place's Sea page, found by its spot; another spot is not near the place, as the integration says.
        const i = (current.place.beaches || []).indexOf(message.spot);
        if (i < 0 || !current.beaches?.[i]) throw { code: 'not_found' };
        return { language: state.lang, ...current.beaches[i] };
      }
      if (message?.type === 'vigie/card/mountain') {
        // The station the card asks for, found by what it sends: its position and its altitudes.
        const stations = current.place.stations || [];
        const i = stations.findIndex(
          (s) =>
            s.latitude === message.lat &&
            s.longitude === message.lon &&
            s.top_m === message.top_m &&
            (s.base_m ?? null) === (message.base_m ?? null)
        );
        if (i < 0 || !current.mountain?.[i]) throw { code: 'not_found' };
        return { language: state.lang, ...current.mountain[i] };
      }
      throw { code: 'unknown_command' };
    },
  };
}

function configOf(rec) {
  const config = {
    type: 'custom:vigie-card',
    entry_id: rec.answer.entry_id,
    name: rec.place.name,
    pages: PAGES,
    spot: rec.place.spot,
    idle: 'none',
  };
  if (rec.place.beaches?.length) config.beaches = rec.place.beaches;
  if (rec.place.stations?.length) config.stations = rec.place.stations;
  return config;
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
  if (config.beaches) lines.push(`beaches: [${config.beaches.join(', ')}]`);
  if (config.stations) {
    lines.push('stations:');
    for (const s of config.stations) {
      lines.push(`  - name: ${s.name}`, `    latitude: ${s.latitude}`, `    longitude: ${s.longitude}`);
      if (s.base_m != null) lines.push(`    base_m: ${s.base_m}`);
      lines.push(`    top_m: ${s.top_m}`);
    }
  }
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
  const note = PLACES.find((p) => p.id === state.place)?.note;
  const noteBox = document.getElementById('note');
  noteBox.textContent = note ? note[state.lang] : '';
  noteBox.hidden = !note;
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

// The three weights of Roboto the card uses (demo.css), loaded before it is drawn: the card draws its home page again when the
// fonts that were loading as it was put on the page are there, not those it asks for afterwards (its weight 500, on a first
// visit), and a page measured in another font is drawn for that font. A font that does not load leaves the card in the
// machine's own.
const fonts = Promise.all(['400', '500', '700'].map((w) => document.fonts?.load(`${w} 14px Roboto`))).catch((err) =>
  console.warn('Roboto did not load:', err)
);

draw();
import('./vigie-card.js')
  .then(() => customElements.whenDefined('vigie-card'))
  .then(() => fonts)
  .then(() => {
    card = document.createElement('vigie-card');
    frame.append(card);
    return draw({ placeChanged: true });
  })
  .catch((err) => {
    console.error(err);
    document.getElementById('failed').hidden = false;
  });
