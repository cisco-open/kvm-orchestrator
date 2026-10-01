import client from "./client";
import type { User } from "../types/user";

export async function listUsers(): Promise<User[]> {
  const response = await client.get<{ users: User[] }>("/users");
  return response.data.users;
}

// ---------------------------------------------------------------------------
// v0.14.2 — caller's saved SSH public keys (Quick Create pre-fill)
// ---------------------------------------------------------------------------

/**
 * One row from `user_ssh_pubkeys`. Identity is the SHA-256 fingerprint
 * of the wire-format key blob (matches `ssh-keygen -lf`).
 */
export interface SshPubkey {
  id: number;
  fingerprint: string;
  /** Original OpenSSH pubkey line (`<algo> <base64> [comment]`). */
  pubkey: string;
  /** Trailing comment if the user pasted one, else null. */
  comment: string | null;
  created_at: string;
  /** Bumped on every successful Quick Create. Pre-fill order key. */
  last_used_at: string;
}

/**
 * Fetch all keys saved against the caller's account, most-recently-used
 * first. The QuickCreateModal pre-fills its textarea with `[0].pubkey`
 * when this returns at least one row.
 */
export async function listMySshPubkeys(): Promise<SshPubkey[]> {
  const response = await client.get<{ ssh_pubkeys: SshPubkey[] }>(
    "/users/me/ssh-pubkeys",
  );
  return response.data.ssh_pubkeys;
}
