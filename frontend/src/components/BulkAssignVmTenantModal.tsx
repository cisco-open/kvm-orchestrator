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

// v0.19.0 — admin-only modal to bulk-assign one tenant to many VMs.
//
// Called from the VMs page when the admin has ticked one or more row
// checkboxes. Radio-button single-select (a VM belongs to exactly one
// tenant). Also offers "(none) — admin-only" and the built-in SHARED
// (id=1) / ODaaSE (id=3, the PERSONAL owner-only tenant since the
// v0.21 merge) targets, which are legitimate VM tenants (unlike nodes,
// where SHARED/PERSONAL are inapplicable).
//
// Server-side: PUT /api/vms/bulk-tenant, admin-only, transactional.

import { useState } from "react";
import { createPortal } from "react-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { bulkAssignVmTenant } from "../api/vms";
import { listTenants, type Tenant } from "../api/tenants";

function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg mx-4">
        <div className="flex items-center justify-between px-6 py-4 border-b">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X size={20} />
          </button>
        </div>
        <div className="px-6 py-5">{children}</div>
      </div>
    </div>,
    document.body,
  );
}

// Sentinel `-1` in local state represents the "(none) — admin-only" radio
// option, since HTML radio values must be strings and we still need to
// distinguish between "user picked null" and "user hasn't picked anything".
const UNTAGGED_SENTINEL = -1;

export default function BulkAssignVmTenantModal({
  vmIds,
  onClose,
}: {
  vmIds: number[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  // Default selection: the "(none) — admin-only" option. Admins usually
  // want to explicitly set a real tenant, so we don't pre-pick a real one
  // to avoid an accidental Save.
  const [selectedId, setSelectedId] = useState<number>(UNTAGGED_SENTINEL);
  const [error, setError] = useState("");

  const { data: tenants = [] } = useQuery<Tenant[]>({
    queryKey: ["tenants"],
    queryFn: listTenants,
    retry: false,
  });

  const mutation = useMutation({
    mutationFn: () =>
      bulkAssignVmTenant(
        vmIds,
        selectedId === UNTAGGED_SENTINEL ? null : selectedId,
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["vms"] });
      onClose();
    },
    onError: (e: any) =>
      setError(e.response?.data?.error ?? "Failed to assign tenant"),
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    mutation.mutate();
  };

  // v0.20: exclude DEFAULT (id=4) from VM assignment. DEFAULT is the
  // shared placement pool — tagging a VM with DEFAULT would blur the
  // semantics (nodes are in the DEFAULT pool; VMs are owned by real
  // tenants that place onto DEFAULT nodes as a fallback).
  //
  // ODaaSE (id=3) is included — since the v0.21 merge it is the
  // owner-only PERSONAL tenant. Admin bulk-assigning to ODaaSE will
  // fail server-side unless the target VMs already have owner_user_id
  // set (see `vm_service.vm_edit`: "Tenant 'ODaaSE' requires an
  // owner"). Use the individual Edit modal for one-off owner+tenant
  // flips.
  const vmAssignableTenants = tenants.filter((t) => t.id !== 4);

  return (
    <Modal
      title={`Assign tenant to ${vmIds.length} VM${vmIds.length === 1 ? "" : "s"}`}
      onClose={onClose}
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <p className="text-sm text-red-600">{error}</p>}

        <p className="text-sm text-gray-600">
          Pick the tenant these VMs should belong to. All {vmIds.length}{" "}
          selected VM{vmIds.length === 1 ? "" : "s"} will be updated.
        </p>

        <div className="border rounded-lg divide-y max-h-72 overflow-y-auto">
          <label className="flex items-center gap-2 px-3 py-2 text-sm cursor-pointer hover:bg-gray-50">
            <input
              type="radio"
              name="bulk-tenant"
              checked={selectedId === UNTAGGED_SENTINEL}
              onChange={() => setSelectedId(UNTAGGED_SENTINEL)}
            />
            <span className="font-medium">(none)</span>
            <span className="text-xs text-gray-500">
              — untagged, admin-only
            </span>
          </label>

          {vmAssignableTenants.map((t) => (
            <label
              key={t.id}
              className="flex items-center gap-2 px-3 py-2 text-sm cursor-pointer hover:bg-gray-50"
            >
              <input
                type="radio"
                name="bulk-tenant"
                checked={selectedId === t.id}
                onChange={() => setSelectedId(t.id)}
              />
              <span className="font-medium">{t.name}</span>
              {t.is_builtin && (
                <span className="text-xs text-gray-500">(built-in)</span>
              )}
            </label>
          ))}
        </div>

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="border rounded-lg px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={mutation.isPending}
            className="bg-blue-600 text-white rounded-lg px-4 py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            {mutation.isPending ? "Saving..." : "Assign"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
