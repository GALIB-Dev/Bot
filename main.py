import os
import discord
import datetime
from discord import app_commands
from dotenv import load_dotenv
from collections import defaultdict

# ------------------------------------------
# ENV & CONFIG
# ------------------------------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise ValueError("[X] No DISCORD_TOKEN found in .env!")

MOD_CHANNEL_ID = 1468542539052355727
MOD_ROLE_NAME  = os.getenv("MOD_ROLE_NAME", "Moderator")

warnings: dict[int, dict[int, list[dict]]] = defaultdict(lambda: defaultdict(list))
dm_state: dict[int, str] = {}


# ------------------------------------------
# HELPERS
# ------------------------------------------
def has_perm(interaction: discord.Interaction, perm: str) -> bool:
    return getattr(interaction.user.guild_permissions, perm, False)


async def send_audit_dm(moderator: discord.Member, embed: discord.Embed):
    try:
        await moderator.send(embed=embed)
    except discord.Forbidden:
        pass


def audit_embed(title, color, moderator, target, **fields):
    embed = discord.Embed(
        title=f"[LOG] {title}",
        color=color,
        timestamp=datetime.datetime.utcnow(),
    )
    embed.add_field(name="Moderator", value=str(moderator), inline=True)
    embed.add_field(name="Target", value=str(target), inline=True)
    for name, value in fields.items():
        embed.add_field(name=name.replace("_", " ").title(), value=str(value), inline=False)
    return embed


