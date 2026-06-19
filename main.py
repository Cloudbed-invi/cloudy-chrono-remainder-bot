import os
import sys

# --- SSL Fix for Windows ---
import ssl
import certifi

_create_default_context = ssl.create_default_context

def create_default_context(purpose=ssl.Purpose.SERVER_AUTH, *, cafile=None, capath=None, cadata=None):
    if cafile is None:
        cafile = certifi.where()
    return _create_default_context(purpose=purpose, cafile=cafile, capath=capath, cadata=cadata)

ssl.create_default_context = create_default_context
# ---------------------------

print("--- CHRONO CLOUDY STARTUP ---")

# --- Health-Check Server (Render Keep-Alive) ---
from aiohttp import web

async def health_handler(request):
    return web.Response(text="OK")

async def start_health_server():
    try:
        app = web.Application()
        app.router.add_get("/health", health_handler)
        app.router.add_get("/", health_handler)
        port = int(os.environ.get("PORT", 8080))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        print(f"Health server running on port {port}")
    except Exception as e:
        print(f"Failed to start health server: {e}") 
# ---------------------------

import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import time
import asyncio
import re
import logging
from typing import Any
from datetime import datetime, timedelta, timezone
import zoneinfo
import groq

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger("Chrono")

# --- Configuration ---
from dotenv import load_dotenv
load_dotenv()
TOKEN = os.getenv("TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

groq_client = None
if GROQ_API_KEY:
    groq_client = groq.AsyncGroq(api_key=GROQ_API_KEY)

from db_turso import init_db
init_db()

DUMMY_SPACER = "https://dummyimage.com/600x1/2f3136/2f3136.png"

DM_TEMPLATES = {
    "Custom": {"emoji": "✏️", "desc": "Enter manually", "label": None, "time": None, "recur": None, "adv": None},
    "Test Template": {"emoji": "🧪", "desc": "Auto-fills 'Test Event'", "label": "Test Event", "time": "1m", "recur": None, "adv": None},
    "Internal": {"emoji": "🏰", "desc": "Internal Castle (28d cycle)", "label": "Internal Castle [Battle]", "time": None, "recur": "28d", "adv": "5h | 30m, 5m"},
    "SvS": {"emoji": "⚔️", "desc": "SvS Battle (28d cycle)", "label": "SvS Castle Battle", "time": None, "recur": "28d", "adv": "5h | 2h, 1h"},
    "Arena": {"emoji": "🛡️", "desc": "Daily Arena Reset", "label": "Arena Reset", "time": None, "recur": "24h", "adv": "5m"},
    "Bear": {"emoji": "🐻", "desc": "Bear Trap (47h 30m)", "label": "🐻 Bear Trap", "time": None, "recur": "47h 30m", "adv": "30m | 5m"},
    "Joe": {"emoji": "🤡", "desc": "Crazy Joe (40m)", "label": "🤡 Crazy Joe", "time": None, "recur": "0", "adv": "40m | 5m"},
}

# --- Bot Setup ---
import sqlite3

if not os.path.exists('db'):
    os.makedirs('db')
    print("db folder created")

databases = {
    "conn_alliance": "db/alliance.sqlite",
    "conn_giftcode": "db/giftcode.sqlite",
    "conn_changes": "db/changes.sqlite",
    "conn_users": "db/users.sqlite",
    "conn_settings": "db/settings.sqlite",
}

connections = {name: sqlite3.connect(path) for name, path in databases.items()}

def create_tables():
    with connections["conn_changes"] as conn_changes:
        conn_changes.execute('''CREATE TABLE IF NOT EXISTS nickname_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            fid INTEGER, 
            old_nickname TEXT, 
            new_nickname TEXT, 
            change_date TEXT
        )''')
        conn_changes.execute('''CREATE TABLE IF NOT EXISTS furnace_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            fid INTEGER, 
            old_furnace_lv INTEGER, 
            new_furnace_lv INTEGER, 
            change_date TEXT
        )''')

    with connections["conn_settings"] as conn_settings:
        conn_settings.execute('''CREATE TABLE IF NOT EXISTS botsettings (
            id INTEGER PRIMARY KEY, 
            channelid INTEGER, 
            giftcodestatus TEXT 
        )''')
        conn_settings.execute('''CREATE TABLE IF NOT EXISTS admin (
            id INTEGER PRIMARY KEY, 
            is_initial INTEGER
        )''')

    with connections["conn_users"] as conn_users:
        conn_users.execute('''CREATE TABLE IF NOT EXISTS users (
            fid INTEGER PRIMARY KEY, 
            nickname TEXT, 
            furnace_lv INTEGER DEFAULT 0, 
            kid INTEGER, 
            stove_lv_content TEXT, 
            alliance TEXT
        )''')

    with connections["conn_giftcode"] as conn_giftcode:
        conn_giftcode.execute('''CREATE TABLE IF NOT EXISTS gift_codes (
            giftcode TEXT PRIMARY KEY, 
            date TEXT
        )''')
        conn_giftcode.execute('''CREATE TABLE IF NOT EXISTS user_giftcodes (
            fid INTEGER, 
            giftcode TEXT, 
            status TEXT, 
            PRIMARY KEY (fid, giftcode),
            FOREIGN KEY (giftcode) REFERENCES gift_codes (giftcode)
        )''')

    with connections["conn_alliance"] as conn_alliance:
        conn_alliance.execute('''CREATE TABLE IF NOT EXISTS alliancesettings (
            alliance_id INTEGER PRIMARY KEY, 
            channel_id INTEGER, 
            interval INTEGER
        )''')
        conn_alliance.execute('''CREATE TABLE IF NOT EXISTS alliance_list (
            alliance_id INTEGER PRIMARY KEY, 
            name TEXT
        )''')

create_tables()

class StratusBot(commands.Bot):
    def __init__(self):
        # Optimization for 512MB RAM: Only enable strictly needed intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True   # Needed for role fetching
        # Presences consume massive amounts of RAM: KEEP DISABLED
        intents.presences = False 
        
        # Max_messages limits the internal memory cache to 10 (default 1000)
        super().__init__(command_prefix="!", intents=intents, max_messages=10)

    async def setup_hook(self):
        # Start health check immediately, don't wait for Discord connection
        await start_health_server()
        
        # Register Persistent Views
        self.add_view(DiceView())
        
        # Legacy cogs removed.
        
        # Force Sync Slash Commands
        logger.info("Forcing Command Tree Sync...")
        await self.tree.sync()
        logger.info("Command Tree Synced!")

bot = StratusBot()

# --- Data Management (Turso Legacy Storage) ---
from db_turso import load_legacy_data as load_data, save_legacy_data as save_data

# --- Autocomplete Helper ---
async def timer_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    data = load_data()
    guild_id = str(interaction.guild_id)
    choices = []
    if guild_id in data and "timers" in data[guild_id]:
        # Filter matching timers
        for t in data[guild_id]["timers"]:
            if current.lower() in t['label'].lower():
                choices.append(app_commands.Choice(name=t['label'], value=t['label']))
    # Return top 25 matches (Discord limit)
    return choices[:25]

# --- Google Calendar Helper ---
def generate_gcal_link(label: str, start_epoch: int, duration_seconds: int = 3600) -> str:
    """Generates a Google Calendar 'Add to Calendar' link."""
    start_dt = datetime.fromtimestamp(start_epoch, timezone.utc)
    end_dt = start_dt + timedelta(seconds=duration_seconds)
    
    # Format: YYYYMMDDTHHMMSSZ
    fmt = "%Y%m%dT%H%M%SZ"
    dates = f"{start_dt.strftime(fmt)}/{end_dt.strftime(fmt)}"
    
    # Minimal URL encoding (manual for safety/speed)
    import urllib.parse
    params = {
        "action": "TEMPLATE",
        "text": label,
        "dates": dates,
        "details": "Scheduled via Chrono Cloudy"
    }
    query = urllib.parse.urlencode(params)
    return f"https://www.google.com/calendar/render?{query}"

# --- User Timezone Helpers ---
def get_user_tz_str(user_id: int) -> str:
    data = load_data()
    prefs = data.get("USER_PREFS", {})
    return prefs.get(str(user_id), "UTC")

def set_user_tz_str(user_id: int, tz_str: str) -> bool:
    data = load_data()
    if "USER_PREFS" not in data:
        data["USER_PREFS"] = {}
    
    try:
        if tz_str.upper() == "UTC":
            data["USER_PREFS"][str(user_id)] = "UTC"
        else:
            zoneinfo.ZoneInfo(tz_str)
            data["USER_PREFS"][str(user_id)] = tz_str
        save_data(data)
        return True
    except:
        return False

# --- Helpers ---
def parse_duration_string(input_str: str) -> int:
    if not input_str: return 0
    clean_str = input_str.strip().lower()
    
    # Check for plain number (default to minutes)
    if clean_str.isdigit():
        return int(clean_str) * 60
        
    # Composite Parser (e.g. "47h 30m", "1d 2h")
    # Finds all pairs of (number, unit)
    matches = re.findall(r"(\d+)\s*([a-z]+)", clean_str)
    
    if not matches:
        # No units found, and wasn't a plain number.
        raise ValueError(f"Invalid Duration: '{input_str}'. Use '30m', '1h', '1d', or '1h 30m'.")
        
    total_seconds = 0
    valid_units = ['m', 'min', 'mins', 'h', 'hr', 'hour', 'hours', 'd', 'day', 'days']
    
    for val_str, unit in matches:
        if unit not in valid_units:
             raise ValueError(f"Invalid Unit: '{unit}' in '{input_str}'.")
             
        val = int(val_str)
        if unit in ['m', 'min', 'mins']: total_seconds += val * 60
        elif unit in ['h', 'hr', 'hour', 'hours']: total_seconds += val * 3600
        elif unit in ['d', 'day', 'days']: total_seconds += val * 86400

    return total_seconds

def parse_reminders_string(input_str: str) -> list:
    if not input_str: return []
    try:
        parts = [p.strip() for p in input_str.split(',')]
        return [parse_duration_string(p) for p in parts if p]
    except: return []

def parse_time_input(user_input: str, mode: str = "smart", user_tz_str: str = "UTC") -> int | tuple[str, str]:
    user_input = user_input.strip().lower()
    
    try:
        user_tz = zoneinfo.ZoneInfo(user_tz_str) if user_tz_str.upper() != "UTC" else timezone.utc
    except:
        user_tz = timezone.utc
        
    current_local = datetime.now(user_tz)
    current_utc = datetime.now(timezone.utc)
    
    if mode == "smart":
        try: return parse_time_input(user_input, "utc_custom", user_tz_str)
        except: pass
        try: return parse_time_input(user_input, "utc_date_only", user_tz_str)
        except: pass
        if re.match(r"^\d{1,2}:\d{2}$", user_input):
             try: return parse_time_input(user_input, "utc_today", user_tz_str)
             except: pass
        try: return parse_time_input(user_input, "duration", user_tz_str)
        except: pass
        raise ValueError(f"Invalid Time. Use '10m', '14:00' ({user_tz_str}), or 'YYYY-MM-DD'.")

    if mode == "duration":
        # Don't catch/mask here, let parse_duration_string error bubble up
        seconds = parse_duration_string(user_input)
        return int((current_utc + timedelta(seconds=seconds)).timestamp())
        
    elif mode == "utc_today":
        match = re.match(r"^(\d{1,2}):(\d{2})$", user_input)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            if not (0 <= hour <= 23 and 0 <= minute <= 59): raise ValueError("Time out of range.")
            target = current_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            return int(target.timestamp())
        raise ValueError("Invalid Format.")

    elif mode == "utc_tomorrow":
        match = re.match(r"^(\d{1,2}):(\d{2})$", user_input)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            target = current_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            target += timedelta(days=1)
            return int(target.timestamp())
        raise ValueError("Invalid Format.")

    elif mode == "utc_custom":
        formats = [
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
            "%d-%m-%Y %H:%M",
            "%d/%m/%Y %H:%M"
        ]
        for fmt in formats:
            try:
                user_tz = zoneinfo.ZoneInfo(user_tz_str) if user_tz_str.upper() != "UTC" else timezone.utc
            except:
                user_tz = timezone.utc
            
            try:
                dt = datetime.strptime(user_input, fmt)
                dt = dt.replace(tzinfo=user_tz)
                return int(dt.timestamp())
            except ValueError: continue
        raise ValueError("Invalid Format.")

    elif mode == "utc_date_only":
        formats = [
            "%Y-%m-%d",
            "%Y/%m/%d",
            "%d-%m-%Y",
            "%d/%m/%Y"
        ]
        for fmt in formats:
            try:
                user_tz = zoneinfo.ZoneInfo(user_tz_str) if user_tz_str.upper() != "UTC" else timezone.utc
            except:
                user_tz = timezone.utc

            try:
                dt = datetime.strptime(user_input, fmt)
                dt = dt.replace(tzinfo=user_tz)
                return ("DATE_ONLY", dt.strftime("%Y-%m-%d"))
            except ValueError: continue
        raise ValueError("Invalid Format.")
    
    raise ValueError("Invalid time expression.")

def get_duration_str(start: int, end: int) -> str:
    diff = end - start
    if diff < 0: return "Unknown"
    days = diff // 86400
    rem = diff % 86400
    hours = rem // 3600
    mins = (rem % 3600) // 60
    parts = []
    if days > 0: parts.append(f"{days}d")
    if hours > 0: parts.append(f"{hours}h")
    if mins > 0: parts.append(f"{mins}m")
    if not parts: return "0m"
    return " ".join(parts)

def get_interval_str(seconds: int) -> str:
    if seconds == 0: return "None"
    return get_duration_str(0, seconds)

def get_next_cycle(start_year: int, start_month: int, start_day: int, hour: int = 12) -> int:
    """Calculates next occurrence of a 28-day cycle from a start date."""
    ref_date = datetime(start_year, start_month, start_day, hour, 0, 0, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    
    # If reference is future, use it
    if ref_date > now: return int(ref_date.timestamp())
    
    # Otherwise, add 28 days until future
    # Optimization: Calculate weeks difference directly
    diff = now - ref_date
    weeks_passed = diff.days // 7
    cycles_passed = weeks_passed // 4
    
    next_date = ref_date + timedelta(weeks=(cycles_passed + 1) * 4)
    # Ensure it's engaging in the future (simple check)
    if next_date <= now: next_date += timedelta(weeks=4)
    
    return int(next_date.timestamp())

def get_next_foundry_target() -> int:
    """Returns next Wednesday 00:00 UTC (Time Selection Phase) for a 14-day cycle.
    Based on the rule that the week of Feb 23, 2026 (Wed Feb 25) is an OFF week,
    meaning the next active Wednesday is March 4, 2026."""
    now = datetime.now(timezone.utc)
    
    # 1. Find the upcoming Wednesday (Weekday 2 is Wednesday)
    days_ahead = (2 - now.weekday()) % 7
    if days_ahead == 0 and now.hour > 20: 
         days_ahead = 7
    
    target_wed = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
    
    # 2. Determine if this Wednesday is an "On" or "Off" week.
    # Known ON week Wednesday: March 4, 2026 UTC
    reference_wed = datetime(2026, 3, 4, tzinfo=timezone.utc)
    
    # Calculate days difference between our target Wednesday and the reference date
    days_diff = (target_wed - reference_wed).days
    
    # If the difference is not a multiple of 14, it's an OFF week, so add 7 days to get to the ON week
    if days_diff % 14 != 0:
        target_wed += timedelta(days=7)
        
    return int(target_wed.timestamp())

def get_next_sunday_from_now() -> int:
    """Returns next Sunday relative to now."""
    now = datetime.now(timezone.utc)
    days_ahead = (6 - now.weekday()) % 7 # Sunday is 6
    if days_ahead == 0: days_ahead = 7 # Next Sunday
    
    target = now + timedelta(days=days_ahead)
    target = target.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(target.timestamp())

# --- Discord Event Helpers ---
async def create_discord_event(guild: discord.Guild, label: str, start_epoch: int, duration_seconds: int = 900, description: str = None):
    try:
        now = datetime.now(timezone.utc)
        start_time = datetime.fromtimestamp(start_epoch, timezone.utc)
        
        # Discord validation: start_time must be in the future?
        # Actually for "Active Now" events, we might need to handle differently.
        # But if we strictly follow "Scheduled Event", it should be future.
        if start_time <= now:
            start_time = now + timedelta(seconds=5) # Buffer
            
        possible_end = start_time + timedelta(seconds=duration_seconds)
        
        # Determine strict end time for Discord (must be after start)
        end_time = possible_end
        if end_time <= start_time:
            end_time = start_time + timedelta(minutes=15)
            
        event = await guild.create_scheduled_event(
            name=label,
            start_time=start_time,
            end_time=end_time,
            entity_type=discord.EntityType.external,
            location="Chrono Dashboard",
            description=description or "Timer managed by Chrono Cloudy.",
            privacy_level=discord.PrivacyLevel.guild_only
        )
        logger.info(f"✅ Discord Event Created: {event.id} for '{label}'")
        return event.id
    except Exception as e:
        logger.error(f"Failed to create event: {e}")
        return None

async def delete_discord_event(guild: discord.Guild, event_id: int):
    if not event_id: return
    try:
        event = await guild.fetch_scheduled_event(event_id)
        await event.delete()
    except: pass

async def update_discord_event(guild: discord.Guild, event_id: int, label: str, start_epoch: int, duration_seconds: int):
    if not event_id: return
    try:
        event = await guild.fetch_scheduled_event(event_id)
        start_time = datetime.fromtimestamp(start_epoch, timezone.utc)
        end_time = start_time + timedelta(seconds=duration_seconds)
        
        await event.edit(
            name=label,
            start_time=start_time,
            end_time=end_time
        )
    except: pass

def is_admin(interaction: discord.Interaction) -> bool:
    """Consolidated Admin Check"""
    return interaction.user.guild_permissions.administrator

def check_permissions(interaction: discord.Interaction, owner_id: int) -> bool:
    """True if user is Owner OR has management perms OR outranks the owner."""
    if interaction.user.id == owner_id: return True
    if not interaction.guild: return False # In DMs, only owner can edit (checked above)
    if interaction.user.guild_permissions.administrator: return True
    if interaction.user.guild_permissions.manage_roles: return True
    if interaction.user.guild_permissions.manage_messages: return True
    
    # Hierarchy check
    owner_member = interaction.guild.get_member(owner_id)
    if owner_member and interaction.user.top_role.position > owner_member.top_role.position:
        return True
    return False

# --- Foundry State ---
user_foundry_state: dict[int, dict[str, Any]] = {} # {user_id: {"step": "awaiting_time", "guild_id": 123, "channel_id": 456}}
user_cycle_states: dict[int, dict[str, Any]] = {}

# --- DM Setup Wizard State ---
user_setup_state: dict[int, dict[str, Any]] = {}
# Format: {user_id: {"step": str, "guild_id": int, "data": {"label": ..., "end_epoch": ..., etc}}}

# --- UI Components ---
class EditTimerModal(discord.ui.Modal, title="Edit Timer"):
    def __init__(self, guild_id: str, timer_index: int, current_label: str):
        super().__init__()
        self.guild_id = guild_id
        self.timer_index = timer_index
        
        self.time_input = discord.ui.TextInput(
            label="New Time or Duration (Optional)", placeholder="Leave empty to keep current time.", required=False
        )
        self.add_item(self.time_input)

        self.recur_input = discord.ui.TextInput(
            label="Edit Interval (Optional)", placeholder="e.g. 5m, 24h. '0' to disable. Left=Keep", required=False
        )
        self.add_item(self.recur_input)

        # New: Duration & Reminders
        self.adv_input = discord.ui.TextInput(
             label="Duration | Reminder (Optional)", placeholder="1h | 10m, 5m", required=False
        )
        self.add_item(self.adv_input)
        
        self.image_input = discord.ui.TextInput(
            label="New Image/GIF URL (Optional)", placeholder="Paste link here... 'none' to clear.", required=False
        )
        self.add_item(self.image_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        new_end = None
        new_recur = None
        new_image = None
        new_duration = None
        new_reminders = None
        clear_image = False
        
        try:
            if self.time_input.value.strip():
                new_end = parse_time_input(self.time_input.value.strip(), "smart")
            if self.recur_input.value.strip():
                if self.recur_input.value.strip() == "0": new_recur = 0
                else: new_recur = parse_duration_string(self.recur_input.value.strip())
            if self.image_input.value.strip():
                if self.image_input.value.strip().lower() == "none": clear_image = True
                else: new_image = self.image_input.value.strip()
            
            # Parse Advanced
            if self.adv_input.value.strip():
                parts = self.adv_input.value.split('|')
                if len(parts) >= 1 and parts[0].strip():
                    new_duration = parse_duration_string(parts[0].strip())
                if len(parts) >= 2 and parts[1].strip():
                     new_reminders = parse_reminders_string(parts[1].strip())
                     
        except ValueError as e:
            await interaction.followup.send(f"❌ {str(e)}", ephemeral=True)
            return

        data = load_data()
        if self.guild_id in data and "timers" in data[self.guild_id]:
            timers = data[self.guild_id]["timers"]
            if 0 <= self.timer_index < len(timers):
                t = timers[self.timer_index]
                
                # Update core fields
                if new_end is not None:
                    t["end_epoch"] = new_end
                    t["start_epoch"] = int(time.time())
                    # Reset reminders on time change
                    t["sent_reminders"] = []
                if new_recur is not None:
                    t["recurrence_seconds"] = new_recur
                if new_image is not None:
                    t["image_url"] = new_image
                elif clear_image and "image_url" in t:
                    del t["image_url"]
                
                if new_duration is not None: t["event_duration"] = new_duration
                if new_reminders is not None: 
                    t["reminders"] = new_reminders
                    t["sent_reminders"] = []

                # Update Discord Event
                if t.get("discord_event_id"):
                    dur = t.get("event_duration", 900)
                    await update_discord_event(interaction.guild, t["discord_event_id"], t["label"], t["end_epoch"], dur)

                timers.sort(key=lambda x: x["end_epoch"])
                save_data(data)
                
                await update_dashboard(interaction.guild, data[self.guild_id], resend=True)
                msg = await interaction.followup.send(f"✅ Timer Updated!", ephemeral=True)
                await asyncio.sleep(5)
                try: await msg.delete()
                except: pass
            else:
                await interaction.followup.send(f"❌ Timer not found.", ephemeral=True)

class RecurringAlertView(discord.ui.View):
    def __init__(self, guild_id: str, timer_index: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.timer_index = timer_index
    
    @discord.ui.button(label="✏️ Edit Next Cycle", style=discord.ButtonStyle.gray, custom_id="btn_recur_edit_v10")
    async def edit_cycle(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Security Check
        data = load_data()
        try:
            t = data[self.guild_id]["timers"][self.timer_index]
            if not check_permissions(interaction, t['owner_id']):
                await interaction.response.send_message("❌ **Access Denied.** You can only edit your own timers.", ephemeral=True)
                return
            await interaction.response.send_modal(EditTimerModal(self.guild_id, self.timer_index, "Next Cycle"))
        except:
             await interaction.response.send_message("❌ Timer not found.", ephemeral=True)

    @discord.ui.button(label="🗑️ Delete Next Cycle", style=discord.ButtonStyle.red, custom_id="btn_recur_del_v19")
    async def delete_cycle(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Security Check
        data = load_data()
        try:
            t = data[self.guild_id]["timers"][self.timer_index]
            if not check_permissions(interaction, t['owner_id']):
                await interaction.response.send_message("❌ **Access Denied.** You can only delete your own timers.", ephemeral=True)
                return
        except: pass

        await interaction.response.defer(ephemeral=True)
        if self.guild_id in data and "timers" in data[self.guild_id]:
            if 0 <= self.timer_index < len(data[self.guild_id]["timers"]):
                removed = data[self.guild_id]["timers"].pop(self.timer_index)
                
                # Delete Event
                if removed.get("discord_event_id"):
                    await delete_discord_event(interaction.guild, removed["discord_event_id"])
                
                save_data(data)
                
                await update_dashboard(interaction.guild, data[self.guild_id], resend=True)
                msg = await interaction.followup.send(f"✅ Cancelled **{removed['label']}**.", ephemeral=True)
                
                for child in self.children: child.disabled = True
                try: await interaction.message.edit(view=self)
                except: pass
                await asyncio.sleep(5)
                try: await msg.delete()
                except: pass

class ManageTimersSelect(discord.ui.Select):
    def __init__(self, timers):
        options = []
        now = int(time.time())
        valid_timers = timers[:25]
        for idx, t in enumerate(valid_timers):
            remaining = t['end_epoch'] - now
            mins_left = remaining // 60
            options.append(discord.SelectOption(label=t['label'][:50], value=str(idx), description=f"Ends in {mins_left}m" if remaining > 0 else "Expired"))
        super().__init__(placeholder="Select a timer to manage...", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        # Fix: UI Refresh Strategy
        idx = int(self.values[0])
        self.view.selected_index = idx
        
        # PERSIST SELECTION:
        for option in self.options:
            option.default = (option.value == self.values[0])

        # Enable Buttons
        for item in self.view.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = False
        
        # Force Redraw
        await interaction.response.edit_message(view=self.view)

class ManageTimersView(discord.ui.View):
    def __init__(self, guild_id: str, timers: list):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.timers = timers
        self.selected_index = None
        
        if not timers:
            self.add_item(discord.ui.Button(label="No Active Timers", disabled=True))
        else:
            self.add_item(ManageTimersSelect(timers))
            
            self.btn_edit = discord.ui.Button(label="✏️ Edit Time", style=discord.ButtonStyle.blurple, row=1, disabled=True)
            self.btn_edit.callback = self.on_edit_click
            self.add_item(self.btn_edit)

            self.btn_delete = discord.ui.Button(label="🗑️ Delete", style=discord.ButtonStyle.red, row=1, disabled=True)
            self.btn_delete.callback = self.on_delete_click
            self.add_item(self.btn_delete)

    async def on_edit_click(self, interaction: discord.Interaction):
        if self.selected_index is not None and 0 <= self.selected_index < len(self.timers):
             t = self.timers[self.selected_index]
             # Security Check
             if not check_permissions(interaction, t['owner_id']):
                 await interaction.response.send_message("❌ **Access Denied.** You can only edit your own timers.", ephemeral=True)
                 return
             await interaction.response.send_modal(EditTimerModal(self.guild_id, self.selected_index, t['label']))

    async def on_delete_click(self, interaction: discord.Interaction):
        if self.selected_index is not None:
            # Security Check Pre-Defer
            try:
                t = self.timers[self.selected_index]
                if not check_permissions(interaction, t['owner_id']):
                    await interaction.response.send_message("❌ **Access Denied.** You can only delete your own timers.", ephemeral=True)
                    return
            except: pass
            
            await interaction.response.defer(ephemeral=True)
            data = load_data()
            if self.guild_id in data and "timers" in data[self.guild_id]:
                if 0 <= self.selected_index < len(data[self.guild_id]["timers"]):
                    removed = data[self.guild_id]["timers"].pop(self.selected_index)
                    
                    # Delete Event
                    if removed.get("discord_event_id"):
                         await delete_discord_event(interaction.guild, removed["discord_event_id"])
                    
                    save_data(data)
                    await update_dashboard(interaction.guild, data[self.guild_id], resend=True)
                    
                    msg = await interaction.followup.send(f"✅ Deleted **{removed['label']}**", ephemeral=True)
                    await asyncio.sleep(5)
                    try: await msg.delete()
                    except: pass
                    try: await interaction.message.delete()
                    except: pass

class TimerDetailsModal(discord.ui.Modal, title="Configure Operation"):
    def __init__(self, mode: str, notify_method: str, role_id: int, user_tz: str, default_label: str | None = None, default_time: str | None = None, template_type: str | None = None):
        super().__init__()
        self.mode = mode
        self.notify_method = notify_method
        self.role_id = role_id
        self.user_tz = user_tz
        
        self.label_input = discord.ui.TextInput(
            label="Event Label", placeholder="e.g. Server Restart", default=default_label, max_length=50
        )
        self.add_item(self.label_input)
        
        self.time_input = discord.ui.TextInput(
            label=f"Time Until Alert ({self.user_tz})", placeholder=f"e.g. 10m, 14:00, or YYYY-MM-DD", default=default_time, min_length=2, max_length=20
        )
        self.add_item(self.time_input)

        # Template Params
        def_recur = None
        def_adv = None
        
        if template_type and template_type in DM_TEMPLATES:
            tpl = DM_TEMPLATES[template_type]
            def_recur = tpl.get("recur")
            def_adv = tpl.get("adv")

        self.recur_input = discord.ui.TextInput(
            label="Repeat Interval (Optional)", placeholder="e.g. 5m, 24h", default=def_recur, required=False, max_length=10
        )
        self.add_item(self.recur_input)
        
        self.adv_input = discord.ui.TextInput(
             label="Duration | Reminders (Optional)", placeholder="e.g. 1h | 10m, 5m", default=def_adv, required=False, max_length=50
        )
        self.add_item(self.adv_input)

        self.desc_input = discord.ui.TextInput(
             label="Details / Message (Optional)", placeholder="e.g. Gather at Hive!", required=False, max_length=200, style=discord.TextStyle.long
        )
        self.add_item(self.desc_input)
        
        # NOTE: Reduced to 5 inputs (Discord Limit) - Removed Image Input

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        event_duration = 0
        reminders = []
        try:
            # Fix: Use self.mode if specific, otherwise fall back to smart
            # logic: if user picked "UTC Today", force that mode. If "duration", force that.
            # "smart" is not a dropdown option, but passed for "Custom" maybe? 
            # Check TimerWizardView.select_mode: options are duration, utc_today, utc_tomorrow, utc_custom
            parse_mode = self.mode if self.mode in ["utc_today", "utc_tomorrow", "duration", "utc_custom"] else "smart"
            end_epoch = parse_time_input(self.time_input.value, parse_mode, self.user_tz)
            
            if isinstance(end_epoch, tuple) and end_epoch[0] == "DATE_ONLY":
                raise ValueError(f"Please provide a specific time along with the date (e.g., '{end_epoch[1]} 14:30 {self.user_tz}').")
            
            recurrence_val = self.recur_input.value.strip()
            recurrence_seconds = 0
            if recurrence_val: recurrence_seconds = parse_duration_string(recurrence_val)
            
            # Parse Advanced
            if self.adv_input.value.strip():
                parts = self.adv_input.value.split('|')
                if len(parts) >= 1 and parts[0].strip():
                    event_duration = parse_duration_string(parts[0].strip())
                if len(parts) >= 2 and parts[1].strip():
                     reminders = parse_reminders_string(parts[1].strip())
        except ValueError as e:
            await interaction.followup.send(f"❌ {str(e)}", ephemeral=True)
            return

        await add_timer(
            interaction, 
            self.label_input.value, 
            int(end_epoch), # Type hint cast
            self.role_id, 
            self.notify_method, 
            self.mode, # Store original mode
            recurrence_seconds, 
            None, # Image URL removed from Modal due to limit
            event_duration=event_duration,
            reminders=reminders,
            description=self.desc_input.value.strip() or None
        )
        try: await interaction.message.edit(content="✅ **Configuration Saved**", view=None)
        except: pass

class TimerWizardView(discord.ui.View):
    def __init__(self, is_dm=False):
        super().__init__(timeout=300)
        self.template = "Custom"
        self.mode = "duration"
        self.notify_method = "⚠️ Message in Server (Ping @everyone)" # Changed default
        self.role_id = None
        self.foundry_lead = None 
        self.is_dm = is_dm
        
        if self.is_dm:
            self.notify_method = "🗣️ Share in Chat"
            # Modify Template Options
            # Remove Foundry, Internal, SvS, Bear, Joe (Keep Generic)
            # Actually user might want others, just Foundry Auto is specifically Server-based automation?
            # Let's just remove Foundry Auto for now as requested plan
            new_opts = [o for o in self.select_template.options if o.value != "Foundry"]
            self.select_template.options = new_opts
            
            # Modify Notify Options
            self.select_notify.options = [
                discord.SelectOption(label="🗣️ Share in Chat", description="Post in this channel", value="🗣️ Share in Chat", default=True),
                discord.SelectOption(label="🔒 Share in Private Message", description="DM you directly", value="📩 DM Me")
            ]

    @discord.ui.select(
        placeholder="Select Template...",
        options=[
            discord.SelectOption(label="Custom", description="Enter manually", emoji="✏️", default=True),
            discord.SelectOption(label="Test Template", description="Auto-fills 'Test Event'", emoji="🧪"),
            discord.SelectOption(label="Internal Castle", description="Next Cycle (Sat 12:00 UTC)", emoji="🏰", value="Internal"),
             discord.SelectOption(label="SvS Battle", description="Next Cycle (Sat 12:00 UTC)", emoji="⚔️", value="SvS"),
             discord.SelectOption(label="Arena Reset", description="Daily (23:55 UTC)", emoji="🛡️", value="Arena"),
             discord.SelectOption(label="🐻 Bear Trap", description="Alliance Event (30m)", emoji="🐻", value="Bear"),
             discord.SelectOption(label="🤡 Crazy Joe", description="Defense Waves (40m)", emoji="🤡", value="Joe"),
             discord.SelectOption(label="Foundry Auto", description="Auto-DM Lead on Wednesday (Bi-weekly)", emoji="🔥", value="Foundry"),
         ], row=0
     )
    async def select_template(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.template = select.values[0]
        
        # UI Persistence
        for option in select.options:
            option.default = (option.value == self.template) if option.value else (option.label == self.template)
        
        # Reset Child Items Logic
        # If Foundry, we need User Select
        if self.template == "Foundry":
            # Remove inapplicable items
            for child in self.children[:]: # Iterate copy
                if isinstance(child, (discord.ui.TextInput, discord.ui.Select, discord.ui.Button, discord.ui.UserSelect)):
                     if getattr(child, 'row', 0) in [1, 2, 3]: self.remove_item(child)
            
            # Add User Select (Dynamically)
            if not any(isinstance(x, discord.ui.UserSelect) for x in self.children):
                 self.user_select = discord.ui.UserSelect(placeholder="Select Foundry Lead (Manager)...", min_values=1, max_values=1, row=1)
                 self.user_select.callback = self.select_lead_callback
                 self.add_item(self.user_select)
                 
        else:
             pass 

        await interaction.response.edit_message(view=self)

    async def select_lead_callback(self, interaction: discord.Interaction):
        self.foundry_lead = self.user_select.values[0]
        await interaction.response.defer()

    @discord.ui.select(
        placeholder="Timer Input Mode...",
        options=[
            discord.SelectOption(label="⏳ Countdown", value="duration", default=True),
            discord.SelectOption(label="📅 UTC Time: Today", value="utc_today"),
            discord.SelectOption(label="🔮 UTC Time: Tomorrow", value="utc_tomorrow"),
            discord.SelectOption(label="📆 UTC Time: Pick Date", value="utc_custom"),
        ], row=1
    )
    async def select_mode(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.mode = select.values[0]
        # UI Persistence
        for option in select.options:
            option.default = (option.value == self.mode)
            
        await interaction.response.edit_message(view=self)

    @discord.ui.select(
        placeholder="Notification Mode...",
        options=[
            discord.SelectOption(label="📢 Message in Server (Ping Role)"),
            discord.SelectOption(label="⚠️ Message in Server (Ping @everyone)", default=True),
            discord.SelectOption(label="🔕 Message in Server (Silent)"),
            discord.SelectOption(label="📩 DM Me"),
        ], row=2
    )
    async def select_notify(self, interaction: discord.Interaction, select: discord.ui.Select):
        # Security: Blocking @everyone for non-admins
        if "everyone" in select.values[0] or "here" in select.values[0]:
            if not is_admin(interaction):
                await interaction.response.send_message("❌ **Permission Denied.** Only Administrators can use `@everyone`.", ephemeral=True)
                # Reset to default
                self.notify_method = "📢 Message in Server (Ping Role)" 
                # Reset UI Persistence
                for option in select.options:
                    option.default = (option.label == self.notify_method)
                await interaction.response.edit_message(view=self)
                return

        self.notify_method = select.values[0]
        
        # UI Persistence
        for option in select.options:
            option.default = (option.value == self.notify_method) if option.value else (option.label == self.notify_method)

        if "Ping Role" in self.notify_method:
            if self.select_role not in self.children: self.add_item(self.select_role)
        else:
            if self.select_role in self.children: self.remove_item(self.select_role)
            self.role_id = None
        await interaction.response.edit_message(view=self)

    @discord.ui.select(
        cls=discord.ui.RoleSelect, placeholder="Select Target Role...", min_values=0, max_values=1, row=3,
    )
    async def select_role(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        if select.values: self.role_id = select.values[0].id
        await interaction.response.defer()

    @discord.ui.button(label="➡️ Enter Details", style=discord.ButtonStyle.green, row=4)
    async def enter_details(self, interaction: discord.Interaction, button: discord.ui.Button):
        
        # Foundry Logic Override
        if self.template == "Foundry":
            if not self.foundry_lead:
                 await interaction.response.send_message("❌ Please select a **Foundry Lead** first.", ephemeral=True)
                 return
            
            # Create Special Timer Logic directly without Modal
            await interaction.response.defer(ephemeral=True)
            data = load_data()
            guild_id = str(interaction.guild_id)
            if guild_id not in data: data[guild_id] = {"timers": []}
            if "timers" not in data[guild_id]: data[guild_id]["timers"] = []

            # Check for existing Foundry Job
            jobs = [t for t in data[guild_id]["timers"] if t.get("type") == "foundry_job"]
            if jobs:
                await interaction.followup.send("❌ **Foundry Automation** is already active. Delete the old one first.", ephemeral=True)
                return

            label = f"🔥 Foundry Automation (Lead: {self.foundry_lead.mention})"
            end_epoch = get_next_foundry_target()
            
            new_job = {
                "label": label,
                "end_epoch": end_epoch,
                "start_epoch": int(time.time()),
                "owner_id": self.foundry_lead.id, # The Lead is the Owner
                "role_id": None,
                "notify_method": "DM", # Internal flag
                "mode": "auto",
                "recurrence_seconds": 604800, # 7 Days
                "type": "foundry_job",
                "reminders": [],
                "sent_reminders": []
            }
            
            data[guild_id]["timers"].append(new_job)
            data[guild_id]["timers"].sort(key=lambda x: x["end_epoch"])
            save_data(data)
            await update_dashboard(interaction.guild, data[guild_id], resend=True)
            await interaction.followup.send(f"✅ **Foundry Automation Active!**\nI will DM {self.foundry_lead.mention} every other Wednesday.", ephemeral=True)
            return

        def_label = None
        def_time = None
        
        tpl = DM_TEMPLATES.get(self.template)
        if tpl:
            def_label = tpl.get("label")
            def_time = tpl.get("time")

        if self.template in ["Internal", "SvS"]:
            # Auto-Create Logic (Skip Modal)
            is_internal = (self.template == "Internal")
            ref_day = 14 if is_internal else 28
            label_prefix = str(tpl["label"])
            
            # Calculate next occurrence
            next_ts = get_next_cycle(2026, 2, ref_day, 12)
            
            await interaction.response.defer(ephemeral=True)
            await add_timer(
                interaction, 
                label_prefix, 
                next_ts, 
                self.role_id, 
                self.notify_method, 
                "utc_today", # Mode is technically calculated, but we use this for internal consistency
                recurrence_seconds=2419200, # 28 days (4 weeks)
                reminders=[18000, 7200, 3600], # 5h, 2h, 1h reminders (approx)
                description="Auto-scheduled based on 4-week cycle."
            )
            try: await interaction.message.edit(content=f"✅ **{label_prefix}** Scheduled!", view=None)
            except: pass
            return

        elif self.template == "Arena":
             # Next 23:55 UTC
             now = datetime.now(timezone.utc)
             target = now.replace(hour=23, minute=55, second=0, microsecond=0)
             if target <= now: target += timedelta(days=1)
             def_time = target.strftime("%Y-%m-%d %H:%M") 
            
        user_tz = get_user_tz_str(interaction.user.id)
        modal = TimerDetailsModal(
            self.mode, self.notify_method, self.role_id, user_tz,
            def_label, def_time, template_type=self.template
        )
        await interaction.response.send_modal(modal)

# --- DM Setup Wizard Views ---

async def start_dm_setup(user: discord.User, guild_id: int):
    """Initiates the DM setup wizard for a user."""
    user_setup_state[user.id] = {
        "step": "awaiting_template",
        "guild_id": guild_id,
        "data": {
            "label": None,
            "end_epoch": None,
            "recurrence_seconds": 0,
            "reminders": [],
            "event_duration": 900,
            "notify_method": "⚠️ Message in Server (Ping @everyone)",
            "role_id": None,
            "description": None,
            "template": None,
        }
    }
    try:
        dm = await user.create_dm()
        # Build template select options
        options = []
        for key, tpl in DM_TEMPLATES.items():
            options.append(discord.SelectOption(label=key, description=tpl["desc"], emoji=tpl["emoji"], value=key))
        
        view = DMSetupTemplateView(user.id, options)
        await dm.send("**☁️ Chrono Setup Wizard**\n\nLet's set up a new alert step by step!\n\n**Step 1/5:** Choose a template or start Custom:", view=view)
    except discord.Forbidden:
        # User has DMs disabled
        del user_setup_state[user.id]
        return False
    return True

class DMSetupTemplateView(discord.ui.View):
    def __init__(self, user_id: int, options: list):
        super().__init__(timeout=300)
        self.user_id = user_id
        select = discord.ui.Select(placeholder="Choose a template...", options=options, row=0)
        select.callback = self.on_select
        self.add_item(select)
        
        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.red, row=1)
        cancel_btn.callback = self.on_cancel
        self.add_item(cancel_btn)

    async def on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your setup!", ephemeral=True)
            return
        
        template_key = interaction.data["values"][0]
        state = user_setup_state.get(self.user_id)
        if not state:
            await interaction.response.send_message("❌ Setup expired. Please start again.", ephemeral=True)
            return
        
        tpl = DM_TEMPLATES[template_key]
        state["data"]["template"] = template_key
        
        # Auto-fill from template
        if tpl.get("label"):
            state["data"]["label"] = tpl["label"]
        if tpl.get("recur"):
            try:
                state["data"]["recurrence_seconds"] = parse_duration_string(str(tpl["recur"])) if tpl["recur"] != "0" else 0
            except: pass
        if tpl.get("adv"):
            try:
                parts = str(tpl["adv"]).split('|')
                if len(parts) >= 1 and parts[0].strip():
                    state["data"]["event_duration"] = parse_duration_string(parts[0].strip())
                if len(parts) >= 2 and parts[1].strip():
                    state["data"]["reminders"] = parse_reminders_string(parts[1].strip())
            except: pass
        
        # Disable the view
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        
        # Auto-create for Internal/SvS (they have fixed cycle times)
        if template_key in ["Internal", "SvS"]:
            ref_day = 14 if template_key == "Internal" else 28
            next_ts = get_next_cycle(2026, 2, ref_day, 12)
            state["data"]["end_epoch"] = next_ts
            state["data"]["recurrence_seconds"] = 2419200  # 28 days
            state["data"]["reminders"] = [18000, 7200, 3600]
            # Skip to notify step
            state["step"] = "awaiting_notify"
            await send_notify_step(interaction.user, state)
            return
        
        # Arena auto-fills time
        if template_key == "Arena":
            now = datetime.now(timezone.utc)
            target = now.replace(hour=23, minute=55, second=0, microsecond=0)
            if target <= now: target += timedelta(days=1)
            state["data"]["end_epoch"] = int(target.timestamp())
        
        # If template gave us a label, skip to time
        if state["data"]["label"]:
            if state["data"]["end_epoch"]:
                # Both label and time set (Arena), go to reminders
                state["step"] = "awaiting_reminders"
                await interaction.channel.send("**Step 4/5:** Want early reminders?\nType reminder times separated by commas (e.g. `10m, 5m` or `1h, 30m`).\nReply **no** to skip.")
            else:
                state["step"] = "awaiting_time"
                await interaction.channel.send(f"**Step 3/5:** When should **{state['data']['label']}** fire? (All times in **UTC**)\n*Examples:* `10m`, `1h 30m`, `14:00` (today), `2026-03-10`")
        else:
            state["step"] = "awaiting_label"
            await interaction.channel.send("**Step 2/5:** What should we call this event?\n*Example:* `Server Restart`")

    async def on_cancel(self, interaction: discord.Interaction):
        if self.user_id in user_setup_state:
            del user_setup_state[self.user_id]
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="❌ Setup cancelled.", view=self)

async def send_notify_step(user: discord.User, state: dict):
    """Sends the notification method selection step."""
    state["step"] = "awaiting_notify"
    options = [
        discord.SelectOption(label="📢 Ping Role", value="role", description="Ping a specific role"),
        discord.SelectOption(label="⚠️ Ping @everyone", value="everyone", description="Ping @everyone"),
        discord.SelectOption(label="🔕 Silent", value="silent", description="No ping, just a message"),
        discord.SelectOption(label="📩 DM Me", value="dm", description="Send alert to your DMs"),
    ]
    view = DMSetupNotifyView(user.id, options, state)
    dm = await user.create_dm()
    await dm.send("**Step 4/5:** How should I notify when this fires?", view=view)

class DMSetupNotifyView(discord.ui.View):
    def __init__(self, user_id: int, options: list, state: dict):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.state = state
        select = discord.ui.Select(placeholder="Notification method...", options=options, row=0)
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your setup!", ephemeral=True)
            return
        
        choice = interaction.data["values"][0]
        state = user_setup_state.get(self.user_id)
        if not state:
            await interaction.response.send_message("❌ Setup expired.", ephemeral=True)
            return
        
        # Map choice to notify_method string
        method_map = {
            "role": "📢 Message in Server (Ping Role)",
            "everyone": "⚠️ Message in Server (Ping @everyone)",
            "silent": "🔕 Message in Server (Silent)",
            "dm": "📩 DM Me",
        }
        state["data"]["notify_method"] = method_map.get(choice, "🔕 Message in Server (Silent)")
        
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        
        if choice == "role":
            # Ask for role - use RoleSelect with guild context
            guild = bot.get_guild(state["guild_id"])
            if guild:
                state["step"] = "awaiting_role"
                view = DMSetupRoleView(self.user_id, state)
                await interaction.channel.send("**Step 4b/5:** Which role should I ping?", view=view)
                return
        
        # Skip to confirmation
        await send_confirm_step(interaction.user, state)

class DMSetupRoleView(discord.ui.View):
    def __init__(self, user_id: int, state: dict):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.state = state
        
        # We can't use discord.ui.RoleSelect in DMs, so ask user to type role name
        # Actually let's just list top roles as buttons or ask for ID
        # Simplest: ask user to type the role name
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

async def send_confirm_step(user: discord.User, state: dict):
    """Sends confirmation summary before creating the timer."""
    state["step"] = "awaiting_confirm"
    d = state["data"]
    
    # Build summary
    label = d.get("label", "Unknown")
    ts = d.get("end_epoch", 0)
    recur = d.get("recurrence_seconds", 0)
    reminders = d.get("reminders", [])
    notify = d.get("notify_method", "Silent")
    desc = d.get("description", "")
    
    summary = f"**Event:** {label}\n"
    summary += f"**Fires:** <t:{ts}:F> (<t:{ts}:R>)\n"
    if recur > 0:
        summary += f"**Repeats:** {get_interval_str(recur)}\n"
    if reminders:
        rem_strs = [get_interval_str(r) for r in reminders]
        summary += f"**Reminders:** {', '.join(rem_strs)} before\n"
    summary += f"**Notify:** {notify}\n"
    if desc:
        summary += f"**Details:** {desc}\n"
    
    view = DMSetupConfirmView(user.id, state)
    dm = await user.create_dm()
    await dm.send(f"**Step 5/5:** Does this look right?\n\n{summary}", view=view)

class DMSetupConfirmView(discord.ui.View):
    def __init__(self, user_id: int, state: dict):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.state = state
        
        confirm_btn = discord.ui.Button(label="✅ Create Timer", style=discord.ButtonStyle.green, row=0)
        confirm_btn.callback = self.on_confirm
        self.add_item(confirm_btn)
        
        cancel_btn = discord.ui.Button(label="❌ Cancel", style=discord.ButtonStyle.red, row=0)
        cancel_btn.callback = self.on_cancel
        self.add_item(cancel_btn)

    async def on_confirm(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your setup!", ephemeral=True)
            return
        
        state = user_setup_state.get(self.user_id)
        if not state:
            await interaction.response.send_message("❌ Setup expired.", ephemeral=True)
            return
        
        d = state["data"]
        guild = bot.get_guild(state["guild_id"])
        
        if not guild:
            await interaction.response.send_message("❌ Can't find the server anymore.", ephemeral=True)
            del user_setup_state[self.user_id]
            return
        
        await interaction.response.defer()
        
        # Create timer using add_timer_internal
        await add_timer_internal(
            guild,
            d["label"],
            d["end_epoch"],
            d.get("role_id"),
            d["notify_method"],
            "smart",
            d.get("recurrence_seconds", 0),
            None,  # image
            d.get("event_duration", 900),
            d.get("reminders", []),
            owner_id=interaction.user.id,
            description=d.get("description")
        )
        
        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(view=self)
        
        await interaction.channel.send(f"✅ **Timer Created!** `{d['label']}` is set in **{guild.name}**.")
        
        if self.user_id in user_setup_state:
            del user_setup_state[self.user_id]

    async def on_cancel(self, interaction: discord.Interaction):
        if self.user_id in user_setup_state:
            del user_setup_state[self.user_id]
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="❌ Setup cancelled.", view=self)

async def handle_dm_setup_step(message: discord.Message):
    """Handles text-based DM wizard steps."""
    uid = message.author.id
    state = user_setup_state.get(uid)
    if not state: return
    
    content = message.content.strip()
    step = state["step"]
    
    # Cancel keyword
    if content.lower() == "cancel":
        del user_setup_state[uid]
        await message.channel.send("❌ Setup cancelled.")
        return
    
    if step == "awaiting_label":
        if len(content) < 1 or len(content) > 50:
            await message.channel.send("❌ Label must be 1-50 characters. Try again:")
            return
        state["data"]["label"] = content
        state["step"] = "awaiting_time"
        await message.channel.send(f"Got it: **{content}**\n\n**Step 3/5:** When should it fire? (All times in **UTC**)\n*Examples:* `10m`, `1h 30m`, `14:00` (today), `2026-03-10`")
    
    elif step == "awaiting_time":
        try:
            result = parse_time_input(content, "smart")
            if isinstance(result, tuple) and result[0] == "DATE_ONLY":
                state["data"]["temp_date"] = result[1]
                state["step"] = "awaiting_time_of_day"
                await message.channel.send(f"📅 You entered the date **{result[1]}**.\nWhat time on that day should it fire? (**UTC Timezone**)\n*Examples:* `14:00`, `09:30`")
                return
                
            end_epoch = result
            state["data"]["end_epoch"] = end_epoch
            state["step"] = "awaiting_reminders"
            await message.channel.send(f"⏰ Set to <t:{end_epoch}:F> (<t:{end_epoch}:R>)\n\n**Step 4/5:** Want early reminders?\nType times separated by commas (e.g. `10m, 5m`) or reply **no** to skip.")
        except ValueError as e:
            await message.channel.send(f"❌ {str(e)}\nTry again (e.g. `10m`, `14:00`, `2026-03-10`):")

    elif step == "awaiting_time_of_day":
        # Combine the saved date and the new time
        date_str = state["data"].get("temp_date")
        combined_str = f"{date_str} {content}"
        try:
            end_epoch = parse_time_input(combined_str, "utc_custom")
            state["data"]["end_epoch"] = end_epoch
            if "temp_date" in state["data"]:
                del state["data"]["temp_date"]
            
            state["step"] = "awaiting_reminders"
            await message.channel.send(f"⏰ Set to <t:{end_epoch}:F> (<t:{end_epoch}:R>)\n\n**Step 4/5:** Want early reminders?\nType times separated by commas (e.g. `10m, 5m`) or reply **no** to skip.")
        except ValueError:
            await message.channel.send(f"❌ Invalid time format.\nTry again (e.g. `14:00`, `09:30`):")
    
    elif step == "awaiting_reminders":
        if content.lower() in ["no", "none", "skip", "n"]:
            state["step"] = "awaiting_recurrence"
            # Check if template already set recurrence
            if state["data"]["recurrence_seconds"] > 0:
                # Skip to notify
                await send_notify_step(message.author, state)
            else:
                await message.channel.send("**Step 5/5:** Should this repeat?\nType the interval (e.g. `24h`, `7d`) or reply **no** for one-time.")
        else:
            reminders = parse_reminders_string(content)
            if reminders:
                state["data"]["reminders"] = reminders
                rem_strs = [get_interval_str(r) for r in reminders]
                await message.channel.send(f"✅ Reminders set: {', '.join(rem_strs)} before.")
            
            # Check if template already set recurrence
            if state["data"]["recurrence_seconds"] > 0:
                await send_notify_step(message.author, state)
            else:
                state["step"] = "awaiting_recurrence"
                await message.channel.send("**Step 5/5:** Should this repeat?\nType the interval (e.g. `24h`, `7d`) or reply **no** for one-time.")
    
    elif step == "awaiting_recurrence":
        if content.lower() in ["no", "none", "skip", "n", "0"]:
            state["data"]["recurrence_seconds"] = 0
        else:
            try:
                recur = parse_duration_string(content)
                state["data"]["recurrence_seconds"] = recur
                await message.channel.send(f"🔄 Repeats every {get_interval_str(recur)}.")
            except ValueError as e:
                await message.channel.send(f"❌ {str(e)}\nTry again (e.g. `24h`, `7d`) or reply **no**:")
                return
        
        await send_notify_step(message.author, state)
    
    elif step == "awaiting_role":
        # User types a role name, we try to find it in the guild
        guild = bot.get_guild(state["guild_id"])
        if guild:
            # Try to find role by name (case-insensitive)
            found_role = None
            for role in guild.roles:
                if role.name.lower() == content.lower():
                    found_role = role
                    break
            
            if found_role:
                state["data"]["role_id"] = found_role.id
                await message.channel.send(f"✅ Will ping **{found_role.name}**.")
                await send_confirm_step(message.author, state)
            elif content.lower() == "skip":
                state["data"]["notify_method"] = "🔕 Message in Server (Silent)"
                state["data"]["role_id"] = None
                await message.channel.send("✅ Skipping role ping.")
                await send_confirm_step(message.author, state)
            else:
                # List available roles to help
                role_names = [r.name for r in guild.roles if not r.is_default() and not r.is_bot_managed()][:15]
                roles_str = ", ".join(f"`{r}`" for r in role_names)
                await message.channel.send(f"❌ Role not found. Available roles: {roles_str}\nType the exact role name, or reply **skip** to continue without pinging a role.")
        else:
            await message.channel.send("❌ Can't find the server.")
            del user_setup_state[uid]


class DMWizardStartView(discord.ui.View):
    """Ephemeral view to choose between DM wizard or classic menu."""
    def __init__(self, guild_id: int):
        super().__init__(timeout=120)
        self.guild_id = guild_id
    
    @discord.ui.button(label="💬 Setup via DM (Recommended)", style=discord.ButtonStyle.green, row=0)
    async def dm_setup(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        success = await start_dm_setup(interaction.user, self.guild_id)
        if success:
            await interaction.followup.send("📩 Check your DMs! I've sent you a setup wizard.", ephemeral=True)
        else:
            await interaction.followup.send("❌ I can't DM you. Please enable DMs from server members in your privacy settings, or use the classic menu.", ephemeral=True)
    
    @discord.ui.button(label="🪟 Classic Menu", style=discord.ButtonStyle.gray, row=0)
    async def classic_setup(self, interaction: discord.Interaction, button: discord.ui.Button):
        is_dm = interaction.guild is None
        await interaction.response.send_message(
            "**☁️ Chrono Dashboard**\nSet up a new alert:",
            view=TimerWizardView(is_dm=is_dm), ephemeral=True
        )


class DashboardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="  ➕ New Alert  ", style=discord.ButtonStyle.blurple, custom_id="btn_yeti_new")
    async def new_operation(self, interaction: discord.Interaction, button: discord.ui.Button):
        is_dm = interaction.guild is None
        if is_dm:
            # In DMs, go straight to classic menu
            await interaction.response.send_message(
                "**☁️ Chrono Dashboard**\nSet up a new alert:",
                view=TimerWizardView(is_dm=True), ephemeral=True
            )
        else:
            # In server, offer DM or Classic
            await interaction.response.send_message(
                "**☁️ New Alert**\nChoose your setup method:",
                view=DMWizardStartView(guild_id=interaction.guild_id), ephemeral=True
            )

    @discord.ui.button(label="  ⚙️ Manage Alerts  ", style=discord.ButtonStyle.gray, custom_id="btn_yeti_manage")
    async def manage_active(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_data()
        guild_id = str(interaction.guild_id)
        if guild_id in data and data[guild_id].get("timers"):
            await interaction.response.send_message(
                "**Manage Active Timers**\nSelect a timer to Edit or Delete:",
                view=ManageTimersView(guild_id, data[guild_id]["timers"]), ephemeral=True
            )
        else:
            await interaction.response.send_message("⚠️ No active timers to manage.", ephemeral=True)
    
    @discord.ui.button(label=" ❓ Guide ", style=discord.ButtonStyle.secondary, custom_id="btn_yeti_guide")
    async def show_guide(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="📚 Chrono Operations Guide", color=discord.Color.from_rgb(200, 200, 200))
        embed.description = "How to control the Chrono scheduler:"
        embed.add_field(name="🆕 New Operation", value="Set timers using **Countdown** (e.g. 10m) or **UTC Alarm** (e.g. 14:00).", inline=False)
        embed.add_field(name="🔁 Recurrence", value="Make alerts repeat automatically by setting an **Interval** (e.g. 24h for daily resets).", inline=False)
        embed.add_field(name="Frame Media", value="Use the **Frame Command** (`/timer`) or Buttons to attach GIFs.", inline=False)
        embed.add_field(name="⚙️ Management", value="Use **Manage Active** to Edit/Delete.", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

# --- Core Logic ---
async def add_timer(interaction: discord.Interaction, label: str, end_epoch: int, role_id: int, notify_method: str, mode: str, recurrence_seconds: int = 0, image_url: str = None, event_duration: int = 900, reminders: list = None, description: str = None):
    # Context ID (Guild OR User)
    context_id = str(interaction.guild_id) if interaction.guild else str(interaction.user.id)
    is_dm = interaction.guild is None

    data = load_data()
    if context_id not in data: data[context_id] = {"timers": []}
    if "timers" not in data[context_id]: data[context_id]["timers"] = []
    
    # Create Discord Event (Only if Guild)
    discord_event_id = None
    if not is_dm and mode != "silent":
         discord_event_id = await create_discord_event(interaction.guild, label, end_epoch, event_duration, description=description)
    
    # Save Timer
    new_timer = {
        "label": label,
        "end_epoch": end_epoch,
        "start_epoch": int(time.time()),
        "owner_id": interaction.user.id,
        "role_id": role_id if not is_dm else None,
        "notify_method": notify_method,
        "mode": mode,
        "recurrence_seconds": recurrence_seconds,
        "image_url": image_url,
        "discord_event_id": discord_event_id,
        "event_duration": event_duration,
        "reminders": reminders or [],
        "sent_reminders": [],
        "description": description
    }
    
    data[context_id]["timers"].append(new_timer)
    data[context_id]["timers"].sort(key=lambda x: x["end_epoch"])
    save_data(data)
    
    # Update Dashboard (Removed from here, moved to end)
    # try:
    #      if not is_dm: await update_dashboard(interaction.guild, data[context_id])
    # except: pass
    
    # Confirmation Embed
    ts = int(end_epoch)
    gcal_link = generate_gcal_link(label, end_epoch, event_duration)
    
    embed = discord.Embed(title="✅ Timer Set", color=discord.Color.green())
    desc = f"**{label}**\n📅 <t:{ts}:F> (<t:{ts}:R>)\n"
    if recurrence_seconds > 0:
        desc += f"🔄 Repeats: {get_interval_str(recurrence_seconds)}\n"
    
    desc += f"\n[📅 **Add to Google Calendar**]({gcal_link})"
    
    if is_dm:
        desc += "\n*(DM Mode: No Discord Event created)*"
        
    embed.description = desc
    if image_url: embed.set_image(url=image_url)
    
    await interaction.followup.send(embed=embed)
    
    # Update Dashboard (Resend to bottom)
    try: 
         if not is_dm: await update_dashboard(interaction.guild, data[context_id], resend=True)
    except: pass


async def update_dashboard(guild_or_user, data, resend: bool = False):
    """Updates the dashboard message."""
    if not data: return
    
    # Context Handling
    db_channel_id = data.get("dashboard_channel_id")
    db_msg_id = data.get("dashboard_message_id")
    
    if not db_channel_id or not db_msg_id: return
    
    try:
        if isinstance(guild_or_user, discord.Guild):
             channel = guild_or_user.get_channel(db_channel_id)
        else:
             # DM Context - we likely can't fetch channel by ID easily unless we keep the DM channel object
             # For now, skip auto-updating dashboard in DMs unless we have a reliable way to get the channel
             return 

        if not channel: return

        if resend:
            try:
                async for p in channel.pins():
                    if p.author == bot.user and p.id != db_msg_id:
                        try: await p.unpin(); await p.delete()
                        except: pass
            except: pass

        embed = discord.Embed(title="☁️ Chrono Dashboard", color=discord.Color.from_rgb(47, 49, 54))
        description = ""
        
        if not data.get("timers"):
            description = "*☁️ Chrono Silent - No Active Operations*"
        else:
            for timer in data["timers"]:
                ts = timer['end_epoch']
                icon = "📢" 
                notify = timer.get("notify_method", "")
                if "DM" in notify and "Server" not in notify: icon = "📩"
                if "Silent" in notify: icon = "🔕"
                repeat_icon = "🔄 " if timer.get("recurrence_seconds", 0) > 0 else ""
                
                owner = f"<@{timer['owner_id']}>"
                role_tag = ""
                if timer.get("role_id"):
                    role_tag = f" <@&{timer['role_id']}>"
                
                if timer.get("type") == "foundry_job":
                     icon = "🔥"
                     description += f"> **{timer['label']}**\n> 🤖 Check: <t:{ts}:f> (<t:{ts}:R>)\n\n"
                else:
                     details = ""
                     if timer.get("description"):
                         details = f"\n> 📝 *{timer['description']}*"
                         
                     description += f"> **{timer['label']}** (by {owner}){role_tag} {icon} {repeat_icon}\n> ⏱️ <t:{ts}:f> (<t:{ts}:R>){details}\n\n"
        
        embed.description = description
        embed.set_image(url=DUMMY_SPACER)
        embed.set_footer(text="Chrono Cloudy | Time is of the Essence ☁️")
        
        view = DashboardView()

        if resend:
            try:
                old_msg = await channel.fetch_message(db_msg_id)
                await old_msg.delete()
            except: pass
            try:
                new_msg = await channel.send(embed=embed, view=view)
                try: await new_msg.pin()
                except: pass
                # Clean up "Pinned a message" notification
                try:
                    async for sys_msg in channel.history(limit=5):
                        if sys_msg.type == discord.MessageType.pins_add and sys_msg.reference and sys_msg.reference.message_id == new_msg.id:
                            await sys_msg.delete()
                            break
                except: pass
                
                data["dashboard_message_id"] = new_msg.id
                # Correctly save the data
                all_data = load_data()
                if isinstance(guild_or_user, discord.Guild):
                    # CRITICAL FIX: Update the DB object with our CURRENT fresh data (including new timers)
                    # before saving. Otherwise, loading stale data overwrites our new timers.
                    all_data[str(guild_or_user.id)] = data 
                    all_data[str(guild_or_user.id)]["dashboard_message_id"] = new_msg.id
                    all_data[str(guild_or_user.id)]["dashboard_channel_id"] = channel.id
                    save_data(all_data)
            except: pass
        else:
            try:
                message = await channel.fetch_message(db_msg_id)
                await message.edit(embed=embed, view=view)
            except: pass
    except Exception as e:
        logger.error(f"Dashboard Update Error: {e}")

# --- Setup Logic ---
async def run_setup(guild, channel):
    data = load_data()
    guild_id = str(guild.id)
    if guild_id in data and "dashboard_message_id" in data[guild_id]:
        try:
            old_chan = guild.get_channel(data[guild_id].get("dashboard_channel_id"))
            if old_chan:
                old_msg = await old_chan.fetch_message(data[guild_id]["dashboard_message_id"])
                return f"EXISTING:{old_msg.jump_url}"
        except: pass

    embed = discord.Embed(title="☁️ Chrono Command Center", description="*Initializing Chrono System...*", color=discord.Color.from_rgb(47, 49, 54))
    embed.set_footer(text="Chrono Cloudy | Time is of the Essence ☁️")
    
    view = DashboardView()
    message = await channel.send(embed=embed, view=view)
    try: 
        await message.pin()
        # Clean up Notification
        async for sys_msg in channel.history(limit=5):
             if sys_msg.type == discord.MessageType.pins_add and sys_msg.reference and sys_msg.reference.message_id == message.id:
                 await sys_msg.delete()
                 break
    except: pass

    data[guild_id] = {
        "dashboard_channel_id": channel.id,
        "dashboard_message_id": message.id,
        "timers": []
    }
    save_data(data)
    await update_dashboard(guild, data[guild_id])
    return message.jump_url

# --- Commands ---

@bot.command()
@commands.has_permissions(administrator=True)
async def refresh(ctx):
    """Refreshes the dashboard by deleting the old one and sending a new one (Force Pin)."""
    data = load_data()
    guild_id = str(ctx.guild.id)
    
    await ctx.send("🔄 **Refreshing Dashboard...**")
    if guild_id not in data or "dashboard_message_id" not in data[guild_id]:
        # Auto-setup if missing
        await run_setup(ctx.guild, ctx.channel)
        await ctx.send("✅ Dashboard initialized.")
    else:
        await update_dashboard(ctx.guild, data[guild_id], resend=True)
        
    # Delete the trigger command and confirmation to keep chat clean
    try: await ctx.message.delete() 
    except: pass

@bot.tree.command(name="refresh", description="Force Refresh & Pin Dashboard")
@app_commands.checks.has_permissions(administrator=True)
async def refresh_slash(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    data = load_data()
    guild_id = str(interaction.guild_id)
    if guild_id in data and "dashboard_message_id" in data[guild_id]:
        await update_dashboard(interaction.guild, data[guild_id], resend=True)
        await interaction.followup.send("✅ **Dashboard Refreshed & Pinned!**", ephemeral=True)
    else:
        await run_setup(interaction.guild, interaction.channel)
        await interaction.followup.send("✅ **Dashboard Initialized & Pinned!**", ephemeral=True)

@bot.command()
async def sync(ctx):
    try:
        if ctx.guild:
            # Guild Sync (Instant for this server)
            await ctx.send(f"🔄 **Syncing to {ctx.guild.name}...**")
            bot.tree.clear_commands(guild=ctx.guild)
            bot.tree.copy_global_to(guild=ctx.guild)
            synced = await bot.tree.sync(guild=ctx.guild)
            await ctx.send(f"✅ **Guild Sync Complete:** {len(synced)} commands.")
        else:
            # Global Sync (For DMs / User App)
            await ctx.send("🔄 **Global Syncing...** (Updates DMs/All Servers - Takes up to 1h)")
            synced = await bot.tree.sync()
            await ctx.send(f"✅ **Global Sync Complete:** {len(synced)} commands.")
    except Exception as e:
        await ctx.send(f"❌ Sync failed: {e}")

async def parse_natural_language_groq(text: str) -> dict:
    if not groq_client:
        raise ValueError("Groq API Key is not configured.")
        
    prompt = f"""
    You are an AI assistant for a discord reminder bot specifically optimized for the mobile game "Whiteout Survival".
    Extract the intent and timing from the user's natural language request.
    
    User Request: "{text}"
    
    CRITICAL INSTRUCTIONS:
    1. Action: Determine if the user wants to "create" a timer, "edit" an existing one, "delete" (cancel/remove) one, "add_manager", "remove_manager", or "set_cycle".
    2. Languages: You must perfectly understand requests in ANY language (Spanish, French, Arabic, etc.), but ALWAYS translate the event name (Label) into standard English.
    3. Custom Events: If they specify a custom event name not listed below, use exactly what they typed (translated to English).
    4. Roles: If they mention pinging/tagging a specific role (like "@North America", "ping R4", "tag the alliance"), extract that role's name WITHOUT the '@'.
    5. PMs/DMs: If they ask to "PM all" or "DM me" AND tag a role, set notify_method to "both". If just DM, "dm". If just role/channel, "channel".
    6. Early Reminders: If they say "ping on time of event and 5 mins before", extract "5m" into the reminders_string.
    7. Managers: If they ask to add or remove someone from the timing managers list, extract the name/tag into `target_role`.
    8. Set Cycle: If they want to setup an automatic cycle for an event (e.g. "I have foundry on this friday voting starts on tuesday and ends on wednesday" or "set cycle for foundry every 14 days, voting is next monday for 24h"), use action="set_cycle". `time_string` should be when Voting Starts, `duration_string` should be how long voting lasts (e.g. "24h" or "48h" based on start/end days), and `interval_string` should be the cycle repeat frequency.
    
    CRITICAL GAME KNOWLEDGE FOR DEFAULTS (Apply these if the user doesn't specify otherwise):
    - "Bear Trap" or "Bear": Label="🐻 Bear Trap", default interval="47h 30m", default early reminders="30m, 5m"
    - "Crazy Joe" or "Joe": Label="🤡 Crazy Joe", default interval="0", default early reminders="40m, 5m"
    - "Arena": Label="🛡️ Arena Reset", default interval="24h", default early reminders="5m"
    - "Castle" or "Sunfire": Label="🏰 Castle Battle", default interval="28d", default early reminders="5h, 1h"
    - "SvS": Label="⚔️ SvS Battle", default interval="28d", default early reminders="5h, 1h"
    
    Respond ONLY with a valid JSON object matching this structure (no markdown tags):
    {{
      "action": "create", // or "edit", "delete", "add_manager", "remove_manager", "set_cycle"
      "label": "The name of the event (use standard game emojis if matching defaults, otherwise use their exact string in English).",
      "time_string": "The extracted time string (e.g., '10m', '1h', '14:00'). For 'delete' actions, leave empty.",
      "duration_string": "Only used for set_cycle (e.g. '24h', '48h'). Empty otherwise.",
      "interval_string": "The extracted repeat interval (e.g., '24h'). Use '0' if it doesn't repeat.",
      "reminders_string": "Any early reminders mentioned (e.g., '10m, 5m').",
      "target_role": "The name of the role they want to ping, or the name of the user to add to managers. Leave empty if not specified.",
      "notify_method": "channel" // or "dm" or "both"
    }}
    
    Example 1: "Remind me everyother day about beartrap at 14:00 UTC and tag role @North America"
    Output: {{"action": "create", "label": "🐻 Bear Trap", "time_string": "14:00 UTC", "duration_string": "", "interval_string": "48h", "reminders_string": "30m, 5m", "target_role": "North America", "notify_method": "channel"}}
    
    Example 2: "PM all and mention role R4 for Castle in 2h"
    Output: {{"action": "create", "label": "🏰 Castle Battle", "time_string": "2h", "duration_string": "", "interval_string": "28d", "reminders_string": "5h, 1h", "target_role": "R4", "notify_method": "both"}}
    
    Example 3: "Elimina mi recordatorio de trampa de osos"
    Output: {{"action": "delete", "label": "🐻 Bear Trap", "time_string": "", "duration_string": "", "interval_string": "0", "reminders_string": "", "target_role": "", "notify_method": "channel"}}

    Example 4: "Add @John to the timing managers"
    Output: {{"action": "add_manager", "label": "", "time_string": "", "duration_string": "", "interval_string": "0", "reminders_string": "", "target_role": "John", "notify_method": "channel"}}

    Example 5: "I have foundry on this friday voting starts on tuesday and ends on wednesday. repeats every 2 weeks."
    Output: {{"action": "set_cycle", "label": "Foundry", "time_string": "Tuesday", "duration_string": "24h", "interval_string": "14d", "reminders_string": "", "target_role": "", "notify_method": "channel"}}
    """
    
    try:
        completion = await groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="openai/gpt-oss-20b",
            temperature=0.0,
            response_format={"type": "json_object"}
        )
        content = completion.choices[0].message.content
        return json.loads(content)
    except Exception as e:
        logger.error(f"Groq parsing error: {e}")
        raise ValueError("Failed to understand the request.")

@bot.tree.command(name="chrono", description="Universal AI Engine: Manage events, timers, and cycles (e.g. 'Set Foundry to 14:00')")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(request="Your request (e.g. 'Move Bear Trap to 14:00', 'Cancel Castle', 'Ping @R4 for Joe')")
async def remind_slash(interaction: discord.Interaction, request: str):
    await interaction.response.defer(ephemeral=True)
    try:
        parsed = await parse_natural_language_groq(request)
        action = parsed.get("action", "create").lower()
        label = parsed.get("label", "Reminder")
        
        # 1. MANAGER ACTIONS
        if action in ["add_manager", "remove_manager"]:
            if not interaction.guild:
                await interaction.followup.send("❌ Timing managers are only available in Servers.", ephemeral=True)
                return
            if not interaction.user.guild_permissions.administrator and interaction.user.id != interaction.guild.owner_id:
                await interaction.followup.send("❌ Only Server Administrators can manage the Timing Managers list.", ephemeral=True)
                return
            
            target_name = parsed.get("target_role", "").lower().strip()
            if not target_name:
                await interaction.followup.send("❌ I couldn't understand who you wanted to add/remove.", ephemeral=True)
                return
                
            # Find the user
            target_member = discord.utils.find(lambda m: target_name in m.name.lower() or target_name in m.display_name.lower(), interaction.guild.members)
            if not target_member:
                await interaction.followup.send(f"❌ Could not find a member matching `{target_name}`.", ephemeral=True)
                return
                
            data = load_data()
            context_id = str(interaction.guild_id)
            if context_id not in data: data[context_id] = {}
            if "timing_managers" not in data[context_id]: data[context_id]["timing_managers"] = []
            
            mgrs = data[context_id]["timing_managers"]
            
            if action == "add_manager":
                if target_member.id not in mgrs:
                    mgrs.append(target_member.id)
                    save_data(data)
                    await interaction.followup.send(f"✅ **{target_member.display_name}** has been added to the Timing Managers list.", ephemeral=True)
                else:
                    await interaction.followup.send(f"⚠️ **{target_member.display_name}** is already a Timing Manager.", ephemeral=True)
            else:
                if target_member.id in mgrs:
                    mgrs.remove(target_member.id)
                    save_data(data)
                    await interaction.followup.send(f"✅ **{target_member.display_name}** has been removed from the Timing Managers list.", ephemeral=True)
                else:
                    await interaction.followup.send(f"⚠️ **{target_member.display_name}** is not in the Timing Managers list.", ephemeral=True)
            return

        # 1.5 SET CYCLE ACTION
        if action == "set_cycle":
            if not interaction.guild:
                await interaction.followup.send("❌ Event cycles are only available in Servers.", ephemeral=True)
                return
            if not interaction.user.guild_permissions.administrator and interaction.user.id != interaction.guild.owner_id:
                await interaction.followup.send("❌ Only Server Administrators can manage Event Cycles.", ephemeral=True)
                return
                
            user_tz = get_user_tz_str(interaction.user.id)
            time_str = parsed.get("time_string", "")
            duration_str = parsed.get("duration_string", "24h")
            interval_str = parsed.get("interval_string", "14d")
            
            if not time_str:
                await interaction.followup.send("❌ I couldn't determine the voting start time.", ephemeral=True)
                return
                
            start_epoch = parse_time_input(time_str, "smart", user_tz)
            # Default to 24h duration and 14d interval if parse fails or empty
            try: duration_sec = parse_duration_string(duration_str) if duration_str else 86400
            except: duration_sec = 86400
            try: interval_sec = parse_duration_string(interval_str) if interval_str else 1209600
            except: interval_sec = 1209600
            
            data = load_data()
            context_id = str(interaction.guild_id)
            if context_id not in data: data[context_id] = {}
            if "cycles" not in data[context_id]: data[context_id]["cycles"] = []
            
            cycles = data[context_id]["cycles"]
            cycle = next((c for c in cycles if c['name'].lower() == label.lower()), None)
            if cycle:
                cycle['start_epoch'] = start_epoch
                cycle['duration_sec'] = duration_sec
                cycle['interval_sec'] = interval_sec
                cycle['pre_dm_sent'] = False
                cycle['post_dm_sent'] = False
                await interaction.followup.send(f"✅ Updated event cycle **{label}**.", ephemeral=True)
            else:
                cycles.append({
                    "name": label,
                    "start_epoch": start_epoch,
                    "duration_sec": duration_sec,
                    "interval_sec": interval_sec,
                    "pre_dm_sent": False,
                    "post_dm_sent": False
                })
                await interaction.followup.send(f"✅ Created event cycle **{label}**. The bot will DM managers 24h before voting begins, and right after voting ends.", ephemeral=True)
                
            save_data(data)
            return

        # 2. DELETE ACTION
        if action == "delete":
            data = load_data()
            context_id = str(interaction.guild_id) if interaction.guild else str(interaction.user.id)
            if context_id in data and "timers" in data[context_id]:
                for idx, t in enumerate(data[context_id]["timers"]):
                    if t['label'].lower() == label.lower():
                        if not check_permissions(interaction, t['owner_id']):
                            await interaction.followup.send("❌ **Access Denied.** You can only delete your own timers.", ephemeral=True); return
                        removed = data[context_id]["timers"].pop(idx)
                        if removed.get("discord_event_id") and interaction.guild:
                            await delete_discord_event(interaction.guild, removed["discord_event_id"])
                        save_data(data)
                        if interaction.guild: await update_dashboard(interaction.guild, data[context_id], resend=True)
                        await interaction.followup.send(f"✅ Deleted timer **{label}**.", ephemeral=True)
                        return
            await interaction.followup.send(f"❌ Timer **{label}** not found to delete.", ephemeral=True)
            return

        # Time Parsing for Create/Edit
        user_tz = get_user_tz_str(interaction.user.id)
        time_str = parsed.get("time_string", "")
        if not time_str: raise ValueError("Could not determine a time.")
        end_epoch = parse_time_input(time_str, "smart", user_tz)
        
        recurrence_seconds = 0
        interval_str = parsed.get("interval_string", "0")
        if interval_str and str(interval_str) != "0":
            try: recurrence_seconds = parse_duration_string(str(interval_str))
            except: pass
            
        reminders_list = []
        reminders_str = parsed.get("reminders_string")
        if reminders_str:
            reminders_list = parse_reminders_string(str(reminders_str))
            
        # 2. EDIT ACTION
        if action == "edit":
            data = load_data()
            context_id = str(interaction.guild_id) if interaction.guild else str(interaction.user.id)
            if context_id in data and "timers" in data[context_id]:
                for t in data[context_id]["timers"]:
                    if t['label'].lower() == label.lower():
                        if not check_permissions(interaction, t['owner_id']):
                            await interaction.followup.send("❌ **Access Denied.** You can only edit your own timers.", ephemeral=True); return
                        
                        t["end_epoch"] = end_epoch
                        t["start_epoch"] = int(time.time())
                        t["sent_reminders"] = []
                        if recurrence_seconds: t["recurrence_seconds"] = recurrence_seconds
                        if reminders_list: t["reminders"] = reminders_list
                        
                        if t.get("discord_event_id") and interaction.guild:
                             await update_discord_event(interaction.guild, t["discord_event_id"], t["label"], t["end_epoch"], t.get("event_duration", 900))
                        
                        data[context_id]["timers"].sort(key=lambda x: x["end_epoch"])
                        save_data(data)
                        if interaction.guild: await update_dashboard(interaction.guild, data[context_id], resend=True)
                        await interaction.followup.send(f"✅ Updated timer **{label}** to new time.", ephemeral=True)
                        return
            await interaction.followup.send(f"❌ Timer **{label}** not found to edit.", ephemeral=True)
            return

        # 3. CREATE ACTION
        # Role & Permissions Logic
        target_role_str = parsed.get("target_role", "")
        role_id = None
        notify_str = parsed.get("notify_method", "channel")
        
        if notify_str == "both": notify_method = "📣 Both (Ping & DM)"
        elif notify_str == "dm": notify_method = "📩 DM Me"
        else: notify_method = "📢 Message in Server (Ping Role)"
        
        if not interaction.guild:
            notify_method = "📩 DM Me"
        elif target_role_str:
            # Fuzzy match role
            for r in interaction.guild.roles:
                if target_role_str.lower() in r.name.lower():
                    # Check hierarchy/permissions
                    if interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_roles or interaction.user.top_role.position > r.position:
                        role_id = r.id
                    else:
                        await interaction.followup.send(f"⚠️ You lack permissions to ping the **{r.name}** role. Reverting to channel alert without ping.", ephemeral=True)
                    break
            
        await add_timer(interaction, label, end_epoch, role_id, notify_method, "smart", recurrence_seconds, None, 900, reminders_list)
        
    except ValueError as e:
        await interaction.followup.send(f"❌ {str(e)}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


@bot.tree.command(name="dashboard", description="Create or Move the Chrono Dashboard")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def dashboard(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ The live dashboard is not supported in DMs. Please use `/mytimers` instead to view your personal timers.", ephemeral=True)
        return

    # Defer the response immediately to prevent timeout
    await interaction.response.defer(ephemeral=False)
    
    # Context ID
    context_id = str(interaction.guild_id)
    is_dm = False
    data = load_data()
    if context_id not in data: data[context_id] = {}
    
    embed = discord.Embed(title="☁️ Chrono Dashboard", color=discord.Color.from_rgb(47, 49, 54))
    embed.description = "*☁️ Chrono Silent - No Active Operations*"
    
    # Send new dashboard
    view = DashboardView()
    # Use followup since we already deferred
    msg = await interaction.followup.send(embed=embed, view=view, wait=True)
    msg = await interaction.original_response()
    # Pin if possible (might fail in User App contexts, that's okay)
    try: 
        await msg.pin()
        # Clean up Notification
        async for sys_msg in interaction.channel.history(limit=5):
             if sys_msg.type == discord.MessageType.pins_add and sys_msg.reference and sys_msg.reference.message_id == msg.id:
                 await sys_msg.delete()
                 break
    except: pass
    
    # Save Location
    data[context_id]["dashboard_channel_id"] = interaction.channel_id
    data[context_id]["dashboard_message_id"] = msg.id
    save_data(data)
    
    # Force Update (if guild)
    if not is_dm: await update_dashboard(interaction.guild, data[context_id], resend=True)

@bot.tree.command(name="mytimers", description="View your active personal timers in DMs")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def mytimers(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    context_id = str(interaction.guild_id) if interaction.guild else str(interaction.user.id)
    data = load_data()
    
    if context_id not in data or "timers" not in data[context_id] or not data[context_id]["timers"]:
        await interaction.followup.send("You have no active timers.", ephemeral=True)
        return
        
    timers = data[context_id]["timers"]
    user_timers = [t for t in timers if t['owner_id'] == interaction.user.id or not interaction.guild]
    
    if not user_timers:
        await interaction.followup.send("You have no active timers.", ephemeral=True)
        return
        
    embed = discord.Embed(title="⏱️ Your Active Timers", color=discord.Color.blue())
    desc = ""
    for idx, t in enumerate(user_timers, 1):
        time_left = max(0, t['end_epoch'] - int(time.time()))
        mins, secs = divmod(time_left, 60)
        hrs, mins = divmod(mins, 60)
        time_str = f"{hrs}h {mins}m" if hrs > 0 else f"{mins}m {secs}s"
        desc += f"**{idx}. {t['label']}** - Ends in: {time_str} (<t:{t['end_epoch']}:R>)\n"
        
    embed.description = desc
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="set_cycle", description="Set up a recurring Global Event (e.g. Foundry) to automatically DM managers to schedule it.")
@app_commands.allowed_installs(guilds=True)
@app_commands.allowed_contexts(guilds=True)
@app_commands.describe(
    event_name="Name of the event (e.g. 'Foundry')",
    voting_start="When does voting open? (e.g. 'Tomorrow 10:00')",
    voting_duration="How long is voting? (e.g. '24h', '48h')",
    interval="Cycle recurrence (e.g. '14d', '28d')"
)
async def set_cycle(interaction: discord.Interaction, event_name: str, voting_start: str, voting_duration: str, interval: str):
    await interaction.response.defer(ephemeral=True)
    if not interaction.user.guild_permissions.administrator and interaction.user.id != interaction.guild.owner_id:
        await interaction.followup.send("❌ Only Server Administrators can manage Event Cycles.", ephemeral=True)
        return
        
    try:
        user_tz = get_user_tz_str(interaction.user.id)
        start_epoch = parse_time_input(voting_start, "smart", user_tz)
        duration_sec = parse_duration_string(voting_duration)
        interval_sec = parse_duration_string(interval)
    except Exception as e:
        await interaction.followup.send(f"❌ Error parsing inputs: {e}", ephemeral=True)
        return
        
    data = load_data()
    context_id = str(interaction.guild_id)
    if context_id not in data: data[context_id] = {}
    if "cycles" not in data[context_id]: data[context_id]["cycles"] = []
    
    # Update or add cycle
    cycles = data[context_id]["cycles"]
    cycle = next((c for c in cycles if c['name'].lower() == event_name.lower()), None)
    if cycle:
        cycle['start_epoch'] = start_epoch
        cycle['duration_sec'] = duration_sec
        cycle['interval_sec'] = interval_sec
        cycle['pre_dm_sent'] = False
        cycle['post_dm_sent'] = False
        await interaction.followup.send(f"✅ Updated event cycle **{event_name}**.", ephemeral=True)
    else:
        cycles.append({
            "name": event_name,
            "start_epoch": start_epoch,
            "duration_sec": duration_sec,
            "interval_sec": interval_sec,
            "pre_dm_sent": False,
            "post_dm_sent": False
        })
        await interaction.followup.send(f"✅ Created event cycle **{event_name}**. The bot will DM managers 24h before voting begins, and right after voting ends.", ephemeral=True)
        
    save_data(data)

@bot.command(name="start")
@commands.has_permissions(administrator=True)
async def start_prefix(ctx, channel: discord.TextChannel = None):
    target = channel or ctx.channel
    result = await run_setup(ctx.guild, target)
    if result.startswith("EXISTING:"):
        await ctx.send(f"⚠️ **Dashboard already active:** {result.split(':',1)[1]}\n(Admin: Delete the old dashboard message to reset.)")
    else:
        msg = await ctx.send(f"✅ Dashboard initialized: {result}")
        await asyncio.sleep(10)
        try: await msg.delete()
        except: pass

@bot.tree.command(name="start", description="Initialize Chrono Dashboard")
@app_commands.checks.has_permissions(administrator=True)
async def start_slash(interaction: discord.Interaction, channel: discord.TextChannel = None):
    await interaction.response.defer(ephemeral=True)
    target = channel or interaction.channel
    result = await run_setup(interaction.guild, target)
    if result.startswith("EXISTING:"):
         await interaction.followup.send(f"⚠️ **Dashboard already active:** {result.split(':',1)[1]}\n(Delete old one manually if broken.)")
    else:
        msg = await interaction.followup.send(f"✅ Dashboard initialized in {target.mention}: {result}")
        await asyncio.sleep(10)
        try: await msg.delete()
        except: pass

@bot.command(name="shutdown")
@commands.has_permissions(administrator=True)
async def shutdown_cmd(ctx):
    await ctx.send("🛑 **Shutdown Initiated.**")
    await bot.close()

@bot.command(name="syncglobal")
@commands.has_permissions(administrator=True)
async def sync_global(ctx):
    """Admin command to force a global slash command sync (for DMs)"""
    msg = await ctx.send("🔄 Syncing global slash commands... (This might take a moment)")
    try:
        synced = await bot.tree.sync()
        await msg.edit(content=f"✅ Successfully synced {len(synced)} global commands! They should now appear in DMs.")
    except Exception as e:
        await msg.edit(content=f"❌ Failed to sync: {e}")

async def check_missed_events():
    logger.info("Checking for missed events...")
    data = load_data()
    try: now = int(time.time())
    except: now = int(time.time()) 
    
    data_changed = False
    
    for context_id_str, context_data in data.items():
        if "timers" not in context_data: continue
        timers_to_keep = []
        
        # Resolve Context
        guild = None
        try: guild = bot.get_guild(int(context_id_str))
        except: pass
        
        # Re-check timers for missed reminders (even if not expired)
        for timer in context_data["timers"]:
            if timer["end_epoch"] > now:
                # Timer still active, check if we missed any reminders
                reminders = timer.get("reminders", [])
                sent = timer.get("sent_reminders", [])
                for r_sec in reminders:
                    if r_sec in sent: continue
                    remain = timer["end_epoch"] - now
                    # If we are PAST the reminder time (remain < r_sec) but within reasonable window (e.g. didn't happen 10 years ago)
                    # And only if remain > 0 (event technically active)
                    if remain <= r_sec:
                        msg = f"⚠️ **Late Reminder (Bot Restarted):** `{timer['label']}` was due {get_interval_str(r_sec)} ago! (Event in {get_interval_str(remain)})"
                        try:
                            if guild:
                                chan = guild.get_channel(context_data.get("dashboard_channel_id"))
                                if chan: await chan.send(msg)
                            # DMs handled if user object available (complex here, skip for robustness/speed)
                        except: pass
                        sent.append(r_sec)
                        timer["sent_reminders"] = sent
                        data_changed = True

            elif timer["end_epoch"] <= now:
                logger.info(f"Restoring expired timer: {timer['label']}")
                try:
                    # Notify logic? Missed event logic usually only server based.
                    # For now keep it server only for simplicity/safey.
                    if guild:
                        chan = guild.get_channel(context_data.get("dashboard_channel_id"))
                        if chan:
                            embed = discord.Embed(title="⚠️ Missed Alert (Offline)", description=f"**{timer['label']}** ended at <t:{timer['end_epoch']}:t>.", color=discord.Color.orange())
                            # if bot.user.avatar: embed.set_thumbnail(url=bot.user.avatar.url)
                            await chan.send(content=f"<@{timer['owner_id']}>", embed=embed)
                except: pass
                
                recur = timer.get("recurrence_seconds", 0)
                if recur > 0:
                    next_time = timer["end_epoch"]
                    while next_time < now: next_time += recur
                    timer["end_epoch"] = next_time
                    timer["start_epoch"] = now 
                    timer["sent_reminders"] = []
                    
                    # New Cycle = New Event (If Guild)
                    if guild:
                        dur = timer.get("event_duration", 900)
                        # Clean old event first if exists
                        if timer.get("discord_event_id"):
                             try:
                                 old_evt = await guild.fetch_scheduled_event(timer["discord_event_id"])
                                 await old_evt.delete()
                             except: pass

                        evt_id = await create_discord_event(guild, timer["label"], next_time, dur)
                        timer["discord_event_id"] = evt_id
                    
                    timers_to_keep.append(timer)
                    data_changed = True
                else:
                    data_changed = True
            else:
                timers_to_keep.append(timer)
        
        # Check Foundry Missed
        # If it's Thursday/Friday/Saturday/Sunday and we haven't asked yet?
        # That's complex state. Simplified: The main loop handles it if we just reset the 'asked' flag?
        # Actually proper way:
        # Foundry jobs are just timers that auto-renew 7 days.
        # If one expired while offline, the logic above (recur > 0) will auto-renew it.
        # But we missed the "Ask" DM.
        # Fix: The main loop checks "if end_epoch <= current_time".
        # If check_missed_events auto-renews it, the main loop sees it as "future" and won't ask.
        # Special handling for Foundry:
        # If a foundry job expired, we *should* DM the user now if it's still relevant.
        # But for v41 robustness, let's trust the auto-renew to at least keep the schedule alive.
        # The user can manually trigger if needed.

        if data_changed:
            timers_to_keep.sort(key=lambda x: x["end_epoch"])
            context_data["timers"] = timers_to_keep
            save_data(data)
            if guild: await update_dashboard(guild, context_data)

dice_pity_counters = {}

class DiceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
    @discord.ui.button(label="Roll Again", style=discord.ButtonStyle.primary, custom_id="dice_roll_again_btn", emoji="🎲")
    async def roll_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        await execute_dice(interaction)

async def execute_dice(interaction: discord.Interaction):
    import random
    import os
    import asyncio
    
    user_id = interaction.user.id
    pity = dice_pity_counters.get(user_id, 0)
    
    # Dynamic weighting for Pity System (Target: Land a 6)
    # Extremely subtle pity: adds a tiny fraction to the weight of rolling a 6.
    # Base weight is 10.0 per face. A bonus of 0.0001 per miss is practically invisible.
    pity_bonus = pity * 0.0001
    weights = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0 + pity_bonus]
    roll = random.choices([1, 2, 3, 4, 5, 6], weights=weights, k=1)[0]
    
    # Reset pity on a 6, otherwise increment
    if roll == 6:
        dice_pity_counters[user_id] = 0
    else:
        dice_pity_counters[user_id] = pity + 1
    
    file_path = f"assets/dice_{roll}.png"
    rolling_path = "assets/rolling.gif"
    
    if os.path.exists(file_path) and os.path.exists(rolling_path):
        # 1. Send the generic looping rolling GIF first
        embed_rolling = discord.Embed(title="🎲 Dice Roll", description="Rolling...", color=discord.Color.dark_gray())
        file_roll = discord.File(rolling_path, filename="rolling.gif")
        embed_rolling.set_thumbnail(url="attachment://rolling.gif")
        
        # Determine if responding to a slash command or a button
        if interaction.type == discord.InteractionType.application_command:
            await interaction.response.defer(thinking=True)
            msg_interaction = await interaction.followup.send(embed=embed_rolling, file=file_roll, wait=True)
        else:
            # We are responding to a button click
            await interaction.response.edit_message(view=None)
            content_str = f"{interaction.user.mention} rolled!"
            try:
                msg_interaction = await interaction.channel.send(
                    content=content_str, 
                    embed=embed_rolling, 
                    file=file_roll
                )
            except discord.Forbidden:
                file_roll = discord.File(rolling_path, filename="rolling.gif")
                msg_interaction = await interaction.followup.send(
                    content=content_str, 
                    embed=embed_rolling, 
                    file=file_roll,
                    wait=True
                )
        
        # 2. Wait for the roll animation
        await asyncio.sleep(1.8)
        
        # 3. Edit the message: Swap the GIF out for a STATIC PNG result.
        embed_result = discord.Embed(title="🎲 Dice Roll", description=f"You rolled a **{roll}**!", color=discord.Color.blue())
        file_result = discord.File(file_path, filename="dice.png")
        embed_result.set_thumbnail(url="attachment://dice.png")
        
        view = DiceView()
        
        try:
            if interaction.type == discord.InteractionType.application_command:
                await interaction.edit_original_response(
                    embed=embed_result, 
                    attachments=[file_result],
                    view=view
                )
            else:
                await msg_interaction.edit(
                    embed=embed_result, 
                    attachments=[file_result],
                    view=view
                )
        except discord.NotFound:
            pass
            
    else:
        # Fallback if images missing
        if not interaction.response.is_done():
            embed = discord.Embed(title="🎲 Dice Roll", description=f"You rolled a **{roll}**!", color=discord.Color.blue())
            await interaction.response.send_message(embed=embed)

# @bot.tree.command(name="dice", description="Roll a 6-sided dice")
# @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
# @app_commands.allowed_installs(guilds=True, users=True)
async def dice_slash(interaction: discord.Interaction):
    await execute_dice(interaction)

active_targeted_rps: dict[str, Any] = {}

class RPSChallengeView(discord.ui.View):
    def __init__(self, match_id: str):
        super().__init__(timeout=None)
        self.match_id = match_id
        
    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="rps_accept", emoji="✅")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        match = active_targeted_rps.get(self.match_id)
        if not match:
            await interaction.response.send_message("This challenge has expired!", ephemeral=True)
            return
        if interaction.user.id != match['target_id']:
            await interaction.response.send_message("You are not the target of this challenge!", ephemeral=True)
            return
            
        match['status'] = 'playing'
        if match['mode'] == 'Random':
            await interaction.response.defer()
            await resolve_rps_match(interaction.message, self.match_id, None, None)
        else:
            view = RPSPlayView(self.match_id)
            embed = discord.Embed(title="RPS Challenge Accepted!", description=f"<@{match['challenger_id']}> vs <@{match['target_id']}>\n\nBoth players, lock in your choices below!", color=discord.Color.green())
            await interaction.response.edit_message(content=None, embed=embed, view=view)

class RPSPlayView(discord.ui.View):
    def __init__(self, match_id: str):
        super().__init__(timeout=None)
        self.match_id = match_id
        
    async def handle_lock(self, interaction: discord.Interaction, choice: str):
        match = active_targeted_rps.get(self.match_id)
        if not match:
            await interaction.response.send_message("This match has expired!", ephemeral=True)
            return
            
        uid = interaction.user.id
        if uid not in (match['challenger_id'], match['target_id']):
            await interaction.response.send_message("You are not in this match!", ephemeral=True)
            return
            
        if uid in match['choices']:
            await interaction.response.send_message("You already locked in!", ephemeral=True)
            return
            
        match['choices'][uid] = choice
        await interaction.response.send_message(f"You securely locked in **{choice.title()}**! 🤫", ephemeral=True)
        
        if len(match['choices']) == 2:
            try: await interaction.message.edit(view=None)
            except: pass
            
            # If playing Bot, auto-generate bot choice
            p2_choice = match['choices'].get(match['target_id'])
            if match['target_id'] == interaction.client.user.id:
                import random
                p2_choice = random.choice(["rock", "paper", "scissors"])
                
            await resolve_rps_match(interaction.message, self.match_id, match['choices'][match['challenger_id']], p2_choice)

    @discord.ui.button(label="Rock", style=discord.ButtonStyle.secondary, custom_id="rps_btn_rock", emoji="🪨")
    async def lock_rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_lock(interaction, "rock")
        
    @discord.ui.button(label="Paper", style=discord.ButtonStyle.secondary, custom_id="rps_btn_paper", emoji="📄")
    async def lock_paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_lock(interaction, "paper")
        
    @discord.ui.button(label="Scissors", style=discord.ButtonStyle.secondary, custom_id="rps_btn_scissors", emoji="✂️")
    async def lock_scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_lock(interaction, "scissors")

async def resolve_rps_match(msg: discord.Message, match_id: str, p1_choice: str = None, p2_choice: str = None):
    import random
    import os
    import asyncio
    import time
    
    match = active_targeted_rps.get(match_id)
    if not match: return
    
    del active_targeted_rps[match_id]
    
    options = ["rock", "paper", "scissors"]
    if not p1_choice: p1_choice = random.choice(options)
    if not p2_choice: p2_choice = random.choice(options)
    
    rolling_path = "assets/rps_roll.gif"
    if not os.path.exists(rolling_path):
        return
        
    embed_rolling = discord.Embed(title="✊ ✋ ✌️ Rock Paper Scissors", description="Evaluating...", color=discord.Color.dark_gray())
    embed_rolling.set_thumbnail(url="attachment://rps_roll.gif")
    file_roll = discord.File(rolling_path, filename="rps_roll.gif")
    
    try:
        await msg.edit(content=None, embed=embed_rolling, attachments=[file_roll], view=None)
    except:
        pass
        
    await asyncio.sleep(1.8)
    
    win_map = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
    
    p1_id = str(match['challenger_id'])
    p1_name = match['challenger_name']
    p2_id = str(match['target_id'])
    p2_name = match['target_name']
    
    if p1_choice == p2_choice:
        winner_id = "tie"
    elif win_map[p1_choice] == p2_choice:
        winner_id = p1_id
    else:
        winner_id = p2_id
        
    result_text = f"<@{p1_id}> threw **{p1_choice.title()}**\n<@{p2_id}> threw **{p2_choice.title()}**\n"
    color = discord.Color.gold()
    
    is_bot_match = (p2_id == str(msg.author.id))
    
    if winner_id != "tie":
        winner_name = p1_name if winner_id == p1_id else p2_name
        winner_choice_str = p1_choice if winner_id == p1_id else p2_choice
        loser_choice_str = p2_choice if winner_id == p1_id else p1_choice
        
        color = discord.Color.green()
        result_text += f"\n🏆 **{winner_name}** wins! ({winner_choice_str.title()} beats {loser_choice_str.title()})"
        
        if not is_bot_match:
            from db import load_data, save_data
            all_data = load_data()
            is_dm = msg.guild is None
            now = time.time()
            channel_id = str(msg.channel.id)
            
            if is_dm:
                target_row_id = "DM_Scores"
                target_data = all_data.get(target_row_id, {"channels": {}})
                channels = target_data.get("channels", {})
            else:
                target_row_id = str(msg.guild.id)
                target_data = all_data.get(target_row_id, {})
                channels = target_data.get("rps_sessions", {})
                
            expired_keys = [cid for cid, cdata in channels.items() if ('last_active' not in cdata or (now - cdata['last_active'] > 3600 * 3))]
            for cid in expired_keys: del channels[cid]
            if channel_id not in channels and len(channels) >= 100:
                del channels[next(iter(channels))]
                
            if channel_id not in channels:
                channels[channel_id] = {'scores': {}, 'last_active': now}
                
            session = channels[channel_id]
            scores = session.get('scores', {})
            scores[winner_id] = scores.get(winner_id, 0) + 1
            session['scores'] = scores
            session['last_active'] = now
            channels[channel_id] = session
            
            if is_dm: target_data["channels"] = channels
            else: target_data["rps_sessions"] = channels
            all_data[target_row_id] = target_data
            save_data(all_data)
            
            result_text += f"\n\n**Scoreboard:**\n{p1_name}: {scores.get(p1_id, 0)}\n{p2_name}: {scores.get(p2_id, 0)}"
            
    else:
        result_text += f"\n🤝 **It's a tie!** Both threw {p1_choice.title()}."
        
    img_choice = p1_choice if winner_id == p1_id else p2_choice
    if winner_id == "tie": img_choice = p1_choice
    file_path = f"assets/rps_{img_choice}.png"
    
    embed_result = discord.Embed(title="✊ ✋ ✌️ Rock Paper Scissors", description=result_text, color=color)
    if os.path.exists(file_path):
        file_result = discord.File(file_path, filename="rps.png")
        embed_result.set_thumbnail(url="attachment://rps.png")
        try:
            await msg.edit(content=None, embed=embed_result, attachments=[file_result], view=None)
        except:
            pass
    else:
        try:
            await msg.edit(content=None, embed=embed_result, view=None)
        except:
            pass

# @bot.tree.command(name="rps", description="Challenge a user or the bot to Rock, Paper, Scissors!")
# @app_commands.describe(target="The user to challenge (leave empty for Bot)", mode="How to play (Selection or Random)")
# @app_commands.choices(mode=[
#     app_commands.Choice(name="Selection (Pick moves)", value="Selection"),
#     app_commands.Choice(name="Random (Auto RNG)", value="Random")
# ])
# @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
# @app_commands.allowed_installs(guilds=True, users=True)
async def rps_slash(interaction: discord.Interaction, target: discord.Member = None, mode: app_commands.Choice[str] = None):
    match_id = f"{interaction.id}"
    chosen_mode = mode.value if mode else "Selection"
    
    if target is None or target.id == interaction.user.id:
        active_targeted_rps[match_id] = {
            'challenger_id': interaction.user.id,
            'challenger_name': interaction.user.display_name,
            'target_id': interaction.client.user.id,
            'target_name': interaction.client.user.display_name,
            'mode': chosen_mode,
            'status': 'playing',
            'choices': {}
        }
        
        if chosen_mode == "Random":
            await interaction.response.defer()
            msg = await interaction.followup.send("Rolling...", wait=True)
            await resolve_rps_match(msg, match_id, None, None)
        else:
            view = RPSPlayView(match_id)
            embed = discord.Embed(title="RPS vs Bot!", description="Lock in your choice below!", color=discord.Color.blue())
            await interaction.response.send_message(embed=embed, view=view)
            
    else:
        active_targeted_rps[match_id] = {
            'challenger_id': interaction.user.id,
            'challenger_name': interaction.user.display_name,
            'target_id': target.id,
            'target_name': target.display_name,
            'mode': chosen_mode,
            'status': 'waiting',
            'choices': {}
        }
        
        view = RPSChallengeView(match_id)
        embed = discord.Embed(title="⚔️ RPS Challenge!", description=f"<@{target.id}>, you have been challenged to RPS by <@{interaction.user.id}>!\n\nMode: **{chosen_mode}**", color=discord.Color.gold())
        await interaction.response.send_message(content=f"<@{target.id}>", embed=embed, view=view)




active_brawls: dict[str, Any] = {}

class BrawlJoinView(discord.ui.View):
    def __init__(self, message_id: str):
        super().__init__(timeout=None)
        self.message_id = message_id
        
    @discord.ui.button(label="Join Brawl!", style=discord.ButtonStyle.success, custom_id="brawl_btn_join", emoji="⚔️")
    async def join_brawl(self, interaction: discord.Interaction, button: discord.ui.Button):
        match = active_brawls.get(self.message_id)
        if not match or match['status'] != 'joining':
            await interaction.response.send_message("This Brawl is no longer accepting players!", ephemeral=True)
            return
            
        uid = str(interaction.user.id)
        if uid in match['players']:
            await interaction.response.send_message("You are already in the lobby!", ephemeral=True)
            return
            
        match['players'][uid] = {
            'name': interaction.user.display_name,
            'choice': None
        }
        await interaction.response.send_message("You joined the Brawl!", ephemeral=True)
        
        player_names = [data['name'] for data in match['players'].values()]
        embed = interaction.message.embeds[0]
        desc = embed.description.split("**Joined Players:**")[0]
        embed.description = desc + f"**Joined Players:** {', '.join(player_names)}"
        try: await interaction.message.edit(embed=embed)
        except: pass

    @discord.ui.button(label="Start Game", style=discord.ButtonStyle.primary, custom_id="brawl_btn_start")
    async def start_game(self, interaction: discord.Interaction, button: discord.ui.Button):
        match = active_brawls.get(self.message_id)
        if not match: return
        if str(interaction.user.id) != match['host_id']:
            await interaction.response.send_message("Only the Host can start the game!", ephemeral=True)
            return
            
        if len(match['players']) < 2:
            await interaction.response.send_message("At least 2 players must join to start!", ephemeral=True)
            return
            
        match['status'] = 'playing'
        await interaction.response.defer()
        await spawn_brawl_round(interaction.message, self.message_id, 1)

    @discord.ui.button(label="Cancel Game", style=discord.ButtonStyle.danger, custom_id="brawl_btn_cancel")
    async def cancel_game(self, interaction: discord.Interaction, button: discord.ui.Button):
        match = active_brawls.get(self.message_id)
        if not match: return
        if str(interaction.user.id) != match['host_id']:
            await interaction.response.send_message("Only the Host can cancel the game!", ephemeral=True)
            return
            
        del active_brawls[self.message_id]
        await interaction.response.edit_message(content="**Brawl Canceled by Host.**", embed=None, view=None)

class BrawlPlayView(discord.ui.View):
    def __init__(self, message_id: str):
        super().__init__(timeout=None)
        self.message_id = message_id
        
    async def handle_lock(self, interaction: discord.Interaction, choice: str):
        match = active_brawls.get(self.message_id)
        if not match or match['status'] != 'playing':
            await interaction.response.send_message("This matches choices are closed!", ephemeral=True)
            return
            
        uid = str(interaction.user.id)
        if uid not in match['players']:
            await interaction.response.send_message("You are not part of this Brawl!", ephemeral=True)
            return
            
        if match['players'][uid]['choice'] is not None:
            await interaction.response.send_message("You already locked in!", ephemeral=True)
            return
            
        match['players'][uid]['choice'] = choice
        await interaction.response.send_message(f"You securely locked in **{choice.title()}**! 🤫", ephemeral=True)
        
        all_locked = all(p['choice'] is not None for p in match['players'].values())
        if all_locked:
            match['status'] = 'resolving'
            try: await interaction.message.edit(view=None)
            except: pass
            await resolve_brawl_round(interaction.message, self.message_id, match['round_num'])
            
    @discord.ui.button(label="Rock", style=discord.ButtonStyle.secondary, custom_id="brawl_btn_rock", emoji="🪨")
    async def lock_rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_lock(interaction, "rock")
        
    @discord.ui.button(label="Paper", style=discord.ButtonStyle.secondary, custom_id="brawl_btn_paper", emoji="📄")
    async def lock_paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_lock(interaction, "paper")
        
    @discord.ui.button(label="Scissors", style=discord.ButtonStyle.secondary, custom_id="brawl_btn_scissors", emoji="✂️")
    async def lock_scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_lock(interaction, "scissors")
        
    @discord.ui.button(label="Host: Force Skip AFK", style=discord.ButtonStyle.danger, custom_id="brawl_btn_skip", row=1)
    async def force_skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        match = active_brawls.get(self.message_id)
        if not match or match['status'] != 'playing': return
        if str(interaction.user.id) != match['host_id']:
            await interaction.response.send_message("Only the Host can Force Skip!", ephemeral=True)
            return
            
        afk_uids = [uid for uid, p in match['players'].items() if p['choice'] is None]
        for uid in afk_uids:
            del match['players'][uid]
            
        if len(match['players']) < 2:
            del active_brawls[self.message_id]
            await interaction.response.edit_message(content="Not enough players left. Game ended.", embed=None, view=None)
            return
            
        match['status'] = 'resolving'
        await interaction.response.defer()
        try: await interaction.message.edit(view=None)
        except: pass
        await resolve_brawl_round(interaction.message, self.message_id, match['round_num'])

async def spawn_brawl_round(msg: discord.Message, message_id: str, round_num: int):
    match = active_brawls.get(message_id)
    if not match: return
    
    match['round_num'] = round_num
    for uid in match['players']:
        match['players'][uid]['choice'] = None
        
    match['status'] = 'playing'
    
    mentions = " ".join([f"<@{uid}>" for uid in match['players']])
    embed = discord.Embed(title=f"⚔️ Brawl! Round {round_num} of {match['max_rounds']}", description=f"The match has started! All players, click your throw below securely!\n\n**Players:** {mentions}", color=discord.Color.red())
    view = BrawlPlayView(message_id)
    
    try:
        await msg.edit(content=mentions, embed=embed, view=view)
    except Exception as e:
        print(e)
        
async def resolve_brawl_round(msg: discord.Message, message_id: str, round_num: int):
    match = active_brawls.get(message_id)
    if not match: return
    
    players = match['players']
    scores_this_round = {uid: 0 for uid in players.keys()}
    win_map = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
    
    for uid1, data1 in players.items():
        for uid2, data2 in players.items():
            if uid1 == uid2: continue
            
            p1_choice = data1['choice']
            p2_choice = data2['choice']
            
            if win_map[p1_choice] == p2_choice:
                scores_this_round[uid1] += 1
                
    for uid, pts in scores_this_round.items():
        match['db_scores'][uid] = match['db_scores'].get(uid, 0) + pts
        
    embed = discord.Embed(title=f"⚔️ Brawl Results (Round {round_num} of {match['max_rounds']})", color=discord.Color.purple())
    
    choice_groups = {"rock": [], "paper": [], "scissors": []}
    for uid, data in players.items():
        choice_groups[data['choice']].append(data['name'])
        
    desc = ""
    if choice_groups["rock"]: desc += f"🪨 **Rock:** {', '.join(choice_groups['rock'])}\n"
    if choice_groups["paper"]: desc += f"📄 **Paper:** {', '.join(choice_groups['paper'])}\n"
    if choice_groups["scissors"]: desc += f"✂️ **Scissors:** {', '.join(choice_groups['scissors'])}\n"
        
    desc += "\n**Match Leaderboard:**\n"
    sorted_scores = sorted(match['db_scores'].items(), key=lambda x: x[1], reverse=True)
    for uid, total_pts in sorted_scores:
        round_pts = scores_this_round.get(uid, 0)
        if round_pts > 0: desc += f"<@{uid}>: **{total_pts}** pts (+{round_pts})\n"
        else: desc += f"<@{uid}>: **{total_pts}** pts\n"
        
    embed.description = desc
    
    view = discord.ui.View(timeout=None)
    if round_num < match['max_rounds']:
        next_btn = discord.ui.Button(label=f"Host: Start Round {round_num + 1}", style=discord.ButtonStyle.primary, emoji="🔥")
        async def next_round_cb(btn_int: discord.Interaction):
            if str(btn_int.user.id) != match['host_id']:
                await btn_int.response.send_message("Only the Host can start the next round!", ephemeral=True)
                return
            await btn_int.response.defer()
            await spawn_brawl_round(msg, message_id, round_num + 1)
        next_btn.callback = next_round_cb
        view.add_item(next_btn)
        
    end_btn = discord.ui.Button(label="Host: End Game", style=discord.ButtonStyle.danger)
    async def end_cb(btn_int: discord.Interaction):
        if str(btn_int.user.id) != match['host_id']:
            await btn_int.response.send_message("Only the Host can end the game!", ephemeral=True)
            return
            
        del active_brawls[message_id]
        embed.title = "🏆 Final Brawl Results"
        await btn_int.response.edit_message(embed=embed, view=None)
        
        # Save points to DB here if we wanted persistent points! 
        # (For now the user requested a unified session wipe, so we skip standard DB saving, 
        #  but we COULD integrate it into the general rps_scores).
        
    end_btn.callback = end_cb
    view.add_item(end_btn)
    
    try: await msg.edit(content=None, embed=embed, view=view)
    except: pass

async def tz_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    choices = [app_commands.Choice(name="UTC", value="UTC")]
    
    # Filter available zones
    matched = []
    current_lower = current.lower()
    for z in zoneinfo.available_timezones():
        if current_lower in z.lower():

            matched.append(app_commands.Choice(name=z, value=z))
            
    matched.sort(key=lambda x: x.name)
    choices.extend(matched)
    return choices[:25]

@bot.tree.command(name="set_timezone", description="Set your local timezone for perfect timer creation!")
@app_commands.autocomplete(timezone=tz_autocomplete)
@app_commands.describe(timezone="Search for your timezone (e.g., 'Asia/Kolkata', 'America/New_York', 'UTC')")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def set_timezone_slash(interaction: discord.Interaction, timezone: str):
    success = set_user_tz_str(interaction.user.id, timezone)
    if success:
        await interaction.response.send_message(f"✅ Your timezone has been secured as **{timezone}**!\n\nWhen you create timers via DM or the Command Menu, I will now assume the time you type belongs to this timezone instead of raw UTC. Easy!", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ Failed to set timezone. Please type a valid timezone like 'America/New_York' or 'Asia/Kolkata'. You provided: {timezone}", ephemeral=True)

# @bot.tree.command(name="brawl", description="Start a multiplayer RPS Brawl!")
# @app_commands.describe(max_rounds="Number of rounds (Default 3, Max 10)")
# @app_commands.allowed_contexts(guilds=True)
# async def brawl_slash(interaction: discord.Interaction, max_rounds: app_commands.Range[int, 1, 10] = 3):
    message_id = f"{interaction.id}"
    
    active_brawls[message_id] = {
        'host_id': str(interaction.user.id),
        'max_rounds': max_rounds,
        'status': 'joining',
        'round_num': 1,
        'db_scores': {str(interaction.user.id): 0},
        'players': {
            str(interaction.user.id): {'name': interaction.user.display_name, 'choice': None}
        }
    }
    
    embed = discord.Embed(title="⚔️ RPS Brawl Lobby!", description=f"<@{interaction.user.id}> started a Brawl! Click **Join Brawl!** if you want to play.\n\n**Joined Players:** {interaction.user.display_name}", color=discord.Color.purple())
    view = BrawlJoinView(message_id)
    await interaction.response.send_message(embed=embed, view=view)



@bot.event
async def on_message(message):
    if message.author.bot: return
    
    # NLP Bot Mention Listener
    if bot.user in message.mentions:
        # Check if the message has "remind" or similar intent (optional but good)
        content_no_mentions = message.content.replace(f'<@{bot.user.id}>', '').strip()
        if content_no_mentions:
            try:
                # We can't easily defer an on_message like an interaction, so we send a thinking message
                msg = await message.reply("⏳ Thinking...")
                parsed = await parse_natural_language_groq(content_no_mentions)
                user_tz = get_user_tz_str(message.author.id)
                
                time_str = parsed.get("time_string", "")
                if not time_str: raise ValueError("Could not determine a time.")
                
                end_epoch = parse_time_input(time_str, "smart", user_tz)
                
                recurrence_seconds = 0
                interval_str = parsed.get("interval_string", "0")
                if interval_str and interval_str != "0":
                    try: recurrence_seconds = parse_duration_string(interval_str)
                    except: pass
                    
                reminders_list = []
                reminders_str = parsed.get("reminders_string")
                if reminders_str:
                    reminders_list = parse_reminders_string(reminders_str)
                    
                label = parsed.get("label", "Reminder")
                notify_method = "📢 Message in Server (Ping Role)" if message.guild else "📩 DM Me"
                
                owner_id = message.author.id
                
                await add_timer_internal(message.guild, label, end_epoch, None, notify_method, "smart", recurrence_seconds, None, 900, reminders_list, owner_id=owner_id)
                
                ts = int(end_epoch)
                embed = discord.Embed(title="✅ Timer Set (NLP)", color=discord.Color.green())
                desc = f"**{label}**\n📅 <t:{ts}:F> (<t:{ts}:R>)\n"
                if recurrence_seconds > 0:
                    desc += f"🔄 Repeats: {get_interval_str(recurrence_seconds)}\n"
                embed.description = desc
                await msg.edit(content=None, embed=embed)
                
            except ValueError as e:
                await msg.edit(content=f"❌ {str(e)}")
            except Exception as e:
                logger.error(f"NLP error: {e}")
                await msg.edit(content=f"❌ An error occurred parsing that.")
    
    # DM Handlers
    if isinstance(message.channel, discord.DMChannel):
        # DM Setup Wizard Handler
        if message.author.id in user_setup_state:
            await handle_dm_setup_step(message)
            return  # Don't process further

        # Cycle Event Scheduler
        if message.author.id in user_cycle_states:
            state = user_cycle_states[message.author.id]
            guild_id = state["guild_id"]
            cycle_name = state["cycle_name"]
            
            try:
                msg = await message.channel.send(f"⏳ Processing time for **{cycle_name}**...")
                
                # Use Groq to parse the time
                parsed = await parse_natural_language_groq(f"Set {cycle_name} to {message.content}")
                user_tz = get_user_tz_str(message.author.id)
                time_str = parsed.get("time_string", "")
                if not time_str: raise ValueError("Could not determine a time.")
                
                end_epoch = parse_time_input(time_str, "smart", user_tz)
                guild = bot.get_guild(guild_id)
                
                if not guild:
                    await msg.edit(content="❌ Could not find your Server to schedule this.")
                else:
                    await add_timer_internal(
                        guild, 
                        parsed.get("label", cycle_name), 
                        end_epoch, 
                        None, 
                        "📢 Message in Server (Ping Role)", 
                        "smart", 
                        0, 
                        None, 
                        900, 
                        [], 
                        owner_id=message.author.id
                    )
                    await msg.edit(content=f"✅ Automatically published **{cycle_name}** to the Server Dashboard!")
                    
                del user_cycle_states[message.author.id]
                return
            except ValueError as e:
                await msg.edit(content=f"❌ {str(e)}\nTry replying again with the time.")
                return
            except Exception as e:
                logger.error(f"Cycle NLP error: {e}")
                await msg.edit(content="❌ An error occurred parsing that. Try replying again with the time.")
                return
        
        # Foundry Handler
        if message.author.id in user_foundry_state:
            state = user_foundry_state[message.author.id]
            step = state["step"]
            
            if step == "awaiting_l1_time":
                # Parse Legion 1 Time
                content = message.content.lower().strip()
                match = re.search(r"\d{1,2}", content)
                if match:
                    hour = int(match.group(0))
                    if 0 <= hour <= 23:
                         user_foundry_state[message.author.id]["l1_time"] = hour
                         user_foundry_state[message.author.id]["step"] = "awaiting_l2_time"
                         await message.channel.send("Got it. Now, what is the **Legion 2 time in UTC**? (Reply with the hour, e.g., '14' or '19')")
                    else:
                         await message.channel.send("❌ Invalid hour (0-23). Try again (e.g., '14').")
                else:
                     await message.channel.send("❌ I didn't see an hour. Try again (e.g., '14').")
            
            elif step == "awaiting_l2_time":
                # Parse Legion 2 Time
                content = message.content.lower().strip()
                match = re.search(r"\d{1,2}", content)
                if match:
                    hour = int(match.group(0))
                    if 0 <= hour <= 23:
                         l1_time = user_foundry_state[message.author.id]["l1_time"]
                         user_foundry_state[message.author.id]["temp_hours"] = [l1_time, hour]
                         user_foundry_state[message.author.id]["step"] = "awaiting_confirm"
                         await message.channel.send(f"Are you sure your **Legion 1** time is **{l1_time}:00 UTC** and **Legion 2** time is **{hour}:00 UTC**? (Reply **Yes** or **No**, if no let's restart)")
                    else:
                         await message.channel.send("❌ Invalid hour (0-23). Try again (e.g., '14').")
                else:
                     await message.channel.send("❌ I didn't see an hour. Try again (e.g., '14').")
            
            elif step == "awaiting_confirm":
                if "yes" in message.content.lower():
                    # Schedule
                    hours = user_foundry_state[message.author.id]["temp_hours"]
                    guild_id = state["guild_id"]
                    guild = bot.get_guild(guild_id)
                    
                    if guild:
                        sun_ts = get_next_sunday_from_now()
                        sun_dt = datetime.fromtimestamp(sun_ts, timezone.utc)
                        
                        count = 0
                        for h in hours:
                            target = sun_dt.replace(hour=h)
                            ts = int(target.timestamp())
                            
                            # Battle
                            title = f"🔥 Foundry Battle - Legion 1" if h == l1_time else f"🔥 Foundry Battle - Legion 2"
                            await add_timer_internal(guild, f"{title} ({h}:00)", ts, 12345, "📢 Message in Server (Ping Role)", "auto", 0, None, 3600, [600])
                            count += 1
                        
                        await message.channel.send(f"✅ Awesome! I've scheduled **{count} alerts** for this Sunday in **{guild.name}**.")
                        del user_foundry_state[message.author.id]
                    else:
                        await message.channel.send("❌ I can't find the server anymore.")
                        del user_foundry_state[message.author.id]

                else:
                    user_foundry_state[message.author.id]["step"] = "awaiting_l1_time"
                    await message.channel.send("Okay, let's restart. What is the **Legion 1 time in UTC**? (Reply with the hour, e.g., '14' or '19')")

    if bot.user.mentioned_in(message) and not message.mention_everyone:
        embed = discord.Embed(title="☁️ Chrono Dashboard", color=discord.Color.blurple())
        embed.description = "Chrono Scheduler Active."
        embed.add_field(name="Commands", value="`/timer`, `/edit`, `/start`", inline=False)
        try: await message.channel.send(embed=embed)
        except: pass
    await bot.process_commands(message)

# Helper Wrapper for Add Timer (Internal Use)
async def add_timer_internal(guild, label, end_epoch, role_id, notify, mode, recur, img, dur, rems, owner_id=None, description=None):
    # Mock Interaction for reusable logic? Hard to mock.
    # Better: access data directly.
    data = load_data()
    gid = str(guild.id)
    if gid not in data: return
    if "timers" not in data[gid]: data[gid]["timers"] = []
    
    evt_id = await create_discord_event(guild, label, end_epoch, dur, description=description)
    
    nt = {
        "label": label, "end_epoch": end_epoch, "start_epoch": int(time.time()),
        "owner_id": owner_id or bot.user.id, "role_id": role_id, "notify_method": notify,
        "mode": mode, "recurrence_seconds": recur, "discord_event_id": evt_id,
        "event_duration": dur, "reminders": rems, "sent_reminders": [],
        "description": description
    }
    data[gid]["timers"].append(nt)
    data[gid]["timers"].sort(key=lambda x: x["end_epoch"])
    save_data(data)
    await update_dashboard(guild, data[gid], resend=True)

# --- Loop ---
@tasks.loop(seconds=5)
async def check_timers():
    data = load_data()
    current_time = int(time.time())
    data_changed = False
    
    for context_id_str, context_data in data.items():
        if "timers" not in context_data: continue
        
        active_timers = []
        expired_timers = []
        
        # Context Resolution (Guild vs DM)
        guild = None
        user = None
        
        # Try to fetch guild first
        try:
             guild = bot.get_guild(int(context_id_str))
        except: pass
        
        # If no guild, maybe it's a User ID (DM)
        if not guild:
             try: user = await bot.fetch_user(int(context_id_str))
             except: pass
        
        # If neither, skip (stale data?)
        if not guild and not user: continue
    
        for timer in context_data["timers"]:
            
            # --- Check Foundry Job ---
            if timer.get("type") == "foundry_job":
                 # (Same Foundry Logic - uses owner_id so it works in DMs too if lead matches)
                 if timer["end_epoch"] <= current_time:
                     lead_id = timer["owner_id"]
                     try:
                         u = await bot.fetch_user(lead_id)
                         if u:
                             await u.send(f"👋 **Foundry Assistant here!**\nTime to schedule this Sunday's battle.\n\n**What is the Legion 1 time in UTC?** (Reply with the hour, e.g., `14` or `19`)")
                             user_foundry_state[lead_id] = {"step": "awaiting_l1_time", "guild_id": int(context_id_str)} # Store context
                     except: pass
                     timer["end_epoch"] += 1209600
                     timer["start_epoch"] = current_time
                     active_timers.append(timer)
                     data_changed = True
                     continue
            
            # --- Early Reminders (Robust) ---
            reminders = timer.get("reminders", [])
            sent = timer.get("sent_reminders", [])
            
            remain = timer["end_epoch"] - current_time
            
            for r_sec in reminders:
                if r_sec in sent: continue
                
                # Check for "Due Now" OR "Missed but Event still Active"
                # If remain <= r_sec, it means we passed the reminder point.
                # But we only send it if the event hasn't expired (remain > -60 for grace)
                if remain <= r_sec and remain > -60:
                     msg = ""
                     if remain > (r_sec - 30):
                         # Normal Timing (within 30s)
                         if r_sec == 600 and "Foundry Battle" in timer['label']:
                             msg = f"⚠️ **Attention!** `{timer['label']}` in 10 minutes! **Call all troops back and free up the hospital NOW!**"
                         else:
                             msg = f"⚠️ **Reminder:** `{timer['label']}` in {get_interval_str(r_sec)}!"
                     else:
                         # Late Timing (Missed window)
                         msg = f"⚠️ **Late Reminder:** `{timer['label']}` was due {get_interval_str(r_sec)} ago! (Event in {get_interval_str(remain)})"

                     try:
                        if guild:
                            # Fallback: Send to dashboard channel if exists
                            db_ch_id = context_data.get("dashboard_channel_id")
                            if db_ch_id:
                                ch = guild.get_channel(db_ch_id)
                                if ch: await ch.send(msg)
                        elif user:
                            await user.send(msg)
                     except: pass
                     
                     sent.append(r_sec)
                     timer["sent_reminders"] = sent
                     data_changed = True

            # --- 5-Minute Early Tag ---
            dur = timer["end_epoch"] - timer.get("start_epoch", timer["end_epoch"] - 301)
            if dur >= 300:
                if remain <= 300 and remain > -60 and "5min_ping" not in sent:
                    msg = f"⚠️ **Event Starting Soon:** `{timer['label']}` in {get_interval_str(remain)}!"
                    notify = timer.get('notify_method', 'Silent')
                    role_id = timer.get('role_id')
                    
                    content = msg
                    if "Ping Role" in notify and role_id:
                         content += f" <@&{role_id}>"
                    elif "everyone" in notify:
                         content += " @everyone"

                    try:
                        if guild:
                            db_ch_id = context_data.get("dashboard_channel_id")
                            if db_ch_id:
                                ch = guild.get_channel(db_ch_id)
                                if ch: await ch.send(content)
                        elif user:
                            await user.send(content)
                    except: pass
                    
                    sent.append("5min_ping")
                    timer["sent_reminders"] = sent
                    data_changed = True

            # --- Expiry Check ---
            if current_time >= timer["end_epoch"]:
                expired_timers.append(timer)
            else:
                active_timers.append(timer)
        
        # Process Expired
        for timer in expired_timers:
            lbl = timer['label']
            notify = timer.get('notify_method', 'Silent')
            owner_id = timer.get('owner_id')
            role_id = timer.get('role_id')
            
            msg = f"⏰ **Timer Ended:** {lbl}"
            
            # Notification Logic
            try:
                if guild:
                    # Find Channel: Dashboard Channel
                    db_ch_id = context_data.get("dashboard_channel_id")
                    channel = guild.get_channel(db_ch_id) if db_ch_id else None
                    if channel:
                         content = msg
                         # Ping at expiry only if timer was too short for the 5-min warning
                         dur = timer["end_epoch"] - timer.get("start_epoch", timer["end_epoch"] - 301)
                         if dur < 300:
                             if "Ping Role" in notify and role_id:
                                  content += f" <@&{role_id}>"
                             elif "everyone" in notify:
                                  content += " @everyone"
                         
                         await channel.send(content)
                elif user:
                    # DM Context
                    if "Chat" in notify:
                        # Try to send to the dashboard channel (Group DM or DM)
                        db_ch_id = context_data.get("dashboard_channel_id")
                        try:
                            # Try fetch if not cached (Group DMs often need fetch)
                            ch = bot.get_channel(db_ch_id) or await bot.fetch_channel(db_ch_id)
                            await ch.send(msg)
                        except Exception as e:
                            logger.warning(f"Failed to share in chat ({db_ch_id}): {e}. Falling back to DM.")
                            # Fallback to User DM with explanation
                            await user.send(f"{msg}\n*(Note: I couldn't post in the group chat, so I sent this to you privately.)*")
                    else:
                        # Default / Private
                        await user.send(msg)
            except Exception as e:
                logger.error(f"Failed to send alert: {e}")

            # Recurrence
            if timer["recurrence_seconds"] > 0:
                timer["end_epoch"] += timer["recurrence_seconds"]
                timer["start_epoch"] = current_time
                timer["sent_reminders"] = [] # Reset reminders
                active_timers.append(timer)
                data_changed = True
                
                # Re-create Event if Guild
                if guild and timer.get("discord_event_id"):
                     # Fire and forget delete old
                     asyncio.create_task(delete_discord_event(guild, timer["discord_event_id"]))
                     dur = timer.get("event_duration", 900)
                     # Create new
                     # Note: This await in loop might slow things down but ensures ID is saved.
                     # Given volume, likely fine.
                     new_id = await create_discord_event(guild, timer["label"], timer["end_epoch"], dur)
                     timer["discord_event_id"] = new_id

            
        context_data["timers"] = active_timers
        if expired_timers and not data_changed: data_changed = True # Removal counts as change

        # --- Cycle Checks ---
        cycles = context_data.get("cycles", [])
        for cycle in cycles:
            mgr_ids = context_data.get("timing_managers", [])
            if not mgr_ids: 
                if guild: mgr_ids = [guild.owner_id]
                else: continue
            
            # Step 1: Pre-Voting (24h before start_epoch)
            pre_time = cycle['start_epoch'] - 86400
            if current_time >= pre_time and not cycle.get('pre_dm_sent', False):
                cycle['pre_dm_sent'] = True
                data_changed = True
                for mid in set(mgr_ids):
                    try:
                        m = await bot.fetch_user(mid)
                        await m.send(f"🏆 **Reminder:** `{cycle['name']}` voting opens in 24 hours! Don't forget to post the poll.")
                    except: pass
            
            # Step 2: Post-Voting (start_epoch + duration_sec)
            post_time = cycle['start_epoch'] + cycle['duration_sec']
            if current_time >= post_time and not cycle.get('post_dm_sent', False):
                cycle['post_dm_sent'] = True
                data_changed = True
                
                for mid in set(mgr_ids):
                    try:
                        m = await bot.fetch_user(mid)
                        await m.send(f"🗳️ Voting has ended for `{cycle['name']}`!\n\n**What time are we running the event?**\n*(Reply here, e.g. \"Set {cycle['name']} for Thursday 14:00 UTC\")*")
                        if guild: user_cycle_states[mid] = {"guild_id": guild.id, "cycle_name": cycle['name']}
                    except: pass
                    
                # Move to next cycle
                if cycle['interval_sec'] > 0:
                    # Catch up if bot was offline
                    while current_time >= (cycle['start_epoch'] + cycle['interval_sec']):
                         cycle['start_epoch'] += cycle['interval_sec']
                    cycle['start_epoch'] += cycle['interval_sec']
                    cycle['pre_dm_sent'] = False
                    cycle['post_dm_sent'] = False

    if data_changed:
        save_data(data)
        # Refresh Dashboards
        for context_id_str, context_data in data.items():
            try:
                g = bot.get_guild(int(context_id_str))
                if g: await update_dashboard(g, context_data, resend=True)
            except: pass

@check_timers.before_loop
async def before_check_timers():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    logger.info(f"Chrono Cloudy v45 ONLINE as {bot.user}")
    
    # Only sync if not already done recently or if needed
    # (setup_hook already does this, but on_ready is a safety net)
    if not hasattr(bot, 'commands_synced'):
        try:
            synced = await bot.tree.sync()
            logger.info(f"✅ Auto-Synced {len(synced)} Global Commands")
            bot.commands_synced = True
        except Exception as e:
            logger.error(f"❌ Auto-Sync Failed: {e}")

    bot.add_view(DashboardView())
    await check_missed_events()
    if not check_timers.is_running(): check_timers.start()



if __name__ == "__main__":
    bot.run(TOKEN)
