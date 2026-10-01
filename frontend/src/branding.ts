// Brand presets. Selected at runtime by window.__ENV__.BRANDING (written by the
// UI container entrypoint from the BRANDING pod env var — see public/env.js +
// docker-entrypoint.sh). This lets the SAME image ship either as the default
// "KVMaestro" build or as your own white-labelled build, purely by namespace
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
}

// Default, open-source identity.
const KVMAESTRO: Brand = {
  name: "KVMaestro",
  logo: "/kvmaestro.svg",
  slug: "KVMaestro",
};

const BRANDS: Record<string, Brand> = {
  kvmaestro: KVMAESTRO,
  // Example custom/white-label brand. Copy this, rename the key, and provide
  // your own logo under frontend/public/. Set config.BRANDING to the key.
  custom: {
    name: "Custom Brand",
    logo: "/custom-logo.svg",
    slug: "Custom Brand",
  },
};

const DEFAULT_BRAND = "kvmaestro";

/** Resolve the active brand key from runtime env, falling back to the default. */
export function brandKey(): string {
  const key = (window.__ENV__?.BRANDING ?? DEFAULT_BRAND).toLowerCase();
  return key in BRANDS ? key : DEFAULT_BRAND;
}

/** The active brand preset. */
export function brand(): Brand {
  return BRANDS[brandKey()] ?? KVMAESTRO;
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

