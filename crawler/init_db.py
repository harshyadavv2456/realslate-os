#!/usr/bin/env python3
"""
RealSlate Core - Database Initialization
Creates PostgreSQL schema and initial data.

Usage:
    python init_db.py              # Create tables
    python init_db.py --drop       # Drop and recreate tables (DANGEROUS!)
    python init_db.py --check      # Check database connection
    python init_db.py --migrate    # Run migrations (if any)
"""

import os
import sys
import argparse
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from loguru import logger

from core.utils import setup_logging, get_config
from core.db import DatabaseManager, Base


def check_connection() -> bool:
    """Check database connection."""
    logger.info("Checking database connection...")
    
    config = get_config()
    db_config = config.get("database", {})
    
    logger.info(f"Host: {db_config.get('host', 'localhost')}")
    logger.info(f"Port: {db_config.get('port', 5432)}")
    logger.info(f"Database: {db_config.get('name', 'realslate')}")
    logger.info(f"User: {db_config.get('user', 'realslate')}")
    
    try:
        db_manager = DatabaseManager()
        db_manager.initialize()
        
        from sqlalchemy import text
        with db_manager.session() as session:
            result = session.execute(text("SELECT 1"))
            logger.info(f"Database query test: OK")
        
        logger.info("Database connection: SUCCESS")
        return True
    
    except Exception as e:
        logger.error(f"Database connection: FAILED")
        logger.error(f"Error: {e}")
        return False


def create_tables(drop_first: bool = False) -> bool:
    """Create database tables."""
    logger.info("Initializing database tables...")
    
    try:
        db_manager = DatabaseManager()
        db_manager.initialize()
        
        if drop_first:
            logger.warning("DROPPING ALL TABLES!")
            confirm = input("Are you sure you want to drop all tables? (yes/no): ")
            
            if confirm.lower() != "yes":
                logger.info("Aborted.")
                return False
            
            db_manager.drop_tables()
            logger.info("Tables dropped.")
        
        # Create tables
        db_manager.create_tables()
        logger.info("Tables created successfully.")
        
        # Verify tables
        verify_tables(db_manager)
        
        return True
    
    except Exception as e:
        logger.error(f"Failed to create tables: {e}")
        return False


def verify_tables(db_manager: DatabaseManager) -> None:
    """Verify all tables exist."""
    from sqlalchemy import text, inspect as sa_inspect
    
    logger.info("Verifying tables...")
    
    expected_tables = [
        "raw_pages",
        "listings",
        "properties",
        "property_events",
        "crawl_sessions",
        "crawl_urls",
        "crawl_state",
        "sources",
    ]
    
    inspector = sa_inspect(db_manager._engine)
    existing_tables = set(inspector.get_table_names())
    
    for table in expected_tables:
        if table in existing_tables:
            logger.info(f"  {table}: OK")
        else:
            logger.warning(f"  {table}: MISSING")


def create_indexes(db_manager: DatabaseManager) -> None:
    """Create additional indexes for performance."""
    from sqlalchemy import text
    
    logger.info("Creating additional indexes...")
    
    indexes = [
        # Listings
        "CREATE INDEX IF NOT EXISTS idx_listings_price ON listings (price)",
        "CREATE INDEX IF NOT EXISTS idx_listings_area ON listings (area)",
        "CREATE INDEX IF NOT EXISTS idx_listings_beds ON listings (beds)",
        
        # Properties
        "CREATE INDEX IF NOT EXISTS idx_properties_price ON properties (current_price)",
    ]
    
    with db_manager.session() as session:
        for idx_sql in indexes:
            try:
                session.execute(text(idx_sql))
                logger.debug(f"Index created: {idx_sql[:50]}...")
            except Exception as e:
                logger.warning(f"Index creation failed: {e}")
    
    logger.info("Indexes created.")


def show_stats(db_manager: DatabaseManager) -> None:
    """Show database statistics."""
    from sqlalchemy import text
    
    logger.info("\nDatabase Statistics:")
    logger.info("-" * 40)
    
    # Use an allow-list of known table names to avoid SQL injection
    tables = [
        ("raw_pages", "Raw Pages"),
        ("listings", "Listings"),
        ("properties", "Properties"),
        ("property_events", "Events"),
        ("crawl_sessions", "Sessions"),
        ("crawl_urls", "URLs"),
        ("crawl_state", "Crawl State"),
        ("sources", "Sources"),
    ]
    
    with db_manager.session() as session:
        for table, label in tables:
            try:
                result = session.execute(text(f"SELECT COUNT(*) FROM \"{table}\""))
                count = result.scalar()
                logger.info(f"  {label}: {count:,} rows")
            except Exception as e:
                logger.warning(f"  {label}: Error - {e}")
    
    # Show disk usage (SQLite)
    try:
        import os
        config = get_config()
        db_path = config.get("database", {}).get("path", "realslate.db")
        if os.path.exists(db_path):
            size_mb = os.path.getsize(db_path) / (1024 * 1024)
            logger.info(f"\n  DB File Size: {size_mb:.2f} MB")
    except Exception:
        pass


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="RealSlate Database Initialization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python init_db.py              # Create tables
    python init_db.py --check      # Check connection
    python init_db.py --drop       # Drop and recreate (DANGER!)
    python init_db.py --stats      # Show statistics

Environment Variables:
    DB_HOST     - Database host (default: localhost)
    DB_PORT     - Database port (default: 5432)
    DB_NAME     - Database name (default: realslate)
    DB_USER     - Database user (default: realslate)
    DB_PASSWORD - Database password
        """
    )
    
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check database connection"
    )
    parser.add_argument(
        "--drop",
        action="store_true",
        help="Drop all tables before creating (DANGEROUS!)"
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show database statistics"
    )
    parser.add_argument(
        "--indexes",
        action="store_true",
        help="Create additional indexes"
    )
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(level="INFO")
    
    if args.check:
        success = check_connection()
        sys.exit(0 if success else 1)
    
    if args.stats:
        try:
            db_manager = DatabaseManager()
            db_manager.initialize()
            show_stats(db_manager)
        except Exception as e:
            logger.error(f"Error: {e}")
            sys.exit(1)
        sys.exit(0)
    
    if args.indexes:
        try:
            db_manager = DatabaseManager()
            db_manager.initialize()
            create_indexes(db_manager)
        except Exception as e:
            logger.error(f"Error: {e}")
            sys.exit(1)
        sys.exit(0)
    
    # Default: create tables
    success = create_tables(drop_first=args.drop)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
