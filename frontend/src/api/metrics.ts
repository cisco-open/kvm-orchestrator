import client from "./client";

export interface ClusterCapacity {
  online_nodes: number;
  vcpus_total: number;
  vcpus_available: number;
  vcpus_used: number;
  ram_mb_total: number;
  ram_mb_available: number;
  ram_mb_used: number;
  disk_gb_total: number;
  disk_gb_available: number;
  disk_gb_used: number;
  vcpus_allocated: number;
  ram_mb_allocated: number;
  vcpu_overcommit: number;
}

export interface NodeUtil {
  id: number;
  hostname: string;
  status: string;
  cpu_model: string | null;
  vcpus_total: number;
  vcpus_available: number;
  vcpus_used?: number;
  ram_mb_total: number;
  ram_mb_available: number;
  disk_gb_total: number;
  disk_gb_available: number;
}

export interface MetricsSummary {
  cluster: ClusterCapacity;
  nodes: NodeUtil[];
  vms_total: number;
  vms_by_state: Record<string, number>;
  vms_by_os: Record<string, number>;
}

export async function getMetricsSummary(): Promise<MetricsSummary> {
  const res = await client.get<MetricsSummary>("/metrics/summary");
  return res.data;
}

export interface CreatedPoint {
  date: string;
  linux: number;
  windows: number;
  custom: number;
  unknown: number;
  total: number;
}

export interface VmsCreated {
  days: number;
  families: string[];
  series: CreatedPoint[];
}

export async function getVmsCreated(days = 90): Promise<VmsCreated> {
  const res = await client.get<VmsCreated>("/metrics/vms-created", { params: { days } });
  return res.data;
}
