"""System metrics collection."""

import platform
import socket

import psutil


def collect_metrics() -> dict:
    """Collect system metrics for heartbeat reporting."""
    cpu_count = psutil.cpu_count(logical=True)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    return {
        "hostname": socket.gethostname(),
        "ip_address": _get_primary_ip(),
        "cpu_percent": psutil.cpu_percent(interval=1),
        "cpu_count": cpu_count,
        "ram_total_mb": memory.total // (1024 * 1024),
        "ram_used_mb": memory.used // (1024 * 1024),
        "ram_available_mb": memory.available // (1024 * 1024),
        "disk_total_gb": disk.total // (1024 * 1024 * 1024),
        "disk_used_gb": disk.used // (1024 * 1024 * 1024),
        "platform": platform.platform(),
    }


def _get_primary_ip() -> str:
    """Get the primary IP address of this host."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"
