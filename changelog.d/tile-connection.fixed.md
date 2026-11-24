- **Radar tiles**: fewer 503s. A connection the gateway closed just as a tile request left is asked once more, a
  new connection that does not answer is given up after 2 s and tried once more (it waited 8 s), and a connection is kept
  30 s instead of 5 (the gateway keeps it 75 s, vigie-meteo#21).
