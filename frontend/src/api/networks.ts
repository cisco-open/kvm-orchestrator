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

/**
 * Networks catalog (v0.15.0).
 *
 * Admin-curated registry of libvirt bridges. The list endpoint is open
 * to all authenticated users — the backend filters down to
 * user_visible=1 rows for non-admins, so the Quick Create dropdown can
 * call `listNetworks()` directly without an isAdmin branch.
 *
 * Mutations are admin-only (the backend `@admin_required` decorator
 * returns 403 for non-admins, surfaced as an axios error).
 */

export interface Network {
  id: number;
  name: string;
  bridge_name: string;
  vlan_id: number | null;
  description: string | null;
  user_visible: boolean;
  is_default: boolean;
  created_at: string;
  // Only present on the list endpoint (LEFT JOIN over vms.vlan).
  vm_count?: number;
}

export interface CreateNetworkFields {
  name: string;
  bridge_name: string;
  vlan_id?: number | null;
  description?: string;
  user_visible?: boolean;
  is_default?: boolean;
}

export interface UpdateNetworkFields {
  name?: string;
  bridge_name?: string;
  vlan_id?: number | null;
  description?: string;
  user_visible?: boolean;
  is_default?: boolean;
}

export async function listNetworks(): Promise<Network[]> {
  const response = await client.get<{ networks: Network[] }>("/networks");
  return response.data.networks;
}

export async function getNetwork(id: number): Promise<Network> {
  const response = await client.get<Network>(`/networks/${id}`);
  return response.data;
}

export async function createNetwork(
  fields: CreateNetworkFields,
): Promise<{ id: number }> {
  const response = await client.post<{ id: number; ok: boolean }>(
    "/networks",
    fields,
  );
  return { id: response.data.id };
}

export async function updateNetwork(
  id: number,
  fields: UpdateNetworkFields,
): Promise<void> {
  await client.put(`/networks/${id}`, fields);
}

export async function deleteNetwork(id: number): Promise<void> {
  await client.delete(`/networks/${id}`);
}
