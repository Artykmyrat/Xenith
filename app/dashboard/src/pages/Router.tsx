import { createHashRouter } from "react-router-dom";
import { setNavigationRouter } from "service/navigation";
import { queryClient } from "utils/react-query";
import { ADMIN_QUERY_KEY, fetchAdmin } from "xenith/api";
import { AppShell } from "../xenith/AppShell";
import { Backup } from "./Backup";
import { Certificates } from "./Certificates";
import { Core } from "./Core";
import { Inbounds } from "./Inbounds";
import { Login } from "./Login";
import { Logs } from "./Logs";
import { Nginx } from "./Nginx";
import { Nodes } from "./Nodes";
import { Overview } from "./Overview";
import { RouteError } from "./RouteError";
import { Settings } from "./Settings";
import { Traffic } from "./Traffic";
import { Users } from "./Users";

/**
 * The session cookie is attached by the browser, so the loader only has to ask
 * who we are. The answer goes through the query cache, so the shell's own
 * `useAdmin` reads it instead of asking a second time, and walking between
 * screens does not re-ask on every step. A 401 falls through to the error
 * element, which is the only thing that shows the login screen again.
 */
const fetchAdminLoader = () =>
  queryClient.fetchQuery(ADMIN_QUERY_KEY, fetchAdmin, { retry: false, staleTime: 30_000 });

export const router = createHashRouter([
  {
    path: "/",
    element: <AppShell />,
    errorElement: <RouteError />,
    loader: fetchAdminLoader,
    children: [
      { index: true, element: <Overview /> },
      { path: "traffic", element: <Traffic /> },
      { path: "nodes", element: <Nodes /> },
      { path: "logs", element: <Logs /> },
      { path: "inbounds", element: <Inbounds /> },
      { path: "certificates", element: <Certificates /> },
      { path: "core", element: <Core /> },
      { path: "nginx", element: <Nginx /> },
      { path: "users", element: <Users /> },
      { path: "settings", element: <Settings /> },
      { path: "backup", element: <Backup /> },
    ],
  },
  {
    path: "/login/",
    element: <Login />,
  },
]);

// Handed to the stores, which navigate without importing this module back.
setNavigationRouter(router);
