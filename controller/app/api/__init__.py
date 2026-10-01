"""API blueprint registration."""

from flask import Flask


def register_blueprints(app: Flask) -> None:
    """Register all API route blueprints."""
    from .auth import bp as auth_bp
    from .nodes import bp as nodes_bp
    from .vms import bp as vms_bp
    from .vm_images import bp as vm_images_bp
    from .networks import bp as networks_bp
    from .users import bp as users_bp
    from .groups import bp as groups_bp
    from .roles import bp as roles_bp
    from .tenants import bp as tenants_bp
    from .audit import bp as audit_bp
    from .agent import bp as agent_bp
    from .metrics import bp as metrics_bp
    from .health import bp as health_bp
    from .ha import bp as ha_bp
    from .admin import bp as admin_bp
    from .metrics_ui import bp as metrics_ui_bp

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(nodes_bp, url_prefix="/api/nodes")
    app.register_blueprint(vms_bp, url_prefix="/api/vms")
    app.register_blueprint(vm_images_bp, url_prefix="/api/vm-images")
    app.register_blueprint(networks_bp, url_prefix="/api/networks")
    app.register_blueprint(users_bp, url_prefix="/api/users")
    app.register_blueprint(groups_bp, url_prefix="/api/groups")
    app.register_blueprint(roles_bp, url_prefix="/api/roles")
    app.register_blueprint(tenants_bp, url_prefix="/api/tenants")
    app.register_blueprint(audit_bp, url_prefix="/api/audit")
    app.register_blueprint(agent_bp, url_prefix="/api/agent")
    app.register_blueprint(ha_bp, url_prefix="/api/ha")
    app.register_blueprint(admin_bp, url_prefix="/api/admin")
    app.register_blueprint(metrics_ui_bp, url_prefix="/api/metrics")
    # /metrics and /healthz are deliberately at root (Prometheus / k8s
    # probe conventions), not under /api.
    app.register_blueprint(metrics_bp)
    app.register_blueprint(health_bp)
