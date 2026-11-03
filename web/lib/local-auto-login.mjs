/** Local development convenience; the web server must bind to loopback only. */
export function allowsLocalAutoLogin({ enabled, nodeEnv, hostname, fetchSite }) {
  return enabled === "true" && nodeEnv === "development"
    && (hostname === "localhost" || hostname === "127.0.0.1")
    && fetchSite === "same-origin";
}
