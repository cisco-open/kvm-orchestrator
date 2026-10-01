import { useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Key, Trash2, X, Server, Tags, Ban, CheckCircle2, Zap, LifeBuoy } from "lucide-react";
import {
  listNodes,
  addNode,
  deleteNode,
  setNodeSchedulable,
  listSshKeys,
  addSshKey,
  getNodeMetrics,
} from "../api/nodes";
import { failoverNode, readmitNode, readmitCheck } from "../api/ha";
import { listTenants, type Tenant } from "../api/tenants";
import { useAuth } from "../hooks/useAuth";
import { Sparkline, type SparklinePoint } from "../components/Sparkline";
import EditNodeTenantsModal from "../components/EditNodeTenantsModal";
import HaControlPanel from "../components/HaControlPanel";
import type { Node, SshKey } from "../types/node";

// ---------------------------------------------------------------------------
// Shared modal wrapper — rendered into document.body via portal so no parent
// CSS (overflow, stacking context, etc.) can ever clip or hide it.
// ---------------------------------------------------------------------------
function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg mx-4">
        <div className="flex items-center justify-between px-6 py-4 border-b">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X size={20} />
          </button>
        </div>
        <div className="px-6 py-5">{children}</div>
      </div>
    </div>,
    document.body
  );
}

// ---------------------------------------------------------------------------
// SSH Keys modal — list stored keys + add new
// ---------------------------------------------------------------------------
function SshKeysModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [error, setError] = useState("");

  const { data: keys = [], isLoading, isError: keysError } = useQuery<SshKey[]>({
    queryKey: ["ssh-keys"],
    queryFn: listSshKeys,
    retry: false,
  });

  const mutation = useMutation({
    mutationFn: () => addSshKey(name, privateKey),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ssh-keys"] });
      setName("");
      setPrivateKey("");
      setError("");
    },
    onError: (e: any) => setError(e.response?.data?.error ?? "Failed to save key"),
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    mutation.mutate();
  };

  return (
    <Modal title="SSH Keys" onClose={onClose}>
      {/* Stored keys */}
      <div className="mb-6">
        <h3 className="text-sm font-medium text-gray-700 mb-2">Stored keys</h3>
        {isLoading ? (
          <p className="text-sm text-gray-500">Loading...</p>
        ) : keysError ? (
          <p className="text-sm text-red-600">Failed to load keys. You may not have admin access.</p>
        ) : keys.length === 0 ? (
          <p className="text-sm text-gray-500">No keys stored yet.</p>
        ) : (
          <ul className="divide-y border rounded-lg">
            {keys.map((key) => (
              <li key={key.id} className="flex items-center justify-between px-4 py-2">
                <span className="text-sm font-medium">{key.name}</span>
                <span className="text-xs text-gray-400">
                  {new Date(key.created_at).toLocaleDateString()}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Add new key */}
      <form onSubmit={handleSubmit} className="space-y-3">
        <h3 className="text-sm font-medium text-gray-700">Add new key</h3>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <input
          type="text"
          placeholder="Key name (e.g. my-ssh-key)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <textarea
          placeholder="Paste private key (PEM format)"
          value={privateKey}
          onChange={(e) => setPrivateKey(e.target.value)}
          required
          rows={6}
          className="w-full border rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <button
          type="submit"
          disabled={mutation.isPending}
          className="w-full bg-blue-600 text-white rounded-lg px-4 py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
        >
          {mutation.isPending ? "Saving..." : "Save Key"}
        </button>
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Add Node modal
// ---------------------------------------------------------------------------
function AddNodeModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({
    hostname: "",
    ip_address: "",
    ssh_user: "root",
    ssh_key_id: "",
    ssh_host: "",
    ssh_port: "22",
  });
  // v0.20 — pre-tick DEFAULT (id=4) by default so a freshly-added node is
  // in the shared placement fallback pool immediately and usable by non-
  // admins. Admin can uncheck if they want the node reserved for admin-
  // only use or for a specific tenant only.
  const [tenantIds, setTenantIds] = useState<Set<number>>(new Set([4]));
  const [error, setError] = useState("");

  const { data: keys = [] } = useQuery<SshKey[]>({
    queryKey: ["ssh-keys"],
    queryFn: listSshKeys,
    retry: false,
  });

  const { data: tenants = [] } = useQuery<Tenant[]>({
    queryKey: ["tenants"],
    queryFn: listTenants,
    retry: false,
  });

  const mutation = useMutation({
    mutationFn: () =>
      addNode({
        hostname: form.hostname,
        ip_address: form.ip_address,
        ssh_user: form.ssh_user,
        ssh_key_id: Number(form.ssh_key_id),
        ssh_host: form.ssh_host || undefined,
        ssh_port: Number(form.ssh_port) || 22,
        tenant_ids: Array.from(tenantIds),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["nodes"] });
      onClose();
    },
    onError: (e: any) => setError(e.response?.data?.error ?? "Failed to add node"),
  });

  const set = (field: string, value: string) =>
    setForm((f) => ({ ...f, [field]: value }));

  const toggleTenant = (id: number) => {
    setTenantIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    mutation.mutate();
  };

  // Hide SHARED (id=1) and PERSONAL/ODaaSE (id=3) — those are VM-only
  // concepts. DEFAULT (id=4) and every user-created tenant are valid
  // node targets. (id=2 no longer exists post-v0.21 merge — the old
  // "Personal VM" was folded into id=3.)
  const nodeAssignableTenants = tenants.filter((t) => t.id !== 1 && t.id !== 3);

  return (
    <Modal title="Add Node" onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <p className="text-sm text-red-600">{error}</p>}

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">
              Hostname <span className="text-gray-400">(optional — auto-detected)</span>
            </label>
            <input
              type="text"
              value={form.hostname}
              onChange={(e) => set("hostname", e.target.value)}
              placeholder="Auto-detected from host"
              className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">IP Address</label>
            <input
              type="text"
              value={form.ip_address}
              onChange={(e) => set("ip_address", e.target.value)}
              required
              placeholder="10.0.0.50"
              className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">
              SSH Host <span className="text-gray-400">(optional)</span>
            </label>
            <input
              type="text"
              value={form.ssh_host}
              onChange={(e) => set("ssh_host", e.target.value)}
              placeholder="Defaults to IP address"
              className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">SSH Port</label>
            <input
              type="number"
              value={form.ssh_port}
              onChange={(e) => set("ssh_port", e.target.value)}
              className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">SSH User</label>
          <input
            type="text"
            value={form.ssh_user}
            onChange={(e) => set("ssh_user", e.target.value)}
            required
            className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">SSH Key</label>
          {keys.length === 0 ? (
            <p className="text-sm text-amber-600">
              No SSH keys stored. Add one via "SSH Keys" first.
            </p>
          ) : (
            <select
              value={form.ssh_key_id}
              onChange={(e) => set("ssh_key_id", e.target.value)}
              required
              className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">Select a key...</option>
              {keys.map((k) => (
                <option key={k.id} value={k.id}>
                  {k.name}
                </option>
              ))}
            </select>
          )}
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">
            Tenants
            <span className="ml-1 text-gray-400 font-normal">
              — which tenants may see and place VMs on this node
            </span>
          </label>
          <div className="border rounded-lg divide-y max-h-40 overflow-y-auto">
            {nodeAssignableTenants.map((t) => (
              <label
                key={t.id}
                className="flex items-center gap-2 px-3 py-2 text-sm cursor-pointer hover:bg-gray-50"
              >
                <input
                  type="checkbox"
                  checked={tenantIds.has(t.id)}
                  onChange={() => toggleTenant(t.id)}
                />
                <span className="font-medium">{t.name}</span>
                {t.id === 3 && (
                  <span className="text-xs text-gray-500">(shared host pool — visible to all users)</span>
                )}
              </label>
            ))}
          </div>
          {tenantIds.size === 0 && (
            <p className="mt-1 text-xs text-amber-600">
              No tenants selected — this node will be admin-only.
            </p>
          )}
        </div>

        <button
          type="submit"
          disabled={mutation.isPending || keys.length === 0}
          className="w-full bg-blue-600 text-white rounded-lg px-4 py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
        >
          {mutation.isPending ? "Adding..." : "Add Node"}
        </button>
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Per-node CPU + RAM sparklines (1-hour window, polled every 30 s).
// Same query key is shared across both cells so we only fetch once per node.
// ---------------------------------------------------------------------------
function useNodeMetrics(nodeId: number, enabled: boolean) {
  return useQuery({
    queryKey: ["nodeMetrics", nodeId],
    queryFn: () => getNodeMetrics(nodeId, 3600),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    enabled,
  });
}

function CpuSparklineCell({ node }: { node: Node }) {
  const enabled = node.status === "online";
  const { data, isLoading } = useNodeMetrics(node.id, enabled);
  if (!enabled) return <span className="text-xs text-gray-400">offline</span>;
  if (isLoading) return <span className="text-xs text-gray-400">…</span>;
  const points: SparklinePoint[] = (data?.samples ?? []).map((s) => ({ t: s.t, v: s.cpu }));
  return (
    <Sparkline
      data={points}
      width={160}
      height={36}
      yMin={0}
      yMax={100}
      color="#2563eb"
      fill="#3b82f6"
      unit="%"
      decimals={0}
      title="CPU % (last hour)"
    />
  );
}

function MemSparklineCell({ node }: { node: Node }) {
  const enabled = node.status === "online";
  const { data, isLoading } = useNodeMetrics(node.id, enabled);
  if (!enabled) return <span className="text-xs text-gray-400">offline</span>;
  if (isLoading) return <span className="text-xs text-gray-400">…</span>;
  const samples = data?.samples ?? [];
  const points: SparklinePoint[] = samples.map((s) => ({
    t: s.t,
    v: s.ram_total_mb > 0 ? (s.ram_used_mb / s.ram_total_mb) * 100 : 0,
  }));
  return (
    <Sparkline
      data={points}
      width={160}
      height={36}
      yMin={0}
      yMax={100}
      color="#16a34a"
      fill="#22c55e"
      unit="%"
      decimals={0}
      title="Memory % (last hour)"
    />
  );
}

// ---------------------------------------------------------------------------
// Main Nodes page
// ---------------------------------------------------------------------------
export default function Nodes() {
  const queryClient = useQueryClient();
  const { isAdmin } = useAuth();
  const [showSshKeys, setShowSshKeys] = useState(false);
  const [showAddNode, setShowAddNode] = useState(false);
  const [editNode, setEditNode] = useState<Node | null>(null);

  const openSshKeys = () => setShowSshKeys(true);
  const openAddNode = () => setShowAddNode(true);

  const { data: nodes = [], isLoading } = useQuery<Node[]>({
    queryKey: ["nodes"],
    queryFn: listNodes,
    refetchInterval: 30_000,
  });

  const deleteMutation = useMutation({
    mutationFn: deleteNode,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["nodes"] }),
  });

  const scheduleMutation = useMutation({
    mutationFn: ({ id, schedulable }: { id: number; schedulable: boolean }) =>
      setNodeSchedulable(id, schedulable),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["nodes"] }),
    onError: (e: any) =>
      window.alert(e.response?.data?.error ?? "Failed to update node"),
  });

  const failoverMutation = useMutation({
    mutationFn: failoverNode,
    onSuccess: (r: any) => {
      queryClient.invalidateQueries({ queryKey: ["nodes"] });
      queryClient.invalidateQueries({ queryKey: ["ha-events"] });
      window.alert(
        `Failover complete: ${r?.relocated?.length ?? 0} relocated, ` +
          `${r?.failed?.length ?? 0} failed.`,
      );
    },
    onError: (e: any) =>
      window.alert(e.response?.data?.error ?? "Failover failed"),
  });

  const readmitMutation = useMutation({
    mutationFn: readmitNode,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["nodes"] });
      queryClient.invalidateQueries({ queryKey: ["ha-events"] });
      window.alert("Node readmitted.");
    },
    onError: (e: any) =>
      window.alert(e.response?.data?.error ?? "Readmit failed"),
  });

  const handleFailover = (node: Node) => {
    if (
      window.confirm(
        `Force failover of ${node.hostname}? It will be Ceph-fenced and its ` +
          `VMs relocated to survivors. Requires HA to be armed.`,
      )
    ) {
      failoverMutation.mutate(node.id);
    }
  };

  const handleReadmit = async (node: Node) => {
    // Pre-flight safety check so the admin KNOWS whether it's safe before the
    // destructive readmit runs.
    let check;
    try {
      check = await readmitCheck(node.id);
    } catch (e: any) {
      window.alert(e.response?.data?.error ?? "Could not run readmit safety check");
      return;
    }

    const lines = (check.reasons || []).map((r) => `• ${r}`).join("\n");
    const header =
      check.verdict === "safe"
        ? "✅ SAFE to readmit"
        : check.verdict === "caution"
        ? "⚠️ CAUTION"
        : "⛔ UNSAFE — do not readmit";

    if (check.verdict === "unsafe") {
      window.alert(
        `${header}\n\n${node.hostname}\n\n${lines}\n\n` +
          "Readmit is blocked. Resolve the split-brain / running-stale condition first.",
      );
      return;
    }

    const detail =
      `${header}\n\n${node.hostname}\n\n${lines}\n\n` +
      (check.reachable ? "" : "Host not reachable yet — readmit may not fully clean up.\n\n") +
      "Proceed with readmit? Stale relocated domains will be destroyed, then the " +
      "node un-fenced and marked online.";

    if (window.confirm(detail)) {
      readmitMutation.mutate(node.id);
    }
  };

  const handleToggleSchedulable = (node: Node) => {
    const isSchedulable = node.schedulable === true || node.schedulable === 1;
    const next = !isSchedulable; // cordoned → uncordon, schedulable → cordon
    if (
      window.confirm(
        next
          ? `Uncordon ${node.hostname}? It will start accepting new VMs again.`
          : `Cordon ${node.hostname}? No new VMs will be created or migrated onto it. Existing VMs keep running.`,
      )
    ) {
      scheduleMutation.mutate({ id: node.id, schedulable: next });
    }
  };

  const handleDelete = (node: Node) => {
    if (window.confirm(`Remove ${node.hostname} from management?`)) {
      deleteMutation.mutate(node.id);
    }
  };

  return (
    <div className="p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Compute Nodes</h1>
        <div className="flex gap-2">
          {isAdmin && (
            <>
              <button
                type="button"
                onClick={openSshKeys}
                className="flex items-center gap-2 border rounded-lg px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
              >
                <Key size={16} />
                SSH Keys
              </button>
              <button
                type="button"
                onClick={openAddNode}
                className="flex items-center gap-2 bg-blue-600 text-white rounded-lg px-3 py-2 text-sm font-medium hover:bg-blue-700"
              >
                <Plus size={16} />
                Add Node
              </button>
            </>
          )}
        </div>
      </div>

      {/* Node list */}
      {isAdmin && <HaControlPanel />}
      {isLoading ? (
        <p className="text-gray-500">Loading...</p>
      ) : nodes.length === 0 ? (
        <div className="text-center py-16 bg-white rounded-xl shadow">
          <Server size={40} className="mx-auto text-gray-300 mb-3" />
          <p className="text-gray-500">No nodes registered yet.</p>
          <button
            onClick={openAddNode}
            className="mt-4 text-sm text-blue-600 hover:underline"
          >
            Add your first node
          </button>
        </div>
      ) : (
        <div className="bg-white rounded-xl shadow overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Hostname</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">IP</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Processor</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">vCPUs</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">RAM</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Tenants</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">CPU %</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Memory</th>
                <th className="px-6 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {nodes.map((node) => (
                <tr key={node.id} className="hover:bg-gray-50">
                  <td className="px-6 py-4 text-sm font-medium">
                    <Link
                      to={`/nodes/${node.id}`}
                      className="text-blue-600 hover:underline"
                    >
                      {node.hostname}
                    </Link>
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-500">{node.ip_address}</td>
                  <td className="px-6 py-4 text-sm text-gray-700">
                    {node.cpu_model ?? <span className="text-gray-400">—</span>}
                  </td>
                  <td className="px-6 py-4">
                    <span
                      className={`inline-flex px-2 py-1 text-xs rounded-full font-medium ${
                        node.status === "online"
                          ? "bg-green-100 text-green-800"
                          : node.status === "maintenance"
                          ? "bg-yellow-100 text-yellow-800"
                          : node.status === "fenced"
                          ? "bg-orange-100 text-orange-800"
                          : "bg-gray-100 text-gray-600"
                      }`}
                    >
                      {node.status}
                    </span>
                    {!(node.schedulable === true || node.schedulable === 1) && (
                      <span
                        className="ml-1 inline-flex px-2 py-1 text-xs rounded-full font-medium bg-red-100 text-red-800"
                        title="Cordoned — accepts no new VMs"
                      >
                        cordoned
                      </span>
                    )}
                  </td>
                  <td className="px-6 py-4 text-sm">
                    {node.available_vcpus}/{node.total_vcpus}
                  </td>
                  <td className="px-6 py-4 text-sm">
                    {Math.round(node.available_ram_mb / 1024)}/{Math.round(node.total_ram_mb / 1024)} GB
                  </td>
                  <td className="px-6 py-4">
                    {node.tenants.length === 0 ? (
                      <span className="text-xs italic text-gray-400">admin-only</span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {node.tenants.map((t) => (
                          <span
                            key={t.id}
                            className={`inline-flex px-2 py-0.5 text-xs rounded-full font-medium ${
                              t.id === 3
                                ? "bg-purple-100 text-purple-800"
                                : "bg-blue-100 text-blue-800"
                            }`}
                            title={t.id === 3 ? "Shared host pool — visible to all users" : t.name}
                          >
                            {t.name}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="px-6 py-4">
                    <CpuSparklineCell node={node} />
                  </td>
                  <td className="px-6 py-4">
                    <MemSparklineCell node={node} />
                  </td>
                  <td className="px-6 py-4 text-right">
                    {isAdmin && (
                      <div className="flex items-center justify-end gap-2">
                        {(node.schedulable === true || node.schedulable === 1) ? (
                          <button
                            onClick={() => handleToggleSchedulable(node)}
                            disabled={scheduleMutation.isPending}
                            className="text-gray-400 hover:text-red-600 disabled:opacity-40"
                            title="Cordon — stop accepting new VMs"
                          >
                            <Ban size={16} />
                          </button>
                        ) : (
                          <button
                            onClick={() => handleToggleSchedulable(node)}
                            disabled={scheduleMutation.isPending}
                            className="text-red-500 hover:text-green-600 disabled:opacity-40"
                            title="Uncordon — allow new VMs"
                          >
                            <CheckCircle2 size={16} />
                          </button>
                        )}
                        <button
                          onClick={() => setEditNode(node)}
                          className="text-gray-400 hover:text-blue-600"
                          title="Edit tenants"
                        >
                          <Tags size={16} />
                        </button>
                        {node.status === "fenced" ? (
                          <button
                            onClick={() => handleReadmit(node)}
                            disabled={readmitMutation.isPending}
                            className="text-orange-500 hover:text-green-600 disabled:opacity-40"
                            title="Readmit fenced node (guarded recovery)"
                          >
                            <LifeBuoy size={16} />
                          </button>
                        ) : (
                          <button
                            onClick={() => handleFailover(node)}
                            disabled={failoverMutation.isPending}
                            className="text-gray-400 hover:text-orange-600 disabled:opacity-40"
                            title="Force failover (fence + relocate VMs)"
                          >
                            <Zap size={16} />
                          </button>
                        )}
                        <button
                          onClick={() => handleDelete(node)}
                          className="text-gray-400 hover:text-red-600"
                          title="Remove node"
                        >
                          <Trash2 size={16} />
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showSshKeys && <SshKeysModal onClose={() => setShowSshKeys(false)} />}
      {showAddNode && <AddNodeModal onClose={() => setShowAddNode(false)} />}
      {editNode && (
        <EditNodeTenantsModal
          node={editNode}
          onClose={() => setEditNode(null)}
        />
      )}
    </div>
  );
}
