import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid,
} from "recharts";
import {
  ArrowLeft, Settings, FileCode, Terminal, Gauge, ExternalLink,
  Play, Square, RotateCcw, PauseCircle, PlayCircle, Zap,
} from "lucide-react";
import { getVM, editVm, requestConsole, vmAction } from "../api/vms";
import type { EditVmFields } from "../api/vms";
import { getNodeMetrics } from "../api/nodes";
import type { VM } from "../types/vm";
import { useAuth } from "../hooks/useAuth";
import { EditModal, XmlModal } from "./VMs";
import VncScreen from "../components/VncScreen";

type TabId = "overview" | "settings" | "xml" | "console";

const ramLabel = (mb: number) =>
  mb > 0 ? (mb % 1024 === 0 ? `${mb / 1024} GB` : `${(mb / 1024).toFixed(1)} GB`) : "—";

function StateBadge({ state }: { state: string }) {
  const color =
    state === "running"
      ? "bg-green-100 text-green-800"
      : state === "paused"
      ? "bg-amber-100 text-amber-800"
      : state === "shutoff" || state === "shut off"
      ? "bg-gray-100 text-gray-600"
      : "bg-gray-100 text-gray-600";
  return (
    <span className={`inline-flex px-2 py-0.5 text-xs font-medium rounded-full ${color}`}>
      {state}
    </span>
  );
}

