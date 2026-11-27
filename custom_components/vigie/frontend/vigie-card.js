// The Vigie card for Home Assistant (ADR 0042 of the API): the weather, the warnings, the sea and the normals of one place, on
// pages one swipes or picks in the bar at the top. It is shipped inside the integration and registered by it (a dashboard resource
// where it can, ADR 0044), and it asks its data of Home Assistant (WebSocket command `vigie/card/data`), never of the instance; the
// last answer is kept, so the card draws at once while it asks again. A value the data does not give is not drawn:
// no empty box, no guess. Colours: the "Sombre uni" and "Clair" themes of the Vigie family, chosen by the dark mode of Home
// Assistant (the background of the card stays the dashboard's own). Weather icons: the icon set of the Vigie family (ADR 0045), in
// the style chosen in the editor (`icon_style`), coloured by the same themes.

const TAG = 'vigie-card';
const EDITOR_TAG = 'vigie-card-editor';
const PAGE_IDS = ['home', 'details', 'alerts', 'sea', 'normal'];
const REFRESH_MS = 5 * 60 * 1000; // the integration reads the instance every 10 minutes
const IDLE_RETURN_MS = 60 * 1000;
const CYCLE_MS = 20 * 1000;
const WIDE_PX = 640;

// ------------------------------------------------------------------------------------------------------------ last answer
// The last answer of each request (an entry and a spot), kept so the card draws at once, with the other cards, while it asks
// again: in memory for the open page, in the browser's storage for the next visit. Never older than LAST_MAX_MS, and a request
// that fails shows its error as before, not the answer kept.
const LAST_MAX_MS = 30 * 60 * 1000;
const LAST_PREFIX = 'vigie-card:last:';
const lastAnswers = new Map();

function lastKey(config) {
  return `${config.entry_id || ''}|${config.spot === 'none' ? '' : config.spot || 'auto'}`;
}

function lastAnswer(key) {
  let kept = lastAnswers.get(key);
  if (!kept) {
    try { kept = JSON.parse(window.localStorage.getItem(LAST_PREFIX + key) || 'null'); } catch (err) { kept = null; }
  }
  return kept && Date.now() - kept.at < LAST_MAX_MS ? kept.data : null;
}

function keepAnswer(key, data) {
  const kept = { at: Date.now(), data };
  lastAnswers.set(key, kept);
  try { window.localStorage.setItem(LAST_PREFIX + key, JSON.stringify(kept)); } catch (err) { /* full or refused: the page keeps it */ }
}

// ------------------------------------------------------------------------------------------------------------------ colours
const PALETTES = {
  dark: {
    texte: '#F4F7FA', t2: '#CBD5DF', t3: '#A3B1C0', t4: '#7D8B9B', filet: '#2C3947', accent: '#3DDC97', sur: '#10151C',
    vert: '#6FE3B0', jaune: '#FFD25C', orange: '#FFA24D', rouge: '#FF6B5E', inconnu: '#A3B1C0',
    vent: '#7FD1FF', pluie: '#58B4FF', pluieFaible: '#7FD1FF', pluieForte: '#3A8CF0',
    ramp: ['#7FB2FF', '#5CD6FF', '#7BE495', '#FFC94A', '#FF6B4A'],
    lueurNuit: '#6F8FE0', lueurCouvert: '#A9B6C4', lueurSoleil: '#FFC94A', lueurA: 1, creux: '#3A4654',
    icon: { soleil: '#FFC83D', lune: '#EFE9CF', nuage: '#E3EAF2', nuageSombre: '#8F9DB2', eau: '#58B4FF', neige: '#FFFFFF', eclair: '#FFD93D', brume: '#AAB6C4' },
  },
  light: {
    texte: '#12171C', t2: '#37424E', t3: '#4F5C6A', t4: '#5F6B78', filet: '#D5DBE2', accent: '#0E8F5F', sur: '#FFFFFF',
    vert: '#1B8A5A', jaune: '#8A6400', orange: '#B45309', rouge: '#C22F2F', inconnu: '#4F5C6A',
    vent: '#0F78B0', pluie: '#1E78D6', pluieFaible: '#4A9BE8', pluieForte: '#155DB0',
    ramp: ['#2F6FDB', '#0A88B8', '#2E9E55', '#C77A00', '#D0391E'],
    lueurNuit: '#4A68C4', lueurCouvert: '#8C9AAA', lueurSoleil: '#F0B400', lueurA: 0.8, creux: '#C9D1DA',
    icon: { soleil: '#F2A900', lune: '#B9A642', nuage: '#B8C3CF', nuageSombre: '#7D8B9B', eau: '#1E78D6', neige: '#7FA6CC', eclair: '#E0A800', brume: '#8C99A8' },
  },
};
const LEVEL_COLOUR = { yellow: 'jaune', orange: 'orange', red: 'rouge' };
const LEVEL_RANK = { yellow: 1, orange: 2, red: 3 };
const VERDICT_COLOUR = { good: 'vert', possible: 'jaune', poor: 'rouge' };

// --------------------------------------------------------------------------------------------------------------------- words
const TEXT = {
  fr: {
    page_home: 'Accueil', page_details: 'Détails', page_alerts: 'Alertes', page_sea: 'Mer', page_normal: 'Normal ?',
    loading: 'Chargement…', not_found: 'Aucune entrée Vigie chargée dans Home Assistant.',
    weather_off: 'L’option « Météo du lieu et carte du tableau de bord » de cette entrée est décochée.',
    error: 'Les données de Vigie ne répondent pas pour le moment.',
    updated: 'Mis à jour à {t}', feels: 'ressenti {t}°', gusts: 'rafales', kmh: 'km/h',
    warning: 'Vigilance {level}', level_yellow: 'jaune', level_orange: 'orange', level_red: 'rouge',
    rain_in: 'Pluie dans {n}&nbsp;min', rain_now: 'Il pleut', rain_none: 'Pas de pluie dans l’heure',
    until: 'jusqu’à {t}', from: 'dès {t}',
    no_warning: 'Aucune vigilance en cours', warnings_unknown: 'Vigilances inconnues', dry: 'sec',
    data: 'Données', relayed: 'relayées par Vigie', warning_of: '{p} {level}',
    gusts_abbr: 'raf.', gust_peak: 'Rafales {v} vers {t}', air: 'Air {w}', uv: 'UV {v}',
    aqi_word: ['bon', 'assez bon', 'moyen', 'dégradé', 'mauvais', 'très mauvais'],
    snow_in: 'Neige dans {n}&nbsp;min', snow_now: 'Il neige', int_light: 'faible', int_moderate: 'modérée', int_heavy: 'forte',
    then: '{a}, puis {b}', next_rain: 'Prochaine pluie {d}vers {t}', dry_until: 'Sec jusqu’à {d}{t}',
    hours_by: '24&nbsp;h · par {n}&nbsp;h', hours_each: '24&nbsp;h · heure par heure', night: 'nuit', sun: 'soleil',
    days_title: '{n} jours · max et min', days_legend: 'pluie · vent', days_legend_gusts: 'pluie · vent / rafales',
    days_legend_sun: 'soleil · pluie · vent', days_legend_sun_gusts: 'soleil · pluie · vent / rafales', sun_h: '{n} h', sun_min: '{n} min', today_short: 'Auj.',
    rain_until: 'La pluie d’ici {t}', noon: 'midi', dry_word: 'Sec', lull: 'Accalmie', light_kind: '{k} faible',
    in_hour: 'Dans l’heure · toutes les {n}&nbsp;min', now_short: 'maint.', radar_piaf: 'radar PIAF',
    quarter_until: 'Jusqu’à {t} · par quart d’heure', gusts_unit: 'rafales km/h', peak_at: '{v} à {t}',
    h48: '{n}&nbsp;h · heure par heure', h48_legend: 'mm · chance de pluie · vent', h48_legend_nop: 'mm · vent', chance: 'chance',
    hail: 'Grêle', hail_none: 'non', hail_small_hail: 'petite grêle', hail_hail: 'grêle',
    visibility: 'Visibilité', vis_none: 'bonne', vis_reduced: 'réduite', vis_very_reduced: 'très réduite',
    chances: 'Chances par {h}&nbsp;h', chances_src: '{n} scénarios PE-AROME · fin du créneau', chances_src_plain: 'PE-AROME · fin du créneau',
    pe_rain: 'Pluie ≥&nbsp;1&nbsp;mm', pe_gust56: 'Rafales ≥&nbsp;56', pe_gust93: 'Rafales ≥&nbsp;93', pe_storm: 'Orage', pe_snow: 'Neige ≥&nbsp;1&nbsp;mm', pe_hail: 'Grêle',
    kind_rain: 'Pluie', kind_drizzle: 'Bruine', kind_freezing: 'Pluie verglaçante', kind_snow: 'Neige', kind_wet_snow: 'Neige mouillée',
    kind_rain_snow: 'Pluie et neige', kind_ice_pellets: 'Granules de glace', kind_snow_pellets: 'Neige roulée', kind_hail: 'Grêle',
    kind_unknown: 'Précipitations',
    read_at: 'lue à {t}', bulletin: 'Le bulletin et les conseils de {issuer}', bulletin_plain: 'Le bulletin officiel',
    floods: 'Crues', no_river: 'Aucun tronçon de rivière en vigilance près du lieu.',
    no_coverage: 'Aucune source d’alertes officielles relayée par Vigie ne couvre ce lieu.',
    nothing: 'Aucune vigilance en cours ni annoncée.', official_text: 'texte officiel tel quel',
    quakes: 'Séismes proches', quake: 'Séisme M&nbsp;{m} à {km}&nbsp;km', quake_dir: 'Séisme M&nbsp;{m} à {km}&nbsp;km {dir}',
    nothing_to_report: 'Rien à signaler', not_relayed: 'Alertes officielles non relayées ici',
    yesterday: 'hier', today_word: 'aujourd’hui',
    sea_rising: 'La mer monte', sea_falling: 'La mer descend', tide: 'Marée', coef: 'coef. {c} (estimé)',
    high_short: 'PM', low_short: 'BM', waves: 'Vagues', water: 'eau',
    sea_CALM: 'calme', sea_SMOOTH: 'belle', sea_MODERATE: 'agitée', sea_ROUGH: 'forte', sea_VERY_ROUGH: 'très forte', sea_HIGH: 'grosse', sea_PHENOMENAL: 'énorme',
    spot_chosen: 'spot choisi', spot_nearest: 'spot le plus proche, à {km}&nbsp;km',
    act_surf: 'Surf', act_kite: 'Kite / wing', act_boat: 'Bateau', act_shore_fishing: 'Pêche à pied',
    verdict_good: 'Bon', verdict_possible: 'Possible', verdict_poor: 'Déconseillé', no_verdict: 'Pas de verdict', cannot_judge: 'Ne peut pas être jugé ici',
    rules_note: 'Règles de Vigie, pas pour la sécurité ni la navigation',
    tide_note: 'Marée du large calculée par Vigie, pas pour la navigation ; dans une baie, la marée réelle peut arriver plus d’une heure après.',
    milder: 'Plus doux que la normale', cooler: 'Plus frais que la normale', near_normal: 'Proche des normales',
    gust_near_record: 'Rafales proches du record de {month}', gust_record: 'Rafales au niveau du record de {month}',
    today_vs: 'Aujourd’hui, comparé à {month}', station_at: 'station à {km}&nbsp;km', maximum: 'Maximum', minimum: 'Minimum',
    normal_lbl: 'normale {v}°', gusts_kmh: 'Rafales, en km/h', measured_lbl: 'mesuré {v}', forecast_lbl: 'prévu {v}', record_lbl: 'record {v} ({y})',
    forecast_src: 'prévu : {src}', measured_wind: 'Vent mesuré', ago: 'il y a {n}&nbsp;min', station: 'Station {n}',
    normal_foot: 'Données : Météo-France (fiches climatologiques, observations) · comparaison calculée par Vigie',
    weather_title: 'Météo', home_entity: 'entité météo de la maison, pas Vigie',
    season: 'De saison', tomorrow: 'demain', day_after: 'après-demain',
    aval: 'Avalanches', aval_word: ['', 'faible', 'limité', 'marqué', 'fort', 'très fort'], aval_pill: 'Avalanches · risque {l} ({w})',
    aval_sector: 'Secteur : {n}', aval_until: 'Bulletin valable jusqu’à {t}', aval_note: 'Risque du bulletin de montagne (BRA), distinct de la vigilance',
    aval_link: 'Le bulletin d’avalanche de {issuer}',
    problem_new_snow: 'neige fraîche', problem_wind_slab: 'neige soufflée', problem_persistent_weak_layers: 'couches fragiles persistantes',
    problem_wet_snow: 'neige mouillée', problem_gliding_snow: 'avalanches de glissement', problem_cornices: 'corniches',
    problem_favourable_situation: 'situation favorable', problem_no_distinct_avalanche_problem: 'pas de problème avalancheux marqué',
    drought: 'Sécheresse', drought_vigilance: 'vigilance', drought_alerte: 'alerte', drought_alerte_renforcee: 'alerte renforcée', drought_crise: 'crise',
    restrictions: 'restrictions d’eau', restrictions_long: 'Des restrictions d’usage de l’eau sont en vigueur.',
    water_AEP: 'Eau potable', water_SUP: 'Eaux de surface', water_SOU: 'Eaux souterraines', until_date: 'jusqu’au {d}',
    drought_link: 'Les restrictions sur VigiEau',
    pollen: 'Pollens', pollen_pill: 'Pollens · {s}', pollen_det: 'jusqu’à {p}&nbsp;grains/m³',
    pollen_note: 'Pic du jour en grains par m³ d’air, prévision du modèle européen CAMS, pas un comptage.',
    species_alder: 'aulne', species_birch: 'bouleau', species_grass: 'graminées', species_mugwort: 'armoise', species_olive: 'olivier',
    species_ragweed: 'ambroisie',
    fire: 'Feux de forêt', fire_word: ['', 'faible', 'modéré', 'élevé', 'très élevé'], fire_pill: 'Feux de forêt · danger {w}',
    fire_note: 'Météo des forêts : un danger pour tout le département, publié de juin à septembre.', dept: 'Département : {n}',
    dir_from: ['du nord', 'du nord-est', 'd’est', 'du sud-est', 'du sud', 'du sud-ouest', 'd’ouest', 'du nord-ouest'],
    dir_to: ['au nord', 'au nord-est', 'à l’est', 'au sud-est', 'au sud', 'au sud-ouest', 'à l’ouest', 'au nord-ouest'],
  },
  en: {
    page_home: 'Home', page_details: 'Details', page_alerts: 'Alerts', page_sea: 'Sea', page_normal: 'Normal?',
    loading: 'Loading…', not_found: 'No Vigie entry is loaded in Home Assistant.',
    weather_off: 'The option “Weather of the place and dashboard card” of this entry is unticked.',
    error: 'The data of Vigie do not answer for now.',
    updated: 'Updated at {t}', feels: 'feels like {t}°', gusts: 'gusts', kmh: 'km/h',
    warning: '{level} warning', level_yellow: 'Yellow', level_orange: 'Orange', level_red: 'Red',
    rain_in: 'Rain in {n}&nbsp;min', rain_now: 'Raining', rain_none: 'No rain in the next hour',
    until: 'until {t}', from: 'from {t}',
    no_warning: 'No warning in force', warnings_unknown: 'Warnings unknown', dry: 'dry',
    data: 'Data', relayed: 'relayed by Vigie', warning_of: '{level} {p}',
    gusts_abbr: 'gusts', gust_peak: 'Gusts {v} around {t}', air: 'Air {w}', uv: 'UV {v}',
    aqi_word: ['good', 'fair', 'moderate', 'poor', 'very poor', 'extremely poor'],
    snow_in: 'Snow in {n}&nbsp;min', snow_now: 'Snowing', int_light: 'light', int_moderate: 'moderate', int_heavy: 'heavy',
    then: '{a}, then {b}', next_rain: 'Next rain {d}around {t}', dry_until: 'Dry until {d}{t}',
    hours_by: '24&nbsp;h · every {n}&nbsp;h', hours_each: '24&nbsp;h · hour by hour', night: 'night', sun: 'sun',
    days_title: '{n} days · high and low', days_legend: 'rain · wind', days_legend_gusts: 'rain · wind / gusts',
    days_legend_sun: 'sun · rain · wind', days_legend_sun_gusts: 'sun · rain · wind / gusts', sun_h: '{n} h', sun_min: '{n} min', today_short: 'Today',
    rain_until: 'Rain until {t}', noon: 'noon', dry_word: 'Dry', lull: 'Lull', light_kind: 'Light {k}',
    in_hour: 'Within the hour · every {n}&nbsp;min', now_short: 'now', radar_piaf: 'PIAF radar',
    quarter_until: 'Until {t} · by quarter hour', gusts_unit: 'gusts km/h', peak_at: '{v} at {t}',
    h48: '{n}&nbsp;h · hour by hour', h48_legend: 'mm · chance of rain · wind', h48_legend_nop: 'mm · wind', chance: 'chance',
    hail: 'Hail', hail_none: 'no', hail_small_hail: 'small hail', hail_hail: 'hail',
    visibility: 'Visibility', vis_none: 'good', vis_reduced: 'reduced', vis_very_reduced: 'very reduced',
    chances: 'Chances per {h}&nbsp;h', chances_src: '{n} PE-AROME scenarios · end of the window', chances_src_plain: 'PE-AROME · end of the window',
    pe_rain: 'Rain ≥&nbsp;1&nbsp;mm', pe_gust56: 'Gusts ≥&nbsp;56', pe_gust93: 'Gusts ≥&nbsp;93', pe_storm: 'Thunderstorm', pe_snow: 'Snow ≥&nbsp;1&nbsp;mm', pe_hail: 'Hail',
    kind_rain: 'Rain', kind_drizzle: 'Drizzle', kind_freezing: 'Freezing rain', kind_snow: 'Snow', kind_wet_snow: 'Wet snow',
    kind_rain_snow: 'Rain and snow', kind_ice_pellets: 'Ice pellets', kind_snow_pellets: 'Snow pellets', kind_hail: 'Hail',
    kind_unknown: 'Precipitation',
    read_at: 'read at {t}', bulletin: 'The bulletin and advice of {issuer}', bulletin_plain: 'The official bulletin',
    floods: 'Floods', no_river: 'No river section under warning near the place.',
    no_coverage: 'No official warning source relayed by Vigie covers this place.',
    nothing: 'No warning in force or announced.', official_text: 'official text as issued',
    quakes: 'Earthquakes nearby', quake: 'Earthquake M&nbsp;{m}, {km}&nbsp;km away', quake_dir: 'Earthquake M&nbsp;{m}, {km}&nbsp;km {dir}',
    nothing_to_report: 'Nothing to report', not_relayed: 'Official warnings not relayed here',
    yesterday: 'yesterday', today_word: 'today',
    sea_rising: 'The tide is rising', sea_falling: 'The tide is falling', tide: 'Tide', coef: 'coef. {c} (estimated)',
    high_short: 'HW', low_short: 'LW', waves: 'Waves', water: 'water',
    sea_CALM: 'calm', sea_SMOOTH: 'smooth', sea_MODERATE: 'moderate', sea_ROUGH: 'rough', sea_VERY_ROUGH: 'very rough', sea_HIGH: 'high', sea_PHENOMENAL: 'phenomenal',
    spot_chosen: 'chosen spot', spot_nearest: 'nearest spot, {km}&nbsp;km away',
    act_surf: 'Surf', act_kite: 'Kite / wing', act_boat: 'Boat', act_shore_fishing: 'Shore fishing',
    verdict_good: 'Good', verdict_possible: 'Possible', verdict_poor: 'Not advised', no_verdict: 'No verdict', cannot_judge: 'Cannot be judged here',
    rules_note: 'Rules of Vigie, not for safety nor navigation',
    tide_note: 'Open-sea tide computed by Vigie, not for navigation; inside a bay the real tide can come an hour or more later.',
    milder: 'Milder than normal', cooler: 'Cooler than normal', near_normal: 'Close to normal',
    gust_near_record: 'Gusts close to the {month} record', gust_record: 'Gusts at the {month} record',
    today_vs: 'Today, compared with {month}', station_at: 'station {km}&nbsp;km away', maximum: 'Maximum', minimum: 'Minimum',
    normal_lbl: 'normal {v}°', gusts_kmh: 'Gusts, in km/h', measured_lbl: 'measured {v}', forecast_lbl: 'forecast {v}', record_lbl: 'record {v} ({y})',
    forecast_src: 'forecast: {src}', measured_wind: 'Measured wind', ago: '{n}&nbsp;min ago', station: 'Station {n}',
    normal_foot: 'Data: Météo-France (climate sheets, observations) · comparison computed by Vigie',
    weather_title: 'Weather', home_entity: 'weather entity of the home, not Vigie',
    season: 'Seasonal', tomorrow: 'tomorrow', day_after: 'the day after',
    aval: 'Avalanches', aval_word: ['', 'low', 'moderate', 'considerable', 'high', 'very high'], aval_pill: 'Avalanches · danger {l} ({w})',
    aval_sector: 'Area: {n}', aval_until: 'Bulletin valid until {t}', aval_note: 'Danger of the mountain bulletin (BRA), not the warning',
    aval_link: 'The avalanche bulletin of {issuer}',
    problem_new_snow: 'new snow', problem_wind_slab: 'wind slab', problem_persistent_weak_layers: 'persistent weak layers',
    problem_wet_snow: 'wet snow', problem_gliding_snow: 'gliding snow', problem_cornices: 'cornices',
    problem_favourable_situation: 'favourable situation', problem_no_distinct_avalanche_problem: 'no distinct avalanche problem',
    drought: 'Drought', drought_vigilance: 'watch', drought_alerte: 'alert', drought_alerte_renforcee: 'reinforced alert', drought_crise: 'crisis',
    restrictions: 'water restrictions', restrictions_long: 'Restrictions on the use of water are in force.',
    water_AEP: 'Drinking water', water_SUP: 'Surface water', water_SOU: 'Groundwater', until_date: 'until {d}',
    drought_link: 'The restrictions on VigiEau',
    pollen: 'Pollen', pollen_pill: 'Pollen · {s}', pollen_det: 'up to {p}&nbsp;grains/m³',
    pollen_note: 'Peak of the day in grains per m³ of air, a forecast of the European CAMS model, not a count.',
    species_alder: 'alder', species_birch: 'birch', species_grass: 'grass', species_mugwort: 'mugwort', species_olive: 'olive',
    species_ragweed: 'ragweed',
    fire: 'Forest fires', fire_word: ['', 'low', 'moderate', 'high', 'very high'], fire_pill: 'Forest fires · {w} danger',
    fire_note: 'Météo des forêts: a danger for the whole department, published from June to September.', dept: 'Department: {n}',
    dir_from: ['from the north', 'from the north-east', 'from the east', 'from the south-east', 'from the south', 'from the south-west', 'from the west', 'from the north-west'],
    dir_to: ['to the north', 'to the north-east', 'to the east', 'to the south-east', 'to the south', 'to the south-west', 'to the west', 'to the north-west'],
  },
};
const PHENOMENON = {
  fr: { wind: 'Vent', rain: 'Pluie', thunderstorm: 'Orages', 'snow-ice': 'Neige-verglas', heat: 'Canicule', cold: 'Grand froid', flood: 'Crues',
    avalanche: 'Avalanches', coastal: 'Vagues-submersion', fire: 'Feux de forêt', fog: 'Brouillard', other: 'Autre phénomène' },
  en: { wind: 'Wind', rain: 'Rain', thunderstorm: 'Thunderstorms', 'snow-ice': 'Snow and ice', heat: 'Heat', cold: 'Cold', flood: 'Floods',
    avalanche: 'Avalanches', coastal: 'Waves and flooding', fire: 'Forest fires', fog: 'Fog', other: 'Other phenomenon' },
};
const PHENOMENON_GLYPH = { wind: 'vent', rain: 'parapluie', thunderstorm: 'eclair', 'snow-ice': 'flocon', heat: 'thermo', cold: 'thermo', flood: 'goutte',
  avalanche: 'montagne', coastal: 'vagues', fire: 'flamme', fog: 'brume', other: 'danger' };
// WMO weather codes (the API's) and Home Assistant's conditions (an entity of the home), in words.
const WMO = {
  fr: { 0: 'Ciel clair', 1: 'Peu nuageux', 2: 'Éclaircies', 3: 'Couvert', 45: 'Brouillard', 48: 'Brouillard givrant', 51: 'Bruine faible', 53: 'Bruine',
    55: 'Bruine forte', 56: 'Bruine verglaçante', 57: 'Bruine verglaçante', 61: 'Pluie faible', 63: 'Pluie', 65: 'Forte pluie', 66: 'Pluie verglaçante',
    67: 'Pluie verglaçante', 71: 'Neige faible', 73: 'Neige', 75: 'Forte neige', 77: 'Grains de neige', 80: 'Averses faibles', 81: 'Averses',
    82: 'Fortes averses', 85: 'Averses de neige', 86: 'Fortes averses de neige', 95: 'Orage', 96: 'Orage avec grêle', 99: 'Orage avec grêle' },
  en: { 0: 'Clear sky', 1: 'Mainly clear', 2: 'Partly cloudy', 3: 'Overcast', 45: 'Fog', 48: 'Freezing fog', 51: 'Light drizzle', 53: 'Drizzle',
    55: 'Dense drizzle', 56: 'Freezing drizzle', 57: 'Freezing drizzle', 61: 'Light rain', 63: 'Rain', 65: 'Heavy rain', 66: 'Freezing rain',
    67: 'Freezing rain', 71: 'Light snow', 73: 'Snow', 75: 'Heavy snow', 77: 'Snow grains', 80: 'Light showers', 81: 'Showers',
    82: 'Heavy showers', 85: 'Snow showers', 86: 'Heavy snow showers', 95: 'Thunderstorm', 96: 'Thunderstorm with hail', 99: 'Thunderstorm with hail' },
};
const CONDITION = {
  fr: { 'clear-night': 'Nuit claire', cloudy: 'Couvert', exceptional: 'Exceptionnel', fog: 'Brouillard', hail: 'Grêle', lightning: 'Orage',
    'lightning-rainy': 'Orage et pluie', partlycloudy: 'Éclaircies', pouring: 'Forte pluie', rainy: 'Pluie', snowy: 'Neige', 'snowy-rainy': 'Pluie et neige',
    sunny: 'Ensoleillé', windy: 'Venteux', 'windy-variant': 'Venteux' },
  en: { 'clear-night': 'Clear night', cloudy: 'Cloudy', exceptional: 'Exceptional', fog: 'Fog', hail: 'Hail', lightning: 'Lightning',
    'lightning-rainy': 'Lightning and rain', partlycloudy: 'Partly cloudy', pouring: 'Pouring', rainy: 'Rainy', snowy: 'Snowy', 'snowy-rainy': 'Snow and rain',
    sunny: 'Sunny', windy: 'Windy', 'windy-variant': 'Windy' },
};
// The icon of a condition of a Home Assistant weather entity. Hail without thunder: the pellets of the snow grains (the set has no
// other hail); rain and snow: the rain with ice of the freezing rain.
const CONDITION_ICON = { 'clear-night': 'lune', cloudy: 'couvert', exceptional: 'couvert', fog: 'brouillard', hail: 'grains_de_neige', lightning: 'orage',
  'lightning-rainy': 'orage', partlycloudy: 'eclaircies', pouring: 'pluie_forte', rainy: 'pluie', snowy: 'neige', 'snowy-rainy': 'pluie_verglacante',
  sunny: 'soleil', windy: 'couvert', 'windy-variant': 'couvert' };
// Why a verdict is not "good", `<criterion>.<state>` of the API: the words of the Vigie site.
const REASON = {
  fr: { criterion: { wind: 'Vent', gust_gap: 'Rafales', wind_direction: 'Vent', wind_force: 'Vent', gust_force: 'Rafales', sea_height: 'Vagues',
    low_water: 'Basse mer', coefficient: 'Coefficient', swell_height: 'Houle', swell_period: 'Période', swell_direction: 'Houle', tide: 'Marée', vigilance: 'Vigilance' },
    state: { 'swell_height.too_small': 'trop petite', 'swell_height.small': 'petite', 'swell_height.right': 'bonne', 'swell_height.big': 'forte',
      'swell_height.too_big': 'trop forte', 'swell_period.short': 'courte', 'swell_period.medium': 'moyenne', 'swell_period.long': 'longue',
      'swell_direction.facing': 'de face', 'swell_direction.oblique': 'de biais', 'swell_direction.off_axis': 'hors de l’axe', 'wind.light': 'léger',
      'wind.offshore': 'de terre', 'wind.cross_shore': 'parallèle à la côte', 'wind.onshore': 'de mer', 'wind.strong': 'trop fort', 'wind.in_range': 'dans la plage voulue',
      'wind.a_bit_light': 'un peu faible', 'wind.a_bit_strong': 'un peu fort', 'wind.too_light': 'trop faible', 'wind.too_strong': 'trop fort',
      'tide.right_stage': 'bon moment', 'tide.wrong_stage': 'mauvais moment', 'gust_gap.steady': 'régulières', 'gust_gap.gusty': 'marquées',
      'gust_gap.very_gusty': 'très marquées', 'wind_direction.offshore': 'de terre', 'wind_direction.cross_shore': 'parallèle à la côte',
      'wind_direction.onshore': 'de mer', 'wind_force.up_to_moderate': 'modéré ou moins', 'wind_force.up_to_fresh': 'frais ou moins', 'wind_force.fresh': 'frais',
      'wind_force.strong': 'fort', 'wind_force.strong_or_more': 'fort ou plus', 'wind_force.near_gale_or_more': 'coup de vent ou plus',
      'gust_force.up_to_fresh': 'modérées', 'gust_force.strong': 'fortes', 'gust_force.gale_or_more': 'coup de vent ou plus', 'sea_height.low': 'faibles',
      'sea_height.moderate': 'modérées', 'sea_height.high': 'fortes', 'low_water.in_window': 'dans les 2 h avant', 'low_water.outside_window': 'loin de la basse mer',
      'coefficient.small': 'petit', 'coefficient.moderate': 'moyen', 'coefficient.large': 'grand', 'coefficient.very_large': 'très fort',
      'vigilance.red': 'rouge', 'vigilance.orange': 'orange' } },
  en: { criterion: { wind: 'Wind', gust_gap: 'Gusts', wind_direction: 'Wind', wind_force: 'Wind', gust_force: 'Gusts', sea_height: 'Waves',
    low_water: 'Low water', coefficient: 'Coefficient', swell_height: 'Swell', swell_period: 'Period', swell_direction: 'Swell', tide: 'Tide', vigilance: 'Warning' },
    state: { 'swell_height.too_small': 'too small', 'swell_height.small': 'small', 'swell_height.right': 'right', 'swell_height.big': 'big',
      'swell_height.too_big': 'too big', 'swell_period.short': 'short', 'swell_period.medium': 'medium', 'swell_period.long': 'long',
      'swell_direction.facing': 'facing', 'swell_direction.oblique': 'oblique', 'swell_direction.off_axis': 'off axis', 'wind.light': 'light',
      'wind.offshore': 'offshore', 'wind.cross_shore': 'cross-shore', 'wind.onshore': 'onshore', 'wind.strong': 'too strong', 'wind.in_range': 'in range',
      'wind.a_bit_light': 'a bit light', 'wind.a_bit_strong': 'a bit strong', 'wind.too_light': 'too light', 'wind.too_strong': 'too strong',
      'tide.right_stage': 'right stage', 'tide.wrong_stage': 'wrong stage', 'gust_gap.steady': 'steady', 'gust_gap.gusty': 'gusty',
      'gust_gap.very_gusty': 'very gusty', 'wind_direction.offshore': 'offshore', 'wind_direction.cross_shore': 'cross-shore', 'wind_direction.onshore': 'onshore',
      'wind_force.up_to_moderate': 'moderate or less', 'wind_force.up_to_fresh': 'fresh or less', 'wind_force.fresh': 'fresh', 'wind_force.strong': 'strong',
      'wind_force.strong_or_more': 'strong or more', 'wind_force.near_gale_or_more': 'near gale or more', 'gust_force.up_to_fresh': 'moderate',
      'gust_force.strong': 'strong', 'gust_force.gale_or_more': 'gale or more', 'sea_height.low': 'low', 'sea_height.moderate': 'moderate', 'sea_height.high': 'high',
      'low_water.in_window': 'within 2 h before', 'low_water.outside_window': 'far from low water', 'coefficient.small': 'small', 'coefficient.moderate': 'moderate',
      'coefficient.large': 'large', 'coefficient.very_large': 'very large', 'vigilance.red': 'red', 'vigilance.orange': 'orange' } },
};
const EDITOR_TEXT = {
  fr: { entry_id: 'Lieu (entrée Vigie)', name: 'Nom affiché', pages: 'Pages affichées et leur ordre', pages_help: 'Glisser une ligne ou utiliser les flèches pour changer l’ordre. Une page sans donnée pour le lieu ne s’affiche pas, même cochée.',
    start_page: 'Page d’ouverture', idle: 'Écran qu’on ne touche pas', idle_return: 'Retour à la page d’ouverture après une minute', idle_cycle: 'Faire défiler les pages toutes les 20 secondes',
    idle_none: 'Rester sur la page choisie', spot: 'Spot de la page Mer', spot_auto: 'Le plus proche (moins de 20 km)', spot_none: 'Aucun',
    weather_entity: 'Hors de France : entité météo de la maison', weather_entity_help: 'Pour l’accueil d’un lieu hors de France ; sans elle, l’accueil montre les alertes, les séismes et la marée.',
    icon_style: 'Style des icônes météo', icon_style_help: 'Les icônes de l’écran Meridien, en quatre styles ; leurs couleurs suivent le thème clair ou sombre.',
    icons_meridien: 'Meridien (celles de l’écran)', icons_line: 'Trait', icons_duo: 'Duo', icons_badge: 'Pastille',
    up: 'Monter', down: 'Descendre', no_form: 'L’éditeur visuel ne s’est pas chargé : la carte se règle aussi en YAML (voir la documentation).' },
  en: { entry_id: 'Place (Vigie entry)', name: 'Name shown', pages: 'Pages shown and their order', pages_help: 'Drag a row or use the arrows to change the order. A page with no data for the place is not shown, even ticked.',
    start_page: 'Opening page', idle: 'Screen nobody touches', idle_return: 'Back to the opening page after a minute', idle_cycle: 'Go through the pages every 20 seconds',
    idle_none: 'Stay on the page chosen', spot: 'Spot of the Sea page', spot_auto: 'The nearest (within 20 km)', spot_none: 'None',
    weather_entity: 'Outside France: weather entity of the home', weather_entity_help: 'For the home page of a place outside France; without it the home page shows the warnings, the earthquakes and the tide.',
    icon_style: 'Style of the weather icons', icon_style_help: 'The icons of the Meridien screen, in four styles; their colours follow the light or dark theme.',
    icons_meridien: 'Meridien (the screen’s)', icons_line: 'Line', icons_duo: 'Duo', icons_badge: 'Badge',
    up: 'Up', down: 'Down', no_form: 'The visual editor did not load: the card can also be set in YAML (see the documentation).' },
};

