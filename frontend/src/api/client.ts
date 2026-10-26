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

import axios from "axios";

const client = axios.create({
  baseURL: "/api",
  headers: { "Content-Type": "application/json" },
});

// Attach JWT token to all requests
client.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Handle 401 responses — attempt token refresh via native fetch (bypasses
// axios interceptors, avoiding header overwrite and deadlock), then retry once.
let _refreshing: Promise<string> | null = null;

client.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;
    // Skip retry for the refresh endpoint itself, or if already retried
    if (
      error.response?.status === 401 &&
      original &&
      !original._retry &&
      !original.url?.includes("/auth/refresh")
    ) {
      original._retry = true;
      try {
        // Deduplicate concurrent refresh attempts
        if (!_refreshing) {
          _refreshing = (async () => {
            const refreshToken = localStorage.getItem("refresh_token");
            // Use native fetch — bypasses axios request interceptor so the
            // Authorization header is NOT overwritten with the access token.
            const res = await fetch("/api/auth/refresh", {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                Authorization: `Bearer ${refreshToken}`,
              },
            });
            if (!res.ok) throw new Error("refresh failed");
            const data = await res.json();
            const newToken: string = data.access_token;
            localStorage.setItem("access_token", newToken);
            return newToken;
          })().finally(() => { _refreshing = null; });
        }
        const newToken = await _refreshing;
        original.headers.Authorization = `Bearer ${newToken}`;
        return client(original);
      } catch {
        // Refresh failed — clear session and go to login
        localStorage.removeItem("access_token");
        localStorage.removeItem("refresh_token");
        localStorage.removeItem("is_admin");
        localStorage.removeItem("username");
        window.location.href = "/";
      }
    }
    return Promise.reject(error);
  }
);

export default client;
