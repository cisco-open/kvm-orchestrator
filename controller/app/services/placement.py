"""Placement algorithm — selects target node for a VM.

Tenant-aware two-pass placement since v0.20:

  1. Pass 1 — caller's tenant only. Try nodes tagged with the VM's
     tenant. If any online node has enough capacity, place there.
  2. Pass 2 — DEFAULT placement pool. Fall through to nodes tagged
     with the DEFAULT built-in (id=4).
  3. Otherwise, return None → HTTP 400.

Rationale (see migrations 0003 and 0004): DEFAULT (id=4) is the shared
placement fallback pool. PERSONAL (id=3, DB name "ODaaSE" since the
v0.21 merge) is the owner-only VM tenant — it is not populated in
`node_tenants`, so pass 1 is always skipped for it and VMs land via
pass 2. The strict two-pass semantic means tenant-dedicated nodes are
always preferred when they have capacity — a DEFAULT-tagged node with
more free RAM can no longer "steal" a placement from an under-loaded
tenant node.

Pass 1 is skipped for VMs whose tenant is one of SHARED, PERSONAL, or
NULL/untagged — none of those are ever populated in `node_tenants`, so
pass 1 would be empty by definition. Those VMs go straight to pass 2.

Deliberately raw-SQL rather than SQLAlchemy — the rest of the service
layer is raw pymysql, and the SQLAlchemy `Node` model is stale (see
migrations/env.py:9-12).
"""

import logging
import os
from urllib.parse import urlparse

import pymysql
import pymysql.cursors

from .tenant_service import (
    DEFAULT_TENANT_ID,
    PERSONAL_TENANT_ID,
    SHARED_TENANT_ID,
)

logger = logging.getLogger("wvc.placement")


def _get_db():
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


def _node_has_rbd_pool(node_id: int) -> bool:
    """Return True if the node can back an RBD-backed VM disk.

    Correct signal: a libvirt storage pool of type ``rbd`` is DEFINED on
    the node — whether active or not. kvmaestro provisions disks via
    raw ``rbd`` CLI against the Ceph pool, so an rbd pool that is merely
    *inactive* (e.g. failed to autostart after a reboot before Ceph was
    ready) still means the node is RBD-capable. Keying off active-only
    pool discovery previously excluded such nodes and caused spurious
    "No eligible host" placement failures.

    Fail-open on "unknown": if the probe can't reach the node (transient
    SSH failure / throttling), we DON'T exclude an otherwise-online node —
    dropping a healthy candidate over an SSH blip is worse than the rare
    mis-placement, and the create path itself will surface any real RBD
    problem. Only a definitive "no rbd pool defined" excludes the node.
    """
    # Imported lazily to avoid a circular import (vm_create imports from
    # the service layer that ultimately pulls placement in some paths).
    from .vm_create import node_has_defined_rbd_pool

    result = node_has_defined_rbd_pool(node_id)
    if result is None:
        # Could not determine — fail open (candidate stays eligible).
        logger.info("RBD probe inconclusive for node %d — keeping eligible", node_id)
        return True
    return result


def _query_best_node(cur, vcpus: int, ram_mb: int, disk_gb: int,
                     tenant_ids: list[int],
                     require_rbd: bool = False) -> dict | None:
    """Return the best-fit online node whose node_tenants include any of
    `tenant_ids` and which has enough capacity. RAM is the primary
    ordering key (typically the binding constraint on KVM hosts), vCPU
    breaks ties.

    When ``require_rbd`` is True, candidates are considered in RAM-desc
    order and the first one that actually exposes an ``rbd`` storage pool
    (verified live via SSH discovery) is returned. This is used for
    non-quick creates, whose disks are RBD-backed and therefore cannot
    land on a host without an RBD pool.
    """
    placeholders = ",".join(["%s"] * len(tenant_ids))
    # When we need to filter on RBD-pool presence we can't stop at the
    # single best-fit row — the best-fit host might lack an RBD pool. Pull
    # the ranked candidate list and probe them in order instead.
    limit_clause = "LIMIT 1" if not require_rbd else ""
    cur.execute(
        f"""
        SELECT n.id, n.hostname, n.available_vcpus, n.available_ram_mb,
               n.available_disk_gb
        FROM nodes n
        WHERE n.status = 'online'
          AND n.schedulable = 1
          AND n.available_vcpus  >= %s
          AND n.available_ram_mb >= %s
          AND n.available_disk_gb >= %s
          AND n.id IN (
              SELECT DISTINCT node_id FROM node_tenants
              WHERE tenant_id IN ({placeholders})
          )
        ORDER BY n.available_ram_mb DESC, n.available_vcpus DESC
        {limit_clause}
        """,
        (vcpus, ram_mb, disk_gb, *tenant_ids),
    )
    if not require_rbd:
        return cur.fetchone()

    for candidate in cur.fetchall():
        if _node_has_rbd_pool(candidate["id"]):
            return candidate
        logger.info(
            "Skipping node %d (%s) — no RBD pool; trying next candidate",
            candidate["id"], candidate["hostname"],
        )
    return None


