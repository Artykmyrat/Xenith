import { useQuery } from "react-query";
import { fetch } from "service/http";
import { User } from "types/User";

export type SystemStats = {
  version: string;
  mem_total: number;
  mem_used: number;
  cpu_cores: number;
  cpu_usage: number;
  total_user: number;
  online_users: number;
  users_active: number;
  users_disabled: number;
  users_expired: number;
  users_limited: number;
  users_on_hold: number;
  incoming_bandwidth: number;
  outgoing_bandwidth: number;
  incoming_bandwidth_speed: number;
  outgoing_bandwidth_speed: number;
};

export type UsagePoint = { time: string; uplink: number; downlink: number };
export type UsageSeries = { period: UsagePeriod; granularity: "hour" | "day"; points: UsagePoint[] };
export type UsagePeriod = "24h" | "7d" | "30d";

export type CoreStats = { version: string; started: boolean; logs_websocket: string };

export type NodeSummary = {
  id: number;
  name: string;
  address: string;
  port: number;
  status: "connected" | "connecting" | "error" | "disabled";
  message?: string;
  xray_version?: string;
};

export type NodeUsage = { node_id: number | null; node_name: string; uplink: number; downlink: number };

export type Inbound = { tag: string; protocol: string; network: string; tls: string; port: number | string };

export type AdminProfile = { username: string; is_sudo: boolean };

const REFETCH_INTERVAL = 10_000;

export const useSystemStats = () =>
  useQuery<SystemStats>("xenith-system", () => fetch("/system"), { refetchInterval: REFETCH_INTERVAL });

export const useUsageSeries = (period: UsagePeriod) =>
  useQuery<UsageSeries>(["xenith-usage", period], () => fetch("/system/usage", { query: { period } }), {
    refetchInterval: 60_000,
  });

export const useCoreStats = () =>
  useQuery<CoreStats>("xenith-core", () => fetch("/core"), { refetchInterval: REFETCH_INTERVAL });

export const useNodes = () =>
  useQuery<NodeSummary[]>("xenith-nodes", () => fetch("/nodes"), { refetchInterval: REFETCH_INTERVAL });

/** Node usage over the last 24 hours. Sudo only, so failures are swallowed. */
export const useNodesUsage = () =>
  useQuery<{ usages: NodeUsage[] }>(
    "xenith-nodes-usage",
    () => {
      const end = new Date();
      const start = new Date(end.getTime() - 24 * 3600 * 1000);
      return fetch("/nodes/usage", { query: { start: start.toISOString(), end: end.toISOString() } });
    },
    { retry: false, refetchInterval: 60_000 },
  );

export const useInbounds = () =>
  useQuery<Record<string, Inbound[]>>("xenith-inbounds", () => fetch("/inbounds"));

/** Proxy hosts keyed by inbound tag. Sudo only. */
export const useHosts = () =>
  useQuery<Record<string, unknown[]>>("xenith-hosts", () => fetch("/hosts"), { retry: false });

export const useTopUsers = (limit = 5) =>
  useQuery<{ users: User[]; total: number }>("xenith-top-users", () =>
    fetch("/users", { query: { limit, sort: "-used_traffic" } }),
  );

export const useAdmin = () => useQuery<AdminProfile>("xenith-admin", () => fetch("/admin"));

export type Certificate = {
  name: string;
  domains: string[];
  expires_at: string | null;
  days_left: number | null;
  certificate_path: string | null;
  private_key_path: string | null;
};

export type CertificateList = {
  enabled: boolean;
  staging: boolean;
  certificates: Certificate[];
};

export type IssueCertificate = {
  domains: string[];
  email?: string;
  method: "standalone" | "webroot";
  webroot?: string;
};

/** Certificates certbot manages on the host. Sudo only. */
export const useCertificates = () =>
  useQuery<CertificateList>("xenith-certificates", () => fetch("/certificates"), { retry: false });

export const issueCertificate = (body: IssueCertificate) =>
  fetch("/certificates", { method: "POST", body });

export const renewCertificate = (name: string) =>
  fetch(`/certificates/${encodeURIComponent(name)}/renew`, { method: "POST" });

export const deleteCertificate = (name: string) =>
  fetch(`/certificates/${encodeURIComponent(name)}`, { method: "DELETE" });

export const restartCore = () => fetch("/core/restart", { method: "POST" });

/** Every inbound, flattened out of the by-protocol map the API returns. */
export const flattenInbounds = (grouped?: Record<string, Inbound[]>): (Inbound & { protocol: string })[] =>
  Object.entries(grouped || {}).flatMap(([protocol, inbounds]) =>
    (inbounds || []).map((inbound) => ({ ...inbound, protocol })),
  );
