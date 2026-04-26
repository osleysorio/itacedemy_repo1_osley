
/* Nivel 1 */

/* Ejercicio 2: Ingesta en Capa Bronze (Conexión DDL) */

CREATE  EXTERNAL TABLE `sprint3-analytics-osley.sprint3_bronze.transactions_raw`
OPTIONS (
  format = 'CSV',
  field_delimiter = ';',
  uris = ['gs://bootcamp-data-analytics-public/ERP/transactions.csv']
);

CREATE  EXTERNAL TABLE `sprint3-analytics-osley.sprint3_bronze.companies_raw`
(
  company_id STRING,
  company_name STRING,
  phone STRING,
  email STRING,
  country STRING,
  website STRING
)
OPTIONS (
  format = 'CSV',
  field_delimiter = ',',
  uris = ['gs://bootcamp-data-analytics-public/ERP/companies.csv'],
  skip_leading_rows = 1
);

CREATE  EXTERNAL TABLE `sprint3-analytics-osley.sprint3_bronze.american_users_raw`
OPTIONS (
  format = 'CSV',
  field_delimiter = ',',
  uris = ['gs://bootcamp-data-analytics-public/CRM/american_users.csv']
);

CREATE  EXTERNAL TABLE `sprint3-analytics-osley.sprint3_bronze.european_users_raw`
OPTIONS (
  format = 'CSV',
  field_delimiter = ',',
  uris = ['gs://bootcamp-data-analytics-public/CRM/european_users.csv']
);


CREATE  EXTERNAL TABLE `sprint3-analytics-osley.sprint3_bronze.credit_cards_raw`
OPTIONS (
  format = 'CSV',
  field_delimiter = ',',
  uris = ['gs://bootcamp-data-analytics-public/CRM/credit_cards.csv']
);

/*-------------------------*/

/* Ejercicio 4: Arquitectura y Rendimiento. Materialización de Datos (Asistido por IA)*/

/*a) Materialización de Datos (Asistido por IA)*/

CREATE OR REPLACE TABLE `sprint3-analytics-osley.sprint3_bronze.transactions_raw_native` AS
SELECT
  *
FROM
  `sprint3-analytics-osley.sprint3_bronze.transactions_raw`;

/*b) Auditoría de Costes*/

  SELECT COUNT(id)
  from `sprint3_bronze.transactions_raw`;

  SELECT COUNT(id)
  from `sprint3_bronze.transactions_raw_native`;

/*  c) El peligro del LIMIT*/

SELECT * FROM `sprint3-analytics-osley.sprint3_bronze.transactions_raw`
LIMIT 10;

SELECT * FROM `sprint3-analytics-osley.sprint3_bronze.transactions_raw`;


/*Ejercicio 5: Adaptación de Sintaxis (Reporting)*/
/*Tu jefe quiere saber cuáles fueron los 5 días con mayores ingresos del año 2021.*/


SELECT DATE(movimiento.timestamp) AS fecha, round( SUM(amount),2) as ventas_dia
FROM `sprint3_bronze.transactions_raw_native`as  movimiento
WHERE EXTRACT(YEAR FROM movimiento.timestamp) = 2021
GROUP BY DATE( movimiento.timestamp)
ORDER BY ventas_dia desc
limit 5;

/*Ejercicio 6: Consultas Complejas*/

SELECT 
    e.company_name, 
    e.country, 
    t.timestamp
FROM 
    `sprint3_bronze.companies_raw` AS e
JOIN 
    `sprint3_bronze.transactions_raw_native` AS t ON e.company_id = t.business_id
WHERE 
    t.amount BETWEEN 100 AND 200
    AND DATE(t.timestamp) IN ('2015-04-29', '2018-07-20', '2024-03-13');


/* NIVEL 2 */

 /*Ejercicio 1: Limpieza de Productos (Data Quality) */

