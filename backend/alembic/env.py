"""
Alembic ka entry point.

Do kaam yahan hue hain jo default template me nahi hote:
  1. DB URL settings se aata hai (alembic.ini me password nahi likha)
  2. target_metadata set kiya hai, taki autogenerate models ko dekh sake
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from config import settings
from database import Base

# Ye import zaroori hai — bina iske Alembic ko models dikhte hi nahi
# aur wo "koi table nahi mili" wali khali migration bana deta hai.
import models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# alembic.ini ki khali URL ko yahan bhar rahe hain
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# Autogenerate isi metadata se compare karta hai
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """DB se connect kiye bina sirf SQL file banao (--sql flag ke liye)."""
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Normal case — DB se connect karke migrations chalao."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Column ka type badla to bhi detect karo (default off hota hai)
            compare_type=True,
            # Default value badla to bhi detect karo
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
