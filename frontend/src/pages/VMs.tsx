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
import { useNavigate } from "react-router-dom";
import { Monitor, Play, Square, RotateCcw, PauseCircle, PlayCircle, Zap, Terminal, ArrowRightLeft, Pencil, Plus, X, Trash2, Search, ChevronUp, ChevronDown, Rocket, FileCode, CalendarClock, ShieldCheck, ShieldOff, Columns3 } from "lucide-react";
import { useState, useEffect, useMemo, useRef } from "react";
import { createPortal } from "react-dom";
import { listVMs, vmAction, requestConsole, migrateVm, editVm, createVm, listVmImages, listVmDisks, attachVmDisk, detachVmDisk, setVmCdrom, deleteVm, getVmXml, setVmXml, setVmPriority, renewVm, setVmExempt, getLifecycleSettings, setLifecycleEnabled } from "../api/vms";
import type { EditVmFields, CreateVmFields, VmImage, VmDiskInfo, LifecycleSettings } from "../api/vms";
import { listNodes, getNodeResources, listPoolVolumes } from "../api/nodes";
import type { NodeResources } from "../api/nodes";
import { listNetworks } from "../api/networks";
import type { Network as CatalogNetwork } from "../api/networks";
import { listTenants } from "../api/tenants";
import type { Tenant } from "../api/tenants";
import { listUsers, listMySshPubkeys } from "../api/users";
import type { SshPubkey } from "../api/users";
import type { User } from "../types/user";
import type { VM } from "../types/vm";
import type { Node } from "../types/node";
import { useAuth } from "../hooks/useAuth";
import type { MeTenant } from "../api/auth";
import BulkAssignVmTenantModal from "../components/BulkAssignVmTenantModal";

