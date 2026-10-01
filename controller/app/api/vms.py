"""VM management endpoints.

Tenant visibility enforcement (v0.10+)
--------------------------------------
Every endpoint here funnels through one of three patterns:

1. `list_vms`            — builds a WHERE filter from `get_user_visibility`.
2. `_assert_can_see_vm`  — 404 gate used by all single-VM routes (read or
                           non-admin mutation). 404 (not 403) so we don't
                           leak the existence of VMs the caller can't see.
3. `@admin_required`     — unchanged decorator for destructive admin-only
                           ops (create, delete, migrate, disk attach/detach,
                           CD-ROM swap). Admins implicitly see every VM, so
                           these don't need a visibility gate.

`update_vm` accepts user fields (vcpus/ram/disk) from anyone who can see
the VM, plus an owner-only SHARED↔PERSONAL tenant flip; the service layer
(`vm_service.vm_edit`) is the source of truth for those rules.
"""

import os
from datetime import datetime
from urllib.parse import urlparse

import logging
import pymysql
import pymysql.cursors
from flask import Blueprint, abort, jsonify, make_response, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from ..services.vm_service import (
    vm_action as _vm_action,
    vm_console as _vm_console,
    vm_edit as _vm_edit,
    vm_migrate as _vm_migrate,
    vm_list_disks as _vm_list_disks,
    vm_attach_disk as _vm_attach_disk,
    vm_detach_disk as _vm_detach_disk,
    vm_set_cdrom as _vm_set_cdrom,
    vm_delete as _vm_delete,
    get_vm_xml as _get_vm_xml,
    set_vm_xml as _set_vm_xml,
)
from ..services.vm_create import vm_create as _vm_create
from ..services import auth_service, network_service, vm_image_service, ssh_pubkey_service
from ..services.placement import select_node
from ..services.tenant_service import (
    PERSONAL_TENANT_ID,
    SHARED_TENANT_ID,
    get_user_tenant_ids,
    get_user_visibility,
)
from ..middleware.auth import admin_required
from ..middleware.mcp import (
    mcp_or_jwt_required,
    current_user_id as _mcp_or_jwt_user_id,
)

bp = Blueprint("vms", __name__)

logger = logging.getLogger("wvc.api.vms")


# Sentinel for distinguishing "key omitted" from "key present with value None"
# in JSON bodies (e.g. bulk_set_vm_tenant needs to reject omission but accept
# explicit `null` to clear the tag).
_MISSING = object()


