import discord
from discord.ext import commands
from discord import app_commands
import io
import datetime
import chat_exporter
import logging
from bot.config import Config
from bot.database.repository import Repo

logger = logging.getLogger('discord')

class Colors:
    TICKET = discord.Color.from_str("#5865F2")
    ERROR = discord.Color.from_str("#ED4245")
    SUCCESS = discord.Color.from_str("#57F287")

def error_embed(msg: str) -> discord.Embed:
    return discord.Embed(description=msg, color=Colors.ERROR)

def success_embed(msg: str) -> discord.Embed:
    return discord.Embed(description=msg, color=Colors.SUCCESS)

class TicketManageView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="\U0001F512", custom_id="ticket_close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        channel = interaction.channel
        user = interaction.user
        
        ticket = await Repo.get_ticket_by_channel(channel.id)
        if not ticket:
            return await interaction.followup.send(embed=error_embed("Could not find this ticket in the database."), ephemeral=True)
            
        mod_role = discord.utils.get(interaction.guild.roles, name=Config.MOD_ROLE_NAME)
        is_mod = mod_role in getattr(user, "roles", [])
        is_creator = (user.id == ticket.user_id)
        
        if not (is_mod or is_creator):
            return await interaction.followup.send(embed=error_embed("Only a moderator or the creator can close this ticket."), ephemeral=True)

        await interaction.followup.send(embed=success_embed("Closing ticket and generating HTML transcript..."))
        
        # Generate HTML Transcript using chat-exporter
        if Config.TRANSCRIPT_CHANNEL_ID:
            transcript_ch = interaction.guild.get_channel(Config.TRANSCRIPT_CHANNEL_ID)
            if transcript_ch:
                try:
                    transcript_html = await chat_exporter.export(channel, tz_info="UTC")
                    if transcript_html:
                        transcript_file = discord.File(
                            io.BytesIO(transcript_html.encode("utf-8")),
                            filename=f"transcript-{channel.name}.html"
                        )
                        embed = discord.Embed(
                            title="Ticket Transcript",
                            description=f"**Channel:** #{channel.name}\n**Closed by:** {user.mention}",
                            color=Colors.TICKET,
                            timestamp=datetime.datetime.now(datetime.timezone.utc)
                        )
                        await transcript_ch.send(embed=embed, file=transcript_file)
                except Exception as e:
                    logger.error(f"Failed to export transcript: {e}")

        # Update DB and delete
        await Repo.close_ticket(ticket.id)
        try:
            await channel.delete(reason=f"Ticket closed by {user}")
        except discord.Forbidden:
            pass

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.success, emoji="\U0001F64B", custom_id="ticket_claim")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        mod_role = discord.utils.get(interaction.guild.roles, name=Config.MOD_ROLE_NAME)
        if mod_role not in getattr(interaction.user, "roles", []):
            return await interaction.response.send_message(embed=error_embed("Only moderators can claim tickets."), ephemeral=True)
            
        embed = success_embed(f"Ticket has been claimed by {interaction.user.mention}. They will be assisting you shortly.")
        await interaction.response.send_message(embed=embed)
        
        try:
            await interaction.channel.edit(topic=f"Claimed by {interaction.user.display_name}")
        except discord.Forbidden:
            pass


TICKET_TYPES = {
    "support":  {"label": "Support",          "prefix": "support",  "emoji": "\u2753",     "description": "General help and support"},
    "bug":      {"label": "Bug Report",       "prefix": "bug",      "emoji": "\U0001f41b", "description": "Report a bug or glitch"},
    "question": {"label": "Question",         "prefix": "question", "emoji": "\u2754",     "description": "Ask a general question"},
    "billing":  {"label": "Billing",          "prefix": "billing",  "emoji": "\U0001f4b3", "description": "Payment and billing inquiries"},
    "report":   {"label": "Report User",      "prefix": "report",   "emoji": "\U0001f6a8", "description": "Report a user for breaking rules"},
    "appeal":   {"label": "Appeal Action",    "prefix": "appeal",   "emoji": "\U0001f4dc", "description": "Appeal a ban, mute, or warning"},
    "feedback": {"label": "Server Feedback",  "prefix": "feedback", "emoji": "\U0001f4e3", "description": "Give suggestions or feedback"},
    "partner":  {"label": "Partnership",      "prefix": "partner",  "emoji": "\U0001f91d", "description": "Discuss a server partnership"},
    "apply":    {"label": "Staff Application","prefix": "apply",    "emoji": "\U0001f4cb", "description": "Apply for a moderator position"},
    "event":    {"label": "Event Inquiries",  "prefix": "event",    "emoji": "\U0001f389", "description": "Questions regarding events or giveaways"},
    "other":    {"label": "Other",            "prefix": "other",    "emoji": "\U0001f4ac", "description": "Any other inquiries"},
}

