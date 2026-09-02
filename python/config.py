# config.py
import pygame

# Размеры поля
WIDTH = 15
HEIGHT = 30
CELL_SIZE = 10

# Стандартные цвета фигур (используются для превью, реальный цвет берётся из настроек игрока)
PIECE_COLORS = {
    'I': (0, 255, 255),
    'O': (255, 255, 0),
    'T': (128, 0, 128),
    'S': (0, 255, 0),
    'Z': (255, 0, 0),
    'L': (255, 165, 0),
    'J': (0, 0, 255)
}

# Формы фигур
SHAPES = {
    'I': [[1,1,1,1]],
    'O': [[1,1],[1,1]],
    'T': [[0,1,0],[1,1,1]],
    'S': [[0,1,1],[1,1,0]],
    'Z': [[1,1,0],[0,1,1]],
    'L': [[1,0,0],[1,1,1]],
    'J': [[0,0,1],[1,1,1]]
}

# Назначение клавиш для четырёх игроков (локальная игра)
KEYMAP = {
    1: {
        'hard_drop': pygame.K_q,
        'rotate': pygame.K_w,
        'left': pygame.K_a,
        'right': pygame.K_d,
        'soft_drop': pygame.K_s,
        'hold': pygame.K_e
    },
    2: {
        'hard_drop': pygame.K_r,
        'rotate': pygame.K_t,
        'left': pygame.K_f,
        'right': pygame.K_h,
        'soft_drop': pygame.K_g,
        'hold': pygame.K_y
    },
    3: {
        'hard_drop': pygame.K_u,
        'rotate': pygame.K_i,
        'left': pygame.K_j,
        'right': pygame.K_l,
        'soft_drop': pygame.K_k,
        'hold': pygame.K_o
    },
    4: {
        'hard_drop': pygame.K_RSHIFT,
        'rotate': pygame.K_UP,
        'left': pygame.K_LEFT,
        'right': pygame.K_RIGHT,
        'soft_drop': pygame.K_DOWN,
        'hold': pygame.K_RCTRL
    }
}

# Настройки задержки и повтора при удержании клавиши
DAS_DELAY = 200      # мс
DAS_REPEAT = 25      # мс

# Очки за линии
LINE_SCORES = {1: 100, 2: 250, 3: 500, 4: 1000}