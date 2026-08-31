import { CSSProperties, FC, ReactNode, useEffect, useRef } from "react";

type DialogProps = {
  open: boolean;
  onClose: () => void;
  /** Ignores Escape and clicks outside while something is in flight. */
  locked?: boolean;
  /** Id of the heading that names the dialog. */
  labelledBy?: string;
  /** Id of the text that describes it, where there is one. */
  describedBy?: string;
  /** Applied to the panel, not to the shell around it. */
  style?: CSSProperties;
  children: ReactNode;
};

/**
 * How many modals are open, so the last one to close is the one that gives the
 * page its scrolling back.
 */
let openDialogs = 0;

/**
 * The shell every modal in the design system is drawn in.
 *
 * It is a real <dialog> shown with showModal(), which is what keeps the focus
 * inside it, closes it on Escape and makes the rest of the page inert — none of
 * which a div over the page can do. The panel is a child of the element rather
 * than the element itself, so a click that lands on the shell is a click on the
 * backdrop and nothing else.
 */
export const Dialog: FC<DialogProps> = ({
  open,
  onClose,
  locked = false,
  labelledBy,
  describedBy,
  style,
  children,
}) => {
  const shell = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = shell.current;
    if (!dialog || !open) return;

    // Where the focus came from, for the cases the platform cannot restore
    // itself — closing by unmounting rather than by the open prop.
    const opener = document.activeElement as HTMLElement | null;
    if (!dialog.open) dialog.showModal();
    openDialogs += 1;
    document.body.classList.add("xn-dialog-open");

    return () => {
      openDialogs -= 1;
      if (openDialogs === 0) document.body.classList.remove("xn-dialog-open");
      if (dialog.open) dialog.close();
      if (opener?.isConnected) opener.focus();
    };
  }, [open]);

  return (
    <dialog
      ref={shell}
      className="xn-dialog-shell"
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      // Escape reaches the element rather than React, so it is turned back into
      // the state change the caller is holding.
      onCancel={(event) => {
        event.preventDefault();
        if (!locked) onClose();
      }}
      onClick={(event) => {
        if (event.target === shell.current && !locked) onClose();
      }}
    >
      <div className="xn-dialog" style={style}>
        {children}
      </div>
    </dialog>
  );
};
