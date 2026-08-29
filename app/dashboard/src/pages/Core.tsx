import { useToast } from "@chakra-ui/react";
import { FC, Suspense, lazy, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "react-query";
import { useCoreSettings } from "contexts/CoreSettingsContext";
import { Blueprint } from "xenith/Blueprint";
import { ConfirmDialog } from "xenith/ConfirmDialog";
import { LogLines, PanelHead, PanelNote } from "xenith/panels";
import { useCoreLogs } from "xenith/useCoreLogs";

const JsonEditor = lazy(() => import("components/JsonEditor").then((mod) => ({ default: mod.JsonEditor })));

/** Enough to watch the core come back after a save; the Logs screen keeps the rest. */
const LOG_LINES = 200;

/** Formatting differences are not edits, so both sides are compared parsed. */
const normalise = (text: string) => {
  try {
    return JSON.stringify(JSON.parse(text));
  } catch {
    return text;
  }
};

const EditorFallback: FC = () => {
  const { t } = useTranslation();
  return (
    <div
      className="xn-mono"
      style={{
        height: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 12,
        color: "var(--xn-neutral-600)",
      }}
    >
      {t("xenith.coreConfig.loading")}
    </div>
  );
};

/**
 * The core's own screen: the xray configuration, the version running it, and
 * the two things one does to it. The editor carries monaco with it, so it is
 * fetched only once this route is opened.
 */
export const Core: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();
  const { fetchCoreSettings, updateConfig, restartCore, config, version, started, isLoading, isPostLoading } =
    useCoreSettings();
  const { logs } = useCoreLogs(LOG_LINES);

  // The editor hands back text, and it binds its callbacks once on mount — so
  // what those callbacks read has to be refs rather than state.
  const draft = useRef("");
  const saved = useRef("");
  const [dirty, setDirty] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [confirmRestart, setConfirmRestart] = useState(false);

  useEffect(() => {
    fetchCoreSettings();
  }, [fetchCoreSettings]);

  // What the file said when it arrived, which is what "unsaved" is measured against.
  useEffect(() => {
    if (isLoading) return;
    saved.current = JSON.stringify(config, null, 2);
    draft.current = saved.current;
  }, [isLoading, config]);

  // Leaving by reload is the one exit React Router cannot intercept, so it is
  // the one the browser has to ask about.
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const onSave = () => {
    const body = draft.current;
    updateConfig(body)
      .then(() => {
        saved.current = body;
        setDirty(false);
        queryClient.invalidateQueries("xenith-core");
        toast({ title: t("core.successMessage"), status: "success", isClosable: true, position: "top", duration: 3000 });
      })
      .catch((err) => {
        const detail = err?.response?._data?.detail;
        const message =
          typeof detail === "string"
            ? detail
            : detail && typeof detail === "object"
              ? detail[Object.keys(detail)[0]]
              : t("core.generalErrorMessage");
        toast({ title: message, status: "error", isClosable: true, position: "top", duration: 5000 });
      });
  };

  const onRestart = () => {
    setRestarting(true);
    restartCore()
      .catch(() => undefined)
      .finally(() => {
        setRestarting(false);
        setConfirmRestart(false);
        queryClient.invalidateQueries("xenith-core");
      });
  };

  return (
    <>
      <Blueprint style={{ padding: "18px 20px 20px", display: "flex", flexDirection: "column", gap: 14 }}>
        <PanelHead
          title={t("xenith.coreConfig.title")}
          note={t("xenith.coreConfig.note")}
          trailing={
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              {version && <span className="xn-tag xn-tag-outline xn-mono">v{version}</span>}
              <span className={`xn-tag ${started ? "xn-tag-accent" : "xn-tag-neutral"}`}>
                {started ? t("xenith.running") : t("xenith.stopped")}
              </span>
            </div>
          }
        />

        <div
          style={{
            border: "1px solid var(--xn-neutral-300)",
            height: "56vh",
            minHeight: 320,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          {isLoading ? (
            <EditorFallback />
          ) : (
            <Suspense fallback={<EditorFallback />}>
              <JsonEditor
                json={config}
                onSave={onSave}
                onChange={(value) => {
                  draft.current = value;
                  setDirty(normalise(value) !== normalise(saved.current));
                }}
              />
            </Suspense>
          )}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <button
            className="xn-btn"
            style={{ fontSize: 12.5 }}
            onClick={() => setConfirmRestart(true)}
            disabled={restarting}
          >
            {t(restarting ? "core.restarting" : "core.restartCore")}
          </button>
          {dirty && <PanelNote>{t("xenith.coreConfig.unsaved")}</PanelNote>}
          <button
            className="xn-btn xn-btn-primary"
            style={{ fontSize: 12.5, marginLeft: "auto" }}
            onClick={onSave}
            disabled={isLoading || isPostLoading || !dirty}
          >
            {t("core.save")}
          </button>
        </div>
      </Blueprint>

      <Blueprint style={{ padding: "18px 20px", display: "flex", flexDirection: "column", gap: 12 }}>
        <PanelHead title={t("xenith.coreConfig.logs")} note={t("xenith.coreConfig.logsNote")} />
        <LogLines logs={logs} maxHeight="34vh" empty={t("xenith.events.waiting")} />
      </Blueprint>

      <ConfirmDialog
        open={confirmRestart}
        title={t("xenith.restartCore")}
        body={t("xenith.restartCorePrompt")}
        confirmLabel={t("xenith.restartCore")}
        busy={restarting}
        onConfirm={onRestart}
        onClose={() => setConfirmRestart(false)}
      />
    </>
  );
};

export default Core;
