import pygame
import sys
import time
from config.settings import *
from core.game_config import GameConfig
from core.camera import Camera
from entities.player import Player
from entities.enemy import Enemy
from entities.npc import NPC
from attacks.projectile import Projectile
from attacks.beam import BeamAttack
from attacks.melee import MeleeAttack
from ui.hud import UI
from ui.dialogue import DialogueBox
from ui.notifications import LevelUpNotification
from rooms.room_manager import RoomManager
from rooms.room import Room
from dev_tools.spawn_menu import SpawnMenu
from dev_tools.dev_menu import DevMenu
from dev_tools.npc_config import NPCConfigMenu
from dev_tools.room_editor import RoomEditorMenu
from objects.room_transition import RoomTransition
from core.transition_controller import TransitionController
from dev_tools.transition_config import TransitionConfigMenu

class Game:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
        pygame.display.set_caption("Legacy of Goku Style Engine")
        self.clock = pygame.time.Clock()
        self.running = True
        self.colors = get_colors()

        # Game configuration
        self.game_config = GameConfig()
        
        # Player and camera
        self.player = Player(WORLD_WIDTH // 2, WORLD_HEIGHT // 2)
        self.player.update_derived_stats()
        self.camera = Camera(SCREEN_WIDTH, SCREEN_HEIGHT)
        
        # UI components
        self.ui = UI(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.dialogue_box = DialogueBox(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.level_up_notification = LevelUpNotification(SCREEN_WIDTH, SCREEN_HEIGHT)
        
        # Room system
        self.room_manager = RoomManager()
        self.current_room = None
        
        # Dev tools
        self.spawn_menu = SpawnMenu(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.dev_menu = DevMenu(self.game_config, SCREEN_WIDTH, SCREEN_HEIGHT)
        self.npc_config_menu = NPCConfigMenu(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.room_editor_menu = RoomEditorMenu(self.room_manager, SCREEN_WIDTH, SCREEN_HEIGHT)
        
        # Game objects
        self.projectiles = []
        self.melee_attacks = []
        self.enemies = []
        self.npcs = []
        
        # Game state
        self.pending_npc_position = None
        self.nearby_npc = None
        self.last_time = time.time()
        
        # Create default room
        self._create_default_room()

        # room transition 
        self.room_transitions = []
        self.transition_controller = TransitionController(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.transition_config_menu = TransitionConfigMenu(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.pending_transition_position = None
    
    def _create_default_room(self):
        """Create and set the default room"""
        room = self.room_manager.create_room("Default Room", WORLD_WIDTH, WORLD_HEIGHT, "Default")
        self.room_manager.current_room = room
        self.current_room = room
    
    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            
            # Handle NPC config menu input
            if self.npc_config_menu.active:
                result = self.npc_config_menu.handle_input(event)
                if result and result != 'cancel' and self.pending_npc_position:
                    # Create NPC with configuration
                    x, y = self.pending_npc_position
                    npc = NPC(x, y, result)
                    npc.npc_type = result['npc_type']
                    self.npcs.append(npc)
                    self.pending_npc_position = None
                elif result == 'cancel':
                    self.pending_npc_position = None
                continue
                    
            # Handle Dev Menu input
            if self.dev_menu.active:
                result = self.dev_menu.handle_input(event)
                if result == 'open_spawn_menu':
                    self.dev_menu.active = False
                    self.spawn_menu.toggle()
                elif result == 'open_room_editor':
                    self.dev_menu.active = False
                    self.room_editor_menu.toggle()
                continue

            # Handle Room Editor Menu input
            if self.room_editor_menu.active:
                result = self.room_editor_menu.handle_input(event)
                if result and isinstance(result, dict):
                    if result['action'] == 'enter_room':
                        # Enter/view the room
                        room = result['room']
                        self.room_manager.current_room = room
                        self.current_room = room
                        # Update world size to room size
                        self.player.x, self.player.y = room.spawn_point
                        self.room_editor_menu.active = False
                    elif result['action'] == 'room_created':
                        # Room was created successfully
                        pass
                continue
            
            elif event.type == pygame.KEYDOWN:
                if self.ui.current_screen == 'game':
                    if event.key == pygame.K_F1:
                        self.dev_menu.toggle()
                    
                    # Spawn menu controls
                    if self.spawn_menu.active:
                        if event.key == pygame.K_a:
                            self.spawn_menu.navigate_category(-1)
                        elif event.key == pygame.K_d:
                            self.spawn_menu.navigate_category(1)
                        elif event.key == pygame.K_w:
                            self.spawn_menu.navigate_item(-1)
                        elif event.key == pygame.K_s:
                            self.spawn_menu.navigate_item(1)
                
                    # Game controls (only when spawn menu is closed)
                    elif not self.spawn_menu.active:
                        if event.key == pygame.K_ESCAPE:
                            self.ui.current_screen = 'main_menu'
                            self.ui.selected_menu_item = 0
                        elif event.key == pygame.K_q:
                            if self.player.ki_attack_mode == 'blast':
                                projectile = self.player.shoot_blast()
                                if projectile:
                                    self.projectiles.append(projectile)
                            elif self.player.ki_attack_mode == 'beam':
                                self.player.start_charging_beam()
                        elif event.key == pygame.K_e:
                            # Check if near NPC first
                            if self.nearby_npc and not self.dialogue_box.active:
                                # Start dialogue
                                text, is_final, item = self.nearby_npc.start_dialogue()
                                if text:
                                    self.dialogue_box.show(text, "NPC", is_final, item)
                                    if item:
                                        self.player.inventory.append(item)
                            elif self.dialogue_box.active:
                                # Continue or close dialogue
                                if self.dialogue_box.is_final:
                                    self.dialogue_box.hide()
                                    if self.nearby_npc:
                                        self.nearby_npc.end_dialogue()
                                else:
                                    # Get next dialogue
                                    text, is_final, item = self.nearby_npc.start_dialogue()
                                    if text:
                                        self.dialogue_box.show(text, "NPC", is_final, item)
                                        if item:
                                            self.player.inventory.append(item)
                            else:
                                # Normal melee attack
                                melee = self.player.melee_attack()
                                if melee:
                                    self.melee_attacks.append(melee)
                        elif event.key == pygame.K_TAB:
                            # Switch ki attack mode
                            if self.player.ki_attack_mode == 'blast':
                                self.player.ki_attack_mode = 'beam'
                            else:
                                self.player.ki_attack_mode = 'blast'
                        elif event.key in [pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN]:
                            if self.player.check_double_tap(event.key):
                                self.player.is_running = True
                
                elif self.ui.current_screen == 'main_menu':
                    if event.key == pygame.K_UP:
                        self.ui.selected_menu_item = (self.ui.selected_menu_item - 1) % len(self.ui.menu_items)
                    elif event.key == pygame.K_DOWN:
                        self.ui.selected_menu_item = (self.ui.selected_menu_item + 1) % len(self.ui.menu_items)
                    elif event.key == pygame.K_RETURN:
                        selected = self.ui.menu_items[self.ui.selected_menu_item]
                        if selected == 'Continue':
                            self.ui.current_screen = 'game'
                        elif selected == 'Status':
                            self.ui.current_screen = 'status'
                        elif selected == 'Inventory':
                            self.ui.current_screen = 'inventory'
                        elif selected == 'Options':
                            self.ui.current_screen = 'options'
                        elif selected == 'Quit':
                            self.running = False
                elif event.key == pygame.K_ESCAPE:
                    self.ui.current_screen = 'game'
            
                elif self.ui.current_screen in ['status', 'inventory', 'options']:
                    if event.key == pygame.K_ESCAPE:
                        self.ui.current_screen = 'main_menu'
        
            elif event.type == pygame.KEYUP:
                if self.ui.current_screen == 'game':
                    if event.key == pygame.K_q and self.player.ki_attack_mode == 'beam':
                        # Release beam
                        if self.player.is_charging_beam:
                            beam = self.player.fire_beam()
                            if beam:
                                self.player.current_beam = beam
        
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if self.spawn_menu.active and event.button == 1:  # Left click
                    # Convert screen coordinates to world coordinates
                    mouse_x, mouse_y = event.pos
                    world_x = mouse_x + self.camera.x
                    world_y = mouse_y + self.camera.y
            
                    # Get selected spawn type
                    category, item = self.spawn_menu.get_selected_spawn()
            
                    # Spawn based on category and item
                    if world_x > 0 and world_x < self.current_room.width and world_y > 0 and world_y < self.current_room.height:
                        if category == 'Enemies':
                            self.enemies.append(Enemy(world_x, world_y))
                        elif category == 'Objects':
                            # TODO: Add object spawning
                            pass
                        elif category == 'NPCs':
                            # Open NPC configuration menu
                            self.pending_npc_position = (world_x, world_y)
                            self.npc_config_menu.toggle()
                            self.spawn_menu.toggle()  # Close spawn menu
    
    def update(self):
        current_time = time.time()
        dt = current_time - self.last_time
        self.last_time = current_time
        
        if self.ui.current_screen == 'game':
            keys = pygame.key.get_pressed()
            dx = dy = 0
            
            # Check for Shift + direction for running
            is_running = keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT] or self.player.is_running
            
            if keys[pygame.K_LEFT]:
                dx = -1
            if keys[pygame.K_RIGHT]:
                dx = 1
            if keys[pygame.K_UP]:
                dy = -1
            if keys[pygame.K_DOWN]:
                dy = 1
            
            # Stop running if no direction key is pressed
            if dx == 0 and dy == 0:
                self.player.is_running = False
            
            if dx != 0 or dy != 0:
                self.player.move(dx, dy, is_running, self.current_room.width, self.current_room.height)
            
            # Update player
            self.player.update(dt)
            
            # Update level up notification
            self.level_up_notification.update(dt)
            
            # Update beam charging
            if self.player.is_charging_beam:
                self.player.update_beam_charge(dt)
            
            # Update beam if firing
            if self.player.current_beam:
                self.player.current_beam.update(dt)
            
            # Update camera
            self.camera.update(self.player, self.current_room.width, self.current_room.height)
            
            # Update projectiles
            for projectile in self.projectiles[:]:
                projectile.update(self.current_room.width, self.current_room.height)
                if not projectile.active:
                    self.projectiles.remove(projectile)
            
            # Update melee attacks
            for melee in self.melee_attacks[:]:
                melee.update(dt)
                if not melee.active:
                    self.melee_attacks.remove(melee)
            
            # Update enemies
            for enemy in self.enemies[:]:
                enemy.update(dt, self.player, self.current_room.width, self.current_room.height)
                
                # Check collisions with attacks
                for melee in self.melee_attacks:
                    if melee.active:
                        enemy.check_collision_with_attack(melee, 'melee')
                
                for projectile in self.projectiles:
                    if projectile.active and enemy.check_collision_with_attack(projectile, 'projectile'):
                        projectile.active = False
                
                if self.player.current_beam:
                    enemy.check_collision_with_attack(self.player.current_beam, 'beam')
                
                # Remove dead enemies
                if not enemy.active:
                    # Award XP
                    xp_reward = enemy.get_xp_reward(self.game_config)
                    self.player.gain_exp(xp_reward, self.game_config)
    
                    # Check if player leveled up
                    if self.player.pending_level_up:
                        self.level_up_notification.show(self.player.level, self.player.stat_points)
                        self.player.pending_level_up = False
                    
                    self.enemies.remove(enemy)
            
            # Update NPCs
            self.nearby_npc = None
            for npc in self.npcs[:]:
                npc.update(dt, self.player, self.current_room.width, self.current_room.height)
                
                # Check if player is near this NPC
                if npc.can_interact(self.player):
                    self.nearby_npc = npc
    
    def draw(self):
        # Background
        self.screen.fill((34, 139, 34))
        
        # Draw world grid
        tile_size = TILE_SIZE
        for x in range(0, self.current_room.width, tile_size):
            screen_x = x - self.camera.x
            if 0 <= screen_x <= SCREEN_WIDTH:
                pygame.draw.line(self.screen, (44, 149, 44), (screen_x, 0), (screen_x, SCREEN_HEIGHT - 100), 1)
        
        for y in range(0, self.current_room.height, tile_size):
            screen_y = y - self.camera.y
            if 0 <= screen_y <= SCREEN_HEIGHT - 100:
                pygame.draw.line(self.screen, (44, 149, 44), (0, screen_y), (SCREEN_WIDTH, screen_y), 1)
        
        # Draw world boundaries
        world_rect_x = 0 - self.camera.x
        world_rect_y = 0 - self.camera.y
        pygame.draw.rect(self.screen, self.colors['RED'], 
                        (world_rect_x, world_rect_y, self.current_room.width, self.current_room.height), 3)
        
        # Draw projectiles
        for projectile in self.projectiles:
            projectile.draw(self.screen, self.camera, self.colors)
        
        # Draw beam
        if self.player.current_beam:
            self.player.current_beam.draw(self.screen, self.camera, self.colors)
        
        # Draw melee attacks
        for melee in self.melee_attacks:
            melee.draw(self.screen, self.camera, self.colors)
        
        # Draw player
        self.player.draw(self.screen, self.camera, self.colors)
        
        # Draw enemies
        for enemy in self.enemies:
            enemy.draw(self.screen, self.camera, self.colors)
            
        # Draw NPCs
        for npc in self.npcs:
            npc.draw(self.screen, self.camera, self.colors)

        # Draw interaction indicator for nearby NPC
        if self.nearby_npc and not self.dialogue_box.active:
            screen_x = self.nearby_npc.x - self.camera.x
            screen_y = self.nearby_npc.y - self.camera.y - 20
            pygame.draw.circle(self.screen, self.colors['YELLOW'], (int(screen_x), int(screen_y)), 6)
            pygame.draw.circle(self.screen, self.colors['WHITE'], (int(screen_x), int(screen_y)), 6, 1)
        
        # Draw spawn menu
        self.spawn_menu.draw(self.screen, self.colors)

        # Draw crosshair when spawn menu is active
        if self.spawn_menu.active:
            mouse_x, mouse_y = pygame.mouse.get_pos()
            pygame.draw.line(self.screen, self.colors['CYAN'], (mouse_x - 10, mouse_y), (mouse_x + 10, mouse_y), 2)
            pygame.draw.line(self.screen, self.colors['CYAN'], (mouse_x, mouse_y - 10), (mouse_x, mouse_y + 10), 2)
            pygame.draw.circle(self.screen, self.colors['CYAN'], (mouse_x, mouse_y), 20, 2)
        
        # Draw NPC config menu
        self.npc_config_menu.draw(self.screen, self.colors)
        
        # Draw dialogue box
        self.dialogue_box.draw(self.screen, self.colors)
        
        # Draw level up notification
        self.level_up_notification.draw(self.screen, self.colors)

        # Draw dev menu
        self.dev_menu.draw(self.screen, self.colors)
        
        # Draw room editor menu
        self.room_editor_menu.draw(self.screen, self.colors)
        
        # Draw UI
        self.ui.draw_hud(self.screen, self.player, self.colors)
        
        # Draw appropriate screen
        if self.ui.current_screen == 'main_menu':
            self.ui.draw_main_menu(self.screen, self.colors)
        elif self.ui.current_screen == 'status':
            self.ui.draw_status_screen(self.screen, self.player, self.game_config, self.colors)
        elif self.ui.current_screen == 'inventory':
            self.ui.draw_inventory_screen(self.screen, self.player, self.colors)
        elif self.ui.current_screen == 'options':
            self.ui.draw_options_screen(self.screen, self.colors)
        
        pygame.display.flip()
    
    def run(self):
        self.last_time = time.time()
        while self.running:
            self.handle_events()
            self.update()
            self.draw()
            self.clock.tick(FPS)
        
        pygame.quit()
        sys.exit()

if __name__ == "__main__":
    game = Game()
    game.run()
