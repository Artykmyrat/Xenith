import debounce from "lodash.debounce";
import { apiErrorMessage } from "service/error";
import { fetch } from "service/http";
import { navigateTo } from "service/navigation";
import { User, UserCreate, UserDevices } from "types/User";
import { queryClient } from "utils/react-query";
import { getUsersPerPageLimitSize } from "utils/userPreferenceStorage";
import { create } from "zustand";
import { subscribeWithSelector } from "zustand/middleware";

export const StatisticsQueryKey = "statistics-query-key";

export type FilterType = {
  limit?: number;
  offset?: number;
  search?: string;
  sort: string;
  status?: "active" | "disabled" | "limited" | "expired" | "on_hold";
};
export type ProtocolType = "vmess" | "vless" | "trojan" | "shadowsocks";

export type FilterUsageType = {
  start?: string;
  end?: string;
};

export type InboundType = {
  tag: string;
  protocol: ProtocolType;
  network: string;
  tls: string;
  port?: number;
};
export type Inbounds = Map<ProtocolType, InboundType[]>;

type DashboardStateType = {
  isCreatingNewUser: boolean;
  editingUser: User | null | undefined;
  deletingUser: User | null;
  version: string | null;
  users: {
    users: User[];
    total: number;
  };
  inbounds: Inbounds;
  loading: boolean;
  /**
   * null while the list is fine. A string when the last fetch failed: what the
   * server said, or an empty string when it said nothing worth printing, which
   * the screen turns into its own wording.
   */
  usersError: string | null;
  filters: FilterType;
  subscribeUrl: string | null;
  QRcodeLinks: string[] | null;
  isEditingHosts: boolean;
  isEditingNodes: boolean;
  isShowingNodesUsage: boolean;
  isResetingAllUsage: boolean;
  resetUsageUser: User | null;
  revokeSubscriptionUser: User | null;
  devicesUser: User | null;
  onCreateUser: (isOpen: boolean) => void;
  onEditingUser: (user: User | null) => void;
  onDeletingUser: (user: User | null) => void;
  onResetAllUsage: (isResetingAllUsage: boolean) => void;
  refetchUsers: () => void;
  resetAllUsage: () => Promise<void>;
  onFilterChange: (filters: Partial<FilterType>, pushState?: boolean) => void;
  deleteUser: (user: User) => Promise<void>;
  createUser: (user: UserCreate) => Promise<void>;
  editUser: (user: UserCreate) => Promise<void>;
  fetchUserUsage: (user: User, query: FilterUsageType) => Promise<void>;
  setQRCode: (links: string[] | null) => void;
  setSubLink: (subscribeURL: string | null) => void;
  onEditingHosts: (isEditingHosts: boolean) => void;
  onEditingNodes: (isEditingHosts: boolean) => void;
  onShowingNodesUsage: (isShowingNodesUsage: boolean) => void;
  resetDataUsage: (user: User) => Promise<void>;
  revokeSubscription: (user: User) => Promise<void>;
  fetchUserDevices: (user: User) => Promise<UserDevices>;
  removeUserDevice: (user: User, deviceId: number) => Promise<UserDevices>;
  resetUserDevices: (user: User) => Promise<UserDevices>;
};

/** The filters worth sending, copied out rather than pruned in place. */
const usersQuery = (filters: FilterType): Record<string, string | number> => {
  const query: Record<string, string | number> = {};
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== null && value !== "") query[key] = value as string | number;
  }
  return query;
};

/**
 * The request in flight, so a later one can drop it. Filters change faster
 * than the server answers, and without this an early answer that arrives late
 * lands on top of the list the reader is actually looking at.
 */
let usersRequest: { id: number; controller: AbortController } | null = null;

const fetchUsers = (filters: FilterType): Promise<void> => {
  usersRequest?.controller.abort();
  const request = { id: (usersRequest?.id ?? 0) + 1, controller: new AbortController() };
  usersRequest = request;

  useDashboard.setState({ loading: true, usersError: null });
  return fetch("/users", { query: usersQuery(filters), signal: request.controller.signal })
    .then((users) => {
      if (usersRequest !== request) return;
      useDashboard.setState({ users, loading: false, usersError: null });
    })
    .catch((error) => {
      // A request dropped in favour of a newer one is not a failure, and the
      // newer one owns the loading flag from here on.
      if (usersRequest !== request) return;
      useDashboard.setState({ loading: false, usersError: apiErrorMessage(error) ?? "" });
    });
};

/**
 * The inbounds the user dialog offers. Best effort: the list is a convenience
 * next to the user list, so a refusal leaves what was there and is not allowed
 * to reject into nothing. It does not own `loading` — that belongs to the user
 * list, and clearing it here used to end the list's loading state early.
 */
