import pygame, random, copy, time, threading, asyncio, json, sqlite3
from queue import Queue, Empty
from datetime import datetime
from typing import Dict, Any, Optional
from rich.console import Console
console = Console()

# ================= CONFIG =================
WIDTH = 15
HEIGHT = 30
CELL_SIZE = 10
MINI_BOARD_WIDTH = 100
PIECE_COLORS = {
    'I': (0, 255, 255), 'O': (255, 255, 0), 'T': (128, 0, 128),
    'S': (0, 255, 0), 'Z': (255, 0, 0), 'L': (255, 165, 0), 'J': (0, 0, 255)
}
SHAPES = {
    'I': [[1,1,1,1]], 'O': [[1,1],[1,1]], 'T': [[0,1,0],[1,1,1]],
    'S': [[0,1,1],[1,1,0]], 'Z': [[1,1,0],[0,1,1]], 'L': [[1,0,0],[1,1,1]], 'J': [[0,0,1],[1,1,1]]
}
KEYMAP = {
    1: {'hard_drop': pygame.K_q, 'rotate': pygame.K_w, 'left': pygame.K_a, 'right': pygame.K_d, 'soft_drop': pygame.K_s, 'hold': pygame.K_e},
    2: {'hard_drop': pygame.K_r, 'rotate': pygame.K_t, 'left': pygame.K_f, 'right': pygame.K_h, 'soft_drop': pygame.K_g, 'hold': pygame.K_y},
    3: {'hard_drop': pygame.K_u, 'rotate': pygame.K_i, 'left': pygame.K_j, 'right': pygame.K_l, 'soft_drop': pygame.K_k, 'hold': pygame.K_o},
    4: {'hard_drop': pygame.K_RSHIFT, 'rotate': pygame.K_UP, 'left': pygame.K_LEFT, 'right': pygame.K_RIGHT, 'soft_drop': pygame.K_DOWN, 'hold': pygame.K_RCTRL}
}
DAS_DELAY = 200
DAS_REPEAT = 25
LINE_SCORES = {1: 100, 2: 250, 3: 500, 4: 1000}
GAME_MODES = ['vs', 'coop', '2vs2', 'lan', 'global', 'self_learning', 'teacher_student']
SELF_LEARNING_DEFAULT_ITERATIONS = 20
SELF_LEARNING_MUTATION_RATE = 0.05
TEACHER_STUDENT_DELAY = 5

class Log:
    @staticmethod
    def info(msg: str, timestamp: bool = True, **kwargs): console.print(f"[cyan]INFO[/cyan]: {msg}")
    @staticmethod
    def error(msg: str, timestamp: bool = True, **kwargs): console.print(f"[red]ERROR[/red]: {msg}")
    @staticmethod
    def warning(msg: str, timestamp: bool = True, **kwargs): console.print(f"[yellow]WARNING[/yellow]: {msg}")
    @staticmethod
    def debug(msg: str, timestamp: bool = True, **kwargs): console.print(f"[green]DEBUG[/green]: {msg}")

class Piece:
    def __init__(self, shape_name, color):
        self.shape_name = shape_name
        self.shape = [row[:] for row in SHAPES[shape_name]]
        self.color = color
        self.x = WIDTH // 2 - len(self.shape[0]) // 2
        self.y = 0
        self.rotation = 0

    def rotate(self):
        self.shape = [list(row) for row in zip(*self.shape[::-1])]
        self.rotation = (self.rotation + 1) % 4

    def move(self, dx, dy):
        self.x += dx
        self.y += dy

    def get_cells(self):
        cells = []
        for r, row in enumerate(self.shape):
            for c, val in enumerate(row):
                if val:
                    cells.append((self.x + c, self.y + r))
        return cells

class Board:
    def __init__(self, width, height, player_color=None):
        self.width = width
        self.height = height
        self.grid = [[None for _ in range(width)] for _ in range(height)]
        self.player_color = player_color
        self.lines_cleared_total = 0
        self.score = 0

    def is_valid_position(self, piece, ignore_active_pieces=True):
        for x, y in piece.get_cells():
            if x < 0 or x >= self.width or y >= self.height:
                return False
            if y >= 0 and self.grid[y][x] is not None:
                return False
        return True

    def place_piece(self, piece):
        for x, y in piece.get_cells():
            if y >= 0:
                self.grid[y][x] = piece.color
        lines = self.clear_lines()
        self.lines_cleared_total += lines
        self.score += LINE_SCORES.get(lines, 0)
        return lines

    def clear_lines(self):
        lines_cleared = 0
        y = self.height - 1
        while y >= 0:
            if all(self.grid[y][x] is not None for x in range(self.width)):
                for yy in range(y, 0, -1):
                    self.grid[yy] = self.grid[yy-1][:]
                self.grid[0] = [None] * self.width
                lines_cleared += 1
            else:
                y -= 1
        return lines_cleared

    def drop_height(self, piece):
        y = piece.y
        while self.is_valid_position(piece):
            y += 1
            piece.y = y
        piece.y = y - 1
        return piece.y

