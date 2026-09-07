#!/usr/bin/env python3
"""
Pomodoro Timer + Task Manager
==============================

A single-file desktop app built with Tkinter (comes standard with Python,
no extra installs needed).

Everything lives on one scrollable page, top to bottom:

1. Pomodoro Timer
   - 25 min work session
   - 5 min short break
   - 10 min long break (automatically kicks in after every 4th work session)
   - Start / Pause / Reset controls
   - Session counter
   - Plays a sound and shows a self-dismissing notification in the
     bottom-right corner of the screen when a session ends

2. Tasks (just below the clock/controls)
   - Add tasks with: priority (0-100, 0 = most urgent), name,
     # of modules to complete, notes
   - Date Created is stamped automatically when a task is added
   - "Start" button stamps Date Started
   - "Complete" button stamps Date Finished and moves the task down into
     the Completed section
   - Delete button to remove a task
   - Double-click a task's Notes cell to edit notes

3. Completed (further down the same page)
   - Read-only list of finished tasks with priority, all their dates, and
     notes

Data is saved to `pomodoro_tasks.json` in the same folder as this script,
so your task list survives closing and reopening the app.

Run it with:
    python3 pomodoro_app.py
"""

import json
import os
import platform
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from datetime import date
import uuid

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pomodoro_tasks.json")

WORK_MINUTES = 25
SHORT_BREAK_MINUTES = 5
LONG_BREAK_MINUTES = 10
SESSIONS_BEFORE_LONG_BREAK = 4

PRIORITY_MIN = 0
PRIORITY_MAX = 100
DEFAULT_PRIORITY = 3

# "priority" is first, as requested
COLUMNS = ("priority", "name", "modules", "created", "started", "finished", "notes")
COLUMN_LABELS = {
    "priority": "Priority",
    "name": "Task Name",
    "modules": "# Modules",
    "created": "Date Created",
    "started": "Date Started",
    "finished": "Date Finished",
    "notes": "Notes",
}


# --------------------------------------------------------------------------
# Data persistence
# --------------------------------------------------------------------------
def load_tasks():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                tasks = json.load(f)
                # backfill / migrate priority for tasks saved before this field
                # existed, or saved with the old High/Medium/Low categories
                legacy_map = {"High": 3, "Medium": 8, "Low": 20}
                for t in tasks:
                    p = t.get("priority", DEFAULT_PRIORITY)
                    if isinstance(p, str):
                        p = legacy_map.get(p, DEFAULT_PRIORITY)
                    try:
                        p = int(p)
                    except (TypeError, ValueError):
                        p = DEFAULT_PRIORITY
                    t["priority"] = max(PRIORITY_MIN, min(PRIORITY_MAX, p))
                return tasks
        except (json.JSONDecodeError, OSError):
            return []
    return []


def save_tasks(tasks):
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(tasks, f, indent=2)
    except OSError as e:
        messagebox.showerror("Save error", f"Could not save tasks:\n{e}")


# --------------------------------------------------------------------------
# Alert sound (cross-platform, no extra packages required)
# --------------------------------------------------------------------------
def play_alert_sound():
    system = platform.system()
    try:
        if system == "Windows":
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        elif system == "Darwin":
            os.system("afplay /System/Library/Sounds/Glass.aiff > /dev/null 2>&1 &")
        else:  # Linux and other Unix-likes
            if os.system("command -v paplay > /dev/null 2>&1") == 0:
                os.system(
                    "paplay /usr/share/sounds/freedesktop/stereo/complete.oga > /dev/null 2>&1 &"
                )
            elif os.system("command -v aplay > /dev/null 2>&1") == 0:
                os.system("aplay /usr/share/sounds/alsa/Front_Center.wav > /dev/null 2>&1 &")
            else:
                print("\a", end="", flush=True)  # terminal bell as a last resort
    except Exception:
        pass  # never let a sound failure crash the timer


