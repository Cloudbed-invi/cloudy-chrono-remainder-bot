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
        print(f"🌐 Health server running on port {port}")
    except Exception as e:
        print(f"❌ Failed to start health server: {e}") 
# ---------------------------

import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import time
import asyncio
import re
import logging
from datetime import datetime, timedelta, timezone

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger("Chrono")

# --- Configuration ---
from dotenv import load_dotenv
load_dotenv()
TOKEN = os.getenv("TOKEN")
DUMMY_SPACER = "https://dummyimage.com/600x1/2f3136/2f3136.png"

# --- Bot Setup ---
class StratusBot(commands.Bot):
    def __init__(self):
        # Deployment: All Intents needed for member/role fetch & events
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Start health check immediately, don't wait for Discord connection
        await start_health_server()

bot = StratusBot()

# --- Data Management (Supabase) ---
from db import load_data, save_data

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

# --- Helpers ---
def parse_duration_string(input_str: str) -> int:
    if not input_str: return 0
    clean_str = input_str.strip().lower().replace(" ", "")
    
    # Check for plain number (default to minutes)
    if clean_str.isdigit():
        return int(clean_str) * 60
        
    # Extended Regex for m, min, mins, h, hr, hours, d, day, days
    match = re.match(r"^(\d+)([a-z]+)$", clean_str)
    if match:
        val = int(match.group(1))
        unit = match.group(2)
        
        if unit in ['m', 'min', 'mins']: return val * 60
        elif unit in ['h', 'hr', 'hour', 'hours']: return val * 3600
        elif unit in ['d', 'day', 'days']: return val * 86400
    
    raise ValueError(f"Invalid Duration: '{input_str}'. Use '30m', '1h', or '1d'.")

def parse_reminders_string(input_str: str) -> list:
    if not input_str: return []
    try:
        parts = [p.strip() for p in input_str.split(',')]
        return [parse_duration_string(p) for p in parts if p]
    except: return []

def parse_time_input(user_input: str, mode: str = "smart") -> int:
    user_input = user_input.strip().lower()
    current_utc = datetime.now(timezone.utc)
    
    if mode == "smart":
        try: return parse_time_input(user_input, "utc_custom")
        except: pass
        if re.match(r"^\d{1,2}:\d{2}$", user_input):
             try: return parse_time_input(user_input, "utc_today")
             except: pass
        try: return parse_time_input(user_input, "duration")
        except: pass
        raise ValueError("Invalid Time. Use '10m', '14:00', or 'YYYY-MM-DD HH:MM'.")

    if mode == "duration":
        try:
            seconds = parse_duration_string(user_input)
            return int((current_utc + timedelta(seconds=seconds)).timestamp())
        except: raise ValueError("Invalid Duration.")
        
    elif mode == "utc_today":
        match = re.match(r"^(\d{1,2}):(\d{2})$", user_input)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            if not (0 <= hour <= 23 and 0 <= minute <= 59): raise ValueError("Time out of range.")
            target = current_utc.replace(hour=hour, minute=minute, second=0, microsecond=0)
            return int(target.timestamp())
        raise ValueError("Invalid Format.")

    elif mode == "utc_tomorrow":
        match = re.match(r"^(\d{1,2}):(\d{2})$", user_input)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            target = current_utc.replace(hour=hour, minute=minute, second=0, microsecond=0)
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
                dt = datetime.strptime(user_input, fmt)
                dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp())
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

def get_next_foundry_thursday() -> int:
    """Returns next Thursday 00:00 UTC."""
    now = datetime.now(timezone.utc)
    days_ahead = (3 - now.weekday()) % 7 # Thursday is 3
    if days_ahead == 0 and now.hour > 0: days_ahead = 7
    
    target = now + timedelta(days=days_ahead)
    target = target.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(target.timestamp())

