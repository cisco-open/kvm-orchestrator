import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid,
  Legend, PieChart, Pie, Cell,
} from "recharts";
import { BarChart3 } from "lucide-react";
import { getMetricsSummary, getVmsCreated, type MetricsSummary, type VmsCreated } from "../api/metrics";
import { useAuth } from "../hooks/useAuth";
import { brand } from "../branding";

const OS_COLORS: Record<string, string> = {
  linux: "#2563eb",   // blue
  windows: "#0891b2", // cyan
  custom: "#a855f7",  // purple
  unknown: "#9ca3af", // gray
};
const STATE_COLORS: Record<string, string> = {
  running: "#16a34a", stopped: "#9ca3af", paused: "#eab308",
  unknown: "#f59e0b", error: "#dc2626", crashed: "#dc2626",
};

function gb(mb: number) {
  return Math.round(mb / 1024);
}

function CapacityBar({
  label, used, total, unit, allocatedNote,
}: {
  label: string; used: number; total: number; unit: string; allocatedNote?: string;
}) {
  const pct = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0;
  const color = pct > 90 ? "bg-red-500" : pct > 75 ? "bg-amber-500" : "bg-green-500";
  return (
    <div>
      <div className="flex justify-between text-sm mb-1">
        <span className="font-medium text-gray-700">{label}</span>
        <span className="text-gray-500">
          {used.toLocaleString()} / {total.toLocaleString()} {unit} ({pct}%)
        </span>
      </div>
      <div className="w-full h-3 bg-gray-100 rounded-full overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      {allocatedNote && <p className="text-xs text-gray-400 mt-1">{allocatedNote}</p>}
    </div>
  );
}

