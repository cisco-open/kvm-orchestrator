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

import { useState, useMemo, Fragment } from "react";
import { useQuery } from "@tanstack/react-query";
import { HardDrive, ChevronDown, ChevronRight } from "lucide-react";
import { listVMs } from "../api/vms";
import type { VM } from "../types/vm";

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

// ---------------------------------------------------------------------------
// Data model
// ---------------------------------------------------------------------------

interface StorageSummary {
  key: string;
  datastore: string;
  vms: VM[];
  totalDiskGb: number;
  nodeHostnames: string[];
}

// ---------------------------------------------------------------------------
// Storage page
// ---------------------------------------------------------------------------

export default function Storage() {
  const { data: vms = [], isLoading } = useQuery<VM[]>({
    queryKey: ["vms"],
    queryFn: () => listVMs(),
    refetchInterval: 15_000,
  });

  const datastores = useMemo<StorageSummary[]>(() => {
    const map = new Map<string, VM[]>();
    for (const vm of vms) {
      const key = vm.datastore ?? "__unknown__";
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(vm);
    }
    return Array.from(map.entries())
      .map(([key, vmList]) => ({
        key,
        datastore: key === "__unknown__" ? "Unknown" : key,
        vms: vmList,
        totalDiskGb: vmList.reduce((sum, v) => sum + (v.disk_gb ?? 0), 0),
        nodeHostnames: [...new Set(vmList.map((v) => v.node_hostname))].sort(),
      }))
      .sort((a, b) => a.datastore.localeCompare(b.datastore));
  }, [vms]);

  const [expanded, setExpanded] = useState<string | null>(null);
  const toggle = (key: string) => setExpanded((prev) => (prev === key ? null : key));

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Storage</h1>
        <span className="text-sm text-gray-500">
          {datastores.length} datastore{datastores.length !== 1 ? "s" : ""}
        </span>
      </div>

      {isLoading ? (
        <p className="text-gray-500">Loading...</p>
      ) : datastores.length === 0 ? (
        <div className="text-center py-16 bg-white rounded-xl shadow">
          <HardDrive size={40} className="mx-auto text-gray-300 mb-3" />
          <p className="text-gray-500">No storage pools found.</p>
        </div>
      ) : (
        <div className="bg-white rounded-xl shadow overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 w-8" />
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Datastore</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">VMs</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Allocated</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Hosts</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {datastores.map((ds) => (
                <Fragment key={ds.key}>
                  <tr
                    className="hover:bg-gray-50 cursor-pointer"
                    onClick={() => toggle(ds.key)}
                  >
                    <td className="px-4 py-4 text-gray-400">
                      {expanded === ds.key
                        ? <ChevronDown size={16} />
                        : <ChevronRight size={16} />}
                    </td>
                    <td className="px-4 py-4 text-sm font-semibold font-mono">{ds.datastore}</td>
                    <td className="px-4 py-4 text-sm">{ds.vms.length}</td>
                    <td className="px-4 py-4 text-sm">{ds.totalDiskGb} GB</td>
                    <td className="px-4 py-4">
                      <div className="flex flex-wrap gap-1">
                        {ds.nodeHostnames.map((h) => (
                          <span key={h} className="inline-flex px-2 py-0.5 text-xs bg-gray-100 text-gray-700 rounded-full">
                            {h}
                          </span>
                        ))}
                      </div>
                    </td>
                  </tr>
                  {expanded === ds.key && (
                    <tr>
                      <td colSpan={5} className="px-8 pb-6 pt-3 bg-gray-50">
                        <div className="grid grid-cols-2 gap-8">
                          {/* Hosts */}
                          <div>
                            <h3 className="text-xs font-semibold text-gray-500 uppercase mb-3">Hosts</h3>
                            <ul className="space-y-1.5">
                              {ds.nodeHostnames.map((h) => (
                                <li key={h} className="text-sm flex items-center gap-2">
                                  <span className="w-2 h-2 rounded-full bg-blue-400 flex-shrink-0" />
                                  {h}
                                </li>
                              ))}
                            </ul>
                          </div>
                          {/* VMs */}
                          <div>
                            <h3 className="text-xs font-semibold text-gray-500 uppercase mb-3">Virtual Machines</h3>
                            <table className="min-w-full text-sm">
                              <thead>
                                <tr className="text-xs text-gray-400 border-b border-gray-200">
                                  <th className="text-left pb-2 font-medium pr-4">Name</th>
                                  <th className="text-left pb-2 font-medium pr-4">Disk</th>
                                  <th className="text-left pb-2 font-medium pr-4">Path</th>
                                  <th className="text-left pb-2 font-medium">State</th>
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-gray-100">
                                {ds.vms.map((vm) => (
                                  <tr key={vm.id}>
                                    <td className="py-1.5 pr-4 font-medium">{vm.name}</td>
                                    <td className="py-1.5 pr-4 text-gray-600">
                                      {vm.disk_gb ? `${vm.disk_gb} GB` : <span className="text-gray-400">—</span>}
                                    </td>
                                    <td className="py-1.5 pr-4 text-gray-400 font-mono text-xs max-w-xs truncate">
                                      {vm.disk_path ?? <span className="text-gray-300">—</span>}
                                    </td>
                                    <td className="py-1.5"><StateBadge state={vm.state} /></td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
