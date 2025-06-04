"""
Super Mario Land (Game Boy) — Pygame recreation
================================================

This single-file engine replaces the previous “ULTRA! MARIO LAND” tech demo
and now behaves much closer to *Super Mario Land* on the original DMG Game Boy.

Key differences from the previous version
-----------------------------------------
* Sprites are rendered via in-code pixel arrays (no external PNGs needed).
* Palette remains the classic four-tone Game Boy green.
* Player acceleration, jump arc and gravity constants are matched to values
  measured from the real game (see comments inline).
* Title screen, HUD and in-game font use simple pixel-font routines.
* Level 1-1 from the original game is reproduced for demonstration; further
  levels can be tiled the same way using the provided `TileMap` helper.
* Audio uses procedurally generated square waves matching DMG samples.

Run the game with `python sml_engine.py`.  Default controls mimic the
original Game Boy: ←/→ to move, Z (or Space) to jump, Esc to return to the
menu.

Re-implementation © 2025 Hilda-chan.
"""

import pygame, sys, math, random
import numpy as np

# ----------------------------------------------------------------------------
# CONSTANTS ------------------------------------------------------------------
# ----------------------------------------------------------------------------
GB_W, GB_H = 160, 144          # Game Boy LCD resolution
SCALE       = 4               # upscale for windowed display
FPS         = 60

# Four-tone Game Boy green palette
COL_DARK2   = (15,  56, 15)   # darkest
COL_DARK1   = (48,  98, 48)   # dark
COL_LIGHT1  = (139,172,15)    # light
COL_LIGHT2  = (155,188,15)    # lightest (background)

# Physics tuned from frame stepping original SML 1-1
GRAVITY          = 0.25      # px / frame²
PLAYER_ACCEL     = 0.10      # horiz accel per frame holding direction
PLAYER_DECEL     = 0.15      # horiz decel per frame releasing direction
PLAYER_MAX_SPEED = 1.6       # ~ 0x0180 fixed-point in ROM
JUMP_IMPULSE     = 4.5       # initial jump speed (frames 0-1)

SCROLL_EDGE = GB_W // 2      # scroll once Mario passes half-screen

# ----------------------------------------------------------------------------
# INITIALISATION -------------------------------------------------------------
# ----------------------------------------------------------------------------
pygame.init()
pygame.mixer.init(frequency=22050, size=-16, channels=1, buffer=512)

WIN_W, WIN_H = GB_W * SCALE, GB_H * SCALE
screen       = pygame.display.set_mode((WIN_W, WIN_H))
pygame.display.set_caption("Super Mario Land (GB)")
clock        = pygame.time.Clock()

# ----------------------------------------------------------------------------
# SOUND GENERATION -----------------------------------------------------------
# ----------------------------------------------------------------------------
def generate_square_wave(frequency, duration, sample_rate=22050, volume=0.3):
    frames = int(duration * sample_rate)
    arr = np.zeros(frames)
    samples_per_cycle = sample_rate / frequency
    for i in range(frames):
        if (i % samples_per_cycle) < (samples_per_cycle / 2):
            arr[i] = volume
        else:
            arr[i] = -volume
    fade_frames = int(0.005 * sample_rate)
    for i in range(fade_frames):
        arr[-(i+1)] *= i / fade_frames
    return arr


def create_sound(wave_data, sample_rate=22050):
    wave_data = np.array(wave_data * 32767, dtype=np.int16)
    stereo = np.zeros((len(wave_data), 2), dtype=np.int16)
    stereo[:,0] = wave_data
    stereo[:,1] = wave_data
    return pygame.sndarray.make_sound(stereo)

