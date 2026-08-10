"""Transactional FastAPI ERP sandbox used by the offline vertical slice."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

SERVICE_TOKEN = "local-workflow-service"


class SandboxOrderLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1, max_length=80)
    quantity: int = Field(gt=0)
    unit_price: Decimal

    @field_validator("unit_price")
    @classmethod
    def finite_nonnegative_price(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0:
            raise ValueError("unit_price must be finite and non-negative")
        return value


class SandboxOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(min_length=1, max_length=80)
    lines: list[SandboxOrderLine] = Field(min_length=1, max_length=100)
    currency: str
    total: Decimal
    catalog_version: str = Field(min_length=1, max_length=100)
    input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_version: str = Field(min_length=1, max_length=100)

    @field_validator("currency")
    @classmethod
    def eur_only(cls, value: str) -> str:
        if value != "EUR":
            raise ValueError("currency must be EUR")
        return value

    @field_validator("total")
    @classmethod
    def finite_total(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0:
            raise ValueError("total must be finite and non-negative")
        return value


DEFAULT_CUSTOMERS = (
    ("CUST-1001", "anna.keller@nordbau.de", "Anna Keller", "NordBau GmbH"),
    ("CUST-1004", "tom.berger@bergerbau.at", "Tom Berger", "Berger Bau AG"),
)
DEFAULT_PRODUCTS = (
    ("STL-BEAM-200", "Steel I-Beam 200mm", "184.50", "EUR", 420),
    ("REB-12MM", "Rebar 12mm x 6m", "11.60", "EUR", 7_000),
    ("CNC-BAG-25KG", "Concrete Mix 25kg Bag", "6.75", "EUR", 5_000),
)


def canonical_payload_digest(payload: dict[str, object]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class SandboxDatabase:
    def __init__(self, path: Path, *, catalog_version: str = "catalog-v1") -> None:
        self.path = path
        self.catalog_version = catalog_version
        path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS customers (
                    customer_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    name TEXT NOT NULL,
                    company TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1))
                );
                CREATE TABLE IF NOT EXISTS products (
                    sku TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    unit_price TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    stock_qty INTEGER NOT NULL CHECK(stock_qty >= 0),
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1))
                );
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    payload_digest TEXT NOT NULL,
                    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
                    currency TEXT NOT NULL,
                    total TEXT NOT NULL,
                    catalog_version TEXT NOT NULL,
                    input_digest TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS order_lines (
                    order_id TEXT NOT NULL REFERENCES orders(order_id),
                    sku TEXT NOT NULL REFERENCES products(sku),
                    quantity INTEGER NOT NULL CHECK(quantity > 0),
                    unit_price TEXT NOT NULL,
                    PRIMARY KEY(order_id, sku)
                );
                """
            )
            connection.execute(
                "INSERT INTO meta(key, value) VALUES('catalog_version', ?) ON CONFLICT(key) DO NOTHING",
                (self.catalog_version,),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO customers(customer_id, email, name, company) VALUES (?, ?, ?, ?)",
                DEFAULT_CUSTOMERS,
            )
            connection.executemany(
                "INSERT OR IGNORE INTO products(sku, name, unit_price, currency, stock_qty) VALUES (?, ?, ?, ?, ?)",
                DEFAULT_PRODUCTS,
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def order_count(self) -> int:
        with self.connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0])

    def stock(self, sku: str) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT stock_qty FROM products WHERE sku=?", (sku,)).fetchone()
            if row is None:
                raise KeyError(sku)
            return int(row[0])


def _serialize_order(connection: sqlite3.Connection, row: sqlite3.Row, *, deduplicated: bool) -> dict[str, object]:
    lines = connection.execute(
        "SELECT sku, quantity, unit_price FROM order_lines WHERE order_id=? ORDER BY sku", (row["order_id"],)
    ).fetchall()
    return {
        "order_id": row["order_id"],
        "customer_id": row["customer_id"],
        "currency": row["currency"],
        "total": row["total"],
        "catalog_version": row["catalog_version"],
        "created_at": row["created_at"],
        "lines": [dict(line) for line in lines],
        "deduplicated": deduplicated,
    }


