/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Copyright 2026 Cisco Systems, Inc. and its affiliates
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

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, X, Rocket, CalendarClock } from "lucide-react";
import { useState, useEffect, useMemo } from "react";
import { createPortal } from "react-dom";
import { listVMs, createVm, listVmImages, getLifecycleSettings, setLifecycleEnabled } from "../api/vms";
import type { CreateVmFields, VmImage, LifecycleSettings } from "../api/vms";
import { listNodes, getNodeResources, listPoolVolumes } from "../api/nodes";
import type { NodeResources } from "../api/nodes";
import { listNetworks } from "../api/networks";
import type { Network as CatalogNetwork } from "../api/networks";
import { listTenants } from "../api/tenants";
import type { Tenant } from "../api/tenants";
import { listMySshPubkeys } from "../api/users";
import type { SshPubkey } from "../api/users";
import type { VM } from "../types/vm";
import type { Node } from "../types/node";
import { useAuth } from "../hooks/useAuth";
import type { MeTenant } from "../api/auth";
import VmTable from "../components/VmTable";
import { SHARED_TENANT_ID, PERSONAL_TENANT_ID } from "../components/vmParts";

// EditModal / XmlModal moved to components/vmParts.tsx (v0.35.7); re-exported
// here so existing imports (VMDetail) keep working.
export { EditModal, XmlModal } from "../components/vmParts";


// ---------------------------------------------------------------------------
// Advanced Create VM modal  (UI label: "Advanced Create VM")
//
// The full/manual creation flow: blank / clone / existing-disk modes with
// explicit pool, disk, network and ISO selection. Historically this was the
// only "Create VM" path; as of the label swap the quick/golden-image flow
// (see "Create VM modal" below) is the primary "Create VM" button and this
// one is surfaced as "Advanced Create VM". Component + behaviour unchanged.
// ---------------------------------------------------------------------------

