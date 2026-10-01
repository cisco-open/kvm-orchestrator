"""
Cloud-init seed-ISO content renderer.

Quick Create injects a NoCloud-style cloud-init seed ISO into Linux VMs.
The seed contains two files:

  user-data         the cloud-init configuration (this module's `render_userdata`)
  network-config    a tiny netplan stub that DHCPs the first NIC

We deliberately keep both renders pure-text helpers — building the ISO
itself is done on the KVM host with `cloud-localds`, which is what the
older self-service portal uses too.

v0.14.2 — render_userdata now defaults to also setting the user's CEC
password (SHA-512 crypt hash supplied by the API layer) so the resulting
VM accepts the same credentials the operator just logged in with. SSH
keys remain mandatory; the password is an addition, not a replacement —
key-based auth is still the recommended way in.
"""

from typing import Iterable
import os


def _domain_suffix() -> str:
    """
    Return the DNS domain suffix to append to guest hostnames.

    Reads the Flask config key ``VM_DOMAIN_SUFFIX`` when an app context is
    available, otherwise falls back to the ``VM_DOMAIN_SUFFIX`` env var
    (default ``example.local``). Blank means "hostname only, no suffix".
    """
    try:
        from flask import current_app
        if current_app:
            return (current_app.config.get("VM_DOMAIN_SUFFIX",
                    os.environ.get("VM_DOMAIN_SUFFIX", "example.local")) or "")
    except Exception:
        pass
    return os.environ.get("VM_DOMAIN_SUFFIX", "example.local") or ""


def _fqdn(vmname: str) -> str:
    """Build the guest FQDN from the configurable domain suffix."""
    suffix = _domain_suffix().strip().lstrip(".")
    return f"{vmname}.{suffix}" if suffix else vmname


def format_ssh_keys(keys: str | Iterable[str]) -> str:
    """
    Format one-or-many SSH public keys as the YAML list body that goes
    under `ssh-authorized-keys:` in cloud-init user-data.

    Accepts:
      * a single key string (with or without trailing whitespace)
      * a list/tuple of key strings
      * a single multi-line string with one key per line

    Returns a string ready to be substituted into the template — each
    key as `- <key>` joined with a leading `\\n      ` so the
    indentation lines up at 6 spaces under `ssh-authorized-keys:`.

    Empty input → empty string (cloud-init then skips the key install).
    """
    if not keys:
        return ""

    if isinstance(keys, str):
        # Accept newline-separated keys in a single textarea paste.
        # `\r` arrives from browsers on Windows; strip it out.
        candidates = [
            line.strip().replace("\r", "")
            for line in keys.splitlines()
        ]
    else:
        candidates = [
            (k or "").strip().replace("\r", "")
            for k in keys
        ]

    clean = [k for k in candidates if k]
    if not clean:
        return ""

    # 6 spaces of indent so the `-` aligns under `ssh-authorized-keys:`
    # which itself sits 4 spaces in under the user entry.
    return "\n      ".join(f"- {k}" for k in clean)


# ---------------------------------------------------------------------------
# user-data (cloud-config)
# ---------------------------------------------------------------------------
#
# Two variants of the template depending on whether the caller supplied
# a password hash:
#
#   * with password    →  lock_passwd: false + passwd: <hash> + ssh_pwauth: true.
#                         Cloud-init does NOT auto-expire passwords set
#                         this way (the chpasswd module is what expires
#                         them, and we don't invoke it). Login at the
#                         VNC console or over SSH-with-password both
#                         work; SSH-key remains the recommended path.
#   * without password →  lock_passwd: true + ssh_pwauth: false. Key-only.
#                         The original v0.14.0 posture, kept as a
#                         fallback for cases where the API layer has no
#                         password to hand us (e.g. Redis miss + the
#                         user explicitly declined to supply alternative
#                         credentials).

_USERDATA_TEMPLATE_WITH_PASSWORD = """\
#cloud-config
hostname: {vmname}
fqdn: {fqdn}
manage_etc_hosts: true

users:
  - name: {user}
    sudo: ALL=(ALL) NOPASSWD:ALL
    groups: adm,sys
    home: /home/{user}
    shell: /bin/bash
    lock_passwd: false
    passwd: {password_hash}
    ssh-authorized-keys:
      {ssh_pub_key}

ssh_pwauth: true
disable_root: false

package_update: true
packages:
  - qemu-guest-agent

runcmd:
  - [ sh, -c, 'umount /dev/sr0 || true' ]
  - [ systemctl, enable, --now, qemu-guest-agent ]

final_message: "The system is finally up, after $UPTIME seconds"
"""

