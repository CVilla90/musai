from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Import all models so autogenerate can see them
import musai.models  # noqa: F401
from sqlmodel import SQLModel
from musai.config import settings

alembic_config = context.config

if alembic_config.config_file_name is not None:
    fileConfig(alembic_config.config_file_name)

# Point Alembic at our models' metadata
target_metadata = SQLModel.metadata

# Override the sqlalchemy.url from alembic.ini with the live settings value
alembic_config.set_main_option("sqlalchemy.url", settings.database_url)


def run_migrations_offline() -> None:
    url = alembic_config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        alembic_config.get_section(alembic_config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
