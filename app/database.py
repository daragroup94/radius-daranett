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
