import { useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Server, Tags } from "lucide-react";
import { listVMs } from "../api/vms";
import { listNodes, getNodeResources, type NodeResources } from "../api/nodes";
import { useAuth } from "../hooks/useAuth";
import EditNodeTenantsModal from "../components/EditNodeTenantsModal";
import type { VM } from "../types/vm";
import type { Node } from "../types/node";

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

function vlanLabel(raw: string | null): string {
  if (!raw) return "Untagged";
  const m = raw.match(/(\d+)$/);
  return m ? `VLAN ${m[1]}` : raw;
}

function ramLabel(mb: number): string {
  if (mb === 0) return "0 MB";
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`;
}

// ---------------------------------------------------------------------------
// ResourceBar
// ---------------------------------------------------------------------------

function ResourceBar({
  label,
  used,
  total,
  formatValue,
}: {
  label: string;
  used: number;
  total: number;
  formatValue: (n: number) => string;
}) {
  const pct = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0;
  const color =
    pct >= 90 ? "bg-red-500" : pct >= 70 ? "bg-amber-500" : "bg-blue-500";

  return (
    <div>
      <div className="flex justify-between text-sm mb-1.5">
        <span className="font-medium text-gray-700">{label}</span>
        <span className="text-gray-500">
          {formatValue(used)} / {formatValue(total)}{" "}
          <span className="text-gray-400">({pct}%)</span>
        </span>
      </div>
      <div className="w-full bg-gray-200 rounded-full h-2.5">
        <div
          className={`h-2.5 rounded-full transition-all ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// NodeDetail page
// ---------------------------------------------------------------------------

export default function NodeDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { isAdmin } = useAuth();
  const nodeId = Number(id);
  const [editTenants, setEditTenants] = useState(false);

  const { data: nodes = [], isLoading: nodesLoading } = useQuery<Node[]>({
    queryKey: ["nodes"],
    queryFn: listNodes,
  });

  const { data: vms = [], isLoading: vmsLoading } = useQuery<VM[]>({
    queryKey: ["vms"],
    queryFn: () => listVMs(),
    refetchInterval: 5_000,
  });

  // Live storage-pool discovery for this node (SSH-based, so it may be
  // slow / fail if the node is offline). Shown in the "Storage pools"
  // panel so pools appear even on a node that has no VMs yet.
  const { data: resources } = useQuery<NodeResources>({
    queryKey: ["node-resources", nodeId],
    queryFn: () => getNodeResources(nodeId),
    enabled: !Number.isNaN(nodeId),
    retry: false,
    staleTime: 30_000,
  });

  const node = useMemo(() => nodes.find((n) => n.id === nodeId), [nodes, nodeId]);
  const nodeVms = useMemo(() => vms.filter((v) => v.node_id === nodeId), [vms, nodeId]);

  // Provisioned allocation: sum across all VMs on this node
  const usedVcpus = useMemo(
    () => nodeVms.reduce((s, v) => s + v.vcpus, 0),
    [nodeVms]
  );
  const usedRamMb = useMemo(
    () => nodeVms.reduce((s, v) => s + v.ram_mb, 0),
    [nodeVms]
  );

  const networks = useMemo(
    () =>
      ([...new Set(nodeVms.map((v) => v.vlan).filter(Boolean))] as string[]).sort(),
    [nodeVms]
  );
  // Storage pools available on the node (from live discovery). Falls back
  // to the datastores of existing VMs if discovery is unavailable (e.g.
  // node offline) so we still show something useful.
  const discoveredPools = useMemo(
    () => (resources?.pools ?? []).map((p) => ({ name: p.name, type: p.type })),
    [resources]
  );
  const vmDatastores = useMemo(
    () =>
      ([...new Set(nodeVms.map((v) => v.datastore).filter(Boolean))] as string[]).sort(),
    [nodeVms]
  );

  if (nodesLoading || vmsLoading) {
    return <div className="p-6 text-gray-500">Loading...</div>;
  }

  if (!node) {
    return (
      <div className="p-6">
        <button
          onClick={() => navigate("/nodes")}
          className="flex items-center gap-2 text-sm text-gray-500 hover:text-gray-800 mb-6"
        >
          <ArrowLeft size={16} /> Back to Nodes
        </button>
        <p className="text-gray-500">Node not found.</p>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6 max-w-5xl">
      {/* Back + Header */}
      <div>
        <button
          onClick={() => navigate("/nodes")}
          className="flex items-center gap-2 text-sm text-gray-500 hover:text-gray-800 mb-4"
        >
          <ArrowLeft size={16} /> Back to Nodes
        </button>
        <div className="flex items-center gap-3 flex-wrap">
          <Server size={28} className="text-gray-400 flex-shrink-0" />
          <div>
            <h1 className="text-2xl font-bold leading-tight">{node.hostname}</h1>
            <p className="text-sm text-gray-500">{node.ip_address}</p>
          </div>
          <span
            className={`inline-flex px-2.5 py-1 text-xs rounded-full font-medium ${
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
          {node.ha_enabled && (
            <span className="inline-flex px-2.5 py-1 text-xs rounded-full font-medium bg-purple-100 text-purple-800">
              HA priority {node.ha_priority}
            </span>
          )}
        </div>
        {node.cpu_model && (
          <p className="mt-1.5 text-sm text-gray-400 pl-10">{node.cpu_model}</p>
        )}
        {(node.libvirt_version || node.qemu_version || node.kvm_version || node.kernel_version) && (
          <p className="text-xs text-gray-400 pl-10 font-mono">
            {node.libvirt_version && <>libvirt {node.libvirt_version}</>}
            {node.qemu_version   && <> · qemu {node.qemu_version}</>}
            {node.kvm_version    && <> · kvm {node.kvm_version}</>}
            {node.kernel_version && <> · kernel {node.kernel_version}</>}
          </p>
        )}
        {node.agent_version && (
          <p className="text-xs text-gray-400 pl-10 font-mono">agent {node.agent_version}</p>
        )}

        {/* v0.18 — tenant chips + admin edit affordance */}
        <div className="mt-3 pl-10 flex items-center gap-2 flex-wrap">
          <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">
            Tenants:
          </span>
          {node.tenants.length === 0 ? (
            <span className="text-xs italic text-gray-400">admin-only</span>
          ) : (
            node.tenants.map((t) => (
              <span
                key={t.id}
                className={`inline-flex px-2 py-0.5 text-xs rounded-full font-medium ${
                  t.id === 3
                    ? "bg-purple-100 text-purple-800"
                    : "bg-blue-100 text-blue-800"
                }`}
                title={
                  t.id === 3
                    ? "Shared host pool — visible to all users"
                    : t.name
                }
              >
                {t.name}
              </span>
            ))
          )}
          {isAdmin && (
            <button
              onClick={() => setEditTenants(true)}
              className="ml-1 inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 hover:underline"
            >
              <Tags size={12} />
              Edit
            </button>
          )}
        </div>
      </div>

      {/* Resource bars */}
      <div className="bg-white rounded-xl shadow p-6 space-y-5">
        <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
          Resource Allocation ({nodeVms.length} VM{nodeVms.length !== 1 ? "s" : ""} provisioned)
        </h2>
        <ResourceBar
          label="vCPUs"
          used={usedVcpus}
          total={node.total_vcpus}
          formatValue={(n) => String(n)}
        />
        <ResourceBar
          label="RAM"
          used={usedRamMb}
          total={node.total_ram_mb}
          formatValue={ramLabel}
        />
      </div>

      {/* Networks + Storage side-by-side */}
      <div className="grid grid-cols-2 gap-4">
        <div className="bg-white rounded-xl shadow p-5">
          <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
            Networks in use
          </h2>
          {networks.length === 0 ? (
            <p className="text-sm text-gray-400">None</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {networks.map((vlan) => (
                <span
                  key={vlan}
                  className="inline-flex items-center gap-1 px-2.5 py-1 text-xs bg-blue-50 text-blue-800 rounded-full font-medium"
                >
                  {vlanLabel(vlan)}
                  <span className="text-blue-400 font-mono">({vlan})</span>
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="bg-white rounded-xl shadow p-5">
          <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
            Storage pools
          </h2>
          {discoveredPools.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {discoveredPools.map((p) => (
                <span
                  key={p.name}
                  className={`inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-full font-mono ${
                    p.type === "rbd"
                      ? "bg-blue-100 text-blue-800"
                      : "bg-gray-100 text-gray-700"
                  }`}
                  title={`${p.type} pool`}
                >
                  {p.name}
                  <span className="text-[10px] uppercase opacity-70">{p.type}</span>
                </span>
              ))}
            </div>
          ) : vmDatastores.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {vmDatastores.map((ds) => (
                <span
                  key={ds}
                  className="inline-flex px-2.5 py-1 text-xs bg-gray-100 text-gray-700 rounded-full font-mono"
                >
                  {ds}
                </span>
              ))}
            </div>
          ) : (
            <p className="text-sm text-gray-400">
              {node.status === "online"
                ? "No pools discovered."
                : "Node offline — pools unavailable."}
            </p>
          )}
        </div>
      </div>

      {/* VM table */}
      <div className="bg-white rounded-xl shadow overflow-hidden">
        <div className="px-6 py-4 border-b">
          <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
            Virtual Machines ({nodeVms.length})
          </h2>
        </div>
        {nodeVms.length === 0 ? (
          <p className="px-6 py-6 text-sm text-gray-400">No VMs on this node.</p>
        ) : (
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">State</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">vCPUs</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">RAM</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Disk</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Network</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {nodeVms.map((vm) => (
                <tr key={vm.id} className="hover:bg-gray-50">
                  <td className="px-6 py-4 text-sm font-medium">{vm.name}</td>
                  <td className="px-6 py-4"><StateBadge state={vm.state} /></td>
                  <td className="px-6 py-4 text-sm">{vm.vcpus}</td>
                  <td className="px-6 py-4 text-sm">{ramLabel(vm.ram_mb)}</td>
                  <td className="px-6 py-4 text-sm text-gray-600">
                    {vm.disk_gb ? `${vm.disk_gb} GB` : <span className="text-gray-400">—</span>}
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-500">{vlanLabel(vm.vlan)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {editTenants && (
        <EditNodeTenantsModal
          node={node}
          onClose={() => setEditTenants(false)}
        />
      )}
    </div>
  );
}
