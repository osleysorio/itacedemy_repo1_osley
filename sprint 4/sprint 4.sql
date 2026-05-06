/* SPRINT 4*/

/* EJERCICIO 1*/

SELECT *
FROM `sprint3-analytics-osley.sprint3_silver.transactions_clean` t
JOIN `sprint3_silver.companies_clean` c on t.business_id = c.company_id
WHERE DATE(t.timestamp) = '2022-03-12' AND c.country = 'Germany';


/* EJERCICIO 2*/

/* Paso 1, Generación  de  Datos Recientes (Mocking Data)  */

CREATE OR REPLACE TABLE `sprint3-analytics-osley.sprint3_silver.transactions_recent` AS
SELECT
  * EXCEPT(timestamp),
  TIMESTAMP_SUB (CURRENT_TIMESTAMP(), INTERVAL CAST(RAND() * 50 AS INT64) DAY) AS timestamp
  -- RAND() * 50 genera un nuemro aleatorio entre 0 y 50, con CAST lo convertimos en entero, con TIMESTAMP_SUB se lo restamos al tiempo actual CURRENT_TIMESTAMP
FROM
  `sprint3-analytics-osley.sprint3_silver.transactions_clean`;


/* Paso 2, Creación  de la Tabla  Optimizada  (Partitioning & Clustering) */

CREATE OR REPLACE TABLE `sprint3-analytics-osley.sprint3_gold.fact_transactions_optimized` 
PARTITION BY DATE(timestamp)
CLUSTER BY business_id AS
SELECT
  *
FROM
  `sprint3-analytics-osley.sprint3_silver.transactions_recent`;


/*Ejercicio 3: La Prueba del Algodón (Benchmark)*/

SELECT *
FROM `sprint3-analytics-osley.sprint3_silver.transactions_recent`
WHERE   timestamp > TIMESTAMP_SUB (CURRENT_TIMESTAMP(),INTERVAL 30 DAY)
;

SELECT *
FROM `sprint3-analytics-osley.sprint3_gold.fact_transactions_optimized`
WHERE   timestamp > TIMESTAMP_SUB (CURRENT_TIMESTAMP(),INTERVAL 30 DAY)
;

/* Ejercicio 4, Smart Caching (Vistas Materializadas)*/

CREATE MATERIALIZED VIEW `sprint3-analytics-osley.sprint3_gold.mv_daily_sales`
AS
SELECT
    DATE(timestamp) AS dia,
    SUM(t.amount) as total
FROM
    `sprint3-analytics-osley.sprint3_gold.fact_transactions_optimized` as t
WHERE declined = 0
GROUP BY dia
;

SELECT *
FROM `sprint3-analytics-osley.sprint3_gold.mv_daily_sales`
;

/*Nivel 2 */

/* Ejercicio 1,Perfilado de Clientes VIP (Métricas Agregadas con CTEs) */

WITH VIP_Stats AS (
  -- Definición de la CTE
  SELECT 
     user_id,
     ROUND(SUM(amount),2) AS gasto_total, 
     COUNT(transaction_id)  AS num_compras,
     ROUND(AVG(amount),2) AS tikect_medio,
     MAX(amount) AS max_compra
  FROM `sprint3-analytics-osley.sprint3_gold.fact_transactions_optimized` 
  WHERE declined = 0
  GROUP BY user_id
  HAVING gasto_total > 500
)
-- Consulta principal
SELECT 
    u.user_id,
    CONCAT(u.name, ' ', u.surname) AS nombre_completo,
     u.email, 
    v.num_compras,
    v. tikect_medio,
    v.max_compra,
    v.gasto_total
FROM VIP_Stats as v
JOIN `sprint3-analytics-osley.sprint3_silver.users_combined` AS u ON u.user_id = v.user_id
ORDER BY v.gasto_total DESC;

/*Ejercio 2, Análisis de Tendencias (Window Functions sobre Vistas)*/

WITH DatosBase AS (
  SELECT 
    dia as fecha, 
    total as ventas_hoy,
    -- calculamos el valor anterior
    LAG(total) OVER (ORDER BY dia) AS total_anterior
  FROM `sprint3-analytics-osley.sprint3_gold.mv_daily_sales`
)
SELECT 
  *,
  ROUND((ventas_hoy - total_anterior) * 100 / total_anterior, 2) AS Diff_Percentual
