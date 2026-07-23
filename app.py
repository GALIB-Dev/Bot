import logging
import logging.handlers
from bot.bot import ModerationBot
from bot.config import Config

def setup_logging():
    logger = logging.getLogger('discord')
    logger.setLevel(logging.INFO)

    handler = logging.handlers.RotatingFileHandler(
        filename='discord.log',
        encoding='utf-8',
        maxBytes=32 * 1024 * 1024,  # 32 MiB
        backupCount=5,
    )
    dt_fmt = '%Y-%m-%d %H:%M:%S'
    formatter = logging.Formatter('[{asctime}] [{levelname:<8}] {name}: {message}', dt_fmt, style='{')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

def main():
    setup_logging()
    
    if not Config.TOKEN:
        logging.getLogger('discord').error("No DISCORD_TOKEN found in environment variables. Check your .env file.")
        return

    bot = ModerationBot()
    bot.run(Config.TOKEN, log_handler=None)

if __name__ == "__main__":
    main()