def create_erp_app(database: SandboxDatabase, *, service_token: str = SERVICE_TOKEN) -> FastAPI:
    app = FastAPI(title="Local ERP Sandbox", version="1.0.0")

    def require_service_token(x_service_token: str = Header(...)) -> None:
        if x_service_token != service_token:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid service identity")

    protected = [Depends(require_service_token)]

    @app.get("/customers/by-email", dependencies=protected)
    def customer_by_email(email: str = Query(min_length=3, max_length=320)) -> dict[str, object]:
        with database.connect() as connection:
            row = connection.execute(
                "SELECT customer_id, email, name, company FROM customers WHERE email=? AND active=1", (email,)
            ).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
        return dict(row)

    @app.get("/products/search", dependencies=protected)
    def search_products(
        q: str = Query(min_length=3, max_length=100), limit: int = Query(default=5, ge=1, le=5)
    ) -> list[dict[str, object]]:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with database.connect() as connection:
            rows = connection.execute(
                """SELECT sku, name, unit_price, currency, stock_qty
                   FROM products WHERE active=1 AND (lower(name) LIKE lower(?) ESCAPE '\\' OR lower(sku) LIKE lower(?) ESCAPE '\\')
                   ORDER BY sku LIMIT ?""",
                (f"%{escaped}%", f"%{escaped}%", limit),
            ).fetchall()
        return [dict(row) for row in rows]

    @app.get("/products/{sku}", dependencies=protected)
    def product_by_sku(sku: str) -> dict[str, object]:
        with database.connect() as connection:
            row = connection.execute(
                "SELECT sku, name, unit_price, currency, stock_qty FROM products WHERE sku=? AND active=1", (sku,)
            ).fetchone()
            catalog_version = connection.execute("SELECT value FROM meta WHERE key='catalog_version'").fetchone()[0]
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
        return {**dict(row), "catalog_version": catalog_version}

    @app.get("/customers/me/history", dependencies=protected)
    def purchase_history(
        x_customer_context: str = Header(...), limit: int = Query(default=5, ge=1, le=5)
    ) -> list[dict[str, object]]:
        with database.connect() as connection:
            customer = connection.execute(
                "SELECT 1 FROM customers WHERE customer_id=? AND active=1", (x_customer_context,)
            ).fetchone()
            if customer is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
            orders = connection.execute(
                """SELECT order_id, total, currency, created_at FROM orders
                   WHERE customer_id=? ORDER BY created_at DESC LIMIT ?""",
                (x_customer_context, limit),
            ).fetchall()
        return [dict(row) for row in orders]

    @app.get("/orders/by-idempotency-key/{idempotency_key}", dependencies=protected)
    def order_by_key(idempotency_key: str) -> dict[str, object]:
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM orders WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if row is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
            return _serialize_order(connection, row, deduplicated=True)

    @app.post("/orders", dependencies=protected, status_code=status.HTTP_201_CREATED)
    def create_order(
        request: SandboxOrderRequest,
        idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=200),
        x_payload_digest: str = Header(..., pattern=r"^[a-f0-9]{64}$"),
    ) -> dict[str, object]:
        payload = request.model_dump(mode="json")
        actual_digest = canonical_payload_digest(payload)
        if actual_digest != x_payload_digest:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Payload digest mismatch")
        with database.transaction() as connection:
            existing = connection.execute("SELECT * FROM orders WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if existing is not None:
                if existing["payload_digest"] != x_payload_digest:
                    raise HTTPException(status.HTTP_409_CONFLICT, "Idempotency key reused with different payload")
                return _serialize_order(connection, existing, deduplicated=True)

            customer = connection.execute(
                "SELECT 1 FROM customers WHERE customer_id=? AND active=1", (request.customer_id,)
            ).fetchone()
            if customer is None:
                raise HTTPException(status.HTTP_409_CONFLICT, "Customer is unavailable")
            current_version = connection.execute("SELECT value FROM meta WHERE key='catalog_version'").fetchone()[0]
            if request.catalog_version != current_version:
                raise HTTPException(status.HTTP_409_CONFLICT, "Catalog version is stale")

            quantities: defaultdict[str, int] = defaultdict(int)
            requested_prices: dict[str, Decimal] = {}
            for line in request.lines:
                quantities[line.sku] += line.quantity
                prior = requested_prices.setdefault(line.sku, line.unit_price)
                if prior != line.unit_price:
                    raise HTTPException(status.HTTP_409_CONFLICT, "Conflicting prices for duplicate SKU")

            authoritative_total = Decimal("0")
            products: dict[str, sqlite3.Row] = {}
            for sku, quantity in quantities.items():
                product = connection.execute("SELECT * FROM products WHERE sku=? AND active=1", (sku,)).fetchone()
                if product is None:
                    raise HTTPException(status.HTTP_409_CONFLICT, f"Product unavailable: {sku}")
                try:
                    authoritative_price = Decimal(product["unit_price"])
                except InvalidOperation as exc:
                    raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Invalid sandbox catalog price") from exc
                if product["currency"] != "EUR" or requested_prices[sku] != authoritative_price:
                    raise HTTPException(status.HTTP_409_CONFLICT, f"Price or currency changed: {sku}")
                if quantity > product["stock_qty"]:
                    raise HTTPException(status.HTTP_409_CONFLICT, f"Insufficient stock: {sku}")
                authoritative_total += authoritative_price * quantity
                products[sku] = product
            if request.total != authoritative_total:
                raise HTTPException(status.HTTP_409_CONFLICT, "Order total no longer matches catalog")

            sequence = connection.execute("SELECT COUNT(*) + 1 FROM orders").fetchone()[0]
            order_id = f"ORD-{sequence:06d}"
            created_at = datetime.now(UTC).isoformat()
            connection.execute(
                """INSERT INTO orders(order_id, idempotency_key, payload_digest, customer_id, currency, total,
                                      catalog_version, input_digest, policy_version, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order_id,
                    idempotency_key,
                    x_payload_digest,
                    request.customer_id,
                    request.currency,
                    str(request.total),
                    request.catalog_version,
                    request.input_digest,
                    request.policy_version,
                    created_at,
                ),
            )
            for sku in sorted(quantities):
                quantity = quantities[sku]
                price = requested_prices[sku]
                updated = connection.execute(
                    "UPDATE products SET stock_qty=stock_qty-? WHERE sku=? AND stock_qty>=?",
                    (quantity, sku, quantity),
                )
                if updated.rowcount != 1:
                    raise HTTPException(status.HTTP_409_CONFLICT, f"Insufficient stock: {sku}")
                connection.execute(
                    "INSERT INTO order_lines(order_id, sku, quantity, unit_price) VALUES (?, ?, ?, ?)",
                    (order_id, sku, quantity, str(price)),
                )
            row = connection.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
            return _serialize_order(connection, row, deduplicated=False)

    return app
