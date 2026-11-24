- **Memory and database**: a source keeps at most 4,096 answers in memory (one per place for the weather), the oldest
  refreshed going first; until now a source kept every place ever asked. The database sorts in memory with a larger
  cache: the 30-day summary of the warnings archive takes about 180 ms instead of 410.
