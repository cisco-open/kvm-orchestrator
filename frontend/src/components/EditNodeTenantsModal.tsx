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

// v0.18 — admin-only modal to edit which tenants may see and place VMs
// on a given node. Extracted to a component so both the Nodes list page
// and the NodeDetail page can use it.
//
// Empty tenant set = admin-only visibility for the node. DEFAULT
// (id=4, v0.20+) and non-builtin tenants are assignable; SHARED (id=1)
// and PERSONAL/ODaaSE (id=3, formerly id=2 pre-v0.21) are VM-scoped
// concepts and never applied to nodes.

import { useState } from "react";
import { createPortal } from "react-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { updateNodeTenants } from "../api/nodes";
import { listTenants, type Tenant } from "../api/tenants";
import type { Node } from "../types/node";

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

export default function EditNodeTenantsModal({
  node,
  onClose,
}: {
  node: Node;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [tenantIds, setTenantIds] = useState<Set<number>>(
    new Set(node.tenants.map((t) => t.id)),
  );
  const [error, setError] = useState("");

  const { data: tenants = [] } = useQuery<Tenant[]>({
    queryKey: ["tenants"],
    queryFn: listTenants,
    retry: false,
  });

  const mutation = useMutation({
    mutationFn: () => updateNodeTenants(node.id, Array.from(tenantIds)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["nodes"] });
      onClose();
    },
    onError: (e: any) => setError(e.response?.data?.error ?? "Failed to update tenants"),
  });

  const toggleTenant = (id: number) => {
    setTenantIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    mutation.mutate();
  };

  // Hide SHARED (id=1) and PERSONAL/ODaaSE (id=3). DEFAULT (id=4) and
  // every user-created tenant are valid node targets. (id=2 no longer
  // exists post-v0.21 merge.)
  const nodeAssignableTenants = tenants.filter((t) => t.id !== 1 && t.id !== 3);

  return (
    <Modal title={`Edit tenants — ${node.hostname}`} onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <p className="text-sm text-red-600">{error}</p>}

        <p className="text-sm text-gray-600">
          Tick every tenant that should be allowed to see this node in the
          UI and place new VMs on it. DEFAULT = shared placement pool
          visible to all authenticated users; VMs land here when their
          own tenant has no dedicated capacity.
        </p>

        <div className="border rounded-lg divide-y max-h-72 overflow-y-auto">
          {nodeAssignableTenants.map((t) => (
            <label
              key={t.id}
              className="flex items-center gap-2 px-3 py-2 text-sm cursor-pointer hover:bg-gray-50"
            >
              <input
                type="checkbox"
                checked={tenantIds.has(t.id)}
                onChange={() => toggleTenant(t.id)}
              />
              <span className="font-medium">{t.name}</span>
              {t.id === 4 && (
                <span className="text-xs text-gray-500">
                  (shared placement pool — visible to all users)
                </span>
              )}
            </label>
          ))}
        </div>

        {tenantIds.size === 0 && (
          <p className="text-xs text-amber-600">
            No tenants selected — this node will be visible to admins only.
          </p>
        )}

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
            {mutation.isPending ? "Saving..." : "Save"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
