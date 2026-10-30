"""Turn a title into a URL slug."""
import re


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower())
    return slug.lstrip("-")
