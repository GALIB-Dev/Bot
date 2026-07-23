import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    TOKEN = os.getenv("DISCORD_TOKEN")
    
    _mod_ch = os.getenv("MOD_CHANNEL_ID", "").strip()
    MOD_CHANNEL_ID = int(_mod_ch) if _mod_ch.isdigit() else None
    
    _log_ch = os.getenv("LOG_CHANNEL_ID", "").strip()
    LOG_CHANNEL_ID = int(_log_ch) if _log_ch.isdigit() else None
    
    _tkt_ch = os.getenv("TICKET_CHANNEL_ID", "").strip()
    TICKET_CHANNEL_ID = int(_tkt_ch) if _tkt_ch.isdigit() else None
    
    _trn_ch = os.getenv("TRANSCRIPT_CHANNEL_ID", "").strip()
    TRANSCRIPT_CHANNEL_ID = int(_trn_ch) if _trn_ch.isdigit() else None
    
    _guild = os.getenv("GUILD_ID", "").strip()
    GUILD_ID = int(_guild) if _guild.isdigit() else None
    
    MOD_ROLE_NAME = os.getenv("MOD_ROLE_NAME", "Moderator")
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///bot_data.db")
