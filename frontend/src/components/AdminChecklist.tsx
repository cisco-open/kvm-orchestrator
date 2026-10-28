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
import { ListChecks, ChevronDown, ChevronRight } from "lucide-react";

// Admin recovery checklist — walk through BEFORE fencing / recovering, to
// avoid fencing a node that is only network-isolated (which would crash its
// still-running VMs).
const CHECKLIST: { id: string; label: string; hint: string }[] = [
  {
    id: "mgmt",
    label: "Rule out a management-network problem first",
    hint:
      "A node can look 'failed' just because the controller can't reach it on " +
      "the management network, while its VMs run fine. Fencing a live node " +
      "crashes those VMs. Confirm the host is genuinely down before fencing.",
  },
  {
    id: "gateway",
    label: "Check the node's gateway / switch and management path",
    hint:
      "Ping/traceroute the node's management IP and its gateway from another " +
      "host on the same segment; check the ToR switch port. If the path is down " +
      "but the host is up, this is a NETWORK issue — fix routing, do NOT fence.",
  },
  {
    id: "vms-reachable",
    label: "Check whether the VMs are still reachable / serving",
    hint:
      "If the guests still respond (ping, SSH, service checks) on the VM " +
      "network, they are ALIVE — this is a partition, not a host failure. " +
      "Do NOT fence.",
  },
  {
    id: "console-power",
    label: "Confirm the host is really down (console / IPMI / power)",
    hint:
      "Use out-of-band console or IPMI/BMC power status. Only proceed to fence " +
      "once you have positive confirmation the host is dead or unrecoverable.",
  },
  {
    id: "fence",
    label: "Fence the node (only after the above)",
    hint:
      "Fencing Ceph-blocklists the host so it can no longer write shared " +
      "storage. Use the 'Fence node' button on the failed node below. VMs " +
      "cannot be recovered until the node is fenced.",
  },
  {
    id: "recover",
    label: "Recover the VMs that were running",
    hint:
      "For each VM shown as 'was on', click Recover and pick an architecture-" +
      "matching (green) target. VMs that were off are left down.",
  },
  {
    id: "readmit",
    label: "When the host returns, run Readmit",
    hint:
      "Power the host back on, then use Readmit — it runs a safety check, " +
      "destroys stale copies, un-blocklists, and brings the node back online.",
  },
];

export default function AdminChecklist() {
  const [open, setOpen] = useState(true);
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const done = CHECKLIST.filter((c) => checked[c.id]).length;

  return (
    <div className="rounded-lg border border-blue-200 bg-blue-50/50">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-4 py-3 text-left"
      >
        <ListChecks size={18} className="text-blue-700" />
        <span className="font-semibold text-blue-900">Admin recovery checklist</span>
        <span className="text-xs text-blue-700">
          ({done}/{CHECKLIST.length})
        </span>
        <span className="ml-auto text-blue-700">
          {open ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
        </span>
      </button>
      {open && (
        <div className="px-4 pb-4 space-y-1">
          <p className="text-xs text-blue-800 mb-2">
            Work through this before fencing.{" "}
            <b>
              Fencing a node that is only network-isolated (not dead) will crash
              its running VMs.
            </b>{" "}
            Rule out network problems first.
          </p>
          {CHECKLIST.map((c, i) => (
            <div key={c.id} className="rounded-md bg-white border border-blue-100">
              <div className="flex items-start gap-2 px-3 py-2">
                <input
                  type="checkbox"
                  className="mt-0.5 accent-blue-600"
                  checked={!!checked[c.id]}
                  onChange={() => setChecked((s) => ({ ...s, [c.id]: !s[c.id] }))}
                />
                <div className="flex-1 min-w-0">
                  <button
                    onClick={() => setExpanded((s) => ({ ...s, [c.id]: !s[c.id] }))}
                    className="text-left w-full text-sm font-medium text-gray-800 flex items-center gap-1"
                  >
                    <span className="text-blue-700 font-mono text-xs">{i + 1}.</span>
                    <span className={checked[c.id] ? "line-through text-gray-400" : ""}>
                      {c.label}
                    </span>
                    <span className="ml-1 text-gray-400">
                      {expanded[c.id] ? (
                        <ChevronDown size={13} />
                      ) : (
                        <ChevronRight size={13} />
                      )}
                    </span>
                  </button>
                  {expanded[c.id] && (
                    <p className="text-xs text-gray-500 mt-1">{c.hint}</p>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
