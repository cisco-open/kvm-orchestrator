# SPDX-License-Identifier: Apache-2.0
#
# Copyright 2026 Cisco Systems, Inc. and its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Per-user SSH public-key store (v0.14.2).

Backs the new "save my SSH keys" experience for Quick Create: every time
a user pastes one or more keys into the QuickCreateModal textarea and
submits, the keys are stamped against their user_id; on the next open
the most-recently-used key is pre-filled so they don't have to copy-paste
from `~/.ssh/` every single time.

This module is **not** in the auth path. The `ssh_keys` table (different
table, different module) stores **node** private keys for the
controller-to-hypervisor SSH path — wholly different purpose. Conflating
the two is the most likely future bug here; keep them strictly separate.

Identity for dedupe is the OpenSSH SHA-256 fingerprint of the wire-format
key blob — the same string `ssh-keygen -lf` prints (minus the "SHA256:"
prefix). This means trailing whitespace, comment edits, or re-pasting
the same key from a different machine all dedupe correctly via the
`uq_user_fingerprint` constraint.
"""

import base64
import hashlib
import logging
import os
from datetime import datetime
from typing import Iterable
from urllib.parse import urlparse

import pymysql
import pymysql.cursors

logger = logging.getLogger("wvc.ssh-pubkey")


def _get_db():
    """Return a raw pymysql connection parsed from DATABASE_URL."""
    url = urlparse(os.environ.get("DATABASE_URL", ""))
    return pymysql.connect(
        host=url.hostname,
        port=url.port or 3306,
        user=url.username,
        password=url.password,
        database=url.path.lstrip("/"),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


# ---------------------------------------------------------------------------
# Parsing + fingerprinting
# ---------------------------------------------------------------------------

def _split_keys(text: str | Iterable[str]) -> list[str]:
    """Accept str/list/multiline-str and return one stripped key per element.

    The QuickCreateModal textarea hands us a multiline blob; the API can
    also accept a JSON array. Empty lines are dropped; `\\r` from Windows
    browser paste is stripped.
    """
    if not text:
        return []
    if isinstance(text, str):
        candidates = [line.strip().replace("\r", "") for line in text.splitlines()]
    else:
        candidates = [(k or "").strip().replace("\r", "") for k in text]
    return [c for c in candidates if c]


def fingerprint(pubkey_line: str) -> tuple[str, str | None]:
    """Compute SHA-256 fingerprint + extract comment from an OpenSSH pubkey line.

    Format reminder: `<algo> <base64-blob> [comment...]`.

    Returns `(fp, comment)`. `fp` is the raw base64 (no padding, no
    "SHA256:" prefix) — that's what `ssh-keygen -lf` prints after the
    prefix and it's stable across the OpenSSH versions we care about.
    `comment` is whatever trailed the key, or None if the line had only
    two whitespace-separated fields.

    Raises ValueError if the input doesn't look like an SSH public key.
    """
    parts = pubkey_line.strip().split(None, 2)
    if len(parts) < 2:
        raise ValueError("Not an SSH public key line — expected '<algo> <base64> [comment]'")
    blob_b64 = parts[1]
    try:
        blob = base64.b64decode(blob_b64, validate=True)
    except (ValueError, base64.binascii.Error) as e:
        raise ValueError(f"Invalid base64 in pubkey: {e}") from None
    digest = hashlib.sha256(blob).digest()
    # ssh-keygen prints the digest as standard base64 with trailing '='
    # stripped. Reproduce that exactly so saved fingerprints line up
    # with what users see from `ssh-keygen -lf`.
    fp = base64.b64encode(digest).rstrip(b"=").decode("ascii")
    comment = parts[2] if len(parts) == 3 else None
    return fp, comment


# ---------------------------------------------------------------------------
# DB operations
# ---------------------------------------------------------------------------

def list_for_user(user_id: int) -> list[dict]:
    """Return all saved keys for a user, most-recently-used first.

    The first row is what the modal pre-fills the textarea with; the rest
    are available for a future "Saved Keys" picker on a Settings page.
    """
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, fingerprint, pubkey, comment,
                       created_at, last_used_at
                FROM user_ssh_pubkeys
                WHERE user_id = %s
                ORDER BY last_used_at DESC, id DESC
                """,
                (user_id,),
            )
            rows = cur.fetchall()
        for r in rows:
            for col in ("created_at", "last_used_at"):
                if isinstance(r.get(col), datetime):
                    r[col] = r[col].isoformat()
        return rows
    finally:
        conn.close()


def save_for_user(user_id: int, pubkeys: str | Iterable[str]) -> dict:
    """Persist one-or-many pubkeys against a user. Idempotent.

    Behaviour for each parseable key:
      * new (fingerprint not seen for this user) → INSERT
      * existing → UPDATE last_used_at = now (so the pre-fill order
        reflects "what they actually use" not "what they once added")

    Unparseable lines are silently skipped (we don't want a malformed
    paste to fail the whole Quick Create — the VM has already been
    created by the time we get called).

    Returns `{inserted: int, touched: int, skipped: int}` for the caller
    to log if it cares.
    """
    lines = _split_keys(pubkeys)
    if not lines:
        return {"inserted": 0, "touched": 0, "skipped": 0}

    now = datetime.utcnow()
    inserted = touched = skipped = 0
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            for line in lines:
                try:
                    fp, comment = fingerprint(line)
                except ValueError as e:
                    logger.info("Skipping unparseable pubkey for user_id=%d: %s",
                                user_id, e)
                    skipped += 1
                    continue

                # INSERT ... ON DUPLICATE KEY UPDATE — single round-trip
                # per key. The ON DUPLICATE branch only bumps the
                # timestamp; the original `pubkey` text (which may have
                # a different comment) is kept intact.
                cur.execute(
                    """
                    INSERT INTO user_ssh_pubkeys
                        (user_id, fingerprint, pubkey, comment,
                         created_at, last_used_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        last_used_at = VALUES(last_used_at)
                    """,
                    (user_id, fp, line, comment, now, now),
                )
                # pymysql/MySQL `rowcount` semantics for ON DUPLICATE KEY
                # UPDATE: 1 = INSERT, 2 = UPDATE, 0 = UPDATE with same
                # values (rare here since we always change last_used_at).
                if cur.rowcount == 1:
                    inserted += 1
                else:
                    touched += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"inserted": inserted, "touched": touched, "skipped": skipped}
