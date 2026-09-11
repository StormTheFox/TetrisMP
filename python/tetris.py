import pygame
import random
import copy
import time
import threading
import asyncio
import json
import uuid
from queue import Queue, Empty
from typing import Dict, Any, Optional
from rich.console import Console

console = Console()

# ================= CONFIG =================

WIDTH = 15
HEIGHT = 30
CELL_SIZE = 10
MINI_BOARD_WIDTH = 100

PIECE_COLORS = {
    "I": (0, 255, 255),
    "O": (255, 255, 0),
    "T": (128, 0, 128),
    "S": (0, 255, 0),
    "Z": (255, 0, 0),
    "L": (255, 165, 0),
    "J": (0, 0, 255),
}

SHAPES = {
    "I": [[1, 1, 1, 1]],
    "O": [[1, 1], [1, 1]],
    "T": [[0, 1, 0], [1, 1, 1]],
    "S": [[0, 1, 1], [1, 1, 0]],
    "Z": [[1, 1, 0], [0, 1, 1]],
    "L": [[1, 0, 0], [1, 1, 1]],
    "J": [[0, 0, 1], [1, 1, 1]],
}

KEYMAP = {
    1: {
        "hard_drop": pygame.K_q,
        "rotate": pygame.K_w,
        "left": pygame.K_a,
        "right": pygame.K_d,
        "soft_drop": pygame.K_s,
        "hold": pygame.K_e,
    },
    2: {
        "hard_drop": pygame.K_r,
        "rotate": pygame.K_t,
        "left": pygame.K_f,
        "right": pygame.K_h,
        "soft_drop": pygame.K_g,
        "hold": pygame.K_y,
    },
    3: {
        "hard_drop": pygame.K_u,
        "rotate": pygame.K_i,
        "left": pygame.K_j,
        "right": pygame.K_l,
        "soft_drop": pygame.K_k,
        "hold": pygame.K_o,
    },
    4: {
        "hard_drop": pygame.K_RSHIFT,
        "rotate": pygame.K_UP,
        "left": pygame.K_LEFT,
        "right": pygame.K_RIGHT,
        "soft_drop": pygame.K_DOWN,
        "hold": pygame.K_RCTRL,
    },
}

DAS_DELAY = 200
DAS_REPEAT = 25

LINE_SCORES = {1: 100, 2: 250, 3: 500, 4: 1000}

GAME_MODES = ["vs", "coop", "2vs2", "lan", "global", "self_learning", "teacher_student"]

SELF_LEARNING_POPULATION = 24
SELF_LEARNING_ELITE_SIZE = 6
SELF_LEARNING_MUTATION_CHANCE = 0.35
SELF_LEARNING_MUTATION_STRENGTH = 0.10
SELF_LEARNING_RANDOM_IMMIGRANT_CHANCE = 0.08


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
                    self.grid[yy] = self.grid[yy - 1][:]

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

        self.weights = {
            "height": -0.510066,
            "lines": 0.760666,
            "holes": -0.356630,
            "bumpiness": -0.184483,
            "well_depth": -0.15,
            "transitions": -0.03,
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
            "grid": [row[:] for row in self.player.board.grid],
            "width": self.player.board.width,
            "height": self.player.board.height,
            "curr_shape": self.player.current_piece.shape_name,
            "hold_shape": self.player.hold_piece.shape_name if self.player.hold_piece else None,
            "hold_used": self.player.hold_used,
            "next_shapes": [p.shape_name for p in self.player.next_pieces],
        }

    def _fast_drop(self, shape, grid, width, height, offset_x):
        sh = len(shape)
        y = 0

        while y + sh <= height:
            collides = False

            for r, row in enumerate(shape):
                for c, val in enumerate(row):
                    if val:
                        nx = offset_x + c
                        ny = y + r

                        if nx < 0 or nx >= width or grid[ny][nx] is not None:
                            collides = True
                            break

                if collides:
                    break

            if collides:
                break

            y += 1

        return y - 1

    def evaluate(self, shape, x, drop_y, grid, width, height):
        sim_grid = [row[:] for row in grid]

        for r, row in enumerate(shape):
            for c, val in enumerate(row):
                if val:
                    sim_grid[drop_y + r][x + c] = 1

        heights = [0] * width
        holes = 0
        lines = 0
        bumpiness = 0
        transitions = 0
        well_depth = 0

        for col in range(width):
            top_row = None

            for row in range(height):
                if sim_grid[row][col] is not None:
                    top_row = row
                    break

            if top_row is not None:
                heights[col] = height - top_row

                for row in range(top_row + 1, height):
                    if sim_grid[row][col] is None:
                        holes += 1

            prev_filled = False

            for row in range(height):
                filled = sim_grid[row][col] is not None

                if filled != prev_filled:
                    transitions += 1

                prev_filled = filled

        for row in range(height):
            if all(sim_grid[row][col] is not None for col in range(width)):
                lines += 1

        for col in range(width - 1):
            bumpiness += abs(heights[col] - heights[col + 1])

        for col in range(1, width - 1):
            cur = heights[col]
            lft = heights[col - 1]
            rgt = heights[col + 1]

            if cur < lft and cur < rgt:
                well_depth += min(lft, rgt) - cur

        max_h = max(heights) if heights else 0
        avg_h = (sum(heights) / width) if width else 0

        return (
            self.weights.get("height", -0.51) * avg_h
            + self.weights.get("lines", 0.76) * lines
            + self.weights.get("holes", -0.36) * holes
            + self.weights.get("bumpiness", -0.18) * bumpiness
            + self.weights.get("well_depth", -0.15) * well_depth
            + self.weights.get("max_height", 0.0) * max_h
            + self.weights.get("transitions", -0.03) * transitions
        )

    def _compute_in_background(self, state):
        t0 = time.time()

        try:
            candidates = [("current", state["curr_shape"], False)]

            if not state["hold_used"]:
                if state["hold_shape"]:
                    candidates.append(("hold", state["hold_shape"], True))
                elif state["next_shapes"]:
                    candidates.append(("next", state["next_shapes"][0], True))

            best_score = float("-inf")
            best_plan = None

            w, h, grid = state["width"], state["height"], state["grid"]

            for src, shape_name, use_hold in candidates:
                for rot_idx, shape in enumerate(self.rotations[shape_name]):
                    sw = len(shape[0])

                    if sw > w:
                        continue

                    for x in range(0, w - sw + 1):
                        y = self._fast_drop(shape, grid, w, h, x)

                        if y < 0:
                            continue

                        score = self.evaluate(shape, x, y, grid, w, h)

                        if score > best_score:
                            best_score = score
                            best_plan = (use_hold, x, rot_idx)

            elapsed = (time.time() - t0) * 1000

            Log.debug(
                f"🧠 ИИ (Игрок {self.player.id}): просчёт за {elapsed:.1f}мс | "
                f"Score: {best_score:.2f} | План: {best_plan}"
            )

            self._result_queue.put(best_plan)

        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            Log.error(f"🧠 ИИ (Игрок {self.player.id}): краш потока за {elapsed:.1f}мс -> {e}")
            self._result_queue.put(None)

    def _generate_actions(self, plan):
        if plan is None:
            if not hasattr(self, "_warned_none"):
                Log.warning(f"⚠️ ИИ (Игрок {self.player.id}): план не найден, экстренный hard_drop")
                self._warned_none = True

            self.action_queue.append("hard_drop")
            return

        self._warned_none = False

        use_hold, target_x, target_rot = plan
        queue = []

        if use_hold:
            queue.append("hold")

        curr = self.player.current_piece

        dr = (target_rot - curr.rotation) % 4

        sim = copy.deepcopy(curr)
        sim.shape = [row[:] for row in self.rotations[sim.shape_name][target_rot]]
        sim.rotation = target_rot
        sim.x = curr.x

        if not self.player.board.is_valid_position(sim):
            for kdx in [-1, 1, -2, 2]:
                sim.move(kdx, 0)

                if self.player.board.is_valid_position(sim):
                    break

                sim.move(-kdx, 0)

        dx = target_x - sim.x

        queue.extend(["rotate"] * dr)

        if dx > 0:
            queue.extend(["right"] * dx)
        else:
            queue.extend(["left"] * abs(dx))

        queue.append("hard_drop")

        Log.debug(f"📝 ИИ (Игрок {self.player.id}): сгенерирована очередь -> {queue}")
        self.action_queue = queue

    def get_action(self):
        if self.action_queue:
            return self.action_queue.pop(0)

        try:
            plan = self._result_queue.get_nowait()
            self._generate_actions(plan)
            self._is_computing = False
        except Empty:
            pass

        if self.action_queue:
            return self.action_queue.pop(0)

        if not self._is_computing:
            Log.debug(f"🚀 ИИ (Игрок {self.player.id}): запуск фоновой задачи")

            self._is_computing = True
            self._compute_thread = threading.Thread(
                target=self._compute_in_background,
                args=(self._capture_state(),),
                daemon=True,
            )
            self._compute_thread.start()

        return None


