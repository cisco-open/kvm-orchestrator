import client from "./client";
import type { Node, SshKey } from "../types/node";

export interface NodePool {
  name: string;
  type: string;
  auth: { username: string; secret_uuid: string } | null;
  hosts: Array<{ name: string; port: string }>;
}

export interface NodeNetwork {
  name: string;
  type: "ovs" | "linux" | "network";
}

export interface NodeIso {
  name: string;
  path: string;
  pool: string;
}

export interface NodeResources {
  pools: NodePool[];
  networks: NodeNetwork[];
  isos: NodeIso[];
  emulator: string;
  disk_xml_template: string;
}

export interface BrowseEntry {
  name: string;
  is_dir: boolean;
  size: number;
}

export interface BrowseResult {
  path: string;
  entries: BrowseEntry[];
  error?: string;
}

export async function listNodes(): Promise<Node[]> {
  const response = await client.get<{ nodes: Node[] }>("/nodes");
  return response.data.nodes;
}

export async function addNode(data: {
  hostname: string;
  ip_address: string;
  ssh_user: string;
  ssh_key_id: number;
  ssh_host?: string;
  ssh_port?: number;
  tenant_ids?: number[];
}): Promise<Node> {
  const response = await client.post<{ node: Node }>("/nodes", data);
  return response.data.node;
}

export async function deleteNode(id: number): Promise<void> {
  await client.delete(`/nodes/${id}`);
}

// v0.18 — admin-only. Replace a node's tenant membership set atomically.
// `[]` means "admin-only visible" (no tenants tagged, not even DEFAULT).
export async function updateNodeTenants(
  nodeId: number,
  tenantIds: number[],
): Promise<Node> {
  const response = await client.put<{ node: Node }>(
    `/nodes/${nodeId}/tenants`,
    { tenant_ids: tenantIds },
  );
  return response.data.node;
}

// v0.25 — admin-only. Cordon (schedulable=false) or uncordon a node.
// A cordoned node accepts no new VMs (placement + migrate UI exclude it);
// existing VMs stay running and the node remains online.
export async function setNodeSchedulable(
  nodeId: number,
  schedulable: boolean,
): Promise<Node> {
  const response = await client.post<{ node: Node }>(
    `/nodes/${nodeId}/schedulable`,
    { schedulable },
  );
  return response.data.node;
}

export async function listSshKeys(): Promise<SshKey[]> {
  const response = await client.get<{ ssh_keys: SshKey[] }>("/nodes/ssh-keys");
  return response.data.ssh_keys;
}

export async function addSshKey(name: string, privateKey: string): Promise<SshKey> {
  const response = await client.post<{ ssh_key: SshKey }>("/nodes/ssh-keys", {
    name,
    private_key: privateKey,
  });
  return response.data.ssh_key;
}

export async function getNodeResources(nodeId: number): Promise<NodeResources> {
  const response = await client.get<NodeResources>(`/nodes/${nodeId}/resources`);
  return response.data;
}

export async function browseNodePath(nodeId: number, path: string): Promise<BrowseResult> {
  const response = await client.get<BrowseResult>(`/nodes/${nodeId}/browse`, {
    params: { path },
  });
  return response.data;
}

export interface PoolVolume {
  name: string;
  path: string;
}

export interface PoolVolumesResult {
  pool: string;
  volumes: PoolVolume[];
  error?: string;
}

export async function listPoolVolumes(
  nodeId: number,
  pool: string,
): Promise<PoolVolumesResult> {
  const response = await client.get<PoolVolumesResult>(
    `/nodes/${nodeId}/pool-volumes`,
    { params: { pool } },
  );
  return response.data;
}

// ---------------------------------------------------------------------------
// Node CPU/RAM time-series
// ---------------------------------------------------------------------------

export interface NodeMetricSample {
  t: number;            // unix timestamp (seconds)
  cpu: number;          // 0 - 100
  ram_used_mb: number;
  ram_total_mb: number;
}

export interface NodeMetricsResult {
  samples: NodeMetricSample[];
  window: number;       // seconds
}

export async function getNodeMetrics(
  nodeId: number,
  windowSeconds = 3600,
): Promise<NodeMetricsResult> {
  const response = await client.get<NodeMetricsResult>(
    `/nodes/${nodeId}/metrics`,
    { params: { window: windowSeconds } },
  );
  return response.data;
}