def _get_db():
    url = urlparse(os.environ.get("DATABASE_URL", ""))
    return pymysql.connect(
        host=url.hostname, port=url.port or 3306,
        user=url.username, password=url.password,
        database=url.path.lstrip("/"),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def _current_user_id() -> int:
    """Return the current caller's user id.

    Delegates to the MCP/JWT-aware helper so endpoints work under both
    the standard JWT flow and MCP impersonation (see middleware/mcp.py).
    """
    return _mcp_or_jwt_user_id()


def _lookup_username(user_id: int) -> str | None:
    """Return the CEC username for a user_id, or None if not found.

    Used by Quick Create to pre-fill `default_user` so the cloud-init
    `users:` block creates an account matching the caller's CEC login
    (`ssh youruser@vm` reads better than `ssh ubuntu@vm`).
    """
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT username FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            return row["username"] if row else None
    finally:
        conn.close()


def _lookup_node_hostname(node_id: int | None) -> str | None:
    """Return a node's hostname for a node_id, or None. Used to name the
    target host in create-failure logs + user-facing error messages."""
    if node_id is None:
        return None
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT hostname FROM nodes WHERE id = %s", (node_id,))
            row = cur.fetchone()
            return row["hostname"] if row else None
    finally:
        conn.close()


def _can_see_vm(vis: dict, tenant_id: int | None,
                owner_user_id: int | None, user_id: int) -> bool:
    """Pure helper applying the v0.10 visibility rule to one VM row."""
    if vis["is_admin"]:
        return True
    if tenant_id is None:
        return False  # untagged → admin-only
    if tenant_id in vis["tenant_ids"]:
        return True
    if tenant_id == PERSONAL_TENANT_ID and owner_user_id == user_id:
        return True
    return False


def _assert_can_see_vm(vm_id: int) -> tuple[int, dict]:
    """Return (user_id, vis) if the caller may see vm_id.

    On denial (VM doesn't exist OR exists but caller can't see it), aborts
    with HTTP 404 + JSON body `{"error": "VM not found"}`. 404 (not 403)
    is deliberate — don't leak the existence of out-of-tenant VMs.
    """
    user_id = _current_user_id()
    vis = get_user_visibility(user_id)
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id, owner_user_id FROM vms WHERE id = %s",
                (vm_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or not _can_see_vm(vis, row["tenant_id"],
                                  row["owner_user_id"], user_id):
        abort(make_response(jsonify({"error": "VM not found"}), 404))
    return user_id, vis


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------

@bp.route("", methods=["GET"])
@mcp_or_jwt_required
def list_vms():
    """List VMs visible to the caller.

    Admins see everything. Non-admins see:
        v.tenant_id IN (their tenants + SHARED)
        OR (v.tenant_id = PERSONAL AND v.owner_user_id = me)
    """
    user_id = _current_user_id()
    vis     = get_user_visibility(user_id)

    if vis["is_admin"]:
        where_sql = ""
        args: list = []
    else:
        placeholders = ",".join(["%s"] * len(vis["tenant_ids"]))
        where_sql = (
            f"WHERE v.tenant_id IN ({placeholders}) "
            f"   OR (v.tenant_id = %s AND v.owner_user_id = %s)"
        )
        args = [*vis["tenant_ids"], PERSONAL_TENANT_ID, user_id]

    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT v.id, v.name, v.uuid, v.state,
                       v.vcpus, v.ram_mb,
                       v.vlan, v.disk_gb, v.disk_path, v.datastore,
                       v.ip,
                       v.node_id,
                       v.tenant_id,
                       v.owner_user_id,
                       v.priority,
                       v.last_seen_at,
                       v.expires_at,
                       v.renewed_at,
                       v.exempt,
                       v.lifecycle_state,
                       n.hostname AS node_hostname,
                       n.ip_address AS node_ip,
                       n.cpu_model AS node_cpu_model,
                       t.name AS tenant_name
                FROM vms v
                JOIN nodes n ON n.id = v.node_id
                LEFT JOIN tenants t ON t.id = v.tenant_id
                {where_sql}
                ORDER BY n.hostname, v.name
                """,
                args,
            )
            rows = cur.fetchall()

        for r in rows:
            if isinstance(r.get("last_seen_at"), datetime):
                r["last_seen_at"] = r["last_seen_at"].isoformat()
            for _f in ("expires_at", "renewed_at"):
                if isinstance(r.get(_f), datetime):
                    r[_f] = r[_f].isoformat()
            r["exempt"] = bool(r.get("exempt"))

        return jsonify({"vms": rows}), 200
    finally:
        conn.close()


@bp.route("/<int:vm_id>", methods=["GET"])
@mcp_or_jwt_required
def get_vm(vm_id: int):
    """Get VM details (visibility-gated)."""
    _assert_can_see_vm(vm_id)  # aborts 404 on denial
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT v.*, n.hostname AS node_hostname, n.ip_address AS node_ip
                FROM vms v JOIN nodes n ON n.id = v.node_id
                WHERE v.id = %s
                """,
                (vm_id,),
            )
            row = cur.fetchone()
        if isinstance(row.get("last_seen_at"), datetime):
            row["last_seen_at"] = row["last_seen_at"].isoformat()
        return jsonify(row), 200
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Create / edit / delete
# ---------------------------------------------------------------------------

@bp.route("", methods=["POST"])
@mcp_or_jwt_required
def create_vm():
    """Create a new VM (blank disk + optional ISO, clone, or existing disk).

    v0.11: opened to all authenticated users (previously admin-only). The
    caller chooses the target tenant via `tenant_id` in the body; we
    validate the choice against the caller's role:

      * admin              — any tenant id, or NULL (untagged).
      * non-admin owner    — PERSONAL or any tenant the user is a member
                             of. Other ids → 403.
      * default if missing — NULL (untagged) for admins, PERSONAL for
                             non-admins.

    v0.11.1: **SHARED is reserved for VMs discovered by `vm_sync`** —
    POSTs with `tenant_id = SHARED` are rejected for everyone. The user
    can always edit the VM into SHARED afterwards (release it back to
    the pool) but new creates must be claimed by someone. Admin default
    flipped from SHARED → untagged to match.

    v0.18: `node_id` is now optional. Non-admins never pick a node —
    the backend runs `placement.select_node()` scoped to the VM's
    tenant. Admins may still pick manually; if they don't, placement
    runs for them too. When placement finds no eligible node the
    request 400s with a clear message.

    Owner is always stamped from the JWT identity so the creator can
    later flip the VM's tenant without admin help.
    """
    body = request.get_json(silent=True) or {}
    required = {"name", "vcpus", "ram_gb", "vlan", "mode"}
    missing = required - body.keys()
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(sorted(missing))}"}), 400

    user_id = _current_user_id()
    vis     = get_user_visibility(user_id)
    is_admin = vis["is_admin"]

    # Tenant choice — validate against caller's role.
    raw_tid = body.get("tenant_id", "__missing__")
    if raw_tid == "__missing__":
        # v0.11.1: admin default flipped from SHARED → None. Admins
        # rarely want a manually-created VM in SHARED; if they do, they
        # can edit it post-creation. Untagged is the safer admin default
        # because non-admins can't see untagged VMs (avoids accidental
        # exposure).
        tenant_id: int | None = None if is_admin else PERSONAL_TENANT_ID
    elif raw_tid in (None, "", 0):
        if not is_admin:
            return jsonify({"error": "Only admins may create untagged VMs"}), 403
        tenant_id = None
    else:
        try:
            tenant_id = int(raw_tid)
        except (TypeError, ValueError):
            return jsonify({"error": "tenant_id must be an integer or null"}), 400
        # v0.11.1: SHARED is reserved for KVM-discovered VMs. Reject
        # explicit SHARED on create for everyone (admin + non-admin).
        # Use the Edit modal to move a VM into SHARED post-creation.
        if tenant_id == SHARED_TENANT_ID:
            return jsonify({
                "error": (
                    "SHARED is reserved for VMs discovered from libvirt. "
                    "Pick ODaaSE or a tenant you are a member of; you "
                    "can release the VM to SHARED later via Edit."
                )
            }), 400
        if not is_admin:
            allowed = {PERSONAL_TENANT_ID,
                       *get_user_tenant_ids(user_id)}
            if tenant_id not in allowed:
                return jsonify({
                    "error": (
                        "You may only create VMs in ODaaSE or a tenant "
                        "you are a member of"
                    )
                }), 403

    body["owner_user_id"] = user_id
    body["tenant_id"]     = tenant_id

    # v0.15.0 — non-admins may only create VMs on networks an admin has
    # marked `user_visible` in the catalog. The Networks page is the
    # canonical place to flip that flag. Backend belt-and-braces: the UI
    # dropdown already filters but a determined non-admin could curl-
    # direct any bridge name. Admins bypass — they can use any bridge
    # whether or not it's in the catalog (matches the Edit VM behaviour).
    if not is_admin:
        vlan = (body.get("vlan") or "").strip()
        if not vlan:
            return jsonify({"error": "vlan is required"}), 400
        if not network_service.is_bridge_user_visible(vlan):
            return jsonify({
                "error": (
                    f"Network '{vlan}' is not available to non-admin "
                    "users. Ask an admin to mark the network as "
                    "user-visible in the Networks page."
                )
            }), 403

    # v0.14.0 — Quick Create (`mode: "quick"`) injects two server-side
    # touches before handing off to the service layer:
    #
    #   * `default_user` is set to the caller's CEC username if not
    #     already supplied. The cloud-init `users:` block then creates
    #     a sudo user matching their login — friendlier than the
    #     image's generic `ubuntu` fallback.
    #   * SSH key validation is light here (presence only, for Linux);
    #     the service layer renders the cloud-init and will reject
    #     empty/malformed key blocks with a clear error.
    #
    # v0.14.2 — also source the cloud-init `passwd:` hash for Linux
    # images so the VM accepts the same CEC password the user logged
    # in with. Three sources, in order:
    #
    #   1. Body `linux_password` (plaintext) — "use different
    #      credentials" branch of the UI. We hash it server-side; the
    #      plaintext is not echoed back, logged, or persisted.
    #   2. Session hash stashed in Redis at login.
    #   3. Neither available → return 409 with `credentials_required:
    #      true` so the frontend can auto-open the override panel
    #      and re-submit with `linux_password`.
    #
    # Windows images skip all of this — cloud-init doesn't run, and the
    # sysprep images carry their own administrator credentials.
    if body.get("mode") == "quick":
        if not body.get("default_user"):
            body["default_user"] = _lookup_username(user_id) or ""

        image_id = (body.get("image_id") or "").strip()
        image    = vm_image_service.get_image(image_id) if image_id else None
        if image and image["os_family"] == "linux":
            # SSH key fallback: the UI submits `ssh_pub_keys` from the
            # modal's textarea (pre-filled from the user's saved keys).
            # Non-UI callers (MCP, curl scripts) may not know to send it,
            # so if the field is empty we auto-populate from the caller's
            # saved keys. The service layer rejects the create with a
            # clear error if the pool is still empty after this.
            #
            # SECURITY (2026-09-07): only inject keys that actually belong
            # to the caller. The `.root` admin identity (and any shared
            # account) can accumulate foreign keys mis-attributed by a bad
            # save; blindly dumping every stored key once leaked a third
            # party's key (otheruser) into a youruser VM. We now filter the
            # fallback to keys whose comment matches the caller's base CEC
            # (username minus any .root/.portal suffix). If nothing matches
            # we inject NOTHING and let the service layer surface the
            # "no ssh keys" error rather than inject someone else's key.
            if not (body.get("ssh_pub_keys") or "").strip():
                saved = ssh_pubkey_service.list_for_user(user_id)
                base_cec = (_lookup_username(user_id) or "").split(".")[0].lower()
                matched = [
                    r["pubkey"] for r in saved
                    if r.get("pubkey")
                    and base_cec
                    and base_cec in (r.get("comment") or "").lower()
                ]
                if matched:
                    body["ssh_pub_keys"] = "\n".join(matched)
                elif saved:
                    # Keys exist but NONE match the caller's CEC — refuse to
                    # guess. Better to error than inject a foreign key.
                    logger.warning(
                        "vm_create: %d saved key(s) for user_id=%s but none "
                        "match CEC '%s' — not auto-injecting (avoids foreign "
                        "key leak). Caller must pass ssh_pub_keys explicitly.",
                        len(saved), user_id, base_cec,
                    )

            override_pw = body.pop("linux_password", None)
            if override_pw:
                # Hash here — never let plaintext reach the service
                # layer or any logging downstream.
                body["password_hash"] = auth_service.hash_password(str(override_pw))
            else:
                stashed = auth_service.get_stashed_password_hash(user_id)
                if stashed:
                    body["password_hash"] = stashed
                else:
                    # 409 Conflict — request is structurally fine but a
                    # session-state precondition isn't met. The frontend
                    # treats this as the signal to show the credentials
                    # override panel.
                    return jsonify({
                        "error": (
                            "Session password unavailable — re-enter your "
                            "credentials or pick 'Use different credentials'."
                        ),
                        "credentials_required": True,
                    }), 409

    # v0.18 — placement. v0.20 — two-pass with DEFAULT fallback.
    #
    #   * Non-admin  → placement always runs; any `node_id` in the body
    #                  is ignored (they never see the dropdown).
    #   * Admin      → honour `node_id` if supplied; otherwise placement
    #                  runs on their behalf so the create call always
    #                  has a node.
    #
    # Placement is a two-pass algorithm (see services/placement.py):
    #   pass 1 = caller's tenant only (skipped for SHARED / PERSONAL /
    #            NULL which never appear in node_tenants);
    #   pass 2 = DEFAULT placement pool.
    # If both passes are empty we 400 with a message that hints at the
    # fix.
    raw_node = body.get("node_id")
    need_placement = (not is_admin) or raw_node in (None, "", 0)
    if need_placement:
        try:
            vcpus  = int(body["vcpus"])
            ram_mb = int(body["ram_gb"]) * 1024
            disk_gb = int(body.get("disk_gb", 0) or 0)
        except (TypeError, ValueError):
            return jsonify({"error": "vcpus, ram_gb, disk_gb must be integers"}), 400
        picked = select_node(
            vcpus=vcpus,
            ram_mb=ram_mb,
            disk_gb=disk_gb,
            tenant_id=tenant_id,
            # Non-quick creates (blank / clone / existing) build RBD-backed
            # disks, so the target host must expose an RBD storage pool.
            # Quick create clones a golden RBD image too, but its placement
            # is unchanged here to preserve existing behaviour.
            require_rbd=(body.get("mode") != "quick"),
        )
        if not picked:
            return jsonify({
                "error": (
                    "No eligible host available for this tenant. Ask an "
                    "admin to tag a node with your tenant or with the "
                    "DEFAULT placement pool, or free capacity on an "
                    "existing tagged node. For non-quick creates the host "
                    "must also have an RBD storage pool."
                )
            }), 400
        body["node_id"] = picked
    else:
        try:
            body["node_id"] = int(raw_node)
        except (TypeError, ValueError):
            return jsonify({"error": "node_id must be an integer"}), 400

    result = _vm_create(body)
    if "ok" in result:
        return jsonify(result), 201

    # Deployment failed. Surface the target node to the user in the error
    # message and log both the requesting user and the node so ops can
    # correlate a user report to a specific host. (The service layer also
    # logs per-branch detail; this adds the who + where at the API edge.)
    node_hostname = _lookup_node_hostname(body.get("node_id"))
    username = _lookup_username(user_id) or f"user_id={user_id}"
    base_err = result.get("error", "VM creation failed")
    logger.error(
        "VM create FAILED for user=%s on node=%s (node_id=%s): %s",
        username, node_hostname or "?", body.get("node_id"), base_err,
    )
    if node_hostname and node_hostname not in base_err:
        result["error"] = f"{base_err} (on node {node_hostname})"
    result["node"] = node_hostname
    return jsonify(result), 400


