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

import { useState } from "react";
import { createPortal } from "react-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Pencil, Trash2, X, Users, Lock } from "lucide-react";
import {
  listTenants,
  createTenant,
  updateTenant,
  deleteTenant,
  type Tenant,
} from "../api/tenants";

// ---------------------------------------------------------------------------
// Modal portal — same pattern as Nodes.tsx so it can never be clipped.
// ---------------------------------------------------------------------------
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
    document.body
  );
}

// ---------------------------------------------------------------------------
// Create / Edit modal (shared form)
// ---------------------------------------------------------------------------
function TenantModal({
  tenant,
  onClose,
}: {
  tenant: Tenant | null; // null = create mode
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(tenant?.name ?? "");
  const [adGroupDn, setAdGroupDn] = useState(tenant?.ad_group_dn ?? "");
  const [description, setDescription] = useState(tenant?.description ?? "");
  const [error, setError] = useState("");

  const mutation = useMutation({
    mutationFn: async () => {
      if (tenant) {
        await updateTenant(tenant.id, {
          name: name.trim(),
          ad_group_dn: adGroupDn.trim(),
          description: description.trim(),
        });
      } else {
        await createTenant({
          name: name.trim(),
          ad_group_dn: adGroupDn.trim(),
          description: description.trim(),
        });
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tenants"] });
      onClose();
    },
    onError: (e: any) => {
      setError(e.response?.data?.error ?? "Request failed");
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (!name.trim()) {
      setError("Name is required");
      return;
    }
    if (!adGroupDn.trim()) {
      setError("AD Group DN is required");
      return;
    }
    mutation.mutate();
  };

  return (
    <Modal title={tenant ? `Edit tenant: ${tenant.name}` : "New tenant"} onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 px-3 py-2 rounded text-sm">
            {error}
          </div>
        )}

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="WME"
            className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            autoFocus
          />
          <p className="text-xs text-gray-500 mt-1">
            Short identifier shown to users. Cannot be <span className="font-mono">SHARED</span>.
          </p>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">AD Group DN</label>
          <input
            type="text"
            value={adGroupDn}
            onChange={(e) => setAdGroupDn(e.target.value)}
            placeholder="CN=kvm-admins,OU=groups,DC=example,DC=com"
            className="w-full border rounded px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <p className="text-xs text-gray-500 mt-1">
            Full distinguished name. Any user whose LDAP <span className="font-mono">memberOf</span> contains
            this DN becomes a member of the tenant at their next login.
          </p>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional"
            rows={2}
            className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 rounded-lg"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={mutation.isPending}
            className="px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg disabled:opacity-50"
          >
            {mutation.isPending ? "Saving..." : tenant ? "Save changes" : "Create tenant"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Confirm-delete modal
// ---------------------------------------------------------------------------
function DeleteModal({
  tenant,
  onClose,
}: {
  tenant: Tenant;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [error, setError] = useState("");

  const mutation = useMutation({
    mutationFn: () => deleteTenant(tenant.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tenants"] });
      onClose();
    },
    onError: (e: any) => {
      setError(e.response?.data?.error ?? "Delete failed");
    },
  });

  return (
    <Modal title={`Delete tenant: ${tenant.name}`} onClose={onClose}>
      <div className="space-y-4">
        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 px-3 py-2 rounded text-sm">
            {error}
          </div>
        )}

        <p className="text-sm text-gray-700">
          This will remove the tenant and any user memberships. VMs currently tagged with this
          tenant must be re-tagged first.
        </p>

        {tenant.vm_count ? (
          <div className="bg-amber-50 border border-amber-200 text-amber-800 px-3 py-2 rounded text-sm">
            <strong>{tenant.vm_count}</strong> VM{tenant.vm_count === 1 ? "" : "s"} currently
            tagged with this tenant. Delete will be rejected until they are moved.
          </div>
        ) : null}

        <div className="flex justify-end gap-2 pt-2">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 rounded-lg"
          >
            Cancel
          </button>
          <button
            onClick={() => {
              setError("");
              mutation.mutate();
            }}
            disabled={mutation.isPending}
            className="px-4 py-2 text-sm font-medium text-white bg-red-600 hover:bg-red-700 rounded-lg disabled:opacity-50"
          >
            {mutation.isPending ? "Deleting..." : "Delete tenant"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------
export default function Tenants() {
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<Tenant | null>(null);
  const [deleting, setDeleting] = useState<Tenant | null>(null);

  const { data: tenants = [], isLoading, isError } = useQuery<Tenant[]>({
    queryKey: ["tenants"],
    queryFn: listTenants,
  });

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Tenants</h1>
          <p className="text-sm text-gray-500 mt-1">
            Multi-tenancy units bound to AD groups. Members of a tenant can see and operate the VMs tagged
            with it. <span className="font-mono">SHARED</span> is visible to every authenticated user.
          </p>
        </div>
        <button
          onClick={() => setCreateOpen(true)}
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg"
        >
          <Plus size={16} />
          New tenant
        </button>
      </div>

      {isLoading ? (
        <p className="text-sm text-gray-500">Loading...</p>
      ) : isError ? (
        <p className="text-sm text-red-600">Failed to load tenants.</p>
      ) : (
        <div className="bg-white border rounded-lg overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Name
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  AD Group DN
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Description
                </th>
                <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Members
                </th>
                <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                  VMs
                </th>
                <th className="px-4 py-2"></th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {tenants.map((t) => (
                <tr key={t.id}>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      {t.is_builtin ? (
                        <Lock size={14} className="text-gray-400" />
                      ) : (
                        <Users size={14} className="text-blue-500" />
                      )}
                      <span className="font-medium text-sm">{t.name}</span>
                      {t.is_builtin && (
                        <span className="text-xs bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded">
                          built-in
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-xs font-mono text-gray-600">
                    {t.ad_group_dn ?? <span className="text-gray-400 italic">— none —</span>}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-600">
                    {t.description ?? <span className="text-gray-400">—</span>}
                  </td>
                  <td className="px-4 py-3 text-right text-sm tabular-nums">
                    {t.member_count ?? 0}
                  </td>
                  <td className="px-4 py-3 text-right text-sm tabular-nums">
                    {t.vm_count ?? 0}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {!t.is_builtin && (
                      <div className="flex items-center justify-end gap-1">
                        <button
                          onClick={() => setEditing(t)}
                          className="p-1.5 text-gray-500 hover:text-blue-600 hover:bg-blue-50 rounded"
                          title="Edit"
                        >
                          <Pencil size={14} />
                        </button>
                        <button
                          onClick={() => setDeleting(t)}
                          className="p-1.5 text-gray-500 hover:text-red-600 hover:bg-red-50 rounded"
                          title="Delete"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
              {tenants.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-sm text-gray-500">
                    No tenants yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {createOpen && <TenantModal tenant={null} onClose={() => setCreateOpen(false)} />}
      {editing && <TenantModal tenant={editing} onClose={() => setEditing(null)} />}
      {deleting && <DeleteModal tenant={deleting} onClose={() => setDeleting(null)} />}
    </div>
  );
}
