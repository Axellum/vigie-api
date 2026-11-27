# 0045. Home Assistant: the weather icons of the card, the icon set of the Vigie family in four styles

- Status: accepted
- Date: 2026-11-27
- Number: given by `scripts/prochain_adr.py` on 2026-11-27.
- Completes [ADR 0042](0042-home-assistant-weather-of-the-place-and-card.md) (the card); the rest of 0042 and
  [ADR 0044](0044-home-assistant-card-as-a-dashboard-resource.md) stand.

## Context

The card of ADR 0042 drew ten weather icons of its own, simplified copies of the drawings of the Meridien screen (the
self-standing weather screen of the Vigie family). The family needs one icon set for all its apps (the card, the Windows app, the
screen), whose colours each app sets, in a few styles to choose from. Four styles were drawn and chosen on 2026-11-27: the
drawings of the screen as they are, and three styles on a grid of 24 sharing one geometry.

## Decision

1. **The card draws the icon set of the family**: the twenty weather icons of the screen (clear, broken clouds and overcast, by day
   and by night; fog, drizzle, rain, heavy rain, freezing rain, showers by day and by night, violent showers, snow, heavy snow, snow
   grains, snow showers by day and by night, thunder, thunder with hail), named as on the screen. The icon of a WMO weather code is
   the one the screen chooses (`meteo_wmo.hpp` of its repository); the hours of the night show the moon. A code the WMO table does
   not use gives an overcast sky, as before. The old names of the card stay understood.
2. **Four styles, chosen in the editor** (`icon_style`): `meridien` (the shapes of the screen, on its grid of 128; the default),
   `line` (a stroke), `duo` (the stroke filled with its own colour at 32 %), `badge` (a solid glyph on a rounded square tinted with
   the colour of the weather). A style the card does not know draws `meridien`.
3. **No colour in a drawing.** Each layer of an icon has the class of its role (sun, moon, cloud, dark cloud, water, snow and ice,
   lightning, mist) and paints in `currentColor`; the card gives each role the colour of its palette for the light or dark theme
   (the colours of 0042). A layer behind another is cut by a mask around the front one, as the screen erases it, so an icon reads
   even in a single colour; each icon drawn gets ids of its own for its masks.
4. **One source.** `scripts/card_icons.py` draws the four styles and writes them into `vigie-card.js` between two marker lines;
   `tests/test_card_icons.py` fails when the card does not hold what it draws. The drawings of the `meridien` style are those of the
   screen's generator (`tools/icones/generer_icones.py` of its repository), ported to SVG: the same discs, capsules and polygons.

## Consequences

- The default look of the card changes: the screen's drawings in place of the card's copies, and twenty kinds of weather in place
  of ten (drizzle, freezing rain or snow grains no longer show as rain or snow).
- The card file grows from about 124 KB to about 184 KB (the four styles, every icon in each), from 37.6 KB to 43.6 KB in the
  gzip copy that `/local` serves (ADR 0044, measured on 2026-11-27); the browser keeps it as long as it does not change.
- The other apps of the family take the same drawings with the same roles, so an icon means the same everywhere; the glyphs of the
  card's pills and titles (umbrella, wind, warning...) are not part of this decision and stay as they are.
