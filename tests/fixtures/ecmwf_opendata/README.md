# ECMWF open data, recorded on 2026-10-25

Leads 3 and 6 hours of the ECMWF IFS 0.25 degree run of 2026-10-25 00 UTC, read on ECMWF's mirror on AWS
(`https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/20261025/00z/ifs/0p25/oper/20261025000000-{3,6}h-oper-fc.{index,grib2}`).
Licence: CC BY 4.0 (https://registry.opendata.aws/ecmwf-forecasts/).

Only the five surface fields the pilot reads were kept (`tp`, `10fg`, `mx2t3`, `mn2t3`, `ptype`), each **cut to Turkey**
(25.5 to 45 E, 35.5 to 42.5 N, 29 x 79 points) with ecCodes so the files stay small: the values are the run's, the grid is a
part of it. The `.index` files are the mirror's lines for those five fields, with `_offset` and `_length` rewritten to point into
the cut `.grib2` files.