@bp.route("/<int:vm_id>", methods=["PUT"])
@mcp_or_jwt_required
def update_vm(vm_id: int):
    """Edit VM configuration.

    User fields  : vcpus, ram_mb, disk_gb (anyone who can see the VM)
    Owner field  : tenant_id (SHARED↔PERSONAL only for the owner)
    Admin fields : name, description, vlan, boot_order, tenant_id (any),
                   owner_user_id

    Field-level authorisation is enforced inside `vm_service.vm_edit`.
    Here we only gate visibility (you can't edit what you can't see).
    """
    user_id, vis = _assert_can_see_vm(vm_id)

    body = request.get_json(silent=True) or {}
    if not body:
        return jsonify({"error": "Request body required"}), 400

    result = _vm_edit(vm_id, body, user_id=user_id, is_admin=vis["is_admin"])
    return jsonify(result), 200 if "ok" in result else 400


@bp.route("/bulk-tenant", methods=["PUT"])
@admin_required
def bulk_set_vm_tenant():
    """Bulk-assign a single tenant to many VMs in one round-trip (admin only).

    Body: `{"vm_ids": [int, ...], "tenant_id": int | null}`

    - `tenant_id=null` clears the tag → VM becomes admin-only.
    - Unknown VM ids are silently skipped (WHERE id IN filters them out);
      the response `updated` count reflects rows actually changed.
    - The tenant FK is validated up-front to give a friendly 400 instead
      of a MySQL constraint error.
    """
    body = request.get_json(silent=True) or {}
    vm_ids = body.get("vm_ids")
    # Use a sentinel to distinguish "missing key" from "explicit null":
    tenant_id = body.get("tenant_id", _MISSING)

    if not isinstance(vm_ids, list) or not vm_ids or not all(
        isinstance(v, int) for v in vm_ids
    ):
        return jsonify({"error": "vm_ids must be a non-empty list of integers"}), 400
    if tenant_id is _MISSING:
        return jsonify({"error": "tenant_id required (integer or null)"}), 400
    if tenant_id is not None and not isinstance(tenant_id, int):
        return jsonify({"error": "tenant_id must be an integer or null"}), 400

    conn = _get_db()
    try:
        with conn.cursor() as cur:
            if tenant_id is not None:
                cur.execute("SELECT id FROM tenants WHERE id = %s", (tenant_id,))
                if not cur.fetchone():
                    return jsonify({"error": f"tenant_id {tenant_id} not found"}), 400
            placeholders = ",".join(["%s"] * len(vm_ids))
            cur.execute(
                f"UPDATE vms SET tenant_id = %s WHERE id IN ({placeholders})",
                [tenant_id, *vm_ids],
            )
            updated = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "updated": updated}), 200