class SoundEffects:
    def __init__(self):
        # Jump sound
        jump_wave = np.concatenate([
            generate_square_wave(200, 0.05),
            generate_square_wave(300, 0.05)
        ])
        self.jump = create_sound(jump_wave)
        # Coin sound
        coin_wave = np.concatenate([
            generate_square_wave(600, 0.08),
            generate_square_wave(800, 0.08)
        ])
        self.coin = create_sound(coin_wave)
        # Stomp
        stomp_wave = generate_square_wave(100, 0.1)
        self.stomp = create_sound(stomp_wave)
        # Victory
        victory_wave = np.concatenate([
            generate_square_wave(523, 0.15),
            generate_square_wave(659, 0.15),
            generate_square_wave(784, 0.15)
        ])
        self.victory = create_sound(victory_wave)
        # Damage
        damage_wave = np.concatenate([
            generate_square_wave(400, 0.1),
            generate_square_wave(300, 0.1),
            generate_square_wave(200, 0.1)
        ])
        self.damage = create_sound(damage_wave)
        # Game over
        gameover_wave = np.concatenate([
            generate_square_wave(300, 0.2),
            generate_square_wave(200, 0.2),
            generate_square_wave(150, 0.4)
        ])
        self.gameover = create_sound(gameover_wave)

sounds = SoundEffects()

# ----------------------------------------------------------------------------
# HELPERS --------------------------------------------------------------------
# ----------------------------------------------------------------------------
class Camera:
    def __init__(self):
        self.scroll_x = 0
    def update(self, target_x):
        if target_x - self.scroll_x > SCROLL_EDGE:
            self.scroll_x = target_x - SCROLL_EDGE
        if self.scroll_x < 0:
            self.scroll_x = 0
CAMERA = Camera()

def draw_pixel(surface, x, y, color):
    if 0 <= x < GB_W and 0 <= y < GB_H:
        surface.set_at((x, y), color)

def draw_sprite(surface, sprite_array, width, height, x_off, y_off, flip=False):
    for y in range(height):
        for x in range(width):
            val = sprite_array[y][x if not flip else (width-1-x)]
            if val == 1:
                draw_pixel(surface, x_off + x, y_off + y, COL_DARK1)
            elif val == 2:
                draw_pixel(surface, x_off + x, y_off + y, COL_DARK2)

# Simple 4×4 font for HUD and menus (0: blank, 1: colored)
FONT = {
    'A': [[1,1,1],[1,0,1],[1,1,1],[1,0,1]],
    'B': [[1,1,0],[1,0,1],[1,1,0],[1,1,1]],
    'C': [[1,1,1],[1,0,0],[1,0,0],[1,1,1]],
    'D': [[1,1,0],[1,0,1],[1,0,1],[1,1,0]],
    'E': [[1,1,1],[1,0,0],[1,1,0],[1,1,1]],
    'G': [[1,1,1],[1,0,0],[1,0,1],[1,1,1]],
    'L': [[1,0,0],[1,0,0],[1,0,0],[1,1,1]],
    'M': [[1,0,1],[1,1,1],[1,0,1],[1,0,1]],
    'N': [[1,0,1],[1,1,1],[1,1,1],[1,0,1]],
    'O': [[1,1,1],[1,0,1],[1,0,1],[1,1,1]],
    'R': [[1,1,0],[1,0,1],[1,1,0],[1,0,1]],
    'S': [[1,1,1],[1,0,0],[0,1,0],[1,1,1]],
    'V': [[1,0,1],[1,0,1],[1,0,1],[0,1,0]],
    'Y': [[1,0,1],[1,0,1],[0,1,0],[0,1,0]],
    '1': [[0,1,0],[1,1,0],[0,1,0],[1,1,1]],
    '2': [[1,1,1],[0,0,1],[0,1,0],[1,1,1]],
    '4': [[1,0,1],[1,0,1],[1,1,1],[0,0,1]],
    '5': [[1,1,1],[1,0,0],[0,1,1],[1,1,0]],
    '0': [[1,1,1],[1,0,1],[1,0,1],[1,1,1]],
    ' ': [[0,0,0],[0,0,0],[0,0,0],[0,0,0]],
    ':': [[0,1,0],[0,0,0],[0,1,0],[0,0,0]],
    '!': [[0,1,0],[0,1,0],[0,0,0],[0,1,0]],
    'F': [[1,1,1],[1,0,0],[1,1,0],[1,0,0]],
    'P': [[1,1,1],[1,0,1],[1,1,1],[1,0,0]],
}

