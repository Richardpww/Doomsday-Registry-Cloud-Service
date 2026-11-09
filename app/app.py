import os

import psycopg
from psycopg.rows import dict_row
from flask import Flask, jsonify, request, render_template

app = Flask(__name__)

CRITICAL_STOCK = 5        # stok <= 5 dianggap KRITIS
MAX_DECREASE = 20         # stok hanya boleh turun maksimal 20 per update
MAX_STOCK = 100000
SUPPLIER_MIN_LEVEL = 2    # level akses minimal untuk melihat nama supplier

SEED = [
    ("makanan", "Beras Premium 5kg", "UD Tani Makmur", "Beras pulen kemasan 5 kg", 40),
    ("minuman", "Kopi Bubuk Robusta", "CV Kopi Nusantara", "Kopi bubuk 200 gram", 12),
    ("kebutuhan-harian", "Sabun Cuci Piring", "PT Bersih Sejahtera", "Sabun cair 800 ml", 4),
    ("makanan", "Mie Instan Goreng", "PT Sumber Pangan", "Mie instan, dus isi 40", 25),
]


def get_conn():
    return psycopg.connect(
        host=os.environ["DB_HOST"],  # nama service Compose: "db"
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        row_factory=dict_row,
    )


def log(conn, product_id, action, detail):
    conn.execute(
        "INSERT INTO audit_log (product_id, action, detail) VALUES (%s,%s,%s)",
        (product_id, action, detail),
    )


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                category VARCHAR(40) NOT NULL,
                name VARCHAR(80) NOT NULL,
                supplier VARCHAR(80) NOT NULL,
                description VARCHAR(200) NOT NULL DEFAULT '',
                stock INTEGER NOT NULL CHECK (stock BETWEEN 0 AND 100000),
                status VARCHAR(10) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','deleted')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (category, name)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id SERIAL PRIMARY KEY,
                product_id INTEGER NOT NULL REFERENCES products(id),
                action VARCHAR(20) NOT NULL,
                detail TEXT NOT NULL,
                at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        n = conn.execute("SELECT count(*) AS n FROM products").fetchone()["n"]
        if n == 0:
            for c, nm, sp, ds, st in SEED:
                row = conn.execute(
                    "INSERT INTO products (category, name, supplier, description, stock) "
                    "VALUES (%s,%s,%s,%s,%s) RETURNING id", (c, nm, sp, ds, st)
                ).fetchone()
                log(conn, row["id"], "CREATE", f"seed data, stock={st}")


init_db()


def get_level():
    try:
        return int(request.headers.get("X-Access-Level", "0"))
    except ValueError:
        return 0


def view(r, level):
    return {
        "id": r["id"],
        "category": r["category"],
        "name": r["name"],
        "supplier": r["supplier"] if level >= SUPPLIER_MIN_LEVEL else "[DISEMBUNYIKAN]",
        "description": r["description"],
        "stock": r["stock"],
        "stock_status": "KRITIS" if r["stock"] <= CRITICAL_STOCK else "AMAN",
        "supplier_access_level": SUPPLIER_MIN_LEVEL,
        "status": r["status"],
    }


def valid_stock(v):
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= MAX_STOCK


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return jsonify(status="ok"), 200
    except Exception:
        return jsonify(status="db_unreachable"), 503


# ---------- CREATE ----------
@app.post("/api/products")
def create_product():
    d = request.get_json(silent=True) or {}
    category = str(d.get("category", "")).strip().lower()
    name = str(d.get("name", "")).strip()
    supplier = str(d.get("supplier", "")).strip()
    description = str(d.get("description", "")).strip()
    stock = d.get("stock")
    if not (category and name and supplier) or not valid_stock(stock):
        return jsonify(error="category, name, supplier wajib diisi dan "
                             f"stock harus int 0-{MAX_STOCK}"), 400
    try:
        with get_conn() as conn:
            row = conn.execute(
                "INSERT INTO products (category, name, supplier, description, stock) "
                "VALUES (%s,%s,%s,%s,%s) RETURNING *",
                (category, name, supplier, description, stock),
            ).fetchone()
            log(conn, row["id"], "CREATE", f"stock={stock}")
    except psycopg.errors.UniqueViolation:
        return jsonify(error=f"produk '{name}' sudah ada di kategori {category}"), 409
    resp = jsonify(view(row, 99))  # pembuat melihat data yang baru dibuatnya
    resp.status_code = 201
    resp.headers["Location"] = f"/api/products/{row['id']}"
    return resp


