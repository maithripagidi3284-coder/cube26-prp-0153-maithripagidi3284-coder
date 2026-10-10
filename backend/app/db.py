from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app import config


connect_args = (
    {"check_same_thread": False}
    if config.DATABASE_URL.startswith("sqlite")
    else {}
)

engine_options = {"connect_args": connect_args}

# Handle stale PostgreSQL connections in production.
if not config.DATABASE_URL.startswith("sqlite"):
    engine_options.update(
        pool_pre_ping=True,
        pool_recycle=300,
    )

engine = create_engine(config.DATABASE_URL, **engine_options)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app import models  # noqa: F401  (register models on Base.metadata)
    Base.metadata.create_all(bind=engine)
