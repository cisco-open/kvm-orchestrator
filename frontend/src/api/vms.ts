/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Copyright [2026] [Cisco Systems, Inc. and its affiliates]
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import client from "./client";
import type { VM } from "../types/vm";

export async function listVMs(group?: string): Promise<VM[]> {
  const params = group ? { group } : {};
  const response = await client.get<{ vms: VM[] }>("/vms", { params });
  return response.data.vms;
}

export async function getVM(id: number): Promise<VM> {
  const response = await client.get<VM>(`/vms/${id}`);
  return response.data;
}

export async function vmAction(
  id: number,
  action: "start" | "stop" | "reboot" | "suspend" | "resume",
  opts: { force?: boolean } = {}
): Promise<void> {
  await client.post(`/vms/${id}/${action}`, opts);
}

export async function migrateVm(
  id: number,
  targetNodeId: number,
  allowShutdown = false,
): Promise<{ ok: boolean; vm: string; from_node: string; to_node: string; live: boolean }> {
  const response = await client.post(`/vms/${id}/migrate`, {
    target_node_id: targetNodeId,
    allow_shutdown: allowShutdown,
  });
  return response.data;
}

export interface EditVmFields {
  vcpus?: number;
  ram_mb?: number;
  disk_gb?: number;
  /** Admin only */
  name?: string;
  /** Admin only */
  description?: string;
  /** Admin only — bridge name e.g. "br100" */
  vlan?: string;
  /** Admin only — ordered list of boot devices e.g. ["hd", "network"] */
  boot_order?: string[];
  /**
   * Tenant assignment.
   *   1     = SHARED   (visible to everyone)
   *   3     = PERSONAL (visible only to owner_user_id; DB name "ODaaSE" since v0.21)
   *   null  = untagged (admin-only)
   *   other = a specific tenant (visible to members)
   *
   * Non-admins may only set this if they own the VM, and only to PERSONAL
   * or a tenant they are a member of. SHARED is reserved for KVM-discovered
   * VMs and can only be re-applied by an admin (v0.11.3).
   */
  tenant_id?: number | null;
  /** Admin only — reassign ownership. null clears the owner. */
  owner_user_id?: number | null;
}

export async function editVm(
  id: number,
  fields: EditVmFields,
): Promise<{ ok: boolean; vm: string; changes: string[] }> {
  const response = await client.put(`/vms/${id}`, fields);
  return response.data;
}

/**
 * v0.19.0: bulk-assign a single tenant to many VMs in one round-trip.
 * Admin-only on the server (PUT /api/vms/bulk-tenant). Pass `tenantId=null`
 * to clear the tag (VM becomes admin-only). Returns the number of rows
 * actually changed (unknown VM ids are silently ignored).
 */
export async function bulkAssignVmTenant(
  vmIds: number[],
  tenantId: number | null,
): Promise<{ ok: boolean; updated: number }> {
  const response = await client.put("/vms/bulk-tenant", {
    vm_ids: vmIds,
    tenant_id: tenantId,
  });
  return response.data;
}

export type VmPriority = "critical" | "priority" | "default";

export async function setVmPriority(
  vmId: number,
  priority: VmPriority,
): Promise<{ ok: boolean; id: number; priority: VmPriority }> {
  const response = await client.post(`/vms/${vmId}/priority`, { priority });
  return response.data;
}

export interface CreateVmFields {
  /**
   * Optional in v0.18. When omitted, the server runs the tenant-aware
   * placement algorithm and picks the best node for the caller's
   * tenant. Admins may still pin a specific node; non-admins never
   * get to pick — the frontend hides the node dropdown for them.
   */
  node_id?: number;
  name: string;
  vcpus: number;
  ram_gb: number;
  vlan: string;
  mode: "blank" | "clone" | "existing" | "quick";
  // blank mode
  pool?: string;
  disk_gb?: number;
  iso_path?: string;
  // clone mode
  source_vm_id?: number;
  // existing disk mode
  existing_disk?: string;   // "pool/image"
  // quick mode (v0.14.0)
  /** Catalog id (e.g. "ubuntu-2204"). Required when mode === "quick". */
  image_id?: string;
  /**
   * One or more OpenSSH public keys. Required for Linux Quick Create.
   * Accepts a single key string, a newline-separated list, or an array.
   * Windows ignores this (cloud-init doesn't run on Windows golden images).
   */
  ssh_pub_keys?: string | string[];
  /**
   * Linux user created by cloud-init. If omitted, the server defaults
   * to the caller's CEC username (or the image's `default_user` if
   * that lookup fails).
   */
  default_user?: string;
  /**
   * v0.14.2 — Linux only. Plaintext password for the cloud-init
   * `passwd:` field. **Only send this for the "use different
   * credentials" branch of the modal** — when absent, the server
   * uses the SHA-512 hash of the caller's CEC password stashed at
   * login (TTL = JWT access-token TTL).
   *
   * The server hashes this server-side; it is never echoed back,
   * logged, or persisted to disk.
   *
   * If neither this nor the session hash is available the create
   * call returns HTTP 409 with `credentials_required: true`; the
   * UI then auto-opens the credentials override panel and the user
   * re-submits with this field populated.
   */
  linux_password?: string;
  /**
   * Initial tenant assignment. See `EditVmFields.tenant_id` for the
   * full taxonomy. If omitted, the server picks the safest default
   * for the caller's role: SHARED for admins, PERSONAL for everyone
   * else.
   *
   * Non-admins may only pass SHARED, PERSONAL, or a tenant they are
   * a member of; the server re-validates.
   */
  tenant_id?: number | null;
}

