import sys

with open("main.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

# Find the end of `def rps_slash:` to append Brawl logic
insertion_idx = -1
for i, line in enumerate(lines):
    # Search backwards for the last rps_slash
    if line.startswith("@bot.tree.command(name=\"rps\""):
        # find the def rps_slash below it
        for j in range(i, len(lines)):
            if lines[j].startswith("async def rps_slash"):
                # find the end of this function
                for k in range(j+1, len(lines)):
                    if lines[k].startswith("@") or k == len(lines)-1:
                        insertion_idx = k
                        break
                break
        break

if insertion_idx == -1:
    print("Could not find rps_slash end!")
    sys.exit(1)

new_code = """
active_brawls = {}

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
    embed = discord.Embed(title=f"⚔️ Brawl! Round {round_num} of {match['max_rounds']}", description=f"The match has started! All players, click your throw below securely!\\n\\n**Players:** {mentions}", color=discord.Color.red())
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
    if choice_groups["rock"]: desc += f"🪨 **Rock:** {', '.join(choice_groups['rock'])}\\n"
    if choice_groups["paper"]: desc += f"📄 **Paper:** {', '.join(choice_groups['paper'])}\\n"
    if choice_groups["scissors"]: desc += f"✂️ **Scissors:** {', '.join(choice_groups['scissors'])}\\n"
        
    desc += "\\n**Match Leaderboard:**\\n"
    sorted_scores = sorted(match['db_scores'].items(), key=lambda x: x[1], reverse=True)
    for uid, total_pts in sorted_scores:
        round_pts = scores_this_round.get(uid, 0)
        if round_pts > 0: desc += f"<@{uid}>: **{total_pts}** pts (+{round_pts})\\n"
        else: desc += f"<@{uid}>: **{total_pts}** pts\\n"
        
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

@bot.tree.command(name="brawl", description="Start a multiplayer RPS Brawl!")
@app_commands.describe(max_rounds="Number of rounds (Default 3, Max 10)")
@app_commands.allowed_contexts(guilds=True)
async def brawl_slash(interaction: discord.Interaction, max_rounds: app_commands.Range[int, 1, 10] = 3):
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
    
    embed = discord.Embed(title="⚔️ RPS Brawl Lobby!", description=f"<@{interaction.user.id}> started a Brawl! Click **Join Brawl!** if you want to play.\\n\\n**Joined Players:** {interaction.user.display_name}", color=discord.Color.purple())
    view = BrawlJoinView(message_id)
    await interaction.response.send_message(embed=embed, view=view)
"""

lines = lines[:insertion_idx] + [new_code] + ["\n"] + lines[insertion_idx:]

with open("main.py", "w", encoding="utf-8") as f:
    f.writelines(lines)

print("Inserted Brawl Logic")
