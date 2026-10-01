"""HA failover notifications (Webex).

Notify-only: posts a card summarising a failover (fence + relocate outcome).
Failure to notify never affects the failover. When ENVIRONMENT=test the card is
prefixed [***TEST***].
"""

import logging

import requests
from flask import current_app

logger = logging.getLogger("wvc.ha-notify")

_API = "https://webexapis.com/v1/messages"


def notify_failover(dead_hostname: str, results: dict) -> None:
    cfg = current_app.config
    token = cfg.get("WEBEX_BOT_TOKEN")
    room = cfg.get("WEBEX_HA_ROOM_ID")
    if not token or not room:
        logger.info("Webex not configured — skipping failover notification")
        return

    prefix = "[***TEST***] " if cfg.get("ENVIRONMENT", "live") == "test" else ""
    affected = results.get("affected", [])
    left_down = results.get("left_down", [])
    base = (cfg.get("WEBEX_DEEP_LINK_BASE", "") or "").rstrip("/")

    lines = [f"{prefix}⚠️ **HA: host down — {dead_hostname}**"]
    lines.append(f"- Host **fenced** (storage isolated): {'yes' if results.get('fenced') else 'NO'}")
    if affected:
        lines.append(f"- **{len(affected)} VM(s) need migration by Ops** "
                     "(the host is fenced; migrate each via the UI — it will pick "
                     "an architecture-compatible target):")
        for a in affected:
            missing_xml = "" if a.get("has_xml", True) else " — ⚠️ no stored XML"
            lines.append(f"    • **{a['vm']}** ({a.get('state','?')}){missing_xml}")
    else:
        lines.append("- No running VMs to migrate.")
    if left_down:
        lines.append(f"- {len(left_down)} VM(s) left down (were not running).")
    if base:
        lines.append(f"\n[Open {'KVMaestro (test)' if prefix else 'KVMaestro'}]({base}/vms) → migrate the affected VMs.")
    lines.append("\n_When the host is recovered, use Readmit before bringing VMs back._")

    try:
        resp = requests.post(
            _API,
            headers={"Authorization": f"Bearer {token}"},
            json={"roomId": room, "markdown": "\n".join(lines)},
            timeout=10,
        )
        if resp.status_code // 100 != 2:
            logger.warning("Webex post failed %s: %s", resp.status_code, resp.text[:200])
        else:
            logger.info("HA failover Webex notification sent for %s", dead_hostname)
    except Exception:
        logger.exception("Webex notify error (non-fatal)")


def notify_partition(hostname: str, probe: dict) -> None:
    """Alert Ops that a node looks unreachable but is STILL writing Ceph — a
    suspected management-network partition. HA did NOT fence (would have crashed
    healthy VMs). Human should investigate the network."""
    cfg = current_app.config
    token = cfg.get("WEBEX_BOT_TOKEN")
    room = cfg.get("WEBEX_HA_ROOM_ID")
    if not token or not room:
        logger.info("Webex not configured — skipping partition notification")
        return
    prefix = "[***TEST***] " if cfg.get("ENVIRONMENT", "live") == "test" else ""
    imgs = probe.get("images_watched", [])
    md = (
        f"{prefix}⚠️ **HA: suspected NETWORK PARTITION — {hostname}**\n\n"
        f"- Node is unreachable on the management network (stale heartbeat) "
        f"**but is STILL writing Ceph** ({len(imgs)} image(s) still watched).\n"
        f"- HA did **NOT** fence it — its VMs are still running. Fencing would "
        f"have crashed them.\n"
        f"- **Action:** investigate the management-network path to {hostname}. "
        f"Do not migrate its VMs — they are alive.\n"
    )
    try:
        resp = requests.post(
            _API, headers={"Authorization": f"Bearer {token}"},
            json={"roomId": room, "markdown": md}, timeout=10)
        if resp.status_code // 100 != 2:
            logger.warning("Webex partition post failed %s: %s",
                           resp.status_code, resp.text[:200])
    except Exception:
        logger.exception("Webex partition notify error")


def notify_node_failed(hostname: str, node_id: int) -> None:
    """Alert Ops that a node is confirmed dead and needs an admin to FENCE it
    (the engine does not auto-fence). Until fenced, its VMs cannot be recovered."""
    cfg = current_app.config
    token = cfg.get("WEBEX_BOT_TOKEN")
    room = cfg.get("WEBEX_HA_ROOM_ID")
    if not token or not room:
        logger.info("Webex not configured — skipping node-failed notification")
        return
    prefix = "[***TEST***] " if cfg.get("ENVIRONMENT", "live") == "test" else ""
    base = (cfg.get("WEBEX_DEEP_LINK_BASE", "") or "").rstrip("/")
    link = f"{base}/ha" if base else ""
    md = (
        f"{prefix}🔴 **HA: node FAILED — {hostname}**\n\n"
        f"- Confirmed dead (unreachable + not writing Ceph).\n"
        f"- **Action required:** an admin must **Fence** the node on the HA page "
        f"before its VMs can be recovered. HA does not fence automatically.\n"
    )
    if link:
        md += f"\n[Open HA page]({link}) → Fence {hostname}, then Recover its VMs."
    try:
        resp = requests.post(
            _API, headers={"Authorization": f"Bearer {token}"},
            json={"roomId": room, "markdown": md}, timeout=10)
        if resp.status_code // 100 != 2:
            logger.warning("Webex node-failed post failed %s: %s",
                           resp.status_code, resp.text[:200])
    except Exception:
        logger.exception("Webex node-failed notify error")
