import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from bot.database.models import Base, Warning, Ticket, ModAction, ModmailThread
from bot.config import Config
from sqlalchemy import select

logger = logging.getLogger('discord')

engine = create_async_engine(Config.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables initialized successfully.")

class Repo:
    @staticmethod
    async def add_warning(guild_id: int, user_id: int, moderator_id: int, reason: str = None) -> Warning:
        async with AsyncSessionLocal() as session:
            warn = Warning(guild_id=guild_id, user_id=user_id, moderator_id=moderator_id, reason=reason)
            session.add(warn)
            
            # Mirror the warning in the audit log table (ModAction)
            action = ModAction(guild_id=guild_id, action_type="warn", user_id=user_id, moderator_id=moderator_id, reason=reason)
            session.add(action)
            
            await session.commit()
            await session.refresh(warn)
            return warn

    @staticmethod
    async def get_warnings(guild_id: int, user_id: int):
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Warning).where(Warning.guild_id == guild_id, Warning.user_id == user_id))
            return result.scalars().all()

    @staticmethod
    async def clear_warnings(guild_id: int, user_id: int):
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Warning).where(Warning.guild_id == guild_id, Warning.user_id == user_id))
            warnings = result.scalars().all()
            for w in warnings:
                await session.delete(w)
            await session.commit()
            return len(warnings)

    @staticmethod
    async def create_ticket(guild_id: int, user_id: int, ticket_type: str, channel_id: int = None) -> Ticket:
        async with AsyncSessionLocal() as session:
            ticket = Ticket(guild_id=guild_id, user_id=user_id, ticket_type=ticket_type, channel_id=channel_id)
            session.add(ticket)
            await session.commit()
            await session.refresh(ticket)
            return ticket
            
    @staticmethod
    async def get_open_ticket(user_id: int):
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Ticket).where(Ticket.user_id == user_id, Ticket.status == 'open'))
            return result.scalars().first()

    @staticmethod
    async def get_ticket_by_channel(channel_id: int):
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Ticket).where(Ticket.channel_id == channel_id))
            return result.scalars().first()

    @staticmethod
    async def update_ticket_channel(ticket_id: int, channel_id: int):
        async with AsyncSessionLocal() as session:
            ticket = await session.get(Ticket, ticket_id)
            if ticket:
                ticket.channel_id = channel_id
                await session.commit()
            
    @staticmethod
    async def close_ticket(ticket_id: int):
        async with AsyncSessionLocal() as session:
            ticket = await session.get(Ticket, ticket_id)
            if ticket:
                ticket.status = 'closed'
                await session.commit()

    @staticmethod
    async def add_mod_action(guild_id: int, action_type: str, user_id: int, moderator_id: int, reason: str = None, expires_at=None) -> ModAction:
        async with AsyncSessionLocal() as session:
            action = ModAction(guild_id=guild_id, action_type=action_type, user_id=user_id, moderator_id=moderator_id, reason=reason, expires_at=expires_at)
            session.add(action)
            await session.commit()
            await session.refresh(action)
            return action
