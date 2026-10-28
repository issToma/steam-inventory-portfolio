from invinv.db.migrate import upgrade_database


def test_upgrade_on_start_finds_the_migrations(database_url: str) -> None:
    # The test database is already at head: this checks the migrations are found and run.
    upgrade_database()
