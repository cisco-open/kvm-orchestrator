import client from "./client";

export interface LoginRequest {
  username: string;
  password: string;
}

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
}

export async function login(data: LoginRequest): Promise<AuthTokens> {
  const response = await client.post<AuthTokens>("/auth/login", data);
  return response.data;
}

export async function refreshToken(): Promise<{ access_token: string }> {
  const token = localStorage.getItem("refresh_token");
  const response = await client.post(
    "/auth/refresh",
    {},
    { headers: { Authorization: `Bearer ${token}` } }
  );
  return response.data;
}

/** A tenant the user is an AD-group member of. SHARED/PERSONAL are
 *  excluded — every user implicitly belongs to those. */
export interface MeTenant {
  id: number;
  name: string;
  ad_group_dn: string | null;
  is_builtin: boolean;
}

export interface MeResponse {
  id: number;
  username: string;
  display_name: string | null;
  email: string | null;
  is_admin: boolean;
  tenants: MeTenant[];
}

export async function getMe(): Promise<MeResponse> {
  const response = await client.get<MeResponse>("/auth/me");
  return response.data;
}
