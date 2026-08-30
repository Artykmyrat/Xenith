import { useToast } from "@chakra-ui/react";
import { FC, Suspense, lazy, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "react-query";
import { useCoreSettings } from "contexts/CoreSettingsContext";
import { InboundSecurity, InboundTransport, inboundTemplate, restartHysteria, useHysteria } from "xenith/api";
import { Blueprint } from "xenith/Blueprint";
import { ConfirmDialog } from "xenith/ConfirmDialog";
import { LogLines, NoticeBox, PanelHead, PanelNote } from "xenith/panels";
import { useCoreLogs } from "xenith/useCoreLogs";

const JsonEditor = lazy(() => import("components/JsonEditor").then((mod) => ({ default: mod.JsonEditor })));

/** Enough to watch the core come back after a save; the Logs screen keeps the rest. */
const LOG_LINES = 200;

/** REALITY has no WebSocket to borrow a handshake from, so that pair is blocked. */
const TEMPLATES: { transport: InboundTransport; label: string }[] = [
  { transport: "tcp", label: "VLESS TCP" },
  { transport: "xhttp", label: "VLESS XHTTP" },
  { transport: "grpc", label: "VLESS gRPC" },
  { transport: "ws", label: "VLESS WS" },
];

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
 * Hysteria2, which is a second daemon rather than an xray protocol: it has its
 * own configuration, its own port and its own reasons for being down. It shows
 * here because this is the screen about cores, and stays hidden entirely while
 * the panel is not configured for it — an empty panel would only ask questions.
 */
const Hysteria: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();
  const { data } = useHysteria();
  const [restarting, setRestarting] = useState(false);

  if (!data?.enabled) return null;

  const onRestart = () => {
    setRestarting(true);
    restartHysteria()
      .then((result) => {
        queryClient.setQueryData("xenith-hysteria", result);
        if (result.reason) {
          toast({ title: result.reason, status: "error", position: "top", duration: 6000, isClosable: true });
        }
      })
      .catch(() => undefined)
      .finally(() => setRestarting(false));
  };

  return (
    <Blueprint style={{ padding: "18px 20px", display: "flex", flexDirection: "column", gap: 12 }}>
      <PanelHead
        title={t("xenith.hysteria.title")}
        note={t("xenith.hysteria.note", { port: data.port })}
        trailing={
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {data.version && <span className="xn-tag xn-tag-outline xn-mono">v{data.version}</span>}
            <span className={`xn-tag ${data.running ? "xn-tag-accent" : "xn-tag-neutral"}`}>
              {data.running ? t("xenith.running") : t("xenith.stopped")}
            </span>
            <button className="xn-btn" style={{ fontSize: 12.5 }} onClick={onRestart} disabled={restarting}>
              {t(restarting ? "core.restarting" : "core.restartCore")}
            </button>
          </div>
        }
      />
      {data.reason && (
        <NoticeBox>
          <span>{data.reason}</span>
        </NoticeBox>
      )}
    </Blueprint>
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
  // Set when a template is appended: the editor takes its content from this
  // once there is one, which is how the new inbound gets in front of the eyes.
  const [doc, setDoc] = useState<any>(null);
  const [security, setSecurity] = useState<InboundSecurity>("reality");
  const [adding, setAdding] = useState<InboundTransport | null>(null);

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

  const onAddTemplate = (transport: InboundTransport) => {
    let current: any;
    try {
      current = JSON.parse(draft.current);
    } catch {
      toast({ title: t("xenith.coreConfig.templateInvalid"), status: "error", position: "top", duration: 4000 });
      return;
    }

    const inbounds: any[] = Array.isArray(current?.inbounds) ? current.inbounds : [];
    setAdding(transport);
    inboundTemplate({
      transport,
      security,
      taken_tags: inbounds.map((inbound) => inbound?.tag).filter((tag): tag is string => typeof tag === "string"),
      taken_ports: inbounds.map((inbound) => inbound?.port).filter((port): port is number => typeof port === "number"),
    })
      .then((inbound) => {
        setDoc({ ...current, inbounds: [...inbounds, inbound] });
        setDirty(true);
        toast({
          title: t("xenith.coreConfig.templateAdded", { tag: inbound.tag }),
          status: "success",
          position: "top",
          duration: 4000,
          isClosable: true,
        });
      })
      .catch((err) => {
        const detail = err?.response?._data?.detail;
        toast({
          title: typeof detail === "string" ? detail : t("core.generalErrorMessage"),
          status: "error",
          position: "top",
          duration: 6000,
          isClosable: true,
        });
      })
      .finally(() => setAdding(null));
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

        {/* Templates append to the configuration rather than replace it, so
            what is on screen is never lost to a stray click. */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span className="xn-label">{t("xenith.coreConfig.templates")}</span>
          {TEMPLATES.map(({ transport, label }) => {
            const blocked = security === "reality" && transport === "ws";
            return (
              <button
                key={transport}
                type="button"
                className="xn-btn"
                style={{ fontSize: 12.5 }}
                title={blocked ? t("xenith.coreConfig.realityNeedsStream") : undefined}
                disabled={blocked || isLoading || adding !== null}
                onClick={() => onAddTemplate(transport)}
              >
                {adding === transport ? t("xenith.working") : label}
              </button>
            );
          })}
          <div className="xn-seg" style={{ marginLeft: "auto" }}>
            {(["reality", "tls"] as InboundSecurity[]).map((option) => (
              <button
                key={option}
                type="button"
                className="xn-seg-opt"
                style={{ fontSize: 12 }}
                aria-pressed={security === option}
                onClick={() => setSecurity(option)}
              >
                {option.toUpperCase()}
              </button>
            ))}
          </div>
        </div>

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
                json={doc ?? config}
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

      <Hysteria />

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
