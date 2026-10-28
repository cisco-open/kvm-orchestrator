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

// Brand presets. Selected at runtime by window.__ENV__.BRANDING (written by the
// UI container entrypoint from the BRANDING pod env var — see public/env.js +
// docker-entrypoint.sh). This lets the SAME image ship either as the default
// "KVM Orchestrator" build or as your own white-labelled build, purely by namespace
// config — no rebrand-rebuild.
//
// To add your own brand: drop a logo in frontend/public/ (e.g. custom-logo.svg),
// add a preset below keyed by a lowercase name, and set BRANDING=<key> in your
// Helm values (config.BRANDING). See the "custom" example.
//
// Branding is INDEPENDENT of ENVIRONMENT (live/test). ENVIRONMENT only overlays
// the red "TEST ENVIRONMENT" theming (see env.ts / App.tsx) on top of whichever
// brand is active, so you can run a test instance of any brand.

export interface Brand {
  /** Display name shown in the sidebar, login, page title and banners. */
  name: string;
  /** Logo served from the nginx public root (see public/). */
  logo: string;
  /** Short blurb source label used in copy (e.g. Metrics page). */
  slug: string;
  /**
   * Optional base URL for fetching per-user avatar photos from a corporate
   * directory, as `${directoryPhotoBase}${username}.jpg`. Leave undefined
   * (the default) to show an initial-letter avatar and never call out to an
   * external host. Only set this if you operate a directory that serves photos
   * keyed by username and you are comfortable the browser will request it.
   */
  directoryPhotoBase?: string;
}

// Default, open-source identity.
const KVM_ORCHESTRATOR: Brand = {
  name: "KVM Orchestrator",
  logo: "/kvm-orchestrator.svg",
  slug: "KVM Orchestrator",
  // No directoryPhotoBase — initial-letter avatars, no external photo fetch.
};

const BRANDS: Record<string, Brand> = {
  "kvm-orchestrator": KVM_ORCHESTRATOR,
  // Example custom/white-label brand. Copy this, rename the key, and provide
  // your own logo under frontend/public/. Set config.BRANDING to the key.
  // Optionally add `directoryPhotoBase: "https://.../photos/"` if you have a
  // corporate photo directory keyed by username.
  custom: {
    name: "Custom Brand",
    logo: "/custom-logo.svg",
    slug: "Custom Brand",
  },
};

const DEFAULT_BRAND = "kvm-orchestrator";

/** Resolve the active brand key from runtime env, falling back to the default. */
export function brandKey(): string {
  const key = (window.__ENV__?.BRANDING ?? DEFAULT_BRAND).toLowerCase();
  return key in BRANDS ? key : DEFAULT_BRAND;
}

/** The active brand preset. */
export function brand(): Brand {
  return BRANDS[brandKey()] ?? KVM_ORCHESTRATOR;
}

/**
 * Apply brand-driven document chrome (favicon + fallback title) at runtime.
 * Called once from each HTML entry point's bootstrap. The title arg lets the
 * console window keep its own "VM Console" title while still picking up the
 * branded favicon. Safe to call before React mounts.
 */
export function applyBrandChrome(title?: string): void {
  const b = brand();
  if (title) document.title = title;
  const link = document.getElementById("favicon") as HTMLLinkElement | null;
  if (link) link.href = b.logo;
}