// Built-in tenant ids — see controller/app/services/tenant_service.py
const SHARED_TENANT_ID   = 1;
// PERSONAL_TENANT_ID renumbered 2 → 3 in v0.21 (migration 0004 merged
// the old "Personal VM" id=2 into "ODaaSE" id=3, keeping the DB name
// "ODaaSE" and adopting Personal VM's owner-only visibility).
const PERSONAL_TENANT_ID = 3;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function StateBadge({ state }: { state: VM["state"] }) {
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
function PriorityBadge({ priority }: { priority?: string }) {
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

// 1-year-TTL lifecycle badge. Derived purely from server-supplied fields:
//   exempt            → no expiry enforcement (grey "Exempt").
//   pending_delete    → expired+unrenewed past grace; Ops asked to delete (red).
//   auto_stopped      → shut down at expiry; renew to restore (red).
//   expires_at ≤ 30d  → amber "Expires in Nd" (whole days).
//   expires_at > 30d  → subtle grey "Expires <date>".
//   none of the above → nothing.
function expiryBadge(vm: VM): React.ReactNode {
  if (vm.exempt) {
    return (
      <span
        className="inline-flex px-2 py-0.5 text-xs font-medium rounded-full bg-gray-100 text-gray-600"
        title="Exempt from the 1-year TTL — no expiry enforcement"
      >
        Exempt
      </span>
    );
  }
  if (vm.lifecycle_state === "pending_delete") {
    return (
      <span
        className="inline-flex px-2 py-0.5 text-xs font-medium rounded-full bg-red-200 text-red-900"
        title="Expired and unrenewed past the grace period — flagged to Ops for deletion. Renew to restore."
      >
        Expired — deletion requested
      </span>
    );
  }
  if (vm.lifecycle_state === "auto_stopped") {
    return (
      <span
        className="inline-flex px-2 py-0.5 text-xs font-medium rounded-full bg-red-100 text-red-800"
        title="Shut down at its 1-year expiry. It cannot be restarted until you Renew it."
      >
        Expired — shut down
      </span>
    );
  }
  if (vm.expires_at) {
    const expires = new Date(vm.expires_at);
    const days = Math.floor((expires.getTime() - Date.now()) / 86_400_000);
    if (days <= 30) {
      return (
        <span
          className="inline-flex px-2 py-0.5 text-xs font-medium rounded-full bg-amber-100 text-amber-800"
          title={`Expires ${expires.toLocaleDateString()}`}
        >
          Expires in {days}d
        </span>
      );
    }
    return (
      <span className="text-xs text-gray-400" title={`Expires ${expires.toLocaleString()}`}>
        Expires {expires.toLocaleDateString()}
      </span>
    );
  }
  return null;
}

// Dedicated "Expires" column cell — ALWAYS shows the remaining days (not just
// within 30d like the State-column badge), so lifecycle is visible at a glance.
//   exempt            → grey "Exempt"
//   auto_stopped / pending_delete → red "Expired"
//   expires_at set    → "Nd left" (amber ≤30d, else grey), tooltip = exact date
//   no expires_at     → "—"
function expiresCell(vm: VM): React.ReactNode {
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

function vlanLabel(raw: string | null): string {
  if (!raw) return "—";
  const m = raw.match(/(?:br|vlan|bond\d+\.)(\d+)$/i);
  return m ? `VLAN ${m[1]}` : raw;
}

function ramLabel(mb: number): string {
  if (mb === 0) return "—";
  return mb >= 1024 ? `${Math.round(mb / 1024)} GB` : `${mb} MB`;
}

// ---------------------------------------------------------------------------
// Migrate modal
// ---------------------------------------------------------------------------

function MigrateModal({
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

function DeleteModal({
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

type VmActionFn = (action: Parameters<typeof vmAction>[1], opts?: { force?: boolean }) => void;

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

function ActionButtons({ vm, busy, onAction, onConsole, onMigrate, onEdit, onDelete, onXml, onRenew, onExempt, isAdmin, userId }: {
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

// Column registry for the VM table. `Name`, `State` and `Actions` are always
// rendered (essential) and are NOT in this list — everything here is
// admin-toggleable via the "Columns" picker. `adminOnly` columns are the infra
// ones that non-admins never see at all (Host/Datastore), so they only appear
// in the picker for admins anyway. Order here matches the on-screen order
// (between State and Priority/Tenant/Actions).
type VmColumnId =
  | "host" | "ip" | "vcpus" | "ram" | "network" | "disk"
  | "datastore" | "priority" | "tenant" | "expires";

interface VmColumnDef {
  id: VmColumnId;
  label: string;
  adminOnly: boolean;
}

const VM_COLUMNS: readonly VmColumnDef[] = [
  { id: "host",      label: "Host",      adminOnly: true  },
  { id: "ip",        label: "IP",        adminOnly: false },
  { id: "vcpus",     label: "vCPUs",     adminOnly: false },
  { id: "ram",       label: "RAM",       adminOnly: false },
  { id: "network",   label: "Network",   adminOnly: false },
  { id: "disk",      label: "Disk",      adminOnly: false },
  { id: "datastore", label: "Datastore", adminOnly: true  },
  { id: "priority",  label: "Priority",  adminOnly: false },
  { id: "tenant",    label: "Tenant",    adminOnly: false },
  { id: "expires",   label: "Expires",   adminOnly: false },
] as const;

// localStorage key for the admin's hidden-column preference (list of column ids).
const VM_COLS_STORAGE_KEY = "wvc.vms.hiddenColumns";

export default function VMs() {
  const queryClient = useQueryClient();
  // `tenants` is the user's AD-mapped membership list from /api/auth/me
  // (excludes SHARED + PERSONAL — those are always available). Threaded into
  // CreateModal + EditModal so non-admins can pick any tenant they belong to.
  const { isAdmin, userId, tenants }          = useAuth();
  const navigate                              = useNavigate();
  const [pendingId, setPendingId]             = useState<number | null>(null);
  const [actionError, setActionError]         = useState<string | null>(null);
  const [migrateVmTarget, setMigrateVmTarget] = useState<VM | null>(null);
  const [migrating, setMigrating]             = useState(false);
  const [editVmTarget, setEditVmTarget]       = useState<VM | null>(null);
  const [editing, setEditing]                 = useState(false);
  const [showCreate, setShowCreate]             = useState(false);
  const [showQuickCreate, setShowQuickCreate]   = useState(false);
  const [deleteVmTarget, setDeleteVmTarget]   = useState<VM | null>(null);
  const [deleting, setDeleting]               = useState(false);
  // v0.22.3 — raw libvirt XML editor (admin only, opened from ActionButtons)
  const [xmlVmTarget, setXmlVmTarget]         = useState<VM | null>(null);
  // v0.12.0: list usability — case-insensitive substring search across
  // VM name + IP, plus click-to-sort on Host and Datastore columns.
  // State is in-memory only (resets on navigation); URL/localStorage
  // persistence would be the next step if users ask for it.
  const [search, setSearch]                   = useState("");
  const [sortBy, setSortBy]                   = useState<"host" | "datastore" | null>(null);
  const [sortDir, setSortDir]                 = useState<"asc" | "desc">("asc");

  // Admin-only column picker. `hiddenCols` is the set of column ids the admin
  // has switched off; persisted to localStorage so the choice sticks across
  // sessions. Name/State/Actions are always shown and are not in the registry.
  const [hiddenCols, setHiddenCols] = useState<Set<VmColumnId>>(() => {
    try {
      const raw = localStorage.getItem(VM_COLS_STORAGE_KEY);
      return raw ? new Set(JSON.parse(raw) as VmColumnId[]) : new Set();
    } catch {
      return new Set();
    }
  });
  const [showColMenu, setShowColMenu] = useState(false);
  const colMenuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    try {
      localStorage.setItem(VM_COLS_STORAGE_KEY, JSON.stringify([...hiddenCols]));
    } catch {
      /* ignore quota / private-mode errors — picker still works in-session */
    }
  }, [hiddenCols]);

  // Close the column menu on outside click.
  useEffect(() => {
    if (!showColMenu) return;
    const onClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement | null;
      if (colMenuRef.current && target && !colMenuRef.current.contains(target)) {
        setShowColMenu(false);
      }
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [showColMenu]);

  // Columns actually visible right now: registry minus admin-only (for
  // non-admins) minus the admin's hidden set. Drives both header and rows so
  // they can never drift out of sync.
  const visibleCols = useMemo(
    () =>
      VM_COLUMNS.filter(
        (c) => (isAdmin || !c.adminOnly) && !hiddenCols.has(c.id),
      ),
    [isAdmin, hiddenCols],
  );
  const colShown = (id: VmColumnId) => visibleCols.some((c) => c.id === id);

  const toggleCol = (id: VmColumnId) =>
    setHiddenCols((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // v0.19.0: multi-select for admin bulk tenant assignment. Non-admins
  // never see the checkbox column, so `selectedIds` stays empty for them.
  const [selectedIds, setSelectedIds]         = useState<Set<number>>(new Set());
  const [showBulkTenant, setShowBulkTenant]   = useState(false);

  const { data: vms, isLoading, isError } = useQuery<VM[]>({
    queryKey: ["vms"],
    queryFn: () => listVMs(),
    refetchInterval: 5_000,
  });

  const mutation = useMutation({
    mutationFn: ({
      id, action, opts,
    }: {
      id: number;
      action: Parameters<typeof vmAction>[1];
      opts?: { force?: boolean };
    }) => vmAction(id, action, opts),
    onMutate: ({ id }) => {
      setPendingId(id);
      setActionError(null);
    },
    onSuccess: () => {
      // Refetch immediately so the optimistic state update is visible
      queryClient.invalidateQueries({ queryKey: ["vms"] });
    },
    onError: (err: unknown) => {
      const msg =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err as Error)?.message ??
        "Action failed";
      setActionError(msg);
    },
    onSettled: () => setPendingId(null),
  });

  const priorityMutation = useMutation({
    mutationFn: ({ id, priority }: { id: number; priority: "critical" | "priority" | "default" }) =>
      setVmPriority(id, priority),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["vms"] }),
    onError: (err: unknown) => {
      const msg =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        "Failed to set priority";
      window.alert(msg);
    },
  });

  // 1-year-TTL lifecycle mutations. Renew resets the VM's TTL (owner or
  // admin); the backend also restarts the VM if it had been auto-stopped.
  // Exempt toggles expiry enforcement off/on (admin-only). Both invalidate
  // ["vms"] and surface errors via the shared actionError banner, mirroring
  // the delete flow.
  const renewMut = useMutation({
    mutationFn: (id: number) => renewVm(id),
    onMutate: (id) => {
      setPendingId(id);
      setActionError(null);
    },
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["vms"] });
      if (res.expires_at) {
        window.alert(`Renewed until ${new Date(res.expires_at).toLocaleDateString()}`);
      }
    },
    onError: (err: unknown) => {
      const msg =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err as Error)?.message ??
        "Renew failed";
      setActionError(msg);
    },
    onSettled: () => setPendingId(null),
  });

  const exemptMut = useMutation({
    mutationFn: ({ id, exempt }: { id: number; exempt: boolean }) => setVmExempt(id, exempt),
    onMutate: ({ id }) => {
      setPendingId(id);
      setActionError(null);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["vms"] }),
    onError: (err: unknown) => {
      const msg =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err as Error)?.message ??
        "Failed to toggle exempt";
      setActionError(msg);
    },
    onSettled: () => setPendingId(null),
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

  // Webex renew deep-link: a lifecycle warning card links to
  // /vms?renew=<vm_id>. When the page opens with that param, confirm and fire
  // the renew for that VM, then strip the param so a refresh doesn't repeat it.
  const renewDeepLinkHandled = useRef(false);
  useEffect(() => {
    if (renewDeepLinkHandled.current) return;
    if (!vms || vms.length === 0) return;
    const params = new URLSearchParams(window.location.search);
    const renewId = params.get("renew");
    if (!renewId) return;
    renewDeepLinkHandled.current = true;
    const id = Number(renewId);
    const target = vms.find((v) => v.id === id);
    // Clear the param regardless so refresh/back doesn't re-trigger.
    params.delete("renew");
    const qs = params.toString();
    window.history.replaceState(
      {},
      "",
      window.location.pathname + (qs ? `?${qs}` : ""),
    );
    if (!target) {
      window.alert(
        "That VM could not be found (it may have been deleted or you may not have access).",
      );
      return;
    }
    if (
      window.confirm(
        `Renew "${target.name}" for another 12 months?` +
          (target.lifecycle_state === "auto_stopped" ||
          target.lifecycle_state === "pending_delete"
            ? "\n\nThis will also power the VM back on."
            : ""),
      )
    ) {
      renewMut.mutate(id);
    }
  }, [vms, renewMut]);

  const handleAction = (
    vm: VM,
    action: Parameters<typeof vmAction>[1],
    opts?: { force?: boolean },
  ) => {
    // Hard kill of a RUNNING VM is an abrupt power-cut (virsh destroy) that can
    // lose unsaved data / corrupt the guest FS — confirm before pulling power.
    // Force-off from an already stopped/suspended/crashed state needs no prompt.
    if (opts?.force && vm.state === "running") {
      if (!window.confirm(
        `Force off "${vm.name}"? This is an immediate hard power-off (no clean shutdown) ` +
        `and may cause data loss or filesystem corruption in the guest.`,
      )) {
        return;
      }
    }
    mutation.mutate({ id: vm.id, action, opts });
  };

  // Nodes drive the Migrate target dropdown (admin) AND the Create-VM node
  // picker (everyone, since v0.11.0). `/api/nodes` is JWT-gated, not
  // admin-gated, so non-admins can fetch this safely.
  const { data: nodes } = useQuery<Node[]>({
    queryKey: ["nodes"],
    queryFn: listNodes,
  });

  const handleConsole = async (vm: VM) => {
    setPendingId(vm.id);
    setActionError(null);
    try {
      const { token, wsUrl } = await requestConsole(vm.id, "vnc");
      // wsUrl is a relative path ("/console-ws") — embed host at open time
      void wsUrl; // used by console.html via the token
      const url = `/console.html?token=${encodeURIComponent(token)}&vm=${encodeURIComponent(vm.name)}`;
      window.open(url, "_blank", "noopener,noreferrer");
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err as Error)?.message ??
        "Failed to open console";
      setActionError(msg);
    } finally {
      setPendingId(null);
    }
  };

  const handleMigrateConfirm = async (targetNodeId: number, allowShutdown: boolean) => {
    if (!migrateVmTarget) return;
    const vmId = migrateVmTarget.id;
    setActionError(null);

    // Optimistically mark the VM as migrating in the cache and close the modal
    // immediately so the amber badge is visible in the table during the operation.
    queryClient.setQueryData<VM[]>(["vms"], (old) =>
      old?.map((v) => v.id === vmId ? { ...v, state: "migrating" } : v) ?? []
    );
    setMigrateVmTarget(null);
    setMigrating(true);

    try {
      await migrateVm(vmId, targetNodeId, allowShutdown);
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err as Error)?.message ??
        "Migration failed";
      setActionError(msg);
    } finally {
      // Always refresh to get the real final state from the server
      queryClient.invalidateQueries({ queryKey: ["vms"] });
      setMigrating(false);
    }
  };

  const handleEditConfirm = async (fields: EditVmFields) => {
    if (!editVmTarget) return;
    const vmId = editVmTarget.id;
    setActionError(null);
    setEditing(true);
    try {
      await editVm(vmId, fields);
      queryClient.invalidateQueries({ queryKey: ["vms"] });
      setEditVmTarget(null);
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err as Error)?.message ??
        "Edit failed";
      setActionError(msg);
    } finally {
      setEditing(false);
    }
  };

  const handleDeleteConfirm = async (deleteDisk: boolean) => {
    if (!deleteVmTarget) return;
    const vmId = deleteVmTarget.id;
    setActionError(null);
    setDeleting(true);
    try {
      await deleteVm(vmId, deleteDisk);
      setDeleteVmTarget(null);
      queryClient.invalidateQueries({ queryKey: ["vms"] });
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err as Error)?.message ??
        "Delete failed";
      setActionError(msg);
      setDeleteVmTarget(null);
    } finally {
      setDeleting(false);
    }
  };

  const vmList = vms ?? [];

  // v0.12.0: filter + sort pipeline. `vmList` is the unfiltered set
  // (used for the CreateModal's name-collision check and the total-count
  // badge); `displayVms` is what the table renders. Empty `search`
  // and null `sortBy` are both no-ops.
  const displayVms = useMemo(() => {
    let list: VM[] = vmList;
    const term = search.trim().toLowerCase();
    if (term) {
      list = list.filter((v) =>
        v.name.toLowerCase().includes(term) ||
        (v.ip ?? "").toLowerCase().includes(term)
      );
    }
    if (sortBy) {
      const key: keyof Pick<VM, "node_hostname" | "datastore"> =
        sortBy === "host" ? "node_hostname" : "datastore";
      list = [...list].sort((a, b) => {
        // Null/empty datastores sort to the end regardless of direction
        // so the meaningful values cluster together.
        const av = (a[key] ?? "").toString().toLowerCase();
        const bv = (b[key] ?? "").toString().toLowerCase();
        if (av === "" && bv === "") return 0;
        if (av === "") return 1;
        if (bv === "") return -1;
        const cmp = av.localeCompare(bv);
        return sortDir === "asc" ? cmp : -cmp;
      });
    }
    return list;
  }, [vmList, search, sortBy, sortDir]);

  // Three-state sort cycle: unsorted → asc → desc → unsorted. Switching
  // columns resets to asc on the new column.
  const handleSortClick = (col: "host" | "datastore") => {
    if (sortBy !== col) {
      setSortBy(col);
      setSortDir("asc");
    } else if (sortDir === "asc") {
      setSortDir("desc");
    } else {
      setSortBy(null);
      setSortDir("asc");
    }
  };

  // v0.19.0: row-selection helpers for admin bulk tenant assign.
  // `allSelected` reflects the *currently displayed* list (search-filtered),
  // not the full VM catalog — matches the header checkbox's mental model.
  const toggleRow = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const displayedIds = displayVms.map((v) => v.id);
  const allSelected =
    displayedIds.length > 0 && displayedIds.every((id) => selectedIds.has(id));
  const someSelected = displayedIds.some((id) => selectedIds.has(id));
  const toggleAll = () => {
    if (allSelected) {
      // Deselect only the displayed rows — a mid-search partial selection
      // shouldn't wipe VMs the user selected before searching.
      setSelectedIds((prev) => {
        const next = new Set(prev);
        for (const id of displayedIds) next.delete(id);
        return next;
      });
    } else {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        for (const id of displayedIds) next.add(id);
        return next;
      });
    }
  };
  const clearSelection = () => setSelectedIds(new Set());

  return (
    <div className="p-6">
      {migrateVmTarget && (
        <MigrateModal
          vm={migrateVmTarget}
          nodes={nodes ?? []}
          isAdmin={isAdmin}
          onConfirm={handleMigrateConfirm}
          onClose={() => setMigrateVmTarget(null)}
          busy={migrating}
        />
      )}
      {editVmTarget && (
        <EditModal
          vm={editVmTarget}
          isAdmin={isAdmin}
          userId={userId}
          userTenants={tenants}
          onConfirm={handleEditConfirm}
          onClose={() => setEditVmTarget(null)}
          busy={editing}
        />
      )}
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
      {deleteVmTarget && (
        <DeleteModal
          vm={deleteVmTarget}
          onConfirm={handleDeleteConfirm}
          onClose={() => setDeleteVmTarget(null)}
          busy={deleting}
        />
      )}
      {xmlVmTarget && (
        <XmlModal vm={xmlVmTarget} onClose={() => setXmlVmTarget(null)} />
      )}
      {showBulkTenant && (
        <BulkAssignVmTenantModal
          vmIds={Array.from(selectedIds)}
          onClose={() => {
            setShowBulkTenant(false);
            setSelectedIds(new Set());
          }}
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

      {actionError && (
        <div className="mb-4 flex justify-between items-start rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
          <span>{actionError}</span>
          <button onClick={() => setActionError(null)} className="ml-4 shrink-0 text-red-400 hover:text-red-600">✕</button>
        </div>
      )}

      {/* v0.19.0: admin bulk-action toolbar. Only rendered when the admin
          has ticked at least one row. Bulk-assign-tenant is the only bulk
          action for now; more can be added inline here. */}
      {isAdmin && selectedIds.size > 0 && (
        <div className="mb-4 flex items-center gap-3 rounded-md bg-blue-50 border border-blue-200 px-4 py-2 text-sm">
          <span className="text-blue-800 font-medium">
            {selectedIds.size} VM{selectedIds.size === 1 ? "" : "s"} selected
          </span>
          <button
            onClick={() => setShowBulkTenant(true)}
            className="inline-flex items-center gap-1 px-3 py-1 rounded-md bg-blue-600 text-white text-xs font-medium hover:bg-blue-700"
          >
            Assign tenant…
          </button>
          <button
            onClick={clearSelection}
            className="ml-auto text-xs text-blue-700 hover:text-blue-900"
          >
            Clear selection
          </button>
        </div>
      )}

      {/* v0.12.0: search + filtered-count badge. Hidden until the VM
          list has loaded so the bar doesn't flash empty during loading. */}
      {!isLoading && !isError && vmList.length > 0 && (
        <div className="mb-4 flex items-center gap-3">
          <div className="relative flex-1 max-w-md">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by name or IP…"
              className="w-full pl-9 pr-9 py-2 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400"
            />
            {search && (
              <button
                onClick={() => setSearch("")}
                aria-label="Clear search"
                className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-gray-400 hover:text-gray-600"
              >
                <X size={14} />
              </button>
            )}
          </div>
          {(search || sortBy) && (
            <span className="text-xs text-gray-500">
              {displayVms.length} of {vmList.length}
              {sortBy && (
                <> · sorted by <span className="font-medium">{sortBy === "host" ? "Host" : "Datastore"}</span> ({sortDir})</>
              )}
            </span>
          )}
          {/* v0.32.0: admin-only column picker. Lets admins show/hide the
              non-essential columns; choice persists in localStorage. */}
          {isAdmin && (
            <div className="relative ml-auto" ref={colMenuRef}>
              <button
                onClick={() => setShowColMenu((v) => !v)}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-sm border border-gray-300 rounded-md text-gray-700 hover:bg-gray-50"
                title="Show/hide columns"
              >
                <Columns3 size={15} /> Columns
              </button>
              {showColMenu && (
                <div className="absolute right-0 z-20 mt-1 w-48 rounded-md border border-gray-200 bg-white shadow-lg py-1">
                  <div className="px-3 py-1.5 text-xs font-semibold text-gray-400 uppercase tracking-wide">
                    Columns
                  </div>
                  {VM_COLUMNS.filter((c) => isAdmin || !c.adminOnly).map((c) => (
                    <label
                      key={c.id}
                      className="flex items-center gap-2 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={!hiddenCols.has(c.id)}
                        onChange={() => toggleCol(c.id)}
                      />
                      {c.label}
                    </label>
                  ))}
                  {hiddenCols.size > 0 && (
                    <button
                      onClick={() => setHiddenCols(new Set())}
                      className="w-full text-left px-3 py-1.5 mt-1 text-xs text-blue-600 hover:bg-gray-50 border-t border-gray-100"
                    >
                      Show all
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {isLoading ? (
        <p className="text-gray-500">Loading...</p>
      ) : isError ? (
        <p className="text-red-500">Failed to load VMs.</p>
      ) : vmList.length === 0 ? (
        <div className="text-center py-16 bg-white rounded-xl shadow">
          <Monitor size={40} className="mx-auto text-gray-300 mb-3" />
          <p className="text-gray-500">No VMs discovered yet — sync runs every 30 s.</p>
        </div>
      ) : displayVms.length === 0 ? (
        <div className="text-center py-16 bg-white rounded-xl shadow">
          <Search size={36} className="mx-auto text-gray-300 mb-3" />
          <p className="text-gray-500 mb-3">No VMs match "<span className="font-mono">{search}</span>".</p>
          <button
            onClick={() => setSearch("")}
            className="text-sm text-blue-600 hover:text-blue-800"
          >
            Clear search
          </button>
        </div>
      ) : (
        <div className="bg-white rounded-xl shadow overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                {/* v0.19.0: admin-only row-selection column. Non-admins
                    see the plain headers as before. */}
                {isAdmin && (
                  <th className="px-4 py-3 text-left w-10">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      // Indeterminate needs a ref; skip the tri-state visual —
                      // partial state is obvious from the blue toolbar above.
                      ref={(el) => {
                        if (el) el.indeterminate = someSelected && !allSelected;
                      }}
                      onChange={toggleAll}
                      aria-label="Select all VMs"
                    />
                  </th>
                )}
                {/* v0.12.0: Host and Datastore headers are click-to-sort
                    (asc → desc → unsorted cycle). Other headers stay plain.
                    v0.19.0: "Tenant" column inserted before Actions.
                    v0.24.0: Host and Datastore are admin-only — they leak
                    infra topology that non-admins don't need.
                    v0.32.0: admin column picker — each toggleable column is
                    gated on colShown(); Name/State/Actions are always shown. */}
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide whitespace-nowrap">Name</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide whitespace-nowrap">State</th>
                {visibleCols.map((col) => {
                  const sortKey: "host" | "datastore" | null =
                    col.id === "host" ? "host" : col.id === "datastore" ? "datastore" : null;
                  if (!sortKey) {
                    return (
                      <th key={col.id} className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide whitespace-nowrap">
                        {col.label}
                      </th>
                    );
                  }
                  const isActive = sortBy === sortKey;
                  return (
                    <th
                      key={col.id}
                      onClick={() => handleSortClick(sortKey)}
                      className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide whitespace-nowrap cursor-pointer select-none hover:bg-gray-100"
                    >
                      <span className="inline-flex items-center gap-1">
                        {col.label}
                        {isActive ? (
                          sortDir === "asc"
                            ? <ChevronUp size={12} className="text-gray-700" />
                            : <ChevronDown size={12} className="text-gray-700" />
                        ) : (
                          <ChevronUp size={12} className="text-gray-300" />
                        )}
                      </span>
                    </th>
                  );
                })}
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide whitespace-nowrap">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {displayVms.map((vm) => (
                <tr key={vm.id} className={`hover:bg-gray-50 ${pendingId === vm.id ? "opacity-60" : ""}`}>
                  {isAdmin && (
                    <td className="px-4 py-3">
                      <input
                        type="checkbox"
                        checked={selectedIds.has(vm.id)}
                        onChange={() => toggleRow(vm.id)}
                        aria-label={`Select ${vm.name}`}
                      />
                    </td>
                  )}
                  <td className="px-4 py-3 font-medium whitespace-nowrap">
                    <button
                      onClick={() => navigate(`/vms/${vm.id}`)}
                      className="text-blue-600 hover:text-blue-800 hover:underline"
                      title="Open VM details"
                    >
                      {vm.name}
                    </button>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <StateBadge state={vm.state} />
                      {expiryBadge(vm)}
                    </div>
                  </td>
                  {colShown("host") && (
                    <td className="px-4 py-3 text-gray-500 whitespace-nowrap">{vm.node_hostname}</td>
                  )}
                  {colShown("ip") && (
                    <td className="px-4 py-3 text-gray-700 whitespace-nowrap font-mono text-xs">
                      {vm.ip || <span className="text-gray-400">—</span>}
                    </td>
                  )}
                  {colShown("vcpus") && (
                    <td className="px-4 py-3 text-gray-700">{vm.vcpus || "—"}</td>
                  )}
                  {colShown("ram") && (
                    <td className="px-4 py-3 text-gray-700 whitespace-nowrap">{ramLabel(vm.ram_mb)}</td>
                  )}
                  {colShown("network") && (
                    <td className="px-4 py-3 text-gray-700 whitespace-nowrap">{vlanLabel(vm.vlan)}</td>
                  )}
                  {colShown("disk") && (
                    <td className="px-4 py-3 text-gray-700 whitespace-nowrap">
                      {vm.disk_gb > 0 ? `${vm.disk_gb} GB` : "—"}
                    </td>
                  )}
                  {colShown("datastore") && (
                    <td className="px-4 py-3 text-gray-500 max-w-[140px] truncate" title={vm.datastore ?? ""}>
                      {vm.datastore || "—"}
                    </td>
                  )}
                  {colShown("priority") && (
                  <td className="px-4 py-3 whitespace-nowrap">
                    {isAdmin ? (
                      <select
                        value={vm.priority ?? "default"}
                        disabled={priorityMutation.isPending}
                        onChange={(e) =>
                          priorityMutation.mutate({
                            id: vm.id,
                            priority: e.target.value as "critical" | "priority" | "default",
                          })
                        }
                        className={`text-xs rounded-md border px-2 py-1 font-medium focus:outline-none focus:ring-1 ${
                          (vm.priority ?? "default") === "critical"
                            ? "bg-red-50 border-red-300 text-red-800"
                            : (vm.priority ?? "default") === "priority"
                            ? "bg-amber-50 border-amber-300 text-amber-800"
                            : "bg-gray-50 border-gray-300 text-gray-600"
                        }`}
                        title="HA priority class (admin only)"
                      >
                        <option value="critical">critical</option>
                        <option value="priority">priority</option>
                        <option value="default">default</option>
                      </select>
                    ) : (
                      <PriorityBadge priority={vm.priority} />
                    )}
                  </td>
                  )}
                  {colShown("tenant") && (
                  <td className="px-4 py-3 whitespace-nowrap">
                    {vm.tenant_name ? (
                      <span
                        className={`inline-flex px-2 py-0.5 text-xs font-medium rounded-full ${
                          vm.tenant_id === SHARED_TENANT_ID
                            ? "bg-green-100 text-green-800"
                            : vm.tenant_id === PERSONAL_TENANT_ID
                            ? "bg-purple-100 text-purple-800"
                            : "bg-blue-100 text-blue-800"
                        }`}
                        title={`tenant_id=${vm.tenant_id}`}
                      >
                        {vm.tenant_name}
                      </span>
                    ) : (
                      <span className="text-xs text-gray-400" title="Untagged — admin-only visibility">
                        —
                      </span>
                    )}
                  </td>
                  )}
                  {colShown("expires") && (
                  <td className="px-4 py-3 whitespace-nowrap">
                    {expiresCell(vm)}
                  </td>
                  )}
                  <td className="px-4 py-3">
                    <ActionButtons
                      vm={vm}
                      busy={pendingId === vm.id}
                      onAction={(action, opts) => handleAction(vm, action, opts)}
                      onConsole={() => handleConsole(vm)}
                      onMigrate={() => setMigrateVmTarget(vm)}
                      onEdit={() => setEditVmTarget(vm)}
                      onDelete={() => setDeleteVmTarget(vm)}
                      onXml={() => setXmlVmTarget(vm)}
                      onRenew={() => renewMut.mutate(vm.id)}
                      onExempt={() => exemptMut.mutate({ id: vm.id, exempt: !vm.exempt })}
                      isAdmin={isAdmin}
                      userId={userId}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