// ------------------------------------------------------------------------------------------------------------------ helpers
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const num = (v) => typeof v === 'number' && Number.isFinite(v);
const r1 = (v) => Math.round(v * 10) / 10;
const rad = (d) => (d * Math.PI) / 180;
const pad2 = (n) => String(n).padStart(2, '0');
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const hexRgb = (h) => [1, 3, 5].map((i) => parseInt(h.substr(i, 2), 16));
const mix = (a, b, t) => { const x = hexRgb(a), y = hexRgb(b); return '#' + x.map((v, i) => Math.round(v + (y[i] - v) * t).toString(16).padStart(2, '0')).join(''); };
const alpha = (hex, a) => hex + Math.round(a * 255).toString(16).padStart(2, '0');
const REP = [-10, 0, 12, 24, 36];
function tempColour(P, t) {
  if (!num(t)) return P.t2;
  if (t <= REP[0]) return P.ramp[0];
  for (let i = 1; i < 5; i++) if (t <= REP[i]) return mix(P.ramp[i - 1], P.ramp[i], (t - REP[i - 1]) / (REP[i] - REP[i - 1]));
  return P.ramp[4];
}
const rainColour = (P, mmh) => (mmh >= 3 ? P.pluieForte : mmh >= 1 ? P.pluie : P.pluieFaible);
// Monotone curve (Fritsch-Carlson): never a bump that is not in the data between two points.
function smooth(p) {
  const n = p.length, dx = [], m = [], t = new Array(n).fill(0);
  if (n < 2) return n ? `M${r1(p[0].x)} ${r1(p[0].y)}` : '';
  for (let i = 0; i < n - 1; i++) { dx[i] = p[i + 1].x - p[i].x; m[i] = (p[i + 1].y - p[i].y) / dx[i]; }
  t[0] = m[0]; t[n - 1] = m[n - 2];
  for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (m[i] === 0) { t[i] = 0; t[i + 1] = 0; } else {
      const a = t[i] / m[i], b = t[i + 1] / m[i], s = a * a + b * b;
      if (s > 9) { const k = 3 / Math.sqrt(s); t[i] = k * a * m[i]; t[i + 1] = k * b * m[i]; }
    }
  }
  let d = `M${r1(p[0].x)} ${r1(p[0].y)}`;
  for (let i = 0; i < n - 1; i++) { const h = dx[i] / 3; d += ` C${r1(p[i].x + h)} ${r1(p[i].y + t[i] * h)} ${r1(p[i + 1].x - h)} ${r1(p[i + 1].y - t[i + 1] * h)} ${r1(p[i + 1].x)} ${r1(p[i + 1].y)}`; }
  return d;
}
// Is the sun up at a place (refraction and the sun's radius: -0.833°)? Enough to draw a moon instead of a sun.
function sunUp(lat, lon, ts) {
  const d = ts / 86400 - 10957.5;
  const g = rad(357.529 + 0.98560028 * d), q = 280.459 + 0.98564736 * d;
  const L = rad(q + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)), e = rad(23.439 - 0.00000036 * d);
  const ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L)), dec = Math.asin(Math.sin(e) * Math.sin(L));
  const gmst = (18.697374558 + 24.06570982441908 * d) % 24, ha = rad(gmst * 15 + lon) - ra;
  return Math.asin(Math.sin(rad(lat)) * Math.sin(dec) + Math.cos(rad(lat)) * Math.cos(dec) * Math.cos(ha)) > rad(-0.833);
}

