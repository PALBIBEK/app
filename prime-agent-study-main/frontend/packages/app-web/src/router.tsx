import {
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
} from "@tanstack/react-router";
import { ShellLayout } from "./ShellLayout";
import { KitchenSinkPage } from "./pages/KitchenSinkPage";
import {
  CatalogPage,
  ObservePage,
  RunsPage,
  SettingsPage,
} from "./pages/Placeholders";

const rootRoute = createRootRoute({
  component: () => <Outlet />,
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/$tenant/runs", params: { tenant: "default" } });
  },
});

const tenantRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/$tenant",
  component: ShellLayout,
});

const catalogRoute = createRoute({
  getParentRoute: () => tenantRoute,
  path: "/catalog",
  component: CatalogPage,
});

const runsRoute = createRoute({
  getParentRoute: () => tenantRoute,
  path: "/runs",
  component: RunsPage,
});

const observeRoute = createRoute({
  getParentRoute: () => tenantRoute,
  path: "/observe",
  component: ObservePage,
});

const settingsRoute = createRoute({
  getParentRoute: () => tenantRoute,
  path: "/settings",
  component: SettingsPage,
});

const kitchenSinkRoute = createRoute({
  getParentRoute: () => tenantRoute,
  path: "/dev/kitchen-sink",
  component: KitchenSinkPage,
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  tenantRoute.addChildren([
    catalogRoute,
    runsRoute,
    observeRoute,
    settingsRoute,
    ...(import.meta.env.PROD ? [] : [kitchenSinkRoute]),
  ]),
]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
