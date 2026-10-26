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

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck, ShieldAlert, ShieldOff, Activity } from "lucide-react";
import { getHaStatus, setHaRuntime, listHaEvents, type HaEvent } from "../api/ha";

// ---------------------------------------------------------------------------
// HA control panel — admin-only. Shows the effective arm state and the
// runtime kill-switch, plus a compact recent-events feed.
//
// Arming model (all must be true for HA to act):
//   env_ha_enabled (boot master-arm) AND runtime_enabled (this toggle)
// The toggle here flips runtime_enabled instantly (takes effect on the
// celery worker within ~15s). If env_ha_enabled is false, arming the
// runtime switch alone will NOT arm HA — surfaced clearly below.
// ---------------------------------------------------------------------------

function resultBadge(result: string): string {
  if (result === "success" || result === "readmitted" || result === "enabled")
    return "bg-green-100 text-green-800";
  if (result === "detected_only") return "bg-yellow-100 text-yellow-800";
  if (result === "partition_suspected") return "bg-purple-100 text-purple-800";
  if (result === "fenced") return "bg-red-100 text-red-800";
  if (result === "failed") return "bg-orange-100 text-orange-800";
  if (result === "needs_migration") return "bg-amber-100 text-amber-800";
  if (
    result.includes("fail") ||
    result === "disabled" ||
    result === "readmit_aborted" ||
    result === "readmit_unsafe"
  )
    return "bg-red-100 text-red-800";
  return "bg-gray-100 text-gray-700";
}

export default function HaControlPanel() {
  const queryClient = useQueryClient();
  const [showEvents, setShowEvents] = useState(false);

  const { data: status } = useQuery({
    queryKey: ["ha-status"],
    queryFn: getHaStatus,
    refetchInterval: 10_000,
    retry: false,
  });

  const { data: events = [] } = useQuery<HaEvent[]>({
    queryKey: ["ha-events"],
    queryFn: () => listHaEvents(25),
    refetchInterval: showEvents ? 10_000 : false,
    enabled: showEvents,
    retry: false,
  });

  const toggle = useMutation({
    mutationFn: (enabled: boolean) =>
      setHaRuntime(
        enabled,
        enabled ? "armed via UI" : "disarmed via UI",
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ha-status"] });
      queryClient.invalidateQueries({ queryKey: ["ha-events"] });
    },
    onError: (e: any) =>
      window.alert(e.response?.data?.error ?? "Failed to change HA state"),
  });

  if (!status) return null;

  const armed = status.armed;
  const runtimeOn = status.runtime_enabled;
  const envOn = status.env_ha_enabled;

  const handleToggle = () => {
    const next = !runtimeOn;
    if (next && !envOn) {
      if (
        !window.confirm(
          "HA_ENABLED (env master-arm) is OFF, so enabling the runtime " +
            "switch will NOT arm failover — it only takes effect once the " +
            "deployment has HA_ENABLED=true. Enable the runtime switch anyway?",
        )
      )
        return;
    }
    if (next && envOn) {
      if (
        !window.confirm(
          "ARM HA? Automatic fence + failover will be LIVE. A dead node " +
            "will be Ceph-fenced and its VMs relocated. Continue?",
        )
      )
        return;
    }
    toggle.mutate(next);
  };

  return (
    <div className="mb-6 bg-white rounded-xl shadow p-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          {armed ? (
            <ShieldCheck className="text-green-600" size={22} />
          ) : runtimeOn ? (
            <ShieldAlert className="text-amber-500" size={22} />
          ) : (
            <ShieldOff className="text-gray-400" size={22} />
          )}
          <div>
            <div className="text-sm font-semibold text-gray-800">
              High Availability —{" "}
              {armed ? (
                <span className="text-green-700">ARMED</span>
              ) : (
                <span className="text-gray-500">disarmed</span>
              )}
            </div>
            <div className="text-xs text-gray-500">
              env master-arm:{" "}
              <span className={envOn ? "text-green-700" : "text-gray-500"}>
                {envOn ? "on" : "off"}
              </span>{" "}
              · runtime switch:{" "}
              <span className={runtimeOn ? "text-green-700" : "text-gray-500"}>
                {runtimeOn ? "on" : "off"}
              </span>{" "}
              · failover threshold {status.failover_threshold}s
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowEvents((s) => !s)}
            className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-md border text-gray-600 hover:bg-gray-50"
          >
            <Activity size={14} />
            {showEvents ? "Hide events" : "HA events"}
          </button>
          <button
            onClick={handleToggle}
            disabled={toggle.isPending}
            className={`text-sm px-3 py-1.5 rounded-md text-white disabled:opacity-40 ${
              runtimeOn
                ? "bg-red-600 hover:bg-red-700"
                : "bg-green-600 hover:bg-green-700"
            }`}
          >
            {runtimeOn ? "Disable HA" : "Enable HA"}
          </button>
        </div>
      </div>

      {showEvents && (
        <div className="mt-4 border-t pt-3">
          {events.length === 0 ? (
            <p className="text-xs text-gray-400">No HA events yet.</p>
          ) : (
            <div className="max-h-64 overflow-y-auto">
              <table className="min-w-full text-xs">
                <thead className="text-gray-500">
                  <tr>
                    <th className="text-left py-1 pr-3 font-medium">Time</th>
                    <th className="text-left py-1 pr-3 font-medium">Trigger</th>
                    <th className="text-left py-1 pr-3 font-medium">Result</th>
                    <th className="text-left py-1 pr-3 font-medium">Node / VM</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((e) => (
                    <tr key={e.id} className="border-t border-gray-100">
                      <td className="py-1 pr-3 text-gray-500 whitespace-nowrap font-mono">
                        {new Date(e.timestamp).toLocaleString()}
                      </td>
                      <td className="py-1 pr-3 text-gray-700">{e.trigger}</td>
                      <td className="py-1 pr-3">
                        <span
                          className={`inline-flex px-1.5 py-0.5 rounded-full font-medium ${resultBadge(
                            e.result,
                          )}`}
                        >
                          {e.result}
                        </span>
                      </td>
                      <td className="py-1 pr-3 text-gray-600">
                        {e.vm_name
                          ? `${e.vm_name}${
                              e.target_hostname ? ` → ${e.target_hostname}` : ""
                            }`
                          : e.node_hostname ??
                            e.source_hostname ??
                            "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
