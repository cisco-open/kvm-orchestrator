import { useState } from "react";
import { createPortal } from "react-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Pencil, Trash2, X, Network as NetIcon, CheckCircle2, Eye, EyeOff, Star } from "lucide-react";
import {
  listNetworks,
  createNetwork,
  updateNetwork,
  deleteNetwork,
  type Network,
} from "../api/networks";

// ---------------------------------------------------------------------------
// Modal portal — same pattern as Tenants.tsx so it can never be clipped.
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
    document.body,
  );
}

// ---------------------------------------------------------------------------
// Create / Edit modal
// ---------------------------------------------------------------------------
function NetworkModal({
  network,
  onClose,
}: {
  network: Network | null; // null = create mode
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(network?.name ?? "");
  const [bridgeName, setBridgeName] = useState(network?.bridge_name ?? "");
  const [vlanId, setVlanId] = useState<string>(
    network?.vlan_id != null ? String(network.vlan_id) : "",
  );
  const [description, setDescription] = useState(network?.description ?? "");
  const [userVisible, setUserVisible] = useState(network?.user_visible ?? false);
  const [isDefault, setIsDefault] = useState(network?.is_default ?? false);
  const [error, setError] = useState("");

  const mutation = useMutation({
    mutationFn: async () => {
      const parsedVlan = vlanId.trim() === "" ? null : Number(vlanId.trim());
      const payload = {
        name: name.trim(),
        bridge_name: bridgeName.trim(),
        vlan_id: parsedVlan,
        description: description.trim(),
        user_visible: userVisible,
        is_default: isDefault,
      };
      if (network) {
        await updateNetwork(network.id, payload);
      } else {
        await createNetwork(payload);
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["networks"] });
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
    if (!bridgeName.trim()) {
      setError("Bridge name is required");
      return;
    }
    if (vlanId.trim() !== "" && Number.isNaN(Number(vlanId.trim()))) {
      setError("VLAN ID must be a number");
      return;
    }
    mutation.mutate();
  };

  return (
    <Modal
      title={network ? `Edit network: ${network.name}` : "New network"}
      onClose={onClose}
    >
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
            placeholder="VLAN 35 (Lab)"
            className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            autoFocus
          />
          <p className="text-xs text-gray-500 mt-1">
            Cosmetic display name shown in dropdowns.
          </p>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Bridge name</label>
          <input
            type="text"
            value={bridgeName}
            onChange={(e) => setBridgeName(e.target.value)}
            placeholder="ovs-br35"
            className="w-full border rounded px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <p className="text-xs text-gray-500 mt-1">
            The libvirt bridge identifier — must match what nodes actually expose.
            Stored in <span className="font-mono">vms.vlan</span>.
          </p>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            VLAN ID <span className="text-gray-400 font-normal">(optional)</span>
          </label>
          <input
            type="number"
            value={vlanId}
            onChange={(e) => setVlanId(e.target.value)}
            placeholder="35"
            className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
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

        <div className="space-y-2 pt-2 border-t">
          <label className="flex items-start gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={userVisible}
              onChange={(e) => setUserVisible(e.target.checked)}
              className="mt-0.5"
            />
            <div>
              <div className="text-sm font-medium text-gray-700">Visible to non-admin users</div>
              <p className="text-xs text-gray-500">
                If unchecked, only admins can pick this network when creating VMs.
              </p>
            </div>
          </label>

          <label className="flex items-start gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={isDefault}
              onChange={(e) => setIsDefault(e.target.checked)}
              className="mt-0.5"
            />
            <div>
              <div className="text-sm font-medium text-gray-700">Default network</div>
              <p className="text-xs text-gray-500">
                Pre-selected in the Quick Create form. Only one network may be the default —
                checking this clears the flag on any other network.
              </p>
            </div>
          </label>
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
            {mutation.isPending ? "Saving..." : network ? "Save changes" : "Create network"}
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
  network,
  onClose,
}: {
  network: Network;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [error, setError] = useState("");

  const mutation = useMutation({
    mutationFn: () => deleteNetwork(network.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["networks"] });
      onClose();
    },
    onError: (e: any) => {
      setError(e.response?.data?.error ?? "Delete failed");
    },
  });

  const vmCount = network.vm_count ?? 0;

  return (
    <Modal title={`Delete network: ${network.name}`} onClose={onClose}>
      <div className="space-y-4">
        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 px-3 py-2 rounded text-sm">
            {error}
          </div>
        )}

        <p className="text-sm text-gray-700">
          This removes the network from the catalog. The bridge itself isn't touched on any
          host — existing VMs continue to use it, but no new VMs can be created against it
          until it's re-added.
        </p>

        {vmCount > 0 && (
          <div className="bg-amber-50 border border-amber-200 text-amber-800 px-3 py-2 rounded text-sm">
            <strong>{vmCount}</strong> VM{vmCount === 1 ? "" : "s"} currently use bridge{" "}
            <span className="font-mono">{network.bridge_name}</span>. Delete will be rejected
            until they are moved.
          </div>
        )}

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
            {mutation.isPending ? "Deleting..." : "Delete network"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------
export default function Networks() {
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<Network | null>(null);
  const [deleting, setDeleting] = useState<Network | null>(null);

  const { data: networks = [], isLoading, isError } = useQuery<Network[]>({
    queryKey: ["networks"],
    queryFn: listNetworks,
  });

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Networks</h1>
          <p className="text-sm text-gray-500 mt-1">
            Admin-curated catalog of libvirt bridges. Networks marked
            <span className="mx-1 inline-flex items-center gap-1 px-1.5 py-0.5 bg-green-50 text-green-700 rounded text-xs">
              <Eye size={11} /> visible
            </span>
            are selectable by non-admin users when creating VMs. The
            <span className="mx-1 inline-flex items-center gap-1 px-1.5 py-0.5 bg-amber-50 text-amber-700 rounded text-xs">
              <Star size={11} /> default
            </span>
            is pre-selected in Quick Create.
          </p>
        </div>
        <button
          onClick={() => setCreateOpen(true)}
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg"
        >
          <Plus size={16} />
          New network
        </button>
      </div>

      {isLoading ? (
        <p className="text-sm text-gray-500">Loading...</p>
      ) : isError ? (
        <p className="text-sm text-red-600">Failed to load networks.</p>
      ) : (
        <div className="bg-white border rounded-lg overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Name
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Bridge
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  VLAN
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Visibility
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Default
                </th>
                <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                  VMs
                </th>
                <th className="px-4 py-2"></th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {networks.map((n) => (
                <tr key={n.id}>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <NetIcon size={14} className="text-blue-500" />
                      <span className="font-medium text-sm">{n.name}</span>
                    </div>
                    {n.description && (
                      <div className="text-xs text-gray-500 mt-0.5 ml-6">{n.description}</div>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs font-mono text-gray-600">
                    {n.bridge_name}
                  </td>
                  <td className="px-4 py-3 text-sm tabular-nums">
                    {n.vlan_id ?? <span className="text-gray-400">—</span>}
                  </td>
                  <td className="px-4 py-3">
                    {n.user_visible ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-green-100 text-green-800 rounded-full">
                        <Eye size={11} />
                        Visible
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-gray-100 text-gray-600 rounded-full">
                        <EyeOff size={11} />
                        Admin only
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {n.is_default ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-amber-100 text-amber-800 rounded-full">
                        <CheckCircle2 size={11} />
                        Default
                      </span>
                    ) : (
                      <span className="text-gray-300 text-xs">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right text-sm tabular-nums">
                    {n.vm_count ?? 0}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        onClick={() => setEditing(n)}
                        className="p-1.5 text-gray-500 hover:text-blue-600 hover:bg-blue-50 rounded"
                        title="Edit"
                      >
                        <Pencil size={14} />
                      </button>
                      <button
                        onClick={() => setDeleting(n)}
                        className="p-1.5 text-gray-500 hover:text-red-600 hover:bg-red-50 rounded"
                        title="Delete"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {networks.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-sm text-gray-500">
                    No networks defined yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {createOpen && <NetworkModal network={null} onClose={() => setCreateOpen(false)} />}
      {editing && <NetworkModal network={editing} onClose={() => setEditing(null)} />}
      {deleting && <DeleteModal network={deleting} onClose={() => setDeleting(null)} />}
    </div>
  );
}
