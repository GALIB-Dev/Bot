import asyncio
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.database.repository import init_db, Repo
from bot.config import Config

async def migrate():
    print("Initializing database schema...")
    await init_db()
    
    data_file = "bot_data.json"
    if not os.path.exists(data_file):
        print(f"No {data_file} found in the root directory. Skipping migration.")
        return
        
    with open(data_file, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    print("Migrating warnings...")
    warnings_data = data.get("warnings", {})
    count_warns = 0
    for guild_id_str, users in warnings_data.items():
        guild_id = int(guild_id_str)
        for user_id_str, warns in users.items():
            user_id = int(user_id_str)
            for w in warns:
                mod_id = w.get("moderator_id", 0)
                reason = w.get("reason", "Migrated from JSON")
                await Repo.add_warning(guild_id, user_id, mod_id, reason)
                count_warns += 1
                
    print(f"Migrated {count_warns} warnings.")
    
    print("Migrating open tickets...")
    ticket_creators = data.get("ticket_creators", {})
    count_tickets = 0
    
    guild_id = Config.GUILD_ID or 0
    for channel_id_str, user_id in ticket_creators.items():
        channel_id = int(channel_id_str)
        await Repo.create_ticket(guild_id, user_id, "migrated", channel_id)
        count_tickets += 1
        
    print(f"Migrated {count_tickets} open tickets.")
    print("\n✅ Migration complete! You can now safely run the new bot.")

if __name__ == "__main__":
    asyncio.run(migrate())
