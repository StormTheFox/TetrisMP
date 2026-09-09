import tkinter as tk
from tkinter import ttk, colorchooser, messagebox, filedialog
from tkinter.font import Font
import json
import sqlite3
import hashlib
import pygame
import sys
import os
from tetris import Game
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
def init_db():
    conn = sqlite3.connect('tetris_db.sqlite')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (username TEXT PRIMARY KEY, password_hash TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS leaderboard
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT, score INTEGER, is_bot INTEGER, date TEXT)''')
    conn.commit()
    conn.close()

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

# ================= AUTH UI =================
class AuthWindow:
    def __init__(self, root, on_success):
        self.root = root
        self.on_success = on_success
        self.root.title("Tetris MP - Вход")
        self.root.geometry("350x300")
        self.root.resizable(False, False)
        self.root.configure(bg='#111')

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True, padx=10, pady=10)

        self.login_frame = tk.Frame(self.notebook, bg='#111')
        self.register_frame = tk.Frame(self.notebook, bg='#111')
        self.notebook.add(self.login_frame, text='Вход')
        self.notebook.add(self.register_frame, text='Регистрация')

        self.build_login()
        self.build_register()

    def build_login(self):
        f = self.login_frame
        tk.Label(f, text="Никнейм:", bg='#111', fg='#eee').pack(pady=(20, 5))
        self.login_user = tk.Entry(f, bg='#333', fg='#fff', insertbackground='#fff')
        self.login_user.pack()

        tk.Label(f, text="Пароль:", bg='#111', fg='#eee').pack(pady=(10, 5))
        self.login_pass = tk.Entry(f, show="*", bg='#333', fg='#fff', insertbackground='#fff')
        self.login_pass.pack()

        tk.Button(f, text="Войти", bg='#0f3460', fg='#fff', command=self.check_login).pack(pady=15)

    def build_register(self):
        f = self.register_frame
        tk.Label(f, text="Новый никнейм:", bg='#111', fg='#eee').pack(pady=(20, 5))
        self.reg_user = tk.Entry(f, bg='#333', fg='#fff', insertbackground='#fff')
        self.reg_user.pack()

        tk.Label(f, text="Пароль:", bg='#111', fg='#eee').pack(pady=(10, 5))
        self.reg_pass = tk.Entry(f, show="*", bg='#333', fg='#fff', insertbackground='#fff')
        self.reg_pass.pack()

        tk.Button(f, text="Зарегистрироваться", bg='#2d6a4f', fg='#fff', command=self.register_user).pack(pady=15)

    def check_login(self):
        user = self.login_user.get().strip()
        pwd = self.login_pass.get()
        if not user or not pwd:
            messagebox.showerror("Ошибка", "Заполните все поля!")
            return

        conn = sqlite3.connect('tetris_db.sqlite')
        c = conn.cursor()
        c.execute("SELECT password_hash FROM users WHERE username=?", (user,))
        row = c.fetchone()
        conn.close()

        if row and row[0] == hash_password(pwd):
            self.on_success(user)
        else:
            messagebox.showerror("Ошибка", "Неверный логин или пароль!")

    def register_user(self):
        user = self.reg_user.get().strip()
        pwd = self.reg_pass.get()
        if not user or not pwd:
            messagebox.showerror("Ошибка", "Заполните все поля!")
            return
        if len(pwd) < 4:
            messagebox.showerror("Ошибка", "Пароль должен быть минимум 4 символа!")
            return

        conn = sqlite3.connect('tetris_db.sqlite')
        c = conn.cursor()
        try:
            c.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                      (user, hash_password(pwd)))
            conn.commit()
            messagebox.showinfo("Успех", "Регистрация прошла успешно! Теперь войдите.")
            self.notebook.select(0)
        except sqlite3.IntegrityError:
            messagebox.showerror("Ошибка", "Такой никнейм уже занят!")
        finally:
            conn.close()

# ================= MAIN MENU =================
class MainMenuWindow:
    def __init__(self, root, username, on_play, on_leaderboard, on_logout):
        self.root = root
        self.username = username
        self.on_play = on_play
        self.on_leaderboard = on_leaderboard
        self.on_logout = on_logout

        for w in self.root.winfo_children():
            w.destroy()

        self.root.title(f"Tetris MP - Главное меню ({self.username})")
        self.root.geometry("400x350")
        self.root.resizable(False, False)
        self.root.configure(bg='#111')

        tk.Label(self.root, text="🎮 TETRIS MP", bg='#111', fg='#e94560',
                 font=("Helvetica", 24, "bold")).pack(pady=(30, 5))
        tk.Label(self.root, text=f"Привет, {self.username}!", bg='#111', fg='#eee',
                 font=("Helvetica", 14)).pack(pady=(0, 30))

        btn_style = {'font': ("Helvetica", 12, "bold"), 'width': 20, 'height': 2, 'bd': 0, 'cursor': 'hand2'}
        tk.Button(self.root, text="▶ Играть", bg='#0f3460', fg='#fff',
                  command=self.on_play, **btn_style).pack(pady=8)
        tk.Button(self.root, text="🏆 Таблица лидеров", bg='#533483', fg='#fff',
                  command=self.on_leaderboard, **btn_style).pack(pady=8)
        tk.Button(self.root, text="🚪 Выйти из аккаунта", bg='#444', fg='#fff',
                  command=self.on_logout, **btn_style).pack(pady=8)

# ================= LEADERBOARD UI =================
class LeaderboardWindow:
    def __init__(self, parent):
        self.win = tk.Toplevel(parent)
        self.win.title("Таблица лидеров")
        self.win.geometry("450x500")
        self.win.configure(bg='#111')
        self.win.transient(parent)
        self.win.grab_set()

        tk.Label(self.win, text="🏆 ТОП-20 ИГРОКОВ", bg='#111', fg='#e94560',
                 font=("Helvetica", 16, "bold")).pack(pady=10)

        columns = ("place", "username", "score", "is_bot", "date")
        self.tree = ttk.Treeview(self.win, columns=columns, show="headings", height=20)
        self.tree.heading("place", text="#")
        self.tree.heading("username", text="Никнейм")
        self.tree.heading("score", text="Очки")
        self.tree.heading("is_bot", text="Бот?")
        self.tree.heading("date", text="Дата")

        self.tree.column("place", width=30, anchor='center')
        self.tree.column("username", width=120)
        self.tree.column("score", width=80, anchor='center')
        self.tree.column("is_bot", width=50, anchor='center')
        self.tree.column("date", width=100)

        self.tree.pack(fill='both', expand=True, padx=10, pady=5)
        self.load_data()

    def load_data(self):
        conn = sqlite3.connect('tetris_db.sqlite')
        c = conn.cursor()
        c.execute("SELECT username, score, is_bot, date FROM leaderboard ORDER BY score DESC LIMIT 20")
        rows = c.fetchall()
        conn.close()

        for i, row in enumerate(rows, 1):
            user, score, is_bot, date = row
            bot_str = "Да" if is_bot else "Нет"
            short_date = date.split('T')[0] if date else ""
            self.tree.insert("", 'end', values=(i, user, score, bot_str, short_date))

# ================= SETTINGS UI =================
class KeybindSettings(tk.Toplevel):
    def __init__(self, master, current_keybinds, on_save_callback):
        super().__init__(master)
        self.title("Настройка управления (Игрок 1)")
        self.geometry("320x420")
        self.resizable(False, False)
        self.configure(bg='#000')
        self.transient(master)
        self.grab_set()

        self.on_save = on_save_callback
        self.keybinds = current_keybinds.copy()
        self.vars = {}
        self.build_ui()

    def build_ui(self):
        tk.Label(self, text="⌨️ Настройка клавиш", bg='#000', fg='#e94560',
                 font=("Helvetica", 16, "bold")).pack(pady=15)

        container = tk.Frame(self, bg='#111')
        container.pack(fill='both', expand=True, padx=15, pady=10)

        actions = [
            ('hard_drop', 'Hard Drop'), ('rotate', 'Поворот'), ('left', 'Влево'),
            ('right', 'Вправо'), ('soft_drop', 'Soft Drop'), ('hold', 'Hold')
        ]

        for action, name in actions:
            frame = tk.Frame(container, bg='#111')
            frame.pack(fill='x', pady=5)
            tk.Label(frame, text=name, bg='#111', fg='#eeeeee', font=("Helvetica", 11)).pack(side='left')

            val = self.keybinds.get(action, '')
            if isinstance(val, int):
                try: val = pygame.key.name(val)
                except: val = str(val)

            var = tk.StringVar(self, value=val)
            self.vars[action] = var

            btn = tk.Button(frame, textvariable=var, bg='#0f3460', fg='white',
                            font=("Helvetica", 10), width=12,
                            command=lambda v=var, a=action: self.record_key(v, a))
            btn.pack(side='right')

        btn_frame = tk.Frame(self, bg='#000')
        btn_frame.pack(pady=15)
        tk.Button(btn_frame, text="💾 Сохранить", bg='#0f3460', fg='white',
                  font=("Helvetica", 10, "bold"), command=self.save_and_close).pack(side='left', padx=8)
        tk.Button(btn_frame, text="🔄 Сбросить", bg='#444', fg='white',
                  font=("Helvetica", 10, "bold"), command=self.reset_defaults).pack(side='left', padx=8)
        tk.Button(btn_frame, text="❌ Отмена", bg='#333', fg='white',
                  font=("Helvetica", 10, "bold"), command=self.destroy).pack(side='left', padx=8)

    def record_key(self, var, action):
        var.set("Нажмите...")
        def on_key(event):
            key_name = event.keysym.lower()
            if key_name in ['shift_l', 'shift_r']: key_name = 'shift'
            elif key_name in ['control_l', 'control_r']: key_name = 'ctrl'
            elif key_name in ['alt_l', 'alt_r']: key_name = 'alt'
            var.set(key_name)
            self.keybinds[action] = key_name
            self.unbind('<Key>')
            self.master.focus_set()

        self.bind('<Key>', on_key)
        self.focus_set()

    def reset_defaults(self):
        defaults = {'hard_drop': 'q', 'rotate': 'w', 'left': 'a', 'right': 'd', 'soft_drop': 's', 'hold': 'e'}
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
        self.geometry("420x680")
        self.resizable(False, False)
        self.configure(bg='#000')
        self.transient(master)
        self.grab_set()

        self.on_save = on_save_callback
        self.current_config = current_config.copy()
        self.weights = {}
        self.labels = {}
        self.build_ui()

    def build_ui(self):
        tk.Label(self, text="⚙️ Custom AI Heuristics", bg='#000', fg='#e94560',
                 font=("Helvetica", 16, "bold")).pack(pady=15)

        container = tk.Frame(self, bg='#111')
        container.pack(fill='both', expand=True, padx=15, pady=10)

        settings = [
            ("height", "📏 Aggregate Height", "Штраф за общую высоту столбов.", -0.51),
            ("lines", "🧱 Lines Cleared", "Бонус за очищенные линии.", 0.76),
            ("holes", "🕳️ Holes", "Жёсткий штраф за пустые клетки под блоками.", -0.36),
            ("bumpiness", "📉 Bumpiness", "Штраф за перепады высот.", -0.18),
            ("well_depth", "🌊 Well Depth", "Штраф за глубокие вертикальные ямы.", -0.15)
        ]

        for key, name, desc, default_val in settings:
            frame = tk.Frame(container, bg='#111')
            frame.pack(fill='x', pady=10)
            tk.Label(frame, text=name, bg='#111', fg='#eeeeee', font=("Helvetica", 11, "bold")).pack(anchor='w')
            tk.Label(frame, text=desc, bg='#111', fg='#aaaaaa', font=("Helvetica", 8),
                     wraplength=360, justify='left').pack(anchor='w', pady=(0, 4))

            val = self.current_config.get(key, default_val)
            var = tk.DoubleVar(self, value=val)
            self.weights[key] = var

            scale_frame = tk.Frame(frame, bg='#111')
            scale_frame.pack(fill='x')
            tk.Scale(scale_frame, from_=-2.0, to=2.0, resolution=0.01, orient='horizontal',
                     variable=var, bg='#111', fg='#e94560', troughcolor='#333',
                     length=300, showvalue=0,
                     command=lambda v, k=key: self.update_label(k, v)).pack(side='left', fill='x', expand=True)

            lbl = tk.Label(scale_frame, text=f"{val:.2f}", bg='#111', fg='#eeeeee', font=("Helvetica", 9, "bold"), width=5)
            lbl.pack(side='right')
            self.labels[key] = lbl

        btn_frame = tk.Frame(self, bg='#000')
        btn_frame.pack(pady=15)
        tk.Button(btn_frame, text="💾 Save & Apply", bg='#0f3460', fg='white',
                  font=("Helvetica", 10, "bold"), command=self.save_and_close).pack(side='left', padx=8)
        tk.Button(btn_frame, text="🔄 Reset", bg='#444', fg='white',
                  font=("Helvetica", 10, "bold"), command=self.reset_defaults).pack(side='left', padx=8)
        tk.Button(btn_frame, text="❌ Cancel", bg='#333', fg='white',
                  font=("Helvetica", 10, "bold"), command=self.destroy).pack(side='left', padx=8)

    def update_label(self, key, value):
        self.labels[key].config(text=f"{float(value):.2f}")

    def reset_defaults(self):
        defaults = {"height": -0.51, "lines": 0.76, "holes": -0.36, "bumpiness": -0.18, "well_depth": -0.15}
        for key, var in self.weights.items():
            var.set(defaults[key])
            self.update_label(key, defaults[key])

    def save_and_close(self):
        config = {k: round(v.get(), 3) for k, v in self.weights.items()}
        self.on_save(config)
        self.destroy()

# ================= SETUP UI =================
class TetrisSetup:
    def __init__(self, username="Guest"):
        self.current_user = username
        self.root = tk.Tk()
        self.root.title(f"Tetris MP – Setup ({self.current_user})")
        self.root.geometry("850x550")
        self.root.resizable(False, False)
        self.root.configure(bg='#000')

        # Обработчик закрытия окна
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.colors = {
            'bg': '#000', 'frame_bg': '#111', 'accent': '#e94560',
            'text': '#eeeeee', 'button': '#0f3460', 'button_hover': '#533483'
        }
        self.player_colors = {1: '#FF4444', 2: '#44FF44', 3: '#4444FF', 4: '#FFFF44'}

        # ИСПРАВЛЕНО: Добавлен master=self.root для всех переменных, чтобы они корректно биндились
        self.player_enabled = {i: tk.BooleanVar(self.root, value=True) for i in range(1, 5)}
        self.player_is_bot = {i: tk.BooleanVar(self.root, value=False) for i in range(1, 5)}

        self.player_ai_type = {}
        for i in range(1, 5):
            self.player_ai_type[i] = tk.StringVar(self.root, value='DeepSeek')

        self.fall_speeds = {}
        self.nickname_entries = {}
        self.custom_ai_config = {}
        self.color_buttons = {}
        self.speed_scales = {}
        self.bot_checkboxes = {}
        self.ai_menus = {}
        self.speed_labels = {}

        self.self_learning_iters = tk.IntVar(self.root, value=20)
        self.self_learning_ai = tk.StringVar(self.root, value='Custom')
        self.teacher_student_delay = tk.IntVar(self.root, value=5)

        self.game_mode = tk.StringVar(self.root, value="vs")
        self.game_mode.trace_add("write", self._on_mode_change)
        self.room_name_var = tk.StringVar(self.root, value="tetris_room")

        self.dynamic_keybinds = {
            'hard_drop': 'q', 'rotate': 'w', 'left': 'a',
            'right': 'd', 'soft_drop': 's', 'hold': 'e'
        }

        self.setup_ui()

    def _on_mode_change(self, *args):
        self.on_game_mode_change()

    def on_game_mode_change(self):
        mode = self.game_mode.get()
        for w in (self.room_name_container, self.ml_container, self.ts_container):
            w.pack_forget()

        if mode in ['lan', 'global']:
            self.room_name_container.pack(fill='x', pady=10, before=self.bottom_frame)
        elif mode == 'self_learning':
            self.ml_container.pack(fill='x', pady=10, before=self.bottom_frame)
        elif mode == 'teacher_student':
            self.ts_container.pack(fill='x', pady=10, before=self.bottom_frame)
            self.player_enabled[1].set(True)
            self.player_enabled[2].set(True)
            self.player_is_bot[1].set(False)
            self.player_is_bot[2].set(True)
            self.player_ai_type[2].set('Student')
            for p in range(1, 3):
                self.update_player_state(p)

    def setup_ui(self):
        main = tk.Frame(self.root, bg=self.colors['bg'])
        main.pack(fill='both', expand=True, padx=20, pady=20)

        title = tk.Label(main, text="TETRIS ⬛ SETUP", font=Font(family="Helvetica", size=24, weight="bold"),
                         bg=self.colors['bg'], fg=self.colors['accent'])
        title.pack(pady=(0, 20))

        top = tk.Frame(main, bg=self.colors['bg'])
        top.pack(fill='both', expand=True)

        left = tk.LabelFrame(top, text="Game Mode", bg=self.colors['frame_bg'], fg=self.colors['text'],
                             font=("Helvetica", 12, "bold"), padx=20, pady=15)
        left.pack(side='left', fill='both', expand=True, padx=(0, 10))

        modes = [
            ("VS", "vs"), ("CO-OP", "coop"), ("2 VS 2", "2vs2"),
            ("Local LAN", "lan"), ("Global", "global"),
            ("🧬 Self-Learning", "self_learning"),
            ("🎓 Teacher-Student", "teacher_student")
        ]
        for text, value in modes:
            tk.Radiobutton(left, text=text, variable=self.game_mode, value=value,
                           bg=self.colors['frame_bg'], fg=self.colors['text'],
                           selectcolor=self.colors['frame_bg'], font=("Helvetica", 11)).pack(anchor='w', pady=5)

        right = tk.LabelFrame(top, text="Players Configuration", bg=self.colors['frame_bg'], fg=self.colors['text'],
                              font=("Helvetica", 12, "bold"), padx=15, pady=10)
        right.pack(side='right', fill='both', expand=True, padx=(10, 0))

        headers = ["Nick", "On", "Color", "Speed", "Bot", "AI Type"]
        for col, h in enumerate(headers):
            tk.Label(right, text=h, bg=self.colors['frame_bg'], fg=self.colors['accent'],
                     font=("Helvetica", 10, "bold")).grid(row=0, column=col, padx=5, pady=5)

        for p in range(1, 5):
            entry = tk.Entry(right, bg='#222', fg=self.colors['text'], width=10)
            entry.insert(0, f"Player{p}")
            entry.grid(row=p, column=0, padx=5, pady=5)
            self.nickname_entries[p] = entry

            cb_en = tk.Checkbutton(right, variable=self.player_enabled[p], bg=self.colors['frame_bg'],
                                   command=lambda pl=p: self.update_player_state(pl))
            cb_en.grid(row=p, column=1, padx=5, pady=5)

            btn = tk.Button(right, text="Pick", bg=self.player_colors[p], fg='white',
                            command=lambda x=p: self.choose_color(x))
            btn.grid(row=p, column=2, padx=5, pady=5)
            self.color_buttons[p] = btn

            speed_frame = tk.Frame(right, bg=self.colors['frame_bg'])
            speed_frame.grid(row=p, column=3, padx=5, pady=5)
            
            lbl = tk.Label(speed_frame, text="5.0", bg=self.colors['frame_bg'], fg=self.colors['text'], font=("Helvetica", 8))
            lbl.pack(anchor='n')
            self.speed_labels[p] = lbl

            # ИСПРАВЛЕНО: Инициализация скорости с явным указанием master
            speed_var = tk.DoubleVar(self.root, value=5.0)
            self.fall_speeds[p] = speed_var

            scale = tk.Scale(speed_frame, from_=0.1, to=10.0, resolution=0.1, orient='horizontal',
                             variable=speed_var, bg=self.colors['frame_bg'], fg=self.colors['text'],
                             length=80, showvalue=0,
                             command=lambda v, pl=p: self.speed_labels[pl].config(text=f"{float(v):.1f}"))
            scale.set(5.0)
            scale.pack()
            self.speed_scales[p] = scale

            cb_bot = tk.Checkbutton(right, variable=self.player_is_bot[p], bg=self.colors['frame_bg'],
                                    command=lambda pl=p: self.update_player_state(pl))
            cb_bot.grid(row=p, column=4, padx=5, pady=5)
            self.bot_checkboxes[p] = cb_bot

            # ИСПРАВЛЕНО: Создаем OptionMenu с правильной переменной
            ai_menu = tk.OptionMenu(right, self.player_ai_type[p], "Qwen", "DeepSeek", "Custom")
            ai_menu.config(bg='#222', fg='white', width=8)
            ai_menu.grid(row=p, column=5, padx=5, pady=5)
            self.ai_menus[p] = ai_menu

        for p in range(1, 5):
            self.update_player_state(p)

        self.room_name_container = tk.Frame(main, bg=self.colors['bg'])
        tk.Label(self.room_name_container, text="Название комнаты:", bg=self.colors['bg'], fg=self.colors['text'], font=("Helvetica", 12, "bold")).pack(side='left', padx=5)
        self.room_name_entry = tk.Entry(self.room_name_container, textvariable=self.room_name_var, bg='#222', fg=self.colors['text'], font=("Helvetica", 12), width=20)
        self.room_name_entry.pack(side='left', padx=5)

        self.keybind_btn = tk.Button(self.room_name_container, text="⌨️ Настроить клавиши", bg='#533483', fg='white', font=("Helvetica", 10, "bold"), command=self.open_keybind_settings)
        self.keybind_btn.pack(side='left', padx=15)

        self.ml_container = tk.Frame(main, bg=self.colors['bg'])
        tk.Label(self.ml_container, text="Итераций:", bg=self.colors['bg'],
                 fg=self.colors['text'], font=("Helvetica", 11)).pack(side='left', padx=5)
        tk.Spinbox(self.ml_container, from_=1, to=500, textvariable=self.self_learning_iters,
                   bg='#222', fg='white', width=6).pack(side='left', padx=5)
        tk.Label(self.ml_container, text="AI:", bg=self.colors['bg'],
                 fg=self.colors['text'], font=("Helvetica", 11)).pack(side='left', padx=(15, 5))
        tk.OptionMenu(self.ml_container, self.self_learning_ai,
                      "Custom", "Qwen", "DeepSeek").pack(side='left')

        self.ts_container = tk.Frame(main, bg=self.colors['bg'])
        tk.Label(self.ts_container, text="Задержка ученика (тики):",
                 bg=self.colors['bg'], fg=self.colors['text'], font=("Helvetica", 11)).pack(side='left', padx=5)
        tk.Spinbox(self.ts_container, from_=0, to=60, textvariable=self.teacher_student_delay,
                   bg='#222', fg='white', width=6).pack(side='left', padx=5)

        self.bottom_frame = tk.LabelFrame(main, text="Controls", bg=self.colors['frame_bg'], fg=self.colors['text'],
                                          font=("Helvetica", 12, "bold"), padx=20, pady=15)
        self.bottom_frame.pack(fill='x', pady=(20, 0))

        btn_frame = tk.Frame(self.bottom_frame, bg=self.colors['frame_bg'])
        btn_frame.pack()

        style = {'font': ("Helvetica", 11, "bold"), 'padx': 15, 'pady': 8, 'bd': 2}
        tk.Button(btn_frame, text="Start Game", bg=self.colors['button'], fg='white', command=self.start_game, **style).pack(side='left', padx=5)
        tk.Button(btn_frame, text="Save Settings", bg='#2d6a4f', fg='white', command=self.save_settings, **style).pack(side='left', padx=5)
        tk.Button(btn_frame, text="Load Settings", bg='#2d6a4f', fg='white', command=self.load_settings, **style).pack(side='left', padx=5)
        tk.Button(btn_frame, text="🤖 Custom AI", bg='#533483', fg='white', command=self.open_custom_ai_settings, **style).pack(side='left', padx=5)
        tk.Button(btn_frame, text="Exit", bg='#dc3545', fg='white', command=self.exit_game, **style).pack(side='left', padx=5)

        self.on_game_mode_change()

    def update_player_state(self, player):
        is_enabled = self.player_enabled[player].get()
        is_bot = self.player_is_bot[player].get()
        state = 'normal' if is_enabled else 'disabled'

        self.color_buttons[player].config(state=state)
        self.speed_scales[player].config(state=state)
        self.nickname_entries[player].config(state=state)
        self.speed_labels[player].config(state=state)
        self.bot_checkboxes[player].config(state=state)

        if is_enabled and is_bot:
            self.ai_menus[player].config(state='normal')
        else:
            self.ai_menus[player].config(state='disabled')

    def choose_color(self, player):
        color = colorchooser.askcolor(title=f"Player {player} color", color=self.player_colors[player])
        if color[1]:
            self.player_colors[player] = color[1]
            self.color_buttons[player].config(bg=color[1])

    def open_keybind_settings(self):
        KeybindSettings(self.root, self.dynamic_keybinds, self.apply_keybinds)

    def apply_keybinds(self, keybinds):
        self.dynamic_keybinds = keybinds
        Log.info(f"Клавиши Игрока 1 обновлены: {keybinds}")

    def get_settings(self):
        players = {}
        for p in range(1, 5):
            if self.player_enabled[p].get():
                is_bot = self.player_is_bot[p].get()
                ai_type = self.player_ai_type[p].get().lower() if is_bot else None
                ai_config = self.custom_ai_config if ai_type == 'custom' else {}
                players[p] = {
                    'enabled': True, 'nickname': self.nickname_entries[p].get(),
                    'color': self.player_colors[p], 'speed': self.fall_speeds[p].get(),
                    'is_bot': is_bot, 'ai_type': ai_type, 'ai_config': ai_config
                }

        settings = {
            'game_mode': self.game_mode.get(),
            'players': players,
            'custom_ai_config': self.custom_ai_config,
            'self_learning_iters': self.self_learning_iters.get(),
            'self_learning_ai': self.self_learning_ai.get().lower(),
            'teacher_student_delay': self.teacher_student_delay.get(),
        }

        if self.game_mode.get() in ['lan', 'global']:
            settings['room_name'] = self.room_name_var.get()
            settings['dynamic_keymap'] = {1: self.dynamic_keybinds}
            settings['server_host'] = '127.0.0.1'
            settings['server_port'] = 8888
            settings['is_host'] = True

        return settings

    def open_custom_ai_settings(self):
        CustomAISettings(self.root, self.custom_ai_config, self.apply_custom_ai_config)

    def apply_custom_ai_config(self, config):
        self.custom_ai_config = config
        Log.info(f"Custom AI конфиг обновлён в UI: {config}")

    def start_game(self):
        settings = self.get_settings()
        settings['current_user'] = self.current_user
        if not settings['players']:
            messagebox.showwarning("No players", "Enable at least one player!")
            return

        # Скрываем окно настроек на время игры
        self.root.withdraw()
        
        game = Game(settings)
        game.run()
        
        # Когда игра закончится, уничтожаем окно настроек, 
        # чтобы App понял, что wait_window завершился, и вернул главное меню
        self.root.destroy()

    def save_settings(self):
        file = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON files", "*.json")])
        if file:
            with open(file, 'w') as f:
                json.dump(self.get_settings(), f, indent=2)

    def load_settings(self):
        file = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")])
        if not file: return

        try:
            with open(file, 'r') as f:
                data = json.load(f)

            Log.info("Файл настроек прочитан успешно.")
            self.game_mode.set(data['game_mode'])

            if 'custom_ai_config' in data:
                self.custom_ai_config = data['custom_ai_config']
            if 'dynamic_keymap' in data and 1 in data['dynamic_keymap']:
                self.dynamic_keybinds = data['dynamic_keymap'][1]

            for p_str, pdata in data['players'].items():
                p = int(p_str)
                if 1 <= p <= 4:
                    self.player_enabled[p].set(pdata.get('enabled', True))
                    self.nickname_entries[p].delete(0, tk.END)
                    self.nickname_entries[p].insert(0, pdata.get('nickname', f'Player{p}'))
                    self.player_colors[p] = pdata['color']
                    self.color_buttons[p].config(bg=pdata['color'])
                    
                    self.fall_speeds[p].set(pdata['speed'])
                    self.speed_scales[p].set(pdata['speed'])
                    self.speed_labels[p].config(text=f"{pdata['speed']:.1f}")

                    is_bot = pdata.get('is_bot', False)
                    self.player_is_bot[p].set(is_bot)
                    if is_bot and 'ai_type' in pdata:
                        ai_val = pdata['ai_type'].capitalize()
                        if ai_val in ['Qwen', 'Deepseek', 'Custom']:
                            self.player_ai_type[p].set(ai_val)
        except Exception as e:
            Log.error(f"Ошибка при загрузке настроек: {e}")
            messagebox.showerror("Load Error", str(e))
            return

        for p in range(1, 5):
            self.update_player_state(p)
        self.on_game_mode_change()
        Log.info("Настройки успешно применены к UI.")

    def exit_game(self):
        if messagebox.askyesno("Exit", "Really quit?"):
            self.on_closing()

    def on_closing(self):
        """Корректное завершение работы"""
        Log.info("Завершение работы...")
        self.root.quit()
        self.root.destroy()
        os._exit(0)  # Жесткий выход, убивает все фоновые потоки и C-расширения

    def run(self):
        self.root.mainloop()

# ================= APP CONTROLLER =================
class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.geometry("350x300")
        self.root.configure(bg='#111')
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.current_user = None
        self.show_auth()

    def show_auth(self):
        self.root.title("Tetris MP - Вход")
        self.root.geometry("350x300")
        AuthWindow(self.root, self.on_auth_success)

    def on_auth_success(self, username):
        self.current_user = username
        Log.info(f"✅ Пользователь {username} вошёл в систему")
        self.show_main_menu()

    def show_main_menu(self):
        self.root.title(f"Tetris MP - Главное меню")
        self.root.geometry("400x350")
        MainMenuWindow(
            self.root,
            username=self.current_user,
            on_play=self.start_setup,
            on_leaderboard=self.show_leaderboard,
            on_logout=self.logout
        )

    def start_setup(self):
        self.root.withdraw()
        setup = TetrisSetup(username=self.current_user)
        self.root.wait_window(setup.root)
        self.root.deiconify()
        self.show_main_menu()

    def show_leaderboard(self):
        LeaderboardWindow(self.root)

    def logout(self):
        Log.info(f"🚪 Пользователь {self.current_user} вышел")
        self.current_user = None
        self.show_auth()

    def on_closing(self):
        """Корректное завершение работы"""
        Log.info("Завершение работы...")
        self.root.quit()
        self.root.destroy()
        os._exit(0)  # Жесткий выход

    def run(self):
        self.root.mainloop()

# ================= MAIN EXECUTION =================
if __name__ == "__main__":
    init_db()
    app = App()
    app.run()