def get_next_sunday_from_now() -> int:
    """Returns next Sunday relative to now."""
    now = datetime.now(timezone.utc)
    days_ahead = (6 - now.weekday()) % 7 # Sunday is 6
    if days_ahead == 0: days_ahead = 7 # Next Sunday
    
    target = now + timedelta(days=days_ahead)
    target = target.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(target.timestamp())

# --- Discord Event Helpers ---
async def create_discord_event(guild: discord.Guild, label: str, start_epoch: int, duration_seconds: int = 900):
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
            description="Timer managed by Chrono Cloudy.",
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
    """True if user is Owner OR Admin."""
    if interaction.user.id == owner_id: return True
    if is_admin(interaction): return True
    return False

# --- Foundry State ---
user_foundry_state = {} # {user_id: {"step": "awaiting_time", "guild_id": 123, "channel_id": 456}}

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
    def __init__(self, mode: str, notify_method: str, role_id: int, default_label: str = None, default_time: str = None, template_type: str = None):
        super().__init__()
        self.mode = mode
        self.notify_method = notify_method
        self.role_id = role_id
        
        self.label_input = discord.ui.TextInput(
            label="Event Label", placeholder="e.g. Server Restart", default=default_label, max_length=50
        )
        self.add_item(self.label_input)
        
        self.time_input = discord.ui.TextInput(
            label="Time Until Alert", placeholder="e.g. 10m, 14:00, or 2026-02-19 12:30", default=default_time, min_length=2, max_length=20
        )
        self.add_item(self.time_input)

        # Template Params
        def_recur = None
        def_adv = None
        
        if template_type == "Internal":
            def_recur = "28d"
            def_adv = "5h | 30m, 5m"
        elif template_type == "SvS":
            def_recur = "28d"
            def_adv = "5h | 2h, 1h"
        elif template_type == "Arena":
            def_recur = "24h"
            def_adv = "5m"
        elif template_type == "Bear":
            def_recur = "47h 30m" # Approx 2 days
            def_adv = "30m | 5m"
        elif template_type == "Joe":
             def_recur = "0"
             def_adv = "40m | 5m"

        self.recur_input = discord.ui.TextInput(
            label="Repeat Interval (Optional)", placeholder="e.g. 5m, 24h", default=def_recur, required=False, max_length=10
        )
        self.add_item(self.recur_input)
        
        self.adv_input = discord.ui.TextInput(
             label="Duration | Reminder (Optional)", placeholder="1h | 10m, 5m", default=def_adv, required=False, max_length=50
        )
        self.add_item(self.adv_input)
        
        self.image_input = discord.ui.TextInput(
            label="Image/GIF URL (Optional)", placeholder="e.g. Tenor Link", required=False, max_length=200
        )
        self.add_item(self.image_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        event_duration = 0
        reminders = []
        try:
            # Fix: Always use 'smart' mode to handle mixed inputs (e.g. 5m OR 2026-02-12)
            end_epoch = parse_time_input(self.time_input.value, "smart")
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

        label = self.label_input.value
        image_url = self.image_input.value.strip() or None
        
        await add_timer(interaction, label, end_epoch, self.role_id, self.notify_method, self.mode, recurrence_seconds, image_url, event_duration, reminders)
        try: await interaction.message.edit(content="✅ **Configuration Saved**", view=None)
        except: pass

class TimerWizardView(discord.ui.View):
    def __init__(self, is_dm=False):
        super().__init__(timeout=300)
        self.template = "Custom"
        self.mode = "duration"
        self.notify_method = "📢 Message in Server (Ping Role)"
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
             discord.SelectOption(label="Foundry Auto", description="Auto-DM Lead on Thursday", emoji="🔥", value="Foundry"),
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
            discord.SelectOption(label="📢 Message in Server (Ping Role)", default=True),
            discord.SelectOption(label="⚠️ Message in Server (Ping @everyone)"),
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
            end_epoch = get_next_foundry_thursday()
            
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
            await interaction.followup.send(f"✅ **Foundry Automation Active!**\nI will DM {self.foundry_lead.mention} every Thursday.", ephemeral=True)
            return

        def_label = None
        def_time = None
        
        # Template Logic
        if self.template == "Test Template":
            def_label = "Test Event"
            def_time = "1m"
            
        elif self.template == "Internal":
            # Reference: Feb 14, 2026 12:00 UTC
            next_ts = get_next_cycle(2026, 2, 14, 12)
            dt = datetime.fromtimestamp(next_ts, timezone.utc)
            def_label = "Internal Castle [Battle]"
            def_time = dt.strftime("%Y-%m-%d %H:%M")
            # Pre-fill modal isn't possible directly for all fields in easy way without custom modal
            # But we can pass these to the modal constructor to pre-fill
            
        elif self.template == "SvS":
            # Reference: Feb 28, 2026 12:00 UTC
            next_ts = get_next_cycle(2026, 2, 28, 12)
            dt = datetime.fromtimestamp(next_ts, timezone.utc)
            def_label = "SvS Castle Battle"
            def_time = dt.strftime("%Y-%m-%d %H:%M")
            
        elif self.template == "Arena":
             # Next 23:55 UTC
             now = datetime.now(timezone.utc)
             target = now.replace(hour=23, minute=55, second=0, microsecond=0)
             if target <= now: target += timedelta(days=1)
             def_label = "Arena Reset"
             def_time = target.strftime("%Y-%m-%d %H:%M")
             
        elif self.template == "Bear":
             def_label = "🐻 Bear Trap"
             def_time = "10m" # Usually set just before opening
             
        elif self.template == "Joe":
             def_label = "🤡 Crazy Joe"
             def_time = "10m"

        if self.mode == "duration" or self.template == "Foundry": pass # Foundry handled above
        else:
            def_time = def_time or "10m"
            
        modal = TimerDetailsModal(
            self.mode, self.notify_method, self.role_id, 
            def_label, def_time, template_type=self.template
        )
        await interaction.response.send_modal(modal)

class DashboardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="  ➕ New Alert  ", style=discord.ButtonStyle.blurple, custom_id="btn_yeti_new")
    async def new_operation(self, interaction: discord.Interaction, button: discord.ui.Button):
        is_dm = interaction.guild is None
        await interaction.response.send_message(
            "**☁️ Chrono Dashboard**\nSet up a new alert:",
            view=TimerWizardView(is_dm=is_dm), ephemeral=True
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
async def add_timer(interaction: discord.Interaction, label: str, end_epoch: int, role_id: int, notify_method: str, mode: str, recurrence_seconds: int = 0, image_url: str = None, event_duration: int = 900, reminders: list = None):
    # Context ID (Guild OR User)
    context_id = str(interaction.guild_id) if interaction.guild else str(interaction.user.id)
    is_dm = interaction.guild is None

    data = load_data()
    if context_id not in data: data[context_id] = {"timers": []}
    if "timers" not in data[context_id]: data[context_id]["timers"] = []
    
    # Create Discord Event (Only if Guild)
    discord_event_id = None
    if not is_dm and mode != "silent":
         discord_event_id = await create_discord_event(interaction.guild, label, end_epoch, event_duration)
    
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
        "sent_reminders": []
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
                     description += f"> **{timer['label']}** (by {owner}){role_tag} {icon} {repeat_icon}\n> ⏱️ <t:{ts}:f> (<t:{ts}:R>)\n\n"
        
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
    if guild_id in data:
        await ctx.send("🔄 **Refreshing Dashboard...**")
        await update_dashboard(ctx.guild, data[guild_id], resend=True)
        # Delete the trigger command and confirmation to keep chat clean
        try: await ctx.message.delete() 
        except: pass
    else:
        await ctx.send("❌ No dashboard found. Use `!start` first.")

@bot.tree.command(name="refresh", description="Force Refresh & Pin Dashboard")
@app_commands.checks.has_permissions(administrator=True)
async def refresh_slash(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    data = load_data()
    guild_id = str(interaction.guild_id)
    if guild_id in data:
        await update_dashboard(interaction.guild, data[guild_id], resend=True)
        await interaction.followup.send("✅ **Dashboard Refreshed & Pinned!**", ephemeral=True)
    else:
        await interaction.followup.send("❌ No dashboard found. Use `/start` first.", ephemeral=True)

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

@bot.tree.command(name="timer", description="Quickly set a timer")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    label="Name of the event", 
    time="Time string (e.g. 10m, 14:00)",
    role="Role to ping (optional)",
    image="Upload an image/GIF",
    image_link="Paste an image/GIF URL",
    interval="Repeat interval (e.g. 24h)",
    duration="Event Duration (Optional, def: 15m)",
    reminders="Early reminders (e.g. '10m, 5m')"
)
async def timer_slash(interaction: discord.Interaction, label: str, time: str, role: discord.Role = None, image: discord.Attachment = None, image_link: str = None, interval: str = None, duration: str = None, reminders: str = None):
    await interaction.response.defer(ephemeral=True)
    try:
        end_epoch = parse_time_input(time, "smart")
    except ValueError:
        await interaction.followup.send("❌ Invalid Time.", ephemeral=True)
        return
    
    recurrence_seconds = 0
    if interval:
       try: recurrence_seconds = parse_duration_string(interval)
       except: pass

    event_duration_sec = 900 # 15 min default
    if duration:
        try: event_duration_sec = parse_duration_string(duration)
        except: pass
    
    reminders_list = []
    if reminders:
        reminders_list = parse_reminders_string(reminders)

    final_image = None
    if image: final_image = image.url
    elif image_link: final_image = image_link
    
    role_id = role.id if role else None
    notify_method = "📢 Message in Server (Ping Role)" 
    
    await add_timer(interaction, label, end_epoch, role_id, notify_method, "smart", recurrence_seconds, final_image, event_duration_sec, reminders_list)

@bot.tree.command(name="edit", description="Edit an existing timer by label")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    label="Exact name of the timer",
    new_time="New time string (optional)",
    new_image="New image/GIF URL or 'none' (optional)",
    new_interval="New interval e.g. 24h or '0' (optional)",
    new_image_file="Upload a new image file",
    new_duration="New Event Duration (Optional)",
    new_reminders="New Reminders e.g. '10m, 5m' (Optional)"
)
@app_commands.autocomplete(label=timer_autocomplete)
async def edit_slash(interaction: discord.Interaction, label: str, new_time: str = None, new_image: str = None, new_interval: str = None, new_image_file: discord.Attachment = None, new_duration: str = None, new_reminders: str = None):
    await interaction.response.defer(ephemeral=True)
    data = load_data()
    # Context
    context_id = str(interaction.guild_id) if interaction.guild else str(interaction.user.id)
    
    if context_id not in data or "timers" not in data[context_id]:
        await interaction.followup.send("❌ No timers found.", ephemeral=True); return

    found = False
    for t in data[context_id]["timers"]:
        if t['label'].lower() == label.lower():
            # Security Check
            if not check_permissions(interaction, t['owner_id']):
                await interaction.followup.send("❌ **Access Denied.** You can only edit your own timers.", ephemeral=True)
                return

            try:
                if new_time:
                    t["end_epoch"] = parse_time_input(new_time, "smart")
                    t["start_epoch"] = int(time.time())
                    t["sent_reminders"] = [] # Reset reminders
                if new_interval:
                    if new_interval == "0": t["recurrence_seconds"] = 0
                    else: t["recurrence_seconds"] = parse_duration_string(new_interval)
                
                if new_image_file:
                     t["image_url"] = new_image_file.url
                elif new_image:
                    if new_image.lower() == "none":
                        if "image_url" in t: del t["image_url"]
                    else:
                         t["image_url"] = new_image

                if new_duration:
                    t["event_duration"] = parse_duration_string(new_duration)
                if new_reminders:
                    t["reminders"] = parse_reminders_string(new_reminders)
                    t["sent_reminders"] = [] 
                
                # Sync Event (Only Guild)
                if t.get("discord_event_id") and interaction.guild:
                     dur = t.get("event_duration", 900)
                     await update_discord_event(interaction.guild, t["discord_event_id"], t["label"], t["end_epoch"], dur)
                
                found = True
                break
            except ValueError:
                await interaction.followup.send("❌ Invalid input format.", ephemeral=True); return
    
    if found:
        data[context_id]["timers"].sort(key=lambda x: x["end_epoch"])
        save_data(data)
        if interaction.guild: await update_dashboard(interaction.guild, data[context_id], resend=True)
        await interaction.followup.send(f"✅ Updated timer **{label}**.", ephemeral=True)
    else:
        await interaction.followup.send(f"❌ Timer **{label}** not found.", ephemeral=True)

