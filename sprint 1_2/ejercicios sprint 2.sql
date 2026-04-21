/*Ejercicio 2
Utilizando JOINrealizarás las siguientes consultas:

- Listado de los países que están generando ventas.
- Desde cuántos países se generan las ventas.
- Identifica a la compañía con la mayor media de ventas.*/

-- Listado de los países que están generando ventas.
SELECT DISTINCT country
FROM company as empresa
JOIN transaction  ON empresa.id = transaction.company_id 
where declined = 0;

-- Desde cuántos países se generan las ventas.
SELECT  count(DISTINCT empresa.country) AS no_paises
FROM company as empresa
JOIN transaction  ON empresa.id = transaction.company_id 
WHERE declined = 0;


-- Identifica a la compañía con la mayor media de ventas.
SELECT  company_name, avg(amount) AS media
FROM company empresa
JOIN transaction  ON empresa.id = transaction.company_id 
WHERE declined = 0
GROUP BY company_name
ORDER BY  media DESC
LIMIT 1;

-- Ejercicio 3

/*Utilizando sólo subconsultas (sin utilizar JOIN):

- Muestra todas las transacciones realizadas por empresas de Alemania.
- Lista las empresas que han realizado transacciones por un amount superior a la media de todas las transacciones.
- Eliminarán del sistema las empresas que carecen de transacciones registradas, entrega el listado de estas empresas.*/


-- Muestra todas las transacciones realizadas por empresas de Alemania.

SELECT  *
FROM transaction 
WHERE   declined = 0 AND company_id in (SELECT id
									FROM company 
									where country = 'Germany') 
;

-- Lista las empresas que han realizado transacciones por un amount superior a la media de todas las transacciones.

SELECT id, company_name
FROM company
WHERE id in (SELECT DISTINCT company_id
			FROM transaction
			WHERE declined = 0 AND amount > (SELECT  avg(amount)
							FROM transaction
		)
)
;

-- Eliminarán del sistema las empresas que carecen de transacciones registradas, entrega el listado de estas empresas.

SELECT DISTINCT company_name
FROM company
WHERE id Not in (SELECT DISTINCT company_id
			FROM transaction
			)
;


/*Ejercicio 4
Tu tarea es diseñar y crear una tabla llamada "credit_card" que almacene detalles cruciales sobre las tarjetas de crédito. 
La nueva tabla debe ser capaz de identificar de forma única cada tarjeta y establecer una relación adecuada con las otras
dos tablas ("transaction" y "company"). Después de crear la tabla será necesario que ingreses la información del documento 
denominado "datos_introducir_credit". Recuerda mostrar el diagrama y realizar una breve descripción del mismo.
*/

 
CREATE TABLE IF NOT EXISTS credit_card (
	id VARCHAR(15) PRIMARY KEY,
    iban VARCHAR(255) NOT NULL,
    pan VARCHAR(255) NOT NULL,
    pin VARCHAR(4) NOT NULL,
    cvv VARCHAR(3) NOT NULL,
    expiring_date VARCHAR(10) NOT NULL
);

/*Ejercicio 5
El departamento de Recursos Humanos ha identificado un error en el número de cuenta asociado a su tarjeta de crédito con ID CcU-2938.
 La información que debe mostrarse para este registro es: TR323456312213576817699999. Recuerda mostrar que el cambio se realizó.*/

UPDATE credit_card 
SET iban = 'TR323456312213576817699999' 
WHERE id = 'CcU-2938';

SELECT *
FROM credit_card
where id = 'CcU-9999';

/*Ejercicio 6
En la tabla "transaction" ingresa una nueva transacción con la siguiente información: */
ALTER TABLE credit_card
    MODIFY COLUMN iban varchar(255) NULL,
    MODIFY COLUMN pan varchar(255) NULL,
    MODIFY COLUMN pin varchar(4) NULL,
    MODIFY COLUMN cvv varchar(3) NULL,
    MODIFY COLUMN expiring_date varchar(10) NULL;

INSERT INTO company (id) VALUES ( 'b-9999');
INSERT INTO credit_card (id) VALUES ('CcU-9999');
INSERT INTO transaction (id, credit_card_id, company_id, user_id, lat, longitude, amount, declined)
VALUES ('108B1D1D-5B23-A76C-55EF-C568E49A99DD', 'CcU-9999', 'b-9999', '9999', '829.999', '-117.999','111.11', '0');

SELECT *
FROM transaction
where id = '108B1D1D-5B23-A76C-55EF-C568E49A99DD';

/*Ejercicio 7
Desde recursos humanos te solicitan eliminar la columna "pan" de la tabla credit_card. Recuerda mostrar el cambio realizado. */


ALTER TABLE credit_card DROP COLUMN pan;

SELECT *
FROM credit_card;


