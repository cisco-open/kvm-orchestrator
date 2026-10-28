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

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ServerCrash, ArrowRightLeft, X, ShieldCheck, Power, PowerOff, ShieldAlert } from "lucide-react";
import { listAffectedVms, fenceNode, type AffectedNode, type AffectedVm, type RecoveryTarget } from "../api/ha";
import { migrateVm } from "../api/vms";
import HaControlPanel from "../components/HaControlPanel";
import AdminChecklist from "../components/AdminChecklist";

function priorityBadge(p: string) {
  const map: Record<string, string> = {
    critical: "bg-red-100 text-red-800 ring-1 ring-red-300",
    priority: "bg-amber-100 text-amber-800",
    default: "bg-gray-100 text-gray-500",
  };
  return (
    <span className={`inline-flex px-2 py-0.5 text-xs font-medium rounded-full ${map[p] ?? map.default}`}>
      {p}
    </span>
  );
}

function wasStateBadge(was: string) {
  if (was === "running") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded-full bg-green-100 text-green-800">
        <Power size={11} /> was on
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded-full bg-gray-100 text-gray-500">
      <PowerOff size={11} /> was off
    </span>
  );
}

function MigrateRecoverModal({
  vm,
  sourceHostname,
  sourceCpu,
  targets,
  onClose,
}: {
  vm: AffectedVm;
  sourceHostname: string;
  sourceCpu: string | null;
  targets: RecoveryTarget[];
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [targetId, setTargetId] = useState<number | "">("");
  const [error, setError] = useState<string | null>(null);

  const mut = useMutation({
    mutationFn: () => migrateVm(vm.id, Number(targetId)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ha-affected-vms"] });
      qc.invalidateQueries({ queryKey: ["vms"] });
      onClose();
    },
    onError: (e: any) => setError(e.response?.data?.error ?? "Recovery failed"),
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-6">
        <div className="flex items-center justify-between mb-1">
          <h2 className="text-base font-semibold">Recover / Migrate VM</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600"><X size={18} /></button>
        </div>
        <p className="text-sm text-gray-500 mb-1">
          Rebuild <span className="font-medium text-gray-800">{vm.name}</span> from its dead host{" "}
          <span className="font-medium">{sourceHostname}</span> onto a healthy node.
        </p>
        {sourceCpu && (
          <p className="text-xs text-gray-400 mb-3">
            Failed host CPU: <span className="font-mono">{sourceCpu}</span>. Green targets match this
            architecture and are guaranteed to start.
          </p>
        )}

        {!vm.has_xml && (
          <div className="mb-3 rounded-md bg-red-50 border border-red-200 p-2 text-xs text-red-700">
            ⚠️ No stored libvirt XML for this VM — it cannot be rebuilt automatically.
          </div>
        )}

        <label className="block text-xs font-medium text-gray-600 mb-1">Target node</label>
        <div className="space-y-1 max-h-72 overflow-y-auto pr-1 mb-3">
          {targets.map((t) => (
            <label
              key={t.id}
              className={`flex items-center gap-3 px-3 py-2 rounded-lg border cursor-pointer transition-colors ${
                targetId === t.id ? "border-blue-500 bg-blue-50" : "border-gray-200 hover:bg-gray-50"
              } ${t.arch_match ? "ring-1 ring-green-300" : ""}`}
            >
              <input
                type="radio"
                name="target"
                className="accent-blue-600"
                checked={targetId === t.id}
                onChange={() => setTargetId(t.id)}
              />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium text-gray-800">{t.hostname}</span>
                  {t.arch_match ? (
                    <span className="text-xs px-1.5 py-0.5 rounded-full font-medium bg-green-100 text-green-800">
                      ✓ arch match
                    </span>
                  ) : (
                    <span className="text-xs px-1.5 py-0.5 rounded-full font-medium bg-gray-100 text-gray-500">
                      different arch
                    </span>
                  )}
                </div>
                <div className="text-xs text-gray-400 truncate">
                  {t.cpu_model || "unknown CPU"} · {t.available_vcpus} vCPU free ·{" "}
                  {t.available_ram_mb ? `${Math.round(t.available_ram_mb / 1024)} GB` : "?"} RAM free
                </div>
              </div>
            </label>
          ))}
          {targets.length === 0 && (
            <p className="text-xs text-gray-400 px-1">No online target nodes available.</p>
          )}
        </div>

        {error && <div className="mb-3 text-xs text-red-600">{error}</div>}

        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="px-3 py-1.5 text-sm rounded-md bg-gray-100 text-gray-700 hover:bg-gray-200">
            Cancel
          </button>
          <button
            onClick={() => { setError(null); mut.mutate(); }}
            disabled={!targetId || mut.isPending || !vm.has_xml}
            className="px-3 py-1.5 text-sm rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40"
          >
            {mut.isPending ? "Recovering…" : "Recover VM"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function HA() {
  const [migrateTarget, setMigrateTarget] = useState<{
    vm: AffectedVm; host: string; cpu: string | null; targets: RecoveryTarget[];
  } | null>(null);

  const { data: affected = [], isLoading } = useQuery<AffectedNode[]>({
    queryKey: ["ha-affected-vms"],
    queryFn: listAffectedVms,
    refetchInterval: 10_000,
  });

  const qc = useQueryClient();
  const fenceMut = useMutation({
    mutationFn: ({ id, force }: { id: number; force: boolean }) => fenceNode(id, force),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ha-affected-vms"] }),
  });

  const handleFence = (node: AffectedNode) => {
    if (!window.confirm(
      `Fence ${node.hostname}?\n\nThis Ceph-blocklists the node so it can no ` +
      `longer touch shared storage. Do this only once you are sure the host is ` +
      `dead. After fencing, you can recover its VMs.`,
    )) return;
    fenceMut.mutate({ id: node.node_id, force: false }, {
      onError: (e: any) => {
        const d = e.response?.data;
        if (d?.needs_fence === undefined && d?.partition_suspected) {
          // Node still writing Ceph — offer force override.
          if (window.confirm(
            `⚠️ REFUSED: ${node.hostname} is still writing Ceph — its VMs appear ` +
            `ALIVE (suspected network partition). Fencing will CRASH them.\n\n` +
            `Only if you are CERTAIN the host is dead, force-fence anyway?`,
          )) {
            fenceMut.mutate({ id: node.node_id, force: true });
          }
        } else {
          window.alert(d?.error ?? "Fence failed");
        }
      },
    });
  };

  const totalVms = affected.reduce((n, node) => n + node.vms.length, 0);

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <ShieldCheck size={22} /> High Availability
        </h1>
        <p className="text-sm text-gray-500">
          Arm state, HA events, and the recovery worklist for VMs on failed hosts.
        </p>
      </div>

      <HaControlPanel />

      {affected.length > 0 && <AdminChecklist />}

      {/* Affected VMs worklist */}
      <div>
        <h2 className="text-lg font-semibold flex items-center gap-2 mb-2">
          <ServerCrash size={18} className="text-red-600" />
          VMs on failed nodes
          {totalVms > 0 && (
            <span className="text-sm font-normal text-red-700">({totalVms} to recover)</span>
          )}
        </h2>

        {isLoading ? (
          <p className="text-gray-500 text-sm">Loading…</p>
        ) : affected.length === 0 ? (
          <div className="rounded-lg border bg-white p-6 text-center text-gray-500">
            <ShieldCheck size={28} className="mx-auto text-green-400 mb-2" />
            No failed nodes. All hosts healthy.
          </div>
        ) : (
          <div className="space-y-4">
            {affected.map((node) => (
              <div key={node.node_id} className="rounded-lg border border-red-200 bg-white overflow-hidden">
                <div className="flex items-center gap-2 bg-red-50 px-4 py-2 border-b border-red-200">
                  <AlertTriangle size={16} className="text-red-600" />
                  <span className="font-semibold">{node.hostname}</span>
                  <span className={`text-xs px-2 py-0.5 rounded-full uppercase font-medium ${
                    node.status === "fenced"
                      ? "bg-red-200 text-red-900"
                      : "bg-orange-100 text-orange-800"
                  }`}>
                    {node.status}
                  </span>
                  {node.cpu_model && (
                    <span className="text-xs text-gray-500">{node.cpu_model}</span>
                  )}
                  <span className="ml-auto text-xs text-gray-500 mr-2">{node.vms.length} VM(s)</span>
                  {node.status !== "fenced" && (
                    <button
                      onClick={() => handleFence(node)}
                      disabled={fenceMut.isPending}
                      className="inline-flex items-center gap-1 text-xs px-3 py-1 rounded-md bg-red-600 text-white hover:bg-red-700 disabled:opacity-40"
                      title="Ceph-fence this dead node so its VMs can be recovered"
                    >
                      <ShieldAlert size={13} /> Fence node
                    </button>
                  )}
                </div>
                {node.status !== "fenced" && (
                  <div className="px-4 py-2 bg-amber-50 border-b border-amber-200 text-xs text-amber-800">
                    ⚠️ This node must be <b>fenced</b> before its VMs can be recovered
                    (prevents split-brain if the host is still writing storage).
                  </div>
                )}
                <table className="min-w-full text-sm">
                  <thead className="bg-gray-50 text-xs uppercase text-gray-500">
                    <tr>
                      <th className="px-4 py-2 text-left">VM</th>
                      <th className="px-4 py-2 text-left">Was</th>
                      <th className="px-4 py-2 text-left">Priority</th>
                      <th className="px-4 py-2 text-left">vCPU / RAM</th>
                      <th className="px-4 py-2 text-left">IP</th>
                      <th className="px-4 py-2 text-left">XML</th>
                      <th className="px-4 py-2 text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {node.vms.map((vm) => (
                      <tr key={vm.id} className={`hover:bg-gray-50 ${vm.was_state === "off" ? "opacity-60" : ""}`}>
                        <td className="px-4 py-2 font-medium">{vm.name}</td>
                        <td className="px-4 py-2">{wasStateBadge(vm.was_state)}</td>
                        <td className="px-4 py-2">{priorityBadge(vm.priority)}</td>
                        <td className="px-4 py-2 text-gray-600 whitespace-nowrap">
                          {vm.vcpus} / {vm.ram_mb ? `${Math.round(vm.ram_mb / 1024)} GB` : "—"}
                        </td>
                        <td className="px-4 py-2 font-mono text-xs text-gray-600">{vm.ip || "—"}</td>
                        <td className="px-4 py-2">
                          {vm.has_xml ? (
                            <span className="text-green-600 text-xs">✓</span>
                          ) : (
                            <span className="text-red-600 text-xs" title="No stored XML — cannot rebuild">✗</span>
                          )}
                        </td>
                        <td className="px-4 py-2 text-right">
                          <button
                            onClick={() => setMigrateTarget({
                              vm, host: node.hostname, cpu: node.cpu_model, targets: node.targets,
                            })}
                            disabled={!vm.has_xml || node.status !== "fenced"}
                            title={
                              node.status !== "fenced"
                                ? "Fence the node first before recovering its VMs"
                                : vm.was_state === "off"
                                ? "This VM was powered off — usually leave it down"
                                : undefined
                            }
                            className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40"
                          >
                            <ArrowRightLeft size={12} /> Recover
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </div>
        )}
      </div>

      {migrateTarget && (
        <MigrateRecoverModal
          vm={migrateTarget.vm}
          sourceHostname={migrateTarget.host}
          sourceCpu={migrateTarget.cpu}
          targets={migrateTarget.targets}
          onClose={() => setMigrateTarget(null)}
        />
      )}
    </div>
  );
}
