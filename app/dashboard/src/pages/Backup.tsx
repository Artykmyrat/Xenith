import { useToast } from "@chakra-ui/react";
import dayjs from "dayjs";
import { Download, RotateCcw, Trash2, Upload } from "lucide-react";
import { ChangeEvent, FC, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "react-query";
import {
  BackupContents,
  BackupFile,
  backupDownloadURL,
  createBackup,
  deleteBackup,
  inspectBackup,
  restoreBackup,
  uploadBackup,
  useBackups,
} from "xenith/api";
import { Blueprint } from "xenith/Blueprint";
import { ConfirmDialog } from "xenith/ConfirmDialog";
import { Checkbox, IconButton } from "xenith/fields";
import { formatBytes } from "xenith/format";
import { NoticeBox, PanelEmpty, PanelHead, PanelNote } from "xenith/panels";

const describe = (err: any, fallback: string) => err?.response?._data?.detail || fallback;

const when = (value: string) => dayjs.utc(value).local().format("DD MMM YYYY, HH:mm");

/** The four things an archive can carry, in the order a restore applies them. */
const ITEMS = ["database", "env", "xray_config", "data"] as const;
type Item = (typeof ITEMS)[number];

/**
 * Backups: making one, keeping it somewhere else, and bringing one back.
 *
 * The import path is the reason this screen exists in the shape it does. A
 * Marzban backup carries the same four things ours does, so an uploaded
 * archive is read first and only then applied — and what it can apply here is
 * decided by the panel, not by the archive, which is what makes taking a file
 * off an old server safe to do from a browser.
 */
export const Backup: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();
  const { data, isLoading, isError, error } = useBackups();

  const [includeDatabase, setIncludeDatabase] = useState(true);
  const [includeEnv, setIncludeEnv] = useState(true);
  const [includeXray, setIncludeXray] = useState(true);
  const [includeData, setIncludeData] = useState(true);
  const [note, setNote] = useState("");

  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [removing, setRemoving] = useState<BackupFile | null>(null);
  const [contents, setContents] = useState<BackupContents | null>(null);
  const [chosen, setChosen] = useState<Item[]>([]);
  const uploadRef = useRef<HTMLInputElement>(null);

  const backups = data?.backups || [];
  const usable = (data?.enabled ?? false) && (data?.writable ?? false);

  const refresh = () => queryClient.invalidateQueries("xenith-backups");

  const good = (title: string) =>
    toast({ title, status: "success", position: "top", duration: 5000, isClosable: true });
  const bad = (err: any, fallback: string) =>
    toast({ title: describe(err, fallback), status: "error", position: "top", duration: 8000, isClosable: true });

  const onCreate = () => {
    setBusy(true);
    createBackup({
      include_database: includeDatabase,
      include_env: includeEnv,
      include_xray_config: includeXray,
      include_data: includeData,
      note,
    })
      .then(() => {
        good(t("xenith.backup.created"));
        setNote("");
        refresh();
      })
      .catch((err) => bad(err, t("xenith.backup.createFailed")))
      .finally(() => setBusy(false));
  };

  const onUpload = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    // Clearing the input is what lets the same file be picked twice running.
    event.target.value = "";
    if (!file) return;
    setUploading(true);
    uploadBackup(file)
      .then((found) => {
        good(t("xenith.backup.uploaded", { name: file.name }));
        refresh();
        openRestore(found);
      })
      .catch((err) => bad(err, t("xenith.backup.uploadFailed")))
      .finally(() => setUploading(false));
  };

  const openRestore = (found: BackupContents) => {
    setContents(found);
    // The database is the point of a restore; the rest is opt-in, because
    // putting back an .env changes the panel's own secrets and ports.
    setChosen(found.restorable.includes("database") ? ["database"] : []);
  };

  const onInspect = (backup: BackupFile) => {
    setBusy(true);
    inspectBackup(backup.name)
      .then(openRestore)
      .catch((err) => bad(err, t("xenith.backup.readFailed")))
      .finally(() => setBusy(false));
  };

  const onRestore = () => {
    if (!contents) return;
    setBusy(true);
    restoreBackup(contents.name, chosen)
      .then((report) => {
        good(report.detail);
        setContents(null);
        refresh();
      })
      .catch((err) => bad(err, t("xenith.backup.restoreFailed")))
      .finally(() => setBusy(false));
  };

  const onDelete = () => {
    if (!removing) return;
    setBusy(true);
    deleteBackup(removing.name)
      .then(() => {
        good(t("xenith.backup.deleted", { name: removing.name }));
        refresh();
      })
      .catch((err) => bad(err, t("xenith.backup.deleteFailed")))
      .finally(() => {
        setBusy(false);
        setRemoving(null);
      });
  };

  const toggle = (item: Item) =>
    setChosen((current) =>
      current.includes(item) ? current.filter((one) => one !== item) : [...current, item],
    );

  const facts: { label: string; value: string }[] = [
    {
      label: t("xenith.backup.database"),
      value: data?.database.target
        ? `${data.database.kind} · ${data.database.target}`
        : data?.database.kind || "—",
    },
    { label: t("xenith.backup.directory"), value: data?.paths.backups || "—" },
    {
      label: t("xenith.backup.schedule"),
      value: data?.schedule.interval_hours
        ? t("xenith.backup.scheduleOn", {
            hours: data.schedule.interval_hours,
            keep: data.schedule.keep,
          })
        : t("xenith.backup.scheduleOff"),
    },
  ];

  return (
    <>
      {!usable && !isLoading && (
        <NoticeBox>
          {isError
            ? describe(error, t("xenith.backup.unavailable"))
            : data?.reason || t("xenith.backup.unavailable")}
        </NoticeBox>
      )}
      {data?.database.reason && <NoticeBox>{data.database.reason}</NoticeBox>}

      <div className="xn-grid-split">
        <Blueprint style={{ padding: "18px 20px 20px", display: "flex", flexDirection: "column", gap: 14 }}>
          <PanelHead title={t("xenith.backup.newTitle")} note={t("xenith.backup.newNote")} />
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <Checkbox
              checked={includeDatabase}
              onChange={(event) => setIncludeDatabase(event.target.checked)}
              label={t("xenith.backup.itemDatabase")}
            />
            <Checkbox
              checked={includeEnv}
              onChange={(event) => setIncludeEnv(event.target.checked)}
              label={t("xenith.backup.itemEnv")}
            />
            <Checkbox
              checked={includeXray}
              onChange={(event) => setIncludeXray(event.target.checked)}
              label={t("xenith.backup.itemXray")}
            />
            <Checkbox
              checked={includeData}
              onChange={(event) => setIncludeData(event.target.checked)}
              label={t("xenith.backup.itemData")}
            />
          </div>
          <input
            className="xn-input"
            placeholder={t("xenith.backup.notePlaceholder")}
            maxLength={200}
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
          <button
            className="xn-btn xn-btn-primary"
            style={{ alignSelf: "flex-start" }}
            disabled={!usable || busy || !(includeDatabase || includeEnv || includeXray || includeData)}
            onClick={onCreate}
          >
            {busy ? t("xenith.backup.working") : t("xenith.backup.create")}
          </button>
        </Blueprint>

        <Blueprint style={{ padding: "18px 20px 20px", display: "flex", flexDirection: "column", gap: 14 }}>
          <PanelHead title={t("xenith.backup.importTitle")} note={t("xenith.backup.importNote")} />
          <p style={{ fontSize: 12.5, lineHeight: 1.55, color: "var(--xn-neutral-700)" }}>
            {t("xenith.backup.importBody")}
          </p>
          <span style={{ fontSize: 11.5, color: "var(--xn-neutral-600)" }}>
            {t("xenith.backup.importFormats")}
          </span>
          <input
            ref={uploadRef}
            type="file"
            accept=".tar.gz,.tgz,.tar,.zip,.sqlite3,.sqlite,.db,.sql"
            onChange={onUpload}
            style={{ display: "none" }}
          />
          <button
            className="xn-btn xn-btn-secondary"
            style={{ alignSelf: "flex-start" }}
            disabled={!usable || uploading}
            onClick={() => uploadRef.current?.click()}
          >
            <Upload size={15} strokeWidth={1.5} />
            {uploading ? t("xenith.backup.uploading") : t("xenith.backup.import")}
          </button>
        </Blueprint>
      </div>

      <Blueprint style={{ padding: "18px 20px 20px", display: "flex", flexDirection: "column", gap: 14 }}>
        <PanelHead
          title={t("xenith.backup.title")}
          note={t("xenith.backup.note", { total: backups.length, size: formatBytes(data?.total_bytes || 0) })}
          trailing={<PanelNote>{t("xenith.backup.keepElsewhere")}</PanelNote>}
        />

        <div className="xn-grid-cells" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
          {facts.map((fact) => (
            <div
              key={fact.label}
              className="xn-cell"
              style={{ padding: "16px 18px 14px", display: "flex", flexDirection: "column", gap: 6 }}
            >
              <span className="xn-label">{fact.label}</span>
              <span className="xn-mono" style={{ fontSize: 12.5, wordBreak: "break-all" }}>
                {fact.value}
              </span>
            </div>
          ))}
        </div>

        {backups.length === 0 ? (
          <PanelEmpty loading={isLoading}>{t("xenith.backup.empty")}</PanelEmpty>
        ) : (
          <div className="xn-scroll-x">
            <table className="xn-table" style={{ fontSize: 13 }}>
              <thead>
                <tr>
                  <th>{t("xenith.backup.colName")}</th>
                  <th>{t("xenith.backup.colKind")}</th>
                  <th style={{ textAlign: "right" }}>{t("xenith.backup.colSize")}</th>
                  <th>{t("xenith.backup.colCreated")}</th>
                  <th style={{ width: 110 }} />
                </tr>
              </thead>
              <tbody>
                {backups.map((backup) => (
                  <tr key={backup.name}>
                    <td className="xn-mono" style={{ fontSize: 12 }}>
                      {backup.name}
                      {backup.note && (
                        <div style={{ fontSize: 11, color: "var(--xn-neutral-600)" }}>{backup.note}</div>
                      )}
                    </td>
                    <td>
                      <span className={`xn-tag ${backup.kind === "manual" ? "xn-tag-accent" : "xn-tag-neutral"}`}>
                        {t(`xenith.backup.kind.${backup.kind}`, { defaultValue: backup.kind })}
                      </span>
                    </td>
                    <td className="xn-mono" style={{ textAlign: "right", fontSize: 12 }}>
                      {formatBytes(backup.size)}
                    </td>
                    <td className="xn-mono" style={{ fontSize: 12 }}>
                      {when(backup.created_at)}
                    </td>
                    <td>
                      <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
                        <a
                          className="xn-btn xn-btn-secondary"
                          style={{ width: 30, height: 30, padding: 0, flex: "none", justifyContent: "center" }}
                          href={backupDownloadURL(backup.name)}
                          title={t("xenith.backup.download")}
                          aria-label={t("xenith.backup.download")}
                          download
                        >
                          <Download size={15} strokeWidth={1.5} />
                        </a>
                        <IconButton
                          title={t("xenith.backup.restore")}
                          onClick={() => onInspect(backup)}
                          disabled={busy}
                        >
                          <RotateCcw size={15} strokeWidth={1.5} />
                        </IconButton>
                        <IconButton
                          title={t("xenith.backup.delete")}
                          onClick={() => setRemoving(backup)}
                          disabled={busy}
                          danger
                        >
                          <Trash2 size={15} strokeWidth={1.5} />
                        </IconButton>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Blueprint>

      <ConfirmDialog
        open={contents !== null}
        title={t("xenith.backup.restoreTitle")}
        confirmLabel={t("xenith.backup.restore")}
        busy={busy}
        danger
        confirmDisabled={chosen.length === 0}
        onConfirm={onRestore}
        onClose={() => setContents(null)}
        body={
          contents && (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <div className="xn-mono" style={{ fontSize: 11.5, color: "var(--xn-neutral-600)" }}>
                {contents.name} · {formatBytes(contents.size)} ·{" "}
                {t(`xenith.backup.source.${contents.source}`, { defaultValue: contents.source })}
              </div>

              {contents.warnings.map((warning) => (
                <NoticeBox key={warning}>{warning}</NoticeBox>
              ))}

              {contents.restorable.length > 0 && (
                <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
                  {ITEMS.filter((item) => contents.restorable.includes(item)).map((item) => (
                    <Checkbox
                      key={item}
                      checked={chosen.includes(item)}
                      onChange={() => toggle(item)}
                      label={
                        <span>
                          {t(`xenith.backup.item.${item}`)}
                          <span style={{ color: "var(--xn-neutral-600)" }}>
                            {item === "database" && ` · ${formatBytes(contents.database_bytes)}`}
                            {item === "data" &&
                              ` · ${t("xenith.backup.dataFiles", {
                                count: contents.data_files,
                                size: formatBytes(contents.data_bytes),
                              })}`}
                          </span>
                        </span>
                      }
                    />
                  ))}
                </div>
              )}

              <p style={{ fontSize: 12.5, lineHeight: 1.5 }}>{t("xenith.backup.restoreBody")}</p>
            </div>
          )
        }
      />

      <ConfirmDialog
        open={removing !== null}
        title={t("xenith.backup.deleteTitle")}
        body={t("xenith.backup.deleteBody", { name: removing?.name || "" })}
        confirmLabel={t("xenith.backup.delete")}
        busy={busy}
        danger
        onConfirm={onDelete}
        onClose={() => setRemoving(null)}
      />
    </>
  );
};

export default Backup;
