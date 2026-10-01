export interface VM {
  id: number;
  name: string;
  uuid: string | null;
  state: "running" | "stopped" | "paused" | "crashed" | "stopping" | "suspended" | "migrating" | "unknown";
  vcpus: number;
  ram_mb: number;
  vlan: string | null;
  disk_gb: number;
  disk_path: string | null;
  datastore: string | null;
  ip: string | null;
  last_seen_at: string | null;
  node_id: number;
  node_hostname: string;
  node_ip: string;
  node_cpu_model: string | null;
  tenant_id: number | null;
  tenant_name: string | null;      // v0.19.0: LEFT JOIN'd from `tenants` in list_vms
  owner_user_id: number | null;
  priority?: "critical" | "priority" | "default";  // HA priority class (admin-set)
  // v0.30 VM lifecycle / 1-year TTL
  expires_at?: string | null;
  renewed_at?: string | null;
  exempt?: boolean;
  lifecycle_state?: "active" | "expiring" | "auto_stopped" | "pending_delete";
}
