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
 * clearly marked (e.g. "Test KVM Orchestrator", "Test Custom Brand").
 */
export function appName(): string {
  const name = brand().name;
  return isTestEnv() ? `Test ${name}` : name;
}
