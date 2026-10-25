# GFS 0.25 degree, cut by the NOMADS grib filter

Two real answers of the NOMADS grib filter, recorded on 2026-10-25 for the unofficial pre-alert pilot
([ADR 0011](../../../docs/decisions/0011-unofficial-pre-alert-turkey-shadow-pilot.md)):

- `gfs.t00z.20261025.f003.grib2` (21 KB) and `gfs.t00z.20261025.f006.grib2` (22 KB): the run of 2026-10-25 at 00 UTC, leads 3 and 6 hours.

Asked with:

```
https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl?dir=%2Fgfs.20261025%2F00%2Fatmos&file=gfs.t00z.pgrb2.0p25.f003
  &var_APCP=on&var_GUST=on&var_TMAX=on&var_TMIN=on&var_TMP=on&var_CSNOW=on&lev_surface=on&lev_2_m_above_ground=on
  &subregion=&toplat=42.5&leftlon=25.5&rightlon=45&bottomlat=35.5
```

Each holds nine GRIB2 messages on a 79 x 29 grid (25.5 to 45 E, 35.5 to 42.5 N): the precipitation accumulated from the start of the
run (twice), the gust, the temperature at 2 m and at the surface, its maximum and minimum since the last 3 or 6 hours, and the
categorical snow (instantaneous and averaged). NOAA's GFS "can be used as desired" (https://registry.opendata.aws/noaa-gfs-bdp-pds/).
