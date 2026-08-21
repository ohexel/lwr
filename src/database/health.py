import json
from src.database.connection import database_connection, database_health

def main() -> None:
    with database_connection(
        application_name="capstone_database_health"
    ) as connection:
        health = database_health(connection)

    print(json.dumps(health, indent=2, sort_keys=True))

if __name__ == "__main__":
    main()
