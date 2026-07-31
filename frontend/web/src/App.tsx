import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useSignIn, useSignOut } from "@reboot-dev/reboot-react";
import {
  Check,
  Loader2,
  LogOut,
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

type DashboardResult = NonNullable<
  ReturnType<UseUserApi["useDashboard"]>["response"]
>;
type TaskSummary = DashboardResult["tasks"][number];
type Filter = "open" | "all" | "done";
type AbortedLike = { error: { type: string }; message: string };

function friendlyError(aborted: AbortedLike): string {
  switch (aborted.error.type) {
    case "PermissionDenied":
      return "You do not have access to that task.";
    case "NotFound":
      return "That task is no longer available.";
    default:
      return aborted.message || "Something went wrong.";
  }
}

export function App() {
  const { user, isLoading } = useUser();
  const signIn = useSignIn();
  const signOut = useSignOut();

  if (isLoading) {
    return (
      <main className="center-screen">
        <Loader2 className="spin" size={22} />
      </main>
    );
  }

  if (user === undefined) {
    return (
      <main className="center-screen">
        <section className="signin-panel">
          <h1>Todo Board</h1>
          <button className="primary-button" onClick={() => signIn()}>
            Sign in
          </button>
        </section>
      </main>
    );
  }

  return <TodoWorkspace user={user} onSignOut={() => signOut()} />;
}

function TodoWorkspace({
  user,
  onSignOut,
}: {
  user: UseUserApi;
  onSignOut: () => void;
}) {
  const [filter, setFilter] = useState<Filter>("open");
  const [error, setError] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const { response, isLoading, aborted } = user.useDashboard({
    cursor: "",
    limit: 100,
  });

  const tasks = response?.tasks ?? [];
  const openCount = tasks.filter((task) => !task.completed).length;
  const doneCount = tasks.length - openCount;
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
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Personal tasks</p>
          <h1>Todo Board</h1>
        </div>
        <button className="icon-text-button" onClick={onSignOut}>
          <LogOut size={16} />
          Sign out
        </button>
      </header>

      <section className="workspace">
        <aside className="task-form-panel">
          <form onSubmit={createTask} className="task-form">
            <label>
              <span>Title</span>
              <input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="Confirm venue"
                maxLength={120}
              />
            </label>
            <label>
              <span>Notes</span>
              <textarea
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                placeholder="Email before noon"
                rows={5}
              />
            </label>
            <button className="primary-button" disabled={isCreating}>
              {isCreating ? (
                <Loader2 className="spin" size={16} />
              ) : (
                <Plus size={16} />
              )}
              Add task
            </button>
          </form>

          <div className="summary-strip" aria-label="Task summary">
            <span>{openCount} open</span>
            <span>{doneCount} done</span>
          </div>
        </aside>

        <section className="task-list-panel">
          <div className="list-toolbar">
            <div className="segmented" aria-label="Task filter">
              <button
                className={filter === "open" ? "active" : ""}
                onClick={() => setFilter("open")}
              >
                Open
              </button>
              <button
                className={filter === "all" ? "active" : ""}
                onClick={() => setFilter("all")}
              >
                All
              </button>
              <button
                className={filter === "done" ? "active" : ""}
                onClick={() => setFilter("done")}
              >
                Done
              </button>
            </div>
            {isLoading && <Loader2 className="spin muted-icon" size={16} />}
          </div>

          {error !== "" && (
            <div className="error-bar" role="alert">
              {error}
            </div>
          )}

          <div className="task-list">
            {visibleTasks.map((task) => (
              <TaskRow
                key={task.taskId}
                task={task}
                user={user}
                onError={setError}
              />
            ))}
            {visibleTasks.length === 0 && (
              <div className="empty-state">
                <Check size={22} />
                <span>No tasks here</span>
              </div>
            )}
          </div>
        </section>
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
    <article className={`task-row ${task.completed ? "completed" : ""}`}>
      <label className="checkbox-cell" title="Toggle completion">
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

      <div className="task-main">
        {isEditing ? (
          <div className="edit-fields">
            <input
              value={draftTitle}
              onChange={(event) => setDraftTitle(event.target.value)}
              maxLength={120}
              aria-label="Task title"
            />
            <textarea
              value={draftNotes}
              onChange={(event) => setDraftNotes(event.target.value)}
              rows={3}
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

      <div className="task-actions">
        {isEditing ? (
          <>
            <button
              className="icon-button"
              title="Save"
              aria-label="Save"
              disabled={isPending}
              onClick={() => void saveTask()}
            >
              <Save size={16} />
            </button>
            <button
              className="icon-button"
              title="Cancel"
              aria-label="Cancel"
              disabled={isPending}
              onClick={() => setIsEditing(false)}
            >
              <X size={16} />
            </button>
          </>
        ) : (
          <button
            className="icon-button"
            title="Edit"
            aria-label="Edit"
            disabled={isPending}
            onClick={() => setIsEditing(true)}
          >
            <Pencil size={16} />
          </button>
        )}
        <button
          className="icon-button danger"
          title="Delete"
          aria-label="Delete"
          disabled={isPending}
          onClick={() =>
            void runMutation(() => user.deleteTask({ taskId: task.taskId }))
          }
        >
          {isPending ? <Loader2 className="spin" size={16} /> : <Trash2 size={16} />}
        </button>
      </div>
    </article>
  );
}
