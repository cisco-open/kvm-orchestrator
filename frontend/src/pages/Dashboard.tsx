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

import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ResponsiveContainer, LineChart, Line, XAxis, Tooltip } from "recharts";
import { listNodes } from "../api/nodes";
import { listVMs } from "../api/vms";
import { getHaSummary, type HaSummary } from "../api/ha";
import { getMetricsSummary, getVmsCreated, type MetricsSummary, type VmsCreated } from "../api/metrics";
import { useAuth } from "../hooks/useAuth";
import type { Node } from "../types/node";
import type { VM } from "../types/vm";

const OS_COLORS: Record<string, string> = {
  linux: "#2563eb", windows: "#0891b2", custom: "#a855f7", unknown: "#9ca3af",
};

export default function Dashboard() {
  const isAdmin = useAuth((s) => s.isAdmin);

  const { data: nodes, error: nodesErr } = useQuery<Node[]>({
    queryKey: ["nodes"], queryFn: listNodes, refetchInterval: 30_000,
  });
  const { data: vms, error: vmsErr } = useQuery<VM[]>({
    queryKey: ["vms"], queryFn: () => listVMs(), refetchInterval: 30_000,
  });
  const { data: ha } = useQuery<HaSummary>({
    queryKey: ["ha-summary"], queryFn: getHaSummary,
    enabled: isAdmin, refetchInterval: 15_000,
  });
  const { data: metrics } = useQuery<MetricsSummary>({
    queryKey: ["metrics-summary"], queryFn: getMetricsSummary,
    enabled: isAdmin, refetchInterval: 30_000,
  });
  const { data: created } = useQuery<VmsCreated>({
    queryKey: ["vms-created", 30], queryFn: () => getVmsCreated(30),
    enabled: isAdmin,
  });

  const error = (nodesErr as Error)?.message ?? (vmsErr as Error)?.message ?? null;
  const onlineNodes = nodes?.filter((n) => n.status === "online").length ?? 0;
  const runningVMs = vms?.filter((v) => v.state === "running").length ?? 0;
  const attention = ha?.nodes_needing_attention ?? 0;
  const cap = metrics?.cluster;
  const gb = (mb: number) => Math.round(mb / 1024);

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold mb-6">Dashboard</h1>

      {error && (
        <div className="mb-4 rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Attention banner (admins) */}
      {isAdmin && attention > 0 && (
        <Link
          to="/ha"
          className="mb-6 flex items-center gap-2 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800 hover:bg-red-100"
        >
          <span className="font-semibold">⚠ {attention} node(s) need attention</span>
          <span className="text-red-600">— failed or fenced. Go to High Availability →</span>
        </Link>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <StatCard
          label="Total Nodes"
          value={nodes === undefined ? "…" : String(nodes.length)}
          sub={nodes !== undefined ? `${onlineNodes} online` : ""}
          subColor={onlineNodes > 0 ? "text-green-600" : "text-gray-400"}
        />
        <StatCard
          label="Virtual Machines"
          value={vms === undefined ? "…" : String(vms.length)}
          sub={vms !== undefined ? `${runningVMs} running` : ""}
          subColor={runningVMs > 0 ? "text-green-600" : "text-gray-400"}
        />
        {isAdmin ? (
          <Link to="/ha" className="block">
            <StatCard
              label="HA Events (24h)"
              value={ha === undefined ? "…" : String(ha.events_24h)}
              sub={attention > 0 ? `${attention} node(s) need attention` : "all nodes healthy"}
              subColor={attention > 0 ? "text-red-600" : "text-green-600"}
            />
          </Link>
        ) : (
          <StatCard label="HA Events (24h)" value="—" sub="admin only" subColor="text-gray-400" />
        )}
      </div>

      {/* Capacity + created chart (admins) */}
      {isAdmin && cap && (
        <div className="mt-6 grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="bg-white rounded-lg shadow p-5">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-medium text-gray-500">Cluster capacity</h2>
              <Link to="/metrics" className="text-xs text-blue-600 hover:underline">More →</Link>
            </div>
            <div className="space-y-3">
              <CapBar label="vCPU" used={cap.vcpus_used} total={cap.vcpus_total} unit="" />
              <CapBar label="Memory" used={gb(cap.ram_mb_used)} total={gb(cap.ram_mb_total)} unit="GB" />
              <CapBar label="Disk" used={cap.disk_gb_used} total={cap.disk_gb_total} unit="GB" />
            </div>
          </div>
          <div className="bg-white rounded-lg shadow p-5">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-medium text-gray-500">VMs over time (30d)</h2>
              <Link to="/metrics" className="text-xs text-blue-600 hover:underline">More →</Link>
            </div>
            {created ? (
              <ResponsiveContainer width="100%" height={160}>
                <LineChart data={created.series} margin={{ left: -20, right: 5 }}>
                  <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={40} />
                  <Tooltip />
                  <Line type="monotone" dataKey="total" name="Total" stroke="#111827" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="linux" name="linux" stroke={OS_COLORS.linux} strokeWidth={1.5} dot={false} />
                  <Line type="monotone" dataKey="windows" name="windows" stroke={OS_COLORS.windows} strokeWidth={1.5} dot={false} />
                  <Line type="monotone" dataKey="custom" name="custom" stroke={OS_COLORS.custom} strokeWidth={1.5} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            ) : <p className="text-sm text-gray-400">Loading…</p>}
          </div>
        </div>
      )}

      {/* HA event breakdown (admins) */}
      {isAdmin && ha && ha.events_24h > 0 && (
        <div className="mt-6 bg-white rounded-lg shadow p-5">
          <h2 className="text-sm font-medium text-gray-500 mb-3">HA activity (last 24h)</h2>
          <div className="flex flex-wrap gap-2">
            {Object.entries(ha.events_24h_by_result).map(([result, count]) => (
              <span
                key={result}
                className={`px-2.5 py-1 rounded-full text-xs font-medium ${resultColor(result)}`}
              >
                {result}: {count}
              </span>
            ))}
          </div>
          {ha.last_event && (
            <p className="mt-3 text-xs text-gray-400">
              Last: {ha.last_event.result}
              {ha.last_event.node_hostname ? ` on ${ha.last_event.node_hostname}` : ""}
              {" — "}
              {new Date(ha.last_event.timestamp).toLocaleString()}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function CapBar({ label, used, total, unit }: { label: string; used: number; total: number; unit: string }) {
  const pct = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0;
  const color = pct > 90 ? "bg-red-500" : pct > 75 ? "bg-amber-500" : "bg-green-500";
  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="font-medium text-gray-700">{label}</span>
        <span className="text-gray-500">{used.toLocaleString()} / {total.toLocaleString()} {unit} ({pct}%)</span>
      </div>
      <div className="w-full h-2.5 bg-gray-100 rounded-full overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function resultColor(result: string): string {
  if (["success", "readmitted", "enabled", "fenced"].includes(result))
    return result === "fenced" ? "bg-red-100 text-red-800" : "bg-green-100 text-green-800";
  if (result === "failed" || result === "needs_migration")
    return "bg-orange-100 text-orange-800";
  if (result === "partition_suspected") return "bg-purple-100 text-purple-800";
  if (result === "detected_only") return "bg-yellow-100 text-yellow-800";
  if (result.includes("unsafe") || result.includes("abort") || result.includes("fail"))
    return "bg-red-100 text-red-800";
  return "bg-gray-100 text-gray-700";
}

function StatCard({
  label, value, sub, subColor,
}: {
  label: string; value: string; sub: string; subColor: string;
}) {
  return (
    <div className="bg-white p-6 rounded-lg shadow hover:shadow-md transition-shadow">
      <h2 className="text-sm font-medium text-gray-500">{label}</h2>
      <p className="text-3xl font-bold mt-2">{value}</p>
      {sub && <p className={`text-sm mt-1 ${subColor}`}>{sub}</p>}
    </div>
  );
}
