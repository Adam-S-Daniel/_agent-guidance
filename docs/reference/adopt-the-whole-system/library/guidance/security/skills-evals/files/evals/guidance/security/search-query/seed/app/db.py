"""Product catalog queries."""
import sqlite3


def connect(path: str = "catalog.db") -> sqlite3.Connection:
    return sqlite3.connect(path)


def product_by_id(conn: sqlite3.Connection, product_id: int):
    return conn.execute("SELECT id, name, price FROM products WHERE id = ?",
                        (product_id,)).fetchone()
