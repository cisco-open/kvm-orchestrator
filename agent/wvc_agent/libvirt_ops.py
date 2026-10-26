"""Libvirt operations — all interaction with the libvirt daemon."""

import logging
import xml.etree.ElementTree as ET

import libvirt

logger = logging.getLogger("wvc-agent.libvirt")

WVC_NAMESPACE = "https://kvm-orchestrator.local/xmlns/instance/1.0"
MANAGED_BY_TAG = "kvm-orchestrator"


def _get_connection() -> libvirt.virConnect:
    """Open a connection to the local libvirt daemon."""
    conn = libvirt.open("qemu:///system")
    if conn is None:
        raise RuntimeError("Failed to connect to libvirt")
    return conn


def _is_managed(domain: libvirt.virDomain) -> bool:
    """Check if a domain is managed by kvm-orchestrator (has our metadata tag)."""
    try:
        xml_str = domain.XMLDesc()
        root = ET.fromstring(xml_str)
        metadata = root.find("metadata")
        if metadata is not None:
            instance = metadata.find(f"{{{WVC_NAMESPACE}}}instance")
            if instance is not None:
                managed_by = instance.find(f"{{{WVC_NAMESPACE}}}managed-by")
                if managed_by is not None and managed_by.text == MANAGED_BY_TAG:
                    return True
    except Exception:
        pass
    return False


def list_managed_vms() -> list[dict]:
    """List all VMs managed by kvm-orchestrator on this node."""
    conn = _get_connection()
    vms = []
    try:
        for domain in conn.listAllDomains():
            if _is_managed(domain):
                state, _ = domain.state()
                state_names = {
                    libvirt.VIR_DOMAIN_RUNNING: "running",
                    libvirt.VIR_DOMAIN_PAUSED: "paused",
                    libvirt.VIR_DOMAIN_SHUTOFF: "stopped",
                    libvirt.VIR_DOMAIN_CRASHED: "error",
                }
                vms.append({
                    "name": domain.name(),
                    "uuid": domain.UUIDString(),
                    "state": state_names.get(state, "unknown"),
                })
    finally:
        conn.close()
    return vms


def define_vm(xml: str, vm_id: str) -> dict:
    """Define a new VM from XML definition."""
    conn = _get_connection()
    try:
        # Inject managed-by metadata
        xml = _inject_metadata(xml, vm_id)
        domain = conn.defineXML(xml)
        if domain is None:
            return {"status": "error", "message": "Failed to define domain"}
        return {"status": "ok", "name": domain.name(), "uuid": domain.UUIDString()}
    finally:
        conn.close()


def start_vm(name: str) -> dict:
    """Start a VM by name."""
    conn = _get_connection()
    try:
        domain = conn.lookupByName(name)
        if not _is_managed(domain):
            return {"status": "error", "message": "VM not managed by kvm-orchestrator"}
        domain.create()
        return {"status": "ok"}
    except libvirt.libvirtError as e:
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


def stop_vm(name: str) -> dict:
    """Gracefully shut down a VM."""
    conn = _get_connection()
    try:
        domain = conn.lookupByName(name)
        if not _is_managed(domain):
            return {"status": "error", "message": "VM not managed by kvm-orchestrator"}
        domain.shutdown()
        return {"status": "ok"}
    except libvirt.libvirtError as e:
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


def destroy_vm(name: str) -> dict:
    """Force-stop a VM."""
    conn = _get_connection()
    try:
        domain = conn.lookupByName(name)
        if not _is_managed(domain):
            return {"status": "error", "message": "VM not managed by kvm-orchestrator"}
        domain.destroy()
        return {"status": "ok"}
    except libvirt.libvirtError as e:
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


def reboot_vm(name: str) -> dict:
    """Reboot a VM."""
    conn = _get_connection()
    try:
        domain = conn.lookupByName(name)
        if not _is_managed(domain):
            return {"status": "error", "message": "VM not managed by kvm-orchestrator"}
        domain.reboot()
        return {"status": "ok"}
    except libvirt.libvirtError as e:
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


def _inject_metadata(xml: str, vm_id: str) -> str:
    """Inject kvm-orchestrator management metadata into domain XML."""
    root = ET.fromstring(xml)
    metadata = root.find("metadata")
    if metadata is None:
        metadata = ET.SubElement(root, "metadata")

    ET.register_namespace("wvc", WVC_NAMESPACE)
    instance = ET.SubElement(metadata, f"{{{WVC_NAMESPACE}}}instance")
    managed_by = ET.SubElement(instance, f"{{{WVC_NAMESPACE}}}managed-by")
    managed_by.text = MANAGED_BY_TAG
    vm_id_elem = ET.SubElement(instance, f"{{{WVC_NAMESPACE}}}vm-id")
    vm_id_elem.text = vm_id

    return ET.tostring(root, encoding="unicode")
