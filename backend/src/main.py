import asyncio
import logging

from example_prompts import example_prompts
from reboot.aio.applications import Application
from reboot.aio.auth.oauth_providers import (
    Development,
    OAuthProviderByEnvironment,
)
from servicers.todo import TaskServicer, UserServicer


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


async def main() -> None:
    application = Application(
        title="Todo Board",
        description="Create, organize, complete, and delete personal tasks.",
        servicers=[UserServicer, TaskServicer],
        oauth=OAuthProviderByEnvironment(
            dev=Development(),
            prod=None,
        ),
        example_prompts=example_prompts,
    )
    await application.run()


if __name__ == "__main__":
    asyncio.run(main())
