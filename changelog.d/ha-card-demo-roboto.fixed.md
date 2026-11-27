- **Card demo**: the card is drawn in Roboto for every visitor, as in Home Assistant. The demo serves the files and the
  declarations of Home Assistant's frontend (weights 400, 500 and 700, with their licence, in `demo/fonts/roboto/`) and loads
  them before drawing the card; a visitor without Roboto saw the Sea page of a phone's card go 16 px past its bottom, drawn in
  a wider font. No other site is asked for it. The `fit` job measures these files, on a machine without Roboto.
