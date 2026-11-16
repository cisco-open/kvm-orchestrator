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

// Shared VM table (v0.35.7): search, admin column picker (localStorage
// persisted), sortable headers, admin bulk tenant select, row ActionButtons and
// the per-VM modals. Extracted unchanged from pages/VMs.tsx; used by the VMs
// page and, pre-filtered, by the node detail page. Both read the ["vms"] query,
// so every mutation's invalidation refreshes both.
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Monitor, X, Search, ChevronUp, ChevronDown, Columns3 } from "lucide-react";
import { useState, useEffect, useMemo, useRef } from "react";
import { listVMs, vmAction, requestConsole, migrateVm, editVm, deleteVm, setVmPriority, renewVm, setVmExempt } from "../api/vms";
import type { EditVmFields } from "../api/vms";
import { listNodes } from "../api/nodes";
import type { VM } from "../types/vm";
import type { Node } from "../types/node";
import { useAuth } from "../hooks/useAuth";
import BulkAssignVmTenantModal from "./BulkAssignVmTenantModal";
import { StateBadge, PriorityBadge, expiresCell, vlanLabel, ramLabel, SHARED_TENANT_ID, PERSONAL_TENANT_ID, MigrateModal, EditModal, DeleteModal, XmlModal, ActionButtons } from "./vmParts";


// Column registry for the VM table. `Name`, `State` and `Actions` are always
// rendered (essential) and are NOT in this list — everything here is
// admin-toggleable via the "Columns" picker. `adminOnly` columns are the infra
// ones that non-admins never see at all (Host/Datastore), so they only appear
// in the picker for admins anyway. Order here matches the on-screen order
// (between State and Priority/Tenant/Actions).
export type VmColumnId =
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

// Every column is sortable. Returns a comparable value for a VM in a given
// column: numbers sort numerically, strings case-insensitively. Unset values
// sort to the end (ip/expires use +Infinity; empty strings handled in the
// comparator). Priority sorts by severity (critical < priority < default).
const PRIORITY_RANK: Record<string, number> = { critical: 0, priority: 1, default: 2 };
function ipToNum(ip: string | null): number {
  if (!ip) return Number.POSITIVE_INFINITY;
  const parts = ip.split(".");
  if (parts.length !== 4) return Number.POSITIVE_INFINITY;
  let n = 0;
  for (const part of parts) {
    const o = Number(part);
    if (Number.isNaN(o)) return Number.POSITIVE_INFINITY;
    n = n * 256 + o;
  }
  return n;
}
function vmSortValue(vm: VM, col: string): number | string {
  switch (col) {
    case "name":      return (vm.name ?? "").toLowerCase();
    case "state":     return vm.state ?? "";
    case "host":      return (vm.node_hostname ?? "").toLowerCase();
    case "ip":        return ipToNum(vm.ip);
    case "vcpus":     return vm.vcpus ?? 0;
    case "ram":       return vm.ram_mb ?? 0;
    case "network":   return (vm.vlan ?? "").toLowerCase();
    case "disk":      return vm.disk_gb ?? 0;
    case "datastore": return (vm.datastore ?? "").toLowerCase();
    case "priority":  return PRIORITY_RANK[vm.priority ?? "default"] ?? 2;
    case "tenant":    return (vm.tenant_name ?? "").toLowerCase();
    case "expires":   return vm.exempt || !vm.expires_at
                               ? Number.POSITIVE_INFINITY
                               : new Date(vm.expires_at).getTime();
    default:          return "";
  }
}

// Human label for the active sort column (Name/State aren't in VM_COLUMNS).
function sortColLabel(id: string): string {
  if (id === "name") return "Name";
  if (id === "state") return "State";
  return VM_COLUMNS.find((c) => c.id === id)?.label ?? id;
}


const NO_COLUMNS: VmColumnId[] = [];

export interface VmTableProps {
  /** Pre-filter applied before search/sort (e.g. vm.node_id === nodeId). */
  filter?: (vm: VM) => boolean;
  /** Columns removed entirely for this instance (header, rows and picker). */
  omitColumns?: VmColumnId[];
  /** Handle the Webex `?renew=<id>` deep-link (VMs page only). */
  renewDeepLink?: boolean;
  /** Message when the (filtered) list is empty. */
  emptyText?: string;
  /** Optional lifted error banner state (VMs page header shares it). */
  actionError?: string | null;
  setActionError?: (msg: string | null) => void;
}

