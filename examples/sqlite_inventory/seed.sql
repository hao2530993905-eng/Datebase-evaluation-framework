INSERT INTO categories (id, name) VALUES
    (1, 'electronics'),
    (2, 'books');

INSERT INTO products (id, sku, category_id, name, price_cents, stock_units) VALUES
    (1, 'KB-100', 1, 'Mechanical Keyboard', 4999, 10),
    (2, 'BK-200', 2, 'Database Systems', 1299, 20),
    (3, 'MN-300', 1, 'Studio Monitor', 19999, 5);

INSERT INTO orders (id, customer_email, status, created_at) VALUES
    (1001, 'alice@example.com', 'pending', '2026-01-10T10:00:00Z'),
    (1002, 'bob@example.com', 'paid', '2026-01-11T11:30:00Z');

INSERT INTO order_items
    (id, order_id, product_id, qty, unit_price_cents)
VALUES
    (1, 1001, 1, 1, 4999),
    (2, 1001, 2, 1, 1299),
    (3, 1002, 3, 1, 19999);
