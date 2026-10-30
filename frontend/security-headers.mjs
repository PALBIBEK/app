/**
 * Response headers for every page of the app.
 *
 * The API already sends these on its own responses; the pages are served by Next, and
 * sent none. Without `frame-ancestors`, another site could load the app in an invisible
 * frame and trick a signed-in user into a click: Approve on a tool call, say.
 *
 * The CSP only restricts what needs no script audit (framing, plugins, <base>, where
 * forms post); a script and style policy for Next's inline runtime is separate work.
 */

/** @param {{ production: boolean }} options */
export function securityHeaders({ production }) {
  const headers = [
    { key: 'Content-Security-Policy', value: "frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'" },
    { key: 'X-Frame-Options', value: 'DENY' }, // browsers without frame-ancestors
    { key: 'X-Content-Type-Options', value: 'nosniff' },
    { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
    { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=(), payment=()' },
  ];
  if (production) {
    // Served over TLS: never downgrade. (Browsers ignore it on plain-HTTP dev servers anyway.)
    headers.push({ key: 'Strict-Transport-Security', value: 'max-age=31536000; includeSubDomains' });
  }
  return headers;
}
