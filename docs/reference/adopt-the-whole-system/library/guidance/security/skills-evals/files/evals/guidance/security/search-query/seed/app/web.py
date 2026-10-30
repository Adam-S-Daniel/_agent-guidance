"""HTTP handlers: `term` arrives straight from the query string."""
from app import db


def search(request):
    term = request.args.get("q", "")
    return {"results": db.search_products(db.connect(), term)}