FROM DatosBase
ORDER BY fecha DESC;




/* Ejercio 3, Totales Acumulados (Running Totales sobre Vistas)*/

SELECT 
      dia AS fecha,
      ROUND(total,2) AS ventas_del_dia,
      ROUND(SUM(total) OVER (PARTITION BY EXTRACT(YEAR FROM dia) ORDER BY dia ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),2) AS ventas_acumuladas_YTD
      
FROM `sprint3-analytics-osley.sprint3_gold.mv_daily_sales`
ORDER BY fecha ASC;

/* Ejercicio 4,Fidelización y Valor del Cliente (Filtraje Avanzado)*/

SELECT 
    u.user_id,
    CONCAT(u.name, ' ', u.surname) AS nombre_completo,
    u.email,
    t.timestamp AS fecha_3_comp,
    t.amount AS importe_3_comp,
    -- Calculamos la media acumulada de las tres primeras comprras
    ROUND(AVG(t.amount) OVER(PARTITION BY u.user_id ORDER BY t.timestamp ASC ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),2) AS media_3_primeras
FROM `sprint3-analytics-osley.sprint3_silver.users_combined` u
JOIN `sprint3-analytics-osley.sprint3_gold.fact_transactions_optimized` t ON t.user_id = u.user_id
WHERE declined = 0
-- El QUALIFY filtra el resultado de la función de ventana ROW_NUMBER()
QUALIFY ROW_NUMBER() OVER(PARTITION BY u.user_id ORDER BY t.timestamp ASC) = 3;


/*Nivel 3 */

/*Ejercicio 1, Allanamiento de Datos (Unnesting)*/

CREATE OR REPLACE TABLE `sprint3-analytics-osley.sprint3_gold.dim_transactions_flat` 
AS
SELECT
  t.transaction_id,
  timestamp, 
  amount,
  pc.product_id,
  pc.name,
  pc.price
FROM `sprint3-analytics-osley.sprint3_gold.fact_transactions_optimized` t
-- Aplanamos la tabla haciendo una fila por producto
CROSS JOIN UNNEST  (SPLIT(t.product_ids,',')) as product_id_flat
JOIN `sprint3-analytics-osley.sprint3_silver.products_clean` AS  pc ON CAST(TRIM(product_id_flat) AS INT64) = pc.product_id ;

SELECT *
FROM `sprint3-analytics-osley.sprint3_gold.dim_transactions_flat`
ORDER BY transaction_id ASC
;

/* Ejercicio 2, El Ranking de Ventas (Agregación Simple) */

SELECT product_id,name, COUNT(product_id) AS total_ventas
FROM `sprint3-analytics-osley.sprint3_gold.dim_transactions_flat`
GROUP BY product_id,name
ORDER BY total_ventas DESC
LIMIT 5;


/* Ejercicio 3, Automatización del Pipeline y Visualización */

CREATE OR REPLACE FUNCTION `sprint3-analytics-osley.sprint3_gold.calculate_tax`(x FLOAT64)
RETURNS FLOAT64
AS (
 ROUND( x * 1.21)
);


CREATE OR REPLACE TABLE `sprint3-analytics-osley.sprint3_gold.dim_transactions_flat` 
AS
SELECT
  t.transaction_id,
  timestamp, 
  amount,
  pc.product_id,
  pc.name,
  pc.price,
  `sprint3-analytics-osley.sprint3_gold.calculate_tax`(pc.price) AS product_price_tax_inc
FROM `sprint3-analytics-osley.sprint3_gold.fact_transactions_optimized` t
-- Aplanamos la tabla haciendo una fila por producto
CROSS JOIN UNNEST  (SPLIT(t.product_ids,',')) as product_id_flat
JOIN `sprint3-analytics-osley.sprint3_silver.products_clean` AS  pc ON CAST(TRIM(product_id_flat) AS INT64) = pc.product_id ;

SELECT *
FROM `sprint3-analytics-osley.sprint3_gold.dim_transactions_flat`
;

