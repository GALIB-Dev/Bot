# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportArgumentType=false, reportReturnType=false, reportCallIssue=false, reportUnusedExpression=false
import os
import sys
import io
import json
import asyncio
import discord
import datetime
from typing import Any
from discord import app_commands
from dotenv import load_dotenv
from collections import defaultdict

# Fix Windows console encoding for emoji
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ------------------------------------------
# ENV & CONFIG
# ------------------------------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise ValueError("[X] No DISCORD_TOKEN found in .env!")

def env_int(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        print(f"[WARN] Ignoring invalid {name}={value!r}; expected a Discord snowflake ID.")
        return None


MOD_CHANNEL_ID        = env_int("MOD_CHANNEL_ID")
LOG_CHANNEL_ID        = env_int("LOG_CHANNEL_ID")
TICKET_CHANNEL_ID     = env_int("TICKET_CHANNEL_ID")
TRANSCRIPT_CHANNEL_ID = env_int("TRANSCRIPT_CHANNEL_ID")
GUILD_ID              = env_int("GUILD_ID")
MOD_ROLE_NAME         = os.getenv("MOD_ROLE_NAME", "Moderator")

DATA_FILE = "bot_data.json"

TICKET_TYPES = {
    "support":  {"label": "Support",     "prefix": "support",  "emoji": "?"},
    "bug":      {"label": "Bug Report",  "prefix": "bug",      "emoji": "\U0001f41b"},
    "question": {"label": "Question",    "prefix": "question", "emoji": "?"},
    "other":    {"label": "Other",       "prefix": "other",    "emoji": "\U0001f4ac"},
}

warnings: defaultdict[int, defaultdict[int, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
dm_state: dict[int, str] = {}
open_tickets: set[int] = set()
ticket_creators: dict[int, int] = {}


# ------------------------------------------
# PERSISTENCE
# ------------------------------------------
def save_data():
    data = {
        "warnings": {str(g): {str(u): wl for u, wl in ud.items()} for g, ud in warnings.items()},
        "open_tickets": list(open_tickets),
        "ticket_creators": {str(c): u for c, u in ticket_creators.items()},
        "dm_state": dm_state,
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

async def save_data_async():
    await asyncio.to_thread(save_data)

def load_data():
    global warnings, open_tickets, ticket_creators, dm_state
    if not os.path.exists(DATA_FILE):
        return
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for g, ud in data.get("warnings", {}).items():
            for u, wl in ud.items():
                warnings[int(g)][int(u)] = wl
        open_tickets.clear()
        open_tickets.update(set(data.get("open_tickets", [])))
        ticket_creators.clear()
        ticket_creators.update({int(c): u for c, u in data.get("ticket_creators", {}).items()})
        dm_state.clear()
        dm_state.update({int(u): s for u, s in data.get("dm_state", {}).items()})
        print(f"[OK] Loaded persisted data from {DATA_FILE}")
    except Exception as e:
        print(f"[WARN] Failed to load {DATA_FILE}: {e}")


# ------------------------------------------
# COLORS
# ------------------------------------------
class Colors:
    BAN     = discord.Color.from_str("#FF4444")
    KICK    = discord.Color.from_str("#FF8800")
    MUTE    = discord.Color.from_str("#FFCC00")
    WARN    = discord.Color.from_str("#FFD700")
    UNBAN   = discord.Color.from_str("#44FF88")
    UNMUTE  = discord.Color.from_str("#44FF88")
    INFO    = discord.Color.from_str("#5865F2")
    SUCCESS = discord.Color.from_str("#57F287")
    ERROR   = discord.Color.from_str("#ED4245")
    LOCK    = discord.Color.from_str("#FF6B6B")
    UNLOCK  = discord.Color.from_str("#6BFF6B")
    NICK    = discord.Color.from_str("#A855F7")
    PURGE   = discord.Color.from_str("#5865F2")
    REPORT  = discord.Color.from_str("#FF4444")
    TICKET  = discord.Color.from_str("#5865F2")


# ------------------------------------------
# HELPERS
# ------------------------------------------
def has_perm(interaction: discord.Interaction, perm: str) -> bool:
    if not isinstance(interaction.user, discord.Member):
        return False
    return getattr(interaction.user.guild_permissions, perm, False)

async def send_audit_dm(moderator: discord.abc.User, embed: discord.Embed):
    try:
        await moderator.send(embed=embed)
    except discord.Forbidden:
        pass

def audit_embed(title, color, moderator, target, thumbnail_url=None, **fields):
    embed = discord.Embed(title=title, color=color, timestamp=datetime.datetime.utcnow())
    embed.add_field(name="Moderator", value=str(moderator), inline=True)
    embed.add_field(name="Target", value=str(target), inline=True)
    for name, value in fields.items():
        embed.add_field(name=name.replace("_", " ").title(), value=str(value), inline=False)
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)
    embed.set_footer(text="Moderation Log")
    return embed

def error_embed(message: str) -> discord.Embed:
    return discord.Embed(title="Error", description=message, color=Colors.ERROR, timestamp=datetime.datetime.utcnow())

def success_embed(message: str, color=None) -> discord.Embed:
    return discord.Embed(description=message, color=color or Colors.SUCCESS, timestamp=datetime.datetime.utcnow())

def log_embed(title, color, description=""):
    return discord.Embed(title=title, description=description, color=color, timestamp=datetime.datetime.utcnow())

async def require_guild(interaction: discord.Interaction) -> discord.Guild | None:
    if interaction.guild is None:
        await interaction.followup.send(
            embed=error_embed("This command can only be used inside a server."), ephemeral=True,
        )
        return None
    return interaction.guild

async def require_text_channel(interaction: discord.Interaction) -> discord.TextChannel | None:
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.followup.send(
            embed=error_embed("This command can only be used in a text channel."), ephemeral=True,
        )
        return None
    return interaction.channel

def configured_channel_embed(guild: discord.Guild) -> discord.Embed:
    def channel_value(channel_id: int | None) -> str:
        if channel_id is None:
            return "Not configured"
        channel = guild.get_channel(channel_id)
        return channel.mention if channel else f"Missing channel `{channel_id}`"

    embed = discord.Embed(
        title="Bot Configuration",
        description="Current channel wiring for moderation, logs, tickets, and transcripts.",
        color=Colors.INFO,
        timestamp=datetime.datetime.utcnow(),
    )
    embed.add_field(name="Mod Mail", value=channel_value(MOD_CHANNEL_ID), inline=True)
    embed.add_field(name="Logs", value=channel_value(LOG_CHANNEL_ID), inline=True)
    embed.add_field(name="Tickets", value=channel_value(TICKET_CHANNEL_ID), inline=True)
    embed.add_field(name="Transcripts", value=channel_value(TRANSCRIPT_CHANNEL_ID), inline=True)
    embed.add_field(name="Moderator Role", value=MOD_ROLE_NAME, inline=True)
    return embed


# ------------------------------------------
# TICKET TRANSCRIPTS
# ------------------------------------------
async def generate_transcript(channel: discord.TextChannel) -> str:
    lines = []
    lines.append(f"# Transcript for #{channel.name}")
    lines.append(f"Channel ID: {channel.id}")
    lines.append(f"Generated: {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("=" * 60)
    lines.append("")
    async for msg in channel.history(limit=1000, oldest_first=True):
        ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        name = msg.author.display_name
        uid = msg.author.id
        content = msg.content or "*[No text content]*"
        lines.append(f"[{ts}] {name} (ID: {uid}):")
        lines.append(f"  {content}")
        if msg.attachments:
            for att in msg.attachments:
                lines.append(f"  [Attachment: {att.url}]")
        lines.append("")
    return "\n".join(lines)

async def send_transcript(guild: discord.Guild, channel: discord.TextChannel, closed_by: discord.abc.User):
    if TRANSCRIPT_CHANNEL_ID is None:
        print("[TRANSCRIPT] TRANSCRIPT_CHANNEL_ID is not configured")
        return
    transcript_ch = guild.get_channel(TRANSCRIPT_CHANNEL_ID)
    if transcript_ch is None:
        print(f"[TRANSCRIPT] Channel {TRANSCRIPT_CHANNEL_ID} not found")
        return
    text = await generate_transcript(channel)
    file = io.BytesIO(text.encode("utf-8"))
    dfile = discord.File(file, filename=f"transcript-{channel.name}.txt")
    embed = discord.Embed(
        title="Ticket Transcript",
        description=f"**Channel:** #{channel.name}\n**Closed by:** {closed_by.mention}\n**Date:** {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        color=Colors.TICKET,
        timestamp=datetime.datetime.utcnow(),
    )
    await transcript_ch.send(embed=embed, file=dfile)


# ------------------------------------------
# IN-CHANNEL MENTION MENU VIEW
# ------------------------------------------
class ChannelMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(label="Contact Moderators", style=discord.ButtonStyle.primary, custom_id="ch_dm_mods")
    async def ch_dm_mods(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=discord.Embed(
                description="Send me a direct message and I will forward it to the moderation team privately.",
                color=Colors.INFO,
            ), ephemeral=True,
        )

    @discord.ui.button(label="Report a User", style=discord.ButtonStyle.danger, custom_id="ch_report")
    async def ch_report(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=discord.Embed(
                description="Report issues privately by sending me a direct message. All reports remain confidential.",
                color=Colors.REPORT,
            ), ephemeral=True,
        )

    @discord.ui.button(label="Server Rules", style=discord.ButtonStyle.secondary, custom_id="ch_rules")
    async def ch_rules(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="Server Rules",
            description=(
                "**1.** Be respectful to all members\n"
                "**2.** No spam or self-promotion\n"
                "**3.** No hate speech or harassment\n"
                "**4.** Follow Discord's Terms of Service\n"
                "**5.** Follow moderator instructions\n\n"
                "Violations may result in warnings, mutes, kicks, or bans."
            ),
            color=Colors.INFO,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Bot Commands", style=discord.ButtonStyle.secondary, custom_id="ch_commands")
    async def ch_commands(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="Available Commands",
            color=Colors.INFO,
            description=(
                "`/ban` `/unban` `/kick` `/mute` `/unmute`\n"
                "`/warn` `/warnings` `/clearwarnings`\n"
                "`/purge` `/slowmode` `/lock` `/unlock`\n"
                "`/userinfo` `/close`\n\n"
                "Send me a direct message to contact moderators, report a user, or request a nickname change."
            ),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


def build_channel_menu_embed() -> discord.Embed:
    return discord.Embed(
        title="How can I help you?",
        description=(
            "Select an option below to get started.\n\n"
            "> **Contact Moderators** - message the moderation team privately\n"
            "> **Report a User** - report rule violations\n"
            "> **Server Rules** - view community guidelines\n"
            "> **Bot Commands** - see all available commands"
        ),
        color=Colors.INFO,
        timestamp=datetime.datetime.utcnow(),
    )


# ------------------------------------------
# DM MENU VIEW (persistent)
# ------------------------------------------
class DMMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Contact Moderators", style=discord.ButtonStyle.primary, custom_id="persistent_dm_mods")
    async def dm_mods(self, interaction: discord.Interaction, button: discord.ui.Button):
        dm_state[interaction.user.id] = "dm_mods"
        await save_data_async()
        await interaction.response.send_message(
            embed=discord.Embed(
                description="Type your message below. I will forward it to the moderation team.\nSend `cancel` to exit.",
                color=Colors.INFO,
            )
        )

    @discord.ui.button(label="Report a User", style=discord.ButtonStyle.danger, custom_id="persistent_report")
    async def report(self, interaction: discord.Interaction, button: discord.ui.Button):
        dm_state[interaction.user.id] = "report"
        await save_data_async()
        await interaction.response.send_message(
            embed=discord.Embed(
                description="Describe the user and the issue. I will submit this report to the moderation team.\nSend `cancel` to exit.",
                color=Colors.REPORT,
            )
        )

    @discord.ui.button(label="Change My Nickname", style=discord.ButtonStyle.secondary, custom_id="persistent_nick")
    async def change_nick(self, interaction: discord.Interaction, button: discord.ui.Button):
        dm_state[interaction.user.id] = "nick"
        await save_data_async()
        await interaction.response.send_message(
            embed=discord.Embed(
                description="Enter your desired nickname. I will update it for you.\nSend `cancel` to exit.",
                color=Colors.NICK,
            )
        )

    @discord.ui.button(label="Server Rules", style=discord.ButtonStyle.secondary, custom_id="persistent_rules")
    async def rules(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="Server Rules",
            description=(
                "**1.** Be respectful to all members.\n"
                "**2.** No spam or self-promotion.\n"
                "**3.** No hate speech or harassment.\n"
                "**4.** Follow Discord's Terms of Service.\n"
                "**5.** Follow moderator instructions.\n\n"
                "Violations may result in warnings, mutes, kicks, or bans."
            ),
            color=Colors.INFO,
        )
        await interaction.response.send_message(embed=embed)


def build_dm_menu_embed() -> discord.Embed:
    return discord.Embed(
        title="Welcome! What do you need?",
        description=(
            "Select an option below.\n\n"
            "> **Contact Moderators** - message the moderation team\n"
            "> **Report a User** - report rule violations\n"
            "> **Change My Nickname** - update your nickname\n"
            "> **Server Rules** - view community guidelines"
        ),
        color=Colors.INFO,
        timestamp=datetime.datetime.utcnow(),
    )


def build_ticket_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Support Tickets",
        description=(
            "Need help? Click the button below to create a private ticket.\n\n"
            "A moderator will assist you as soon as possible."
        ),
        color=Colors.TICKET,
        timestamp=datetime.datetime.utcnow(),
    )
    embed.set_footer(text="Click to open a support ticket")
    return embed


# ------------------------------------------
# TICKET CREATE VIEW (pinned in ticket channel)
# ------------------------------------------
class TicketCreateView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Create Ticket",
        style=discord.ButtonStyle.primary,
        emoji="\U0001F3AB",
        custom_id="persistent_ticket_create",
    )
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in open_tickets:
            return await interaction.response.send_message(
                embed=error_embed("You already have an open ticket. Close it before creating a new one."),
                ephemeral=True,
            )
        embed = discord.Embed(
            title="Select Ticket Type",
            description="Choose the type of ticket you want to create.",
            color=Colors.TICKET,
        )
        await interaction.response.send_message(
            embed=embed, view=TicketTypeSelectView(interaction.user), ephemeral=True,
        )


# ------------------------------------------
# TICKET TYPE SELECT VIEW
# ------------------------------------------
class TicketTypeSelectView(discord.ui.View):
    def __init__(self, user: discord.abc.User):
        super().__init__(timeout=60)
        self.user = user

    @discord.ui.select(
        placeholder="Select a ticket type...",
        custom_id="ticket_type_select",
        options=[
            discord.SelectOption(label="Support", value="support", description="General support and help", emoji="?"),
            discord.SelectOption(label="Bug Report", value="bug", description="Report a bug or issue", emoji="\U0001f41b"),
            discord.SelectOption(label="Question", value="question", description="Ask a question", emoji="?"),
            discord.SelectOption(label="Other", value="other", description="Other inquiries", emoji="\U0001f4ac"),
        ],
    )
    async def select_type(self, interaction: discord.Interaction, select: discord.ui.Select):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message(
                embed=error_embed("Only the user who clicked the button can select a ticket type."),
                ephemeral=True,
            )
        await interaction.response.defer(ephemeral=True)
        await self._create_ticket(interaction, select.values[0])

    async def _create_ticket(self, interaction: discord.Interaction, ticket_type: str):
        user = interaction.user
        guild = interaction.guild
        if guild is None or guild.me is None:
            return await interaction.followup.send(
                embed=error_embed("This can only be used inside a server where the bot is available."),
                ephemeral=True,
            )
        tt = TICKET_TYPES[ticket_type]

        category = discord.utils.get(guild.categories, name="Tickets")
        if category is None:
            try:
                category = await guild.create_category("Tickets")
            except discord.Forbidden:
                return await interaction.followup.send(
                    embed=error_embed("I can't create a ticket category. Contact an admin."), ephemeral=True,
                )
        if category.position != 0:
            try:
                await category.move(beginning=True, reason="Place Tickets category at top")
            except (discord.Forbidden, ValueError, discord.HTTPException):
                pass

        mod_role = discord.utils.get(guild.roles, name=MOD_ROLE_NAME)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False),
            user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True,
                read_message_history=True, attach_files=True, embed_links=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True,
                manage_channels=True, read_message_history=True,
            ),
        }
        if mod_role:
            overwrites[mod_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True,
                read_message_history=True, manage_messages=True,
            )

        channel_name = f"{tt['prefix']}-{user.id}"
        try:
            ticket_channel = await guild.create_text_channel(
                name=channel_name, category=category,
                overwrites=overwrites, reason=f"Ticket created by {user}",
            )
            await ticket_channel.move(beginning=True, sync_permissions=True, reason="New ticket at top")
        except discord.Forbidden:
            return await interaction.followup.send(
                embed=error_embed("I can't create ticket channels. Contact an admin."), ephemeral=True,
            )
        except discord.HTTPException as e:
            return await interaction.followup.send(
                embed=error_embed(f"Failed to create ticket: {e}"), ephemeral=True,
            )

        open_tickets.add(user.id)
        ticket_creators[ticket_channel.id] = user.id
        await save_data_async()

        close_embed = discord.Embed(
            title=f"{tt['label']} Ticket",
            description=(
                f"{user.mention}, your ticket has been created.\n\n"
                "A moderator will assist you shortly.\n"
                "Click the button below to close this ticket."
            ),
            color=Colors.TICKET,
            timestamp=datetime.datetime.utcnow(),
        )
        close_embed.set_footer(text=f"Ticket for {user} (ID: {user.id})")
        await ticket_channel.send(
            content=user.mention, embed=close_embed, view=TicketCloseView(),
        )

        await interaction.followup.send(
            embed=success_embed(f"Ticket created: {ticket_channel.mention}"), ephemeral=True,
        )

        log_ch = await get_log_channel(guild)
        if log_ch:
            le = log_embed("Ticket Created", Colors.TICKET,
                           f"{user.mention} created a {tt['label'].lower()} ticket: {ticket_channel.mention}")
            le.set_thumbnail(url=user.display_avatar.url)
            le.add_field(name="User", value=f"{user} (ID: {user.id})", inline=True)
            le.add_field(name="Type", value=tt["label"], inline=True)
            await log_ch.send(embed=le)


# ------------------------------------------
# TICKET CLOSE VIEW (inside ticket channels)
# ------------------------------------------
class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.danger,
        emoji="\U0001F512",
        custom_id="persistent_ticket_close",
    )
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                embed=error_embed("Tickets can only be closed inside ticket text channels."), ephemeral=True,
            )

        creator_id = ticket_creators.get(channel.id)
        if creator_id is None:
            return await interaction.response.send_message(
                embed=error_embed("Could not find ticket data. Contact an admin."), ephemeral=True,
            )

        mod_role = guild and discord.utils.get(guild.roles, name=MOD_ROLE_NAME)
        is_mod = mod_role is not None and isinstance(user, discord.Member) and mod_role in user.roles
        is_creator = user.id == creator_id

        if not (is_mod or is_creator):
            return await interaction.response.send_message(
                embed=error_embed("Only a moderator or the ticket creator can close this ticket."), ephemeral=True,
            )

        await interaction.response.send_message(
            embed=success_embed("Closing ticket and generating transcript..."), ephemeral=True,
        )

        await send_transcript(guild, channel, user)

        log_ch = await get_log_channel(guild)
        if log_ch:
            le = log_embed("Ticket Closed", Colors.LOCK, f"Ticket `{channel.name}` was closed by {user.mention}.")
            le.add_field(name="Closed By", value=f"{user} (ID: {user.id})", inline=True)
            le.add_field(name="Creator ID", value=str(creator_id), inline=True)
            await log_ch.send(embed=le)

        ticket_creators.pop(channel.id, None)
        open_tickets.discard(creator_id)
        await save_data_async()

        try:
            await channel.delete(reason=f"Ticket closed by {user}")
        except discord.Forbidden:
            pass


