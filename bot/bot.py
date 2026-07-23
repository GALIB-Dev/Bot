import discord
from discord.ext import commands
import logging
import os
from bot.config import Config

logger = logging.getLogger('discord')

class ModerationBot(commands.AutoShardedBot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        
        super().__init__(
            command_prefix="!", # Required for AutoShardedBot, but we will mostly use slash commands
            intents=intents,
            help_command=None
        )

    async def setup_hook(self):
        logger.info("Setting up extensions...")
        from bot.database.repository import init_db
        await init_db()
        
        # Load cogs dynamically
        cogs_dir = os.path.join(os.path.dirname(__file__), "cogs")
        if os.path.exists(cogs_dir):
            for filename in os.listdir(cogs_dir):
                if filename.endswith(".py") and not filename.startswith("__"):
                    await self.load_extension(f"bot.cogs.{filename[:-3]}")
                    logger.info(f"Loaded cog: {filename}")
                
        # Sync slash commands
        if Config.GUILD_ID:
            guild = discord.Object(id=Config.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info(f"Synced slash commands to guild {Config.GUILD_ID}")
        else:
            await self.tree.sync()
            logger.info("Synced slash commands globally")

    async def on_ready(self):
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Shards: {self.shard_count}")