// ---------------------------------------------------------------------------------------------------------- weather icons
// <icons: drawn by scripts/card_icons.py, edit the script and run it, never this block by hand>
const ICON_STYLES = ['meridien', 'line', 'duo', 'badge'];
const ICON_SETS = {
  meridien: { box: '0 0 128 128', icons: {
    soleil: '<g class="vs"><circle cx="64" cy="64" r="24"/><g fill="none" stroke="currentColor" stroke-width="8"><path d="M100 64L117 64"/><path d="M89.5 89.5L101.5 101.5"/><path d="M64 100L64 117"/><path d="M38.5 89.5L26.5 101.5"/><path d="M28 64L11 64"/><path d="M38.5 38.5L26.5 26.5"/><path d="M64 28L64 11"/><path d="M89.5 38.5L101.5 26.5"/></g></g>',
    lune: '<mask id="@0" maskUnits="userSpaceOnUse" x="-64" y="-64" width="256" height="256"><rect x="-64" y="-64" width="256" height="256" fill="#fff"/><g fill="#000"><circle cx="81.9" cy="49.5" r="31"/></g></mask><g class="vl"><g mask="url(#@0)"><circle cx="60" cy="68" r="36"/></g><polygon points="97 29 99 36 106 38 99 40 97 47 95 40 88 38 95 36"/><polygon points="104 60.5 105.2 64.8 109.5 66 105.2 67.2 104 71.5 102.8 67.2 98.5 66 102.8 64.8"/></g>',
    eclaircies: '<mask id="@0" maskUnits="userSpaceOnUse" x="-64" y="-64" width="256" height="256"><rect x="-64" y="-64" width="256" height="256" fill="#fff"/><g fill="#000"><rect x="19.9" y="77" width="102.1" height="35.5" rx="17.7"/><circle cx="49.4" cy="82.9" r="23.6"/><circle cx="75.9" cy="74.1" r="29.5"/><circle cx="100.4" cy="90.8" r="21.7"/></g></mask><g class="vs"><g mask="url(#@0)"><circle cx="46" cy="45" r="19"/><g fill="none" stroke="currentColor" stroke-width="6.5"><path d="M25.5 65.5L17 74"/><path d="M17 45L5 45"/><path d="M25.5 24.5L17 16"/><path d="M46 16L46 4"/><path d="M66.5 24.5L75 16"/></g></g></g><g class="vn"><rect x="24.9" y="82" width="92.1" height="25.5" rx="12.7"/><circle cx="49.4" cy="82.9" r="18.6"/><circle cx="75.9" cy="74.1" r="24.5"/><circle cx="100.4" cy="90.8" r="16.7"/></g>',
    nuit_nuageuse: '<mask id="@0" maskUnits="userSpaceOnUse" x="-64" y="-64" width="256" height="256"><rect x="-64" y="-64" width="256" height="256" fill="#fff"/><g fill="#000"><circle cx="62.4" cy="32.1" r="23.2"/><rect x="19.9" y="77" width="102.1" height="35.5" rx="17.7"/><circle cx="49.4" cy="82.9" r="23.6"/><circle cx="75.9" cy="74.1" r="29.5"/><circle cx="100.4" cy="90.8" r="21.7"/></g></mask><mask id="@1" maskUnits="userSpaceOnUse" x="-64" y="-64" width="256" height="256"><rect x="-64" y="-64" width="256" height="256" fill="#fff"/><g fill="#000"><rect x="19.9" y="77" width="102.1" height="35.5" rx="17.7"/><circle cx="49.4" cy="82.9" r="23.6"/><circle cx="75.9" cy="74.1" r="29.5"/><circle cx="100.4" cy="90.8" r="21.7"/></g></mask><g class="vl"><g mask="url(#@0)"><circle cx="46" cy="46" r="27"/></g><g mask="url(#@1)"><polygon points="76 17 77.5 22.5 83 24 77.5 25.5 76 31 74.5 25.5 69 24 74.5 22.5"/></g></g><g class="vn"><rect x="24.9" y="82" width="92.1" height="25.5" rx="12.7"/><circle cx="49.4" cy="82.9" r="18.6"/><circle cx="75.9" cy="74.1" r="24.5"/><circle cx="100.4" cy="90.8" r="16.7"/></g>',
    couvert: '<mask id="@0" maskUnits="userSpaceOnUse" x="-64" y="-64" width="256" height="256"><rect x="-64" y="-64" width="256" height="256" fill="#fff"/><g fill="#000"><rect x="5" y="67" width="104" height="36" rx="18"/><circle cx="35" cy="73" r="24"/><circle cx="62" cy="64" r="30"/><circle cx="87" cy="81" r="22"/></g></mask><g class="vm"><g mask="url(#@0)"><rect x="49.2" y="43.6" width="75.2" height="20.8" rx="10.4"/><circle cx="69.2" cy="44.4" r="15.2"/><circle cx="90.8" cy="37.2" r="20"/><circle cx="110.8" cy="50.8" r="13.6"/></g></g><g class="vn"><rect x="10" y="72" width="94" height="26" rx="13"/><circle cx="35" cy="73" r="19"/><circle cx="62" cy="64" r="25"/><circle cx="87" cy="81" r="17"/></g>',
    brouillard: '<g class="vn"><rect x="18" y="48" width="94" height="26" rx="13"/><circle cx="43" cy="49" r="19"/><circle cx="70" cy="40" r="25"/><circle cx="95" cy="57" r="17"/></g><g class="vb"><g fill="none" stroke="currentColor" stroke-width="7.5"><path d="M26 86L96 86"/><path d="M40 100L108 100"/><path d="M28 114L84 114"/></g></g>',
    bruine: '<g class="vn"><rect x="18" y="48" width="94" height="26" rx="13"/><circle cx="43" cy="49" r="19"/><circle cx="70" cy="40" r="25"/><circle cx="95" cy="57" r="17"/></g><g class="ve"><circle cx="40" cy="94" r="4.2"/><circle cx="64" cy="94" r="4.2"/><circle cx="88" cy="94" r="4.2"/><circle cx="52" cy="108" r="4.2"/><circle cx="76" cy="108" r="4.2"/></g>',
    pluie: '<g class="vm"><rect x="18" y="48" width="94" height="26" rx="13"/><circle cx="43" cy="49" r="19"/><circle cx="70" cy="40" r="25"/><circle cx="95" cy="57" r="17"/></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="7.5"><path d="M48.9 88.5L43.1 107.5"/><path d="M68.8 96.5L63.1 115.5"/><path d="M88.8 88.5L83.2 107.5"/></g></g>',
    pluie_forte: '<g class="vm"><rect x="18" y="48" width="94" height="26" rx="13"/><circle cx="43" cy="49" r="19"/><circle cx="70" cy="40" r="25"/><circle cx="95" cy="57" r="17"/></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="8"><path d="M42.7 83L37.3 101"/><path d="M64.7 83L59.3 101"/><path d="M86.7 83L81.3 101"/><path d="M54.7 103L49.3 121"/><path d="M76.7 103L71.3 121"/><path d="M98.7 103L93.3 121"/></g></g>',
    pluie_verglacante: '<g class="vm"><rect x="18" y="48" width="94" height="26" rx="13"/><circle cx="43" cy="49" r="19"/><circle cx="70" cy="40" r="25"/><circle cx="95" cy="57" r="17"/></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="7.5"><path d="M48.9 88.5L43.1 107.5"/><path d="M84.8 88.5L79.2 107.5"/></g></g><g class="vg"><circle cx="64" cy="94" r="5.2"/><circle cx="56" cy="112" r="5.2"/><circle cx="74" cy="112" r="5.2"/></g>',
    averses: '<mask id="@0" maskUnits="userSpaceOnUse" x="-64" y="-64" width="256" height="256"><rect x="-64" y="-64" width="256" height="256" fill="#fff"/><g fill="#000"><rect x="25.6" y="48.8" width="94.6" height="33.4" rx="16.7"/><circle cx="53.1" cy="54.7" r="22.1"/><circle cx="77.4" cy="46.6" r="27.5"/><circle cx="99.9" cy="61.9" r="20.3"/></g></mask><g class="vs"><g mask="url(#@0)"><circle cx="47" cy="34" r="17"/><g fill="none" stroke="currentColor" stroke-width="6"><path d="M29.3 51.7L21.5 59.5"/><path d="M22 34L11 34"/><path d="M29.3 16.3L21.5 8.5"/><path d="M47 9L47 -2"/></g></g></g><g class="vm"><rect x="30.6" y="53.8" width="84.6" height="23.4" rx="11.7"/><circle cx="53.1" cy="54.7" r="17.1"/><circle cx="77.4" cy="46.6" r="22.5"/><circle cx="99.9" cy="61.9" r="15.3"/></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="7.5"><path d="M54.9 89.5L49.1 108.5"/><path d="M74.8 97.5L69.2 116.5"/></g></g>',
    averses_nuit: '<mask id="@0" maskUnits="userSpaceOnUse" x="-64" y="-64" width="256" height="256"><rect x="-64" y="-64" width="256" height="256" fill="#fff"/><g fill="#000"><circle cx="60" cy="22.2" r="19.8"/><rect x="25.6" y="48.8" width="94.6" height="33.4" rx="16.7"/><circle cx="53.1" cy="54.7" r="22.1"/><circle cx="77.4" cy="46.6" r="27.5"/><circle cx="99.9" cy="61.9" r="20.3"/></g></mask><g class="vl"><g mask="url(#@0)"><circle cx="46" cy="34" r="23"/></g></g><g class="vm"><rect x="30.6" y="53.8" width="84.6" height="23.4" rx="11.7"/><circle cx="53.1" cy="54.7" r="17.1"/><circle cx="77.4" cy="46.6" r="22.5"/><circle cx="99.9" cy="61.9" r="15.3"/></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="7.5"><path d="M54.9 89.5L49.1 108.5"/><path d="M74.8 97.5L69.2 116.5"/></g></g>',
    averses_violentes: '<g class="vm"><rect x="18" y="48" width="94" height="26" rx="13"/><circle cx="43" cy="49" r="19"/><circle cx="70" cy="40" r="25"/><circle cx="95" cy="57" r="17"/></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="8"><path d="M46.7 87L41.3 105"/><path d="M66.7 87L61.3 105"/><path d="M86.7 87L81.3 105"/><path d="M56.7 105L51.3 123"/><path d="M76.7 105L71.3 123"/></g></g>',
    neige: '<g class="vn"><rect x="18" y="48" width="94" height="26" rx="13"/><circle cx="43" cy="49" r="19"/><circle cx="70" cy="40" r="25"/><circle cx="95" cy="57" r="17"/></g><g class="vg"><g fill="none" stroke="currentColor" stroke-width="4.8"><path d="M42 89.5L42 110.5"/><path d="M51.1 94.8L32.9 105.2"/><path d="M51.1 105.2L32.9 94.8"/></g><g fill="none" stroke="currentColor" stroke-width="3.5"><path d="M42 106.5L44.4 108.5"/><path d="M42 106.5L39.6 108.5"/><path d="M36.4 103.3L35.8 106.4"/><path d="M36.4 103.3L33.4 102.2"/><path d="M36.4 96.7L33.4 97.8"/><path d="M36.4 96.7L35.8 93.6"/><path d="M42 93.5L39.6 91.5"/><path d="M42 93.5L44.4 91.5"/><path d="M47.6 96.7L48.2 93.6"/><path d="M47.6 96.7L50.6 97.8"/><path d="M47.6 103.3L50.6 102.2"/><path d="M47.6 103.3L48.2 106.4"/></g><g fill="none" stroke="currentColor" stroke-width="4.8"><path d="M64 97.5L64 118.5"/><path d="M73.1 102.8L54.9 113.2"/><path d="M73.1 113.2L54.9 102.8"/></g><g fill="none" stroke="currentColor" stroke-width="3.5"><path d="M64 114.5L66.4 116.5"/><path d="M64 114.5L61.6 116.5"/><path d="M58.4 111.3L57.8 114.4"/><path d="M58.4 111.3L55.4 110.2"/><path d="M58.4 104.7L55.4 105.8"/><path d="M58.4 104.7L57.8 101.6"/><path d="M64 101.5L61.6 99.5"/><path d="M64 101.5L66.4 99.5"/><path d="M69.6 104.7L70.2 101.6"/><path d="M69.6 104.7L72.6 105.8"/><path d="M69.6 111.3L72.6 110.2"/><path d="M69.6 111.3L70.2 114.4"/></g><g fill="none" stroke="currentColor" stroke-width="4.8"><path d="M86 89.5L86 110.5"/><path d="M95.1 94.8L76.9 105.2"/><path d="M95.1 105.2L76.9 94.8"/></g><g fill="none" stroke="currentColor" stroke-width="3.5"><path d="M86 106.5L88.4 108.5"/><path d="M86 106.5L83.6 108.5"/><path d="M80.4 103.3L79.8 106.4"/><path d="M80.4 103.3L77.4 102.2"/><path d="M80.4 96.7L77.4 97.8"/><path d="M80.4 96.7L79.8 93.6"/><path d="M86 93.5L83.6 91.5"/><path d="M86 93.5L88.4 91.5"/><path d="M91.6 96.7L92.2 93.6"/><path d="M91.6 96.7L94.6 97.8"/><path d="M91.6 103.3L94.6 102.2"/><path d="M91.6 103.3L92.2 106.4"/></g></g>',
    neige_forte: '<g class="vn"><rect x="18" y="48" width="94" height="26" rx="13"/><circle cx="43" cy="49" r="19"/><circle cx="70" cy="40" r="25"/><circle cx="95" cy="57" r="17"/></g><g class="vg"><g fill="none" stroke="currentColor" stroke-width="4.8"><path d="M38 83.5L38 104.5"/><path d="M47.1 88.8L28.9 99.2"/><path d="M47.1 99.2L28.9 88.8"/></g><g fill="none" stroke="currentColor" stroke-width="3.5"><path d="M38 100.5L40.4 102.5"/><path d="M38 100.5L35.6 102.5"/><path d="M32.4 97.3L31.8 100.4"/><path d="M32.4 97.3L29.4 96.2"/><path d="M32.4 90.7L29.4 91.8"/><path d="M32.4 90.7L31.8 87.6"/><path d="M38 87.5L35.6 85.5"/><path d="M38 87.5L40.4 85.5"/><path d="M43.6 90.7L44.2 87.6"/><path d="M43.6 90.7L46.6 91.8"/><path d="M43.6 97.3L46.6 96.2"/><path d="M43.6 97.3L44.2 100.4"/></g><g fill="none" stroke="currentColor" stroke-width="4.8"><path d="M64 83.5L64 104.5"/><path d="M73.1 88.8L54.9 99.2"/><path d="M73.1 99.2L54.9 88.8"/></g><g fill="none" stroke="currentColor" stroke-width="3.5"><path d="M64 100.5L66.4 102.5"/><path d="M64 100.5L61.6 102.5"/><path d="M58.4 97.3L57.8 100.4"/><path d="M58.4 97.3L55.4 96.2"/><path d="M58.4 90.7L55.4 91.8"/><path d="M58.4 90.7L57.8 87.6"/><path d="M64 87.5L61.6 85.5"/><path d="M64 87.5L66.4 85.5"/><path d="M69.6 90.7L70.2 87.6"/><path d="M69.6 90.7L72.6 91.8"/><path d="M69.6 97.3L72.6 96.2"/><path d="M69.6 97.3L70.2 100.4"/></g><g fill="none" stroke="currentColor" stroke-width="4.8"><path d="M90 83.5L90 104.5"/><path d="M99.1 88.8L80.9 99.2"/><path d="M99.1 99.2L80.9 88.8"/></g><g fill="none" stroke="currentColor" stroke-width="3.5"><path d="M90 100.5L92.4 102.5"/><path d="M90 100.5L87.6 102.5"/><path d="M84.4 97.3L83.8 100.4"/><path d="M84.4 97.3L81.4 96.2"/><path d="M84.4 90.7L81.4 91.8"/><path d="M84.4 90.7L83.8 87.6"/><path d="M90 87.5L87.6 85.5"/><path d="M90 87.5L92.4 85.5"/><path d="M95.6 90.7L96.2 87.6"/><path d="M95.6 90.7L98.6 91.8"/><path d="M95.6 97.3L98.6 96.2"/><path d="M95.6 97.3L96.2 100.4"/></g><g fill="none" stroke="currentColor" stroke-width="4.8"><path d="M51 103.5L51 124.5"/><path d="M60.1 108.8L41.9 119.2"/><path d="M60.1 119.2L41.9 108.8"/></g><g fill="none" stroke="currentColor" stroke-width="3.5"><path d="M51 120.5L53.4 122.5"/><path d="M51 120.5L48.6 122.5"/><path d="M45.4 117.3L44.8 120.4"/><path d="M45.4 117.3L42.4 116.2"/><path d="M45.4 110.7L42.4 111.8"/><path d="M45.4 110.7L44.8 107.6"/><path d="M51 107.5L48.6 105.5"/><path d="M51 107.5L53.4 105.5"/><path d="M56.6 110.7L57.2 107.6"/><path d="M56.6 110.7L59.6 111.8"/><path d="M56.6 117.3L59.6 116.2"/><path d="M56.6 117.3L57.2 120.4"/></g><g fill="none" stroke="currentColor" stroke-width="4.8"><path d="M77 103.5L77 124.5"/><path d="M86.1 108.8L67.9 119.2"/><path d="M86.1 119.2L67.9 108.8"/></g><g fill="none" stroke="currentColor" stroke-width="3.5"><path d="M77 120.5L79.4 122.5"/><path d="M77 120.5L74.6 122.5"/><path d="M71.4 117.3L70.8 120.4"/><path d="M71.4 117.3L68.4 116.2"/><path d="M71.4 110.7L68.4 111.8"/><path d="M71.4 110.7L70.8 107.6"/><path d="M77 107.5L74.6 105.5"/><path d="M77 107.5L79.4 105.5"/><path d="M82.6 110.7L83.2 107.6"/><path d="M82.6 110.7L85.6 111.8"/><path d="M82.6 117.3L85.6 116.2"/><path d="M82.6 117.3L83.2 120.4"/></g></g>',
    grains_de_neige: '<g class="vn"><rect x="18" y="48" width="94" height="26" rx="13"/><circle cx="43" cy="49" r="19"/><circle cx="70" cy="40" r="25"/><circle cx="95" cy="57" r="17"/></g><g class="vg"><circle cx="40" cy="96" r="5.4"/><circle cx="62" cy="96" r="5.4"/><circle cx="84" cy="96" r="5.4"/><circle cx="51" cy="112" r="5.4"/><circle cx="73" cy="112" r="5.4"/></g>',
    averses_neige: '<mask id="@0" maskUnits="userSpaceOnUse" x="-64" y="-64" width="256" height="256"><rect x="-64" y="-64" width="256" height="256" fill="#fff"/><g fill="#000"><rect x="25.6" y="48.8" width="94.6" height="33.4" rx="16.7"/><circle cx="53.1" cy="54.7" r="22.1"/><circle cx="77.4" cy="46.6" r="27.5"/><circle cx="99.9" cy="61.9" r="20.3"/></g></mask><g class="vs"><g mask="url(#@0)"><circle cx="47" cy="34" r="17"/><g fill="none" stroke="currentColor" stroke-width="6"><path d="M29.3 51.7L21.5 59.5"/><path d="M22 34L11 34"/><path d="M29.3 16.3L21.5 8.5"/><path d="M47 9L47 -2"/></g></g></g><g class="vn"><rect x="30.6" y="53.8" width="84.6" height="23.4" rx="11.7"/><circle cx="53.1" cy="54.7" r="17.1"/><circle cx="77.4" cy="46.6" r="22.5"/><circle cx="99.9" cy="61.9" r="15.3"/></g><g class="vg"><g fill="none" stroke="currentColor" stroke-width="4.8"><path d="M52 89.5L52 110.5"/><path d="M61.1 94.8L42.9 105.2"/><path d="M61.1 105.2L42.9 94.8"/></g><g fill="none" stroke="currentColor" stroke-width="3.5"><path d="M52 106.5L54.4 108.5"/><path d="M52 106.5L49.6 108.5"/><path d="M46.4 103.3L45.8 106.4"/><path d="M46.4 103.3L43.4 102.2"/><path d="M46.4 96.7L43.4 97.8"/><path d="M46.4 96.7L45.8 93.6"/><path d="M52 93.5L49.6 91.5"/><path d="M52 93.5L54.4 91.5"/><path d="M57.6 96.7L58.2 93.6"/><path d="M57.6 96.7L60.6 97.8"/><path d="M57.6 103.3L60.6 102.2"/><path d="M57.6 103.3L58.2 106.4"/></g><g fill="none" stroke="currentColor" stroke-width="4.8"><path d="M76 97.5L76 118.5"/><path d="M85.1 102.8L66.9 113.2"/><path d="M85.1 113.2L66.9 102.8"/></g><g fill="none" stroke="currentColor" stroke-width="3.5"><path d="M76 114.5L78.4 116.5"/><path d="M76 114.5L73.6 116.5"/><path d="M70.4 111.3L69.8 114.4"/><path d="M70.4 111.3L67.4 110.2"/><path d="M70.4 104.7L67.4 105.8"/><path d="M70.4 104.7L69.8 101.6"/><path d="M76 101.5L73.6 99.5"/><path d="M76 101.5L78.4 99.5"/><path d="M81.6 104.7L82.2 101.6"/><path d="M81.6 104.7L84.6 105.8"/><path d="M81.6 111.3L84.6 110.2"/><path d="M81.6 111.3L82.2 114.4"/></g></g>',
    averses_neige_nuit: '<mask id="@0" maskUnits="userSpaceOnUse" x="-64" y="-64" width="256" height="256"><rect x="-64" y="-64" width="256" height="256" fill="#fff"/><g fill="#000"><circle cx="60" cy="22.2" r="19.8"/><rect x="25.6" y="48.8" width="94.6" height="33.4" rx="16.7"/><circle cx="53.1" cy="54.7" r="22.1"/><circle cx="77.4" cy="46.6" r="27.5"/><circle cx="99.9" cy="61.9" r="20.3"/></g></mask><g class="vl"><g mask="url(#@0)"><circle cx="46" cy="34" r="23"/></g></g><g class="vn"><rect x="30.6" y="53.8" width="84.6" height="23.4" rx="11.7"/><circle cx="53.1" cy="54.7" r="17.1"/><circle cx="77.4" cy="46.6" r="22.5"/><circle cx="99.9" cy="61.9" r="15.3"/></g><g class="vg"><g fill="none" stroke="currentColor" stroke-width="4.8"><path d="M52 89.5L52 110.5"/><path d="M61.1 94.8L42.9 105.2"/><path d="M61.1 105.2L42.9 94.8"/></g><g fill="none" stroke="currentColor" stroke-width="3.5"><path d="M52 106.5L54.4 108.5"/><path d="M52 106.5L49.6 108.5"/><path d="M46.4 103.3L45.8 106.4"/><path d="M46.4 103.3L43.4 102.2"/><path d="M46.4 96.7L43.4 97.8"/><path d="M46.4 96.7L45.8 93.6"/><path d="M52 93.5L49.6 91.5"/><path d="M52 93.5L54.4 91.5"/><path d="M57.6 96.7L58.2 93.6"/><path d="M57.6 96.7L60.6 97.8"/><path d="M57.6 103.3L60.6 102.2"/><path d="M57.6 103.3L58.2 106.4"/></g><g fill="none" stroke="currentColor" stroke-width="4.8"><path d="M76 97.5L76 118.5"/><path d="M85.1 102.8L66.9 113.2"/><path d="M85.1 113.2L66.9 102.8"/></g><g fill="none" stroke="currentColor" stroke-width="3.5"><path d="M76 114.5L78.4 116.5"/><path d="M76 114.5L73.6 116.5"/><path d="M70.4 111.3L69.8 114.4"/><path d="M70.4 111.3L67.4 110.2"/><path d="M70.4 104.7L67.4 105.8"/><path d="M70.4 104.7L69.8 101.6"/><path d="M76 101.5L73.6 99.5"/><path d="M76 101.5L78.4 99.5"/><path d="M81.6 104.7L82.2 101.6"/><path d="M81.6 104.7L84.6 105.8"/><path d="M81.6 111.3L84.6 110.2"/><path d="M81.6 111.3L82.2 114.4"/></g></g>',
    orage: '<g class="vm"><rect x="18" y="48" width="94" height="26" rx="13"/><circle cx="43" cy="49" r="19"/><circle cx="70" cy="40" r="25"/><circle cx="95" cy="57" r="17"/></g><g class="vx"><g stroke="currentColor" stroke-width="3.2"><polygon points="80 64 52 100 65 100 56 124 84 86 70 86"/></g></g>',
    orage_grele: '<g class="vm"><rect x="18" y="48" width="94" height="26" rx="13"/><circle cx="43" cy="49" r="19"/><circle cx="70" cy="40" r="25"/><circle cx="95" cy="57" r="17"/></g><g class="vx"><g stroke="currentColor" stroke-width="3.2"><polygon points="80 64 52 100 65 100 56 124 84 86 70 86"/></g></g><g class="vg"><circle cx="36" cy="100" r="5.4"/><circle cx="47" cy="117" r="5.4"/><circle cx="94" cy="100" r="5.4"/><circle cx="83" cy="117" r="5.4"/></g>',
  } },
  line: { box: '0 0 24 24', icons: {
    soleil: '<g class="vs"><g fill="none" stroke="currentColor" stroke-width="1.75"><circle cx="12" cy="12" r="4.4"/><path d="M19.4 12L21.9 12"/><path d="M17.23 17.23L19 19"/><path d="M12 19.4L12 21.9"/><path d="M6.77 17.23L5 19"/><path d="M4.6 12L2.1 12"/><path d="M6.77 6.77L5 5"/><path d="M12 4.6L12 2.1"/><path d="M17.23 6.77L19 5"/></g></g>',
    lune: '<g class="vl"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M10.2 5.44A7.6 7.6 0 1 0 18.32 15.05A6.54 6.54 0 0 1 10.2 5.44Z"/></g><polygon points="18.6 4.1 19.15 5.85 20.9 6.4 19.15 6.95 18.6 8.7 18.05 6.95 16.3 6.4 18.05 5.85"/><polygon points="20.3 11 20.64 12.06 21.7 12.4 20.64 12.74 20.3 13.8 19.96 12.74 18.9 12.4 19.96 12.06"/></g>',
    eclaircies: '<mask id="@0" maskUnits="userSpaceOnUse" x="-12" y="-12" width="48" height="48"><rect x="-12" y="-12" width="48" height="48" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="4.55"><path d="M8.93 20.77H18.82A3.6 3.6 0 0 0 18.94 13.58A4.95 4.95 0 0 0 9.45 14.52A3.15 3.15 0 1 0 8.93 20.77Z"/></g></mask><g class="vs"><g mask="url(#@0)"><g fill="none" stroke="currentColor" stroke-width="1.75"><circle cx="9.1" cy="9.6" r="3.7"/><path d="M15 9.6L17 9.6"/><path d="M13.27 13.77L14.69 15.19"/><path d="M9.1 15.5L9.1 17.5"/><path d="M4.93 13.77L3.51 15.19"/><path d="M3.2 9.6L1.2 9.6"/><path d="M4.93 5.43L3.51 4.01"/><path d="M9.1 3.7L9.1 1.7"/><path d="M13.27 5.43L14.69 4.01"/></g></g></g><g class="vn"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M8.93 20.77H18.82A3.6 3.6 0 0 0 18.94 13.58A4.95 4.95 0 0 0 9.45 14.52A3.15 3.15 0 1 0 8.93 20.77Z"/></g></g>',
    nuit_nuageuse: '<mask id="@0" maskUnits="userSpaceOnUse" x="-12" y="-12" width="48" height="48"><rect x="-12" y="-12" width="48" height="48" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="4.55"><path d="M8.93 20.77H18.82A3.6 3.6 0 0 0 18.94 13.58A4.95 4.95 0 0 0 9.45 14.52A3.15 3.15 0 1 0 8.93 20.77Z"/></g></mask><g class="vl"><g mask="url(#@0)"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M8.61 4.23A5.6 5.6 0 1 0 14.59 11.31A4.82 4.82 0 0 1 8.61 4.23Z"/></g><polygon points="16.4 2.5 16.83 3.87 18.2 4.3 16.83 4.73 16.4 6.1 15.97 4.73 14.6 4.3 15.97 3.87"/></g></g><g class="vn"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M8.93 20.77H18.82A3.6 3.6 0 0 0 18.94 13.58A4.95 4.95 0 0 0 9.45 14.52A3.15 3.15 0 1 0 8.93 20.77Z"/></g></g>',
    couvert: '<mask id="@0" maskUnits="userSpaceOnUse" x="-12" y="-12" width="48" height="48"><rect x="-12" y="-12" width="48" height="48" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="4.55"><path d="M5.04 19.26H15.49A3.8 3.8 0 0 0 15.61 11.66A5.22 5.22 0 0 0 5.59 12.66A3.32 3.32 0 1 0 5.04 19.26Z"/></g></mask><g class="vm"><g mask="url(#@0)"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M11.46 12.94H19.38A2.88 2.88 0 0 0 19.47 7.18A3.96 3.96 0 0 0 11.88 7.94A2.52 2.52 0 1 0 11.46 12.94Z"/></g></g></g><g class="vn"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M5.04 19.26H15.49A3.8 3.8 0 0 0 15.61 11.66A5.22 5.22 0 0 0 5.59 12.66A3.32 3.32 0 1 0 5.04 19.26Z"/></g></g>',
    brouillard: '<g class="vn"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M6.54 14.06H16.99A3.8 3.8 0 0 0 17.11 6.46A5.22 5.22 0 0 0 7.09 7.46A3.32 3.32 0 1 0 6.54 14.06Z"/></g></g><g class="vb"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M4.6 17.4L16.8 17.4"/><path d="M7.6 20.8L19.6 20.8"/></g></g>',
    bruine: '<g class="vn"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="ve"><circle cx="7.8" cy="18.4" r="1"/><circle cx="12" cy="18.4" r="1"/><circle cx="16.2" cy="18.4" r="1"/><circle cx="9.9" cy="21.5" r="1"/><circle cx="14.1" cy="21.5" r="1"/></g>',
    pluie: '<g class="vm"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M8.94 17.5L7.86 20.9"/><path d="M12.84 18.9L11.76 22.3"/><path d="M16.74 17.5L15.66 20.9"/></g></g>',
    pluie_forte: '<g class="vm"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="1.93"><path d="M7.6 17.3L6.2 21.7"/><path d="M11.3 18.3L9.9 22.7"/><path d="M15 17.3L13.6 21.7"/><path d="M18.7 18.3L17.3 22.7"/></g></g>',
    pluie_verglacante: '<g class="vm"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M8.74 17.7L7.66 21.1"/><path d="M16.94 17.7L15.86 21.1"/></g></g><g class="vg"><circle cx="12.3" cy="18.2" r="1.15"/><circle cx="10.5" cy="21.6" r="1.15"/><circle cx="14.1" cy="21.6" r="1.15"/></g>',
    averses: '<mask id="@0" maskUnits="userSpaceOnUse" x="-12" y="-12" width="48" height="48"><rect x="-12" y="-12" width="48" height="48" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="4.55"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></mask><g class="vs"><g mask="url(#@0)"><g fill="none" stroke="currentColor" stroke-width="1.75"><circle cx="8" cy="8" r="3.2"/><path d="M13.1 8L14.9 8"/><path d="M11.61 11.61L12.88 12.88"/><path d="M8 13.1L8 14.9"/><path d="M4.39 11.61L3.12 12.88"/><path d="M2.9 8L1.1 8"/><path d="M4.39 4.39L3.12 3.12"/><path d="M8 2.9L8 1.1"/><path d="M11.61 4.39L12.88 3.12"/></g></g></g><g class="vm"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M12.14 17.6L11.06 21"/><path d="M16.14 18.9L15.06 22.3"/></g></g>',
    averses_nuit: '<mask id="@0" maskUnits="userSpaceOnUse" x="-12" y="-12" width="48" height="48"><rect x="-12" y="-12" width="48" height="48" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="4.55"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></mask><g class="vl"><g mask="url(#@0)"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M7.71 2.93A4.7 4.7 0 1 0 12.73 8.87A4.04 4.04 0 0 1 7.71 2.93Z"/></g></g></g><g class="vm"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M12.14 17.6L11.06 21"/><path d="M16.14 18.9L15.06 22.3"/></g></g>',
    averses_violentes: '<g class="vm"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="1.93"><path d="M7.98 16.9L7.22 19.3"/><path d="M11.98 16.9L11.22 19.3"/><path d="M15.98 16.9L15.22 19.3"/><path d="M9.98 20.1L9.22 22.5"/><path d="M13.98 20.1L13.22 22.5"/></g></g>',
    neige: '<g class="vn"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="vg"><g fill="none" stroke="currentColor" stroke-width="1.4"><path d="M7.2 17.25L7.2 20.55"/><path d="M8.63 18.07L5.77 19.72"/><path d="M8.63 19.72L5.77 18.07"/><path d="M12 19.45L12 22.75"/><path d="M13.43 20.28L10.57 21.93"/><path d="M13.43 21.93L10.57 20.28"/><path d="M16.8 17.25L16.8 20.55"/><path d="M18.23 18.07L15.37 19.72"/><path d="M18.23 19.72L15.37 18.07"/></g></g>',
    neige_forte: '<g class="vn"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="vg"><g fill="none" stroke="currentColor" stroke-width="1.22"><path d="M7.1 16.8L7.1 19.4"/><path d="M8.23 17.45L5.97 18.75"/><path d="M8.23 18.75L5.97 17.45"/><path d="M12 16.8L12 19.4"/><path d="M13.13 17.45L10.87 18.75"/><path d="M13.13 18.75L10.87 17.45"/><path d="M16.9 16.8L16.9 19.4"/><path d="M18.03 17.45L15.77 18.75"/><path d="M18.03 18.75L15.77 17.45"/><path d="M9.55 20.1L9.55 22.7"/><path d="M10.68 20.75L8.42 22.05"/><path d="M10.68 22.05L8.42 20.75"/><path d="M14.45 20.1L14.45 22.7"/><path d="M15.58 20.75L13.32 22.05"/><path d="M15.58 22.05L13.32 20.75"/></g></g>',
    grains_de_neige: '<g class="vn"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="vg"><circle cx="7.6" cy="18.4" r="1.1"/><circle cx="12" cy="18.4" r="1.1"/><circle cx="16.4" cy="18.4" r="1.1"/><circle cx="9.8" cy="21.5" r="1.1"/><circle cx="14.2" cy="21.5" r="1.1"/></g>',
    averses_neige: '<mask id="@0" maskUnits="userSpaceOnUse" x="-12" y="-12" width="48" height="48"><rect x="-12" y="-12" width="48" height="48" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="4.55"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></mask><g class="vs"><g mask="url(#@0)"><g fill="none" stroke="currentColor" stroke-width="1.75"><circle cx="8" cy="8" r="3.2"/><path d="M13.1 8L14.9 8"/><path d="M11.61 11.61L12.88 12.88"/><path d="M8 13.1L8 14.9"/><path d="M4.39 11.61L3.12 12.88"/><path d="M2.9 8L1.1 8"/><path d="M4.39 4.39L3.12 3.12"/><path d="M8 2.9L8 1.1"/><path d="M11.61 4.39L12.88 3.12"/></g></g></g><g class="vn"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></g><g class="vg"><g fill="none" stroke="currentColor" stroke-width="1.4"><path d="M11.3 17.6L11.3 20.8"/><path d="M12.69 18.4L9.91 20"/><path d="M12.69 20L9.91 18.4"/><path d="M16 19.2L16 22.4"/><path d="M17.39 20L14.61 21.6"/><path d="M17.39 21.6L14.61 20"/></g></g>',
    averses_neige_nuit: '<mask id="@0" maskUnits="userSpaceOnUse" x="-12" y="-12" width="48" height="48"><rect x="-12" y="-12" width="48" height="48" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="4.55"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></mask><g class="vl"><g mask="url(#@0)"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M7.71 2.93A4.7 4.7 0 1 0 12.73 8.87A4.04 4.04 0 0 1 7.71 2.93Z"/></g></g></g><g class="vn"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></g><g class="vg"><g fill="none" stroke="currentColor" stroke-width="1.4"><path d="M11.3 17.6L11.3 20.8"/><path d="M12.69 18.4L9.91 20"/><path d="M12.69 20L9.91 18.4"/><path d="M16 19.2L16 22.4"/><path d="M17.39 20L14.61 21.6"/><path d="M17.39 21.6L14.61 20"/></g></g>',
    orage: '<mask id="@0" maskUnits="userSpaceOnUse" x="-12" y="-12" width="48" height="48"><rect x="-12" y="-12" width="48" height="48" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="4.55"><polygon points="15.16 10.9 9.84 17.74 12.31 17.74 10.6 22.3 15.92 15.08 13.26 15.08"/></g></mask><g class="vm"><g mask="url(#@0)"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g></g><g class="vx"><g fill="none" stroke="currentColor" stroke-width="1.75"><polygon points="15.16 10.9 9.84 17.74 12.31 17.74 10.6 22.3 15.92 15.08 13.26 15.08"/></g></g>',
    orage_grele: '<mask id="@0" maskUnits="userSpaceOnUse" x="-12" y="-12" width="48" height="48"><rect x="-12" y="-12" width="48" height="48" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="4.55"><polygon points="15.16 10.9 9.84 17.74 12.31 17.74 10.6 22.3 15.92 15.08 13.26 15.08"/></g></mask><g class="vm"><g mask="url(#@0)"><g fill="none" stroke="currentColor" stroke-width="1.75"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g></g><g class="vx"><g fill="none" stroke="currentColor" stroke-width="1.75"><polygon points="15.16 10.9 9.84 17.74 12.31 17.74 10.6 22.3 15.92 15.08 13.26 15.08"/></g></g><g class="vg"><circle cx="6.4" cy="19" r="1.1"/><circle cx="7.8" cy="22" r="1.1"/><circle cx="17.9" cy="19.3" r="1.1"/><circle cx="16.5" cy="22.2" r="1.1"/></g>',
  } },
  duo: { box: '0 0 24 24', icons: {
    soleil: '<g class="vs"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><circle cx="12" cy="12" r="4.4"/></g><g fill="none" stroke="currentColor" stroke-width="1.6"><path d="M19.4 12L21.9 12"/><path d="M17.23 17.23L19 19"/><path d="M12 19.4L12 21.9"/><path d="M6.77 17.23L5 19"/><path d="M4.6 12L2.1 12"/><path d="M6.77 6.77L5 5"/><path d="M12 4.6L12 2.1"/><path d="M17.23 6.77L19 5"/></g></g>',
    lune: '<g class="vl"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M10.2 5.44A7.6 7.6 0 1 0 18.32 15.05A6.54 6.54 0 0 1 10.2 5.44Z"/></g><polygon points="18.6 4.1 19.15 5.85 20.9 6.4 19.15 6.95 18.6 8.7 18.05 6.95 16.3 6.4 18.05 5.85"/><polygon points="20.3 11 20.64 12.06 21.7 12.4 20.64 12.74 20.3 13.8 19.96 12.74 18.9 12.4 19.96 12.06"/></g>',
    eclaircies: '<mask id="@0" maskUnits="userSpaceOnUse" x="-12" y="-12" width="48" height="48"><rect x="-12" y="-12" width="48" height="48" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="4.4"><path d="M8.93 20.77H18.82A3.6 3.6 0 0 0 18.94 13.58A4.95 4.95 0 0 0 9.45 14.52A3.15 3.15 0 1 0 8.93 20.77Z"/></g></mask><g class="vs"><g mask="url(#@0)"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><circle cx="9.1" cy="9.6" r="3.7"/></g><g fill="none" stroke="currentColor" stroke-width="1.6"><path d="M15 9.6L17 9.6"/><path d="M13.27 13.77L14.69 15.19"/><path d="M9.1 15.5L9.1 17.5"/><path d="M4.93 13.77L3.51 15.19"/><path d="M3.2 9.6L1.2 9.6"/><path d="M4.93 5.43L3.51 4.01"/><path d="M9.1 3.7L9.1 1.7"/><path d="M13.27 5.43L14.69 4.01"/></g></g></g><g class="vn"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M8.93 20.77H18.82A3.6 3.6 0 0 0 18.94 13.58A4.95 4.95 0 0 0 9.45 14.52A3.15 3.15 0 1 0 8.93 20.77Z"/></g></g>',
    nuit_nuageuse: '<mask id="@0" maskUnits="userSpaceOnUse" x="-12" y="-12" width="48" height="48"><rect x="-12" y="-12" width="48" height="48" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="4.4"><path d="M8.93 20.77H18.82A3.6 3.6 0 0 0 18.94 13.58A4.95 4.95 0 0 0 9.45 14.52A3.15 3.15 0 1 0 8.93 20.77Z"/></g></mask><g class="vl"><g mask="url(#@0)"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M8.61 4.23A5.6 5.6 0 1 0 14.59 11.31A4.82 4.82 0 0 1 8.61 4.23Z"/></g><polygon points="16.4 2.5 16.83 3.87 18.2 4.3 16.83 4.73 16.4 6.1 15.97 4.73 14.6 4.3 15.97 3.87"/></g></g><g class="vn"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M8.93 20.77H18.82A3.6 3.6 0 0 0 18.94 13.58A4.95 4.95 0 0 0 9.45 14.52A3.15 3.15 0 1 0 8.93 20.77Z"/></g></g>',
    couvert: '<mask id="@0" maskUnits="userSpaceOnUse" x="-12" y="-12" width="48" height="48"><rect x="-12" y="-12" width="48" height="48" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="4.4"><path d="M5.04 19.26H15.49A3.8 3.8 0 0 0 15.61 11.66A5.22 5.22 0 0 0 5.59 12.66A3.32 3.32 0 1 0 5.04 19.26Z"/></g></mask><g class="vm"><g mask="url(#@0)"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M11.46 12.94H19.38A2.88 2.88 0 0 0 19.47 7.18A3.96 3.96 0 0 0 11.88 7.94A2.52 2.52 0 1 0 11.46 12.94Z"/></g></g></g><g class="vn"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M5.04 19.26H15.49A3.8 3.8 0 0 0 15.61 11.66A5.22 5.22 0 0 0 5.59 12.66A3.32 3.32 0 1 0 5.04 19.26Z"/></g></g>',
    brouillard: '<g class="vn"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M6.54 14.06H16.99A3.8 3.8 0 0 0 17.11 6.46A5.22 5.22 0 0 0 7.09 7.46A3.32 3.32 0 1 0 6.54 14.06Z"/></g></g><g class="vb"><g fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4.6 17.4L16.8 17.4"/><path d="M7.6 20.8L19.6 20.8"/></g></g>',
    bruine: '<g class="vn"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="ve"><circle cx="7.8" cy="18.4" r="1"/><circle cx="12" cy="18.4" r="1"/><circle cx="16.2" cy="18.4" r="1"/><circle cx="9.9" cy="21.5" r="1"/><circle cx="14.1" cy="21.5" r="1"/></g>',
    pluie: '<g class="vm"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="1.6"><path d="M8.94 17.5L7.86 20.9"/><path d="M12.84 18.9L11.76 22.3"/><path d="M16.74 17.5L15.66 20.9"/></g></g>',
    pluie_forte: '<g class="vm"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="1.76"><path d="M7.6 17.3L6.2 21.7"/><path d="M11.3 18.3L9.9 22.7"/><path d="M15 17.3L13.6 21.7"/><path d="M18.7 18.3L17.3 22.7"/></g></g>',
    pluie_verglacante: '<g class="vm"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="1.6"><path d="M8.74 17.7L7.66 21.1"/><path d="M16.94 17.7L15.86 21.1"/></g></g><g class="vg"><circle cx="12.3" cy="18.2" r="1.15"/><circle cx="10.5" cy="21.6" r="1.15"/><circle cx="14.1" cy="21.6" r="1.15"/></g>',
    averses: '<mask id="@0" maskUnits="userSpaceOnUse" x="-12" y="-12" width="48" height="48"><rect x="-12" y="-12" width="48" height="48" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="4.4"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></mask><g class="vs"><g mask="url(#@0)"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><circle cx="8" cy="8" r="3.2"/></g><g fill="none" stroke="currentColor" stroke-width="1.6"><path d="M13.1 8L14.9 8"/><path d="M11.61 11.61L12.88 12.88"/><path d="M8 13.1L8 14.9"/><path d="M4.39 11.61L3.12 12.88"/><path d="M2.9 8L1.1 8"/><path d="M4.39 4.39L3.12 3.12"/><path d="M8 2.9L8 1.1"/><path d="M11.61 4.39L12.88 3.12"/></g></g></g><g class="vm"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="1.6"><path d="M12.14 17.6L11.06 21"/><path d="M16.14 18.9L15.06 22.3"/></g></g>',
    averses_nuit: '<mask id="@0" maskUnits="userSpaceOnUse" x="-12" y="-12" width="48" height="48"><rect x="-12" y="-12" width="48" height="48" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="4.4"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></mask><g class="vl"><g mask="url(#@0)"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M7.71 2.93A4.7 4.7 0 1 0 12.73 8.87A4.04 4.04 0 0 1 7.71 2.93Z"/></g></g></g><g class="vm"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="1.6"><path d="M12.14 17.6L11.06 21"/><path d="M16.14 18.9L15.06 22.3"/></g></g>',
    averses_violentes: '<g class="vm"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="1.76"><path d="M7.98 16.9L7.22 19.3"/><path d="M11.98 16.9L11.22 19.3"/><path d="M15.98 16.9L15.22 19.3"/><path d="M9.98 20.1L9.22 22.5"/><path d="M13.98 20.1L13.22 22.5"/></g></g>',
    neige: '<g class="vn"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="vg"><g fill="none" stroke="currentColor" stroke-width="1.28"><path d="M7.2 17.25L7.2 20.55"/><path d="M8.63 18.07L5.77 19.72"/><path d="M8.63 19.72L5.77 18.07"/><path d="M12 19.45L12 22.75"/><path d="M13.43 20.28L10.57 21.93"/><path d="M13.43 21.93L10.57 20.28"/><path d="M16.8 17.25L16.8 20.55"/><path d="M18.23 18.07L15.37 19.72"/><path d="M18.23 19.72L15.37 18.07"/></g></g>',
    neige_forte: '<g class="vn"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="vg"><g fill="none" stroke="currentColor" stroke-width="1.12"><path d="M7.1 16.8L7.1 19.4"/><path d="M8.23 17.45L5.97 18.75"/><path d="M8.23 18.75L5.97 17.45"/><path d="M12 16.8L12 19.4"/><path d="M13.13 17.45L10.87 18.75"/><path d="M13.13 18.75L10.87 17.45"/><path d="M16.9 16.8L16.9 19.4"/><path d="M18.03 17.45L15.77 18.75"/><path d="M18.03 18.75L15.77 17.45"/><path d="M9.55 20.1L9.55 22.7"/><path d="M10.68 20.75L8.42 22.05"/><path d="M10.68 22.05L8.42 20.75"/><path d="M14.45 20.1L14.45 22.7"/><path d="M15.58 20.75L13.32 22.05"/><path d="M15.58 22.05L13.32 20.75"/></g></g>',
    grains_de_neige: '<g class="vn"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="vg"><circle cx="7.6" cy="18.4" r="1.1"/><circle cx="12" cy="18.4" r="1.1"/><circle cx="16.4" cy="18.4" r="1.1"/><circle cx="9.8" cy="21.5" r="1.1"/><circle cx="14.2" cy="21.5" r="1.1"/></g>',
    averses_neige: '<mask id="@0" maskUnits="userSpaceOnUse" x="-12" y="-12" width="48" height="48"><rect x="-12" y="-12" width="48" height="48" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="4.4"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></mask><g class="vs"><g mask="url(#@0)"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><circle cx="8" cy="8" r="3.2"/></g><g fill="none" stroke="currentColor" stroke-width="1.6"><path d="M13.1 8L14.9 8"/><path d="M11.61 11.61L12.88 12.88"/><path d="M8 13.1L8 14.9"/><path d="M4.39 11.61L3.12 12.88"/><path d="M2.9 8L1.1 8"/><path d="M4.39 4.39L3.12 3.12"/><path d="M8 2.9L8 1.1"/><path d="M11.61 4.39L12.88 3.12"/></g></g></g><g class="vn"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></g><g class="vg"><g fill="none" stroke="currentColor" stroke-width="1.28"><path d="M11.3 17.6L11.3 20.8"/><path d="M12.69 18.4L9.91 20"/><path d="M12.69 20L9.91 18.4"/><path d="M16 19.2L16 22.4"/><path d="M17.39 20L14.61 21.6"/><path d="M17.39 21.6L14.61 20"/></g></g>',
    averses_neige_nuit: '<mask id="@0" maskUnits="userSpaceOnUse" x="-12" y="-12" width="48" height="48"><rect x="-12" y="-12" width="48" height="48" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="4.4"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></mask><g class="vl"><g mask="url(#@0)"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M7.71 2.93A4.7 4.7 0 1 0 12.73 8.87A4.04 4.04 0 0 1 7.71 2.93Z"/></g></g></g><g class="vn"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></g><g class="vg"><g fill="none" stroke="currentColor" stroke-width="1.28"><path d="M11.3 17.6L11.3 20.8"/><path d="M12.69 18.4L9.91 20"/><path d="M12.69 20L9.91 18.4"/><path d="M16 19.2L16 22.4"/><path d="M17.39 20L14.61 21.6"/><path d="M17.39 21.6L14.61 20"/></g></g>',
    orage: '<mask id="@0" maskUnits="userSpaceOnUse" x="-12" y="-12" width="48" height="48"><rect x="-12" y="-12" width="48" height="48" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="4.4"><polygon points="15.16 10.9 9.84 17.74 12.31 17.74 10.6 22.3 15.92 15.08 13.26 15.08"/></g></mask><g class="vm"><g mask="url(#@0)"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g></g><g class="vx"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><polygon points="15.16 10.9 9.84 17.74 12.31 17.74 10.6 22.3 15.92 15.08 13.26 15.08"/></g></g>',
    orage_grele: '<mask id="@0" maskUnits="userSpaceOnUse" x="-12" y="-12" width="48" height="48"><rect x="-12" y="-12" width="48" height="48" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="4.4"><polygon points="15.16 10.9 9.84 17.74 12.31 17.74 10.6 22.3 15.92 15.08 13.26 15.08"/></g></mask><g class="vm"><g mask="url(#@0)"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g></g><g class="vx"><g stroke="currentColor" stroke-width="1.6" fill-opacity=".32"><polygon points="15.16 10.9 9.84 17.74 12.31 17.74 10.6 22.3 15.92 15.08 13.26 15.08"/></g></g><g class="vg"><circle cx="6.4" cy="19" r="1.1"/><circle cx="7.8" cy="22" r="1.1"/><circle cx="17.9" cy="19.3" r="1.1"/><circle cx="16.5" cy="22.2" r="1.1"/></g>',
  } },
  badge: { box: '-4 -4 32 32', icons: {
    soleil: '<g class="vs"><g fill-opacity=".2"><rect x="-4" y="-4" width="32" height="32" rx="9"/></g></g><g class="vs"><g stroke="currentColor" stroke-width="1.1"><circle cx="12" cy="12" r="4.4"/></g><g fill="none" stroke="currentColor" stroke-width="2.1"><path d="M19.4 12L21.9 12"/><path d="M17.23 17.23L19 19"/><path d="M12 19.4L12 21.9"/><path d="M6.77 17.23L5 19"/><path d="M4.6 12L2.1 12"/><path d="M6.77 6.77L5 5"/><path d="M12 4.6L12 2.1"/><path d="M17.23 6.77L19 5"/></g></g>',
    lune: '<g class="vl"><g fill-opacity=".2"><rect x="-4" y="-4" width="32" height="32" rx="9"/></g></g><g class="vl"><g stroke="currentColor" stroke-width="1.1"><path d="M10.2 5.44A7.6 7.6 0 1 0 18.32 15.05A6.54 6.54 0 0 1 10.2 5.44Z"/></g><polygon points="18.6 4.1 19.15 5.85 20.9 6.4 19.15 6.95 18.6 8.7 18.05 6.95 16.3 6.4 18.05 5.85"/><polygon points="20.3 11 20.64 12.06 21.7 12.4 20.64 12.74 20.3 13.8 19.96 12.74 18.9 12.4 19.96 12.06"/></g>',
    eclaircies: '<g class="vs"><g fill-opacity=".2"><rect x="-4" y="-4" width="32" height="32" rx="9"/></g></g><mask id="@0" maskUnits="userSpaceOnUse" x="-20" y="-20" width="64" height="64"><rect x="-20" y="-20" width="64" height="64" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="3.9"><path d="M8.93 20.77H18.82A3.6 3.6 0 0 0 18.94 13.58A4.95 4.95 0 0 0 9.45 14.52A3.15 3.15 0 1 0 8.93 20.77Z"/></g></mask><g class="vs"><g mask="url(#@0)"><g stroke="currentColor" stroke-width="1.1"><circle cx="9.1" cy="9.6" r="3.7"/></g><g fill="none" stroke="currentColor" stroke-width="2.1"><path d="M15 9.6L17 9.6"/><path d="M13.27 13.77L14.69 15.19"/><path d="M9.1 15.5L9.1 17.5"/><path d="M4.93 13.77L3.51 15.19"/><path d="M3.2 9.6L1.2 9.6"/><path d="M4.93 5.43L3.51 4.01"/><path d="M9.1 3.7L9.1 1.7"/><path d="M13.27 5.43L14.69 4.01"/></g></g></g><g class="vn"><g stroke="currentColor" stroke-width="1.1"><path d="M8.93 20.77H18.82A3.6 3.6 0 0 0 18.94 13.58A4.95 4.95 0 0 0 9.45 14.52A3.15 3.15 0 1 0 8.93 20.77Z"/></g></g>',
    nuit_nuageuse: '<g class="vl"><g fill-opacity=".2"><rect x="-4" y="-4" width="32" height="32" rx="9"/></g></g><mask id="@0" maskUnits="userSpaceOnUse" x="-20" y="-20" width="64" height="64"><rect x="-20" y="-20" width="64" height="64" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="3.9"><path d="M8.93 20.77H18.82A3.6 3.6 0 0 0 18.94 13.58A4.95 4.95 0 0 0 9.45 14.52A3.15 3.15 0 1 0 8.93 20.77Z"/></g></mask><g class="vl"><g mask="url(#@0)"><g stroke="currentColor" stroke-width="1.1"><path d="M8.61 4.23A5.6 5.6 0 1 0 14.59 11.31A4.82 4.82 0 0 1 8.61 4.23Z"/></g><polygon points="16.4 2.5 16.83 3.87 18.2 4.3 16.83 4.73 16.4 6.1 15.97 4.73 14.6 4.3 15.97 3.87"/></g></g><g class="vn"><g stroke="currentColor" stroke-width="1.1"><path d="M8.93 20.77H18.82A3.6 3.6 0 0 0 18.94 13.58A4.95 4.95 0 0 0 9.45 14.52A3.15 3.15 0 1 0 8.93 20.77Z"/></g></g>',
    couvert: '<g class="vm"><g fill-opacity=".2"><rect x="-4" y="-4" width="32" height="32" rx="9"/></g></g><mask id="@0" maskUnits="userSpaceOnUse" x="-20" y="-20" width="64" height="64"><rect x="-20" y="-20" width="64" height="64" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="3.9"><path d="M5.04 19.26H15.49A3.8 3.8 0 0 0 15.61 11.66A5.22 5.22 0 0 0 5.59 12.66A3.32 3.32 0 1 0 5.04 19.26Z"/></g></mask><g class="vm"><g mask="url(#@0)"><g stroke="currentColor" stroke-width="1.1"><path d="M11.46 12.94H19.38A2.88 2.88 0 0 0 19.47 7.18A3.96 3.96 0 0 0 11.88 7.94A2.52 2.52 0 1 0 11.46 12.94Z"/></g></g></g><g class="vn"><g stroke="currentColor" stroke-width="1.1"><path d="M5.04 19.26H15.49A3.8 3.8 0 0 0 15.61 11.66A5.22 5.22 0 0 0 5.59 12.66A3.32 3.32 0 1 0 5.04 19.26Z"/></g></g>',
    brouillard: '<g class="vb"><g fill-opacity=".2"><rect x="-4" y="-4" width="32" height="32" rx="9"/></g></g><g class="vn"><g stroke="currentColor" stroke-width="1.1"><path d="M6.54 14.06H16.99A3.8 3.8 0 0 0 17.11 6.46A5.22 5.22 0 0 0 7.09 7.46A3.32 3.32 0 1 0 6.54 14.06Z"/></g></g><g class="vb"><g fill="none" stroke="currentColor" stroke-width="2.1"><path d="M4.6 17.4L16.8 17.4"/><path d="M7.6 20.8L19.6 20.8"/></g></g>',
    bruine: '<g class="ve"><g fill-opacity=".2"><rect x="-4" y="-4" width="32" height="32" rx="9"/></g></g><g class="vn"><g stroke="currentColor" stroke-width="1.1"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="ve"><circle cx="7.8" cy="18.4" r="1"/><circle cx="12" cy="18.4" r="1"/><circle cx="16.2" cy="18.4" r="1"/><circle cx="9.9" cy="21.5" r="1"/><circle cx="14.1" cy="21.5" r="1"/></g>',
    pluie: '<g class="ve"><g fill-opacity=".2"><rect x="-4" y="-4" width="32" height="32" rx="9"/></g></g><g class="vm"><g stroke="currentColor" stroke-width="1.1"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="2.1"><path d="M8.94 17.5L7.86 20.9"/><path d="M12.84 18.9L11.76 22.3"/><path d="M16.74 17.5L15.66 20.9"/></g></g>',
    pluie_forte: '<g class="ve"><g fill-opacity=".2"><rect x="-4" y="-4" width="32" height="32" rx="9"/></g></g><g class="vm"><g stroke="currentColor" stroke-width="1.1"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="2.31"><path d="M7.6 17.3L6.2 21.7"/><path d="M11.3 18.3L9.9 22.7"/><path d="M15 17.3L13.6 21.7"/><path d="M18.7 18.3L17.3 22.7"/></g></g>',
    pluie_verglacante: '<g class="vg"><g fill-opacity=".2"><rect x="-4" y="-4" width="32" height="32" rx="9"/></g></g><g class="vm"><g stroke="currentColor" stroke-width="1.1"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="2.1"><path d="M8.74 17.7L7.66 21.1"/><path d="M16.94 17.7L15.86 21.1"/></g></g><g class="vg"><circle cx="12.3" cy="18.2" r="1.15"/><circle cx="10.5" cy="21.6" r="1.15"/><circle cx="14.1" cy="21.6" r="1.15"/></g>',
    averses: '<g class="ve"><g fill-opacity=".2"><rect x="-4" y="-4" width="32" height="32" rx="9"/></g></g><mask id="@0" maskUnits="userSpaceOnUse" x="-20" y="-20" width="64" height="64"><rect x="-20" y="-20" width="64" height="64" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="3.9"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></mask><g class="vs"><g mask="url(#@0)"><g stroke="currentColor" stroke-width="1.1"><circle cx="8" cy="8" r="3.2"/></g><g fill="none" stroke="currentColor" stroke-width="2.1"><path d="M13.1 8L14.9 8"/><path d="M11.61 11.61L12.88 12.88"/><path d="M8 13.1L8 14.9"/><path d="M4.39 11.61L3.12 12.88"/><path d="M2.9 8L1.1 8"/><path d="M4.39 4.39L3.12 3.12"/><path d="M8 2.9L8 1.1"/><path d="M11.61 4.39L12.88 3.12"/></g></g></g><g class="vm"><g stroke="currentColor" stroke-width="1.1"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="2.1"><path d="M12.14 17.6L11.06 21"/><path d="M16.14 18.9L15.06 22.3"/></g></g>',
    averses_nuit: '<g class="ve"><g fill-opacity=".2"><rect x="-4" y="-4" width="32" height="32" rx="9"/></g></g><mask id="@0" maskUnits="userSpaceOnUse" x="-20" y="-20" width="64" height="64"><rect x="-20" y="-20" width="64" height="64" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="3.9"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></mask><g class="vl"><g mask="url(#@0)"><g stroke="currentColor" stroke-width="1.1"><path d="M7.71 2.93A4.7 4.7 0 1 0 12.73 8.87A4.04 4.04 0 0 1 7.71 2.93Z"/></g></g></g><g class="vm"><g stroke="currentColor" stroke-width="1.1"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="2.1"><path d="M12.14 17.6L11.06 21"/><path d="M16.14 18.9L15.06 22.3"/></g></g>',
    averses_violentes: '<g class="ve"><g fill-opacity=".2"><rect x="-4" y="-4" width="32" height="32" rx="9"/></g></g><g class="vm"><g stroke="currentColor" stroke-width="1.1"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="ve"><g fill="none" stroke="currentColor" stroke-width="2.31"><path d="M7.98 16.9L7.22 19.3"/><path d="M11.98 16.9L11.22 19.3"/><path d="M15.98 16.9L15.22 19.3"/><path d="M9.98 20.1L9.22 22.5"/><path d="M13.98 20.1L13.22 22.5"/></g></g>',
    neige: '<g class="vg"><g fill-opacity=".2"><rect x="-4" y="-4" width="32" height="32" rx="9"/></g></g><g class="vn"><g stroke="currentColor" stroke-width="1.1"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="vg"><g fill="none" stroke="currentColor" stroke-width="1.68"><path d="M7.2 17.25L7.2 20.55"/><path d="M8.63 18.07L5.77 19.72"/><path d="M8.63 19.72L5.77 18.07"/><path d="M12 19.45L12 22.75"/><path d="M13.43 20.28L10.57 21.93"/><path d="M13.43 21.93L10.57 20.28"/><path d="M16.8 17.25L16.8 20.55"/><path d="M18.23 18.07L15.37 19.72"/><path d="M18.23 19.72L15.37 18.07"/></g></g>',
    neige_forte: '<g class="vg"><g fill-opacity=".2"><rect x="-4" y="-4" width="32" height="32" rx="9"/></g></g><g class="vn"><g stroke="currentColor" stroke-width="1.1"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="vg"><g fill="none" stroke="currentColor" stroke-width="1.47"><path d="M7.1 16.8L7.1 19.4"/><path d="M8.23 17.45L5.97 18.75"/><path d="M8.23 18.75L5.97 17.45"/><path d="M12 16.8L12 19.4"/><path d="M13.13 17.45L10.87 18.75"/><path d="M13.13 18.75L10.87 17.45"/><path d="M16.9 16.8L16.9 19.4"/><path d="M18.03 17.45L15.77 18.75"/><path d="M18.03 18.75L15.77 17.45"/><path d="M9.55 20.1L9.55 22.7"/><path d="M10.68 20.75L8.42 22.05"/><path d="M10.68 22.05L8.42 20.75"/><path d="M14.45 20.1L14.45 22.7"/><path d="M15.58 20.75L13.32 22.05"/><path d="M15.58 22.05L13.32 20.75"/></g></g>',
    grains_de_neige: '<g class="vg"><g fill-opacity=".2"><rect x="-4" y="-4" width="32" height="32" rx="9"/></g></g><g class="vn"><g stroke="currentColor" stroke-width="1.1"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g><g class="vg"><circle cx="7.6" cy="18.4" r="1.1"/><circle cx="12" cy="18.4" r="1.1"/><circle cx="16.4" cy="18.4" r="1.1"/><circle cx="9.8" cy="21.5" r="1.1"/><circle cx="14.2" cy="21.5" r="1.1"/></g>',
    averses_neige: '<g class="vg"><g fill-opacity=".2"><rect x="-4" y="-4" width="32" height="32" rx="9"/></g></g><mask id="@0" maskUnits="userSpaceOnUse" x="-20" y="-20" width="64" height="64"><rect x="-20" y="-20" width="64" height="64" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="3.9"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></mask><g class="vs"><g mask="url(#@0)"><g stroke="currentColor" stroke-width="1.1"><circle cx="8" cy="8" r="3.2"/></g><g fill="none" stroke="currentColor" stroke-width="2.1"><path d="M13.1 8L14.9 8"/><path d="M11.61 11.61L12.88 12.88"/><path d="M8 13.1L8 14.9"/><path d="M4.39 11.61L3.12 12.88"/><path d="M2.9 8L1.1 8"/><path d="M4.39 4.39L3.12 3.12"/><path d="M8 2.9L8 1.1"/><path d="M11.61 4.39L12.88 3.12"/></g></g></g><g class="vn"><g stroke="currentColor" stroke-width="1.1"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></g><g class="vg"><g fill="none" stroke="currentColor" stroke-width="1.68"><path d="M11.3 17.6L11.3 20.8"/><path d="M12.69 18.4L9.91 20"/><path d="M12.69 20L9.91 18.4"/><path d="M16 19.2L16 22.4"/><path d="M17.39 20L14.61 21.6"/><path d="M17.39 21.6L14.61 20"/></g></g>',
    averses_neige_nuit: '<g class="vg"><g fill-opacity=".2"><rect x="-4" y="-4" width="32" height="32" rx="9"/></g></g><mask id="@0" maskUnits="userSpaceOnUse" x="-20" y="-20" width="64" height="64"><rect x="-20" y="-20" width="64" height="64" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="3.9"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></mask><g class="vl"><g mask="url(#@0)"><g stroke="currentColor" stroke-width="1.1"><path d="M7.71 2.93A4.7 4.7 0 1 0 12.73 8.87A4.04 4.04 0 0 1 7.71 2.93Z"/></g></g></g><g class="vn"><g stroke="currentColor" stroke-width="1.1"><path d="M9.29 15.91H18.3A3.28 3.28 0 0 0 18.41 9.36A4.51 4.51 0 0 0 9.76 10.22A2.87 2.87 0 1 0 9.29 15.91Z"/></g></g><g class="vg"><g fill="none" stroke="currentColor" stroke-width="1.68"><path d="M11.3 17.6L11.3 20.8"/><path d="M12.69 18.4L9.91 20"/><path d="M12.69 20L9.91 18.4"/><path d="M16 19.2L16 22.4"/><path d="M17.39 20L14.61 21.6"/><path d="M17.39 21.6L14.61 20"/></g></g>',
    orage: '<g class="vx"><g fill-opacity=".2"><rect x="-4" y="-4" width="32" height="32" rx="9"/></g></g><mask id="@0" maskUnits="userSpaceOnUse" x="-20" y="-20" width="64" height="64"><rect x="-20" y="-20" width="64" height="64" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="3.9"><polygon points="15.16 10.9 9.84 17.74 12.31 17.74 10.6 22.3 15.92 15.08 13.26 15.08"/></g></mask><g class="vm"><g mask="url(#@0)"><g stroke="currentColor" stroke-width="1.1"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g></g><g class="vx"><g stroke="currentColor" stroke-width="1.1"><polygon points="15.16 10.9 9.84 17.74 12.31 17.74 10.6 22.3 15.92 15.08 13.26 15.08"/></g></g>',
    orage_grele: '<g class="vx"><g fill-opacity=".2"><rect x="-4" y="-4" width="32" height="32" rx="9"/></g></g><mask id="@0" maskUnits="userSpaceOnUse" x="-20" y="-20" width="64" height="64"><rect x="-20" y="-20" width="64" height="64" fill="#fff"/><g fill="#000" stroke="#000" stroke-width="3.9"><polygon points="15.16 10.9 9.84 17.74 12.31 17.74 10.6 22.3 15.92 15.08 13.26 15.08"/></g></mask><g class="vm"><g mask="url(#@0)"><g stroke="currentColor" stroke-width="1.1"><path d="M6.54 14.76H16.99A3.8 3.8 0 0 0 17.11 7.16A5.22 5.22 0 0 0 7.09 8.16A3.32 3.32 0 1 0 6.54 14.76Z"/></g></g></g><g class="vx"><g stroke="currentColor" stroke-width="1.1"><polygon points="15.16 10.9 9.84 17.74 12.31 17.74 10.6 22.3 15.92 15.08 13.26 15.08"/></g></g><g class="vg"><circle cx="6.4" cy="19" r="1.1"/><circle cx="7.8" cy="22" r="1.1"/><circle cx="17.9" cy="19.3" r="1.1"/><circle cx="16.5" cy="22.2" r="1.1"/></g>',
  } },
};
// </icons>
// The colour of each role of the drawings above (their classes), from the palette of the theme.
const ICON_ROLE = { vs: 'soleil', vl: 'lune', vn: 'nuage', vm: 'nuageSombre', ve: 'eau', vg: 'neige', vx: 'eclair', vb: 'brume' };
// The names of the icons before the icon set (ADR 0045), still understood.
const ICON_ALIAS = { peuNuageux: 'eclaircies', nuitNuageuse: 'nuit_nuageuse', nuage: 'couvert', averse: 'averses', brume: 'brouillard' };
const ICON_ROOT = 'fill="currentColor" stroke-linecap="round" stroke-linejoin="round"';
const iconsColoured = new WeakMap(); // palette of the icons -> style:name -> the drawing with its colours
let iconSeq = 0;
// The drawing of an icon in the style of the context (P.iconStyle), coloured by its palette; null for a name it does not have.
// Each drawing gets ids of its own for its masks.
function iconDrawing(P, kind) {
  const style = ICON_SETS[P.iconStyle] ? P.iconStyle : 'meridien', set = ICON_SETS[style];
  const name = set.icons[kind] ? kind : ICON_ALIAS[kind];
  if (!name || !set.icons[name]) return null;
  let done = iconsColoured.get(P.icon);
  if (!done) iconsColoured.set(P.icon, (done = new Map()));
  const key = `${style}:${name}`;
  if (!done.has(key)) done.set(key, set.icons[name].replace(/ class="(v[a-z])"/g, (m, role) => `${m} color="${P.icon[ICON_ROLE[role]]}"`));
  const body = done.get(key);
  iconSeq = (iconSeq + 1) % 1e9;
  return { box: set.box, body: body.includes('@') ? body.replaceAll('@', `vi${iconSeq}-`) : body };
}
// The icon of a WMO weather code, as the Meridien screen chooses it; atNight gives the night one.
function wmoKind(code) {
  if (!num(code)) return null;
  if (code === 0) return 'soleil';
  if (code <= 2) return 'eclaircies';
  if (code === 45 || code === 48) return 'brouillard';
  if (code >= 51 && code <= 55) return 'bruine';
  if (code === 56 || code === 57 || code === 66 || code === 67) return 'pluie_verglacante';
  if (code === 65) return 'pluie_forte';
  if (code >= 51 && code <= 67) return 'pluie';
  if (code === 75) return 'neige_forte';
  if (code === 77) return 'grains_de_neige';
  if (code >= 71 && code <= 77) return 'neige';
  if (code === 80 || code === 81) return 'averses';
  if (code === 82) return 'averses_violentes';
  if (code === 85 || code === 86) return 'averses_neige';
  if (code === 96 || code === 99) return 'orage_grele';
  if (code >= 95) return 'orage';
  return 'couvert';
}
const NIGHT_ICON = { soleil: 'lune', eclaircies: 'nuit_nuageuse', averses: 'averses_nuit', averses_neige: 'averses_neige_nuit' };
const atNight = (kind, isDay) => (isDay === false && NIGHT_ICON[kind]) || kind;
const icon = (P, kind, s) => {
  const d = kind ? iconDrawing(P, kind) : null;
  return d ? `<svg width="${s}" height="${s}" viewBox="${d.box}" ${ICON_ROOT} aria-hidden="true">${d.body}</svg>` : `<span style="display:inline-block;width:${s}px;height:${s}px"></span>`;
};
// The same, inside a drawing (the curve of the hours), centred on (cx, cy).
const iconAt = (P, kind, cx, cy, s) => {
  const d = kind ? iconDrawing(P, kind) : null;
  return d ? `<svg x="${r1(cx - s / 2)}" y="${r1(cy - s / 2)}" width="${s}" height="${s}" viewBox="${d.box}" ${ICON_ROOT}>${d.body}</svg>` : '';
};
const GLYPHS = {
  repere: '<path d="M12 21 s-7 -6.5 -7 -11 a7 7 0 0 1 14 0 c0 4.5 -7 11 -7 11 Z"/><circle cx="12" cy="10" r="2.5"/>',
  parapluie: '<path d="M3 12 a9 9 0 0 1 18 0 Z"/><path d="M12 12 v6 a2 2 0 0 1 -4 0"/>',
  danger: '<path d="M12 3 L22 20 H2 Z"/><line x1="12" y1="9" x2="12" y2="14"/><circle cx="12" cy="17" r="0.6"/>',
  vent: '<path d="M3 8 h10 a3 3 0 1 0 -3 -3"/><path d="M3 13 h15 a3 3 0 1 1 -3 3"/><path d="M3 18 h6"/>',
  vagues: '<path d="M3 8 q2.25 -3 4.5 0 t4.5 0 t4.5 0 t4.5 0"/><path d="M3 13 q2.25 -3 4.5 0 t4.5 0 t4.5 0 t4.5 0"/><path d="M3 18 q2.25 -3 4.5 0 t4.5 0 t4.5 0 t4.5 0"/>',
  maree: '<path d="M3 15 q2.25 -3 4.5 0 t4.5 0 t4.5 0 t4.5 0"/><line x1="12" y1="3" x2="12" y2="11"/><polyline points="9,6 12,3 15,6"/>',
  planche: '<ellipse cx="12" cy="12" rx="3.6" ry="10" transform="rotate(35 12 12)"/><line x1="7" y1="7" x2="17" y2="17"/>',
  voilier: '<path d="M12 3 V17"/><path d="M12 3 L19 14 H12"/><path d="M10 6 L5 14 H10"/><path d="M3 18 H21 L19 21 H5 Z"/>',
  cerfVolant: '<polygon points="12,2 19,10 12,22 5,10"/><line x1="12" y1="2" x2="12" y2="22"/><line x1="5" y1="10" x2="19" y2="10"/>',
  seau: '<path d="M5 9 h14 l-1.6 11 H6.6 Z"/><path d="M8 9 a4 4 0 0 1 8 0"/>',
  horloge: '<circle cx="12" cy="12" r="9"/><polyline points="12,7 12,12 15.5,14"/>',
  jauge: '<path d="M4 17 a8 8 0 1 1 16 0"/><line x1="12" y1="17" x2="16" y2="10"/>',
  accueil: '<path d="M3 11 L12 3 L21 11"/><path d="M5 10 V20 H19 V10"/>',
  goutte: '<path d="M12 3 C12 3 5 11 5 15 a7 7 0 0 0 14 0 C19 11 12 3 12 3 Z"/>',
  ok: '<circle cx="12" cy="12" r="9"/><polyline points="8,12.5 11,15.5 16,9"/>',
  info: '<circle cx="12" cy="12" r="9"/><line x1="12" y1="11" x2="12" y2="16.5"/><circle cx="12" cy="7.8" r="0.6"/>',
  onde: '<polyline points="2,12 6,12 8,6 11,19 14,4 16,15 18,12 22,12"/>',
  eclair: '<polygon points="13,2 5,14 11,14 10,22 19,9 13,9"/>',
  lien: '<path d="M14 4 h6 v6"/><line x1="20" y1="4" x2="11" y2="13"/><path d="M18 14 v5 a1 1 0 0 1 -1 1 H5 a1 1 0 0 1 -1 -1 V7 a1 1 0 0 1 1 -1 h5"/>',
  flocon: '<line x1="12" y1="3" x2="12" y2="21"/><line x1="4.2" y1="7.5" x2="19.8" y2="16.5"/><line x1="4.2" y1="16.5" x2="19.8" y2="7.5"/>',
  thermo: '<path d="M10 14 V5 a2 2 0 0 1 4 0 v9 a4 4 0 1 1 -4 0 Z"/>',
  montagne: '<path d="M2 20 L9 7 L13 13 L16 9 L22 20 Z"/>',
  flamme: '<path d="M12 3 c1 4 6 6 6 11 a6 6 0 0 1 -12 0 c0 -3 2 -4 2 -6 c1.5 1 2 2 2 3 c0 -3 1 -5 2 -8 Z"/>',
  feuille: '<path d="M5 19 C5 10 11 4 20 4 C20 13 14 19 5 19 Z"/><line x1="5" y1="19" x2="13" y2="11"/>',
  secheresse: '<path d="M12 3 C12 3 5 11 5 15 a7 7 0 0 0 14 0 C19 11 12 3 12 3 Z"/><line x1="4" y1="4" x2="20" y2="20"/>',
  fleur: '<circle cx="12" cy="7" r="3"/><circle cx="17" cy="12" r="3"/><circle cx="12" cy="17" r="3"/><circle cx="7" cy="12" r="3"/><circle cx="12" cy="12" r="1.4"/>',
  brume: '<line x1="4" y1="9" x2="20" y2="9"/><line x1="3" y1="13" x2="21" y2="13"/><line x1="5" y1="17" x2="19" y2="17"/>',
};
const glyph = (name, s, c, sw = 2) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="${c}" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" style="flex:none" aria-hidden="true">${GLYPHS[name] || ''}</svg>`;
// Wind arrow: it points where the wind GOES.
const arrow = (s, fromDeg, c) => `<svg width="${s}" height="${s}" viewBox="-1.1 -1.1 2.2 2.2" style="flex:none" aria-hidden="true"><g transform="rotate(${(fromDeg + 180) % 360})"><path d="M0 -1 L0.62 0.85 L0 0.42 L-0.62 0.85 Z" fill="${c}"/></g></svg>`;
const ACTIVITY_GLYPH = { surf: 'planche', kite: 'cerfVolant', boat: 'voilier', shore_fishing: 'seau' };
const PAGE_GLYPH = { home: 'accueil', details: 'horloge', alerts: 'danger', sea: 'vagues', normal: 'jauge' };