# ------------------------------------------
# CLIENT
# ------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.emojis_and_stickers = True
intents.invites = True
intents.voice_states = True
intents.moderation = True


class ModerationBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.add_view(DMMenuView())
        self.add_view(TicketCreateView())
        self.add_view(TicketCloseView())
        if GUILD_ID:
            guild_obj = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild_obj)
            await self.tree.sync(guild=guild_obj)
            print(f"[OK] Slash commands synced to guild {GUILD_ID}.")
        else:
            await self.tree.sync()
            print("[OK] Slash commands synced globally.")


client = ModerationBot()


# ------------------------------------------
# COOLDOWN ERROR HANDLER
# ------------------------------------------
@client.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        embed = error_embed(f"This command is on cooldown. Try again in {error.retry_after:.1f}s.")
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        raise error


# ------------------------------------------
# DM HANDLER
# ------------------------------------------
@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if not isinstance(message.channel, discord.DMChannel):
        if (
            client.user
            and client.user.mentioned_in(message)
            and not message.mention_everyone
            and isinstance(message.channel, discord.TextChannel)
        ):
            await message.channel.send(embed=build_channel_menu_embed(), view=ChannelMenuView())
        return

    user = message.author
    state = dm_state.get(user.id)

    if state:
        if message.content.strip().lower() == "cancel":
            dm_state.pop(user.id, None)
            await save_data_async()
            await message.channel.send(embed=discord.Embed(
                description="Cancelled. Send a new message to open the menu again.", color=Colors.ERROR,
            ))
            return

        mod_channel = None
        member = None
        for guild in client.guilds:
            m = guild.get_member(user.id)
            if m:
                member = m
                if MOD_CHANNEL_ID is not None:
                    mod_channel = guild.get_channel(MOD_CHANNEL_ID)
                break

        embed = None

        if state == "dm_mods":
            embed = discord.Embed(
                title="Message from a Member", description=message.content,
                color=Colors.INFO, timestamp=datetime.datetime.utcnow(),
            )
            embed.set_footer(text=f"From: {user} (ID: {user.id})")
            if user.display_avatar:
                embed.set_thumbnail(url=user.display_avatar.url)

        elif state == "report":
            embed = discord.Embed(
                title="New Report", description=message.content,
                color=Colors.REPORT, timestamp=datetime.datetime.utcnow(),
            )
            embed.set_footer(text=f"Reported by: {user} (ID: {user.id})")
            if user.display_avatar:
                embed.set_thumbnail(url=user.display_avatar.url)

        elif state == "nick":
            new_nick = message.content.strip()
            if member:
                try:
                    old_nick = member.display_name
                    await member.edit(nick=new_nick)
                    await message.channel.send(embed=discord.Embed(
                        title="Nickname Changed",
                        description=f"Your nickname has been updated.\n\n**Before:** {old_nick}\n**After:** {new_nick}",
                        color=Colors.NICK, timestamp=datetime.datetime.utcnow(),
                    ))
                    if mod_channel:
                        le = discord.Embed(title="Nickname Changed", color=Colors.NICK, timestamp=datetime.datetime.utcnow())
                        le.add_field(name="Member", value=str(user), inline=True)
                        le.add_field(name="Before", value=old_nick, inline=True)
                        le.add_field(name="After", value=new_nick, inline=True)
                        if user.display_avatar:
                            le.set_thumbnail(url=user.display_avatar.url)
                        await mod_channel.send(embed=le)
                    dm_state.pop(user.id, None)
                    await save_data_async()
                    return
                except discord.Forbidden:
                    await message.channel.send(embed=error_embed("I don't have permission to change your nickname. Please ask a moderator directly."))
                    dm_state.pop(user.id, None)
                    await save_data_async()
                    return
                except Exception as e:
                    await message.channel.send(embed=error_embed(f"Unexpected error: {e}"))
                    dm_state.pop(user.id, None)
                    await save_data_async()
                    return
            else:
                await message.channel.send(embed=error_embed("I could not find your account in the server. Are you still a member?"))
                dm_state.pop(user.id, None)
                await save_data_async()
                return

        if embed and mod_channel:
            await mod_channel.send(embed=embed)
            await message.channel.send(embed=success_embed("Your message has been forwarded to the moderation team."))
        elif embed:
            await message.channel.send(embed=error_embed("Could not reach the mod channel. Please contact a moderator directly."))

        dm_state.pop(user.id, None)
        await save_data_async()
        return

    await message.channel.send(embed=build_dm_menu_embed(), view=DMMenuView())