@bot.tree.command(name="delete", description="Delete an existing timer")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(label="Name of the timer to delete")
@app_commands.autocomplete(label=timer_autocomplete)
async def delete_slash(interaction: discord.Interaction, label: str):
    await interaction.response.defer(ephemeral=True)
    data = load_data()
    context_id = str(interaction.guild_id) if interaction.guild else str(interaction.user.id)
    
    if context_id in data and "timers" in data[context_id]:
        for idx, t in enumerate(data[context_id]["timers"]):
            if t['label'] == label:
                # Security Check
                if not check_permissions(interaction, t['owner_id']):
                    await interaction.followup.send("❌ **Access Denied.** You can only delete your own timers.", ephemeral=True)
                    return

                removed = data[context_id]["timers"].pop(idx)
                
                if removed.get("discord_event_id") and interaction.guild:
                    await delete_discord_event(interaction.guild, removed["discord_event_id"])
                    
                save_data(data)
                if interaction.guild: await update_dashboard(interaction.guild, data[context_id], resend=True)
                await interaction.followup.send(f"✅ Deleted timer **{label}**.", ephemeral=True)
                return
    
    await interaction.followup.send(f"❌ Timer **{label}** not found.", ephemeral=True)

@bot.tree.command(name="dashboard", description="Create or Move the Chrono Dashboard")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def dashboard(interaction: discord.Interaction):
    # Context ID
    context_id = str(interaction.guild_id) if interaction.guild else str(interaction.user.id)
    is_dm = interaction.guild is None

    data = load_data()
    if context_id not in data: data[context_id] = {}
    
    embed = discord.Embed(title="🌩️ Stratus Timers", color=discord.Color.dark_theme())
    embed.description = "No active timers."
    
    # Send new dashboard
    view = DashboardView()
    # Use interaction response directly (User App Safe)
    await interaction.response.send_message(embed=embed, view=view)
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

