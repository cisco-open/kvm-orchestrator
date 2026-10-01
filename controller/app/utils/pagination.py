"""Pagination helpers."""


def paginate(query, page: int = 1, per_page: int = 50) -> dict:
    """Apply pagination to a SQLAlchemy query and return structured result."""
    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    return {
        "items": items,
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": (total + per_page - 1) // per_page,
    }
