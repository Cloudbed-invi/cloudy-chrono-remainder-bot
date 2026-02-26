import sys

with open("main.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

start_idx = -1
end_idx = -1

for i, line in enumerate(lines):
    if line.startswith("class RPSView(discord.ui.View):"):
        start_idx = i
    if line.startswith("    await execute_rps(interaction)"):
        end_idx = i

if start_idx == -1 or end_idx == -1:
    print("Could not find block!")
    sys.exit(1)

new_code = """active_targeted_rps = {}

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
            embed = discord.Embed(title="RPS Challenge Accepted!", description=f"<@{match['challenger_id']}> vs <@{match['target_id']}>\\n\\nBoth players, lock in your choices below!", color=discord.Color.green())
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
        
    result_text = f"<@{p1_id}> threw **{p1_choice.title()}**\\n<@{p2_id}> threw **{p2_choice.title()}**\\n"
    color = discord.Color.gold()
    
    is_bot_match = (p2_id == str(msg.author.id))
    
    if winner_id != "tie":
        winner_name = p1_name if winner_id == p1_id else p2_name
        winner_choice_str = p1_choice if winner_id == p1_id else p2_choice
        loser_choice_str = p2_choice if winner_id == p1_id else p1_choice
        
        color = discord.Color.green()
        result_text += f"\\n🏆 **{winner_name}** wins! ({winner_choice_str.title()} beats {loser_choice_str.title()})"
        
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
            
            result_text += f"\\n\\n**Scoreboard:**\\n{p1_name}: {scores.get(p1_id, 0)}\\n{p2_name}: {scores.get(p2_id, 0)}"
            
    else:
        result_text += f"\\n🤝 **It's a tie!** Both threw {p1_choice.title()}."
        
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

@bot.tree.command(name="rps", description="Challenge a user or the bot to Rock, Paper, Scissors!")
@app_commands.describe(target="The user to challenge (leave empty for Bot)", mode="How to play (Selection or Random)")
@app_commands.choices(mode=[
    app_commands.Choice(name="Selection (Pick moves)", value="Selection"),
    app_commands.Choice(name="Random (Auto RNG)", value="Random")
])
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
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
        embed = discord.Embed(title="⚔️ RPS Challenge!", description=f"<@{target.id}>, you have been challenged to RPS by <@{interaction.user.id}>!\\n\\nMode: **{chosen_mode}**", color=discord.Color.gold())
        await interaction.response.send_message(content=f"<@{target.id}>", embed=embed, view=view)
"""

lines = lines[:start_idx] + [new_code] + ["\n"] + lines[end_idx+1:]

with open("main.py", "w", encoding="utf-8") as f:
    f.writelines(lines)

print("Replaced old RPS successfully!")
