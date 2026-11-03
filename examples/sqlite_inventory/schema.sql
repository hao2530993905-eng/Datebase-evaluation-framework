PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;

CREATE TABLE categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    name TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    stock_units INTEGER NOT NULL CHECK (stock_units >= 0)
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_email TEXT NOT NULL CHECK (
        instr(customer_email, '@') > 1
        AND instr(substr(customer_email, instr(customer_email, '@') + 1), '.') > 1
    ),
    status TEXT NOT NULL CHECK (status IN ('pending', 'paid', 'cancelled')),
    created_at TEXT NOT NULL
);

CREATE TABLE order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty INTEGER NOT NULL CHECK (qty > 0),
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    UNIQUE (order_id, product_id)
);

CREATE INDEX idx_orders_status_created
    ON orders(status, created_at);

CREATE INDEX idx_order_items_product
    ON order_items(product_id);
