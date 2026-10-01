-- v0.14.2 — Per-user SSH public-key store for Quick Create pre-fill.
--
-- Backs the new GET /api/users/me/ssh-pubkeys endpoint and is populated
-- as a side effect of every successful Linux Quick Create (the keys
-- pasted in the modal textarea are saved against the calling user_id).
--
-- The QuickCreateModal fetches this list on open and pre-fills the
-- textarea with the most-recently-used key so the user no longer has to
-- copy-paste from their `~/.ssh/` on every create.
--
-- Identity is the OpenSSH SHA-256 fingerprint of the key blob (base64
-- of the SHA-256 of the wire-format key, the same string `ssh-keygen
-- -lf` prints, minus the "SHA256:" prefix). This survives whitespace
-- changes and trailing-comment edits, so re-pasting the same key with a
-- different label simply bumps last_used_at on the existing row.
--
-- ON DELETE CASCADE: dropping a user removes their saved keys; rows
-- here are convenience cache only, not authorisation state.

CREATE TABLE IF NOT EXISTS user_ssh_pubkeys (
  id           BIGINT       NOT NULL AUTO_INCREMENT,
  user_id      INT          NOT NULL,
  fingerprint  VARCHAR(64)  NOT NULL,
  pubkey       TEXT         NOT NULL,
  comment      VARCHAR(255)     NULL,
  created_at   DATETIME     NOT NULL,
  last_used_at DATETIME     NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_user_fingerprint (user_id, fingerprint),
  KEY idx_user_last_used (user_id, last_used_at),
  CONSTRAINT user_ssh_pubkeys_user_fk
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
