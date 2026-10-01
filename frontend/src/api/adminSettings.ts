import client from "./client";

/**
 * LDAP directory configuration, as surfaced to the admin Users tab.
 *
 * Open-sourcing model: this config lives in the DB (app_settings) and is
 * editable at runtime with no redeploy. The bind password is write-only — the
 * API never returns it, only `bind_password_set` telling us whether one is
 * stored.
 */
export interface LdapConfig {
  enabled: boolean;
  uri: string;
  base_dn: string;
  bind_dn: string;
  bind_password_set: boolean;
  user_filter: string;
  group_base_dn: string;
  admin_group: string;
}

/** Fields an admin can submit. `bind_password` is optional/write-only. */
export interface LdapConfigUpdate {
  enabled?: boolean;
  uri?: string;
  base_dn?: string;
  bind_dn?: string;
  /** Send a non-empty value to set; omit to keep the stored one. */
  bind_password?: string;
  /** Send true to remove the stored bind password. */
  clear_bind_password?: boolean;
  user_filter?: string;
  group_base_dn?: string;
  admin_group?: string;
}

export interface LdapTestResult {
  ok: boolean;
  message?: string;
  error?: string;
}

export async function getLdapConfig(): Promise<LdapConfig> {
  const res = await client.get<LdapConfig>("/admin/ldap-config");
  return res.data;
}

export async function updateLdapConfig(
  update: LdapConfigUpdate,
): Promise<{ status: string; changed: string[] }> {
  const res = await client.put("/admin/ldap-config", update);
  return res.data;
}

/** Test the currently SAVED config (service-account bind). Save before testing. */
export async function testLdapConfig(): Promise<LdapTestResult> {
  const res = await client.post<LdapTestResult>("/admin/ldap-config/test", {});
  return res.data;
}
