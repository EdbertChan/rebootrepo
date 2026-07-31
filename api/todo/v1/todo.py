from reboot.api import (
    API,
    Field,
    Methods,
    Model,
    Reader,
    Tool,
    Transaction,
    Type,
    UI,
    Writer,
)


class UserState(Model):
    tasks_index_id: str = Field(tag=1, default="")


class TaskSummary(Model):
    task_id: str = Field(tag=1, default="")
    title: str = Field(tag=2, default="")
    notes: str = Field(tag=3, default="")
    completed: bool = Field(tag=4, default=False)


class DashboardRequest(Model):
    cursor: str = Field(tag=1, default="")
    limit: int = Field(tag=2, default=0)


class DashboardResponse(Model):
    tasks: list[TaskSummary] = Field(tag=1, default_factory=list)
    next_cursor: str = Field(tag=2, default="")


class CreateTaskRequest(Model):
    title: str = Field(tag=1, default="")
    notes: str = Field(tag=2, default="")


class CreateTaskResponse(Model):
    task_id: str = Field(tag=1, default="")


class UpdateTaskRequest(Model):
    task_id: str = Field(tag=1, default="")
    title: str = Field(tag=2, default="")
    notes: str = Field(tag=3, default="")
    completed: bool = Field(tag=4, default=False)


class SetTaskCompletedRequest(Model):
    task_id: str = Field(tag=1, default="")
    completed: bool = Field(tag=2, default=False)


class DeleteTaskRequest(Model):
    task_id: str = Field(tag=1, default="")


class TaskState(Model):
    owner_user_id: str = Field(tag=1, default="")
    index_key: str = Field(tag=2, default="")
    title: str = Field(tag=3, default="")
    notes: str = Field(tag=4, default="")
    completed: bool = Field(tag=5, default=False)
    deleted: bool = Field(tag=6, default=False)


class TaskCreateRequest(Model):
    owner_user_id: str = Field(tag=1, default="")
    index_key: str = Field(tag=2, default="")
    title: str = Field(tag=3, default="")
    notes: str = Field(tag=4, default="")


class TaskRecord(Model):
    task_id: str = Field(tag=1, default="")
    owner_user_id: str = Field(tag=2, default="")
    index_key: str = Field(tag=3, default="")
    title: str = Field(tag=4, default="")
    notes: str = Field(tag=5, default="")
    completed: bool = Field(tag=6, default=False)
    deleted: bool = Field(tag=7, default=False)


class TaskUpdateRequest(Model):
    title: str = Field(tag=1, default="")
    notes: str = Field(tag=2, default="")
    completed: bool = Field(tag=3, default=False)


class TaskSetCompletedRequest(Model):
    completed: bool = Field(tag=1, default=False)


UserMethods = Methods(
    show=UI(
        request=None,
        path="frontend/mcp/todos",
        title="Todo Board",
        description="Interactive todo board for the signed-in user.",
    ),
    dashboard=Reader(
        request=DashboardRequest,
        response=DashboardResponse,
        description="List the signed-in user's tasks.",
        mcp=Tool(title="List tasks"),
    ),
    create_task=Transaction(
        request=CreateTaskRequest,
        response=CreateTaskResponse,
        description="Create a task for the signed-in user.",
        mcp=Tool(title="Create task"),
    ),
    update_task=Transaction(
        request=UpdateTaskRequest,
        response=None,
        description="Update a task's title, notes, and completion state.",
        mcp=Tool(title="Update task"),
    ),
    set_task_completed=Transaction(
        request=SetTaskCompletedRequest,
        response=None,
        description="Mark a task complete or incomplete.",
        mcp=Tool(title="Set task completion"),
    ),
    delete_task=Transaction(
        request=DeleteTaskRequest,
        response=None,
        description="Delete one of the signed-in user's tasks.",
        mcp=Tool(title="Delete task"),
    ),
)


TaskMethods = Methods(
    create=Writer(
        request=TaskCreateRequest,
        response=None,
        factory=True,
        mcp=None,
    ),
    get=Reader(
        request=None,
        response=TaskRecord,
        mcp=None,
    ),
    update=Writer(
        request=TaskUpdateRequest,
        response=None,
        mcp=None,
    ),
    set_completed=Writer(
        request=TaskSetCompletedRequest,
        response=None,
        mcp=None,
    ),
    mark_deleted=Writer(
        request=None,
        response=None,
        mcp=None,
    ),
)


api = API(
    User=Type(
        state=UserState,
        methods=UserMethods,
    ),
    Task=Type(
        state=TaskState,
        methods=TaskMethods,
    ),
)