# ================= AI ARCHITECTURE =================
# (Оставлено без изменений для краткости, так как проблема была не в ИИ)
class BaseAI:
    def __init__(self, player, config=None):
        self.player = player
        self.config = config or {}
        self.action_queue = []
        self._result_queue = Queue()
        self._compute_thread = None
        self._is_computing = False
        self._precompute_rotations()
        self.weights = {
            'height': -0.510066, 'lines': 0.760666,
            'holes': -0.356630, 'bumpiness': -0.184483,
            'well_depth': -0.15, 'transitions': -0.03
        }

    def _precompute_rotations(self):
        self.rotations = {}
        for name, shape in SHAPES.items():
            rots = [shape]
            s = [row[:] for row in shape]
            for _ in range(3):
                s = [list(row) for row in zip(*s[::-1])]
                rots.append(s)
            self.rotations[name] = rots

    def _capture_state(self):
        return {
            'grid': [row[:] for row in self.player.board.grid],
            'width': self.player.board.width, 'height': self.player.board.height,
            'curr_shape': self.player.current_piece.shape_name,
            'hold_shape': self.player.hold_piece.shape_name if self.player.hold_piece else None,
            'hold_used': self.player.hold_used,
            'next_shapes': [p.shape_name for p in self.player.next_pieces]
        }

    def _fast_drop(self, shape, grid, width, height, offset_x):
        sh, sw = len(shape), len(shape[0])
        y = 0
        while y + sh <= height:
            collides = False
            for r, row in enumerate(shape):
                for c, val in enumerate(row):
                    if val:
                        nx, ny = offset_x + c, y + r
                        if nx < 0 or nx >= width or grid[ny][nx] is not None:
                            collides = True; break
                if collides: break
            if collides: break
            y += 1
        return y - 1

    def evaluate(self, shape, x, drop_y, grid, width, height):
        sim_grid = [row[:] for row in grid]
        for r, row in enumerate(shape):
            for c, val in enumerate(row):
                if val: sim_grid[drop_y + r][x + c] = 1
        
        heights, holes, lines, bumpiness = [0]*width, 0, 0, 0
        for col in range(width):
            h = 0
            for row in range(height):
                if sim_grid[row][col] is not None: h = height - row; break
            heights[col] = h
            found = False
            for row in range(height):
                if sim_grid[row][col] is not None: found = True
                elif found: holes += 1
        for row in range(height):
            if all(sim_grid[row][col] is not None for col in range(width)): lines += 1
        for col in range(width - 1): bumpiness += abs(heights[col] - heights[col+1])
        
        return (self.weights['height'] * (sum(heights)/width) +
                self.weights['lines'] * lines +
                self.weights['holes'] * holes +
                self.weights['bumpiness'] * bumpiness)

    def _compute_in_background(self, state):
        t0 = time.time()
        try:
            candidates = [('current', state['curr_shape'], False)]
            if not state['hold_used']:
                if state['hold_shape']:
                    candidates.append(('hold', state['hold_shape'], True))
                elif len(state['next_shapes']) > 0:
                    candidates.append(('next', state['next_shapes'][0], True))
            
            best_score, best_plan = float('-inf'), None
            w, h, grid = state['width'], state['height'], state['grid']
            
            for src, shape_name, use_hold in candidates:
                for rot_idx, shape in enumerate(self.rotations[shape_name]):
                    sw = len(shape[0])
                    if sw > w: continue
                    min_x, max_x = max(0, -(sw - 1)), min(w - 1, w - sw)
                    for x in range(min_x, max_x + 1):
                        y = self._fast_drop(shape, grid, w, h, x)
                        if y < 0: continue
                        score = self.evaluate(shape, x, y, grid, w, h)
                        if score > best_score:
                            best_score, best_plan = score, (use_hold, x, rot_idx)
            
            elapsed = (time.time() - t0) * 1000
            Log.debug(f"🧠 ИИ (Игрок {self.player.id}): просчёт за {elapsed:.1f}мс | Score: {best_score:.2f} | План: {best_plan}")
            self._result_queue.put(best_plan)
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            Log.error(f"🧠 ИИ (Игрок {self.player.id}): краш потока за {elapsed:.1f}мс -> {e}")
            self._result_queue.put(None)

    def _generate_actions(self, plan):
        if plan is None:
            if not hasattr(self, '_warned_none'):
                Log.warning(f"⚠️ ИИ (Игрок {self.player.id}): план не найден, экстренный hard_drop")
                self._warned_none = True
            self.action_queue.append('hard_drop')
            return
        
        self._warned_none = False
        use_hold, target_x, target_rot = plan
        queue = []
        if use_hold: queue.append('hold')
        
        curr = self.player.current_piece
        dr = (target_rot - curr.rotation) % 4
        sim = copy.deepcopy(curr)
        sim.shape = [row[:] for row in self.rotations[sim.shape_name][target_rot]]
        sim.rotation = target_rot; sim.x = curr.x
        
        if not self.player.board.is_valid_position(sim):
            for kdx in [-1, 1, -2, 2]:
                sim.move(kdx, 0)
                if self.player.board.is_valid_position(sim): break
                sim.move(-kdx, 0)
        
        dx = target_x - sim.x
        queue.extend(['rotate'] * dr)
        queue.extend(['right'] * dx if dx > 0 else ['left'] * abs(dx))
        queue.append('hard_drop')
        
        Log.debug(f"📝 ИИ (Игрок {self.player.id}): сгенерирована очередь -> {queue}")
        self.action_queue = queue

    def get_action(self):
        if self.action_queue: return self.action_queue.pop(0)
        try:
            plan = self._result_queue.get_nowait()
            self._generate_actions(plan)
            self._is_computing = False
        except Empty: pass
        
        if self.action_queue: return self.action_queue.pop(0)
        
        if not self._is_computing:
            Log.debug(f"🚀 ИИ (Игрок {self.player.id}): запуск фоновой задачи")
            self._is_computing = True
            self._compute_thread = threading.Thread(target=self._compute_in_background, args=(self._capture_state(),), daemon=True)
            self._compute_thread.start()
        return None

