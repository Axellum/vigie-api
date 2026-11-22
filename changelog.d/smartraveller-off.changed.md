- **Travel advice**: Smartraveller (Australia) is off by default, `VIGIE_ENABLE_SMARTRAVELLER=1` turns it back on. The protection
  of its server answers neither the TLS client of the image nor the addresses of hosting providers (measured on 2026-11-22), so
  every read timed out or was refused. Off, Australia is listed as not read with that reason (`ministries[].why_not_read`), and the
  source appears in `disabled_sources` of `GET /v1/health` instead of the providers.
