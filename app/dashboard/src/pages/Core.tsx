import { useToast } from "@chakra-ui/react";
import { FC, Suspense, lazy, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "react-query";
import { useCoreSettings } from "contexts/CoreSettingsContext";
import {
  HysteriaSettings,
  HysteriaSettingsPatch,
  InboundSecurity,
  InboundTransport,
  inboundTemplate,
  restartHysteria,
  saveHysteriaSettings,
  useHysteriaSettings,
} from "xenith/api";
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

/** One labelled control in the hysteria form. */
const Field: FC<{ label: string; hint?: string; children: React.ReactNode; wide?: boolean }> = ({
  label,
  hint,
  children,
  wide,
}) => (
  <div
    style={{
      display: "flex",
      flexDirection: "column",
      gap: 4,
      minWidth: 0,
      gridColumn: wide ? "1 / -1" : undefined,
    }}
  >
    <span className="xn-label">{label}</span>
    {children}
    {hint && <span style={{ fontSize: 11, lineHeight: 1.45, color: "var(--xn-neutral-600)" }}>{hint}</span>}
  </div>
);

/**
 * Hysteria2, which is a second daemon rather than an xray protocol: it has its
 * own configuration, its own port and its own reasons for being down. It shows
 * here because this is the screen about cores.
 *
 * Saving restarts the daemon, because the configuration is rendered when it
 * starts and settings that are stored but not running are the thing an admin
 * is least likely to notice. The rendered file is shown read-only underneath:
 * it is regenerated on every start, so editing it by hand would last until the
 * next one — anything the form does not cover goes in `extra` instead.
 */
const Hysteria: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();
  const { data, isLoading } = useHysteriaSettings();
  const [draft, setDraft] = useState<HysteriaSettingsPatch | null>(null);
  const [extraText, setExtraText] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [showConfig, setShowConfig] = useState(false);

  // The form edits a copy; what is on screen is that copy over what the server
  // last said, so a field nobody touched follows the server.
  const value = <K extends keyof HysteriaSettingsPatch>(key: K): any =>
    draft && key in draft ? draft[key] : (data as any)?.[key];

  const set = (changes: HysteriaSettingsPatch) => setDraft((d) => ({ ...(d || {}), ...changes }));

  const extraOnScreen =
    extraText !== null ? extraText : data?.extra ? JSON.stringify(data.extra, null, 2) : "";

  const settle = (result: HysteriaSettings, title: string) => {
    queryClient.setQueryData("xenith-hysteria-settings", result);
    queryClient.invalidateQueries("xenith-hysteria");
    setDraft(null);
    setExtraText(null);
    toast({
      title: result.reason || title,
      description: result.reason ? undefined : undefined,
      status: result.reason ? "warning" : "success",
      position: "top",
      duration: result.reason ? 7000 : 3000,
      isClosable: true,
    });
  };

  const fail = (err: any) =>
    toast({
      title: err?.response?._data?.detail || t("xenith.hysteria.saveFailed"),
      status: "error",
      position: "top",
      duration: 6000,
      isClosable: true,
    });

  const onSave = () => {
    const body: HysteriaSettingsPatch = { ...(draft || {}) };

    if (extraText !== null) {
      const text = extraText.trim();
      if (!text) {
        body.extra = null;
      } else {
        try {
          const parsed = JSON.parse(text);
          if (typeof parsed !== "object" || Array.isArray(parsed) || parsed === null) {
            throw new Error("not an object");
          }
          body.extra = parsed;
        } catch {
          toast({
            title: t("xenith.hysteria.extraInvalid"),
            status: "error",
            position: "top",
            duration: 5000,
            isClosable: true,
          });
          return;
        }
      }
    }

    if (!Object.keys(body).length) return;

    setSaving(true);
    saveHysteriaSettings(body)
      .then((result) => settle(result, t("xenith.hysteria.saved")))
      .catch(fail)
      .finally(() => setSaving(false));
  };

  // Turning the switch is applied straight away rather than waiting for Save:
  // it is the one control whose meaning is "do it now".
  const onToggle = (enabled: boolean) => {
    setSaving(true);
    saveHysteriaSettings({ enabled })
      .then((result) =>
        settle(result, enabled ? t("xenith.hysteria.turnedOn") : t("xenith.hysteria.turnedOff")),
      )
      .catch(fail)
      .finally(() => setSaving(false));
  };

  const onRestart = () => {
    setRestarting(true);
    restartHysteria()
      .then((result) => {
        queryClient.invalidateQueries("xenith-hysteria-settings");
        if (result.reason) {
          toast({ title: result.reason, status: "error", position: "top", duration: 6000, isClosable: true });
        }
      })
      .catch(() => undefined)
      .finally(() => setRestarting(false));
  };

  const dirty = draft !== null || extraText !== null;
  const busy = saving || restarting || isLoading;

  return (
    <Blueprint style={{ padding: "18px 20px", display: "flex", flexDirection: "column", gap: 14 }}>
      <PanelHead
        title={t("xenith.hysteria.title")}
        note={t("xenith.hysteria.note", { port: data?.port ?? "—" })}
        trailing={
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {data?.version && <span className="xn-tag xn-tag-outline xn-mono">v{data.version}</span>}
            <span className={`xn-tag ${data?.running ? "xn-tag-accent" : "xn-tag-neutral"}`}>
              {data?.running ? t("xenith.running") : t("xenith.stopped")}
            </span>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={!!data?.enabled}
                disabled={busy}
                onChange={(e) => onToggle(e.target.checked)}
              />
              <span>{t("xenith.hysteria.enabled")}</span>
            </label>
            <button
              className="xn-btn"
              style={{ fontSize: 12.5 }}
              onClick={onRestart}
              disabled={busy || !data?.enabled}
            >
              {t(restarting ? "core.restarting" : "core.restartCore")}
            </button>
          </div>
        }
      />

      {data?.reason && (
        <NoticeBox>
          <span>{data.reason}</span>
        </NoticeBox>
      )}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))",
          gap: 12,
        }}
      >
        <Field label={t("xenith.hysteria.port")} hint={t("xenith.hysteria.portHint")}>
          <input
            className="xn-input xn-mono"
            type="number"
            min={1}
            max={65535}
            value={value("port") ?? ""}
            disabled={busy}
            onChange={(e) => set({ port: Number(e.target.value) })}
          />
        </Field>

        <Field label={t("xenith.hysteria.domain")} hint={t("xenith.hysteria.domainHint")}>
          <select
            className="xn-input xn-mono"
            value={value("domain") ?? ""}
            disabled={busy}
            onChange={(e) => set({ domain: e.target.value || null })}
          >
            <option value="">{t("xenith.hysteria.domainAny")}</option>
            {(data?.certificates || []).map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </Field>

        <Field label={t("xenith.hysteria.obfs")} hint={t("xenith.hysteria.obfsHint")}>
          <div style={{ display: "flex", gap: 6 }}>
            <input
              className="xn-input xn-mono"
              style={{ minWidth: 0 }}
              value={value("obfs_password") ?? ""}
              placeholder={t("xenith.hysteria.obfsOff")}
              disabled={busy}
              onChange={(e) => set({ obfs_password: e.target.value || null })}
            />
            <button
              className="xn-btn xn-btn-secondary"
              style={{ fontSize: 12 }}
              disabled={busy}
              onClick={() =>
                set({
                  obfs_password: Array.from(crypto.getRandomValues(new Uint8Array(12)))
                    .map((b) => b.toString(16).padStart(2, "0"))
                    .join(""),
                })
              }
            >
              {t("xenith.hysteria.generate")}
            </button>
          </div>
        </Field>

        <Field label={t("xenith.hysteria.statsPort")} hint={t("xenith.hysteria.statsPortHint")}>
          <input
            className="xn-input xn-mono"
            type="number"
            min={1}
            max={65535}
            value={value("stats_port") ?? ""}
            disabled={busy}
            onChange={(e) => set({ stats_port: Number(e.target.value) })}
          />
        </Field>

        <Field label={t("xenith.hysteria.up")} hint={t("xenith.hysteria.bandwidthHint")}>
          <input
            className="xn-input xn-mono"
            type="number"
            min={0}
            value={value("up_mbps") ?? 0}
            disabled={busy}
            onChange={(e) => set({ up_mbps: Number(e.target.value) })}
          />
        </Field>

        <Field label={t("xenith.hysteria.down")} hint={t("xenith.hysteria.bandwidthHint")}>
          <input
            className="xn-input xn-mono"
            type="number"
            min={0}
            value={value("down_mbps") ?? 0}
            disabled={busy}
            onChange={(e) => set({ down_mbps: Number(e.target.value) })}
          />
        </Field>

        <Field label={t("xenith.hysteria.masquerade")} hint={t("xenith.hysteria.masqueradeHint")} wide>
          <input
            className="xn-input xn-mono"
            value={value("masquerade_url") ?? ""}
            placeholder={t("xenith.hysteria.masqueradeOff")}
            disabled={busy}
            onChange={(e) => set({ masquerade_url: e.target.value })}
          />
        </Field>

        <Field
          label={t("xenith.hysteria.extra")}
          hint={t("xenith.hysteria.extraHint", { keys: (data?.reserved_keys || []).join(", ") })}
          wide
        >
          <textarea
            className="xn-input xn-mono"
            rows={4}
            spellCheck={false}
            style={{ resize: "vertical", lineHeight: 1.5 }}
            value={extraOnScreen}
            placeholder='{ "udpIdleTimeout": "90s" }'
            disabled={busy}
            onChange={(e) => setExtraText(e.target.value)}
          />
        </Field>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button className="xn-btn xn-btn-primary" style={{ fontSize: 12.5 }} onClick={onSave} disabled={busy || !dirty}>
          {saving ? t("xenith.hysteria.saving") : t("xenith.hysteria.save")}
        </button>
        {dirty && (
          <button
            className="xn-link"
            style={{ fontSize: 12 }}
            onClick={() => {
              setDraft(null);
              setExtraText(null);
            }}
            disabled={busy}
          >
            {t("xenith.hysteria.discard")}
          </button>
        )}
        <span style={{ fontSize: 11.5, color: "var(--xn-neutral-600)", marginLeft: "auto" }}>
          {t("xenith.hysteria.savingRestarts")}
        </span>
      </div>

      {data?.config && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <button
            type="button"
            className="xn-link"
            style={{ fontSize: 12, alignSelf: "flex-start" }}
            onClick={() => setShowConfig((open) => !open)}
          >
            {showConfig ? t("xenith.hysteria.hideConfig") : t("xenith.hysteria.showConfig")}
          </button>
          {showConfig && (
            <>
              <PanelNote>{t("xenith.hysteria.configNote")}</PanelNote>
              <pre
                className="xn-mono"
                style={{
                  fontSize: 11.5,
                  lineHeight: 1.55,
                  margin: 0,
                  padding: 12,
                  overflowX: "auto",
                  border: "1px solid var(--xn-neutral-200)",
                  background: "var(--xn-neutral-100)",
                }}
              >
                {data.config}
              </pre>
            </>
          )}
        </div>
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
  const { fetchCoreSettings, updateConfig, restartCore, config, version, started, isLoading, isPostLoading, error } =
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

        {error !== null && <NoticeBox>{error || t("core.generalErrorMessage")}</NoticeBox>}

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
