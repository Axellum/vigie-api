# Recorded climate sheets of Météo-France

Two genuine sheets of the "Fiches climatologiques" of Météo-France (data.gouv.fr, Licence Ouverte 2.0), recorded verbatim on
`2026-11-17` (edited by Météo-France on `2026-11-06`) from
`https://meteofrance.s3.sbg.io.cloud.ovh.net/data/synchro_ftp/REF_STATION/FICHECLIM_<id>.data`:

- `FICHECLIM_34107006.data`: Murat-sur-Vèbre (Hérault, 1022 m), a mountain station whose temperature and rain normals cover
  2002-2020 only, with no sunshine (the block says "Données non disponibles" and must not take the rows of the next one).
- `FICHECLIM_98715002.data`: Faaa (French Polynesia), southern and western coordinates, no frost row.

They are read by `tests/test_build_climate_normals.py`; the files have Windows line ends upstream (`.gitattributes` stores LF).
