"""Custom error handlers."""

from flask import Flask, jsonify


class APIError(Exception):
    """Base API error."""

    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code


class NotFoundError(APIError):
    def __init__(self, message: str = "Resource not found"):
        super().__init__(message, 404)


class ForbiddenError(APIError):
    def __init__(self, message: str = "Forbidden"):
        super().__init__(message, 403)


def register_error_handlers(app: Flask) -> None:
    """Register custom error handlers on the Flask app."""

    @app.errorhandler(APIError)
    def handle_api_error(error):
        return jsonify({"error": error.message}), error.status_code

    @app.errorhandler(404)
    def handle_404(error):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(500)
    def handle_500(error):
        return jsonify({"error": "Internal server error"}), 500