_VALID_PRIORITIES = ("critical", "priority", "default")


@bp.route("/<int:vm_id>/priority", methods=["POST"])
@admin_required
def set_vm_priority(vm_id: int):
    """Set a VM's HA priority class (admin only).

    Body: `{"priority": "critical" | "priority" | "default"}`

    - critical — restarted first on failover; may evict non-critical VMs to
      reclaim capacity (DEFAULT first, PRIORITY only as a last resort).
    - priority — restarted after critical; may evict DEFAULT VMs.
    - default  — restarted last, best-effort; evicts nothing, evicted first.

    Eviction itself is always a separate approval in the recovery plan; this
    endpoint only classifies the VM.
    """
    body = request.get_json(silent=True) or {}
    priority = body.get("priority")
    if priority not in _VALID_PRIORITIES:
        return jsonify({
            "error": f"priority must be one of {_VALID_PRIORITIES}"
        }), 400

    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM vms WHERE id = %s", (vm_id,))
            if not cur.fetchone():
                return jsonify({"error": "VM not found"}), 404
            cur.execute(
                "UPDATE vms SET priority = %s WHERE id = %s",
                (priority, vm_id),
            )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "id": vm_id, "priority": priority}), 200


@bp.route("/<int:vm_id>", methods=["DELETE"])
@mcp_or_jwt_required
def delete_vm(vm_id: int):
    """
    Delete a VM — force-stop, undefine, optionally delete its primary
    disk (Ceph `rbd rm`), then drop the DB row.

    v0.22: opened to VM owners as well as admins (previously admin-only).
    A non-admin caller may delete a VM iff `owner_user_id == caller`.
    Team-mates who happen to share the VM's tenant but did NOT create
    the VM cannot delete it; only the owner or an admin can. This makes
    self-service cleanup possible without escalating to admin.

    Denial modes:
      * VM does not exist, or exists but caller can't see it via the
        v0.10 visibility rule → 404 (don't leak existence of out-of-
        tenant VMs).
      * Caller can see the VM but is neither admin nor owner → 403
        with a clear message pointing them at their admin.

    Body (optional): `{"delete_disk": true|false}` — defaults to true.
    When true and the VM's disk_path is `rbd:pool/image`, the primary
    disk image is removed from Ceph (RBD keyring-fed on the host).
    """
    user_id  = _current_user_id()
    vis      = get_user_visibility(user_id)
    is_admin = vis["is_admin"]

    # Load tenant_id + owner for both visibility and ownership checks.
    # We can't use _assert_can_see_vm here because we also need
    # owner_user_id to distinguish "can see but can't delete" (403)
    # from "can't see at all" (404).
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id, owner_user_id FROM vms WHERE id = %s",
                (vm_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or not _can_see_vm(vis, row["tenant_id"],
                                  row["owner_user_id"], user_id):
        return jsonify({"error": "VM not found"}), 404
    if not is_admin and row["owner_user_id"] != user_id:
        return jsonify({
            "error": (
                "You may only delete VMs you own. Ask an admin if "
                "this VM should be removed."
            )
        }), 403

    body        = request.get_json(silent=True) or {}
    delete_disk = bool(body.get("delete_disk", True))
    result      = _vm_delete(vm_id, delete_disk=delete_disk)
    return jsonify(result), 200 if "ok" in result else 400


@bp.route("/<int:vm_id>/renew", methods=["POST"])
@mcp_or_jwt_required
def renew_vm(vm_id: int):
    """Renew a VM's 1-year term (owner or admin). Resets expires_at = now + TTL
    and lifecycle_state back to 'active'. If the VM had been auto-stopped at
    expiry, restart it so a renew fully undoes the expiry action.

    Same ownership rules as delete: owner or admin only (see-but-not-owner=403,
    can't-see=404)."""
    user_id  = _current_user_id()
    vis      = get_user_visibility(user_id)
    is_admin = vis["is_admin"]

    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id, owner_user_id, lifecycle_state "
                "FROM vms WHERE id = %s", (vm_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or not _can_see_vm(vis, row["tenant_id"],
                                  row["owner_user_id"], user_id):
        return jsonify({"error": "VM not found"}), 404
    if not is_admin and row["owner_user_id"] != user_id:
        return jsonify({"error": "You may only renew VMs you own."}), 403

    from ..services.vm_lifecycle import renew as _renew
    result = _renew(vm_id)
    if "error" in result:
        return jsonify(result), 400

    # If the term had lapsed and the VM was auto-stopped, bring it back up.
    if row.get("lifecycle_state") == "auto_stopped":
        try:
            from ..services.vm_service import vm_action
            start = vm_action(vm_id, "start")
            result["restarted"] = bool(start.get("ok"))
        except Exception:
            result["restarted"] = False
    return jsonify(result), 200


@bp.route("/<int:vm_id>/exempt", methods=["POST"])
@mcp_or_jwt_required
def exempt_vm(vm_id: int):
    """Admin-only: toggle a VM's TTL exemption. Body: {"exempt": true|false}.
    Exempt VMs are never warned, auto-stopped or auto-deleted by the checker
    (used for long-lived infrastructure VMs)."""
    user_id  = _current_user_id()
    vis      = get_user_visibility(user_id)
    if not vis["is_admin"]:
        return jsonify({"error": "Admin only."}), 403

    body   = request.get_json(silent=True) or {}
    exempt = bool(body.get("exempt", True))

    from ..services.vm_lifecycle import set_exempt as _set_exempt
    result = _set_exempt(vm_id, exempt)
    return jsonify(result), 200 if "ok" in result else 400


@bp.route("/lifecycle/settings", methods=["GET"])
@mcp_or_jwt_required
def get_lifecycle_settings():
    """Current runtime state of the 1-year-TTL checker.

    Returns {enabled, source, env_default}. `source` is 'runtime' if an admin
    has toggled it, else 'env_default'. Any authenticated user can read this
    (drives the UI badge); only admins can change it.
    """
    from flask import current_app
    from ..services.vm_lifecycle import lifecycle_status
    env_default = bool(current_app.config.get("LIFECYCLE_ENABLED", False))
    st = lifecycle_status(env_default)
    st["env_default"] = env_default
    return jsonify(st), 200


@bp.route("/lifecycle/settings", methods=["POST"])
@mcp_or_jwt_required
def set_lifecycle_settings():
    """Admin-only: arm/disarm the 1-year-TTL checker at runtime (no redeploy).
    Body: {"enabled": true|false}. Persists to ha_settings (lifecycle_enabled);
    the engine reads it at the top of every run."""
    user_id  = _current_user_id()
    vis      = get_user_visibility(user_id)
    if not vis["is_admin"]:
        return jsonify({"error": "Admin only."}), 403

    body    = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled", False))

    from ..services.vm_lifecycle import set_lifecycle_enabled, lifecycle_status
    _uname = None
    try:
        conn = _get_db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT username FROM users WHERE id = %s", (user_id,))
                _r = cur.fetchone()
                _uname = (_r or {}).get("username")
        finally:
            conn.close()
    except Exception:
        pass
    set_lifecycle_enabled(enabled, updated_by=_uname)
    return jsonify({"ok": True, **lifecycle_status()}), 200


# ---------------------------------------------------------------------------
# Power actions — visibility-gated, any visible-to-caller VM is operable
# ---------------------------------------------------------------------------

@bp.route("/<int:vm_id>/start", methods=["POST"])
@mcp_or_jwt_required
def start_vm(vm_id: int):
    _assert_can_see_vm(vm_id)
    result = _vm_action(vm_id, "start")
    return jsonify(result), 200 if "ok" in result else 400


@bp.route("/<int:vm_id>/stop", methods=["POST"])
@mcp_or_jwt_required
def stop_vm(vm_id: int):
    _assert_can_see_vm(vm_id)
    body = request.get_json(silent=True) or {}
    action = "force-off" if body.get("force") else "stop"
    result = _vm_action(vm_id, action)
    return jsonify(result), 200 if "ok" in result else 400


@bp.route("/<int:vm_id>/reboot", methods=["POST"])
@mcp_or_jwt_required
def reboot_vm(vm_id: int):
    _assert_can_see_vm(vm_id)
    result = _vm_action(vm_id, "reboot")
    return jsonify(result), 200 if "ok" in result else 400


@bp.route("/<int:vm_id>/suspend", methods=["POST"])
@jwt_required()
def suspend_vm(vm_id: int):
    _assert_can_see_vm(vm_id)
    result = _vm_action(vm_id, "suspend")
    return jsonify(result), 200 if "ok" in result else 400


@bp.route("/<int:vm_id>/resume", methods=["POST"])
@jwt_required()
def resume_vm(vm_id: int):
    _assert_can_see_vm(vm_id)
    result = _vm_action(vm_id, "resume")
    return jsonify(result), 200 if "ok" in result else 400


@bp.route("/<int:vm_id>/migrate", methods=["POST"])
@admin_required
def migrate_vm(vm_id: int):
    body = request.get_json(silent=True) or {}
    target_node_id = body.get("target_node_id")
    if not target_node_id:
        return jsonify({"error": "target_node_id is required"}), 400
    allow_shutdown = bool(body.get("allow_shutdown", False))
    result = _vm_migrate(vm_id, int(target_node_id), allow_shutdown=allow_shutdown)
    return jsonify(result), 200 if "ok" in result else 400


@bp.route("/<int:vm_id>/console", methods=["POST"])
@jwt_required()
def request_console(vm_id: int):
    """Issue a one-shot console token. Gated by VM visibility so that the
    proxy itself can stay dumb (no per-VM ACL lookup on connect)."""
    user_id, _ = _assert_can_see_vm(vm_id)
    result = _vm_console(vm_id, user_id)
    return jsonify(result), 200 if "token" in result else 400


# ---------------------------------------------------------------------------
# Disk management (admin only — disk topology is operator surface area)
# ---------------------------------------------------------------------------

@bp.route("/<int:vm_id>/disks", methods=["GET"])
@jwt_required()
def list_vm_disks(vm_id: int):
    """List disks and CD-ROM device for a VM (visibility-gated)."""
    _assert_can_see_vm(vm_id)
    result = _vm_list_disks(vm_id)
    return jsonify(result), 200 if "error" not in result else 400


@bp.route("/<int:vm_id>/disks", methods=["POST"])
@admin_required
def attach_vm_disk(vm_id: int):
    """Create a new RBD image and attach it as an additional disk. Admin only."""
    body = request.get_json(silent=True) or {}
    pool       = (body.get("pool") or "").strip()
    image_name = (body.get("name") or "").strip()
    size_gb    = body.get("size_gb")

    if not pool or not image_name:
        return jsonify({"error": "Fields 'pool' and 'name' are required"}), 400
    try:
        size_gb = int(size_gb)
        if size_gb < 1:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "'size_gb' must be a positive integer"}), 400

    result = _vm_attach_disk(vm_id, pool, image_name, size_gb)
    return jsonify(result), 200 if "ok" in result else 400


