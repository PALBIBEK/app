import axios from 'axios';

export function getApiErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string' && detail.length > 0) {
      return detail;
    }
    if (Array.isArray(detail) && detail[0]?.msg) {
      return String(detail[0].msg);
    }
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}
