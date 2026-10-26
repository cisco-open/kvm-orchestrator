"""
VM creation service — creates new VMs on KVM nodes via SSH.

Supports four modes:
  blank    — creates a new RBD image + builds libvirt XML from scratch
  clone    — rbd-copies an existing VM's disk + modifies its XML.
             Always done on the source VM's node; if the chosen target
             differs, vm_service.vm_migrate moves the new VM afterwards.
  existing — adopts an already-present RBD image as a fresh domain.
  quick    — (v0.14.0) clones a curated "golden" RBD image (Ubuntu /
             Windows) via a protected snapshot — instant COW — then for
             Linux injects a cloud-init seed ISO carrying SSH keys + a
             DHCP netplan, and for Windows boots a pre-sysprepped image
             under UEFI/OVMF. Powered by `vm_image_service` (catalog)
             and `cloud_init_service` (seed renderers).
"""

import base64
import io
import json
import logging
import os
import re
import secrets
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import urlparse

import paramiko

from ..metrics import track_vm_create
import pymysql
import pymysql.cursors

from . import cloud_init_service, vm_image_service
from .tenant_service import SHARED_TENANT_ID

logger = logging.getLogger("wvc.vm-create")

# Ensure our namespace prefix is consistent when serialising XML
ET.register_namespace("wvc", "https://kvm-orchestrator")