/*Ejercicio 8
Descarga los archivos CSV que encontrarás en el apartado de recursos :

american_users.csv
european_users.csv
companies.csv
credit_cards.csv
transactions.csv
Estudia y diseña una base de datos con un esquema de estrella que contenga, al menos 4 tablas de las que puedas realizar las siguientes consultas:*/

SET GLOBAL local_infile = 1;

LOAD DATA LOCAL INFILE 'C:/sprint1/N1-Ex.8__ american_users.csv'
INTO TABLE users
FIELDS TERMINATED BY ','
ENCLOSED BY '"'
IGNORE 1 LINES; 

LOAD DATA LOCAL INFILE 'C:/sprint1/N1.Ex.8__ european_users.csv'
INTO TABLE users
FIELDS TERMINATED BY ','
ENCLOSED BY '"'
IGNORE 1 LINES; 

LOAD DATA LOCAL INFILE 'C:/sprint1/N1.Ex.8__ companies.csv'
INTO TABLE companies
FIELDS TERMINATED BY ','
ENCLOSED BY '"'
IGNORE 1 LINES; 

LOAD DATA LOCAL INFILE 'C:/sprint1/N1.Ex.8__ credit_cards.csv'
INTO TABLE credit_cards
FIELDS TERMINATED BY ','
ENCLOSED BY '"'
IGNORE 1 LINES; 

LOAD DATA LOCAL INFILE 'C:/sprint1/N1.Ex.8__ transactions.csv'
INTO TABLE transaction
FIELDS TERMINATED BY ';'
ENCLOSED BY '"'
IGNORE 1 LINES; 


SELECT id , COUNT(*)
FROM users
GROUP BY id
HAVING COUNT(*) > 1;

SELECT *
FROM users
WHERE id IS NULL;


SELECT id , COUNT(*)
FROM transaction
GROUP BY id
HAVING COUNT(*) > 1;

SELECT *
FROM transaction
WHERE id IS NULL;


SELECT id , COUNT(*)
FROM credit_cards
GROUP BY id
HAVING COUNT(*) > 1;

SELECT *
FROM credit_cards
WHERE id IS NULL;

SELECT company_id , COUNT(*)
FROM companies
GROUP BY company_id
HAVING COUNT(*) > 1;

SELECT *
FROM companies
WHERE company_id IS NULL;

-- convertir campos como llave primaria

ALTER TABLE users
MODIFY COLUMN id INT NOT NULL,
ADD PRIMARY KEY (id);

ALTER TABLE companies
ADD PRIMARY KEY (company_id);

ALTER TABLE credit_cards
ADD PRIMARY KEY (id);

ALTER TABLE transaction
ADD PRIMARY KEY (id);

-- definiendo las llaves foraneas

ALTER TABLE transaction
ADD CONSTRAINT fk_credit_cards
FOREIGN KEY (card_id)
REFERENCES credit_cards(id);

ALTER TABLE transaction
ADD CONSTRAINT fk_companies
FOREIGN KEY (busines_id)
REFERENCES companies(company_id);

ALTER TABLE transaction
MODIFY COLUMN user_id INT NOT NULL;

ALTER TABLE transaction
ADD CONSTRAINT fk_users
FOREIGN KEY (user_id)
REFERENCES users(id);

/*Ejercicio 9
Realiza una subconsulta que muestre a todos los usuarios con más de 80 transacciones utilizando al menos 2 tablas.*/

SELECT usuarios.name, usuarios.id
FROM users usuarios
where id IN (SELECT user_id
	FROM transaction tr
	GROUP BY user_id
	having count(*)>80);




/*Ejercicio 10
Muestra la media de amount por IBAN de las tarjetas de crédito en la compañía Donec Ltd., utiliza por lo menos 2 tablas.*/

SELECT iban, compannia.company_name, avg(amount)
FROM  transaction transaccion
JOIN companies as compannia on compannia.company_id = transaccion.busines_id
JOIN credit_cards c_cards on  c_cards.id = transaccion.card_id
WHERE compannia.company_name ='Donec Ltd'
GROUP BY  iban;

-- NIVEL 2
/*Ejercicio 1
Identifica los cinco días que se generó la mayor cantidad de ingresos en la empresa por ventas. 
Muestra la fecha de cada transacción junto con el total de las ventas.*/

ALTER TABLE transaction
MODIFY COLUMN amount float;

ALTER TABLE transaction
MODIFY COLUMN timestamp DATETIME;

SELECT DATE(movimiento.timestamp) AS FECHA,  SUM(amount) as ventas_dia
FROM transaction  movimiento
JOIN companies compannia on compannia.company_id = movimiento.busines_id
WHERE declined= 0
GROUP BY DATE( movimiento.timestamp)
ORDER BY ventas_dia desc
limit 5;



