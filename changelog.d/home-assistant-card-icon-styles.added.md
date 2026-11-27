- **Four styles of weather icons on the dashboard card** (Home Assistant integration 0.9.0, ADR 0045): the icons are now the
  icon set of the Vigie family, the very drawings of the Meridien screen, with its twenty kinds of weather instead of ten (drizzle,
  heavy rain, freezing rain, showers by day and by night, heavy snow, snow grains, thunder with hail...), chosen for each weather
  code as the screen chooses them. The editor offers four styles (`icon_style`): Meridien, the screen's own shapes, the default;
  Line; Duo; Badge. Their colours follow the light or dark theme. `scripts/card_icons.py` draws them into the card, and a test
  checks that the card holds what it draws.
