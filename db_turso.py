import os
import logging
import sqlite3

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("Chrono")

def get_db_connection():
    try:
        conn = sqlite3.connect("chrono_local.db", check_same_thread=False)
        return conn
    except Exception as e:
        logger.error(f"Error connecting to local SQLite: {e}")
        return None

def init_db():
    conn = get_db_connection()
    if not conn:
        return
    
    cursor = conn.cursor()
    
    # Alliances table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alliances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT,
            name TEXT,
            UNIQUE(server_id, name)
        )
    """)
    
    # Server Players table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS server_players (
            fid TEXT PRIMARY KEY,
            server_id TEXT,
            discord_user_id TEXT,
            alliance_id INTEGER,
            name TEXT,
            furnace_level INTEGER,
            power INTEGER,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (alliance_id) REFERENCES alliances (id)
        )
    """)
    
    # Gift Codes table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gift_codes (
            code TEXT PRIMARY KEY,
            status TEXT,
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # User Giftcodes (Redemption history)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_giftcodes (
            fid TEXT,
            giftcode TEXT,
            status TEXT,
            redeemed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (fid, giftcode),
            FOREIGN KEY (giftcode) REFERENCES gift_codes (code)
        )
    """)
    
    # Bot Settings
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_settings (
            guild_id TEXT PRIMARY KEY,
            admin_role_id TEXT,
            giftcode_channel_id TEXT,
            giftcode_dashboard_id TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Legacy timer data (migrating from timers.json/Supabase if needed)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS legacy_bot_data (
            guild_id TEXT PRIMARY KEY,
            data TEXT
        )
    """)
    
    # Run migrations
    try:
        cursor.execute("ALTER TABLE bot_settings ADD COLUMN giftcode_dashboard_id TEXT")
    except Exception as e:
        # Ignore if the column already exists
        if "duplicate column name" not in str(e).lower() and "already exists" not in str(e).lower():
            logger.debug(f"Migration notice: {e}")

    conn.commit()
    conn.close()
    logger.info("Turso database initialized successfully.")

def load_legacy_data() -> dict:
    conn = get_db_connection()
    if not conn: return {}
    cursor = conn.cursor()
    cursor.execute("SELECT guild_id, data FROM legacy_bot_data")
    rows = cursor.fetchall()
    conn.close()
    
    import json
    result = {}
    for gid, data in rows:
        result[gid] = json.loads(data)
    return result

def save_legacy_data(data: dict):
    conn = get_db_connection()
    if not conn: return
    cursor = conn.cursor()
    import json
    for gid, gdata in data.items():
        cursor.execute("""
            INSERT OR REPLACE INTO legacy_bot_data (guild_id, data)
            VALUES (?, ?)
        """, (str(gid), json.dumps(gdata)))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
