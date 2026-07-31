import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  Check,
  Loader2,
  Pencil,
  Plus,
  Save,
  Trash2,
  X,
} from "lucide-react";
import {
  type UseUserApi,
  useUser,
} from "@api/todo/v1/todo_rbt_react";
import css from "./App.module.css";

type DashboardResult = NonNullable<
  ReturnType<UseUserApi["useDashboard"]>["response"]
>;
type TaskSummary = DashboardResult["tasks"][number];
type Filter = "open" | "all" | "done";
type AbortedLike = { error: { type: string }; message: string };

function friendlyError(aborted: AbortedLike): string {
  switch (aborted.error.type) {
    case "PermissionDenied":
      return "No access to that task.";
    case "NotFound":
      return "Task not found.";
    default:
      return aborted.message || "Something went wrong.";
  }
}

export function TodoMcpApp() {
  const { user, isLoading } = useUser();

  if (isLoading) {
    return (
      <main className={css.center}>
        <Loader2 className={css.spin} size={20} />
      </main>
    );
  }

  if (user === undefined) {
    return (
      <main className={css.center}>
        <span>Sign in required</span>
      </main>
    );
  }

  return <TodoBoard user={user} />;
}

function TodoBoard({ user }: { user: UseUserApi }) {
  const [filter, setFilter] = useState<Filter>("open");
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const { response, isLoading, aborted } = user.useDashboard({
    cursor: "",
    limit: 100,
  });

  const tasks = response?.tasks ?? [];
  const visibleTasks = useMemo(() => {
    if (filter === "open") return tasks.filter((task) => !task.completed);
    if (filter === "done") return tasks.filter((task) => task.completed);
    return tasks;
  }, [filter, tasks]);

  useEffect(() => {
    if (aborted !== undefined) setError(friendlyError(aborted));
  }, [aborted]);

  async function createTask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (title.trim() === "" && notes.trim() === "") return;
    setIsCreating(true);
    setError("");
    const result = await user.createTask({ title, notes });
    setIsCreating(false);
    if (result.aborted !== undefined) {
      setError(friendlyError(result.aborted));
      return;
    }
    setTitle("");
    setNotes("");
  }

  return (
    <main className={css.shell}>
      <header className={css.header}>
        <div>
          <h1>Todo Board</h1>
          <p>
            {tasks.filter((task) => !task.completed).length} open /{" "}
            {tasks.filter((task) => task.completed).length} done
          </p>
        </div>
        {isLoading && <Loader2 className={css.spin} size={16} />}
      </header>

      <form className={css.quickAdd} onSubmit={createTask}>
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="Add a task"
          maxLength={120}
        />
        <textarea
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          placeholder="Notes"
          rows={2}
        />
        <button disabled={isCreating}>
          {isCreating ? (
            <Loader2 className={css.spin} size={16} />
          ) : (
            <Plus size={16} />
          )}
          Add
        </button>
      </form>

      <div className={css.segmented} aria-label="Task filter">
        <button
          className={filter === "open" ? css.active : ""}
          onClick={() => setFilter("open")}
        >
          Open
        </button>
        <button
          className={filter === "all" ? css.active : ""}
          onClick={() => setFilter("all")}
        >
          All
        </button>
        <button
          className={filter === "done" ? css.active : ""}
          onClick={() => setFilter("done")}
        >
          Done
        </button>
      </div>

      {error !== "" && (
        <div className={css.error} role="alert">
          {error}
        </div>
      )}

      <section className={css.list}>
        {visibleTasks.map((task) => (
          <TaskRow key={task.taskId} task={task} user={user} onError={setError} />
        ))}
        {visibleTasks.length === 0 && (
          <div className={css.empty}>
            <Check size={20} />
            <span>No tasks here</span>
          </div>
        )}
      </section>
    </main>
  );
}

function TaskRow({
  task,
  user,
  onError,
}: {
  task: TaskSummary;
  user: UseUserApi;
  onError: (message: string) => void;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [isPending, setIsPending] = useState(false);
  const [draftTitle, setDraftTitle] = useState(task.title);
  const [draftNotes, setDraftNotes] = useState(task.notes);

  useEffect(() => {
    if (!isEditing) {
      setDraftTitle(task.title);
      setDraftNotes(task.notes);
    }
  }, [isEditing, task.notes, task.title]);

  async function runMutation(action: () => Promise<{ aborted?: AbortedLike }>) {
    setIsPending(true);
    onError("");
    const result = await action();
    setIsPending(false);
    if (result.aborted !== undefined) onError(friendlyError(result.aborted));
    return result.aborted === undefined;
  }

  async function saveTask() {
    const ok = await runMutation(() =>
      user.updateTask({
        taskId: task.taskId,
        title: draftTitle,
        notes: draftNotes,
        completed: task.completed,
      }),
    );
    if (ok) setIsEditing(false);
  }

  return (
    <article className={`${css.task} ${task.completed ? css.completed : ""}`}>
      <label className={css.checkbox} title="Toggle completion">
        <input
          type="checkbox"
          checked={task.completed}
          disabled={isPending}
          onChange={(event) =>
            void runMutation(() =>
              user.setTaskCompleted({
                taskId: task.taskId,
                completed: event.target.checked,
              }),
            )
          }
        />
      </label>
      <div className={css.taskBody}>
        {isEditing ? (
          <div className={css.editFields}>
            <input
              value={draftTitle}
              onChange={(event) => setDraftTitle(event.target.value)}
              aria-label="Task title"
            />
            <textarea
              value={draftNotes}
              onChange={(event) => setDraftNotes(event.target.value)}
              rows={2}
              aria-label="Task notes"
            />
          </div>
        ) : (
          <>
            <h2>{task.title}</h2>
            {task.notes !== "" && <p>{task.notes}</p>}
          </>
        )}
      </div>
      <div className={css.actions}>
        {isEditing ? (
          <>
            <button
              title="Save"
              aria-label="Save"
              disabled={isPending}
              onClick={() => void saveTask()}
            >
              <Save size={15} />
            </button>
            <button
              title="Cancel"
              aria-label="Cancel"
              disabled={isPending}
              onClick={() => setIsEditing(false)}
            >
              <X size={15} />
            </button>
          </>
        ) : (
          <button
            title="Edit"
            aria-label="Edit"
            disabled={isPending}
            onClick={() => setIsEditing(true)}
          >
            <Pencil size={15} />
          </button>
        )}
        <button
          className={css.danger}
          title="Delete"
          aria-label="Delete"
          disabled={isPending}
          onClick={() =>
            void runMutation(() => user.deleteTask({ taskId: task.taskId }))
          }
        >
          {isPending ? (
            <Loader2 className={css.spin} size={15} />
          ) : (
            <Trash2 size={15} />
          )}
        </button>
      </div>
    </article>
  );
}