// The providers named in the credits the data give, in the order of the list: the pages say who the values are from.
const PROVIDERS = ['Météo-France', 'Vigicrues', 'VigiEau', 'MeteoAlarm', 'Open-Meteo', 'Copernicus', 'SLF', 'Avalanche.report', 'EOT20', 'OpenStreetMap', 'USGS', 'EMSC', 'NOAA', 'ECMWF', 'DWD'];
function providers(...credits) {
  const text = credits.filter((c) => typeof c === 'string').join(' ');
  return PROVIDERS.filter((p) => text.includes(p));
}

// ---------------------------------------------------------------------------------------------------------- the context
// Everything a page needs to draw: the words, the colours, the width, the clock and the time zone of the place.
function makeContext(lang, dark, width, data, config) {
  const P = { ...(dark ? PALETTES.dark : PALETTES.light), iconStyle: config?.icon_style }; // the icons drawn in the style chosen
  const wide = width >= WIDE_PX;
  const pad = wide ? 22 : 16;
  const W = Math.max(240, Math.floor(width - 2 * pad));
  const point = data?.point || null;
  const offset = num(point?.utc_offset_s) ? point.utc_offset_s : -new Date().getTimezoneOffset() * 60;
  const now = Math.floor(Date.now() / 1000);
  const t = (key, vars = {}) => {
    const s = TEXT[lang]?.[key] ?? TEXT.en[key] ?? key;
    return typeof s === 'string' ? s.replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? '')) : s;
  };
  const fmt = (v, digits = 0) => {
    if (!num(v)) return '–';
    const s = digits ? String(Math.round(v * 10 ** digits) / 10 ** digits) : String(Math.round(v));
    return lang === 'fr' ? s.replace('.', ',') : s;
  };
  const local = (ts) => new Date((ts + offset) * 1000); // read with the getUTC* methods
  const hm = (ts) => {
    const d = local(ts), h = d.getUTCHours(), m = d.getUTCMinutes();
    return lang === 'fr' ? (m ? `${h}&nbsp;h&nbsp;${pad2(m)}` : `${h}&nbsp;h`) : `${pad2(h)}:${pad2(m)}`;
  };
  const hourOnly = (ts) => { const h = local(ts).getUTCHours(); return lang === 'fr' ? `${h} h` : `${pad2(h)}:00`; };
  const dayKey = (ts) => local(ts).toISOString().slice(0, 10);
  const dayName = (iso) => {
    const s = new Intl.DateTimeFormat(lang, { weekday: 'short', timeZone: 'UTC' }).format(new Date(`${iso}T12:00:00Z`)).replace('.', '');
    return s.charAt(0).toUpperCase() + s.slice(1);
  };
  const monthName = (m) => new Intl.DateTimeFormat(lang, { month: 'long', timeZone: 'UTC' }).format(new Date(Date.UTC(2001, m - 1, 15)));
  const sector = (deg) => Math.round((((deg % 360) + 360) % 360) / 45) % 8;
  const k = wide ? clamp(W / 937, 0.72, 1.1) : clamp(W / 327, 0.86, 1.12);
  const normals = data?.normals || null;
  const inFrance = normals?.status === 'available' || (typeof point?.weather_provider === 'string' && point.weather_provider.startsWith('Météo-France'));
  return { lang, P, wide, pad, W, k, now, offset, point, data, config, normals, inFrance, t, fmt, local, hm, hourOnly, dayKey, dayName, monthName, sector };
}

// ------------------------------------------------------------------------------------------------------------------ blocks
const sep = (m = '14px 0') => `<div class="sep" style="margin:${m}"></div>`;
const title = (c, g, text, src = '') => `<div class="titre">${g ? glyph(g, 15, c.P.t3) : ''}<span>${text}</span>${src ? `<span class="src">${src}</span>` : ''}</div>`;
function foot(c, names, extra = '') {
  if (!names.length) return extra ? `<div class="pied">${extra}</div>` : '';
  const colon = c.lang === 'fr' ? '&nbsp;: ' : ': ';
  return `<div class="pied">${c.t('data')}${colon}${names.map(esc).join(', ')}${extra} · ${c.t('relayed')}</div>`;
}

function sortedWarnings(c) {
  const list = c.data?.warnings?.vigilances;
  if (!Array.isArray(list)) return [];
  return list.filter((v) => LEVEL_RANK[v.level]).sort((a, b) => LEVEL_RANK[b.level] - LEVEL_RANK[a.level] || String(a.onset || '').localeCompare(String(b.onset || '')));
}
const isoTs = (s) => { const v = Date.parse(s); return Number.isFinite(v) ? Math.floor(v / 1000) : null; };
// One group per phenomenon: the highest level, the earliest start and the latest end.
function warningGroups(c) {
  const groups = new Map();
  for (const v of sortedWarnings(c)) {
    const key = v.phenomenon || 'other';
    const g = groups.get(key) || { phenomenon: key, level: v.level, onset: null, expires: null, open: false, items: [] };
    g.items.push(v);
    if (LEVEL_RANK[v.level] > LEVEL_RANK[g.level]) g.level = v.level;
    const on = isoTs(v.onset), ex = isoTs(v.expires);
    if (on !== null && (g.onset === null || on < g.onset)) g.onset = on;
    if (ex === null) g.open = true; else if (g.expires === null || ex > g.expires) g.expires = ex;
    groups.set(key, g);
  }
  return [...groups.values()].sort((a, b) => LEVEL_RANK[b.level] - LEVEL_RANK[a.level]);
}

function header(c, onAlerts) {
  const w = c.data?.warnings;
  const level = w?.max_level;
  const fs = r1((c.wide ? 24 : 18) * Math.min(c.k, 1));
  const name = esc(c.config.name || placeName(c.data?.title));
  let right;
  if (LEVEL_COLOUR[level]) {
    const col = c.P[LEVEL_COLOUR[level]], top = warningGroups(c)[0], phen = top && top.level === level ? PHENOMENON[c.lang][top.phenomenon] : null;
    const words = phen ? c.t('warning_of', { p: c.lang === 'fr' ? phen : phen.toLowerCase(), level: c.t(`level_${level}`) }) : c.t('warning', { level: c.t(`level_${level}`) });
    right = `<button class="pastille" data-go="alerts" style="background:${col};color:${c.P.sur};font-size:${c.wide ? 17 : 14}px">${glyph('danger', 16, c.P.sur, 2.4)}${words}</button>`;
  } else {
    const read = c.data?.read_utc;
    right = num(read) ? `<span class="maj">${c.t('updated', { t: c.hm(read) })}</span>` : '';
  }
  return { name: `<span class="lieu" style="font-size:${fs}px">${glyph('repere', fs, c.P.t3)}<span>${name}</span></span>`, right };
}
const placeName = (title) => (typeof title === 'string' ? title.replace(/^Vigie\s+/i, '') || title : 'Vigie');

function nav(c, pages, current) {
  const fs = c.wide ? 15 : 13, h = c.wide ? 38 : 30;
  return `<div class="nav" role="tablist">` + pages.map((p) => {
    const on = p === current;
    return `<button role="tab" aria-selected="${on}" data-go="${p}" class="${on ? 'actif' : ''}" style="font-size:${fs}px;height:${h}px">${c.wide ? glyph(PAGE_GLYPH[p], fs + 3, on ? c.P.sur : c.P.t3, 2.2) : ''}<span>${c.t(`page_${p}`)}</span></button>`;
  }).join('') + `</div>`;
}

// The moment: icon, temperature in its colour of the ramp, the condition, the feeling and the wind.
function moment(c, m, scale = 1) {
  const k = c.k * scale, ic = Math.round((c.wide ? 128 : 84) * k), ts = Math.round((c.wide ? 124 : 76) * k);
  const l1 = Math.max(17, Math.round((c.wide ? 27 : 20) * k)), l2 = Math.max(13, Math.round((c.wide ? 19 : 15) * k));
  const wind = num(m.wind) ? `<span class="vent" style="font-size:${l2}px">${num(m.deg) ? arrow(l2 + 3, m.deg, c.P.vent) : ''}<span><b style="color:${c.P.vent}">${c.fmt(m.wind)}</b>&nbsp;${esc(m.windUnit || 'km/h')}${num(m.gust) ? ` · ${c.t('gusts')}&nbsp;<b style="color:${c.P.vent}">${c.fmt(m.gust)}</b>` : ''}</span></span>` : '';
  return `<div class="moment">${icon(c.P, m.kind, ic)}` +
    (num(m.temp) ? `<span class="temp" style="font-size:${ts}px;color:${tempColour(c.P, m.temp)}">${c.fmt(m.temp)}°</span>` : '') +
    `<div class="txt">${m.label ? `<span class="l1" style="font-size:${l1}px">${esc(m.label)}</span>` : ''}` +
    (num(m.feels) ? `<span class="t2" style="font-size:${l2}px">${c.t('feels', { t: c.fmt(m.feels) })}</span>` : '') + wind + `</div></div>`;
}
function momentOfPoint(c) {
  const cur = c.point?.current || {};
  const hourly = currentHours(c);
  const coords = c.point?.coordinates;
  let isDay = hourly.length && typeof hourly[0].is_day === 'boolean' ? hourly[0].is_day : null;
  if (isDay === null && num(coords?.lat) && num(coords?.lon)) isDay = sunUp(coords.lat, coords.lon, c.now);
  const kind = atNight(wmoKind(cur.weather_code), isDay);
  let label = WMO[c.lang]?.[cur.weather_code] ?? null;
  if (isDay === false && cur.weather_code === 0) label = c.lang === 'fr' ? 'Nuit claire' : 'Clear night';
  return { kind, temp: cur.temp_c, feels: cur.feels_like_c, label, wind: cur.wind_kmh, gust: cur.gust_kmh, deg: cur.wind_deg };
}
// The hours of the answer from the current hour (API ADR 0040); none when the instance does not give them.
function currentHours(c) {
  const hours = c.point?.hourly;
  if (!Array.isArray(hours)) return [];
  const from = c.now - (c.now % 3600);
  return hours.filter((h) => h && num(h.time_utc) && h.time_utc >= from);
}