function Donut({ data, title }: { data: { name: string; value: number; color: string }[]; title: string }) {
  const total = data.reduce((s, d) => s + d.value, 0);
  return (
    <div className="bg-white rounded-lg shadow p-4">
      <h3 className="text-sm font-medium text-gray-500 mb-2">{title}</h3>
      {total === 0 ? (
        <p className="text-sm text-gray-400 py-8 text-center">No data</p>
      ) : (
        <ResponsiveContainer width="100%" height={200}>
          <PieChart>
            <Pie data={data} dataKey="value" nameKey="name" innerRadius={45} outerRadius={70} paddingAngle={2}>
              {data.map((d) => <Cell key={d.name} fill={d.color} />)}
            </Pie>
            <Tooltip />
            <Legend />
          </PieChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

export default function Metrics() {
  const isAdmin = useAuth((s) => s.isAdmin);
  const [days, setDays] = useState(90);

  const { data: summary } = useQuery<MetricsSummary>({
    queryKey: ["metrics-summary"], queryFn: getMetricsSummary,
    enabled: isAdmin, refetchInterval: 30_000,
  });
  const { data: created } = useQuery<VmsCreated>({
    queryKey: ["vms-created", days], queryFn: () => getVmsCreated(days),
    enabled: isAdmin,
  });

  if (!isAdmin) return <div className="p-6 text-gray-500">Admins only.</div>;

  const c = summary?.cluster;
  const osData = Object.entries(summary?.vms_by_os ?? {}).map(([name, value]) => ({
    name, value, color: OS_COLORS[name] ?? OS_COLORS.unknown,
  })) as { name: string; value: number; color: string }[];
  const stateData = Object.entries(summary?.vms_by_state ?? {}).map(([name, value]) => ({
    name, value, color: STATE_COLORS[name] ?? "#9ca3af",
  })) as { name: string; value: number; color: string }[];

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center gap-2">
        <BarChart3 size={22} />
        <h1 className="text-2xl font-bold">Metrics & Capacity</h1>
      </div>

      {/* Cluster capacity */}
      <div className="bg-white rounded-lg shadow p-5">
        <h2 className="text-sm font-medium text-gray-500 mb-4">
          Cluster capacity {c ? `(${c.online_nodes} online nodes)` : ""}
        </h2>
        {c ? (
          <div className="space-y-4">
            <CapacityBar
              label="vCPU" used={c.vcpus_used} total={c.vcpus_total} unit="vCPU"
              allocatedNote={`${c.vcpus_allocated.toLocaleString()} allocated to running VMs · overcommit ${c.vcpu_overcommit}×`}
            />
            <CapacityBar label="Memory" used={gb(c.ram_mb_used)} total={gb(c.ram_mb_total)} unit="GB" />
            <CapacityBar label="Disk" used={c.disk_gb_used} total={c.disk_gb_total} unit="GB" />
          </div>
        ) : (
          <p className="text-sm text-gray-400">Loading…</p>
        )}
      </div>

      {/* VMs over time (cumulative: rises on create, falls on delete) */}
      <div className="bg-white rounded-lg shadow p-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium text-gray-500">VMs created via {brand().slug} over time (cumulative, by OS)</h2>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}
            className="border rounded-md px-2 py-1 text-sm">
            <option value={30}>30 days</option>
            <option value={90}>90 days</option>
            <option value={365}>1 year</option>
          </select>
        </div>
        <p className="text-xs text-gray-400 -mt-2 mb-3">
          Running total of VMs created through {brand().slug} (rises on create,
          falls on delete). Excludes VMs discovered on hosts but not created here.
        </p>
        {created ? (
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={created.series} margin={{ left: -10, right: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={40} />
              <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
              <Tooltip />
              <Legend />
              <Line type="monotone" dataKey="total" name="Total" stroke="#111827" strokeWidth={2.5} dot={false} />
              <Line type="monotone" dataKey="linux" name="linux" stroke={OS_COLORS.linux} strokeWidth={1.75} dot={false} />
              <Line type="monotone" dataKey="windows" name="windows" stroke={OS_COLORS.windows} strokeWidth={1.75} dot={false} />
              <Line type="monotone" dataKey="custom" name="custom" stroke={OS_COLORS.custom} strokeWidth={1.75} dot={false} />
              <Line type="monotone" dataKey="unknown" name="unknown" stroke={OS_COLORS.unknown} strokeWidth={1.5} dot={false} strokeDasharray="4 3" />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <p className="text-sm text-gray-400">Loading…</p>
        )}
      </div>

      {/* Inventory donuts */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Donut title={`VMs by OS (${summary?.vms_total ?? 0} total)`} data={osData} />
        <Donut title="VMs by state" data={stateData} />
      </div>

      {/* Per-node utilisation */}
      <div className="bg-white rounded-lg shadow overflow-hidden">
        <h2 className="text-sm font-medium text-gray-500 px-4 pt-4">Per-node utilisation</h2>
        <div className="overflow-x-auto mt-2">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-xs uppercase text-gray-500">
              <tr>
                <th className="px-4 py-2 text-left">Node</th>
                <th className="px-4 py-2 text-left">Status</th>
                <th className="px-4 py-2 text-left">vCPU used</th>
                <th className="px-4 py-2 text-left">RAM used</th>
                <th className="px-4 py-2 text-left">Disk used</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(summary?.nodes ?? []).map((n) => {
                const vUsed = n.vcpus_total - n.vcpus_available;
                const rUsed = n.ram_mb_total - n.ram_mb_available;
                const dUsed = n.disk_gb_total - n.disk_gb_available;
                const pct = (u: number, t: number) => (t > 0 ? Math.round((u / t) * 100) : 0);
                return (
                  <tr key={n.id} className="hover:bg-gray-50">
                    <td className="px-4 py-2 font-medium">{n.hostname}</td>
                    <td className="px-4 py-2">
                      <span className={`text-xs px-2 py-0.5 rounded-full ${
                        n.status === "online" ? "bg-green-100 text-green-800"
                        : n.status === "fenced" || n.status === "failed" ? "bg-red-100 text-red-800"
                        : "bg-gray-100 text-gray-600"}`}>{n.status}</span>
                    </td>
                    <td className="px-4 py-2 text-gray-600">{vUsed}/{n.vcpus_total} ({pct(vUsed, n.vcpus_total)}%)</td>
                    <td className="px-4 py-2 text-gray-600">{gb(rUsed)}/{gb(n.ram_mb_total)} GB ({pct(rUsed, n.ram_mb_total)}%)</td>
                    <td className="px-4 py-2 text-gray-600">{dUsed}/{n.disk_gb_total} GB ({pct(dUsed, n.disk_gb_total)}%)</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
