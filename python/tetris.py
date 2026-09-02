import pygame, random, copy, time, threading
from queue import Queue, Empty
from config import *
from cuslib import Log

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
class BaseAI:
    def __init__(self, player, config=None):
        self.player = player
        self.config = config or {}
        self.action_queue = []
        self._result_queue = Queue()
        self._compute_thread = None
        self._is_computing = False
        self._precompute_rotations()
        # Базовые веса (можно переопределить)
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
        """Базовая оценка позиции. Переопределяется в QwenAI."""
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
        t0 = time.time()  # 🔑 ОБЯЗАТЕЛЬНО определяем ДО try
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

class DeepSeekAI_v1(BaseAI): ## Version 1
    """
    Улучшенный AI с акцентом на выживание и избегание дыр.
    Веса подобраны так, чтобы не строить столбы.
    """
    def __init__(self, player, config=None):
        super().__init__(player, config)
        # Критически важные веса: избегаем высоты и дыр любой ценой
        self.weights = {
            'aggregate_height': -0.85,   # очень большой штраф за общую высоту
            'lines': 0.55,               # умеренный бонус за линии (не в ущерб выживанию)
            'holes': -1.20,              # огромный штраф за дыры
            'bumpiness': -0.30,          # штраф за неровность
            'well_depth': -0.40,         # штраф за глубокие ямы
            'max_height': -0.70,         # штраф за самый высокий столбец (предотвращает "башню")
            'hole_penalty_factor': 2.0   # дополнительный множитель для дыр
        }

    def evaluate(self, shape, x, drop_y, grid, width, height):
        """
        Оценка позиции после размещения фигуры.
        Возвращает число: чем выше, тем лучше.
        """
        # Создаём копию поля и размещаем фигуру
        sim = [row[:] for row in grid]
        for r, row in enumerate(shape):
            for c, val in enumerate(row):
                if val and (drop_y + r) < height:
                    sim[drop_y + r][x + c] = 1  # цвет не важен, только факт заполнения

        # Очищаем заполненные линии (это влияет на последующую оценку)
        lines_cleared = 0
        row = height - 1
        while row >= 0:
            if all(sim[row][col] is not None for col in range(width)):
                # удаляем линию
                for rr in range(row, 0, -1):
                    sim[rr] = sim[rr-1][:]
                sim[0] = [None] * width
                lines_cleared += 1
                # не увеличиваем row, т.к. следующая строка сместилась вниз
            else:
                row -= 1

        # Вычисляем метрики
        heights = [0] * width
        holes = 0
        max_h = 0
        bumpiness = 0
        well_depth = 0

        for col in range(width):
            # высота столбца (расстояние от верха до первого блока)
            h = 0
            for r in range(height):
                if sim[r][col] is not None:
                    h = height - r
                    break
            heights[col] = h
            max_h = max(max_h, h)

            # дыры (пустые клетки под блоком)
            found_block = False
            for r in range(height):
                if sim[r][col] is not None:
                    found_block = True
                elif found_block:
                    holes += 1

        # перепады высот между соседними столбцами
        for col in range(width - 1):
            bumpiness += abs(heights[col] - heights[col+1])

        # глубина колодцев (впадина между двумя высокими столбцами)
        for col in range(1, width - 1):
            if heights[col] < heights[col-1] and heights[col] < heights[col+1]:
                well_depth += min(heights[col-1], heights[col+1]) - heights[col]

        aggregate_height = sum(heights)

        # Итоговая оценка: все штрафы отрицательные, бонус положительный
        score = (self.weights['aggregate_height'] * aggregate_height +
                 self.weights['lines'] * lines_cleared +
                 self.weights['holes'] * holes * self.weights['hole_penalty_factor'] +
                 self.weights['bumpiness'] * bumpiness +
                 self.weights['well_depth'] * well_depth +
                 self.weights['max_height'] * max_h)

        # Дополнительный штраф, если максимальная высота превышает половину поля — паника
        if max_h > height // 2:
            score -= 50 * (max_h - height // 2)

        return score

    def _get_best_placement(self, shape_name, grid, width, height, hold_used, hold_shape, next_shape):
        """
        Возвращает лучший (use_hold, x, rot) для данной фигуры.
        Если использование hold даёт лучший результат, возвращает (True, ...).
        """
        best_score = -float('inf')
        best_plan = (False, 0, 0)  # по умолчанию не использовать hold

        # Вариант 1: не использовать hold, играем текущей фигурой
        rotations = self.rotations[shape_name]
        for rot_idx, shape in enumerate(rotations):
            sw = len(shape[0])
            if sw > width:
                continue
            min_x = max(0, -(sw - 1))
            max_x = min(width - 1, width - sw)
            for x in range(min_x, max_x + 1):
                y = self._fast_drop(shape, grid, width, height, x)
                if y < 0:
                    continue
                score = self.evaluate(shape, x, y, grid, width, height)

                # Учитываем следующую фигуру: если следующая фигура хорошо ляжет на то же место,
                # даём небольшой бонус (предвидение)
                if next_shape:
                    next_rotations = self.rotations[next_shape]
                    best_next = -float('inf')
                    for nrot, nshape in enumerate(next_rotations):
                        nsw = len(nshape[0])
                        if nsw > width: continue
                        min_nx = max(0, -(nsw - 1))
                        max_nx = min(width - 1, width - nsw)
                        for nx in range(min_nx, max_nx + 1):
                            ny = self._fast_drop(nshape, grid, width, height, nx)
                            if ny < 0: continue
                            # Оцениваем позицию после текущего хода, но на том же поле (без очистки линий – грубо)
                            # Для скорости используем текущую сетку grid (можно и улучшить, но достаточно)
                            s = self.evaluate(nshape, nx, ny, grid, width, height)
                            if s > best_next:
                                best_next = s
                    if best_next > -float('inf'):
                        score += 0.3 * best_next  # небольшая добавка за хорошую совместимость

                if score > best_score:
                    best_score = score
                    best_plan = (False, x, rot_idx)

        # Вариант 2: используем hold (если доступно)
        if not hold_used and hold_shape:
            hold_rotations = self.rotations[hold_shape]
            for rot_idx, shape in enumerate(hold_rotations):
                sw = len(shape[0])
                if sw > width: continue
                min_x = max(0, -(sw - 1))
                max_x = min(width - 1, width - sw)
                for x in range(min_x, max_x + 1):
                    y = self._fast_drop(shape, grid, width, height, x)
                    if y < 0: continue
                    # Оцениваем позицию от фигуры из hold
                    score = self.evaluate(shape, x, y, grid, width, height)
                    if next_shape:
                        # Аналогичный бонус за следующую фигуру
                        next_rotations = self.rotations[next_shape]
                        best_next = -float('inf')
                        for nrot, nshape in enumerate(next_rotations):
                            nsw = len(nshape[0])
                            if nsw > width: continue
                            min_nx = max(0, -(nsw - 1))
                            max_nx = min(width - 1, width - nsw)
                            for nx in range(min_nx, max_nx + 1):
                                ny = self._fast_drop(nshape, grid, width, height, nx)
                                if ny < 0: continue
                                s = self.evaluate(nshape, nx, ny, grid, width, height)
                                if s > best_next:
                                    best_next = s
                        if best_next > -float('inf'):
                            score += 0.3 * best_next
                    if score > best_score:
                        best_score = score
                        best_plan = (True, x, rot_idx)

        return best_plan

    def _compute_in_background(self, state):
        """
        Асинхронный поиск лучшего действия.
        """
        try:
            grid = state['grid']
            width = state['width']
            height = state['height']
            curr_shape = state['curr_shape']
            hold_shape = state['hold_shape']
            hold_used = state['hold_used']
            next_shape = state['next_shapes'][0] if state['next_shapes'] else None

            best_plan = self._get_best_placement(curr_shape, grid, width, height,
                                                 hold_used, hold_shape, next_shape)
            self._result_queue.put(best_plan)
        except Exception:
            self._result_queue.put(None)

class DeepSeekAIv2(BaseAI): ## Version 2
    """Продвинутый AI с глубокой оценкой и предсказанием на 1 ход вперёд."""
    def __init__(self, player, config=None):
        super().__init__(player, config)
        self.weights = {
            'aggregate_height': -0.78,
            'lines': 0.65,
            'holes': -1.35,
            'bumpiness': -0.42,
            'well_depth': -0.60,
            'max_height': -0.85,
            'erosion': 0.50,
        }

    def _evaluate_position(self, shape, x, y, grid, width, height):
        """Улучшенная оценка позиции после размещения фигуры."""
        sim = [row[:] for row in grid]
        for r, row in enumerate(shape):
            for c, val in enumerate(row):
                if val and y + r < height:
                    sim[y + r][x + c] = 1

        # Очистка линий (эрозия)
        lines_cleared = 0
        row = height - 1
        while row >= 0:
            if all(sim[row][col] is not None for col in range(width)):
                for rr in range(row, 0, -1):
                    sim[rr] = sim[rr-1][:]
                sim[0] = [None] * width
                lines_cleared += 1
            else:
                row -= 1

        heights = [0] * width
        holes = 0
        max_h = 0
        bumpiness = 0
        well_depth = 0

        for col in range(width):
            h = 0
            for r in range(height):
                if sim[r][col] is not None:
                    h = height - r
                    break
            heights[col] = h
            max_h = max(max_h, h)

            found = False
            for r in range(height):
                if sim[r][col] is not None:
                    found = True
                elif found:
                    holes += 1

        for col in range(width - 1):
            bumpiness += abs(heights[col] - heights[col+1])

        for col in range(1, width - 1):
            if heights[col] < heights[col-1] and heights[col] < heights[col+1]:
                well_depth += min(heights[col-1], heights[col+1]) - heights[col]

        agg_height = sum(heights)

        # Паника при высоком столбце
        panic_penalty = 0
        if max_h > height * 0.6:
            panic_penalty = -50 * (max_h - height * 0.6)

        score = (self.weights['aggregate_height'] * agg_height +
                 self.weights['lines'] * lines_cleared +
                 self.weights['holes'] * holes +
                 self.weights['bumpiness'] * bumpiness +
                 self.weights['well_depth'] * well_depth +
                 self.weights['max_height'] * max_h +
                 self.weights['erosion'] * lines_cleared +
                 panic_penalty)
        return score

    def _compute_in_background(self, state):
        try:
            grid = state['grid']
            w, h = state['width'], state['height']
            curr_shape = state['curr_shape']
            hold_shape = state['hold_shape']
            hold_used = state['hold_used']
            next_shape = state['next_shapes'][0] if state['next_shapes'] else None

            best_score = -float('inf')
            best_plan = (False, 0, 0)

            # Проверяем текущую фигуру
            for rot_idx, shape in enumerate(self.rotations[curr_shape]):
                sw = len(shape[0])
                if sw > w: continue
                min_x = max(0, -(sw - 1))
                max_x = min(w - 1, w - sw)
                for x in range(min_x, max_x + 1):
                    y = self._fast_drop(shape, grid, w, h, x)
                    if y < 0: continue
                    score = self._evaluate_position(shape, x, y, grid, w, h)
                    
                    # Бонус за совместимость со следующей фигурой
                    if next_shape:
                        next_bonus = self._estimate_next_fit(shape, x, y, next_shape, grid, w, h)
                        score += 0.25 * next_bonus
                    
                    if score > best_score:
                        best_score = score
                        best_plan = (False, x, rot_idx)

            # Hold используем только если это даёт значительное улучшение (>15%)
            if not hold_used and hold_shape:
                for rot_idx, shape in enumerate(self.rotations[hold_shape]):
                    sw = len(shape[0])
                    if sw > w: continue
                    min_x = max(0, -(sw - 1))
                    max_x = min(w - 1, w - sw)
                    for x in range(min_x, max_x + 1):
                        y = self._fast_drop(shape, grid, w, h, x)
                        if y < 0: continue
                        score = self._evaluate_position(shape, x, y, grid, w, h)
                        if next_shape:
                            next_bonus = self._estimate_next_fit(shape, x, y, next_shape, grid, w, h)
                            score += 0.25 * next_bonus
                        if score > best_score * 1.15:  # Только если значительно лучше
                            best_score = score
                            best_plan = (True, x, rot_idx)

            self._result_queue.put(best_plan)
        except Exception:
            self._result_queue.put(None)

    def _estimate_next_fit(self, shape, x, y, next_shape, grid, w, h):
        """Оценивает, насколько хорошо следующая фигура ляжет на это же поле."""
        sim = [row[:] for row in grid]
        for r, row in enumerate(shape):
            for c, val in enumerate(row):
                if val and y + r < h:
                    sim[y + r][x + c] = 1
        # Очищаем линии
        row = h - 1
        while row >= 0:
            if all(sim[row][col] is not None for col in range(w)):
                for rr in range(row, 0, -1):
                    sim[rr] = sim[rr-1][:]
                sim[0] = [None] * w
            else:
                row -= 1
        # Оцениваем лучшую позицию для следующей фигуры
        best = -float('inf')
        for rot_idx, nshape in enumerate(self.rotations[next_shape]):
            sw = len(nshape[0])
            if sw > w: continue
            min_x = max(0, -(sw - 1))
            max_x = min(w - 1, w - sw)
            for nx in range(min_x, max_x + 1):
                ny = self._fast_drop(nshape, sim, w, h, nx)
                if ny < 0: continue
                s = self._evaluate_position(nshape, nx, ny, sim, w, h)
                if s > best:
                    best = s
        return best if best != -float('inf') else 0

class DeepSeekAI(BaseAI):  # Version 2 – с даунстакингом
    """Продвинутый AI с глубокой оценкой и приоритетом даунстакинга."""
    def __init__(self, player, config=None):
        super().__init__(player, config)
        self.weights = {
            'aggregate_height': -0.78,
            'lines': 0.65,
            'holes': -1.35,
            'bumpiness': -0.42,
            'well_depth': -0.60,
            'max_height': -0.85,
            'erosion': 0.50,
            'downstack_bonus': 2.0,      # бонус за закрытие дыр
            'smoothness_bonus': 0.8,     # бонус за сглаживание перепадов
            'well_fill_bonus': 1.2,      # бонус за заполнение колодца
        }
        self._panic_mode = False

    def _compute_metrics(self, grid, width, height):
        """Вычисляет основные метрики поля: высоты, дыры, bumpiness, well_depth."""
        heights = [0] * width
        holes = 0
        max_h = 0
        bumpiness = 0
        well_depth = 0

        for col in range(width):
            h = 0
            for r in range(height):
                if grid[r][col] is not None:
                    h = height - r
                    break
            heights[col] = h
            max_h = max(max_h, h)

            found = False
            for r in range(height):
                if grid[r][col] is not None:
                    found = True
                elif found:
                    holes += 1

        for col in range(width - 1):
            bumpiness += abs(heights[col] - heights[col+1])

        for col in range(1, width - 1):
            if heights[col] < heights[col-1] and heights[col] < heights[col+1]:
                well_depth += min(heights[col-1], heights[col+1]) - heights[col]

        return heights, holes, max_h, bumpiness, well_depth

    def _evaluate_position(self, shape, x, y, grid, width, height):
        """
        Улучшенная оценка позиции с учётом даунстакинга.
        Возвращает число (чем больше, тем лучше).
        """
        # 1. Сохраняем исходное состояние для сравнения
        orig_grid = [row[:] for row in grid]

        # 2. Размещаем фигуру
        sim = [row[:] for row in grid]
        for r, row in enumerate(shape):
            for c, val in enumerate(row):
                if val and y + r < height:
                    sim[y + r][x + c] = 1

        # 3. Очищаем полные линии (эрозия)
        lines_cleared = 0
        row = height - 1
        while row >= 0:
            if all(sim[row][col] is not None for col in range(width)):
                for rr in range(row, 0, -1):
                    sim[rr] = sim[rr-1][:]
                sim[0] = [None] * width
                lines_cleared += 1
            else:
                row -= 1

        # 4. Считаем метрики ДО и ПОСЛЕ
        _, holes_before, max_h_before, bump_before, well_before = self._compute_metrics(orig_grid, width, height)
        heights_after, holes_after, max_h_after, bump_after, well_after = self._compute_metrics(sim, width, height)

        # 5. Базовые штрафы/бонусы (как в оригинале)
        agg_height = sum(heights_after)

        # 6. Бонусы за даунстакинг
        holes_reduced = max(0, holes_before - holes_after)          # сколько дыр закрыто
        bump_reduced = max(0, bump_before - bump_after)            # насколько сгладился рельеф
        well_filled = max(0, well_before - well_after)             # насколько уменьшилась глубина колодца

        downstack_score = (
            self.weights['downstack_bonus'] * holes_reduced +
            self.weights['smoothness_bonus'] * bump_reduced +
            self.weights['well_fill_bonus'] * well_filled
        )

        # 7. Паника при высокой заполненности
        panic_penalty = 0
        if max_h_after > height * 0.6:
            panic_penalty = -50 * (max_h_after - height * 0.6)
            self._panic_mode = True
        else:
            self._panic_mode = False

        # 8. Итоговая оценка
        score = (
            self.weights['aggregate_height'] * agg_height +
            self.weights['lines'] * lines_cleared +
            self.weights['holes'] * holes_after +
            self.weights['bumpiness'] * bump_after +
            self.weights['well_depth'] * well_after +
            self.weights['max_height'] * max_h_after +
            self.weights['erosion'] * lines_cleared +
            downstack_score +
            panic_penalty
        )

        # Если в панике – дополнительно поощряем закрытие дыр
        if self._panic_mode:
            score += 2.0 * holes_reduced

        return score

    def _estimate_next_fit(self, shape, x, y, next_shape, grid, w, h):
        """Оценивает, насколько хорошо следующая фигура ляжет на поле после текущего хода."""
        # Строим поле после текущей фигуры (без очистки линий, чтобы сохранить эффект)
        sim = [row[:] for row in grid]
        for r, row in enumerate(shape):
            for c, val in enumerate(row):
                if val and y + r < h:
                    sim[y + r][x + c] = 1

        # Очищаем линии (как в реальной игре)
        row = h - 1
        while row >= 0:
            if all(sim[row][col] is not None for col in range(w)):
                for rr in range(row, 0, -1):
                    sim[rr] = sim[rr-1][:]
                sim[0] = [None] * w
            else:
                row -= 1

        best = -float('inf')
        for rot_idx, nshape in enumerate(self.rotations[next_shape]):
            sw = len(nshape[0])
            if sw > w:
                continue
            min_x = max(0, -(sw - 1))
            max_x = min(w - 1, w - sw)
            for nx in range(min_x, max_x + 1):
                ny = self._fast_drop(nshape, sim, w, h, nx)
                if ny < 0:
                    continue
                s = self._evaluate_position(nshape, nx, ny, sim, w, h)
                if s > best:
                    best = s
        return best if best != -float('inf') else 0

    def _compute_in_background(self, state):
        """Асинхронный поиск лучшего хода с учётом даунстакинга."""
        try:
            grid = state['grid']
            w, h = state['width'], state['height']
            curr_shape = state['curr_shape']
            hold_shape = state['hold_shape']
            hold_used = state['hold_used']
            next_shape = state['next_shapes'][0] if state['next_shapes'] else None

            best_score = -float('inf')
            best_plan = (False, 0, 0)

            # 1. Оцениваем все варианты с текущей фигурой
            for rot_idx, shape in enumerate(self.rotations[curr_shape]):
                sw = len(shape[0])
                if sw > w:
                    continue
                min_x = max(0, -(sw - 1))
                max_x = min(w - 1, w - sw)
                for x in range(min_x, max_x + 1):
                    y = self._fast_drop(shape, grid, w, h, x)
                    if y < 0:
                        continue
                    score = self._evaluate_position(shape, x, y, grid, w, h)

                    # Бонус за совместимость со следующей фигурой (lookahead)
                    if next_shape:
                        next_bonus = self._estimate_next_fit(shape, x, y, next_shape, grid, w, h)
                        score += 0.25 * next_bonus

                    if score > best_score:
                        best_score = score
                        best_plan = (False, x, rot_idx)

            # 2. Пробуем Hold (только если это даёт заметный выигрыш в даунстакинге)
            if not hold_used and hold_shape:
                for rot_idx, shape in enumerate(self.rotations[hold_shape]):
                    sw = len(shape[0])
                    if sw > w:
                        continue
                    min_x = max(0, -(sw - 1))
                    max_x = min(w - 1, w - sw)
                    for x in range(min_x, max_x + 1):
                        y = self._fast_drop(shape, grid, w, h, x)
                        if y < 0:
                            continue
                        score = self._evaluate_position(shape, x, y, grid, w, h)
                        if next_shape:
                            next_bonus = self._estimate_next_fit(shape, x, y, next_shape, grid, w, h)
                            score += 0.25 * next_bonus

                        # Используем hold только если он даёт улучшение >20%
                        if score > best_score * 1.20:
                            best_score = score
                            best_plan = (True, x, rot_idx)

            self._result_queue.put(best_plan)
        except Exception as e:
            Log.error(f"🧠 DeepSeekAI краш потока: {e}")
            self._result_queue.put(None)

class QwenAI(BaseAI):
    """
    Улучшенный QwenAI:
    - даунстэкинг;
    - 1-piece lookahead;
    - panic mode;
    - безопасное выполнение плана;
    - защита от устаревших потоков.
    """

    def __init__(self, player, config=None):
        super().__init__(player, config)

        self.weights = {
            # Базовая оценка поля
            'height': -0.62,
            'lines': 0.95,
            'holes': -0.85,
            'bumpiness': -0.24,
            'well_depth': -0.18,
            'max_height': -0.55,
            'transitions': -0.006,

            # Даунстэкинг: награда за улучшение поля
            'downstack_holes': 2.4,
            'downstack_bump': 0.35,
            'downstack_well': 0.75,
            'downstack_height': 0.07,
            'downstack_max': 0.9,

            # Штрафы за ухудшение
            'create_hole_penalty': -0.75,
            'height_added_penalty': -0.015,
            'panic_height_penalty': -1.25,

            # Режим паники
            'panic_threshold': 0.62,

            # Lookahead и служебные веса
            'lookahead': 0.22,
            'hold_cost': 0.08,
            'top_n': 8
        }

        if isinstance(config, dict):
            cfg = config.get('weights', config)
            if isinstance(cfg, dict):
                for k, v in cfg.items():
                    if k in self.weights:
                        self.weights[k] = v

        self._last_board_hash = None
        self._target_plan = None
        self._panic_mode = False
        self._generation = 0
        self._rot_mask_cache = {}
        self._plan_hold_done = False

    def _hash_board(self):
        grid = self.player.board.grid
        return hash(tuple(tuple(1 if cell is not None else 0 for cell in row) for row in grid))

    def _drain_results(self):
        while True:
            try:
                self._result_queue.get_nowait()
            except Empty:
                break

    def get_action(self):
        current_hash = self._hash_board()

        # Если поле изменилось, старые планы больше не актуальны
        if self._last_board_hash != current_hash:
            self._last_board_hash = current_hash
            self._generation += 1
            self.action_queue.clear()
            self._drain_results()
            self._is_computing = False
            self._target_plan = None
            self._plan_hold_done = False

        if self.action_queue:
            return self.action_queue.pop(0)

        # Обрабатываем результаты фонового потока
        while True:
            try:
                item = self._result_queue.get_nowait()
            except Empty:
                break

            try:
                gen, plan = item
            except Exception:
                continue

            # Игнорируем устаревшие результаты
            if gen != self._generation:
                continue

            self._is_computing = False

            if plan is None:
                self._target_plan = None
                self._plan_hold_done = False
                self.action_queue.append('hard_drop')
                return self.action_queue.pop(0)

            self._target_plan = plan
            self._plan_hold_done = False
            break

        if self.action_queue:
            return self.action_queue.pop(0)

        # Запускаем новый расчёт только если сейчас нет активного плана
        if self._target_plan is None and not self._is_computing:
            self._is_computing = True
            state = self._capture_state()
            threading.Thread(
                target=self._compute_in_background,
                args=(state, self._generation),
                daemon=True
            ).start()

        return self._fallback_action()

    def _fallback_action(self):
        """
        Безопасное выполнение плана по одному действию за кадр.
        План: (use_hold, shape_name, target_x, target_rot, target_y)
        """
        plan = self._target_plan
        player = self.player

        if plan is None or player is None or not player.alive or player.current_piece is None:
            return None

        try:
            use_hold, shape_name, target_x, target_rot, target_y = plan
        except ValueError:
            try:
                use_hold, shape_name, target_x, target_rot = plan
                target_y = None
            except Exception:
                return 'hard_drop'

        curr = player.current_piece

        # Если план требует hold, сначала делаем hold
        if use_hold and not player.hold_used and not self._plan_hold_done:
            self._plan_hold_done = True
            return 'hold'

        # Если hold должен был случиться, но не случился, не зависаем
        if use_hold and not player.hold_used and self._plan_hold_done:
            return 'hard_drop'

        # Если фигура уже не та, план устарел или что-то пошло не так
        if curr.shape_name != shape_name:
            return 'hard_drop'

        # Поворот
        if curr.rotation != target_rot:
            if self._can_rotate(curr):
                return 'rotate'

            # Иногда повернуть получается только после небольшого спуска
            if target_y is not None and curr.y < target_y and self._can_move(curr, 0, 1):
                return 'soft_drop'

            return 'hard_drop'

        # Движение вправо
        if curr.x < target_x:
            if self._can_move(curr, 1, 0):
                return 'right'

            # Если по горизонтали не проходим, пробуем спуститься
            if target_y is not None and curr.y < target_y and self._can_move(curr, 0, 1):
                return 'soft_drop'

            return 'hard_drop'

        # Движение влево
        if curr.x > target_x:
            if self._can_move(curr, -1, 0):
                return 'left'

            if target_y is not None and curr.y < target_y and self._can_move(curr, 0, 1):
                return 'soft_drop'

            return 'hard_drop'

        # Если позиция достигнута, роняем фигуру
        return 'hard_drop'

    def _can_rotate(self, piece):
        board = self.player.board
        test = copy.deepcopy(piece)
        test.rotate()

        if board.is_valid_position(test):
            return True

        for dx in (-1, 1, -2, 2):
            test.move(dx, 0)
            if board.is_valid_position(test):
                return True
            test.move(-dx, 0)

        return False

    def _can_move(self, piece, dx, dy=0):
        board = self.player.board
        test = copy.deepcopy(piece)
        test.move(dx, dy)
        return board.is_valid_position(test)

    def _compute_in_background(self, state, generation=0):
        try:
            plan = self._find_best_plan(state)
            self._result_queue.put((generation, plan))
        except Exception as e:
            Log.error(f"🧠 QwenAI краш потока: {e}")
            self._result_queue.put((generation, None))

    def _find_best_plan(self, state):
        w = state['width']
        h = state['height']
        grid = state['grid']

        rows = self._grid_to_masks(grid, w, h)
        before_metrics = self._compute_metrics(rows, w, h)

        curr_shape = state['curr_shape']
        hold_shape = state['hold_shape']
        hold_used = state['hold_used']
        next_shapes = state.get('next_shapes') or []

        if not curr_shape:
            return None

        candidates = [(False, curr_shape)]

        if not hold_used:
            if hold_shape:
                candidates.append((True, hold_shape))
            elif next_shapes:
                candidates.append((True, next_shapes[0]))

        initial = []

        for use_hold, shape_name in candidates:
            if not shape_name:
                continue

            rot_data = self._get_rot_data(shape_name)

            for rot_idx, (masks, sw, sh) in enumerate(rot_data):
                if sw <= 0 or sh <= 0 or sw > w or sh > h:
                    continue

                for x in range(0, w - sw + 1):
                    y = self._fast_drop_mask(masks, sw, sh, rows, w, h, x)
                    if y < 0:
                        continue

                    after_rows, lines = self._apply_and_clear(rows, masks, x, y, w, h)
                    after_metrics = self._compute_metrics(after_rows, w, h)
                    score = self._score_metrics(before_metrics, after_metrics, lines, w, h)

                    # Небольшой штраф за использование hold, чтобы не тратить его без нужды
                    if use_hold:
                        score -= float(self.weights.get('hold_cost', 0.08))

                    initial.append((
                        score,
                        use_hold,
                        shape_name,
                        x,
                        rot_idx,
                        y,
                        after_rows,
                        after_metrics
                    ))

        if not initial:
            return None

        initial.sort(key=lambda item: item[0], reverse=True)

        try:
            top_n = max(1, int(self.weights.get('top_n', 8)))
        except Exception:
            top_n = 8

        best_score = -float('inf')
        best_plan = None

        for score, use_hold, shape_name, x, rot_idx, y, after_rows, after_metrics in initial[:top_n]:
            next_name = self._get_lookahead_shape(use_hold, hold_shape, next_shapes)

            if next_name:
                next_score = self._best_future_score(next_name, after_rows, after_metrics, w, h)
                score += float(self.weights.get('lookahead', 0.22)) * next_score

            if score > best_score:
                best_score = score
                best_plan = (use_hold, shape_name, x, rot_idx, y)

        return best_plan

    def _get_lookahead_shape(self, use_hold, hold_shape, next_shapes):
        """
        Возвращает фигуру, которую нужно оценивать как следующую.
        Если hold пуст и мы его используем, активной станет next_shapes[0],
        а следующей после неё уже next_shapes[1].
        """
        if not use_hold:
            return next_shapes[0] if next_shapes else None

        if hold_shape:
            return next_shapes[0] if next_shapes else None

        return next_shapes[1] if len(next_shapes) > 1 else None

    def _best_future_score(self, shape_name, rows, before_metrics, width, height):
        best = -float('inf')

        for masks, sw, sh in self._get_rot_data(shape_name):
            if sw <= 0 or sh <= 0 or sw > width or sh > height:
                continue

            for x in range(0, width - sw + 1):
                y = self._fast_drop_mask(masks, sw, sh, rows, width, height, x)
                if y < 0:
                    continue

                after_rows, lines = self._apply_and_clear(rows, masks, x, y, width, height)
                after_metrics = self._compute_metrics(after_rows, width, height)
                score = self._score_metrics(before_metrics, after_metrics, lines, width, height)

                if score > best:
                    best = score

        return best if best != -float('inf') else -1000.0

    def _grid_to_masks(self, grid, width, height):
        rows = []

        for r in range(height):
            mask = 0
            row = grid[r]

            for c in range(width):
                if row[c] is not None:
                    mask |= 1 << c

            rows.append(mask)

        return rows

    def _masks_for_shape(self, shape):
        masks = []
        sh = len(shape)
        sw = len(shape[0]) if sh else 0

        for row in shape:
            mask = 0
            for c, val in enumerate(row):
                if val:
                    mask |= 1 << c
            masks.append(mask)

        return masks, sw, sh

    def _get_rot_data(self, shape_name):
        cached = self._rot_mask_cache.get(shape_name)
        if cached is not None:
            return cached

        rotations = self.rotations.get(shape_name, [])

        if not rotations and shape_name in SHAPES:
            rotations = [SHAPES[shape_name]]

        data = []

        for shape in rotations:
            masks, sw, sh = self._masks_for_shape(shape)
            data.append((masks, sw, sh))

        self._rot_mask_cache[shape_name] = data
        return data

    def _fast_drop_mask(self, shape_masks, sw, sh, rows, width, height, x):
        if x < 0 or x + sw > width or sw <= 0 or sh <= 0 or sh > height:
            return -1

        shifted = [m << x for m in shape_masks]
        y = 0
        limit = height - sh

        while y <= limit:
            collides = False

            for r, m in enumerate(shifted):
                if m and rows[y + r] & m:
                    collides = True
                    break

            if collides:
                break

            y += 1

        y -= 1
        return y if y >= 0 else -1

    def _apply_and_clear(self, rows, shape_masks, x, y, width, height):
        new_rows = rows[:]

        for r, mask in enumerate(shape_masks):
            if not mask:
                continue

            yy = y + r
            if 0 <= yy < height:
                new_rows[yy] |= mask << x

        full_mask = (1 << width) - 1
        cleared = [row for row in new_rows if row != full_mask]
        lines = height - len(cleared)

        if lines > 0:
            new_rows = [0] * lines + cleared

        return new_rows, lines

    def _compute_metrics(self, rows, width, height):
        heights = [0] * width
        holes = 0

        for c in range(width):
            bit = 1 << c
            found = False
            top = -1

            for r in range(height):
                if rows[r] & bit:
                    if top < 0:
                        top = r
                    found = True
                elif found:
                    holes += 1

            if top >= 0:
                heights[c] = height - top

        aggregate_height = sum(heights)
        max_height = max(heights) if heights else 0

        bumpiness = 0
        for i in range(width - 1):
            bumpiness += abs(heights[i] - heights[i + 1])

        well_depth = 0

        if width >= 2:
            if heights[0] < heights[1]:
                well_depth += heights[1] - heights[0]
            if heights[-1] < heights[-2]:
                well_depth += heights[-1] - heights[-2]

        for i in range(1, width - 1):
            if heights[i] < heights[i - 1] and heights[i] < heights[i + 1]:
                well_depth += min(heights[i - 1], heights[i + 1]) - heights[i]

        transitions = 0

        if self.weights.get('transitions', 0):
            if width > 1:
                low_mask = (1 << (width - 1)) - 1

                for row in rows:
                    transitions += ((row ^ (row >> 1)) & low_mask).bit_count()

                full_mask = (1 << width) - 1

                for i in range(height - 1):
                    transitions += ((rows[i] ^ rows[i + 1]) & full_mask).bit_count()

        return (
            heights,
            holes,
            max_height,
            aggregate_height,
            bumpiness,
            well_depth,
            transitions
        )

    def _score_metrics(self, before, after, lines, width, height):
        _, holes_before, max_before, agg_before, bump_before, well_before, _ = before
        _, holes_after, max_after, agg_after, bump_after, well_after, trans_after = after

        w = self.weights

        avg_height = agg_after / max(1, width)

        try:
            panic_threshold = float(w.get('panic_threshold', 0.62))
        except Exception:
            panic_threshold = 0.62

        panic = avg_height > height * panic_threshold or max_after >= max(1, height - 2)
        self._panic_mode = panic

        height_weight = w['height'] * (1.5 if panic else 1.0)
        holes_weight = w['holes'] * (1.3 if panic else 1.0)
        lines_weight = w['lines'] * (1.2 if panic else 1.0)

        score = (
            height_weight * avg_height +
            lines_weight * lines +
            holes_weight * holes_after +
            w['bumpiness'] * bump_after +
            w['well_depth'] * well_depth if False else 0
        )

        # Аккуратно собираем базовый счёт
        score = (
            height_weight * avg_height +
            lines_weight * lines +
            holes_weight * holes_after +
            w['bumpiness'] * bump_after +
            w['well_depth'] * well_after +
            w['max_height'] * max_after +
            w['transitions'] * trans_after
        )

        # Даунстэкинг: награждаем за улучшение состояния поля
        holes_fixed = max(0, holes_before - holes_after)
        bump_fixed = max(0, bump_before - bump_after)
        well_fixed = max(0, well_before - well_after)
        height_fixed = max(0, agg_before - agg_after)
        max_fixed = max(0, max_before - max_after)

        downstack_mult = 1.4 if panic else 1.0

        score += downstack_mult * (
            w['downstack_holes'] * holes_fixed +
            w['downstack_bump'] * bump_fixed +
            w['downstack_well'] * well_fixed +
            w['downstack_height'] * height_fixed +
            w['downstack_max'] * max_fixed
        )

        # Штраф за создание новых дыр
        holes_added = max(0, holes_after - holes_before)
        score += w.get('create_hole_penalty', -0.75) * holes_added

        # Штраф за рост высоты без очистки линий
        height_added = max(0, agg_after - agg_before)
        score += w.get('height_added_penalty', -0.015) * height_added

        # В панике дополнительно наказываем за рост максимального столбца
        if panic and max_after > max_before:
            score += w.get('panic_height_penalty', -1.25) * (max_after - max_before)

        # Почти проигрыш/проигрыш
        if max_after >= height:
            score -= 10000.0

        return score

    def evaluate(self, shape, x, drop_y, grid, width, height):
        """
        Совместимый внешний интерфейс оценки.
        Внутри использует уже новую даунстэкинг-эвристику.
        """
        rows = self._grid_to_masks(grid, width, height)
        before = self._compute_metrics(rows, width, height)

        masks, _, _ = self._masks_for_shape(shape)
        after_rows, lines = self._apply_and_clear(rows, masks, x, drop_y, width, height)
        after = self._compute_metrics(after_rows, width, height)

        return self._score_metrics(before, after, lines, width, height)

class CustomAI(BaseAI):
    """Кастомный ИИ с настраиваемыми весами из UI."""
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
        Log.info(f"CustomAI инициализирован для игрока {player.id} с весами: {self.weights}")

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
            ai_config = settings.get('ai_config', {}) # Теперь сюда придёт реальный конфиг из main.py
            Log.info(f"Игрок {player_id}: запуск бота типа '{ai_type}'")
            
            if ai_type == 'deepseek': 
                self.bot = DeepSeekAI(self, ai_config)
            elif ai_type == 'custom': 
                self.bot = CustomAI(self, ai_config)
            else: 
                self.bot = QwenAI(self, ai_config)

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
            Log.warning(f"💀 Игрок {self.id}: GAME OVER! Стакан заполнен до верха.")

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
            self.bot_update(dt, current_time)
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
        if lines_cleared > 0:
            Log.info(f"✨ Игрок {self.id} собрал {lines_cleared} линий! Score: {self.board.score}")
        
        new_level = 1 + self.board.lines_cleared_total // 10
        if new_level > self.level:
            self.level = new_level
            Log.info(f"📈 Игрок {self.id} достиг уровня {new_level}! Скорость: {min(10.0, self.speed + (self.level - 1) * 0.15):.1f}")
        self.spawn_piece()

    def bot_update(self, dt, current_time):
        if self.bot is None or self.current_piece is None: return
        action = self.bot.get_action()
        if action is not None:
            Log.debug(f"🤖 Бот {self.id} выполняет: {action}")
            self.handle_action(action)

# ================= GAME CLASS =================
class Game:
    def __init__(self, settings):
        pygame.init()
        self.settings = settings
        self.game_mode = settings['game_mode']
        self.players_data = settings['players']
        self.num_players = len([p for p in self.players_data.values() if p.get('enabled', False)])
        self.players = []
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
                if pdata.get('enabled'): (t1 if len(t1)<2 else t2).append((pid, pdata))
            self.team_board_w = WIDTH * 2  # 🔧 15 * 2 = 30 клеток для двух игроков в команде
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
            board_px_w = self.team_board_w * CELL_SIZE  # 🔧 Подхватываем реальную ширину команды
            total_w = (board_px_w * 2) + (spacing * 3)
            total_h = info_top + board_px_h + info_bottom + spacing * 2
            self.layout_width, self.layout_height = total_w, total_h
            self.board_width_px, self.board_height_px = board_px_w, board_px_h
            self.team_positions = [(spacing, info_top + spacing), (spacing + board_px_w + spacing, info_top + spacing)]
            return self.team_positions
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
        while self.running:
            dt = self.clock.tick(60)
            current_time = time.time()
            for event in pygame.event.get():
                if event.type == pygame.QUIT: self.running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_p or event.key == pygame.K_SPACE:
                        self.paused = not self.paused
                        Log.info(f"⏸️ Игра {'ПРИОСТАНОВЛЕНА' if self.paused else 'ПРОДОЛЖЕНА'}")
                    elif not self.paused: self.handle_keydown(event.key)
                elif event.type == pygame.KEYUP and not self.paused: self.handle_keyup(event.key)
            if not self.paused:
                for player in self.players: player.update(dt, current_time)
                self.check_game_over()
            self.draw()
            if self.paused: self.draw_pause_overlay()
            pygame.display.flip()
        pygame.quit()

    def handle_keydown(self, key):
        for player in self.players:
            if player.is_bot or not player.alive: continue
            keymap = KEYMAP.get(player.id, {})
            for action, k in keymap.items():
                if key == k:
                    if action in ['rotate', 'hard_drop', 'hold']: player.handle_action(action)
                    else: player.key_state[action] = True

    def handle_keyup(self, key):
        for player in self.players:
            if player.is_bot: continue
            keymap = KEYMAP.get(player.id, {})
            for action, k in keymap.items():
                if key == k and action not in ['rotate', 'hard_drop', 'hold']: player.key_state[action] = False

    def check_game_over(self):
        if self.game_mode == '2vs2':
            t1_alive = any(p.alive for p in self.teams[0]['players'])
            t2_alive = any(p.alive for p in self.teams[1]['players'])
            if not t1_alive or not t2_alive: 
                Log.info(f"🏆 Команда {'1' if not t1_alive else '2'} проиграла. Игра завершена.")
                self.running = False
        else:
            if not any(p.alive for p in self.players):
                Log.info("💀 Все игроки проиграли. Игра завершена.")
                self.running = False

    def draw(self):
        self.screen.fill((30,30,30))
        if self.game_mode == 'coop': self.draw_coop()
        elif self.game_mode == '2vs2': self.draw_2vs2()
        else: self.draw_vs()

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
        for i in range(2):
            x, y = self.team_positions[i]
            team_label = self.font.render(f"TEAM {i+1}", True, (200, 200, 200))
            self.screen.blit(team_label, (x + self.board_width_px//2 - team_label.get_width()//2, y - 25))
            board = self.teams[i]['board']
            active = next((p for p in self.teams[i]['players'] if p.alive and p.current_piece), None)
            self.draw_board(board, x, y, active)
            info_start_y = y + self.board_height_px + 5
            for j, player in enumerate(self.teams[i]['players']):
                self.draw_player_info(player, x, info_start_y + j * 130)

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
        text_rect = pause_text.get_rect(center=(self.layout_width//2, self.layout_height//2))
        self.screen.blit(pause_text, text_rect)
        hint = self.small_font.render("Press P or Space to resume", True, (200, 200, 200))
        hint_rect = hint.get_rect(center=(self.layout_width//2, self.layout_height//2 + 40))
        self.screen.blit(hint, hint_rect)
