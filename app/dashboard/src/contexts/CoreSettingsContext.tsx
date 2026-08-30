import { fetchInbounds } from "@/contexts/DashboardContext";
import { apiErrorMessage } from "service/error";
import { fetch } from "service/http";
import { create } from "zustand";

type CoreSettingsStore = {
  isLoading: boolean;
  isPostLoading: boolean;
  /** What went wrong reading the core settings, when something did. */
  error: string | null;
  fetchCoreSettings: () => void;
  updateConfig: (json: string) => Promise<void>;
  restartCore: () => Promise<void>;
  version: string | null;
  started: boolean | null;
  logs_websocket: string | null;
  config: string;
};

export const useCoreSettings = create<CoreSettingsStore>((set) => ({
  isLoading: true,
  isPostLoading: false,
  error: null,
  version: null,
  started: false,
  logs_websocket: null,
  config: "",
  fetchCoreSettings: () => {
    set({ isLoading: true, error: null });
    Promise.all([
      fetch("/core").then(({ version, started, logs_websocket }) => set({ version, started, logs_websocket })),
      fetch("/core/config").then((config) => set({ config })),
    ])
      // Without this the screen showed an empty editor over a rejection nobody
      // handled, which reads as a core with no configuration at all.
      .catch((error) => set({ error: apiErrorMessage(error) ?? "" }))
      .finally(() => set({ isLoading: false }));
  },
  updateConfig: (body) => {
    set({ isPostLoading: true });
    return fetch("/core/config", { method: "PUT", body })
      // The inbounds are refreshed as a courtesy; `fetchInbounds` swallows its
      // own failures, and the save itself is what the caller is waiting on.
      .then(() => fetchInbounds())
      .finally(() => {
        set({ isPostLoading: false });
      });
  },
  restartCore: () => {
    return fetch("/core/restart", { method: "POST" });
  },
}));