// The rain of the next hour by the nowcast of the place (PIAF in France: 5-minute rates, or 15-minute amounts): the minutes
// to the first rain, 0 when it rains now, -1 for none in the hour, null without a nowcast. The home page and the sentence of
// the Details page both read it, so that they never say two different things about the hour.
// The steps start at `rain_nowcast.start_utc`: a step already over is skipped (with Open-Meteo the first one always is) and the
// minutes count from now. An answer without it (an older API) counts from its first step.
function nowcastFirst(c) {
  const rn = c.point?.rain_nowcast;
  const rates = Array.isArray(rn?.rate_5m_mm_h) && rn.rate_5m_mm_h.length && rn.rate_5m_mm_h.every(num) ? rn.rate_5m_mm_h : null;
  const steps = Array.isArray(rn?.mm_per_step) && rn.mm_per_step.length && rn.mm_per_step.every(num) ? rn.mm_per_step : null;
  const series = rates || steps;
  if (!series) return null;
  const minutes = rates ? 5 : rn.interval_minutes || 15;
  const start = num(rn.start_utc) ? rn.start_utc : null;
  for (let i = 0; i < series.length; i++) {
    if (!(series[i] > 0)) continue;
    if (start === null) return i * minutes;
    const from = start + i * minutes * 60;
    if (from + minutes * 60 <= c.now) continue;
    return Math.max(0, Math.ceil((from - c.now) / 60));
  }
  return -1;
}

function warningPill(c, fs, g, inline) {
  const w = c.data?.warnings;
  const groups = warningGroups(c);
  const go = c.config._pages.includes('alerts') ? ' data-go="alerts"' : '';
  if (!groups.length) {
    const unknown = !w || !w.max_level || w.max_level === 'unknown';
    const words = unknown ? (w?.note ? c.t('no_coverage') : c.t('warnings_unknown')) : c.t('no_warning');
    return `<button class="puce"${go} style="font-size:${fs}px;background:transparent;border-color:${c.P.filet}">${glyph(unknown ? 'info' : 'ok', g, unknown ? c.P.t3 : c.P.vert, 2.2)}<span class="tx">${words}</span></button>`;
  }
  const top = groups[0], col = c.P[LEVEL_COLOUR[top.level]];
  const more = groups.length > 1 ? ` <span class="plus" style="background:${alpha(col, 0.25)};color:${col}">+${groups.length - 1}</span>` : '';
  const when = top.onset !== null && top.onset > c.now ? c.t('from', { t: c.hm(top.onset) }) : top.expires !== null && !top.open ? c.t('until', { t: c.hm(top.expires) }) : '';
  const det = when ? (inline ? ` <span class="det" style="font-size:${fs - 1}px">· ${when}</span>` : `<small>${when}</small>`) : '';
  return `<button class="puce"${go} style="font-size:${fs}px;background:${alpha(col, 0.2)};border-color:${alpha(col, 0.65)}">${glyph(PHENOMENON_GLYPH[top.phenomenon] || 'danger', g, col, 2.2)}<span class="tx">${esc(PHENOMENON[c.lang][top.phenomenon] || top.phenomenon)} · ${c.t(`level_${top.level}`).toLowerCase()}${more}${det}</span></button>`;
}

function days(c, list, n, o = {}) {
  const fs = Math.round((c.wide ? 18 : 14) * Math.min(c.k, 1.05)), ic = Math.round((c.wide ? 48 : 34) * Math.min(c.k, 1.05));
  const items = list.slice(0, n);
  if (!items.length) return '';
  return `<div class="jours" style="grid-template-columns:repeat(${items.length},1fr)">` + items.map((d, i) =>
    `<div class="jour${i === 0 ? ' auj' : ''}"><span class="n" style="font-size:${fs}px">${esc(d.name)}</span>${icon(c.P, d.kind, ic)}` +
    `<span style="font-size:${fs + 2}px"><span class="mx" style="color:${tempColour(c.P, d.max)}">${c.fmt(d.max)}°</span> <span class="mn" style="font-size:${fs}px">${c.fmt(d.min)}°</span></span>` +
    (o.noRain ? '' : num(d.mm) ? (d.mm >= 0.5 ? `<span class="gel" style="background:${rainColour(c.P, d.mm / 6)};color:${c.P.sur}">${c.fmt(d.mm)}&nbsp;mm</span>` : `<span class="gel" style="color:${c.P.t3}">${c.t('dry')}</span>`) : '') +
    `</div>`).join('') + `</div>`;
}

// ----------------------------------------------------------------------------------------------------------------- season
// Avalanches, drought, pollen and forest fires: a line only when there is something to say (thresholds below). The strongest
// line is a pill of the home page ("+n" for the others) that opens Alerts, where each one has its block.
const SEASON = { droughtFrom: 'alerte', fireFrom: 2, pollenFrom: 1 }; // pollen: the rule of the site, in grains per m³
const DROUGHT_RANK = { vigilance: 1, alerte: 2, alerte_renforcee: 3, crise: 4 };
// The colours of the site for these native levels (none of them is an official warning level of Vigie).
const DROUGHT_TONE = { vigilance: 'jaune', alerte: 'orange', alerte_renforcee: 'orange', crise: 'rouge' };
const avalancheTone = (l) => (l <= 1 ? 'vert' : l === 2 ? 'jaune' : l === 3 ? 'orange' : 'rouge');
const fireTone = (l) => (l <= 1 ? 'vert' : l === 2 ? 'jaune' : l === 3 ? 'orange' : 'rouge');
const TONE_RANK = { neutre: 0, vert: 0, jaune: 1, orange: 2, rouge: 3 };
const POLLEN_SPECIES = ['alder', 'birch', 'grass', 'mugwort', 'olive', 'ragweed'];
const AVALANCHE_ISSUER = { meteofrance_bra: 'Météo-France', slf_avalanche: 'SLF', euregio_avalanche: 'Avalanche.report' };
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const httpUrl = (u) => typeof u === 'string' && /^https?:\/\//.test(u);

