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

export interface Tenant {
  id: number;
  name: string;
  ad_group_dn: string | null;
  description: string | null;
  is_builtin: boolean;
  created_at: string;
  vm_count?: number;
  member_count?: number;
}

export interface CreateTenantFields {
  name: string;
  ad_group_dn: string;
  description?: string;
}

export interface UpdateTenantFields {
  name?: string;
  ad_group_dn?: string;
  description?: string;
}

export async function listTenants(): Promise<Tenant[]> {
  const response = await client.get<{ tenants: Tenant[] }>("/tenants");
  return response.data.tenants;
}

export async function getTenant(id: number): Promise<Tenant> {
  const response = await client.get<Tenant>(`/tenants/${id}`);
  return response.data;
}

export async function createTenant(fields: CreateTenantFields): Promise<{ id: number }> {
  const response = await client.post<{ id: number; ok: boolean }>("/tenants", fields);
  return { id: response.data.id };
}

export async function updateTenant(id: number, fields: UpdateTenantFields): Promise<void> {
  await client.put(`/tenants/${id}`, fields);
}

export async function deleteTenant(id: number): Promise<void> {
  await client.delete(`/tenants/${id}`);
}