// Power-action button for the detail-page actions bar. Labelled (not icon-only)
// since there's room here, unlike the compact VMs-list row.
function ActionBtn({
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
      className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md border transition-colors
        ${danger
          ? "border-red-200 bg-red-50 text-red-700 hover:bg-red-100"
          : "border-gray-300 bg-white text-gray-700 hover:bg-gray-50"}
        disabled:opacity-40 disabled:cursor-not-allowed`}
    >
      {icon}
      {label}
    </button>
  );
}

type DetailActionFn = (
  action: Parameters<typeof vmAction>[1],
  opts?: { force?: boolean },
) => void;

function ActionBar({ vm, busy, onAction }: {
  vm: VM; busy: boolean; onAction: DetailActionFn;
}) {
  // The detail VM.state is the normalized form ("stopped", not libvirt's
  // "shut off"). Treat paused/suspended equivalently for the resume control.
  const s = vm.state;
  const isRunning = s === "running";
  const isStopped = s === "stopped";
  const isPaused = s === "paused" || s === "suspended";
  const isCrashed = s === "crashed";

  let buttons: React.ReactNode = null;
  if (isRunning) {
    buttons = (
      <>
        <ActionBtn label="Stop" icon={<Square size={14} />} onClick={() => onAction("stop")} disabled={busy} />
        <ActionBtn label="Force off" icon={<Zap size={14} />} onClick={() => onAction("stop", { force: true })} disabled={busy} danger />
        <ActionBtn label="Reboot" icon={<RotateCcw size={14} />} onClick={() => onAction("reboot")} disabled={busy} />
        <ActionBtn label="Suspend" icon={<PauseCircle size={14} />} onClick={() => onAction("suspend")} disabled={busy} />
      </>
    );
  } else if (isStopped) {
    buttons = (
      <ActionBtn label="Start" icon={<Play size={14} />} onClick={() => onAction("start")} disabled={busy} />
    );
  } else if (isPaused) {
    buttons = (
      <>
        <ActionBtn label="Resume" icon={<PlayCircle size={14} />} onClick={() => onAction("resume")} disabled={busy} />
        <ActionBtn label="Force off" icon={<Zap size={14} />} onClick={() => onAction("stop", { force: true })} disabled={busy} danger />
      </>
    );
  } else if (isCrashed) {
    buttons = (
      <>
        <ActionBtn label="Start" icon={<Play size={14} />} onClick={() => onAction("start")} disabled={busy} />
        <ActionBtn label="Force off" icon={<Zap size={14} />} onClick={() => onAction("stop", { force: true })} disabled={busy} danger />
      </>
    );
  } else {
    // Transient states (stopping/migrating/…): no actions, just a hint.
    return (
      <div className="text-sm text-gray-400 italic">{s}…</div>
    );
  }

  return <div className="flex flex-wrap gap-2">{buttons}</div>;
}

export default function VMDetail() {
  const { id } = useParams<{ id: string }>();
  const vmId = Number(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { isAdmin, userId, tenants } = useAuth();

  const [tab, setTab] = useState<TabId>("overview");

  const { data: vm, isLoading, isError } = useQuery<VM>({
    queryKey: ["vm", vmId],
    queryFn: () => getVM(vmId),
    enabled: !Number.isNaN(vmId),
    refetchInterval: 5_000,
  });

  const editMutation = useMutation({
    mutationFn: (fields: EditVmFields) => editVm(vmId, fields),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["vm", vmId] });
      queryClient.invalidateQueries({ queryKey: ["vms"] });
    },
  });

  const [actionError, setActionError] = useState<string | null>(null);
  const actionMutation = useMutation({
    mutationFn: ({ action, opts }: {
      action: Parameters<typeof vmAction>[1];
      opts?: { force?: boolean };
    }) => vmAction(vmId, action, opts),
    onMutate: () => setActionError(null),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["vm", vmId] });
      queryClient.invalidateQueries({ queryKey: ["vms"] });
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { error?: string } } };
      setActionError(err?.response?.data?.error ?? "Action failed.");
    },
  });

  const runAction = (
    action: Parameters<typeof vmAction>[1],
    opts?: { force?: boolean },
  ) => {
    // Hard kill of a RUNNING VM is an abrupt power-cut (virsh destroy) that can
    // lose unsaved data / corrupt the guest FS — confirm before pulling power.
    if (opts?.force && vm?.state === "running") {
      if (!window.confirm(
        `Force off "${vm.name}"? This is an immediate hard power-off (no clean ` +
        `shutdown) and may cause data loss or filesystem corruption in the guest.`,
      )) {
        return;
      }
    }
    actionMutation.mutate({ action, opts });
  };

  if (isLoading) return <div className="p-6 text-gray-500">Loading…</div>;
  if (isError || !vm) {
    return (
      <div className="p-6">
        <button
          onClick={() => navigate("/vms")}
          className="inline-flex items-center gap-1 text-sm text-blue-600 hover:text-blue-800 mb-4"
        >
          <ArrowLeft size={15} /> Back to VMs
        </button>
        <p className="text-gray-500">VM not found, or you don't have access to it.</p>
      </div>
    );
  }

  // Non-admins can't open the XML tab (GET /xml is admin-only). If somehow
  // selected, fall back to overview.
  const tabs: { id: TabId; label: string; icon: React.ReactNode; adminOnly?: boolean }[] = [
    { id: "overview", label: "Overview", icon: <Gauge size={15} /> },
    { id: "settings", label: "Settings", icon: <Settings size={15} /> },
    { id: "xml", label: "XML", icon: <FileCode size={15} />, adminOnly: true },
    { id: "console", label: "Console", icon: <Terminal size={15} /> },
  ];
  const visibleTabs = tabs.filter((t) => !t.adminOnly || isAdmin);
  const activeTab = visibleTabs.some((t) => t.id === tab) ? tab : "overview";

  return (
    <div className="p-6 space-y-5 max-w-5xl">
      <button
        onClick={() => navigate("/vms")}
        className="inline-flex items-center gap-1 text-sm text-blue-600 hover:text-blue-800"
      >
        <ArrowLeft size={15} /> Back to VMs
      </button>

      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold">{vm.name}</h1>
        <StateBadge state={vm.state} />
        {vm.tenant_name && (
          <span className="text-sm text-gray-400">· {vm.tenant_name}</span>
        )}
      </div>

      {/* Power actions bar — mirrors the VMs-list controls, state-driven. */}
      <ActionBar vm={vm} busy={actionMutation.isPending} onAction={runAction} />

      {actionError && (
        <div className="flex justify-between items-start rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
          <span>{actionError}</span>
          <button
            onClick={() => setActionError(null)}
            className="ml-3 text-red-400 hover:text-red-600"
            aria-label="Dismiss"
          >
            ✕
          </button>
        </div>
      )}

      {/* Tabs */}
      <div className="border-b border-gray-200 flex gap-1">
        {visibleTabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 -mb-px ${
              activeTab === t.id
                ? "border-blue-600 text-blue-700"
                : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>

      {activeTab === "overview" && <OverviewTab vm={vm} />}

      {activeTab === "settings" && (
        <EditModal
          embedded
          vm={vm}
          isAdmin={isAdmin}
          userId={userId}
          userTenants={tenants}
          busy={editMutation.isPending}
          onConfirm={(fields) => editMutation.mutate(fields)}
          onClose={() => {}}
        />
      )}

      {activeTab === "xml" && isAdmin && <XmlModal embedded vm={vm} />}

      {activeTab === "console" && <ConsoleTab vm={vm} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Overview: static specs + host-node live metrics graph (context).
// ---------------------------------------------------------------------------
function OverviewTab({ vm }: { vm: VM }) {
  const { data: metrics } = useQuery({
    queryKey: ["nodeMetrics", vm.node_id],
    queryFn: () => getNodeMetrics(vm.node_id, 3600),
    refetchInterval: 30_000,
    enabled: !!vm.node_id,
  });

  const cpuSeries = (metrics?.samples ?? []).map((s) => ({
    t: new Date(s.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    cpu: Math.round(s.cpu),
    ram: s.ram_total_mb > 0 ? Math.round((s.ram_used_mb / s.ram_total_mb) * 100) : 0,
  }));

  const specs: [string, React.ReactNode][] = [
    ["State", <StateBadge key="s" state={vm.state} />],
    ["vCPUs", vm.vcpus || "—"],
    ["RAM", ramLabel(vm.ram_mb)],
    ["Disk", vm.disk_gb > 0 ? `${vm.disk_gb} GB` : "—"],
    ["IP", vm.ip || "—"],
    ["Network", vm.vlan || "—"],
    ["Host", vm.node_hostname || "—"],
    ["Datastore", vm.datastore || "—"],
    ["Tenant", vm.tenant_name || "—"],
    ["UUID", <span key="u" className="font-mono text-xs">{vm.uuid || "—"}</span>],
  ];

  return (
    <div className="space-y-5">
      <div className="bg-white rounded-xl shadow p-6">
        <h2 className="text-sm font-semibold text-gray-700 mb-4">Specifications</h2>
        <dl className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-4">
          {specs.map(([label, value]) => (
            <div key={label}>
              <dt className="text-xs uppercase tracking-wide text-gray-400">{label}</dt>
              <dd className="text-sm text-gray-800 mt-0.5">{value}</dd>
            </div>
          ))}
        </dl>
      </div>

      <div className="bg-white rounded-xl shadow p-6">
        <div className="flex items-baseline justify-between mb-1">
          <h2 className="text-sm font-semibold text-gray-700">Host utilisation</h2>
          <span className="text-xs text-gray-400">
            {vm.node_hostname} · last hour
          </span>
        </div>
        <p className="text-xs text-gray-400 mb-4">
          Per-VM metrics aren't collected yet — this shows the utilisation of the
          host node <span className="font-medium">{vm.node_hostname}</span> this VM
          runs on, for context.
        </p>
        {cpuSeries.length === 0 ? (
          <div className="text-sm text-gray-400 italic py-8 text-center">
            No metrics available (host may be offline).
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={cpuSeries} margin={{ left: -10, right: 10, top: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="t" tick={{ fontSize: 11 }} minTickGap={40} />
              <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} unit="%" />
              <Tooltip />
              <Line type="monotone" dataKey="cpu" name="CPU %" stroke="#2563eb" dot={false} strokeWidth={2} />
              <Line type="monotone" dataKey="ram" name="RAM %" stroke="#16a34a" dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Console: embedded noVNC + pop-out. Requests a fresh one-shot token on mount.
// ---------------------------------------------------------------------------
function ConsoleTab({ vm }: { vm: VM }) {
  const [token, setToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setToken(null);
    setError(null);
    requestConsole(vm.id, "vnc")
      .then((r) => !cancelled && setToken(r.token))
      .catch((e) =>
        !cancelled && setError(e?.response?.data?.error ?? "Failed to open console."),
      );
    return () => { cancelled = true; };
  }, [vm.id]);

  const popOut = () => {
    requestConsole(vm.id, "vnc").then((r) => {
      window.open(
        `/console.html?token=${encodeURIComponent(r.token)}&vm=${encodeURIComponent(vm.name)}`,
        "_blank",
        "noopener,noreferrer",
      );
    });
  };

  if (vm.state !== "running") {
    return (
      <div className="bg-white rounded-xl shadow p-8 text-center text-gray-500">
        The console is available when the VM is running (current state:{" "}
        <span className="font-medium">{vm.state}</span>).
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <button
          onClick={popOut}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm border border-gray-300 rounded-md text-gray-700 hover:bg-gray-50"
        >
          <ExternalLink size={14} /> Pop out
        </button>
      </div>
      <div
        className="rounded-xl overflow-hidden shadow border border-gray-800"
        style={{ height: "70vh" }}
      >
        {error ? (
          <div className="h-full flex items-center justify-center bg-gray-900 text-red-300 text-sm">
            {error}
          </div>
        ) : token ? (
          <VncScreen token={token} vmName={vm.name} embedded />
        ) : (
          <div className="h-full flex items-center justify-center bg-gray-900 text-gray-400 text-sm">
            Opening console…
          </div>
        )}
      </div>
    </div>
  );
}