class QwenAI(BaseAI):
    def __init__(self, player, config=None):
        super().__init__(player, config)
        # Упрощенная версия для примера, в реальном коде остается ваш оригинальный QwenAI
        self.weights = {
            'height': -0.62, 'lines': 0.95, 'holes': -0.85, 'bumpiness': -0.24,
            'well_depth': -0.18, 'max_height': -0.55, 'transitions': -0.006
        }

class CustomAI(BaseAI):
    def __init__(self, player, config=None):
        super().__init__(player, config)
        self.weights = {
            'height': config.get('height', -0.51),
            'lines': config.get('lines', 0.76),
            'holes': config.get('holes', -0.36),
            'bumpiness': config.get('bumpiness', -0.18),
            'well_depth': config.get('well_depth', -0.15),
            'transitions': config.get('transitions', -0.03)
        }

class StudentAI(BaseAI):
    def __init__(self, player, teacher_player, delay=5, config=None):
        super().__init__(player, config)
        self.teacher = teacher_player
        self.delay = max(0, int(delay))
        self.action_log = []
        self.tick = 0

    def log_teacher_action(self, action: str):
        self.action_log.append((self.tick + self.delay, action))

    def get_action(self):
        self.tick += 1
        while self.action_log and self.action_log[0][0] <= self.tick:
            return self.action_log.pop(0)[1]
        return None

class SelfLearningEngine:
    def __init__(self, iterations: int, ai_type: str, base_config: dict):
        self.iterations = max(1, int(iterations))
        self.ai_type = ai_type
        self.weights = dict(base_config) if base_config else {
            'height': -0.51, 'lines': 0.76, 'holes': -0.36,
            'bumpiness': -0.18, 'well_depth': -0.15
        }
        self.best_weights = dict(self.weights)
        self.best_score = -1
        self.stats = []

    def run(self, on_iter_callback=None):
        for i in range(1, self.iterations + 1):
            score, lines, t = self._run_one()
            entry = {'iter': i, 'score': score, 'lines': lines, 'time': round(t, 2)}
            self.stats.append(entry)
            if score > self.best_score:
                self.best_score = score
                self.best_weights = dict(self.weights)
        return {'best_score': self.best_score, 'best_weights': self.best_weights, 'stats': self.stats}

    def _run_one(self):
        return 1000, 10, 1.0 # Заглушка для краткости

class Player:
    def __init__(self, player_id, settings, board):
        self.id = player_id
        self.nickname = settings.get('nickname', f'Player {player_id}')
        self.color = pygame.Color(settings['color'])
        self.speed = settings['speed']
        self.is_bot = settings.get('is_bot', False)
        self.board = board
        self.hold_piece = None
        self.hold_used = False
        self.next_pieces = []
        self.current_piece = None
        self.alive = True
        self.fall_timer = 0
        self.level = 1
        self.game_over_time = None
        self.key_state = {action: False for action in ['left', 'right', 'soft_drop', 'hard_drop', 'rotate', 'hold']}
        self.key_timers = {action: 0 for action in ['left', 'right', 'soft_drop']}
        self.das_triggered = {action: False for action in ['left', 'right', 'soft_drop']}
        self.bot = None
        
        if self.is_bot:
            ai_type = settings.get('ai_type', 'qwen')
            ai_config = settings.get('ai_config', {})
            if ai_type == 'custom': self.bot = CustomAI(self, ai_config)
            elif ai_type == 'student': self.bot = None
            else: self.bot = QwenAI(self, ai_config)
            
        self.generate_next_pieces()
        self.spawn_piece()

    def generate_next_pieces(self, count=3):
        shapes = list(SHAPES.keys())
        for _ in range(count):
            self.next_pieces.append(Piece(random.choice(shapes), self.color))

    def spawn_piece(self):
        if not self.next_pieces: self.generate_next_pieces()
        self.current_piece = self.next_pieces.pop(0)
        self.generate_next_pieces(1)
        self.hold_used = False
        if not self.board.is_valid_position(self.current_piece):
            self.alive = False
            self.game_over_time = time.time()

    def hold_current(self):
        if self.hold_piece is None:
            self.hold_piece = Piece(self.current_piece.shape_name, self.color)
            self.spawn_piece()
        else:
            temp = self.hold_piece
            self.hold_piece = Piece(self.current_piece.shape_name, self.color)
            self.current_piece = temp
            self.current_piece.x = self.board.width // 2 - len(self.current_piece.shape[0]) // 2
            self.current_piece.y = 0
            if not self.board.is_valid_position(self.current_piece):
                self.alive = False
                self.game_over_time = time.time()
        self.hold_used = True
        return True

    def update(self, dt, current_time):
        if not self.alive: return
        if self.is_bot:
            if self.bot:
                action = self.bot.get_action()
                if action: self.handle_action(action)
            return

        for action in ['left', 'right', 'soft_drop']:
            if self.key_state[action]:
                if not self.das_triggered[action]:
                    self.handle_action(action)
                    self.das_triggered[action] = True
                    self.key_timers[action] = 0
                else:
                    self.key_timers[action] += dt
                    if self.key_timers[action] >= DAS_DELAY:
                        while self.key_timers[action] >= DAS_DELAY + DAS_REPEAT:
                            self.handle_action(action)
                            self.key_timers[action] -= DAS_REPEAT
            else:
                self.das_triggered[action] = False
                self.key_timers[action] = 0

        self.fall_timer += dt
        effective_speed = min(10.0, self.speed + (self.level - 1) * 0.15)
        fall_interval = max(10, 1000 / (effective_speed * 10))
        while self.fall_timer >= fall_interval:
            self.move_piece(0, 1)
            self.fall_timer -= fall_interval

    def handle_action(self, action):
        if not self.alive or self.current_piece is None: return
        if action == 'left': self.move_piece(-1, 0)
        elif action == 'right': self.move_piece(1, 0)
        elif action == 'soft_drop': self.move_piece(0, 1)
        elif action == 'hard_drop': self.hard_drop()
        elif action == 'rotate': self.rotate_piece()
        elif action == 'hold': self.hold_current()

    def move_piece(self, dx, dy):
        self.current_piece.move(dx, dy)
        if not self.board.is_valid_position(self.current_piece):
            self.current_piece.move(-dx, -dy)
            if dy == 1: self.lock_piece()

    def rotate_piece(self):
        self.current_piece.rotate()
        if not self.board.is_valid_position(self.current_piece):
            for dx in [-1, 1, -2, 2]:
                self.current_piece.move(dx, 0)
                if self.board.is_valid_position(self.current_piece): return
                self.current_piece.move(-dx, 0)
            for _ in range(3): self.current_piece.rotate()

    def hard_drop(self):
        while self.board.is_valid_position(self.current_piece): self.current_piece.move(0, 1)
        self.current_piece.move(0, -1)
        self.lock_piece()

    def lock_piece(self):
        lines_cleared = self.board.place_piece(self.current_piece)
        new_level = 1 + self.board.lines_cleared_total // 10
        if new_level > self.level: self.level = new_level
        self.spawn_piece()