# ------------------------------------------
# DM MENU VIEW (persistent)
# ------------------------------------------
class DMMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # persistent — never expires

    @discord.ui.button(label="Message the Mods", style=discord.ButtonStyle.primary, custom_id="persistent_dm_mods")
    async def dm_mods(self, interaction: discord.Interaction, button: discord.ui.Button):
        dm_state[interaction.user.id] = "dm_mods"
        await interaction.response.send_message(
            "Type your message and I will forward it to the moderators.\nSend `cancel` to cancel.",
        )

    @discord.ui.button(label="Report a User", style=discord.ButtonStyle.danger, custom_id="persistent_report")
    async def report(self, interaction: discord.Interaction, button: discord.ui.Button):
        dm_state[interaction.user.id] = "report"
        await interaction.response.send_message(
            "Describe the user you want to report and what they did.\nSend `cancel` to cancel.",
        )

    @discord.ui.button(label="Request Nickname Change", style=discord.ButtonStyle.secondary, custom_id="persistent_nick")
    async def change_nick(self, interaction: discord.Interaction, button: discord.ui.Button):
        dm_state[interaction.user.id] = "nick"
        await interaction.response.send_message(
            "Type the nickname you would like to request.\nSend `cancel` to cancel.",
        )

    @discord.ui.button(label="Server Rules", style=discord.ButtonStyle.secondary, custom_id="persistent_rules")
    async def rules(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="Server Rules",
            description=(
                "1. Be respectful to all members.\n"
                "2. No spam or self-promotion.\n"
                "3. No hate speech or harassment.\n"
                "4. Follow Discord's Terms of Service.\n"
                "5. Listen to moderators.\n\n"
                "Breaking rules may result in a warn, mute, kick, or ban."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed)


def build_dm_menu_embed() -> discord.Embed:
    return discord.Embed(
        title="Hello! How can I help you?",
        description=(
            "Please choose an option below:\n\n"
            "> **Message the Mods** - Send a message to the mod team\n"
            "> **Report a User** - Report someone breaking the rules\n"
            "> **Request Nickname Change** - Ask for a nickname change\n"
            "> **Server Rules** - View the server rules"
        ),
        color=discord.Color.blurple(),
        timestamp=datetime.datetime.utcnow(),
    )


# ------------------------------------------
# CLIENT
# ------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True


class ModerationBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # Register persistent view so buttons work after restarts
        self.add_view(DMMenuView())
        await self.tree.sync()
        print("[OK] Slash commands synced.")


client = ModerationBot()


# ------------------------------------------
# DM HANDLER
# ------------------------------------------
@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if not isinstance(message.channel, discord.DMChannel):
        return

    user = message.author
    state = dm_state.get(user.id)

    print(f"[DM] {user} ({user.id}): {message.content!r} | state={state}")

    if state:
        if message.content.strip().lower() == "cancel":
            dm_state.pop(user.id, None)
            await message.channel.send("[X] Cancelled. Send me anything to open the menu again.")
            return

        # Find mod channel
        mod_channel = None
        member = None
        for guild in client.guilds:
            m = guild.get_member(user.id)
            if m:
                member = m
                mod_channel = guild.get_channel(MOD_CHANNEL_ID)
                break

        if state == "dm_mods":
            embed = discord.Embed(
                title="[MAIL] Message from a Member",
                description=message.content,
                color=discord.Color.blurple(),
                timestamp=datetime.datetime.utcnow(),
            )
            embed.set_footer(text=f"From: {user} (ID: {user.id})")

        elif state == "report":
            embed = discord.Embed(
                title="[!] New Report",
                description=message.content,
                color=discord.Color.red(),
                timestamp=datetime.datetime.utcnow(),
            )
            embed.set_footer(text=f"Reported by: {user} (ID: {user.id})")

        elif state == "nick":
            embed = discord.Embed(
                title="[NICK] Nickname Request",
                color=discord.Color.yellow(),
                timestamp=datetime.datetime.utcnow(),
            )
            embed.add_field(name="Requested Nickname", value=message.content, inline=False)
            embed.add_field(name="Current Nickname", value=member.display_name if member else str(user), inline=False)
            embed.set_footer(text=f"From: {user} (ID: {user.id})")

        if mod_channel:
            await mod_channel.send(embed=embed)
            await message.channel.send("[OK] Done! Your message has been forwarded to the moderators.")
        else:
            print(f"[X] Could not find mod channel {MOD_CHANNEL_ID}")
            await message.channel.send("[X] Could not reach the mod channel. Please contact a moderator directly.")

        dm_state.pop(user.id, None)
        return

    # No active state — show the menu
    await message.channel.send(embed=build_dm_menu_embed(), view=DMMenuView())


# ------------------------------------------
# /hello
# ------------------------------------------
@client.tree.command(name="hello", description="Say hello to the bot")
async def hello(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send(f"Hey {interaction.user.mention}! ~", ephemeral=True)


# ------------------------------------------
# /ban
# ------------------------------------------
@client.tree.command(name="ban", description="Ban a member from the server")
@app_commands.describe(user="Member to ban", reason="Reason for the ban")
async def ban(
    interaction: discord.Interaction,
    user: discord.Member,
    reason: str = "No reason provided",
):
    await interaction.response.defer(ephemeral=True)

    if not has_perm(interaction, "ban_members"):
        return await interaction.followup.send("[X] You need the **Ban Members** permission.", ephemeral=True)
    if user == interaction.user:
        return await interaction.followup.send("[X] You cannot ban yourself.", ephemeral=True)
    if user.top_role >= interaction.user.top_role:
        return await interaction.followup.send("[X] You can't ban someone with an equal or higher role.", ephemeral=True)

    try:
        await user.ban(reason=reason)
        await interaction.followup.send(f"[BAN] **{user}** has been banned. | Reason: {reason}")
        await send_audit_dm(interaction.user, audit_embed("Ban", discord.Color.red(), interaction.user, user, reason=reason))
    except discord.Forbidden:
        await interaction.followup.send("[X] I don't have permission to ban that user.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"[X] Unexpected error: {e}", ephemeral=True)


# ------------------------------------------
# /unban
# ------------------------------------------
@client.tree.command(name="unban", description="Unban a user by their ID")
@app_commands.describe(user_id="The user's Discord ID", reason="Reason for unban")
async def unban(
    interaction: discord.Interaction,
    user_id: str,
    reason: str = "No reason provided",
):
    await interaction.response.defer(ephemeral=True)

    if not has_perm(interaction, "ban_members"):
        return await interaction.followup.send("[X] You need the **Ban Members** permission.", ephemeral=True)

    try:
        uid = int(user_id)
    except ValueError:
        return await interaction.followup.send("[X] Invalid user ID -- must be a number.", ephemeral=True)

    try:
        user = await client.fetch_user(uid)
        await interaction.guild.unban(user, reason=reason)
        await interaction.followup.send(f"[UNMUTE] **{user}** has been unbanned. | Reason: {reason}")
        await send_audit_dm(interaction.user, audit_embed("Unban", discord.Color.green(), interaction.user, user, reason=reason))
    except discord.NotFound:
        await interaction.followup.send("[X] That user isn't banned or doesn't exist.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"[X] Unexpected error: {e}", ephemeral=True)


# ------------------------------------------
# /kick
# ------------------------------------------
@client.tree.command(name="kick", description="Kick a member from the server")
@app_commands.describe(user="Member to kick", reason="Reason for the kick")
async def kick(
    interaction: discord.Interaction,
    user: discord.Member,
    reason: str = "No reason provided",
):
    await interaction.response.defer(ephemeral=True)

    if not has_perm(interaction, "kick_members"):
        return await interaction.followup.send("[X] You need the **Kick Members** permission.", ephemeral=True)
    if user == interaction.user:
        return await interaction.followup.send("[X] You cannot kick yourself.", ephemeral=True)
    if user.top_role >= interaction.user.top_role:
        return await interaction.followup.send("[X] You can't kick someone with an equal or higher role.", ephemeral=True)

    try:
        await user.kick(reason=reason)
        await interaction.followup.send(f"[KICK] **{user}** has been kicked. | Reason: {reason}")
        await send_audit_dm(interaction.user, audit_embed("Kick", discord.Color.orange(), interaction.user, user, reason=reason))
    except discord.Forbidden:
        await interaction.followup.send("[X] I don't have permission to kick that user.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"[X] Unexpected error: {e}", ephemeral=True)


# ------------------------------------------
# /mute (timeout)
# ------------------------------------------
@client.tree.command(name="mute", description="Timeout a member")
@app_commands.describe(user="Member to mute", minutes="Duration in minutes (max 40320 = 28 days)")
async def mute(
    interaction: discord.Interaction,
    user: discord.Member,
    minutes: app_commands.Range[int, 1, 40320],
):
    await interaction.response.defer(ephemeral=True)

    if not has_perm(interaction, "moderate_members"):
        return await interaction.followup.send("[X] You need the **Moderate Members** permission.", ephemeral=True)
    if user.top_role >= interaction.user.top_role:
        return await interaction.followup.send("[X] You can't mute someone with an equal or higher role.", ephemeral=True)

    try:
        until = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
        await user.timeout(until)
        await interaction.followup.send(f"[MUTE] **{user}** has been muted for **{minutes} minute(s)**.")
        await send_audit_dm(interaction.user, audit_embed("Mute", discord.Color.yellow(), interaction.user, user, duration=f"{minutes} minutes"))
    except discord.Forbidden:
        await interaction.followup.send("[X] I don't have permission to mute that user.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"[X] Unexpected error: {e}", ephemeral=True)


# ------------------------------------------
# /unmute
# ------------------------------------------
@client.tree.command(name="unmute", description="Remove a member's timeout")
@app_commands.describe(user="Member to unmute")
async def unmute(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)

    if not has_perm(interaction, "moderate_members"):
        return await interaction.followup.send("[X] You need the **Moderate Members** permission.", ephemeral=True)

    try:
        await user.timeout(None)
        await interaction.followup.send(f"[UNMUTE] **{user}** has been unmuted.")
        await send_audit_dm(interaction.user, audit_embed("Unmute", discord.Color.green(), interaction.user, user))
    except discord.Forbidden:
        await interaction.followup.send("[X] I don't have permission to unmute that user.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"[X] Unexpected error: {e}", ephemeral=True)


# ------------------------------------------
# /warn
# ------------------------------------------
@client.tree.command(name="warn", description="Warn a member")
@app_commands.describe(user="Member to warn", reason="Reason for the warning")
async def warn(
    interaction: discord.Interaction,
    user: discord.Member,
    reason: str = "No reason provided",
):
    await interaction.response.defer(ephemeral=True)

    if not has_perm(interaction, "moderate_members"):
        return await interaction.followup.send("[X] You need the **Moderate Members** permission.", ephemeral=True)
    if user.bot:
        return await interaction.followup.send("[X] You cannot warn a bot.", ephemeral=True)

    entry = {
        "reason": reason,
        "by": str(interaction.user),
        "at": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    }
    warnings[interaction.guild_id][user.id].append(entry)
    total = len(warnings[interaction.guild_id][user.id])

    await interaction.followup.send(
        f"[!] **{user}** has been warned. (Total warnings: **{total}**) | Reason: {reason}"
    )
    await send_audit_dm(
        interaction.user,
        audit_embed("Warn", discord.Color.gold(), interaction.user, user, reason=reason, total_warnings=total),
    )

    try:
        await user.send(
            f"[!] You have been warned in **{interaction.guild.name}**.\n"
            f"Reason: {reason}\nTotal warnings: {total}"
        )
    except discord.Forbidden:
        pass


# ------------------------------------------
# /warnings
# ------------------------------------------
@client.tree.command(name="warnings", description="View warnings for a member")
@app_commands.describe(user="Member to check")
async def view_warnings(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)

    if not has_perm(interaction, "moderate_members"):
        return await interaction.followup.send("[X] You need the **Moderate Members** permission.", ephemeral=True)

    user_warns = warnings[interaction.guild_id][user.id]
    if not user_warns:
        return await interaction.followup.send(f"[OK] **{user}** has no warnings.", ephemeral=True)

    embed = discord.Embed(
        title=f"[!] Warnings for {user}",
        color=discord.Color.gold(),
        timestamp=datetime.datetime.utcnow(),
    )
    for i, w in enumerate(user_warns, 1):
        embed.add_field(
            name=f"Warning #{i}",
            value=f"**Reason:** {w['reason']}\n**By:** {w['by']}\n**At:** {w['at']}",
            inline=False,
        )
    await interaction.followup.send(embed=embed, ephemeral=True)


# ------------------------------------------
# /clearwarnings
# ------------------------------------------
@client.tree.command(name="clearwarnings", description="Clear all warnings for a member")
@app_commands.describe(user="Member to clear warnings for")
async def clear_warnings(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)

    if not has_perm(interaction, "administrator"):
        return await interaction.followup.send("[X] You need the **Administrator** permission.", ephemeral=True)

    warnings[interaction.guild_id][user.id].clear()
    await interaction.followup.send(f"[DEL] All warnings for **{user}** have been cleared.", ephemeral=True)


# ------------------------------------------
# /purge
# ------------------------------------------
@client.tree.command(name="purge", description="Delete a number of recent messages")
@app_commands.describe(amount="Number of messages to delete (1-100)")
async def purge(
    interaction: discord.Interaction,
    amount: app_commands.Range[int, 1, 100],
):
    await interaction.response.defer(ephemeral=True)

    if not has_perm(interaction, "manage_messages"):
        return await interaction.followup.send("[X] You need the **Manage Messages** permission.", ephemeral=True)

    try:
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(f"[DEL] Deleted **{len(deleted)}** message(s).", ephemeral=True)
        await send_audit_dm(
            interaction.user,
            audit_embed("Purge", discord.Color.blurple(), interaction.user, f"#{interaction.channel.name}", messages_deleted=len(deleted)),
        )
    except discord.Forbidden:
        await interaction.followup.send("[X] I don't have permission to delete messages here.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"[X] Unexpected error: {e}", ephemeral=True)


# ------------------------------------------
# READY EVENT
# ------------------------------------------
@client.event
async def on_ready():
    print(f"> Logged in as {client.user} (ID: {client.user.id})")
    print(f"> Watching for DMs...")
    print("-" * 40)


client.run(TOKEN)