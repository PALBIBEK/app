import axios from 'axios';

/**
 * The reason a backend error response gives, for showing to the user. The backend answers
 * `{detail: "..."}`, `{detail: {message: "..."}}` or, for validation, `{detail: [{msg: "..."}]}`.
 */
export function serverMessage(data: unknown): string | null {
  if (!data || typeof data !== 'object') return null;
  const detail = (data as { detail?: unknown }).detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === 'string' && message.trim()) return message;
  }
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (d && typeof d === 'object' ? (d as { msg?: unknown }).msg : null))
      .filter((m): m is string => typeof m === 'string' && m.length > 0);
    if (msgs.length) return msgs.join('; ');
  }
  return null;
}

export function getApiErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error)) {
    const reason = serverMessage(error.response?.data);
    if (reason) return reason;
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}