export const fetchInbounds = (): Promise<void> => {
  return fetch("/inbounds")
    .then((inbounds: Inbounds) => {
      useDashboard.setState({
        inbounds: new Map(Object.entries(inbounds)) as Inbounds,
      });
    })
    .catch(() => undefined);
};

const serializeFilters = (f: Partial<FilterType>) => {
  const filters = { ...f };
  delete filters.limit;
  if (filters.sort === "-created_at") delete filters.sort;

  const parsedFilters = Object.keys(filters).reduce(
    (acc, key) => {
      const value = filters[key as keyof FilterType];
      if (value) {
        acc[key] = String(value);
      }
      return acc;
    },
    {} as Record<string, string>,
  );

  // The user list lives at /users now, so filters serialise onto that route.
  navigateTo(`/users?${new URLSearchParams(parsedFilters).toString()}`, { replace: false });
};

export const useDashboard = create(
  subscribeWithSelector<DashboardStateType>((set, get) => ({
    version: null,
    editingUser: null,
    deletingUser: null,
    isCreatingNewUser: false,
    QRcodeLinks: null,
    subscribeUrl: null,
    users: {
      users: [],
      total: 0,
    },
    loading: true,
    usersError: null,
    isResetingAllUsage: false,
    isEditingHosts: false,
    isEditingNodes: false,
    isShowingNodesUsage: false,
    resetUsageUser: null,
    revokeSubscriptionUser: null,
    devicesUser: null,
    filters: {
      limit: getUsersPerPageLimitSize(),
      sort: "-created_at",
    },
    inbounds: new Map(),
    refetchUsers: () => {
      // Whatever was still in flight is dropped inside; only this answer counts.
      fetchUsers(get().filters);
    },
    resetAllUsage: () => {
      return fetch(`/users/reset`, { method: "POST" }).then(() => {
        get().onResetAllUsage(false);
        get().refetchUsers();
      });
    },
    onResetAllUsage: (isResetingAllUsage) => set({ isResetingAllUsage }),
    onCreateUser: (isCreatingNewUser) => set({ isCreatingNewUser }),
    onEditingUser: (editingUser) => {
      set({ editingUser });
    },
    onDeletingUser: (deletingUser) => {
      set({ deletingUser });
    },
    onFilterChange: (filters, pushState = true) => {
      const allFilters = {
        ...get().filters,
        ...filters,
      };
      set({
        filters: allFilters,
      });
      if (pushState) serializeFilters(allFilters);
      get().refetchUsers();
    },
    setQRCode: (QRcodeLinks) => {
      set({ QRcodeLinks });
    },
    deleteUser: (user: User) => {
      set({ editingUser: null });
      return fetch(`/user/${user.username}`, { method: "DELETE" }).then(() => {
        set({ deletingUser: null });
        get().refetchUsers();
        queryClient.invalidateQueries(StatisticsQueryKey);
      });
    },
    createUser: (body: UserCreate) => {
      return fetch(`/user`, { method: "POST", body }).then(() => {
        set({ editingUser: null });
        get().refetchUsers();
        queryClient.invalidateQueries(StatisticsQueryKey);
      });
    },
    editUser: (body: UserCreate) => {
      return fetch(`/user/${body.username}`, { method: "PUT", body }).then(() => {
        get().onEditingUser(null);
        get().refetchUsers();
      });
    },
    fetchUserUsage: (body: User, query: FilterUsageType) => {
      const active = Object.fromEntries(Object.entries(query).filter(([, value]) => !!value));
      return fetch(`/user/${body.username}/usage`, { method: "GET", query: active });
    },
    onEditingHosts: (isEditingHosts: boolean) => {
      set({ isEditingHosts });
    },
    onEditingNodes: (isEditingNodes: boolean) => {
      set({ isEditingNodes });
    },
    onShowingNodesUsage: (isShowingNodesUsage: boolean) => {
      set({ isShowingNodesUsage });
    },
    setSubLink: (subscribeUrl) => {
      set({ subscribeUrl });
    },
    resetDataUsage: (user) => {
      return fetch(`/user/${user.username}/reset`, { method: "POST" }).then(() => {
        set({ resetUsageUser: null });
        get().refetchUsers();
      });
    },
    revokeSubscription: (user) => {
      return fetch(`/user/${user.username}/revoke_sub`, {
        method: "POST",
      }).then((user) => {
        set({ revokeSubscriptionUser: null, editingUser: user });
        get().refetchUsers();
      });
    },
    // The three below all answer with the whole device list, so the modal
    // renders what the server has rather than guessing at the result of a
    // delete it just made.
    fetchUserDevices: (user) => {
      return fetch(`/user/${user.username}/devices`);
    },
    removeUserDevice: (user, deviceId) => {
      return fetch(`/user/${user.username}/devices/${deviceId}`, { method: "DELETE" });
    },
    resetUserDevices: (user) => {
      return fetch(`/user/${user.username}/devices`, { method: "DELETE" });
    },
  })),
);
