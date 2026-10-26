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

import client from "./client";
import type { User } from "../types/user";

export async function listUsers(): Promise<User[]> {
  const response = await client.get<{ users: User[] }>("/users");
  return response.data.users;
}

// ---------------------------------------------------------------------------
// v0.14.2 — caller's saved SSH public keys (Quick Create pre-fill)
// ---------------------------------------------------------------------------

/**
 * One row from `user_ssh_pubkeys`. Identity is the SHA-256 fingerprint
 * of the wire-format key blob (matches `ssh-keygen -lf`).
 */
export interface SshPubkey {
  id: number;
  fingerprint: string;
  /** Original OpenSSH pubkey line (`<algo> <base64> [comment]`). */
  pubkey: string;
  /** Trailing comment if the user pasted one, else null. */
  comment: string | null;
  created_at: string;
  /** Bumped on every successful Quick Create. Pre-fill order key. */
  last_used_at: string;
}

/**
 * Fetch all keys saved against the caller's account, most-recently-used
 * first. The QuickCreateModal pre-fills its textarea with `[0].pubkey`
 * when this returns at least one row.
 */
export async function listMySshPubkeys(): Promise<SshPubkey[]> {
  const response = await client.get<{ ssh_pubkeys: SshPubkey[] }>(
    "/users/me/ssh-pubkeys",
  );
  return response.data.ssh_pubkeys;
}
