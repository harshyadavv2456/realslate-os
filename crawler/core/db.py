"""
RealSlate Core - Database Management
PostgreSQL database with SQLAlchemy ORM and historical preservation.
"""

import os
import gzip
import base64
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Generator
from contextlib import contextmanager
from threading import Lock

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    BigInteger,
    String,
    Text,
    Float,
    Boolean,
    DateTime,
    JSON,
    Index,
    ForeignKey,
    event,
    func,
    and_,
    or_,
    text,
    inspect,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session
from sqlalchemy.pool import QueuePool, NullPool
from sqlalchemy.dialects.postgresql import JSONB
from loguru import logger

from .utils import get_config, generate_uid


Base = declarative_base()


# =============================================================================
# Database Models
# =============================================================================

class RawPage(Base):
    """
    Store raw HTML pages for debugging and reprocessing.
    """
    __tablename__ = "raw_pages"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    url = Column(String(2000), nullable=False, index=True)
    url_hash = Column(String(32), nullable=False, index=True)
    html = Column(Text, nullable=True)
    html_compressed = Column(Text, nullable=True)  # For gzip compressed storage
    fetched_at = Column(DateTime, default=datetime.utcnow, index=True)
    source = Column(String(100), nullable=False, index=True)
    city = Column(String(100), nullable=True, index=True)
    page_type = Column(String(50), default="listing")  # listing, detail
    status_code = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    
    __table_args__ = (
        Index("idx_raw_pages_source_fetched", "source", "fetched_at"),
    )


class Listing(Base):
    """
    Individual property listings as scraped.
    Supports versioning to track changes over time.
    """
    __tablename__ = "listings"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    property_uid = Column(String(32), nullable=False, index=True)
    source = Column(String(100), nullable=False, index=True)
    city = Column(String(100), nullable=True, index=True)
    listing_type = Column(String(10), nullable=False, default="buy", index=True)  # buy, rent
    
    # Core fields
    price = Column(Float, nullable=True, index=True)
    address = Column(String(500), nullable=True)
    address_hash = Column(String(32), nullable=True, index=True)
    beds = Column(Integer, nullable=True)
    bathrooms = Column(Integer, nullable=True)
    area = Column(Float, nullable=True)  # sq.ft
    
    # URLs
    url = Column(String(2000), nullable=True)
    url_hash = Column(String(32), nullable=False, index=True)
    
    # Extended fields
    property_type = Column(String(100), nullable=True)
    builder_name = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    amenities = Column(JSON, nullable=True)
    locality = Column(String(200), nullable=True)
    
    # Metadata
    posted_date = Column(DateTime, nullable=True)
    scraped_at = Column(DateTime, default=datetime.utcnow, index=True)
    last_seen = Column(DateTime, default=datetime.utcnow, index=True)
    raw_data = Column(JSON, nullable=True)  # Store all extracted data
    
    # Processing flags
    is_valid = Column(Boolean, default=True)
    is_duplicate = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True, index=True)
    completeness_score = Column(Float, nullable=True)
    
    # Version tracking
    version = Column(Integer, default=1, nullable=False)
    previous_version_id = Column(Integer, ForeignKey("listings.id"), nullable=True)
    is_latest = Column(Boolean, default=True, index=True)
    
    # Foreign keys
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=True, index=True)
    
    __table_args__ = (
        Index("idx_listings_source_city_scraped", "source", "city", "scraped_at"),
        Index("idx_listings_property_uid_source", "property_uid", "source"),
        Index("idx_listings_url_hash_latest", "url_hash", "is_latest"),
        Index("idx_listings_listing_type", "listing_type"),
        Index("idx_listings_last_seen", "last_seen"),
        Index("idx_listings_active_source", "is_active", "source"),
    )


