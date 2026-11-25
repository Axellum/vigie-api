- **Fourteen days of forecast instead of ten**: the non-compact `/v1/point` and `/v1/snapshot` (and so the weather of the
  Home Assistant integration) give 14 days. Open-Meteo is asked for 14 days, still one call (`app/collectors/weights.py`);
  in France the first days stay Météo-France's. The rain probability may be missing on the last days (the self-hosted
  Open-Meteo has it for 10 days). The compact profiles (the frame, `compact=true`) keep 5 days.
