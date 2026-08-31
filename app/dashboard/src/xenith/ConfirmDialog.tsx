import { FC, ReactNode, useId } from "react";
import { useTranslation } from "react-i18next";
import { Dialog } from "./Dialog";

type ConfirmDialogProps = {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel: string;
  busy?: boolean;
  danger?: boolean;
  /** Blocks confirmation while the dialog's own form is incomplete. */
  confirmDisabled?: boolean;
  onConfirm: () => void;
  onClose: () => void;
};

/** The design system's dialog: square, hairline, no shadow. */
export const ConfirmDialog: FC<ConfirmDialogProps> = ({
  open,
  title,
  body,
  confirmLabel,
  busy = false,
  danger = false,
  confirmDisabled = false,
  onConfirm,
  onClose,
}) => {
  const { t } = useTranslation();
  const titleId = useId();
  const bodyId = useId();

  if (!open) return null;

  return (
    <Dialog open onClose={onClose} locked={busy} labelledBy={titleId} describedBy={bodyId}>
      <h3 id={titleId} className="xn-heading" style={{ fontSize: 20, lineHeight: 1.1 }}>
        {title}
      </h3>
      <div id={bodyId} style={{ fontSize: 13.5, lineHeight: 1.5, color: "var(--xn-neutral-700)" }}>
        {body}
      </div>
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 6 }}>
        <button className="xn-btn xn-btn-secondary" onClick={onClose} disabled={busy}>
          {t("cancel")}
        </button>
        <button
          className={`xn-btn ${danger ? "xn-btn-danger" : "xn-btn-primary"}`}
          onClick={onConfirm}
          disabled={busy || confirmDisabled}
        >
          {busy ? t("xenith.working") : confirmLabel}
        </button>
      </div>
    </Dialog>
  );
};
