from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from app.config.settings import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def add_missing_columns(base) -> None:
    """create_all() only creates missing tables, never missing columns. Add any
    model column that an existing table lacks so new fields don't need a manual
    migration or a fresh database."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                default = column.default.arg if column.default is not None and column.default.is_scalar else None
                ddl = f'ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}'
                if default is not None:
                    ddl += f" DEFAULT {default!r}" if isinstance(default, str) else f" DEFAULT {default}"
                conn.execute(text(ddl))