class TicketTypeSelect(discord.ui.Select):
    def __init__(self, user: discord.abc.User):
        self.user = user
        options = [
            discord.SelectOption(
                label=info["label"],
                value=key,
                description=info["description"],
                emoji=info["emoji"]
            )
            for key, info in TICKET_TYPES.items()
        ]
        super().__init__(placeholder="Select a ticket category...", options=options, custom_id="ticket_select")

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message(embed=error_embed("This menu is not for you."), ephemeral=True)
            
        ticket_type = self.values[0]
        await interaction.response.defer(ephemeral=True)
        
        guild = interaction.guild
        user = interaction.user
        
        # Check if they already have an open ticket
        existing = await Repo.get_open_ticket(user.id)
        if existing:
            return await interaction.followup.send(embed=error_embed("You already have an open ticket. Please close it first."), ephemeral=True)
            
        # Create ticket in DB to get the auto-incremented ID
        ticket = await Repo.create_ticket(guild.id, user.id, ticket_type)
        
        category = discord.utils.get(guild.categories, name="Tickets")
        if not category:
            try:
                category = await guild.create_category("Tickets")
            except discord.Forbidden:
                pass

        mod_role = discord.utils.get(guild.roles, name=Config.MOD_ROLE_NAME)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True)
        }
        if mod_role:
            overwrites[mod_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True, read_message_history=True)
            
        # Format: username-reason-ticketnumber
        type_info = TICKET_TYPES.get(ticket_type, {"prefix": ticket_type, "label": ticket_type.title()})
        safe_name = "".join(c for c in user.name.lower() if c.isalnum() or c == "_")
        channel_name = f"{safe_name}-{type_info['prefix']}-{ticket.id}"
        
        try:
            t_channel = await guild.create_text_channel(name=channel_name, category=category, overwrites=overwrites)
            await Repo.update_ticket_channel(ticket.id, t_channel.id)
            
            embed = discord.Embed(
                title=f"{type_info['label']} Ticket",
                description=f"Welcome {user.mention}! Please describe your issue in detail.\nA staff member will assist you shortly.",
                color=Colors.TICKET
            )
            await t_channel.send(content=f"{user.mention} {mod_role.mention if mod_role else ''}", embed=embed, view=TicketManageView())
            
            await interaction.followup.send(embed=success_embed(f"Ticket created: {t_channel.mention}"), ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error creating ticket channel: {e}")
            await Repo.close_ticket(ticket.id)
            await interaction.followup.send(embed=error_embed(f"An error occurred while creating the channel: {e}"), ephemeral=True)


class TicketCreateView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.primary, emoji="\U0001F3AB", custom_id="persistent_ticket_create")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        existing = await Repo.get_open_ticket(interaction.user.id)
        if existing:
            return await interaction.response.send_message(embed=error_embed("You already have an open ticket."), ephemeral=True)
            
        view = discord.ui.View(timeout=60)
        view.add_item(TicketTypeSelect(interaction.user))
        await interaction.response.send_message(embed=discord.Embed(description="Choose a ticket category:", color=Colors.TICKET), view=view, ephemeral=True)


class TicketsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.add_view(TicketCreateView())
        self.bot.add_view(TicketManageView())
        
    @app_commands.command(name="panel", description="Spawns the ticket creation panel (Admin only)")
    @app_commands.default_permissions(administrator=True)
    async def panel(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Support Tickets",
            description="Need help? Click the button below to create a private ticket.\nA staff member will assist you as soon as possible.",
            color=Colors.TICKET
        )
        await interaction.channel.send(embed=embed, view=TicketCreateView())
        await interaction.response.send_message("Panel created successfully.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(TicketsCog(bot))
