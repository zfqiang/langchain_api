import argparse
import asyncio
from getpass import getpass

from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.models import Base, User
from app.db.session import create_database
from app.schemas import CreateUserRequest, Role


async def init_database() -> None:
    settings = get_settings()
    engine, _ = create_database(settings.database_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()
    print("Database tables created.")


async def create_user(data: CreateUserRequest) -> None:
    settings = get_settings()
    engine, session_factory = create_database(settings.database_url)
    try:
        async with session_factory() as session:
            existing_user = await session.scalar(
                select(User).where(User.username == data.username)
            )
            if existing_user is not None:
                raise ValueError(f"Username {data.username!r} already exists.")
            session.add(
                User(
                    username=data.username,
                    hashed_password=hash_password(data.password),
                    role=data.role.value,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()
    print(f"Created {data.role.value} user {data.username!r}.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Order Analytics administration")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="Create the template tables if absent")
    user_parser = commands.add_parser("create-user", help="Create an application user")
    user_parser.add_argument("--username", required=True)
    user_parser.add_argument(
        "--role", choices=[role.value for role in Role], required=True
    )
    args = parser.parse_args()

    if args.command == "init-db":
        asyncio.run(init_database())
        return

    password = getpass("Password (12+ characters): ")
    if len(password) < 12:
        parser.error("Password must contain at least 12 characters.")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        parser.error("Passwords do not match.")
    try:
        user_data = CreateUserRequest(
            username=args.username,
            password=password,
            role=Role(args.role),
        )
    except ValidationError as exc:
        parser.error(str(exc))
    asyncio.run(create_user(user_data))


if __name__ == "__main__":
    main()