_USERDATA_TEMPLATE_KEY_ONLY = """\
#cloud-config
hostname: {vmname}
fqdn: {fqdn}
manage_etc_hosts: true

users:
  - name: {user}
    sudo: ALL=(ALL) NOPASSWD:ALL
    groups: adm,sys
    home: /home/{user}
    shell: /bin/bash
    lock_passwd: true
    ssh-authorized-keys:
      {ssh_pub_key}

ssh_pwauth: false
disable_root: false

package_update: true
packages:
  - qemu-guest-agent

runcmd:
  - [ sh, -c, 'umount /dev/sr0 || true' ]
  - [ systemctl, enable, --now, qemu-guest-agent ]

final_message: "The system is finally up, after $UPTIME seconds"
"""


def render_userdata(vmname: str, user: str,
                    ssh_keys: str | Iterable[str],
                    password_hash: str | None = None) -> str:
    """
    Render the cloud-init user-data YAML for a Linux VM.

    Parameters:
      vmname         sets `hostname` (and `<vmname>.<VM_DOMAIN_SUFFIX>` FQDN).
      user           the default sudo user created in the VM. The API
                     layer typically passes the caller's CEC username.
      ssh_keys       string or iterable of OpenSSH public keys. See
                     `format_ssh_keys` for accepted shapes. **Required** —
                     a VM with neither password nor keys would be
                     unreachable, and we don't trust password-only.
      password_hash  optional SHA-512 crypt(3) hash (the `$6$<salt>$...`
                     format produced by `auth_service.hash_password`).
                     When supplied, the resulting VM accepts both SSH
                     keys AND the corresponding password. When None,
                     the VM is key-only (the v0.14.0 default).

    Raises ValueError if vmname/user is blank or no SSH keys are
    provided. If a password_hash is supplied it must already be in
    crypt(3) format — we do NOT accept plaintext here; that's a
    deliberate API to keep plaintext from leaking into the seed ISO.
    """
    vmname = (vmname or "").strip()
    user   = (user or "").strip()
    if not vmname:
        raise ValueError("vmname is required for cloud-init render")
    if not user:
        raise ValueError("user is required for cloud-init render")

    ssh_block = format_ssh_keys(ssh_keys)
    if not ssh_block:
        raise ValueError(
            "At least one SSH public key is required — VMs are "
            "configured for key-based auth (password is an addition, "
            "not a replacement)."
        )

    if password_hash:
        ph = password_hash.strip()
        if not ph.startswith("$"):
            # crypt(3) output always begins with `$<id>$` (e.g. `$6$`).
            # If the caller hands us something that doesn't, it's almost
            # certainly plaintext — refuse rather than risk writing
            # plaintext into a seed ISO that ends up on the KVM host.
            raise ValueError(
                "password_hash must be in crypt(3) format (e.g. '$6$...') — "
                "use auth_service.hash_password() to compute it"
            )
        return _USERDATA_TEMPLATE_WITH_PASSWORD.format(
            vmname=vmname,
            fqdn=_fqdn(vmname),
            user=user,
            ssh_pub_key=ssh_block,
            password_hash=ph,
        )

    return _USERDATA_TEMPLATE_KEY_ONLY.format(
        vmname=vmname,
        fqdn=_fqdn(vmname),
        user=user,
        ssh_pub_key=ssh_block,
    )


# ---------------------------------------------------------------------------
# network-config (netplan v2)
# ---------------------------------------------------------------------------

# We use a netplan `match: name: "en*"` rather than a hardcoded interface
# name. Linux Quick Create VMs default to libvirt's `pc-i440fx` machine
# type, where Ubuntu's predictable interface naming yields `enp0s3`
# (PCI bus 0, slot 3). On q35 the same NIC ends up as `enp1s0`. A
# wildcard match catches either — and stays robust to any future
# machine-type change.
# `dhcp-identifier: mac` makes the lease stable across rebuilds with the
# same MAC (matters when DHCP reservations have been made by hand).
_NETWORK_CONFIG = """\
version: 2
ethernets:
  primary:
    match:
      name: "en*"
    dhcp4: true
    dhcp-identifier: mac
"""


def render_network_config() -> str:
    """Return the netplan stub that goes into the seed ISO as `network-config`."""
    return _NETWORK_CONFIG
