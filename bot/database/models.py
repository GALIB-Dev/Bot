from sqlalchemy import Column, Integer, String, BigInteger, DateTime, Boolean, ForeignKey, Text
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()

class GuildConfig(Base):
    __tablename__ = 'guild_configs'
    guild_id = Column(BigInteger, primary_key=True)
    mod_role_name = Column(String, default="Moderator")
    # Will add more as needed

class Warning(Base):
    __tablename__ = 'warnings'
    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False)
    user_id = Column(BigInteger, nullable=False)
    moderator_id = Column(BigInteger, nullable=False)
    reason = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

class Ticket(Base):
    __tablename__ = 'tickets'
    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False)
    user_id = Column(BigInteger, nullable=False)
    channel_id = Column(BigInteger, nullable=True)
    ticket_type = Column(String, nullable=False)
    status = Column(String, default='open') # open, closed
    created_at = Column(DateTime, server_default=func.now())

class TicketMessage(Base):
    __tablename__ = 'ticket_messages'
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_id = Column(Integer, ForeignKey('tickets.id'), nullable=False)
    author_id = Column(BigInteger, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    
class ModmailThread(Base):
    __tablename__ = 'modmail_threads'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    staff_thread_id = Column(BigInteger, nullable=True)
    status = Column(String, default='open')
    created_at = Column(DateTime, server_default=func.now())

class ModAction(Base):
    __tablename__ = 'mod_actions'
    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False)
    action_type = Column(String, nullable=False) # ban, kick, mute, warn
    user_id = Column(BigInteger, nullable=False)
    moderator_id = Column(BigInteger, nullable=False)
    reason = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=True)
    active = Column(Boolean, default=True)
