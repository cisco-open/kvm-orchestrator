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
Background node monitor.

Runs as a daemon thread inside each gunicorn worker.  Every POLL_INTERVAL
seconds it SSH-polls every registered node, collects basic metrics, and
updates the nodes table.  This makes nodes go online immediately without
requiring a separate agent process on each KVM host.

When the real kvm-orchestrator-agent is deployed the agent's heartbeats (via
/api/agent/heartbeat) will take over; this monitor acts as a fallback and
will no longer drive status once the agent sends its first heartbeat.
"""

import io
import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from urllib.parse import urlparse

import paramiko
import pymysql
import pymysql.cursors
import redis as redis_lib

from ..metrics import (
    node_monitor_iterations_total,
    track_node_poll,
)

# ---------------------------------------------------------------------------
# CPU model parsing
# ---------------------------------------------------------------------------

# Intel Xeon Scalable: 2nd digit of the 4-digit model number = generation
_INTEL_GEN_MAP = {
    "1": "Skylake",
    "2": "Cascade Lake",
    "3": "Ice Lake",
    "4": "Sapphire Rapids",
    "5": "Emerald Rapids",
}

# AMD EPYC: last digit of model number = generation (7xxx line)
_AMD_7_GEN_MAP = {"1": "Naples", "2": "Rome", "3": "Milan"}
# 9xxx line: last digit
_AMD_9_GEN_MAP = {"4": "Genoa", "5": "Turin"}


def _detect_microarch(model: str) -> str:
    """Return microarchitecture name (e.g. 'Sapphire Rapids') or empty string."""
    u = model.upper()

    if "INTEL" in u and "XEON" in u:
        # Classic Xeon: E5-26xx v3 (Haswell), E5-26xx v4 (Broadwell), etc.
        v = re.search(r"E[357]-\d{4}\s+V(\d)", u)
        if v:
            return {"1": "Sandy Bridge", "2": "Ivy Bridge",
                    "3": "Haswell", "4": "Broadwell"}.get(v.group(1), "")
        # Xeon Scalable: 4-digit number, 2nd digit encodes generation
        sc = re.search(r"\b[3-9](\d)\d{2}[A-Z+]*\b", u)
        if sc:
            return _INTEL_GEN_MAP.get(sc.group(1), "")
        if re.search(r"XEON\s+6\b", u):
            return "Granite Rapids"

    if "EPYC" in u:
        ep = re.search(r"EPYC\s+([79])(\d{2})(\d)", u)
        if ep:
            prefix, last = ep.group(1), ep.group(3)
            if prefix == "7":
                return _AMD_7_GEN_MAP.get(last, "")
            return _AMD_9_GEN_MAP.get(last, "Genoa")

    return ""


def _format_cpu_model(raw: str) -> str:
    """
    Clean a raw /proc/cpuinfo model name and append the microarchitecture.

    'Intel(R) Xeon(R) Platinum 8480+ CPU @ 3.80GHz'
     -> 'Intel Xeon Platinum 8480+ (Sapphire Rapids)'
    'INTEL XEON GOLD 6542Y'
     -> 'Intel Xeon Gold 6542Y (Emerald Rapids)'
    'Intel Xeon CPU E5-2690 v3 @ 2.60GHz'
     -> 'Intel Xeon E5-2690 v3 (Haswell)'
    """
    s = raw.strip()
    # Some BIOSes report in ALL CAPS — convert to title case
    if s == s.upper():
        s = s.title()
    s = re.sub(r"\(R\)|\(TM\)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\bCPU\b", "", s)                      # remove bare "CPU" word
    s = re.sub(r"\s+@\s+[\d.]+\s*GHz", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()

    arch = _detect_microarch(s)
    return f"{s} ({arch})" if arch else s

logger = logging.getLogger("wvc.node-monitor")

_NODE_DOWN_THRESHOLD = int(os.environ.get("NODE_DOWN_THRESHOLD", "60"))
_POLL_INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", "30"))

# ---------------------------------------------------------------------------
# Redis time-series for node CPU/RAM samples
# ---------------------------------------------------------------------------
# Key:    node_metrics:<node_id>          (ZSET, score=unix_ts, member=JSON)
# Lock:   node_metrics_lock:<node_id>     (1 writer per poll cycle)
# Window: kept = METRIC_WINDOW_SECONDS    (older entries trimmed each write)

METRIC_WINDOW_SECONDS = int(os.environ.get("NODE_METRIC_WINDOW", "3600"))


def _redis() -> "redis_lib.Redis":
    return redis_lib.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )


def _record_node_sample(node_id: int, metrics: dict) -> None:
    """Append one (cpu_pct, ram_used_mb, ram_total_mb) sample to Redis.

    Uses a short-lived NX lock so that only one gunicorn worker writes
    per poll cycle; the others skip silently.  Drops samples older than
    METRIC_WINDOW_SECONDS to bound memory.
    """
    try:
        r = _redis()
        lock_ttl = max(_POLL_INTERVAL - 5, 5)
        if not r.set(f"node_metrics_lock:{node_id}", "1", nx=True, ex=lock_ttl):
            return  # another worker is recording this cycle
        now = int(time.time())
        sample = json.dumps({
            "t":            now,
            "cpu":          metrics.get("cpu_pct", 0.0),
            "ram_used_mb":  metrics.get("ram_used_mb", 0),
            "ram_total_mb": metrics.get("total_ram_mb", 0),
        })
        key = f"node_metrics:{node_id}"
        pipe = r.pipeline()
        pipe.zadd(key, {sample: now})
        pipe.zremrangebyscore(key, 0, now - METRIC_WINDOW_SECONDS)
        pipe.expire(key, METRIC_WINDOW_SECONDS * 2)
        pipe.execute()
    except Exception as exc:
        logger.warning("Failed to record metric sample for node %d: %s", node_id, exc)


def get_node_metrics(node_id: int, window_seconds: int | None = None) -> list[dict]:
    """Return time-series samples for a node within the window."""
    try:
        r = _redis()
        now = int(time.time())
        cutoff = now - (window_seconds or METRIC_WINDOW_SECONDS)
        raw = r.zrangebyscore(f"node_metrics:{node_id}", cutoff, now)
        out = []
        for item in raw:
            try:
                out.append(json.loads(item))
            except (ValueError, TypeError):
                continue
        return out
    except Exception as exc:
        logger.warning("Failed to fetch metric samples for node %d: %s", node_id, exc)
        return []


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------

def _load_key(key_str: str) -> paramiko.PKey:
    """Auto-detect and load an SSH private key from a string."""
    key_classes = [
        paramiko.Ed25519Key,
        paramiko.RSAKey,
        paramiko.ECDSAKey,
        paramiko.DSSKey,
    ]
    last_exc = None
    for cls in key_classes:
        try:
            return cls.from_private_key(io.StringIO(key_str))
        except Exception as e:
            last_exc = e
    raise ValueError(f"Unsupported private key format: {last_exc}")


@track_node_poll
def _poll_node(node: dict) -> dict | None:
    """
    Open an SSH connection to the node and collect metrics.
    Returns a metrics dict on success, None on failure.
    """
    private_key_str = node.get("private_key")
    if not private_key_str:
        logger.warning("Node %s: no SSH key configured", node["hostname"])
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
        client.connect(
            hostname=host,
            port=port,
            username=user,
            pkey=pkey,
            timeout=10,
            look_for_keys=False,
            allow_agent=False,
        )

        # One round-trip: nproc + free + df + cpu model + cpu% (0.5s delta)
        cmd = (
            "echo cpu=$(nproc); "
            "free -m | awk '/^Mem:/{printf \"ram_total=%s ram_avail=%s\\n\",$2,$7}'; "
            "df -BG / | awk 'NR==2{gsub(/G/,\"\",$2); gsub(/G/,\"\",$4); "
            "printf \"disk_total=%s disk_avail=%s\\n\",$2,$4}'; "
            "grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | sed 's/^ //'; "
            "T1=$(awk '/^cpu /{i=$5;t=0;for(j=2;j<=8;j++)t+=$j;print t,i}' /proc/stat); "
            "sleep 0.5; "
            "T2=$(awk '/^cpu /{i=$5;t=0;for(j=2;j<=8;j++)t+=$j;print t,i}' /proc/stat); "
            "echo \"$T1 $T2\" | awk '{tot=$3-$1;idle=$4-$2;"
            "if(tot>0)printf \"cpu_pct=%.1f\\n\",100*(1-idle/tot);else print \"cpu_pct=0\"}'; "
            # Hypervisor stack versions (best-effort; blank if not installed).
            # Prefixed with tag= so the parser can pick them out without
            # colliding with the model-name line.
            "echo hv_kernel=$(uname -r); "
            "echo hv_libvirt=$(virsh --version 2>/dev/null); "
            "echo hv_qemu=$(qemu-system-x86_64 --version 2>/dev/null "
            "| awk 'NR==1{for(i=1;i<=NF;i++)if($i ~ /^[0-9]+\\.[0-9]/){print $i;exit}}'); "
            "echo hv_kvm=$(modinfo -F version kvm 2>/dev/null)"
        )
        _, stdout, _ = client.exec_command(cmd, timeout=15)
        raw = stdout.read().decode()

        lines = raw.strip().splitlines()
        parsed: dict[str, float] = {}
        strings: dict[str, str] = {}
        cpu_model_raw = ""
        for line in lines:
            line = line.strip()
            for token in line.split():
                if "=" in token:
                    k, v = token.split("=", 1)
                    # hv_* are version strings, keep verbatim
                    if k.startswith("hv_"):
                        strings[k] = v
                        continue
                    try:
                        parsed[k] = float(v)
                    except ValueError:
                        pass
            # CPU model line has no '=' tokens — it's just the model name string
            if line and "=" not in line:
                cpu_model_raw = line

        cpu_model = _format_cpu_model(cpu_model_raw) if cpu_model_raw else None

        total_ram = int(parsed.get("ram_total", 0))
        avail_ram = int(parsed.get("ram_avail", 0))
        return {
            "status": "online",
            "total_vcpus":      int(parsed.get("cpu", 0)),
            "available_vcpus":  int(parsed.get("cpu", 0)),
            "total_ram_mb":     total_ram,
            "available_ram_mb": avail_ram,
            "total_disk_gb":    int(parsed.get("disk_total", 0)),
            "available_disk_gb": int(parsed.get("disk_avail", 0)),
            "cpu_model":        cpu_model,
            "cpu_pct":          round(parsed.get("cpu_pct", 0.0), 1),
            "ram_used_mb":      max(total_ram - avail_ram, 0),
            "kernel_version":   strings.get("hv_kernel") or None,
            "libvirt_version":  strings.get("hv_libvirt") or None,
            "qemu_version":     strings.get("hv_qemu") or None,
            "kvm_version":      strings.get("hv_kvm") or None,
        }

    except Exception as e:
        logger.warning("Node %s (%s): SSH poll failed — %s", node["hostname"], host, e)
        return None
    finally:
        client.close()


# ---------------------------------------------------------------------------
# DB update
# ---------------------------------------------------------------------------

def update_node_status(node_id: int, metrics: dict | None) -> None:
    """
    Write poll result to the nodes table.
    - On success: update status + metrics + last_heartbeat_at.
    - On failure: mark offline only if last_heartbeat_at is stale (avoids
      flapping on a single transient SSH error).
    """
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            if metrics:
                # Always refresh heartbeat + metrics for a reachable node — even
                # one that is 'fenced'/'failed' — so the DB reflects that the host
                # is alive again. But do NOT overwrite a 'fenced'/'failed' STATUS
                # here: those are deliberate HA states cleared only by readmit.
                # (Previously the whole UPDATE was gated on status NOT IN
                # (fenced,failed), which starved a recovered host of heartbeat
                # updates → it could never leave 'failed' and readmit couldn't
                # stick because the engine re-failed it on the next cycle.)
                cur.execute(
                    """
                    UPDATE nodes SET
                        status = CASE
                                   WHEN status IN ('fenced', 'failed') THEN status
                                   ELSE %s
                                 END,
                        total_vcpus       = %s,
                        available_vcpus   = %s,
                        total_ram_mb      = %s,
                        available_ram_mb  = %s,
                        total_disk_gb     = %s,
                        available_disk_gb = %s,
                        cpu_model         = COALESCE(%s, cpu_model),
                        kernel_version    = COALESCE(%s, kernel_version),
                        libvirt_version   = COALESCE(%s, libvirt_version),
                        qemu_version      = COALESCE(%s, qemu_version),
                        kvm_version       = COALESCE(%s, kvm_version),
                        last_heartbeat_at = %s
                    WHERE id = %s
                    """,
                    (
                        metrics["status"],
                        metrics["total_vcpus"],
                        metrics["available_vcpus"],
                        metrics["total_ram_mb"],
                        metrics["available_ram_mb"],
                        metrics["total_disk_gb"],
                        metrics["available_disk_gb"],
                        metrics.get("cpu_model"),
                        metrics.get("kernel_version"),
                        metrics.get("libvirt_version"),
                        metrics.get("qemu_version"),
                        metrics.get("kvm_version"),
                        datetime.utcnow(),
                        node_id,
                    ),
                )
            else:
                cur.execute(
                    """
                    UPDATE nodes SET status = 'offline'
                    WHERE id = %s
                      AND status NOT IN ('fenced', 'failed')
                      AND (last_heartbeat_at IS NULL
                           OR last_heartbeat_at < NOW() - INTERVAL %s SECOND)
                    """,
                    (node_id, _NODE_DOWN_THRESHOLD),
                )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Monitor loop
# ---------------------------------------------------------------------------

def _monitor_loop() -> None:
    logger.info("Node monitor started (poll interval %ds)", _POLL_INTERVAL)
    while True:
        outcome = "ok"
        try:
            conn = _get_db()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT n.id, n.hostname, n.ip_address, n.ssh_host,
                               n.ssh_port, n.ssh_user, k.private_key
                        FROM nodes n
                        LEFT JOIN ssh_keys k ON k.id = n.ssh_key_id
                        """
                    )
                    nodes = cur.fetchall()
            finally:
                conn.close()

            for node in nodes:
                # Single-flight the SSH poll across ALL gunicorn workers / api
                # replicas. Without this, every worker (2 pods x 4 workers = 8)
                # opens its own SSH connection to every node each cycle, which
                # can overwhelm a node's sshd (MaxStartups) → "protocol banner"
                # / "opening channel" timeouts → false stale-heartbeat → HA
                # partition alerts. An NX lock means exactly one worker polls a
                # given node per cycle; the rest skip it. Fail OPEN on any Redis
                # error so a Redis blip can never starve heartbeats.
                got_lock = True
                try:
                    _r = _redis()
                    # TTL must exceed the worst-case SSH time (connect 10s +
                    # exec 15s = 25s) so a slow/hung poll can't have its lock
                    # expire and let a second worker start a concurrent poll —
                    # that concurrency is exactly what overwhelmed sshd. It's
                    # fine for the lock to outlive a fast poll: workers sleep
                    # _POLL_INTERVAL between cycles and aren't synchronized, so
                    # the node still gets polled ~once per interval.
                    _ttl = 28
                    got_lock = bool(_r.set(f"node_poll_lock:{node['id']}", "1",
                                           nx=True, ex=_ttl))
                except Exception:
                    got_lock = True  # Redis unavailable → don't block polling
                if not got_lock:
                    continue  # another worker is polling this node this cycle

                metrics = _poll_node(node)
                update_node_status(node["id"], metrics)
                # Release the poll lock after a SUCCESSFUL poll so the next
                # cycle can re-poll at the normal interval (fresh heartbeats).
                # If the poll FAILED/hung, leave the lock to expire on its own
                # (28s) — that backs off retries against a struggling sshd
                # instead of piling on, which is what caused this incident.
                if metrics:
                    try:
                        _redis().delete(f"node_poll_lock:{node['id']}")
                    except Exception:
                        pass
                if metrics:
                    _record_node_sample(node["id"], metrics)
                    logger.debug("Node %s: online (%d vCPU, %d MB RAM, %.1f%% CPU)",
                                 node["hostname"],
                                 metrics["total_vcpus"],
                                 metrics["total_ram_mb"],
                                 metrics["cpu_pct"])

        except Exception as e:
            outcome = "error"
            logger.error("Monitor loop error: %s", e, exc_info=True)

        node_monitor_iterations_total.labels(result=outcome).inc()
        time.sleep(_POLL_INTERVAL)


def start_monitor() -> None:
    """Start the SSH-poll monitor in a background daemon thread."""
    t = threading.Thread(target=_monitor_loop, daemon=True, name="node-monitor")
    t.start()
    logger.info("Node monitor thread launched")
