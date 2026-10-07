IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'sales') EXEC('CREATE SCHEMA sales');

IF OBJECT_ID('dbo.orders') IS NOT NULL DROP TABLE dbo.orders;
IF OBJECT_ID('dbo.customers') IS NOT NULL DROP TABLE dbo.customers;
IF OBJECT_ID('sales.invoices') IS NOT NULL DROP TABLE sales.invoices;
IF OBJECT_ID('sales.regions') IS NOT NULL DROP TABLE sales.regions;

CREATE TABLE dbo.customers (customer_id int PRIMARY KEY, name nvarchar(100), country nvarchar(10));
CREATE TABLE dbo.orders (order_id int PRIMARY KEY, customer_id int, amount decimal(10,2), status nvarchar(20), order_date date);
CREATE TABLE sales.regions (region_id int PRIMARY KEY, region_name nvarchar(50));
CREATE TABLE sales.invoices (invoice_id int PRIMARY KEY, region_id int, total decimal(10,2));

WITH n AS (SELECT TOP (1000) ROW_NUMBER() OVER (ORDER BY (SELECT 1)) AS i FROM sys.all_objects a CROSS JOIN sys.all_objects b)
INSERT INTO dbo.orders
SELECT i, 1 + i % 50, 10 + (i * 7) % 490,
       CASE i % 3 WHEN 0 THEN 'completed' WHEN 1 THEN 'refunded' ELSE 'pending' END,
       DATEADD(day, i % 365, '2025-01-01')
FROM n;

WITH n AS (SELECT TOP (50) ROW_NUMBER() OVER (ORDER BY (SELECT 1)) AS i FROM sys.all_objects)
INSERT INTO dbo.customers
SELECT i, 'Customer ' + CAST(i AS nvarchar(10)),
       CASE i % 5 WHEN 0 THEN 'US' WHEN 1 THEN 'DE' WHEN 2 THEN 'PK' WHEN 3 THEN 'JP' ELSE 'BR' END
FROM n;

INSERT INTO sales.regions VALUES (1, 'North'), (2, 'South'), (3, 'East'), (4, 'West'), (5, 'Central');

WITH n AS (SELECT TOP (200) ROW_NUMBER() OVER (ORDER BY (SELECT 1)) AS i FROM sys.all_objects)
INSERT INTO sales.invoices
SELECT i, 1 + i % 5, 100 + (i * 13) % 900 FROM n;
