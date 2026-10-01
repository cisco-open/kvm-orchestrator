// Runtime environment accessor. Reads window.__ENV__ injected by the UI
// container entrypoint (see public/env.js + Dockerfile entrypoint). Kept tiny
// and dependency-free so it can be imported anywhere without side effects.

import { brand } from "./branding";

export interface RuntimeEnv {
  ENVIRONMENT: string;
  BRANDING?: string;
}

declare global {
  interface Window {
    __ENV__?: RuntimeEnv;
  }
}

export function getEnvironment(): string {
  return (window.__ENV__?.ENVIRONMENT ?? "live").toLowerCase();
}

/** True when running as a test environment (red theme + warning banner). */
export function isTestEnv(): boolean {
  return getEnvironment() === "test";
}

/**
 * App display name. Driven by the active brand (BRANDING), with a "Test"
 * prefix overlaid when ENVIRONMENT=test so a test instance of any brand is
 * clearly marked (e.g. "Test KVMaestro", "Test Custom Brand").
 */
export function appName(): string {
  const name = brand().name;
  return isTestEnv() ? `Test ${name}` : name;
}
