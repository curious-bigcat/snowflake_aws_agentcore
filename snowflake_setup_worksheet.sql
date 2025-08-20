-- Snowflake Setup Worksheet for Travel Planner AI
-- This worksheet will set up all required Snowflake resources for the project.

-- 1. Use admin role and create database
use role accountadmin;
create or replace database travel_db;

-- 2. Create stages for data and docs
create stage docs ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE');
create stage data ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE');

-- 3. (Optional) Allow all IPs for testing (not for production)
CREATE NETWORK POLICY allow_all_policy ALLOWED_IP_LIST = ('0.0.0.0/0');
ALTER USER <your_user> SET NETWORK_POLICY = allow_all_policy;

-- 4. Upload data files to @data stage (flight_data.csv, hotel_data.csv, Travel_Plan_Guide.pdf)
-- Use Snowflake Web UI or PUT command
-- Example:
-- PUT file://flight_data.csv @data;
-- PUT file://hotel_data.csv @data;
-- PUT file://Travel_Plan_Guide.pdf @data;

-- 5. Create tables
CREATE OR REPLACE TABLE FLIGHT_DATA (
    AIRLINE STRING,
    SOURCE STRING,
    DESTINATION STRING,
    NEAREST_AIRPORT_SOURCE STRING,
    NEAREST_AIRPORT_DESTINATION STRING,
    IATA_SOURCE STRING,
    IATA_DESTINATION STRING,
    ROUTE STRING,
    DEP_TIME TIME,
    ARRIVAL_TIME TIME,
    DURATION STRING,
    TOTAL_STOPS INT,
    ADDITIONAL_INFO STRING,
    PRICE INT,
    DIRECT_CONNECTING STRING
);

CREATE OR REPLACE TABLE HOTEL_DATA (
    HOTEL_NAME VARCHAR,
    HOTEL_RATING FLOAT,
    CITY VARCHAR,
    HOTEL_TYPE VARCHAR,
    BREAKFAST_INCLUDED VARCHAR,
    WIFI_INCLUDED VARCHAR,
    PARKING_INCLUDED VARCHAR,
    FACILITY_1 VARCHAR,
    FACILITY_2 VARCHAR,
    FACILITY_3 VARCHAR,
    FACILITY_4 VARCHAR,
    FACILITY_5 VARCHAR,
    HOTEL_PRICE NUMBER
);

-- 6. Load data from CSVs
COPY INTO FLIGHT_DATA FROM @data/flight_data.csv FILE_FORMAT = (TYPE = 'CSV' FIELD_OPTIONALLY_ENCLOSED_BY='"' SKIP_HEADER=1);
COPY INTO HOTEL_DATA FROM @data/hotel_data.csv FILE_FORMAT = (TYPE = 'CSV' FIELD_OPTIONALLY_ENCLOSED_BY='"' SKIP_HEADER=1);

-- 7. Parse PDF and extract text
CREATE OR REPLACE TABLE RAW_TEXT_TRAVEL AS
SELECT 
    RELATIVE_PATH,
    SIZE,
    FILE_URL,
    BUILD_SCOPED_FILE_URL(@TRAVEL_DB.PUBLIC.DATA, RELATIVE_PATH) AS SCOPED_FILE_URL,
    TO_VARCHAR(
        SNOWFLAKE.CORTEX.PARSE_DOCUMENT(
            '@TRAVEL_DB.PUBLIC.DATA',
            RELATIVE_PATH,
            {'mode': 'LAYOUT'}
        ):content
    ) AS EXTRACTED_LAYOUT
FROM 
    DIRECTORY(@TRAVEL_DB.PUBLIC.DATA);

-- 8. Chunk the extracted text
CREATE OR REPLACE TABLE DOCS_CHUNKS_TABLE (
    RELATIVE_PATH VARCHAR,
    SIZE NUMBER,
    FILE_URL VARCHAR,
    SCOPED_FILE_URL VARCHAR,
    CHUNK VARCHAR,
    CHUNK_INDEX INTEGER,
    CATEGORY VARCHAR
);

INSERT INTO DOCS_CHUNKS_TABLE (
    RELATIVE_PATH, SIZE, FILE_URL, SCOPED_FILE_URL, CHUNK, CHUNK_INDEX
)
SELECT 
    RELATIVE_PATH,
    SIZE,
    FILE_URL,
    SCOPED_FILE_URL,
    c.VALUE::STRING AS CHUNK,
    c.INDEX::INTEGER AS CHUNK_INDEX
FROM 
    RAW_TEXT_TRAVEL,
    LATERAL FLATTEN(
        INPUT => SNOWFLAKE.CORTEX.SPLIT_TEXT_RECURSIVE_CHARACTER(
            EXTRACTED_LAYOUT,
            'markdown',
            1512,
            256,
            ['\n\n', '\n', ' ', '']
        )
    ) c;

-- 9. (Optional) Classify and update chunk categories
CREATE OR REPLACE TEMPORARY TABLE DOCS_CATEGORIES AS
WITH unique_documents AS (
    SELECT DISTINCT RELATIVE_PATH, CHUNK
    FROM DOCS_CHUNKS_TABLE
    WHERE CHUNK_INDEX = 0
),
docs_category_cte AS (
    SELECT 
        RELATIVE_PATH,
        TRIM(
            SNOWFLAKE.CORTEX.CLASSIFY_TEXT(
                'Title: ' || RELATIVE_PATH || ' Content: ' || CHUNK,
                ['Travel Guide', 'Itinerary', 'Unknown']
            )['label'], '"'
        ) AS CATEGORY
    FROM unique_documents
)
SELECT * FROM docs_category_cte;

UPDATE DOCS_CHUNKS_TABLE
SET CATEGORY = dc.CATEGORY
FROM DOCS_CATEGORIES dc
WHERE DOCS_CHUNKS_TABLE.RELATIVE_PATH = dc.RELATIVE_PATH;

-- 10. Create Cortex Search Service
CREATE OR REPLACE CORTEX SEARCH SERVICE TRAVEL_SEARCH_SERVICE
ON chunk
ATTRIBUTES category
WAREHOUSE = COMPUTE_WH
TARGET_LAG = '1 minute'
AS (
    SELECT 
        chunk,
        chunk_index,
        relative_path,
        file_url,
        category
    FROM DOCS_CHUNKS_TABLE
);

-- 11. Test Data and Search
SELECT * FROM TRAVEL_DB.PUBLIC.FLIGHT_DATA;
SELECT * FROM TRAVEL_DB.PUBLIC.HOTEL_DATA;
SELECT * FROM SNOWFLAKE.CORTEX.SEARCH('TRAVEL_SEARCH_SERVICE', 'Give me a 3-day itinerary for chennai');
