import tkinter as tk
from tkinter import ttk, colorchooser, messagebox, filedialog, simpledialog
from tkinter.font import Font
import json
import sqlite3
import hashlib
import os
import threading
import queue
from datetime import datetime
import pygame
from tetris import Game, SelfLearningEngine, WIDTH, HEIGHT
from rich.console import Console

console = Console()


class Log:
    @staticmethod
    def info(msg: str, timestamp: bool = True, **kwargs):
        console.print(f"[cyan]INFO[/cyan]: {msg}")

    @staticmethod
    def error(msg: str, timestamp: bool = True, **kwargs):
        console.print(f"[red]ERROR[/red]: {msg}")

    @staticmethod
    def warning(msg: str, timestamp: bool = True, **kwargs):
        console.print(f"[yellow]WARNING[/yellow]: {msg}")

    @staticmethod
    def debug(msg: str, timestamp: bool = True, **kwargs):
        console.print(f"[green]DEBUG[/green]: {msg}")


# ================= DATABASE =================

DB_NAME = "tetris_db.sqlite"

DEFAULT_AI_PARAMS = {
    "height": -0.51,
    "lines": 0.76,
    "holes": -0.36,
    "bumpiness": -0.18,
    "well_depth": -0.15,
}

AI_PRESETS = {
    "custom": DEFAULT_AI_PARAMS.copy(),
    "qwen": {
        "height": -0.62,
        "lines": 0.95,
        "holes": -0.85,
        "bumpiness": -0.24,
        "well_depth": -0.18,
    },
    "deepseek": {
        "height": -0.55,
        "lines": 1.10,
        "holes": -0.95,
        "bumpiness": -0.22,
        "well_depth": -0.12,
    },
}


def normalize_tags(tags):
    if tags is None:
        return []
    if isinstance(tags, str):
        raw_tags = tags.split(",")
    else:
        raw_tags = list(tags)
    result = []
    seen = set()
    for tag in raw_tags:
        tag = str(tag).strip()
        if not tag:
            continue
        tag = tag[:100]
        key = tag.lower()
        if key not in seen:
            seen.add(key)
            result.append(tag)
        if len(result) >= 50:
            break
    return result


def parse_iterations(value):
    """
    Supports:
    -1 -> infinite
    10^10
    10 10
    normal ints
    """
    s = str(value).strip().replace(" ", "^")
    while "^^" in s:
        s = s.replace("^^", "^")
    if not s:
        return 20
    try:
        if "^" in s:
            base, exp = s.split("^", 1)
            v = int(base) ** int(exp)
        else:
            v = int(s)
    except Exception:
        return 20
    if v < 0:
        return -1
    if v == 0:
        return 1
    return v


def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT
        )
    """)

    migration_queries = [
        "ALTER TABLE users ADD COLUMN default_nickname TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN favorite_color TEXT DEFAULT '#FF4444'",
        "ALTER TABLE users ADD COLUMN default_speed REAL DEFAULT 5.0",
    ]
    for query in migration_queries:
        try:
            c.execute(query)
        except sqlite3.OperationalError:
            pass

    c.execute("""
        CREATE TABLE IF NOT EXISTS leaderboard (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            score INTEGER,
            is_bot INTEGER,
            date TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS CustomAlgModels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            params TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '[]',
            timestamp TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_custom_alg_models_timestamp
        ON CustomAlgModels(timestamp DESC)
    """)

    try:
        c.execute('SELECT 1 FROM "CustomAI-params" LIMIT 1')
        c.execute("""
            INSERT OR REPLACE INTO CustomAlgModels (name, params, tags, timestamp)
            SELECT
                COALESCE(model_name, 'model_' || id),
                COALESCE(params_json, '{}'),
                COALESCE(tags_json, '[]'),
                COALESCE(created_at, datetime('now'))
            FROM "CustomAI-params"
        """)
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def get_user_profile(username: str) -> dict:
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    try:
        c.execute("""
            SELECT username, default_nickname, favorite_color, default_speed
            FROM users
            WHERE username = ?
        """, (username,))
        row = c.fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.close()

    if not row:
        return {
            "default_nickname": username,
            "favorite_color": "#FF4444",
            "default_speed": 5.0,
        }
    return {
        "default_nickname": row["default_nickname"] or row["username"],
        "favorite_color": row["favorite_color"] or "#FF4444",
        "default_speed": float(row["default_speed"] or 5.0),
    }


def model_exists(name: str) -> bool:
    name = str(name or "").strip()
    if not name:
        return False
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT 1 FROM CustomAlgModels WHERE name = ? LIMIT 1
        """, (name,))
        row = cur.fetchone()
    finally:
        conn.close()
    return row is not None


def ask_model_name(parent, title: str, label: str, default_name: str = "") -> str:
    while True:
        name = simpledialog.askstring(title, label, initialvalue=default_name, parent=parent)
        if name is None:
            return ""
        name = name.strip()
        if not name:
            messagebox.showwarning(title, "Model name cannot be empty.", parent=parent)
            continue
        if model_exists(name):
            if messagebox.askyesno(title, f"Model '{name}' already exists.\n\nOverwrite?", parent=parent):
                return name
            continue
        return name


def save_custom_alg_model(name: str, params: dict, tags=None) -> int:
    name = str(name or "").strip()
    if not name:
        raise ValueError("Model name is not set.")
    tags = normalize_tags(tags)

    safe_params = {}
    for key, value in (params or DEFAULT_AI_PARAMS).items():
        try:
            safe_params[key] = float(value)
        except Exception:
            continue
    if not safe_params:
        safe_params = DEFAULT_AI_PARAMS.copy()

    timestamp = datetime.now().isoformat(timespec="seconds")
    params_json = json.dumps(safe_params, ensure_ascii=False)
    tags_json = json.dumps(tags, ensure_ascii=False)

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO CustomAlgModels (name, params, tags, timestamp)
        VALUES (?, ?, ?, ?)
    """, (name, params_json, tags_json, timestamp))
    model_id = cur.lastrowid
    conn.commit()
    conn.close()
    return model_id


def list_custom_alg_models(limit: int = 100) -> list:
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, timestamp, tags
        FROM CustomAlgModels
        ORDER BY timestamp DESC, id DESC
        LIMIT ?
    """, (limit,))
    rows = []
    for row in cur.fetchall():
        item = dict(row)
        try:
            item["tags"] = json.loads(item.get("tags") or "[]")
        except json.JSONDecodeError:
            item["tags"] = []
        item["model_name"] = item["name"]
        item["created_at"] = item["timestamp"]
        rows.append(item)
    conn.close()
    return rows