function CreateModal({
  nodes,
  vms,
  isAdmin,
  userTenants,
  onClose,
}: {
  nodes: Node[];
  vms: VM[];
  /** Drives the tenant-selector default and which options are visible. */
  isAdmin: boolean;
  /**
   * AD-mapped tenants the user is a member of (from `/api/auth/me`).
   * Excludes SHARED and PERSONAL — those are always in the list.
   */
  userTenants: { id: number; name: string }[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const onlineNodes = nodes.filter((n) => n.status === "online");
  // v0.18 — non-admins never pick a node; the server auto-places.
  // We still keep an internal selectedNodeId to drive the resources
  // and pool dropdowns (both are per-node lookups). Payload omits it
  // for non-admins so the backend runs placement.
  const [selectedNodeId, setSelectedNodeId] = useState<string>(
    onlineNodes[0]?.id != null ? String(onlineNodes[0].id) : ""
  );
  const [mode, setMode]               = useState<"blank" | "clone" | "existing">("blank");
  const [name, setName]               = useState("");
  const [vcpus, setVcpus]             = useState("2");
  const [ramGb, setRamGb]             = useState("4");
  const [vlan, setVlan]               = useState("");
  const [pool, setPool]               = useState("");
  const [diskGb, setDiskGb]           = useState("50");
  const [isoPool, setIsoPool]         = useState("");
  const [iso, setIso]                 = useState("");
  const [sourceVmId, setSourceVmId]   = useState<string>("");
  const [existingPool, setExistingPool] = useState("");
  const [existingDisk, setExistingDisk] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  // Tenant: default mirrors the server's chosen default for the caller's
  // role — untagged (admin) or PERSONAL (non-admin). v0.11.1: SHARED was
  // removed as a create-time choice for everyone; it's reserved for VMs
  // discovered by vm_sync. The user can always release a VM back to
  // SHARED post-creation via the Edit modal.
  const [tenantSel, setTenantSel] = useState<string>(
    isAdmin ? "" : String(PERSONAL_TENANT_ID),
  );

  // Tenants for the admin tenant-selector dropdown. Non-admins don't need
  // the full roster — they get SHARED, PERSONAL, and their own
  // memberships, all of which already live in props.
  const { data: allTenants } = useQuery<Tenant[]>({
    queryKey: ["tenants"],
    queryFn: listTenants,
    enabled: isAdmin,
  });

  const { data: resources, isLoading: resLoading } = useQuery<NodeResources>({
    queryKey: ["node-resources", selectedNodeId],
    queryFn: () => getNodeResources(Number(selectedNodeId)),
    enabled: !!selectedNodeId,
  });

  // v0.15.0 — networks catalog. Replaces the per-node SSH-discovered list
  // as the source of truth for network selection.
  const { data: catalogNetworks, isLoading: networksLoading } = useQuery<CatalogNetwork[]>({
    queryKey: ["networks"],
    queryFn: listNetworks,
  });

  // RBD images for the "existing" mode pool picker
  const { data: existingVolData, isLoading: existingVolLoading } = useQuery({
    queryKey: ["node-pool-volumes", selectedNodeId, existingPool],
    queryFn: () => listPoolVolumes(Number(selectedNodeId), existingPool),
    enabled: mode === "existing" && !!selectedNodeId && !!existingPool,
  });

  // Volumes for the ISO pool picker (blank mode)
  const { data: isoVolData, isLoading: isoVolLoading } = useQuery({
    queryKey: ["node-pool-volumes", selectedNodeId, isoPool],
    queryFn: () => listPoolVolumes(Number(selectedNodeId), isoPool),
    enabled: mode === "blank" && !!selectedNodeId && !!isoPool,
  });

  // Only Ceph RBD pools are valid disk targets — VM creation provisions
  // disks with raw `rbd` commands, so a libvirt `dir` pool (e.g. 'default'
  // or 'nvram') would fail with "rbd: error opening pool". Filter them out
  // of every pool selector so users can't pick one.
  const rbdPools = useMemo(
    () => (resources?.pools ?? []).filter((p) => p.type === "rbd"),
    [resources],
  );

  // Auto-set pool defaults when resources load. Don't overwrite values
  // the user has already set. Only offer RBD pools (see rbdPools above).
  useEffect(() => {
    const first = rbdPools[0];
    if (!first) return;
    if (!pool) setPool(first.name);
    if (!existingPool) setExistingPool(first.name);
  }, [rbdPools, pool, existingPool]);

  // Auto-pick a network once the catalog loads. Prefer is_default. Don't
  // overwrite a deliberate choice (clone mode pre-fills from source VM).
  useEffect(() => {
    if (!catalogNetworks || vlan) return;
    const def = catalogNetworks.find((n) => n.is_default) ?? catalogNetworks[0];
    if (def) setVlan(def.bridge_name);
  }, [catalogNetworks, vlan]);

  // Reset image selection when pool changes
  useEffect(() => { setExistingDisk(""); }, [existingPool]);
  useEffect(() => { setIso(""); }, [isoPool]);

  // v0.13.0: clone source may be on any node. Show every VM whose node
  // is currently online (we need libvirt access for `virsh dumpxml` on
  // the source), sorted by name for ergonomics on big inventories.
  const onlineNodeIds = useMemo(
    () => new Set(onlineNodes.map((n) => n.id)),
    [onlineNodes],
  );
  const cloneableVms = useMemo(
    () => vms
      .filter((v) => onlineNodeIds.has(v.node_id))
      .slice()
      .sort((a, b) => a.name.localeCompare(b.name)),
    [vms, onlineNodeIds],
  );
  const selectedSource = useMemo(
    () => (sourceVmId ? cloneableVms.find((v) => String(v.id) === sourceVmId) : undefined),
    [cloneableVms, sourceVmId],
  );

  // When a source VM is picked in clone mode, default the target node
  // to the source's node (per v0.13.0 spec — clone is done in situ then
  // migrated if the user changes target). Also pre-fill specs.
  useEffect(() => {
    if (mode !== "clone" || !sourceVmId) return;
    const src = vms.find((v) => String(v.id) === sourceVmId);
    if (!src) return;
    setSelectedNodeId(String(src.node_id));
    if (src.vcpus > 0) setVcpus(String(src.vcpus));
    if (src.ram_mb > 0) setRamGb(String(+(src.ram_mb / 1024).toFixed(2)));
    if (src.vlan) setVlan(src.vlan);
  }, [sourceVmId, mode, vms]);

  // Clear source picker when the user leaves clone mode. NOT when the
  // target node changes — they may be deliberately picking a different
  // host to migrate the clone to.
  useEffect(() => { setSourceVmId(""); }, [mode]);

  const createMutation = useMutation({
    mutationFn: (fields: CreateVmFields) => createVm(fields),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["vms"] });
      onClose();
    },
    onError: (err: unknown) => {
      const msg =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err as Error)?.message ?? "Create failed";
      setCreateError(msg);
    },
  });

  const handleSubmit = () => {
    setCreateError(null);
    if (!name.trim())      { setCreateError("VM name is required"); return; }
    // v0.18 — admins must still pick a node here (they see the dropdown).
    // Non-admins skip this check; the backend will place for them.
    if (isAdmin && !selectedNodeId) { setCreateError("Select a node"); return; }

    const fields: CreateVmFields = {
      name:    name.trim(),
      vcpus:   parseInt(vcpus) || 2,
      ram_gb:  parseFloat(ramGb) || 4,
      vlan:    vlan.trim(),
      mode,
    };

    // v0.18 — only admins send node_id. Non-admins get auto-placement
    // (see api/vms.py create_vm and services/placement.py).
    if (isAdmin && selectedNodeId) {
      fields.node_id = Number(selectedNodeId);
    }

    // Tenant selection — "" means "untagged" (admin-only); the server
    // rejects this for non-admins so we never let them pick it. Any
    // other value is a tenant id; the server re-validates membership.
    if (tenantSel === "") {
      fields.tenant_id = null;
    } else {
      fields.tenant_id = parseInt(tenantSel);
    }

    if (mode === "blank") {
      fields.pool    = pool.trim();
      fields.disk_gb = parseInt(diskGb) || 50;
      if (iso.trim()) fields.iso_path = iso.trim();
    } else if (mode === "clone") {
      if (!sourceVmId) { setCreateError("Select a source VM to clone"); return; }
      fields.source_vm_id = Number(sourceVmId);
    } else {
      if (!existingDisk) { setCreateError("Select an existing disk image"); return; }
      fields.existing_disk = existingDisk;
    }

    createMutation.mutate(fields);
  };

  const busy      = createMutation.isPending;
  const inputCls  = "w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 disabled:bg-gray-50";

  // Shared pool dropdown (blank + existing modes)
  const PoolSelect = ({ value, onChange }: { value: string; onChange: (v: string) => void }) => (
    resLoading ? (
      <select disabled className={inputCls}><option>Loading pools…</option></select>
    ) : rbdPools.length > 0 ? (
      <select value={value} onChange={(e) => onChange(e.target.value)} disabled={busy} className={inputCls}>
        {rbdPools.map((p) => (
          <option key={p.name} value={p.name}>{p.name}</option>
        ))}
      </select>
    ) : (
      <p className="text-xs text-amber-600 mt-1">No RBD storage pools found on this node.</p>
    )
  );

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-6 overflow-y-auto max-h-[90vh]">

        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-base font-semibold">Advanced Create VM</h2>
          <button onClick={onClose} disabled={busy} className="text-gray-400 hover:text-gray-600 disabled:opacity-40">
            <X size={18} />
          </button>
        </div>

        {createError && (
          <div className="mb-4 flex justify-between items-start rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
            <span>{createError}</span>
            <button onClick={() => setCreateError(null)} className="ml-3 shrink-0 text-red-400 hover:text-red-600">✕</button>
          </div>
        )}

        <div className="space-y-4">

          {/* Node — v0.18 admin-only. Non-admins get auto-placement:
              the backend runs placement.select_node() as a two-pass
              algorithm — try the caller's tenant first, then fall
              through to the DEFAULT placement pool (v0.20+). */}
          {isAdmin ? (
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Node</label>
              <select value={selectedNodeId} onChange={(e) => setSelectedNodeId(e.target.value)} disabled={busy} className={inputCls}>
                {onlineNodes.length === 0 && <option value="">No online nodes</option>}
                {onlineNodes.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.hostname}
                    {n.tenants.length > 0 && ` — ${n.tenants.map((t) => t.name).join(", ")}`}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <div className="rounded-md bg-blue-50 border border-blue-100 px-3 py-2 text-xs text-blue-700">
              Placement is automatic — the server picks the best-suited
              node in your tenant.
            </div>
          )}

          {/* Mode toggle — 3 options */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Mode</label>
            <div className="flex rounded-md border border-gray-300 overflow-hidden w-fit">
              {(["blank", "clone", "existing"] as const).map((m) => (
                <button
                  key={m} type="button" onClick={() => setMode(m)} disabled={busy}
                  className={`px-4 py-1.5 text-sm font-medium transition-colors disabled:opacity-40 border-r border-gray-300 last:border-r-0
                    ${mode === m ? "bg-blue-600 text-white" : "bg-white text-gray-600 hover:bg-gray-50"}`}
                >
                  {m === "blank" ? "Blank disk" : m === "clone" ? "Clone VM" : "Existing disk"}
                </button>
              ))}
            </div>
          </div>

          {/* Tenant — who can see/operate the new VM. Default mirrors the
              server-side default: untagged (admin) / PERSONAL (non-admin).
              v0.11.1: SHARED is reserved for KVM-discovered VMs and is
              filtered out of both branches; admins see all other tenants
              (+ untagged); non-admins see PERSONAL + their AD-mapped
              tenants. The user can release a VM back to SHARED later via
              the Edit modal. */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Tenant</label>
            <select
              value={tenantSel}
              onChange={(e) => setTenantSel(e.target.value)}
              disabled={busy}
              className={inputCls}
            >
              {isAdmin ? (
                <>
                  <option value="">(none) — untagged, admin-only</option>
                  {(allTenants ?? [])
                    .filter((t) => t.id !== SHARED_TENANT_ID)
                    .map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}{t.is_builtin ? " (built-in)" : ""}
                      </option>
                    ))}
                </>
              ) : (
                <>
                  <option value={String(PERSONAL_TENANT_ID)}>ODaaSE (built-in)</option>
                  {userTenants.map((t) => (
                    <option key={t.id} value={t.id}>{t.name}</option>
                  ))}
                </>
              )}
            </select>
            <p className="text-xs text-gray-400 mt-1">
              {isAdmin
                ? "Controls who can see and operate the new VM. SHARED is reserved for KVM-discovered VMs — use Edit to release one."
                : "ODaaSE hides this VM from other users (only you and admins see it); or scope it to one of your tenants for team visibility."}
            </p>
          </div>

          {/* VM Name */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">VM Name</label>
            <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="my-vm" disabled={busy} className={inputCls} />
          </div>

          {/* Clone: source VM (any host since v0.13.0 — clone is done
              on the source's node, then migrated to the chosen target
              node if different). */}
          {mode === "clone" && (
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Source VM</label>
              <select value={sourceVmId} onChange={(e) => setSourceVmId(e.target.value)} disabled={busy} className={inputCls}>
                <option value="">— select source VM —</option>
                {cloneableVms.map((v) => (
                  <option key={v.id} value={v.id}>{v.name} ({v.node_hostname})</option>
                ))}
              </select>
              {cloneableVms.length === 0 && (
                <p className="text-xs text-amber-600 mt-1">No VMs on online nodes to clone from.</p>
              )}
              {selectedSource && selectedSource.node_id !== Number(selectedNodeId) && (
                <p className="text-xs text-blue-600 mt-1">
                  Will clone on <b>{selectedSource.node_hostname}</b>, then migrate to{" "}
                  <b>{onlineNodes.find((n) => n.id === Number(selectedNodeId))?.hostname ?? "?"}</b>.
                </p>
              )}
            </div>
          )}

          {/* vCPUs + RAM */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">vCPUs</label>
              <input type="number" min="1" value={vcpus} onChange={(e) => setVcpus(e.target.value)} disabled={busy} className={inputCls} />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">RAM (GB)</label>
              <input type="number" min="0.5" step="0.5" value={ramGb} onChange={(e) => setRamGb(e.target.value)} disabled={busy} className={inputCls} />
            </div>
          </div>

          {/* Network — v0.15.0: strict dropdown over the admin-curated
              catalog. Non-admins only see networks an admin has marked
              user_visible. */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Network</label>
            {networksLoading ? (
              <select disabled className={inputCls}><option>Loading networks…</option></select>
            ) : (catalogNetworks ?? []).length === 0 ? (
              <select disabled className={inputCls}>
                <option>
                  {isAdmin
                    ? "No networks defined — add one in the Networks page"
                    : "No networks available — ask an admin"}
                </option>
              </select>
            ) : (
              <select value={vlan} onChange={(e) => setVlan(e.target.value)} disabled={busy} className={inputCls}>
                {catalogNetworks!.map((n) => (
                  <option key={n.id} value={n.bridge_name}>
                    {n.name}
                    {n.vlan_id != null ? ` — VLAN ${n.vlan_id}` : ""}
                    {n.is_default ? " ★" : ""}
                  </option>
                ))}
              </select>
            )}
          </div>

          {/* Blank mode: pool, disk size, ISO */}
          {mode === "blank" && (
            <>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Storage Pool</label>
                <PoolSelect value={pool} onChange={setPool} />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Disk Size (GB)</label>
                <input type="number" min="1" value={diskGb} onChange={(e) => setDiskGb(e.target.value)} disabled={busy} className={inputCls} />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">
                  ISO Pool <span className="font-normal text-gray-400">(optional)</span>
                </label>
                {resLoading && selectedNodeId ? (
                  <select disabled className={inputCls}><option>Loading pools…</option></select>
                ) : (
                  <select value={isoPool} onChange={(e) => setIsoPool(e.target.value)} disabled={busy} className={inputCls}>
                    <option value="">— no ISO —</option>
                    {(resources?.pools ?? []).map((p) => (
                      <option key={p.name} value={p.name}>{p.name}</option>
                    ))}
                  </select>
                )}
              </div>
              {isoPool && (
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">ISO File</label>
                  {isoVolLoading ? (
                    <select disabled className={inputCls}><option>Loading volumes…</option></select>
                  ) : (isoVolData?.volumes ?? []).length > 0 ? (
                    <select value={iso} onChange={(e) => setIso(e.target.value)} disabled={busy} className={inputCls}>
                      <option value="">— select ISO —</option>
                      {isoVolData!.volumes.map((v) => (
                        <option key={v.name} value={v.path}>{v.name}</option>
                      ))}
                    </select>
                  ) : (
                    <p className="text-xs text-amber-600 mt-1">No volumes found in pool {isoPool}.</p>
                  )}
                </div>
              )}
            </>
          )}

          {/* Existing disk mode: pool + image picker */}
          {mode === "existing" && (
            <>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Storage Pool</label>
                <PoolSelect value={existingPool} onChange={setExistingPool} />
              </div>
              {existingPool && (
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Disk Image</label>
                  {existingVolLoading ? (
                    <select disabled className={inputCls}><option>Loading volumes…</option></select>
                  ) : (existingVolData?.volumes ?? []).length > 0 ? (
                    <select
                      value={existingDisk}
                      onChange={(e) => setExistingDisk(e.target.value)}
                      disabled={busy}
                      className={inputCls}
                    >
                      <option value="">— select image —</option>
                      {existingVolData!.volumes.map((v) => (
                        <option key={v.name} value={`${existingPool}/${v.name}`}>{v.name}</option>
                      ))}
                    </select>
                  ) : (
                    <p className="text-xs text-amber-600">No images found in pool {existingPool}.</p>
                  )}
                </div>
              )}
            </>
          )}

        </div>

        <div className="flex justify-end gap-2 mt-6">
          <button onClick={onClose} disabled={busy} className="px-3 py-1.5 text-sm rounded-md bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-40">
            Cancel
          </button>
          <button onClick={handleSubmit} disabled={busy || onlineNodes.length === 0} className="px-3 py-1.5 text-sm rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40">
            {busy
              ? mode === "clone" ? "Cloning…" : "Creating…"
              : mode === "clone" ? "Clone VM" : mode === "existing" ? "Create VM" : "Create VM"}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}

// ---------------------------------------------------------------------------
// Create VM modal (v0.14.0) — golden-image catalog + T-shirt sizes
// (UI label: "Create VM" — the primary/recommended path)
// ---------------------------------------------------------------------------
//
// Internally still named QuickCreateModal (the "quick" golden-image flow);
// only the user-facing button/title label is "Create VM". Intentionally
// separate from the advanced modal so each can stay focused:
//   * CreateModal      — blank disk / clone / existing-image → "Advanced Create VM".
//   * QuickCreateModal — pick OS + size, paste SSH keys, go → "Create VM".
//
// Both buttons live in the page header. Server-side tenant rules apply
// identically (admin = any/none, non-admin = PERSONAL default; SHARED
// rejected on create).

function QuickCreateModal({
  nodes,
  isAdmin,
  userTenants,
  onClose,
}: {
  nodes: Node[];
  isAdmin: boolean;
  userTenants: MeTenant[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const { username } = useAuth();
  const onlineNodes = nodes.filter((n) => n.status === "online");
  const [selectedNodeId, setSelectedNodeId] = useState<string>(
    onlineNodes[0]?.id != null ? String(onlineNodes[0].id) : ""
  );
  const [name, setName]           = useState("");
  const [imageId, setImageId]     = useState("");
  const [size, setSize]           = useState<"S" | "M" | "L">("M");
  const [vlan, setVlan]           = useState("");
  const [sshKeys, setSshKeys]     = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  // Default tenant mirrors CreateModal: admins = untagged, non-admins = PERSONAL.
  const [tenantSel, setTenantSel] = useState<string>(
    isAdmin ? "" : String(PERSONAL_TENANT_ID),
  );

  // v0.14.2 — Linux credentials sourcing. Two modes:
  //   * "session"  — server uses the SHA-512 crypt hash of the user's
  //                  CEC password stashed in Redis at login. No extra
  //                  inputs shown; the cloud-init `users:` block uses
  //                  `default_user` = CEC username (set server-side).
  //   * "override" — user supplies a different (username, password)
  //                  pair. Useful when the session hash is gone (TTL
  //                  expired, controller restarted) or the user wants
  //                  a different login on this specific VM.
  //
  // If the server responds with 409 + `credentials_required: true`
  // (session hash missing) the onError handler flips us into "override"
  // automatically so the user just sees a credentials panel and a
  // single "Create" retry.
  const [credentialsMode, setCredentialsMode] =
    useState<"session" | "override">("session");
  const [linuxUser, setLinuxUser]         = useState("");
  const [linuxPassword, setLinuxPassword] = useState("");

  // Image catalog — driven by the new GET /api/vm-images endpoint.
  const { data: images, isLoading: imagesLoading } = useQuery<VmImage[]>({
    queryKey: ["vm-images"],
    queryFn: listVmImages,
  });

  // v0.15.0 — networks catalog. Replaces the per-node SSH-discovered list
  // as the source of truth for network selection. Backend filters to
  // user_visible=1 for non-admins, so the same call works for both.
  // The old getNodeResources() call was removed here — it ran a slow
  // SSH script per node selection and Quick Create no longer needs the
  // pool/disk discovery (size and image dictate disk_gb).
  const { data: catalogNetworks, isLoading: networksLoading } = useQuery<CatalogNetwork[]>({
    queryKey: ["networks"],
    queryFn: listNetworks,
  });

  // Tenants dropdown for admins (non-admins use props.userTenants).
  const { data: allTenants } = useQuery<Tenant[]>({
    queryKey: ["tenants"],
    queryFn: listTenants,
    enabled: isAdmin,
  });

  // v0.14.2 — caller's saved SSH pubkeys. Used to pre-fill the textarea
  // with the most-recently-used key so a returning user can hit Create
  // without re-pasting from `~/.ssh/id_*.pub` every time. New users get
  // an empty list and see the empty textarea exactly as before.
  const { data: savedPubkeys } = useQuery<SshPubkey[]>({
    queryKey: ["my-ssh-pubkeys"],
    queryFn: listMySshPubkeys,
    // Refetch lazily — keys rarely change within a session and a stale
    // pre-fill is harmless (the user can always paste over it).
    staleTime: 60_000,
  });

  // Default image once the catalog loads — first Linux entry is the
  // nicest default (Ubuntu 20.04 → 24.04 are the safe bets).
  useEffect(() => {
    if (!imageId && images && images.length > 0) {
      const linux = images.find((i) => i.os_family === "linux") ?? images[0];
      if (linux) setImageId(linux.id);
    }
  }, [images, imageId]);

  // Auto-pick a network once the catalog loads (don't overwrite a
  // deliberate choice). Prefer the is_default row, fall back to the
  // first entry.
  useEffect(() => {
    if (!catalogNetworks || vlan) return;
    const def = catalogNetworks.find((n) => n.is_default) ?? catalogNetworks[0];
    if (def) setVlan(def.bridge_name);
  }, [catalogNetworks, vlan]);

  // v0.14.2 — pre-fill the SSH key textarea with the most-recently-used
  // saved key on first render after the fetch resolves. We only touch
  // the textarea when it's still empty (don't clobber an in-progress
  // paste) and only on the initial load.
  useEffect(() => {
    const first = savedPubkeys?.[0]?.pubkey;
    if (!first) return;
    setSshKeys((prev) => (prev.trim() === "" ? first : prev));
    // Intentionally depend only on the resolved data — we don't want to
    // overwrite the textarea every time the user types.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedPubkeys]);

  const selectedImage = useMemo(
    () => (images ?? []).find((i) => i.id === imageId),
    [images, imageId],
  );
  const selectedSize = selectedImage?.sizes[size];

  // Group images by OS family for clean rendering — Linux first
  // (typically the most common ask), Windows below.
  const linuxImages   = useMemo(() => (images ?? []).filter((i) => i.os_family === "linux"), [images]);
  const windowsImages = useMemo(() => (images ?? []).filter((i) => i.os_family === "windows"), [images]);

  const createMutation = useMutation({
    mutationFn: (fields: CreateVmFields) => createVm(fields),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["vms"] });
      // The Quick Create side-effect persisted any new SSH keys against
      // the user — invalidate so the next open picks up additions.
      queryClient.invalidateQueries({ queryKey: ["my-ssh-pubkeys"] });
      onClose();
    },
    onError: (err: unknown) => {
      const resp = (err as { response?: { data?: {
        error?: string;
        credentials_required?: boolean;
      } } })?.response?.data;
      // v0.14.2 — 409 with credentials_required is the server saying
      // "I have no session password to use; show me an override". Flip
      // the panel open and surface a clear message rather than the raw
      // server error which is technical.
      if (resp?.credentials_required) {
        setCredentialsMode("override");
        setLinuxUser((u) => u || username || "");
        setCreateError(
          "Your session password isn't cached — enter a password for the new VM below.",
        );
        return;
      }
      const msg = resp?.error ?? (err as Error)?.message ?? "Create VM failed";
      setCreateError(msg);
    },
  });

  const handleSubmit = () => {
    setCreateError(null);
    if (!name.trim())          { setCreateError("VM name is required"); return; }
    // v0.18 — admins must still pick a node here (they see the dropdown).
    // Non-admins skip this check; the backend will place for them.
    if (isAdmin && !selectedNodeId) { setCreateError("Select a node"); return; }
    if (!selectedImage)        { setCreateError("Select an image"); return; }
    if (!selectedSize)         { setCreateError("Select a size"); return; }
    if (!vlan.trim())          { setCreateError("Pick a network"); return; }
    if (selectedImage.os_family === "linux" && !sshKeys.trim()) {
      setCreateError("Linux Create VM requires at least one SSH public key");
      return;
    }
    // v0.14.2 — when the user picked "use different credentials" we
    // need both a username and a password. The username defaults to
    // their CEC if they don't override it (the server would do the
    // same fallback, but doing it here keeps the API contract clean:
    // "explicit override means a complete (user, password) tuple").
    if (selectedImage.os_family === "linux" && credentialsMode === "override") {
      if (!linuxPassword) {
        setCreateError("Enter a password for the new VM");
        return;
      }
    }

    const fields: CreateVmFields = {
      name:     name.trim(),
      vcpus:    selectedSize.vcpus,
      ram_gb:   selectedSize.ram_mb / 1024,
      vlan:     vlan.trim(),
      mode:     "quick",
      image_id: selectedImage.id,
      disk_gb:  selectedSize.disk_gb,
    };
    // v0.18 — only admins pin the node explicitly. Non-admins are
    // auto-placed by services/placement.py.
    if (isAdmin && selectedNodeId) {
      fields.node_id = Number(selectedNodeId);
    }
    if (selectedImage.os_family === "linux") {
      fields.ssh_pub_keys = sshKeys.trim();
      if (credentialsMode === "override") {
        // The plaintext is hashed server-side and never echoed back.
        fields.linux_password = linuxPassword;
        const u = linuxUser.trim();
        if (u) fields.default_user = u;
      }
    }
    fields.tenant_id = tenantSel === "" ? null : parseInt(tenantSel);

    createMutation.mutate(fields);
  };

  const busy     = createMutation.isPending;
  const inputCls = "w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 disabled:bg-gray-50";

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-xl p-6 overflow-y-auto max-h-[90vh]">

        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-base font-semibold flex items-center gap-2">
            <Rocket size={16} className="text-blue-600" />
            Create VM
          </h2>
          <button onClick={onClose} disabled={busy} className="text-gray-400 hover:text-gray-600 disabled:opacity-40">
            <X size={18} />
          </button>
        </div>

        {createError && (
          <div className="mb-4 flex justify-between items-start rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
            <span>{createError}</span>
            <button onClick={() => setCreateError(null)} className="ml-3 shrink-0 text-red-400 hover:text-red-600">✕</button>
          </div>
        )}

        <div className="space-y-4">

          {/* Image picker — radio cards grouped by OS family */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-2">Operating System</label>
            {imagesLoading ? (
              <p className="text-xs text-gray-400">Loading catalog…</p>
            ) : (images ?? []).length === 0 ? (
              <p className="text-xs text-amber-600">No images available.</p>
            ) : (
              <div className="space-y-3">
                {linuxImages.length > 0 && (
                  <div>
                    <p className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Linux</p>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                      {linuxImages.map((img) => (
                        <button
                          key={img.id} type="button" disabled={busy}
                          onClick={() => setImageId(img.id)}
                          className={`text-left px-3 py-2 rounded-md border text-sm transition-colors disabled:opacity-40
                            ${imageId === img.id
                              ? "border-blue-500 bg-blue-50 text-blue-900"
                              : "border-gray-300 hover:border-blue-300"}`}
                        >
                          {img.name}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {windowsImages.length > 0 && (
                  <div>
                    <p className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Windows</p>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      {windowsImages.map((img) => (
                        <button
                          key={img.id} type="button" disabled={busy}
                          onClick={() => setImageId(img.id)}
                          className={`text-left px-3 py-2 rounded-md border text-sm transition-colors disabled:opacity-40
                            ${imageId === img.id
                              ? "border-blue-500 bg-blue-50 text-blue-900"
                              : "border-gray-300 hover:border-blue-300"}`}
                        >
                          {img.name}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Size picker — three cards with vCPU / RAM / disk per image */}
          {selectedImage && (
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-2">Size</label>
              <div className="grid grid-cols-3 gap-2">
                {(["S", "M", "L"] as const).map((s) => {
                  const sz = selectedImage.sizes[s];
                  if (!sz) return null;
                  return (
                    <button
                      key={s} type="button" disabled={busy}
                      onClick={() => setSize(s)}
                      className={`text-left px-3 py-2 rounded-md border transition-colors disabled:opacity-40
                        ${size === s
                          ? "border-blue-500 bg-blue-50"
                          : "border-gray-300 hover:border-blue-300"}`}
                    >
                      <div className={`text-sm font-semibold ${size === s ? "text-blue-900" : "text-gray-700"}`}>
                        {s === "S" ? "Small" : s === "M" ? "Medium" : "Large"}
                      </div>
                      <div className="text-[11px] text-gray-500 mt-0.5 leading-tight">
                        {sz.vcpus} vCPU · {(sz.ram_mb / 1024).toFixed(0)} GB RAM<br />
                        {sz.disk_gb} GB disk
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* Node — v0.18 admin-only. Non-admins get auto-placement. */}
          {isAdmin ? (
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Node</label>
              <select value={selectedNodeId} onChange={(e) => setSelectedNodeId(e.target.value)} disabled={busy} className={inputCls}>
                {onlineNodes.length === 0 && <option value="">No online nodes</option>}
                {onlineNodes.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.hostname}
                    {n.tenants.length > 0 && ` — ${n.tenants.map((t) => t.name).join(", ")}`}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <div className="rounded-md bg-blue-50 border border-blue-100 px-3 py-2 text-xs text-blue-700">
              Placement is automatic — the server picks the best-suited
              node in your tenant.
            </div>
          )}

          {/* Tenant — same rules as CreateModal */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Tenant</label>
            <select
              value={tenantSel}
              onChange={(e) => setTenantSel(e.target.value)}
              disabled={busy}
              className={inputCls}
            >
              {isAdmin ? (
                <>
                  <option value="">(none) — untagged, admin-only</option>
                  {(allTenants ?? [])
                    .filter((t) => t.id !== SHARED_TENANT_ID)
                    .map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}{t.is_builtin ? " (built-in)" : ""}
                      </option>
                    ))}
                </>
              ) : (
                <>
                  <option value={String(PERSONAL_TENANT_ID)}>ODaaSE (built-in)</option>
                  {userTenants.map((t) => (
                    <option key={t.id} value={t.id}>{t.name}</option>
                  ))}
                </>
              )}
            </select>
          </div>

          {/* VM Name */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">VM Name</label>
            <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="my-vm" disabled={busy} className={inputCls} />
          </div>

          {/* Network — v0.15.0: strict dropdown over the admin-curated
              catalog. Non-admins only see networks an admin has marked
              user_visible. */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Network</label>
            {networksLoading ? (
              <select disabled className={inputCls}><option>Loading networks…</option></select>
            ) : (catalogNetworks ?? []).length === 0 ? (
              <select disabled className={inputCls}>
                <option>
                  {isAdmin
                    ? "No networks defined — add one in the Networks page"
                    : "No networks available — ask an admin"}
                </option>
              </select>
            ) : (
              <select value={vlan} onChange={(e) => setVlan(e.target.value)} disabled={busy} className={inputCls}>
                {catalogNetworks!.map((n) => (
                  <option key={n.id} value={n.bridge_name}>
                    {n.name}
                    {n.vlan_id != null ? ` — VLAN ${n.vlan_id}` : ""}
                    {n.is_default ? " ★" : ""}
                  </option>
                ))}
              </select>
            )}
          </div>

          {/* SSH keys — Linux only. Windows golden images are pre-sysprepped
              and don't run cloud-init, so the textarea is hidden for them.
              v0.14.2: pre-filled from `user_ssh_pubkeys` on open (most-
              recently-used first). Saved as a side-effect of every
              successful Quick Create so the next open just works. */}
          {selectedImage?.os_family === "linux" && (
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">
                SSH Public Keys <span className="font-normal text-gray-400">(one per line)</span>
              </label>
              <textarea
                value={sshKeys}
                onChange={(e) => setSshKeys(e.target.value)}
                placeholder="ssh-ed25519 AAAA… you@laptop&#10;ssh-rsa  AAAA… you@desktop"
                rows={4}
                disabled={busy}
                className={`${inputCls} font-mono text-xs`}
              />
              <p className="text-xs text-gray-400 mt-1">
                {savedPubkeys && savedPubkeys.length > 0
                  ? "Pre-filled from your last Create VM — edit or paste to override."
                  : "Keys are saved against your account and pre-filled next time."}
              </p>
            </div>
          )}

          {/* v0.14.2 — Login credentials panel. Linux only. The default
              ("session") branch shows just a one-line summary; clicking
              "Use different credentials" expands the override inputs.
              Server hashes the password and never echoes/logs/persists
              the plaintext. */}
          {selectedImage?.os_family === "linux" && (
            <div className="rounded-md border border-gray-200 p-3">
              <label className="block text-xs font-medium text-gray-600 mb-2">
                Login credentials
              </label>
              <div className="flex flex-col gap-2 text-sm">
                <label className="inline-flex items-start gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="qc-credentials-mode"
                    className="mt-0.5"
                    checked={credentialsMode === "session"}
                    disabled={busy}
                    onChange={() => setCredentialsMode("session")}
                  />
                  <span>
                    <span className="font-medium">Use my directory credentials</span>
                    {username && (
                      <span className="text-gray-500"> ({username})</span>
                    )}
                    <span className="block text-xs text-gray-400 mt-0.5">
                      Same username and password you logged in with — works for both SSH and console.
                    </span>
                  </span>
                </label>
                <label className="inline-flex items-start gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="qc-credentials-mode"
                    className="mt-0.5"
                    checked={credentialsMode === "override"}
                    disabled={busy}
                    onChange={() => {
                      setCredentialsMode("override");
                      // Pre-fill the username with CEC so the common
                      // "same user, different password" case is one
                      // field shorter.
                      setLinuxUser((u) => u || username || "");
                    }}
                  />
                  <span>
                    <span className="font-medium">Use different credentials</span>
                    <span className="block text-xs text-gray-400 mt-0.5">
                      Pick a username and password just for this VM.
                    </span>
                  </span>
                </label>
              </div>

              {credentialsMode === "override" && (
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <div>
                    <label className="block text-[11px] font-medium text-gray-500 mb-1">
                      Username
                    </label>
                    <input
                      type="text"
                      value={linuxUser}
                      onChange={(e) => setLinuxUser(e.target.value)}
                      placeholder={username ?? "username"}
                      disabled={busy}
                      autoComplete="username"
                      className={inputCls}
                    />
                  </div>
                  <div>
                    <label className="block text-[11px] font-medium text-gray-500 mb-1">
                      Password
                    </label>
                    <input
                      type="password"
                      value={linuxPassword}
                      onChange={(e) => setLinuxPassword(e.target.value)}
                      placeholder="••••••••"
                      disabled={busy}
                      autoComplete="new-password"
                      className={inputCls}
                    />
                  </div>
                </div>
              )}
            </div>
          )}

          {selectedImage?.os_family === "windows" && (
            <div className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">
              Windows VMs boot from a pre-sysprepped golden image — no cloud-init.
              The default Administrator credentials are documented in the lab wiki.
              UEFI/OVMF firmware must be installed on the chosen node (<code>ovmf</code> or <code>edk2-ovmf</code> package).
            </div>
          )}

        </div>

        <div className="flex justify-end gap-2 mt-6">
          <button onClick={onClose} disabled={busy} className="px-3 py-1.5 text-sm rounded-md bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-40">
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={busy || onlineNodes.length === 0 || !selectedImage}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40"
          >
            {busy ? "Creating…" : <><Rocket size={14} /> Create</>}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function VMs() {
  const queryClient = useQueryClient();
  // `tenants` is the user's AD-mapped membership list from /api/auth/me
  // (excludes SHARED + PERSONAL — those are always available). Threaded into
  // CreateModal so non-admins can pick any tenant they belong to.
  const { isAdmin, tenants }                  = useAuth();
  const [actionError, setActionError]         = useState<string | null>(null);
  const [showCreate, setShowCreate]           = useState(false);
  const [showQuickCreate, setShowQuickCreate] = useState(false);

  // Same query (and cache entry) as the table; used for the Create modal's
  // name-collision check.
  const { data: vms } = useQuery<VM[]>({
    queryKey: ["vms"],
    queryFn: () => listVMs(),
    refetchInterval: 5_000,
  });
  const vmList = vms ?? [];

  const { data: nodes } = useQuery<Node[]>({
    queryKey: ["nodes"],
    queryFn: listNodes,
  });

  // Lifecycle checker runtime switch (admin toggle, no redeploy). Any user can
  // read it (drives the header badge); only admins can flip it.
  const { data: lifecycleSettings } = useQuery<LifecycleSettings>({
    queryKey: ["lifecycle-settings"],
    queryFn: getLifecycleSettings,
  });
  const lifecycleToggleMut = useMutation({
    mutationFn: (enabled: boolean) => setLifecycleEnabled(enabled),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["lifecycle-settings"] });
      queryClient.invalidateQueries({ queryKey: ["vms"] });
    },
    onError: (err: unknown) => {
      const msg =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err as Error)?.message ??
        "Failed to toggle lifecycle checker";
      setActionError(msg);
    },
  });

  return (
    <div className="p-6">
      {showCreate && (
        <CreateModal
          nodes={nodes ?? []}
          vms={vmList}
          isAdmin={isAdmin}
          userTenants={tenants}
          onClose={() => setShowCreate(false)}
        />
      )}
      {showQuickCreate && (
        <QuickCreateModal
          nodes={nodes ?? []}
          isAdmin={isAdmin}
          userTenants={tenants}
          onClose={() => setShowQuickCreate(false)}
        />
      )}
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold">Virtual Machines</h1>
        {/* v0.11.0: VM creation is available to everyone — non-admins land in
            PERSONAL by default; admins still default to SHARED. The server
            enforces tenant authorisation at /api/vms POST time.
            Button labels (label-only swap; underlying flows unchanged):
              * "Create VM"          → the quick / golden-image flow
                (setShowQuickCreate): pick OS + size + SSH keys and the server
                clones a golden RBD image with cloud-init injection. This is
                the primary, recommended path.
              * "Advanced Create VM" → the full/manual flow (setShowCreate):
                blank / clone / existing-disk modes with explicit pool, disk,
                network, ISO selection. */}
        <div className="flex items-center gap-2">
          {isAdmin && lifecycleSettings && (
            <button
              onClick={() => {
                const next = !lifecycleSettings.enabled;
                if (
                  window.confirm(
                    next
                      ? "Enable the 1-year VM lifecycle checker?\n\nOwners/tenants will start receiving expiry warnings, and expired VMs will be shut down (and flagged to Ops for deletion) per the TTL policy."
                      : "Disable the 1-year VM lifecycle checker?\n\nNo expiry warnings, auto-stops or deletion requests will be sent until re-enabled.",
                  )
                ) {
                  lifecycleToggleMut.mutate(next);
                }
              }}
              disabled={lifecycleToggleMut.isPending}
              title={
                lifecycleSettings.source === "runtime"
                  ? "Runtime override set by an admin"
                  : "Following the deployment default (LIFECYCLE_ENABLED)"
              }
              className={
                "inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md " +
                (lifecycleSettings.enabled
                  ? "bg-green-100 text-green-800 hover:bg-green-200"
                  : "bg-gray-100 text-gray-600 hover:bg-gray-200")
              }
            >
              <CalendarClock size={15} />
              Lifecycle: {lifecycleSettings.enabled ? "On" : "Off"}
            </button>
          )}
          <button
            onClick={() => setShowQuickCreate(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md bg-blue-600 text-white hover:bg-blue-700"
          >
            <Rocket size={15} />
            Create VM
          </button>
          <button
            onClick={() => setShowCreate(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md bg-gray-100 text-gray-700 hover:bg-gray-200"
          >
            <Plus size={15} />
            Advanced Create VM
          </button>
        </div>
      </div>

      <VmTable renewDeepLink actionError={actionError} setActionError={setActionError} />
    </div>
  );
}