# ------------------------------------------
# /bothelp
# ------------------------------------------
@client.tree.command(name="bothelp", description="Show the bot's moderation and support commands")
async def bothelp(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Bot Commands",
        description="Moderation, support tickets, private mod contact, and server logging are enabled.",
        color=Colors.INFO,
        timestamp=datetime.datetime.utcnow(),
    )
    embed.add_field(
        name="Moderation",
        value="`/ban` `/unban` `/kick` `/mute` `/unmute` `/warn` `/warnings` `/clearwarnings`",
        inline=False,
    )
    embed.add_field(
        name="Channels",
        value="`/purge` `/slowmode` `/lock` `/unlock`",
        inline=False,
    )
    embed.add_field(
        name="Support",
        value="`/panel` posts the ticket button. `/close` closes a ticket. Mention the bot or DM it for member menus.",
        inline=False,
    )
    embed.add_field(
        name="Admin",
        value="`/setup` creates the recommended channels. `/config` shows the active channel wiring.",
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ------------------------------------------
# /config
# ------------------------------------------
@client.tree.command(name="config", description="Show current bot channel configuration")
async def config(interaction: discord.Interaction):
    if not has_perm(interaction, "administrator"):
        return await interaction.response.send_message(
            embed=error_embed("You need the **Administrator** permission."), ephemeral=True,
        )
    await interaction.response.send_message(embed=configured_channel_embed(interaction.guild), ephemeral=True)


# ------------------------------------------
# /setup
# ------------------------------------------
@client.tree.command(name="setup", description="Create recommended bot channels and post the ticket panel")
@app_commands.checks.cooldown(1, 30, key=lambda i: (i.guild_id, i.user.id))
async def setup(interaction: discord.Interaction):
    global MOD_CHANNEL_ID, LOG_CHANNEL_ID, TICKET_CHANNEL_ID, TRANSCRIPT_CHANNEL_ID

    await interaction.response.defer(ephemeral=True)
    if not has_perm(interaction, "administrator"):
        return await interaction.followup.send(
            embed=error_embed("You need the **Administrator** permission."), ephemeral=True,
        )

    guild = interaction.guild
    created: list[str] = []

    async def ensure_text_channel(name: str, topic: str) -> discord.TextChannel:
        existing = discord.utils.get(guild.text_channels, name=name)
        if existing:
            return existing
        channel = await guild.create_text_channel(name=name, topic=topic, reason=f"Bot setup by {interaction.user}")
        created.append(channel.mention)
        return channel

    try:
        mod_channel = await ensure_text_channel("mod-mail", "Private messages and reports forwarded to moderators.")
        log_channel = await ensure_text_channel("server-logs", "Moderation and server event logs.")
        ticket_channel = await ensure_text_channel("tickets", "Members can create private support tickets here.")
        transcript_channel = await ensure_text_channel("ticket-transcripts", "Closed ticket transcripts.")
        discord.utils.get(guild.categories, name="Tickets") or await guild.create_category("Tickets", reason=f"Bot setup by {interaction.user}")

        MOD_CHANNEL_ID = mod_channel.id
        LOG_CHANNEL_ID = log_channel.id
        TICKET_CHANNEL_ID = ticket_channel.id
        TRANSCRIPT_CHANNEL_ID = transcript_channel.id

        panel_msg = await ticket_channel.send(embed=build_ticket_panel_embed(), view=TicketCreateView())
        try:
            await panel_msg.pin(reason=f"Ticket panel posted by {interaction.user}")
        except discord.Forbidden:
            pass

        embed = success_embed("Setup complete. Add these IDs to `.env` so the bot keeps the same wiring after restart.")
        embed.add_field(name="Created", value=", ".join(created) if created else "Used existing channels", inline=False)
        embed.add_field(
            name=".env values",
            value=(
                f"MOD_CHANNEL_ID={mod_channel.id}\n"
                f"LOG_CHANNEL_ID={log_channel.id}\n"
                f"TICKET_CHANNEL_ID={ticket_channel.id}\n"
                f"TRANSCRIPT_CHANNEL_ID={transcript_channel.id}\n"
                f"GUILD_ID={guild.id}"
            ),
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(embed=error_embed("I need permission to create channels and send messages."), ephemeral=True)
    except discord.HTTPException as e:
        await interaction.followup.send(embed=error_embed(f"Setup failed: {e}"), ephemeral=True)


# ------------------------------------------
# /panel
# ------------------------------------------
@client.tree.command(name="panel", description="Post the ticket creation panel in this channel")
@app_commands.checks.cooldown(1, 10, key=lambda i: (i.guild_id, i.user.id))
async def panel(interaction: discord.Interaction):
    if not has_perm(interaction, "manage_channels"):
        return await interaction.response.send_message(
            embed=error_embed("You need the **Manage Channels** permission."), ephemeral=True,
        )
    await interaction.response.defer(ephemeral=True)
    msg = await interaction.channel.send(embed=build_ticket_panel_embed(), view=TicketCreateView())
    try:
        await msg.pin(reason=f"Ticket panel posted by {interaction.user}")
    except discord.Forbidden:
        pass
    await interaction.followup.send(embed=success_embed("Ticket panel posted."), ephemeral=True)


# ------------------------------------------
# /userinfo
# ------------------------------------------
@client.tree.command(name="userinfo", description="View detailed info about a member")
@app_commands.describe(user="Member to inspect (leave blank for yourself)")
async def userinfo(interaction: discord.Interaction, user: discord.Member = None):
    await interaction.response.defer(ephemeral=True)
    target = user or interaction.user
    roles = [r.mention for r in reversed(target.roles) if r.name != "@everyone"]
    roles_str = " ".join(roles) if roles else "No roles"
    joined_at = discord.utils.format_dt(target.joined_at, "F") if target.joined_at else "Unknown"
    created_at = discord.utils.format_dt(target.created_at, "F")
    warn_count = len(warnings[interaction.guild_id][target.id])

    embed = discord.Embed(
        title=f"{target.display_name}",
        color=target.color if target.color.value else Colors.INFO,
        timestamp=datetime.datetime.utcnow(),
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Username", value=str(target), inline=True)
    embed.add_field(name="User ID", value=str(target.id), inline=True)
    embed.add_field(name="Bot", value="Yes" if target.bot else "No", inline=True)
    embed.add_field(name="Joined", value=joined_at, inline=False)
    embed.add_field(name="Account Created", value=created_at, inline=False)
    embed.add_field(name="Warnings", value=str(warn_count), inline=True)
    embed.add_field(name="Top Role", value=target.top_role.mention, inline=True)
    embed.add_field(name="Roles", value=roles_str, inline=False)
    embed.set_footer(text=f"Requested by {interaction.user}")
    await interaction.followup.send(embed=embed, ephemeral=True)


# ------------------------------------------
# /ban
# ------------------------------------------
@client.tree.command(name="ban", description="Ban a member from the server")
@app_commands.describe(user="Member to ban", reason="Reason for the ban")
@app_commands.checks.cooldown(1, 5, key=lambda i: (i.guild_id, i.user.id))
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided"):
    await interaction.response.defer(ephemeral=True)
    if not has_perm(interaction, "ban_members"):
        return await interaction.followup.send(embed=error_embed("You need the **Ban Members** permission."), ephemeral=True)
    if user == interaction.user:
        return await interaction.followup.send(embed=error_embed("You cannot ban yourself."), ephemeral=True)
    if user.top_role >= interaction.user.top_role:
        return await interaction.followup.send(embed=error_embed("You cannot ban someone with an equal or higher role."), ephemeral=True)

    try:
        ban_dm = discord.Embed(
            title=f"Banned from {interaction.guild.name}",
            color=Colors.BAN, timestamp=datetime.datetime.utcnow(),
        )
        ban_dm.add_field(name="Reason", value=reason, inline=False)
        ban_dm.add_field(name="Moderator", value=str(interaction.user), inline=False)
        await user.send(embed=ban_dm)
    except discord.Forbidden:
        pass

    try:
        await user.ban(reason=reason)
        embed = discord.Embed(title="Member Banned", color=Colors.BAN, timestamp=datetime.datetime.utcnow())
        embed.add_field(name="Banned User", value=str(user), inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_thumbnail(url=user.display_avatar.url)
        await interaction.followup.send(embed=embed)
        await send_audit_dm(interaction.user, audit_embed("Ban", Colors.BAN, interaction.user, user, thumbnail_url=user.display_avatar.url, reason=reason))
    except discord.Forbidden:
        await interaction.followup.send(embed=error_embed("I don't have permission to ban that user."), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(embed=error_embed(f"Unexpected error: {e}"), ephemeral=True)


# ------------------------------------------
# /unban
# ------------------------------------------
@client.tree.command(name="unban", description="Unban a user by their ID")
@app_commands.describe(user_id="The user's Discord ID", reason="Reason for unban")
@app_commands.checks.cooldown(1, 5, key=lambda i: (i.guild_id, i.user.id))
async def unban(interaction: discord.Interaction, user_id: str, reason: str = "No reason provided"):
    await interaction.response.defer(ephemeral=True)
    if not has_perm(interaction, "ban_members"):
        return await interaction.followup.send(embed=error_embed("You need the **Ban Members** permission."), ephemeral=True)
    try:
        uid = int(user_id)
    except ValueError:
        return await interaction.followup.send(embed=error_embed("Invalid user ID - must be a number."), ephemeral=True)
    try:
        user = await client.fetch_user(uid)
        await interaction.guild.unban(user, reason=reason)
        embed = discord.Embed(title="Member Unbanned", color=Colors.UNBAN, timestamp=datetime.datetime.utcnow())
        embed.add_field(name="Unbanned User", value=str(user), inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_thumbnail(url=user.display_avatar.url)
        await interaction.followup.send(embed=embed)
        await send_audit_dm(interaction.user, audit_embed("Unban", Colors.UNBAN, interaction.user, user, thumbnail_url=user.display_avatar.url, reason=reason))
    except discord.NotFound:
        await interaction.followup.send(embed=error_embed("That user is not banned or does not exist."), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(embed=error_embed(f"Unexpected error: {e}"), ephemeral=True)


# ------------------------------------------
# /kick
# ------------------------------------------
@client.tree.command(name="kick", description="Kick a member from the server")
@app_commands.describe(user="Member to kick", reason="Reason for the kick")
@app_commands.checks.cooldown(1, 5, key=lambda i: (i.guild_id, i.user.id))
async def kick(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided"):
    await interaction.response.defer(ephemeral=True)
    if not has_perm(interaction, "kick_members"):
        return await interaction.followup.send(embed=error_embed("You need the **Kick Members** permission."), ephemeral=True)
    if user == interaction.user:
        return await interaction.followup.send(embed=error_embed("You cannot kick yourself."), ephemeral=True)
    if user.top_role >= interaction.user.top_role:
        return await interaction.followup.send(embed=error_embed("You cannot kick someone with an equal or higher role."), ephemeral=True)
    try:
        await user.kick(reason=reason)
        embed = discord.Embed(title="Member Kicked", color=Colors.KICK, timestamp=datetime.datetime.utcnow())
        embed.add_field(name="Kicked User", value=str(user), inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_thumbnail(url=user.display_avatar.url)
        await interaction.followup.send(embed=embed)
        await send_audit_dm(interaction.user, audit_embed("Kick", Colors.KICK, interaction.user, user, thumbnail_url=user.display_avatar.url, reason=reason))
    except discord.Forbidden:
        await interaction.followup.send(embed=error_embed("I don't have permission to kick that user."), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(embed=error_embed(f"Unexpected error: {e}"), ephemeral=True)


# ------------------------------------------
# /mute (timeout)
# ------------------------------------------
@client.tree.command(name="mute", description="Timeout a member")
@app_commands.describe(user="Member to mute", minutes="Duration in minutes (max 40320 = 28 days)")
@app_commands.checks.cooldown(1, 5, key=lambda i: (i.guild_id, i.user.id))
async def mute(interaction: discord.Interaction, user: discord.Member, minutes: app_commands.Range[int, 1, 40320]):
    await interaction.response.defer(ephemeral=True)
    if not has_perm(interaction, "moderate_members"):
        return await interaction.followup.send(embed=error_embed("You need the **Moderate Members** permission."), ephemeral=True)
    if user.top_role >= interaction.user.top_role:
        return await interaction.followup.send(embed=error_embed("You cannot mute someone with an equal or higher role."), ephemeral=True)
    try:
        until = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
        await user.timeout(until)
        embed = discord.Embed(title="Member Muted", color=Colors.MUTE, timestamp=datetime.datetime.utcnow())
        embed.add_field(name="Muted User", value=str(user), inline=True)
        embed.add_field(name="Duration", value=f"{minutes} minute(s)", inline=True)
        embed.set_thumbnail(url=user.display_avatar.url)
        await interaction.followup.send(embed=embed)
        await send_audit_dm(interaction.user, audit_embed("Mute", Colors.MUTE, interaction.user, user, thumbnail_url=user.display_avatar.url, duration=f"{minutes} minutes"))
    except discord.Forbidden:
        await interaction.followup.send(embed=error_embed("I don't have permission to mute that user."), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(embed=error_embed(f"Unexpected error: {e}"), ephemeral=True)


# ------------------------------------------
# /unmute
# ------------------------------------------
@client.tree.command(name="unmute", description="Remove a member's timeout")
@app_commands.describe(user="Member to unmute")
@app_commands.checks.cooldown(1, 5, key=lambda i: (i.guild_id, i.user.id))
async def unmute(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)
    if not has_perm(interaction, "moderate_members"):
        return await interaction.followup.send(embed=error_embed("You need the **Moderate Members** permission."), ephemeral=True)
    try:
        await user.timeout(None)
        embed = discord.Embed(title="Member Unmuted", color=Colors.UNMUTE, timestamp=datetime.datetime.utcnow())
        embed.add_field(name="Unmuted User", value=str(user), inline=True)
        embed.set_thumbnail(url=user.display_avatar.url)
        await interaction.followup.send(embed=embed)
        await send_audit_dm(interaction.user, audit_embed("Unmute", Colors.UNMUTE, interaction.user, user, thumbnail_url=user.display_avatar.url))
    except discord.Forbidden:
        await interaction.followup.send(embed=error_embed("I don't have permission to unmute that user."), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(embed=error_embed(f"Unexpected error: {e}"), ephemeral=True)


# ------------------------------------------
# /warn
# ------------------------------------------
@client.tree.command(name="warn", description="Warn a member")
@app_commands.describe(user="Member to warn", reason="Reason for the warning")
@app_commands.checks.cooldown(1, 5, key=lambda i: (i.guild_id, i.user.id))
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided"):
    await interaction.response.defer(ephemeral=True)
    if not has_perm(interaction, "moderate_members"):
        return await interaction.followup.send(embed=error_embed("You need the **Moderate Members** permission."), ephemeral=True)
    if user.bot:
        return await interaction.followup.send(embed=error_embed("You cannot warn a bot."), ephemeral=True)

    entry = {"reason": reason, "by": str(interaction.user), "at": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}
    warnings[interaction.guild_id][user.id].append(entry)
    total = len(warnings[interaction.guild_id][user.id])
    await save_data_async()

    embed = discord.Embed(title="Member Warned", color=Colors.WARN, timestamp=datetime.datetime.utcnow())
    embed.add_field(name="User", value=str(user), inline=True)
    embed.add_field(name="Total Warnings", value=str(total), inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_thumbnail(url=user.display_avatar.url)
    await interaction.followup.send(embed=embed)
    await send_audit_dm(interaction.user, audit_embed("Warn", Colors.WARN, interaction.user, user, thumbnail_url=user.display_avatar.url, reason=reason, total_warnings=total))

    try:
        dm_embed = discord.Embed(
            title=f"Warning from {interaction.guild.name}",
            color=Colors.WARN, timestamp=datetime.datetime.utcnow(),
        )
        dm_embed.add_field(name="Reason", value=reason, inline=False)
        dm_embed.add_field(name="Total Warnings", value=str(total), inline=True)
        await user.send(embed=dm_embed)
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
        return await interaction.followup.send(embed=error_embed("You need the **Moderate Members** permission."), ephemeral=True)
    user_warns = warnings[interaction.guild_id][user.id]
    if not user_warns:
        return await interaction.followup.send(embed=success_embed(f"**{user}** has no warnings."), ephemeral=True)
    embed = discord.Embed(title=f"Warnings for {user.display_name}", color=Colors.WARN, timestamp=datetime.datetime.utcnow())
    embed.set_thumbnail(url=user.display_avatar.url)
    for i, w in enumerate(user_warns, 1):
        embed.add_field(name=f"Warning #{i}", value=f"**Reason:** {w['reason']}\n**Issued By:** {w['by']}\n**Date:** {w['at']}", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


# ------------------------------------------
# /clearwarnings
# ------------------------------------------
@client.tree.command(name="clearwarnings", description="Clear all warnings for a member")
@app_commands.describe(user="Member to clear warnings for")
async def clear_warnings(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)
    if not has_perm(interaction, "administrator"):
        return await interaction.followup.send(embed=error_embed("You need the **Administrator** permission."), ephemeral=True)
    warnings[interaction.guild_id][user.id].clear()
    await save_data_async()
    await interaction.followup.send(embed=success_embed(f"All warnings for **{user}** have been cleared."), ephemeral=True)


# ------------------------------------------
# /purge
# ------------------------------------------
@client.tree.command(name="purge", description="Delete a number of recent messages")
@app_commands.describe(amount="Number of messages to delete (1-100)")
@app_commands.checks.cooldown(1, 5, key=lambda i: (i.guild_id, i.user.id))
async def purge(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
    await interaction.response.defer(ephemeral=True)
    if not has_perm(interaction, "manage_messages"):
        return await interaction.followup.send(embed=error_embed("You need the **Manage Messages** permission."), ephemeral=True)
    try:
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(embed=success_embed(f"Deleted **{len(deleted)}** message(s).", Colors.PURGE), ephemeral=True)
        await send_audit_dm(interaction.user, audit_embed("Purge", Colors.PURGE, interaction.user, f"#{interaction.channel.name}", messages_deleted=len(deleted)))
    except discord.Forbidden:
        await interaction.followup.send(embed=error_embed("I don't have permission to delete messages here."), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(embed=error_embed(f"Unexpected error: {e}"), ephemeral=True)


# ------------------------------------------
# /slowmode
# ------------------------------------------
@client.tree.command(name="slowmode", description="Set slowmode for the current channel (0 to disable)")
@app_commands.describe(seconds="Slowmode delay in seconds (0 = off, max 21600)")
@app_commands.checks.cooldown(1, 5, key=lambda i: (i.guild_id, i.user.id))
async def slowmode(interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600]):
    await interaction.response.defer(ephemeral=True)
    if not has_perm(interaction, "manage_channels"):
        return await interaction.followup.send(embed=error_embed("You need the **Manage Channels** permission."), ephemeral=True)
    try:
        await interaction.channel.edit(slowmode_delay=seconds)
        if seconds == 0:
            msg = f"Slowmode has been disabled for {interaction.channel.mention}."
        else:
            msg = f"Slowmode set to **{seconds}s** in {interaction.channel.mention}."
        await interaction.followup.send(embed=success_embed(msg), ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(embed=error_embed("I don't have permission to edit this channel."), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(embed=error_embed(f"Unexpected error: {e}"), ephemeral=True)


# ------------------------------------------
# /lock
# ------------------------------------------
@client.tree.command(name="lock", description="Lock the current channel")
@app_commands.describe(reason="Reason for locking the channel")
@app_commands.checks.cooldown(1, 5, key=lambda i: (i.guild_id, i.user.id))
async def lock(interaction: discord.Interaction, reason: str = "No reason provided"):
    await interaction.response.defer(ephemeral=True)
    if not has_perm(interaction, "manage_channels"):
        return await interaction.followup.send(embed=error_embed("You need the **Manage Channels** permission."), ephemeral=True)
    guild = interaction.guild
    channel = interaction.channel
    everyone = guild.default_role
    try:
        await channel.set_permissions(everyone, send_messages=False)
        embed = discord.Embed(
            title="Channel Locked",
            description=f"{channel.mention} has been locked.\n**Reason:** {reason}",
            color=Colors.LOCK, timestamp=datetime.datetime.utcnow(),
        )
        embed.set_footer(text=f"Locked by {interaction.user}")
        await channel.send(embed=embed)
        await interaction.followup.send(embed=success_embed("Channel locked."), ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(embed=error_embed("I don't have permission to lock this channel."), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(embed=error_embed(f"Unexpected error: {e}"), ephemeral=True)


# ------------------------------------------
# /unlock
# ------------------------------------------
@client.tree.command(name="unlock", description="Unlock the current channel")
@app_commands.describe(reason="Reason for unlocking the channel")
@app_commands.checks.cooldown(1, 5, key=lambda i: (i.guild_id, i.user.id))
async def unlock(interaction: discord.Interaction, reason: str = "No reason provided"):
    await interaction.response.defer(ephemeral=True)
    if not has_perm(interaction, "manage_channels"):
        return await interaction.followup.send(embed=error_embed("You need the **Manage Channels** permission."), ephemeral=True)
    guild = interaction.guild
    channel = interaction.channel
    everyone = guild.default_role
    try:
        await channel.set_permissions(everyone, send_messages=None)
        embed = discord.Embed(
            title="Channel Unlocked",
            description=f"{channel.mention} has been unlocked.\n**Reason:** {reason}",
            color=Colors.UNLOCK, timestamp=datetime.datetime.utcnow(),
        )
        embed.set_footer(text=f"Unlocked by {interaction.user}")
        await channel.send(embed=embed)
        await interaction.followup.send(embed=success_embed("Channel unlocked."), ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(embed=error_embed("I don't have permission to unlock this channel."), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(embed=error_embed(f"Unexpected error: {e}"), ephemeral=True)


# ------------------------------------------
# /close
# ------------------------------------------
@client.tree.command(name="close", description="Close the current ticket")
async def close(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    channel = interaction.channel
    user = interaction.user
    guild = interaction.guild

    creator_id = ticket_creators.get(channel.id)
    if creator_id is None:
        return await interaction.followup.send(
            embed=error_embed("This command can only be used inside a ticket channel."), ephemeral=True,
        )

    mod_role = guild and discord.utils.get(guild.roles, name=MOD_ROLE_NAME)
    is_mod = mod_role is not None and isinstance(user, discord.Member) and mod_role in user.roles
    is_creator = user.id == creator_id
    if not (is_mod or is_creator):
        return await interaction.followup.send(
            embed=error_embed("Only a moderator or the ticket creator can close this ticket."), ephemeral=True,
        )

    await send_transcript(guild, channel, user)

    log_ch = await get_log_channel(guild)
    if log_ch:
        le = log_embed("Ticket Closed", Colors.LOCK, f"Ticket `{channel.name}` was closed by {user.mention} via /close.")
        le.add_field(name="Closed By", value=f"{user} (ID: {user.id})", inline=True)
        le.add_field(name="Creator ID", value=str(creator_id), inline=True)
        await log_ch.send(embed=le)

    ticket_creators.pop(channel.id, None)
    open_tickets.discard(creator_id)
    await save_data_async()

    await interaction.followup.send(embed=success_embed("Ticket closed."), ephemeral=True)

    try:
        await channel.delete(reason=f"Ticket closed by {user} via /close")
    except discord.Forbidden:
        pass


# ------------------------------------------
# SERVER UPDATE LOG HELPERS
# ------------------------------------------
async def get_log_channel(guild: Any) -> discord.TextChannel | None:
    if not isinstance(guild, discord.Guild) or LOG_CHANNEL_ID is None:
        return None
    channel = guild.get_channel(LOG_CHANNEL_ID)
    return channel if isinstance(channel, discord.TextChannel) else None


# ------------------------------------------
# MEMBER JOIN / LEAVE
# ------------------------------------------
@client.event
async def on_member_join(member: discord.Member):
    ch = await get_log_channel(member.guild)
    if not ch:
        return
    embed = log_embed("Member Joined", discord.Color.from_str("#57F287"))
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="User", value=f"{member.mention} (`{member}`)", inline=False)
    embed.add_field(name="Account Age", value=discord.utils.format_dt(member.created_at, "R"), inline=True)
    embed.add_field(name="ID", value=str(member.id), inline=True)
    await ch.send(embed=embed)


@client.event
async def on_member_remove(member: discord.Member):
    ch = await get_log_channel(member.guild)
    if not ch:
        return
    roles = [r.name for r in member.roles if r.name != "@everyone"]
    embed = log_embed("Member Left / Removed", discord.Color.from_str("#ED4245"))
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="User", value=f"`{member}`", inline=False)
    embed.add_field(name="Roles", value=", ".join(roles) if roles else "None", inline=False)
    embed.add_field(name="ID", value=str(member.id), inline=True)
    await ch.send(embed=embed)


# ------------------------------------------
# MEMBER UPDATE (roles / nickname)
# ------------------------------------------
@client.event
async def on_member_update(before: discord.Member, after: discord.Member):
    ch = await get_log_channel(after.guild)
    if not ch:
        return

    if before.nick != after.nick:
        embed = log_embed("Nickname Changed", discord.Color.from_str("#A855F7"))
        embed.set_thumbnail(url=after.display_avatar.url)
        embed.add_field(name="User", value=f"{after.mention} (`{after}`)", inline=False)
        embed.add_field(name="Before", value=before.nick or "*None*", inline=True)
        embed.add_field(name="After", value=after.nick or "*None*", inline=True)
        try:
            async for entry in after.guild.audit_logs(limit=5, action=discord.AuditLogAction.member_update):
                if entry.target and entry.target.id == after.id and entry.user and entry.user.id != after.id:
                    embed.add_field(name="Changed By", value=f"{entry.user.mention} (`{entry.user}`)", inline=False)
                    break
        except (discord.Forbidden, discord.HTTPException):
            pass
        await ch.send(embed=embed)

    added = [r for r in after.roles if r not in before.roles]
    removed = [r for r in before.roles if r not in after.roles]
    if added or removed:
        embed = log_embed("Member Roles Updated", discord.Color.from_str("#5865F2"))
        embed.set_thumbnail(url=after.display_avatar.url)
        embed.add_field(name="User", value=f"{after.mention} (`{after}`)", inline=False)
        if added:
            embed.add_field(name="Roles Added", value=" ".join(r.mention for r in added), inline=False)
        if removed:
            embed.add_field(name="Roles Removed", value=" ".join(r.mention for r in removed), inline=False)
        await ch.send(embed=embed)


# ------------------------------------------
# MESSAGE DELETE / EDIT
# ------------------------------------------
@client.event
async def on_message_delete(message: discord.Message):
    if message.author.bot:
        return
    ch = await get_log_channel(message.guild)
    if not ch:
        return
    embed = log_embed("Message Deleted", discord.Color.from_str("#FF4444"))
    embed.set_thumbnail(url=message.author.display_avatar.url)
    embed.add_field(name="Author", value=f"{message.author.mention} (`{message.author}`)", inline=False)
    embed.add_field(name="Channel", value=message.channel.mention, inline=True)
    embed.add_field(name="Content", value=message.content[:1024] if message.content else "*No text content*", inline=False)
    await ch.send(embed=embed)


@client.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.author.bot or before.content == after.content:
        return
    ch = await get_log_channel(after.guild)
    if not ch:
        return
    embed = log_embed("Message Edited", discord.Color.from_str("#FFCC00"))
    embed.set_thumbnail(url=after.author.display_avatar.url)
    embed.add_field(name="Author", value=f"{after.author.mention} (`{after.author}`)", inline=False)
    embed.add_field(name="Channel", value=after.channel.mention, inline=True)
    embed.add_field(name="Jump", value=f"[View message]({after.jump_url})", inline=True)
    embed.add_field(name="Before", value=before.content[:512] if before.content else "*empty*", inline=False)
    embed.add_field(name="After", value=after.content[:512] if after.content else "*empty*", inline=False)
    await ch.send(embed=embed)


# ------------------------------------------
# CHANNEL CREATE / DELETE / UPDATE
# ------------------------------------------
@client.event
async def on_guild_channel_create(channel: discord.abc.GuildChannel):
    ch = await get_log_channel(channel.guild)
    if not ch:
        return
    embed = log_embed("Channel Created", discord.Color.from_str("#57F287"))
    embed.add_field(name="Name", value=channel.mention if hasattr(channel, "mention") else channel.name, inline=True)
    embed.add_field(name="Type", value=str(channel.type).replace("_", " ").title(), inline=True)
    await ch.send(embed=embed)


@client.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    ch = await get_log_channel(channel.guild)
    if not ch:
        return
    embed = log_embed("Channel Deleted", discord.Color.from_str("#ED4245"))
    embed.add_field(name="Name", value=f"`#{channel.name}`", inline=True)
    embed.add_field(name="Type", value=str(channel.type).replace("_", " ").title(), inline=True)
    await ch.send(embed=embed)


@client.event
async def on_guild_channel_update(before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
    ch = await get_log_channel(after.guild)
    if not ch:
        return
    changes = []
    if before.name != after.name:
        changes.append(f"**Name:** `{before.name}` -> `{after.name}`")
    if hasattr(before, "topic") and hasattr(after, "topic") and before.topic != after.topic:
        changes.append(f"**Topic:** `{before.topic or 'none'}` -> `{after.topic or 'none'}`")
    if hasattr(before, "slowmode_delay") and before.slowmode_delay != after.slowmode_delay:
        changes.append(f"**Slowmode:** `{before.slowmode_delay}s` -> `{after.slowmode_delay}s`")
    if not changes:
        return
    embed = log_embed("Channel Updated", discord.Color.from_str("#5865F2"))
    embed.add_field(name="Channel", value=after.mention if hasattr(after, "mention") else after.name, inline=True)
    embed.add_field(name="Changes", value="\n".join(changes), inline=False)
    await ch.send(embed=embed)


# ------------------------------------------
# ROLE CREATE / DELETE / UPDATE
# ------------------------------------------
@client.event
async def on_guild_role_create(role: discord.Role):
    ch = await get_log_channel(role.guild)
    if not ch:
        return
    embed = log_embed("Role Created", discord.Color.from_str("#57F287"))
    embed.add_field(name="Role", value=role.mention, inline=True)
    embed.add_field(name="Color", value=str(role.color), inline=True)
    await ch.send(embed=embed)


@client.event
async def on_guild_role_delete(role: discord.Role):
    ch = await get_log_channel(role.guild)
    if not ch:
        return
    embed = log_embed("Role Deleted", discord.Color.from_str("#ED4245"))
    embed.add_field(name="Role", value=f"`@{role.name}`", inline=True)
    embed.add_field(name="Color", value=str(role.color), inline=True)
    await ch.send(embed=embed)


@client.event
async def on_guild_role_update(before: discord.Role, after: discord.Role):
    ch = await get_log_channel(after.guild)
    if not ch:
        return
    changes = []
    if before.name != after.name:
        changes.append(f"**Name:** `{before.name}` -> `{after.name}`")
    if before.color != after.color:
        changes.append(f"**Color:** `{before.color}` -> `{after.color}`")
    if before.hoist != after.hoist:
        changes.append(f"**Hoisted:** `{before.hoist}` -> `{after.hoist}`")
    if before.mentionable != after.mentionable:
        changes.append(f"**Mentionable:** `{before.mentionable}` -> `{after.mentionable}`")
    if not changes:
        return
    embed = log_embed("Role Updated", discord.Color.from_str("#FFCC00"))
    embed.add_field(name="Role", value=after.mention, inline=True)
    embed.add_field(name="Changes", value="\n".join(changes), inline=False)
    await ch.send(embed=embed)


# ------------------------------------------
# SERVER (GUILD) UPDATE
# ------------------------------------------
@client.event
async def on_guild_update(before: discord.Guild, after: discord.Guild):
    ch = await get_log_channel(after)
    if not ch:
        return
    changes = []
    if before.name != after.name:
        changes.append(f"**Name:** `{before.name}` -> `{after.name}`")
    if before.description != after.description:
        changes.append("**Description** updated")
    if before.icon != after.icon:
        changes.append("**Icon** changed")
    if before.verification_level != after.verification_level:
        changes.append(f"**Verification Level:** `{before.verification_level}` -> `{after.verification_level}`")
    if before.default_notifications != after.default_notifications:
        changes.append(f"**Notifications:** `{before.default_notifications}` -> `{after.default_notifications}`")
    if not changes:
        return
    embed = log_embed("Server Updated", discord.Color.from_str("#5865F2"))
    embed.add_field(name="Changes", value="\n".join(changes), inline=False)
    if after.icon:
        embed.set_thumbnail(url=after.icon.url)
    await ch.send(embed=embed)


# ------------------------------------------
# VOICE STATE UPDATE
# ------------------------------------------
@client.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    ch = await get_log_channel(member.guild)
    if not ch:
        return
    if before.channel == after.channel:
        return

    if before.channel is None and after.channel is not None:
        desc = f"{member.mention} joined **{after.channel.name}**"
        color = discord.Color.from_str("#57F287")
        title = "Joined Voice"
    elif before.channel is not None and after.channel is None:
        desc = f"{member.mention} left **{before.channel.name}**"
        color = discord.Color.from_str("#ED4245")
        title = "Left Voice"
    else:
        desc = f"{member.mention} moved from **{before.channel.name}** -> **{after.channel.name}**"
        color = discord.Color.from_str("#FFCC00")
        title = "Switched Voice"

    embed = log_embed(title, color, desc)
    embed.set_thumbnail(url=member.display_avatar.url)
    await ch.send(embed=embed)


# ------------------------------------------
# INVITE CREATE / DELETE
# ------------------------------------------
@client.event
async def on_invite_create(invite: discord.Invite):
    ch = await get_log_channel(invite.guild)
    if not ch:
        return
    embed = log_embed("Invite Created", discord.Color.from_str("#57F287"))
    embed.add_field(name="Code", value=f"`{invite.code}`", inline=True)
    embed.add_field(name="By", value=str(invite.inviter), inline=True)
    embed.add_field(name="Channel", value=invite.channel.mention if invite.channel else "Unknown", inline=True)
    embed.add_field(name="Max Uses", value=str(invite.max_uses) if invite.max_uses else "unlimited", inline=True)
    embed.add_field(name="Expires", value=discord.utils.format_dt(invite.expires_at, "R") if invite.expires_at else "Never", inline=True)
    await ch.send(embed=embed)


@client.event
async def on_invite_delete(invite: discord.Invite):
    ch = await get_log_channel(invite.guild)
    if not ch:
        return
    embed = log_embed("Invite Deleted", discord.Color.from_str("#ED4245"))
    embed.add_field(name="Code", value=f"`{invite.code}`", inline=True)
    await ch.send(embed=embed)


# ------------------------------------------
# EMOJI / STICKER UPDATE
# ------------------------------------------
@client.event
async def on_guild_emojis_update(guild: discord.Guild, before: list, after: list):
    ch = await get_log_channel(guild)
    if not ch:
        return
    added = [e for e in after if e not in before]
    removed = [e for e in before if e not in after]
    if not added and not removed:
        return
    embed = log_embed("Emojis Updated", discord.Color.from_str("#5865F2"))
    if added:
        embed.add_field(name="Added", value=" ".join(str(e) for e in added), inline=False)
    if removed:
        embed.add_field(name="Removed", value=" ".join(f"`:{e.name}:`" for e in removed), inline=False)
    await ch.send(embed=embed)


# ------------------------------------------
# BAN / UNBAN (audit log events)
# ------------------------------------------
@client.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    ch = await get_log_channel(guild)
    if not ch:
        return
    embed = log_embed("Member Banned", discord.Color.from_str("#FF4444"))
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="User", value=f"`{user}` (ID: {user.id})", inline=False)
    await ch.send(embed=embed)


@client.event
async def on_member_unban(guild: discord.Guild, user: discord.User):
    ch = await get_log_channel(guild)
    if not ch:
        return
    embed = log_embed("Member Unbanned", discord.Color.from_str("#44FF88"))
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="User", value=f"`{user}` (ID: {user.id})", inline=False)
    await ch.send(embed=embed)


# ------------------------------------------
# READY EVENT
# ------------------------------------------
@client.event
async def on_ready():
    load_data()
    print(f"> Logged in as {client.user} (ID: {client.user.id})")
    print("> Watching for DMs and @mentions...")
    print("-" * 40)

    # Send & pin ticket creation message
    for guild in client.guilds:
        if TICKET_CHANNEL_ID is None:
            print(f"[TICKET] TICKET_CHANNEL_ID is not configured for {guild.name}; run /setup or /panel.")
            continue
        ticket_channel = guild.get_channel(TICKET_CHANNEL_ID)
        if ticket_channel is None:
            print(f"[TICKET] Channel {TICKET_CHANNEL_ID} not found in {guild.name}")
            continue

        already_pinned = False
        async for msg in ticket_channel.pins():
            if msg.author.id == client.user.id and len(msg.components) > 0:
                already_pinned = True
                break
        if already_pinned:
            print(f"[TICKET] Pinned ticket message already exists in #{ticket_channel.name}")
            continue

        try:
            ticket_msg = await ticket_channel.send(embed=build_ticket_panel_embed(), view=TicketCreateView())
            await ticket_msg.pin()
            print(f"[OK] Ticket message sent and pinned in #{ticket_channel.name}")
        except discord.Forbidden:
            print(f"[X] Cannot send/pin in #{ticket_channel.name} -- missing permissions")
        except discord.HTTPException as e:
            print(f"[X] Failed to send ticket message: {e}")


client.run(TOKEN)

