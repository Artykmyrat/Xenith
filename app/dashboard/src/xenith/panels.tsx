import { FC, ReactNode, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Blueprint } from "./Blueprint";
import { CoreLogLine, LEVEL_COLORS } from "./useCoreLogs";

/** Header row shared by the side panels: title, note, optional trailing slot. */
export const PanelHead: FC<{ title: string; note?: ReactNode; trailing?: ReactNode }> = ({
  title,
  note,
  trailing,
}) => (
  <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
    <h2 className="xn-panel-title" style={{ marginRight: note ? 0 : "auto" }}>
      {title}
    </h2>
    {note && (
      <span style={{ fontSize: 11.5, color: "var(--xn-neutral-600)", marginRight: "auto" }}>{note}</span>
    )}
    {trailing}
  </div>
);

export const PanelNote: FC<{ children: ReactNode }> = ({ children }) => (
  <span
    style={{
      fontSize: 11,
      letterSpacing: "0.08em",
      textTransform: "uppercase",
      color: "var(--xn-neutral-600)",
    }}
  >
    {children}
  </span>
);

/**
 * A two-line meter row: label plus trailing value, then a track with a caption.
 * Used by the node list and the top-consumption list.
 */
export const MeterRow: FC<{
  lead: ReactNode;
  label: ReactNode;
  value: ReactNode;
  percent: number;
  caption: ReactNode;
  captionWidth?: number;
  fill?: string;
}> = ({ lead, label, value, percent, caption, captionWidth = 80, fill = "var(--xn-accent)" }) => (
  <div
    style={{
      display: "flex",
      flexDirection: "column",
      gap: 6,
      padding: "10px 0",
      borderTop: "1px solid var(--xn-neutral-200)",
    }}
  >
    <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
      {lead}
      <span style={{ fontSize: 13, marginRight: "auto", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {label}
      </span>
      <span className="xn-mono" style={{ fontSize: 11.5, color: "var(--xn-accent-800)" }}>
        {value}
      </span>
    </div>
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div style={{ flex: 1, height: 5, background: "var(--xn-neutral-200)", position: "relative" }}>
        <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, background: fill, width: `${percent}%` }} />
      </div>
      <span
        className="xn-mono"
        style={{
          fontSize: 10.5,
          color: "var(--xn-neutral-600)",
          width: captionWidth,
          textAlign: "right",
          whiteSpace: "nowrap",
        }}
      >
        {caption}
      </span>
    </div>
  </div>
);

/** Placeholder used when a panel has nothing to show yet. */
export const PanelEmpty: FC<{ loading?: boolean; children?: ReactNode }> = ({ loading, children }) => {
  const { t } = useTranslation();
  return (
    <div style={{ padding: "18px 0", fontSize: 12.5, color: "var(--xn-neutral-600)" }}>
      {loading ? t("xenith.loading") : children || t("xenith.empty")}
    </div>
  );
};

/**
 * The core log as rows of time, level and message, scrolled to the newest line.
 * Shared by the Logs screen and the tail under the core configuration so both
 * read the same way.
 */
export const LogLines: FC<{ logs: CoreLogLine[]; maxHeight?: string; empty?: ReactNode }> = ({
  logs,
  maxHeight = "62vh",
  empty,
}) => {
  const boxRef = useRef<HTMLDivElement>(null);
  // Following the tail is only wanted while the reader is at the tail. Scrolling
  // up to read something is a decision the next line should not undo.
  const following = useRef(true);

  useEffect(() => {
    const box = boxRef.current;
    if (!box || !following.current) return;
    // Moving this box's own scrollTop rather than scrollIntoView, which scrolls
    // every scrollable ancestor it needs to: under the core configuration this
    // log sits below the editor, and each arriving line dragged the whole page
    // down with it, mid-edit.
    box.scrollTop = box.scrollHeight;
  }, [logs.length]);

  const onScroll = () => {
    const box = boxRef.current;
    if (!box) return;
    following.current = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
  };

  if (logs.length === 0) return <PanelEmpty>{empty}</PanelEmpty>;

  return (
    <div ref={boxRef} onScroll={onScroll} style={{ maxHeight, overflowY: "auto" }}>
      {logs.map((line) => (
        <div
          key={line.id}
          className="xn-mono"
          style={{
            display: "grid",
            gridTemplateColumns: "68px 92px 1fr",
            gap: 16,
            padding: "7px 0",
            borderTop: "1px solid var(--xn-neutral-200)",
            fontSize: 12,
          }}
        >
          <span style={{ color: "var(--xn-neutral-500)", whiteSpace: "nowrap" }}>{line.time}</span>
          <span style={{ letterSpacing: "0.06em", color: LEVEL_COLORS[line.level] || "var(--xn-accent-700)" }}>
            {line.level}
          </span>
          <span style={{ color: "var(--xn-neutral-800)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
            {line.text}
          </span>
        </div>
      ))}
    </div>
  );
};

/** A panel that only wraps children in the blueprint frame with standard padding. */
export const Panel: FC<{ children: ReactNode; padding?: string; gap?: number; className?: string }> = ({
  children,
  padding = "18px",
  gap = 14,
  className,
}) => (
  <Blueprint className={className} style={{ padding, display: "flex", flexDirection: "column", gap }}>
    {children}
  </Blueprint>
);