@bot.event
async def on_message(message):
    if message.author.bot: return
    
    # DM Handler for Foundry
    if isinstance(message.channel, discord.DMChannel):
        if message.author.id in user_foundry_state:
            state = user_foundry_state[message.author.id]
            step = state["step"]
            
            if step == "awaiting_time":
                # Parse Time
                content = message.content.lower().strip()
                # Find number
                match = re.search(r"\d{1,2}", content)
                if match:
                    hour = int(match.group(0))
                    if 0 <= hour <= 23:
                         # confirm
                         user_foundry_state[message.author.id]["temp_hours"] = [hour]
                         user_foundry_state[message.author.id]["step"] = "awaiting_confirm"
                         await message.channel.send(f"Found Sunday @ **{hour}:00 UTC**. Is this correct? (Reply **Yes** or **No**)")
                    else:
                         await message.channel.send("❌ Invalid hour (0-23). Try again (e.g. '14').")
                else:
                     await message.channel.send("❌ I didn't see an hour. Try again (e.g. '14').")
            
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
                            
                            # Prep (-1h)
                            prep_ts = ts - 3600
                            await add_timer_internal(guild, f"⚠️ Foundry Prep ({h}:00 match)", prep_ts, 12345, "📢 Message in Server (Ping Role)", "auto", 0, None, 900, [])
                            
                            # Battle
                            await add_timer_internal(guild, f"🔥 Foundry Battle ({h}:00 match)", ts, 12345, "📢 Message in Server (Ping Role)", "auto", 0, None, 3600, [])
                            count += 2
                        
                        await message.channel.send(f"✅ Awesome! I've scheduled **{count} alerts** for this Sunday in **{guild.name}**.")
                        del user_foundry_state[message.author.id]
                    else:
                        await message.channel.send("❌ I can't find the server anymore.")
                        del user_foundry_state[message.author.id]

                else:
                    user_foundry_state[message.author.id]["step"] = "awaiting_time"
                    await message.channel.send("Okay, let's try again. What time is the battle? (e.g. '14' or '19')")

    if bot.user.mentioned_in(message) and not message.mention_everyone:
        embed = discord.Embed(title="☁️ Chrono Dashboard", color=discord.Color.blurple())
        embed.description = "Chrono Scheduler Active."
        embed.add_field(name="Commands", value="`/timer`, `/edit`, `/start`", inline=False)
        try: await message.channel.send(embed=embed)
        except: pass
    await bot.process_commands(message)

