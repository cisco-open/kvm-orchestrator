"""HA VM relocation tasks."""

from ..extensions import celery


@celery.task(name="app.tasks.ha_relocate.relocate_node_vms")
def relocate_node_vms(failed_node_id: int):
    """Relocate all HA-enabled VMs from a failed node."""
    from ..services.ha_engine import relocate_vms

    relocate_vms(failed_node_id)
