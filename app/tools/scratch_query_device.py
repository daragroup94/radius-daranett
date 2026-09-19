import sys
sys.path.append('/app')
from database import execute_query

def main():
    print("--- Searching by ID 27 ---")
    try:
        # Check standard tables
        for table in ['nas', 'infrastructure_coordinates', 'integrations']:
            try:
                res = execute_query(f"SELECT * FROM {table} WHERE id=27")
                if res:
                    print(f"Table '{table}' has ID 27: {res}")
            except Exception as e:
                # Table might not have 'id' column
                try:
                    res = execute_query(f"SELECT * FROM {table} LIMIT 1")
                    if res:
                        print(f"Table '{table}' columns: {list(res[0].keys())}")
                except Exception as e2:
                    print(f"Error checking '{table}': {e2}")
    except Exception as e:
        print(f"General error: {e}")

    print("\n--- Searching by IP 172.16.100.2 ---")
    # Search all tables for any column containing '172.16.100.2'
    for table in ['nas', 'infrastructure_coordinates', 'integrations']:
        try:
            rows = execute_query(f"SELECT * FROM {table}")
            for r in rows:
                for k, v in r.items():
                    if str(v) == '172.16.100.2':
                        print(f"Found in Table '{table}' (key={k}): {r}")
        except Exception as e:
            print(f"Error checking table '{table}' for IP: {e}")

if __name__ == '__main__':
    main()