class Property(Base):
    """
    Canonical properties (deduplicated across sources).
    Supports versioning to track data changes over time.
    """
    __tablename__ = "properties"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    property_uid = Column(String(32), unique=True, nullable=False, index=True)
    
    # Location
    canonical_address = Column(String(500), nullable=True)
    address_hash = Column(String(32), nullable=True, index=True)
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    city = Column(String(100), nullable=True, index=True)
    locality = Column(String(200), nullable=True)
    
    # Current values (latest from any source)
    current_price = Column(Float, nullable=True)
    current_area = Column(Float, nullable=True)
    beds = Column(Integer, nullable=True)
    bathrooms = Column(Integer, nullable=True)
    property_type = Column(String(100), nullable=True)
    
    # Tracking
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow, index=True)
    last_updated = Column(DateTime, default=datetime.utcnow)
    
    # Source tracking
    sources = Column(JSON, default=list)  # List of sources where this property appears
    source_urls = Column(JSON, default=dict)  # {source: url}
    
    # Stats
    price_history_count = Column(Integer, default=0)
    listing_count = Column(Integer, default=0)
    
    # Version tracking
    version = Column(Integer, default=1, nullable=False)
    data_hash = Column(String(32), nullable=True, index=True)  # Hash of key fields for change detection
    
    # Status tracking
    status = Column(String(20), default="active")  # active, delisted, sold
    status_changed_at = Column(DateTime, nullable=True)
    
    # Relationships
    listings = relationship("Listing", backref="property", foreign_keys=[Listing.property_id])
    events = relationship("PropertyEvent", backref="property")
    
    __table_args__ = (
        Index("idx_properties_city_last_seen", "city", "last_seen"),
        Index("idx_properties_lat_lng", "lat", "lng"),
        Index("idx_properties_status", "status"),
    )


class PropertyEvent(Base):
    """
    Track property events (price changes, status changes, etc.).
    """
    __tablename__ = "property_events"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False, index=True)
    property_uid = Column(String(32), nullable=False, index=True)
    
    event_type = Column(String(50), nullable=False, index=True)
    # Event types: price_change, new_listing, delisted, relisted, data_update
    
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    value_numeric = Column(Float, nullable=True)  # For price changes
    
    source = Column(String(100), nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    event_metadata = Column(JSON, nullable=True)
    
    __table_args__ = (
        Index("idx_events_property_timestamp", "property_id", "timestamp"),
        Index("idx_events_type_timestamp", "event_type", "timestamp"),
    )


class CrawlSession(Base):
    """
    Track crawl sessions for monitoring.
    """
    __tablename__ = "crawl_sessions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(50), unique=True, nullable=False)
    
    source = Column(String(100), nullable=False, index=True)
    city = Column(String(100), nullable=True)
    
    started_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    status = Column(String(20), default="running")  # running, completed, failed, interrupted
    
    # Stats
    pages_crawled = Column(Integer, default=0)
    listings_found = Column(Integer, default=0)
    listings_new = Column(Integer, default=0)
    listings_updated = Column(Integer, default=0)
    errors = Column(Integer, default=0)
    
    # Details
    error_messages = Column(JSON, default=list)
    config_snapshot = Column(JSON, nullable=True)
    
    __table_args__ = (
        Index("idx_sessions_source_started", "source", "started_at"),
    )


class CrawlUrl(Base):
    """
    Track URLs for crawling status.
    """
    __tablename__ = "crawl_urls"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    url = Column(String(2000), nullable=False)
    url_hash = Column(String(32), nullable=False, index=True)
    
    source = Column(String(100), nullable=False, index=True)
    city = Column(String(100), nullable=True)
    page_type = Column(String(50), default="listing")
    
    # Status
    status = Column(String(20), default="pending")  # pending, in_progress, completed, failed
    attempts = Column(Integer, default=0)
    last_attempt = Column(DateTime, nullable=True)
    last_success = Column(DateTime, nullable=True)
    
    # Priority (lower = higher priority)
    priority = Column(Integer, default=100)
    
    # Error tracking
    last_error = Column(Text, nullable=True)
    
    __table_args__ = (
        Index("idx_crawl_urls_status_priority", "status", "priority"),
        Index("idx_crawl_urls_source_status", "source", "status"),
    )


class CrawlState(Base):
    """
    Persistent crawl state for incremental engine.
    Tracks per-source/city/listing_type last crawl info.
    """
    __tablename__ = "crawl_state"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(100), nullable=False, index=True)
    city = Column(String(100), nullable=False, index=True)
    listing_type = Column(String(10), nullable=False, default="buy")
    
    # State
    last_crawl_at = Column(DateTime, nullable=True)
    last_successful_crawl_at = Column(DateTime, nullable=True)
    last_page_reached = Column(Integer, default=0)
    listings_found_last_run = Column(Integer, default=0)
    listings_new_last_run = Column(Integer, default=0)
    listings_updated_last_run = Column(Integer, default=0)
    listings_unchanged_last_run = Column(Integer, default=0)
    
    # Cumulative
    total_crawls = Column(Integer, default=0)
    total_listings_found = Column(Integer, default=0)
    total_errors = Column(Integer, default=0)
    
    # Priority for recrawl scheduling
    recrawl_priority = Column(Integer, default=100)  # Lower = higher priority
    next_crawl_after = Column(DateTime, nullable=True)
    
    # Error tracking
    consecutive_errors = Column(Integer, default=0)
    last_error = Column(Text, nullable=True)
    last_error_at = Column(DateTime, nullable=True)
    
    __table_args__ = (
        Index("idx_crawl_state_source_city_type", "source", "city", "listing_type", unique=True),
        Index("idx_crawl_state_next_crawl", "next_crawl_after"),
        Index("idx_crawl_state_priority", "recrawl_priority"),
    )