export async function createVm(
  fields: CreateVmFields,
): Promise<{ ok: boolean; id: number; name: string; uuid: string }> {
  const response = await client.post("/vms", fields);
  return response.data;
}

// ---------------------------------------------------------------------------
// VM image catalog (Quick Create) — v0.14.0
// ---------------------------------------------------------------------------

export interface VmImageSize {
  vcpus: number;
  ram_mb: number;
  disk_gb: number;
}

export interface VmImage {
  /** Stable id, e.g. "ubuntu-2204". */
  id: string;
  /** Human-readable label for the modal dropdown. */
  name: string;
  os_family: "linux" | "windows";
  /** Ceph RBD pool on the node (typically "libvirt-pool"). */
  pool: string;
  /** RBD image name to clone from. */
  image: string;
  /**
   * Default Linux user created by cloud-init. The server overrides this
   * with the caller's CEC username when not supplied on the request.
   */
  default_user: string;
  /** T-shirt sizes — keyed by "S" / "M" / "L". */
  sizes: Record<string, VmImageSize>;
}

export async function listVmImages(): Promise<VmImage[]> {
  const response = await client.get<{ images: VmImage[] }>("/vm-images");
  return response.data.images;
}

export async function requestConsole(
  id: number,
  type: "vnc" | "serial"
): Promise<{ token: string; wsUrl: string }> {
  const response = await client.post(`/vms/${id}/console`, { type });
  return response.data;
}

// ---------------------------------------------------------------------------
// Disk management (admin only)
// ---------------------------------------------------------------------------

export interface VmDisk {
  target: string;    // e.g. "vda"
  source: string;    // pool/image or file path
  size_gb: number;
}

export interface VmCdrom {
  dev: string;       // e.g. "sda"
  iso: string;       // currently loaded ISO path, or ""
}

export interface VmDiskInfo {
  disks: VmDisk[];
  cdrom: VmCdrom | null;
}

export async function listVmDisks(id: number): Promise<VmDiskInfo> {
  const response = await client.get<VmDiskInfo>(`/vms/${id}/disks`);
  return response.data;
}

export async function attachVmDisk(
  id: number,
  pool: string,
  name: string,
  sizeGb: number,
): Promise<{ ok: boolean; target: string }> {
  const response = await client.post(`/vms/${id}/disks`, {
    pool,
    name,
    size_gb: sizeGb,
  });
  return response.data;
}

export async function detachVmDisk(
  id: number,
  target: string,
): Promise<{ ok: boolean }> {
  const response = await client.delete(`/vms/${id}/disks/${target}`);
  return response.data;
}

export async function setVmCdrom(
  id: number,
  isoPath: string,
): Promise<{ ok: boolean }> {
  const response = await client.put(`/vms/${id}/cdrom`, { iso_path: isoPath });
  return response.data;
}

export async function deleteVm(
  id: number,
  deleteDisk = true,
): Promise<{ ok: boolean; vm: string }> {
  const response = await client.delete(`/vms/${id}`, {
    data: { delete_disk: deleteDisk },
  });
  return response.data;
}

// ---------------------------------------------------------------------------
// VM lifecycle / 1-year TTL — v0.30
// ---------------------------------------------------------------------------
export async function renewVm(
  id: number,
): Promise<{ ok: boolean; expires_at?: string; vm_name?: string; restarted?: boolean }> {
  const response = await client.post(`/vms/${id}/renew`, {});
  return response.data;
}

export async function setVmExempt(
  id: number,
  exempt: boolean,
): Promise<{ ok: boolean; exempt: boolean; vm_name?: string }> {
  const response = await client.post(`/vms/${id}/exempt`, { exempt });
  return response.data;
}

export interface LifecycleSettings {
  enabled: boolean;
  source: "runtime" | "env_default";
  env_default?: boolean;
}

export async function getLifecycleSettings(): Promise<LifecycleSettings> {
  const response = await client.get<LifecycleSettings>("/vms/lifecycle/settings");
  return response.data;
}

export async function setLifecycleEnabled(
  enabled: boolean,
): Promise<{ ok: boolean; enabled: boolean; source: string }> {
  const response = await client.post("/vms/lifecycle/settings", { enabled });
  return response.data;
}

// ---------------------------------------------------------------------------
// Raw libvirt XML (admin only) — v0.22.3
// ---------------------------------------------------------------------------
export async function getVmXml(id: number): Promise<string> {
  const response = await client.get<{ xml: string }>(`/vms/${id}/xml`);
  return response.data.xml;
}

export async function setVmXml(
  id: number,
  xml: string,
): Promise<{ ok: boolean; message?: string }> {
  const response = await client.put(`/vms/${id}/xml`, { xml });
  return response.data;
}
