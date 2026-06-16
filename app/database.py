import psycopg2
from psycopg2 import pool as psycopg2_pool
from config import Config

_db_pool = None

def get_db_pool():
    global _db_pool
    if _db_pool is None:
        _db_pool = psycopg2_pool.ThreadedConnectionPool(
            minconn=3, maxconn=10,
            host=Config.DB_HOST,
            database=Config.DB_NAME,
            user=Config.DB_USER,
            password=Config.DB_PASSWORD,
            port=Config.DB_PORT
        )
    return _db_pool

def get_db_connection():
    return get_db_pool().getconn()

def execute_query(query, params=None, fetch=True):
    pool = get_db_pool()
    conn = pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        if fetch:
            result = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
            cur.close()
            conn.commit()
            return [dict(zip(columns, row)) for row in result]
        else:
            conn.commit()
            cur.close()
            return True
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        pool.putconn(conn)

def init_db_schema():
    """Menginisialisasi semua tabel spasial dan relasi koordinat jika belum ada."""
    try:
        # 1. customer_coordinates
        execute_query("""
            CREATE TABLE IF NOT EXISTS public.customer_coordinates (
                username VARCHAR(100) PRIMARY KEY,
                latitude NUMERIC(12, 9) NOT NULL,
                longitude NUMERIC(12, 9) NOT NULL,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """, fetch=False)

        # 2. nas_coordinates
        execute_query("""
            CREATE TABLE IF NOT EXISTS public.nas_coordinates (
                nasname VARCHAR(100) PRIMARY KEY,
                latitude NUMERIC(12, 9) NOT NULL,
                longitude NUMERIC(12, 9) NOT NULL,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """, fetch=False)

        # 3. ont_status
        execute_query("""
            CREATE TABLE IF NOT EXISTS public.ont_status (
                username VARCHAR(100) PRIMARY KEY,
                serial VARCHAR(100),
                model VARCHAR(100),
                manufacturer VARCHAR(100),
                rx_power VARCHAR(20),
                temperature VARCHAR(20),
                last_inform VARCHAR(100),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """, fetch=False)

        # 4. ont_serial_mapping
        execute_query("""
            CREATE TABLE IF NOT EXISTS public.ont_serial_mapping (
                username VARCHAR(100) PRIMARY KEY,
                serial_number VARCHAR(100) UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """, fetch=False)

        # 5. infrastructure_coordinates
        execute_query("""
            CREATE TABLE IF NOT EXISTS public.infrastructure_coordinates (
                id SERIAL PRIMARY KEY,
                name VARCHAR(150) NOT NULL,
                type VARCHAR(50) NOT NULL,
                latitude NUMERIC(12, 9) NOT NULL,
                longitude NUMERIC(12, 9) NOT NULL,
                details TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """, fetch=False)
        execute_query("""
            ALTER TABLE public.infrastructure_coordinates ADD COLUMN IF NOT EXISTS icon VARCHAR(50)
        """, fetch=False)
        execute_query("""
            ALTER TABLE public.infrastructure_coordinates ADD COLUMN IF NOT EXISTS status VARCHAR(50) DEFAULT 'online'
        """, fetch=False)

        # 6. infrastructure_connections
        execute_query("""
            CREATE TABLE IF NOT EXISTS public.infrastructure_connections (
                id SERIAL PRIMARY KEY,
                src_id INTEGER NOT NULL,
                dst_id INTEGER NOT NULL,
                coordinates JSONB DEFAULT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(src_id, dst_id)
            )
        """, fetch=False)
        execute_query("""
            ALTER TABLE public.infrastructure_connections ADD COLUMN IF NOT EXISTS is_disconnected BOOLEAN DEFAULT FALSE
        """, fetch=False)

        # 7. cable_connections
        execute_query("""
            CREATE TABLE IF NOT EXISTS public.cable_connections (
                id SERIAL PRIMARY KEY,
                odp_id INTEGER NOT NULL,
                customer_username VARCHAR(100) NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(odp_id, customer_username)
            )
        """, fetch=False)
        execute_query("""
            ALTER TABLE public.cable_connections ADD COLUMN IF NOT EXISTS coordinates JSONB DEFAULT NULL
        """, fetch=False)

        # 8. ont_signal_history
        execute_query("""
            CREATE TABLE IF NOT EXISTS public.ont_signal_history (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) NOT NULL,
                rx_power VARCHAR(20) NOT NULL,
                recorded_at TIMESTAMP DEFAULT NOW()
            )
        """, fetch=False)

        # 9. ont_health_issues
        execute_query("""
            CREATE TABLE IF NOT EXISTS public.ont_health_issues (
                username VARCHAR(100) PRIMARY KEY,
                serial VARCHAR(100),
                model VARCHAR(100),
                manufacturer VARCHAR(100),
                rx_power VARCHAR(20),
                temperature VARCHAR(20),
                pon_mode VARCHAR(50),
                last_inform VARCHAR(100),
                detected_at TIMESTAMP DEFAULT NOW()
            )
        """, fetch=False)
        execute_query("ALTER TABLE public.ont_health_issues ADD COLUMN IF NOT EXISTS pon_mode VARCHAR(50)", fetch=False)

        # 10. integrations
        execute_query("""
            CREATE TABLE IF NOT EXISTS public.integrations (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                type VARCHAR(50) DEFAULT 'mikrotik' NOT NULL,
                host VARCHAR(255) NOT NULL,
                port INTEGER,
                username VARCHAR(100),
                password VARCHAR(255),
                description TEXT,
                is_active BOOLEAN DEFAULT true,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """, fetch=False)

        print("[database] Database schema initialized/verified successfully.")
    except Exception as e:
        print(f"[database] Error initializing database schema: {str(e)}")


