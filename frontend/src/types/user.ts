export interface User {
  id: number;
  username: string;
  email: string | null;
  display_name: string | null;
  auth_source: "local" | "ldap";
  is_active: boolean;
  is_admin: boolean;
  last_login_at: string | null;
  created_at: string;
}
