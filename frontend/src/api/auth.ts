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

export interface LoginRequest {
  username: string;
  password: string;
}

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
}

export async function login(data: LoginRequest): Promise<AuthTokens> {
  const response = await client.post<AuthTokens>("/auth/login", data);
  return response.data;
}

export async function refreshToken(): Promise<{ access_token: string }> {
  const token = localStorage.getItem("refresh_token");
  const response = await client.post(
    "/auth/refresh",
    {},
    { headers: { Authorization: `Bearer ${token}` } }
  );
  return response.data;
}

/** A tenant the user is an AD-group member of. SHARED/PERSONAL are
 *  excluded — every user implicitly belongs to those. */
export interface MeTenant {
  id: number;
  name: string;
  ad_group_dn: string | null;
  is_builtin: boolean;
}

export interface MeResponse {
  id: number;
  username: string;
  display_name: string | null;
  email: string | null;
  is_admin: boolean;
  tenants: MeTenant[];
}

export async function getMe(): Promise<MeResponse> {
  const response = await client.get<MeResponse>("/auth/me");
  return response.data;
}
