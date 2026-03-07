# utils/gate_font.py

import pygame

# Store the loaded font globally so all gates share the same instance
_gate_font = None


def get_gate_font():
    """Get the shared font for all level gates"""
    global _gate_font

    if _gate_font is None:
        try:
            # Load your TTF file once
            _gate_font = pygame.font.Font("assets/ui/fonts/gate.ttf", 24)
        except:
            print("Warning: Gate font not found, using default")
            _gate_font = pygame.font.Font(None, 24)

    return _gate_font