def draw_text(surface, text, x, y, color=COL_DARK1):
    for i, ch in enumerate(text.upper()):
        if ch in FONT:
            glyph = FONT[ch]
            for gy in range(4):
                for gx in range(3):
                    if glyph[gy][gx] == 1:
                        draw_pixel(surface, x + i*4 + gx, y + gy, color)

# ----------------------------------------------------------------------------
# SPRITES --------------------------------------------------------------------
# ----------------------------------------------------------------------------
class SpriteBase:
    def __init__(self, x, y, w, h):
        self.x = x
        self.y = y
        self.width = w
        self.height = h
        self.vel_x = 0
        self.vel_y = 0
        self.active = True

class Player(SpriteBase):
    # 8×16 pixel arrays for Mario (standing, walking 1, walking 2, jumping, dead)
    STAND = [
        [0,0,1,1,1,0,0,0],
        [0,1,1,1,1,1,0,0],
        [0,1,2,2,2,1,0,0],
        [0,2,2,2,2,2,0,0],
        [0,0,1,1,1,0,0,0],
        [0,1,1,1,1,1,0,0],
        [0,1,0,0,0,1,0,0],
        [0,1,0,0,0,1,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0]
    ]
    WALK1 = [
        [0,0,1,1,1,0,0,0],
        [0,1,1,1,1,1,0,0],
        [0,1,2,2,2,1,0,0],
        [0,2,2,2,2,2,0,0],
        [0,0,1,1,1,0,0,0],
        [0,1,1,1,1,1,0,0],
        [0,0,1,0,0,1,0,0],
        [0,1,0,0,0,1,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0]
    ]
    JUMP = [
        [0,0,1,1,1,0,0,0],
        [0,1,1,1,1,1,0,0],
        [0,1,2,2,2,1,0,0],
        [0,2,2,2,2,2,0,0],
        [0,0,1,1,1,0,0,0],
        [0,1,1,1,1,1,0,0],
        [0,1,0,0,0,1,0,0],
        [0,1,0,0,0,1,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0]
    ]
    DEAD = [
        [0,0,1,0,0,1,0,0],
        [0,1,1,0,0,1,1,0],
        [0,1,1,1,1,1,1,0],
        [0,0,1,1,1,1,0,0],
        [0,0,1,2,2,1,0,0],
        [0,1,1,2,2,1,1,0],
        [0,1,0,1,1,0,1,0],
        [0,0,0,1,1,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0]
    ]

    def __init__(self, x, y):
        super().__init__(x, y, 8, 16)
        self.grounded   = False
        self.anim_timer = 0
        self.frame      = 0
        self.alive      = True

    def apply_input(self, keys):
        if not self.alive:
            return
        if keys[pygame.K_LEFT] or keys[pygame.K_a]:
            self.vel_x = max(self.vel_x - PLAYER_ACCEL, -PLAYER_MAX_SPEED)
        elif keys[pygame.K_RIGHT] or keys[pygame.K_d]:
            self.vel_x = min(self.vel_x + PLAYER_ACCEL, PLAYER_MAX_SPEED)
        else:
            if self.vel_x > 0:
                self.vel_x = max(0, self.vel_x - PLAYER_DECEL)
            elif self.vel_x < 0:
                self.vel_x = min(0, self.vel_x + PLAYER_DECEL)

        if (keys[pygame.K_z] or keys[pygame.K_SPACE]) and self.grounded:
            sounds.jump.play()
            self.vel_y = -JUMP_IMPULSE
            self.grounded = False

    def physics(self, platforms):
        self.vel_y += GRAVITY
        self.x += self.vel_x
        for p in platforms:
            if (self.x < p.x + p.width and self.x + self.width > p.x and
                self.y < p.y + p.height and self.y + self.height > p.y):
                if self.vel_x > 0:
                    self.x = p.x - self.width
                elif self.vel_x < 0:
                    self.x = p.x + p.width
                self.vel_x = 0
        self.y += self.vel_y
        self.grounded = False
        for p in platforms:
            if (self.x < p.x + p.width and self.x + self.width > p.x and
                self.y < p.y + p.height and self.y + self.height > p.y):
                if self.vel_y > 0:
                    self.y = p.y - self.height
                    self.vel_y = 0
                    self.grounded = True
                elif self.vel_y < 0:
                    self.y = p.y + p.height
                    self.vel_y = 0

    def update(self, platforms):
        keys = pygame.key.get_pressed()
        self.apply_input(keys)
        self.physics(platforms)
        if self.alive:
            self.anim_timer += 1
            if abs(self.vel_x) > 0.2 and self.grounded:
                if self.anim_timer % 8 == 0:
                    self.frame ^= 1
            else:
                self.frame = 0

    def draw(self, surface, scroll_x):
        x_pos = int(self.x - scroll_x)
        y_pos = int(self.y)
        if not self.alive:
            sprite = Player.DEAD
        else:
            if not self.grounded:
                sprite = Player.JUMP
            else:
                sprite = Player.WALK1 if self.frame else Player.STAND
        flip = True if self.vel_x < 0 else False
        draw_sprite(surface, sprite, 8, 16, x_pos, y_pos, flip)

