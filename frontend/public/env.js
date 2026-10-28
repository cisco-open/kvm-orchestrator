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

// Default runtime config. The UI container entrypoint overwrites this file at
// startup from the ENVIRONMENT and BRANDING pod env vars. Kept in public/ so
// `vite dev` and a plain build serve a sane default: the debranded open-source
// KVM Orchestrator build, live (blue) environment.
window.__ENV__ = { ENVIRONMENT: "live", BRANDING: "kvm-orchestrator" };