# ---------- READ (list) ----------
@app.get("/api/products")
def list_products():
    sql, params = "SELECT * FROM products WHERE 1=1", []
    if request.args.get("include_deleted") != "true":
        sql += " AND status = 'active'"
    if request.args.get("category"):
        sql += " AND category = %s"
        params.append(request.args["category"].lower())
    if request.args.get("max_stock", "").isdigit():
        sql += " AND stock <= %s"
        params.append(int(request.args["max_stock"]))
    sql += " ORDER BY stock ASC, id"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    lv = get_level()
    return jsonify([view(r, lv) for r in rows]), 200


# ---------- READ (stok kritis) ----------
@app.get("/api/products/low-stock")
def low_stock():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM products WHERE status='active' AND stock <= %s "
            "ORDER BY stock, id", (CRITICAL_STOCK,)
        ).fetchall()
    lv = get_level()
    return jsonify([view(r, lv) for r in rows]), 200


# ---------- READ (one) ----------
@app.get("/api/products/<int:pid>")
def get_product(pid):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM products WHERE id=%s", (pid,)).fetchone()
    if not row:
        return jsonify(error="produk tidak ditemukan"), 404
    if row["status"] == "deleted":
        return jsonify(error="produk sudah dihapus"), 410
    return jsonify(view(row, get_level())), 200


# ---------- UPDATE ----------
@app.patch("/api/products/<int:pid>")
def update_product(pid):
    d = request.get_json(silent=True) or {}
    new_stock = d.get("stock")
    new_desc = str(d["description"]).strip() if "description" in d else None
    if new_stock is None and new_desc is None:
        return jsonify(error="kirim stock dan/atau description"), 400
    if new_stock is not None and not valid_stock(new_stock):
        return jsonify(error=f"stock harus int 0-{MAX_STOCK}"), 400
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM products WHERE id=%s", (pid,)).fetchone()
        if not row:
            return jsonify(error="produk tidak ditemukan"), 404
        if row["status"] == "deleted":
            return jsonify(error="produk sudah dihapus"), 410
        if new_stock is not None and new_stock < row["stock"] - MAX_DECREASE:
            return jsonify(error=f"stok hanya boleh turun maksimal {MAX_DECREASE} "
                                 "per update"), 422
        stock = new_stock if new_stock is not None else row["stock"]
        desc = new_desc if new_desc is not None else row["description"]
        row2 = conn.execute(
            "UPDATE products SET stock=%s, description=%s WHERE id=%s RETURNING *",
            (stock, desc, pid),
        ).fetchone()
        log(conn, pid, "UPDATE",
            f"stock {row['stock']}->{stock}; description changed={desc != row['description']}")
    return jsonify(view(row2, get_level())), 200


# ---------- DELETE (soft delete) ----------
@app.delete("/api/products/<int:pid>")
def delete_product(pid):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM products WHERE id=%s", (pid,)).fetchone()
        if not row:
            return jsonify(error="produk tidak ditemukan"), 404
        if row["status"] == "deleted":
            return jsonify(error="produk sudah dihapus"), 410
        conn.execute("UPDATE products SET status='deleted' WHERE id=%s", (pid,))
        log(conn, pid, "DELETE", "produk dihapus (soft delete)")
    return "", 204


# ---------- Ekstra ----------
@app.get("/api/products/<int:pid>/history")
def history(pid):
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM products WHERE id=%s", (pid,)).fetchone():
            return jsonify(error="produk tidak ditemukan"), 404
        rows = conn.execute(
            "SELECT action, detail, at FROM audit_log WHERE product_id=%s ORDER BY id",
            (pid,),
        ).fetchall()
    return jsonify([{**r, "at": r["at"].isoformat()} for r in rows]), 200


@app.get("/api/summary")
def summary():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT category, count(*) AS products, sum(stock) AS total_stock "
            "FROM products WHERE status='active' GROUP BY category ORDER BY category"
        ).fetchall()
    return jsonify(rows), 200