// "demain", "après-demain", a day name, or nothing for today; then the hour when asked.
function dayWord(c, ts, withHour) {
  const d = Math.round((Date.parse(`${c.dayKey(ts)}T00:00:00Z`) - Date.parse(`${c.dayKey(c.now)}T00:00:00Z`)) / 86400000);
  const day = d === 0 ? '' : d === 1 ? c.t('tomorrow') : d === 2 ? c.t('day_after') : c.dayName(c.dayKey(ts)).toLowerCase();
  return [day, withHour ? c.hm(ts) : ''].filter(Boolean).join(' ');
}
const dateWord = (c, iso) => {
  const v = Date.parse(iso);
  return Number.isFinite(v) ? new Intl.DateTimeFormat(c.lang, { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(v)) : '';
};

// The species whose peak reaches the threshold over the days from today, the highest first.
function pollenPeaks(c, p) {
  const today = c.dayKey(c.now);
  const days = Array.isArray(p?.daily) ? p.daily.filter((d) => d && typeof d.date === 'string' && d.date >= today) : [];
  const out = [];
  for (const s of POLLEN_SPECIES) {
    const peak = Math.max(0, ...days.map((d) => d[`${s}_max_grains_m3`]).filter(num), num(p?.current?.[`${s}_grains_m3`]) ? p.current[`${s}_grains_m3`] : 0);
    if (peak >= SEASON.pollenFrom) out.push({ s, peak });
  }
  return { days: days.slice(0, 4), peaks: out.sort((a, b) => b.peak - a.peak) };
}

// The lines worth saying, the strongest first. Nothing is shown from an answer that is not there or not in season.
function seasonItems(c) {
  const pt = c.point || {}, out = [];
  const av = pt.avalanche, until = isoTs(av?.valid_until);
  if (av && av.status === 'active' && num(av.max_danger_level) && av.max_danger_level >= 1 && av.max_danger_level <= 5 && (until === null || until > c.now)) {
    const l = av.max_danger_level, word = c.t('aval_word')[l], issuer = AVALANCHE_ISSUER[av.covered_by];
    const problems = (Array.isArray(av.problem_types) ? av.problem_types : []).map((p) => TEXT[c.lang][`problem_${p}`]).filter(Boolean);
    out.push({
      id: 'avalanche', tone: avalancheTone(l), glyph: 'montagne', name: c.t('aval'), word: `${l} · ${word}`,
      pill: c.t('aval_pill', { l, w: word }), det: av.region_name ? esc(av.region_name) : '', scale: l, attribution: av.attribution,
      lines: [[av.region_name ? c.t('aval_sector', { n: esc(av.region_name) }) : '', problems.length ? cap(problems.join(', ')) : ''].filter(Boolean).join(' · '),
        until !== null ? c.t('aval_until', { t: dayWord(c, until, true) }) : ''],
      note: av.covered_by === 'meteofrance_bra' ? c.t('aval_note') : '',
      link: httpUrl(av.bulletin_url) ? [av.bulletin_url, issuer ? c.t('aval_link', { issuer }) : c.t('bulletin_plain')] : null,
    });
  }
  const dr = pt.drought;
  if (dr && dr.status === 'available' && DROUGHT_RANK[dr.max_level] >= DROUGHT_RANK[SEASON.droughtFrom]) {
    const seen = new Set();
    const zones = (Array.isArray(dr.zones) ? dr.zones : []).filter((z) => z && DROUGHT_RANK[z.level] >= DROUGHT_RANK[SEASON.droughtFrom] && TEXT.en[`water_${z.water_type}`])
      .filter((z) => { const k = `${z.water_type}|${z.level}`; if (seen.has(k)) return false; seen.add(k); return true; });
    const mixed = new Set(zones.map((z) => z.level)).size > 1;
    out.push({
      id: 'drought', tone: DROUGHT_TONE[dr.max_level], glyph: 'secheresse', name: c.t('drought'), word: c.t(`drought_${dr.max_level}`),
      pill: `${c.t('drought')} · ${c.t(`drought_${dr.max_level}`)}`, det: dr.restrictions_in_force === true ? c.t('restrictions') : '', attribution: dr.attribution,
      lines: [dr.restrictions_in_force === true ? c.t('restrictions_long') : ''],
      rows: zones.slice(0, 3).map((z) => ({ tone: DROUGHT_TONE[z.level], a: c.t(`water_${z.water_type}`), b: mixed ? c.t(`drought_${z.level}`) : '',
        c: z.decree_valid_until ? c.t('until_date', { d: dateWord(c, z.decree_valid_until) }) : '' })),
      link: ['https://vigieau.gouv.fr', c.t('drought_link')],
    });
  }
  const pollens = pt.air_quality?.pollens;
  const pol = pollenPeaks(c, pollens);
  if (pol.peaks.length) {
    const top = pol.peaks[0], name = c.t(`species_${top.s}`);
    out.push({
      id: 'pollen', tone: 'neutre', glyph: 'fleur', name: c.t('pollen'), word: cap(name), pill: c.t('pollen_pill', { s: name }),
      det: c.t('pollen_det', { p: c.fmt(top.peak, 1) }), pollen: { peaks: pol.peaks.slice(0, 3), days: pol.days }, note: c.t('pollen_note'),
      attribution: pollens.attribution,
    });
  }
  const ff = c.data?.forest_fire;
  if (ff && ff.status === 'available') {
    // day1 and day2 are the two days after the publication; a day already past is left out.
    const pub = isoTs(ff.reference_time), today = c.dayKey(c.now);
    const days = [ff.day1, ff.day2].map((d, i) => (d && num(d.level) && d.level >= 1 && d.level <= 4 ? { level: d.level, ts: pub !== null ? pub + (i + 1) * 86400 : null } : null))
      .filter((d) => d && (d.ts === null || c.dayKey(d.ts) >= today));
    const top = days.reduce((a, d) => (d.level > (a?.level || 0) ? d : a), null);
    if (top && top.level >= SEASON.fireFrom) {
      out.push({
        id: 'fire', tone: fireTone(top.level), glyph: 'flamme', name: c.t('fire'), word: c.t('fire_word')[top.level],
        pill: c.t('fire_pill', { w: c.t('fire_word')[top.level] }), det: top.ts !== null ? dayWord(c, top.ts, false) : '', fire: days,
        lines: [ff.department?.name ? c.t('dept', { n: esc(ff.department.name) }) : ''], note: c.t('fire_note'), attribution: ff.attribution,
      });
    }
  }
  return out.sort((a, b) => TONE_RANK[b.tone] - TONE_RANK[a.tone]);
}

const toneColour = (c, tone) => (tone === 'neutre' ? c.P.t2 : c.P[tone]);
// The home page: the strongest line, "+n" for the others; it opens Alerts. On a phone the detail goes when "+n" is there.
function seasonPill(c, fs, g, inline, pad = '') {
  const items = seasonItems(c);
  if (!items.length) return '';
  const it = items[0], col = toneColour(c, it.tone), neutral = it.tone === 'neutre', more = items.length - 1;
  const plus = more ? ` <span class="plus" style="background:${alpha(neutral ? c.P.t3 : col, 0.25)};color:${col}">+${more}</span>` : '';
  const det = it.det && !(more && inline) ? (inline ? ` <span class="det" style="font-size:${fs - 1}px">· ${it.det}</span>` : `<small>${it.det}</small>`) : '';
  const go = c.config._pages.includes('alerts') ? ' data-go="alerts"' : '';
  const bg = neutral ? 'transparent' : alpha(col, 0.2), bd = neutral ? c.P.filet : alpha(col, 0.65);
  return `<button class="puce"${go} style="font-size:${fs}px;${pad ? `padding:${pad};` : ''}background:${bg};border-color:${bd}">${glyph(it.glyph, g, col, 2.2)}<span class="tx">${it.pill}${plus}${det}</span></button>`;
}

function dangerScale(c, level, w) {
  const gap = 4, n = 5, cw = (w - gap * (n - 1)) / n, h = 10;
  let s = `<svg width="${w}" height="${h + 2}" viewBox="0 0 ${w} ${h + 2}" style="margin-top:6px" aria-hidden="true">`;
  for (let i = 1; i <= n; i++) s += `<rect x="${r1((i - 1) * (cw + gap))}" y="1" width="${r1(cw)}" height="${h}" rx="${h / 2}" fill="${c.P[avalancheTone(i)]}" opacity="${i === level ? 1 : 0.22}"/>`;
  return s + '</svg>';
}
// The peaks of the day per species, in a neutral colour: the model gives no health threshold and none is made up here.
function pollenDays(c, p, w) {
  if (!p.days.length) return '';
  const top = Math.max(...p.peaks.map((x) => x.peak)), nameW = c.wide ? 110 : 92, cw = (w - nameW) / p.days.length, fs = 12, bh = 22;
  const h = 18 + p.peaks.length * (bh + 18);
  let s = `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="margin-top:6px" aria-hidden="true">`;
  p.days.forEach((d, i) => { s += `<text x="${r1(nameW + i * cw + cw / 2)}" y="12" font-size="${fs}" font-weight="700" fill="${i === 0 && d.date === c.dayKey(c.now) ? c.P.accent : c.P.t3}" text-anchor="middle">${c.dayName(d.date)}</text>`; });
  p.peaks.forEach((x, j) => {
    const y0 = 22 + j * (bh + 18);
    s += `<text x="0" y="${y0 + bh - 4}" font-size="${fs + 1}" font-weight="700" fill="${c.P.texte}">${cap(c.t(`species_${x.s}`))}</text>`;
    p.days.forEach((d, i) => {
      const v = d[`${x.s}_max_grains_m3`], bw = Math.min(cw - 14, 34), cx = nameW + i * cw + cw / 2;
      if (!num(v)) return;
      const on = v >= SEASON.pollenFrom, hh = on ? Math.max(3, (v / top) * bh) : 2;
      s += `<rect x="${r1(cx - bw / 2)}" y="${r1(y0 + bh - hh)}" width="${r1(bw)}" height="${r1(hh)}" rx="3" fill="${c.P.t3}" opacity="${on ? 0.85 : 0.3}"/>` +
        `<text x="${r1(cx)}" y="${y0 + bh + 13}" font-size="${fs - 1}" font-weight="700" fill="${c.P.t3}" text-anchor="middle">${c.fmt(v, 1)}</text>`;
    });
  });
  return s + '</svg>';
}
function fireDays(c, days) {
  return `<div class="chips" style="margin-top:8px">` + days.map((d) => {
    const col = c.P[fireTone(d.level)], when = d.ts !== null ? dayWord(c, d.ts, false) : '';
    return `<span class="chip" style="font-size:13px;border-color:${alpha(col, 0.65)};background:${alpha(col, 0.16)}">${when ? `${cap(when)}${c.lang === 'fr' ? '&nbsp;: ' : ': '}` : ''}<b>${c.t('fire_word')[d.level]}</b></span>`;
  }).join('') + `</div>`;
}
// One line in full: its name and level in colour, what it is, and the official page.
function seasonEntry(c, it, w) {
  const col = toneColour(c, it.tone);
  let s = `<div class="frise-l" style="font-size:15px;margin-top:0">${glyph(it.glyph, 19, col, 2.2)}<span>${it.name}</span><span class="fin" style="font-weight:700;color:${col};font-size:14px">${it.word}</span></div>`;
  if (it.scale) s += dangerScale(c, it.scale, Math.min(w, 300));
  if (it.fire) s += fireDays(c, it.fire);
  for (const l of (it.lines || []).filter(Boolean)) s += `<div class="t2" style="font-size:13px;margin-top:5px">${l}</div>`;
  for (const r of it.rows || []) s += `<div class="lig" style="padding:3px 0;font-size:13px"><b style="color:${c.P[r.tone]}">${r.a}</b>${r.b ? `<span>${r.b}</span>` : ''}${r.c ? `<span class="t3" style="margin-left:auto">${r.c}</span>` : ''}</div>`;
  if (it.pollen) s += pollenDays(c, it.pollen, Math.min(w, 420));
  if (it.note) s += `<div class="note" style="margin-top:5px">${it.note}</div>`;
  if (it.link) s += `<div style="height:8px"></div><a class="lien" href="${esc(it.link[0])}" target="_blank" rel="noopener noreferrer">${glyph('lien', 15, c.P.t2, 2.2)}${it.link[1]}</a>`;
  return s;
}
function seasonBlock(c, items, w) {
  if (!items.length) return '';
  // The sources go on the line of the title only when they fit there whole; each line names its own source anyway, and the
  // foot of the page credits them all.
  const src = providers(...items.map((it) => it.attribution)).join(', ');
  return title(c, 'feuille', c.t('season'), src.length * 6.6 + 120 <= w ? src : '') +
    items.map((it, i) => (i ? sep('12px 0') : '') + seasonEntry(c, it, w)).join('');
}

// ------------------------------------------------------------------------------------------------------ hours, days, glow
// The columns of the hours and of the days are the Meridien screen's, tinted by their sun ("Lueur", chosen on the sketches of
// 2026-11-27): the tint of the sky at the top of the column, fading downwards; grey to gold with the share of sun, navy at night.
// The share of sun is 1 - the cloud cover of the hours of daylight, else a guess from the weather code.
const sunOfCode = (code) => (!num(code) ? 0.3 : code === 0 ? 0.9 : code === 1 ? 0.75 : code === 2 ? 0.5 : code === 3 ? 0.12 : code <= 48 ? 0.1 : 0.06);
function glow(P, sun, day) {
  const s = clamp(num(sun) ? sun : 0.3, 0, 1);
  const col = day ? mix(P.lueurCouvert, P.lueurSoleil, s) : P.lueurNuit;
  const a = (day ? 0.11 + 0.21 * s : 0.16) * P.lueurA;
  return `linear-gradient(to bottom,${alpha(col, Math.min(0.62, a * 2.3))} 0%,${alpha(col, a * 0.9)} 38%,${alpha(col, 0.03)} 100%)`;
}
const column = (c, left, width, height, bg, ring, radius) => `<div class="cl" style="left:${r1(left + 1)}px;width:${r1(Math.max(1, width - 2))}px;height:${height}px;border-radius:${radius}px;background:${bg}${ring ? `;box-shadow:inset 0 0 0 1.5px ${alpha(c.P.accent, 0.75)}` : ''}"></div>`;
const cells = (n, top, list, style = '') => `<div class="gr" style="top:${top}px;grid-template-columns:repeat(${n},minmax(0,1fr));${style}">${list.join('')}</div>`;
const section = (left, right = '') => `<div class="st"><b>${left}</b>${right ? `<span>${right}</span>` : ''}</div>`;
const uid = () => `v${Math.random().toString(36).slice(2, 8)}`;
const sum = (l) => l.reduce((a, b) => a + b, 0);
const vals = (list, key) => list.map((x) => x[key]).filter(num);
const gustColour = (P, v) => (v >= 80 ? P.orange : v >= 60 ? P.jaune : null);
const colonOf = (c) => (c.lang === 'fr' ? '&nbsp;: ' : ': ');
// A horizontal gradient through the colours of a list of temperatures, one stop in the middle of each column.
const tempStops = (P, id, temps, w) => `<linearGradient id="${id}" gradientUnits="userSpaceOnUse" x1="0" x2="${r1(w)}" y1="0" y2="0">` +
  temps.map((t, i) => `<stop offset="${r1(((i + 0.5) / temps.length) * 100)}%" stop-color="${tempColour(P, t)}"/>`).join('') + '</linearGradient>';
// Where the wind of some hours comes from: the mean of their vectors, weighted by the speed.
function windFrom(hours) {
  let x = 0, y = 0;
  for (const h of hours) if (num(h.wind_deg) && num(h.wind_kmh)) { x += Math.sin(rad(h.wind_deg)) * h.wind_kmh; y += Math.cos(rad(h.wind_deg)) * h.wind_kmh; }
  return x || y ? ((Math.atan2(x, y) * 180) / Math.PI + 360) % 360 : null;
}
function sunOfHours(hours) {
  const lit = hours.filter((h) => h.is_day !== false && num(h.cloud_cover_pct));
  return lit.length ? lit.reduce((a, h) => a + 1 - h.cloud_cover_pct / 100, 0) / lit.length : null;
}
function isDayAt(c, h, mid = 1800) {
  if (typeof h.is_day === 'boolean') return h.is_day;
  const co = c.point?.coordinates;
  return num(co?.lat) && num(co?.lon) ? sunUp(co.lat, co.lon, h.time_utc + mid) : true;
}

// The hours from now by 1, 2 or 3 hours, as the width allows (a column of at least 34 px by hour, 28 px by 2 hours): the
// temperature at the start of the slot, its most significant weather (the highest WMO code), the sum of its rain, its highest
// chance of rain, its mean wind and its highest gust.
function hourSlots(c, w) {
  const hours = currentHours(c).filter((h) => num(h.temp_c));
  const step = w / 24 >= 34 ? 1 : w / 12 >= 28 ? 2 : 3;
  const n = Math.min(Math.floor(hours.length / step), 24 / step);
  if (n < 4) return null;
  const slots = [];
  for (let i = 0; i < n; i++) {
    const g = hours.slice(i * step, (i + 1) * step), codes = vals(g, 'weather_code'), code = codes.length ? Math.max(...codes) : null;
    const day = isDayAt(c, g[0], step * 1800);
    const mm = vals(g, 'precipitation_mm'), p = vals(g, 'precipitation_probability_pct'), wind = vals(g, 'wind_kmh'), gust = vals(g, 'wind_gust_kmh');
    slots.push({
      ts: g[0].time_utc, t: g[0].temp_c, kind: atNight(wmoKind(code), day), day, sun: day ? (sunOfHours(g) ?? sunOfCode(code)) : null,
      mm: mm.length ? sum(mm) : null, p: p.length ? Math.max(...p) : null, wind: wind.length ? sum(wind) / wind.length : null,
      gust: gust.length ? Math.max(...gust) : null, deg: windFrom(g),
    });
  }
  return { step, slots };
}

// The legend of the glow: night, then grey to gold.
const glowLegend = (c) => `${c.t('night')}<span class="ramp" style="background:linear-gradient(to right,${alpha(c.P.lueurNuit, 0.55)} 0%,${alpha(c.P.lueurNuit, 0.55)} 22%,${alpha(c.P.lueurCouvert, 0.45)} 30%,${alpha(c.P.lueurSoleil, 0.85)} 100%)"></span>${c.t('sun')}`;

// The next 24 hours: a column per slot, its icon, the temperature curve, the rain, its chance (when the forecast gives one), the
// wind and the gusts, the hour. `cut`: a flatter curve, that many px less (the card has a fixed size; at most 20).
function hoursBlock(c, w, cut = 0) {
  const S = hourSlots(c, w);
  if (!S) return '';
  const P = c.P, list = S.slots, n = list.length, cw = w / n, hasP = list.some((x) => num(x.p));
  const H = (hasP ? 206 : 192) - cut, yWind = (hasP ? 158 : 144) - cut;
  const temps = list.map((x) => x.t), mn = Math.min(...temps), mx = Math.max(...temps);
  const amp = Math.max(4, mx - mn), low = (mn + mx) / 2 - amp / 2; // at least 4 °C from top to bottom: 2° must not look like a fall
  const pts = temps.map((t, i) => ({ x: cw * (i + 0.5), y: 66 - cut - ((t - low) / amp) * (44 - cut) }));
  const id = uid(), top = 2.5 * S.step;
  let s = section(S.step === 1 ? c.t('hours_each') : c.t('hours_by', { n: S.step }), glowLegend(c)) +
    `<div class="cols" style="width:${r1(w)}px;height:${H}px">` + list.map((x, i) => column(c, cw * i, cw, H, glow(P, x.sun, x.day), i === 0, 9)).join('');
  s += cells(n, 2, list.map((x) => icon(P, x.kind, 24)));
  s += `<svg class="ov" style="top:26px" width="${r1(w)}" height="${74 - cut}" viewBox="0 0 ${r1(w)} ${74 - cut}" aria-hidden="true"><defs>${tempStops(P, id, temps, w)}</defs>` +
    `<path d="${smooth(pts)}" fill="none" stroke="url(#${id})" stroke-width="4" stroke-linecap="round"/></svg>`;
  s += list.map((x, i) => `<span class="lb" style="left:${r1(pts[i].x - 20)}px;top:${r1(pts[i].y + 7)}px;width:40px;font-size:13px;color:${tempColour(P, x.t)}">${c.fmt(x.t)}°</span>`).join('');
  s += cells(n, 104 - cut, list.map((x) => {
    const mm = num(x.mm) && x.mm >= 0.1 ? x.mm : 0, bh = mm ? Math.max(4, Math.sqrt(Math.min(mm, top) / top) * 22) : 0;
    return `<div class="pl">${mm ? `<span style="color:${P.pluieFaible}">${c.fmt(mm, mm < 10 ? 1 : 0)}</span><i style="height:${r1(bh)}px;background:${rainColour(P, mm / S.step)}"></i>` : ''}</div>`;
  }));
  if (hasP) s += cells(n, 144 - cut, list.map((x) => `<span class="pc">${num(x.p) ? `${x.p}&nbsp;%` : ''}</span>`));
  s += cells(n, yWind, list.map((x) => {
    const g = num(x.gust) ? gustColour(P, x.gust) : null;
    return `<div class="vt"><span>${num(x.deg) ? arrow(11, x.deg, P.vent) : ''}${c.fmt(x.wind)}</span>${num(x.gust) ? `<small style="color:${g || P.t3};font-weight:${g ? 700 : 400}">${c.fmt(x.gust)}</small>` : ''}</div>`;
  }));
  // Under 36 px a column has no room for "21:00" (English): the hour alone, the title says what the row is.
  s += cells(n, H - 17, list.map((x, i) => `<span class="hr" style="color:${i === 0 ? P.accent : P.t2}">${cw < 36 ? c.hourOnly(x.ts).replace(/:00$/, '') : c.hourOnly(x.ts)}</span>`));
  return `<div class="bloc">${s}</div></div>`;
}

// The hours of daylight of a local day, counted by 10 minutes (a day's share of sun is its hours of sunshine over them).
function daylightHours(c, date) {
  const co = c.point?.coordinates;
  if (!num(co?.lat) || !num(co?.lon)) return null;
  const start = Date.parse(`${date}T00:00:00Z`) / 1000 - c.offset;
  let n = 0;
  for (let k = 0; k < 144; k++) if (sunUp(co.lat, co.lon, start + k * 600 + 300)) n++;
  return n / 6;
}
// The hours of sunshine of a day as the sketches say them: "7 h", "30 min" ("0,5 h" in a narrow column), "0 h".
function sunHoursText(c, h, tight) {
  if (h === 0 || h >= 0.95) return c.t('sun_h', { n: c.fmt(Math.round(h)) });
  return tight ? c.t('sun_h', { n: c.fmt(Math.max(0.1, h), 1) }) : c.t('sun_min', { n: Math.max(10, Math.round(h * 6) * 10) });
}

// The days: a column per day, tinted by its sun; the highest and lowest as two curves around a band; the rain, its chance, the
// strongest wind (with the gusts and the direction where the hours of the answer cover the day). Where the answer gives the hours
// of sunshine of a day, they are said under its icon and tint its column (their share of the daylight); else the cloud cover of
// its hours, then its weather code. `cut`: flatter curves, that many px less (at most 40).
function daysBlock(c, w, nMax, cut = 0) {
  const P = c.P, today = c.dayKey(c.now);
  const list = (Array.isArray(c.point?.daily) ? c.point.daily : []).filter((d) => d && typeof d.date === 'string' && d.date >= today && num(d.tmax) && num(d.tmin)).slice(0, nMax);
  const n = list.length;
  if (n < 2) return '';
  // Under 50 px a column has no room for "Mar 24" nor "0,5 mm" beside its neighbour: the day name alone, smaller amounts.
  const cw = w / n, tight = cw < 50, hasP = list.some((d) => num(d.rain_probability_pct));
  const hasSun = list.some((d) => num(d.sunshine_h)), sh = hasSun ? 14 : 0, H = (hasP ? 250 : 236) + sh - cut; // sh: the line of sunshine
  const hourly = Array.isArray(c.point?.hourly) ? c.point.hourly.filter((h) => h && num(h.time_utc)) : [];
  const info = list.map((d) => {
    const hrs = hourly.filter((h) => c.dayKey(h.time_utc) === d.date && h.time_utc >= c.now - (c.now % 3600));
    const start = Date.parse(`${d.date}T00:00:00Z`) / 1000 - c.offset, need = Math.round((start + 86400 - Math.max(start, c.now - (c.now % 3600))) / 3600);
    const whole = need > 0 && hrs.length >= need - 1; // an hour of slack for the change of time
    // Today the gust is the one of the hours still to come, sometimes below the wind of the whole day: then none (not "33/24").
    const gusts = vals(hrs, 'wind_gust_kmh'), gust = whole && gusts.length ? Math.max(...gusts) : null;
    const light = num(d.sunshine_h) ? daylightHours(c, d.date) : null, share = light > 0 ? clamp(d.sunshine_h / light, 0, 1) : null;
    return { share, sun: share ?? sunOfHours(hrs.filter((h) => h.is_day === true)) ?? sunOfCode(d.code), gust: num(gust) && !(num(d.wind_max) && gust <= d.wind_max) ? gust : null, deg: whole ? windFrom(hrs) : null };
  });
  const lo = Math.floor(Math.min(...list.map((d) => d.tmin))) - 1, hi = Math.ceil(Math.max(...list.map((d) => d.tmax))) + 1;
  const yOf = (t) => 150 + sh - cut - ((t - lo) / (hi - lo)) * (76 - cut);
  const mx = list.map((d, i) => ({ x: cw * (i + 0.5), y: yOf(d.tmax) })), mn = list.map((d, i) => ({ x: cw * (i + 0.5), y: yOf(d.tmin) }));
  const maxD = smooth(mx), minD = smooth(mn), band = `${maxD}${smooth(mn.slice().reverse()).replace(/^M/, ' L')} Z`, id = uid();
  const mean = (key) => sum(list.map((d) => d[key])) / n;
  const legend = c.t(`days_legend${hasSun ? '_sun' : ''}${info.some((x) => num(x.gust)) ? '_gusts' : ''}`);
  const sunLine = (d, i) => {
    if (!num(d.sunshine_h)) return '<span class="ds"></span>';
    const k = info[i].share ?? d.sunshine_h / 9, col = k >= 0.45 ? P.icon.soleil : k >= 0.2 ? mix(P.t3, P.icon.soleil, 0.55) : P.t3;
    return `<span class="ds" style="color:${col}"><i style="background:${col}"></i>${sunHoursText(c, d.sunshine_h, tight)}</span>`;
  };
  let s = section(c.t('days_title', { n }), legend) + `<div class="cols" style="width:${r1(w)}px;height:${H}px">` +
    list.map((d, i) => column(c, cw * i, cw, H, glow(P, info[i].sun, true), i === 0, 10)).join('');
  s += cells(n, 3, list.map((d, i) => `<div class="dh"><b style="color:${i === 0 ? P.accent : P.texte}">${i === 0 ? c.t('today_short') : tight ? c.dayName(d.date) : `${c.dayName(d.date)} ${Number(d.date.slice(8))}`}</b>${icon(P, wmoKind(d.code), 30)}${hasSun ? sunLine(d, i) : ''}</div>`));
  const dot = (p, col) => `<circle cx="${r1(p.x)}" cy="${r1(p.y)}" r="3.75" fill="var(--vc-carte)" stroke="${col}" stroke-width="2.5"/>`;
  s += `<svg class="ov" style="top:0" width="${r1(w)}" height="${170 + sh - cut}" viewBox="0 0 ${r1(w)} ${170 + sh - cut}" aria-hidden="true"><defs>` +
    `<linearGradient id="${id}b" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${tempColour(P, mean('tmax'))}" stop-opacity=".2"/><stop offset="1" stop-color="${tempColour(P, mean('tmin'))}" stop-opacity=".16"/></linearGradient>` +
    tempStops(P, `${id}x`, list.map((d) => d.tmax), w) + tempStops(P, `${id}n`, list.map((d) => d.tmin), w) + '</defs>' +
    `<path d="${band}" fill="url(#${id}b)"/><path d="${maxD}" fill="none" stroke="url(#${id}x)" stroke-width="4" stroke-linecap="round"/>` +
    `<path d="${minD}" fill="none" stroke="url(#${id}n)" stroke-width="3" stroke-linecap="round"/>` +
    list.map((d, i) => dot(mx[i], tempColour(P, d.tmax)) + dot(mn[i], tempColour(P, d.tmin))).join('') + '</svg>';
  s += list.map((d, i) => `<span class="lb" style="left:${r1(mx[i].x - 22)}px;top:${r1(mx[i].y - 26)}px;width:44px;font-size:15px;color:${tempColour(P, d.tmax)}">${c.fmt(d.tmax)}°</span>` +
    `<span class="lb" style="left:${r1(mn[i].x - 22)}px;top:${r1(mn[i].y + 7)}px;width:44px;font-size:13px;font-weight:500;color:${tempColour(P, d.tmin)}">${c.fmt(d.tmin)}°</span>`).join('');
  s += cells(n, 176 + sh - cut, list.map((d, i) => {
    const mm = num(d.rain_mm) ? d.rain_mm : null, wet = mm !== null && mm >= 0.5;
    const bh = mm > 0 ? Math.max(4, Math.sqrt(Math.min(mm, 30) / 30) * 24) : 2;
    const txt = mm === null ? '' : wet ? `${mm < 1 ? c.fmt(mm, 1) : c.fmt(mm)}&nbsp;mm` : c.t('dry');
    return `<div class="db"><div class="bw"><i style="height:${r1(bh)}px;background:${wet ? rainColour(P, mm / 6) : P.creux}"></i></div>` +
      `<b style="color:${wet ? P.pluieFaible : P.t3}">${txt}</b>` + (hasP ? `<span class="pc">${num(d.rain_probability_pct) ? `${d.rain_probability_pct}&nbsp;%` : ''}</span>` : '') +
      `<span class="vt2">${num(info[i].deg) ? arrow(11, info[i].deg, P.vent) : ''}${num(d.wind_max) ? c.fmt(d.wind_max) : ''}${num(info[i].gust) ? `<small>/${c.fmt(info[i].gust)}</small>` : ''}</span></div>`;
  }));
  return `<div class="bloc${tight ? ' serre' : ''}">${s}</div></div>`;
}

// ------------------------------------------------------------------------------------------------------------------ now
// The highest gust of the next hours: AROME-PI's 15-minute steps where there are, else the hours of the answer (12 hours).
function gustPeak(c) {
  const steps = (Array.isArray(c.point?.short_term?.steps) ? c.point.short_term.steps : []).filter((s) => num(s.time_utc) && num(s.wind_gust_kmh) && s.time_utc + 900 > c.now);
  const list = steps.length ? steps : currentHours(c).slice(0, 12).filter((h) => num(h.wind_gust_kmh));
  if (!list.length) return null;
  const best = list.reduce((a, b) => (b.wind_gust_kmh > a.wind_gust_kmh ? b : a));
  return { v: best.wind_gust_kmh, at: best.time_utc };
}
// The trend of the pressure, the rule of the Vigie site: a change of 1 hPa or more in 3 hours, read on the history of the answer.
function pressureTrend(c) {
  const ph = (Array.isArray(c.point?.pressure_history) ? c.point.pressure_history : []).filter((x) => x && num(x.time_utc) && num(x.pressure_hpa)).sort((a, b) => a.time_utc - b.time_utc);
  if (ph.length < 2) return '';
  const last = ph[ph.length - 1];
  if (c.now - last.time_utc > 3 * 3600) return '';
  const old = ph.reduce((a, x) => (Math.abs(x.time_utc - (last.time_utc - 10800)) < Math.abs(a.time_utc - (last.time_utc - 10800)) ? x : a));
  const hours = (last.time_utc - old.time_utc) / 3600;
  if (hours < 1.5) return '';
  const per3 = ((last.pressure_hpa - old.pressure_hpa) * 3) / hours;
  return per3 >= 1 ? '↗' : per3 <= -1 ? '↘' : '→';
}
// The next sunrise and sunset, to the minute (none in a polar day or night).
function sunTimes(c) {
  const co = c.point?.coordinates;
  if (!num(co?.lat) || !num(co?.lon)) return null;
  const up = (ts) => sunUp(co.lat, co.lon, ts), out = { up: up(c.now) };
  let prev = out.up;
  for (let i = 1; i <= 156 && !(out.rise && out.set); i++) {
    const ts = c.now + i * 600, u = up(ts);
    if (u === prev) continue;
    let a = ts - 600, b = ts;
    while (b - a > 60) { const m = Math.floor((a + b) / 2); if (up(m) === prev) a = m; else b = m; }
    if (u && !out.rise) out.rise = b;
    if (!u && !out.set) out.set = b;
    prev = u;
  }
  return out;
}
// The European air quality index in the bands of the site, and their colours.
const AQI_FROM = [20, 40, 60, 80, 100];
const AQI_TONE = ['vert', 'vert', 'jaune', 'orange', 'rouge', 'rouge'];
function nowFacts(c) {
  const P = c.P, cur = c.point?.current || {}, out = [];
  const peak = gustPeak(c);
  if (peak) out.push(c.t('gust_peak', { v: `<b style="color:${gustColour(P, peak.v) || P.vent}">${c.fmt(peak.v)}</b>`, t: c.hm(peak.at) }));
  if (num(cur.pressure_hpa)) { const tr = pressureTrend(c); out.push(`<b style="color:${P.texte}">${c.fmt(cur.pressure_hpa)}</b>&nbsp;hPa${tr ? ` ${tr}` : ''}`); }
  const air = [];
  if (num(cur.aqi_eu)) { const i = AQI_FROM.filter((b) => cur.aqi_eu >= b).length; air.push(c.t('air', { w: `<b style="color:${P[AQI_TONE[i]]}">${c.t('aqi_word')[i]}</b>` })); }
  if (num(cur.uv_index)) air.push(c.t('uv', { v: c.fmt(cur.uv_index) }));
  if (air.length) out.push(air.join(' · '));
  const sun = sunTimes(c);
  if (sun && (sun.rise || sun.set)) {
    const set = sun.set ? `↓&nbsp;${c.hm(sun.set)}` : '', rise = sun.rise ? `↑&nbsp;${c.hm(sun.rise)}` : '';
    out.push(`<svg width="12" height="12" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="5" fill="${P.jaune}"/></svg>${(sun.up ? [set, rise] : [rise, set]).filter(Boolean).join(' · ')}`);
  }
  return out;
}
// Now: icon, temperature in its colour, the weather, feeling and humidity, the wind; beside it the gusts to come, the pressure, the
// air and the UV, the sun. So that nothing is cut on a narrow card (measured on 2026-11-27 with Home Assistant's fonts): under
// 370 px inside, the gust of now is left out (the gusts to come stay beside); under 340, the humidity too, and the icon and the
// temperature are smaller.
function nowBlock(c, w) {
  const P = c.P, m = momentOfPoint(c), cur = c.point?.current || {}, less = Math.max(c.nowLess || 0, w < 340 ? 2 : w < 370 ? 1 : 0);
  const narrow = less >= 1, narrower = less >= 2;
  c.nowLevel = less; // what the card measures after drawing starts from (_render)
  const k = clamp(w / 448, narrower ? 0.72 : 0.8, c.wide ? 1.25 : 1), fs = k < 0.9 ? 11 : 12;
  const hum = num(cur.humidity_pct) && !narrower ? `${c.fmt(cur.humidity_pct)}&nbsp;%` : '';
  const feels = [num(m.feels) ? c.t('feels', { t: c.fmt(m.feels) }) : '', hum].filter(Boolean).join(' · ');
  const wind = num(m.wind) ? `<span class="nl">${num(m.deg) ? arrow(13, m.deg, P.vent) : ''}<span><b style="color:${P.vent}">${c.fmt(m.wind)}</b>&nbsp;km/h${num(m.gust) && !narrow ? ` · ${c.t('gusts_abbr')} <b style="color:${P.vent}">${c.fmt(m.gust)}</b>` : ''}</span></span>` : '';
  const facts = nowFacts(c);
  return `<div class="now" style="min-height:${Math.round(68 * k)}px">${icon(P, m.kind, Math.round(54 * k))}` +
    (num(m.temp) ? `<span class="temp" style="font-size:${Math.round(52 * k)}px;color:${tempColour(P, m.temp)}">${c.fmt(m.temp)}°</span>` : '') +
    `<div class="now-t">${m.label ? `<b style="font-size:${Math.round(15 * Math.min(k, 1.1))}px">${esc(m.label)}</b>` : ''}${feels ? `<span>${feels}</span>` : ''}${wind}</div>` +
    (facts.length ? `<div class="now-f" style="width:${Math.round(146 * Math.max(k, 0.8))}px;font-size:${fs}px">${facts.map((f) => `<span>${f}</span>`).join('')}</div>` : '') + '</div>';
}

// ----------------------------------------------------------------------------------------------------------------- rain
// The steps of the nowcast still to come (the one in progress included), in mm/h: twelve 5-minute rates (PIAF) or four
// 15-minute amounts. `at` is the start of a step; an older API without `start_utc` counts from now (`known` false).
function nowcastBars(c) {
  const rn = c.point?.rain_nowcast;
  const rates = Array.isArray(rn?.rate_5m_mm_h) && rn.rate_5m_mm_h.length && rn.rate_5m_mm_h.every(num) ? rn.rate_5m_mm_h : null;
  const steps = Array.isArray(rn?.mm_per_step) && rn.mm_per_step.length && rn.mm_per_step.every(num) ? rn.mm_per_step : null;
  if (!rates && !steps) return [];
  const minutes = rates ? 5 : rn.interval_minutes || 15, start = num(rn.start_utc) ? rn.start_utc : null;
  return (rates || steps.map((v) => (v * 60) / minutes)).map((r, i) => ({ r, minutes, known: start !== null, at: (start ?? c.now) + i * minutes * 60 }))
    .filter((b) => !b.known || b.at + b.minutes * 60 > c.now);
}
const SNOWY = ['snow', 'wet_snow', 'snow_pellets'];
// The kind of precipitation of a WMO code, in the words of AROME-PI.
function wmoPrecip(code) {
  if (!num(code)) return 'rain';
  if ((code >= 71 && code <= 77) || code === 85 || code === 86) return 'snow';
  if (code === 56 || code === 57 || code === 66 || code === 67) return 'freezing';
  return code >= 51 && code <= 55 ? 'drizzle' : 'rain';
}
// The kind of precipitation at a time: AROME-PI's step there, else the hour of the answer, else the weather now.
function kindAt(c, ts) {
  const st = c.point?.short_term, m = (st?.interval_minutes || 15) * 60;
  const s = (Array.isArray(st?.steps) ? st.steps : []).find((x) => num(x.time_utc) && x.time_utc <= ts && ts < x.time_utc + m && typeof x.precipitation === 'string' && x.precipitation !== 'none');
  if (s) return s.precipitation;
  const h = currentHours(c).find((x) => x.time_utc <= ts && ts < x.time_utc + 3600);
  return wmoPrecip(h ? h.weather_code : c.point?.current?.weather_code);
}
// The rain from now on, as one list of slots: the nowcast first, then AROME-PI by 15 minutes, then the hours; each source only
// after the end of the one before. A slot: {from, to, wet, kind, r} (r in mm per hour, null when unknown).
function rainSlots(c) {
  const out = [];
  let end = c.now;
  const push = (from, to, wet, kind, r) => { if (to <= end) return; out.push({ from: Math.max(from, end), to, wet, kind, r }); end = to; };
  for (const b of nowcastBars(c)) push(b.at, b.at + b.minutes * 60, b.r > 0, null, b.r);
  const st = c.point?.short_term, m = (st?.interval_minutes || 15) * 60;
  for (const s of Array.isArray(st?.steps) ? st.steps : []) {
    if (num(s.time_utc) && typeof s.precipitation === 'string') push(s.time_utc, s.time_utc + m, s.precipitation !== 'none', s.precipitation === 'none' ? null : s.precipitation, null);
  }
  for (const h of currentHours(c)) if (num(h.precipitation_mm)) push(h.time_utc, h.time_utc + 3600, h.precipitation_mm >= 0.1, wmoPrecip(h.weather_code), h.precipitation_mm);
  return out;
}
const intensity = (r) => (r >= 3 ? 'int_heavy' : r >= 1 ? 'int_moderate' : 'int_light');
const dayAndHour = (c, ts) => { const d = dayWord(c, ts, false); return { d: d ? `${d} ` : '', t: c.hm(ts) }; };
// After a dry hour: when the next rain comes, or until when it stays dry.
function nextRain(c, after) {
  const slots = rainSlots(c), wet = slots.find((s) => s.wet && s.to > after);
  if (wet) return c.t('next_rain', dayAndHour(c, Math.max(wet.from, after)));
  const last = slots[slots.length - 1];
  return last && last.to > after ? c.t('dry_until', dayAndHour(c, last.to)) : '';
}
// The rain of the next hour (the nowcast of the place, PIAF in France): when it comes, how strong, a bar per step. It opens
// Details. The words are the ones the Details page reads too (nowcastFirst), so the two never say two things about the hour.
function rainTile(c, w) {
  const first = nowcastFirst(c);
  if (first === null) return '';
  const P = c.P, bars = nowcastBars(c), wet = bars.filter((b) => b.r > 0);
  const snow = first >= 0 && SNOWY.includes(kindAt(c, c.now + first * 60));
  const head = first < 0 ? c.t('rain_none') : first === 0 ? c.t(snow ? 'snow_now' : 'rain_now') : c.t(snow ? 'snow_in' : 'rain_in', { n: first });
  let sub;
  if (wet.length) {
    const a = intensity(wet[0].r), b = intensity(Math.max(...wet.map((x) => x.r)));
    sub = a === b ? c.t(a) : c.t('then', { a: c.t(a), b: c.t(b) });
  } else sub = nextRain(c, c.now + 3600);
  const marks = bars.length && bars[0].known ? [...new Set([0, Math.floor((bars.length - 1) / 2), bars.length - 1])] : [];
  const go = c.config._pages.includes('details') ? ' data-go="details"' : '';
  return `<button class="tuile"${go} style="background:${alpha(P.pluie, 0.12)};border-color:${alpha(P.pluie, 0.45)}">${glyph('parapluie', 22, P.pluie, 2.2)}` +
    `<span class="tt" style="max-width:${Math.round(w * 0.6)}px"><b>${head}</b>${sub ? `<small>${sub}</small>` : ''}</span>` +
    `<span class="tb"><span class="bars">${bars.map((x) => `<i style="height:${x.r > 0 ? r1(5 + Math.sqrt(Math.min(x.r, 4) / 4) * 25) : 3}px;background:${x.r > 0 ? rainColour(P, x.r) : P.creux}"></i>`).join('')}</span>` +
    (marks.length > 1 ? `<span class="tl">${marks.map((i) => `<span>${c.hm(bars[i].at)}</span>`).join('')}</span>` : '') + '</span></button>';
}

// ------------------------------------------------------------------------------------------------------------- details
// The first local noon at least 12 hours ahead ("tomorrow noon" in the evening).
function nextNoon(c) {
  let ts = Date.parse(`${c.dayKey(c.now)}T12:00:00Z`) / 1000 - c.offset;
  while (ts - c.now < 12 * 3600) ts += 86400;
  return ts;
}
// The rain until then, said in a few periods: "Dry → 14:50", "Rain → 16:15", "Lull → 17:30"... A dry gap of 30 minutes or
// less between two rains is part of the rain, and a rain whose known rates all stay under 1 mm/h is light.
function rainChips(c, until) {
  const P = c.P, runs = [];
  for (const s of rainSlots(c)) {
    if (s.from >= until) break;
    const to = Math.min(s.to, until), last = runs[runs.length - 1];
    if (last && last.wet === s.wet) { last.to = to; last.kind = last.kind || s.kind; if (num(s.r)) last.rs.push(s.r); } else runs.push({ wet: s.wet, from: s.from, to, kind: s.kind, rs: num(s.r) ? [s.r] : [] });
  }
  for (let i = 1; i < runs.length - 1; i++) {
    if (!runs[i].wet && runs[i].to - runs[i].from <= 1800 && runs[i - 1].wet && runs[i + 1].wet) {
      const a = runs[i - 1], b = runs[i + 1];
      runs.splice(i - 1, 3, { wet: true, from: a.from, to: b.to, kind: a.kind || b.kind, rs: a.rs.concat(b.rs) });
      i -= 1;
    }
  }
  if (!runs.length) return '';
  const rain = { bg: alpha(P.pluie, 0.16), bd: alpha(P.pluie, 0.55), dot: P.pluie }, dry = { bg: alpha(P.vert, 0.1), bd: alpha(P.vert, 0.45), dot: P.vert };
  return '<div class="chipsr">' + runs.slice(0, 8).map((r, i) => {
    let what;
    if (r.wet) {
      const k = kindWord(c, r.kind || 'rain');
      what = r.rs.length && Math.max(...r.rs) < 1 ? c.t('light_kind', { k: c.lang === 'fr' ? k : k.toLowerCase() }) : k;
    } else what = i > 0 && i < runs.length - 1 && r.to - r.from < 3 * 3600 ? c.t('lull') : c.t('dry_word');
    const st = r.wet ? rain : dry;
    return `<span class="pp" style="background:${st.bg};border-color:${st.bd}"><i style="background:${st.dot}"></i>${what}<span>→ ${c.hm(r.to)}</span></span>`;
  }).join('') + '</div>';
}
// Within the hour: a bar per step of the nowcast, the levels of a moderate and a heavy rain dashed.
function minutesBlock(c, w) {
  const bars = nowcastBars(c);
  if (!bars.length || nowcastFirst(c) === null) return '';
  const P = c.P, rn = c.point.rain_nowcast, n = bars.length, bw = w - 52, firstWet = bars.findIndex((b) => b.r > 0);
  const src = typeof rn.attribution === 'string' && rn.attribution.includes('PIAF') ? c.t('radar_piaf') : 'Open-Meteo';
  let s = section(c.t('in_hour', { n: bars[0].minutes }), `${src} · mm/h`) + `<div class="cols" style="width:${r1(w)}px;height:72px">`;
  [[14, 'int_heavy'], [38, 'int_moderate']].forEach(([y, key]) => { s += `<div class="dl" style="top:${y}px;width:${bw}px"></div><span class="lb2" style="left:${bw + 4}px;top:${y - 7}px">${c.t(key)}</span>`; });
  s += `<div class="bars5" style="width:${bw}px">${bars.map((b) => `<i style="height:${b.r > 0 ? r1(Math.max(5, Math.min(b.r, 4) * 12)) : 3}px;background:${b.r > 0 ? rainColour(P, b.r) : P.creux}"></i>`).join('')}</div>`;
  s += `<div class="bars5 lbl" style="width:${bw}px;top:56px">` + bars.map((b, i) => {
    const show = i === 0 || i === firstWet || (n <= 6 || (i % 2 === 0 && Math.abs(i - firstWet) > 1));
    const txt = !show ? '' : i === 0 && (!b.known || b.at <= c.now) ? c.t('now_short') : b.known ? c.hm(b.at) : '';
    const on = i === 0 || i === firstWet;
    return `<span style="color:${i === 0 ? P.accent : i === firstWet ? P.pluieFaible : P.t3};font-weight:${on ? 700 : 500}">${txt}</span>`;
  }).join('') + '</div></div>';
  return `<div class="bloc">${s}</div>`;
}
// Until the evening by quarter hour (AROME-PI): the gusts as a curve with their peak, the periods of rain, hail and visibility.
function quarterBlock(c, w) {
  const st = c.point?.short_term, m = (st?.interval_minutes || 15) * 60;
  const steps = (Array.isArray(st?.steps) ? st.steps : []).filter((s) => num(s.time_utc) && s.time_utc + m > c.now).slice(0, 20);
  if (steps.length < 2) return '';
  const P = c.P, n = steps.length, cw = w / n, colon = colonOf(c);
  const said = (level) => level && (level !== 'none' || w >= 420);
  const right = [said(st.hail?.level) ? `${c.t('hail').toLowerCase()}${colon}${c.t(`hail_${st.hail.level}`)}` : '', said(st.visibility?.level) ? `${c.t('visibility').toLowerCase()}${colon}${c.t(`vis_${st.visibility.level}`)}` : ''].filter(Boolean).join(' · ');
  let s = section(c.t('quarter_until', { t: c.hm(steps[n - 1].time_utc + m) }), right) + `<div class="cols" style="width:${r1(w)}px;height:92px">`;
  const g = steps.map((x) => (num(x.wind_gust_kmh) ? x.wind_gust_kmh : null)), gv = g.filter(num);
  if (gv.length >= 2) {
    const lo = Math.max(0, Math.floor((Math.min(...gv) - 5) / 10) * 10), hi = Math.max(lo + 30, Math.ceil((Math.max(...gv) + 5) / 10) * 10);
    const Y = (v) => 46 - ((v - lo) / (hi - lo)) * 36;
    const pts = g.map((v, i) => (num(v) ? { x: cw * (i + 0.5), y: Y(v), v, ts: steps[i].time_utc } : null)).filter(Boolean);
    const d = smooth(pts), best = pts.reduce((a, b) => (b.v > a.v ? b : a)), hot = gustColour(P, best.v) || P.texte;
    s += `<span class="lb2" style="${best.x > w / 2 ? 'left:0' : 'right:0'};top:0">${c.t('gusts_unit')}</span>` +
      `<svg class="ov" style="top:0" width="${r1(w)}" height="48" viewBox="0 0 ${r1(w)} 48" aria-hidden="true">` +
      (lo < 80 && 80 < hi ? `<line x1="0" x2="${r1(w)}" y1="${r1(Y(80))}" y2="${r1(Y(80))}" stroke="${P.orange}" stroke-width="1" stroke-dasharray="4 4" opacity=".6"/>` : '') +
      `<path d="${d} L${r1(pts[pts.length - 1].x)} 48 L${r1(pts[0].x)} 48 Z" fill="${alpha(P.vent, 0.14)}"/><path d="${d}" fill="none" stroke="${P.vent}" stroke-width="2.5" stroke-linecap="round"/>` +
      `<circle cx="${r1(best.x)}" cy="${r1(best.y)}" r="4.5" fill="var(--vc-carte)" stroke="${hot}" stroke-width="2.5"/></svg>` +
      `<span class="lb2 pk" style="${best.x > w - 100 ? `right:${r1(w - best.x - 6)}px` : `left:${r1(Math.max(0, best.x - 6))}px`};top:${r1(best.y >= 22 ? best.y - 22 : best.y + 7)}px;color:${hot}">${c.t('peak_at', { v: c.fmt(best.v), t: c.hm(best.ts) })}</span>`;
  }
  const runs = [];
  steps.forEach((x, i) => {
    const wet = typeof x.precipitation === 'string' && x.precipitation !== 'none', r = runs[runs.length - 1];
    if (r && r.wet === wet) r.n += 1; else runs.push({ wet, n: 1, i0: i, kind: wet ? x.precipitation : null });
  });
  s += runs.map((r) => {
    const wd = r.n * cw - 2, txt = wd >= 34 ? (r.wet ? kindWord(c, r.kind).toLowerCase() : c.t('dry')) : '';
    return `<div class="run" style="left:${r1(r.i0 * cw + 1)}px;width:${r1(wd)}px;${r.wet ? `background:${P.pluie};border-color:${P.pluie};color:${P.sur}` : `border-color:${P.filet};color:${P.t4}`}">${txt}</div>`;
  }).join('');
  const marks = [];
  steps.forEach((x, i) => { if ((i === 0 || c.local(x.time_utc).getUTCMinutes() === 0) && (!marks.length || i - marks[marks.length - 1] >= 3)) marks.push(i); });
  s += marks.map((i) => `<span class="lb2 tk" style="left:${r1(clamp(cw * (i + 0.5) - 22, 0, w - 44))}px;top:76px;width:44px">${c.hm(steps[i].time_utc)}</span>`).join('');
  return `<div class="bloc">${s}</div></div>`;
}
// The next 48 hours hour by hour: a column per hour tinted by its sun, the days apart, the rain, its chance when the forecast
// gives one, the wind every few hours.
function hours48Block(c, w) {
  const hrs = currentHours(c).slice(0, 48);
  if (hrs.length < 12) return '';
  const P = c.P, n = hrs.length, cw = w / n, hasP = hrs.some((h) => num(h.precipitation_probability_pct));
  let s = section(c.t('h48', { n }), c.t(hasP ? 'h48_legend' : 'h48_legend_nop')) + `<div class="cols" style="width:${r1(w)}px;height:122px">`;
  s += hrs.map((h, i) => {
    const day = isDayAt(c, h);
    return `<div class="cl" style="left:${r1(i * cw + 0.5)}px;width:${r1(Math.max(1, cw - 1))}px;height:92px;border-radius:3px;background:${glow(P, day ? (sunOfHours([h]) ?? sunOfCode(h.weather_code)) : null, day)}"></div>`;
  }).join('');
  hrs.forEach((h, i) => {
    if (i > 0 && c.local(h.time_utc).getUTCHours() === 0) s += `<div class="ml" style="left:${r1(i * cw)}px"></div><span class="lb2 dn" style="left:${r1(i * cw + 3)}px;top:0">${c.dayName(c.dayKey(h.time_utc))}</span>`;
  });
  if (hasP) {
    const pts = hrs.map((h, i) => (num(h.precipitation_probability_pct) ? { x: cw * (i + 0.5), y: 40 - (h.precipitation_probability_pct / 100) * 32 } : null)).filter(Boolean);
    s += `<svg class="ov" style="top:0" width="${r1(w)}" height="44" viewBox="0 0 ${r1(w)} 44" aria-hidden="true"><path d="${smooth(pts)}" fill="none" stroke="${P.t2}" stroke-width="1.5" stroke-dasharray="3 3" opacity=".8"/></svg>` +
      `<span class="lb2" style="left:4px;top:30px;font-size:9px;color:${P.t3}">${c.t('chance')}</span>`;
  }
  const bw = r1(Math.min(6, cw * 0.7));
  s += cells(n, 44, hrs.map((h) => {
    const v = num(h.precipitation_mm) && h.precipitation_mm >= 0.1 ? h.precipitation_mm : 0;
    return `<i class="b48" style="width:${bw}px;height:${v ? r1(Math.max(3, (Math.min(v, 2) / 2) * 44)) : 0}px;background:${rainColour(P, v)}"></i>`;
  }), 'height:48px;align-items:end');
  const every = cw * 3 >= 30 ? 3 : cw * 4 >= 30 ? 4 : 6;
  hrs.forEach((h, i) => {
    if (i % every || !num(h.wind_kmh)) return;
    s += `<span class="w3" style="left:${r1(clamp(cw * (i + 0.5) - 15, 0, w - 30))}px;top:94px;width:30px">${num(h.wind_deg) ? arrow(9, h.wind_deg, P.vent) : ''}${c.fmt(h.wind_kmh)}</span>`;
  });
  hrs.forEach((h, i) => {
    if (i % 6) return;
    s += `<span class="lb2 tk" style="left:${r1(clamp(cw * (i + 0.5) - 15, 0, w - 30))}px;top:108px;width:30px;color:${i === 0 ? P.accent : P.t3}">${c.hourOnly(h.time_utc)}</span>`;
  });
  return `<div class="bloc">${s}</div></div>`;
}
// The chances per 3-hour window of the ensembles (PE-AROME), a cell per window shaded by its chance: rain of 1 mm or more,
// gusts of 56 km/h (30 kt) or more and thunderstorms always; gusts of 93 km/h (50 kt), snow and hail when one window has some.
function chancesBlock(c, w) {
  const sr = c.point?.probabilities?.short_range;
  const all = (Array.isArray(sr?.windows) ? sr.windows : []).filter((x) => x && num(x.to_utc) && x.to_utc > c.now);
  if (!all.length) return '';
  const P = c.P, lab = c.wide ? 104 : 84, win = all.slice(0, clamp(Math.floor((w - lab) / 34), 4, 16));
  const ROWS = [['rain_1mm_pct', 'pe_rain', P.pluie, true], ['gust_30kt_pct', 'pe_gust56', P.orange, true], ['gust_50kt_pct', 'pe_gust93', P.rouge, false],
    ['storm_pct', 'pe_storm', P.jaune, true], ['snow_1mm_pct', 'pe_snow', P.t2, false], ['hail_pct', 'pe_hail', P.t2, false]];
  const rows = ROWS.filter(([key, , , always]) => win.some((x) => num(x[key])) && (always || win.some((x) => x[key] > 0)));
  const src = num(sr.members) ? c.t('chances_src', { n: sr.members }) : c.t('chances_src_plain');
  let s = section(c.t('chances', { h: sr.window_hours || 3 }), src) + `<div class="pe" style="grid-template-columns:${lab}px repeat(${win.length},minmax(0,1fr))"><span></span>`;
  s += win.map((x) => `<span class="h">${c.hourOnly(x.to_utc)}</span>`).join('');
  for (const [key, label, col] of rows) {
    s += `<span class="r" style="color:${col}">${c.t(label)}</span>` + win.map((x) => {
      const v = x[key];
      return num(v) ? `<span class="v" style="background:${alpha(col, 0.08 + (v / 100) * 0.82)};color:${v >= 55 ? P.sur : P.t2}">${v}</span>` : '<span class="v"></span>';
    }).join('');
  }
  return `<div class="bloc">${s}</div></div>`;
}

// ------------------------------------------------------------------------------------------------------------------- pages
// The home page in France (piste B of the sketches, with the rain tile of piste A): now, the rain of the hour, the strongest line
// of the season, the next 24 hours and the days. The credits are on the other pages. It has to fit the card: with the pill of
// the season on a narrow card, or when the card measures it too tall (`c.squeeze`, px, see `_render`), the curves of the hours
// and of the days are flattened, a third and two thirds of what is missing.
function pageHome(c) {
  if (!c.inFrance) return pageHomeWorld(c);
  const W = c.W, season = seasonPill(c, 13, 18, true, '7px 12px');
  const less = (season && !c.wide ? 38 : 0) + (c.squeeze || 0), cutH = Math.min(20, Math.round(less / 3)), cutD = Math.min(40, less - cutH);
  if (c.wide) {
    const left = Math.round(W * 0.52), right = W - left - 20;
    return `<div class="pile" style="gap:10px"><div class="duo" style="grid-template-columns:${left}px ${right}px">${nowBlock(c, left)}` +
      `<div class="pile" style="gap:8px">${rainTile(c, right)}${season}</div></div>${hoursBlock(c, W, cutH)}${daysBlock(c, W, 10, cutD)}</div>`;
  }
  return `<div class="pile" style="gap:8px">${nowBlock(c, W)}${rainTile(c, W)}${season}${hoursBlock(c, W, cutH)}${daysBlock(c, W, 7, cutD)}</div>`;
}

// Outside France: the weather of the home's own entity when one is chosen; else the warnings, the earthquakes and the tide.
function pageHomeWorld(c) {
  const ent = c.config._weatherState;
  const quakes = c.data?.earthquakes;
  const q = quakes?.earthquakes?.[0];
  const quakePill = q ? `<div class="puce" style="font-size:14px;background:transparent;border-color:${c.P.filet}">${glyph('onde', 20, c.P.texte, 2.2)}<span class="tx">${quakeText(c, q)}<span class="det" style="font-size:13px"> · ${quakeWhen(c, q)}</span></span></div>` : '';
  if (ent) {
    const a = ent.attributes || {};
    const m = moment(c, { kind: CONDITION_ICON[ent.state] || null, temp: a.temperature, feels: a.apparent_temperature, label: CONDITION[c.lang][ent.state] || null,
      wind: a.wind_speed, gust: a.wind_gust_speed, deg: a.wind_bearing, windUnit: a.wind_speed_unit });
    const list = (c.config._forecast || []).map((f) => ({ name: c.dayName(String(f.datetime).slice(0, 10)), kind: CONDITION_ICON[f.condition] || null, max: f.temperature, min: f.templow }));
    const season = seasonPill(c, 14, 20, true);
    return title(c, 'accueil', c.t('weather_title'), c.t('home_entity')) + m + (quakePill ? `<div style="height:12px"></div>${quakePill}` : '') +
      (season ? `<div style="height:8px"></div>${season}` : '') + sep() +
      days(c, list, c.wide ? 7 : 5, { noRain: true }) + foot(c, providers(quakes?.attribution, seasonItems(c)[0]?.attribution), '');
  }
  const answer = q ? `<div class="rep"><div class="ligne" style="font-size:20px">${glyph('onde', 24, c.P.texte, 2.3)}<span>${quakeText(c, q)}</span></div></div>`
    : `<div class="rep"><div class="ligne" style="font-size:20px">${glyph('ok', 24, c.P.vert, 2.3)}<span>${c.t('nothing_to_report')}</span></div></div>`;
  const season = seasonPill(c, 14, 20, true);
  return answer + `<div style="height:12px"></div>${officialPill(c)}` + (season ? `<div style="height:8px"></div>${season}` : '') + (c.data?.tides ? sep() + tideBlock(c, c.wide ? Math.min(c.W, 640) : c.W) : '') + foot(c, providers(c.data?.warnings?.attribution, quakes?.attribution, c.data?.tides?.attribution, seasonItems(c)[0]?.attribution));
}
function officialPill(c) {
  const groups = warningGroups(c);
  if (groups.length) return warningPill(c, 15, 22, false);
  const w = c.data?.warnings;
  if (w?.note && !(w.vigilances || []).length) return `<div class="puce" style="font-size:15px;background:transparent;border-color:${c.P.filet}">${glyph('info', 22, c.P.t3, 2.2)}<span class="tx">${c.t('not_relayed')}<small>${c.t('no_coverage')}</small></span></div>`;
  return warningPill(c, 15, 22, false);
}
const quakeText = (c, q) => {
  const dir = typeof q.bearing_cardinal === 'string' ? cardinalTo(c, q.bearing_cardinal) : '';
  return dir ? c.t('quake_dir', { m: c.fmt(q.magnitude, 1), km: c.fmt(q.distance_km), dir }) : c.t('quake', { m: c.fmt(q.magnitude, 1), km: c.fmt(q.distance_km) });
};
const CARDINALS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
function cardinalTo(c, card) {
  const i = CARDINALS.indexOf(card.toUpperCase().replace('O', 'W'));
  return i < 0 ? '' : TEXT[c.lang].dir_to[i];
}
function quakeWhen(c, q) {
  const ts = isoTs(q.time_utc) ?? (num(q.time_utc) ? q.time_utc : null);
  if (ts === null) return '';
  const today = c.dayKey(c.now), day = c.dayKey(ts), yesterday = c.dayKey(c.now - 86400);
  // A weekday alone only within the week; an older one says its date.
  const word = day === today ? c.t('today_word') : day === yesterday ? c.t('yesterday') : c.now - ts < 6 * 86400 ? c.dayName(day)
    : new Intl.DateTimeFormat(c.lang, { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(`${day}T12:00:00Z`));
  return `${word}, ${c.hm(ts)}`;
}

// The word of a kind of precipitation of AROME-PI ("rain", "snow"...); an unknown kind is said as such, never as its code.
const kindWord = (c, kind) => c.t(`kind_${kind}` in TEXT.en ? `kind_${kind}` : 'kind_unknown');

// The Details page (piste A of the sketches, "three magnifiers"): the rain until tomorrow noon in a few periods, then the hour by
// 5 minutes (the nowcast), the evening by quarter hour (AROME-PI), 48 hours hour by hour, and the chances per 3 hours (PE-AROME).
function pageDetails(c) {
  const W = c.W, st = c.point?.short_term, pr = c.point?.probabilities, rn = c.point?.rain_nowcast;
  const until = nextNoon(c), chips = rainChips(c, until), day = dayWord(c, until, false);
  const chipsBlock = chips ? `<div class="bloc">${section(c.t('rain_until', { t: `${day ? `${day} ` : ''}${c.t('noon')}` }))}${chips}</div>` : '';
  const models = [typeof rn?.attribution === 'string' && rn.attribution.includes('PIAF') ? 'PIAF' : '', st?.steps?.length ? 'AROME-PI' : '', pr?.short_range?.windows?.length ? 'PE-AROME' : ''].filter(Boolean);
  const credit = foot(c, providers(rn?.attribution, st?.attribution, pr?.attribution, c.point?.weather_attribution), models.length ? ` (${models.join(', ')})` : '');
  if (c.wide) {
    const half = Math.floor((W - 28) / 2);
    return `<div class="pile" style="gap:14px">${chipsBlock}<div class="duo" style="grid-template-columns:${half}px ${half}px;align-items:start;gap:28px">` +
      `<div class="pile" style="gap:14px">${minutesBlock(c, half)}${quarterBlock(c, half)}</div><div class="pile" style="gap:14px">${hours48Block(c, half)}${chancesBlock(c, half)}</div></div></div>${credit}`;
  }
  return `<div class="pile" style="gap:14px">${chipsBlock}${minutesBlock(c, W)}${quarterBlock(c, W)}${hours48Block(c, W)}${chancesBlock(c, W)}</div>${credit}`;
}

function pageAlerts(c) {
  const w = c.data?.warnings, groups = warningGroups(c), P = c.P;
  const quakes = c.data?.earthquakes, qs = Array.isArray(quakes?.earthquakes) ? quakes.earthquakes : [];
  let main = '';
  if (groups.length) {
    const top = groups[0], first = top.items[0] || {};
    const where = [first.area, first.issuer].filter((x) => typeof x === 'string' && x).map(esc).join(' · ');
    const read = num(w?.server_utc) ? c.t('read_at', { t: c.hm(w.server_utc) }) : '';
    main = `<div class="b" style="font-size:${c.wide ? 28 : 24}px;line-height:1.1;color:${P[LEVEL_COLOUR[w?.max_level] || LEVEL_COLOUR[top.level]]}">${c.t('warning', { level: c.t(`level_${w?.max_level && LEVEL_COLOUR[w.max_level] ? w.max_level : top.level}`) })}</div>` +
      `<div class="t2" style="font-size:14px;margin-top:4px">${[where, read].filter(Boolean).join(', ')}</div>` +
      frieze(c, groups, !c.wide ? c.W : c.inFrance || qs.length ? Math.floor(((c.W - 26) * 1.2) / 2.2) : Math.min(c.W, 760));
    const web = groups.flatMap((g) => g.items).find((v) => typeof v.web === 'string' && /^https?:\/\//.test(v.web));
    if (web) main += `<div style="height:14px"></div><a class="lien" href="${esc(web.web)}" target="_blank" rel="noopener noreferrer">${glyph('lien', 15, P.t2, 2.2)}${web.issuer ? c.t('bulletin', { issuer: esc(web.issuer) }) : c.t('bulletin_plain')}</a>`;
  } else {
    const none = w && !(w.vigilances || []).length && w.note ? c.t('no_coverage') : w && w.max_level === 'none' ? c.t('nothing') : c.t('warnings_unknown');
    main = `<div class="rep"><div class="ligne" style="font-size:${c.wide ? 24 : 20}px">${glyph(w?.max_level === 'none' ? 'ok' : 'info', 24, w?.max_level === 'none' ? P.vert : P.t3, 2.3)}<span>${none}</span></div></div>`;
  }
  let side = '';
  if (c.inFrance) {
    const rivers = (w?.vigilances || []).filter((v) => v.source === 'vigicrues' && LEVEL_RANK[v.level]);
    side += title(c, 'goutte', c.t('floods'), 'Vigicrues') + (rivers.length
      ? rivers.slice(0, 4).map((v) => `<div class="lig"><b style="color:${P[LEVEL_COLOUR[v.level]]}">${c.t(`level_${v.level}`)}</b><span>${esc(v.area || v.headline || '')}</span>${num(v.distance_km) ? `<span class="t3" style="margin-left:auto">${c.fmt(v.distance_km, 1)}&nbsp;km</span>` : ''}</div>`).join('')
      : `<div class="t2" style="font-size:14px">${c.t('no_river')}</div>`);
  }
  // The lines of the season: under the warnings on a wall screen (the side keeps the rivers), after the rivers on a phone.
  const items = seasonItems(c);
  if (items.length && c.wide) main += sep('18px 0 14px') + seasonBlock(c, items, Math.floor(((c.W - 26) * 1.2) / 2.2));
  else if (items.length) side += (side ? sep() : '') + seasonBlock(c, items, c.W);
  if (qs.length) {
    side += (side ? sep() : '') + title(c, 'onde', c.t('quakes'), providers(quakes?.attribution).join(', ')) +
      qs.slice(0, 3).map((q) => `<div class="lig"><span>${quakeText(c, q)}</span><span class="t3" style="margin-left:auto;font-size:13px">${quakeWhen(c, q)}</span></div>`).join('');
  }
  const credit = foot(c, providers(w?.attribution, ...(w?.vigilances || []).map((v) => v.source === 'vigicrues' ? 'Vigicrues' : ''), ...items.map((it) => it.attribution)), groups.length ? ` · ${c.t('official_text')}` : '');
  if (c.wide && side) return `<div class="grille" style="grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);align-items:start"><div>${main}</div><div>${side}</div></div>${credit}`;
  return main + (side ? sep() + side : '') + credit;
}
// One row per phenomenon, from its start (or now) to its end; the accent line is now.
function frieze(c, groups, w) {
  const P = c.P, fs = 13, hb = 12;
  const starts = groups.map((g) => g.onset ?? c.now), ends = groups.map((g) => (g.open || g.expires === null ? c.now + 12 * 3600 : g.expires));
  const t0 = Math.min(c.now, ...starts), t1 = Math.max(c.now + 3600, ...ends);
  const X = (t) => 4 + ((t - t0) / (t1 - t0)) * (w - 8);
  let s = '';
  groups.forEach((g, i) => {
    const col = P[LEVEL_COLOUR[g.level]];
    const end = g.open || g.expires === null ? '' : c.t('until', { t: c.hm(g.expires) });
    const head = g.items[0]?.headline;
    s += `<div class="frise-l" style="font-size:${fs + 2}px">${glyph(PHENOMENON_GLYPH[g.phenomenon] || 'danger', fs + 6, col, 2.2)}<span>${esc(PHENOMENON[c.lang][g.phenomenon] || g.phenomenon)}</span><span class="fin">${end}</span></div>`;
    s += `<svg width="${w}" height="${hb + 8}" viewBox="0 0 ${w} ${hb + 8}" style="margin-top:6px" aria-hidden="true"><rect x="${r1(X(t0))}" y="4" width="${r1(X(t1) - X(t0))}" height="${hb}" rx="${hb / 2}" fill="${P.filet}"/>` +
      `<rect x="${r1(X(starts[i]))}" y="4" width="${r1(Math.max(hb, X(ends[i]) - X(starts[i])))}" height="${hb}" rx="${hb / 2}" fill="${col}"${g.open ? ' opacity=".75"' : ''}/>` +
      `<line x1="${r1(X(c.now))}" y1="0" x2="${r1(X(c.now))}" y2="${hb + 8}" stroke="${P.accent}" stroke-width="2.5"/></svg>`;
    if (typeof head === 'string' && head) s += `<div class="t2" style="font-size:13px;margin-top:4px">${esc(head)}</div>`;
  });
  const span = t1 - t0, every = span > 36 * 3600 ? 12 : span > 18 * 3600 ? 6 : span > 9 * 3600 ? 3 : 2;
  let ax = `<svg width="${w}" height="${fs + 8}" viewBox="0 0 ${w} ${fs + 8}" style="margin-top:4px" aria-hidden="true">`;
  ax += `<text x="${r1(X(c.now))}" y="${fs + 3}" font-size="${fs - 1}" font-weight="700" fill="${P.accent}" text-anchor="${X(c.now) < 30 ? 'start' : 'middle'}">${c.hourOnly(c.now)}</text>`;
  const firstTick = Math.ceil((t0 + c.offset) / (every * 3600)) * every * 3600 - c.offset;
  for (let t = firstTick; t <= t1; t += every * 3600) {
    if (Math.abs(X(t) - X(c.now)) < 34) continue;
    ax += `<text x="${r1(X(t))}" y="${fs + 3}" font-size="${fs - 1}" font-weight="700" fill="${P.t3}" text-anchor="${X(t) > w - 20 ? 'end' : 'middle'}">${c.hourOnly(t)}</text>`;
  }
  return `<div style="max-width:${w}px">${s}${ax}</svg></div>`;
}

function pageSea(c) {
  const P = c.P, tides = c.data?.tides, marine = c.point?.marine;
  const left = (w) => (tides ? tideBlock(c, w) : '') + seaLine(c, marine);
  const spot = spotBlock(c);
  const credit = foot(c, providers(tides?.attribution, marine?.attribution, c.data?.activities?.attribution));
  if (c.wide && spot) return `<div class="grille" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr);align-items:start"><div>${left(Math.floor((c.W - 28) / 2))}</div><div>${spot}</div></div>${credit}`;
  return left(c.W) + (spot ? sep() + spot : '') + credit;
}
function tideBlock(c, w) {
  const tides = c.data?.tides;
  const trend = tides?.now?.trend === 'rising' ? c.t('sea_rising') : tides?.now?.trend === 'falling' ? c.t('sea_falling') : c.t('tide');
  let coef = '';
  if (c.inFrance && Array.isArray(tides?.coefficients_estimated) && tides.coefficients_estimated.length) {
    const best = tides.coefficients_estimated.filter((k) => num(k.brest_high_water_utc) && num(k.value)).sort((a, b) => Math.abs(a.brest_high_water_utc - c.now) - Math.abs(b.brest_high_water_utc - c.now))[0];
    if (best) coef = `<span class="t2" style="font-size:14px">${c.t('coef', { c: best.value })}</span>`;
  }
  const curve = tideCurve(c, tides, w, c.wide ? 150 : 116);
  return `<div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap"><span class="b" style="font-size:${c.wide ? 28 : 24}px">${trend}</span>${coef}</div>` +
    (curve ? `<div style="height:6px"></div>${curve}` : '') + `<div class="note" style="margin-top:6px">${c.t('tide_note')}</div>`;
}
// The next 24 hours of tide, the high and low waters marked, the accent line now.
function tideCurve(c, tides, w, h) {
  const all = Array.isArray(tides?.heights) ? tides.heights.filter((x) => num(x.time_utc) && num(x.height_m)) : [];
  const pts0 = all.filter((x) => x.time_utc >= c.now - 1800 && x.time_utc <= c.now + 24 * 3600);
  if (pts0.length < 6) return '';
  const P = c.P, fs = 12, y0 = 24, y1 = h - 20, pad = 4, t0 = pts0[0].time_utc, t1 = pts0[pts0.length - 1].time_utc;
  const amp = Math.max(0.2, ...pts0.map((x) => Math.abs(x.height_m))) * 1.12, id = `m${Math.random().toString(36).slice(2, 8)}`;
  const X = (t) => pad + ((t - t0) / (t1 - t0)) * (w - 2 * pad), Y = (v) => y1 - ((v + amp) / (2 * amp)) * (y1 - y0);
  const pts = pts0.map((x) => ({ x: X(x.time_utc), y: Y(x.height_m) })), d = smooth(pts);
  let s = `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${P.pluie}" stop-opacity=".35"/><stop offset="1" stop-color="${P.pluie}" stop-opacity=".04"/></linearGradient></defs>`;
  s += `<path d="${d} L${r1(pts[pts.length - 1].x)} ${y1 + 4} L${r1(pts[0].x)} ${y1 + 4} Z" fill="url(#${id})"/><path d="${d}" fill="none" stroke="${P.pluie}" stroke-width="3"/>`;
  s += `<line x1="${r1(X(c.now))}" y1="${y0 - 8}" x2="${r1(X(c.now))}" y2="${y1 + 4}" stroke="${P.accent}" stroke-width="2.5"/>`;
  const half = (txt) => txt.replace(/&nbsp;/g, ' ').length * (fs + 1) * 0.29, fit = (x, txt) => Math.max(half(txt), Math.min(w - half(txt), x));
  const taken = [];
  (Array.isArray(tides.extremes) ? tides.extremes : []).filter((e) => num(e.time_utc) && e.time_utc >= t0 && e.time_utc <= t1 && num(e.height_m)).forEach((e) => {
    const x = X(e.time_utc), y = Y(e.height_m), txt = `${c.t(e.kind === 'high' ? 'high_short' : 'low_short')} ${c.hm(e.time_utc)}`, xt = fit(x, txt);
    s += `<circle cx="${r1(x)}" cy="${r1(y)}" r="4.5" fill="${P.texte}"/>`;
    if (e.kind === 'low') taken.push([xt - half(txt), xt + half(txt)]);
    s += `<text x="${r1(xt)}" y="${e.kind === 'low' ? h - 3 : r1(y - 9)}" font-size="${fs + 1}" font-weight="700" fill="${P.texte}" text-anchor="middle">${txt}</text>`;
  });
  [[t0, 'start'], [(t0 + t1) / 2, 'middle'], [t1, 'end']].forEach(([t, a], k) => {
    const txt = c.hourOnly(t), l = txt.length * fs * 0.6, x = X(t), g = a === 'start' ? x : a === 'end' ? x - l : x - l / 2;
    if (taken.some(([p0, p1]) => g - 8 < p1 && p0 < g + l + 8)) return;
    s += `<text x="${r1(x)}" y="${h - 3}" font-size="${fs}" font-weight="700" fill="${k === 0 ? P.accent : P.t3}" text-anchor="${a}">${txt}</text>`;
  });
  return s + '</svg>';
}
function seaLine(c, marine) {
  if (!marine || !num(marine.wave_height_m)) return '';
  const parts = [`${c.t('waves')} <b>${c.fmt(marine.wave_height_m, 1)}&nbsp;m</b>`];
  if (num(marine.wave_period_s)) parts.push(`${c.fmt(marine.wave_period_s)}&nbsp;s`);
  if (num(marine.sea_surface_temperature_c)) parts.push(`${c.t('water')} ${c.fmt(marine.sea_surface_temperature_c)}°`);
  if (marine.sea_state_label && TEXT[c.lang][`sea_${marine.sea_state_label}`]) parts.push(c.t(`sea_${marine.sea_state_label}`));
  return `<div class="t2" style="font-size:14px;margin:10px 0 4px">${parts.join(' · ')}</div>`;
}
function spotBlock(c) {
  const spot = c.data?.spot, act = c.data?.activities;
  if (!spot || !act || !Array.isArray(act.activities)) return '';
  const chosen = c.config.spot && c.config.spot !== 'auto';
  const src = chosen ? c.t('spot_chosen') : c.t('spot_nearest', { km: c.fmt(spot.distance_km, 1) });
  const cols = c.wide ? 2 : 2, fs = c.wide ? 17 : 16;
  const tiles = act.activities.map((a) => {
    const name = c.t(`act_${a.id}`);
    let word, col, why = '';
    if (!a.available) { word = c.t('cannot_judge'); col = c.P.t3; } else if (!a.verdict) { word = c.t('no_verdict'); col = c.P.t3; } else {
      word = c.t(`verdict_${a.verdict}`); col = c.P[VERDICT_COLOUR[a.verdict]];
      why = a.verdict !== 'good' ? reasonText(c, a.reason) : '';
    }
    return `<div class="vd"><div class="nom">${glyph(ACTIVITY_GLYPH[a.id] || 'vagues', 16, c.P.t2)}${esc(name)}</div><div class="v" style="font-size:${fs}px;color:${col}">${word}</div>${why ? `<div class="r">${why}</div>` : ''}</div>`;
  }).join('');
  return title(c, 'planche', esc(spot.name || spot.id), src) + `<div class="verd" style="grid-template-columns:repeat(${cols},1fr)">${tiles}</div>` +
    `<div class="note" style="margin-top:8px">${c.t('rules_note')}</div>`;
}
function reasonText(c, reason) {
  if (typeof reason !== 'string') return '';
  const R = REASON[c.lang], [name] = reason.split('.');
  const state = R.state[reason];
  if (!state) return ''; // a code the card does not know is never shown raw
  const crit = R.criterion[name];
  return esc(crit ? `${crit}${c.lang === 'fr' ? ' : ' : ': '}${state}` : state);
}

function pageNormal(c) {
  const P = c.P, N = c.normals, day = Array.isArray(c.point?.daily) ? c.point.daily[0] : null;
  if (!N || N.status !== 'available') return '';
  const month = num(N.month) ? c.monthName(N.month) : '';
  const nMax = N.normals?.tmax_mean_c?.value, nMin = N.normals?.tmin_mean_c?.value;
  const dMax = day?.tmax, dMin = day?.tmin;
  const lines = [];
  const diffs = [num(dMax) && num(nMax) ? dMax - nMax : null, num(dMin) && num(nMin) ? dMin - nMin : null].filter(num);
  // A rule of the card, not of Météo-France: more than 1 °C on average from the normals is "milder" or "cooler".
  if (diffs.length) {
    const mean = diffs.reduce((a, b) => a + b, 0) / diffs.length;
    const words = mean > 1 ? c.t('milder') : mean < -1 ? c.t('cooler') : c.t('near_normal');
    lines.push(`<div class="ligne" style="font-size:${c.wide ? 26 : 22}px">${glyph('jauge', 26, tempColour(P, num(dMax) ? dMax : nMax), 2.3)}<span>${words}</span></div>`);
  }
  const recordMs = N.records?.gust_ms?.value, record = num(recordMs) ? recordMs * 3.6 : null;
  const st = c.point?.short_term, forecast = num(st?.gusts?.max_kmh) ? st.gusts.max_kmh : null;
  const fcSrc = 'AROME-PI';
  if (num(record) && num(forecast) && forecast >= 0.85 * record) {
    lines.push(`<div class="ligne" style="font-size:${c.wide ? 26 : 22}px">${glyph('vent', 26, P.vent, 2.3)}<span>${c.t(forecast >= record ? 'gust_record' : 'gust_near_record', { month })}</span></div>`);
  }
  const answer = lines.length ? `<div class="rep">${lines.join('')}</div>` : '';
  const ruler = (w, today, normal) => tempRuler(c, w, today, normal, [dMax, dMin, nMax, nMin].filter(num));
  const tempsBlock = (w) => {
    let s = title(c, '', c.t('today_vs', { month }), num(N.station?.distance_km) ? c.t('station_at', { km: c.fmt(N.station.distance_km, 1) }) : '');
    if (num(dMax) && num(nMax)) s += `<div class="ligne2">${c.t('maximum')}<span class="ecart" style="color:${tempColour(P, dMax)}">${dMax - nMax >= 0 ? '+' : '−'}${c.fmt(Math.abs(dMax - nMax), 1)}°</span></div>${ruler(w, dMax, nMax)}`;
    if (num(dMin) && num(nMin)) s += `<div style="height:8px"></div><div class="ligne2">${c.t('minimum')}<span class="ecart" style="color:${tempColour(P, dMin)}">${dMin - nMin >= 0 ? '+' : '−'}${c.fmt(Math.abs(dMin - nMin), 1)}°</span></div>${ruler(w, dMin, nMin)}`;
    return s;
  };
  const sw = c.data?.station_wind;
  const measured = sw && num(sw.wind_gust_kmh) ? sw.wind_gust_kmh : null;
  const gustBlock = (w) => (num(record) || num(forecast) ? title(c, 'vent', c.t('gusts_kmh'), num(forecast) ? c.t('forecast_src', { src: fcSrc }) : '') + gustRuler(c, w, forecast, measured, record, N.records?.gust_ms?.date) : '');
  let windBlock = '';
  if (sw && num(sw.wind_speed_kmh)) {
    const age = num(sw.measured_utc) ? Math.max(0, Math.round((c.now - sw.measured_utc) / 60)) : null;
    const dir = num(sw.wind_from_deg) ? TEXT[c.lang].dir_from[c.sector(sw.wind_from_deg)] : '';
    windBlock = title(c, 'vent', c.t('measured_wind'), num(age) ? c.t('ago', { n: age }) : '') +
      `<div style="display:flex;align-items:center;gap:8px;font-size:16px">${num(sw.wind_from_deg) ? arrow(18, sw.wind_from_deg, P.vent) : ''}<span><b style="color:${P.vent}">${c.fmt(sw.wind_speed_kmh)}</b>&nbsp;km/h${num(sw.wind_gust_kmh) ? ` · ${c.t('gusts')} <b style="color:${P.vent}">${c.fmt(sw.wind_gust_kmh)}</b>` : ''}${dir ? ` · ${dir}` : ''}</span></div>` +
      (sw.name ? `<div class="t3" style="font-size:13px;margin-top:4px">${c.t('station', { n: esc(titleCase(sw.name)) })}</div>` : '');
  }
  const credit = `<div class="pied">${c.t('normal_foot')}</div>`;
  if (c.wide) {
    const half = Math.floor((c.W - 28) / 2);
    return `<div class="grille" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr);align-items:start"><div>${answer}${answer ? sep() : ''}${tempsBlock(half)}</div><div>${gustBlock(half)}${windBlock ? sep() + windBlock : ''}</div></div>${credit}`;
  }
  return answer + (answer ? sep() : '') + tempsBlock(c.W) + (gustBlock(c.W) ? sep() + gustBlock(c.W) : '') + (windBlock ? sep() + windBlock : '') + credit;
}
const titleCase = (s) => String(s).toLowerCase().replace(/(^|[\s-])(\p{L})/gu, (m, a, b) => a + b.toUpperCase());
// "Is it normal?": the normal (a line) and the value of the day (a dot), on a common scale.
function tempRuler(c, w, today, normal, all) {
  const P = c.P, fs = 12, a = Math.floor(Math.min(...all) - 3), b = Math.ceil(Math.max(...all) + 3), X = (v) => 6 + ((v - a) / (b - a)) * (w - 12), id = `t${Math.random().toString(36).slice(2, 8)}`;
  return `<svg width="${w}" height="46" viewBox="0 0 ${w} 46" aria-hidden="true"><defs><linearGradient id="${id}"><stop offset="0" stop-color="${tempColour(P, a)}"/><stop offset="1" stop-color="${tempColour(P, b)}"/></linearGradient></defs>` +
    `<rect x="6" y="20" width="${w - 12}" height="8" rx="4" fill="url(#${id})" opacity=".5"/>` +
    `<line x1="${r1(X(normal))}" y1="13" x2="${r1(X(normal))}" y2="35" stroke="${P.t2}" stroke-width="3" stroke-linecap="round"/>` +
    `<text x="${r1(clamp(X(normal), 40, w - 40))}" y="45" font-size="${fs}" fill="${P.t3}" text-anchor="middle" font-weight="700">${c.t('normal_lbl', { v: c.fmt(normal, 1) })}</text>` +
    `<circle cx="${r1(X(today))}" cy="24" r="8" fill="${tempColour(P, today)}" stroke="var(--vc-carte)" stroke-width="3"/>` +
    `<text x="${r1(clamp(X(today), 16, w - 16))}" y="10" font-size="${fs + 1}" fill="${P.texte}" text-anchor="middle" font-weight="700">${c.fmt(today, 1)}°</text></svg>`;
}
function gustRuler(c, w, forecast, measured, record, recordDate) {
  const P = c.P, fs = 12, top = Math.max(60, ...[forecast, measured, record].filter(num)) * 1.1, X = (v) => 6 + (v / top) * (w - 12), id = `q${Math.random().toString(36).slice(2, 8)}`;
  let s = `<svg width="${w}" height="50" viewBox="0 0 ${w} 50" aria-hidden="true"><defs><linearGradient id="${id}"><stop offset="0" stop-color="${P.filet}"/><stop offset="1" stop-color="${P.vent}"/></linearGradient></defs>` +
    `<rect x="6" y="22" width="${w - 12}" height="8" rx="4" fill="url(#${id})" opacity=".6"/>`;
  if (num(record)) {
    const year = typeof recordDate === 'string' ? recordDate.slice(0, 4) : '';
    s += `<line x1="${r1(X(record))}" y1="15" x2="${r1(X(record))}" y2="37" stroke="${P.t2}" stroke-width="3" stroke-linecap="round"/>` +
      `<text x="${w - 2}" y="49" font-size="${fs}" fill="${P.t3}" text-anchor="end" font-weight="700">${c.t('record_lbl', { v: c.fmt(record), y: year })}</text>`;
  }
  if (num(measured)) s += `<circle cx="${r1(X(measured))}" cy="26" r="6" fill="${P.t2}" stroke="var(--vc-carte)" stroke-width="3"/><text x="${r1(clamp(X(measured), 40, w - 150))}" y="49" font-size="${fs}" fill="${P.t2}" text-anchor="middle" font-weight="700">${c.t('measured_lbl', { v: c.fmt(measured) })}</text>`;
  if (num(forecast)) s += `<circle cx="${r1(X(forecast))}" cy="26" r="8" fill="${P.vent}" stroke="var(--vc-carte)" stroke-width="3"/><text x="${r1(clamp(X(forecast), 70, w - 4))}" y="11" font-size="${fs + 1}" fill="${P.texte}" text-anchor="end" font-weight="700">${c.t('forecast_lbl', { v: c.fmt(forecast) })}</text>`;
  return s + '</svg>';
}

// Which pages have something to show for this place (a page without data is not shown, even ticked).
function availablePages(c, wanted) {
  const pt = c.point, d = c.data;
  const has = {
    home: true,
    details: !!(pt?.short_term?.steps?.length || pt?.probabilities?.short_range?.windows?.length ||
      (c.inFrance && (nowcastFirst(c) !== null || currentHours(c).length >= 12))),
    alerts: true,
    sea: !!(d?.tides || (d?.spot && d?.activities)),
    normal: c.normals?.status === 'available',
  };
  return wanted.filter((p) => has[p]);
}
const PAGE_RENDER = { home: pageHome, details: pageDetails, alerts: pageAlerts, sea: pageSea, normal: pageNormal };

// ------------------------------------------------------------------------------------------------------------------- style
// The home and Details pages (`.pile`) set their own line height: Home Assistant gives its text 1.6, which made their blocks
// taller than drawn and the card of fixed height overflow on a phone (seen on 2026-11-27). The other pages keep Home Assistant's.
const STYLE = `
:host{display:block}
ha-card{height:760px;box-sizing:border-box;display:flex;flex-direction:column;overflow:hidden;--vc-carte:var(--ha-card-background,var(--card-background-color,#19232E))}
.root{flex:1 1 auto;min-height:0;display:flex;flex-direction:column;padding:var(--vc-pad);font-weight:500;touch-action:pan-y;user-select:none;-webkit-user-select:none}
svg{display:block;overflow:visible} svg text{font-family:inherit}
button{font:inherit;color:inherit;background:none;border:0;padding:0;margin:0;cursor:pointer;text-align:left}
.entete{display:flex;align-items:center;justify-content:space-between;gap:10px;min-width:0}
.entete.mur{justify-content:flex-start;gap:16px}
.lieu{display:flex;align-items:center;gap:6px;font-weight:700;min-width:0;color:var(--vc-texte)}
.lieu span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.maj{font-size:13px;color:var(--vc-t3);white-space:nowrap}
.pastille{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:5px 12px 5px 9px;font-weight:700;white-space:nowrap}
.nav{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;gap:3px;padding:3px;border-radius:14px;background:var(--vc-piste);border:1.5px solid var(--vc-filet);margin:12px 0 16px}
.entete.mur .nav{margin:0 0 0 auto;width:min(560px,60%)}
.nav button{display:flex;align-items:center;justify-content:center;gap:6px;border-radius:10px;font-weight:700;color:var(--vc-t3);white-space:nowrap;overflow:hidden}
.nav button.actif{background:var(--vc-accent);color:var(--vc-sur)}
.nav button:focus-visible,.puce:focus-visible,.pastille:focus-visible{outline:2px solid var(--vc-accent);outline-offset:2px}
.page{color:var(--vc-texte);flex:1 1 auto;min-height:0;overflow-y:auto;overflow-x:hidden;scrollbar-width:none;outline:none}
.page::-webkit-scrollbar{display:none}
.grille{display:grid;gap:26px}
.moment{display:flex;align-items:center;gap:12px}
.temp{font-weight:700;line-height:.9;letter-spacing:-1px}
.moment .txt{display:flex;flex-direction:column;gap:4px;min-width:0}
.moment .l1{font-weight:700}
.vent{display:flex;align-items:center;gap:6px;color:var(--vc-t2)}
.pills{display:grid;gap:8px}
.puce{display:flex;align-items:center;gap:10px;border-radius:16px;padding:11px 14px;border:1.5px solid;font-weight:700;min-width:0;width:100%;color:var(--vc-texte)}
.puce small{display:block;font-weight:500;color:var(--vc-t2);font-size:13px;margin-top:2px}
.puce .det{font-weight:500;color:var(--vc-t2)}
.puce .tx{min-width:0}
.plus{display:inline-flex;align-items:center;justify-content:center;min-width:26px;height:22px;border-radius:11px;font-size:13px;padding:0 6px}
.jours{display:grid;gap:4px}
.jour{display:flex;flex-direction:column;align-items:center;gap:3px;min-width:0}
.jour .n{font-weight:700;color:var(--vc-t2)}
.jour.auj .n{color:var(--vc-accent)}
.jour .mx{font-weight:700}
.jour .mn{color:var(--vc-t3)}
.gel{height:20px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;padding:0 7px;min-width:20px;white-space:nowrap}
.titre{font-size:13px;font-weight:700;color:var(--vc-t3);display:flex;align-items:center;gap:6px;margin:0 0 8px;min-width:0}
.titre .src{margin-left:auto;padding-left:8px;font-weight:500;font-size:12px;color:var(--vc-t4);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pied{font-size:12px;color:var(--vc-t4);margin-top:12px}
.sep{height:1px;background:var(--vc-filet)}
.rep{display:flex;flex-direction:column;gap:10px}
.rep .ligne{display:flex;align-items:center;gap:12px;font-weight:700;line-height:1.15}
.rep em{font-style:normal}
.em-pluie em{color:var(--vc-pluie)} .em-vent em{color:var(--vc-vent)}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{border:1.5px solid var(--vc-filet);border-radius:999px;padding:4px 11px;color:var(--vc-t2)}
.chip b{color:var(--vc-texte)}
.frise-l{display:flex;align-items:center;gap:8px;font-weight:700;margin-top:12px}
.frise-l .fin{margin-left:auto;font-weight:500;color:var(--vc-t2);font-size:13px;white-space:nowrap}
.lien{display:inline-flex;align-items:center;gap:6px;border:1.5px solid var(--vc-filet);border-radius:999px;padding:6px 12px;font-size:13px;font-weight:700;color:var(--vc-t2);text-decoration:none}
.verd{display:grid;gap:8px}
.vd{border:1.5px solid var(--vc-filet);border-radius:14px;padding:10px 12px;min-width:0}
.vd .nom{display:flex;align-items:center;gap:6px;font-size:13px;font-weight:700;color:var(--vc-t2)}
.vd .v{font-weight:700;margin-top:3px}
.vd .r{font-size:12px;color:var(--vc-t3);margin-top:1px}
.note{font-size:12px;color:var(--vc-t4)}
.lig{display:flex;align-items:baseline;gap:10px;padding:4px 0;color:var(--vc-t2);font-size:14px}
.lig b{color:var(--vc-texte)}
.ligne2{display:flex;align-items:baseline;gap:8px;font-size:15px;font-weight:700;margin:2px 0}
.ligne2 .ecart{margin-left:auto}
.t2{color:var(--vc-t2)} .t3{color:var(--vc-t3)} .b{font-weight:700}
.vide{color:var(--vc-t2);font-size:14px;padding:8px 0}
.pile{display:flex;flex-direction:column;min-width:0;line-height:normal}
.duo{display:grid;gap:20px;align-items:center}
.bloc{display:flex;flex-direction:column;gap:4px;min-width:0}
.st{display:flex;justify-content:space-between;align-items:center;gap:10px;height:16px;min-width:0}
.st b{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--vc-t3);white-space:nowrap}
.st>span{display:block;font-size:10.5px;color:var(--vc-t4);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.ramp{display:inline-block;width:72px;height:8px;border-radius:4px;vertical-align:middle;margin:0 6px}
.now{display:flex;align-items:center;gap:8px;min-width:0;flex:none}
.now .temp{font-weight:700;line-height:1;letter-spacing:-1px}
.now-t{display:flex;flex-direction:column;gap:3px;flex:1 1 auto;min-width:0}
.now-t>*{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.now-t>span{font-size:12px;color:var(--vc-t2)}
.now-t .nl{display:flex;align-items:center;gap:4px;font-size:13px}
.now-f{display:flex;flex-direction:column;gap:2px;flex:none;color:var(--vc-t2);white-space:nowrap}
.now-f>span{display:block;overflow:hidden;text-overflow:ellipsis}
.now-f svg{display:inline-block;vertical-align:-1px;margin-right:4px}
.tuile{display:flex;align-items:center;gap:10px;height:56px;box-sizing:border-box;padding:0 12px;border-radius:12px;border:1px solid;width:100%;color:var(--vc-texte);flex:none}
.tuile .tt{display:flex;flex-direction:column;gap:1px;flex:0 1 auto;min-width:0}
.tuile .tt b{font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tuile .tt small{font-size:12px;color:var(--vc-t2);font-weight:500;line-height:1.2;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}
.tuile .tb{flex:1 1 auto;display:flex;flex-direction:column;gap:3px;min-width:60px;container-type:inline-size}
.tuile .bars{height:30px;display:flex;align-items:flex-end;gap:4px}
.tuile .bars i,.bars5 i{flex:1 1 0;border-radius:3px;display:block}
.tuile .tl{display:flex;justify-content:space-between;font-size:10px;color:var(--vc-t3);font-weight:500;white-space:nowrap}
@container (max-width:130px){.tuile .tl span:nth-child(2){display:none}}
@container (max-width:80px){.tuile .tl{display:none}}
.cols{position:relative;flex:none}
.cl{position:absolute;top:0;box-sizing:border-box}
.gr{position:absolute;left:0;right:0;display:grid;justify-items:center}
.ov{position:absolute;left:0}
.lb{position:absolute;text-align:center;font-weight:700;line-height:1.2;white-space:nowrap}
.lb2{position:absolute;font-size:10px;color:var(--vc-t4);white-space:nowrap;line-height:1.2}
.lb2.pk{font-size:12px;font-weight:700}
.lb2.tk{text-align:center;font-weight:700;color:var(--vc-t3)}
.lb2.dn{font-weight:700;color:var(--vc-t2)}
.pl{display:flex;flex-direction:column;align-items:center;justify-content:flex-end;gap:2px;height:38px;width:100%;font-size:10px;font-weight:700}
.pl i{display:block;width:min(18px,70%);border-radius:4px}
.pc{font-size:10px;color:var(--vc-t3);white-space:nowrap}
.vt{display:flex;flex-direction:column;align-items:center;line-height:1.25}
.vt span{display:flex;align-items:center;gap:2px;font-size:12px;font-weight:700;color:var(--vc-vent)}
.vt small{font-size:10px}
.hr{font-size:11px;font-weight:700;white-space:nowrap}
.dh{display:flex;flex-direction:column;align-items:center;gap:1px}
.dh b{font-size:13px;white-space:nowrap}
.dh .ds{display:flex;align-items:center;gap:3px;height:13px;font-size:11px;font-weight:600;white-space:nowrap}
.dh .ds i{display:block;width:5px;height:5px;border-radius:50%}
.db{display:flex;flex-direction:column;align-items:center;gap:2px;width:100%}
.db .bw{height:24px;width:100%;display:flex;align-items:flex-end;justify-content:center}
.db .bw i{display:block;width:min(26px,55%);border-radius:4px}
.db b{font-size:12px;white-space:nowrap}
.vt2{display:flex;align-items:center;gap:2px;margin-top:3px;font-size:12px;font-weight:700;color:var(--vc-vent);white-space:nowrap}
.vt2 small{font-size:10px;font-weight:400;color:var(--vc-t3)}
.serre .db b{font-size:11px}.serre .vt2{font-size:11px;gap:1px}.serre .vt2 small{font-size:9px}
.chipsr{display:flex;flex-wrap:wrap;gap:6px}
.pp{height:28px;box-sizing:border-box;display:flex;align-items:center;gap:5px;padding:0 9px;border-radius:14px;border:1px solid;font-size:13px;font-weight:700;color:var(--vc-texte);white-space:nowrap}
.pp i{width:8px;height:8px;border-radius:4px;flex:none}
.pp span{font-weight:400;color:var(--vc-t2)}
.dl{position:absolute;left:0;border-top:1px dashed var(--vc-creux)}
.bars5{position:absolute;left:0;top:0;height:50px;display:flex;align-items:flex-end;gap:4px}
.bars5 i{border-radius:4px 4px 2px 2px}
.bars5.lbl{height:auto;align-items:flex-start}
.bars5.lbl span{flex:1 1 0;text-align:center;white-space:nowrap;font-size:10px;overflow:visible;min-width:0}
.run{position:absolute;top:52px;height:20px;box-sizing:border-box;border-radius:5px;border:1px solid;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;overflow:hidden;white-space:nowrap}
.ml{position:absolute;top:0;height:92px;border-left:1px dashed var(--vc-creux)}
.b48{display:block;border-radius:3px 3px 1px 1px}
.w3{position:absolute;display:flex;align-items:center;justify-content:center;gap:1px;font-size:10px;font-weight:700;color:var(--vc-vent);white-space:nowrap}
.pe{display:grid;gap:3px;align-items:center}
.pe .h{font-size:10px;font-weight:700;color:var(--vc-t3);text-align:center;white-space:nowrap}
.pe .r{font-size:12px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pe .v{height:24px;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700}
`;

// -------------------------------------------------------------------------------------------------------------------- card
class VigieCard extends HTMLElement {
  static getConfigElement() { return document.createElement(EDITOR_TAG); }
  static getStubConfig() { return { spot: 'auto' }; }

  setConfig(config) {
    if (!config || typeof config !== 'object') throw new Error('Invalid configuration');
    const pages = Array.isArray(config.pages) ? config.pages.filter((p) => PAGE_IDS.includes(p)) : PAGE_IDS;
    if (!pages.length) throw new Error('pages: at least one of home, details, alerts, sea, normal');
    this._config = { ...config, _pages: pages };
    if (!this._page || !pages.includes(this._page)) this._page = pages.includes(config.start_page) ? config.start_page : pages[0];
    const key = lastKey(this._config);
    if (key !== this._dataKey) { this._dataKey = key; this._data = lastAnswer(key); this._error = null; }
    if (this._hass) this._fetch();
    this._subscribeForecast();
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    const lang = String(hass?.locale?.language || hass?.language || 'en').toLowerCase().startsWith('fr') ? 'fr' : 'en';
    const dark = !!hass?.themes?.darkMode;
    const ent = this._config?.weather_entity ? hass?.states?.[this._config.weather_entity] : null;
    const changed = lang !== this._lang || dark !== this._dark || ent !== this._entity;
    this._lang = lang; this._dark = dark; this._entity = ent;
    if (first) { this._fetch(); this._subscribeForecast(); }
    if (changed || first) this._render();
  }

  // A fixed size, the one of the sketches (480 x 760 px): 12 rows and the whole width of a section of the sections grid (half
  // of it, about 240 px, cuts its texts), 15 units of 50 px elsewhere; a page longer than the card scrolls inside it.
  getCardSize() { return 15; }
  getGridOptions() { return { columns: 12, min_columns: 12, rows: 12, min_rows: 12, max_rows: 12 }; }

  connectedCallback() {
    this._timer = setInterval(() => this._fetch(), REFRESH_MS);
    this._tick = setInterval(() => this._idle(), 5000);
    this._touched = Date.now();
    if (!this._resize && 'ResizeObserver' in window) {
      this._resize = new ResizeObserver((entries) => {
        const w = Math.round(entries[0].contentRect.width);
        if (w && w !== this._width) { this._width = w; this._render(); }
      });
    }
    this._resize?.observe(this);
    // Drawn before the fonts are there, the home page was measured with others: once they are, it is drawn and measured again.
    document.fonts?.ready?.then(() => { if (this.isConnected) this._render(); });
    if (this._hass && this._data) this._fetch();
    this._subscribeForecast();
  }

  disconnectedCallback() {
    clearInterval(this._timer); clearInterval(this._tick);
    this._resize?.disconnect();
    this._unsubscribeForecast();
  }

  async _fetch() {
    if (!this._hass || !this._config || this._asking) return;
    const key = this._dataKey;
    const spot = this._config.spot === 'none' ? null : this._config.spot || 'auto';
    const msg = { type: 'vigie/card/data', spot };
    if (this._config.entry_id) msg.entry_id = this._config.entry_id;
    this._asking = true;
    try {
      const data = await this._hass.callWS(msg);
      keepAnswer(key, data);
      if (key === this._dataKey) { this._data = data; this._error = null; }
    } catch (err) {
      if (key === this._dataKey) this._error = err?.code || 'error';
    } finally {
      this._asking = false;
    }
    if (key !== this._dataKey) { this._fetch(); return; } // the editor changed the entry or the spot meanwhile
    this._render();
  }

  // Outside France, the home page shows the weather entity of the home chosen in the editor, with its days.
  async _subscribeForecast() {
    const id = this._config?.weather_entity;
    if (!this._hass?.connection || !id || !this.isConnected) return;
    if (this._forecastFor === id && this._unsub) return;
    this._unsubscribeForecast();
    this._forecastFor = id;
    try {
      this._unsub = await this._hass.connection.subscribeMessage((msg) => { this._forecast = msg?.forecast || []; this._render(); },
        { type: 'weather/subscribe_forecast', entity_id: id, forecast_type: 'daily' });
    } catch (err) {
      this._forecast = [];
    }
  }
  _unsubscribeForecast() {
    if (this._unsub) { try { this._unsub(); } catch (err) { /* already closed */ } }
    this._unsub = null; this._forecastFor = null;
  }

  _idle() {
    const idle = this._config?.idle || 'return';
    const quiet = Date.now() - (this._touched || 0);
    if (idle === 'cycle' && quiet >= CYCLE_MS && Date.now() - (this._cycled || 0) >= CYCLE_MS) {
      const pages = this._visible || [];
      if (pages.length > 1) { this._page = pages[(pages.indexOf(this._page) + 1) % pages.length]; this._cycled = Date.now(); this._render(); }
    } else if (idle === 'return' && quiet >= IDLE_RETURN_MS) {
      const start = this._startPage();
      if (start && this._page !== start) { this._page = start; this._render(); }
    }
  }
  _startPage() {
    const pages = this._visible || [];
    return pages.includes(this._config?.start_page) ? this._config.start_page : pages[0];
  }

  _go(page) {
    if (!this._visible?.includes(page)) return;
    this._page = page; this._touched = Date.now(); this._render();
  }

  _render() {
    if (!this._config) return;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: 'open' });
      this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card><div class="root" part="content"></div></ha-card>`;
      const root = this.shadowRoot.querySelector('.root');
      root.addEventListener('click', (ev) => { const el = ev.target.closest('[data-go]'); if (el) { ev.stopPropagation(); this._go(el.dataset.go); } });
      root.addEventListener('pointerdown', (ev) => { this._touched = Date.now(); this._swipe = { x: ev.clientX, y: ev.clientY }; });
      root.addEventListener('pointerup', (ev) => {
        const s = this._swipe; this._swipe = null;
        if (!s) return;
        const dx = ev.clientX - s.x, dy = ev.clientY - s.y;
        if (Math.abs(dx) < 50 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
        const pages = this._visible || [], i = pages.indexOf(this._page);
        const next = pages[clamp(i + (dx < 0 ? 1 : -1), 0, pages.length - 1)];
        if (next && next !== this._page) this._go(next);
      });
      root.addEventListener('keydown', (ev) => { if (ev.key === 'ArrowRight' || ev.key === 'ArrowLeft') { const pages = this._visible || [], i = pages.indexOf(this._page); const next = pages[clamp(i + (ev.key === 'ArrowRight' ? 1 : -1), 0, pages.length - 1)]; if (next) this._go(next); } });
    }
    const root = this.shadowRoot.querySelector('.root');
    // The blocks are drawn to the pixel: the width inside the border of ha-card (1 px each side by default).
    const width = (this._width || this.getBoundingClientRect().width || 380) - 2;
    const lang = this._lang || 'en';
    const config = { ...this._config, _weatherState: this._entity || null, _forecast: this._forecast || [] };
    const c = makeContext(lang, !!this._dark, width, this._data, config);
    const P = c.P;
    const vars = { carte: null, texte: P.texte, t2: P.t2, t3: P.t3, t4: P.t4, filet: P.filet, accent: P.accent, sur: P.sur, pluie: P.pluie, vent: P.vent, creux: P.creux, piste: this._dark ? 'rgba(255,255,255,.04)' : 'rgba(0,0,0,.03)' };
    root.style.cssText = Object.entries(vars).filter(([, v]) => v).map(([k, v]) => `--vc-${k}:${v}`).join(';') + `;--vc-pad:${c.pad}px`;
    if (this._error || !this._data) {
      const words = this._error === 'not_found' ? c.t('not_found') : this._error === 'weather_off' ? c.t('weather_off') : this._error ? c.t('error') : c.t('loading');
      root.innerHTML = `<div class="vide">${words}</div>`;
      return;
    }
    const visible = availablePages(c, this._config._pages);
    this._visible = visible;
    config._pages = visible;
    if (!visible.includes(this._page)) this._page = this._startPage() || visible[0];
    const head = header(c);
    const bar = visible.length > 1 ? nav(c, visible, this._page) : '';
    const top = c.wide ? `<div class="entete mur">${head.name}${head.right}${bar}</div><div style="height:22px"></div>` : `<div class="entete">${head.name}${head.right}</div>${bar || '<div style="height:12px"></div>'}`;
    let body = '';
    try {
      body = PAGE_RENDER[this._page](c) || '';
    } catch (err) {
      console.error('Vigie card:', err);
      body = `<div class="vide">${c.t('error')}</div>`;
    }
    root.innerHTML = `${top}<div class="page" tabindex="0">${body}</div>`;
    // The home page fits the card without scrolling: drawn a few px too tall (fonts, a long header, a pill of the season), it is
    // drawn again with flatter curves, by what it is over as measured here, at most three times (the blocks round their
    // heights; nothing to measure while the card is hidden).
    // The lines of "now" are measured the same way: one cut (a longer word than in French, a wider font) draws the block again
    // without the gust of now, then without the humidity.
    const page = root.querySelector('.page'), cut = () => [...page.querySelectorAll('.now-t>*')].some((e) => e.scrollWidth > e.clientWidth + 0.5);
    for (let i = 0; i < 2 && this._page === 'home' && c.inFrance && cut(); i++) {
      c.nowLess = (c.nowLevel || 0) + 1;
      try { page.innerHTML = pageHome(c); } catch (err) { console.error('Vigie card:', err); break; }
    }
    for (let i = 0; i < 3 && this._page === 'home' && c.inFrance; i++) {
      const over = page.scrollHeight - page.clientHeight;
      if (over <= 0) break;
      c.squeeze = (c.squeeze || 0) + over;
      try { page.innerHTML = pageHome(c); } catch (err) { console.error('Vigie card:', err); break; }
    }
  }
}

// ------------------------------------------------------------------------------------------------------------------ editor
async function loadHaForm() {
  if (customElements.get('ha-form')) return true;
  try {
    const helpers = await window.loadCardHelpers?.();
    const card = await helpers?.createCardElement({ type: 'entities', entities: [] });
    await card?.constructor?.getConfigElement?.();
  } catch (err) { /* the fallback text says it */ }
  await Promise.race([customElements.whenDefined('ha-form'), new Promise((resolve) => setTimeout(resolve, 4000))]);
  return !!customElements.get('ha-form');
}

class VigieCardEditor extends HTMLElement {
  setConfig(config) { this._config = { ...config }; this._render(); }
  set hass(hass) {
    this._hass = hass;
    this._lang = String(hass?.locale?.language || hass?.language || 'en').toLowerCase().startsWith('fr') ? 'fr' : 'en';
    if (this._form) this._form.hass = hass;
    if (!this._loading) { this._loading = true; this._load(); }
  }
  async _load() {
    try { this._entries = await this._hass.callWS({ type: 'vigie/card/entries' }); } catch (err) { this._entries = []; }
    await this._loadSpots();
    this._formReady = await loadHaForm();
    this._render();
  }
  async _loadSpots() {
    const msg = { type: 'vigie/card/spots' };
    if (this._config?.entry_id) msg.entry_id = this._config.entry_id;
    try { this._spots = await this._hass.callWS(msg); } catch (err) { this._spots = []; }
  }
  _t(key) { return EDITOR_TEXT[this._lang || 'en'][key] ?? key; }
  _changed(config) {
    const clean = Object.fromEntries(Object.entries(config).filter(([k, v]) => !k.startsWith('_') && v !== undefined && v !== '' && v !== null));
    const before = this._config?.entry_id;
    this._config = clean;
    this.dispatchEvent(new CustomEvent('config-changed', { detail: { config: clean }, bubbles: true, composed: true }));
    if (clean.entry_id !== before) this._loadSpots().then(() => this._render()); else this._render();
  }
  _render() {
    if (!this._hass || !this._config) return;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: 'open' });
      this.shadowRoot.innerHTML = `<style>
        .pages{border:1px solid var(--divider-color,#ccc);border-radius:8px;margin:8px 0 4px}
        .row{display:flex;align-items:center;gap:8px;padding:6px 8px;border-bottom:1px solid var(--divider-color,#ddd);cursor:grab;color:var(--primary-text-color)}
        .row:last-child{border-bottom:0} .row.drag{opacity:.4}
        .row label{flex:1;display:flex;align-items:center;gap:8px;cursor:pointer}
        .row button{background:none;border:1px solid var(--divider-color,#ccc);border-radius:6px;color:var(--primary-text-color);cursor:pointer;width:32px;height:28px}
        .row button:disabled{opacity:.3;cursor:default}
        h4{margin:16px 0 0;font-weight:500;color:var(--primary-text-color)} .help{font-size:12px;color:var(--secondary-text-color)}
        .fallback{color:var(--secondary-text-color);font-size:14px;margin-top:12px}
      </style><div id="pages"></div><div id="form"></div>`;
    }
    this._renderPages();
    const holder = this.shadowRoot.getElementById('form');
    if (!this._formReady) { holder.innerHTML = this._formReady === false ? `<div class="fallback">${this._t('no_form')}</div>` : ''; return; }
    if (!this._form) {
      this._form = document.createElement('ha-form');
      this._form.addEventListener('value-changed', (ev) => this._changed({ ...this._config, ...ev.detail.value }));
      holder.appendChild(this._form);
    }
    const lang = this._lang || 'en', T = TEXT[lang];
    const entries = (this._entries || []).filter((e) => e.weather_and_card);
    const spots = this._spots || [];
    const order = this._order();
    this._form.hass = this._hass;
    this._form.schema = [
      { name: 'entry_id', selector: { select: { mode: 'dropdown', options: entries.map((e) => ({ value: e.entry_id, label: e.title })) } } },
      { name: 'name', selector: { text: {} } },
      { name: 'start_page', selector: { select: { mode: 'dropdown', options: order.filter((p) => this._shown().includes(p)).map((p) => ({ value: p, label: T[`page_${p}`] })) } } },
      { name: 'idle', selector: { select: { mode: 'dropdown', options: ['return', 'cycle', 'none'].map((v) => ({ value: v, label: this._t(`idle_${v}`) })) } } },
      { name: 'spot', selector: { select: { mode: 'dropdown', options: [{ value: 'auto', label: this._t('spot_auto') }, { value: 'none', label: this._t('spot_none') },
        ...spots.map((s) => ({ value: s.id, label: `${s.name || s.id}${s.place ? `, ${s.place}` : ''} (${String(s.distance_km).replace('.', lang === 'fr' ? ',' : '.')} km)` }))] } } },
      { name: 'weather_entity', selector: { entity: { domain: 'weather' } } },
      { name: 'icon_style', selector: { select: { mode: 'dropdown', options: ICON_STYLES.map((v) => ({ value: v, label: this._t(`icons_${v}`) })) } } },
    ];
    this._form.data = { idle: 'return', spot: 'auto', start_page: 'home', icon_style: 'meridien', ...this._config };
    this._form.computeLabel = (s) => this._t(s.name);
    this._form.computeHelper = (s) => (s.name === 'weather_entity' || s.name === 'icon_style' ? this._t(`${s.name}_help`) : undefined);
  }
  _order() {
    const chosen = Array.isArray(this._config.pages) ? this._config.pages.filter((p) => PAGE_IDS.includes(p)) : [];
    const order = Array.isArray(this._config._order) ? this._config._order : null;
    return order || [...chosen, ...PAGE_IDS.filter((p) => !chosen.includes(p))];
  }
  _shown() { return Array.isArray(this._config.pages) ? this._config.pages.filter((p) => PAGE_IDS.includes(p)) : PAGE_IDS; }
  _setPages(order, shown) {
    const pages = order.filter((p) => shown.includes(p));
    if (!pages.length) return;
    const config = { ...this._config, pages };
    if (pages.length === PAGE_IDS.length && pages.every((p, i) => p === PAGE_IDS[i])) delete config.pages;
    this._changed(config);
  }
  _renderPages() {
    const holder = this.shadowRoot.getElementById('pages');
    const T = TEXT[this._lang || 'en'], order = this._order(), shown = this._shown();
    holder.innerHTML = `<h4>${this._t('pages')}</h4><div class="help">${this._t('pages_help')}</div><div class="pages">` + order.map((p, i) =>
      `<div class="row" draggable="true" data-page="${p}"><label><input type="checkbox" ${shown.includes(p) ? 'checked' : ''} data-check="${p}">${T[`page_${p}`]}</label>` +
      `<button data-move="${p}" data-by="-1" title="${this._t('up')}" aria-label="${this._t('up')}" ${i === 0 ? 'disabled' : ''}>▲</button>` +
      `<button data-move="${p}" data-by="1" title="${this._t('down')}" aria-label="${this._t('down')}" ${i === order.length - 1 ? 'disabled' : ''}>▼</button></div>`).join('') + `</div>`;
    holder.querySelectorAll('[data-check]').forEach((box) => box.addEventListener('change', () => {
      const next = box.checked ? [...shown, box.dataset.check] : shown.filter((p) => p !== box.dataset.check);
      this._setPages(order, next);
    }));
    holder.querySelectorAll('[data-move]').forEach((btn) => btn.addEventListener('click', () => {
      const i = order.indexOf(btn.dataset.move), j = i + Number(btn.dataset.by);
      if (j < 0 || j >= order.length) return;
      const next = [...order]; [next[i], next[j]] = [next[j], next[i]];
      this._setPages(next, shown);
    }));
    let dragged = null;
    holder.querySelectorAll('.row').forEach((row) => {
      row.addEventListener('dragstart', () => { dragged = row.dataset.page; row.classList.add('drag'); });
      row.addEventListener('dragend', () => row.classList.remove('drag'));
      row.addEventListener('dragover', (ev) => ev.preventDefault());
      row.addEventListener('drop', (ev) => {
        ev.preventDefault();
        if (!dragged || dragged === row.dataset.page) return;
        const next = order.filter((p) => p !== dragged);
        next.splice(next.indexOf(row.dataset.page) + (order.indexOf(dragged) < order.indexOf(row.dataset.page) ? 1 : 0), 0, dragged);
        this._setPages(next, shown);
      });
    });
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, VigieCard);
if (!customElements.get(EDITOR_TAG)) customElements.define(EDITOR_TAG, VigieCardEditor);
window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === TAG)) {
  window.customCards.push({
    type: TAG,
    name: 'Vigie',
    description: 'The weather, the warnings, the sea and the normals of a place, on pages (Vigie integration).',
    preview: true,
    documentationURL: 'https://github.com/Axellum/vigie-api/blob/main/docs/home-assistant.md',
  });
}
