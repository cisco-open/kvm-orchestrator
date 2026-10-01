"""RBAC permission checking middleware."""

from functools import wraps

from flask import jsonify
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request


def require_permission(resource_type: str, action: str):
    """
    Decorator factory: require the current user to have a specific permission.

    Usage:
        @require_permission("vm", "create")
        def create_vm():
            ...
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request()
            identity = get_jwt_identity()
            # TODO: Check user roles -> permissions for resource_type + action
            # For now, pass through
            return fn(*args, **kwargs)

        return wrapper

    return decorator
