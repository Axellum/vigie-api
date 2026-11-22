- **Home Assistant**: the integration (0.5.1) is declared `cloud_polling` instead of `local_push`. By default it reads the public
  instance over the Internet and polls it; the push of `WS /v1/stream` only comes on top from an instance that runs its scheduler.
  Home Assistant shows this class on the page of the integration.
