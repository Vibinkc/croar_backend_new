import json
import os
import sqlite3

CACHE_DB_PATH = os.path.join(os.path.dirname(__file__), "sourcing_cache.db")


class SourcingCache:
    def __init__(self):
        self.db_path = CACHE_DB_PATH
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS search_cache (
                    query_key TEXT PRIMARY KEY,
                    results_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS profile_cache (
                    url TEXT PRIMARY KEY,
                    details_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def get_search_cache(self, query: str, location: str, platform: str, page: int):
        key = f"{platform}:{query.lower().strip()}:{location.lower().strip()}:{page}"
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT results_json FROM search_cache WHERE query_key = ?", (key,))
                row = cursor.fetchone()
                if row:
                    print(f"DEBUG CACHE: Hit search cache for {key}")
                    return json.loads(row[0])
        except Exception as e:
            print(f"DEBUG CACHE ERROR: {e}")
        return None

    def set_search_cache(self, query: str, location: str, platform: str, page: int, results):
        key = f"{platform}:{query.lower().strip()}:{location.lower().strip()}:{page}"
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO search_cache (query_key, results_json) VALUES (?, ?)",
                    (key, json.dumps(results)),
                )
                conn.commit()
                print(f"DEBUG CACHE: Saved search cache for {key}")
        except Exception as e:
            print(f"DEBUG CACHE ERROR: {e}")

    def get_profile_cache(self, url: str):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT details_json FROM profile_cache WHERE url = ?", (url,))
                row = cursor.fetchone()
                if row:
                    print(f"DEBUG CACHE: Hit profile cache for {url}")
                    return json.loads(row[0])
        except Exception as e:
            print(f"DEBUG CACHE ERROR: {e}")
        return None

    def set_profile_cache(self, url: str, details):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO profile_cache (url, details_json) VALUES (?, ?)",
                    (url, json.dumps(details)),
                )
                conn.commit()
                print(f"DEBUG CACHE: Saved profile cache for {url}")
        except Exception as e:
            print(f"DEBUG CACHE ERROR: {e}")


sourcing_cache = SourcingCache()
