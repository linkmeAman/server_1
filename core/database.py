"""Database configuration and models for the Dynamic API."""

import logging
from typing import Dict, Generator, Optional
from urllib.parse import quote_plus

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from .settings import get_settings

logger = logging.getLogger(__name__)

# Shared Base for all SQLAlchemy models
Base = declarative_base()

# SQLAlchemy-style bind constants (main + central DB)
SQLALCHEMY_DATABASE_URL: Optional[str] = None
SQLALCHEMY_BINDS: Dict[str, str] = {}

# Global engines and session maker
engine: Optional[Engine] = None
engines: Dict[str, Engine] = {}
SessionLocal = None


def _build_mysql_url(
    host: str,
    user: str,
    password: str,
    db_name: str,
    port: int,
    driver: str = "pymysql",
) -> Optional[str]:
    """Build a SQLAlchemy MySQL URL with utf8mb4 charset."""
    if not all([host, user, db_name]):
        return None

    encoded_password = quote_plus(password or "")
    return (
        f"mysql+{driver}://{user}:{encoded_password}@{host}:{port}/{db_name}"
        "?charset=utf8mb4"
    )


def _to_sync_engine_url(url: str) -> str:
    """
    Convert async MySQL URLs to sync driver URLs for create_engine().

    The app currently uses synchronous SQLAlchemy sessions.
    """
    if url.startswith("mysql+aiomysql://"):
        return url.replace("mysql+aiomysql://", "mysql+pymysql://", 1)
    return url


def get_main_database_url() -> Optional[str]:
    """Main/business DB URL used as SQLAlchemy default bind."""
    settings = get_settings()
    if settings.DATABASE_URL:
        return settings.DATABASE_URL

    return _build_mysql_url(
        host=settings.DB_HOST,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        db_name=settings.DB_NAME,
        port=settings.DB_PORT,
        driver="pymysql",
    )


def get_central_database_url() -> Optional[str]:
    """Central/user DB URL used for auth models."""
    settings = get_settings()
    if settings.CENTRAL_DATABASE_URL:
        return settings.CENTRAL_DATABASE_URL

    return _build_mysql_url(
        host=settings.DB_HOST,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        db_name=settings.CENTRAL_DB_NAME,
        port=settings.DB_PORT,
        driver="aiomysql",
    )


def _get_model_binds_map(central_engine: Optional[Engine]) -> Dict[object, Engine]:
    """Map model classes to specific engines for session binds."""
    if central_engine is None:
        return {}

    try:
        from .models import AuthIdentity, User
    except Exception as exc:
        logger.warning("Could not import auth models for bind mapping: %s", exc)
        return {}

    return {
        User: central_engine,
        AuthIdentity: central_engine,
    }


def _create_auth_sidecar_table(central_engine: Optional[Engine]) -> None:
    """Create only the new auth sidecar table in central DB."""
    if central_engine is None:
        return

    try:
        inspector = inspect(central_engine)
        if not inspector.has_table("user"):
            logger.info(
                "Skipping auth sidecar table creation in central DB because 'user' table is missing"
            )
            return

        from .models import AuthIdentity

        # Strict constraint: do not modify legacy `user` table schema.
        AuthIdentity.__table__.create(bind=central_engine, checkfirst=True)
        logger.info("Auth sidecar table is ready in central DB")
    except Exception as exc:
        logger.error("Failed creating auth sidecar table: %s", exc)
        raise


def init_database() -> bool:
    """Initialize main DB + central DB (bind-aware sessionmaker)."""
    global engine, engines, SessionLocal, SQLALCHEMY_DATABASE_URL, SQLALCHEMY_BINDS

    SQLALCHEMY_DATABASE_URL = get_main_database_url()
    SQLALCHEMY_BINDS = {}

    if not SQLALCHEMY_DATABASE_URL:
        logger.warning("No main DB configuration found. Database features will be disabled.")
        return False

    try:
        main_engine = create_engine(
            _to_sync_engine_url(SQLALCHEMY_DATABASE_URL),
            echo=get_settings().DEBUG,
            pool_pre_ping=True,
            pool_recycle=300,
            pool_size=5,
            max_overflow=10,
        )

        central_url = get_central_database_url()
        central_engine = None
        if central_url:
            SQLALCHEMY_BINDS["central"] = central_url
            central_engine = create_engine(
                _to_sync_engine_url(central_url),
                echo=get_settings().DEBUG,
                pool_pre_ping=True,
                pool_recycle=300,
                pool_size=5,
                max_overflow=10,
            )
        else:
            logger.warning("No central DB configuration found. Auth features will be limited.")

        model_binds = _get_model_binds_map(central_engine)
        SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=main_engine,
            binds=model_binds,
        )

        # Verify main DB connection
        with main_engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        # Verify central DB connection and ensure sidecar table exists
        if central_engine is not None:
            with central_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            _create_auth_sidecar_table(central_engine)

        engine = main_engine
        # Mutate the shared engines map in place so modules importing
        # `engines` keep seeing the current connections.
        engines.clear()
        engines["default"] = main_engine
        if central_engine is not None:
            engines["central"] = central_engine

        logger.info("Database connection(s) initialized successfully")
        return True
    except Exception as exc:
        logger.error("Database connection failed: %s", exc)
        return False


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a DB session."""
    if SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_session() -> Session:
    """Get a DB session. Caller must close it."""
    if SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")

    return SessionLocal()


# ============================================================================
# Database Models (READ-ONLY - Maps to existing production tables)
# ============================================================================

class Venue(Base):
    """Venue model - Maps to existing 'venue' table"""
    __tablename__ = "venue"
    
    id = Column(Integer, primary_key=True, index=True)
    venue = Column(String(255), index=True)
    display_name = Column(String(255))
    contact_person = Column(String(255))
    mobile = Column(String(20))
    address = Column(Text)
    short_address = Column(String(500))
    city = Column(String(100), index=True)
    country = Column(String(100))
    pincode = Column(Integer)
    state = Column(String(100))
    lat = Column(Float(precision=53), index=True)
    lng = Column(Float(precision=53), index=True)
    slug = Column(String(255))
    status = Column(Integer, default=1)
    show_on_website = Column(Integer, default=0)
    created_at = Column(DateTime)
    modified_at = Column(DateTime)
    
    def to_dict(self):
        """Convert model to dictionary"""
        return {
            "id": self.id,
            "venue": self.venue,
            "display_name": self.display_name,
            "contact_person": self.contact_person,
            "mobile": self.mobile,
            "address": self.address,
            "short_address": self.short_address,
            "city": self.city,
            "country": self.country,
            "pincode": self.pincode,
            "state": self.state,
            "lat": float(self.lat) if self.lat else None,
            "lng": float(self.lng) if self.lng else None,
            "slug": self.slug,
            "status": self.status,
            "show_on_website": self.show_on_website,
        }


class City(Base):
    """City model - Maps to existing 'cities' table"""
    __tablename__ = "cities"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), index=True)
    state_id = Column(Integer)
    state_code = Column(String(255))
    country_id = Column(Integer)
    country_code = Column(String(2))
    latitude = Column(Float(precision=53))
    longitude = Column(Float(precision=53))
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    flag = Column(Integer)
    wikiDataId = Column(String(255))
    
    def to_dict(self):
        """Convert model to dictionary"""
        return {
            "id": self.id,
            "name": self.name,
            "state_id": self.state_id,
            "state_code": self.state_code,
            "country_id": self.country_id,
            "country_code": self.country_code,
            "lat": float(self.latitude) if self.latitude else None,
            "lng": float(self.longitude) if self.longitude else None,
        }