@bp.route("/<int:vm_id>/disks/<target>", methods=["DELETE"])
@admin_required
def detach_vm_disk(vm_id: int, target: str):
    """Detach a disk by target device name (e.g. vdb). Admin only. Image is NOT deleted."""
    result = _vm_detach_disk(vm_id, target)
    return jsonify(result), 200 if "ok" in result else 400


@bp.route("/<int:vm_id>/cdrom", methods=["PUT"])
@admin_required
def set_vm_cdrom(vm_id: int):
    """Mount or eject an ISO. Admin only. Body: {iso_path: "/path/…" | ""}."""
    body     = request.get_json(silent=True) or {}
    iso_path = (body.get("iso_path") or "").strip()
    result   = _vm_set_cdrom(vm_id, iso_path)
    return jsonify(result), 200 if "ok" in result else 400


# ---------------------------------------------------------------------------
# Raw libvirt XML (admin only — direct redefine, use with care)
# ---------------------------------------------------------------------------

@bp.route("/<int:vm_id>/xml", methods=["GET"])
@admin_required
def get_vm_xml_route(vm_id: int):
    """Return the live libvirt domain XML for a VM. Admin only."""
    result = _get_vm_xml(vm_id)
    return jsonify(result), 200 if "xml" in result else 400


@bp.route("/<int:vm_id>/xml", methods=["PUT"])
@admin_required
def set_vm_xml_route(vm_id: int):
    """Redefine a VM from admin-edited XML. Admin only.

    Body: {"xml": "<domain>…</domain>"}.  Most changes require a VM
    reboot to take effect (libvirt applies to config, not live domain).
    """
    body = request.get_json(silent=True) or {}
    xml  = body.get("xml") or ""
    if not xml.strip():
        return jsonify({"error": "Field 'xml' is required"}), 400
    result = _set_vm_xml(vm_id, xml)
    return jsonify(result), 200 if "ok" in result else 400
