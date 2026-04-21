CREATE DATABASE IF NOT EXISTS busines_transaciones;
    USE busines_transaciones;

    -- Creamos la tabla companies
    CREATE TABLE IF NOT EXISTS companies (
        company_id VARCHAR(255),
        company_name VARCHAR(255),
        phone VARCHAR(255),
        email VARCHAR(255),
        country VARCHAR(255),
        website VARCHAR(255)
    );
    
      CREATE TABLE IF NOT EXISTS users (
        id VARCHAR(255),
        name VARCHAR(255),
        surname VARCHAR(255),
        phone VARCHAR(255),
        email VARCHAR(255),
        birth_date VARCHAR(255),
        country VARCHAR(255),
        city VARCHAR(255),
        postal_code VARCHAR(255),
        adress VARCHAR(255)
    );


    -- Creamos la tabla transaction
    CREATE TABLE IF NOT EXISTS transaction (
        id VARCHAR(255),
        card_id VARCHAR(255),
        busines_id VARCHAR(255), 
        timestamp VARCHAR(255),
        amount VARCHAR(255),
		declined VARCHAR(255),
        product_id VARCHAR(255),
        user_id VARCHAR(255),
        lat VARCHAR(255),
        longitude VARCHAR(255)
    );
    
CREATE TABLE IF NOT EXISTS credit_cards (
	id VARCHAR(255),
    user_id VARCHAR(255),
    iban VARCHAR(255),
    pan VARCHAR(255),
    pin VARCHAR(255),
    cvv VARCHAR(255),
    track1 VARCHAR(255),
	track2 VARCHAR(255),
    expiring_date VARCHAR(255)
);