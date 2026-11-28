# MapLibre GL JS, for the card's Map page

The map of the card's Map page (ADR 0048) is drawn by [MapLibre GL JS](https://github.com/maplibre/maplibre-gl-js),
version **6.14.0**, the one the Vigie website uses. The integration serves these files itself, beside the card, so no
dashboard asks a CDN for them. The card loads them only when its Map page opens. The rest of the card never loads them.

The three files are those of the `dist` folder of the npm package, unchanged:

| File | SHA-256 |
|---|---|
| `maplibre-gl.mjs` | `244d36122c1398da3a8259817cc6d71e04321afe6404f32606b4b6c6cc4f26fb` |
| `maplibre-gl-worker.mjs` | `5f1e2f0e1624082b7e88a06e4c86085992b0f8c02929801e552f3f7a119856a9` |
| `maplibre-gl.css` | `bacba6a85a867fcf602f2ff88331b8acf7edf3fbd063a519888cfbff39970193` |

They come from `https://registry.npmjs.org/maplibre-gl/-/maplibre-gl-6.14.0.tgz`, whose integrity is
`sha512-L7U9y9TyPJ3P/JJbW+eH+A3FzdPq+3fnZFqqeCwf/S6cRGganHwVI5b/C8/zOYzreUzsloDPZVVaMQA94wOt4A==` (`npm view
maplibre-gl@6.14.0 dist.integrity`). `tests_ha/test_map.py` checks the digests above.

Licence: BSD 3-Clause, © MapLibre contributors (`LICENSE.txt`, copied from the package with its notices).

To update: take the three files and `LICENSE.txt` of the new package, write their digests here and in the test, and change
`MAPLIBRE_VERSION` in `card.py` and in the card. The version goes into the URL the card asks, so a browser reads the new files.
