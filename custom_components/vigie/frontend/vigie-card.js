// The Vigie card for Home Assistant (ADR 0042 of the API): the weather, the warnings, the sea and the normals of one place, on
// pages one swipes or picks in the bar at the top. It is shipped inside the integration and registered by it, and it asks its data
// of Home Assistant (WebSocket command `vigie/card/data`), never of the instance. A value the data does not give is not drawn:
// no empty box, no guess. Colours: the "Sombre uni" and "Clair" themes of the Vigie family, chosen by the dark mode of Home
// Assistant (the background of the card stays the dashboard's own).

const TAG = 'vigie-card';
const EDITOR_TAG = 'vigie-card-editor';
const PAGE_IDS = ['home', 'details', 'alerts', 'sea', 'normal'];
const REFRESH_MS = 5 * 60 * 1000; // the integration reads the instance every 10 minutes
const IDLE_RETURN_MS = 60 * 1000;
const CYCLE_MS = 20 * 1000;
const WIDE_PX = 640;

// ------------------------------------------------------------------------------------------------------------------ colours
const PALETTES = {
  dark: {
    texte: '#F4F7FA', t2: '#CBD5DF', t3: '#A3B1C0', t4: '#7D8B9B', filet: '#2C3947', accent: '#3DDC97', sur: '#10151C',
    vert: '#6FE3B0', jaune: '#FFD25C', orange: '#FFA24D', rouge: '#FF6B5E', inconnu: '#A3B1C0',
    vent: '#7FD1FF', pluie: '#58B4FF', pluieFaible: '#7FD1FF', pluieForte: '#3A8CF0',
    ramp: ['#7FB2FF', '#5CD6FF', '#7BE495', '#FFC94A', '#FF6B4A'],
    icon: { soleil: '#FFC83D', lune: '#EFE9CF', nuage: '#E3EAF2', nuageSombre: '#8F9DB2', eau: '#58B4FF', neige: '#FFFFFF', eclair: '#FFD93D', brume: '#AAB6C4' },
  },
  light: {
    texte: '#12171C', t2: '#37424E', t3: '#4F5C6A', t4: '#5F6B78', filet: '#D5DBE2', accent: '#0E8F5F', sur: '#FFFFFF',
    vert: '#1B8A5A', jaune: '#8A6400', orange: '#B45309', rouge: '#C22F2F', inconnu: '#4F5C6A',
    vent: '#0F78B0', pluie: '#1E78D6', pluieFaible: '#4A9BE8', pluieForte: '#155DB0',
    ramp: ['#2F6FDB', '#0A88B8', '#2E9E55', '#C77A00', '#D0391E'],
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
    mm_hour: '{mm}&nbsp;mm dans l’heure', until: 'jusqu’à {t}', from: 'dès {t}',
    no_warning: 'Aucune vigilance en cours', warnings_unknown: 'Vigilances inconnues', dry: 'sec',
    data: 'Données', relayed: 'relayées par Vigie',
    next_hours: 'Les {n} prochaines heures', rain: 'Pluie', gust_lbl: 'Rafales', step15: 'pas de 15&nbsp;min',
    hail: 'Grêle', hail_none: 'non', hail_small_hail: 'petite grêle', hail_hail: 'grêle',
    visibility: 'Visibilité', vis_none: 'bonne', vis_reduced: 'réduite', vis_very_reduced: 'très réduite',
    gust93: 'Rafales de 93&nbsp;km/h et plus : <b>{p}&nbsp;%</b> d’ici {t}',
    chances: 'Chances par {h}&nbsp;h', legend_rain: 'pluie', legend_gust: 'rafales ≥&nbsp;56&nbsp;km/h',
    precip_in: '{kind} dans <em>{n}&nbsp;min</em>, {how}jusqu’à&nbsp;{t}', precip_at: '{kind} à <em>{at}</em>, {how}jusqu’à&nbsp;{t}',
    precip_now: '{kind} {how}jusqu’à&nbsp;<em>{t}</em>', precip_none: 'Pas de pluie d’ici&nbsp;{t}', on_and_off: 'par&nbsp;moments ',
    gusts_at: 'Rafales à <em>{v}&nbsp;km/h</em> vers&nbsp;{t}',
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
    mm_hour: '{mm}&nbsp;mm in the hour', until: 'until {t}', from: 'from {t}',
    no_warning: 'No warning in force', warnings_unknown: 'Warnings unknown', dry: 'dry',
    data: 'Data', relayed: 'relayed by Vigie',
    next_hours: 'The next {n} hours', rain: 'Rain', gust_lbl: 'Gusts', step15: '15-minute steps',
    hail: 'Hail', hail_none: 'no', hail_small_hail: 'small hail', hail_hail: 'hail',
    visibility: 'Visibility', vis_none: 'good', vis_reduced: 'reduced', vis_very_reduced: 'very reduced',
    gust93: 'Gusts of 93&nbsp;km/h or more: <b>{p}&nbsp;%</b> until {t}',
    chances: 'Chances per {h}&nbsp;h', legend_rain: 'rain', legend_gust: 'gusts ≥&nbsp;56&nbsp;km/h',
    precip_in: '{kind} in <em>{n}&nbsp;min</em>, {how}until&nbsp;{t}', precip_at: '{kind} at <em>{at}</em>, {how}until&nbsp;{t}',
    precip_now: '{kind} {how}until&nbsp;<em>{t}</em>', precip_none: 'No rain until&nbsp;{t}', on_and_off: 'on&nbsp;and&nbsp;off ',
    gusts_at: 'Gusts of <em>{v}&nbsp;km/h</em> around&nbsp;{t}',
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
const CONDITION_ICON = { 'clear-night': 'lune', cloudy: 'nuage', exceptional: 'nuage', fog: 'brume', hail: 'averse', lightning: 'orage',
  'lightning-rainy': 'orage', partlycloudy: 'peuNuageux', pouring: 'averse', rainy: 'pluie', snowy: 'neige', 'snowy-rainy': 'neige', sunny: 'soleil',
  windy: 'nuage', 'windy-variant': 'nuage' };
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
    up: 'Monter', down: 'Descendre', no_form: 'L’éditeur visuel ne s’est pas chargé : la carte se règle aussi en YAML (voir la documentation).' },
  en: { entry_id: 'Place (Vigie entry)', name: 'Name shown', pages: 'Pages shown and their order', pages_help: 'Drag a row or use the arrows to change the order. A page with no data for the place is not shown, even ticked.',
    start_page: 'Opening page', idle: 'Screen nobody touches', idle_return: 'Back to the opening page after a minute', idle_cycle: 'Go through the pages every 20 seconds',
    idle_none: 'Stay on the page chosen', spot: 'Spot of the Sea page', spot_auto: 'The nearest (within 20 km)', spot_none: 'None',
    weather_entity: 'Outside France: weather entity of the home', weather_entity_help: 'For the home page of a place outside France; without it the home page shows the warnings, the earthquakes and the tide.',
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
const cloudShape = (f) => `<g fill="${f}"><circle cx="36" cy="58" r="16"/><circle cx="55" cy="48" r="22"/><circle cx="74" cy="60" r="14"/><rect x="22" y="58" width="64" height="22" rx="11"/></g>`;
function rays(cx, cy, a0, a1, n, c, sw) {
  let s = '';
  for (let i = 0; i < n; i++) { const a = rad((i * 360) / n); s += `<line x1="${r1(cx + Math.cos(a) * a0)}" y1="${r1(cy + Math.sin(a) * a0)}" x2="${r1(cx + Math.cos(a) * a1)}" y2="${r1(cy + Math.sin(a) * a1)}" stroke="${c}" stroke-width="${sw}" stroke-linecap="round"/>`; }
  return s;
}
const drop = (I, x, y, l = 16, sw = 6) => `<line x1="${x}" y1="${y}" x2="${r1(x - l * 0.38)}" y2="${y + l}" stroke="${I.eau}" stroke-width="${sw}" stroke-linecap="round"/>`;
const moonPath = (I) => `<path d="M12 -26 A28 28 0 1 0 26 12 A22 22 0 1 1 12 -26 Z" fill="${I.lune}"/>`;
const ICONS = {
  soleil: (I) => `<circle cx="50" cy="50" r="19" fill="${I.soleil}"/>` + rays(50, 50, 29, 41, 8, I.soleil, 6),
  lune: (I) => `<g transform="translate(50 50)">${moonPath(I)}</g>`,
  peuNuageux: (I) => `<circle cx="38" cy="38" r="15" fill="${I.soleil}"/>` + rays(38, 38, 22, 31, 8, I.soleil, 5) + `<g transform="translate(6 8)">${cloudShape(I.nuage)}</g>`,
  nuitNuageuse: (I) => `<g transform="translate(40 38) scale(.62)">${moonPath(I)}</g><g transform="translate(6 8)">${cloudShape(I.nuage)}</g>`,
  nuage: (I) => `<g transform="translate(-8 -10) scale(.82)">${cloudShape(I.nuageSombre)}</g><g transform="translate(2 4)">${cloudShape(I.nuage)}</g>`,
  pluie: (I) => `<g transform="translate(0 -12)">${cloudShape(I.nuageSombre)}</g>` + drop(I, 34, 70) + drop(I, 52, 74) + drop(I, 70, 70),
  averse: (I) => `<g transform="translate(0 -14)">${cloudShape(I.nuageSombre)}</g>` + drop(I, 30, 68, 20, 7) + drop(I, 48, 72, 20, 7) + drop(I, 66, 68, 20, 7) + drop(I, 80, 74, 14, 6),
  orage: (I) => `<g transform="translate(0 -14)">${cloudShape(I.nuageSombre)}</g><polygon points="56,58 40,82 51,82 45,100 68,72 56,72 64,58" fill="${I.eclair}"/>`,
  neige: (I) => `<g transform="translate(0 -12)">${cloudShape(I.nuageSombre)}</g>` + [[34, 78], [52, 86], [70, 78]].map(([x, y]) => `<circle cx="${x}" cy="${y}" r="5.5" fill="${I.neige}"/>`).join(''),
  brume: (I) => [[20, 38, 76], [30, 54, 86], [20, 70, 70]].map(([a, y, b]) => `<line x1="${a}" y1="${y}" x2="${b}" y2="${y}" stroke="${I.brume}" stroke-width="7" stroke-linecap="round"/>`).join(''),
};
function wmoKind(code) {
  if (!num(code)) return null;
  if (code === 0) return 'soleil';
  if (code <= 2) return 'peuNuageux';
  if (code === 3) return 'nuage';
  if (code === 45 || code === 48) return 'brume';
  if ((code >= 71 && code <= 77) || code === 85 || code === 86) return 'neige';
  if (code >= 51 && code <= 67) return 'pluie';
  if (code >= 80 && code <= 82) return 'averse';
  if (code >= 95) return 'orage';
  return 'nuage';
}
const atNight = (kind, isDay) => (isDay === false ? (kind === 'soleil' ? 'lune' : kind === 'peuNuageux' ? 'nuitNuageuse' : kind) : kind);
const icon = (P, kind, s) => (kind && ICONS[kind] ? `<svg width="${s}" height="${s}" viewBox="0 0 100 100" aria-hidden="true">${ICONS[kind](P.icon)}</svg>` : `<span style="display:inline-block;width:${s}px;height:${s}px"></span>`);
const iconAt = (P, kind, cx, cy, s) => (kind && ICONS[kind] ? `<g transform="translate(${r1(cx - s / 2)} ${r1(cy - s / 2)}) scale(${s / 100})">${ICONS[kind](P.icon)}</g>` : '');
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
  const P = dark ? PALETTES.dark : PALETTES.light;
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
    const col = c.P[LEVEL_COLOUR[level]];
    right = `<button class="pastille" data-go="alerts" style="background:${col};color:${c.P.sur};font-size:${c.wide ? 17 : 14}px">${glyph('danger', 16, c.P.sur, 2.4)}${c.t('warning', { level: c.t(`level_${level}`) })}</button>`;
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

// The 12 hours: temperature curve, icons above, rain pills below.
function hoursCurve(c, w, h) {
  const all = currentHours(c).slice(0, 12).filter((x) => num(x.temp_c));
  if (all.length < 4) return '';
  const P = c.P, n = all.length, fs = c.wide ? Math.round(16 * c.k) : 13, ti = c.wide ? Math.round(44 * c.k) : 26, hp = c.wide ? 34 : 24, pad = c.wide ? 22 : 14;
  const yTop = c.wide ? 66 : 44, yPil = h - fs - 12 - hp, yBas = yPil - fs - 14;
  const temps = all.map((x) => x.temp_c), mn = Math.min(...temps), mx = Math.max(...temps), id = `g${Math.random().toString(36).slice(2, 8)}`;
  const amp = Math.max(4, mx - mn), low = (mn + mx) / 2 - amp / 2; // at least 4 °C from top to bottom: 2° must not look like a fall
  const pts = all.map((x, i) => ({ x: pad + (i * (w - 2 * pad)) / (n - 1), y: yBas - 6 - ((x.temp_c - low) / amp) * (yBas - yTop - 26) }));
  let s = `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><defs><linearGradient id="${id}" x1="0" x2="1" y1="0" y2="0">` +
    temps.map((t, i) => `<stop offset="${r1((i / (n - 1)) * 100)}%" stop-color="${tempColour(P, t)}"/>`).join('') + `</linearGradient>` +
    `<linearGradient id="${id}v" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${tempColour(P, (mn + mx) / 2)}" stop-opacity=".30"/><stop offset="1" stop-color="${tempColour(P, (mn + mx) / 2)}" stop-opacity="0"/></linearGradient></defs>`;
  const d = smooth(pts);
  s += `<path d="${d} L${r1(pts[n - 1].x)} ${yBas + 4} L${r1(pts[0].x)} ${yBas + 4} Z" fill="url(#${id}v)"/><path d="${d}" fill="none" stroke="url(#${id})" stroke-width="${c.wide ? 5.5 : 4.5}" stroke-linecap="round"/>`;
  const step = (w - 2 * pad) / (n - 1) < 50 ? 2 : 1; // an icon every hour only where they do not touch
  all.forEach((x, i) => { if (i % step === 0) s += iconAt(P, atNight(wmoKind(x.weather_code), x.is_day), pts[i].x, yTop - ti / 2 - 8, ti); });
  const iMax = temps.indexOf(mx), iMin = temps.indexOf(mn);
  [[iMax, -12], [iMin, fs + 12]].forEach(([i, dy]) => {
    s += `<circle cx="${r1(pts[i].x)}" cy="${r1(pts[i].y)}" r="6" fill="var(--vc-carte)" stroke="${tempColour(P, temps[i])}" stroke-width="3"/>` +
      `<text x="${r1(pts[i].x)}" y="${r1(pts[i].y + dy)}" font-size="${fs + 2}" font-weight="700" fill="${tempColour(P, temps[i])}" text-anchor="middle">${c.fmt(temps[i])}°</text>`;
  });
  const gap = (w - 2 * pad) / (n - 1), lw = Math.min(gap * 0.5, 16), top = 6;
  all.forEach((x, i) => {
    const v = x.precipitation_mm;
    if (num(v) && v > 0) { const hh = Math.max(8, Math.sqrt(Math.min(v, top) / top) * hp); s += `<rect x="${r1(pts[i].x - lw / 2)}" y="${r1(yPil + hp - hh)}" width="${r1(lw)}" height="${r1(hh)}" rx="${r1(lw / 2)}" fill="${rainColour(P, v)}"/>`; }
  });
  for (let i = 0; i < n; i += 3) s += `<text x="${r1(pts[i].x)}" y="${h - 4}" font-size="${fs}" font-weight="700" fill="${i === 0 ? P.accent : P.t3}" text-anchor="${i === 0 ? 'start' : 'middle'}">${c.hourOnly(all[i].time_utc)}</text>`;
  return s + '</svg>';
}

// The rain of the next hour (radar nowcast, France) and the first warning: the two pills that open Details and Alerts.
// The rain of the next hour by the nowcast of the place (PIAF in France: 5-minute rates, or 15-minute amounts): the minutes
// to the first rain, 0 when it rains now, -1 for none in the hour, null without a nowcast. The home page and the sentence of
// the Details page both read it, so that they never say two different things about the hour.
function nowcastFirst(c) {
  const rn = c.point?.rain_nowcast;
  const rates = Array.isArray(rn?.rate_5m_mm_h) && rn.rate_5m_mm_h.length && rn.rate_5m_mm_h.every(num) ? rn.rate_5m_mm_h : null;
  const steps = Array.isArray(rn?.mm_per_step) && rn.mm_per_step.length && rn.mm_per_step.every(num) ? rn.mm_per_step : null;
  const series = rates || steps;
  if (!series) return null;
  const first = series.findIndex((v) => v > 0);
  return first < 0 ? -1 : first * (rates ? 5 : rn.interval_minutes || 15);
}

function rainPill(c, fs, g, inline) {
  const rn = c.point?.rain_nowcast;
  if (!rn) return '';
  const steps = Array.isArray(rn.mm_per_step) ? rn.mm_per_step : null;
  const total = steps && steps.every(num) ? steps.reduce((a, b) => a + b, 0) : null;
  const first = nowcastFirst(c);
  const text = first === null ? null : first < 0 ? c.t('rain_none') : first === 0 ? c.t('rain_now') : c.t('rain_in', { n: first });
  if (!text) return '';
  const detail = num(total) && total > 0 ? c.t('mm_hour', { mm: c.fmt(total, 1) }) : '';
  const det = detail ? (inline ? ` <span class="det" style="font-size:${fs - 1}px">· ${detail}</span>` : `<small>${detail}</small>`) : '';
  const go = c.config._pages.includes('details') ? ' data-go="details"' : '';
  return `<button class="puce"${go} style="font-size:${fs}px;background:${alpha(c.P.pluie, 0.2)};border-color:${alpha(c.P.pluie, 0.65)}">${glyph('parapluie', g, c.P.pluie, 2.2)}<span class="tx">${text}${det}</span></button>`;
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
function daysOfPoint(c) {
  const daily = Array.isArray(c.point?.daily) ? c.point.daily : [];
  return daily.filter((d) => d && typeof d.date === 'string').map((d) => ({ name: c.dayName(d.date), kind: wmoKind(d.code), max: d.tmax, min: d.tmin, mm: d.rain_mm }));
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
function seasonPill(c, fs, g, inline) {
  const items = seasonItems(c);
  if (!items.length) return '';
  const it = items[0], col = toneColour(c, it.tone), neutral = it.tone === 'neutre', more = items.length - 1;
  const plus = more ? ` <span class="plus" style="background:${alpha(neutral ? c.P.t3 : col, 0.25)};color:${col}">+${more}</span>` : '';
  const det = it.det && !(more && inline) ? (inline ? ` <span class="det" style="font-size:${fs - 1}px">· ${it.det}</span>` : `<small>${it.det}</small>`) : '';
  const go = c.config._pages.includes('alerts') ? ' data-go="alerts"' : '';
  const bg = neutral ? 'transparent' : alpha(col, 0.2), bd = neutral ? c.P.filet : alpha(col, 0.65);
  return `<button class="puce"${go} style="font-size:${fs}px;background:${bg};border-color:${bd}">${glyph(it.glyph, g, col, 2.2)}<span class="tx">${it.pill}${plus}${det}</span></button>`;
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

// ------------------------------------------------------------------------------------------------------------------- pages
function pageHome(c) {
  if (!c.inFrance) return pageHomeWorld(c);
  const P = c.P, W = c.W;
  const now = momentOfPoint(c);
  const m = moment(c, now);
  const credit = foot(c, providers(c.point?.weather_attribution, c.point?.rain_nowcast?.attribution, seasonItems(c)[0]?.attribution));
  if (c.wide) {
    // The moment (icon, temperature, words) needs about 640 px at full size: in a column it is scaled to that column.
    const fit = (w) => clamp(w / (640 * c.k), 0.6, 1);
    const pills = `<div class="pills">${rainPill(c, 18, 26, false)}${warningPill(c, 18, 26, false)}${seasonPill(c, 18, 26, false)}</div>`;
    const half = Math.floor((W - 26) / 2);
    const pair = `<div class="grille" style="grid-template-columns:1fr 1fr;align-items:center"><div>${moment(c, now, fit(half))}</div><div>${pills}</div></div>`;
    const daysBlock = sep('20px 0 16px') + days(c, daysOfPoint(c), 7) + credit;
    if (W >= 900) {
      // A wall screen: the moment and the pills on the left, the 12 hours on the right.
      const left = Math.round(Math.min(560, W * 0.45)), right = W - left - 26;
      const curve = hoursCurve(c, right, Math.round(318 * clamp(c.k, 0.8, 1.05)));
      if (curve) return `<div class="grille" style="grid-template-columns:${left}px 1fr;align-items:center"><div>${moment(c, now, fit(left))}<div style="height:24px"></div>${pills}</div><div>${curve}</div></div>` + daysBlock;
    } else {
      // Between a phone and a wall: the 12 hours across the card, under the moment and the pills.
      const curve = hoursCurve(c, W, 230);
      if (curve) return pair + `<div style="height:14px"></div>${curve}` + daysBlock;
    }
    // Without the hours (an instance that does not give them yet): the moment and the two pills side by side.
    return pair + daysBlock;
  }
  const curve = hoursCurve(c, W, 200);
  return m + (curve ? `<div style="height:10px"></div>${curve}` : '') + `<div style="height:12px"></div><div class="pills">${rainPill(c, 14, 20, true)}${warningPill(c, 14, 20, true)}${seasonPill(c, 14, 20, true)}</div>` +
    sep() + days(c, daysOfPoint(c), 5) + credit;
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

// The sentence of the rain on the Details page when there is a nowcast: within the hour it follows the nowcast (the home
// page's), after the hour AROME-PI. Two Météo-France forecasts can disagree (PIAF dry for 40 minutes while AROME-PI has rain
// from now on, read on 2026-11-22): the card then says what the home page says.
function rainWithNowcast(c, st, first) {
  const step = (st.interval_minutes || 15) * 60;
  const steps = st.steps.filter((x) => num(x.time_utc));
  const last = steps[steps.length - 1];
  const wet = (x) => typeof x.precipitation === 'string' && x.precipitation !== 'none';
  const from = first < 0 ? c.now + 3600 : c.now;
  const rainy = steps.filter((x) => wet(x) && x.time_utc + step > from);
  const kindOf = (x) => kindWord(c, x ? x.precipitation : 'rain');
  if (first < 0) {
    if (!rainy.length) return last ? c.t('precip_none', { t: c.hm(last.time_utc + step) }) : c.t('rain_none');
    const start = rainy[0].time_utc, stop = rainy[rainy.length - 1].time_utc;
    const how = steps.some((x) => x.time_utc > start && x.time_utc < stop && !wet(x)) ? c.t('on_and_off') : '';
    return c.t('precip_at', { kind: kindOf(rainy[0]), at: c.hm(start), t: c.hm(stop + step), how });
  }
  const start = c.now + first * 60;
  const stop = rainy.length ? rainy[rainy.length - 1].time_utc + step : null;
  if (stop === null || stop <= start) return first === 0 ? c.t('rain_now') : c.t('rain_in', { n: first });
  const how = steps.some((x) => x.time_utc + step > start && x.time_utc < stop - step && !wet(x)) ? c.t('on_and_off') : '';
  const kind = kindOf(rainy[0]);
  return first === 0 ? c.t('precip_now', { kind, t: c.hm(stop), how }) : c.t('precip_in', { kind, n: first, t: c.hm(stop), how });
}

function pageDetails(c) {
  const st = c.point?.short_term, pr = c.point?.probabilities;
  const big = c.wide ? Math.round(31 * c.k) : 23, g = c.wide ? Math.round(36 * c.k) : 28;
  const lines = [];
  const nowcast = nowcastFirst(c);
  if (st && Array.isArray(st.steps) && st.steps.length) {
    const lastStep = st.steps[st.steps.length - 1];
    const end = num(lastStep?.time_utc) ? lastStep.time_utc + (st.interval_minutes || 15) * 60 : null;
    const span = Array.isArray(st.precipitation) ? st.precipitation[0] : null;
    let rain;
    if (nowcast !== null) {
      rain = rainWithNowcast(c, st, nowcast);
    } else if (span && num(span.first_at_utc)) {
      const kind = kindWord(c, span.kind);
      const until = num(span.last_at_utc) ? c.hm(span.last_at_utc + (st.interval_minutes || 15) * 60) : '';
      const mins = Math.round((span.first_at_utc - c.now) / 300) * 5;
      // "until" is the last step of that kind: when dry steps come between, the rain is said to come and go.
      const between = st.steps.filter((x) => num(x.time_utc) && x.time_utc >= span.first_at_utc && x.time_utc <= (span.last_at_utc ?? span.first_at_utc));
      const how = between.some((x) => x.precipitation === 'none') ? c.t('on_and_off') : '';
      rain = span.first_at_utc <= c.now + 120 ? c.t('precip_now', { kind, t: until, how }) : mins <= 90 ? c.t('precip_in', { kind, n: mins, t: until, how }) : c.t('precip_at', { kind, at: c.hm(span.first_at_utc), t: until, how });
    } else if (end !== null) rain = c.t('precip_none', { t: c.hm(end) });
    if (rain) lines.push(`<div class="ligne" style="font-size:${big}px">${glyph('parapluie', g, c.P.pluie, 2.3)}<span class="em-pluie">${rain}</span></div>`);
    if (num(st.gusts?.max_kmh) && num(st.gusts?.at_utc)) lines.push(`<div class="ligne" style="font-size:${big}px">${glyph('vent', g, c.P.vent, 2.3)}<span class="em-vent">${c.t('gusts_at', { v: c.fmt(st.gusts.max_kmh), t: c.hm(st.gusts.at_utc) })}</span></div>`);
  }
  const answer = lines.length ? `<div class="rep">${lines.join('')}</div>` : '';
  const chips = [];
  if (st?.hail?.level) chips.push(`<span class="chip">${c.t('hail')}&nbsp;: <b>${c.t(`hail_${st.hail.level}`)}</b></span>`);
  if (st?.visibility?.level) chips.push(`<span class="chip">${c.t('visibility')}&nbsp;: <b>${c.t(`vis_${st.visibility.level}`)}</b></span>`);
  const w0 = Array.isArray(pr?.short_range?.windows) ? pr.short_range.windows[0] : null;
  if (w0 && num(w0.gust_50kt_pct) && num(w0.to_utc)) chips.push(`<span class="chip">${c.t('gust93', { p: w0.gust_50kt_pct, t: c.hm(w0.to_utc) })}</span>`);
  const chipsHtml = chips.length ? `<div class="chips" style="font-size:${c.wide ? 15 : 13}px">${chips.join('')}</div>`.replace(/&nbsp;: /g, c.lang === 'fr' ? '&nbsp;: ' : ': ') : '';
  const nHours = st && num(st.horizon_minutes) ? Math.max(1, Math.round(st.horizon_minutes / 60)) : 5;
  const stepsTitle = (w) => title(c, 'horloge', c.t('next_hours', { n: nHours }), c.wide ? `AROME-PI, ${c.t('step15')}` : 'AROME-PI') + stepsBand(c, st, w, c.wide ? 230 : 150);
  const windows = Array.isArray(pr?.short_range?.windows) ? pr.short_range.windows : [];
  const legend = `<span style="color:${c.P.pluie}">■ ${c.t('legend_rain')}</span>&ensp;<span style="color:${c.P.vent}">■ ${c.t('legend_gust')}</span>`;
  const probaBlock = (w, n) => windows.length ? title(c, 'jauge', `${c.t('chances', { h: pr.short_range.window_hours || 3 })}&ensp;<span style="font-weight:500">${legend}</span>`, c.wide ? `PE-AROME, ${n} × ${pr.short_range.window_hours || 3} h` : '') + probabilities(c, windows, w, c.wide ? 178 : 150, n, pr.short_range.window_hours) : '';
  // PIAF, the nowcast of the sentence, is Météo-France's too (its own credit also names the providers of the other parts).
  const credit = foot(c, providers(st?.attribution, pr?.attribution), nowcast !== null ? ' (PIAF, AROME-PI, PE-AROME)' : ' (AROME-PI, PE-AROME)');
  if (c.wide) {
    const left = Math.round(Math.min(372, c.W * 0.4)), right = c.W - left - 28;
    return `<div class="grille" style="grid-template-columns:${left}px 1fr;align-items:start"><div>${answer}<div style="height:22px"></div>${chipsHtml}</div><div>${st?.steps?.length ? stepsTitle(right) : ''}</div></div>` +
      (windows.length ? sep('18px 0 14px') + probaBlock(c.W, Math.min(8, windows.length)) : '') + credit;
  }
  return answer + (st?.steps?.length ? sep() + stepsTitle(c.W) + `<div style="height:8px"></div>${chipsHtml}` : chipsHtml) +
    (windows.length ? sep() + probaBlock(c.W, Math.min(6, windows.length)) : '') + credit;
}
// The short term (AROME-PI): precipitation by 15-minute step, the gusts as a curve.
function stepsBand(c, st, w, h) {
  const steps = (st?.steps || []).filter((s) => num(s.time_utc));
  if (!steps.length) return '';
  const P = c.P, n = steps.length, fs = c.wide ? 14 : 12, gx = c.wide ? 76 : 62, x0 = gx, x1 = w - 8, gap = (x1 - x0) / n;
  const yPl = 4, hPl = c.wide ? 24 : 18, yR0 = yPl + hPl + 26, yR1 = h - 22;
  const gusts = steps.map((s) => (num(s.wind_gust_kmh) ? s.wind_gust_kmh : null)), known = gusts.filter(num);
  const lo = known.length ? Math.max(0, Math.floor((Math.min(...known) - 10) / 10) * 10) : 0, hi = known.length ? Math.ceil((Math.max(...known) + 10) / 10) * 10 : 100;
  const Y = (v) => yR1 - ((v - lo) / (hi - lo)) * (yR1 - yR0);
  let s = `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">`;
  s += `<text x="0" y="${yPl + hPl - 4}" font-size="${fs}" font-weight="700" fill="${P.t3}">${c.t('rain')}</text>`;
  steps.forEach((st0, i) => {
    const wet = typeof st0.precipitation === 'string' && st0.precipitation !== 'none';
    s += `<rect x="${r1(x0 + i * gap + 1.5)}" y="${wet ? yPl : yPl + hPl / 2 - 2}" width="${r1(Math.max(1, gap - 3))}" height="${wet ? hPl : 4}" rx="${wet ? 5 : 2}" fill="${wet ? P.pluie : P.filet}"/>`;
  });
  if (known.length >= 2) {
    s += `<text x="0" y="${r1((yR0 + yR1) / 2 + 4)}" font-size="${fs}" font-weight="700" fill="${P.t3}">${c.t('gust_lbl')}</text>`;
    const marks = [lo + (hi - lo) / 3, lo + (2 * (hi - lo)) / 3].map((v) => Math.round(v / 10) * 10);
    marks.forEach((v) => (s += `<line x1="${x0}" y1="${r1(Y(v))}" x2="${x1}" y2="${r1(Y(v))}" stroke="${P.filet}" stroke-width="1" stroke-dasharray="3 5"/><text x="${x0 - 6}" y="${r1(Y(v) + 4)}" font-size="${fs - 1}" fill="${P.t4}" text-anchor="end">${v}</text>`));
    const pts = gusts.map((v, i) => (num(v) ? { x: x0 + i * gap + gap / 2, y: Y(v), v } : null)).filter(Boolean);
    const d = smooth(pts), id = `r${Math.random().toString(36).slice(2, 8)}`;
    s += `<defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${P.vent}" stop-opacity=".32"/><stop offset="1" stop-color="${P.vent}" stop-opacity="0"/></linearGradient></defs>`;
    s += `<path d="${d} L${r1(pts[pts.length - 1].x)} ${yR1} L${r1(pts[0].x)} ${yR1} Z" fill="url(#${id})"/><path d="${d}" fill="none" stroke="${P.vent}" stroke-width="${c.wide ? 4.5 : 3.5}" stroke-linecap="round"/>`;
    const best = pts.reduce((a, b) => (b.v > a.v ? b : a));
    s += `<circle cx="${r1(best.x)}" cy="${r1(best.y)}" r="6" fill="var(--vc-carte)" stroke="${P.texte}" stroke-width="3"/><text x="${r1(best.x)}" y="${r1(best.y - 11)}" font-size="${fs + 4}" font-weight="700" fill="${P.texte}" text-anchor="middle">${c.fmt(best.v)}</text>`;
  }
  const ticks = [0, Math.round(n / 3), Math.round((2 * n) / 3), n - 1];
  [...new Set(ticks)].forEach((i, k) => (s += `<text x="${r1(x0 + i * gap + gap / 2)}" y="${h - 4}" font-size="${fs}" font-weight="700" fill="${k === 0 ? P.accent : P.t3}" text-anchor="${k === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}">${c.hm(steps[i].time_utc)}</text>`));
  return s + '</svg>';
}
// Chances per window of the ensembles (PE-AROME): rain of 1 mm or more, gusts of 30 kt (56 km/h) or more.
function probabilities(c, windows, w, h, n, hours) {
  const P = c.P, fs = c.wide ? 14 : 12, gap = w / n, lw = Math.min(gap * 0.62, 34), hr = (h - 20) / 2 - 16, wh = 3600 * (num(hours) ? hours : 3);
  let s = `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">`;
  [['rain_1mm_pct', P.pluie, 0], ['gust_30kt_pct', P.vent, 1]].forEach(([key, col, row]) => {
    const yb = 14 + hr + row * (hr + 18);
    windows.slice(0, n).forEach((win, i) => {
      const v = win[key], x = i * gap + (gap - lw) / 2;
      s += `<rect x="${r1(x)}" y="${r1(yb - hr)}" width="${r1(lw)}" height="${r1(hr)}" rx="6" fill="${P.filet}" opacity=".55"/>`;
      if (num(v)) {
        const hh = Math.max(3, (v / 100) * hr);
        s += `<rect x="${r1(x)}" y="${r1(yb - hh)}" width="${r1(lw)}" height="${r1(hh)}" rx="6" fill="${col}" opacity="${v ? 1 : 0}"/>` +
          `<text x="${r1(x + lw / 2)}" y="${r1(yb - hr - 3)}" font-size="${fs}" font-weight="700" fill="${v >= 50 ? P.texte : P.t3}" text-anchor="middle">${v}</text>`;
      }
    });
  });
  windows.slice(0, n).forEach((win, i) => {
    if (!num(win.to_utc)) return;
    const from = win.to_utc - wh;
    const label = c.lang === 'fr' ? `${c.local(from).getUTCHours()}-${c.local(win.to_utc).getUTCHours()} h` : `${c.local(from).getUTCHours()}-${c.local(win.to_utc).getUTCHours()}h`;
    s += `<text x="${r1(i * gap + gap / 2)}" y="${h - 3}" font-size="${fs - 1}" font-weight="700" fill="${i === 0 ? P.accent : P.t3}" text-anchor="middle">${label}</text>`;
  });
  return s + '</svg>';
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
    details: !!(pt?.short_term?.steps?.length || pt?.probabilities?.short_range?.windows?.length),
    alerts: true,
    sea: !!(d?.tides || (d?.spot && d?.activities)),
    normal: c.normals?.status === 'available',
  };
  return wanted.filter((p) => has[p]);
}
const PAGE_RENDER = { home: pageHome, details: pageDetails, alerts: pageAlerts, sea: pageSea, normal: pageNormal };

// ------------------------------------------------------------------------------------------------------------------- style
const STYLE = `
:host{display:block}
ha-card{overflow:hidden;--vc-carte:var(--ha-card-background,var(--card-background-color,#19232E))}
.root{padding:var(--vc-pad);font-weight:500;touch-action:pan-y;user-select:none;-webkit-user-select:none}
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
.page{color:var(--vc-texte)}
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
    this._dataKey = null;
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

  getCardSize() { return 9; }
  getGridOptions() { return { columns: 12, min_columns: 6 }; }

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
    if (this._hass && this._data) this._fetch();
    this._subscribeForecast();
  }

  disconnectedCallback() {
    clearInterval(this._timer); clearInterval(this._tick);
    this._resize?.disconnect();
    this._unsubscribeForecast();
  }

  async _fetch() {
    if (!this._hass || !this._config) return;
    const spot = this._config.spot === 'none' ? null : this._config.spot || 'auto';
    const msg = { type: 'vigie/card/data', spot };
    if (this._config.entry_id) msg.entry_id = this._config.entry_id;
    try {
      this._data = await this._hass.callWS(msg);
      this._error = null;
    } catch (err) {
      this._error = err?.code || 'error';
    }
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
    const width = this._width || this.getBoundingClientRect().width || 380;
    const lang = this._lang || 'en';
    const config = { ...this._config, _weatherState: this._entity || null, _forecast: this._forecast || [] };
    const c = makeContext(lang, !!this._dark, width, this._data, config);
    const P = c.P;
    const vars = { carte: null, texte: P.texte, t2: P.t2, t3: P.t3, t4: P.t4, filet: P.filet, accent: P.accent, sur: P.sur, pluie: P.pluie, vent: P.vent, piste: this._dark ? 'rgba(255,255,255,.04)' : 'rgba(0,0,0,.03)' };
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
    ];
    this._form.data = { idle: 'return', spot: 'auto', start_page: 'home', ...this._config };
    this._form.computeLabel = (s) => this._t(s.name);
    this._form.computeHelper = (s) => (s.name === 'weather_entity' ? this._t('weather_entity_help') : undefined);
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
