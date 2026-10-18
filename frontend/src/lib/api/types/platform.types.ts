/**
 * Platform readiness types (D2) — mirror BFF PlatformStatusResponse.
 */

export interface ComponentStatus {
  ready: boolean;
  status: string;
  reason: string | null;
}

export interface PlatformStatus {
  ready: boolean;
  run_allowed: boolean;
  checked_at: string;
  components: Record<string, ComponentStatus>;
  residual?: string[];
}
