"""Migrace musí odpovídat modelům: kdo změní model a zapomene na migraci, test spadne."""

from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask_migrate import upgrade

from bakix import create_app
from bakix.extensions import db

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def test_migrations_match_models(tmp_path):
    app = create_app("testing", {"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'm.db'}"})
    with app.app_context():
        upgrade(directory=str(MIGRATIONS))
        with db.engine.connect() as connection:
            diff = compare_metadata(MigrationContext.configure(connection), db.metadata)
        db.engine.dispose()
    assert diff == []
