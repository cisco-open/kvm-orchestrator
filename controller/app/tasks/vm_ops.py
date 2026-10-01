"""Async VM operation tasks."""

from ..extensions import celery


@celery.task(name="app.tasks.vm_ops.vm_action")
def vm_action(vm_id: int, action: str):
    """Execute a VM action asynchronously (start/stop/reboot/destroy)."""
    from ..services.vm_service import send_vm_action

    return send_vm_action(vm_id, action)


@celery.task(name="app.tasks.vm_ops.vm_migrate")
def vm_migrate(vm_id: int, target_node_id: int):
    """Execute a live migration."""
    # TODO: Implement migration logic
    pass
