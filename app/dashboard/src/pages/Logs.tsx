import { FC } from "react";
import { useTranslation } from "react-i18next";
import { ReadyState } from "react-use-websocket";
import { Blueprint } from "xenith/Blueprint";
import { LogLines, PanelHead, PanelNote } from "xenith/panels";
import { useCoreLogs } from "xenith/useCoreLogs";

const MAX_LINES = 500;

/** The live core log stream, in the same row format as the overview panel. */
export const Logs: FC = () => {
  const { t } = useTranslation();
  const { logs, readyState } = useCoreLogs(MAX_LINES);

  const status =
    readyState === ReadyState.OPEN
      ? t("xenith.logs.connected")
      : readyState === ReadyState.CONNECTING
        ? t("xenith.logs.connecting")
        : t("xenith.logs.closed");

  return (
    <Blueprint style={{ padding: "18px 20px", display: "flex", flexDirection: "column", gap: 12 }}>
      <PanelHead title={t("xenith.events.title")} note={t("xenith.events.note")} trailing={<PanelNote>{status}</PanelNote>} />
      <LogLines logs={logs} empty={t("xenith.events.waiting")} />
    </Blueprint>
  );
};

export default Logs;
