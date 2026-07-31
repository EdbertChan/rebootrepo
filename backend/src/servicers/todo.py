from typing import Any
from uuid import uuid4

import rbt.v1alpha1.errors_pb2 as errors
from reboot.aio.auth.authorizers import (
    Authorizer,
    AuthorizerRule,
    allow_if,
    is_app_internal,
)
from reboot.aio.contexts import ReaderContext, TransactionContext, WriterContext
from reboot.std.collections.ordered_map.v1.ordered_map import OrderedMap
from todo.v1.todo import TaskState, TaskSummary
from todo.v1.todo_rbt import Task, User
from uuid7 import create as uuid7


PAGE_LIMIT_DEFAULT = 50
PAGE_LIMIT_MAX = 100


def _normalized_title(title: str) -> str:
    return title.strip() or "Untitled task"


def _normalized_notes(notes: str) -> str:
    return notes.strip()


def _is_task_owner(
    *,
    context: ReaderContext,
    state: TaskState | None = None,
    **kwargs: Any,
) -> Authorizer.Decision:
    if context.auth is None or context.auth.user_id is None:
        return errors.Unauthenticated()
    if state is not None and state.owner_user_id == context.auth.user_id:
        return errors.Ok()
    return errors.PermissionDenied()


def _task_authorizer() -> AuthorizerRule[TaskState, Any]:
    return allow_if(any=[is_app_internal, _is_task_owner])


class UserServicer(User.Servicer):
    async def create(self, context: TransactionContext) -> None:
        if context.constructor:
            self.state.tasks_index_id = str(uuid4())
            await OrderedMap.ref(self.state.tasks_index_id).create(context)

    async def dashboard(
        self,
        context: ReaderContext,
        request: User.DashboardRequest,
    ) -> User.DashboardResponse:
        limit = request.limit or PAGE_LIMIT_DEFAULT
        limit = max(1, min(limit, PAGE_LIMIT_MAX))

        tasks_index = OrderedMap.ref(self.state.tasks_index_id)
        if request.cursor:
            page = await tasks_index.range(
                context,
                start_key=request.cursor,
                limit=limit + 1,
            )
        else:
            page = await tasks_index.range(context, limit=limit + 1)

        entries = list(page.entries)
        if request.cursor and entries and entries[0].key == request.cursor:
            entries = entries[1:]

        user_id = self.ref().state_id
        tasks: list[TaskSummary] = []
        for entry in entries[:limit]:
            task_id = entry.bytes.decode()
            task = await Task.ref(task_id).get(context)
            if task.deleted or task.owner_user_id != user_id:
                continue
            tasks.append(
                TaskSummary(
                    task_id=task.task_id,
                    title=task.title,
                    notes=task.notes,
                    completed=task.completed,
                )
            )

        next_cursor = ""
        if len(entries) > limit and entries[limit - 1:limit]:
            next_cursor = entries[limit - 1].key

        return User.DashboardResponse(tasks=tasks, next_cursor=next_cursor)

    async def create_task(
        self,
        context: TransactionContext,
        request: User.CreateTaskRequest,
    ) -> User.CreateTaskResponse:
        task_id = str(uuid4())
        index_key = str(uuid7())
        title = _normalized_title(request.title)
        notes = _normalized_notes(request.notes)

        task, _ = await Task.create(
            context,
            task_id,
            owner_user_id=self.ref().state_id,
            index_key=index_key,
            title=title,
            notes=notes,
        )
        await OrderedMap.ref(self.state.tasks_index_id).insert(
            context,
            key=index_key,
            bytes=task.state_id.encode(),
        )
        return User.CreateTaskResponse(task_id=task.state_id)

    async def update_task(
        self,
        context: TransactionContext,
        request: User.UpdateTaskRequest,
    ) -> None:
        task = await self._task_for_request(
            context,
            request.task_id,
            User.UpdateTaskAborted,
        )
        await Task.ref(task.task_id).update(
            context,
            title=_normalized_title(request.title),
            notes=_normalized_notes(request.notes),
            completed=request.completed,
        )

    async def set_task_completed(
        self,
        context: TransactionContext,
        request: User.SetTaskCompletedRequest,
    ) -> None:
        task = await self._task_for_request(
            context,
            request.task_id,
            User.SetTaskCompletedAborted,
        )
        await Task.ref(task.task_id).set_completed(
            context,
            completed=request.completed,
        )

    async def delete_task(
        self,
        context: TransactionContext,
        request: User.DeleteTaskRequest,
    ) -> None:
        task = await self._task_for_request(
            context,
            request.task_id,
            User.DeleteTaskAborted,
        )
        await Task.ref(task.task_id).mark_deleted(context)
        await OrderedMap.ref(self.state.tasks_index_id).remove(
            context,
            key=task.index_key,
        )

    async def _task_for_request(
        self,
        context: TransactionContext,
        task_id: str,
        aborted_type: type[Exception],
    ) -> Task.GetResponse:
        if task_id == "":
            raise aborted_type(errors.NotFound())
        task = await Task.ref(task_id).get(context)
        if task.deleted:
            raise aborted_type(errors.NotFound())
        if task.owner_user_id != self.ref().state_id:
            raise aborted_type(errors.PermissionDenied())
        return task


class TaskServicer(Task.Servicer):
    def authorizer(self) -> AuthorizerRule[TaskState, Any]:
        return _task_authorizer()

    async def create(
        self,
        context: WriterContext,
        request: Task.CreateRequest,
    ) -> None:
        if context.constructor:
            self.state.owner_user_id = request.owner_user_id
            self.state.index_key = request.index_key
            self.state.title = _normalized_title(request.title)
            self.state.notes = _normalized_notes(request.notes)
            self.state.completed = False
            self.state.deleted = False

    async def get(self, context: ReaderContext) -> Task.GetResponse:
        return Task.GetResponse(
            task_id=self.ref().state_id,
            owner_user_id=self.state.owner_user_id,
            index_key=self.state.index_key,
            title=self.state.title,
            notes=self.state.notes,
            completed=self.state.completed,
            deleted=self.state.deleted,
        )

    async def update(
        self,
        context: WriterContext,
        request: Task.UpdateRequest,
    ) -> None:
        self.state.title = _normalized_title(request.title)
        self.state.notes = _normalized_notes(request.notes)
        self.state.completed = request.completed

    async def set_completed(
        self,
        context: WriterContext,
        request: Task.SetCompletedRequest,
    ) -> None:
        self.state.completed = request.completed

    async def mark_deleted(self, context: WriterContext) -> None:
        self.state.deleted = True