# ================= DEEPSEEK AI =================


class DeepSeekAI(BaseAI):
    def __init__(self, player, config=None):
        super().__init__(player, config)

        self.weights = {
            "height": -0.55,
            "lines": 1.10,
            "holes": -0.95,
            "bumpiness": -0.22,
            "well_depth": -0.12,
            "max_height": -0.65,
            "transitions": -0.018,
        }

        self.critical_weights = {
            "height": -0.90,
            "lines": 1.55,
            "holes": -1.45,
            "bumpiness": -0.35,
            "well_depth": -0.05,
            "max_height": -1.60,
            "transitions": -0.030,
        }

    def evaluate(self, shape, x, drop_y, grid, width, height):
        sim_grid = [row[:] for row in grid]

        for r, row in enumerate(shape):
            for c, val in enumerate(row):
                if val:
                    sim_grid[drop_y + r][x + c] = 1

        heights = [0] * width
        holes = 0
        bumpiness = 0
        transitions = 0
        well_depth = 0

        for col in range(width):
            top_row = None

            for row in range(height):
                if sim_grid[row][col] is not None:
                    top_row = row
                    break

            if top_row is not None:
                heights[col] = height - top_row

                for row in range(top_row + 1, height):
                    if sim_grid[row][col] is None:
                        holes += 1

            prev_filled = False

            for row in range(height):
                filled = sim_grid[row][col] is not None

                if filled != prev_filled:
                    transitions += 1

                prev_filled = filled

        lines = 0

        for row in range(height):
            if all(sim_grid[row][col] is not None for col in range(width)):
                lines += 1

        for col in range(width - 1):
            bumpiness += abs(heights[col] - heights[col + 1])

        for col in range(1, width - 1):
            cur = heights[col]
            lft = heights[col - 1]
            rgt = heights[col + 1]

            if cur < lft and cur < rgt:
                well_depth += min(lft, rgt) - cur

        max_h = max(heights) if heights else 0
        avg_h = (sum(heights) / width) if width else 0

        if height and max_h / height > 0.7:
            w = self.critical_weights
        else:
            w = self.weights

        return (
            w["height"] * avg_h
            + w["lines"] * lines
            + w["holes"] * holes
            + w["bumpiness"] * bumpiness
            + w["well_depth"] * well_depth
            + w["max_height"] * max_h
            + w["transitions"] * transitions
        )


class QwenAI(BaseAI):
    def __init__(self, player, config=None):
        super().__init__(player, config)

        self.weights = {
            "height": -0.62,
            "lines": 0.95,
            "holes": -0.85,
            "bumpiness": -0.24,
            "well_depth": -0.18,
            "max_height": -0.55,
            "transitions": -0.006,
        }


