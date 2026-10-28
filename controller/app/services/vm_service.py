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

"""VM action service — executes virsh commands on KVM nodes via SSH."""

import base64
import io
import json
import logging
import os
import secrets
import time
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import paramiko
import pymysql
import pymysql.cursors
import redis as redis_lib

from ..services.vm_create import get_node_resources as _get_node_resources_ext
from .tenant_service import (
    SHARED_TENANT_ID, PERSONAL_TENANT_ID, get_user_tenant_ids,
)
from ..metrics import (
    track_operation, track_vm_action, track_vm_migrate,
)

logger = logging.getLogger("wvc.vm-service")

# ---------------------------------------------------------------------------
# virsh command templates and expected post-action states
# ---------------------------------------------------------------------------

_VIRSH_CMD = {
    "start":     "virsh start '{name}'",
    "stop":      "virsh shutdown '{name}'",
    "force-off": "virsh destroy '{name}'",
    "reboot":    "virsh reboot '{name}'",
    "suspend":   "virsh suspend '{name}'",
    "resume":    "virsh resume '{name}'",
}

_NEXT_STATE = {
    "start":     "running",
    "stop":      "stopping",
    "force-off": "stopped",
    "reboot":    "running",
    "suspend":   "suspended",
    "resume":    "running",
}


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


