/**
 * English catalog — shell + foundation strings only in Phase 1.
 * Full sentences; interpolate via `t()`, never concatenate fragments.
 */
export const catalog = {
  "nav.catalog": "Catalog",
  "nav.runs": "Runs",
  "nav.observe": "Observe",
  "nav.settings": "Settings",
  "shell.search": "Search",
  "shell.search.placeholder": "Search routes and actions…",
  "shell.theme": "Theme",
  "shell.theme.light": "Light",
  "shell.theme.dark": "Dark",
  "shell.theme.system": "System",
  "shell.user": "Account",
  "shell.tenant": "Tenant",
  "shell.commandPalette.title": "Command palette",
  "shell.commandPalette.empty": "No matching commands.",
  "shell.breadcrumb.home": "Home",
  "error.network": "Network error. Check your connection and try again.",
  "error.auth": "Your session expired. Sign in again to continue.",
  "error.permission": "You need {missing} permission for this action.",
  "error.notFound": "{resource} was not found.",
  "error.rateLimit": "Too many requests. Try again in {retryAfter}s.",
  "error.server": "Something went wrong on the server.",
  "error.budgetExceeded": "Budget {limit} exceeded (current: {current}).",
  "error.validation": "Please fix the highlighted fields.",
  "runs.empty.title": "No runs yet",
  "runs.empty.body": "Launch a run from the catalog to see it here.",
} as const;

export type CatalogKey = keyof typeof catalog;
