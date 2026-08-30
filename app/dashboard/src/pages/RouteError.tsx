import { TriangleAlert } from "lucide-react";
import { FC } from "react";
import { useTranslation } from "react-i18next";
import { Link, isRouteErrorResponse, useRouteError } from "react-router-dom";
import { apiErrorMessage, apiErrorStatus } from "service/error";
import { Blueprint } from "xenith/Blueprint";
import { LogoLockup } from "xenith/Logo";
import { Login } from "./Login";

/**
 * What the router falls back to when a loader or a screen throws.
 *
 * Only an answer from the API saying who we are not ends the session; anything
 * else — a server that fell over, a request that never arrived, a component
 * that threw while rendering — is reported as the failure it is. Sending every
 * one of those to the login screen used to sign the reader out over a hiccup.
 */
export const RouteError: FC = () => {
  const error = useRouteError();
  const { t } = useTranslation();
  const status = isRouteErrorResponse(error) ? error.status : apiErrorStatus(error);

  if (status === 401 || status === 403) return <Login />;

  const detail =
    apiErrorMessage(error) || (error instanceof Error ? error.message : "") || t("xenith.routeError.unknown");

  return (
    <div
      className="xn-root"
      style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 24 }}
    >
      <div style={{ width: 520, maxWidth: "100%", display: "flex", flexDirection: "column", gap: 22 }}>
        <LogoLockup />

        <Blueprint style={{ padding: "30px 28px", display: "flex", flexDirection: "column", gap: 18 }}>
          <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
            <TriangleAlert size={20} strokeWidth={1.5} color="var(--xn-accent-800)" style={{ flex: "none", marginTop: 2 }} />
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <span className="xn-kicker">
                {status ? t("xenith.routeError.status", { status }) : t("xenith.routeError.kicker")}
              </span>
              <h1 style={{ fontSize: 28 }}>{t("xenith.routeError.title")}</h1>
            </div>
          </div>

          <p style={{ margin: 0, fontSize: 13.5, lineHeight: 1.5, color: "var(--xn-neutral-700)" }}>
            {t("xenith.routeError.body")}
          </p>

          <div
            className="xn-mono"
            style={{
              fontSize: 12,
              lineHeight: 1.45,
              padding: "10px 12px",
              border: "1px solid var(--xn-divider)",
              color: "var(--xn-neutral-800)",
              overflowWrap: "anywhere",
            }}
          >
            {detail}
          </div>

          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <Link className="xn-btn xn-btn-secondary" to="/login">
              {t("xenith.routeError.signIn")}
            </Link>
            <button className="xn-btn xn-btn-primary" onClick={() => window.location.reload()}>
              {t("xenith.routeError.retry")}
            </button>
          </div>
        </Blueprint>
      </div>
    </div>
  );
};

export default RouteError;
