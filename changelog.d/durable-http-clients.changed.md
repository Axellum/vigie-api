- **Connections to the providers**: each source keeps its HTTP client and its connections between requests, instead of a
  new client for every request (2.7 ms instead of 11.8 ms per request to the vigie-meteo gateway, measured from the
  container of the API). A connection the provider closed just as a request left is asked once more. The gateway sources
  give up a new connection that does not answer after 2 s and try once more (they waited 8 s); a self-hosted Open-Meteo
  gets the same 2 s, and is left aside 15 s instead of 60 when it cannot be reached.
