import tkinter as tk
from tkinter import ttk, colorchooser, messagebox, filedialog
from tkinter.font import Font
import json
import pygame
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

            var = tk.StringVar(value=val)
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
            var = tk.DoubleVar(value=val)
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

class TetrisSetup:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Tetris MP – Setup")
        self.root.geometry("850x550")
        self.root.resizable(False, False)
        self.root.configure(bg='#000')

        self.colors = {
            'bg': '#000', 'frame_bg': '#111', 'accent': '#e94560',
            'text': '#eeeeee', 'button': '#0f3460', 'button_hover': '#533483'
        }
        self.player_colors = {1: '#FF4444', 2: '#44FF44', 3: '#4444FF', 4: '#FFFF44'}
        self.player_enabled = {i: tk.BooleanVar(value=True) for i in range(1,5)}
        self.player_is_bot = {i: tk.BooleanVar(value=False) for i in range(1,5)}
        self.player_ai_type = {i: tk.StringVar(value='Qwen') for i in range(1,5)}
        self.fall_speeds = {}
        self.nickname_entries = {}
        self.custom_ai_config = {}
        self.color_buttons = {}
        self.speed_scales = {}
        self.bot_checkboxes = {}
        self.ai_menus = {}
        self.speed_labels = {}

        self.room_name_var = tk.StringVar(value="tetris_room")
        self.dynamic_keybinds = {
            'hard_drop': 'q', 'rotate': 'w', 'left': 'a',
            'right': 'd', 'soft_drop': 's', 'hold': 'e'
        }

        self.game_mode = tk.StringVar(value="vs")
        self.game_mode.trace_add("write", self._on_mode_change)
        self.setup_ui()

    def _on_mode_change(self, *args):
        self.on_game_mode_change()

    def on_game_mode_change(self):
        mode = self.game_mode.get()
        if mode == 'lan':
            self.room_name_container.pack(fill='x', pady=10, before=self.bottom_frame)
            for p in [2, 3, 4]:
                self.player_enabled[p].set(False)
                self.update_player_state(p)
        else:
            self.room_name_container.pack_forget()
            for p in [2, 3, 4]:
                self.player_enabled[p].set(True)
                self.update_player_state(p)

    def setup_ui(self):
        main = tk.Frame(self.root, bg=self.colors['bg'])
        main.pack(fill='both', expand=True, padx=20, pady=20)

        title = tk.Label(main, text="TETRIS ⬛ SETUP", font=Font(family="Helvetica", size=24, weight="bold"),
                         bg=self.colors['bg'], fg=self.colors['accent'])
        title.pack(pady=(0,20))

        top = tk.Frame(main, bg=self.colors['bg'])
        top.pack(fill='both', expand=True)

        left = tk.LabelFrame(top, text="Game Mode", bg=self.colors['frame_bg'], fg=self.colors['text'],
                             font=("Helvetica", 12, "bold"), padx=20, pady=15)
        left.pack(side='left', fill='both', expand=True, padx=(0,10))

        modes = [("VS", "vs"), ("CO-OP", "coop"), ("2 VS 2", "2vs2"), ("LAN Multiplayer", "lan")]
        for text, value in modes:
            tk.Radiobutton(left, text=text, variable=self.game_mode, value=value,
                           bg=self.colors['frame_bg'], fg=self.colors['text'],
                           selectcolor=self.colors['frame_bg'], font=("Helvetica", 11)).pack(anchor='w', pady=5)

        right = tk.LabelFrame(top, text="Players Configuration", bg=self.colors['frame_bg'], fg=self.colors['text'],
                              font=("Helvetica", 12, "bold"), padx=15, pady=10)
        right.pack(side='right', fill='both', expand=True, padx=(10,0))

        headers = ["Nick", "On", "Color", "Speed", "Bot", "AI Type"]
        for col, h in enumerate(headers):
            tk.Label(right, text=h, bg=self.colors['frame_bg'], fg=self.colors['accent'],
                     font=("Helvetica", 10, "bold")).grid(row=0, column=col, padx=5, pady=5)

        for p in range(1,5):
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

            speed_var = tk.DoubleVar(value=5.0)
            self.fall_speeds[p] = speed_var
            scale = tk.Scale(speed_frame, from_=0.1, to=10.0, resolution=0.1, orient='horizontal',
                             variable=speed_var, bg=self.colors['frame_bg'], fg=self.colors['text'],
                             length=80, showvalue=0,
                             command=lambda v, pl=p: self.speed_labels[pl].config(text=f"{float(v):.1f}"))
            scale.pack()
            self.speed_scales[p] = scale

            cb_bot = tk.Checkbutton(right, variable=self.player_is_bot[p], bg=self.colors['frame_bg'],
                                    command=lambda pl=p: self.update_player_state(pl))
            cb_bot.grid(row=p, column=4, padx=5, pady=5)
            self.bot_checkboxes[p] = cb_bot

            ai_menu = tk.OptionMenu(right, self.player_ai_type[p], "Qwen", "DeepSeek", "Custom")
            ai_menu.config(bg='#222', fg='white', width=8)
            ai_menu.grid(row=p, column=5, padx=5, pady=5)
            self.ai_menus[p] = ai_menu

        for p in range(1, 5):
            self.update_player_state(p)

        # Room Name Container (initially hidden)
        self.room_name_container = tk.Frame(main, bg=self.colors['bg'])
        tk.Label(self.room_name_container, text="Название комнаты:", bg=self.colors['bg'], fg=self.colors['text'], font=("Helvetica", 12, "bold")).pack(side='left', padx=5)
        self.room_name_entry = tk.Entry(self.room_name_container, textvariable=self.room_name_var, bg='#222', fg=self.colors['text'], font=("Helvetica", 12), width=20)
        self.room_name_entry.pack(side='left', padx=5)
        self.keybind_btn = tk.Button(self.room_name_container, text="⌨️ Настроить клавиши", bg='#533483', fg='white', font=("Helvetica", 10, "bold"), command=self.open_keybind_settings)
        self.keybind_btn.pack(side='left', padx=15)

        # Нижняя панель с кнопками
        self.bottom_frame = tk.LabelFrame(main, text="Controls", bg=self.colors['frame_bg'], fg=self.colors['text'],
                                          font=("Helvetica", 12, "bold"), padx=20, pady=15)
        self.bottom_frame.pack(fill='x', pady=(20,0))

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

        settings = {'game_mode': self.game_mode.get(), 'players': players, 'custom_ai_config': self.custom_ai_config}
        if self.game_mode.get() == 'lan':
            settings['room_name'] = self.room_name_var.get()
            settings['dynamic_keymap'] = {1: self.dynamic_keybinds}
        return settings

    def open_custom_ai_settings(self):
        CustomAISettings(self.root, self.custom_ai_config, self.apply_custom_ai_config)

    def apply_custom_ai_config(self, config):
        self.custom_ai_config = config
        Log.info(f"Custom AI конфиг обновлён в UI: {config}")

    def start_game(self):
        settings = self.get_settings()
        if not settings['players']:
            messagebox.showwarning("No players", "Enable at least one player!")
            return
        self.root.destroy()
        game = Game(settings)
        game.run()

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
            self.root.destroy()

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    TetrisSetup().run()
