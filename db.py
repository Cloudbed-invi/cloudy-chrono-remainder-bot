"""
Supabase-backed data persistence for Chrono Cloudy.

Drop-in replacement for the old timers.json file I/O.
Uses a single `bot_data` table with one row per guild/context,
storing the full data dict as JSONB.

Requires env vars: SUPABASE_URL, SUPABASE_KEY
"""

import os
import json
import logging
from supabase import create_client, Client

logger = logging.getLogger("Chrono")

# --- Supabase Client ---
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

_supabase: Client = None

def _get_client() -> Client:
    global _supabase
    if _supabase is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            logger.error("SUPABASE_URL or SUPABASE_KEY not set!")
            raise RuntimeError("Missing Supabase credentials")
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase

TABLE = "bot_data"

# --- Public API (same signature as old file I/O) ---

def load_data() -> dict:
    """Load all guild data from Supabase. Returns {guild_id: data_dict}."""
    try:
        client = _get_client()
        response = client.table(TABLE).select("guild_id, data").execute()
        result = {}
        for row in response.data:
            guild_id = row["guild_id"]
            data = row["data"]
            # Handle case where data might be stored as string
            if isinstance(data, str):
                data = json.loads(data)
            result[guild_id] = data
        return result
    except Exception as e:
        logger.error(f"Supabase load_data error: {e}")
        return {}


def save_data(data: dict) -> None:
    """Save all guild data to Supabase. Upserts each guild row."""
    if data is None or not isinstance(data, dict):
        return
    try:
        client = _get_client()
        for guild_id, guild_data in data.items():
            client.table(TABLE).upsert({
                "guild_id": str(guild_id),
                "data": guild_data
            }, on_conflict="guild_id").execute()
    except Exception as e:
        logger.error(f"Supabase save_data error: {e}")
