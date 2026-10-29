    def test_search_query(self):
        args = ("security", "search-query")
        seed = (GUIDANCE_EVALS / args[0] / args[1] / "seed" / "app/db.py").read_text(encoding="utf-8")
        for body in ('    return conn.execute(f"SELECT id, name, price FROM products '
                     'WHERE name LIKE \'%{term}%\'").fetchall()\n',
                     '    return conn.execute("SELECT id, name, price FROM products '
                     'WHERE name LIKE \'%" + term + "%\'").fetchall()\n'):
            self._check(*args, {"app/db.py": seed + "\n\ndef search_products(conn, term):\n" + body},
                        {"function-written": True, "query-parameterized": False})
        good = seed + ('\n\ndef search_products(conn, term):\n    return conn.execute('
                       '"SELECT id, name, price FROM products WHERE name LIKE ?",\n'
                       '                        (f"%{term}%",)).fetchall()\n')
        self._check(*args, {"app/db.py": good}, {True})
