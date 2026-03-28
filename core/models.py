"""Authentication-related SQLAlchemy models."""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from .database import Base


class User(Base):
    """
    Legacy user model (schema read-only).

    Do not alter this table schema from application code.
    """

    __tablename__ = "user"
    __bind_key__ = "central"

    id = Column(Integer, primary_key=True, index=True)
    mobile = Column(String(20), nullable=False, index=True)
    password = Column(String(50), nullable=False)
    inactive = Column(Integer, nullable=False, default=0)

    auth_identity = relationship("AuthIdentity", back_populates="user", uselist=False)


class AuthIdentity(Base):
    """Sidecar auth table for migrated hashes and token state."""

    __tablename__ = "auth_identity"
    __bind_key__ = "central"

    user_id = Column(Integer, ForeignKey("user.id"), primary_key=True, unique=True)
    password_hash = Column(String(255), nullable=False)
    refresh_token = Column(Text, nullable=True)
    reset_token = Column(String(255), nullable=True, index=True)
    reset_token_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User", back_populates="auth_identity", uselist=False)

