/**
 * Local development convenience; the web server must bind to loopback only.
 *
 * `allowAnyLoopback` covers the browserless audio participant and scripted
 * local clients, which cannot present a browser cookie or a Sec-Fetch-Site
 * header. It still requires development mode on a loopback hostname.
 */
export function allowsLocalAutoLogin({ enabled, nodeEnv, hostname, fetchSite, allowAnyLoopback = false }) {
  const loopback = hostname === "localhost" || hostname === "127.0.0.1";
  if (!loopback || nodeEnv !== "development") return false;
  if (allowAnyLoopback) return true;
  return enabled === "true" && fetchSite === "same-origin";
}

/** True only for a loopback hostname in development, regardless of headers. */
export function isLoopbackDevelopment({ nodeEnv, hostname }) {
  return nodeEnv === "development" && (hostname === "localhost" || hostname === "127.0.0.1");
}
