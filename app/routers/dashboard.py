"""Web supervision dashboard (``GET /dashboard``).

A self-contained HTML5 / CSS / JS page (dark theme, no external dependency) that shows:

1. The health of every provider and its circuit breaker.
2. The watched zones (``/v1/zones``) and the active hazards (``/v1/snapshot``).
3. Buttons to start a simulated scenario (``/v1/simulate``, shown only when the instance enables the
   simulator with ``VIGIE_ENABLE_SIMULATION=1``) or to run one scheduler cycle (``/v1/scheduler/trigger``).
4. The live embedded frame (``GET /v1/embedded/frame``) for the Waveshare 7B and the Tab5.

A value the API reports as unknown (``null``) is shown as "n/a", never as a number.

The page ends with the credits of the data sources, read from ``app/attribution.py`` (the table that
every response reads too), and with the notice that the alerts of GDACS are automatic and do not replace
official warnings.
"""

from __future__ import annotations

import html
import re

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.attribution import CREDITS
from app.collectors.weather import rainviewer_enabled_from_env

router = APIRouter(tags=["Dashboard"])

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Vigie API - Supervision</title>
  <style>
    :root {
      --bg-0: hsl(222, 32%, 7%);
      --bg-1: hsl(222, 28%, 11%);
      --bg-2: hsl(222, 24%, 15%);
      --border: hsl(220, 20%, 22%);
      --text-0: hsl(210, 35%, 96%);
      --text-1: hsl(215, 20%, 72%);
      --accent: hsl(195, 92%, 52%);
      --green: hsl(150, 75%, 44%);
      --yellow: hsl(44, 96%, 54%);
      --orange: hsl(24, 95%, 56%);
      --red: hsl(354, 88%, 58%);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg-0);
      color: var(--text-0);
      min-height: 100vh;
      padding: 18px 24px;
    }
    header {
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 14px;
      padding: 16px 20px;
      background: var(--bg-1);
      border: 1px solid var(--border);
      border-radius: 8px;
      margin-bottom: 18px;
    }
    .brand h1 { font-size: 1.25rem; letter-spacing: 0.2px; }
    .brand p { color: var(--text-1); font-size: 0.85rem; margin-top: 4px; }
    .badges { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .badge {
      padding: 6px 12px;
      border-radius: 999px;
      font-weight: 600;
      font-size: 0.8rem;
      border: 1px solid var(--border);
      background: var(--bg-2);
    }
    .sev-GREEN { background: hsla(150, 75%, 44%, 0.18); color: var(--green); border-color: var(--green); }
    .sev-YELLOW { background: hsla(44, 96%, 54%, 0.18); color: var(--yellow); border-color: var(--yellow); }
    .sev-ORANGE { background: hsla(24, 95%, 56%, 0.18); color: var(--orange); border-color: var(--orange); }
    .sev-RED { background: hsla(354, 88%, 58%, 0.25); color: var(--red); border-color: var(--red); }
    .toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      padding: 14px 18px;
      background: var(--bg-1);
      border: 1px solid var(--border);
      border-radius: 8px;
      margin-bottom: 18px;
      align-items: center;
    }
    .toolbar span { font-size: 0.85rem; color: var(--text-1); font-weight: 600; margin-right: 6px; }
    button, select {
      background: var(--bg-2);
      color: var(--text-0);
      border: 1px solid var(--border);
      padding: 8px 13px;
      border-radius: 6px;
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
    }
    button:hover { border-color: var(--accent); }
    .sim-controls { display: contents; }
    .sim-controls[hidden] { display: none; }
    .grid {
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 16px;
    }
    .card {
      background: var(--bg-1);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
    }
    .col-6 { grid-column: span 6; }
    .col-7 { grid-column: span 7; }
    .col-5 { grid-column: span 5; }
    @media (max-width: 1100px) {
      .col-5, .col-6, .col-7 { grid-column: span 12; }
    }
    .card h2 {
      font-size: 0.95rem;
      margin-bottom: 12px;
      color: var(--accent);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
    th, td {
      text-align: left;
      padding: 8px 6px;
      border-bottom: 1px solid hsla(220, 20%, 22%, 0.6);
    }
    th { color: var(--text-1); font-weight: 600; }
    footer { margin-top: 18px; color: var(--text-1); font-size: 0.8rem; line-height: 1.5; }
    footer h2 { font-size: 0.85rem; color: var(--text-0); margin: 12px 0 6px; }
    footer ul { padding-left: 18px; }
    footer a { color: var(--accent); }
    pre {
      background: hsl(224, 36%, 5%);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 12px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.78rem;
      color: hsl(165, 85%, 72%);
      overflow: auto;
      line-height: 1.45;
      max-height: 320px;
    }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <h1>Vigie API - Supervision</h1>
      <p>Sources: USGS, EMSC, GDACS, NOAA NWS, USGS Volcano, NOAA SWPC, Open-Meteo, MET Norway, RainViewer. Unknown values are shown as n/a.</p>
    </div>
    <div class="badges">
      <span id="severity-badge" class="badge">Max severity: --</span>
      <span id="kp-badge" class="badge">Solar Kp: --</span>
      <span id="wal-badge" class="badge">SQLite: --</span>
      <span id="sched-badge" class="badge">Scheduler: --</span>
      <span id="sim-badge" class="badge">Simulation: --</span>
    </div>
  </header>

  <section class="toolbar">
    <div id="sim-controls" class="sim-controls" hidden>
      <span>Simulation (test data, always labelled):</span>
      <button onclick="triggerSim('mega_tsunami_japan')">M7.8 earthquake and tsunami, Japan</button>
      <button onclick="triggerSim('cat5_hurricane_caribbean')">Category 5 hurricane, Antilles</button>
      <button onclick="triggerSim('volcano_eruption_iceland')">Eruption and ash, Iceland</button>
      <button onclick="triggerSim('severe_squall_marine')">Violent squall and 5.4 m swell</button>
      <button onclick="triggerSim('clear')">Back to real data</button>
    </div>
    <span id="sim-off-note" hidden>Simulator off (set VIGIE_ENABLE_SIMULATION=1 to enable it).</span>
    <button onclick="triggerScheduler()">Run one collection cycle</button>
    <select id="device-select" onchange="refreshAll()">
      <option value="waveshare-7b">Display: Waveshare 7B (1024x600)</option>
      <option value="tab5">Display: M5Stack Tab5 (1280x720 + GPS)</option>
      <option value="marine">Display: marine navigation</option>
    </select>
  </section>

  <main class="grid">
    <section class="card col-7">
      <h2>Active hazards <span id="hazards-count">0</span></h2>
      <table>
        <thead>
          <tr>
            <th>Severity</th>
            <th>Category</th>
            <th>Title</th>
            <th>Value</th>
            <th>Sources</th>
            <th>Distance / bearing / tsunami ETA</th>
          </tr>
        </thead>
        <tbody id="hazards-body"></tbody>
      </table>
    </section>

    <section class="card col-5">
      <h2>Watched zones</h2>
      <table>
        <thead>
          <tr>
            <th>Zone</th>
            <th>Alert</th>
            <th>Temp</th>
            <th>Wind / gust</th>
            <th>Rain, next hour</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody id="zones-body"></tbody>
      </table>
    </section>

    <section class="card col-6">
      <h2>Provider health and circuit breakers</h2>
      <table>
        <thead>
          <tr>
            <th>Provider</th>
            <th>Domain</th>
            <th>Circuit</th>
            <th>Latency</th>
            <th>Cache</th>
          </tr>
        </thead>
        <tbody id="providers-body"></tbody>
      </table>
    </section>

    <section class="card col-6">
      <h2>Embedded frame (/v1/embedded/frame, sample GPS position 47.75, -3.36)</h2>
      <pre id="embedded-frame-box">Loading the frame...</pre>
    </section>
  </main>

  <script>
    function esc(value) {
      return String(value).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
    }

    function fmt(value, unit) {
      return (value === null || value === undefined) ? 'n/a' : esc(value) + (unit ? ' ' + unit : '');
    }

    // An instance that sets VIGIE_ADMIN_TOKEN asks for it on every write: it is asked once, kept for this tab only, and sent as a Bearer header.
    function adminToken() {
      try { return sessionStorage.getItem('vigie-admin-token') || ''; } catch (e) { return ''; }
    }

    async function writeRequest(url, options) {
      const send = () => {
        const token = adminToken();
        const headers = Object.assign({}, options.headers || {}, token ? {'Authorization': 'Bearer ' + token} : {});
        return fetch(url, Object.assign({}, options, {headers}));
      };
      let response = await send();
      if (response.status === 401) {
        const entered = window.prompt('This instance asks for its admin token to change anything (VIGIE_ADMIN_TOKEN):');
        if (entered && entered.trim()) {
          try { sessionStorage.setItem('vigie-admin-token', entered.trim()); } catch (e) { /* kept for this request only */ }
          response = await send();
        }
      }
      return response;
    }

    async function triggerSim(scenario) {
      await writeRequest('/v1/simulate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({scenario})
      });
      await refreshAll();
    }

    async function triggerScheduler() {
      await writeRequest('/v1/scheduler/trigger', {method: 'POST'});
      await refreshAll();
    }

    async function refreshAll() {
      const dev = document.getElementById('device-select').value;
      const [snapRes, healthRes, frameRes] = await Promise.all([
        fetch(`/v1/snapshot?device_type=${dev}&compact=true`),
        fetch('/v1/health'),
        fetch(`/v1/embedded/frame?device_type=${dev}&lat=47.75&lon=-3.36&marine=true`)
      ]);
      const snap = await snapRes.json();
      const health = await healthRes.json();
      const frameText = await frameRes.text();

      const gs = snap.global_status;
      const sev = document.getElementById('severity-badge');
      if (gs.max_severity === null) {
        sev.textContent = 'Max severity: unknown (no hazard source answered)';
        sev.className = 'badge';
      } else {
        sev.textContent = `Max severity: ${gs.max_severity} (red ${gs.active_red_count}, orange ${gs.active_orange_count})`;
        sev.className = `badge sev-${gs.max_severity}`;
      }
      document.getElementById('kp-badge').textContent =
        gs.solar_kp === null ? 'Solar Kp: n/a' : `Solar Kp: ${gs.solar_kp} (${gs.solar_storm_level})`;
      document.getElementById('wal-badge').textContent = `SQLite: ${(health.storage_journal_mode || 'n/a').toUpperCase()}`;
      document.getElementById('sched-badge').textContent =
        `Scheduler: ${health.scheduler.running ? 'running' : 'stopped'} (${health.scheduler.cycles_count} cycles)`;
      document.getElementById('sim-badge').textContent =
        `Simulation: ${health.simulation_active ? 'ACTIVE (' + esc(health.active_scenario) + ')' : 'off'}`;
      document.getElementById('sim-controls').hidden = !health.simulation_enabled;
      document.getElementById('sim-off-note').hidden = !!health.simulation_enabled;

      document.getElementById('hazards-count').textContent = `${snap.hazards.length}`;
      document.getElementById('hazards-body').innerHTML = snap.hazards.map(h => `
        <tr>
          <td><span class="badge sev-${esc(h.severity)}">${esc(h.severity)}</span></td>
          <td>${esc(h.category)}</td>
          <td>${esc(h.title)}</td>
          <td><strong>${fmt(h.value_primary, h.value_unit)}</strong></td>
          <td>${esc((h.sources || [h.source]).join(' + '))}</td>
          <td>${fmt(h.distance_to_zone_km, 'km')} ${h.bearing_cardinal ? '(' + esc(h.bearing_cardinal) + ')' : ''}${h.tsunami_warning ? ' - tsunami ETA ' + fmt(h.tsunami_eta_minutes, 'min') : ''}</td>
        </tr>
      `).join('') || '<tr><td colspan="6">No active hazard.</td></tr>';

      document.getElementById('zones-body').innerHTML = snap.zones.map(z => `
        <tr>
          <td><strong>${esc(z.name)}</strong></td>
          <td><span class="badge sev-${esc(z.local_alert_level)}">${esc(z.local_alert_level)}</span></td>
          <td>${z.current ? fmt(z.current.temp_c, 'C') : 'n/a'}</td>
          <td>${z.current ? fmt(z.current.wind_kmh) + ' / ' + fmt(z.current.gust_kmh) + ' km/h' : 'n/a'}</td>
          <td>${z.rain_next_60m ? '[' + z.rain_next_60m.map(esc).join(', ') + '] mm' : 'n/a'}</td>
          <td>${z.weather_source ? esc(z.weather_source) : 'no weather'}</td>
        </tr>
      `).join('');

      document.getElementById('providers-body').innerHTML = (health.providers || []).map(p => `
        <tr>
          <td>${esc(p.name)}</td>
          <td>${esc(p.domain)}</td>
          <td><span class="badge ${p.circuit_state === 'CLOSED' ? 'sev-GREEN' : 'sev-ORANGE'}">${esc(p.circuit_state)}</span></td>
          <td>${esc(p.latency_ms)} ms</td>
          <td>${p.cache_age_s !== null ? esc(p.cache_age_s) + ' s' : 'cold'}</td>
        </tr>
      `).join('');

      document.getElementById('embedded-frame-box').textContent = frameText;
    }

    refreshAll();
  </script>

  <footer>
    <p><strong>Not an official warning service.</strong> GDACS alerts are automatic and do not replace official
    warnings. Alerts and weather come from public providers, can be late, incomplete or wrong, and must not be the
    only thing a decision rests on. The code is MIT-licensed; the data belongs to each provider under its own terms
    (<code>SOURCES.md</code>).</p>
    <h2>Data credits</h2>
    <ul>
<!--CREDITS-->
    </ul>
  </footer>
</body>
</html>
"""

_URL = re.compile(r"https?://[^\s)<,]+")


def credits_html(include_rainviewer: bool = True) -> str:
    """The credit lines of the sources as HTML list items, with their links (the text is escaped)."""
    items: list[str] = []
    for credit in CREDITS.values():
        if credit.key == "rainviewer" and not include_rainviewer:
            continue
        text = _URL.sub(
            lambda m: f'<a href="{m.group(0)}" rel="noopener noreferrer">{m.group(0)}</a>',
            html.escape(credit.text, quote=True),
        )
        items.append(f"      <li><strong>{html.escape(credit.provider)}</strong>: {text}</li>")
    return "\n".join(items)


def render_dashboard(include_rainviewer: bool = True) -> str:
    """The dashboard page with the credits of the sources filled in."""
    return DASHBOARD_HTML.replace("<!--CREDITS-->", credits_html(include_rainviewer))


@router.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard() -> HTMLResponse:
    """Serve the web supervision dashboard."""
    return HTMLResponse(content=render_dashboard(include_rainviewer=rainviewer_enabled_from_env()))
