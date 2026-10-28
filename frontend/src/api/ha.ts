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

import client from "./client";

export interface HaStatus {
  env_ha_enabled: boolean;   // boot-level master arm (env var)
  runtime_enabled: boolean;  // instant kill-switch (Redis)
  armed: boolean;            // effective: env AND runtime
  failover_threshold: number;
}

export interface HaEvent {
  id: number;
  timestamp: string;
  node_id: number | null;
  vm_id: number | null;
  source_node_id: number | null;
  target_node_id: number | null;
  trigger: string;
  result: string;
  detail: Record<string, unknown> | null;
  node_hostname: string | null;
  source_hostname: string | null;
  target_hostname: string | null;
  vm_name: string | null;
}

export async function getHaStatus(): Promise<HaStatus> {
  const res = await client.get<HaStatus>("/ha/status");
  return res.data;
}

export async function setHaRuntime(enabled: boolean, reason?: string): Promise<HaStatus> {
  const res = await client.post<HaStatus>("/ha/runtime", { enabled, reason });
  return res.data;
}

export async function listHaEvents(limit = 100): Promise<HaEvent[]> {
  const res = await client.get<{ events: HaEvent[] }>("/ha/events", {
    params: { limit },
  });
  return res.data.events;
}

export async function failoverNode(nodeId: number): Promise<unknown> {
  const res = await client.post(`/nodes/${nodeId}/failover`, {});
  return res.data;
}

export async function readmitNode(nodeId: number): Promise<unknown> {
  const res = await client.post(`/nodes/${nodeId}/readmit`, {});
  return res.data;
}

export async function fenceNode(nodeId: number, force = false): Promise<unknown> {
  const res = await client.post(`/nodes/${nodeId}/fence`, { force });
  return res.data;
}

export interface ReadmitCheck {
  verdict: "safe" | "caution" | "unsafe";
  reachable: boolean;
  relocated_vms: string[];
  stale_domains: string[];
  running_stale: string[];
  watcher_conflicts: string[];
  reasons: string[];
}

export async function readmitCheck(nodeId: number): Promise<ReadmitCheck> {
  const res = await client.get<ReadmitCheck>(`/nodes/${nodeId}/readmit-check`);
  return res.data;
}

export interface AffectedVm {
  id: number;
  name: string;
  state: string;
  was_state: "running" | "off";
  vcpus: number;
  ram_mb: number;
  disk_gb: number;
  ip: string | null;
  priority: "critical" | "priority" | "default";
  has_xml: boolean;
}

export interface RecoveryTarget {
  id: number;
  hostname: string;
  cpu_model: string | null;
  arch_match: boolean;
  available_vcpus: number;
  available_ram_mb: number;
}

export interface AffectedNode {
  node_id: number;
  hostname: string;
  status: string;
  cpu_model: string | null;
  targets: RecoveryTarget[];
  vms: AffectedVm[];
}

export async function listAffectedVms(): Promise<AffectedNode[]> {
  const res = await client.get<{ nodes: AffectedNode[] }>("/ha/affected-vms");
  return res.data.nodes;
}

export interface HaSummary {
  events_24h: number;
  events_24h_by_result: Record<string, number>;
  node_status: Record<string, number>;
  nodes_needing_attention: number;
  last_event: HaEvent | null;
}

export async function getHaSummary(): Promise<HaSummary> {
  const res = await client.get<HaSummary>("/ha/summary");
  return res.data;
}
