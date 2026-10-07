CREATE SCHEMA IF NOT EXISTS sales;

DROP TABLE IF EXISTS public.orders;
DROP TABLE IF EXISTS public.customers;
DROP TABLE IF EXISTS sales.invoices;
DROP TABLE IF EXISTS sales.regions;

CREATE TABLE public.customers (customer_id integer PRIMARY KEY, name text, country text);
INSERT INTO public.customers
SELECT n, 'Customer ' || n, (ARRAY['US','DE','PK','JP','BR'])[1 + n % 5] FROM generate_series(1, 50) AS n;

CREATE TABLE public.orders (order_id integer PRIMARY KEY, customer_id integer, amount numeric(10,2), status text, order_date date);
INSERT INTO public.orders
SELECT n, 1 + n % 50, round((10 + (n * 7) % 490)::numeric, 2), (ARRAY['completed','refunded','pending'])[1 + n % 3],
       DATE '2025-01-01' + (n % 365)
FROM generate_series(1, 1000) AS n;

CREATE TABLE sales.regions (region_id integer PRIMARY KEY, region_name text);
INSERT INTO sales.regions VALUES (1, 'North'), (2, 'South'), (3, 'East'), (4, 'West'), (5, 'Central');

CREATE TABLE sales.invoices (invoice_id integer PRIMARY KEY, region_id integer, total numeric(10,2));
INSERT INTO sales.invoices
SELECT n, 1 + n % 5, round((100 + (n * 13) % 900)::numeric, 2) FROM generate_series(1, 200) AS n;