class CustomAI(BaseAI):
    def __init__(self, player, config=None):
        super().__init__(player, config)

        config = config or {}

        self.weights = {
            "height": config.get("height", -0.51),
            "lines": config.get("lines", 0.76),
            "holes": config.get("holes", -0.36),
            "bumpiness": config.get("bumpiness", -0.18),
            "well_depth": config.get("well_depth", -0.15),
            "transitions": config.get("transitions", -0.03),
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

    def export_learned_weights(self):
        counts = {
            "left": 0,
            "right": 0,
            "rotate": 0,
            "soft_drop": 0,
            "hard_drop": 0,
            "hold": 0,
        }

        for _, action in getattr(self, "action_log", []):
            if action in counts:
                counts[action] += 1

        total = max(1, sum(counts.values()))

        rotate_ratio = counts["rotate"] / total
        move_ratio = (counts["left"] + counts["right"]) / total
        soft_ratio = counts["soft_drop"] / total
        hold_ratio = counts["hold"] / total

        weights = dict(self.weights)

        base_height = float(weights.get("height", -0.51))
        base_lines = float(weights.get("lines", 0.76))
        base_holes = float(weights.get("holes", -0.36))
        base_bumpiness = float(weights.get("bumpiness", -0.18))
        base_well_depth = float(weights.get("well_depth", -0.15))

        weights["height"] = base_height - (soft_ratio * 0.20)
        weights["lines"] = base_lines + (rotate_ratio * 0.30)
        weights["holes"] = base_holes - (move_ratio * 0.25)
        weights["bumpiness"] = base_bumpiness - (move_ratio * 0.12)
        weights["well_depth"] = base_well_depth - (hold_ratio * 0.08)

        result = {}

        for key, value in weights.items():
            try:
                result[key] = max(-2.0, min(2.0, float(value)))
            except Exception:
                continue

        return result

class SelfLearningEngine:
    def __init__(
        self,
        iterations: int,
        ai_type: str,
        base_config: dict,
        start_from_zero: bool = False,
        show_gameplay_callback=None
    ):
        try:
            iterations = int(iterations)
        except Exception:
            iterations = SELF_LEARNING_DEFAULT_ITERATIONS

        if iterations == 0:
            iterations = 1

        self.iterations = iterations
        self.ai_type = ai_type
        self.start_from_zero = start_from_zero
        self.show_gameplay_callback = show_gameplay_callback

        default_weights = {
            "height": -0.51,
            "lines": 0.76,
            "holes": -0.36,
            "bumpiness": -0.18,
            "well_depth": -0.15,
        }

        if self.start_from_zero:
            base = {k: 0.0 for k in default_weights}
        else:
            base = dict(base_config) if base_config else {}
            for key, value in default_weights.items():
                base.setdefault(key, value)

        self.base_weights = self._sanitize_weights(base)
        self.weights = dict(self.base_weights)

        self.best_weights = dict(self.base_weights)
        self.best_score = -1
        self.best_lines = 0

        self.population = []
        self.population_size = max(4, int(SELF_LEARNING_POPULATION))
        self.elite_size = max(
            1,
            min(int(SELF_LEARNING_ELITE_SIZE), self.population_size // 3)
        )

        self.stats = []
        self.stats_limit = 1000
        self.iterations_done = 0

        self.rotations = self._precompute_rotations()

    def _sanitize_weights(self, weights: dict) -> dict:
        result = {}

        for key, value in (weights or {}).items():
            try:
                result[str(key)] = max(-2.0, min(2.0, float(value)))
            except Exception:
                continue

        if not result:
            result = {
                "height": -0.51,
                "lines": 0.76,
                "holes": -0.36,
                "bumpiness": -0.18,
                "well_depth": -0.15,
            }

        return result

    def run(self, on_iter_callback=None, stop_event=None):
        infinite = self.iterations < 0
        i = 0

        while True:
            if stop_event is not None and stop_event.is_set():
                break

            i += 1

            if not infinite and i > self.iterations:
                break

            candidate_weights = self._next_candidate()
            score, lines, elapsed = self._evaluate_candidate(
                candidate_weights,
                stop_event=stop_event
            )

            self._add_candidate(candidate_weights, score, lines)

            if score > self.best_score or (
                score == self.best_score and lines > self.best_lines
            ):
                self.best_score = score
                self.best_lines = lines
                self.best_weights = dict(candidate_weights)

            entry = {
                "iter": i,
                "score": int(round(score)),
                "lines": int(lines),
                "time": round(elapsed, 2),
                "best": int(round(self.best_score)),
                "top": int(round(self.population[0]["score"])) if self.population else int(round(score)),
            }

            self.stats.append(entry)

            if len(self.stats) > self.stats_limit * 2:
                self.stats = self.stats[-self.stats_limit:]

            self.iterations_done += 1

            if on_iter_callback:
                try:
                    if on_iter_callback(entry) is False:
                        break
                except Exception:
                    pass

            if i % 25 == 0:
                Log.info(
                    f"🧬 Self-learning итерация {i}"
                    f"{'/∞' if infinite else f'/{self.iterations}'}: "
                    f"score={entry['score']}, lines={entry['lines']}, "
                    f"best={entry['best']}, time={elapsed:.2f}s"
                )

        if self.best_score < 0:
            self.best_score = 0

        self.weights = dict(self.best_weights)

        return {
            "best_score": self.best_score,
            "best_weights": self.best_weights,
            "stats": self.stats,
            "iterations_done": self.iterations_done,
            "stopped": bool(stop_event is not None and stop_event.is_set()),
        }

    def _next_candidate(self) -> dict:
        if not self.population:
            return dict(self.base_weights)

        if random.random() < SELF_LEARNING_RANDOM_IMMIGRANT_CHANCE:
            return self._random_exploration_weights()

        if len(self.population) < self.population_size and random.random() < 0.25:
            return self._random_exploration_weights()

        parent = self._select_parent()
        return self._mutate_weights(parent["weights"])

    def _select_parent(self) -> dict:
        top_n = max(1, min(self.elite_size, len(self.population)))
        top = self.population[:top_n]

        weights = list(range(top_n, 0, -1))

        return random.choices(top, weights=weights, k=1)[0]

    def _add_candidate(self, weights: dict, score: float, lines: int):
        self.population.append(
            {
                "weights": self._sanitize_weights(weights),
                "score": float(score),
                "lines": int(lines),
            }
        )

        self.population.sort(
            key=lambda item: (item["score"], item["lines"]),
            reverse=True
        )

        if len(self.population) > self.population_size:
            self.population.pop()

    def _random_exploration_weights(self) -> dict:
        if self.best_score > -1 and random.random() < 0.65:
            source = self.best_weights
        else:
            source = self.base_weights

        return self._mutate_weights(
            source,
            chance=0.75,
            strength=SELF_LEARNING_MUTATION_STRENGTH * 2.5
        )

    def _mutate_weights(
        self,
        src: dict,
        chance: float = None,
        strength: float = None
    ) -> dict:
        if chance is None:
            chance = SELF_LEARNING_MUTATION_CHANCE

        if strength is None:
            strength = SELF_LEARNING_MUTATION_STRENGTH

        src = self._sanitize_weights(src)
        child = {}
        mutated = False

        keys = list(src.keys())

        if not keys:
            return dict(self.base_weights)

        for key in keys:
            value = float(src[key])

            if random.random() < chance:
                value += random.uniform(-strength, strength)
                mutated = True

            child[key] = max(-2.0, min(2.0, value))

        if not mutated:
            key = random.choice(keys)
            child[key] = max(
                -2.0,
                min(
                    2.0,
                    float(src[key]) + random.uniform(-strength, strength)
                )
            )

        return child

    def _evaluate_candidate(self, weights: dict, stop_event=None):
        self.weights = dict(weights)
        return self._run_one(stop_event=stop_event)

    def _precompute_rotations(self):
        rotations = {}

        for name, shape in SHAPES.items():
            rots = [shape]
            current = [row[:] for row in shape]

            for _ in range(3):
                current = [list(row) for row in zip(*current[::-1])]
                rots.append(current)

            rotations[name] = rots

        return rotations

    def _fast_drop(self, shape, grid, width, height, offset_x):
        shape_height = len(shape)
        y = 0

        while y + shape_height <= height:
            collides = False

            for r, row in enumerate(shape):
                for c, val in enumerate(row):
                    if val:
                        nx = offset_x + c
                        ny = y + r

                        if nx < 0 or nx >= width or grid[ny][nx] is not None:
                            collides = True
                            break

                if collides:
                    break

            if collides:
                break

            y += 1

        return y - 1

    def _evaluate(self, shape, x, drop_y, grid, width, height):
        sim_grid = [row[:] for row in grid]

        for r, row in enumerate(shape):
            for c, val in enumerate(row):
                if val:
                    yy = drop_y + r
                    xx = x + c

                    if yy < 0 or yy >= height or xx < 0 or xx >= width:
                        return float("-inf")

                    sim_grid[yy][xx] = 1

        heights = [0] * width
        holes = 0
        lines = 0
        bumpiness = 0

        for col in range(width):
            top_row = None

            for row in range(height):
                if sim_grid[row][col] is not None:
                    top_row = row
                    break

            if top_row is not None:
                heights[col] = height - top_row

                for row in range(top_row + 1, height):
                    if sim_grid[row][col] is None:
                        holes += 1

        for row in range(height):
            if all(sim_grid[row][col] is not None for col in range(width)):
                lines += 1

        for col in range(width - 1):
            bumpiness += abs(heights[col] - heights[col + 1])

        well_depth = 0

        for col in range(1, width - 1):
            current = heights[col]
            left = heights[col - 1]
            right = heights[col + 1]

            if current < left and current < right:
                well_depth += min(left, right) - current

        avg_height = sum(heights) / width if width else 0
        max_height = max(heights) if heights else 0

        return (
            self.weights.get("height", -0.51) * avg_height
            + self.weights.get("lines", 0.76) * lines
            + self.weights.get("holes", -0.36) * holes
            + self.weights.get("bumpiness", -0.18) * bumpiness
            + self.weights.get("well_depth", -0.15) * well_depth
            + self.weights.get("max_height", 0.0) * max_height
        )

    def _run_one(self, stop_event=None):
        start_time = time.time()

        board = Board(WIDTH, HEIGHT)
        shape_names = list(SHAPES.keys())

        piece = Piece(random.choice(shape_names), (255, 255, 255))

        max_pieces = 350
        placed_pieces = 0

        while placed_pieces < max_pieces:
            if stop_event is not None and stop_event.is_set():
                break

            if not board.is_valid_position(piece):
                break

            best_score = float("-inf")
            best_placement = None

            for shape in self.rotations[piece.shape_name]:
                shape_width = len(shape[0])

                if shape_width > board.width:
                    continue

                for x in range(0, board.width - shape_width + 1):
                    y = self._fast_drop(
                        shape,
                        board.grid,
                        board.width,
                        board.height,
                        x
                    )

                    if y < 0:
                        continue

                    score = self._evaluate(
                        shape,
                        x,
                        y,
                        board.grid,
                        board.width,
                        board.height
                    )

                    if score > best_score or (
                        score == best_score and random.random() < 0.3
                    ):
                        best_score = score
                        best_placement = (shape, x, y)

            if best_placement is None:
                break

            shape, x, y = best_placement

            for r, row in enumerate(shape):
                for c, val in enumerate(row):
                    if val and y + r >= 0:
                        board.grid[y + r][x + c] = piece.color

            lines = board.clear_lines()
            board.lines_cleared_total += lines
            board.score += LINE_SCORES.get(lines, 0)

            if self.show_gameplay_callback:
                self.show_gameplay_callback(
                    grid=[row[:] for row in board.grid],
                    width=board.width,
                    height=board.height,
                    score=board.score,
                    lines=board.lines_cleared_total,
                    piece_name=piece.shape_name,
                    placed_pieces=placed_pieces + 1,
                )
                time.sleep(0.01)

            placed_pieces += 1
            piece = Piece(random.choice(shape_names), piece.color)

        elapsed = time.time() - start_time

        return board.score, board.lines_cleared_total, elapsed

class Player:
    def __init__(self, player_id, settings, board):
        self.id = player_id
        self.nickname = settings.get("nickname", f"Player {player_id}")
        self.color = pygame.Color(settings["color"])
        self.speed = settings["speed"]
        self.is_bot = settings.get("is_bot", False)

        self.board = board
        self.hold_piece = None
        self.hold_used = False
        self.next_pieces = []
        self.current_piece = None
        self.alive = True
        self.fall_timer = 0
        self.level = 1
        self.game_over_time = None

        self.key_state = {
            action: False
            for action in ["left", "right", "soft_drop", "hard_drop", "rotate", "hold"]
        }

        self.key_timers = {action: 0 for action in ["left", "right", "soft_drop"]}
        self.das_triggered = {action: False for action in ["left", "right", "soft_drop"]}

        self.bot = None

        if self.is_bot:
            ai_type = settings.get("ai_type", "qwen")
            ai_config = settings.get("ai_config", {})

            if ai_type == "custom":
                self.bot = CustomAI(self, ai_config)
            elif ai_type == "student":
                self.bot = None
            elif ai_type == "deepseek":
                self.bot = DeepSeekAI(self, ai_config)
            else:
                self.bot = QwenAI(self, ai_config)

        self.generate_next_pieces()
        self.spawn_piece()

    def generate_next_pieces(self, count=3):
        shapes = list(SHAPES.keys())

        for _ in range(count):
            self.next_pieces.append(Piece(random.choice(shapes), self.color))

    def spawn_piece(self):
        if not self.next_pieces:
            self.generate_next_pieces()

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
        if not self.alive:
            return

        if self.is_bot:
            if self.bot:
                action = self.bot.get_action()

                if action:
                    self.handle_action(action)

            return

        for action in ["left", "right", "soft_drop"]:
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
        if not self.alive or self.current_piece is None:
            return

        if action == "left":
            self.move_piece(-1, 0)
        elif action == "right":
            self.move_piece(1, 0)
        elif action == "soft_drop":
            self.move_piece(0, 1)
        elif action == "hard_drop":
            self.hard_drop()
        elif action == "rotate":
            self.rotate_piece()
        elif action == "hold":
            self.hold_current()

    def move_piece(self, dx, dy):
        self.current_piece.move(dx, dy)

        if not self.board.is_valid_position(self.current_piece):
            self.current_piece.move(-dx, -dy)

            if dy == 1:
                self.lock_piece()

    def rotate_piece(self):
        self.current_piece.rotate()

        if not self.board.is_valid_position(self.current_piece):
            for dx in [-1, 1, -2, 2]:
                self.current_piece.move(dx, 0)

                if self.board.is_valid_position(self.current_piece):
                    return

                self.current_piece.move(-dx, 0)

            for _ in range(3):
                self.current_piece.rotate()

    def hard_drop(self):
        while self.board.is_valid_position(self.current_piece):
            self.current_piece.move(0, 1)

        self.current_piece.move(0, -1)
        self.lock_piece()

    def lock_piece(self):
        self.board.place_piece(self.current_piece)

        new_level = 1 + self.board.lines_cleared_total // 10

        if new_level > self.level:
            self.level = new_level

        self.spawn_piece()


# ================= GAME CLASS =================


class Game:
    def __init__(self, settings):
        pygame.init()

        self.settings = settings
        self.current_user = settings.get("current_user", "Guest")
        self.game_mode = settings["game_mode"]
        self.players_data = settings["players"]
        self.dynamic_keymap = settings.get("dynamic_keymap", {})
        self.is_spectator = settings.get("is_spectator", False)

        self.num_players = len([p for p in self.players_data.values() if p.get("enabled", False)])

        self.players = []
        self.client_id = None

        if self.game_mode == "self_learning":
            self.running = False
            return

        self.running = True
        self.paused = False
        self.clock = pygame.time.Clock()
        self.start_time = time.time()

        self.font = pygame.font.Font(None, 24)
        self.small_font = pygame.font.Font(None, 18)
        self.big_font = pygame.font.Font(None, 48)

        if self.game_mode == "coop":
            total_width = WIDTH * self.num_players
            self.shared_board = Board(total_width, HEIGHT)

            for pid, pdata in self.players_data.items():
                if pdata.get("enabled"):
                    self.players.append(Player(pid, pdata, self.shared_board))

        elif self.game_mode == "2vs2":
            t1, t2 = [], []

            for pid, pdata in self.players_data.items():
                if pdata.get("enabled"):
                    if len(t1) < 2:
                        t1.append((pid, pdata))
                    else:
                        t2.append((pid, pdata))

            self.team_board_w = WIDTH * 2
            self.team1_board = Board(self.team_board_w * 2, HEIGHT)
            self.team2_board = Board(self.team_board_w * 2, HEIGHT)

            self.teams = [
                {"board": self.team1_board, "players": []},
                {"board": self.team2_board, "players": []},
            ]

            for pid, pdata in t1:
                p = Player(pid, pdata, self.team1_board)
                self.players.append(p)
                self.teams[0]["players"].append(p)

            for pid, pdata in t2:
                p = Player(pid, pdata, self.team2_board)
                self.players.append(p)
                self.teams[1]["players"].append(p)

        else:
            for pid, pdata in self.players_data.items():
                if pdata.get("enabled"):
                    self.players.append(
                        Player(
                            pid,
                            pdata,
                            Board(WIDTH, HEIGHT, pdata["color"]),
                        )
                    )

        if self.game_mode == "teacher_student":
            teacher = next((p for p in self.players if p.id == 1), None)
            student = next((p for p in self.players if p.id == 2), None)

            if student is not None:
                delay = int(settings.get("teacher_student_delay", TEACHER_STUDENT_DELAY))
                student_config = settings.get("players", {}).get(2, {}).get("ai_config", {})

                student.bot = StudentAI(
                    student,
                    teacher,
                    delay=delay,
                    config=student_config,
                )

                student.is_bot = True

        self.layout_positions = self.calculate_layout()
        self.remote_player_boards = {}
        self.network_client = None

        if self.game_mode in ["lan", "global"] or self.settings.get("network_mode") in ["lan", "online"]:
            host = settings.get("server_host", "127.0.0.1")
            port = settings.get("server_port", 8888)
            room_name = settings.get("room_name", "default_room")
            is_host = settings.get("is_host", True)

            self.client_id = uuid.uuid4().hex[:8]
            self._own_state_keys = set()

            player_info = {
                f"{self.client_id}:{p.id}": {
                    "client_id": self.client_id,
                    "nickname": p.nickname,
                    "color": f"#{p.color.r:02x}{p.color.g:02x}{p.color.b:02x}",
                    "is_spectator": False,
                }
                for p in self.players
            }

            self.network_client = NetworkClient(
                host,
                port,
                room_name,
                is_host,
                player_info,
                client_id=self.client_id,
            )

            self.network_client.start()
            self.remote_player_boards = {}

        self.screen = pygame.display.set_mode((self.layout_width, self.layout_height))
        pygame.display.set_caption("Tetris MP")

    def calculate_layout(self):
        board_px_w, board_px_h = WIDTH * CELL_SIZE, HEIGHT * CELL_SIZE
        spacing, info_top = 20, 30
        info_bottom = 100 if self.game_mode != "coop" else 140

        if self.game_mode == "coop":
            total_w = (WIDTH * self.num_players * CELL_SIZE) + 40
            total_h = info_top + board_px_h + info_bottom + 40

            self.layout_width, self.layout_height = total_w, total_h
            self.board_position = (20, info_top + 20)
            self.board_width_px = WIDTH * self.num_players * CELL_SIZE
            self.board_height_px = board_px_h

            return []

        elif self.game_mode == "2vs2":
            board_px_w = self.team_board_w * CELL_SIZE

            total_w = (board_px_w * 2) + (spacing * 3)
            total_h = info_top + board_px_h + info_bottom + spacing * 2

            self.layout_width, self.layout_height = total_w, total_h
            self.board_width_px, self.board_height_px = board_px_w, board_px_h

            self.team_positions = [
                (spacing, info_top + spacing),
                (spacing + board_px_w + spacing, info_top + spacing),
            ]

            return self.team_positions

        elif self.game_mode in ["lan", "global", "multiplayer"] or self.settings.get("network_mode") in ["lan", "online"]:
            board_px_w = WIDTH * CELL_SIZE
            board_px_h = HEIGHT * CELL_SIZE

            mini_w = 100
            info_w = 150
            side_gap = 20
            spacing = 20

            total_w = (mini_w + info_w) + side_gap + board_px_w + side_gap + (mini_w + info_w) + spacing
            total_h = board_px_h + 120

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

                positions.append(
                    (
                        spacing + col * (board_px_w + spacing),
                        info_top + spacing + row * (board_px_h + info_bottom + spacing),
                    )
                )

            return positions

    def run(self):
        Log.info(f"🎮 Игра запущена. Режим: {self.game_mode}, Игроков: {self.num_players}")

        if self.game_mode == "self_learning":
            return

        try:
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
                        if not getattr(player, "is_spectator", False):
                            player.update(dt, current_time)

                    self.check_game_over()

                if self.network_client:
                    self.remote_player_boards = dict(self.network_client.remote_players)

                    if not self.paused:
                        local_state = {}

                        for p in self.players:
                            key = f"{self.network_client.client_id}:{p.id}"

                            local_state[key] = {
                                "client_id": self.network_client.client_id,
                                "nickname": p.nickname,
                                "color": f"#{p.color.r:02x}{p.color.g:02x}{p.color.b:02x}",
                                "score": p.board.score,
                                "lines": p.board.lines_cleared_total,
                                "alive": p.alive,
                                "level": p.level,
                                "speed": min(10.0, p.speed + (p.level - 1) * 0.15),
                                "time": int(
                                    (p.game_over_time - self.start_time)
                                    if (not p.alive and p.game_over_time)
                                    else (time.time() - self.start_time)
                                ),
                                "grid": [
                                    [1 if cell is not None else 0 for cell in row]
                                    for row in p.board.grid
                                ],
                                "next_shape": p.next_pieces[0].shape_name if p.next_pieces else None,
                                "next_color": (
                                    f"#{p.next_pieces[0].color.r:02x}"
                                    f"{p.next_pieces[0].color.g:02x}"
                                    f"{p.next_pieces[0].color.b:02x}"
                                    if p.next_pieces else "#FFFFFF"
                                ),
                                "hold_shape": p.hold_piece.shape_name if p.hold_piece else None,
                                "hold_color": (
                                    f"#{p.hold_piece.color.r:02x}"
                                    f"{p.hold_piece.color.g:02x}"
                                    f"{p.hold_piece.color.b:02x}"
                                    if p.hold_piece else "#FFFFFF"
                                ),
                            }

                        self._own_state_keys = set(local_state.keys())
                        self.network_client.send_state(local_state)

                self.draw()

                if self.paused:
                    self.draw_pause_overlay()

                pygame.display.flip()

        finally:
            if self.network_client:
                self.network_client.stop()
                self.network_client = None

            pygame.display.quit()
            pygame.quit()

            Log.info("🛑 Игра остановлена, ресурсы освобождены.")

    def handle_keydown(self, key):
        for player in self.players:
            if player.is_bot or not player.alive:
                continue

            base_keymap = KEYMAP.get(player.id, {})
            custom_keymap = self.dynamic_keymap.get(player.id, {})
            active_keymap = {**base_keymap, **custom_keymap}

            for action, k in active_keymap.items():
                if isinstance(k, int):
                    match = key == k
                else:
                    event_name = pygame.key.name(key).lower().replace(" ", "")
                    map_name = str(k).lower().replace(" ", "")
                    match = event_name == map_name

                if match:
                    if action in ["rotate", "hard_drop", "hold"]:
                        player.handle_action(action)
                    else:
                        player.key_state[action] = True

                    if self.game_mode == "teacher_student" and player.id == 1:
                        student = next((p for p in self.players if p.id == 2), None)

                        if student and student.bot and isinstance(student.bot, StudentAI):
                            student.bot.log_teacher_action(action)

    def handle_keyup(self, key):
        for player in self.players:
            if player.is_bot:
                continue

            base_keymap = KEYMAP.get(player.id, {})
            custom_keymap = self.dynamic_keymap.get(player.id, {})
            active_keymap = {**base_keymap, **custom_keymap}

            for action, k in active_keymap.items():
                if isinstance(k, int):
                    match = key == k
                else:
                    event_name = pygame.key.name(key).lower().replace(" ", "")
                    map_name = str(k).lower().replace(" ", "")
                    match = event_name == map_name

                if match and action not in ["rotate", "hard_drop", "hold"]:
                    player.key_state[action] = False

    def check_game_over(self):
        if self.game_mode == "2vs2":
            t1_alive = any(p.alive for p in self.teams[0]["players"])
            t2_alive = any(p.alive for p in self.teams[1]["players"])

            if not t1_alive or not t2_alive:
                self.running = False

        elif self.game_mode == "teacher_student":
            teacher = next((p for p in self.players if p.id == 1), None)
            student = next((p for p in self.players if p.id == 2), None)

            if teacher and not teacher.alive:
                self.running = False

            if student and not student.alive:
                self.running = False

        else:
            if not any(p.alive for p in self.players):
                self.running = False

    def draw(self):
        self.screen.fill((30, 30, 30))

        if self.game_mode in ["lan", "global"] or self.settings.get("network_mode") in ["lan", "online"]:
            self.draw_network()
        elif self.game_mode == "coop":
            self.draw_coop()
        elif self.game_mode == "2vs2":
            self.draw_2vs2()
        else:
            self.draw_vs()

        if self.network_client and self.network_client.connection_status == "failed":
            overlay = pygame.Surface((self.layout_width, self.layout_height), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 160))
            self.screen.blit(overlay, (0, 0))

            err_text = self.big_font.render("ОШИБКА ПОДКЛЮЧЕНИЯ", True, (255, 50, 50))
            sub_text = self.font.render("Сервер недоступен. Игра в локальном режиме.", True, (255, 255, 255))

            self.screen.blit(
                err_text,
                err_text.get_rect(center=(self.layout_width // 2, self.layout_height // 2 - 20)),
            )

            self.screen.blit(
                sub_text,
                sub_text.get_rect(center=(self.layout_width // 2, self.layout_height // 2 + 20)),
            )

    def _parse_hex_color(self, color_str):
        if isinstance(color_str, str) and color_str.startswith("#") and len(color_str) == 7:
            try:
                return (
                    int(color_str[1:3], 16),
                    int(color_str[3:5], 16),
                    int(color_str[5:7], 16),
                )
            except ValueError:
                return (255, 255, 255)

        return (255, 255, 255)

    def _draw_remote_piece_preview(self, shape_name, color, x, y):
        if not shape_name or shape_name not in SHAPES:
            return

        shape = SHAPES[shape_name]
        cell_size = 6

        for r, row in enumerate(shape):
            for c, val in enumerate(row):
                if val:
                    pygame.draw.rect(
                        self.screen,
                        color,
                        (x + c * cell_size, y + r * cell_size, cell_size, cell_size),
                    )

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
                    rect = pygame.Rect(
                        x + int(col * mini_cell_x),
                        y + int(row * mini_cell_y),
                        max(1, int(mini_cell_x)),
                        max(1, int(mini_cell_y)),
                    )

                    pygame.draw.rect(self.screen, color, rect)

        if nickname:
            nick_surf = self.small_font.render(nickname[:10], True, player_color)
            self.screen.blit(nick_surf, (x, y - 12))

    def draw_network(self):
        if not self.players:
            return

        local_player = next((p for p in self.players if not p.is_bot), self.players[0])
        others = []

        for p in self.players:
            if p is not local_player:
                others.append(("local", p, None))

        prefix = f"{self.client_id}:" if getattr(self, "client_id", None) else None
        own_state_keys = getattr(self, "_own_state_keys", set())

        own_identity = set()

        for p in self.players:
            own_identity.add(
                (
                    p.nickname,
                    f"#{p.color.r:02x}{p.color.g:02x}{p.color.b:02x}",
                )
            )

        for pid, pdata in self.remote_player_boards.items():
            if not isinstance(pdata, dict):
                continue

            if prefix and isinstance(pid, str) and pid.startswith(prefix):
                continue

            if pid in own_state_keys:
                continue

            remote_client_id = pdata.get("client_id")

            if remote_client_id is not None:
                if remote_client_id == self.client_id:
                    continue
            else:
                identity = (pdata.get("nickname"), pdata.get("color"))

                if identity in own_identity:
                    continue

            others.append(("remote", pid, pdata))

        main_x = (self.layout_width - self.board_width_px) // 2
        main_y = max(20, (self.layout_height - self.board_height_px) // 2)

        self.draw_board(local_player.board, main_x, main_y, local_player)

        info_x = main_x
        info_y = main_y + self.board_height_px + 10

        self.draw_player_info(local_player, info_x, info_y)

        if not others:
            hint = self.font.render("Ожидание других игроков...", True, (200, 200, 200))
            self.screen.blit(hint, hint.get_rect(center=(self.layout_width // 2, 20)))
            return

        mini_w = 100
        info_w = 150
        side_gap = 20

        left_x = max(10, main_x - side_gap - mini_w - info_w)
        right_x = min(
            self.layout_width - mini_w - info_w - 10,
            main_x + self.board_width_px + side_gap,
        )

        count_left = min(8, len(others))
        count_right = min(8, max(0, len(others) - 8))
        max_per_side = max(count_left, count_right, 1)

        available_height = self.layout_height - main_y - 20
        mini_h = min(150, max(40, available_height // max_per_side))

        for i, item in enumerate(others):
            if i < 8:
                self._draw_other_player(item, left_x, main_y + i * mini_h, mini_h)
            elif i < 16:
                self._draw_other_player(item, right_x, main_y + (i - 8) * mini_h, mini_h)

    def _draw_other_player(self, item, x, y, mini_h):
        kind, payload, pdata = item

        if kind == "local":
            p = payload
            self.draw_minimized_board(p.board, x, y, p.color, p.nickname, mini_h)
        else:
            self._draw_remote_minimized_board(pdata, x, y, mini_h)

    def _draw_remote_minimized_board(self, pdata, x, y, mini_h=150):
        grid = pdata.get("grid") or []
        color_str = pdata.get("color", "#FFFFFF")
        nickname = pdata.get("nickname", "Remote")
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
                    rect = pygame.Rect(
                        x + int(col * mini_cell_x),
                        y + int(row * mini_cell_y),
                        max(1, int(mini_cell_x)),
                        max(1, int(mini_cell_y)),
                    )

                    pygame.draw.rect(self.screen, color, rect)

        text_x = x + w + 10
        text_y = y

        self.screen.blit(
            self.small_font.render(str(nickname)[:12], True, color),
            (text_x, text_y),
        )

        self.screen.blit(
            self.small_font.render(f"Score: {pdata.get('score', 0)}", True, (255, 255, 255)),
            (text_x, text_y + 20),
        )

        self.screen.blit(
            self.small_font.render(f"Lines: {pdata.get('lines', 0)}", True, (200, 200, 200)),
            (text_x, text_y + 38),
        )

        time_val = pdata.get("time", 0)

        self.screen.blit(
            self.small_font.render(f"Time: {time_val}s", True, (255, 255, 255)),
            (text_x, text_y + 56),
        )

        self.screen.blit(
            self.small_font.render("Next:", True, (255, 255, 255)),
            (text_x, text_y + 80),
        )

        if pdata.get("next_shape"):
            next_color = self._parse_hex_color(pdata.get("next_color", "#FFFFFF"))
            self._draw_remote_piece_preview(pdata["next_shape"], next_color, text_x + 45, text_y + 82)

        self.screen.blit(
            self.small_font.render("Hold:", True, (255, 255, 255)),
            (text_x, text_y + 105),
        )

        if pdata.get("hold_shape"):
            hold_color = self._parse_hex_color(pdata.get("hold_color", "#FFFFFF"))
            self._draw_remote_piece_preview(pdata["hold_shape"], hold_color, text_x + 45, text_y + 107)

    def draw_vs(self):
        for idx, player in enumerate(self.players):
            x, y = self.layout_positions[idx]

            nick_surf = self.font.render(player.nickname, True, player.color)

            self.screen.blit(
                nick_surf,
                (x + self.board_width_px // 2 - nick_surf.get_width() // 2, y - 30),
            )

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

            team_label = self.font.render(f"TEAM {i + 1}", True, (200, 200, 200))

            self.screen.blit(
                team_label,
                (x + self.board_width_px // 2 - team_label.get_width() // 2, y - 25),
            )

            board = self.teams[i]["board"]
            active = next((p for p in self.teams[i]["players"] if p.alive and p.current_piece), None)

            self.draw_board(board, x, y, active)

            info_y = y + self.board_height_px + 5

            for j, p in enumerate(self.teams[i]["players"]):
                self.draw_player_info(p, x + j * 150, info_y)

    def draw_board(self, board, x, y, active_player=None):
        pygame.draw.rect(
            self.screen,
            (100, 100, 100),
            (x - 2, y - 2, board.width * CELL_SIZE + 4, board.height * CELL_SIZE + 4),
            2,
        )

        for row in range(board.height):
            for col in range(board.width):
                color = board.grid[row][col]
                rect = pygame.Rect(x + col * CELL_SIZE, y + row * CELL_SIZE, CELL_SIZE, CELL_SIZE)

                if color:
                    pygame.draw.rect(self.screen, color, rect)

                pygame.draw.rect(self.screen, (60, 60, 60), rect, 1)

        if active_player and active_player.current_piece and active_player.alive:
            piece = active_player.current_piece
            ghost = copy.deepcopy(piece)

            board.drop_height(ghost)

            for cx, cy in ghost.get_cells():
                if 0 <= cy < board.height:
                    s = pygame.Surface((CELL_SIZE, CELL_SIZE), pygame.SRCALPHA)
                    s.fill((*piece.color[:3], 100))
                    self.screen.blit(s, (x + cx * CELL_SIZE, y + cy * CELL_SIZE))

            for cx, cy in piece.get_cells():
                if 0 <= cy < board.height:
                    pygame.draw.rect(
                        self.screen,
                        piece.color,
                        (x + cx * CELL_SIZE, y + cy * CELL_SIZE, CELL_SIZE, CELL_SIZE),
                    )

                    pygame.draw.rect(
                        self.screen,
                        (255, 255, 255),
                        (x + cx * CELL_SIZE, y + cy * CELL_SIZE, CELL_SIZE, CELL_SIZE),
                        1,
                    )

    def draw_player_info(self, player, x, y):
        self.screen.blit(self.small_font.render("Next:", True, (255, 255, 255)), (x, y))

        if player.next_pieces:
            self.draw_piece_preview(player.next_pieces[0], x, y + 15)

        self.screen.blit(self.small_font.render("Hold:", True, (255, 255, 255)), (x + 60, y))

        if player.hold_piece:
            self.draw_piece_preview(player.hold_piece, x + 60, y + 15)

        self.screen.blit(
            self.small_font.render(f"Score: {player.board.score}", True, (255, 255, 255)),
            (x, y + 50),
        )

        self.screen.blit(
            self.small_font.render(f"Lines: {player.board.lines_cleared_total}", True, (200, 200, 200)),
            (x, y + 68),
        )

        self.screen.blit(
            self.small_font.render(
                f"Lvl: {player.level} | Spd: {min(10.0, player.speed + (player.level - 1) * 0.15):.1f}",
                True,
                (200, 200, 200),
            ),
            (x, y + 86),
        )

        time_val = (
            (player.game_over_time - self.start_time)
            if (not player.alive and player.game_over_time)
            else (time.time() - self.start_time)
        )

        self.screen.blit(
            self.small_font.render(f"Time: {int(time_val)}s", True, (255, 255, 255)),
            (x, y + 104),
        )

    def draw_piece_preview(self, piece, x, y):
        for r, row in enumerate(piece.shape):
            for c, val in enumerate(row):
                if val:
                    pygame.draw.rect(
                        self.screen,
                        piece.color,
                        (x + c * CELL_SIZE, y + r * CELL_SIZE, CELL_SIZE, CELL_SIZE),
                    )

                    pygame.draw.rect(
                        self.screen,
                        (255, 255, 255),
                        (x + c * CELL_SIZE, y + r * CELL_SIZE, CELL_SIZE, CELL_SIZE),
                        1,
                    )

    def draw_pause_overlay(self):
        overlay = pygame.Surface((self.layout_width, self.layout_height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 180))
        self.screen.blit(overlay, (0, 0))

        pause_text = self.big_font.render("PAUSE", True, (255, 255, 255))
        text_rect = pause_text.get_rect(center=(self.layout_width // 2, self.layout_height // 2 - 60))
        self.screen.blit(pause_text, text_rect)

        btn_w, btn_h = 220, 50

        self.btn_resume = pygame.Rect(
            self.layout_width // 2 - btn_w // 2,
            self.layout_height // 2,
            btn_w,
            btn_h,
        )

        self.btn_quit = pygame.Rect(
            self.layout_width // 2 - btn_w // 2,
            self.layout_height // 2 + 70,
            btn_w,
            btn_h,
        )

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
    def __init__(
        self,
        host: str,
        port: int,
        room_name: str,
        is_host: bool,
        player_info: Dict[str, Any],
        client_id: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.room_name = room_name
        self.is_host = is_host
        self.player_info = player_info
        self.client_id = client_id or uuid.uuid4().hex[:8]

        self.room_id: Optional[str] = None
        self.is_spectator: bool = False
        self.remote_players: Dict[str, Any] = {}

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        self._writer: Optional[asyncio.StreamWriter] = None
        self._reader: Optional[asyncio.StreamReader] = None

        self.connection_status = "connecting"
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
            self.connection_status = "connected"

            if self.is_host:
                msg = {
                    "action": "create_room",
                    "room_id": self.room_name,
                    "game_mode": "coop",
                    "player_info": self.player_info,
                }
            else:
                msg = {
                    "action": "join_room",
                    "room_id": self.room_name,
                    "player_info": self.player_info,
                }

            self._writer.write((json.dumps(msg) + "\n").encode("utf-8"))
            await self._writer.drain()

            if not self.room_id:
                self.room_id = self.room_name

            while self._running and self._reader and not self._reader.at_eof():
                line = await self._reader.readline()

                if not line:
                    break

                msg = json.loads(line.decode("utf-8").strip())
                self._process_message(msg)

        except Exception as e:
            self.connection_status = "failed"
            self.connection_error = str(e)
            Log.error(f"🌐 Ошибка сети: {e}")

        finally:
            self._running = False

            if self._writer:
                self._writer.close()

    def _process_message(self, msg: Dict[str, Any]) -> None:
        action = msg.get("action")

        if action in ("room_created", "room_joined"):
            self.room_id = msg.get("room_id", self.room_name)
            self.is_spectator = msg.get("is_spectator", False)

            for pid, pinfo in msg.get("players", {}).items():
                if self._is_own_entry(pid, pinfo):
                    continue

                if pid not in self.remote_players:
                    self.remote_players[pid] = pinfo

        elif action == "state_update":
            incoming = msg.get("state", {})

            incoming = {
                pid: info
                for pid, info in incoming.items()
                if not self._is_own_entry(pid, info)
            }

            self.remote_players.update(incoming)

            for pid in list(self.remote_players.keys()):
                if self._is_own_entry(pid, self.remote_players[pid]):
                    self.remote_players.pop(pid, None)

        elif action in ("player_joined", "player_left"):
            players = msg.get("players", {})

            if action == "player_joined":
                for pid, pinfo in players.items():
                    if self._is_own_entry(pid, pinfo):
                        continue

                    if pid not in self.remote_players:
                        self.remote_players[pid] = pinfo
            else:
                players = {
                    pid: info
                    for pid, info in players.items()
                    if not self._is_own_entry(pid, info)
                }

                self.remote_players = {
                    k: v
                    for k, v in self.remote_players.items()
                    if k in players
                }

        elif action == "error":
            self.connection_status = "failed"
            self.connection_error = msg.get("message", "Unknown error")
            Log.error(f"🌐 Ошибка сервера: {self.connection_error}")

    def _is_own_entry(self, pid: str, pinfo: Dict[str, Any]) -> bool:
        if isinstance(pid, str) and pid.startswith(f"{self.client_id}:"):
            return True

        if isinstance(pinfo, dict) and pinfo.get("client_id") == self.client_id:
            return True

        return False

    def send_state(self, state: Dict[str, Any]) -> None:
        if self._loop and self._running and self.room_id and not self.is_spectator:
            msg = {
                "action": "update_state",
                "room_id": self.room_id,
                "state": state,
            }

            asyncio.run_coroutine_threadsafe(self._send_msg(msg), self._loop)

    async def _send_msg(self, msg: Dict[str, Any]) -> None:
        if self._writer:
            try:
                self._writer.write((json.dumps(msg) + "\n").encode("utf-8"))
                await self._writer.drain()
            except Exception:
                pass

    def stop(self) -> None:
        if self._thread is None:
            return

        self._running = False

        if self._loop and self._writer:
            try:
                asyncio.run_coroutine_threadsafe(self._shutdown_writer(), self._loop)
            except RuntimeError:
                pass

        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

        if self._thread.is_alive() and self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=1.0)

        if self._thread.is_alive():
            Log.warning("⚠️ Network-поток не завершился за 3с")

        self._thread = None
        self._writer = None

    async def _shutdown_writer(self):
        try:
            if self._writer:
                self._writer.close()
                await self._writer.wait_closed()
        except Exception:
            pass