CREATE OR REPLACE TABLE `sprint3-analytics-osley.sprint3_silver.products_clean` AS
SELECT
    id as product_id,
    product_name as name,
    CAST(REGEXP_REPLACE(CAST(price AS STRING), r'[$,€£ ]', '') AS FLOAT64) AS price,
    colour,
    weight,
    TRIM(warehouse_id,'WH-') as warehouse_id
  
FROM
  `sprint3-analytics-osley.sprint3_bronze.products_raw`;

 

/* Ejercicio 2: Creación de Transacciones Limpias (Capa Silver) */

CREATE OR REPLACE TABLE `sprint3-analytics-osley.sprint3_silver.transactions_clean` AS
SELECT
   id as transaction_id,
  card_id,
  business_id,
  SAFE_CAST(timestamp AS TIMESTAMP) as timestamp,
  IFNULL(SAFE_CAST(amount AS FLOAT64),0) as amount,
  declined,
  product_ids,
  user_id,
  IFNULL(SAFE_CAST(lat AS FLOAT64),0) as lat,
  IFNULL(SAFE_CAST(longitude AS FLOAT64),0) as longitude, 
  
FROM
  `sprint3-analytics-osley.sprint3_bronze.transactions_raw_native`;


/* Ejercicio 3: Unificación de Usuarios (UNION) */

CREATE OR REPLACE TABLE `sprint3-analytics-osley.sprint3_silver.users_combined` AS
  SELECT id as user_id, name,surname,email,phone, birth_date,address,city,country,postal_code,'Europa' AS region_user
  FROM `sprint3-analytics-osley.sprint3_bronze.european_users_raw`
UNION ALL
  SELECT id as user_id,name,surname,email,phone,birth_date,address,city,country,postal_code,'America' AS region_user
  FROM `sprint3-analytics-osley.sprint3_bronze.american_users_raw`; 




  /* Ejercicio 4: Materialización de Compañías y Tarjetas de Crédito */

CREATE TABLE `sprint3_silver.companies_clean`
AS
SELECT * FROM `sprint3_bronze.companies_raw`;



CREATE TABLE `sprint3_silver.credit_cards_clean`
AS
SELECT  id as credit_card_id, * EXCEPT (id) FROM `sprint3_bronze.credit_cards_raw`;


/* NIVEL 3*/

/* Ejercicio 1: La Vista de Marketing (Lógica de Negocio) */

CREATE VIEW `sprint3-analytics-osley.sprint3_gold.v_marketing_kpis` AS
  SELECT 
    c.company_name,
    C.country,
    C.phone,
    AVG(t.amount) AS avg_amount,
  CASE
		WHEN AVG(t.amount) > 260 THEN  "Premium" 
		ELSE  "Standard"
    END   AS client_tier
  FROM `sprint3-analytics-osley.sprint3_silver.companies_clean` AS c
  JOIN `sprint3-analytics-osley.sprint3_silver.transactions_clean` t ON t.business_id = c.company_id
  GROUP BY c.company_name,c.country, c.phone;


SELECT *
FROM `sprint3-analytics-osley.sprint3_gold.v_marketing_kpis`
ORDER BY 
    CASE client_tier 
        WHEN 'Premium' THEN 1
        WHEN 'Standard' THEN 2
    END,
    avg_amount DESC;

/* Ejercicio 2: Ranking de Productos (La Potencia de los Arrays)*/
CREATE TABLE `sprint3_gold.product_sales_ranking` AS
SELECT p.product_id,p.name ,p.price,p.colour, COUNT(t.transaction_id) AS total_sales
FROM `sprint3-analytics-osley.sprint3_silver.products_clean` AS p
JOIN
  (
    SELECT transaction_id, TRIM(product_id) AS product_id_flat
    FROM
      `sprint3-analytics-osley.sprint3_silver.transactions_clean`,
      UNNEST(SPLIT(product_ids, ',')) AS product_id
  ) AS t
  ON CAST(p.product_id AS STRING) = t.product_id_flat
GROUP BY p.product_id,p.name,p.price,p.colour
ORDER BY total_sales DESC;