def _get_vm_with_node(vm_id: int) -> dict | None:
    """Return VM row joined with node + SSH private key, or None.

    Includes tenant_id and owner_user_id so callers (notably vm_edit)
    can run service-layer authorisation without a second query.
    """
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT v.id, v.name, v.state, v.node_id,
                       v.tenant_id, v.owner_user_id,
                       v.libvirt_xml,
                       n.hostname, n.ip_address,
                       n.ssh_host, n.ssh_port, n.ssh_user,
                       n.cpu_model AS node_cpu_model,
                       n.status AS node_status,
                       k.private_key
                FROM vms v
                JOIN nodes n ON n.id = v.node_id
                LEFT JOIN ssh_keys k ON k.id = n.ssh_key_id
                WHERE v.id = %s
                """,
                (vm_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def _update_vm_state(vm_id: int, state: str) -> None:
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE vms SET state = %s WHERE id = %s", (state, vm_id))
        conn.commit()
    finally:
        conn.close()


def _update_vm_node(vm_id: int, target_node_id: int, state: str) -> None:
    """Move a VM to a different node and set its state.

    The vm_sync daemon may race us:  if it discovers the migrated VM on
    the destination node before this UPDATE runs, it will INSERT a new
    row for (target_node_id, name), which then collides with our UPDATE
    via the unique constraint uq_node_name(node_id, name).

    On collision we treat the destination row as authoritative — vm_sync
    populated it from the live host XML, so it has the freshest data.
    We delete the now-stale source row and set the desired state on the
    destination row.
    """
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            # Resolve the VM name up-front so we can purge stale copies on any
            # OTHER node (e.g. the fenced source it was recovered off).
            cur.execute("SELECT name FROM vms WHERE id = %s", (vm_id,))
            _row = cur.fetchone()
            vm_name = _row["name"] if _row else None

            try:
                cur.execute(
                    "UPDATE vms SET node_id = %s, state = %s WHERE id = %s",
                    (target_node_id, state, vm_id),
                )
                # Purge any stale rows for this VM on other nodes (the old/
                # fenced source). The row we just updated is authoritative.
                if vm_name:
                    cur.execute(
                        "DELETE FROM vms WHERE name = %s AND node_id <> %s",
                        (vm_name, target_node_id),
                    )
                conn.commit()
                return
            except pymysql.err.IntegrityError as exc:
                if exc.args[0] != 1062:
                    raise
                conn.rollback()

            # Race with vm_sync — destination row already exists.
            cur.execute("SELECT name FROM vms WHERE id = %s", (vm_id,))
            row = cur.fetchone()
            if not row:
                # Source row already gone; nothing to do.
                logger.warning(
                    "_update_vm_node: source vm_id=%d disappeared during migration",
                    vm_id,
                )
                return

            name = row["name"]
            logger.warning(
                "_update_vm_node: vm_sync raced migration of '%s' (vm_id=%d) "
                "to node_id=%d — reconciling by deleting stale source row",
                name, vm_id, target_node_id,
            )
            cur.execute("DELETE FROM vms WHERE id = %s", (vm_id,))
            # Also purge any other stale copies on non-destination nodes.
            cur.execute(
                "DELETE FROM vms WHERE name = %s AND node_id <> %s",
                (name, target_node_id),
            )
            cur.execute(
                "UPDATE vms SET state = %s WHERE node_id = %s AND name = %s",
                (state, target_node_id, name),
            )
            conn.commit()
    finally:
        conn.close()


def _get_node(node_id: int) -> dict | None:
    """Return node row joined with SSH key, or None."""
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT n.id, n.hostname, n.ip_address, n.status,
                       n.ssh_host, n.ssh_port, n.ssh_user,
                       n.cpu_model,
                       k.private_key
                FROM nodes n
                LEFT JOIN ssh_keys k ON k.id = n.ssh_key_id
                WHERE n.id = %s
                """,
                (node_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------

def _load_key(key_str: str) -> paramiko.PKey:
    for cls in (paramiko.Ed25519Key, paramiko.RSAKey,
                paramiko.ECDSAKey, paramiko.DSSKey):
        try:
            return cls.from_private_key(io.StringIO(key_str))
        except Exception:
            pass
    raise ValueError("Unsupported private key format")


def _ssh_run(node: dict, command: str) -> tuple[str, str, int]:
    """Open an SSH connection to node, run command, return (stdout, stderr, exit_code)."""
    pkey = _load_key(node["private_key"])
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    host = node.get("ssh_host") or node["ip_address"]
    port = int(node.get("ssh_port") or 22)
    user = node.get("ssh_user") or "root"

    try:
        client.connect(
            hostname=host, port=port, username=user, pkey=pkey,
            timeout=10, look_for_keys=False, allow_agent=False,
        )
        _stdin, stdout, stderr = client.exec_command(command, timeout=30)
        _stdin.close()
        out = stdout.read().decode(errors="replace").strip()
        err = stderr.read().decode(errors="replace").strip()
        rc  = stdout.channel.recv_exit_status()
        return out, err, rc
    finally:
        client.close()


def _ssh_run_long(node: dict, command: str, timeout: int = 3600) -> tuple[str, str, int]:
    """Like _ssh_run but with a much longer timeout — for disk-copy / live migration."""
    pkey = _load_key(node["private_key"])
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    host = node.get("ssh_host") or node["ip_address"]
    port = int(node.get("ssh_port") or 22)
    user = node.get("ssh_user") or "root"
    try:
        client.connect(hostname=host, port=port, username=user, pkey=pkey,
                       timeout=30, look_for_keys=False, allow_agent=False)
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        _stdin.close()
        out = stdout.read().decode(errors="replace").strip()
        err = stderr.read().decode(errors="replace").strip()
        rc  = stdout.channel.recv_exit_status()
        return out, err, rc
    finally:
        client.close()


def _write_remote_file(node: dict, remote_path: str, content: str) -> dict:
    """Write content to a file on a remote node via SFTP (binary, Unix line endings)."""
    pkey = _load_key(node["private_key"])
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    host = node.get("ssh_host") or node["ip_address"]
    port = int(node.get("ssh_port") or 22)
    user = node.get("ssh_user") or "root"
    try:
        client.connect(hostname=host, port=port, username=user, pkey=pkey,
                       timeout=10, look_for_keys=False, allow_agent=False)
        sftp = client.open_sftp()
        try:
            # Normalise to Unix line endings and write as raw bytes so that
            # OpenSSH on the remote host can parse the key without libcrypto errors.
            data = content.replace('\r\n', '\n').replace('\r', '\n').encode('utf-8')
            with sftp.open(remote_path, "wb") as fh:
                fh.write(data)
        finally:
            sftp.close()
        return {"ok": True}
    except Exception as exc:
        return {"error": f"SFTP write {host}:{remote_path}: {exc}"}
    finally:
        client.close()


def _get_disk_info(vm: dict) -> list[dict]:
    """
    Return disk attachments for a VM:
      [{"type": "file"|"network"|"block", "target": "vda", "source": "/path/or/empty"}]
    Parses output of: virsh domblklist <name> --details
    """
    out, _err, rc = _ssh_run(vm, f"virsh domblklist '{vm['name']}' --details")
    if rc != 0 or not out:
        return []
    disks = []
    for line in out.splitlines()[2:]:   # first two lines are header
        parts = line.split()
        if len(parts) < 3:
            continue
        disk_type = parts[0]            # file | network | block
        device    = parts[1]            # disk | cdrom | floppy
        if device != "disk":
            continue
        target = parts[2]               # vda, vdb …
        source = parts[3] if len(parts) > 3 else ""
        disks.append({"type": disk_type, "target": target, "source": source})
    return disks


def _is_shared_disk(disk: dict) -> bool:
    """Return True if disk lives on shared/Ceph storage — no local copy needed."""
    if disk["type"] == "network":
        return True
    src = disk.get("source", "")
    return src.startswith("rbd:") or "/dev/rbd" in src


def _copy_disk_via_controller(src_node: dict, dst_node: dict,
                               src_path: str, dst_path: str) -> dict:
    """
    Stream a disk image from src_node to dst_node relayed through the controller.
    Opens SFTP to both nodes simultaneously and transfers in 64 MB chunks to
    avoid loading the full disk image into memory.
    """
    src_pkey = _load_key(src_node["private_key"])
    dst_pkey = _load_key(dst_node["private_key"])
    src_client = paramiko.SSHClient()
    src_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    dst_client = paramiko.SSHClient()
    dst_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        src_client.connect(
            hostname=src_node.get("ssh_host") or src_node["ip_address"],
            port=int(src_node.get("ssh_port") or 22),
            username=src_node.get("ssh_user") or "root",
            pkey=src_pkey, timeout=30, look_for_keys=False, allow_agent=False,
        )
        dst_client.connect(
            hostname=dst_node.get("ssh_host") or dst_node["ip_address"],
            port=int(dst_node.get("ssh_port") or 22),
            username=dst_node.get("ssh_user") or "root",
            pkey=dst_pkey, timeout=30, look_for_keys=False, allow_agent=False,
        )
        src_sftp = src_client.open_sftp()
        dst_sftp = dst_client.open_sftp()
        try:
            CHUNK = 64 * 1024 * 1024  # 64 MB
            logger.info("Disk copy start: %s:%s → %s:%s",
                        src_node["hostname"], src_path, dst_node["hostname"], dst_path)
            with src_sftp.open(src_path, "rb") as src_fh:
                with dst_sftp.open(dst_path, "wb") as dst_fh:
                    while True:
                        data = src_fh.read(CHUNK)
                        if not data:
                            break
                        dst_fh.write(data)
            logger.info("Disk copy complete: %s → %s", src_path, dst_path)
            return {"ok": True}
        finally:
            src_sftp.close()
            dst_sftp.close()
    except Exception as exc:
        logger.error("Disk copy %s:%s → %s:%s failed: %s",
                     src_node["hostname"], src_path, dst_node["hostname"], dst_path, exc)
        return {"error": f"Disk copy failed: {exc}"}
    finally:
        src_client.close()
        dst_client.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_vm_xml(vm_id: int) -> dict:
    """Return the live libvirt domain XML for a VM.

    Uses `virsh dumpxml` (not --migratable) so the returned document is
    the full runtime XML — matching what the admin will edit and redefine.
    """
    vm = _get_vm_with_node(vm_id)
    if not vm:
        return {"error": "VM not found"}
    if not vm.get("private_key"):
        return {"error": f"Node {vm['hostname']} has no SSH key configured"}
    out, err, rc = _ssh_run(vm, f"virsh dumpxml '{vm['name']}'")
    if rc != 0 or not out:
        return {"error": f"virsh dumpxml failed: {err or 'empty output'}"}
    return {"xml": out}


def set_vm_xml(vm_id: int, new_xml: str) -> dict:
    """Redefine a VM from admin-edited XML.

    Basic safeguards:
      * Parseable XML with a <domain> root and a <name> matching the
        record — we refuse renames to avoid orphaning the DB row.
      * Written to a temp file on the node and defined via `virsh define`;
        libvirt will reject structurally invalid docs before we touch
        the running config.
      * NOT applied live — libvirt only picks up changes on next boot
        for most sections.  Caller (UI) is warned in the modal.
    """
    vm = _get_vm_with_node(vm_id)
    if not vm:
        return {"error": "VM not found"}
    if not vm.get("private_key"):
        return {"error": f"Node {vm['hostname']} has no SSH key configured"}

    try:
        root = ET.fromstring(new_xml)
    except ET.ParseError as exc:
        return {"error": f"Invalid XML: {exc}"}
    if root.tag != "domain":
        return {"error": f"Root element must be <domain>, got <{root.tag}>"}
    name_el = root.find("name")
    if name_el is None or (name_el.text or "").strip() != vm["name"]:
        return {"error": f"<name> must remain '{vm['name']}' (renames are not supported)"}

    tmp_xml = f"/tmp/wvc_xmledit_{uuid.uuid4().hex[:8]}.xml"
    wr = _write_remote_file(vm, tmp_xml, new_xml)
    if "error" in wr:
        return wr
    out, err, rc = _ssh_run(vm, f"virsh define '{tmp_xml}'")
    _ssh_run(vm, f"rm -f '{tmp_xml}'")
    if rc != 0:
        return {"error": f"virsh define failed: {err or out}"}
    logger.info("VM %s (id=%d) XML redefined by admin", vm["name"], vm_id)
    return {"ok": True, "message": out or "Domain defined"}


@track_vm_action
def vm_action(vm_id: int, action: str) -> dict:
    """
    Execute a virsh action on a VM via SSH.

    action: start | stop | force-off | reboot | suspend | resume

    Returns {"ok": True, "vm": name, "action": action}
         or {"error": "..."} with a human-readable message.
    """
    if action not in _VIRSH_CMD:
        return {"error": f"Unknown action '{action}'"}

    # Lifecycle gate: a VM auto-stopped at expiry may NOT be powered back on
    # except by renewing (which clears lifecycle_state and restarts it via the
    # renew endpoint). This enforces the 1-year TTL — you can't sidestep expiry
    # by just pressing Start. Only power-on actions are blocked; stop/force-off
    # remain allowed. Best-effort: if the lookup fails we do not block.
    if action in ("start", "reboot", "resume"):
        try:
            conn = _get_db()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT lifecycle_state FROM vms WHERE id = %s", (vm_id,))
                    _lc = cur.fetchone()
            finally:
                conn.close()
            if _lc and _lc.get("lifecycle_state") == "auto_stopped":
                return {"error": (
                    "This VM was stopped because its 1-year term expired. "
                    "Click Renew to extend it for another 12 months — that will "
                    "restart it. It cannot be powered on until renewed."
                ), "expired": True}
        except Exception:
            logger.debug("lifecycle power-on gate lookup failed for vm %s",
                         vm_id, exc_info=True)

    vm = _get_vm_with_node(vm_id)
    if not vm:
        return {"error": "VM not found"}
    if not vm.get("private_key"):
        return {"error": "No SSH key configured for this node"}

    cmd = _VIRSH_CMD[action].format(name=vm["name"])
    logger.info("VM %s (%d): %s on node %s", vm["name"], vm_id, cmd, vm["hostname"])

    try:
        out, err, rc = _ssh_run(vm, cmd)
    except Exception as exc:
        logger.error("VM %s action '%s' SSH error: %s", vm["name"], action, exc)
        return {"error": f"SSH error: {exc}"}

    if rc != 0:
        raw = (err or out or f"virsh exited {rc}").strip()
        # Strip "error: " prefixes and collapse to a single readable message
        lines = [l.removeprefix("error: ").strip() for l in raw.splitlines() if l.strip()]
        msg = " — ".join(lines) if lines else f"virsh exited {rc}"
        logger.warning("VM %s action '%s' failed (rc=%d): %s", vm["name"], action, rc, raw)
        return {"error": msg}

    # Optimistic DB update — vm_sync will correct on next pass if needed
    _update_vm_state(vm_id, _NEXT_STATE[action])
    logger.info("VM %s action '%s' succeeded", vm["name"], action)
    return {"ok": True, "vm": vm["name"], "action": action}


def vm_console(vm_id: int, user_id: int) -> dict:
    """
    Create a single-use console token for VNC access.

    SSHes to the node to discover the VNC display port, then stores a
    short-lived token in Redis that the console proxy will consume when
    the browser opens the WebSocket connection.

    Returns {"token": "...", "wsUrl": "/console-ws"}
         or {"error": "..."}.
    """
    vm = _get_vm_with_node(vm_id)
    if not vm:
        return {"error": "VM not found"}
    if not vm.get("private_key"):
        return {"error": "No SSH key configured for this node"}

    # Discover VNC display — only works when the VM is running
    try:
        out, err, rc = _ssh_run(vm, f"virsh vncdisplay '{vm['name']}'")
    except Exception as exc:
        logger.error("VM %s console SSH error: %s", vm["name"], exc)
        return {"error": f"SSH error: {exc}"}

    if rc != 0 or not out.strip():
        msg = err.strip() or "VM has no VNC display (not running or VNC not configured)"
        return {"error": msg}

    # Parse "127.0.0.1:0"  or  ":1"  → vnc_port = 5900 + display_number
    try:
        display  = int(out.strip().rsplit(":", 1)[-1])
        vnc_port = 5900 + display
    except ValueError:
        return {"error": f"Unexpected vncdisplay output: {out!r}"}

    # Store single-use token in Redis (TTL = 60 s to open the WebSocket)
    token = str(uuid.uuid4())
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    r = redis_lib.from_url(redis_url)
    token_data = json.dumps({
        "vm_id":    vm_id,
        "type":     "vnc",
        "user_id":  user_id,
        "node_ip":  vm["ip_address"],
        "ssh_user": vm.get("ssh_user") or "root",
        "ssh_key":  vm["private_key"],
        "vnc_port": vnc_port,
    })
    r.setex(f"console_token:{token}", 60, token_data)

    logger.info("Console token issued: vm=%s user=%s vnc_port=%d", vm["name"], user_id, vnc_port)
    return {"token": token, "wsUrl": "/console-ws"}


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def _strip_host_devices(vm_xml: str) -> tuple[str, list[str]]:
    """
    Remove host-specific passthrough devices from VM XML before defining on a
    different host.  PCI and USB hostdevs reference physical addresses on the
    source machine that will not exist (or be in use) on the target.

    Returns (cleaned_xml, [list of removed device descriptions]).
    """
    try:
        root = ET.fromstring(vm_xml)
    except ET.ParseError:
        return vm_xml, []

    removed = []
    devices = root.find("devices")
    if devices is None:
        return vm_xml, []

    # Remove LOCAL file-backed disks/cdroms (e.g. the cloud-init seed ISO under
    # /var/lib/libvirt/images/wvc-seed/). These live on the source host's local
    # filesystem and do NOT exist on the target, so a relocated domain fails to
    # start ("Cannot access storage file ..."). The seed ISO is a one-time
    # first-boot provisioning artifact and is not needed after relocation.
    # Shared network disks (RBD) are kept.
    dropped_local_disk = False
    for disk in list(devices.findall("disk")):
        if disk.get("type") != "file":
            continue  # keep network (rbd), block, volume disks
        src = disk.find("source")
        path = src.get("file", "") if src is not None else ""
        device = disk.get("device", "disk")  # 'disk' or 'cdrom'
        # Only strip cdroms/seed ISOs, never a file-backed primary data disk
        # (dropping a real disk would corrupt the guest). Restrict to cdrom or
        # the known wvc-seed path.
        if device == "cdrom" or "wvc-seed" in path or path.endswith("-seed.iso"):
            tgt = disk.find("target")
            tdev = tgt.get("dev", "?") if tgt is not None else "?"
            devices.remove(disk)
            removed.append(f"local {device} {tdev} ({path or 'no source'})")
            dropped_local_disk = True

    # If we dropped a cdrom, also drop any <boot dev='cdrom'/> so the VM boots
    # from its remaining (RBD) disk instead of a now-missing cdrom.
    if dropped_local_disk:
        os_el = root.find("os")
        if os_el is not None:
            for boot in list(os_el.findall("boot")):
                if boot.get("dev") == "cdrom":
                    os_el.remove(boot)
                    removed.append("boot dev=cdrom")

    for hostdev in list(devices.findall("hostdev")):
        dev_type = hostdev.get("type", "")
        if dev_type not in ("pci", "usb"):
            continue
        # Build a human-readable address for the log
        source = hostdev.find("source/address")
        if source is not None:
            if dev_type == "pci":
                desc = (
                    f"pci {source.get('domain','0000')}:"
                    f"{source.get('bus','??')}:"
                    f"{source.get('slot','??')}."
                    f"{source.get('function','?')}"
                )
            else:
                desc = (
                    f"usb bus={source.get('bus','?')} "
                    f"device={source.get('device','?')}"
                )
        else:
            desc = f"{dev_type} hostdev"
        devices.remove(hostdev)
        removed.append(desc)

    if not removed:
        return vm_xml, []

    return ET.tostring(root, encoding="unicode"), removed


def _neutralize_cpu_features(vm_xml: str) -> tuple[str, bool]:
    """
    Make the <cpu> block portable across hosts with differing CPU features for
    an OFFLINE migration.

    `virsh dumpxml --migratable` expands a host-model/host-passthrough CPU into
    an explicit <model> + <feature name='...'/> list reflecting the SOURCE host
    (e.g. an Intel 'mpx' feature). `virsh define` on a destination whose CPU
    lacks one of those features is then rejected at define time with
    "missing features: MPX" — even though the VM is STOPPED and will boot fresh
    on the target, so the source's feature list is irrelevant.

    We reset the block to the abstract form kvm-orchestrator uses at create time
    (`<cpu mode='host-model' check='partial'><model fallback='allow'/></cpu>`),
    dropping the explicit <feature> children and any pinned <model>/<vendor>.
    libvirt then resolves CPU features against the DESTINATION host. CPU
    topology (<topology .../>) is preserved so vCPU/socket/core/thread layout
    is unchanged.

    Returns (xml, changed). Only applied on the offline path.
    """
    try:
        root = ET.fromstring(vm_xml)
    except ET.ParseError:
        return vm_xml, False

    cpu = root.find("cpu")
    if cpu is None:
        return vm_xml, False

    had_explicit = cpu.find("feature") is not None or cpu.find("model") is not None

    # Preserve topology if present; drop everything else under <cpu>.
    topology = cpu.find("topology")
    for child in list(cpu):
        cpu.remove(child)

    # Reset to the portable abstract definition used at VM create time. Clear
    # any stale attributes left from a pinned 'custom' CPU (e.g. match='exact')
    # so only the intended host-model attrs remain.
    cpu.attrib.clear()
    cpu.set("mode", "host-model")
    cpu.set("check", "partial")

    model = ET.SubElement(cpu, "model")
    model.set("fallback", "allow")
    if topology is not None:
        cpu.append(topology)

    # Only report a change if we actually removed a pinned model/feature list.
    return ET.tostring(root, encoding="unicode"), had_explicit


def _recover_migrate(vm: dict, target: dict) -> dict:
    """Recover a VM from a DEAD/fenced source node by rebuilding its domain on
    `target` from the last-known-good libvirt_xml (captured by vm_sync). The
    disk is shared Ceph and the source is fenced, so no source access is needed.
    """
    vm_name = vm["name"]
    xml = vm.get("libvirt_xml")
    if not xml:
        return {"error": f"No stored libvirt_xml for '{vm_name}' — cannot rebuild. "
                         "The VM was never captured by a sync while running."}

    xml, stripped = _strip_host_devices(xml)
    if stripped:
        logger.info("recover %s: stripped %s", vm_name, stripped)

    import uuid as _uuid
    tmp = f"/tmp/wvc_recover_{_uuid.uuid4().hex[:8]}.xml"
    wr = _write_remote_file(target, tmp, xml)
    if "error" in wr:
        return wr

    out, err, rc = _ssh_run(target, f"virsh define '{tmp}'")
    if rc != 0:
        _ssh_run(target, f"rm -f '{tmp}'")
        return {"error": f"Failed to define VM on target {target['hostname']}: {err or out}"}

    out, err, rc = _ssh_run(target, f"virsh start '{vm_name}'")
    _ssh_run(target, f"rm -f '{tmp}'")
    if rc != 0:
        return {"error": f"Defined on {target['hostname']} but start failed: {err or out}",
                "defined": True}

    # Enable autostart on the NEW host so the recovered VM comes back on its own
    # if this target later reboots (matches the fleet convention where VMs are
    # autostart-enabled). Best-effort — never fail the recovery over it.
    _o, _e, arc = _ssh_run(target, f"virsh autostart '{vm_name}'")
    if arc != 0:
        logger.warning("recover %s: could not enable autostart on %s (non-fatal)",
                       vm_name, target["hostname"])

    _update_vm_node(vm["id"], target["id"], "running")
    logger.warning("RECOVERED VM %s onto %s (rebuilt from stored XML, autostart set)",
                   vm_name, target["hostname"])
    return {"ok": True, "vm": vm_name, "from_node": vm.get("hostname"),
            "to_node": target["hostname"], "recovered": True}


def _offline_migrate(vm: dict, target: dict) -> dict:
    """Offline migration.
    Offline migration for a stopped VM:
      1. virsh dumpxml --migratable on source
      2. Copy local disk images via controller relay (Ceph disks skipped)
      3. virsh define on destination
      4. virsh undefine on source
    """
    vm_name = vm["name"]

    # Portable XML from source
    out, err, rc = _ssh_run(vm, f"virsh dumpxml --migratable '{vm_name}'")
    if rc != 0 or not out:
        return {"error": f"Failed to get VM XML: {err or 'empty output'}"}
    vm_xml = out

    # Strip PCI/USB passthrough hostdevs — they reference host-specific physical
    # addresses that will not be valid (or may already be in use) on the target.
    vm_xml, stripped = _strip_host_devices(vm_xml)
    if stripped:
        logger.warning(
            "VM %s: removed %d host-specific device(s) from XML for offline migration: %s",
            vm_name, len(stripped), stripped,
        )

    # Neutralize the source host's expanded CPU feature list. `--migratable`
    # above pins the source CPU (e.g. Intel 'mpx'); defining that on a target
    # CPU without those features fails with "missing features: ...". The VM is
    # stopped and reboots fresh on the target, so reset to the portable
    # host-model form and let the destination resolve features.
    vm_xml, cpu_changed = _neutralize_cpu_features(vm_xml)
    if cpu_changed:
        logger.info(
            "VM %s: neutralized source CPU feature list for offline migration "
            "(reset to host-model so target resolves features)", vm_name,
        )

    # Disk list
    disks = _get_disk_info(vm)
    logger.info("VM %s offline migration disks: %s", vm_name, disks)

    for disk in disks:
        if _is_shared_disk(disk):
            logger.info("Disk %s is shared storage — no copy needed", disk["target"])
            continue
        src_path = disk.get("source", "")
        if not src_path:
            continue
        dest_dir = os.path.dirname(src_path)
        _ssh_run(target, f"mkdir -p '{dest_dir}'")
        result = _copy_disk_via_controller(vm, target, src_path, src_path)
        if "error" in result:
            return result

    # Write XML to temp file on destination, define, remove temp
    tmp_xml = f"/tmp/wvc_migrate_{uuid.uuid4().hex[:8]}.xml"
    wr = _write_remote_file(target, tmp_xml, vm_xml)
    if "error" in wr:
        return wr

    out, err, rc = _ssh_run(target, f"virsh define '{tmp_xml}'")
    _ssh_run(target, f"rm -f '{tmp_xml}'")
    if rc != 0:
        return {"error": f"Failed to define VM on target: {err or out}"}

    # Undefine on source (domain only — keep disk if local, already copied)
    out, err, rc = _ssh_run(vm, f"virsh undefine '{vm_name}'")
    if rc != 0:
        logger.warning("Failed to undefine %s on source %s: %s", vm_name, vm["hostname"], err or out)

    return {"ok": True}


def _live_migrate(vm: dict, target: dict) -> dict:
    """
    Live migration using virsh migrate with qemu+ssh transport.
    Temporarily writes the SSH private key to the source node so it can
    authenticate node-to-node for the migration.  Key is always cleaned up.
    """
    vm_name  = vm["name"]
    dst_host = target.get("ssh_host") or target["ip_address"]
    dst_user = target.get("ssh_user") or "root"

    disks    = _get_disk_info(vm)
    has_local = any(not _is_shared_disk(d) and d.get("source") for d in disks)

    # Write the SSH private key to the source node via base64-encoded echo.
    # Using SFTP for this is unreliable — OpenSSH's libcrypto is strict about
    # exact byte content, and SFTP text/binary mode handling varies by paramiko
    # version.  Base64 → pipe → base64 -d guarantees byte-perfect output.
    normalized_key = vm["private_key"].replace('\r\n', '\n').replace('\r', '\n')
    if not normalized_key.endswith('\n'):
        normalized_key += '\n'
    key_b64 = base64.b64encode(normalized_key.encode('utf-8')).decode('ascii')
    key_tmp = f"/tmp/wvc_mig_{uuid.uuid4().hex[:8]}"
    out, err, rc = _ssh_run(vm,
        f"printf '%s' '{key_b64}' | base64 -d > '{key_tmp}' && chmod 600 '{key_tmp}'"
    )
    if rc != 0:
        return {"error": f"Failed to stage SSH key on source node: {err or out}"}

    try:
        dest_uri = (
            f"qemu+ssh://{dst_user}@{dst_host}/system"
            f"?keyfile={key_tmp}&no_verify=1"
        )
        flags = "--live --persistent --undefinesource"
        if has_local:
            flags += " --copy-storage-all"
        cmd = f"virsh migrate {flags} '{vm_name}' '{dest_uri}'"
        logger.info("Live migrate VM %s: flags=%s dest=%s:%s", vm_name, flags, dst_user, dst_host)

        out, err, rc = _ssh_run_long(vm, cmd, timeout=3600)
        if rc != 0:
            raw   = (err or out or f"virsh migrate exited {rc}").strip()
            lines = [l.removeprefix("error: ").strip() for l in raw.splitlines() if l.strip()]
            msg   = " — ".join(lines) if lines else f"virsh migrate exited {rc}"
            return {"error": msg}

        return {"ok": True}
    finally:
        _ssh_run(vm, f"rm -f '{key_tmp}'")


def _parse_rbd_image(source: str) -> str | None:
    """
    Extract pool/image from a libvirt RBD source path.

    Handles:
      rbd:pool/image            → pool/image
      rbd:pool/image:id=...     → pool/image
      /dev/rbd/pool/image       → pool/image
      pool/image                → pool/image   (defensive: bare form that
                                                vm_sync used to store before
                                                v0.13.2; safe to accept since
                                                file paths always start with
                                                "/" so a non-"/"-prefixed
                                                "X/Y" can only be RBD)
    """
    if source.startswith("rbd:"):
        path = source[4:].split(":")[0]
        return path if "/" in path else None
    if "/dev/rbd/" in source:
        idx = source.index("/dev/rbd/") + 9
        rest = source[idx:]
        parts = rest.split("/", 1)
        return "/".join(parts) if len(parts) == 2 else None
    # Defensive: bare "pool/image" with no leading slash and exactly the
    # shape pool/image is almost certainly RBD (filesystem paths start
    # with "/", block devs start with "/dev/", iSCSI/NBD URLs have schemes).
    if (not source.startswith("/")
            and "/" in source
            and ":" not in source
            and "@" not in source):
        parts = source.split("/", 1)
        if len(parts) == 2 and parts[0] and parts[1]:
            return source
    return None


@track_operation("edit")
def vm_edit(vm_id: int, fields: dict, user_id: int,
            is_admin: bool = False) -> dict:
    """
    Edit VM configuration via virsh on the host node.

    User fields  : vcpus, ram_mb, disk_gb
    Owner fields : tenant_id — non-admin owners may set PERSONAL or any
                   tenant they are a member of. SHARED is **not** a valid
                   target for non-admins (v0.11.3): SHARED is reserved for
                   the KVM-discovered unclaimed pool, so "releasing" a VM
                   back into SHARED is an admin-only operation. The same
                   allow-list applies to "claimers" (see next paragraph).
                   v0.11.1: a non-owner may also change tenant_id if the
                   VM is currently SHARED with `owner_user_id IS NULL`
                   ("unclaimed") — used to adopt KVM-sync'd VMs out of the
                   shared pool. Owner is auto-stamped to the caller in
                   that case.
    Admin fields : name (stopped only), description, vlan, boot_order,
                   tenant_id (any value, including SHARED), owner_user_id

    `tenant_id` / `owner_user_id` are DB-only fields (not reflected in
    libvirt XML). The built-in tenants are SHARED (id=1, visible to
    everyone) and PERSONAL (id=3, DB name "ODaaSE" since the v0.21 merge
    of the old id=2 "Personal VM" tenant into id=3 — visible only to
    owner_user_id + admins). `tenant_id = NULL` is the "untagged" state
    — admin-only.

    Returns {"ok": True, "vm": name, "changes": [...]}
         or {"error": "..."}.
    """
    if not fields:
        return {"ok": True, "vm": "", "changes": [], "note": "No fields provided"}

    vm = _get_vm_with_node(vm_id)
    if not vm:
        return {"error": "VM not found"}
    if not vm.get("private_key"):
        return {"error": "No SSH key configured for this node"}

    is_owner = (vm.get("owner_user_id") == user_id)

    # ---- Field-level authorisation ----------------------------------------
    # `tenant_id` is special: non-admins can change it only if they own the VM
    # AND only to one of the tenants they may create in (SHARED, PERSONAL, or
    # an AD-mapped tenant they're a member of). Other admin-only fields are
    # flatly blocked for non-admins.
    pure_admin_only = {"name", "description", "vlan", "boot_order", "owner_user_id"}
    for f in pure_admin_only:
        if f in fields and not is_admin:
            return {"error": f"Field '{f}' requires admin privileges"}

    if "tenant_id" in fields and not is_admin:
        # v0.11.1: a SHARED VM with no owner is "unclaimed" — any user may
        # claim it by moving it to PERSONAL or one of their member tenants
        # (the existing tenant-target gate below still applies). Owner gets
        # auto-stamped to the caller a few lines down so the v0.10 invariant
        # "PERSONAL ⇒ owner_user_id IS NOT NULL" is satisfied. A SHARED VM
        # that already has an owner can only be moved by that owner (or an
        # admin); this prevents "claim wars".
        is_unclaimed = (
            vm.get("tenant_id") == SHARED_TENANT_ID
            and vm.get("owner_user_id") is None
        )
        if not is_owner and not is_unclaimed:
            return {"error": "Only the VM owner or an admin can change its tenant"}
        raw = fields["tenant_id"]
        try:
            requested = int(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            return {"error": "tenant_id must be an integer"}
        if requested is None:
            return {"error": "Only admins may set a VM to untagged"}
        # v0.11.3: SHARED is reserved for the KVM-discovered unclaimed pool.
        # Once a VM is in any tenant (PERSONAL or a member tenant), only an
        # admin can release it back to SHARED. This is checked before the
        # generic allow-list so the error message is specific. It also
        # blocks the (otherwise no-op) case where a claimer picks SHARED
        # on a SHARED+unowned VM — the frontend disables that option but
        # the backend still enforces.
        if requested == SHARED_TENANT_ID:
            return {"error": "Only admins can release a VM back to SHARED"}
        allowed = {PERSONAL_TENANT_ID, *get_user_tenant_ids(user_id)}
        if requested not in allowed:
            return {"error": (
                "You may only set tenant to ODaaSE or a "
                "tenant you are a member of"
            )}
        # Auto-stamp ownership when claiming an unowned SHARED VM (i.e.
        # moving it out of SHARED). Since the SHARED target is now blocked
        # outright for non-admins, `requested != SHARED_TENANT_ID` is always
        # true here, but we keep the explicit check for self-documentation
        # in case the SHARED gate above is ever relaxed.
        if is_unclaimed and requested != SHARED_TENANT_ID:
            fields["owner_user_id"] = user_id

    # Fetch current resource values for range checks
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT vcpus, ram_mb, disk_gb FROM vms WHERE id = %s", (vm_id,))
            cur_vals = cur.fetchone() or {}
    finally:
        conn.close()

    vm_name   = vm["name"]
    is_running = vm["state"] == "running"
    changes   = []

    # Name change requires the VM to be stopped
    if "name" in fields and is_running:
        return {"error": "VM must be stopped to rename it"}

    # -----------------------------------------------------------------------
    # vCPUs and/or RAM — single XML pass to avoid topology/NUMA conflicts.
    #
    # virsh setvcpus --maximum fails when the VM XML has an explicit
    # <cpu><topology> whose product doesn't match the new count (e.g. prime
    # numbers like 17 break sockets×cores×threads).  Editing the XML directly
    # lets us update <vcpu> and <topology> atomically.
    #
    # virsh setmaxmem/setmem --config is likewise fragile when <numa> cells
    # have explicit memory sizes that no longer sum to the new total.  Using
    # the XML is safer and avoids the ordering dance (max-first vs mem-first).
    # -----------------------------------------------------------------------
    if "vcpus" in fields or "ram_mb" in fields:
        vcpus  = int(fields["vcpus"])  if "vcpus"  in fields else None
        ram_mb = int(fields["ram_mb"]) if "ram_mb" in fields else None

        if vcpus is not None and vcpus < 1:
            return {"error": "vCPUs must be at least 1"}
        if ram_mb is not None and ram_mb < 128:
            return {"error": "RAM must be at least 128 MB"}

        # Fetch current XML
        out, err, rc = _ssh_run(vm, f"virsh dumpxml '{vm_name}'")
        if rc != 0 or not out:
            return {"error": f"Failed to get VM XML: {err or 'empty output'}"}
        try:
            root = ET.fromstring(out)
        except ET.ParseError as exc:
            return {"error": f"Failed to parse VM XML: {exc}"}

        # -- vCPUs --
        if vcpus is not None:
            vcpu_el = root.find("vcpu")
            if vcpu_el is None:
                vcpu_el = ET.SubElement(root, "vcpu")
            # Remove stale attributes (current=, cpuset=) then set clean ones
            for k in list(vcpu_el.attrib):
                del vcpu_el.attrib[k]
            vcpu_el.set("placement", "static")
            vcpu_el.text = str(vcpus)

            # Update <cpu><topology> if present — keep sockets×cores×threads == vcpus
            cpu_el = root.find("cpu")
            if cpu_el is not None:
                topo_el = cpu_el.find("topology")
                if topo_el is not None:
                    topo_el.set("sockets", "1")
                    topo_el.set("cores",   str(vcpus))
                    topo_el.set("threads", "1")

        # -- RAM --
        if ram_mb is not None:
            ram_kb = ram_mb * 1024

            mem_el = root.find("memory")
            if mem_el is None:
                mem_el = ET.SubElement(root, "memory")
            mem_el.attrib.clear()
            mem_el.set("unit", "KiB")
            mem_el.text = str(ram_kb)

            cur_el = root.find("currentMemory")
            if cur_el is None:
                cur_el = ET.SubElement(root, "currentMemory")
            cur_el.attrib.clear()
            cur_el.set("unit", "KiB")
            cur_el.text = str(ram_kb)

            # If there's a single NUMA cell, update its memory allocation too
            cpu_el = root.find("cpu")
            if cpu_el is not None:
                numa_el = cpu_el.find("numa")
                if numa_el is not None:
                    cells = numa_el.findall("cell")
                    if len(cells) == 1:
                        cells[0].set("memory", str(ram_kb))
                        cells[0].set("unit", "KiB")
                    # Multi-cell NUMA left untouched — too complex to auto-distribute

        # Write updated XML and redefine
        new_xml = ET.tostring(root, encoding="unicode")
        tmp_xml = f"/tmp/wvc_res_{uuid.uuid4().hex[:8]}.xml"
        wr = _write_remote_file(vm, tmp_xml, new_xml)
        if "error" in wr:
            return wr
        try:
            out, err, rc = _ssh_run(vm, f"virsh define '{tmp_xml}'")
            if rc != 0:
                return {"error": f"Failed to apply resource changes: {(err or out).strip()}"}
        finally:
            _ssh_run(vm, f"rm -f '{tmp_xml}'")

        # Live hotplug attempts for running VMs — non-fatal if unsupported
        if is_running:
            if vcpus is not None:
                out, err, rc = _ssh_run(vm, f"virsh setvcpus '{vm_name}' {vcpus} --live")
                if rc != 0:
                    logger.info(
                        "VM %s: live vCPU hotplug not supported (%s) — takes effect on next boot",
                        vm_name, (err or out).strip(),
                    )
            if ram_mb is not None:
                ram_kb = ram_mb * 1024
                _ssh_run(vm, f"virsh setmaxmem '{vm_name}' {ram_kb} --live")
                out, err, rc = _ssh_run(vm, f"virsh setmem '{vm_name}' {ram_kb} --live")
                if rc != 0:
                    logger.info(
                        "VM %s: live RAM change not supported (%s) — takes effect on next boot",
                        vm_name, (err or out).strip(),
                    )

        if vcpus  is not None: changes.append(f"vcpus={vcpus}")
        if ram_mb is not None: changes.append(f"ram_mb={ram_mb}")


    # -----------------------------------------------------------------------
    # Disk
    # -----------------------------------------------------------------------
    if "disk_gb" in fields:
        disk_gb     = int(fields["disk_gb"])
        cur_disk_gb = cur_vals.get("disk_gb") or 0

        if disk_gb <= cur_disk_gb:
            return {"error": f"Disk can only be increased (current: {cur_disk_gb} GB)"}

        disks = _get_disk_info(vm)
        if not disks:
            return {"error": "Could not enumerate VM disks"}

        primary = disks[0]
        target  = primary["target"]   # e.g. "vda"
        source  = primary.get("source", "")

        if is_running:
            # virsh blockresize works for both Ceph and local-disk on running VMs
            out, err, rc = _ssh_run(vm, f"virsh blockresize '{vm_name}' {target} {disk_gb}G")
            if rc != 0:
                return {"error": f"Failed to resize disk: {(err or out).strip()}"}
        else:
            if _is_shared_disk(primary):
                pool_image = _parse_rbd_image(source)
                if not pool_image:
                    return {"error": f"Could not parse RBD pool/image from: {source!r}"}
                out, err, rc = _ssh_run(vm, f"rbd resize --size {disk_gb}G {pool_image}")
                if rc != 0:
                    return {"error": f"Failed to resize RBD image: {(err or out).strip()}"}
            else:
                if not source:
                    return {"error": "Could not determine local disk path"}
                out, err, rc = _ssh_run(vm, f"qemu-img resize '{source}' {disk_gb}G")
                if rc != 0:
                    return {"error": f"Failed to resize disk image: {(err or out).strip()}"}

        changes.append(f"disk_gb={disk_gb}")

    # -----------------------------------------------------------------------
    # Admin XML fields: description, vlan, boot_order, name
    # -----------------------------------------------------------------------
    xml_field_keys = {"name", "description", "vlan", "boot_order"}
    xml_changes = {f: fields[f] for f in xml_field_keys if f in fields}

    if xml_changes:
        out, err, rc = _ssh_run(vm, f"virsh dumpxml '{vm_name}'")
        if rc != 0 or not out:
            return {"error": f"Failed to get VM XML: {err or 'empty output'}"}

        try:
            root = ET.fromstring(out)
        except ET.ParseError as exc:
            return {"error": f"Failed to parse VM XML: {exc}"}

        # -- Description --
        if "description" in xml_changes:
            desc_el = root.find("description")
            if desc_el is None:
                desc_el = ET.SubElement(root, "description")
            desc_el.text = xml_changes["description"]
            changes.append("description")

        # -- VLAN (first bridged network interface) --
        if "vlan" in xml_changes:
            new_bridge = xml_changes["vlan"]
            devices = root.find("devices")
            if devices is None:
                return {"error": "No <devices> element in VM XML"}
            iface = devices.find("interface[@type='bridge']")
            if iface is None:
                return {"error": "No bridged network interface found in VM XML"}
            source_el = iface.find("source")
            if source_el is None:
                source_el = ET.SubElement(iface, "source")
            source_el.set("bridge", new_bridge)
            changes.append(f"vlan={new_bridge}")

        # -- Boot order --
        if "boot_order" in xml_changes:
            boot_devs = xml_changes["boot_order"]
            os_el = root.find("os")
            if os_el is None:
                return {"error": "No <os> element in VM XML"}
            for b in os_el.findall("boot"):
                os_el.remove(b)
            for dev in boot_devs:
                b = ET.SubElement(os_el, "boot")
                b.set("dev", dev)
            changes.append(f"boot_order={','.join(boot_devs)}")

        # -- Name (must be last — requires undefine/define cycle) --
        new_name = None
        if "name" in xml_changes:
            new_name = xml_changes["name"].strip()
            if not new_name:
                return {"error": "VM name cannot be empty"}
            name_el = root.find("name")
            if name_el is None:
                return {"error": "No <name> element in VM XML"}
            name_el.text = new_name
            changes.append(f"name={new_name}")

        new_xml = ET.tostring(root, encoding="unicode")
        tmp_xml = f"/tmp/wvc_edit_{uuid.uuid4().hex[:8]}.xml"
        wr = _write_remote_file(vm, tmp_xml, new_xml)
        if "error" in wr:
            return wr

        try:
            if new_name:
                # Must undefine the old domain before defining under a new name
                out, err, rc = _ssh_run(vm, f"virsh undefine '{vm_name}'")
                if rc != 0:
                    return {"error": f"Failed to undefine VM before rename: {(err or out).strip()}"}

            out, err, rc = _ssh_run(vm, f"virsh define '{tmp_xml}'")
            if rc != 0:
                if new_name:
                    logger.error(
                        "VM %s: define after rename failed — original domain may need manual recovery: %s",
                        vm_name, (err or out).strip(),
                    )
                return {"error": f"Failed to redefine VM: {(err or out).strip()}"}

            # Update DB name so vm_sync identifies the domain correctly on the next poll
            if new_name:
                conn = _get_db()
                try:
                    with conn.cursor() as cur:
                        cur.execute("UPDATE vms SET name = %s WHERE id = %s", (new_name, vm_id))
                    conn.commit()
                finally:
                    conn.close()
        finally:
            _ssh_run(vm, f"rm -f '{tmp_xml}'")

    # -----------------------------------------------------------------------
    # Tenant assignment + owner reassignment (DB-only)
    #
    # Authorisation was gated at the top:
    #   * owner_user_id changes        — admin only
    #   * tenant_id (any value)        — admin
    #   * tenant_id SHARED ↔ PERSONAL  — owner OR admin
    #
    # Invariant enforced here:
    #   tenant_id = PERSONAL  ⇒  owner_user_id IS NOT NULL
    #
    # A PERSONAL VM with no owner would be visible to nobody but admins
    # (every non-admin's owner_id check would fail), which is almost
    # never what the caller intended.
    # -----------------------------------------------------------------------
    new_tid: int | None = vm.get("tenant_id")
    new_owner: int | None = vm.get("owner_user_id")
    touch_tid   = "tenant_id" in fields
    touch_owner = "owner_user_id" in fields

    if touch_tid:
        raw = fields["tenant_id"]
        if raw in (None, "", 0):
            new_tid = None
        else:
            try:
                new_tid = int(raw)
            except (TypeError, ValueError):
                return {"error": "tenant_id must be an integer or null"}

    if touch_owner:
        raw = fields["owner_user_id"]
        if raw in (None, "", 0):
            new_owner = None
        else:
            try:
                new_owner = int(raw)
            except (TypeError, ValueError):
                return {"error": "owner_user_id must be an integer or null"}

    if touch_tid or touch_owner:
        conn = _get_db()
        try:
            with conn.cursor() as cur:
                if new_tid is not None:
                    cur.execute("SELECT id FROM tenants WHERE id = %s", (new_tid,))
                    if not cur.fetchone():
                        return {"error": f"Tenant id={new_tid} not found"}

                if new_owner is not None:
                    cur.execute(
                        "SELECT id FROM users WHERE id = %s AND is_active = 1",
                        (new_owner,),
                    )
                    if not cur.fetchone():
                        return {"error": f"User id={new_owner} not found or inactive"}

                if new_tid == PERSONAL_TENANT_ID and new_owner is None:
                    return {"error": (
                        "Tenant 'ODaaSE' requires an owner — "
                        "set owner_user_id or pick a different tenant"
                    )}

                sets, args = [], []
                if touch_tid:
                    sets.append("tenant_id = %s")
                    args.append(new_tid)
                if touch_owner:
                    sets.append("owner_user_id = %s")
                    args.append(new_owner)
                args.append(vm_id)
                cur.execute(
                    f"UPDATE vms SET {', '.join(sets)} WHERE id = %s",
                    args,
                )
            conn.commit()
        finally:
            conn.close()

        if touch_tid:
            changes.append(f"tenant_id={'null' if new_tid is None else new_tid}")
        if touch_owner:
            changes.append(f"owner_user_id={'null' if new_owner is None else new_owner}")

    if not changes:
        return {"ok": True, "vm": vm_name, "changes": [], "note": "No fields changed"}

    logger.info("VM %s (%d) edited: %s", vm_name, vm_id, ", ".join(changes))
    return {"ok": True, "vm": vm_name, "changes": changes}


@track_vm_migrate
def vm_migrate(vm_id: int, target_node_id: int, allow_shutdown: bool = False) -> dict:
    """
    Migrate a VM to a different node.

    Stopped VMs  → offline migration (any storage).
    Running VMs  → live migration if CPUs match; otherwise requires allow_shutdown=True
                   which force-stops the VM then does offline migration.

    Returns {"ok": True, "vm": name, "from_node": ..., "to_node": ..., "live": bool}
         or {"error": "...", "cpu_mismatch": True, ...} on CPU incompatibility.
    """
    vm = _get_vm_with_node(vm_id)
    if not vm:
        return {"error": "VM not found"}
    if not vm.get("private_key"):
        return {"error": "No SSH key configured for source node"}

    target = _get_node(target_node_id)
    if not target:
        return {"error": "Target node not found"}
    if (target.get("status") or "").lower() != "online":
        return {"error": f"Target node '{target['hostname']}' is not online"}
    if vm["node_id"] == target_node_id:
        return {"error": "VM is already on the target node"}

    # --- RECOVERY path: source node is dead/fenced ------------------------
    # If the VM's current (source) node has failed, we cannot do a normal
    # migration (that dumps XML / copies disk over SSH FROM the source, which is
    # unreachable). Instead we REBUILD the domain on the target from the
    # last-known-good libvirt_xml captured by vm_sync — the disk is on shared
    # Ceph.
    #
    # INTERLOCK: this is only SAFE once the source node has been FENCED (Ceph-
    # blocklisted) so it can no longer write the disk. If the node is merely
    # 'failed' (detected dead but not yet fenced) or 'offline', we REFUSE and
    # tell the admin to fence it first on the HA page — otherwise starting the
    # VM on the target while the source might still be writing risks split-brain
    # corruption.
    src_status = (vm.get("node_status") or "").lower()
    vm_state = vm["state"]
    if src_status == "fenced":
        return _recover_migrate(vm, target)
    if src_status in ("failed", "offline") or vm_state == "unknown":
        return {
            "error": (
                f"Cannot recover '{vm['name']}' — its host "
                f"'{vm.get('hostname')}' has failed but is NOT fenced yet. "
                "Fence the node first (HA page → Fence) so it can no longer "
                "write shared storage, then recover the VM. This prevents "
                "split-brain corruption."
            ),
            "needs_fence": True,
            "source_node_id": vm.get("node_id"),
            "source_hostname": vm.get("hostname"),
        }

    if vm_state not in ("running", "stopped"):
        return {"error": f"Cannot migrate VM in '{vm_state}' state — must be running or stopped"}

    live = (vm_state == "running")
    forced_shutdown = False  # True if we stopped the VM; we'll restart it on the target

    # --- CPU compatibility pre-flight for live migration ---
    if live:
        src_cpu = (vm.get("node_cpu_model") or "").strip()
        dst_cpu = (target.get("cpu_model") or "").strip()
        if src_cpu and dst_cpu and src_cpu != dst_cpu:
            if not allow_shutdown:
                return {
                    "error": (
                        f"Live migration not possible — CPU models differ between nodes "
                        f"({src_cpu!r} → {dst_cpu!r}). "
                        "Use 'Shutdown & Migrate' to stop the VM and migrate offline."
                    ),
                    "cpu_mismatch": True,
                    "src_cpu": src_cpu,
                    "dst_cpu": dst_cpu,
                }
            # Force-stop then fall through to offline migration
            logger.info("CPU mismatch — force-stopping '%s' for offline migration", vm["name"])
            _update_vm_state(vm_id, "stopping")
            out, err, rc = _ssh_run(vm, f"virsh destroy '{vm['name']}'")
            if rc != 0:
                _update_vm_state(vm_id, vm_state)
                return {"error": f"Failed to stop VM: {err or out}"}
            time.sleep(2)
            live = False
            forced_shutdown = True

    _update_vm_state(vm_id, "migrating")

    try:
        result = _live_migrate(vm, target) if live else _offline_migrate(vm, target)
    except Exception as exc:
        logger.error("VM %s migration exception: %s", vm["name"], exc)
        result = {"error": f"Migration failed: {exc}"}

    if "error" in result:
        _update_vm_state(vm_id, vm_state)
        return result

    _update_vm_node(vm_id, target_node_id, "running" if live else "stopped")
    logger.info("VM %s migrated %s → %s (%s)",
                vm["name"], vm["hostname"], target["hostname"], "live" if live else "offline")

    # If we shut the VM down ourselves to perform offline migration, start it on the target
    if forced_shutdown:
        logger.info("Restarting '%s' on target node %s after offline migration",
                    vm["name"], target["hostname"])
        out, err, rc = _ssh_run(target, f"virsh start '{vm['name']}'")
        if rc != 0:
            logger.warning("Failed to start '%s' on %s after migration: %s",
                           vm["name"], target["hostname"], err or out)
            # Migration succeeded — don't fail, but report the start failure
            return {
                "ok":        True,
                "vm":        vm["name"],
                "from_node": vm["hostname"],
                "to_node":   target["hostname"],
                "live":      False,
                "started":   False,
                "start_error": (err or out).strip(),
            }
        _update_vm_state(vm_id, "running")
        logger.info("VM '%s' running on %s", vm["name"], target["hostname"])

    return {
        "ok":        True,
        "vm":        vm["name"],
        "from_node": vm["hostname"],
        "to_node":   target["hostname"],
        "live":      live,
        "started":   forced_shutdown,
    }


# ---------------------------------------------------------------------------
# Disk management helpers
# ---------------------------------------------------------------------------

def _get_rbd_auth_node(node: dict, resources: dict) -> tuple[str, str | None]:
    """
    Build rbd command-line auth args using the node's disk XML template.
    Like vm_create._get_rbd_auth but uses _ssh_run (new connection per call)
    instead of an open paramiko client.

    Returns (auth_args_str, tmp_keyring_path | None).
    Caller must remove the keyring file when done.
    """
    template = resources.get("disk_xml_template", "")
    if not template:
        return "", None

    try:
        root = ET.fromstring(template)
    except ET.ParseError:
        return "", None

    auth_el = root.find("auth")
    if auth_el is None:
        return "", None

    username    = auth_el.get("username", "admin")
    sec         = auth_el.find("secret")
    secret_uuid = sec.get("uuid", "") if sec is not None else ""

    src   = root.find("source")
    hosts = []
    if src is not None:
        for h in src.findall("host"):
            hosts.append(f"{h.get('name')}:{h.get('port', '6789')}")

    auth_args    = f"--id {username}"
    keyring_path: str | None = None

    if secret_uuid:
        key_value, _, rc = _ssh_run(
            node, f"virsh secret-get-value '{secret_uuid}' 2>/dev/null"
        )
        if rc == 0 and key_value.strip():
            keyring_path = f"/tmp/wvc_rbd_{secrets.token_hex(4)}.keyring"
            keyring_content = f"[client.{username}]\n\tkey = {key_value.strip()}\n"
            kb64 = base64.b64encode(keyring_content.encode()).decode()
            _ssh_run(
                node,
                f"printf '%s' '{kb64}' | base64 -d > {keyring_path}"
                f" && chmod 600 {keyring_path}",
            )
            auth_args += f" --keyring {keyring_path}"
        else:
            logger.warning("Could not read Ceph secret %s", secret_uuid)

    if hosts:
        auth_args += f" --mon-host '{','.join(hosts)}'"

    return auth_args, keyring_path


def _build_disk_xml_attach(pool: str, image: str, target: str,
                            template_xml: str) -> str:
    """
    Build a <disk> element for an additional RBD disk using the node's
    existing disk XML as a template (preserves auth/host entries).
    Sets target dev to `target` (e.g. "vdb").
    """
    if template_xml:
        try:
            root = ET.fromstring(template_xml)
            src = root.find("source")
            if src is not None:
                src.set("name", f"{pool}/{image}")
            for tag in ("serial", "address"):
                for el in root.findall(tag):
                    root.remove(el)
            tgt = root.find("target")
            if tgt is not None:
                tgt.set("dev", target)
                tgt.set("bus", "virtio")
            return ET.tostring(root, encoding="unicode")
        except ET.ParseError:
            pass
    # Fallback
    return (
        f"<disk type='network' device='disk'>"
        f"<driver name='qemu' type='raw'/>"
        f"<source protocol='rbd' name='{pool}/{image}'/>"
        f"<target dev='{target}' bus='virtio'/>"
        f"</disk>"
    )


def _next_virtio_target(existing_targets: list[str]) -> str:
    """Return the next unused vd* target letter (vdb, vdc …)."""
    used = set(existing_targets)
    for letter in "bcdefghijklmnopqrstuvwxyz":
        candidate = f"vd{letter}"
        if candidate not in used:
            return candidate
    raise ValueError("All virtio disk targets vdb–vdz are in use")


# ---------------------------------------------------------------------------
# Public disk-management API
# ---------------------------------------------------------------------------

def vm_list_disks(vm_id: int) -> dict:
    """
    Return disk and CD-ROM info for a VM.

    Returns:
      {
        "disks": [{"target", "source", "size_gb"}],
        "cdrom": {"dev": "sda", "iso": "/path" | ""} | None
      }
    """
    vm = _get_vm_with_node(vm_id)
    if not vm:
        return {"error": "VM not found"}
    if not vm.get("private_key"):
        return {"error": "No SSH key configured for this node"}

    vm_name = vm["name"]
    out, err, rc = _ssh_run(vm, f"virsh domblklist '{vm_name}' --details")
    if rc != 0:
        return {"error": f"virsh domblklist failed: {(err or out).strip()}"}

    disks  = []
    cdrom  = None

    for line in out.splitlines()[2:]:
        parts = line.split()
        if len(parts) < 3:
            continue
        dev_type = parts[1]   # disk | cdrom | floppy
        target   = parts[2]
        source   = parts[3] if len(parts) > 3 else ""

        if dev_type == "disk":
            # Try to get capacity
            size_gb = 0.0
            bi_out, _, bi_rc = _ssh_run(
                vm, f"virsh domblkinfo '{vm_name}' '{target}' 2>/dev/null"
            )
            if bi_rc == 0:
                for bi_line in bi_out.splitlines():
                    if bi_line.lower().startswith("capacity:"):
                        try:
                            cap_bytes = int(bi_line.split(":", 1)[1].strip())
                            size_gb   = round(cap_bytes / (1024 ** 3), 1)
                        except ValueError:
                            pass
                        break
            disks.append({"target": target, "source": source, "size_gb": size_gb})

        elif dev_type == "cdrom" and cdrom is None:
            cdrom = {"dev": target, "iso": source}

    return {"disks": disks, "cdrom": cdrom}


def vm_attach_disk(vm_id: int, pool: str, image_name: str, size_gb: int) -> dict:
    """
    Create a new RBD image and hot-/cold-attach it to a VM as the next
    virtio disk target (vdb, vdc …).

    Returns {"ok": True, "target": "vdb"} or {"error": "..."}.
    """
    vm = _get_vm_with_node(vm_id)
    if not vm:
        return {"error": "VM not found"}
    if not vm.get("private_key"):
        return {"error": "No SSH key configured for this node"}

    # Need node_id to fetch resources
    resources = _get_node_resources_ext(vm["node_id"])
    if "error" in resources:
        return resources

    # Determine next disk target
    disk_info = vm_list_disks(vm_id)
    if "error" in disk_info:
        return disk_info
    existing_targets = [d["target"] for d in disk_info["disks"]]
    try:
        target = _next_virtio_target(existing_targets)
    except ValueError as exc:
        return {"error": str(exc)}

    is_running   = vm["state"] == "running"
    keyring_path = None
    vm_name      = vm["name"]

    try:
        auth_args, keyring_path = _get_rbd_auth_node(vm, resources)

        # Check image doesn't already exist
        _, _, rc = _ssh_run(vm, f"rbd {auth_args} info {pool}/{image_name} 2>/dev/null")
        if rc == 0:
            return {"error": f"RBD image '{pool}/{image_name}' already exists"}

        # Create the RBD image
        out, err, rc = _ssh_run(
            vm, f"rbd {auth_args} create {pool}/{image_name} --size {size_gb}G 2>&1"
        )
        if rc != 0:
            return {"error": f"rbd create failed: {(err or out).strip()}"}

        # Build disk XML and attach
        disk_xml = _build_disk_xml_attach(
            pool, image_name, target, resources.get("disk_xml_template", "")
        )
        tmp_xml = f"/tmp/wvc_disk_{uuid.uuid4().hex[:8]}.xml"
        wr = _write_remote_file(vm, tmp_xml, disk_xml)
        if "error" in wr:
            _ssh_run(vm, f"rbd {auth_args} rm {pool}/{image_name} 2>/dev/null")
            return wr

        flags = "--config"
        if is_running:
            flags += " --live"
        out, err, rc = _ssh_run(vm, f"virsh attach-device '{vm_name}' '{tmp_xml}' {flags}")
        _ssh_run(vm, f"rm -f '{tmp_xml}'")

        if rc != 0:
            _ssh_run(vm, f"rbd {auth_args} rm {pool}/{image_name} 2>/dev/null")
            return {"error": f"virsh attach-device failed: {(err or out).strip()}"}

        logger.info("VM %s: attached new disk %s/%s as %s", vm_name, pool, image_name, target)
        return {"ok": True, "target": target}

    finally:
        if keyring_path:
            _ssh_run(vm, f"rm -f '{keyring_path}'")


def vm_detach_disk(vm_id: int, target: str) -> dict:
    """
    Detach a disk from a VM by target device name (e.g. "vdb").
    The RBD image is NOT deleted.

    Returns {"ok": True} or {"error": "..."}.
    """
    vm = _get_vm_with_node(vm_id)
    if not vm:
        return {"error": "VM not found"}
    if not vm.get("private_key"):
        return {"error": "No SSH key configured for this node"}

    vm_name    = vm["name"]
    is_running = vm["state"] == "running"

    flags = "--config"
    if is_running:
        flags += " --live"

    out, err, rc = _ssh_run(vm, f"virsh detach-disk '{vm_name}' '{target}' {flags}")
    if rc != 0:
        return {"error": f"virsh detach-disk failed: {(err or out).strip()}"}

    logger.info("VM %s: detached disk %s", vm_name, target)
    return {"ok": True}


def vm_set_cdrom(vm_id: int, iso_path: str) -> dict:
    """
    Mount or eject an ISO from a VM's CD-ROM device.

    iso_path — non-empty string to mount, empty string to eject.

    If no cdrom device exists and iso_path is non-empty, attaches one.
    Returns {"ok": True} or {"error": "..."}.
    """
    vm = _get_vm_with_node(vm_id)
    if not vm:
        return {"error": "VM not found"}
    if not vm.get("private_key"):
        return {"error": "No SSH key configured for this node"}

    vm_name    = vm["name"]
    is_running = vm["state"] == "running"
    flags      = "--config"
    if is_running:
        flags += " --live"

    disk_info = vm_list_disks(vm_id)
    if "error" in disk_info:
        return disk_info
    cdrom = disk_info.get("cdrom")

    if not iso_path:
        # Eject
        if not cdrom:
            return {"ok": True, "note": "No CD-ROM device attached"}
        dev = cdrom["dev"]
        out, err, rc = _ssh_run(
            vm, f"virsh change-media '{vm_name}' '{dev}' --eject {flags}"
        )
        if rc != 0:
            return {"error": f"virsh change-media eject failed: {(err or out).strip()}"}
        logger.info("VM %s: ejected ISO from %s", vm_name, dev)
        return {"ok": True}
    else:
        # Mount
        if cdrom:
            dev = cdrom["dev"]
            # Eject first if media is currently loaded
            if cdrom.get("iso"):
                _ssh_run(vm, f"virsh change-media '{vm_name}' '{dev}' --eject --config 2>/dev/null")
            out, err, rc = _ssh_run(
                vm,
                f"virsh change-media '{vm_name}' '{dev}' '{iso_path}' --insert {flags}",
            )
            if rc != 0:
                return {"error": f"virsh change-media insert failed: {(err or out).strip()}"}
        else:
            # No cdrom device — attach one
            cdrom_xml = (
                f"<disk type='file' device='cdrom'>"
                f"<driver name='qemu' type='raw'/>"
                f"<source file='{iso_path}'/>"
                f"<target dev='sda' bus='sata'/>"
                f"<readonly/>"
                f"</disk>"
            )
            tmp_xml = f"/tmp/wvc_cdrom_{uuid.uuid4().hex[:8]}.xml"
            wr = _write_remote_file(vm, tmp_xml, cdrom_xml)
            if "error" in wr:
                return wr
            out, err, rc = _ssh_run(
                vm, f"virsh attach-device '{vm_name}' '{tmp_xml}' {flags}"
            )
            _ssh_run(vm, f"rm -f '{tmp_xml}'")
            if rc != 0:
                return {"error": f"virsh attach-device (cdrom) failed: {(err or out).strip()}"}

        logger.info("VM %s: mounted ISO %s", vm_name, iso_path)
        return {"ok": True}


@track_operation("delete")
def vm_delete(vm_id: int, delete_disk: bool = True) -> dict:
    """
    Permanently delete a VM.

    Steps:
      1. Force-stop the VM if it is not already stopped.
      2. Undefine the libvirt domain (virsh undefine).
      3. Delete the primary disk image from Ceph (rbd rm) — only when
         delete_disk=True and disk_path starts with "rbd:".
      4. Remove the VM row from the database.

    Returns {"ok": True, "vm": name} or {"error": "..."}.
    """
    vm = _get_vm_with_node(vm_id)
    if not vm:
        return {"error": "VM not found"}
    if not vm.get("private_key"):
        return {"error": "No SSH key configured for this node"}

    vm_name = vm["name"]

    # Fetch disk_path before we touch anything
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT disk_path FROM vms WHERE id = %s", (vm_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    disk_path = (row or {}).get("disk_path") or ""

    # 1. Force-stop if running / paused / suspended
    if vm["state"] not in ("stopped", "crashed"):
        out, err, rc = _ssh_run(vm, f"virsh destroy '{vm_name}'")
        if rc != 0:
            # Tolerate "not running" — anything else is a real problem
            err_lower = (err or out or "").lower()
            if "not running" not in err_lower and "domain is not running" not in err_lower:
                logger.warning("VM %s: virsh destroy failed (continuing): %s", vm_name, err or out)

    # 2. Undefine the libvirt domain
    #
    # `--nvram` removes the per-VM UEFI VARS file alongside the domain
    # config. It's harmless on non-UEFI VMs (no NVRAM ⇒ no-op) and is
    # supported on every libvirt ≥ 1.2.9; if for some reason the flag
    # is rejected, fall back to the bare form so we don't block delete.
    out, err, rc = _ssh_run(vm, f"virsh undefine '{vm_name}' --nvram")
    if rc != 0 and "unsupported" in (err or out).lower():
        out, err, rc = _ssh_run(vm, f"virsh undefine '{vm_name}'")
    if rc != 0:
        err_lower = (err or out or "").lower()
        # "failed to get domain" means it's already gone — treat as success
        if "failed to get domain" not in err_lower and "not found" not in err_lower:
            return {"error": f"virsh undefine failed: {(err or out).strip()}"}

    # 2a. Tidy quick-create artefacts. Best-effort — leftover files don't
    # break anything but bloat the host filesystem. These paths mirror
    # the constants in vm_create.py (SEED_ISO_DIR / NVRAM_DIR); we
    # inline them here so vm_service stays independent of vm_create.
    _ssh_run(vm, f"rm -f /var/lib/libvirt/images/wvc-seed/{vm_name}.iso")
    _ssh_run(vm, f"rm -f /var/lib/libvirt/qemu/nvram/{vm_name}_VARS.fd")

    # 3. Delete primary disk image
    if delete_disk and disk_path:
        rbd_image = _parse_rbd_image(disk_path)
        if rbd_image:
            resources = _get_node_resources_ext(vm["node_id"])
            if "error" not in resources:
                keyring_path = None
                try:
                    auth_args, keyring_path = _get_rbd_auth_node(vm, resources)
                    out, err, rc = _ssh_run(
                        vm, f"rbd {auth_args} rm {rbd_image} 2>&1"
                    )
                    if rc != 0:
                        logger.warning(
                            "VM %s: rbd rm %s failed (proceeding with DB delete): %s",
                            vm_name, rbd_image, (err or out).strip(),
                        )
                finally:
                    if keyring_path:
                        _ssh_run(vm, f"rm -f '{keyring_path}'")
            else:
                logger.warning(
                    "VM %s: could not fetch node resources for disk deletion: %s",
                    vm_name, resources.get("error"),
                )

    # 4. Remove from database
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT os_family FROM vms WHERE id = %s", (vm_id,))
            _r = cur.fetchone()
            _os = (_r or {}).get("os_family") if _r else None
            cur.execute("DELETE FROM vms WHERE id = %s", (vm_id,))
        conn.commit()
    finally:
        conn.close()

    # Lifecycle ledger (best-effort) — falls the cumulative VM-count chart.
    try:
        from .vm_lifecycle import record as _lifecycle_record
        _lifecycle_record("delete", os_family=_os, vm_name=vm_name, vm_id=vm_id)
    except Exception:
        pass

    logger.info("VM %s (id=%d) deleted (delete_disk=%s)", vm_name, vm_id, delete_disk)
    return {"ok": True, "vm": vm_name}
