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
VM inventory sync.

Runs as a daemon thread.  Every POLL_INTERVAL seconds it SSHes to every
online node, runs a self-contained Python script that parses virsh XML, and
upserts the results into the vms table.

No agent needs to be installed on the KVM hosts — virsh is always present.
"""

import io
import base64
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

import paramiko
import pymysql
import pymysql.cursors

from .tenant_service import SHARED_TENANT_ID
from ..metrics import vm_sync_iterations_total, vm_sync_duration_seconds

logger = logging.getLogger("wvc.vm-sync")

_POLL_INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", "30"))

# v0.13.1: how long a previously-seen VM may be absent from poll results
# before we delete its row. A short blip (libvirtd warm-up, paramiko
# timeout, transient virsh hiccup) used to delete the row on the spot,
# nuking admin-set tenant_id + owner_user_id. Next poll would re-INSERT
# it with tenant_id=SHARED. Grace period below tolerates ~10 missed
# polls at the default 30s interval.
_STALE_VM_GRACE_SECONDS = int(os.environ.get("STALE_VM_GRACE_SECONDS", "300"))

# ---------------------------------------------------------------------------
# Python script executed on each KVM host via SSH stdin.
# Outputs a JSON array — one object per VM.
# ---------------------------------------------------------------------------
_COLLECT_SCRIPT = r'''
import subprocess, json, re, os, base64
import xml.etree.ElementTree as ET

def sh(cmd, timeout=30):
    return subprocess.run(cmd, shell=True, text=True,
                          capture_output=True, timeout=timeout).stdout.strip()

STATE_MAP = {
    'running': 'running', 'paused': 'paused',
    'shut off': 'stopped', 'crashed': 'crashed',
    'in shutdown': 'stopping', 'pmsuspended': 'suspended',
}

# Interfaces to ignore when picking the guest's primary IPv4
_IFACE_SKIP_PREFIXES = (
    'lo', 'docker', 'cni', 'flannel', 'br-',
    'virbr', 'veth', 'cali', 'kube', 'tun', 'tap',
)

def vm_ip(vm_name):
    """Best-effort: ask the guest agent for the VM's primary IPv4.

    Returns '' if the agent isn't installed/responding, or if no
    non-loopback IPv4 is reported.  Never raises.
    """
    try:
        out = sh(
            f"virsh domifaddr '{vm_name}' --source agent 2>/dev/null",
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return ''
    current = ''
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        if parts[0] != '-':
            current = parts[0]
        if not current or any(current.startswith(p) for p in _IFACE_SKIP_PREFIXES):
            continue
        proto, addr = parts[2], parts[3]
        if proto != 'ipv4' or addr == 'N/A' or '/' not in addr:
            continue
        ip = addr.split('/')[0]
        if ip.startswith('127.'):
            continue
        return ip
    return ''

def vol_pool(path):
    """Map a disk path to a storage pool name."""
    if path.startswith('rbd:') or path.startswith('/dev/rbd'):
        # rbd:poolname/volume  or  /dev/rbd/poolname/volume
        m = re.search(r'rbd[:/]+([^/]+)/', path)
        return m.group(1) if m else 'ceph'
    p = sh(f"virsh vol-pool '{path}' 2>/dev/null")
    if p:
        return p
    return os.path.basename(os.path.dirname(path)) or os.path.dirname(path)

def disk_cap_gb(vm_name, dev, path):
    """Best-effort virtual disk capacity in GB."""
    # virsh domblkinfo works for running and stopped domains
    out = sh(f"virsh domblkinfo '{vm_name}' {dev} 2>/dev/null")
    for line in out.splitlines():
        if line.startswith('Capacity:'):
            try:
                return int(line.split()[-1]) // (1024 ** 3)
            except ValueError:
                pass
    # Fallback: qemu-img for file-based disks
    if os.path.isfile(path):
        out = sh(f"qemu-img info --output=json '{path}' 2>/dev/null")
        try:
            return json.loads(out).get('virtual-size', 0) // (1024 ** 3)
        except Exception:
            pass
    return 0

# ---- collect all VMs ----
raw = sh("virsh list --all")
vms = []
for line in raw.splitlines()[2:]:          # skip "Id Name State" header + dashes
    parts = line.split(None, 2)
    if len(parts) < 2:
        continue
    name = parts[1]
    state = STATE_MAP.get(parts[2].strip() if len(parts) > 2 else '', 'unknown')

    xml_str = sh(f"virsh dumpxml '{name}'")
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        vms.append({'name': name, 'state': state, 'error': 'xml parse failed'})
        continue

    uuid   = root.findtext('uuid', '')
    ram_kb = int(root.findtext('memory', '0'))
    vcpus  = int(root.findtext('vcpu',   '0'))

    # Primary disk
    disk_gb, disk_path, datastore = 0, '', ''
    for disk in root.findall(".//disk[@device='disk']"):
        src = disk.find('source')
        tgt = disk.find('target')
        if src is None:
            continue
        # v0.13.2: detect storage backend from <disk type=...> + <source protocol=...>
        # and normalise disk_path so downstream code (vm_service.vm_delete →
        # _parse_rbd_image, frontend hasRbdDisk check, vm_create source-lookup)
        # can rely on a single canonical format. Previously RBD disks were
        # stored as bare "pool/image" with no prefix because the libvirt XML
        # for RBD uses <source protocol='rbd' name='pool/image'> (no rbd:
        # prefix in the name attribute) — which then failed _parse_rbd_image's
        # rbd:-prefix regex, causing vm_delete to silently skip the rbd rm
        # step and leak the Ceph image. We now match the format that
        # vm_create stores ("rbd:pool/image") for parity.
        disk_type = disk.get('type', 'file')
        protocol  = src.get('protocol', '')
        if disk_type == 'network' and protocol == 'rbd':
            # <source protocol='rbd' name='pool/image'>
            name_attr = src.get('name', '')
            path = f'rbd:{name_attr}' if name_attr else ''
        elif disk_type == 'block':
            # <source dev='/dev/sdX' /> or <source dev='/dev/rbd/...' />
            path = src.get('dev', '')
        elif disk_type == 'volume':
            # <source pool='X' volume='Y' />
            pool_attr = src.get('pool', '')
            vol_attr  = src.get('volume', '')
            path = f'{pool_attr}/{vol_attr}' if pool_attr and vol_attr else (vol_attr or pool_attr)
        else:
            # 'file' or unknown — file-backed, iSCSI/NBD/etc. fall through to
            # whatever attribute is present; keep prior best-effort behaviour.
            path = (src.get('file') or src.get('dev')
                    or src.get('name', '') or src.get('volume', ''))
        dev  = tgt.get('dev', 'vda') if tgt is not None else 'vda'
        disk_gb   = disk_cap_gb(name, dev, path)
        disk_path = path
        datastore = vol_pool(path)
        break   # primary disk only

    # First network interface → bridge/network name
    vlan = ''
    for iface in root.findall('.//interface'):
        src = iface.find('source')
        if src is not None:
            vlan = src.get('bridge') or src.get('network', '')
            if vlan:
                break

    vms.append({
        'name': name, 'uuid': uuid, 'state': state,
        'vcpus': vcpus, 'ram_mb': ram_kb // 1024,
        'vlan': vlan,
        'disk_gb': disk_gb, 'disk_path': disk_path, 'datastore': datastore,
        'ip': vm_ip(name) if state == 'running' else '',
        # v0.27 (HA): capture the full domain XML, base64-encoded, so the
        # HA engine can redefine the domain on a survivor when the source
        # host is DEAD (you can't dumpxml a dead host at failover time).
        # base64 keeps the JSON payload clean regardless of XML content.
        'libvirt_xml_b64': base64.b64encode(xml_str.encode()).decode() if xml_str else '',
    })

print(json.dumps(vms))
'''


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_db():
    url = urlparse(os.environ.get("DATABASE_URL", ""))
    return pymysql.connect(
        host=url.hostname, port=url.port or 3306,
        user=url.username, password=url.password,
        database=url.path.lstrip("/"),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def _decode_libvirt_xml(b64: str) -> str | None:
    """Decode the base64 domain XML captured by the collector.

    Returns the XML string, or None when absent/undecodable so the upsert
    COALESCE preserves the last-known-good XML instead of clobbering it
    with an empty value.
    """
    if not b64:
        return None
    try:
        return base64.b64decode(b64).decode("utf-8", errors="replace")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# SSH helpers (re-uses logic from node_monitor)
# ---------------------------------------------------------------------------

def _load_key(key_str: str) -> paramiko.PKey:
    for cls in (paramiko.Ed25519Key, paramiko.RSAKey,
                paramiko.ECDSAKey, paramiko.DSSKey):
        try:
            return cls.from_private_key(io.StringIO(key_str))
        except Exception:
            pass
    raise ValueError("Unsupported private key format")


def _collect_vms_from_node(node: dict) -> list[dict] | None:
    """SSH to node, run the collection script, return parsed VM list."""
    private_key_str = node.get("private_key")
    if not private_key_str:
        return None

    try:
        pkey = _load_key(private_key_str)
    except ValueError as e:
        logger.error("Node %s: %s", node["hostname"], e)
        return None

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    host = node.get("ssh_host") or node["ip_address"]
    port = int(node.get("ssh_port") or 22)
    user = node.get("ssh_user") or "root"

    try:
        client.connect(hostname=host, port=port, username=user, pkey=pkey,
                       timeout=10, look_for_keys=False, allow_agent=False)

        # Feed the script to python3 via stdin — no temp file needed on host
        stdin, stdout, stderr = client.exec_command("python3", timeout=120)
        stdin.write(_COLLECT_SCRIPT)
        stdin.channel.shutdown_write()

        raw = stdout.read().decode(errors="replace").strip()
        err = stderr.read().decode(errors="replace").strip()
        if err:
            logger.debug("Node %s vm-collect stderr: %s", node["hostname"], err[:300])

        return json.loads(raw) if raw else []

    except json.JSONDecodeError as e:
        logger.error("Node %s: JSON decode failed — %s | raw: %.200s",
                     node["hostname"], e, raw)
        return None
    except Exception as e:
        logger.warning("Node %s: vm-collect SSH failed — %s", node["hostname"], e)
        return None
    finally:
        client.close()


# ---------------------------------------------------------------------------
# DB upsert
# ---------------------------------------------------------------------------

def _upsert_vms(node_id: int, vms: list[dict]) -> None:
    """Insert or update VM rows; remove VMs no longer seen on this node.

    Retries up to 3 times on InnoDB deadlock (errno 1213) which can occur
    when multiple gunicorn workers run concurrent sync threads.
    """
    for attempt in range(3):
        try:
            _do_upsert(node_id, vms)
            return
        except pymysql.err.OperationalError as exc:
            if exc.args[0] == 1213 and attempt < 2:
                logger.warning("Node %d: deadlock on upsert, retry %d/2", node_id, attempt + 1)
                time.sleep(0.5 * (attempt + 1))
            else:
                raise


def _do_upsert(node_id: int, vms: list[dict]) -> None:
    conn = _get_db()
    now = datetime.utcnow()
    try:
        with conn.cursor() as cur:
            # ------------------------------------------------------------------
            # Preserve admin-set metadata across a reap+reinsert.
            #
            # The reap below DELETEs stale rows; a later rediscovery re-INSERTs
            # them with DEFAULT tenant_id (SHARED) / owner_user_id (NULL) /
            # priority ('default') / exempt (0) / expires_at — silently wiping
            # admin choices. Snapshot those fields by the STABLE uuid BEFORE we
            # touch anything, then re-apply them after the upserts + reap so a
            # sync cycle can never lose them.
            disc_uuids = [v.get("uuid") for v in vms if v.get("uuid")]
            preserved: dict[str, dict] = {}
            if disc_uuids:
                ph = ",".join(["%s"] * len(disc_uuids))
                cur.execute(
                    "SELECT uuid, tenant_id, owner_user_id, priority, exempt, "
                    "expires_at, renewed_at, lifecycle_state "
                    f"FROM vms WHERE uuid IN ({ph})",
                    disc_uuids,
                )
                preserved = {r["uuid"]: r for r in cur.fetchall()}

            for vm in vms:
                if "error" in vm and len(vm) <= 3:
                    continue  # skip collection errors
                name = vm.get("name", "")
                if not name:
                    continue

                # HA dedup: a VM may still be DEFINED (shut off) on a node it
                # was recovered/relocated OFF (e.g. a fenced host that came
                # back). The authoritative copy runs elsewhere. Do NOT resurrect
                # the stale copy here: if this same name already exists on a
                # DIFFERENT node in a running/paused state and the copy we see
                # here is not running, skip it. (readmit/undefine cleans the
                # stale domain; this prevents a duplicate row in the meantime.)
                this_state = vm.get("state", "unknown")
                if this_state not in ("running", "paused"):
                    cur.execute(
                        "SELECT 1 FROM vms WHERE name=%s AND node_id<>%s "
                        "AND state IN ('running','paused') LIMIT 1",
                        (name, node_id),
                    )
                    if cur.fetchone():
                        logger.info(
                            "vm_sync: skipping stale '%s' on node %d — authoritative "
                            "running copy exists on another node", name, node_id)
                        continue

                # v0.27 (HA): decode captured domain XML. Empty string when
                # capture failed/absent → stored as NULL and the COALESCE
                # below preserves the last good XML.
                libvirt_xml = _decode_libvirt_xml(vm.get("libvirt_xml_b64", ""))
                cur.execute(
                    """
                    INSERT INTO vms
                        (node_id, name, uuid, state, vcpus, ram_mb,
                         vlan, disk_gb, disk_path, datastore, ip,
                         tenant_id, libvirt_xml, last_seen_at, last_running_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                        uuid         = VALUES(uuid),
                        state        = VALUES(state),
                        vcpus        = VALUES(vcpus),
                        ram_mb       = VALUES(ram_mb),
                        vlan         = VALUES(vlan),
                        disk_gb      = VALUES(disk_gb),
                        disk_path    = VALUES(disk_path),
                        datastore    = VALUES(datastore),
                        ip           = COALESCE(NULLIF(VALUES(ip), ''), ip),
                        libvirt_xml  = COALESCE(VALUES(libvirt_xml), libvirt_xml),
                        last_seen_at = VALUES(last_seen_at),
                        -- Only advance last_running_at when this poll saw the VM
                        -- running/paused; otherwise preserve the prior value so
                        -- HA can tell a VM that WAS running (host later vanished)
                        -- from one deliberately shut down.
                        last_running_at = COALESCE(VALUES(last_running_at), last_running_at)
                        -- NOTE: tenant_id and owner_user_id are intentionally
                        -- NOT updated here. Sync only sets tenant_id on the
                        -- initial INSERT (default SHARED); any subsequent
                        -- admin reassignment via the Edit modal must survive
                        -- the next discovery cycle. The same applies to the
                        -- HA `priority` column (admin-set, ENUM default
                        -- 'default' on INSERT) — not listed here so it is
                        -- preserved across syncs.
                    """,
                    (
                        node_id, name,
                        vm.get("uuid") or None,
                        vm.get("state", "unknown"),
                        vm.get("vcpus", 0),
                        vm.get("ram_mb", 0),
                        vm.get("vlan") or None,
                        vm.get("disk_gb", 0),
                        vm.get("disk_path") or None,
                        vm.get("datastore") or None,
                        vm.get("ip") or None,
                        SHARED_TENANT_ID,
                        libvirt_xml,
                        now,
                        now if vm.get("state") in ("running", "paused") else None,
                    ),
                )

            # Reap rows whose last_seen_at is older than the grace
            # threshold.
            #
            # v0.13.1: the previous immediate `DELETE ... WHERE name NOT
            # IN (seen_names)` would wipe a VM as soon as ONE poll
            # missed it — and then the next successful poll re-INSERTed
            # it with tenant_id=SHARED, losing any admin-set tenancy.
            # Bounded delays in libvirtd / SSH / virsh routinely make a
            # poll miss a VM that is still very much alive. We now reap
            # only rows whose last_seen_at is older than
            # STALE_VM_GRACE_SECONDS (default 300s ≈ 10 missed polls at
            # the default 30s interval). The UPSERTs above just touched
            # last_seen_at for every VM we *did* see, so they are
            # naturally safe; the `NOT IN (seen_names)` filter is no
            # longer needed.
            #
            # Note: _do_upsert is only called when the collector
            # returned a list (empty or not); a failed SSH / JSON
            # decode returns None and is filtered out in _sync_loop,
            # so a transient SSH outage does NOT trigger any reap.
            # A node that legitimately has zero VMs will simply have
            # its rows age out and be reaped after the grace period.
            stale_threshold = now - timedelta(seconds=_STALE_VM_GRACE_SECONDS)
            cur.execute(
                "DELETE FROM vms "
                "WHERE node_id = %s "
                "  AND (last_seen_at IS NULL OR last_seen_at < %s)",
                (node_id, stale_threshold),
            )
            if cur.rowcount:
                logger.info(
                    "vm_sync: reaped %d stale VM row(s) on node_id=%d "
                    "(unseen for >%ds)",
                    cur.rowcount, node_id, _STALE_VM_GRACE_SECONDS,
                )

            # Re-apply the admin-metadata snapshot by uuid. Restores admin-set
            # tenancy/owner/priority/exempt/TTL on any row that was reaped+
            # reinserted this cycle; harmless no-op write otherwise.
            for v_uuid, meta in preserved.items():
                cur.execute(
                    "UPDATE vms SET tenant_id=%s, owner_user_id=%s, priority=%s, "
                    "exempt=%s, expires_at=%s, renewed_at=%s, lifecycle_state=%s "
                    "WHERE uuid=%s",
                    (meta["tenant_id"], meta["owner_user_id"], meta["priority"],
                     meta["exempt"], meta["expires_at"], meta["renewed_at"],
                     meta["lifecycle_state"], v_uuid),
                )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Sync loop
# ---------------------------------------------------------------------------

def _probe_guest_alive(ip: str, timeout: float = 2.0) -> bool:
    """Best-effort liveness probe of a guest by IP, used when its HOST is
    unreachable (so we can't ask libvirt). Returns True if the guest appears
    up.

    Tries ICMP ping first (cheap, no open port needed), then falls back to a
    TCP connect on a few common ports. A positive result means the VM is still
    running somewhere reachable — do NOT mark it 'unknown'/down.
    """
    if not ip:
        return False
    import subprocess
    import socket as _socket
    # ICMP ping (1 packet, short deadline).
    try:
        rc = subprocess.run(
            ["ping", "-c", "1", "-W", str(int(max(timeout, 1))), ip],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=timeout + 1,
        ).returncode
        if rc == 0:
            return True
    except Exception:
        pass
    # TCP fallback — some guests drop ICMP but answer on a port.
    for port in (22, 3389, 443, 80):
        try:
            with _socket.create_connection((ip, port), timeout=timeout):
                return True
        except Exception:
            continue
    return False


def _mark_node_vms_on_unreachable(node_id: int) -> None:
    """When a node can't be polled (SSH/collect failed), we can no longer trust
    the last-known VM state. Set each VM on the node to 'unknown', EXCEPT where
    the guest's own IP is reachable — a live guest means it's still running even
    though its host is unreachable.

    This replaces the previous behaviour of leaving VM state frozen at its
    stale last-known value.
    """
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, state, ip FROM vms WHERE node_id = %s", (node_id,))
            rows = cur.fetchall()
            for vm in rows:
                ip = (vm.get("ip") or "").strip()
                if ip and _probe_guest_alive(ip):
                    # Guest still answering — keep it running, note we inferred it.
                    if vm["state"] != "running":
                        cur.execute(
                            "UPDATE vms SET state='running' WHERE id=%s", (vm["id"],))
                    logger.info(
                        "vm_sync: node %d unreachable but guest %s (%s) responds "
                        "— keeping 'running'", node_id, vm["name"], ip)
                else:
                    if vm["state"] not in ("unknown",):
                        cur.execute(
                            "UPDATE vms SET state='unknown' WHERE id=%s", (vm["id"],))
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("vm_sync: failed to mark node %d VMs unknown", node_id)
    finally:
        conn.close()


def _sync_loop() -> None:
    logger.info("VM sync started (interval %ds)", _POLL_INTERVAL)
    while True:
        iteration_start = time.perf_counter()
        outcome = "ok"
        try:
            conn = _get_db()
            try:
                with conn.cursor() as cur:
                    # Poll every node we have SSH creds for — NOT only 'online'
                    # ones — so that a node which just went unreachable still
                    # gets its VMs marked 'unknown' (or probed) this cycle.
                    cur.execute(
                        """
                        SELECT n.id, n.hostname, n.ip_address, n.ssh_host,
                               n.ssh_port, n.ssh_user, k.private_key
                        FROM nodes n
                        LEFT JOIN ssh_keys k ON k.id = n.ssh_key_id
                        WHERE n.status != 'maintenance'
                        """
                    )
                    nodes = cur.fetchall()
            finally:
                conn.close()

            for node in nodes:
                vms = _collect_vms_from_node(node)
                if vms is not None:
                    _upsert_vms(node["id"], vms)
                    logger.debug("Node %s: synced %d VMs",
                                 node["hostname"], len(vms))
                else:
                    # Node unreachable — don't leave VM state frozen. Mark
                    # 'unknown', or keep 'running' if the guest IP still answers.
                    _mark_node_vms_on_unreachable(node["id"])
                    logger.debug("Node %s: unreachable — VMs marked unknown/probed",
                                 node["hostname"])

        except Exception as e:
            outcome = "error"
            logger.error("VM sync loop error: %s", e, exc_info=True)
        finally:
            vm_sync_iterations_total.labels(result=outcome).inc()
            vm_sync_duration_seconds.observe(time.perf_counter() - iteration_start)

        time.sleep(_POLL_INTERVAL)


def start_vm_sync() -> None:
    """Start the VM inventory sync in a background daemon thread."""
    t = threading.Thread(target=_sync_loop, daemon=True, name="vm-sync")
    t.start()
    logger.info("VM sync thread launched")
