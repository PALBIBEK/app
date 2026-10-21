// Shared by the browser journeys: settings, a hard check, and API calls as the demo user.
export const BASE = process.env.BASE_URL ?? 'http://localhost:3000';
export const API = process.env.API_URL ?? 'http://127.0.0.1:8000/api/v1';
export const EMAIL = process.env.DEMO_EMAIL ?? 'demo@example.com';
export const PASSWORD = process.env.DEMO_PASSWORD ?? 'demo-password-123';

/** Fail the journey (exit 1) when a step did not do what it should. */
export function check(ok, what) {
  if (!ok) {
    console.error(`FAILED: ${what}`);
    process.exit(1);
  }
}

/** Headers for the backend as the demo user. */
export async function apiHeaders() {
  const H = { 'Content-Type': 'application/json', Origin: BASE };
  const r = await fetch(`${API}/auth/login`, { method: 'POST', headers: H, body: JSON.stringify({ email: EMAIL, password: PASSWORD }) });
  check(r.ok, `API login (${r.status})`);
  return { ...H, Authorization: `Bearer ${(await r.json()).access_token}` };
}

/** Remove a harness a journey made, so runs do not pile up in the demo account. */
export async function deleteHarness(id) {
  const r = await fetch(`${API}/harnesses/${id}`, { method: 'DELETE', headers: await apiHeaders() });
  console.log('cleanup: harness deleted', r.status);
}
