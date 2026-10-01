import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Users as UsersIcon, Save, PlugZap, ShieldCheck } from "lucide-react";
import {
  getLdapConfig,
  updateLdapConfig,
  testLdapConfig,
  type LdapConfig,
  type LdapConfigUpdate,
  type LdapTestResult,
} from "../api/adminSettings";
import { listUsers } from "../api/users";
import type { User } from "../types/user";
import { useAuth } from "../hooks/useAuth";

const input =
  "w-full border rounded-md px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500";
const label = "block text-xs font-semibold text-gray-600 mb-1";

/** Editable form state — mirrors LdapConfig minus the write-only password. */
type FormState = Omit<LdapConfig, "bind_password_set">;

export default function UsersPage() {
  const isAdmin = useAuth((s) => s.isAdmin);
  const qc = useQueryClient();

  const { data: cfg } = useQuery<LdapConfig>({
    queryKey: ["ldap-config"],
    queryFn: getLdapConfig,
    enabled: isAdmin,
  });

  const { data: users = [] } = useQuery<User[]>({
    queryKey: ["users"],
    queryFn: listUsers,
    enabled: isAdmin,
  });

  const [form, setForm] = useState<FormState | null>(null);
  const [newPassword, setNewPassword] = useState("");
  const [clearPassword, setClearPassword] = useState(false);
  const [testResult, setTestResult] = useState<LdapTestResult | null>(null);

  // Hydrate the form once the config arrives.
  useEffect(() => {
    if (cfg && form === null) {
      const { bind_password_set: _ignored, ...rest } = cfg;
      setForm(rest);
    }
  }, [cfg, form]);

  const save = useMutation({
    mutationFn: (update: LdapConfigUpdate) => updateLdapConfig(update),
    onSuccess: () => {
      setNewPassword("");
      setClearPassword(false);
      qc.invalidateQueries({ queryKey: ["ldap-config"] });
    },
  });

  const test = useMutation({
    mutationFn: testLdapConfig,
    onSuccess: (r) => setTestResult(r),
  });

  if (!isAdmin) return <div className="p-6 text-gray-500">Admins only.</div>;
  if (!form || !cfg) return <div className="p-6 text-gray-500">Loading…</div>;

  const set = (k: keyof FormState, v: string | boolean) =>
    setForm({ ...form, [k]: v });

  const onSave = () => {
    const update: LdapConfigUpdate = { ...form };
    if (clearPassword) update.clear_bind_password = true;
    else if (newPassword) update.bind_password = newPassword;
    setTestResult(null);
    save.mutate(update);
  };

  return (
    <div className="p-6 max-w-5xl">
      <div className="flex items-center gap-2 mb-1">
        <UsersIcon size={22} />
        <h1 className="text-2xl font-bold">Users &amp; Authentication</h1>
      </div>
      <p className="text-sm text-gray-500 mb-6">
        Configure the LDAP/Active Directory used for user login and admin
        rights. Changes take effect immediately — no redeploy. The built-in{" "}
        <code>admin</code> account (set via <code>ADMIN_PASSWORD</code>) always
        works even if LDAP is down.
      </p>

      {/* ---------------- LDAP configuration ---------------- */}
      <section className="rounded-lg border bg-white p-5 mb-8">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">LDAP / Active Directory</h2>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => set("enabled", e.target.checked)}
            />
            <span className="font-medium">Enabled</span>
          </label>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="md:col-span-2">
            <label className={label}>Server URI</label>
            <input
              className={input}
              placeholder="ldaps://dc.example.com:636"
              value={form.uri}
              onChange={(e) => set("uri", e.target.value)}
            />
          </div>

          <div className="md:col-span-2">
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
              Base user LDAP
            </p>
          </div>
          <div>
            <label className={label}>Base DN</label>
            <input
              className={input}
              placeholder="dc=example,dc=com"
              value={form.base_dn}
              onChange={(e) => set("base_dn", e.target.value)}
            />
          </div>
          <div>
            <label className={label}>User search filter</label>
            <input
              className={input}
              placeholder="(sAMAccountName={username})"
              value={form.user_filter}
              onChange={(e) => set("user_filter", e.target.value)}
            />
          </div>
          <div>
            <label className={label}>Bind DN (service account)</label>
            <input
              className={input}
              placeholder="cn=svc-ldap,ou=svc,dc=example,dc=com"
              value={form.bind_dn}
              onChange={(e) => set("bind_dn", e.target.value)}
            />
          </div>
          <div>
            <label className={label}>
              Bind password{" "}
              {cfg.bind_password_set && !clearPassword && (
                <span className="text-green-600 font-normal">
                  (configured — leave blank to keep)
                </span>
              )}
            </label>
            <input
              className={input}
              type="password"
              placeholder={
                cfg.bind_password_set ? "••••••••" : "Enter bind password"
              }
              value={newPassword}
              disabled={clearPassword}
              onChange={(e) => setNewPassword(e.target.value)}
            />
            {cfg.bind_password_set && (
              <label className="flex items-center gap-1 text-xs text-red-600 mt-1">
                <input
                  type="checkbox"
                  checked={clearPassword}
                  onChange={(e) => setClearPassword(e.target.checked)}
                />
                Clear stored password
              </label>
            )}
          </div>

          <div className="md:col-span-2 mt-2">
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
              Admin LDAP
            </p>
          </div>
          <div>
            <label className={label}>Group search base DN</label>
            <input
              className={input}
              placeholder="ou=groups,dc=example,dc=com"
              value={form.group_base_dn}
              onChange={(e) => set("group_base_dn", e.target.value)}
            />
          </div>
          <div>
            <label className={label}>Admin group DN</label>
            <input
              className={input}
              placeholder="cn=kvm-admins,ou=groups,dc=example,dc=com"
              value={form.admin_group}
              onChange={(e) => set("admin_group", e.target.value)}
            />
            <p className="text-xs text-gray-400 mt-1">
              Members of this group get admin rights on login.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3 mt-5">
          <button
            onClick={onSave}
            disabled={save.isPending}
            className="flex items-center gap-1.5 bg-blue-600 text-white rounded-md px-4 py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            <Save size={15} /> {save.isPending ? "Saving…" : "Save"}
          </button>
          <button
            onClick={() => test.mutate()}
            disabled={test.isPending}
            className="flex items-center gap-1.5 border rounded-md px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            <PlugZap size={15} /> {test.isPending ? "Testing…" : "Test saved config"}
          </button>
          {save.isSuccess && !save.isPending && (
            <span className="text-sm text-green-600">Saved.</span>
          )}
          {save.isError && (
            <span className="text-sm text-red-600">Save failed.</span>
          )}
          {testResult && (
            <span
              className={`text-sm ${testResult.ok ? "text-green-600" : "text-red-600"}`}
            >
              {testResult.ok
                ? testResult.message || "Bind OK"
                : `Bind failed: ${testResult.error}`}
            </span>
          )}
        </div>
      </section>

      {/* ---------------- User roster ---------------- */}
      <section className="rounded-lg border bg-white p-5">
        <div className="flex items-center gap-2 mb-4">
          <ShieldCheck size={18} />
          <h2 className="text-lg font-semibold">Users</h2>
          <span className="text-sm text-gray-400">({users.length})</span>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase text-gray-500 border-b">
                <th className="px-3 py-2">Username</th>
                <th className="px-3 py-2">Display name</th>
                <th className="px-3 py-2">Email</th>
                <th className="px-3 py-2">Source</th>
                <th className="px-3 py-2">Admin</th>
                <th className="px-3 py-2">Active</th>
                <th className="px-3 py-2">Last login</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-b last:border-0 hover:bg-gray-50">
                  <td className="px-3 py-2 font-medium">{u.username}</td>
                  <td className="px-3 py-2 text-gray-600">{u.display_name || "—"}</td>
                  <td className="px-3 py-2 text-gray-600">{u.email || "—"}</td>
                  <td className="px-3 py-2">
                    <span
                      className={`px-1.5 py-0.5 rounded text-xs ${
                        u.auth_source === "local"
                          ? "bg-purple-100 text-purple-700"
                          : "bg-blue-100 text-blue-700"
                      }`}
                    >
                      {u.auth_source}
                    </span>
                  </td>
                  <td className="px-3 py-2">{u.is_admin ? "✓" : ""}</td>
                  <td className="px-3 py-2">{u.is_active ? "✓" : "—"}</td>
                  <td className="px-3 py-2 text-gray-500">
                    {u.last_login_at
                      ? new Date(u.last_login_at).toLocaleString()
                      : "never"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
