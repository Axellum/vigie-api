"""Dashboard Web Pro de Supervision & Salle de Crise (`GET /dashboard`).

Fournit une interface HTML5/CSS/JS autonome (Mode Sombre Tactique, zéro dépendance externe)
permettant :
1. De visualiser en temps réel l'état des 12 providers mondiaux et de leurs Circuit Breakers.
2. De visualiser les zones mondiales surveillées (`/v1/zones`) et les alertes actives (`/v1/snapshot`).
3. D'injecter en 1 clic les scénarios de simulation (`/v1/simulate`) ou de déclencher un cycle
   du scheduler (`/v1/scheduler/trigger`).
4. D'inspecter en direct la trame embarquée C++ (`GET /v1/embedded/frame`) et sa somme CRC32
   pour les terminaux Waveshare 7B (1024×600) et M5Stack Tab5 (1280×720 + GPS Grove).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["Web Crisis Dashboard"])

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Vigie Mondiale Pro — Centre de Supervision & Salle de Crise</title>
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
      background: radial-gradient(circle at top right, hsl(220, 38%, 13%), var(--bg-0));
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
      border-radius: 12px;
      margin-bottom: 18px;
    }
    .brand h1 { font-size: 1.35rem; letter-spacing: 0.4px; }
    .brand p { color: var(--text-1); font-size: 0.85rem; margin-top: 4px; }
    .badges { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .badge {
      padding: 6px 12px;
      border-radius: 999px;
      font-weight: 700;
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
      border-radius: 12px;
      margin-bottom: 18px;
      align-items: center;
    }
    .toolbar span { font-size: 0.85rem; color: var(--text-1); font-weight: 600; margin-right: 6px; }
    button, select {
      background: var(--bg-2);
      color: var(--text-0);
      border: 1px solid var(--border);
      padding: 8px 13px;
      border-radius: 8px;
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    button:hover { border-color: var(--accent); background: hsl(222, 26%, 19%); }
    .btn-danger { border-color: hsla(354, 88%, 58%, 0.6); color: hsl(354, 95%, 74%); }
    .btn-ok { border-color: hsla(150, 75%, 44%, 0.6); color: hsl(150, 85%, 68%); }
    .grid {
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 16px;
    }
    .card {
      background: var(--bg-1);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px;
    }
    .col-4 { grid-column: span 4; }
    .col-6 { grid-column: span 6; }
    .col-8 { grid-column: span 8; }
    .col-12 { grid-column: span 12; }
    @media (max-width: 1100px) {
      .col-4, .col-6, .col-8 { grid-column: span 12; }
    }
    .card h2 {
      font-size: 0.98rem;
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
    pre {
      background: hsl(224, 36%, 5%);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.78rem;
      color: hsl(165, 85%, 72%);
      overflow-x: auto;
      line-height: 1.45;
      max-height: 320px;
    }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <h1>🌍 VIGIE MONDIALE PRO — SALLE DE CRISE & SUPERVISION API</h1>
      <p>Multi-Providers (USGS · EMSC · GDACS · NOAA · Open-Meteo HD · MET Norway · Marine · GloFAS · SWPC) · ESP32-P4 Waveshare 7B & M5Stack Tab5</p>
    </div>
    <div class="badges">
      <span id="defcon-badge" class="badge sev-GREEN">DEFCON : GREEN</span>
      <span id="kp-badge" class="badge">SOLAIRE Kp : --</span>
      <span id="wal-badge" class="badge">SQLite : WAL</span>
      <span id="sched-badge" class="badge">Scheduler : Actif</span>
    </div>
  </header>

  <section class="toolbar">
    <span>⚡ Simulateur de Crises :</span>
    <button class="btn-danger" onclick="triggerSim('mega_tsunami_japan')">🌊 Séisme M7.8 + Tsunami Japon</button>
    <button class="btn-danger" onclick="triggerSim('cat5_hurricane_caribbean')">🌀 Ouragan Cat. 5 Antilles</button>
    <button class="btn-danger" onclick="triggerSim('volcano_eruption_iceland')">🌋 Éruption + Cendres Islande</button>
    <button onclick="triggerSim('severe_squall_marine')">⛵ Grain Violent & Houle 5.4m</button>
    <button class="btn-ok" onclick="triggerSim('clear')">✅ Retour au Calme (Clear)</button>
    <button onclick="triggerScheduler()">🔄 Cycle Scheduler Immédiat</button>
    <select id="device-select" onchange="refreshAll()">
      <option value="waveshare-7b">Écran : Waveshare 7B (1024×600)</option>
      <option value="tab5">Écran : M5Stack Tab5 (1280×720 + GPS)</option>
      <option value="marine">Écran : Navigation Marine Bateau</option>
    </select>
  </section>

  <main class="grid">
    <section class="card col-7" style="grid-column: span 7;">
      <h2>🚨 Alertes Catastrophes Mondiales Dédupliquées <span id="hazards-count">0</span></h2>
      <table>
        <thead>
          <tr>
            <th>Sévérité</th>
            <th>Catégorie</th>
            <th>Titre & Épicentre</th>
            <th>Valeur</th>
            <th>Sources Fusionnées</th>
            <th>Dist / Cap / ETA Tsunami</th>
          </tr>
        </thead>
        <tbody id="hazards-body"></tbody>
      </table>
    </section>

    <section class="card col-5" style="grid-column: span 5;">
      <h2>📍 Watchlist Zones Surveillées (SQLite WAL)</h2>
      <table>
        <thead>
          <tr>
            <th>Zone</th>
            <th>Alerte</th>
            <th>Temp</th>
            <th>Vent / Raf</th>
            <th>Pluie 15m</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody id="zones-body"></tbody>
      </table>
    </section>

    <section class="card col-6">
      <h2>🛡️ Santé des 12 Collecteurs & Circuit Breakers</h2>
      <table>
        <thead>
          <tr>
            <th>Provider</th>
            <th>Domaine</th>
            <th>Circuit</th>
            <th>Latence</th>
            <th>Cache</th>
          </tr>
        </thead>
        <tbody id="providers-body"></tbody>
      </table>
    </section>

    <section class="card col-6">
      <h2>📟 Trame Embarquée C++ (`/v1/embedded/frame` — Sans malloc + CRC32)</h2>
      <pre id="embedded-frame-box">Chargement de la trame C++...</pre>
    </section>
  </main>

  <script>
    async function triggerSim(scenario) {
      await fetch('/v1/simulate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({scenario})
      });
      await refreshAll();
    }

    async function triggerScheduler() {
      await fetch('/v1/scheduler/trigger', {method: 'POST'});
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
      const defcon = document.getElementById('defcon-badge');
      defcon.textContent = `DEFCON : ${gs.max_severity} (R:${gs.active_red_count} O:${gs.active_orange_count})`;
      defcon.className = `badge sev-${gs.max_severity}`;

      document.getElementById('kp-badge').textContent = `SOLAIRE Kp : ${gs.solar_kp} (${gs.solar_storm_level})`;
      document.getElementById('wal-badge').textContent = `SQLite : ${(health.storage_journal_mode || 'wal').toUpperCase()}`;

      const hBody = document.getElementById('hazards-body');
      document.getElementById('hazards-count').textContent = `${snap.hazards.length} active(s)`;
      hBody.innerHTML = snap.hazards.map(h => `
        <tr>
          <td><span class="badge sev-${h.severity}">${h.severity}</span></td>
          <td>${h.category}</td>
          <td>${h.title}</td>
          <td><strong>${h.value_primary} ${h.value_unit}</strong></td>
          <td>${(h.sources || [h.source]).join(' + ')}</td>
          <td>${h.distance_to_zone_km || 0} km (${h.bearing_cardinal || 'N'})${h.tsunami_warning ? ' · 🌊 ETA ' + (h.tsunami_eta_minutes || '?') + 'm' : ''}</td>
        </tr>
      `).join('') || '<tr><td colspan="6">Aucune catastrophe critique active.</td></tr>';

      const zBody = document.getElementById('zones-body');
      zBody.innerHTML = snap.zones.map(z => `
        <tr>
          <td><strong>${z.name}</strong></td>
          <td><span class="badge sev-${z.local_alert_level}">${z.local_alert_level}</span></td>
          <td>${z.current.temp_c}°C</td>
          <td>${z.current.wind_kmh} / ${z.current.gust_kmh} km/h</td>
          <td>[${(z.rain_next_60m || []).join(', ')}] mm</td>
          <td>${z.weather_source}</td>
        </tr>
      `).join('');

      const pBody = document.getElementById('providers-body');
      pBody.innerHTML = (health.providers || []).map(p => `
        <tr>
          <td>${p.name}</td>
          <td>${p.domain}</td>
          <td><span class="badge ${p.circuit_state === 'CLOSED' ? 'sev-GREEN' : 'sev-ORANGE'}">${p.circuit_state}</span></td>
          <td>${p.latency_ms} ms</td>
          <td>${p.cache_age_s !== null ? p.cache_age_s + 's' : 'froid'}</td>
        </tr>
      `).join('');

      document.getElementById('embedded-frame-box').textContent = frameText;
    }

    refreshAll();
  </script>
</body>
</html>
"""


@router.get("/dashboard", response_class=HTMLResponse)
async def get_crisis_dashboard() -> HTMLResponse:
    """Sert le Dashboard Web Pro de Supervision et de Salle de Crise."""
    return HTMLResponse(content=DASHBOARD_HTML)
