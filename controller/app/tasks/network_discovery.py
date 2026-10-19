"""Network auto-discovery task (Celery Beat).

Periodically scans online nodes for OVS VLAN bridges and adds any missing
ones to the networks catalog (hidden, pending admin review). ADD-only — it
never modifies or deletes existing catalog rows.
"""

from ..extensions import celery


@celery.task(name="app.tasks.network_discovery.discover_networks")
def discover_networks():
    """Periodic task: import newly-provisioned VLAN bridges into the catalog."""
    from ..services.network_service import discover_networks as _discover

    return _discover()
