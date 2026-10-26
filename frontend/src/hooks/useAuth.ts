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

import { create } from "zustand";
import { getMe } from "../api/auth";
import type { MeTenant } from "../api/auth";

interface AuthState {
  isAuthenticated: boolean;
  isAdmin: boolean;
  username: string | null;
  /**
   * Human-readable name from AD ("givenName surname") — preferred over
   * `username` for the sidebar identity strip. Falls back to `username`
   * if AD never populated this column.
   */
  displayName: string | null;
  /**
   * The signed-in user's numeric DB id, mirrored from `/api/auth/me`.
   * Used client-side to drive owner-only UI affordances (e.g. allow a
   * non-admin to flip *their own* VM between SHARED and PERSONAL in
   * the Edit modal). Server-side authorisation is the real gate; this
   * is just for UX so we don't show controls that will 403.
   */
  userId: number | null;
  /**
   * AD-mapped tenants the user is a member of. SHARED + PERSONAL are
   * intentionally excluded here — every user implicitly belongs to
   * both, and the UI hard-codes their ids. Used to populate the tenant
   * selector at VM-create time for non-admins.
   */
  tenants: MeTenant[];
  login: (accessToken: string, refreshToken: string) => Promise<void>;
  logout: () => void;
}

const storedUserId = localStorage.getItem("user_id");
const storedTenantsRaw = localStorage.getItem("tenants");
let storedTenants: MeTenant[] = [];
try {
  storedTenants = storedTenantsRaw ? JSON.parse(storedTenantsRaw) : [];
} catch {
  // Corrupted localStorage — start fresh; next login() will repopulate.
  storedTenants = [];
}

export const useAuth = create<AuthState>((set) => ({
  isAuthenticated: !!localStorage.getItem("access_token"),
  isAdmin: localStorage.getItem("is_admin") === "true",
  username: localStorage.getItem("username"),
  displayName: localStorage.getItem("display_name"),
  userId: storedUserId ? Number(storedUserId) : null,
  tenants: storedTenants,

  login: async (accessToken: string, refreshToken: string) => {
    localStorage.setItem("access_token", accessToken);
    localStorage.setItem("refresh_token", refreshToken);
    set({ isAuthenticated: true });

    // Fetch profile to get is_admin + id + display_name + tenants
    try {
      const me = await getMe();
      localStorage.setItem("is_admin", String(me.is_admin));
      localStorage.setItem("username", me.username);
      localStorage.setItem("user_id", String(me.id));
      if (me.display_name) {
        localStorage.setItem("display_name", me.display_name);
      } else {
        localStorage.removeItem("display_name");
      }
      localStorage.setItem("tenants", JSON.stringify(me.tenants ?? []));
      set({
        isAdmin:     me.is_admin,
        username:    me.username,
        displayName: me.display_name,
        userId:      me.id,
        tenants:     me.tenants ?? [],
      });
    } catch {
      // Non-fatal — user is logged in but admin status unknown
    }
  },

  logout: () => {
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
    localStorage.removeItem("is_admin");
    localStorage.removeItem("username");
    localStorage.removeItem("display_name");
    localStorage.removeItem("user_id");
    localStorage.removeItem("tenants");
    set({
      isAuthenticated: false,
      isAdmin:         false,
      username:        null,
      displayName:     null,
      userId:          null,
      tenants:         [],
    });
  },
}));