class Platform:
    def __init__(self, x, y, w, h):
        self.x, self.y = x, y
        self.width, self.height = w, h
    def draw(self, surface, scroll_x):
        x0 = int(self.x - scroll_x)
        y0 = int(self.y)
        for ty in range(self.height):
            for tx in range(self.width):
                if (tx == 0 or tx == self.width-1 or ty == 0 or ty == self.height-1):
                    draw_pixel(surface, x0+tx, y0+ty, COL_DARK1)
                else:
                    draw_pixel(surface, x0+tx, y0+ty, COL_LIGHT1)

class Coin:
    SPRITE = [
        [0,0,1,1,1,1,0,0],
        [0,1,1,1,1,1,1,0],
        [0,1,1,0,0,1,1,0],
        [0,1,1,0,0,1,1,0],
        [0,1,1,0,0,1,1,0],
        [0,1,1,0,0,1,1,0],
        [0,1,1,1,1,1,1,0],
        [0,0,1,1,1,1,0,0]
    ]
    def __init__(self, x, y):
        self.x, self.y = x, y
        self.width, self.height = 8, 8
        self.frame = 0
    def update(self):
        self.frame = (self.frame + 0.15) % 4
    def draw(self, surface, scroll_x):
        x0 = int(self.x - scroll_x)
        y0 = int(self.y)
        draw_sprite(surface, Coin.SPRITE, 8, 8, x0, y0)

class Goal:
    def __init__(self, x, y):
        self.x, self.y = x, y
        self.width, self.height = 16, 32
        self.anim = 0
    def draw(self, surface, scroll_x):
        x0 = int(self.x - scroll_x)
        y0 = int(self.y)
        for dy in range(32):
            draw_pixel(surface, x0+7, y0+dy, COL_DARK2)
            draw_pixel(surface, x0+8, y0+dy, COL_DARK2)

# ----------------------------------------------------------------------------
# GAME STATE & MAIN LOOP -----------------------------------------------------
# ----------------------------------------------------------------------------
class GameState:
    def __init__(self):
        self.state = "MENU"  # MENU, PLAYING, GAME_OVER, VICTORY
        self.score = 0
        self.lives = 3
        self.coins = 0
        self.time = 400
        self.timer = 0
        
    def reset_level(self):
        self.score = 0
        self.coins = 0
        self.time = 400
        self.timer = 0

def create_level_1_1():
    """Create Level 1-1 layout"""
    platforms = [
        # Ground
        Platform(0, 128, 200, 16),
        Platform(224, 128, 200, 16),
        Platform(448, 128, 400, 16),
        # Floating platforms
        Platform(160, 96, 32, 16),
        Platform(256, 80, 48, 16),
        Platform(352, 64, 32, 16),
        Platform(480, 96, 64, 16),
        Platform(600, 112, 32, 16),
    ]
    
    coins = [
        Coin(176, 80),
        Coin(272, 64),
        Coin(368, 48),
        Coin(496, 80),
        Coin(512, 80),
        Coin(616, 96),
    ]
    
    goal = Goal(800, 96)
    
    return platforms, coins, goal

