- **CORS**: any page may still read the API, but no longer with the browser's cookies. The API answered
  `Access-Control-Allow-Credentials: true` and echoed the origin of the page; it reads no cookie (the admin token is a header),
  but the site's account cookie lives on the same host, so a route trusting it later would have been readable from any page.
  The answers now carry `Access-Control-Allow-Origin: *` without credentials; the `Authorization` header still goes through.