class Source(Base):
    """
    Source registry with health and configuration metadata.
    """
    __tablename__ = "sources"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    base_url = Column(String(500), nullable=True)
    enabled = Column(Boolean, default=True)
    priority = Column(Integer, default=50)
    
    # Stats
    total_listings = Column(Integer, default=0)
    total_properties = Column(Integer, default=0)
    total_crawls = Column(Integer, default=0)
    
    # Health
    last_crawl_at = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    success_rate = Column(Float, default=1.0)
    avg_listings_per_crawl = Column(Float, default=0.0)
    
    # Ban detection
    is_banned = Column(Boolean, default=False)
    ban_detected_at = Column(DateTime, nullable=True)
    ban_lifted_at = Column(DateTime, nullable=True)
    
    # Configuration hash (detect config changes)
    config_hash = Column(String(32), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# =============================================================================
# Database Manager
# =============================================================================

class DatabaseManager:
    """
    Database connection and operations manager.
    """
    
    _instance = None
    
    def __new__(cls, config: Optional[dict] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, config: Optional[dict] = None):
        if self._initialized:
            return
        
        self.config = config or get_config()
        self.db_config = self.config.get("database", {})
        
        self._engine = None
        self._session_factory = None
        self._initialized = True
    
    @property
    def connection_string(self) -> str:
        """Build database connection string (PostgreSQL or SQLite)."""
        db_type = self.db_config.get("type", "sqlite")
        
        if db_type == "sqlite":
            db_path = self.db_config.get("path", "realslate.db")
            return f"sqlite:///{db_path}"
        
        # PostgreSQL
        host = self.db_config.get("host", "localhost")
        port = self.db_config.get("port", 5432)
        name = self.db_config.get("name", "realslate")
        user = self.db_config.get("user", "realslate")
        password = self.db_config.get("password", "")
        
        return f"postgresql://{user}:{password}@{host}:{port}/{name}"
    
    def initialize(self) -> None:
        """Initialize database engine and session factory."""
        if self._engine is not None:
            return
        
        logger.info("Initializing database connection...")
        
        db_type = self.db_config.get("type", "sqlite")
        
        if db_type == "sqlite":
            # SQLite doesn't support pool settings
            self._engine = create_engine(
                self.connection_string,
                echo=self.db_config.get("echo", False),
                connect_args={"check_same_thread": False},
            )
        else:
            # PostgreSQL with connection pooling
            self._engine = create_engine(
                self.connection_string,
                poolclass=QueuePool,
                pool_size=self.db_config.get("pool_size", 10),
                max_overflow=self.db_config.get("max_overflow", 20),
                pool_pre_ping=True,
                echo=self.db_config.get("echo", False),
            )
        
        self._session_factory = sessionmaker(bind=self._engine)
        
        logger.info(f"Database connection initialized ({db_type})")
    
    def create_tables(self) -> None:
        """Create all database tables."""
        if self._engine is None:
            self.initialize()
        
        logger.info("Creating database tables...")
        Base.metadata.create_all(self._engine)
        logger.info("Database tables created")
    
    def drop_tables(self) -> None:
        """Drop all database tables (use with caution!)."""
        if self._engine is None:
            self.initialize()
        
        logger.warning("Dropping all database tables!")
        Base.metadata.drop_all(self._engine)
        logger.info("Database tables dropped")
    
    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """Get a database session context manager."""
        if self._session_factory is None:
            self.initialize()
        
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Database session error: {e}")
            raise
        finally:
            session.close()
    
    def get_session(self) -> Session:
        """Get a new database session (caller must manage lifecycle)."""
        if self._session_factory is None:
            self.initialize()
        return self._session_factory()
    
    # =========================================================================
    # Listing Operations
    # =========================================================================
    
    def save_listing(self, session: Session, listing_data: Dict[str, Any]) -> Listing:
        """Save a listing to database."""
        listing = Listing(
            property_uid=listing_data.get("property_uid"),
            source=listing_data.get("source"),
            city=listing_data.get("city"),
            listing_type=listing_data.get("listing_type", "buy"),
            price=listing_data.get("price"),
            address=listing_data.get("address"),
            address_hash=listing_data.get("address_hash"),
            beds=listing_data.get("beds"),
            bathrooms=listing_data.get("bathrooms"),
            area=listing_data.get("area"),
            url=listing_data.get("detail_link"),
            url_hash=listing_data.get("url_hash"),
            property_type=listing_data.get("property_type"),
            builder_name=listing_data.get("builder_name"),
            description=listing_data.get("description"),
            amenities=listing_data.get("amenities"),
            locality=listing_data.get("locality"),
            posted_date=listing_data.get("posted_date"),
            raw_data=listing_data,
            completeness_score=listing_data.get("completeness_score"),
        )
        
        session.add(listing)
        return listing
    
    def save_listings_batch(
        self,
        session: Session,
        listings: List[Dict[str, Any]],
        batch_size: int = 100
    ) -> tuple:
        """
        Save multiple listings efficiently using bulk insert.
        Uses batched inserts for better performance and memory usage.
        Returns (saved_count, failed_count).
        
        Supports version tracking - if a listing with same url_hash exists,
        creates a new version and marks old as not latest.
        """
        if not listings:
            return 0, 0
        
        saved_count = 0
        failed_count = 0
        
        # Process in batches
        for i in range(0, len(listings), batch_size):
            batch = listings[i:i + batch_size]
            
            try:
                # Check for existing listings to handle versioning
                url_hashes = [l.get("url_hash") for l in batch if l.get("url_hash")]
                
                existing_listings = {}
                if url_hashes:
                    existing = session.query(Listing).filter(
                        and_(
                            Listing.url_hash.in_(url_hashes),
                            Listing.is_latest == True
                        )
                    ).all()
                    existing_listings = {l.url_hash: l for l in existing}
                
                # Prepare listing objects
                listing_objects = []
                listings_to_mark_old = []
                
                for listing_data in batch:
                    url_hash = listing_data.get("url_hash")
                    existing = existing_listings.get(url_hash)
                    
                    version = 1
                    previous_id = None
                    
                    if existing:
                        # Check if data actually changed
                        if self._listing_changed(existing, listing_data):
                            version = existing.version + 1
                            previous_id = existing.id
                            listings_to_mark_old.append(existing.id)
                        else:
                            # No change, skip this listing
                            continue
                    
                    listing_objects.append({
                        "property_uid": listing_data.get("property_uid"),
                        "source": listing_data.get("source"),
                        "city": listing_data.get("city"),
                        "listing_type": listing_data.get("listing_type", "buy"),
                        "price": listing_data.get("price"),
                        "address": listing_data.get("address"),
                        "address_hash": listing_data.get("address_hash"),
                        "beds": listing_data.get("beds"),
                        "bathrooms": listing_data.get("bathrooms"),
                        "area": listing_data.get("area"),
                        "url": listing_data.get("detail_link"),
                        "url_hash": listing_data.get("url_hash"),
                        "property_type": listing_data.get("property_type"),
                        "builder_name": listing_data.get("builder_name"),
                        "description": listing_data.get("description"),
                        "amenities": listing_data.get("amenities"),
                        "locality": listing_data.get("locality"),
                        "posted_date": listing_data.get("posted_date"),
                        "raw_data": listing_data,
                        "completeness_score": listing_data.get("completeness_score"),
                        "scraped_at": datetime.utcnow(),
                        "last_seen": datetime.utcnow(),
                        "version": version,
                        "previous_version_id": previous_id,
                        "is_latest": True,
                    })
                
                # Mark old versions as not latest
                if listings_to_mark_old:
                    session.query(Listing).filter(
                        Listing.id.in_(listings_to_mark_old)
                    ).update({"is_latest": False}, synchronize_session=False)
                
                # Bulk insert new listings
                if listing_objects:
                    session.bulk_insert_mappings(Listing, listing_objects)
                    saved_count += len(listing_objects)
                
            except Exception as e:
                logger.warning(f"Batch insert failed, falling back to individual: {e}")
                
                # Fallback to individual inserts
                for listing_data in batch:
                    try:
                        self.save_listing(session, listing_data)
                        saved_count += 1
                    except Exception as e2:
                        logger.warning(f"Failed to save listing: {e2}")
                        failed_count += 1
        
        return saved_count, failed_count
    
    def _listing_changed(self, existing: Listing, new_data: Dict[str, Any]) -> bool:
        """Check if listing data has meaningfully changed."""
        # Compare key fields
        if existing.price != new_data.get("price"):
            return True
        if existing.beds != new_data.get("beds"):
            return True
        if existing.bathrooms != new_data.get("bathrooms"):
            return True
        if existing.area != new_data.get("area"):
            return True
        if existing.address != new_data.get("address"):
            return True
        
        return False
    
    def get_listing_by_url_hash(
        self,
        session: Session,
        url_hash: str,
        source: str
    ) -> Optional[Listing]:
        """Get listing by URL hash."""
        return session.query(Listing).filter(
            and_(Listing.url_hash == url_hash, Listing.source == source)
        ).first()
    
    def listing_exists(
        self,
        session: Session,
        url_hash: str,
        source: str,
        within_hours: int = 24
    ) -> bool:
        """Check if listing was recently scraped."""
        cutoff = datetime.utcnow() - timedelta(hours=within_hours)
        
        return session.query(Listing).filter(
            and_(
                Listing.url_hash == url_hash,
                Listing.source == source,
                Listing.scraped_at > cutoff
            )
        ).first() is not None
    
    # =========================================================================
    # Property Operations
    # =========================================================================
    
    def get_or_create_property(
        self,
        session: Session,
        property_uid: str,
        listing_data: Dict[str, Any]
    ) -> tuple:
        """
        Get existing property or create new one.
        Returns (property, is_new, price_changed).
        Supports version tracking and price change detection.
        """
        prop = session.query(Property).filter(
            Property.property_uid == property_uid
        ).first()
        
        price_changed = False
        
        if prop:
            # Update last_seen
            prop.last_seen = datetime.utcnow()
            prop.listing_count += 1
            
            # Add source if new
            source = listing_data.get("source")
            if source and source not in (prop.sources or []):
                prop.sources = (prop.sources or []) + [source]
            
            # Check for price change
            new_price = listing_data.get("price")
            if new_price and prop.current_price and new_price != prop.current_price:
                price_changed = True
                prop.price_history_count += 1
                prop.version += 1
                prop.last_updated = datetime.utcnow()
                prop.current_price = new_price
            
            # Update status to active if previously delisted
            if prop.status == "delisted":
                prop.status = "active"
                prop.status_changed_at = datetime.utcnow()
            
            # Compute data hash for change detection
            prop.data_hash = self._compute_property_hash(listing_data)
            
            return prop, False, price_changed
        
        # Create new property
        prop = Property(
            property_uid=property_uid,
            canonical_address=listing_data.get("address"),
            address_hash=listing_data.get("address_hash"),
            city=listing_data.get("city"),
            current_price=listing_data.get("price"),
            current_area=listing_data.get("area"),
            beds=listing_data.get("beds"),
            bathrooms=listing_data.get("bathrooms"),
            property_type=listing_data.get("property_type"),
            sources=[listing_data.get("source")],
            listing_count=1,
            version=1,
            status="active",
            data_hash=self._compute_property_hash(listing_data),
        )
        
        session.add(prop)
        return prop, True, False
    
    def _compute_property_hash(self, data: Dict[str, Any]) -> str:
        """Compute a hash of key property fields for change detection."""
        import hashlib
        
        key_fields = [
            str(data.get("price", "")),
            str(data.get("beds", "")),
            str(data.get("bathrooms", "")),
            str(data.get("area", "")),
            data.get("address", ""),
        ]
        
        hash_input = "|".join(key_fields)
        return hashlib.md5(hash_input.encode()).hexdigest()
    
    def get_listing_history(
        self,
        session: Session,
        url_hash: str,
        source: str
    ) -> List[Listing]:
        """Get all versions of a listing by URL hash."""
        return session.query(Listing).filter(
            and_(Listing.url_hash == url_hash, Listing.source == source)
        ).order_by(Listing.version.desc()).all()
    
    def get_property_price_history(
        self,
        session: Session,
        property_uid: str
    ) -> List[Dict[str, Any]]:
        """Get price history for a property from events."""
        events = session.query(PropertyEvent).filter(
            and_(
                PropertyEvent.property_uid == property_uid,
                PropertyEvent.event_type == "price_change"
            )
        ).order_by(PropertyEvent.timestamp.asc()).all()
        
        return [
            {
                "timestamp": e.timestamp,
                "old_price": float(e.old_value) if e.old_value else None,
                "new_price": float(e.new_value) if e.new_value else None,
                "source": e.source,
            }
            for e in events
        ]
    
    def record_property_event(
        self,
        session: Session,
        property_id: int,
        property_uid: str,
        event_type: str,
        old_value: Any = None,
        new_value: Any = None,
        source: str = None,
        metadata: dict = None
    ) -> PropertyEvent:
        """Record a property event."""
        event = PropertyEvent(
            property_id=property_id,
            property_uid=property_uid,
            event_type=event_type,
            old_value=str(old_value) if old_value is not None else None,
            new_value=str(new_value) if new_value is not None else None,
            value_numeric=new_value if isinstance(new_value, (int, float)) else None,
            source=source,
            event_metadata=metadata,
        )
        
        session.add(event)
        return event
    
    # =========================================================================
    # Raw Page Operations
    # =========================================================================
    
    def save_raw_page(
        self,
        session: Session,
        url: str,
        url_hash: str,
        html: str,
        source: str,
        city: str = None,
        page_type: str = "listing",
        status_code: int = None,
        error: str = None,
        compress: bool = True
    ) -> RawPage:
        """
        Save raw HTML page with optional compression.
        Compression typically achieves 80-90% size reduction.
        """
        html_content = None
        html_compressed = None
        
        if html:
            if compress:
                # Compress HTML
                try:
                    compressed = gzip.compress(html.encode('utf-8'))
                    html_compressed = base64.b64encode(compressed).decode('ascii')
                except Exception as e:
                    logger.warning(f"Failed to compress HTML: {e}")
                    html_content = html
            else:
                html_content = html
        
        page = RawPage(
            url=url,
            url_hash=url_hash,
            html=html_content,
            html_compressed=html_compressed,
            source=source,
            city=city,
            page_type=page_type,
            status_code=status_code,
            error_message=error,
        )
        
        session.add(page)
        return page
    
    def get_raw_page_html(self, page: RawPage) -> Optional[str]:
        """Get decompressed HTML from a raw page."""
        if page.html:
            return page.html
        
        if page.html_compressed:
            try:
                compressed = base64.b64decode(page.html_compressed.encode('ascii'))
                return gzip.decompress(compressed).decode('utf-8')
            except Exception as e:
                logger.error(f"Failed to decompress HTML: {e}")
        
        return None
    
    def cleanup_old_raw_pages(
        self,
        session: Session,
        retention_days: int = 7
    ) -> int:
        """Delete old raw pages in batches to avoid lock contention."""
        from sqlalchemy import select
        
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        
        total_deleted = 0
        batch_size = 1000
        
        while True:
            # Delete in batches using proper select() for SQLAlchemy 2.0
            subquery = select(RawPage.id).where(
                RawPage.fetched_at < cutoff
            ).limit(batch_size).scalar_subquery()
            
            deleted = session.query(RawPage).filter(
                RawPage.id.in_(subquery)
            ).delete(synchronize_session=False)
            
            session.commit()
            total_deleted += deleted
            
            if deleted < batch_size:
                break
        
        if total_deleted > 0:
            logger.info(f"Cleaned up {total_deleted} old raw pages")
        
        return total_deleted
    
    def vacuum_database(self, session: Session) -> None:
        """Run VACUUM to reclaim storage (PostgreSQL only)."""
        db_type = self.db_config.get("type", "sqlite")
        
        if db_type == "postgresql":
            try:
                # Need to run outside transaction
                session.execute(text("COMMIT"))
                session.execute(text("VACUUM ANALYZE"))
                logger.info("Database VACUUM completed")
            except Exception as e:
                logger.warning(f"VACUUM failed: {e}")
        elif db_type == "sqlite":
            try:
                session.execute(text("VACUUM"))
                logger.info("SQLite VACUUM completed")
            except Exception as e:
                logger.warning(f"VACUUM failed: {e}")
    
    # =========================================================================
    # Crawl Session Operations
    # =========================================================================
    
    def create_crawl_session(
        self,
        session: Session,
        session_id: str,
        source: str,
        city: str = None,
        config: dict = None
    ) -> CrawlSession:
        """Create a new crawl session."""
        crawl_session = CrawlSession(
            session_id=session_id,
            source=source,
            city=city,
            config_snapshot=config,
        )
        
        session.add(crawl_session)
        return crawl_session
    
    def update_crawl_session(
        self,
        session: Session,
        session_id: str,
        **kwargs
    ) -> None:
        """Update crawl session stats."""
        crawl_session = session.query(CrawlSession).filter(
            CrawlSession.session_id == session_id
        ).first()
        
        if crawl_session:
            for key, value in kwargs.items():
                if hasattr(crawl_session, key):
                    setattr(crawl_session, key, value)
    
    def end_crawl_session(
        self,
        session: Session,
        session_id: str,
        status: str = "completed"
    ) -> None:
        """Mark crawl session as ended."""
        self.update_crawl_session(
            session,
            session_id,
            ended_at=datetime.utcnow(),
            status=status
        )
    
    # =========================================================================
    # URL Queue Operations
    # =========================================================================
    
    def add_url_to_queue(
        self,
        session: Session,
        url: str,
        url_hash: str,
        source: str,
        city: str = None,
        page_type: str = "listing",
        priority: int = 100
    ) -> CrawlUrl:
        """Add URL to crawl queue."""
        # Check if URL already exists
        existing = session.query(CrawlUrl).filter(
            CrawlUrl.url_hash == url_hash
        ).first()
        
        if existing:
            return existing
        
        crawl_url = CrawlUrl(
            url=url,
            url_hash=url_hash,
            source=source,
            city=city,
            page_type=page_type,
            priority=priority,
        )
        
        session.add(crawl_url)
        return crawl_url
    
    def get_pending_urls(
        self,
        session: Session,
        source: str = None,
        limit: int = 100
    ) -> List[CrawlUrl]:
        """Get pending URLs for crawling."""
        query = session.query(CrawlUrl).filter(
            CrawlUrl.status == "pending"
        )
        
        if source:
            query = query.filter(CrawlUrl.source == source)
        
        return query.order_by(CrawlUrl.priority).limit(limit).all()
    
    def mark_url_status(
        self,
        session: Session,
        url_hash: str,
        status: str,
        error: str = None
    ) -> None:
        """Update URL crawl status."""
        crawl_url = session.query(CrawlUrl).filter(
            CrawlUrl.url_hash == url_hash
        ).first()
        
        if crawl_url:
            crawl_url.status = status
            crawl_url.attempts += 1
            crawl_url.last_attempt = datetime.utcnow()
            
            if status == "completed":
                crawl_url.last_success = datetime.utcnow()
            
            if error:
                crawl_url.last_error = error
    
    # =========================================================================
    # Statistics
    # =========================================================================
    
    # =========================================================================
    # Crawl State Operations
    # =========================================================================
    
    def get_or_create_crawl_state(
        self,
        session: Session,
        source: str,
        city: str,
        listing_type: str = "buy"
    ) -> CrawlState:
        """Get or create a crawl state record."""
        state = session.query(CrawlState).filter(
            and_(
                CrawlState.source == source,
                CrawlState.city == city,
                CrawlState.listing_type == listing_type,
            )
        ).first()
        
        if not state:
            state = CrawlState(
                source=source,
                city=city,
                listing_type=listing_type,
            )
            session.add(state)
            session.flush()
        
        return state
    
    def update_crawl_state_success(
        self,
        session: Session,
        source: str,
        city: str,
        listing_type: str,
        page_reached: int,
        found: int,
        new: int,
        updated: int,
        unchanged: int
    ) -> None:
        """Update crawl state after a successful crawl."""
        state = self.get_or_create_crawl_state(session, source, city, listing_type)
        
        now = datetime.utcnow()
        state.last_crawl_at = now
        state.last_successful_crawl_at = now
        state.last_page_reached = page_reached
        state.listings_found_last_run = found
        state.listings_new_last_run = new
        state.listings_updated_last_run = updated
        state.listings_unchanged_last_run = unchanged
        state.total_crawls += 1
        state.total_listings_found += found
        state.consecutive_errors = 0
        state.last_error = None
        
        # Set next crawl time based on activity
        if new > 0 or updated > 0:
            state.recrawl_priority = max(10, state.recrawl_priority - 10)
            state.next_crawl_after = now + timedelta(hours=12)
        else:
            state.recrawl_priority = min(200, state.recrawl_priority + 5)
            state.next_crawl_after = now + timedelta(hours=24)
    
    def update_crawl_state_error(
        self,
        session: Session,
        source: str,
        city: str,
        listing_type: str,
        error: str
    ) -> None:
        """Update crawl state after an error."""
        state = self.get_or_create_crawl_state(session, source, city, listing_type)
        
        now = datetime.utcnow()
        state.last_crawl_at = now
        state.total_crawls += 1
        state.total_errors += 1
        state.consecutive_errors += 1
        state.last_error = error[:500]
        state.last_error_at = now
        
        backoff_hours = min(48, 2 ** state.consecutive_errors)
        state.next_crawl_after = now + timedelta(hours=backoff_hours)
    
    def get_crawl_states_due(
        self,
        session: Session,
        source: str = None,
        limit: int = 100
    ) -> List[CrawlState]:
        """Get crawl states due for recrawl, ordered by priority."""
        now = datetime.utcnow()
        query = session.query(CrawlState).filter(
            or_(
                CrawlState.next_crawl_after.is_(None),
                CrawlState.next_crawl_after <= now,
            )
        )
        
        if source:
            query = query.filter(CrawlState.source == source)
        
        return query.order_by(CrawlState.recrawl_priority).limit(limit).all()
    
    # =========================================================================
    # Source Operations
    # =========================================================================
    
    def get_or_create_source(
        self,
        session: Session,
        name: str,
        base_url: str = None,
        priority: int = 50
    ) -> Source:
        """Get or create a source registry entry."""
        source = session.query(Source).filter(Source.name == name).first()
        
        if not source:
            source = Source(
                name=name,
                base_url=base_url,
                priority=priority,
            )
            session.add(source)
            session.flush()
        
        return source
    
    def update_source_stats(
        self,
        session: Session,
        name: str,
        listings_count: int = 0,
        success: bool = True
    ) -> None:
        """Update source statistics after crawl."""
        source = session.query(Source).filter(Source.name == name).first()
        if not source:
            return
        
        now = datetime.utcnow()
        source.last_crawl_at = now
        source.total_crawls += 1
        
        if success:
            source.last_success_at = now
            source.total_listings += listings_count
            
            if source.total_crawls > 0:
                source.avg_listings_per_crawl = (
                    source.total_listings / source.total_crawls
                )
            
            # Update success rate (exponential moving average)
            alpha = 0.1
            source.success_rate = alpha * 1.0 + (1 - alpha) * source.success_rate
        else:
            source.success_rate = 0.1 * 0.0 + 0.9 * source.success_rate
    
    def get_active_listing_for_incremental(
        self,
        session: Session,
        url_hash: str,
        source: str
    ) -> Optional[Listing]:
        """Get the latest active listing for incremental comparison."""
        return session.query(Listing).filter(
            and_(
                Listing.url_hash == url_hash,
                Listing.source == source,
                Listing.is_latest == True,
            )
        ).first()
    
    def mark_listings_inactive(
        self,
        session: Session,
        source: str,
        city: str,
        listing_type: str,
        stale_days: int = 7
    ) -> int:
        """Mark listings not seen for N days as inactive. Returns count."""
        cutoff = datetime.utcnow() - timedelta(days=stale_days)
        
        count = session.query(Listing).filter(
            and_(
                Listing.source == source,
                Listing.city == city,
                Listing.listing_type == listing_type,
                Listing.is_latest == True,
                Listing.is_active == True,
                Listing.last_seen < cutoff,
            )
        ).update({"is_active": False}, synchronize_session=False)
        
        return count
    
    # =========================================================================
    # Statistics
    # =========================================================================
    
    def get_daily_stats(self, session: Session, date: datetime = None) -> Dict[str, Any]:
        """Get crawl statistics for a day."""
        if date is None:
            date = datetime.utcnow()
        
        start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        
        listings_count = session.query(func.count(Listing.id)).filter(
            and_(Listing.scraped_at >= start, Listing.scraped_at < end)
        ).scalar()
        
        properties_count = session.query(func.count(Property.id)).filter(
            and_(Property.first_seen >= start, Property.first_seen < end)
        ).scalar()
        
        events_count = session.query(func.count(PropertyEvent.id)).filter(
            and_(PropertyEvent.timestamp >= start, PropertyEvent.timestamp < end)
        ).scalar()
        
        return {
            "date": start.date().isoformat(),
            "listings_scraped": listings_count,
            "new_properties": properties_count,
            "events_recorded": events_count,
        }
    
    def close(self) -> None:
        """Close database connection."""
        if self._engine:
            self._engine.dispose()
            self._engine = None
            self._session_factory = None
            logger.info("Database connection closed")


# Convenience function for getting sessions
def get_db_session() -> Session:
    """Get a database session."""
    db = DatabaseManager()
    return db.get_session()