/*Ejercicio 2
Presenta el nombre, teléfono, país, fecha y amount, de aquellas empresas que realizaron transacciones con un valor comprendido
entre 350 y 400 euros y en alguna de estas fechas: 29 de abril de 2015, 20 de julio de 2018 y 13 de marzo de 2024. 
Ordena los resultados de mayor a menor cantidad.*/


SELECT company_name, phone, country, timestamp, amount
FROM transaction
JOIN companies empresa on empresa.company_id = busines_id
WHERE amount> 350  AND amount < 400 AND (DATE(timestamp) = '2015-04-29' OR  DATE(timestamp) = '2018-07-20' OR DATE(timestamp) = '2024-03-13' )
ORDER BY amount desc;
;

/*Ejercicio 3
Necesitamos optimizar la asignación de los recursos y dependerá de la capacidad operativa que se requiera, 
por lo que te piden la información sobre la cantidad de transacciones que realizan las empresas, pero el departamento
 de recursos humanos es exigente y quiere un listado de las empresas en las que especifiques si tienen más de 400 transacciones o menos.*/

SELECT empresa1.company_name , count(id) total_trans,
    CASE
		WHEN count(id) > 400 THEN TRUE
		ELSE  FALSE
    END   AS mas_400
FROM transaction transaccion1     
JOIN companies empresa1 on empresa1.company_id = busines_id
GROUP BY empresa1.company_name;   

/*Ejercicio 4
Elimina de la tabla transacción el registro con ID 000447FE-B650-4DCF-85DE-C7ED0EE1CAAD de la base de datos.*/

DELETE FROM transaction WHERE id= '000447FE-B650-4DCF-85DE-C7ED0EE1CAAD';


/*Ejercicio 5
La sección de marketing desea tener acceso a información específica para realizar análisis y estrategias efectivas.
Se ha solicitado crear una vista que proporcione detalles clave sobre las compañías y sus transacciones.
Será necesaria que crees una vista llamada VistaMarketing que contenga la siguiente información: Nombre de la compañía. 
Teléfono de contacto. País de residencia. Media de compra realizado por cada compañía. Presenta la vista creada,
ordenando los datos de mayor a menor promedio de compra.*/

CREATE VIEW VistaMarketing AS
SELECT emp.company_name, emp.phone,emp.country, avg(mov.amount) as promedio
FROM companies emp
JOIN transaction mov ON mov.busines_id = emp.company_id
GROUP BY emp.company_name, emp.phone, emp.country;

SELECT * 
FROM VistaMarketing
order by promedio desc;

/*Nivel 3
Ejercicio 1
Crea una nueva tabla que refleje el estado de las tarjetas de crédito basado en si las tres últimas transacciones han sido declinadas entonces es inactivo,
 si al menos una no es rechazada entonces es activo. Partiendo de esta tabla responde:
 ¿Cuántas tarjetas están activas?*/

ALTER TABLE transaction
MODIFY COLUMN declined boolean;

CREATE TABLE estado_tarjetas AS
SELECT 
    card_id,
    IF(SUM(declined) = 3, 'Inactivo', 'Activo') AS estado
FROM (
    SELECT 
        card_id,
        declined,
        ROW_NUMBER() OVER(PARTITION BY card_id ORDER BY timestamp DESC) as rango
    FROM transaction
) AS historial_reciente
WHERE rango <= 3
GROUP BY card_id;



select *
from estado_tarjetas
where estado = 'activo';

/*Ejercicio 2
Crea una tabla con la que podamos unir los datos del nuevo archivo products.csv con la base de datos creada, 
teniendo en cuenta que desde transaction tienes product_ids. Genera la siguiente consulta:

Necesitamos conocer el número de veces que se ha vendido cada producto.*/

 CREATE TABLE IF NOT EXISTS productos (
        id INT PRIMARY KEY,
        product_name VARCHAR(255),
        price VARCHAR(15),
        colour VARCHAR(100),
        wheight VARCHAR(100),
        warehouse_id VARCHAR(255)
    );
    
LOAD DATA LOCAL INFILE 'C:/sprint1/N3.Ex.2__ products.csv'
INTO TABLE productos
FIELDS TERMINATED BY ','
ENCLOSED BY '"'
IGNORE 1 LINES; 

CREATE TABLE transaction_1 AS
SELECT 
    transaccion_alias.id,
    transaccion_alias.declined,
    CAST(j_tabla.product_id AS UNSIGNED) AS product_id
FROM 
    transaction transaccion_alias
JOIN JSON_TABLE(
    -- Convertimos la cadena de tipo "1,2,3" en un array JSON ["1","2","3"]
    CONCAT('["', REPLACE(transaccion_alias.product_id, ',', '","'), '"]'),
    '$[*]' COLUMNS (
        product_id VARCHAR(255) PATH '$'
    )
) AS j_tabla;



select product_name , count(product_id)
from transaction_1 t_1
join productos on productos.id = t_1.product_id
where t_1.declined = 0
GROUP BY product_id;