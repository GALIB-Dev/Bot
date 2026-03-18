import os
import discord
from dotenv import load_dotenv

# 1. Load the variables from the .env file
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

class MyClient(discord.Client):
    async def on_ready(self):
        print(f'Logged on as {self.user}')

    async def on_message(self, message):
        # Don't respond to ourselves
        if message.author == self.user:
            return

        if message.content.startswith('!hello'):
            await message.channel.send(f'Hi there, {message.author.name}!')

# 2. Setup intents
intents = discord.Intents.default()
intents.message_content = True

# 3. Initialize and run
client = MyClient(intents=intents)

if TOKEN:
    client.run(TOKEN)
else:
    print("Error: DISCORD_TOKEN not found in .env file!")