# ================= GAME CLASS =================
class Game:
    def __init__(self, settings):
        pygame.init()
        self.settings = settings
        self.current_user = settings.get('current_user', 'Guest')
        self.game_mode = settings['game_mode']
        self.players_data = settings['players']
        self.dynamic_keymap = settings.get('dynamic_keymap', {})
        self.is_spectator = settings.get('is_spectator', False)
        self.num_players = len([p for p in self.players_data.values() if p.get('enabled', False)])
        self.players = []
        
        if self.game_mode == 'self_learning':
            self.running = False
            return

        self.running = True
        self.paused = False
        self.clock = pygame.time.Clock()
        self.start_time = time.time()
        self.font = pygame.font.Font(None, 24)
        self.small_font = pygame.font.Font(None, 18)
        self.big_font = pygame.font.Font(None, 48)

        if self.game_mode == 'coop':
            total_width = WIDTH * self.num_players
            self.shared_board = Board(total_width, HEIGHT)
            for pid, pdata in self.players_data.items():
                if pdata.get('enabled'):
                    self.players.append(Player(pid, pdata, self.shared_board))
        elif self.game_mode == '2vs2':
            t1, t2 = [], []
            for pid, pdata in self.players_data.items():
                if pdata.get('enabled'): (t1 if len(t1) <2 else t2).append((pid, pdata))
            self.team_board_w = WIDTH * 2
            self.team1_board = Board(self.team_board_w * 2, HEIGHT)
            self.team2_board = Board(self.team_board_w * 2, HEIGHT)
            self.teams = [{'board': self.team1_board, 'players': []}, {'board': self.team2_board, 'players': []}]
            for pid, pdata in t1:
                p = Player(pid, pdata, self.team1_board)
                self.players.append(p); self.teams[0]['players'].append(p)
            for pid, pdata in t2:
                p = Player(pid, pdata, self.team2_board)
                self.players.append(p); self.teams[1]['players'].append(p)
        else:
            for pid, pdata in self.players_data.items():
                if pdata.get('enabled'):
                    self.players.append(Player(pid, pdata, Board(WIDTH, HEIGHT, pdata['color'])))

        self.layout_positions = self.calculate_layout()
        self.remote_player_boards = {}
        self.network_client = None

        if self.game_mode in ['lan', 'global']:
            host = settings.get('server_host', '127.0.0.1')
            port = settings.get('server_port', 8888)
            room_name = settings.get('room_name', 'default_room')
            is_host = settings.get('is_host', True)
            player_info = {
                str(p.id): {
                    'nickname': p.nickname,
                    'color': f"#{p.color.r:02x}{p.color.g:02x}{p.color.b:02x}",
                    'is_spectator': False
                } for p in self.players
            }
            self.network_client = NetworkClient(host, port, room_name, is_host, player_info)
            self.network_client.start()
            self.remote_player_boards = {}

        self.screen = pygame.display.set_mode((self.layout_width, self.layout_height))
        pygame.display.set_caption("Tetris MP")

    def calculate_layout(self):
        board_px_w, board_px_h = WIDTH * CELL_SIZE, HEIGHT * CELL_SIZE
        spacing, info_top = 20, 30
        info_bottom = 100 if self.game_mode != 'coop' else 140
        
        if self.game_mode == 'coop':
            total_w = (WIDTH * self.num_players * CELL_SIZE) + 40
            total_h = info_top + board_px_h + info_bottom + 40
            self.layout_width, self.layout_height = total_w, total_h
            self.board_position = (20, info_top + 20)
            self.board_width_px = WIDTH * self.num_players * CELL_SIZE
            self.board_height_px = board_px_h
            return []
        elif self.game_mode == '2vs2':
            board_px_w = self.team_board_w * CELL_SIZE
            total_w = (board_px_w * 2) + (spacing * 3)
            total_h = info_top + board_px_h + info_bottom + spacing * 2
            self.layout_width, self.layout_height = total_w, total_h
            self.board_width_px, self.board_height_px = board_px_w, board_px_h
            self.team_positions = [(spacing, info_top + spacing), (spacing + board_px_w + spacing, info_top + spacing)]
            return self.team_positions
        elif self.game_mode in ['lan', 'global']:
            mini_w = 100
            info_w = 130  # Место для UI других игроков
            side_gap = 20
            spacing = 20
            total_w = (mini_w + info_w) + side_gap + board_px_w + side_gap + (mini_w + info_w) + spacing
            total_h = board_px_h + 50
            self.layout_width, self.layout_height = total_w, total_h
            self.board_width_px, self.board_height_px = board_px_w, board_px_h
            return []
        else:
            cols = min(self.num_players, 4)
            rows = (self.num_players + cols - 1) // cols
            total_w = cols * (board_px_w + spacing) + spacing
            total_h = rows * (board_px_h + info_bottom + spacing) + info_top
            self.layout_width, self.layout_height = total_w, total_h
            self.board_width_px, self.board_height_px = board_px_w, board_px_h
            positions = []
            for idx in range(self.num_players):
                col, row = idx % cols, idx // cols
                positions.append((spacing + col * (board_px_w + spacing), info_top + spacing + row * (board_px_h + info_bottom + spacing)))
            return positions

    def run(self):
        Log.info(f"🎮 Игра запущена. Режим: {self.game_mode}, Игроков: {self.num_players}")
        if self.game_mode == 'self_learning':
            pygame.quit()
            return

        while self.running:
            dt = self.clock.tick(60)
            current_time = time.time()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_p, pygame.K_SPACE, pygame.K_ESCAPE):
                        self.paused = not self.paused
                    elif not self.paused and not self.is_spectator:
                        self.handle_keydown(event.key)
                elif event.type == pygame.KEYUP:
                    if not self.paused and not self.is_spectator:
                        self.handle_keyup(event.key)
                elif event.type == pygame.MOUSEBUTTONDOWN and self.paused:
                    mouse_pos = pygame.mouse.get_pos()

                    if hasattr(self, "btn_resume") and self.btn_resume.collidepoint(mouse_pos):
                        self.paused = False

                    elif hasattr(self, "btn_quit") and self.btn_quit.collidepoint(mouse_pos):
                        self.running = False

            if not self.paused:
                for player in self.players:
                    if not getattr(player, 'is_spectator', False):
                        player.update(dt, current_time)
                self.check_game_over()

            if self.network_client:
                self.remote_player_boards = dict(self.network_client.remote_players)
                if not self.paused:
                    # Расширенный пакет данных для отрисовки UI у других игроков
                    local_state = {
                        str(p.id): {
                            'score': p.board.score,
                            'lines': p.board.lines_cleared_total,
                            'alive': p.alive,
                            'grid': [[1 if cell is not None else 0 for cell in row] for row in p.board.grid],
                            'next_shape': p.next_pieces[0].shape_name if p.next_pieces else None,
                            'next_color': f"#{p.next_pieces[0].color.r:02x}{p.next_pieces[0].color.g:02x}{p.next_pieces[0].color.b:02x}" if p.next_pieces else "#FFFFFF",
                            'hold_shape': p.hold_piece.shape_name if p.hold_piece else None,
                            'hold_color': f"#{p.hold_piece.color.r:02x}{p.hold_piece.color.g:02x}{p.hold_piece.color.b:02x}" if p.hold_piece else "#FFFFFF",
                            'level': p.level,
                            'speed': min(10.0, p.speed + (p.level - 1) * 0.15),
                            'time': int((p.game_over_time - self.start_time) if (not p.alive and p.game_over_time) else (time.time() - self.start_time))
                        } for p in self.players if not p.is_bot
                    }
                    self.network_client.send_state(local_state)

            self.draw()
            if self.paused: self.draw_pause_overlay()
            pygame.display.flip()

        pygame.quit()

    def handle_keydown(self, key):
        for player in self.players:
            if player.is_bot or not player.alive: continue
            base_keymap = KEYMAP.get(player.id, {})
            custom_keymap = self.dynamic_keymap.get(player.id, {})
            active_keymap = {**base_keymap, **custom_keymap}
            for action, k in active_keymap.items():
                if isinstance(k, int): match = (key == k)
                else:
                    event_name = pygame.key.name(key).lower().replace(' ', '')
                    map_name = str(k).lower().replace(' ', '')
                    match = (event_name == map_name)
                if match:
                    if action in ['rotate', 'hard_drop', 'hold']: player.handle_action(action)
                    else: player.key_state[action] = True
                    
                    if self.game_mode == 'teacher_student' and player.id == 1:
                        student = next((p for p in self.players if p.id == 2), None)
                        if student and student.bot and isinstance(student.bot, StudentAI):
                            student.bot.log_teacher_action(action)

    def handle_keyup(self, key):
        for player in self.players:
            if player.is_bot: continue
            base_keymap = KEYMAP.get(player.id, {})
            custom_keymap = self.dynamic_keymap.get(player.id, {})
            active_keymap = {**base_keymap, **custom_keymap}
            for action, k in active_keymap.items():
                if isinstance(k, int): match = (key == k)
                else:
                    event_name = pygame.key.name(key).lower().replace(' ', '')
                    map_name = str(k).lower().replace(' ', '')
                    match = (event_name == map_name)
                if match and action not in ['rotate', 'hard_drop', 'hold']:
                    player.key_state[action] = False

    def check_game_over(self):
        if self.game_mode == '2vs2':
            t1_alive = any(p.alive for p in self.teams[0]['players'])
            t2_alive = any(p.alive for p in self.teams[1]['players'])
            if not t1_alive or not t2_alive: self.running = False
        else:
            if not any(p.alive for p in self.players): self.running = False

    def draw(self):
        self.screen.fill((30,30,30))
        if self.game_mode in ['lan', 'global']: self.draw_network()
        elif self.game_mode == 'coop': self.draw_coop()
        elif self.game_mode == '2vs2': self.draw_2vs2()
        else: self.draw_vs()

        # Оверлей ошибки подключения к серверу
        if self.network_client and self.network_client.connection_status == 'failed':
            overlay = pygame.Surface((self.layout_width, self.layout_height), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 160))
            self.screen.blit(overlay, (0, 0))
            
            err_text = self.big_font.render("ОШИБКА ПОДКЛЮЧЕНИЯ", True, (255, 50, 50))
            sub_text = self.font.render("Сервер недоступен. Игра в локальном режиме.", True, (255, 255, 255))
            
            self.screen.blit(err_text, err_text.get_rect(center=(self.layout_width//2, self.layout_height//2 - 20)))
            self.screen.blit(sub_text, sub_text.get_rect(center=(self.layout_width//2, self.layout_height//2 + 20)))

    def _parse_hex_color(self, color_str):
        if isinstance(color_str, str) and color_str.startswith('#') and len(color_str) == 7:
            try:
                return (int(color_str[1:3], 16), int(color_str[3:5], 16), int(color_str[5:7], 16))
            except ValueError:
                return (255, 255, 255)
        return (255, 255, 255)

    def _draw_remote_piece_preview(self, shape_name, color, x, y):
        if not shape_name or shape_name not in SHAPES: return
        shape = SHAPES[shape_name]
        cell_size = 6
        for r, row in enumerate(shape):
            for c, val in enumerate(row):
                if val:
                    pygame.draw.rect(self.screen, color, (x + c*cell_size, y + r*cell_size, cell_size, cell_size))

    def draw_minimized_board(self, board, x, y, player_color, nickname="", mini_h=150):
        mini_cell_x = MINI_BOARD_WIDTH / board.width
        mini_cell_y = mini_h / board.height
        w = int(mini_cell_x * board.width)
        h = int(mini_cell_y * board.height)
        pygame.draw.rect(self.screen, (80, 80, 80), (x - 1, y - 1, w + 2, h + 2), 1)
        for row in range(board.height):
            for col in range(board.width):
                color = board.grid[row][col]
                if color:
                    rect = pygame.Rect(x + int(col * mini_cell_x), y + int(row * mini_cell_y), max(1, int(mini_cell_x)), max(1, int(mini_cell_y)))
                    pygame.draw.rect(self.screen, color, rect)
        if nickname:
            nick_surf = self.small_font.render(nickname[:10], True, player_color)
            self.screen.blit(nick_surf, (x, y - 12))

    def draw_network(self):
        if not self.players: return
        local_player = next((p for p in self.players if not p.is_bot), self.players[0])
        
        main_x = (self.layout_width - self.board_width_px) // 2
        main_y = (self.layout_height - self.board_height_px) // 2
        
        self.draw_board(local_player.board, main_x, main_y, local_player)
        
        # Отрисовка UI для локального игрока в онлайн-режиме
        info_x = main_x
        info_y = main_y + self.board_height_px + 10
        self.draw_player_info(local_player, info_x, info_y)

        local_ids = {str(p.id) for p in self.players}
        others = []
        for p in self.players:
            if p != local_player: others.append(('local', p, None))
        for pid, pdata in self.remote_player_boards.items():
            if pid not in local_ids: others.append(('remote', pid, pdata))

        mini_w = 100
        info_w = 130
        side_gap = 20
        
        left_x = main_x - side_gap - mini_w - info_w
        right_x = main_x + self.board_width_px + side_gap

        count_left = min(8, len(others))
        count_right = min(8, max(0, len(others) - 8))
        max_per_side = max(count_left, count_right, 1)
        mini_h = min(150, self.board_height_px // max_per_side)

        for i, item in enumerate(others):
            if i < 8:
                y = main_y + i * mini_h
                self._draw_other_player(item, left_x, y, mini_h)
            elif i < 16:
                y = main_y + (i - 8) * mini_h
                self._draw_other_player(item, right_x, y, mini_h)

    def _draw_other_player(self, item, x, y, mini_h):
        kind, payload, pdata = item
        if kind == 'local':
            p = payload
            self.draw_minimized_board(p.board, x, y, p.color, p.nickname, mini_h)
        else:
            self._draw_remote_minimized_board(pdata, x, y, mini_h)

    def _draw_remote_minimized_board(self, pdata, x, y, mini_h=150):
        grid = pdata.get('grid') or []
        color_str = pdata.get('color', '#FFFFFF')
        nickname = pdata.get('nickname', 'Remote')
        color = self._parse_hex_color(color_str)

        mini_cell_x = MINI_BOARD_WIDTH / WIDTH
        mini_cell_y = mini_h / HEIGHT
        w = int(mini_cell_x * WIDTH)
        h = int(mini_cell_y * HEIGHT)
        
        pygame.draw.rect(self.screen, (80, 80, 80), (x - 1, y - 1, w + 2, h + 2), 1)
        for row in range(min(len(grid), HEIGHT)):
            row_data = grid[row]
            for col in range(min(len(row_data), WIDTH)):
                if row_data[col]:
                    rect = pygame.Rect(x + int(col * mini_cell_x), y + int(row * mini_cell_y), max(1, int(mini_cell_x)), max(1, int(mini_cell_y)))
                    pygame.draw.rect(self.screen, color, rect)

        # Отрисовка UI для удаленного игрока
        text_x = x + w + 10
        text_y = y
        
        self.screen.blit(self.small_font.render(str(nickname)[:12], True, color), (text_x, text_y))
        self.screen.blit(self.small_font.render(f"Score: {pdata.get('score', 0)}", True, (255,255,255)), (text_x, text_y + 20))
        self.screen.blit(self.small_font.render(f"Lines: {pdata.get('lines', 0)}", True, (200,200,200)), (text_x, text_y + 38))
        
        time_val = pdata.get('time', 0)
        self.screen.blit(self.small_font.render(f"Time: {time_val}s", True, (255,255,255)), (text_x, text_y + 56))
        
        self.screen.blit(self.small_font.render("Next:", True, (255,255,255)), (text_x, text_y + 80))
        if pdata.get('next_shape'):
            next_color = self._parse_hex_color(pdata.get('next_color', '#FFFFFF'))
            self._draw_remote_piece_preview(pdata['next_shape'], next_color, text_x + 45, text_y + 82)
            
        self.screen.blit(self.small_font.render("Hold:", True, (255,255,255)), (text_x, text_y + 105))
        if pdata.get('hold_shape'):
            hold_color = self._parse_hex_color(pdata.get('hold_color', '#FFFFFF'))
            self._draw_remote_piece_preview(pdata['hold_shape'], hold_color, text_x + 45, text_y + 107)

    def draw_vs(self):
        for idx, player in enumerate(self.players):
            x, y = self.layout_positions[idx]
            nick_surf = self.font.render(player.nickname, True, player.color)
            self.screen.blit(nick_surf, (x + self.board_width_px//2 - nick_surf.get_width()//2, y - 30))
            self.draw_board(player.board, x, y, player)
            self.draw_player_info(player, x, y + self.board_height_px + 5)

    def draw_coop(self):
        x, y = self.board_position
        spacing = 150
        start_x = (self.layout_width - spacing * self.num_players) // 2 + 20
        for i, player in enumerate(self.players):
            px = start_x + i * spacing
            nick_surf = self.font.render(player.nickname, True, player.color)
            self.screen.blit(nick_surf, (px, 10))
        self.draw_board(self.shared_board, x, y, self.players[0] if self.players else None)
        info_y = y + self.board_height_px + 10
        for i, player in enumerate(self.players):
            px = start_x + i * spacing
            self.draw_player_info(player, px, info_y)

    def draw_2vs2(self):
        # Убраны мини-поля, теперь рисуются полноценные поля команд
        for i in range(2):
            x, y = self.team_positions[i]
            team_label = self.font.render(f"TEAM {i+1}", True, (200, 200, 200))
            self.screen.blit(team_label, (x + self.board_width_px//2 - team_label.get_width()//2, y - 25))
            
            board = self.teams[i]['board']
            active = next((p for p in self.teams[i]['players'] if p.alive and p.current_piece), None)
            self.draw_board(board, x, y, active)
            
            info_y = y + self.board_height_px + 5
            for j, p in enumerate(self.teams[i]['players']):
                self.draw_player_info(p, x + j * 150, info_y)

    def draw_board(self, board, x, y, active_player=None):
        pygame.draw.rect(self.screen, (100,100,100), (x-2, y-2, board.width*CELL_SIZE+4, board.height*CELL_SIZE+4), 2)
        for row in range(board.height):
            for col in range(board.width):
                color = board.grid[row][col]
                rect = pygame.Rect(x + col*CELL_SIZE, y + row*CELL_SIZE, CELL_SIZE, CELL_SIZE)
                if color: pygame.draw.rect(self.screen, color, rect)
                pygame.draw.rect(self.screen, (60,60,60), rect, 1)
                
        if active_player and active_player.current_piece and active_player.alive:
            piece = active_player.current_piece
            ghost = copy.deepcopy(piece)
            board.drop_height(ghost)
            for cx, cy in ghost.get_cells():
                if 0 <= cy < board.height:
                    s = pygame.Surface((CELL_SIZE, CELL_SIZE), pygame.SRCALPHA)
                    s.fill((*piece.color[:3], 100))
                    self.screen.blit(s, (x + cx*CELL_SIZE, y + cy*CELL_SIZE))
            for cx, cy in piece.get_cells():
                if 0 <= cy < board.height:
                    pygame.draw.rect(self.screen, piece.color, (x + cx*CELL_SIZE, y + cy*CELL_SIZE, CELL_SIZE, CELL_SIZE))
                    pygame.draw.rect(self.screen, (255,255,255), (x + cx*CELL_SIZE, y + cy*CELL_SIZE, CELL_SIZE, CELL_SIZE), 1)

    def draw_player_info(self, player, x, y):
        self.screen.blit(self.small_font.render("Next:", True, (255,255,255)), (x, y))
        if player.next_pieces: self.draw_piece_preview(player.next_pieces[0], x, y+15)
        self.screen.blit(self.small_font.render("Hold:", True, (255,255,255)), (x+60, y))
        if player.hold_piece: self.draw_piece_preview(player.hold_piece, x+60, y+15)
        self.screen.blit(self.small_font.render(f"Score: {player.board.score}", True, (255,255,255)), (x, y+50))
        self.screen.blit(self.small_font.render(f"Lines: {player.board.lines_cleared_total}", True, (200,200,200)), (x, y+68))
        self.screen.blit(self.small_font.render(f"Lvl: {player.level} | Spd: {min(10.0, player.speed + (player.level - 1) * 0.15):.1f}", True, (200,200,200)), (x, y+86))
        time_val = (player.game_over_time - self.start_time) if (not player.alive and player.game_over_time) else (time.time() - self.start_time)
        self.screen.blit(self.small_font.render(f"Time: {int(time_val)}s", True, (255,255,255)), (x, y+104))

    def draw_piece_preview(self, piece, x, y):
        for r, row in enumerate(piece.shape):
            for c, val in enumerate(row):
                if val:
                    pygame.draw.rect(self.screen, piece.color, (x + c*CELL_SIZE, y + r*CELL_SIZE, CELL_SIZE, CELL_SIZE))
                    pygame.draw.rect(self.screen, (255,255,255), (x + c*CELL_SIZE, y + r*CELL_SIZE, CELL_SIZE, CELL_SIZE), 1)

    def draw_pause_overlay(self):
        overlay = pygame.Surface((self.layout_width, self.layout_height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 180))
        self.screen.blit(overlay, (0, 0))
        pause_text = self.big_font.render("PAUSE", True, (255, 255, 255))
        text_rect = pause_text.get_rect(center=(self.layout_width//2, self.layout_height//2 - 60))
        self.screen.blit(pause_text, text_rect)
        btn_w, btn_h = 220, 50
        self.btn_resume = pygame.Rect(self.layout_width//2 - btn_w//2, self.layout_height//2, btn_w, btn_h)
        self.btn_quit = pygame.Rect(self.layout_width//2 - btn_w//2, self.layout_height//2 + 70, btn_w, btn_h)
        mouse_pos = pygame.mouse.get_pos()
        color_resume = (0, 220, 0) if self.btn_resume.collidepoint(mouse_pos) else (0, 180, 0)
        pygame.draw.rect(self.screen, color_resume, self.btn_resume, border_radius=8)
        text_resume = self.font.render("Продолжить", True, (255, 255, 255))
        self.screen.blit(text_resume, text_resume.get_rect(center=self.btn_resume.center))
        color_quit = (220, 0, 0) if self.btn_quit.collidepoint(mouse_pos) else (180, 0, 0)
        pygame.draw.rect(self.screen, color_quit, self.btn_quit, border_radius=8)
        text_quit = self.font.render("Покинуть игру", True, (255, 255, 255))
        self.screen.blit(text_quit, text_quit.get_rect(center=self.btn_quit.center))

class NetworkClient:
    def __init__(self, host: str, port: int, room_name: str, is_host: bool, player_info: Dict[str, Any]):
        self.host = host
        self.port = port
        self.room_name = room_name
        self.is_host = is_host
        self.player_info = player_info
        self.room_id: Optional[str] = None
        self.is_spectator: bool = False
        self.remote_players: Dict[str, Any] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._writer: Optional[asyncio.StreamWriter] = None
        
        # Статусы: 'connecting', 'connected', 'failed'
        self.connection_status = 'connecting'
        self.connection_error = ""

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run_asyncio, daemon=True)
        self._thread.start()

    def _run_asyncio(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._connect_and_listen())

    async def _connect_and_listen(self) -> None:
        try:
            self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
            self.connection_status = 'connected'
            
            if self.is_host:
                msg = {"action": "create_room", "room_id": self.room_name, "game_mode": "coop", "player_info": self.player_info}
            else:
                msg = {"action": "join_room", "room_id": self.room_name, "player_info": self.player_info}
                
            self._writer.write((json.dumps(msg) + '\n').encode('utf-8'))
            await self._writer.drain()
            
            while self._running and self._reader and not self._reader.at_eof():
                line = await self._reader.readline()
                if not line: break
                msg = json.loads(line.decode('utf-8').strip())
                self._process_message(msg)
        except Exception as e:
            self.connection_status = 'failed'
            self.connection_error = str(e)
            Log.error(f"🌐 Ошибка сети: {e}")
        finally:
            self._running = False
            if self._writer:
                self._writer.close()

    def _process_message(self, msg: Dict[str, Any]) -> None:
        action = msg.get('action')
        if action in ('room_created', 'room_joined'):
            self.room_id = msg.get('room_id')
            self.is_spectator = msg.get('is_spectator', False)
            self.remote_players = msg.get('players', {})
        elif action == 'state_update':
            # Сервер теперь присылает уже плоский словарь {player_id: data}
            self.remote_players = msg.get('state', {})
        elif action in ('player_joined', 'player_left'):
            self.remote_players = msg.get('players', {})

    def send_state(self, state: Dict[str, Any]) -> None:
        if self._loop and self._running and self.room_id and not self.is_spectator:
            msg = {"action": "update_state", "room_id": self.room_id, "state": state}
            asyncio.run_coroutine_threadsafe(self._send_msg(msg), self._loop)

    async def _send_msg(self, msg: Dict[str, Any]) -> None:
        if self._writer:
            try:
                self._writer.write((json.dumps(msg) + '\n').encode('utf-8'))
                await self._writer.drain()
            except Exception:
                pass

    def stop(self) -> None:
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=1.0)
