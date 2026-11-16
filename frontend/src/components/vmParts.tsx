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

// Shared VM UI pieces (badges, cell helpers, row ActionButtons and the
// per-VM modals). Moved verbatim out of pages/VMs.tsx in v0.35.7 so the VMs
// page and the node detail page render the exact same VM table.


// Built-in tenant ids — see controller/app/services/tenant_service.py
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, Square, RotateCcw, PauseCircle, PlayCircle, Zap, Terminal, ArrowRightLeft, Pencil, X, Trash2, FileCode, CalendarClock, ShieldCheck, ShieldOff } from "lucide-react";
import { useState, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { vmAction, listVmDisks, attachVmDisk, detachVmDisk, setVmCdrom, getVmXml, setVmXml } from "../api/vms";
import type { EditVmFields, VmDiskInfo } from "../api/vms";
import { getNodeResources, listPoolVolumes } from "../api/nodes";
import type { NodeResources } from "../api/nodes";
import { listNetworks } from "../api/networks";
import type { Network as CatalogNetwork } from "../api/networks";
import { listTenants } from "../api/tenants";
import type { Tenant } from "../api/tenants";
import { listUsers } from "../api/users";
import type { User } from "../types/user";
import type { VM } from "../types/vm";
import type { Node } from "../types/node";
import type { MeTenant } from "../api/auth";
export const SHARED_TENANT_ID   = 1;
// PERSONAL_TENANT_ID renumbered 2 → 3 in v0.21 (migration 0004 merged
// the old "Personal VM" id=2 into "ODaaSE" id=3, keeping the DB name
// "ODaaSE" and adopting Personal VM's owner-only visibility).
export const PERSONAL_TENANT_ID = 3;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function StateBadge({ state }: { state: VM["state"] }) {
  const styles: Record<string, string> = {
    running:   "bg-green-100 text-green-800",
    stopped:   "bg-gray-100 text-gray-600",
    paused:    "bg-yellow-100 text-yellow-800",
    crashed:   "bg-red-100 text-red-800",
    stopping:  "bg-orange-100 text-orange-800",
    suspended: "bg-blue-100 text-blue-800",
    migrating: "bg-amber-100 text-amber-800",
    unknown:   "bg-gray-100 text-gray-500",
  };
  return (
    <span className={`inline-flex px-2 py-0.5 text-xs font-medium rounded-full ${styles[state] ?? styles.unknown}`}>
      {state}
    </span>
  );
}

// HA priority class badge. critical > priority > default.
export function PriorityBadge({ priority }: { priority?: string }) {
  const p = priority ?? "default";
  const styles: Record<string, string> = {
    critical: "bg-red-100 text-red-800 ring-1 ring-red-300",
    priority: "bg-amber-100 text-amber-800",
    default:  "bg-gray-100 text-gray-500",
  };
  return (
    <span
      className={`inline-flex px-2 py-0.5 text-xs font-medium rounded-full ${styles[p] ?? styles.default}`}
      title={
        p === "critical"
          ? "Critical — restarted first; may evict non-critical VMs (default first, priority last resort)"
          : p === "priority"
          ? "Priority — restarted after critical; may evict default VMs"
          : "Default — restarted last; evicts nothing, evicted first"
      }
    >
      {p}
    </span>
  );
}


// Dedicated "Expires" column cell — ALWAYS shows the remaining days (not just
// within 30d like the State-column badge), so lifecycle is visible at a glance.
//   exempt            → grey "Exempt"
//   auto_stopped / pending_delete → red "Expired"
//   expires_at set    → "Nd left" (amber ≤30d, else grey), tooltip = exact date
//   no expires_at     → "—"
export function expiresCell(vm: VM): React.ReactNode {
  if (vm.exempt) {
    return (
      <span className="text-xs text-gray-500" title="Exempt from the 1-year TTL — no expiry enforcement">
        Exempt
      </span>
    );
  }
  if (vm.lifecycle_state === "pending_delete" || vm.lifecycle_state === "auto_stopped") {
    return (
      <span className="text-xs font-medium text-red-700" title="Expired — Renew to restore">
        Expired
      </span>
    );
  }
  if (vm.expires_at) {
    const expires = new Date(vm.expires_at);
    const days = Math.floor((expires.getTime() - Date.now()) / 86_400_000);
    const cls = days <= 30 ? "text-amber-700 font-medium" : "text-gray-600";
    return (
      <span className={`text-xs ${cls}`} title={`Expires ${expires.toLocaleString()}`}>
        {days}d left
      </span>
    );
  }
  return <span className="text-xs text-gray-400">—</span>;
}

export function vlanLabel(raw: string | null): string {
  if (!raw) return "—";
  const m = raw.match(/(?:br|vlan|bond\d+\.)(\d+)$/i);
  return m ? `VLAN ${m[1]}` : raw;
}

export function ramLabel(mb: number): string {
  if (mb === 0) return "—";
  return mb >= 1024 ? `${Math.round(mb / 1024)} GB` : `${mb} MB`;
}

// ---------------------------------------------------------------------------
// Migrate modal
// ---------------------------------------------------------------------------

export function MigrateModal({
  vm, nodes, isAdmin, onConfirm, onClose, busy,
}: {
  vm: VM;
  nodes: Node[];
  isAdmin: boolean;
  onConfirm: (targetNodeId: number, allowShutdown: boolean) => void;
  onClose: () => void;
  busy: boolean;
}) {
  // v0.20 — tenant awareness (two-pass placement semantics).
  //
  //   Non-admins may only migrate to nodes that share a tenant with the
  //   VM, or that are tagged with the DEFAULT placement pool (id=4).
  //   PERSONAL/ODaaSE (id=3) is an owner-only VM tenant since the v0.21
  //   merge and has no node_tenants rows, so its VMs are eligible for
  //   DEFAULT-tagged nodes only (the pass-2 fallback pool).
  //
  //   Admins see every other online node but with a "Same tenant" badge
  //   on the ones that match — helpful hint without being a blocker.
  const DEFAULT_POOL = 4;
  const shareTenantWithVm = (n: Node): boolean => {
    if (vm.tenant_id == null) return false;
    return n.tenants.some((t) => t.id === vm.tenant_id);
  };
  const onDefaultPool = (n: Node): boolean => n.tenants.some((t) => t.id === DEFAULT_POOL);

  const otherOnline = nodes.filter(
    (n) => n.id !== vm.node_id && n.status === "online" &&
           (n.schedulable === true || n.schedulable === 1),
  );
  const eligible = isAdmin
    ? otherOnline
    : otherOnline.filter((n) => shareTenantWithVm(n) || onDefaultPool(n));

  const [targetId, setTargetId] = useState<number | "">(eligible[0]?.id ?? "");
  const isRunning = vm.state === "running";

  const canLive = (node: Node): boolean => {
    // If either CPU is unknown, optimistically allow live migration attempt
    if (!vm.node_cpu_model || !node.cpu_model) return true;
    return vm.node_cpu_model === node.cpu_model;
  };

  const selectedNode = eligible.find((n) => n.id === targetId);
  const liveOk = selectedNode ? canLive(selectedNode) : true;
  const willShutdown = isRunning && !liveOk;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6">
        <h2 className="text-base font-semibold mb-1">Migrate VM</h2>
        <p className="text-sm text-gray-500 mb-4">
          <span className="font-medium text-gray-800">{vm.name}</span>
          {" — currently on "}
          <span className="font-medium text-gray-700">{vm.node_hostname}</span>
        </p>

        {eligible.length === 0 ? (
          <p className="text-sm text-red-600 mb-4">
            {isAdmin
              ? "No other online nodes available."
              : "No eligible nodes in this VM's tenant. Ask an admin to tag a target node with your tenant or with the DEFAULT placement pool."}
          </p>
        ) : (
          <div className="mb-4">
            <label className="block text-xs font-medium text-gray-600 mb-1">Target node</label>
            <div className="space-y-1 max-h-64 overflow-y-auto pr-1">
              {eligible.map((n) => {
                const live = canLive(n);
                const sameTenant = shareTenantWithVm(n);
                const shared = onDefaultPool(n);
                return (
                  <label
                    key={n.id}
                    className={`flex items-center gap-3 px-3 py-2 rounded-lg border cursor-pointer transition-colors
                      ${targetId === n.id
                        ? "border-blue-500 bg-blue-50"
                        : "border-gray-200 hover:border-gray-300 hover:bg-gray-50"}`}
                  >
                    <input
                      type="radio"
                      name="target_node"
                      value={n.id}
                      checked={targetId === n.id}
                      onChange={() => setTargetId(n.id)}
                      disabled={busy}
                      className="accent-blue-600"
                    />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-medium text-gray-800">{n.hostname}</span>
                        {sameTenant && (
                          <span className="text-xs px-1.5 py-0.5 rounded-full font-medium bg-blue-100 text-blue-700">
                            Same tenant
                          </span>
                        )}
                        {!sameTenant && shared && (
                          <span className="text-xs px-1.5 py-0.5 rounded-full font-medium bg-purple-100 text-purple-700">
                            DEFAULT pool
                          </span>
                        )}
                        {isRunning && (
                          <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${
                            live
                              ? "bg-green-100 text-green-700"
                              : "bg-amber-100 text-amber-700"
                          }`}>
                            {live ? "Live OK" : "Shutdown required"}
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-gray-400 truncate">
                        {n.ip_address}
                        {n.cpu_model && ` · ${n.cpu_model}`}
                      </div>
                    </div>
                  </label>
                );
              })}
            </div>
          </div>
        )}

        {/* Contextual hint */}
        {isRunning && selectedNode && (
          <p className={`text-xs mb-4 rounded-md px-3 py-2 ${
            liveOk
              ? "bg-green-50 text-green-700"
              : "bg-amber-50 text-amber-700"
          }`}>
            {liveOk
              ? "CPU models match — VM will stay running during migration."
              : `CPU models differ — the VM will be shut down, migrated offline, then remain stopped on the target node.`}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            disabled={busy}
            className="px-3 py-1.5 text-sm rounded-md bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-40"
          >
            Cancel
          </button>
          <button
            onClick={() => targetId !== "" && onConfirm(targetId, willShutdown)}
            disabled={busy || targetId === "" || eligible.length === 0}
            className={`px-3 py-1.5 text-sm rounded-md text-white disabled:opacity-40 ${
              willShutdown
                ? "bg-amber-500 hover:bg-amber-600"
                : "bg-blue-600 hover:bg-blue-700"
            }`}
          >
            {busy
              ? (willShutdown ? "Stopping & Migrating…" : "Migrating…")
              : (willShutdown ? "Shutdown & Migrate" : isRunning ? "Live Migrate" : "Migrate")}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Edit modal
// ---------------------------------------------------------------------------

export function EditModal({
  vm, isAdmin, userId, userTenants, onConfirm, onClose, busy, embedded = false,
}: {
  vm: VM;
  isAdmin: boolean;
  /** Signed-in user's id; used to surface owner-only controls. */
  userId: number | null;
  /**
   * AD-mapped tenants the signed-in user is a member of (from
   * `/api/auth/me`). Excludes SHARED + PERSONAL. v0.11.0: non-admin owners
   * may move their VM into any of these tenants in addition to
   * SHARED↔PERSONAL. The server re-validates membership.
   */
  userTenants: MeTenant[];
  onConfirm: (fields: EditVmFields) => void;
  onClose: () => void;
  busy: boolean;
  /** When true, render inline (no modal overlay) for the VM detail Settings tab. */
  embedded?: boolean;
}) {
  const queryClient = useQueryClient();

  // ── basic edit form state ──────────────────────────────────────────────
  const [vcpus,            setVcpus]           = useState(String(vm.vcpus || ""));
  const [ramGb,            setRamGb]            = useState(
    vm.ram_mb > 0 ? String(+(vm.ram_mb / 1024).toFixed(2)) : ""
  );
  const [diskGb,           setDiskGb]           = useState(String(vm.disk_gb || ""));
  const [vmName,           setVmName]           = useState(vm.name);
  const [description,      setDescription]      = useState("");
  const [vlan,             setVlan]             = useState(vm.vlan || "");
  const [changeBootOrder,  setChangeBootOrder]  = useState(false);
  const [bootSlots,        setBootSlots]        = useState(["hd", "network", ""]);
  // Tenant select: "" = untagged (admin-only), otherwise stringified tenant id.
  const [tenantSel,        setTenantSel]        = useState<string>(
    vm.tenant_id == null ? "" : String(vm.tenant_id)
  );
  // Owner select (admin-only): "" = none, otherwise stringified user id.
  const [ownerSel,         setOwnerSel]         = useState<string>(
    vm.owner_user_id == null ? "" : String(vm.owner_user_id)
  );

  // ── storage section state (admin) ──────────────────────────────────────
  const [addDiskPool,    setAddDiskPool]    = useState("");
  const [addDiskName,    setAddDiskName]    = useState("");
  const [addDiskSize,    setAddDiskSize]    = useState("20");
  const [storageError,   setStorageError]   = useState<string | null>(null);
  const [storageBusy,    setStorageBusy]    = useState(false);
  const [isoPool,        setIsoPool]        = useState("");
  const [isoPath,        setIsoPath]        = useState("");

  const isStopped = vm.state === "stopped";
  const isOwner   = userId != null && vm.owner_user_id === userId;
  // v0.11.1 claim path: any logged-in user may move a SHARED+unowned VM
  // out of SHARED into PERSONAL or one of their member tenants; the backend
  // auto-stamps owner_user_id to the claimer. Surface this in the UI by
  // letting non-owner non-admin viewers of such VMs see the Tenant select.
  const isUnclaimedShared =
    vm.tenant_id === SHARED_TENANT_ID && vm.owner_user_id == null;
  const canClaim       = !isAdmin && !isOwner && isUnclaimedShared;
  const canEditTenant  = isAdmin || isOwner || canClaim;
  const inputCls  = "w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 disabled:bg-gray-50";

  // ── node resources (admin, for pool lists) ─────────────────────────────
  const { data: resources } = useQuery<NodeResources>({
    queryKey: ["node-resources", vm.node_id],
    queryFn: () => getNodeResources(vm.node_id),
    enabled: isAdmin,
  });

  // ── current disk list ──────────────────────────────────────────────────
  const { data: diskInfo, refetch: refetchDisks } = useQuery<VmDiskInfo>({
    queryKey: ["vm-disks", vm.id],
    queryFn: () => listVmDisks(vm.id),
    enabled: isAdmin,
  });

  // ── ISO volumes for selected pool ──────────────────────────────────────
  const { data: isoVolData } = useQuery({
    queryKey: ["node-pool-volumes", vm.node_id, isoPool],
    queryFn: () => listPoolVolumes(vm.node_id, isoPool),
    enabled: isAdmin && !!isoPool,
  });

  // ── tenants — needed whenever the tenant select is shown ───────────────
  const { data: tenants } = useQuery<Tenant[]>({
    queryKey: ["tenants"],
    queryFn: listTenants,
    enabled: canEditTenant,
  });

  // ── networks catalog (admin only — the network select is admin-gated) ──
  // v0.15.0: the Edit modal's network field is a strict dropdown of the
  // admin-curated catalog rather than a free-text bridge name.
  const { data: catalogNetworks } = useQuery<CatalogNetwork[]>({
    queryKey: ["networks"],
    queryFn: listNetworks,
    enabled: isAdmin,
  });

  // ── users — admin-only roster for the Owner dropdown ───────────────────
  const { data: users } = useQuery<User[]>({
    queryKey: ["users"],
    queryFn: listUsers,
    enabled: isAdmin,
  });

  // Auto-select first RBD pool for "add disk" when resources load. Only
  // rbd pools are valid disk targets (dir pools like 'default'/'nvram'
  // fail with "rbd: error opening pool").
  useEffect(() => {
    const firstRbd = (resources?.pools ?? []).find((p) => p.type === "rbd");
    if (firstRbd && !addDiskPool) {
      setAddDiskPool(firstRbd.name);
    }
  }, [resources]);

  // Reset ISO image when pool changes
  useEffect(() => { setIsoPath(""); }, [isoPool]);

  const handleSubmit = () => {
    const f: EditVmFields = {};

    const v = parseInt(vcpus);
    if (!isNaN(v) && v > 0 && v !== vm.vcpus) f.vcpus = v;

    const r = Math.round(parseFloat(ramGb) * 1024);
    if (!isNaN(r) && r >= 128 && r !== vm.ram_mb) f.ram_mb = r;

    const d = parseInt(diskGb);
    if (!isNaN(d) && d > 0 && d !== vm.disk_gb) f.disk_gb = d;

    if (isAdmin) {
      const trimmedName = vmName.trim();
      if (trimmedName && trimmedName !== vm.name) f.name = trimmedName;
      if (description.trim()) f.description = description.trim();
      const trimmedVlan = vlan.trim();
      if (trimmedVlan && trimmedVlan !== (vm.vlan || "")) f.vlan = trimmedVlan;
      if (changeBootOrder) {
        const boot = bootSlots.filter(Boolean);
        if (boot.length > 0) f.boot_order = boot;
      }
      // Admin owner assignment — "" → null clears the owner.
      const newOwner: number | null =
        ownerSel === "" ? null : parseInt(ownerSel);
      if (newOwner !== vm.owner_user_id) f.owner_user_id = newOwner;
    }

    // Tenant change — admins may set anything, owners only SHARED↔PERSONAL.
    // Either path lands on the same wire field; the server re-validates.
    if (canEditTenant) {
      const newTid: number | null =
        tenantSel === "" ? null : parseInt(tenantSel);
      if (newTid !== vm.tenant_id) f.tenant_id = newTid;
    }

    if (Object.keys(f).length === 0) { onClose(); return; }
    onConfirm(f);
  };

  // ── storage action handlers ────────────────────────────────────────────

  const handleAttachDisk = async () => {
    if (!addDiskPool || !addDiskName.trim()) {
      setStorageError("Pool and image name are required");
      return;
    }
    const size = parseInt(addDiskSize);
    if (isNaN(size) || size < 1) {
      setStorageError("Size must be a positive integer");
      return;
    }
    setStorageError(null);
    setStorageBusy(true);
    try {
      await attachVmDisk(vm.id, addDiskPool, addDiskName.trim(), size);
      setAddDiskName("");
      setAddDiskSize("20");
      await refetchDisks();
      queryClient.invalidateQueries({ queryKey: ["vms"] });
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err as Error)?.message ?? "Attach failed";
      setStorageError(msg);
    } finally {
      setStorageBusy(false);
    }
  };

  const handleDetachDisk = async (target: string) => {
    setStorageError(null);
    setStorageBusy(true);
    try {
      await detachVmDisk(vm.id, target);
      await refetchDisks();
      queryClient.invalidateQueries({ queryKey: ["vms"] });
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err as Error)?.message ?? "Detach failed";
      setStorageError(msg);
    } finally {
      setStorageBusy(false);
    }
  };

  const handleMountIso = async () => {
    if (!isoPath) { setStorageError("Select an ISO first"); return; }
    setStorageError(null);
    setStorageBusy(true);
    try {
      await setVmCdrom(vm.id, isoPath);
      await refetchDisks();
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err as Error)?.message ?? "Mount failed";
      setStorageError(msg);
    } finally {
      setStorageBusy(false);
    }
  };

  const handleEjectIso = async () => {
    setStorageError(null);
    setStorageBusy(true);
    try {
      await setVmCdrom(vm.id, "");
      await refetchDisks();
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err as Error)?.message ?? "Eject failed";
      setStorageError(msg);
    } finally {
      setStorageBusy(false);
    }
  };

  const formInner = (
    <>
        <h2 className="text-base font-semibold mb-1">Edit VM</h2>
        <p className="text-sm text-gray-500 mb-5">
          <span className="font-medium text-gray-800">{vm.name}</span>
          {" — "}
          <span className="text-gray-500">{vm.node_hostname}</span>
        </p>

        {/* User-accessible fields */}
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">vCPUs</label>
              <input
                type="number" min="1" value={vcpus}
                onChange={(e) => setVcpus(e.target.value)}
                disabled={busy}
                className={inputCls}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">RAM (GB)</label>
              <input
                type="number" min="0.5" step="0.5" value={ramGb}
                onChange={(e) => setRamGb(e.target.value)}
                disabled={busy}
                className={inputCls}
              />
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">
              Disk (GB)
              <span className="ml-2 text-xs text-orange-500 font-normal">increase only</span>
            </label>
            <input
              type="number" min={vm.disk_gb > 0 ? vm.disk_gb : 1} value={diskGb}
              onChange={(e) => setDiskGb(e.target.value)}
              disabled={busy}
              className={inputCls}
            />
            {vm.disk_gb > 0 && (
              <p className="text-xs text-gray-400 mt-1">Current: {vm.disk_gb} GB</p>
            )}
          </div>
        </div>

        {/* Tenant — visible to admins (full list), owners (PERSONAL plus
            AD-mapped tenants), and any user who can claim a SHARED+unowned
            VM into PERSONAL or a member tenant (v0.11.2: ownership
            auto-stamps to the claimer). v0.11.3: SHARED is admin-only as a
            **target** — non-admins can leave SHARED (claim) but can't go
            back; the SHARED option is rendered disabled for them so a VM
            currently at SHARED still has a matching <select> value while
            preventing the release-back action. */}
        {canEditTenant && (
          <div className="mt-5 pt-5 border-t border-gray-100">
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
                  {(tenants ?? []).map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}{t.is_builtin ? " (built-in)" : ""}
                    </option>
                  ))}
                </>
              ) : (
                // Owner non-admin (and claimer): SHARED (disabled — admin-
                // only target) + PERSONAL (always available) + every tenant
                // the user is an AD-group member of. Server re-validates
                // membership on PUT and re-rejects SHARED for non-admins.
                (tenants ?? [])
                  .filter((t) =>
                    t.id === SHARED_TENANT_ID ||
                    t.id === PERSONAL_TENANT_ID ||
                    userTenants.some((ut) => ut.id === t.id)
                  )
                  .map((t) => (
                    <option
                      key={t.id}
                      value={t.id}
                      disabled={t.id === SHARED_TENANT_ID}
                    >
                      {t.name}{t.is_builtin ? " (built-in)" : ""}
                      {t.id === SHARED_TENANT_ID ? " — admin-only" : ""}
                    </option>
                  ))
              )}
            </select>
            <p className="text-xs text-gray-400 mt-1">
              {isAdmin
                ? "Controls which users can see and operate this VM."
                : canClaim
                ? "This VM is unclaimed. Picking ODaaSE or one of your tenants will assign you as the owner."
                : "ODaaSE hides this VM from other users (only you and admins see it); scope it to one of your tenants for team visibility. Only an admin can release a VM back to Shared."}
            </p>
          </div>
        )}

        {/* Admin-only fields */}
        {isAdmin && (
          <div className="mt-5 pt-5 border-t border-gray-100 space-y-4">
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Admin fields</p>

            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">
                VM name
                {!isStopped && (
                  <span className="ml-2 text-xs text-orange-500 font-normal">VM must be stopped to rename</span>
                )}
              </label>
              <input
                type="text" value={vmName}
                onChange={(e) => setVmName(e.target.value)}
                disabled={busy || !isStopped}
                className={`${inputCls} disabled:text-gray-400`}
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Description</label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Leave blank to keep current description"
                rows={2}
                disabled={busy}
                className={`${inputCls} resize-none`}
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Network</label>
              {(() => {
                // v0.15.0: strict dropdown over the admin-curated catalog.
                // If the VM's current vlan doesn't match any catalog row
                // (legacy bridge, freshly-deleted network) prepend a
                // disabled "(undefined: …)" option so the field shows the
                // current value without silently swapping it on save.
                const list = catalogNetworks ?? [];
                const hasMatch = list.some((n) => n.bridge_name === vlan);
                const orphan = vlan && !hasMatch ? vlan : null;
                return (
                  <select
                    value={vlan}
                    onChange={(e) => setVlan(e.target.value)}
                    disabled={busy}
                    className={inputCls}
                  >
                    <option value="">— none —</option>
                    {orphan && (
                      <option value={orphan} disabled>
                        (undefined: {orphan})
                      </option>
                    )}
                    {list.map((n) => (
                      <option key={n.id} value={n.bridge_name}>
                        {n.name}
                        {n.vlan_id != null ? ` — VLAN ${n.vlan_id}` : ""}
                        {" "}({n.bridge_name})
                      </option>
                    ))}
                  </select>
                );
              })()}
              {vm.vlan && <p className="text-xs text-gray-400 mt-1">Current: {vm.vlan}</p>}
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Owner</label>
              <select
                value={ownerSel}
                onChange={(e) => setOwnerSel(e.target.value)}
                disabled={busy}
                className={inputCls}
              >
                <option value="">(none)</option>
                {(users ?? []).filter((u) => u.is_active).map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.username}{u.is_admin ? " (admin)" : ""}
                  </option>
                ))}
              </select>
              <p className="text-xs text-gray-400 mt-1">
                Required when tenant is "ODaaSE" — that user (plus admins) gets exclusive visibility.
              </p>
            </div>

            <div>
              <label className="flex items-center gap-2 text-xs font-medium text-gray-600 cursor-pointer mb-2">
                <input
                  type="checkbox"
                  checked={changeBootOrder}
                  onChange={(e) => setChangeBootOrder(e.target.checked)}
                  disabled={busy}
                  className="accent-blue-600"
                />
                Change boot order
              </label>
              {changeBootOrder && (
                <div className="space-y-2 pl-1">
                  {bootSlots.map((slot, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <span className="text-xs text-gray-400 w-12 shrink-0">
                        {i + 1}{i === 0 ? "st" : i === 1 ? "nd" : "rd"} boot
                      </span>
                      <select
                        value={slot}
                        onChange={(e) => {
                          const updated = [...bootSlots];
                          updated[i] = e.target.value;
                          setBootSlots(updated);
                        }}
                        disabled={busy}
                        className="flex-1 px-2 py-1.5 border border-gray-300 rounded text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
                      >
                        <option value="">(none)</option>
                        <option value="hd">Hard disk</option>
                        <option value="network">Network (PXE)</option>
                        <option value="cdrom">CD-ROM</option>
                      </select>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* ── Storage section ──────────────────────────────────────── */}
            <div className="pt-4 border-t border-gray-100">
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">Storage</p>

              {storageError && (
                <div className="mb-3 flex justify-between items-start rounded-md bg-red-50 px-3 py-2 text-xs text-red-700">
                  <span>{storageError}</span>
                  <button onClick={() => setStorageError(null)} className="ml-3 shrink-0 text-red-400 hover:text-red-600">✕</button>
                </div>
              )}

              {/* Current disks table */}
              {diskInfo && diskInfo.disks.length > 0 ? (
                <table className="w-full text-xs mb-3 border border-gray-100 rounded-md overflow-hidden">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-3 py-1.5 text-left font-medium text-gray-500">Target</th>
                      <th className="px-3 py-1.5 text-left font-medium text-gray-500">Source</th>
                      <th className="px-3 py-1.5 text-left font-medium text-gray-500">Size</th>
                      <th className="px-3 py-1.5"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {diskInfo.disks.map((d) => (
                      <tr key={d.target}>
                        <td className="px-3 py-1.5 font-mono text-gray-700">{d.target}</td>
                        <td className="px-3 py-1.5 text-gray-500 max-w-[160px] truncate" title={d.source}>{d.source || "—"}</td>
                        <td className="px-3 py-1.5 text-gray-600 whitespace-nowrap">
                          {d.size_gb > 0 ? `${d.size_gb} GB` : "—"}
                        </td>
                        <td className="px-3 py-1.5 text-right">
                          {d.target !== "vda" && (
                            <button
                              onClick={() => handleDetachDisk(d.target)}
                              disabled={storageBusy || busy}
                              className="px-2 py-0.5 text-xs rounded bg-red-50 text-red-700 hover:bg-red-100 disabled:opacity-40"
                            >
                              Detach
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : diskInfo ? (
                <p className="text-xs text-gray-400 mb-3">No disks found.</p>
              ) : (
                <p className="text-xs text-gray-400 mb-3">Loading disks…</p>
              )}

              {/* Add disk */}
              <div className="mb-3">
                <p className="text-xs font-medium text-gray-600 mb-1.5">Add disk</p>
                <div className="flex gap-2 items-end">
                  <div className="w-28 shrink-0">
                    <label className="block text-xs text-gray-500 mb-0.5">Pool</label>
                    <select
                      value={addDiskPool}
                      onChange={(e) => setAddDiskPool(e.target.value)}
                      disabled={storageBusy || busy}
                      className="w-full px-2 py-1.5 border border-gray-300 rounded text-xs focus:outline-none focus:ring-2 focus:ring-blue-400 disabled:bg-gray-50"
                    >
                      {(resources?.pools ?? []).filter((p) => p.type === "rbd").map((p) => (
                        <option key={p.name} value={p.name}>{p.name}</option>
                      ))}
                    </select>
                  </div>
                  <div className="flex-1 min-w-0">
                    <label className="block text-xs text-gray-500 mb-0.5">Image name</label>
                    <input
                      type="text"
                      value={addDiskName}
                      onChange={(e) => setAddDiskName(e.target.value)}
                      placeholder={`${vm.name}-data`}
                      disabled={storageBusy || busy}
                      className="w-full px-2 py-1.5 border border-gray-300 rounded text-xs focus:outline-none focus:ring-2 focus:ring-blue-400 disabled:bg-gray-50"
                    />
                  </div>
                  <div className="w-16 shrink-0">
                    <label className="block text-xs text-gray-500 mb-0.5">GB</label>
                    <input
                      type="number" min="1"
                      value={addDiskSize}
                      onChange={(e) => setAddDiskSize(e.target.value)}
                      disabled={storageBusy || busy}
                      className="w-full px-2 py-1.5 border border-gray-300 rounded text-xs focus:outline-none focus:ring-2 focus:ring-blue-400 disabled:bg-gray-50"
                    />
                  </div>
                  <button
                    onClick={handleAttachDisk}
                    disabled={storageBusy || busy || !addDiskPool}
                    className="shrink-0 px-3 py-1.5 text-xs rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40"
                  >
                    {storageBusy ? "…" : "Add"}
                  </button>
                </div>
              </div>

              {/* CD-ROM / ISO */}
              <div>
                <p className="text-xs font-medium text-gray-600 mb-1.5">
                  ISO / CD-ROM
                  {diskInfo?.cdrom?.iso && (
                    <span className="ml-2 font-normal text-gray-400 truncate max-w-[200px] inline-block align-bottom" title={diskInfo.cdrom.iso}>
                      {diskInfo.cdrom.iso.split("/").pop()}
                    </span>
                  )}
                </p>
                <div className="flex gap-2 items-end">
                  <div className="w-28 shrink-0">
                    <label className="block text-xs text-gray-500 mb-0.5">Pool</label>
                    <select
                      value={isoPool}
                      onChange={(e) => setIsoPool(e.target.value)}
                      disabled={storageBusy || busy}
                      className="w-full px-2 py-1.5 border border-gray-300 rounded text-xs focus:outline-none focus:ring-2 focus:ring-blue-400 disabled:bg-gray-50"
                    >
                      <option value="">— select —</option>
                      {(resources?.pools ?? []).map((p) => (
                        <option key={p.name} value={p.name}>{p.name}</option>
                      ))}
                    </select>
                  </div>
                  <div className="flex-1 min-w-0">
                    <label className="block text-xs text-gray-500 mb-0.5">ISO file</label>
                    {isoPool ? (
                      <select
                        value={isoPath}
                        onChange={(e) => setIsoPath(e.target.value)}
                        disabled={storageBusy || busy}
                        className="w-full px-2 py-1.5 border border-gray-300 rounded text-xs focus:outline-none focus:ring-2 focus:ring-blue-400 disabled:bg-gray-50"
                      >
                        <option value="">— select ISO —</option>
                        {(isoVolData?.volumes ?? []).map((v) => (
                          <option key={v.name} value={v.path}>{v.name}</option>
                        ))}
                      </select>
                    ) : (
                      <select disabled className="w-full px-2 py-1.5 border border-gray-300 rounded text-xs bg-gray-50 text-gray-400">
                        <option>Select a pool first</option>
                      </select>
                    )}
                  </div>
                  <button
                    onClick={handleMountIso}
                    disabled={storageBusy || busy || !isoPath}
                    className="shrink-0 px-3 py-1.5 text-xs rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40"
                  >
                    Mount
                  </button>
                  <button
                    onClick={handleEjectIso}
                    disabled={storageBusy || busy || !diskInfo?.cdrom?.iso}
                    className="shrink-0 px-3 py-1.5 text-xs rounded bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-40"
                  >
                    Eject
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        <div className="flex justify-end gap-2 mt-6">
          {!embedded && (
            <button
              onClick={onClose}
              disabled={busy}
              className="px-3 py-1.5 text-sm rounded-md bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-40"
            >
              Cancel
            </button>
          )}
          <button
            onClick={handleSubmit}
            disabled={busy}
            className="px-3 py-1.5 text-sm rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40"
          >
            {busy ? "Saving…" : "Save changes"}
          </button>
        </div>
    </>
  );

  if (embedded) {
    return <div className="bg-white rounded-xl shadow p-6">{formInner}</div>;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-6 overflow-y-auto max-h-[90vh]">
        {formInner}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Delete modal
// ---------------------------------------------------------------------------
// v0.22: rendered both for admins and for VM owners (self-service delete).
// The parent gates the Delete button on `isAdmin || vm.owner_user_id === me`,
// so anyone opening this modal is authorised to delete. UX is the same for
// both roles — the `delete_disk` checkbox appears iff the VM has an RBD
// disk, defaults to on, and the backend `rbd rm` runs when checked.

export function DeleteModal({
  vm, onConfirm, onClose, busy,
}: {
  vm: VM;
  onConfirm: (deleteDisk: boolean) => void;
  onClose: () => void;
  busy: boolean;
}) {
  const hasRbdDisk = !!vm.disk_path?.startsWith("rbd:");
  const [deleteDisk, setDeleteDisk] = useState(hasRbdDisk);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6">
        <h2 className="text-base font-semibold text-red-700 mb-1">Delete VM</h2>
        <p className="text-sm text-gray-600 mb-4">
          This will permanently destroy{" "}
          <span className="font-semibold text-gray-900">{vm.name}</span>
          {vm.state !== "stopped" && (
            <span className="ml-1 text-orange-600">(will be force-stopped first)</span>
          )}
          . This cannot be undone.
        </p>

        {hasRbdDisk && (
          <label className="flex items-start gap-2 text-sm text-gray-700 mb-5 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={deleteDisk}
              onChange={(e) => setDeleteDisk(e.target.checked)}
              disabled={busy}
              className="mt-0.5 accent-red-600"
            />
            <span>
              Also delete disk image
              {vm.disk_path && (
                <span className="ml-1 font-mono text-xs text-gray-400">
                  ({vm.disk_path.replace(/^rbd:/, "")})
                </span>
              )}
            </span>
          </label>
        )}

        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            disabled={busy}
            className="px-3 py-1.5 text-sm rounded-md bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-40"
          >
            Cancel
          </button>
          <button
            onClick={() => onConfirm(deleteDisk)}
            disabled={busy}
            className="px-3 py-1.5 text-sm rounded-md bg-red-600 text-white hover:bg-red-700 disabled:opacity-40"
          >
            {busy ? "Deleting…" : "Delete VM"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Action buttons
// ---------------------------------------------------------------------------

// v0.22.3 — raw libvirt XML editor. Admin-only, opened from ActionButtons.
// Loads `virsh dumpxml` output into a textarea with a syntax-highlighted
// overlay (no external deps — a transparent <textarea> sits on top of a
// coloured <pre> that mirrors its contents; the textarea handles input and
// scrolling, the <pre> just draws colours). On save calls
// PUT /api/vms/<id>/xml which pipes to `virsh define` on the node — most
// changes require a VM reboot to take effect.

// Escape HTML entities before injecting into the highlighter output.
const escapeHtml = (s: string) =>
  s.replace(/&/g, "&amp;")
   .replace(/</g, "&lt;")
   .replace(/>/g, "&gt;");

// Very small XML tokenizer → HTML with span classes. Order matters:
// comments first (may contain < > that would otherwise be treated as
// tags), then tags (with attributes/values), then bare text.
const highlightXml = (src: string): string => {
  // Split on comments so their content is preserved verbatim as one span.
  const parts = src.split(/(<!--[\s\S]*?-->)/g);
  return parts.map((chunk) => {
    if (chunk.startsWith("<!--")) {
      return `<span class="text-gray-400 italic">${escapeHtml(chunk)}</span>`;
    }
    // Highlight tags: <name attr="val" attr2='v'>, </name>, <name/>
    return escapeHtml(chunk).replace(
      /(&lt;\/?)([\w:.-]+)((?:\s+[\w:.-]+\s*=\s*(?:&quot;[^&]*?&quot;|&#39;[^&]*?&#39;))*)(\s*\/?&gt;)/g,
      (_m, open: string, name: string, attrs: string, close: string) => {
        const colouredAttrs = attrs.replace(
          /(\s+)([\w:.-]+)(\s*=\s*)(&quot;[^&]*?&quot;|&#39;[^&]*?&#39;)/g,
          (_a, ws: string, an: string, eq: string, av: string) =>
            `${ws}<span class="text-amber-600">${an}</span>${eq}<span class="text-emerald-700">${av}</span>`,
        );
        return `<span class="text-blue-600">${open}${name}</span>${colouredAttrs}<span class="text-blue-600">${close}</span>`;
      },
    );
  }).join("");
};

export function XmlModal({ vm, onClose, embedded = false }: { vm: VM; onClose?: () => void; embedded?: boolean }) {
  const [xml, setXml] = useState<string>("");
  const [original, setOriginal] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const preRef = useRef<HTMLPreElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getVmXml(vm.id)
      .then((doc) => {
        if (cancelled) return;
        setXml(doc);
        setOriginal(doc);
        setError(null);
      })
      .catch((e) => !cancelled && setError(e?.response?.data?.error ?? String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [vm.id]);

  const dirty = xml !== original;

  const save = async () => {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const res = await setVmXml(vm.id, xml);
      setOriginal(xml);
      setMessage(res.message ?? "Saved. Reboot the VM for most changes to take effect.");
    } catch (e: unknown) {
      const err = e as { response?: { data?: { error?: string } } };
      setError(err?.response?.data?.error ?? String(e));
    } finally {
      setSaving(false);
    }
  };

  // Keep the highlight <pre> scroll in lockstep with the textarea.
  const syncScroll = (e: React.UIEvent<HTMLTextAreaElement>) => {
    const el = e.currentTarget;
    if (preRef.current) {
      preRef.current.scrollTop  = el.scrollTop;
      preRef.current.scrollLeft = el.scrollLeft;
    }
  };

  // Shared styles for textarea + <pre> so glyphs land on the same pixels.
  // Trailing newline in the highlighter guarantees the last line renders
  // (some browsers trim it inside <pre>).
  const editorStyle: React.CSSProperties = {
    margin: 0,
    padding: "0.5rem",
    border: "1px solid #d1d5db",
    borderRadius: "0.375rem",
    font: "12px/1.5 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
    whiteSpace: "pre",
    tabSize: 2,
  };

  const editorBody = (
    <>
      {loading ? (
        <div className="text-sm text-gray-500 italic">Loading XML…</div>
      ) : (
        <div className="relative w-full" style={{ height: embedded ? "60vh" : "60vh" }}>
          <pre
            ref={preRef}
            aria-hidden="true"
            style={{
              ...editorStyle,
              position: "absolute",
              inset: 0,
              overflow: "auto",
              background: "#fafafa",
              color: "#111827",
              pointerEvents: "none",
            }}
            dangerouslySetInnerHTML={{ __html: highlightXml(xml) + "\n" }}
          />
          <textarea
            value={xml}
            onChange={(e) => setXml(e.target.value)}
            onScroll={syncScroll}
            spellCheck={false}
            style={{
              ...editorStyle,
              position: "absolute",
              inset: 0,
              width: "100%",
              height: "100%",
              background: "transparent",
              color: "transparent",
              caretColor: "#111827",
              resize: "none",
            }}
          />
        </div>
      )}
    </>
  );

  const statusBlocks = (
    <>
      {error && (
        <div className="mx-4 mb-2 p-2 text-xs bg-red-50 text-red-700 rounded border border-red-200 font-mono whitespace-pre-wrap">
          {error}
        </div>
      )}
      {message && !error && (
        <div className="mx-4 mb-2 p-2 text-xs bg-green-50 text-green-700 rounded border border-green-200">
          {message}
        </div>
      )}
    </>
  );

  // Embedded (VM detail page tab): plain card, no portal/overlay, no Close.
  if (embedded) {
    return (
      <div className="bg-white rounded-xl shadow flex flex-col">
        <div className="px-5 py-3 border-b">
          <h2 className="text-base font-semibold">Edit libvirt XML — {vm.name}</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            Direct <code className="font-mono">virsh define</code>. Most changes require a VM reboot.
            Renaming the domain is not supported.
          </p>
        </div>
        <div className="p-4">{editorBody}</div>
        {statusBlocks}
        <div className="flex justify-end gap-2 px-5 py-3 border-t bg-gray-50 rounded-b-xl">
          <button
            onClick={save}
            disabled={saving || loading || !dirty}
            className="px-3 py-1.5 text-sm rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40"
          >
            {saving ? "Saving…" : "Save (virsh define)"}
          </button>
        </div>
      </div>
    );
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-5xl flex flex-col" style={{ maxHeight: "90vh" }}>
        <div className="flex items-center justify-between px-5 py-3 border-b">
          <div>
            <h2 className="text-base font-semibold">Edit libvirt XML — {vm.name}</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Direct <code className="font-mono">virsh define</code>. Most changes require a VM reboot.
              Renaming the domain is not supported.
            </p>
          </div>
          <button onClick={onClose} className="p-1 text-gray-500 hover:text-gray-700" aria-label="Close">
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 min-h-0 p-4">{editorBody}</div>

        {statusBlocks}

        <div className="flex justify-end gap-2 px-5 py-3 border-t bg-gray-50 rounded-b-xl">
          <button
            onClick={onClose}
            disabled={saving}
            className="px-3 py-1.5 text-sm rounded-md bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-40"
          >
            Close
          </button>
          <button
            onClick={save}
            disabled={saving || loading || !dirty}
            className="px-3 py-1.5 text-sm rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40"
          >
            {saving ? "Saving…" : "Save (virsh define)"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

export type VmActionFn = (action: Parameters<typeof vmAction>[1], opts?: { force?: boolean }) => void;

function Btn({
  label, icon, onClick, disabled, danger,
}: {
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
  disabled: boolean;
  danger?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
      className={`inline-flex items-center justify-center p-1.5 rounded transition-colors
        ${danger
          ? "bg-red-50 text-red-700 hover:bg-red-100"
          : "bg-gray-100 text-gray-700 hover:bg-gray-200"}
        disabled:opacity-40 disabled:cursor-not-allowed`}
    >
      {icon}
    </button>
  );
}

export function ActionButtons({ vm, busy, onAction, onConsole, onMigrate, onEdit, onDelete, onXml, onRenew, onExempt, isAdmin, userId }: {
  vm: VM; busy: boolean; onAction: VmActionFn; onConsole: () => void;
  onMigrate: () => void; onEdit: () => void; onDelete: () => void; onXml: () => void;
  onRenew: () => void; onExempt: () => void; isAdmin: boolean;
  userId: number | null;
}) {
  const editBtn = (
    <Btn label="Edit" icon={<Pencil size={14} />} onClick={onEdit} disabled={busy} />
  );
  const migrateBtn = isAdmin && (
    <Btn label="Migrate" icon={<ArrowRightLeft size={14} />} onClick={onMigrate} disabled={busy} />
  );
  // v0.22.3 — admin-only "raw libvirt XML" editor. Direct redefine; most
  // changes require a VM reboot to take effect. Rarely-used escape hatch,
  // so it lives after Migrate to keep the row visually stable for users
  // who never see it.
  const xmlBtn = isAdmin && (
    <Btn label="XML" icon={<FileCode size={14} />} onClick={onXml} disabled={busy} />
  );
  // v0.22: self-service delete — VM owner may delete their own VM in
  // addition to admins. Backend enforces the same rule (403 for non-
  // admin non-owner). Team-mates in the same tenant who did NOT create
  // the VM cannot delete it — ownership is strict, not tenant-based.
  const isOwner = userId != null && vm.owner_user_id === userId;
  const deleteBtn = (isAdmin || isOwner) && (
    <Btn label="Delete" icon={<Trash2 size={14} />} onClick={onDelete} disabled={busy} danger />
  );
  // 1-year-TTL lifecycle controls. Renew resets the TTL (and restarts the
  // VM if it was auto-stopped) — available to the owner or an admin; early
  // renewal is always allowed. Exempt is an admin-only toggle that opts the
  // VM out of expiry enforcement entirely.
  const renewBtn = (isAdmin || isOwner) && (
    <Btn label="Renew" icon={<CalendarClock size={14} />} onClick={onRenew} disabled={busy} />
  );
  const exemptBtn = isAdmin && (
    <Btn
      label={vm.exempt ? "Un-exempt" : "Exempt"}
      icon={vm.exempt ? <ShieldOff size={14} /> : <ShieldCheck size={14} />}
      onClick={onExempt}
      disabled={busy}
    />
  );
  switch (vm.state) {
    case "running":
      return (
        <div className="flex flex-wrap gap-1">
          {editBtn}
          <Btn label="Stop"    icon={<Square      size={14} />} onClick={() => onAction("stop")}    disabled={busy} />
          <Btn label="Force off (hard kill)" icon={<Zap size={14} />} onClick={() => onAction("stop", { force: true })} disabled={busy} danger />
          <Btn label="Reboot"  icon={<RotateCcw   size={14} />} onClick={() => onAction("reboot")}  disabled={busy} />
          <Btn label="Suspend" icon={<PauseCircle size={14} />} onClick={() => onAction("suspend")} disabled={busy} />
          <Btn label="Console" icon={<Terminal    size={14} />} onClick={onConsole}                  disabled={busy} />
          {migrateBtn}
          {xmlBtn}
          {renewBtn}
          {exemptBtn}
          {deleteBtn}
        </div>
      );
    case "stopped":
      return (
        <div className="flex flex-wrap gap-1">
          {editBtn}
          <Btn label="Start" icon={<Play size={14} />} onClick={() => onAction("start")} disabled={busy} />
          {migrateBtn}
          {xmlBtn}
          {renewBtn}
          {exemptBtn}
          {deleteBtn}
        </div>
      );
    case "suspended":
    case "paused":
      return (
        <div className="flex flex-wrap gap-1">
          {editBtn}
          <Btn label="Resume"    icon={<PlayCircle size={14} />} onClick={() => onAction("resume")}                   disabled={busy} />
          <Btn label="Force off" icon={<Zap        size={14} />} onClick={() => onAction("stop", { force: true })} disabled={busy} danger />
          {renewBtn}
          {exemptBtn}
          {deleteBtn}
        </div>
      );
    case "crashed":
      return (
        <div className="flex flex-wrap gap-1">
          {editBtn}
          <Btn label="Start"     icon={<Play size={14} />} onClick={() => onAction("start")}                        disabled={busy} />
          <Btn label="Force off" icon={<Zap  size={14} />} onClick={() => onAction("stop", { force: true })} disabled={busy} danger />
          {renewBtn}
          {exemptBtn}
          {deleteBtn}
        </div>
      );
    case "stopping":
      return <span className="text-xs text-gray-400 italic">stopping…</span>;
    case "migrating":
      return <span className="text-xs text-amber-600 italic">migrating…</span>;
    default:
      return null;
  }
}
