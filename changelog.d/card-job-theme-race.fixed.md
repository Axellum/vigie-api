- **Tests**: the `card` job no longer fails now and then with "dark mode is None". Home Assistant skips applying its theme
  when its list of themes arrives before its configuration, and leaves `darkMode` unset until the next change; the job now
  changes the browser's colour scheme once the dashboard is drawn, which makes Home Assistant apply the theme asked, and says
  when that was needed.