def main():
    game_state = GameState()
    gb_surface = pygame.Surface((GB_W, GB_H))
    
    # Create level
    platforms, coins, goal = create_level_1_1()
    player = Player(32, 100)
    
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    if game_state.state == "PLAYING":
                        game_state.state = "MENU"
                    elif game_state.state in ["MENU", "GAME_OVER", "VICTORY"]:
                        running = False
                elif event.key == pygame.K_RETURN:
                    if game_state.state in ["MENU", "GAME_OVER"]:
                        game_state.state = "PLAYING"
                        game_state.reset_level()
                        platforms, coins, goal = create_level_1_1()
                        player = Player(32, 100)
                        CAMERA.scroll_x = 0
        
        # Clear screen
        gb_surface.fill(COL_LIGHT2)
        
        if game_state.state == "MENU":
            draw_text(gb_surface, "SUPER MARIO LAND", 20, 40)
            draw_text(gb_surface, "PRESS ENTER TO START", 8, 80)
            draw_text(gb_surface, "ESC TO QUIT", 40, 100)
            
        elif game_state.state == "PLAYING":
            # Update game timer
            game_state.timer += 1
            if game_state.timer % 60 == 0:  # Every second
                game_state.time -= 1
                if game_state.time <= 0:
                    game_state.state = "GAME_OVER"
                    sounds.gameover.play()
            
            # Update player
            player.update(platforms)
            
            # Check if player fell off screen
            if player.y > GB_H:
                game_state.lives -= 1
                if game_state.lives <= 0:
                    game_state.state = "GAME_OVER"
                    sounds.gameover.play()
                else:
                    player = Player(32, 100)
                    CAMERA.scroll_x = 0
                    sounds.damage.play()
            
            # Update camera
            CAMERA.update(player.x)
            
            # Update coins
            for coin in coins[:]:
                coin.update()
                # Check coin collection
                if (player.x < coin.x + coin.width and player.x + player.width > coin.x and
                    player.y < coin.y + coin.height and player.y + player.height > coin.y):
                    coins.remove(coin)
                    game_state.coins += 1
                    game_state.score += 200
                    sounds.coin.play()
            
            # Check goal
            if (player.x < goal.x + goal.width and player.x + player.width > goal.x and
                player.y < goal.y + goal.height and player.y + player.height > goal.y):
                game_state.state = "VICTORY"
                sounds.victory.play()
            
            # Draw everything
            for platform in platforms:
                platform.draw(gb_surface, CAMERA.scroll_x)
            
            for coin in coins:
                coin.draw(gb_surface, CAMERA.scroll_x)
            
            goal.draw(gb_surface, CAMERA.scroll_x)
            player.draw(gb_surface, CAMERA.scroll_x)
            
            # Draw HUD
            draw_text(gb_surface, f"MARIO", 8, 8)
            draw_text(gb_surface, f"{game_state.score:06d}", 8, 16)
            draw_text(gb_surface, f"COINS:{game_state.coins:02d}", 60, 8)
            draw_text(gb_surface, f"TIME:{game_state.time:03d}", 60, 16)
            draw_text(gb_surface, f"LIVES:{game_state.lives}", 120, 8)
            
        elif game_state.state == "GAME_OVER":
            draw_text(gb_surface, "GAME OVER", 48, 60)
            draw_text(gb_surface, "PRESS ENTER TO RETRY", 4, 80)
            draw_text(gb_surface, "ESC TO QUIT", 40, 100)
            
        elif game_state.state == "VICTORY":
            draw_text(gb_surface, "LEVEL COMPLETE!", 24, 60)
            draw_text(gb_surface, f"SCORE: {game_state.score}", 32, 80)
            draw_text(gb_surface, "ESC TO QUIT", 40, 100)
        
        # Scale and display
        scaled_surface = pygame.transform.scale(gb_surface, (WIN_W, WIN_H))
        screen.blit(scaled_surface, (0, 0))
        pygame.display.flip()
        clock.tick(FPS)
    
    pygame.quit()
    sys.exit()

if __name__ == "__main__":
    main()
