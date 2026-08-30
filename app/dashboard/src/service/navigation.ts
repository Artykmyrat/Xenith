import type { Router } from "@remix-run/router";

/**
 * The running router, handed over once it exists.
 *
 * Stores need to navigate, and importing `pages/Router` from a store would
 * close an import cycle — the store reaches for the router, the router renders
 * the screens, and the screens reach back for the store. Registering the
 * router here leaves the store depending on this module alone.
 */
let router: Router | null = null;

export const setNavigationRouter = (instance: Router) => {
  router = instance;
};

export const navigateTo = (to: string, options?: { replace?: boolean }) => {
  router?.navigate(to, options);
};
