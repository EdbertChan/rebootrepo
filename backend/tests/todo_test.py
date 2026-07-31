import unittest

from reboot.aio.aborted import Aborted
from reboot.aio.applications import Application
from reboot.aio.tests import Reboot
from servicers.todo import TaskServicer, UserServicer
from todo.v1.todo_rbt import User


class TodoTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.rbt = Reboot()
        await self.rbt.start()
        await self.rbt.up(
            Application(
                servicers=[UserServicer, TaskServicer],
            ),
        )
        self.user_id = f"alice-{self.id()}"
        self.context = await self.rbt.create_external_context_as(
            name=f"alice-{self.id()}",
            user_id=self.user_id,
        )

    async def asyncTearDown(self) -> None:
        await self.rbt.stop()

    async def test_create_update_complete_and_delete_task(self) -> None:
        user = User.ref(self.user_id)

        empty = await user.dashboard(self.context, cursor="", limit=100)
        self.assertEqual(empty.tasks, [])

        created = await user.create_task(
            self.context,
            title="Book venue",
            notes="Call the event space",
        )

        dashboard = await user.dashboard(self.context, cursor="", limit=100)
        self.assertEqual(len(dashboard.tasks), 1)
        self.assertEqual(dashboard.tasks[0].task_id, created.task_id)
        self.assertEqual(dashboard.tasks[0].title, "Book venue")
        self.assertFalse(dashboard.tasks[0].completed)

        await user.set_task_completed(
            self.context,
            task_id=created.task_id,
            completed=True,
        )
        completed = await user.dashboard(self.context, cursor="", limit=100)
        self.assertTrue(completed.tasks[0].completed)

        await user.update_task(
            self.context,
            task_id=created.task_id,
            title="Book backup venue",
            notes="Confirm capacity",
            completed=False,
        )
        updated = await user.dashboard(self.context, cursor="", limit=100)
        self.assertEqual(updated.tasks[0].title, "Book backup venue")
        self.assertEqual(updated.tasks[0].notes, "Confirm capacity")
        self.assertFalse(updated.tasks[0].completed)

        await user.delete_task(self.context, task_id=created.task_id)
        after_delete = await user.dashboard(self.context, cursor="", limit=100)
        self.assertEqual(after_delete.tasks, [])

    async def test_other_user_cannot_modify_task_by_id(self) -> None:
        alice = User.ref(self.user_id)
        created = await alice.create_task(
            self.context,
            title="Private task",
            notes="Only Alice can edit this",
        )

        bob_id = f"bob-{self.id()}"
        bob_context = await self.rbt.create_external_context_as(
            name=f"bob-{self.id()}",
            user_id=bob_id,
        )
        bob = User.ref(bob_id)

        with self.assertRaises(Aborted):
            await bob.set_task_completed(
                bob_context,
                task_id=created.task_id,
                completed=True,
            )

        alice_dashboard = await alice.dashboard(
            self.context,
            cursor="",
            limit=100,
        )
        self.assertEqual(len(alice_dashboard.tasks), 1)
        self.assertFalse(alice_dashboard.tasks[0].completed)

        bob_dashboard = await bob.dashboard(bob_context, cursor="", limit=100)
        self.assertEqual(bob_dashboard.tasks, [])