def get_custom_alg_model(model_id: int):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, params, tags, timestamp
        FROM CustomAlgModels
        WHERE id = ?
    """, (model_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    item = dict(row)
    try:
        item["params"] = json.loads(item.get("params") or "{}")
    except json.JSONDecodeError:
        item["params"] = {}
    try:
        item["tags"] = json.loads(item.get("tags") or "[]")
    except json.JSONDecodeError:
        item["tags"] = []
    item["model_name"] = item["name"]
    item["created_at"] = item["timestamp"]
    return item


# Aliases for compatibility
def save_custom_ai_model(model_name: str, params: dict, tags=None,
                         source: str = "custom", owner: str = "") -> int:
    tags = normalize_tags(tags)
    if source:
        tags.append(source)
    if owner:
        tags.append(owner)
    return save_custom_alg_model(model_name, params, tags)


def list_custom_ai_models(limit: int = 100) -> list:
    return list_custom_alg_models(limit=limit)


def get_custom_ai_model(model_id: int):
    return get_custom_alg_model(model_id)


# ================= AUTH UI =================

class AuthWindow:
    def __init__(self, root, on_success):
        self.root = root
        self.on_success = on_success
        self.root.title("Tetris MP - Login")
        self.root.geometry("430x640")
        self.root.resizable(False, False)
        self.root.configure(bg="#111")

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)

        self.login_frame = tk.Frame(self.notebook, bg="#111")
        self.register_frame = tk.Frame(self.notebook, bg="#111")
        self.notebook.add(self.login_frame, text="Login")
        self.notebook.add(self.register_frame, text="Register")

        Log.info("Building login")
        self.build_login()
        Log.info("Success!")
        Log.info("Building register")
        self.build_register()
        Log.info("Success")

    def build_login(self):
        f = self.login_frame
        tk.Label(f, text="Nickname:", bg="#111", fg="#eee").pack(pady=(20, 5))
        self.login_user = tk.Entry(f, bg="#333", fg="#fff", insertbackground="#fff")
        self.login_user.pack()
        tk.Label(f, text="Password:", bg="#111", fg="#eee").pack(pady=(10, 5))
        self.login_pass = tk.Entry(f, show="*", bg="#333", fg="#fff", insertbackground="#fff")
        self.login_pass.pack()
        tk.Button(f, text="Login", bg="#0f3460", fg="#fff", command=self.check_login).pack(pady=15)
        tk.Button(f, text="Continue as guest", bg="#444", fg="#fff", command=self.login_as_guest).pack(pady=5)

    def build_register(self):
        f = self.register_frame
        tk.Label(f, text="New nickname:", bg="#111", fg="#eee").pack(pady=(20, 5))
        self.reg_user = tk.Entry(f, bg="#333", fg="#fff", insertbackground="#fff")
        self.reg_user.pack()
        tk.Label(f, text="Password:", bg="#111", fg="#eee").pack(pady=(10, 5))
        self.reg_pass = tk.Entry(f, show="*", bg="#333", fg="#fff", insertbackground="#fff")
        self.reg_pass.pack()
        tk.Label(f, text="Default game nickname:", bg="#111", fg="#eee").pack(pady=(10, 5))
        self.reg_default_nick = tk.Entry(f, bg="#333", fg="#fff", insertbackground="#fff")
        self.reg_default_nick.pack()
        tk.Label(f, text="Favorite color:", bg="#111", fg="#eee").pack(pady=(10, 5))
        self.reg_color = "#FF4444"
        self.reg_color_btn = tk.Button(f, text=self.reg_color, bg=self.reg_color, fg="white", width=14,
                                       command=self.pick_reg_color)
        self.reg_color_btn.pack()
        tk.Label(f, text="Default speed:", bg="#111", fg="#eee").pack(pady=(10, 0))
        self.reg_speed = tk.DoubleVar(value=5.0)
        self.reg_speed_label = tk.Label(f, text="5.0", bg="#111", fg="#eee", font=("Helvetica", 9, "bold"))
        self.reg_speed_label.pack()
        tk.Scale(f, from_=0.1, to=10.0, resolution=0.1, orient="horizontal", variable=self.reg_speed,
                 bg="#111", fg="#e94560", troughcolor="#333", length=240, showvalue=0,
                 command=lambda v: self.reg_speed_label.config(text=f"{float(v):.1f}")).pack(pady=(0, 10))
        tk.Button(f, text="Register", bg="#2d6a4f", fg="#fff", command=self.register_user).pack(pady=10)

    def pick_reg_color(self):
        color = colorchooser.askcolor(title="Favorite player color", color=self.reg_color)
        if color[1]:
            self.reg_color = color[1]
            self.reg_color_btn.config(bg=color[1], text=color[1])

    def check_login(self):
        user = self.login_user.get().strip()
        pwd = self.login_pass.get()
        if not user or not pwd:
            messagebox.showerror("Error", "Fill all fields!")
            return
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        try:
            c.execute("SELECT password_hash FROM users WHERE username=?", (user,))
            row = c.fetchone()
        finally:
            conn.close()
        if row and row[0] == hash_password(pwd):
            profile = get_user_profile(user)
            self.on_success(user, profile)
        else:
            messagebox.showerror("Error", "Wrong login or password!")

    def register_user(self):
        user = self.reg_user.get().strip()
        pwd = self.reg_pass.get()
        if not user or not pwd:
            messagebox.showerror("Error", "Fill all fields!")
            return
        if len(pwd) < 4:
            messagebox.showerror("Error", "Password must be at least 4 characters!")
            return
        default_nick = self.reg_default_nick.get().strip() or user
        favorite_color = getattr(self, "reg_color", "#FF4444")
        default_speed = float(self.reg_speed.get())

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        try:
            c.execute("""
                INSERT INTO users
                    (username, password_hash, default_nickname, favorite_color, default_speed)
                VALUES (?, ?, ?, ?, ?)
            """, (user, hash_password(pwd), default_nick, favorite_color, default_speed))
            conn.commit()
            messagebox.showinfo("Success", "Registration successful!\nNow log in.")
            self.notebook.select(0)
        except sqlite3.IntegrityError:
            messagebox.showerror("Error", "Nickname already taken!")
        finally:
            conn.close()

    def login_as_guest(self):
        profile = {
            "default_nickname": "Guest",
            "favorite_color": "#FF4444",
            "default_speed": 5.0,
        }
        self.on_success("Guest", profile)


# ================= MAIN MENU =================

class MainMenuWindow:
    def __init__(self, root, username, on_play, on_leaderboard, on_logout):
        Log.info(f"Main menu init for user: {username}")
        self.root = root
        self.username = username
        self.on_play = on_play
        self.on_leaderboard = on_leaderboard
        self.on_logout = on_logout

        try:
            for w in self.root.winfo_children():
                w.destroy()
            self.root.title(f"Tetris MP - Main menu ({self.username})")
            self.root.geometry("400x350")
            self.root.resizable(False, False)
            self.root.configure(bg="#111")

            tk.Label(self.root, text="TETRIS MP", bg="#111", fg="#e94560",
                     font=("Helvetica", 24, "bold")).pack(pady=(30, 5))
            tk.Label(self.root, text=f"Hello, {self.username}!", bg="#111", fg="#eee",
                     font=("Helvetica", 14)).pack(pady=(0, 30))

            btn_style = {"font": ("Helvetica", 12, "bold"), "width": 20, "height": 2, "bd": 0, "cursor": "hand2"}
            tk.Button(self.root, text="Play", bg="#0f3460", fg="#fff", command=self._handle_play,
                      **btn_style).pack(pady=8)
            tk.Button(self.root, text="Leaderboard", bg="#533483", fg="#fff", command=self._handle_leaderboard,
                      **btn_style).pack(pady=8)
            tk.Button(self.root, text="Logout", bg="#444", fg="#fff", command=self._handle_logout,
                      **btn_style).pack(pady=8)

            Log.info("Main menu created")
        except Exception as e:
            Log.error(f"Main menu creation error: {e}")
            messagebox.showerror("Error", f"Failed to create menu: {e}")

    def _handle_play(self):
        Log.info(f"User {self.username} pressed Play")
        try:
            if callable(self.on_play):
                self.on_play()
        except Exception as e:
            Log.error(f"Game start error: {e}")
            messagebox.showerror("Error", f"Failed to start game: {e}")

    def _handle_leaderboard(self):
        Log.info(f"User {self.username} opened leaderboard")
        try:
            if callable(self.on_leaderboard):
                self.on_leaderboard()
        except Exception as e:
            Log.error(f"Leaderboard error: {e}")
            messagebox.showerror("Error", f"Failed to open leaderboard: {e}")

    def _handle_logout(self):
        Log.info(f"User {self.username} logs out")
        try:
            if callable(self.on_logout):
                self.on_logout()
        except Exception as e:
            Log.error(f"Logout error: {e}")
            messagebox.showerror("Error", f"Failed to logout: {e}")


# ================= LEADERBOARD UI =================

class LeaderboardWindow:
    def __init__(self, parent):
        self.win = tk.Toplevel(parent)
        self.win.title("Leaderboard")
        self.win.geometry("450x500")
        self.win.configure(bg="#111")
        self.win.transient(parent)
        self.win.grab_set()

        tk.Label(self.win, text="TOP-20 PLAYERS", bg="#111", fg="#e94560",
                 font=("Helvetica", 16, "bold")).pack(pady=10)

        columns = ("place", "username", "score", "is_bot", "date")
        self.tree = ttk.Treeview(self.win, columns=columns, show="headings", height=20)
        self.tree.heading("place", text="#")
        self.tree.heading("username", text="Nickname")
        self.tree.heading("score", text="Score")
        self.tree.heading("is_bot", text="Bot?")
        self.tree.heading("date", text="Date")
        self.tree.column("place", width=30, anchor="center")
        self.tree.column("username", width=120)
        self.tree.column("score", width=80, anchor="center")
        self.tree.column("is_bot", width=50, anchor="center")
        self.tree.column("date", width=100)
        self.tree.pack(fill="both", expand=True, padx=10, pady=5)
        self.load_data()

    def load_data(self):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("""
            SELECT username, score, is_bot, date
            FROM leaderboard
            ORDER BY score DESC
            LIMIT 20
        """)
        rows = c.fetchall()
        conn.close()

        for i, row in enumerate(rows, 1):
            user, score, is_bot, date = row
            bot_str = "Yes" if is_bot else "No"
            short_date = date.split("T")[0] if date else ""
            self.tree.insert("", "end", values=(i, user, score, bot_str, short_date))


# ================= SETTINGS UI =================

class KeybindSettings(tk.Toplevel):
    def __init__(self, master, current_keybinds, on_save_callback):
        super().__init__(master)
        self.title("Controls setup (Player 1)")
        self.geometry("320x420")
        self.resizable(False, False)
        self.configure(bg="#000")
        self.transient(master)
        self.grab_set()
        self.on_save = on_save_callback
        self.keybinds = current_keybinds.copy()
        self.vars = {}
        self.build_ui()

    def build_ui(self):
        tk.Label(self, text="Keybinds", bg="#000", fg="#e94560",
                 font=("Helvetica", 16, "bold")).pack(pady=15)

        container = tk.Frame(self, bg="#111")
        container.pack(fill="both", expand=True, padx=15, pady=10)

        actions = [
            ("hard_drop", "Hard Drop"),
            ("rotate", "Rotate"),
            ("left", "Left"),
            ("right", "Right"),
            ("soft_drop", "Soft Drop"),
            ("hold", "Hold"),
        ]

        for action, name in actions:
            frame = tk.Frame(container, bg="#111")
            frame.pack(fill="x", pady=5)
            tk.Label(frame, text=name, bg="#111", fg="#eeeeee", font=("Helvetica", 11)).pack(side="left")
            val = self.keybinds.get(action, "")
            if isinstance(val, int):
                try:
                    val = pygame.key.name(val)
                except Exception:
                    val = str(val)
            var = tk.StringVar(self, value=val)
            self.vars[action] = var
            btn = tk.Button(frame, textvariable=var, bg="#0f3460", fg="white", font=("Helvetica", 10),
                            width=12, command=lambda v=var, a=action: self.record_key(v, a))
            btn.pack(side="right")

        btn_frame = tk.Frame(self, bg="#000")
        btn_frame.pack(pady=15)
        tk.Button(btn_frame, text="Save", bg="#0f3460", fg="white", font=("Helvetica", 10, "bold"),
                  command=self.save_and_close).pack(side="left", padx=8)
        tk.Button(btn_frame, text="Reset", bg="#444", fg="white", font=("Helvetica", 10, "bold"),
                  command=self.reset_defaults).pack(side="left", padx=8)
        tk.Button(btn_frame, text="Cancel", bg="#333", fg="white", font=("Helvetica", 10, "bold"),
                  command=self.destroy).pack(side="left", padx=8)

    def record_key(self, var, action):
        var.set("Press...")

        def on_key(event):
            key_name = event.keysym.lower()
            if key_name in ["shift_l", "shift_r"]:
                key_name = "shift"
            elif key_name in ["control_l", "control_r"]:
                key_name = "ctrl"
            elif key_name in ["alt_l", "alt_r"]:
                key_name = "alt"
            var.set(key_name)
            self.keybinds[action] = key_name
            self.unbind("<Key>")
            self.master.focus_set()

        self.bind("<Key>", on_key)
        self.focus_set()

    def reset_defaults(self):
        defaults = {
            "hard_drop": "q",
            "rotate": "w",
            "left": "a",
            "right": "d",
            "soft_drop": "s",
            "hold": "e",
        }
        for action, val in defaults.items():
            self.keybinds[action] = val
            self.vars[action].set(val)

    def save_and_close(self):
        self.on_save(self.keybinds)
        self.destroy()


class CustomAISettings(tk.Toplevel):
    def __init__(self, master, current_config, on_save_callback):
        super().__init__(master)
        self.title("Custom AI Configuration")
        self.geometry("940x560")
        self.minsize(860, 480)
        self.resizable(True, True)
        self.configure(bg="#000")
        self.transient(master)
        self.grab_set()
        self.on_save = on_save_callback
        self.current_config = current_config.copy()
        self.weights = {}
        self.entry_vars = {}
        self._models = []
        self.build_ui()

    def build_ui(self):
        tk.Label(self, text="Custom AI Heuristics", bg="#000", fg="#e94560",
                 font=("Helvetica", 16, "bold")).pack(pady=(10, 5))

        main = tk.Frame(self, bg="#111")
        main.pack(fill="both", expand=True, padx=10, pady=5)

        left = tk.LabelFrame(main, text="Parameters (values can be entered manually)",
                             bg="#111", fg="#e94560", font=("Helvetica", 11, "bold"), padx=10, pady=10)
        left.pack(side="left", fill="both", expand=True)

        sep = tk.Frame(main, bg="#111", width=50)
        sep.pack(side="left", fill="y", padx=8)
        tk.Label(sep, text="OR", bg="#111", fg="#e94560", font=("Helvetica", 11, "bold")).pack(pady=(15, 5))
        tk.Frame(sep, bg="#444", width=2).pack(fill="both", expand=True)

        right = tk.LabelFrame(main, text="Load saved models", bg="#111", fg="#e94560",
                              font=("Helvetica", 11, "bold"), padx=10, pady=10)
        right.pack(side="left", fill="both", expand=True)

        self._build_left_panel(left)
        self._build_right_panel(right)

        btn_frame = tk.Frame(self, bg="#000")
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="Save & Apply", bg="#0f3460", fg="white", font=("Helvetica", 10, "bold"),
                  command=self.save_and_close).pack(side="left", padx=8)
        tk.Button(btn_frame, text="Reset", bg="#444", fg="white", font=("Helvetica", 10, "bold"),
                  command=self.reset_defaults).pack(side="left", padx=8)
        tk.Button(btn_frame, text="Cancel", bg="#333", fg="white", font=("Helvetica", 10, "bold"),
                  command=self.destroy).pack(side="left", padx=8)

    def _build_left_panel(self, parent):
        settings = [
            ("height", "Aggregate Height", -0.51),
            ("lines", "Lines Cleared", 0.76),
            ("holes", "Holes", -0.36),
            ("bumpiness", "Bumpiness", -0.18),
            ("well_depth", "Well Depth", -0.15),
        ]
        for key, name, default_val in settings:
            frame = tk.Frame(parent, bg="#111")
            frame.pack(fill="x", pady=6)
            header = tk.Frame(frame, bg="#111")
            header.pack(fill="x")
            tk.Label(header, text=name, bg="#111", fg="#eeeeee", font=("Helvetica", 10, "bold")).pack(side="left")

            entry_var = tk.StringVar(self, value=f"{self.current_config.get(key, default_val):.3f}")
            self.entry_vars[key] = entry_var
            entry = tk.Entry(header, textvariable=entry_var, bg="#222", fg="white",
                             insertbackground="white", width=10, justify="right", font=("Consolas", 9))
            entry.pack(side="right")
            entry.bind("<Return>", lambda e, k=key: self._on_entry_commit(k))
            entry.bind("<FocusOut>", lambda e, k=key: self._on_entry_commit(k))

            var = tk.DoubleVar(self, value=self.current_config.get(key, default_val))
            self.weights[key] = var

            scale_frame = tk.Frame(frame, bg="#111")
            scale_frame.pack(fill="x", pady=(2, 0))
            tk.Scale(scale_frame, from_=-2.0, to=2.0, resolution=0.01, orient="horizontal",
                     variable=var, bg="#111", fg="#e94560", troughcolor="#333", length=260, showvalue=0,
                     command=lambda v, k=key: self._on_scale_change(k, v)).pack(side="left", fill="x", expand=True)

    def _build_right_panel(self, parent):
        tk.Label(parent, text="Select model and press Load", bg="#111", fg="#aaaaaa",
                 font=("Helvetica", 9)).pack(anchor="w")
        self.model_list = tk.Listbox(parent, height=14, bg="#222", fg="white",
                                     selectbackground="#0f3460", font=("Consolas", 9))
        self.model_list.pack(fill="both", expand=True, pady=(5, 8))

        list_btns = tk.Frame(parent, bg="#111")
        list_btns.pack(fill="x")
        tk.Button(list_btns, text="Refresh", bg="#444", fg="white", font=("Helvetica", 9, "bold"),
                  command=self.refresh_model_list).pack(side="left", padx=(0, 6))
        tk.Button(list_btns, text="Load selected", bg="#0f3460", fg="white", font=("Helvetica", 9, "bold"),
                  command=self.load_selected_model).pack(side="left")

        self.refresh_model_list()

    def _on_scale_change(self, key, value):
        try:
            val = float(value)
        except Exception:
            return
        self.entry_vars[key].set(f"{val:.3f}")

    def _on_entry_commit(self, key):
        txt = self.entry_vars[key].get().strip().replace(",", ".")
        try:
            val = float(txt)
        except Exception:
            self.entry_vars[key].set(f"{self.weights[key].get():.3f}")
            return
        self.weights[key].set(val)
        self.entry_vars[key].set(f"{val:.3f}")

    def refresh_model_list(self):
        self.model_list.delete(0, tk.END)
        self._models = list_custom_alg_models(limit=100)
        for model in self._models:
            tags = model.get("tags", [])
            short_tags = ",".join(tags[:3])
            if len(tags) > 3:
                short_tags += "..."
            line = f"{model.get('timestamp', '')} | {model.get('name', '')} | {short_tags}"
            self.model_list.insert(tk.END, line)

    def load_selected_model(self):
        selection = self.model_list.curselection()
        if not selection:
            messagebox.showinfo("Custom AI", "Select a model first.")
            return
        model = self._models[selection[0]]
        data = get_custom_alg_model(model["id"])
        if not data:
            messagebox.showerror("Custom AI", "Failed to load model.")
            return
        params = data.get("params", {})
        for key, var in self.weights.items():
            if key in params:
                try:
                    value = float(params[key])
                    var.set(value)
                    self.entry_vars[key].set(f"{value:.3f}")
                except Exception:
                    continue
        self.current_config = {key: round(var.get(), 3) for key, var in self.weights.items()}
        Log.info(f"Loaded CustomAlg model: {data.get('name')}")

    def reset_defaults(self):
        defaults = {
            "height": -0.51,
            "lines": 0.76,
            "holes": -0.36,
            "bumpiness": -0.18,
            "well_depth": -0.15,
        }
        for key, var in self.weights.items():
            val = defaults.get(key, 0.0)
            var.set(val)
            self.entry_vars[key].set(f"{val:.3f}")

    def save_and_close(self):
        config = {k: round(v.get(), 3) for k, v in self.weights.items()}
        self.on_save(config)
        self.destroy()


# ================= SELF-LEARNING PROGRESS =================

class SelfLearningProgress(tk.Toplevel):
    def __init__(self, parent, model_name: str, iterations: int, q: queue.Queue,
                 stop_event: threading.Event, on_finished=None, on_error=None):
        super().__init__(parent)
        self.title("Self-Learning")
        self.geometry("580x400")
        self.configure(bg="#111")
        self.transient(parent)
        self.q = q
        self.stop_event = stop_event
        self.on_finished = on_finished
        self.on_error = on_error
        self.finished = False
        self.protocol("WM_DELETE_WINDOW", self.stop)

        mode_text = "inf (manual stop)" if iterations < 0 else str(iterations)
        tk.Label(self, text=f"Model: {model_name}", bg="#111", fg="#e94560",
                 font=("Helvetica", 12, "bold")).pack(pady=(10, 2))
        tk.Label(self, text=f"Iterations: {mode_text}", bg="#111", fg="#eeeeee",
                 font=("Helvetica", 10)).pack()
        self.status = tk.Label(self, text="Preparing...", bg="#111", fg="#00ff88",
                               font=("Helvetica", 10, "bold"))
        self.status.pack(pady=5)
        self.text = tk.Text(self, bg="#000", fg="#00ff88", font=("Consolas", 9), state="disabled")
        self.text.pack(fill="both", expand=True, padx=10, pady=5)
        tk.Button(self, text="Stop and save", bg="#dc3545", fg="white", font=("Helvetica", 10, "bold"),
                  command=self.stop).pack(pady=8)
        self.after(100, self.poll)

    def stop(self):
        if self.finished:
            return
        self.stop_event.set()
        self.status.config(text="Stopping... waiting for current iteration")

    def append_log(self, line: str):
        self.text.config(state="normal")
        self.text.insert(tk.END, line + "\n")
        line_count = int(self.text.index("end-1c").split(".")[0])
        if line_count > 500:
            self.text.delete("1.0", f"{line_count - 500}.0")
        self.text.see(tk.END)
        self.text.config(state="disabled")

    def poll(self):
        if self.finished:
            return
        try:
            while True:
                entry = self.q.get_nowait()
                if entry.get("__done__"):
                    self.finished = True
                    result = entry.get("result")
                    self.destroy()
                    if self.on_finished:
                        self.on_finished(result)
                    return
                if entry.get("__error__"):
                    self.finished = True
                    self.destroy()
                    if self.on_error:
                        self.on_error()
                    messagebox.showerror("Self-Learning Error", entry.get("__error__", "Unknown error"))
                    return

                self.status.config(text=f"Iteration {entry.get('iter', '?')}")
                try:
                    time_val = float(entry.get("time", 0.0) or 0.0)
                except Exception:
                    time_val = 0.0
                self.append_log(
                    f"#{entry.get('iter', '?')} | "
                    f"score={entry.get('score', 0)} | "
                    f"lines={entry.get('lines', 0)} | "
                    f"time={time_val:.2f}s"
                )
        except queue.Empty:
            pass
        self.after(100, self.poll)


# ================= SETUP UI =================

class TetrisSetup:
    def __init__(self, parent, username="Guest", profile=None):
        self.parent = parent
        self.current_user = username
        self.profile = profile or {}
        self.self_learning_running = False
        self.self_learning_stop_event = None

        self.root = tk.Toplevel(parent)
        self.root.title(f"Tetris MP - Setup ({self.current_user})")
        self.root.geometry("1250x850")
        self.root.minsize(900, 350)
        self.root.resizable(True, True)
        self.root.configure(bg="#000")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.colors = {
            "bg": "#000",
            "frame_bg": "#111",
            "accent": "#e94560",
            "text": "#eeeeee",
            "button": "#0f3460",
            "button_hover": "#533483",
        }

        self.player_colors = {1: "#FF4444", 2: "#44FF44", 3: "#4444FF", 4: "#FFFF44"}
        self.player_enabled = {i: tk.BooleanVar(self.root, value=True) for i in range(1, 5)}
        self.player_is_bot = {i: tk.BooleanVar(self.root, value=False) for i in range(1, 5)}
        self.player_ai_type = {}
        for i in range(1, 5):
            self.player_ai_type[i] = tk.StringVar(self.root, value="DeepSeek")

        self.fall_speeds = {}
        self.nickname_entries = {}
        self.custom_ai_config = {}
        self.color_buttons = {}
        self.speed_scales = {}
        self.bot_checkboxes = {}
        self.ai_menus = {}
        self.speed_labels = {}

        self.self_learning_iters = tk.StringVar(self.root, value="20")
        self.self_learning_ai = tk.StringVar(self.root, value="Custom")
        self.teacher_student_delay = tk.IntVar(self.root, value=5)
        self.self_learning_model = tk.StringVar(self.root, value=f"{self.current_user}_selfAI")
        self.self_learning_tags = tk.StringVar(self.root, value="self-learning,trained,tetris")
        self.teacher_student_tags = tk.StringVar(self.root, value="teacher-student,trained,tetris")

        self.game_mode = tk.StringVar(self.root, value="vs")
        self.game_mode.trace_add("write", self._on_mode_change)
        self.network_mode = tk.StringVar(self.root, value="local")
        self.network_mode.trace_add("write", self._on_network_mode_change)

        self.room_name_var = tk.StringVar(self.root, value="tetris_room")
        self.server_host_var = tk.StringVar(self.root, value="127.0.0.1")
        self.is_host_var = tk.BooleanVar(self.root, value=True)

        self.dynamic_keybinds = {
            "hard_drop": "q",
            "rotate": "w",
            "left": "a",
            "right": "d",
            "soft_drop": "s",
            "hold": "e",
        }

        self.setup_ui()
        self._apply_profile_to_player1()

    def _on_mode_change(self, *args):
        self.on_game_mode_change()

    def _on_network_mode_change(self, *args):
        self.on_network_mode_change()

    def on_network_mode_change(self):
        mode = self.network_mode.get()
        if mode in ["lan", "online"]:
            self.room_name_container.pack(fill="x", pady=10, before=self.bottom_frame)
            self.host_join_frame.pack(fill="x", pady=(0, 5))
        else:
            self.room_name_container.pack_forget()
            self.host_join_frame.pack_forget()
        self.update_keybind_button()

    def on_game_mode_change(self):
        mode = self.game_mode.get()
        for w in (self.room_name_container, self.ml_container, self.ts_container):
            w.pack_forget()

        if mode in ["lan", "global"]:
            self.room_name_container.pack(fill="x", pady=10, before=self.bottom_frame)
        elif mode == "self_learning":
            self.ml_container.pack(fill="x", pady=10, before=self.bottom_frame)
        elif mode == "teacher_student":
            self.ts_container.pack(fill="x", pady=10, before=self.bottom_frame)
            self.player_enabled[1].set(True)
            self.player_enabled[2].set(True)
            self.player_is_bot[1].set(False)
            self.player_is_bot[2].set(True)
            self.player_ai_type[2].set("Student")
            for p in range(1, 3):
                self.update_player_state(p)

    def setup_ui(self):
        main = tk.Frame(self.root, bg=self.colors["bg"])
        main.pack(fill="both", expand=True, padx=20, pady=20)

        title = tk.Label(main, text="TETRIS SETUP", font=Font(family="Helvetica", size=24, weight="bold"),
                         bg=self.colors["bg"], fg=self.colors["accent"])
        title.pack(pady=(0, 20))

        top = tk.Frame(main, bg=self.colors["bg"])
        top.pack(fill="both", expand=True)

        left = tk.LabelFrame(top, text="Game Mode", bg=self.colors["frame_bg"], fg=self.colors["text"],
                             font=("Helvetica", 12, "bold"), padx=20, pady=15)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))

        modes = [
            ("VS", "vs"),
            ("CO-OP", "coop"),
            ("2 VS 2", "2vs2"),
            ("Self-Learning", "self_learning"),
            ("Teacher-Student", "teacher_student"),
        ]
        for text, value in modes:
            tk.Radiobutton(left, text=text, variable=self.game_mode, value=value,
                           bg=self.colors["frame_bg"], fg=self.colors["text"],
                           selectcolor=self.colors["frame_bg"], font=("Helvetica", 11)).pack(anchor="w", pady=5)

        connection_frame = tk.LabelFrame(top, text="Connection", bg=self.colors["frame_bg"], fg=self.colors["text"],
                                         font=("Helvetica", 12, "bold"), padx=20, pady=10)
        connection_frame.pack(side="left", fill="both", expand=True, padx=(10, 10))

        connection_modes = [
            ("Local game", "local"),
            ("LAN multiplayer", "lan"),
            ("Online multiplayer", "online"),
        ]
        for text, value in connection_modes:
            tk.Radiobutton(connection_frame, text=text, variable=self.network_mode, value=value,
                           bg=self.colors["frame_bg"], fg=self.colors["text"],
                           selectcolor=self.colors["frame_bg"], font=("Helvetica", 11)).pack(anchor="w", pady=5)

        self.host_join_frame = tk.Frame(connection_frame, bg=self.colors["frame_bg"])
        tk.Radiobutton(self.host_join_frame, text="Create room", variable=self.is_host_var, value=True,
                       bg=self.colors["frame_bg"], fg=self.colors["text"],
                       selectcolor=self.colors["frame_bg"], font=("Helvetica", 10)).pack(anchor="w")
        tk.Radiobutton(self.host_join_frame, text="Join room", variable=self.is_host_var, value=False,
                       bg=self.colors["frame_bg"], fg=self.colors["text"],
                       selectcolor=self.colors["frame_bg"], font=("Helvetica", 10)).pack(anchor="w")

        right = tk.LabelFrame(top, text="Players Configuration", bg=self.colors["frame_bg"], fg=self.colors["text"],
                              font=("Helvetica", 12, "bold"), padx=15, pady=10)
        right.pack(side="right", fill="both", expand=True, padx=(10, 0))

        headers = ["Nick", "On", "Color", "Speed", "Bot", "AI Type"]
        for col, h in enumerate(headers):
            tk.Label(right, text=h, bg=self.colors["frame_bg"], fg=self.colors["accent"],
                     font=("Helvetica", 10, "bold")).grid(row=0, column=col, padx=5, pady=5)

        ai_values = ["Qwen", "DeepSeek", "Custom", "Student"]
        for p in range(1, 5):
            entry = tk.Entry(right, bg="#222", fg=self.colors["text"], width=10)
            entry.insert(0, f"Player{p}")
            entry.grid(row=p, column=0, padx=5, pady=5)
            self.nickname_entries[p] = entry

            cb_en = tk.Checkbutton(right, variable=self.player_enabled[p], bg=self.colors["frame_bg"],
                                   command=lambda pl=p: self.update_player_state(pl))
            cb_en.grid(row=p, column=1, padx=5, pady=5)

            btn = tk.Button(right, text="Pick", bg=self.player_colors[p], fg="white",
                            command=lambda x=p: self.choose_color(x))
            btn.grid(row=p, column=2, padx=5, pady=5)
            self.color_buttons[p] = btn

            speed_frame = tk.Frame(right, bg=self.colors["frame_bg"])
            speed_frame.grid(row=p, column=3, padx=5, pady=5)
            lbl = tk.Label(speed_frame, text="5.0", bg=self.colors["frame_bg"], fg=self.colors["text"],
                           font=("Helvetica", 8))
            lbl.pack(anchor="n")
            self.speed_labels[p] = lbl
            speed_var = tk.DoubleVar(self.root, value=5.0)
            self.fall_speeds[p] = speed_var
            scale = tk.Scale(speed_frame, from_=0.1, to=10.0, resolution=0.1, orient="horizontal",
                             variable=speed_var, bg=self.colors["frame_bg"], fg=self.colors["text"],
                             length=80, showvalue=0,
                             command=lambda v, pl=p: self.speed_labels[pl].config(text=f"{float(v):.1f}"))
            scale.set(5.0)
            scale.pack()
            self.speed_scales[p] = scale

            cb_bot = tk.Checkbutton(right, variable=self.player_is_bot[p], bg=self.colors["frame_bg"],
                                    command=lambda pl=p: self.update_player_state(pl))
            cb_bot.grid(row=p, column=4, padx=5, pady=5)
            self.bot_checkboxes[p] = cb_bot

            ai_menu = tk.OptionMenu(right, self.player_ai_type[p], *ai_values)
            ai_menu.config(bg="#222", fg="white", width=8)
            ai_menu.grid(row=p, column=5, padx=5, pady=5)
            self.ai_menus[p] = ai_menu

        for p in range(1, 5):
            self.update_player_state(p)

        self.room_name_container = tk.Frame(main, bg=self.colors["bg"])
        tk.Label(self.room_name_container, text="Room name:", bg=self.colors["bg"], fg=self.colors["text"],
                 font=("Helvetica", 12, "bold")).pack(side="left", padx=5)
        self.room_name_entry = tk.Entry(self.room_name_container, textvariable=self.room_name_var,
                                        bg="#222", fg=self.colors["text"], font=("Helvetica", 12), width=20)
        self.room_name_entry.pack(side="left", padx=5)
        tk.Label(self.room_name_container, text="Host:", bg=self.colors["bg"], fg=self.colors["text"],
                 font=("Helvetica", 12, "bold")).pack(side="left", padx=(15, 5))
        self.server_host_entry = tk.Entry(self.room_name_container, textvariable=self.server_host_var,
                                          bg="#222", fg=self.colors["text"], font=("Helvetica", 12), width=15)
        self.server_host_entry.pack(side="left", padx=5)

        self.ml_container = tk.Frame(main, bg=self.colors["bg"])
        tk.Label(self.ml_container, text="Iterations (-1 = inf, can use 10^10):", bg=self.colors["bg"],
                 fg=self.colors["text"], font=("Helvetica", 11)).pack(side="left", padx=5)
        tk.Entry(self.ml_container, textvariable=self.self_learning_iters, bg="#222", fg="white",
                 width=14).pack(side="left", padx=5)
        tk.Label(self.ml_container, text="AI:", bg=self.colors["bg"], fg=self.colors["text"],
                 font=("Helvetica", 11)).pack(side="left", padx=(15, 5))
        tk.OptionMenu(self.ml_container, self.self_learning_ai, "Custom", "Qwen", "DeepSeek").pack(side="left")
        tk.Label(self.ml_container, text="Default name:", bg=self.colors["bg"], fg=self.colors["text"],
                 font=("Helvetica", 11)).pack(side="left", padx=(15, 5))
        tk.Entry(self.ml_container, textvariable=self.self_learning_model, bg="#222", fg="white",
                 width=25).pack(side="left", padx=5)
        tk.Label(self.ml_container, text="Tags:", bg=self.colors["bg"], fg=self.colors["text"],
                 font=("Helvetica", 11)).pack(side="left", padx=(15, 5))
        tk.Entry(self.ml_container, textvariable=self.self_learning_tags, bg="#222", fg="white",
                 width=35).pack(side="left", padx=5)

        self.show_gameplay_var = tk.BooleanVar(self.root, value=False)
        tk.Checkbutton(self.ml_container, text="Show bot gameplay", variable=self.show_gameplay_var,
                       bg=self.colors["bg"], fg=self.colors["text"], selectcolor=self.colors["frame_bg"],
                       font=("Helvetica", 10)).pack(side="left", padx=10)

        self.start_from_zero_var = tk.BooleanVar(self.root, value=True)
        tk.Checkbutton(self.ml_container, text="Start from zero weights", variable=self.start_from_zero_var,
                       bg=self.colors["bg"], fg=self.colors["text"], selectcolor=self.colors["frame_bg"],
                       font=("Helvetica", 10)).pack(side="left", padx=10)

        self.ts_container = tk.Frame(main, bg=self.colors["bg"])
        tk.Label(self.ts_container, text="Student delay (ticks):", bg=self.colors["bg"], fg=self.colors["text"],
                 font=("Helvetica", 11)).pack(side="left", padx=5)
        tk.Spinbox(self.ts_container, from_=0, to=60, textvariable=self.teacher_student_delay,
                   bg="#222", fg="white", width=6).pack(side="left", padx=5)
        tk.Label(self.ts_container, text="Tags:", bg=self.colors["bg"], fg=self.colors["text"],
                 font=("Helvetica", 11)).pack(side="left", padx=(15, 5))
        tk.Entry(self.ts_container, textvariable=self.teacher_student_tags, bg="#222", fg="white",
                 width=40).pack(side="left", padx=5)

        self.bottom_frame = tk.LabelFrame(main, text="Controls", bg=self.colors["frame_bg"], fg=self.colors["text"],
                                          font=("Helvetica", 12, "bold"), padx=20, pady=15)
        self.bottom_frame.pack(fill="x", pady=(20, 0))

        btn_frame = tk.Frame(self.bottom_frame, bg=self.colors["frame_bg"])
        btn_frame.pack()

        style = {"font": ("Helvetica", 11, "bold"), "padx": 15, "pady": 8, "bd": 2}
        tk.Button(btn_frame, text="Start Game", bg=self.colors["button"], fg="white",
                  command=self.start_game, **style).pack(side="left", padx=5)
        tk.Button(btn_frame, text="Save Settings", bg="#2d6a4f", fg="white",
                  command=self.save_settings, **style).pack(side="left", padx=5)
        tk.Button(btn_frame, text="Load Settings", bg="#2d6a4f", fg="white",
                  command=self.load_settings, **style).pack(side="left", padx=5)
        tk.Button(btn_frame, text="Leaderboard", bg="#533483", fg="white",
                  command=self.show_leaderboard, **style).pack(side="left", padx=5)
        tk.Button(btn_frame, text="Custom AI", bg="#533483", fg="white",
                  command=self.open_custom_ai_settings, **style).pack(side="left", padx=5)

        self.keybind_btn = tk.Button(btn_frame, text="Configure keys", bg="#533483", fg="white",
                                     command=self.open_keybind_settings, **style)
        self.keybind_btn.pack(side="left", padx=5)

        tk.Button(btn_frame, text="Exit", bg="#dc3545", fg="white",
                  command=self.exit_game, **style).pack(side="left", padx=5)

        self.on_game_mode_change()
        self.on_network_mode_change()

    def _apply_profile_to_player1(self):
        if not self.profile:
            return
        self.player_enabled[1].set(True)
        nickname = self.profile.get("default_nickname") or self.current_user
        self.nickname_entries[1].delete(0, tk.END)
        self.nickname_entries[1].insert(0, nickname)
        color = self.profile.get("favorite_color") or self.player_colors[1]
        self.player_colors[1] = color
        self.color_buttons[1].config(bg=color)
        speed = float(self.profile.get("default_speed", 5.0))
        self.fall_speeds[1].set(speed)
        self.speed_scales[1].set(speed)
        self.speed_labels[1].config(text=f"{speed:.1f}")
        self.update_player_state(1)

    def show_leaderboard(self):
        LeaderboardWindow(self.root)

    def update_player_state(self, player):
        is_enabled = self.player_enabled[player].get()
        is_bot = self.player_is_bot[player].get()
        state = "normal" if is_enabled else "disabled"

        self.color_buttons[player].config(state=state)
        self.speed_scales[player].config(state=state)
        self.nickname_entries[player].config(state=state)
        self.speed_labels[player].config(state=state)
        self.bot_checkboxes[player].config(state=state)

        if is_enabled and is_bot:
            self.ai_menus[player].config(state="normal")
        else:
            self.ai_menus[player].config(state="disabled")

        self.update_keybind_button()

    def update_keybind_button(self):
        enabled_players = sum(1 for p in range(1, 5) if self.player_enabled[p].get())
        if self.network_mode.get() in ["lan", "online"] and enabled_players > 1:
            if hasattr(self, "keybind_btn"):
                self.keybind_btn.config(state="disabled")
        else:
            if hasattr(self, "keybind_btn"):
                self.keybind_btn.config(state="normal")

    def choose_color(self, player):
        color = colorchooser.askcolor(title=f"Player {player} color", color=self.player_colors[player],
                                      parent=self.root)
        if color[1]:
            self.player_colors[player] = color[1]
            self.color_buttons[player].config(bg=color[1])

    def open_keybind_settings(self):
        KeybindSettings(self.root, self.dynamic_keybinds, self.apply_keybinds)

    def apply_keybinds(self, keybinds):
        self.dynamic_keybinds = keybinds
        Log.info(f"Player 1 keybinds updated: {keybinds}")

    def get_settings(self):
        players = {}
        for p in range(1, 5):
            if self.player_enabled[p].get():
                is_bot = self.player_is_bot[p].get()
                ai_type = self.player_ai_type[p].get().lower() if is_bot else None
                ai_config = self.custom_ai_config if ai_type == "custom" else {}
                players[p] = {
                    "enabled": True,
                    "nickname": self.nickname_entries[p].get(),
                    "color": self.player_colors[p],
                    "speed": self.fall_speeds[p].get(),
                    "is_bot": is_bot,
                    "ai_type": ai_type,
                    "ai_config": ai_config,
                }

        settings = {
            "game_mode": self.game_mode.get(),
            "players": players,
            "custom_ai_config": self.custom_ai_config,
            "self_learning_iters": parse_iterations(self.self_learning_iters.get()),
            "self_learning_ai": self.self_learning_ai.get().lower(),
            "teacher_student_delay": self.teacher_student_delay.get(),
            "self_learning_model_name": self.self_learning_model.get().strip()
                                        or f"{self.current_user}_selfAI",
            "self_learning_tags": normalize_tags(self.self_learning_tags.get()),
            "teacher_student_tags": normalize_tags(self.teacher_student_tags.get()),
            "show_gameplay": self.show_gameplay_var.get(),
            "start_from_zero": self.start_from_zero_var.get(),
        }

        enabled_players = sum(1 for p in range(1, 5) if self.player_enabled[p].get())

        if self.network_mode.get() in ["lan", "online"]:
            settings["network_mode"] = self.network_mode.get()
            settings["room_name"] = self.room_name_var.get().strip() or "tetris_room"
            settings["server_port"] = 8888
            settings["is_host"] = self.is_host_var.get()
            settings["server_host"] = self.server_host_var.get().strip() or "127.0.0.1"
            if enabled_players > 1:
                settings["dynamic_keymap"] = {}
            else:
                settings["dynamic_keymap"] = {1: self.dynamic_keybinds}
        else:
            settings["network_mode"] = "local"
            settings["dynamic_keymap"] = {1: self.dynamic_keybinds}

        return settings

    def open_custom_ai_settings(self):
        CustomAISettings(self.root, self.custom_ai_config, self.apply_custom_ai_config)

    def apply_custom_ai_config(self, config):
        self.custom_ai_config = config
        Log.info(f"Custom AI config updated in UI: {config}")

    def start_game(self):
        settings = self.get_settings()
        settings["current_user"] = self.current_user

        if not settings["players"] and settings["game_mode"] != "self_learning":
            messagebox.showwarning("No players", "Enable at least one player!")
            return

        if settings["game_mode"] == "self_learning":
            self.run_self_learning(settings)
            return

        self.root.withdraw()
        game = None
        try:
            game = Game(settings)
            game.run()
        except Exception as e:
            Log.error(f"Critical game error: {e}")
        finally:
            if settings["game_mode"] == "teacher_student" and game is not None:
                try:
                    self.save_teacher_student_model(game, settings)
                except Exception as e:
                    Log.error(f"Teacher-student model save error: {e}")
            try:
                self.root.destroy()
            except tk.TclError:
                pass

    def run_self_learning(self, settings):
        if self.self_learning_running:
            messagebox.showinfo("Self-Learning", "Learning already running.")
            return

        try:
            ai_type = str(settings.get("self_learning_ai", "custom")).lower()
            iterations = parse_iterations(settings.get("self_learning_iters", 20))

            if ai_type == "custom":
                base_config = dict(settings.get("custom_ai_config") or DEFAULT_AI_PARAMS)
            else:
                base_config = dict(AI_PRESETS.get(ai_type, DEFAULT_AI_PARAMS))

            default_name = settings.get("self_learning_model_name") or f"{self.current_user}_selfAI"
            model_name = ask_model_name(parent=self.root, title="Self-Learning",
                                        label="Model name to save:", default_name=default_name)
            if not model_name:
                Log.info("Self-learning cancelled: user did not set model name.")
                return

            stop_event = threading.Event()
            self.self_learning_stop_event = stop_event
            q = queue.Queue()
            frame_q = queue.Queue(maxsize=2)
            show_gameplay = settings.get("show_gameplay", False)
            viewer = None

            def push_frame(frame):
                try:
                    frame_q.put_nowait(frame)
                except queue.Full:
                    try:
                        frame_q.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        frame_q.put_nowait(frame)
                    except queue.Full:
                        pass

            def on_frame(grid, width, height, score, lines, piece_name, placed_pieces):
                if not show_gameplay:
                    return
                frame = {
                    "grid": [row[:] for row in grid],
                    "score": score,
                    "lines": lines,
                    "piece_name": piece_name,
                    "placed_pieces": placed_pieces,
                }
                push_frame(frame)

            if show_gameplay:
                viewer = GameplayViewer(self.root, WIDTH, HEIGHT, cell_size=18)

                def poll_viewer():
                    if viewer is None or getattr(viewer, "_closed", True):
                        return
                    try:
                        while True:
                            frame = frame_q.get_nowait()
                            viewer.update_frame(frame["grid"], frame["score"], frame["lines"],
                                                frame["piece_name"], frame["placed_pieces"])
                    except queue.Empty:
                        pass
                    if not getattr(viewer, "_closed", True):
                        viewer.after(33, poll_viewer)

                viewer.after(100, poll_viewer)

            engine = SelfLearningEngine(
                iterations=iterations,
                ai_type=ai_type,
                base_config=base_config,
                start_from_zero=settings.get("start_from_zero", True),
                show_gameplay_callback=on_frame if show_gameplay else None,
            )

            def worker():
                try:
                    result = engine.run(on_iter_callback=lambda entry: q.put(entry), stop_event=stop_event)
                    q.put({"__done__": True, "result": result})
                except Exception as e:
                    q.put({"__error__": str(e)})

            def close_viewer():
                if viewer is not None and not getattr(viewer, "_closed", True):
                    try:
                        viewer.close()
                    except Exception:
                        pass

            def handle_finished(result):
                close_viewer()
                self._finish_self_learning(model_name=model_name, ai_type=ai_type,
                                           settings=settings, result=result)

            def handle_error():
                close_viewer()
                self._on_self_learning_error()

            self.self_learning_running = True
            threading.Thread(target=worker, daemon=True).start()

            SelfLearningProgress(
                parent=self.root,
                model_name=model_name,
                iterations=iterations,
                q=q,
                stop_event=stop_event,
                on_finished=handle_finished,
                on_error=handle_error,
            )
        except Exception as e:
            self.self_learning_running = False
            Log.error(f"Self-learning error: {e}")
            messagebox.showerror("Self-Learning Error", str(e))

    def _on_self_learning_error(self):
        self.self_learning_running = False

    def _finish_self_learning(self, model_name: str, ai_type: str, settings: dict, result: dict):
        try:
            if not result:
                messagebox.showwarning("Self-Learning", "No result to save.")
                return

            tags = normalize_tags(settings.get("self_learning_tags", []))
            tags.extend([ai_type, "self-learning"])
            iterations = parse_iterations(settings.get("self_learning_iters", 20))
            if iterations < 0:
                tags.append("infinite")

            best_weights = result.get("best_weights") or {}
            save_custom_alg_model(name=model_name, params=best_weights, tags=tags)

            best_score = result.get("best_score", 0)
            if best_score < 0:
                best_score = 0
            iterations_done = result.get("iterations_done", len(result.get("stats", [])))

            messagebox.showinfo(
                "Self-Learning",
                f"Training completed.\n\n"
                f"Model saved:\n{model_name}\n\n"
                f"Iterations done: {iterations_done}\n"
                f"Best score: {best_score}",
            )
        except Exception as e:
            Log.error(f"Self-learning model save error: {e}")
            messagebox.showerror("Self-Learning Save Error", str(e))
        finally:
            self.self_learning_running = False

    def save_teacher_student_model(self, game, settings):
        teacher = next((p for p in game.players if p.id == 1), None)
        student = next((p for p in game.players if p.id == 2), None)

        if teacher is None:
            Log.warning("Teacher-student: teacher not found, model not saved.")
            return

        try:
            self.root.deiconify()
            self.root.lift()
            self.root.update_idletasks()
        except Exception:
            pass

        default_name = f"{teacher.nickname}_teachingAI"
        model_name = ask_model_name(parent=self.root, title="Teacher-Student",
                                    label="Model name to save:", default_name=default_name)
        if not model_name:
            Log.warning("Teacher-student: user cancelled model save.")
            return

        params = None
        if student and getattr(student, "bot", None):
            if hasattr(student.bot, "export_learned_weights"):
                try:
                    params = student.bot.export_learned_weights()
                except Exception as e:
                    Log.error(f"Student weights export error: {e}")

        if not params:
            params = settings.get("custom_ai_config") or DEFAULT_AI_PARAMS.copy()

        tags = normalize_tags(settings.get("teacher_student_tags", []))
        tags.extend(["teacher-student", teacher.nickname])

        if student is not None:
            tags.append(f"student_score_{student.board.score}")
            tags.append(f"student_lines_{student.board.lines_cleared_total}")

        try:
            save_custom_alg_model(name=model_name, params=params, tags=tags)
            Log.info(f"Teacher-student model saved: {model_name}")
            messagebox.showinfo("Teacher-Student", f"Model saved:\n{model_name}")
        except Exception as e:
            Log.error(f"Teacher-student model save error: {e}")
            messagebox.showerror("Teacher-Student Save Error", str(e))

    def save_settings(self):
        file = filedialog.asksaveasfilename(defaultextension=".json",
                                            filetypes=[("JSON files", "*.json")], parent=self.root)
        if file:
            with open(file, "w", encoding="utf-8") as f:
                json.dump(self.get_settings(), f, indent=2, ensure_ascii=False)

    def load_settings(self):
        file = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")], parent=self.root)
        if not file:
            return
        try:
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)

            Log.info("Settings file loaded.")

            self.game_mode.set(data.get("game_mode", "vs"))
            if "custom_ai_config" in data:
                self.custom_ai_config = data["custom_ai_config"]
            if "dynamic_keymap" in data and 1 in data["dynamic_keymap"]:
                self.dynamic_keybinds = data["dynamic_keymap"][1]
            if "server_host" in data:
                self.server_host_var.set(data["server_host"])
            if "room_name" in data:
                self.room_name_var.set(data["room_name"])
            if "self_learning_model_name" in data:
                self.self_learning_model.set(data["self_learning_model_name"])
            if "self_learning_iters" in data:
                self.self_learning_iters.set(str(data["self_learning_iters"]))
            if "self_learning_tags" in data:
                self.self_learning_tags.set(",".join(normalize_tags(data["self_learning_tags"])))
            if "teacher_student_delay" in data:
                try:
                    self.teacher_student_delay.set(int(data["teacher_student_delay"]))
                except Exception:
                    pass
            if "teacher_student_tags" in data:
                self.teacher_student_tags.set(",".join(normalize_tags(data["teacher_student_tags"])))

            ai_mapping = {
                "qwen": "Qwen",
                "deepseek": "DeepSeek",
                "custom": "Custom",
                "student": "Student",
            }

            for p_str, pdata in data.get("players", {}).items():
                p = int(p_str)
                if 1 <= p <= 4:
                    self.player_enabled[p].set(pdata.get("enabled", True))
                    self.nickname_entries[p].delete(0, tk.END)
                    self.nickname_entries[p].insert(0, pdata.get("nickname", f"Player{p}"))
                    self.player_colors[p] = pdata.get("color", self.player_colors[p])
                    self.color_buttons[p].config(bg=self.player_colors[p])
                    speed = float(pdata.get("speed", 5.0))
                    self.fall_speeds[p].set(speed)
                    self.speed_scales[p].set(speed)
                    self.speed_labels[p].config(text=f"{speed:.1f}")
                    is_bot = pdata.get("is_bot", False)
                    self.player_is_bot[p].set(is_bot)
                    if is_bot and "ai_type" in pdata:
                        ai_val = str(pdata.get("ai_type", "")).lower()
                        if ai_val in ai_mapping:
                            self.player_ai_type[p].set(ai_mapping[ai_val])
        except Exception as e:
            Log.error(f"Settings load error: {e}")
            messagebox.showerror("Load Error", str(e))
            return

        for p in range(1, 5):
            self.update_player_state(p)
        self.on_game_mode_change()
        self.on_network_mode_change()
        Log.info("Settings applied to UI.")

    def exit_game(self):
        if messagebox.askyesno("Exit", "Really quit?", parent=self.root):
            self.on_closing()

    def on_closing(self):
        if getattr(self, "self_learning_running", False):
            if not messagebox.askyesno("Tetris Setup", "Training is still running.\nStop and close?",
                                       parent=self.root):
                return
            if hasattr(self, "self_learning_stop_event") and self.self_learning_stop_event:
                self.self_learning_stop_event.set()
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        self.root.mainloop()


class GameplayViewer(tk.Toplevel):
    def __init__(self, parent, width=15, height=30, cell_size=18):
        super().__init__(parent)
        self.title("Self-Learning - Bot gameplay")
        self.configure(bg="#111")
        self.transient(parent)
        self.width = width
        self.height = height
        self.cell_size = cell_size
        canvas_w = width * cell_size
        canvas_h = height * cell_size
        self.canvas = tk.Canvas(self, width=canvas_w, height=canvas_h, bg="#1a1a1a", highlightthickness=0)
        self.canvas.pack(padx=10, pady=10)
        self.info_label = tk.Label(self, text=" ", bg="#111", fg="#00ff88", font=("Consolas", 10))
        self.info_label.pack(pady=(0, 8))
        self._closed = False
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.destroy()
        except Exception:
            pass

    def update_frame(self, grid, score, lines, piece_name, placed_pieces):
        if self._closed:
            return
        try:
            self.canvas.delete("all")
            cs = self.cell_size
            for row in range(self.height):
                for col in range(self.width):
                    x1, y1 = col * cs, row * cs
                    x2, y2 = x1 + cs, y1 + cs
                    color = None
                    if row < len(grid) and col < len(grid[row]):
                        color = grid[row][col]
                    fill = self._color_to_hex(color) if color else "#1a1a1a"
                    self.canvas.create_rectangle(x1, y1, x2, y2, fill=fill, outline="#333", width=1)
            self.info_label.config(
                text=f"Piece: {placed_pieces} | Score: {score} | Lines: {lines} | Shape: {piece_name}"
            )
            self.update_idletasks()
        except tk.TclError:
            self._closed = True

    @staticmethod
    def _color_to_hex(color):
        if isinstance(color, str) and color.startswith("#"):
            return color
        if isinstance(color, (tuple, list)) and len(color) >= 3:
            return f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"
        return "#888888"


# ================= APP CONTROLLER =================

class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.geometry("430x640")
        self.root.configure(bg="#111")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.current_user = None
        self.current_profile = None

        Log.info("Showing auth window")
        self.show_auth()

    def show_auth(self):
        self.root.title("Tetris MP - Login")
        self.root.geometry("430x640")
        Log.info("Initializing AuthWindow class")
        AuthWindow(self.root, self.on_auth_success)

    def on_auth_success(self, username, profile=None):
        self.current_user = username
        self.current_profile = profile or get_user_profile(username)
        Log.info(f"User {username} logged in")
        self.show_main_menu()

    def show_main_menu(self):
        self.root.title("Tetris MP - Main menu")
        self.root.geometry("400x350")
        Log.info("Initializing MainMenuWindow")
        MainMenuWindow(
            self.root,
            username=self.current_user,
            on_play=self.start_setup,
            on_leaderboard=self.show_leaderboard,
            on_logout=self.logout,
        )

    def start_setup(self):
        self.root.withdraw()
        setup = TetrisSetup(self.root, username=self.current_user, profile=self.current_profile)
        self.root.wait_window(setup.root)
        self.root.deiconify()
        self.show_main_menu()

    def show_leaderboard(self):
        LeaderboardWindow(self.root)

    def logout(self):
        Log.info(f"User {self.current_user} logged out")
        self.current_user = None
        self.current_profile = None
        self.show_auth()

    def on_closing(self):
        Log.info("Shutting down...")
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)

    def run(self):
        self.root.mainloop()


# ================= MAIN EXECUTION =================

if __name__ == "__main__":
    init_db()
    app = App()
    app.run()