# --------------------------------------------------------------------------
# Main Application
# --------------------------------------------------------------------------
class PomodoroApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Pomodoro Timer & Task Manager")
        self.root.geometry("920x900")
        self.root.minsize(760, 560)

        self.tasks = load_tasks()  # list of dicts, saved to DATA_FILE on every change

        # --- Timer state ---
        self.mode = "work"  # 'work', 'short_break', 'long_break'
        self.remaining_seconds = WORK_MINUTES * 60
        self.running = False
        self.timer_job = None
        self.completed_work_sessions = 0

        self._build_ui()
        self._refresh_task_tables()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        # Scrollable container so the timer, tasks, and completed sections
        # can all live on one page even on smaller screens.
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, highlightthickness=0)
        vscroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vscroll.set)

        canvas.pack(side="left", fill="both", expand=True)
        vscroll.pack(side="right", fill="y")

        content = ttk.Frame(canvas)
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")

        def _on_content_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event):
            canvas.itemconfig(content_window, width=event.width)

        content.bind("<Configure>", _on_content_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)          # Windows / macOS
        canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))  # Linux
        canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))   # Linux

        # ---- Sections, stacked top to bottom on the single page ----
        self._build_timer_section(content)
        self._build_tasks_section(content)
        self._build_completed_section(content)

    # ---------------- Timer section ----------------
    def _build_timer_section(self, parent):
        frame = ttk.LabelFrame(parent, text="⏱  Pomodoro Timer")
        frame.pack(fill="x", padx=10, pady=(10, 5))

        self.mode_label = ttk.Label(
            frame, text="WORK SESSION", font=("Helvetica", 18, "bold")
        )
        self.mode_label.pack(pady=(20, 5))

        self.time_label = ttk.Label(
            frame, text=self._format_time(self.remaining_seconds),
            font=("Helvetica", 56, "bold")
        )
        self.time_label.pack(pady=5)

        self.session_label = ttk.Label(
            frame, text=self._session_status_text(), font=("Helvetica", 11)
        )
        self.session_label.pack(pady=(0, 15))

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=5)

        self.start_pause_btn = ttk.Button(
            btn_frame, text="Start", width=12, command=self._toggle_start_pause
        )
        self.start_pause_btn.grid(row=0, column=0, padx=6)

        reset_btn = ttk.Button(btn_frame, text="Reset", width=12, command=self._reset_timer)
        reset_btn.grid(row=0, column=1, padx=6)

        skip_btn = ttk.Button(btn_frame, text="Skip to Next", width=12, command=self._skip_session)
        skip_btn.grid(row=0, column=2, padx=6)

        self.auto_start_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frame, text="Auto-start next session", variable=self.auto_start_var
        ).pack(pady=(5, 0))

        mode_frame = ttk.LabelFrame(frame, text="Jump to a mode manually")
        mode_frame.pack(pady=20)

        ttk.Button(mode_frame, text=f"Work ({WORK_MINUTES}m)",
                   command=lambda: self._set_mode("work")).grid(row=0, column=0, padx=6, pady=6)
        ttk.Button(mode_frame, text=f"Short Break ({SHORT_BREAK_MINUTES}m)",
                   command=lambda: self._set_mode("short_break")).grid(row=0, column=1, padx=6, pady=6)
        ttk.Button(mode_frame, text=f"Long Break ({LONG_BREAK_MINUTES}m)",
                   command=lambda: self._set_mode("long_break")).grid(row=0, column=2, padx=6, pady=6)

    def _format_time(self, seconds):
        m, s = divmod(max(seconds, 0), 60)
        return f"{m:02d}:{s:02d}"

    def _session_status_text(self):
        return (f"Completed work sessions: {self.completed_work_sessions}   "
                f"(long break every {SESSIONS_BEFORE_LONG_BREAK})")

    def _mode_seconds(self, mode):
        return {
            "work": WORK_MINUTES * 60,
            "short_break": SHORT_BREAK_MINUTES * 60,
            "long_break": LONG_BREAK_MINUTES * 60,
        }[mode]

    def _mode_display_name(self, mode):
        return {
            "work": "WORK SESSION",
            "short_break": "SHORT BREAK",
            "long_break": "LONG BREAK",
        }[mode]

    def _set_mode(self, mode):
        self._stop_timer_job()
        self.running = False
        self.mode = mode
        self.remaining_seconds = self._mode_seconds(mode)
        self.mode_label.config(text=self._mode_display_name(mode))
        self.time_label.config(text=self._format_time(self.remaining_seconds))
        self.start_pause_btn.config(text="Start")
        self.session_label.config(text=self._session_status_text())

    def _toggle_start_pause(self):
        if self.running:
            self.running = False
            self.start_pause_btn.config(text="Start")
            self._stop_timer_job()
        else:
            self._start_timer()

    def _start_timer(self):
        self.running = True
        self.start_pause_btn.config(text="Pause")
        self._tick()

    def _stop_timer_job(self):
        if self.timer_job is not None:
            self.root.after_cancel(self.timer_job)
            self.timer_job = None

    def _tick(self):
        if not self.running:
            return
        if self.remaining_seconds <= 0:
            self._on_session_complete()
            return
        self.time_label.config(text=self._format_time(self.remaining_seconds))
        self.remaining_seconds -= 1
        self.timer_job = self.root.after(1000, self._tick)

    def _on_session_complete(self):
        self.running = False
        self._stop_timer_job()
        finished_mode = self.mode

        if finished_mode == "work":
            self.completed_work_sessions += 1
            if self.completed_work_sessions % SESSIONS_BEFORE_LONG_BREAK == 0:
                next_mode = "long_break"
            else:
                next_mode = "short_break"
        else:
            next_mode = "work"

        play_alert_sound()
        self.root.bell()
        self._show_toast(
            f"{self._mode_display_name(finished_mode)} finished!",
            f"Next up: {self._mode_display_name(next_mode)}"
        )
        self._set_mode(next_mode)
        if self.auto_start_var.get():
            self._start_timer()

    def _reset_timer(self):
        self._set_mode(self.mode)

    def _skip_session(self):
        self._on_session_complete()

    def _show_toast(self, title, message, duration_ms=6000):
        """Small self-dismissing notification popup, pinned to the
        bottom-right corner of the screen (doesn't block the app)."""
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)  # no title bar / borders
        try:
            toast.attributes("-topmost", True)
        except tk.TclError:
            pass

        width, height = 320, 100
        screen_w = toast.winfo_screenwidth()
        screen_h = toast.winfo_screenheight()
        margin = 20
        taskbar_allowance = 60  # keep clear of the Windows taskbar / macOS dock
        x = screen_w - width - margin
        y = screen_h - height - taskbar_allowance
        toast.geometry(f"{width}x{height}+{x}+{y}")

        card = tk.Frame(toast, bg="#242424", padx=14, pady=12, highlightbackground="#444444",
                         highlightthickness=1)
        card.pack(fill="both", expand=True)

        tk.Label(
            card, text=f"🍅  {title}", bg="#242424", fg="#ffffff",
            font=("Helvetica", 12, "bold"), anchor="w", justify="left"
        ).pack(fill="x")

        tk.Label(
            card, text=message, bg="#242424", fg="#dddddd",
            font=("Helvetica", 10), anchor="w", justify="left", wraplength=290
        ).pack(fill="x", pady=(6, 0))

        close_btn = tk.Label(card, text="✕", bg="#242424", fg="#999999", font=("Helvetica", 10, "bold"),
                              cursor="hand2")
        close_btn.place(relx=1.0, rely=0.0, anchor="ne")

        def dismiss(event=None):
            try:
                toast.destroy()
            except tk.TclError:
                pass

        close_btn.bind("<Button-1>", dismiss)
        card.bind("<Button-1>", dismiss)
        toast.after(duration_ms, dismiss)

    # ---------------- Tasks section ----------------
    def _build_tasks_section(self, parent):
        section = ttk.LabelFrame(parent, text="📋  Tasks")
        section.pack(fill="both", expand=True, padx=10, pady=5)

        form = ttk.LabelFrame(section, text="Add a new task")
        form.pack(fill="x", padx=10, pady=10)

        ttk.Label(form, text="Priority (0=urgent, 100=low):").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.new_priority_var = tk.StringVar(value=str(DEFAULT_PRIORITY))
        ttk.Spinbox(
            form, from_=PRIORITY_MIN, to=PRIORITY_MAX, textvariable=self.new_priority_var, width=6
        ).grid(row=0, column=1, padx=5, pady=5, sticky="w")

        ttk.Label(form, text="Task name:").grid(row=0, column=2, sticky="w", padx=5, pady=5)
        self.new_name_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.new_name_var, width=28).grid(row=0, column=3, padx=5, pady=5, sticky="w")

        ttk.Label(form, text="# Modules:").grid(row=0, column=4, sticky="w", padx=5, pady=5)
        self.new_modules_var = tk.StringVar(value="1")
        ttk.Spinbox(form, from_=1, to=999, textvariable=self.new_modules_var, width=6).grid(
            row=0, column=5, padx=5, pady=5, sticky="w"
        )

        ttk.Label(form, text="Notes:").grid(row=1, column=0, sticky="nw", padx=5, pady=5)
        self.new_notes_text = tk.Text(form, width=60, height=3)
        self.new_notes_text.grid(row=1, column=1, columnspan=5, padx=5, pady=5, sticky="w")

        ttk.Button(form, text="Add Task", command=self._add_task).grid(
            row=2, column=0, columnspan=6, pady=8
        )

        list_frame = ttk.LabelFrame(section, text="Active tasks")
        list_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.tasks_tree = self._make_tree(list_frame)
        self.tasks_tree.bind("<Double-1>", self._on_tree_double_click)

        action_frame = ttk.Frame(section)
        action_frame.pack(fill="x", padx=10, pady=(0, 10))

        ttk.Button(action_frame, text="Start Selected", command=self._start_selected_task).pack(side="left", padx=5)
        ttk.Button(action_frame, text="Mark Complete", command=self._complete_selected_task).pack(side="left", padx=5)
        ttk.Button(action_frame, text="Edit Notes", command=self._edit_notes_selected).pack(side="left", padx=5)
        ttk.Button(action_frame, text="Change Priority", command=self._change_priority_selected).pack(side="left", padx=5)
        ttk.Button(action_frame, text="Delete Task", command=self._delete_selected_task).pack(side="left", padx=5)

    def _make_tree(self, parent, height=8):
        tree = ttk.Treeview(parent, columns=COLUMNS, show="headings", height=height)
        for col in COLUMNS:
            tree.heading(col, text=COLUMN_LABELS[col])
            if col == "notes":
                width = 240
            elif col == "name":
                width = 160
            elif col == "priority":
                width = 80
            else:
                width = 100
            tree.column(col, width=width, anchor="w")
        tree.pack(fill="both", expand=True, padx=5, pady=5, side="left")

        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        # colour tags for quick visual scanning by priority band
        # (0 = most urgent)
        tree.tag_configure("prio_high", foreground="#b00020")   # 0-6
        tree.tag_configure("prio_mid", foreground="#8a6d00")    # 7-15
        tree.tag_configure("prio_low", foreground="#1b5e20")    # 16+
        return tree

    def _priority_tag(self, priority):
        if priority <= 6:
            return "prio_high"
        if priority <= 15:
            return "prio_mid"
        return "prio_low"

    # ---------------- Completed section ----------------
    def _build_completed_section(self, parent):
        section = ttk.LabelFrame(parent, text="✅  Completed")
        section.pack(fill="both", expand=True, padx=10, pady=(5, 10))
        self.completed_tree = self._make_tree(section, height=8)

    # ------------------------------------------------------------------
    # Task logic
    # ------------------------------------------------------------------
    def _add_task(self):
        name = self.new_name_var.get().strip()
        if not name:
            messagebox.showwarning("Missing name", "Please enter a task name.")
            return
        try:
            modules = int(self.new_modules_var.get())
            if modules < 1:
                raise ValueError
        except ValueError:
            messagebox.showwarning("Invalid number", "# Modules must be a positive whole number.")
            return

        try:
            priority = int(self.new_priority_var.get())
            if not (PRIORITY_MIN <= priority <= PRIORITY_MAX):
                raise ValueError
        except ValueError:
            messagebox.showwarning(
                "Invalid priority", f"Priority must be a whole number between {PRIORITY_MIN} and {PRIORITY_MAX}."
            )
            return

        notes = self.new_notes_text.get("1.0", "end").strip()
        today_str = date.today().isoformat()

        task = {
            "id": str(uuid.uuid4()),
            "priority": priority,
            "name": name,
            "modules": modules,
            "created": today_str,
            "started": "",
            "finished": "",
            "notes": notes,
            "completed": False,
        }
        self.tasks.append(task)
        save_tasks(self.tasks)

        self.new_name_var.set("")
        self.new_modules_var.set("1")
        self.new_priority_var.set(str(DEFAULT_PRIORITY))
        self.new_notes_text.delete("1.0", "end")

        self._refresh_task_tables()

    def _get_selected_task_id(self, tree):
        selection = tree.selection()
        if not selection:
            return None
        return selection[0]  # we use task id as the iid

    def _start_selected_task(self):
        task_id = self._get_selected_task_id(self.tasks_tree)
        if not task_id:
            messagebox.showinfo("No selection", "Select a task first.")
            return
        task = self._find_task(task_id)
        if task is None:
            return
        if task["started"]:
            if not messagebox.askyesno("Already started",
                                        f"This task was already started on {task['started']}.\nUpdate start date to today?"):
                return
        task["started"] = date.today().isoformat()
        save_tasks(self.tasks)
        self._refresh_task_tables()

    def _complete_selected_task(self):
        task_id = self._get_selected_task_id(self.tasks_tree)
        if not task_id:
            messagebox.showinfo("No selection", "Select a task first.")
            return
        task = self._find_task(task_id)
        if task is None:
            return
        if not task["started"]:
            task["started"] = date.today().isoformat()
        task["finished"] = date.today().isoformat()
        task["completed"] = True
        save_tasks(self.tasks)
        self._refresh_task_tables()

    def _edit_notes_selected(self):
        task_id = self._get_selected_task_id(self.tasks_tree)
        if not task_id:
            messagebox.showinfo("No selection", "Select a task first.")
            return
        task = self._find_task(task_id)
        if task is None:
            return
        new_notes = simpledialog.askstring(
            "Edit notes", "Notes:", initialvalue=task["notes"], parent=self.root
        )
        if new_notes is not None:
            task["notes"] = new_notes
            save_tasks(self.tasks)
            self._refresh_task_tables()

    def _change_priority_selected(self):
        task_id = self._get_selected_task_id(self.tasks_tree)
        if not task_id:
            messagebox.showinfo("No selection", "Select a task first.")
            return
        task = self._find_task(task_id)
        if task is None:
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Change priority")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text=f"Priority (0=urgent, 100=low) for '{task['name']}':").pack(padx=15, pady=(15, 5))
        var = tk.StringVar(value=str(task["priority"]))
        spin = ttk.Spinbox(dialog, from_=PRIORITY_MIN, to=PRIORITY_MAX, textvariable=var, width=8)
        spin.pack(padx=15, pady=5)

        def apply_and_close():
            try:
                value = int(var.get())
                if not (PRIORITY_MIN <= value <= PRIORITY_MAX):
                    raise ValueError
            except ValueError:
                messagebox.showwarning(
                    "Invalid priority",
                    f"Priority must be a whole number between {PRIORITY_MIN} and {PRIORITY_MAX}.",
                    parent=dialog,
                )
                return
            task["priority"] = value
            save_tasks(self.tasks)
            self._refresh_task_tables()
            dialog.destroy()

        ttk.Button(dialog, text="Save", command=apply_and_close).pack(pady=(5, 15))

    def _on_tree_double_click(self, event):
        tree = event.widget
        row_id = tree.identify_row(event.y)
        col_id = tree.identify_column(event.x)
        if not row_id:
            return
        tree.selection_set(row_id)
        if col_id == f"#{COLUMNS.index('notes') + 1}" and tree is self.tasks_tree:
            self._edit_notes_selected()
        elif col_id == f"#{COLUMNS.index('priority') + 1}" and tree is self.tasks_tree:
            self._change_priority_selected()

    def _delete_selected_task(self):
        task_id = self._get_selected_task_id(self.tasks_tree) or self._get_selected_task_id(self.completed_tree)
        if not task_id:
            messagebox.showinfo("No selection", "Select a task first.")
            return
        task = self._find_task(task_id)
        if task is None:
            return
        if messagebox.askyesno("Delete task", f"Delete '{task['name']}'? This cannot be undone."):
            self.tasks = [t for t in self.tasks if t["id"] != task_id]
            save_tasks(self.tasks)
            self._refresh_task_tables()

    def _find_task(self, task_id):
        for t in self.tasks:
            if t["id"] == task_id:
                return t
        return None

    # ------------------------------------------------------------------
    # Table refresh
    # ------------------------------------------------------------------
    def _refresh_task_tables(self):
        for tree in (self.tasks_tree, self.completed_tree):
            for row in tree.get_children():
                tree.delete(row)

        # sort by priority, most urgent (lowest number) first; stable within ties
        sorted_tasks = sorted(
            self.tasks,
            key=lambda t: t.get("priority", DEFAULT_PRIORITY)
        )

        for task in sorted_tasks:
            priority = task.get("priority", DEFAULT_PRIORITY)
            values = (
                priority,
                task["name"],
                task["modules"],
                task["created"],
                task["started"],
                task["finished"],
                task["notes"],
            )
            target_tree = self.completed_tree if task.get("completed") else self.tasks_tree
            target_tree.insert(
                "", "end", iid=task["id"], values=values, tags=(self._priority_tag(priority),)
            )


def main():
    root = tk.Tk()
    app = PomodoroApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()