export default function VmTable({
  filter,
  omitColumns = NO_COLUMNS,
  renewDeepLink = false,
  emptyText = "No VMs discovered yet — sync runs every 30 s.",
  actionError: actionErrorProp,
  setActionError: setActionErrorProp,
}: VmTableProps) {
  const queryClient = useQueryClient();
  // `tenants` is the user's AD-mapped membership list from /api/auth/me
  // (excludes SHARED + PERSONAL — those are always available). Threaded into
  // CreateModal + EditModal so non-admins can pick any tenant they belong to.
  const { isAdmin, userId, tenants }          = useAuth();
  const navigate                              = useNavigate();
  const [pendingId, setPendingId]             = useState<number | null>(null);
  const [ownActionError, setOwnActionError]   = useState<string | null>(null);
  // The VMs page lifts this so its header controls share the same banner.
  const actionError    = setActionErrorProp ? (actionErrorProp ?? null) : ownActionError;
  const setActionError = setActionErrorProp ?? setOwnActionError;
  const [migrateVmTarget, setMigrateVmTarget] = useState<VM | null>(null);
  const [migrating, setMigrating]             = useState(false);
  const [editVmTarget, setEditVmTarget]       = useState<VM | null>(null);
  const [editing, setEditing]                 = useState(false);
  const [deleteVmTarget, setDeleteVmTarget]   = useState<VM | null>(null);
  const [deleting, setDeleting]               = useState(false);
  // v0.22.3 — raw libvirt XML editor (admin only, opened from ActionButtons)
  const [xmlVmTarget, setXmlVmTarget]         = useState<VM | null>(null);
  // v0.12.0: list usability — case-insensitive substring search across
  // VM name + IP, plus click-to-sort on Host and Datastore columns.
  // State is in-memory only (resets on navigation); URL/localStorage
  // persistence would be the next step if users ask for it.
  const [search, setSearch]                   = useState("");
  const [sortBy, setSortBy]                   = useState<string | null>(null);
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
        (c) => (isAdmin || !c.adminOnly) && !hiddenCols.has(c.id) && !omitColumns.includes(c.id),
      ),
    [isAdmin, hiddenCols, omitColumns],
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


  // Webex renew deep-link: a lifecycle warning card links to
  // /vms?renew=<vm_id>. When the page opens with that param, confirm and fire
  // the renew for that VM, then strip the param so a refresh doesn't repeat it.
  const renewDeepLinkHandled = useRef(false);
  useEffect(() => {
    if (!renewDeepLink || renewDeepLinkHandled.current) return;
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
  }, [vms, renewMut, renewDeepLink]);

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

  // Optional pre-filter (e.g. the node detail page: VMs on this node only).
  const vmList = useMemo(() => (filter ? (vms ?? []).filter(filter) : vms ?? []), [vms, filter]);

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
      list = [...list].sort((a, b) => {
        const av = vmSortValue(a, sortBy);
        const bv = vmSortValue(b, sortBy);
        let cmp: number;
        if (typeof av === "number" && typeof bv === "number") {
          cmp = av === bv ? 0 : av < bv ? -1 : 1;
        } else {
          // String compare; empty/unset values cluster at the end.
          const as = String(av);
          const bs = String(bv);
          if (as === "" && bs === "") cmp = 0;
          else if (as === "") cmp = 1;
          else if (bs === "") cmp = -1;
          else cmp = as.localeCompare(bs);
        }
        return sortDir === "asc" ? cmp : -cmp;
      });
    }
    return list;
  }, [vmList, search, sortBy, sortDir]);

  // Three-state sort cycle: unsorted → asc → desc → unsorted. Switching
  // columns resets to asc on the new column.
  const handleSortClick = (col: string) => {
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
    <div>
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
                <> · sorted by <span className="font-medium">{sortColLabel(sortBy)}</span> ({sortDir})</>
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
                  {VM_COLUMNS.filter((c) => (isAdmin || !c.adminOnly) && !omitColumns.includes(c.id)).map((c) => (
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
          <p className="text-gray-500">{emptyText}</p>
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
                {([{ id: "name", label: "Name" }, { id: "state", label: "State" }] as const).map((col) => {
                  const isActive = sortBy === col.id;
                  return (
                    <th
                      key={col.id}
                      onClick={() => handleSortClick(col.id)}
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
                {visibleCols.map((col) => {
                  const isActive = sortBy === col.id;
                  return (
                    <th
                      key={col.id}
                      onClick={() => handleSortClick(col.id)}
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
