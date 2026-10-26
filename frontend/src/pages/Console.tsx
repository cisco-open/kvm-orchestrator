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

import VncScreen from "../components/VncScreen";

/**
 * Standalone console page (target of the pop-out /console.html?token=…&vm=…).
 * Thin wrapper: reads the one-shot token from the URL and hands it to the
 * shared VncScreen. The embedded Console tab on the VM detail page uses the
 * same VncScreen component directly.
 */
export default function Console() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token") ?? "";
  const vmName = params.get("vm") ?? "VM Console";

  return <VncScreen token={token} vmName={vmName} embedded={false} />;
}
