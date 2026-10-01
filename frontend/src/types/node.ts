export interface NodeTenant {
  id: number;
  name: string;
}

export interface Node {
  id: number;
  hostname: string;
  ip_address: string;
  ssh_host: string | null;
  ssh_port: number;
  ssh_user: string | null;
  ssh_key_id: number | null;
  ssh_key_name: string | null;
  cpu_model: string | null;
  status: "online" | "offline" | "maintenance" | "fenced";
  total_vcpus: number;
  total_ram_mb: number;
  available_vcpus: number;
  available_ram_mb: number;
  agent_version: string | null;
  last_heartbeat_at: string | null;
  ha_enabled: boolean;
  ha_priority: number;
  // v0.25 — cordon flag. When falsy (0/false) the node is cordoned and
  // accepts no new VMs (placement + migrate-target UI exclude it).
  // Existing VMs stay running; the node remains online. Comes back as
  // 0/1 from the raw-SQL layer, so coerce with `!!` / `=== 1`.
  schedulable: boolean | number;
  // v0.22.3 — hypervisor stack versions collected on each SSH poll.
  // Displayed as a passive info block on NodeDetail; may be null on
  // nodes that never came online since the migration.
  kvm_version: string | null;
  qemu_version: string | null;
  libvirt_version: string | null;
  kernel_version: string | null;
  // v0.18 — tenants a node is tagged with. `[]` means admin-only.
  // DEFAULT (id=4, since v0.20) means "visible to all authenticated
  // users" and puts the node in the shared placement fallback pool.
  // PERSONAL/ODaaSE (id=3, since the v0.21 merge) is owner-only and
  // never appears in this list — it is a VM-scoped tenant with no
  // node-visibility role.
  tenants: NodeTenant[];
}

export interface SshKey {
  id: number;
  name: string;
  created_at: string;
}
