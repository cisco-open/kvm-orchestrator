"""VM lifecycle expiry task (Celery Beat).

Runs the 1-year TTL grace machine once a day: warn owners approaching expiry,
auto-stop at expiry, auto-delete after the final grace. Gated by
LIFECYCLE_ENABLED (off by default) so it is a safe no-op until armed.
"""

from ..extensions import celery


@celery.task(name="app.tasks.vm_expiry.check_vm_expiry")
def check_vm_expiry():
    from ..services.lifecycle_engine import run_expiry_check

    return run_expiry_check()