# Helper Wrapper for Add Timer (Internal Use)
async def add_timer_internal(guild, label, end_epoch, role_id, notify, mode, recur, img, dur, rems):
    # Mock Interaction for reusable logic? Hard to mock.
    # Better: access data directly.
    data = load_data()
    gid = str(guild.id)
    if gid not in data: return
    if "timers" not in data[gid]: data[gid]["timers"] = []
    
    evt_id = await create_discord_event(guild, label, end_epoch, dur)
    
    nt = {
        "label": label, "end_epoch": end_epoch, "start_epoch": int(time.time()),
        "owner_id": bot.user.id, "role_id": None, "notify_method": notify,
        "mode": mode, "recurrence_seconds": recur, "discord_event_id": evt_id,
        "event_duration": dur, "reminders": rems, "sent_reminders": []
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
                             await u.send(f"👋 **Foundry Assistant here!**\nTime to schedule this Sunday's battle.\n\n**What time is the battle?** (Reply with the hour, e.g. `14` or `19`)")
                             user_foundry_state[lead_id] = {"step": "awaiting_time", "guild_id": int(context_id_str)} # Store context
                     except: pass
                     timer["end_epoch"] += 604800
                     timer["start_epoch"] = current_time
                     active_timers.append(timer)
                     data_changed = True
                     continue
            
            # --- Early Reminders (Robust) ---
            reminders = timer.get("reminders", [])
            sent = timer.get("sent_reminders", [])
            
            for r_sec in reminders:
                if r_sec in sent: continue
                remain = timer["end_epoch"] - current_time
                
                # Check for "Due Now" OR "Missed but Event still Active"
                # If remain <= r_sec, it means we passed the reminder point.
                # But we only send it if the event hasn't expired (remain > -60 for grace)
                if remain <= r_sec and remain > -60:
                     msg = ""
                     if remain > (r_sec - 30):
                         # Normal Timing (within 30s)
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
    
    # Auto-Sync Global Commands (For DMs/User Install)
    try:
        synced = await bot.tree.sync()
        logger.info(f"✅ Auto-Synced {len(synced)} Global Commands")
    except Exception as e:
        logger.error(f"❌ Auto-Sync Failed: {e}")

    bot.add_view(DashboardView())
    await check_missed_events()
    if not check_timers.is_running(): check_timers.start()



if __name__ == "__main__":
    bot.run(TOKEN)