def select_node(vcpus: int, ram_mb: int, disk_gb: int = 0,
                tenant_id: int | None = None,
                require_rbd: bool = False) -> int | None:
    """Select the best node for VM placement within a tenant.

    Two-pass algorithm:
      * Pass 1 — caller's tenant only (skipped for SHARED / PERSONAL /
        NULL, which have no node_tenants rows).
      * Pass 2 — DEFAULT placement pool.

    Rules for each pass:
      * Node must be online.
      * Node must have sufficient available_vcpus, available_ram_mb,
        available_disk_gb capacity.
      * Node must be tagged with the pass's tenant id in node_tenants.
      * Picks the node with the largest `available_ram_mb`. RAM is
        typically the binding constraint on KVM hosts.

    When `require_rbd` is True (non-quick creates, whose disks are
    RBD-backed), candidates that lack an `rbd` storage pool are skipped
    within each pass and the next best-fit host is tried instead.

    Returns node_id or None if both passes fail.
    """
    # Pass 1 eligibility: a "real" tenant with the possibility of
    # dedicated nodes. SHARED / PERSONAL / NULL are never in
    # `node_tenants`, so we skip straight to pass 2 for them.
    pass1_eligible = (
        tenant_id is not None
        and tenant_id != SHARED_TENANT_ID
        and tenant_id != PERSONAL_TENANT_ID
        and tenant_id != DEFAULT_TENANT_ID  # already covered by pass 2
    )

    conn = _get_db()
    try:
        with conn.cursor() as cur:
            if pass1_eligible:
                row = _query_best_node(cur, vcpus, ram_mb, disk_gb, [tenant_id],
                                       require_rbd=require_rbd)
                if row:
                    logger.info(
                        "Placed tenant=%s vcpus=%d ram_mb=%d on node %d "
                        "(%s, free_ram=%d MB) via pass1[tenant-dedicated]",
                        tenant_id, vcpus, ram_mb, row["id"], row["hostname"],
                        row["available_ram_mb"],
                    )
                    return row["id"]
                logger.info(
                    "Pass1 miss for tenant=%s vcpus=%d ram_mb=%d disk_gb=%d — "
                    "no tenant-dedicated node online with capacity; "
                    "falling through to DEFAULT pool",
                    tenant_id, vcpus, ram_mb, disk_gb,
                )

            # Pass 2 — DEFAULT placement pool.
            row = _query_best_node(cur, vcpus, ram_mb, disk_gb, [DEFAULT_TENANT_ID],
                                   require_rbd=require_rbd)
            if row:
                logger.info(
                    "Placed tenant=%s vcpus=%d ram_mb=%d on node %d "
                    "(%s, free_ram=%d MB) via pass2[DEFAULT]",
                    tenant_id, vcpus, ram_mb, row["id"], row["hostname"],
                    row["available_ram_mb"],
                )
                return row["id"]

            logger.info(
                "No node available for tenant=%s vcpus=%d ram_mb=%d disk_gb=%d "
                "(pass1_eligible=%s, both passes empty)",
                tenant_id, vcpus, ram_mb, disk_gb, pass1_eligible,
            )
            return None
    finally:
        conn.close()