# ---------------------------------------------------------------------------
# Python script executed on KVM node via SSH stdin to discover resources.
# Outputs a single JSON object.
# ---------------------------------------------------------------------------
_RESOURCES_SCRIPT = r'''
import subprocess, json, re, os
import xml.etree.ElementTree as ET

def sh(cmd):
    return subprocess.run(
        cmd, shell=True, text=True, capture_output=True, timeout=30
    ).stdout.strip()

result = {
    "pools": [],
    "networks": [],
    "isos": [],
    "emulator": "/usr/bin/qemu-system-x86_64",
    "disk_xml_template": "",
}

# Emulator path
for p in ["/usr/bin/qemu-system-x86_64", "/usr/libexec/qemu-kvm", "/usr/bin/kvm"]:
    if os.path.exists(p):
        result["emulator"] = p
        break

# Storage pools
for pool_name in sh("virsh pool-list --name").splitlines():
    pool_name = pool_name.strip()
    if not pool_name:
        continue
    pool_xml_str = sh(f"virsh pool-dumpxml '{pool_name}'")
    try:
        pool_root = ET.fromstring(pool_xml_str)
    except ET.ParseError:
        continue
    pool_type = pool_root.get("type", "")
    if pool_type == "rbd":
        auth_el = pool_root.find(".//auth")
        auth_info = None
        if auth_el is not None:
            sec = auth_el.find("secret")
            auth_info = {
                "username": auth_el.get("username", "admin"),
                "secret_uuid": sec.get("uuid", "") if sec is not None else "",
            }
        hosts = []
        src = pool_root.find(".//source")
        if src is not None:
            for h in src.findall("host"):
                hosts.append({"name": h.get("name", "localhost"), "port": h.get("port", "6789")})
        # The Ceph pool name (source/name) may differ from the libvirt pool
        # name (e.g. libvirt pool "rbdpool" backed by Ceph pool "libvirt-pool").
        ceph_pool = ""
        if src is not None:
            nm = src.find("name")
            if nm is not None and nm.text:
                ceph_pool = nm.text.strip()
        result["pools"].append({
            "name": pool_name, "type": "rbd",
            "auth": auth_info, "hosts": hosts, "ceph_pool": ceph_pool,
        })
        # Synthesize a disk-shaped auth template directly from the pool
        # definition. This is what lets us create the FIRST RBD VM on a
        # freshly-added node that has no existing VM to sample from (the
        # loop below only fires when a VM already exists). We keep the
        # VM-sampled template as the primary source (it captures any
        # node-specific disk quirks) and fall back to this synthetic one.
        if not result.get("_rbd_pool_template") and auth_info and ceph_pool:
            host_xml = "".join(
                "<host name='{}' port='{}'/>".format(h["name"], h["port"])
                for h in hosts
            )
            secret_xml = ""
            if auth_info["secret_uuid"]:
                secret_xml = (
                    "<secret type='ceph' uuid='{}'/>".format(auth_info["secret_uuid"])
                )
            result["_rbd_pool_template"] = (
                "<disk type='network' device='disk'>"
                "<driver name='qemu' type='raw' cache='directsync'/>"
                "<auth username='{user}'>{secret}</auth>"
                "<source protocol='rbd' name='{pool}/__IMAGE__'>{hosts}</source>"
                "<target dev='vda' bus='virtio'/>"
                "</disk>".format(
                    user=auth_info["username"], secret=secret_xml,
                    pool=ceph_pool, hosts=host_xml,
                )
            )
    elif pool_type in ("dir", "logical", "fs", "netfs"):
        tgt = pool_root.find(".//target/path")
        target_path = tgt.text if tgt is not None else ""
        result["pools"].append({
            "name": pool_name, "type": pool_type, "path": target_path, "auth": None, "hosts": [],
        })
        for line in sh(f"virsh vol-list '{pool_name}'").splitlines()[2:]:
            parts = line.split()
            if len(parts) >= 2 and (parts[0].endswith(".iso") or parts[1].endswith(".iso")):
                result["isos"].append({"name": parts[0], "path": parts[1], "pool": pool_name})

# Networks / bridges
seen = set()
for br in sh("ovs-vsctl list-br 2>/dev/null").splitlines():
    br = br.strip()
    if br and br not in seen:
        result["networks"].append({"name": br, "type": "ovs"})
        seen.add(br)
for line in sh("ip -o link show type bridge 2>/dev/null").splitlines():
    parts = line.split()
    if len(parts) >= 2:
        br = parts[1].rstrip(":")
        if br not in seen:
            result["networks"].append({"name": br, "type": "linux"})
            seen.add(br)
for net in sh("virsh net-list --name").splitlines():
    net = net.strip()
    if net and net not in seen:
        result["networks"].append({"name": net, "type": "network"})
        seen.add(net)

# Sample disk XML from the first RBD-backed VM (for auth template).
# This is preferred because it reflects a real, working disk on THIS
# node. On a freshly-added node with no VMs yet, there is nothing to
# sample — fall back to the template synthesized from the rbd pool
# definition above so the first VM can still be created.
for vm_name in sh("virsh list --all --name").splitlines():
    vm_name = vm_name.strip()
    if not vm_name:
        continue
    try:
        vm_root = ET.fromstring(sh(f"virsh dumpxml '{vm_name}'"))
    except ET.ParseError:
        continue
    for disk in vm_root.findall(".//disk"):
        if disk.get("type") == "network" and disk.get("device") == "disk":
            result["disk_xml_template"] = ET.tostring(disk, encoding="unicode")
            break
    if result["disk_xml_template"]:
        break

# Fallback: no existing RBD VM to sample — use the pool-derived template.
if not result["disk_xml_template"] and result.get("_rbd_pool_template"):
    result["disk_xml_template"] = result["_rbd_pool_template"]

result.pop("_rbd_pool_template", None)

print(json.dumps(result))
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


def _get_node(node_id: int) -> dict | None:
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT n.id, n.hostname, n.ip_address, n.status,
                       n.ssh_host, n.ssh_port, n.ssh_user, k.private_key
                FROM nodes n
                LEFT JOIN ssh_keys k ON k.id = n.ssh_key_id
                WHERE n.id = %s
                """,
                (node_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def _get_vm_by_id(vm_id: int) -> dict | None:
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, node_id, name, state, vcpus, ram_mb, vlan, disk_gb, disk_path, datastore "
                "FROM vms WHERE id = %s",
                (vm_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def _name_exists_on_node(node_id: int, name: str) -> bool:
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM vms WHERE node_id = %s AND name = %s",
                (node_id, name),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def _insert_vm(node_id, name, vm_uuid, state, vcpus, ram_mb,
               vlan, disk_gb, disk_path, datastore,
               tenant_id=SHARED_TENANT_ID, owner_user_id=None,
               os_family=None) -> int:
    """Insert a freshly-created VM row.

    Defaults to `tenant_id = SHARED` so a UI-created VM is visible to
    everyone unless the caller (api.vms.create_vm) overrides it. The
    owner is stamped from the JWT identity by the API layer. `os_family`
    ('linux'|'windows'|'custom') drives OS-breakdown metrics.
    """
    conn = _get_db()
    now = datetime.utcnow()
    # Stamp the initial 1-year term (TTL from config; a test environment can
    # shrink it). Best-effort — if the helper import fails we still create
    # the VM (the daily checker's backfill would set it later anyway).
    try:
        from .vm_lifecycle import new_expiry as _new_expiry
        expires_at = _new_expiry(now)
    except Exception:
        expires_at = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vms
                  (node_id, name, uuid, state, vcpus, ram_mb,
                   vlan, disk_gb, disk_path, datastore,
                   tenant_id, owner_user_id, os_family, last_seen_at,
                   expires_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (node_id, name, vm_uuid, state, vcpus, ram_mb,
                 vlan, disk_gb, disk_path, datastore,
                 tenant_id, owner_user_id, os_family, now,
                 expires_at),
            )
            vm_id = cur.lastrowid
        conn.commit()
        # Lifecycle ledger (best-effort) — powers the cumulative VM-count chart.
        try:
            from .vm_lifecycle import record as _lifecycle_record
            _lifecycle_record("create", os_family=os_family, vm_name=name, vm_id=vm_id)
        except Exception:
            pass
        return vm_id
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


def _ssh_connect(node: dict) -> paramiko.SSHClient:
    pkey = _load_key(node["private_key"])
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    host = node.get("ssh_host") or node["ip_address"]
    port = int(node.get("ssh_port") or 22)
    user = node.get("ssh_user") or "root"
    client.connect(
        hostname=host, port=port, username=user, pkey=pkey,
        timeout=15, look_for_keys=False, allow_agent=False,
    )
    return client


def _run(client: paramiko.SSHClient, cmd: str,
         timeout: int = 60) -> tuple[str, str, int]:
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    stdin.close()
    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()
    rc = stdout.channel.recv_exit_status()
    return out, err, rc


def _write_file(client: paramiko.SSHClient, path: str, content: str) -> None:
    """Write a text file on the remote host via SFTP."""
    sftp = client.open_sftp()
    try:
        with sftp.open(path, "w") as fh:
            fh.write(content)
    finally:
        sftp.close()


# ---------------------------------------------------------------------------
# Resources discovery
# ---------------------------------------------------------------------------

def get_node_resources(node_id: int) -> dict:
    """SSH to node, run discovery script, return parsed resource dict."""
    node = _get_node(node_id)
    if not node:
        return {"error": "Node not found"}
    if not node.get("private_key"):
        return {"error": "No SSH key configured for this node"}

    client = _ssh_connect(node)
    try:
        stdin, stdout, stderr = client.exec_command("python3", timeout=60)
        stdin.write(_RESOURCES_SCRIPT)
        stdin.channel.shutdown_write()
        raw = stdout.read().decode(errors="replace").strip()
        err = stderr.read().decode(errors="replace").strip()
        if err:
            logger.debug("Node %s resources stderr: %s", node["hostname"], err[:300])
        return json.loads(raw) if raw else {"error": "Empty response from node"}
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse error from node: {e}"}
    except Exception as e:
        return {"error": f"SSH error: {e}"}
    finally:
        client.close()


def node_has_defined_rbd_pool(node_id: int) -> bool | None:
    """Return True if the node has a libvirt storage pool of type ``rbd``
    that is DEFINED — whether it is currently active or not.

    This is the correct RBD-capability signal for placement. kvm-orchestrator
    provisions VM disks with raw ``rbd`` CLI against the Ceph pool (it does
    NOT use the libvirt-managed pool for disk I/O), so an rbd pool that is
    merely *inactive* (e.g. libvirt failed to autostart it after a reboot
    because Ceph wasn't ready yet) does NOT mean the node can't run RBD
    VMs. The previous check keyed off `virsh pool-list` (active only) via
    get_node_resources and wrongly excluded such nodes, causing spurious
    "No eligible host" placement failures.

    Uses ``virsh pool-list --all --type rbd`` so inactive pools count.
    Returns:
      * True  — a defined rbd pool exists (node is RBD-capable)
      * False — no rbd pool defined at all
      * None  — could not determine (node offline / SSH failed / no key);
                the caller decides how to treat "unknown".
    """
    node = _get_node(node_id)
    if not node or not node.get("private_key"):
        return None
    try:
        client = _ssh_connect(node)
    except Exception:
        logger.warning("RBD probe: SSH connect failed for node %d", node_id)
        return None
    try:
        # --all includes inactive pools; --type rbd filters to Ceph RBD.
        _stdin, stdout, stderr = client.exec_command(
            "virsh pool-list --all --type rbd --name 2>/dev/null", timeout=20
        )
        out = stdout.read().decode(errors="replace").strip()
        # Any non-empty pool name → a defined rbd pool exists.
        names = [ln.strip() for ln in out.splitlines() if ln.strip()]
        return len(names) > 0
    except Exception:
        logger.warning("RBD probe: virsh pool-list failed for node %d", node_id)
        return None
    finally:
        try:
            client.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def _random_mac() -> str:
    """Generate a QEMU-style MAC (52:54:00:xx:xx:xx)."""
    b = secrets.token_bytes(3)
    return f"52:54:00:{b[0]:02x}:{b[1]:02x}:{b[2]:02x}"


def _build_disk_xml(pool: str, image: str, template_xml: str) -> str:
    """Build a <disk> element for a new RBD-backed image.

    If a template_xml from an existing VM on the same node is provided, we
    clone its structure (preserving auth / host entries) and only swap the
    source name.  This handles all Ceph auth variants automatically.
    """
    if template_xml:
        try:
            root = ET.fromstring(template_xml)
            src = root.find("source")
            if src is not None:
                src.set("name", f"{pool}/{image}")
            # Remove per-volume metadata that shouldn't carry over.
            # `boot` is critical: a cloned template disk may carry a
            # per-device <boot order='N'/>, which libvirt rejects when the
            # domain also sets os-level <boot dev='hd'/> ("per-device boot
            # elements cannot be used together with os/boot elements").
            for tag in ("serial", "address", "boot"):
                for el in root.findall(tag):
                    root.remove(el)
            tgt = root.find("target")
            if tgt is not None:
                tgt.set("dev", "vda")
                tgt.set("bus", "virtio")
            return ET.tostring(root, encoding="unicode")
        except ET.ParseError:
            pass
    # Fallback: bare RBD disk relying on ceph.conf for auth
    return (
        f"<disk type='network' device='disk'>"
        f"<driver name='qemu' type='raw'/>"
        f"<source protocol='rbd' name='{pool}/{image}'/>"
        f"<target dev='vda' bus='virtio'/>"
        f"</disk>"
    )


def _build_interface_xml(bridge: str, net_type: str, mac: str) -> str:
    if net_type == "ovs":
        return (
            f"<interface type='bridge'>"
            f"<mac address='{mac}'/>"
            f"<source bridge='{bridge}'/>"
            f"<virtualport type='openvswitch'/>"
            f"<model type='virtio'/>"
            f"</interface>"
        )
    elif net_type == "network":
        return (
            f"<interface type='network'>"
            f"<mac address='{mac}'/>"
            f"<source network='{bridge}'/>"
            f"<model type='virtio'/>"
            f"</interface>"
        )
    else:
        return (
            f"<interface type='bridge'>"
            f"<mac address='{mac}'/>"
            f"<source bridge='{bridge}'/>"
            f"<model type='virtio'/>"
            f"</interface>"
        )


def _build_cdrom_xml(iso_path: str) -> str:
    if not iso_path:
        return ""
    return (
        f"<disk type='file' device='cdrom'>"
        f"<driver name='qemu' type='raw'/>"
        f"<source file='{iso_path}'/>"
        f"<target dev='sda' bus='sata'/>"
        f"<readonly/>"
        f"</disk>"
    )


def _build_domain_xml(name: str, vm_uuid: str, vcpus: int, ram_mb: int,
                      disk_xml: str, interface_xml: str, emulator: str,
                      cdrom_xml: str = "", iso_path: str = "") -> str:
    boot = (
        "<boot dev='cdrom'/><boot dev='hd'/>" if iso_path
        else "<boot dev='hd'/>"
    )
    # virtio-serial channel exposes the qemu-guest-agent socket on the host
    # so `virsh domifaddr --source agent` can return the VM's IP once the
    # agent (installed via cloud-init) starts inside the guest. Without
    # this channel the agent has no transport and vm_sync can't discover
    # the VM's IP.
    guest_agent_channel = (
        "<controller type='virtio-serial' index='0'/>"
        "<channel type='unix'>"
        "<target type='virtio' name='org.qemu.guest_agent.0'/>"
        "</channel>"
    )
    return f"""<domain type='kvm'>
  <name>{name}</name>
  <uuid>{vm_uuid}</uuid>
  <metadata>
    <wvc:managed-by xmlns:wvc="https://kvm-orchestrator">kvm-orchestrator</wvc:managed-by>
  </metadata>
  <memory unit='MiB'>{ram_mb}</memory>
  <currentMemory unit='MiB'>{ram_mb}</currentMemory>
  <vcpu placement='static'>{vcpus}</vcpu>
  <os>
    <type arch='x86_64'>hvm</type>
    {boot}
  </os>
  <features><acpi/><apic/></features>
  <cpu mode='host-model' check='partial'><model fallback='allow'/></cpu>
  <devices>
    <emulator>{emulator}</emulator>
    {disk_xml}
    {cdrom_xml}
    {interface_xml}
    {guest_agent_channel}
    <serial type='pty'><target type='isa-serial' port='0'/></serial>
    <console type='pty'><target type='serial' port='0'/></console>
    <graphics type='vnc' port='-1' autoport='yes' listen='127.0.0.1'>
      <listen type='address' address='127.0.0.1'/>
    </graphics>
    <video><model type='vga' vram='16384' heads='1' primary='yes'/></video>
  </devices>
</domain>"""


def _clone_modify_xml(source_xml: str, new_name: str, new_uuid: str,
                      pool: str, source_image: str, new_image: str,
                      vcpus: int, ram_mb: int,
                      vlan: str, net_type: str) -> str:
    """Modify a cloned VM's XML: name, uuid, mac, disk, vcpu/ram, network."""
    root = ET.fromstring(source_xml)

    # Name and UUID
    name_el = root.find("name")
    if name_el is not None:
        name_el.text = new_name
    uuid_el = root.find("uuid")
    if uuid_el is not None:
        uuid_el.text = new_uuid

    # Remove title / description
    for tag in ("title", "description"):
        el = root.find(tag)
        if el is not None:
            root.remove(el)

    # Ensure managed-by metadata
    ns = "https://kvm-orchestrator"
    meta = root.find("metadata")
    if meta is None:
        meta = ET.SubElement(root, "metadata")
    for child in list(meta):
        if child.tag == f"{{{ns}}}managed-by":
            meta.remove(child)
    wvc = ET.SubElement(meta, f"{{{ns}}}managed-by")
    wvc.text = "kvm-orchestrator"

    # vCPUs
    vcpu_el = root.find("vcpu")
    if vcpu_el is not None:
        vcpu_el.text = str(vcpus)
    # CPU topology: update or strip to avoid mismatch
    cpu_el = root.find("cpu")
    if cpu_el is not None:
        topo = cpu_el.find("topology")
        if topo is not None:
            topo.set("sockets", "1")
            topo.set("cores", str(vcpus))
            topo.set("threads", "1")
        # Remove NUMA cells (complex to recalculate)
        for numa in cpu_el.findall("numa"):
            cpu_el.remove(numa)

    # RAM (convert MB → KiB, which is the libvirt internal unit)
    ram_kib = ram_mb * 1024
    for tag in ("memory", "currentMemory"):
        el = root.find(tag)
        if el is not None:
            existing_unit = el.get("unit", "KiB").lower()
            el.set("unit", "KiB")
            el.text = str(ram_kib)

    # Disk source: replace pool/image name
    for disk in root.findall(".//disk"):
        if disk.get("type") == "network" and disk.get("device") == "disk":
            src = disk.find("source")
            if src is not None:
                src.set("name", f"{pool}/{new_image}")
            # Remove per-volume address element
            for addr in disk.findall("address"):
                disk.remove(addr)
            break

    # Network interface: update bridge + virtualport + regenerate MAC
    for iface in root.findall(".//interface"):
        src = iface.find("source")
        if src is None:
            continue
        if net_type == "network":
            iface.set("type", "network")
            src.attrib.clear()
            src.set("network", vlan)
            for vp in iface.findall("virtualport"):
                iface.remove(vp)
        else:
            iface.set("type", "bridge")
            src.attrib.clear()
            src.set("bridge", vlan)
            existing_vp = iface.find("virtualport")
            if net_type == "ovs":
                if existing_vp is None:
                    existing_vp = ET.SubElement(iface, "virtualport")
                existing_vp.set("type", "openvswitch")
            else:
                if existing_vp is not None:
                    iface.remove(existing_vp)
        mac_el = iface.find("mac")
        if mac_el is not None:
            mac_el.set("address", _random_mac())
        break  # only first interface

    return ET.tostring(root, encoding="unicode")


# ---------------------------------------------------------------------------
# Parse RBD path from disk_path field stored in DB
# ---------------------------------------------------------------------------

def _parse_rbd_path(disk_path: str) -> tuple[str, str] | None:
    """Return (pool, image) from a stored disk_path, or None."""
    if not disk_path:
        return None
    # "rbd:poolname/imagename"
    m = re.match(r"rbd[:/]+([^/]+)/(.+)", disk_path)
    if m:
        return m.group(1), m.group(2)
    # "poolname/imagename"
    parts = disk_path.split("/", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@track_vm_create
def vm_create(fields: dict) -> dict:
    """Main entry point — create a new VM in blank, clone, or existing-disk mode.

    `owner_user_id` is stamped from the calling user's JWT identity by
    `api.vms.create_vm`. Pre-stamping in the service layer keeps the
    visibility model honest: every UI-created VM has an owner, so the
    owner can later flip it between SHARED and PERSONAL without admin
    intervention.

    `tenant_id` is also chosen + validated by `api.vms.create_vm` against
    the caller's role/membership and passed through here. If absent we
    default to SHARED inside `_insert_vm` (preserves the v0.10
    admin-only-create behaviour for any code path that bypasses the API
    layer).
    """
    node_id  = int(fields["node_id"])
    name     = fields["name"].strip()
    vcpus    = int(fields["vcpus"])
    ram_mb   = int(fields["ram_gb"]) * 1024
    vlan     = fields["vlan"]
    mode     = fields.get("mode", "blank")
    owner_user_id = fields.get("owner_user_id")
    if owner_user_id is not None:
        owner_user_id = int(owner_user_id)

    # `tenant_id` may legitimately be None (= untagged, admin-only). The API
    # layer is authoritative for "missing vs. explicit None" — by the time we
    # see it, the value is final.
    if "tenant_id" in fields:
        raw_tid = fields["tenant_id"]
        tenant_id: int | None = None if raw_tid in (None, "", 0) else int(raw_tid)
    else:
        tenant_id = SHARED_TENANT_ID

    if not name:
        return {"error": "VM name is required"}

    node = _get_node(node_id)
    if not node:
        return {"error": "Node not found"}
    if node["status"] != "online":
        return {"error": f"Node is {node['status']} — can only create VMs on online nodes"}
    if not node.get("private_key"):
        return {"error": "No SSH key configured for this node"}

    if _name_exists_on_node(node_id, name):
        return {"error": f"A VM named '{name}' already exists on this node"}

    if mode == "blank":
        pool     = fields.get("pool", "")
        disk_gb  = int(fields.get("disk_gb", 20))
        iso_path = fields.get("iso_path", "") or ""
        return _blank_create(node, name, vcpus, ram_mb, pool, disk_gb, vlan,
                             iso_path, owner_user_id, tenant_id)

    elif mode == "clone":
        source_vm_id = fields.get("source_vm_id")
        if not source_vm_id:
            return {"error": "source_vm_id is required for clone mode"}
        return _clone_create(node, name, vcpus, ram_mb, vlan,
                             int(source_vm_id), owner_user_id, tenant_id)

    elif mode == "existing":
        existing_disk = fields.get("existing_disk", "").strip()
        if not existing_disk:
            return {"error": "existing_disk (pool/image) is required for existing mode"}
        return _existing_create(node, name, vcpus, ram_mb, vlan,
                                existing_disk, owner_user_id, tenant_id)

    elif mode == "quick":
        image_id = (fields.get("image_id") or "").strip()
        if not image_id:
            return {"error": "image_id is required for quick mode"}

        image = vm_image_service.get_image(image_id)
        if not image:
            return {"error": f"Unknown image id: {image_id}"}

        # Linux VMs need at least one SSH key — they're configured for
        # key-only auth. Windows VMs ignore this (cloud-init doesn't run).
        ssh_keys = fields.get("ssh_pub_keys", "")

        # The cloud-init `users:` block needs a username. The API layer
        # typically passes the caller's CEC; fall back to the image's
        # documented default (`ubuntu` for Ubuntu cloud images).
        default_user = (fields.get("default_user") or "").strip() or image["default_user"]

        # v0.14.2 — optional crypt(3) password hash. When present, the
        # cloud-init seed unlocks the user's password (lock_passwd:
        # false) and enables ssh_pwauth so console login works too.
        # When absent, falls back to the v0.14.0 key-only posture.
        # The API layer is responsible for sourcing this (session
        # password from Redis, or hash of a user-supplied alternative);
        # the service layer just passes it through.
        password_hash = fields.get("password_hash") or None

        # Owner is needed for the saved-pubkey side-effect below.
        # `owner_user_id` was set by the API layer from JWT identity.

        # disk_gb: explicit value from the request wins; otherwise the
        # image's "M" T-shirt size is the safest middle default.
        disk_gb_raw = fields.get("disk_gb")
        if disk_gb_raw:
            try:
                disk_gb = int(disk_gb_raw)
            except (TypeError, ValueError):
                return {"error": "disk_gb must be an integer"}
        else:
            m_size = image.get("sizes", {}).get("M", {})
            disk_gb = int(m_size.get("disk_gb", 50))

        return _quick_create(
            node=node, name=name, vcpus=vcpus, ram_mb=ram_mb,
            vlan=vlan, image=image, ssh_keys=ssh_keys,
            default_user=default_user, disk_gb=disk_gb,
            owner_user_id=owner_user_id, tenant_id=tenant_id,
            password_hash=password_hash,
        )

    return {"error": f"Unknown mode: {mode}"}


def _get_rbd_auth(client: paramiko.SSHClient, resources: dict) -> tuple[str, str | None]:
    """
    Build rbd command-line auth args from the node's disk XML template.

    Reads auth username, libvirt secret UUID, and monitor hosts directly from the
    disk_xml_template returned by get_node_resources().  This works even when the
    Ceph pool is not registered as a virsh storage pool (e.g. libvirt-pool).

    Returns (auth_args_str, tmp_keyring_path).
    tmp_keyring_path is not None when a temp keyring was written — caller must rm it.
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
        key_value, _, rc = _run(client, f"virsh secret-get-value '{secret_uuid}' 2>/dev/null")
        if rc == 0 and key_value.strip():
            keyring_path = f"/tmp/wvc_rbd_{secrets.token_hex(4)}.keyring"
            keyring_content = f"[client.{username}]\n\tkey = {key_value.strip()}\n"
            kb64 = base64.b64encode(keyring_content.encode()).decode()
            _run(client,
                 f"printf '%s' '{kb64}' | base64 -d > {keyring_path}"
                 f" && chmod 600 {keyring_path}")
            auth_args += f" --keyring {keyring_path}"
        else:
            logger.warning("Could not read Ceph secret %s — rbd may fail", secret_uuid)

    if hosts:
        auth_args += f" --mon-host '{','.join(hosts)}'"

    return auth_args, keyring_path


def _validate_rbd_pool(resources: dict, pool: str, name: str,
                       node: dict) -> dict | None:
    """Validate that `pool` exists on the node AND is a Ceph RBD pool.

    All VM-create paths provision the disk with raw `rbd` commands, so a
    non-rbd libvirt pool (e.g. a `dir` pool such as 'default' or 'nvram')
    is invalid — selecting one produced
    `rbd: error opening pool '<name>': (2) No such file or directory`
    because there is no matching Ceph pool behind it.

    Returns an error dict to return to the caller, or None if the pool is
    a valid rbd pool.
    """
    pool_info = next((p for p in resources.get("pools", [])
                      if p["name"] == pool), None)
    if not pool_info:
        available = [p["name"] for p in resources.get("pools", [])]
        logger.error("Create '%s' on %s: pool '%s' not found (available: %s)",
                     name, node["hostname"], pool, available)
        return {"error": f"Pool '{pool}' not found on node. Available: {available}"}

    if pool_info.get("type") != "rbd":
        rbd_pools = [p["name"] for p in resources.get("pools", [])
                     if p.get("type") == "rbd"]
        logger.error("Create '%s' on %s: pool '%s' is type '%s', not 'rbd'; "
                     "rbd-backed pools available: %s",
                     name, node["hostname"], pool, pool_info.get("type"),
                     rbd_pools)
        return {"error": (
            f"Pool '{pool}' is a '{pool_info.get('type')}' pool, not an RBD "
            f"pool. VM disks must use a Ceph RBD pool. Available RBD pools: "
            f"{rbd_pools or 'none on this node'}"
        )}
    return None


def _resolve_ceph_pool(resources: dict, pool: str) -> str:
    """Translate a selected libvirt pool NAME to the underlying Ceph pool
    name used by raw `rbd` commands.

    A libvirt rbd storage pool can be named differently from the Ceph pool
    it fronts — e.g. libvirt pool ``rbdpool`` backed by Ceph pool
    ``libvirt-pool`` (this is the case on the entire lab fleet). The
    `rbd create/info/...` CLI needs the CEPH pool name, not the libvirt
    one. Discovery captures both (``name`` = libvirt, ``ceph_pool`` =
    Ceph source name); this returns the ceph_pool when known, else falls
    back to the given name (correct when they're identical, e.g. a pool
    literally named ``libvirt-pool``).
    """
    for p in resources.get("pools", []):
        if p.get("name") == pool and p.get("type") == "rbd":
            cp = (p.get("ceph_pool") or "").strip()
            return cp or pool
    return pool


def _blank_create(node: dict, name: str, vcpus: int, ram_mb: int,
                  pool: str, disk_gb: int, vlan: str, iso_path: str,
                  owner_user_id: int | None = None,
                  tenant_id: int | None = SHARED_TENANT_ID) -> dict:
    node_id = node["id"]

    # Fetch node resources to get auth/template and network type
    resources = get_node_resources(node_id)
    if "error" in resources:
        return resources

    pool_err = _validate_rbd_pool(resources, pool, name, node)
    if pool_err:
        return pool_err

    # Translate the selected libvirt pool name to the underlying Ceph pool
    # for all raw `rbd` + disk-XML operations (e.g. libvirt 'rbdpool' →
    # Ceph 'libvirt-pool'). Passing the libvirt name straight to `rbd`
    # produced `rbd: error opening pool 'rbdpool'`.
    ceph_pool = _resolve_ceph_pool(resources, pool)

    net_info = next(
        (n for n in resources.get("networks", []) if n["name"] == vlan),
        {"name": vlan, "type": "linux"},
    )

    vm_uuid  = str(uuid.uuid4())
    mac      = _random_mac()
    disk_xml = _build_disk_xml(ceph_pool, name, resources.get("disk_xml_template", ""))
    iface_xml = _build_interface_xml(vlan, net_info["type"], mac)
    cdrom_xml = _build_cdrom_xml(iso_path)
    emulator  = resources.get("emulator", "/usr/bin/qemu-system-x86_64")
    domain_xml = _build_domain_xml(
        name=name, vm_uuid=vm_uuid, vcpus=vcpus, ram_mb=ram_mb,
        disk_xml=disk_xml, interface_xml=iface_xml, emulator=emulator,
        cdrom_xml=cdrom_xml, iso_path=iso_path,
    )

    xml_path = f"/tmp/wvc_create_{name}.xml"
    client = _ssh_connect(node)
    keyring_path = None
    try:
        auth_args, keyring_path = _get_rbd_auth(client, resources)

        # Check if image already exists
        _, _, rc = _run(client, f"rbd {auth_args} info {ceph_pool}/{name} 2>/dev/null")
        if rc == 0:
            logger.error("Create '%s' on %s: RBD image %s/%s already exists",
                         name, node["hostname"], ceph_pool, name)
            return {"error": f"RBD image '{ceph_pool}/{name}' already exists"}

        # Create RBD image
        out, err, rc = _run(client, f"rbd {auth_args} create {ceph_pool}/{name} --size {disk_gb}G 2>&1")
        if rc != 0:
            logger.error("Create '%s' on %s (node_id=%s): rbd create %s/%s "
                         "failed (rc=%s): %s",
                         name, node["hostname"], node_id, ceph_pool, name, rc,
                         (err or out).strip())
            return {"error": f"rbd create failed: {err or out}"}

        # Write VM XML and define
        _write_file(client, xml_path, domain_xml)
        out, err, rc = _run(client, f"virsh define {xml_path}")
        if rc != 0:
            logger.error("Create '%s' on %s: virsh define failed (rc=%s): %s",
                         name, node["hostname"], rc, (err or out).strip())
            _run(client, f"rbd {auth_args} rm {ceph_pool}/{name} 2>/dev/null")
            return {"error": f"virsh define failed: {err or out}"}

        # Start
        out, err, rc = _run(client, f"virsh start '{name}'")
        if rc != 0:
            logger.error("Create '%s' on %s: virsh start failed (rc=%s): %s",
                         name, node["hostname"], rc, (err or out).strip())
            _run(client, f"virsh undefine '{name}' 2>/dev/null")
            _run(client, f"rbd {auth_args} rm {ceph_pool}/{name} 2>/dev/null")
            return {"error": f"virsh start failed: {err or out}"}

        _run(client, f"rm -f {xml_path}")
    finally:
        if keyring_path:
            _run(client, f"rm -f {keyring_path}")
        client.close()

    vm_id = _insert_vm(
        node_id, name, vm_uuid, "running", vcpus, ram_mb,
        vlan, disk_gb, f"rbd:{pool}/{name}", pool,
        tenant_id=tenant_id, owner_user_id=owner_user_id,
        os_family="custom",  # blank disk — no catalog OS
    )
    logger.info("Created blank VM '%s' (id=%d) on node %d", name, vm_id, node_id)
    return {"ok": True, "id": vm_id, "name": name, "uuid": vm_uuid}


def _clone_create(target_node: dict, name: str, vcpus: int, ram_mb: int,
                  vlan: str, source_vm_id: int,
                  owner_user_id: int | None = None,
                  tenant_id: int | None = SHARED_TENANT_ID) -> dict:
    """Clone an existing VM. v0.13.0: source VM may live on any node.

    The clone is always created on the **source VM's** node (we need
    libvirt access to the source domain for `virsh dumpxml`, and Ceph
    RBD is globally addressable so `rbd copy` works from any node). If
    the caller chose a different target node, we then delegate to
    `vm_service.vm_migrate(..., allow_shutdown=True)`:

      * same CPU model → live migration (Ceph: no disk copy, seconds)
      * different CPU  → force-stop + offline migrate + auto-restart

    If migration fails after a successful clone, the new VM is left
    intact on the source node; the response carries a `warning` rather
    than rolling back, so the user keeps the work.
    """
    target_node_id = target_node["id"]

    source = _get_vm_by_id(source_vm_id)
    if not source:
        return {"error": "Source VM not found"}

    source_node_id = source["node_id"]
    same_node = source_node_id == target_node_id

    if same_node:
        source_node = target_node
    else:
        source_node = _get_node(source_node_id)
        if not source_node:
            return {"error": "Source VM's node not found"}
        if source_node["status"] != "online":
            return {"error": (
                f"Source VM's node ({source_node['hostname']}) is "
                f"{source_node['status']} — must be online to clone"
            )}
        if not source_node.get("private_key"):
            return {"error": "No SSH key configured for source VM's node"}
        # vm_create() has already checked the target node; ensure the
        # name is also free on the source (we'll define it there first).
        if _name_exists_on_node(source_node_id, name):
            return {"error": (
                f"A VM named '{name}' already exists on the source node "
                f"({source_node['hostname']})"
            )}

    parsed = _parse_rbd_path(source.get("disk_path", ""))
    if not parsed:
        return {"error": f"Cannot determine source RBD image from disk_path: {source.get('disk_path')}"}
    pool, source_image = parsed

    # Fetch resources from the SOURCE node — that's where we'll do the
    # rbd copy + virsh ops, so the pool auth template and network type
    # must come from there.
    resources = get_node_resources(source_node_id)
    net_info = next(
        (n for n in resources.get("networks", []) if n["name"] == vlan),
        {"name": vlan, "type": "linux"},
    )
    pool_info = next((p for p in resources.get("pools", []) if p["name"] == pool), None)

    vm_uuid  = str(uuid.uuid4())
    xml_path = f"/tmp/wvc_clone_{name}.xml"

    client = _ssh_connect(source_node)
    keyring_path = None
    try:
        auth_args, keyring_path = _get_rbd_auth(client, resources)

        # Check if destination image already exists
        _, _, rc = _run(client, f"rbd {auth_args} info {pool}/{name} 2>/dev/null")
        if rc == 0:
            return {"error": f"RBD image '{pool}/{name}' already exists"}

        # Get source VM XML from the source's libvirt
        out, err, rc = _run(client, f"virsh dumpxml --migratable '{source['name']}'")
        if rc != 0:
            return {"error": f"virsh dumpxml failed on source node: {err or out}"}

        try:
            domain_xml = _clone_modify_xml(
                source_xml=out,
                new_name=name,
                new_uuid=vm_uuid,
                pool=pool,
                source_image=source_image,
                new_image=name,
                vcpus=vcpus,
                ram_mb=ram_mb,
                vlan=vlan,
                net_type=net_info["type"],
            )
        except Exception as e:
            return {"error": f"XML modification failed: {e}"}

        # Clone the RBD image (may take minutes for large disks)
        logger.info("rbd copy %s/%s → %s/%s on node %d (%s)",
                    pool, source_image, pool, name,
                    source_node_id, source_node.get("hostname"))
        out, err, rc = _run(
            client, f"rbd {auth_args} copy {pool}/{source_image} {pool}/{name} 2>&1",
            timeout=600,
        )
        if rc != 0:
            logger.error("Clone '%s' on %s: rbd copy %s/%s → %s/%s failed "
                         "(rc=%s): %s", name, source_node.get("hostname"),
                         pool, source_image, pool, name, rc, (err or out).strip())
            return {"error": f"rbd copy failed: {err or out}"}

        # Define + start on the source node
        _write_file(client, xml_path, domain_xml)
        out, err, rc = _run(client, f"virsh define {xml_path}")
        if rc != 0:
            logger.error("Clone '%s' on %s: virsh define failed (rc=%s): %s",
                         name, source_node.get("hostname"), rc, (err or out).strip())
            _run(client, f"rbd {auth_args} rm {pool}/{name} 2>/dev/null")
            return {"error": f"virsh define failed: {err or out}"}

        out, err, rc = _run(client, f"virsh start '{name}'")
        if rc != 0:
            logger.error("Clone '%s' on %s: virsh start failed (rc=%s): %s",
                         name, source_node.get("hostname"), rc, (err or out).strip())
            _run(client, f"virsh undefine '{name}' 2>/dev/null")
            _run(client, f"rbd {auth_args} rm {pool}/{name} 2>/dev/null")
            return {"error": f"virsh start failed: {err or out}"}

        _run(client, f"rm -f {xml_path}")
    finally:
        if keyring_path:
            _run(client, f"rm -f {keyring_path}")
        client.close()

    disk_gb = source.get("disk_gb", 0)
    vm_id = _insert_vm(
        node_id, name, vm_uuid, "running", vcpus, ram_mb,
        vlan, disk_gb, f"rbd:{pool}/{name}", pool,
        tenant_id=tenant_id, owner_user_id=owner_user_id,
        os_family="custom",  # clone of an existing VM — OS not tracked at source
    )
    logger.info("Cloned VM '%s' (id=%d) from '%s' on node %d (%s)",
                name, vm_id, source["name"], source_node_id,
                source_node.get("hostname"))

    # Same-node clone — done.
    if same_node:
        return {"ok": True, "id": vm_id, "name": name, "uuid": vm_uuid}

    # Cross-node clone — migrate to the chosen target. allow_shutdown=True
    # because a brand-new clone has no live state worth preserving; if
    # CPUs differ we'd rather force-stop and offline-migrate than 400.
    from . import vm_service  # late import (vm_service imports from us)
    logger.info(
        "Clone '%s' (id=%d) created on source node %d (%s), "
        "migrating to target node %d (%s)",
        name, vm_id, source_node_id, source_node.get("hostname"),
        target_node_id, target_node.get("hostname"),
    )
    mig_result = vm_service.vm_migrate(vm_id, target_node_id, allow_shutdown=True)
    if "error" in mig_result:
        logger.warning(
            "Clone '%s' (id=%d) succeeded on '%s' but migration to '%s' failed: %s",
            name, vm_id, source_node.get("hostname"),
            target_node.get("hostname"), mig_result["error"],
        )
        return {
            "ok": True,
            "id": vm_id,
            "name": name,
            "uuid": vm_uuid,
            "warning": (
                f"Clone created on '{source_node.get('hostname')}' but "
                f"migration to '{target_node.get('hostname')}' failed: "
                f"{mig_result['error']}"
            ),
        }

    return {
        "ok": True,
        "id": vm_id,
        "name": name,
        "uuid": vm_uuid,
        "migrated": True,
        "from_node": source_node.get("hostname"),
        "to_node": target_node.get("hostname"),
        "live": mig_result.get("live"),
    }


def _existing_create(node: dict, name: str, vcpus: int, ram_mb: int,
                     vlan: str, existing_disk: str,
                     owner_user_id: int | None = None,
                     tenant_id: int | None = SHARED_TENANT_ID) -> dict:
    """
    Create a VM that boots from an existing RBD image.
    The image is NOT created or copied — it is used as-is.
    """
    node_id = node["id"]

    parts = existing_disk.split("/", 1)
    if len(parts) != 2:
        return {"error": f"existing_disk must be 'pool/image', got: {existing_disk!r}"}
    pool, image = parts

    resources = get_node_resources(node_id)
    if "error" in resources:
        return resources

    net_info = next(
        (n for n in resources.get("networks", []) if n["name"] == vlan),
        {"name": vlan, "type": "linux"},
    )
    # Same rbd-pool guard as _blank_create: `existing_disk` is user-supplied
    # as 'pool/image', and this path runs `rbd info`/`rbd` against that pool.
    # A non-rbd (dir) pool such as 'default'/'nvram' has no Ceph pool behind
    # it → "rbd: error opening pool". Reject with a clear message.
    pool_err = _validate_rbd_pool(resources, pool, name, node)
    if pool_err:
        return pool_err
    pool_info = next((p for p in resources.get("pools", []) if p["name"] == pool), None)

    # Translate libvirt pool name → Ceph pool for raw rbd + disk XML
    # (e.g. 'rbdpool' → 'libvirt-pool'). The user-supplied existing_disk
    # uses the libvirt pool name; rbd needs the Ceph one.
    ceph_pool = _resolve_ceph_pool(resources, pool)
    ceph_existing_disk = f"{ceph_pool}/{image}"

    vm_uuid   = str(uuid.uuid4())
    mac       = _random_mac()
    disk_xml  = _build_disk_xml(ceph_pool, image, resources.get("disk_xml_template", ""))
    iface_xml = _build_interface_xml(vlan, net_info["type"], mac)
    emulator  = resources.get("emulator", "/usr/bin/qemu-system-x86_64")
    domain_xml = _build_domain_xml(
        name=name, vm_uuid=vm_uuid, vcpus=vcpus, ram_mb=ram_mb,
        disk_xml=disk_xml, interface_xml=iface_xml, emulator=emulator,
    )

    xml_path = f"/tmp/wvc_create_{name}.xml"
    client = _ssh_connect(node)
    keyring_path = None
    try:
        auth_args, keyring_path = _get_rbd_auth(client, resources)

        _, _, rc = _run(client, f"rbd {auth_args} info {ceph_existing_disk} 2>/dev/null")
        if rc != 0:
            logger.error("Create-existing '%s' on %s: RBD image '%s' not found",
                         name, node["hostname"], ceph_existing_disk)
            return {"error": f"RBD image '{ceph_existing_disk}' not found on node"}

        _write_file(client, xml_path, domain_xml)
        out, err, rc = _run(client, f"virsh define {xml_path}")
        if rc != 0:
            logger.error("Create-existing '%s' on %s: virsh define failed "
                         "(rc=%s): %s", name, node["hostname"], rc,
                         (err or out).strip())
            return {"error": f"virsh define failed: {err or out}"}

        out, err, rc = _run(client, f"virsh start '{name}'")
        if rc != 0:
            logger.error("Create-existing '%s' on %s: virsh start failed "
                         "(rc=%s): %s", name, node["hostname"], rc,
                         (err or out).strip())
            _run(client, f"virsh undefine '{name}' 2>/dev/null")
            return {"error": f"virsh start failed: {err or out}"}

        _run(client, f"rm -f {xml_path}")
    finally:
        if keyring_path:
            _run(client, f"rm -f {keyring_path}")
        client.close()

    vm_id = _insert_vm(
        node_id, name, vm_uuid, "running", vcpus, ram_mb,
        vlan, 0, f"rbd:{existing_disk}", pool,
        tenant_id=tenant_id, owner_user_id=owner_user_id,
        os_family="custom",  # existing disk — no catalog OS
    )
    logger.info("Created VM '%s' (id=%d) on existing disk '%s'", name, vm_id, existing_disk)
    return {"ok": True, "id": vm_id, "name": name, "uuid": vm_uuid}


# ---------------------------------------------------------------------------
# Quick Create (v0.14.0) — golden RBD image clone + cloud-init / UEFI
# ---------------------------------------------------------------------------

# Standard snapshot name we create + protect on every golden image the
# Quick Create flow touches. Lives in its own namespace so it never
# conflicts with self-service's "snap" snapshot on the same images —
# the two systems can coexist on the same RBD pool indefinitely.
WVC_GOLDEN_SNAP = "wvc-golden"

# Per-VM seed ISO location on the KVM host. Lives under the standard
# libvirt images directory so SELinux / AppArmor labels apply correctly.
# We `mkdir -p` it on every quick-create.
SEED_ISO_DIR = "/var/lib/libvirt/images/wvc-seed"

# Libvirt's standard per-VM NVRAM location for UEFI guests. The VARS
# file lives here; libvirt itself manages this directory.
NVRAM_DIR = "/var/lib/libvirt/qemu/nvram"

# OVMF firmware paths to probe on the KVM host. Order matters — we
# pick the first match. `_4M` files are preferred when present (newer
# OVMF spec, larger NVRAM). We need both an OVMF_CODE (read-only
# firmware) and an OVMF_VARS template (per-VM NVRAM seed).
_OVMF_CODE_CANDIDATES = [
    "/usr/share/OVMF/OVMF_CODE_4M.fd",
    "/usr/share/OVMF/OVMF_CODE.fd",
    "/usr/share/edk2-ovmf/x64/OVMF_CODE.fd",
    "/usr/share/edk2/x64/OVMF_CODE.fd",
    "/usr/share/edk2-ovmf/OVMF_CODE.fd",
    "/usr/share/qemu/OVMF_CODE.fd",
    "/usr/share/qemu/ovmf-x86_64-code.bin",
]
_OVMF_VARS_CANDIDATES = [
    "/usr/share/OVMF/OVMF_VARS_4M.fd",
    "/usr/share/OVMF/OVMF_VARS.fd",
    "/usr/share/edk2-ovmf/x64/OVMF_VARS.fd",
    "/usr/share/edk2/x64/OVMF_VARS.fd",
    "/usr/share/edk2-ovmf/OVMF_VARS.fd",
    "/usr/share/qemu/OVMF_VARS.fd",
    "/usr/share/qemu/ovmf-x86_64-vars.bin",
]


def _probe_ovmf(client: paramiko.SSHClient) -> tuple[str, str] | None:
    """Find an OVMF firmware + VARS template on the KVM host.

    Returns (code_path, vars_template_path) or None if no usable pair
    was found. We require both halves to come from compatible probes —
    practically we just take the first hit in each list, which works
    because every Linux distro that ships OVMF ships both halves
    together at matching paths.
    """
    code_path = ""
    for p in _OVMF_CODE_CANDIDATES:
        _, _, rc = _run(client, f"test -f {p}")
        if rc == 0:
            code_path = p
            break

    vars_path = ""
    for p in _OVMF_VARS_CANDIDATES:
        _, _, rc = _run(client, f"test -f {p}")
        if rc == 0:
            vars_path = p
            break

    if not code_path or not vars_path:
        return None
    return code_path, vars_path


def _ensure_golden_snap(client: paramiko.SSHClient, auth_args: str,
                        pool: str, image: str,
                        snap: str = WVC_GOLDEN_SNAP) -> tuple[bool, str]:
    """Bootstrap a protected RBD snapshot suitable for cloning.

    Idempotent — safe to call on every quick-create. Performs three
    steps in order:

      1. `rbd feature enable ... layering` (no-op if already on).
         Required for clones to work at all.
      2. `rbd snap create <pool>/<image>@<snap>` if missing.
      3. `rbd snap protect ...` if not already protected. A protected
         snapshot is what makes clones safe — you can't accidentally
         delete the parent while children depend on it.

    Returns (True, "ok") on success or (False, "<reason>") on failure;
    the caller surfaces the reason to the user.
    """
    # 0. Confirm the source image exists.
    out, _, rc = _run(client, f"rbd {auth_args} info {pool}/{image} 2>&1")
    if rc != 0:
        return False, f"source image {pool}/{image} not found: {out}"

    # 1. layering feature — probe via `info`, only call enable if needed.
    if "layering" not in out:
        out2, err2, rc2 = _run(
            client,
            f"rbd {auth_args} feature enable {pool}/{image} layering 2>&1",
        )
        if rc2 != 0 and "already enabled" not in (out2 + err2).lower():
            return False, f"enable layering: {err2 or out2}"

    # 2. Snapshot exists?  Use JSON output where available; fall back to
    #    grepping plain text for very old `rbd` versions.
    has_snap, is_protected = _read_snap_state(client, auth_args, pool, image, snap)

    if not has_snap:
        out, err, rc = _run(
            client,
            f"rbd {auth_args} snap create {pool}/{image}@{snap} 2>&1",
            timeout=60,
        )
        if rc != 0 and "already exists" not in (out + err).lower():
            return False, f"snap create: {err or out}"
        # Re-read state so we know whether to protect.
        _, is_protected = _read_snap_state(client, auth_args, pool, image, snap)

    # 3. Protect — required for clone. Idempotent.
    if not is_protected:
        out, err, rc = _run(
            client,
            f"rbd {auth_args} snap protect {pool}/{image}@{snap} 2>&1",
            timeout=30,
        )
        if rc != 0 and "already protected" not in (out + err).lower():
            return False, f"snap protect: {err or out}"

    return True, "ok"


def _read_snap_state(client: paramiko.SSHClient, auth_args: str,
                     pool: str, image: str,
                     snap: str) -> tuple[bool, bool]:
    """Return (snap_exists, snap_is_protected) for one snapshot."""
    out, _, rc = _run(
        client,
        f"rbd {auth_args} snap ls {pool}/{image} --format json 2>/dev/null",
    )
    if rc == 0 and out.strip():
        try:
            for s in json.loads(out):
                if s.get("name") == snap:
                    return True, s.get("protected") in (True, "true")
        except json.JSONDecodeError:
            pass

    # Fallback: parse plain output (older rbd). Each non-empty line after
    # the header looks like: "    1   wvc-golden  10 GiB  yes"
    out_plain, _, _ = _run(
        client, f"rbd {auth_args} snap ls {pool}/{image} 2>/dev/null"
    )
    for line in out_plain.splitlines():
        toks = line.split()
        if len(toks) >= 2 and toks[1] == snap:
            protected = "yes" in line.lower()
            return True, protected
    return False, False


def _build_windows_domain_xml(name: str, vm_uuid: str, vcpus: int, ram_mb: int,
                              disk_xml: str, interface_xml: str,
                              emulator: str, ovmf_code: str,
                              nvram_path: str) -> str:
    """Build a Windows-compatible domain XML.

    Differences from the generic `_build_domain_xml`:
      * `machine='q35'` (modern PCIe chipset, required by Win10/11)
      * `<loader type='pflash'>` + `<nvram>` for UEFI boot
      * `host-passthrough` CPU with explicit `<topology>` —  matches
        self-service's Windows path; the trade-off is no live-migration
        across mixed CPU models, which our offline-migrate fallback
        handles anyway.
      * Hyper-V enlightenments (`hyperv` features) for guest performance
      * SATA + USB-XHCI controllers (Windows wants explicit topology)
      * memballoon (virtio) for ram reclaim from QEMU
    """
    return f"""<domain type='kvm'>
  <name>{name}</name>
  <uuid>{vm_uuid}</uuid>
  <metadata>
    <wvc:managed-by xmlns:wvc="https://kvm-orchestrator">kvm-orchestrator</wvc:managed-by>
  </metadata>
  <memory unit='MiB'>{ram_mb}</memory>
  <currentMemory unit='MiB'>{ram_mb}</currentMemory>
  <vcpu placement='static'>{vcpus}</vcpu>
  <os>
    <type arch='x86_64' machine='q35'>hvm</type>
    <loader readonly='yes' secure='no' type='pflash'>{ovmf_code}</loader>
    <nvram>{nvram_path}</nvram>
    <boot dev='hd'/>
    <bootmenu enable='yes'/>
  </os>
  <features>
    <acpi/>
    <apic/>
    <hyperv>
      <relaxed state='on'/>
      <vapic state='on'/>
      <spinlocks state='on' retries='8191'/>
    </hyperv>
  </features>
  <cpu mode='host-passthrough' check='none' migratable='on'>
    <topology sockets='1' dies='1' cores='{vcpus}' threads='1'/>
  </cpu>
  <clock offset='utc'>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='pit' tickpolicy='delay'/>
    <timer name='hpet' present='no'/>
    <timer name='hypervclock' present='yes'/>
  </clock>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>destroy</on_crash>
  <pm>
    <suspend-to-mem enabled='no'/>
    <suspend-to-disk enabled='no'/>
  </pm>
  <devices>
    <emulator>{emulator}</emulator>
    {disk_xml}
    <controller type='usb' index='0' model='qemu-xhci' ports='15'/>
    <controller type='pci' index='0' model='pcie-root'/>
    <controller type='sata' index='0'/>
    {interface_xml}
    <serial type='pty'><target type='isa-serial' port='0'/></serial>
    <console type='pty'><target type='serial' port='0'/></console>
    <graphics type='vnc' port='-1' autoport='yes' listen='127.0.0.1'>
      <listen type='address' address='127.0.0.1'/>
    </graphics>
    <video><model type='qxl' ram='65536' vram='65536' vgamem='16384' heads='1' primary='yes'/></video>
    <memballoon model='virtio'/>
  </devices>
</domain>"""


def _quick_create(node: dict, name: str, vcpus: int, ram_mb: int,
                  vlan: str, image: dict, ssh_keys,
                  default_user: str, disk_gb: int,
                  owner_user_id: int | None = None,
                  tenant_id: int | None = SHARED_TENANT_ID,
                  password_hash: str | None = None) -> dict:
    """Clone a golden RBD image and bring up the resulting VM.

    Steps:
      1. Resolve image catalog entry → (pool, src_image, os_family).
      2. SSH to node, build rbd auth args.
      3. Idempotently set up the protected `wvc-golden` snapshot on
         the source image (first call per image takes ~1s extra).
      4. `rbd clone src@wvc-golden dst` (instant — COW), then
         `rbd resize` to the requested disk_gb.
      5. **Linux**: render cloud-init user-data + network-config →
         build seed ISO with `cloud-localds` → attach as virtual CDROM
         in the libvirt XML. If `password_hash` is supplied (v0.14.2),
         the seed unlocks the user's password (lock_passwd: false) and
         enables ssh_pwauth so VNC + SSH-password login both work.
         **Windows**: probe OVMF on the host → copy the OVMF_VARS
         template into a per-VM NVRAM file → build a q35 + pflash XML.
         `password_hash` is ignored for Windows (no cloud-init).
      6. `virsh define` + `virsh start`. On any error after the clone,
         we destroy the partially-built artefacts (rbd image, seed ISO,
         NVRAM file) so a retry with the same name succeeds.
      7. Insert the VM row with `disk_path = rbd:<pool>/<name>` so
         the rest of the controller treats it identically to a
         blank-mode VM.
      8. v0.14.2 — for Linux, side-effect: persist the user's SSH keys
         in `user_ssh_pubkeys` against `owner_user_id` so the next
         Quick Create can pre-fill the textarea. Failures here are
         logged and swallowed — the VM is already up; not worth
         failing the whole call for a cache miss.
    """
    node_id = node["id"]

    pool      = image["pool"]
    src_image = image["image"]
    os_family = image["os_family"]
    image_id  = image["id"]

    # Resources needed for: rbd auth args, network type, qemu emulator path.
    resources = get_node_resources(node_id)
    if "error" in resources:
        return resources

    net_info = next(
        (n for n in resources.get("networks", []) if n["name"] == vlan),
        {"name": vlan, "type": "linux"},
    )
    emulator = resources.get("emulator", "/usr/bin/qemu-system-x86_64")

    # Early Linux validation so we fail before touching RBD.
    if os_family == "linux":
        try:
            cloud_init_service.format_ssh_keys(ssh_keys)
            user_for_cloud = (default_user or "").strip()
            if not user_for_cloud:
                return {"error": "default_user is required for Linux quick-create"}
            # Probe-render to catch empty key edge cases + invalid
            # password-hash format up front, before we touch RBD.
            cloud_init_service.render_userdata(
                name, user_for_cloud, ssh_keys, password_hash=password_hash,
            )
        except ValueError as e:
            return {"error": str(e)}

    vm_uuid       = str(uuid.uuid4())
    mac           = _random_mac()
    seed_iso_path = ""
    nvram_path    = ""
    cleanup_tmp: list[str] = []

    client = _ssh_connect(node)
    keyring_path = None
    try:
        auth_args, keyring_path = _get_rbd_auth(client, resources)

        # 1. Destination must not already exist.
        _, _, rc = _run(client, f"rbd {auth_args} info {pool}/{name} 2>/dev/null")
        if rc == 0:
            return {"error": f"RBD image '{pool}/{name}' already exists"}

        # 2. Make sure the golden snapshot is set up + protected.
        ok, err_msg = _ensure_golden_snap(client, auth_args, pool, src_image)
        if not ok:
            logger.error("Quick-create '%s' on %s: golden snapshot setup "
                         "failed for %s/%s: %s", name, node.get("hostname"),
                         pool, src_image, err_msg)
            return {"error": f"golden snapshot setup failed: {err_msg}"}

        # 3. COW clone (instant) + grow to requested size.
        logger.info(
            "rbd clone %s/%s@%s → %s/%s on node %d (%s)",
            pool, src_image, WVC_GOLDEN_SNAP, pool, name,
            node_id, node.get("hostname"),
        )
        out, err, rc = _run(
            client,
            f"rbd {auth_args} clone {pool}/{src_image}@{WVC_GOLDEN_SNAP} {pool}/{name} 2>&1",
            timeout=60,
        )
        if rc != 0:
            logger.error("Quick-create '%s' on %s (node_id=%s): rbd clone "
                         "%s/%s@%s → %s/%s failed (rc=%s): %s",
                         name, node.get("hostname"), node_id, pool, src_image,
                         WVC_GOLDEN_SNAP, pool, name, rc, (err or out).strip())
            return {"error": f"rbd clone failed: {err or out}"}

        out, err, rc = _run(
            client,
            f"rbd {auth_args} resize {pool}/{name} --size {disk_gb}G --allow-shrink 2>&1",
            timeout=60,
        )
        if rc != 0:
            logger.error("Quick-create '%s' on %s: rbd resize %s/%s to %sG "
                         "failed (rc=%s): %s", name, node.get("hostname"),
                         pool, name, disk_gb, rc, (err or out).strip())
            _run(client, f"rbd {auth_args} rm {pool}/{name} 2>/dev/null")
            return {"error": f"rbd resize failed: {err or out}"}

        # 4. OS-specific seed generation.
        if os_family == "linux":
            userdata = cloud_init_service.render_userdata(
                vmname=name, user=default_user, ssh_keys=ssh_keys,
                password_hash=password_hash,
            )
            netplan = cloud_init_service.render_network_config()

            tmp_userdata = f"/tmp/wvc_quick_{name}_userdata.yaml"
            tmp_netplan  = f"/tmp/wvc_quick_{name}_network.yaml"
            cleanup_tmp.extend([tmp_userdata, tmp_netplan])

            _write_file(client, tmp_userdata, userdata)
            _write_file(client, tmp_netplan, netplan)

            _, _, rc = _run(client, "command -v cloud-localds")
            if rc != 0:
                _run(client, f"rbd {auth_args} rm {pool}/{name} 2>/dev/null")
                return {"error": (
                    "cloud-localds not installed on this node — "
                    "install the cloud-image-utils (Debian/Ubuntu) or "
                    "cloud-utils (RHEL) package"
                )}

            _run(client, f"mkdir -p {SEED_ISO_DIR}")
            seed_iso_path = f"{SEED_ISO_DIR}/{name}.iso"
            out, err, rc = _run(
                client,
                f"cloud-localds --network-config={tmp_netplan} "
                f"{seed_iso_path} {tmp_userdata} 2>&1",
                timeout=30,
            )
            if rc != 0:
                _run(client, f"rbd {auth_args} rm {pool}/{name} 2>/dev/null")
                _run(client, f"rm -f {seed_iso_path}")
                return {"error": f"cloud-localds failed: {err or out}"}

        elif os_family == "windows":
            ovmf = _probe_ovmf(client)
            if ovmf is None:
                _run(client, f"rbd {auth_args} rm {pool}/{name} 2>/dev/null")
                return {"error": (
                    "OVMF firmware not found on node — install the "
                    "ovmf / edk2-ovmf package to enable Windows VMs"
                )}
            ovmf_code, ovmf_vars_template = ovmf
            nvram_path = f"{NVRAM_DIR}/{name}_VARS.fd"
            _run(client, f"mkdir -p {NVRAM_DIR}")
            out, err, rc = _run(
                client,
                f"cp -p {ovmf_vars_template} {nvram_path} 2>&1",
                timeout=30,
            )
            if rc != 0:
                _run(client, f"rbd {auth_args} rm {pool}/{name} 2>/dev/null")
                return {"error": f"NVRAM template copy failed: {err or out}"}

        # 5. Build XML — same disk + interface helpers as the other modes.
        disk_xml  = _build_disk_xml(pool, name, resources.get("disk_xml_template", ""))
        iface_xml = _build_interface_xml(vlan, net_info["type"], mac)

        if os_family == "windows":
            domain_xml = _build_windows_domain_xml(
                name=name, vm_uuid=vm_uuid, vcpus=vcpus, ram_mb=ram_mb,
                disk_xml=disk_xml, interface_xml=iface_xml,
                emulator=emulator,
                ovmf_code=ovmf_code, nvram_path=nvram_path,
            )
        else:
            cdrom_xml = _build_cdrom_xml(seed_iso_path)
            domain_xml = _build_domain_xml(
                name=name, vm_uuid=vm_uuid, vcpus=vcpus, ram_mb=ram_mb,
                disk_xml=disk_xml, interface_xml=iface_xml,
                emulator=emulator,
                cdrom_xml=cdrom_xml, iso_path=seed_iso_path,
            )

        xml_path = f"/tmp/wvc_quick_{name}.xml"
        cleanup_tmp.append(xml_path)
        _write_file(client, xml_path, domain_xml)

        # 6. Define + start. Roll back on either failure.
        out, err, rc = _run(client, f"virsh define {xml_path}")
        if rc != 0:
            logger.error("Quick-create '%s' on %s: virsh define failed "
                         "(rc=%s): %s", name, node.get("hostname"), rc,
                         (err or out).strip())
            _run(client, f"rbd {auth_args} rm {pool}/{name} 2>/dev/null")
            if seed_iso_path:
                _run(client, f"rm -f {seed_iso_path}")
            if nvram_path:
                _run(client, f"rm -f {nvram_path}")
            return {"error": f"virsh define failed: {err or out}"}

        out, err, rc = _run(client, f"virsh start '{name}'")
        if rc != 0:
            logger.error("Quick-create '%s' on %s: virsh start failed "
                         "(rc=%s): %s", name, node.get("hostname"), rc,
                         (err or out).strip())
            _run(client, f"virsh undefine '{name}' --nvram 2>/dev/null || virsh undefine '{name}' 2>/dev/null")
            _run(client, f"rbd {auth_args} rm {pool}/{name} 2>/dev/null")
            if seed_iso_path:
                _run(client, f"rm -f {seed_iso_path}")
            if nvram_path:
                _run(client, f"rm -f {nvram_path}")
            return {"error": f"virsh start failed: {err or out}"}
    finally:
        for tmp in cleanup_tmp:
            _run(client, f"rm -f {tmp}")
        if keyring_path:
            _run(client, f"rm -f {keyring_path}")
        client.close()

    vm_id = _insert_vm(
        node_id, name, vm_uuid, "running", vcpus, ram_mb,
        vlan, disk_gb, f"rbd:{pool}/{name}", pool,
        tenant_id=tenant_id, owner_user_id=owner_user_id,
        os_family=os_family,  # from catalog image (linux/windows)
    )
    logger.info(
        "Quick-created VM '%s' (id=%d, image=%s, os=%s) on node %d",
        name, vm_id, image_id, os_family, node_id,
    )

    # v0.14.2 — persist the pasted SSH keys against the creator so the
    # next Quick Create can pre-fill the textarea. Linux only (Windows
    # quick-create ignores ssh_keys). Best-effort: a DB hiccup here
    # mustn't fail the call — the VM is already up and the user has
    # what they came for. Future Quick Creates will just see an empty
    # pre-fill until the next successful save.
    if os_family == "linux" and owner_user_id and ssh_keys:
        try:
            from . import ssh_pubkey_service
            stats = ssh_pubkey_service.save_for_user(owner_user_id, ssh_keys)
            logger.info(
                "Saved SSH pubkeys for user_id=%d: inserted=%d touched=%d skipped=%d",
                owner_user_id,
                stats["inserted"], stats["touched"], stats["skipped"],
            )
        except Exception:
            logger.exception(
                "Failed to persist SSH pubkeys for user_id=%d after "
                "successful Quick Create of '%s' — VM is up, pre-fill "
                "may be stale next time",
                owner_user_id, name,
            )

    return {"ok": True, "id": vm_id, "name": name, "uuid": vm_uuid}


# ---------------------------------------------------------------------------
# Path browsing and RBD image listing (used by nodes API endpoints)
# ---------------------------------------------------------------------------

def list_path(node_id: int, path: str) -> dict:
    """
    List files and directories at path on a node via SSH.
    Returns {"path": str, "entries": [{"name", "is_dir", "size"}]}
    Hidden files (dot-prefixed) are excluded.
    """
    import posixpath
    path = posixpath.normpath(path or "/var/lib/libvirt/images")

    node = _get_node(node_id)
    if not node:
        return {"error": "Node not found", "path": path, "entries": []}
    if not node.get("private_key"):
        return {"error": "No SSH key configured", "path": path, "entries": []}

    script = (
        "import os, json\n"
        f"path = {json.dumps(path)}\n"
        "entries = []\n"
        "try:\n"
        "    it = sorted(os.scandir(path), key=lambda x: (not x.is_dir(), x.name.lower()))\n"
        "    for e in it:\n"
        "        if e.name.startswith('.'): continue\n"
        "        try:\n"
        "            sz = e.stat().st_size if e.is_file() else 0\n"
        "            entries.append({'name': e.name, 'is_dir': e.is_dir(), 'size': sz})\n"
        "        except OSError: pass\n"
        "except (PermissionError, FileNotFoundError) as exc:\n"
        "    print(json.dumps({'error': str(exc), 'path': path, 'entries': []})); raise SystemExit\n"
        "print(json.dumps({'path': path, 'entries': entries}))\n"
    )

    client = _ssh_connect(node)
    try:
        stdin, stdout, _stderr = client.exec_command("python3", timeout=15)
        stdin.write(script)
        stdin.channel.shutdown_write()
        raw = stdout.read().decode(errors="replace").strip()
        if not raw:
            return {"path": path, "entries": [], "error": "Empty response from node"}
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"error": f"Parse error: {exc}", "path": path, "entries": []}
    except Exception as exc:
        return {"error": str(exc), "path": path, "entries": []}
    finally:
        client.close()


def list_pool_volumes(node_id: int, pool: str) -> dict:
    """
    List volumes in a virsh storage pool via 'virsh vol-list'.
    Works for all pool types: rbd, dir, logical, fs.
    Returns {"pool": str, "volumes": [{"name": str, "path": str}]}
    """
    node = _get_node(node_id)
    if not node:
        return {"error": "Node not found", "pool": pool, "volumes": []}
    if not node.get("private_key"):
        return {"error": "No SSH key configured", "pool": pool, "volumes": []}

    client = _ssh_connect(node)
    try:
        out, err, rc = _run(client, f"virsh vol-list '{pool}' 2>&1")
        if rc != 0:
            return {"error": f"virsh vol-list failed: {(out or err).strip()}", "pool": pool, "volumes": []}
        volumes = []
        for line in out.splitlines()[2:]:   # skip the two header lines
            parts = line.split()
            if len(parts) >= 2:
                volumes.append({"name": parts[0], "path": parts[1]})
            elif len(parts) == 1:
                volumes.append({"name": parts[0], "path": parts[0]})
        return {"pool": pool, "volumes": volumes}
    except Exception as exc:
        return {"error": str(exc), "pool": pool, "volumes": []}
    finally:
        client.close()
