"""
Item definitions for consumable supplies (Legacy of Goku Maker Engine).

Each entry in ITEMS is keyed by item_id, which must match both the sprite
filename in assets/sprites/items/ (e.g. 'miso_soup' -> miso_soup.png) and
the string stored in player.inventory when the item is granted — dialogue
rewards, mission rewards, world pickups, etc. all already append plain
item_id strings to player.inventory (see game.py), so no new inventory
data structure is introduced here.

Effect types (see systems/item_effects.py for how each is applied):
  'heal_hp'      -> {'amount': int}                   restores HP
  'heal_ep'      -> {'amount': int}                   restores EP (player.ki)
  'full_restore' -> {}                                 fully restores HP and EP
  'revive'       -> {'hp_ratio': float}                revives a KO'd character
  'buff'         -> {'duration': float, 'stats': {stat_id: amount, ...}}
  'equip_stat'   -> {'stats': {stat_id: amount, ...}, 'exp_bonus': float}
                                                        worn item stat bonus (see 'slot' key).
                                                        'exp_bonus' is optional — a fraction
                                                        (0.10 = +10%) added to XP gained while
                                                        the item is equipped.

Equip items (category CATEGORY_EQUIP_BODY, CATEGORY_EQUIP_HANDS, etc.)
additionally carry a top-level 'slot' key (e.g. 'body', 'hands',
'accessory') identifying which equip slot they occupy — see
systems/item_effects.py's equip_item()/unequip_item().

Also home to ItemPickup (bottom of the file) — the world object for an item
the player has dropped from the pause menu's Equip tab. Kept in this file
rather than a separate module because it needs item_icon_path()/get_item()
right next to the ITEMS table it reads from, the same way zeni_system.py
keeps its pickup class next to its own denomination tables.
"""

import math
import os
import random

import pygame

from config.settings import RENDER_SCALE
from core.draw_layers import DrawLayer

CATEGORY_SUPPLIES    = 'supplies'
CATEGORY_STORY_ITEMS = 'storyitems'
CATEGORY_EQUIP_BODY  = 'equip_body'
CATEGORY_EQUIP_HANDS = 'equip_hands'
CATEGORY_EQUIP_FEET  = 'equip_feet'
CATEGORY_EQUIP_ACCESSORY = 'equip_accessory'

ITEMS = {
    # ── HP restoratives ─────────────────────────────────────────────────
    'miso_soup': {
        'name': 'Miso Soup',
        'description': 'A tasty, yet nutritious soup.',
        'effect_text': 'Restores 20 hit points.',
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'heal_hp', 'amount': 20},
    },
    'rice_ball': {
        'name': 'Rice Ball',
        'description': 'A small ball of sticky rice.',
        'effect_text': 'Restores 40 HP',
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'heal_hp', 'amount': 40},
    },
    'chicken_leg': {
        'name': 'Chicken Leg',
        'description': 'It tastes like Chicken!',
        'effect_text': 'Restores 80 HP',
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'heal_hp', 'amount': 80},
    },
    'hamburger': {
        'name': 'Hamburger',
        'description': 'A flame broiled and delicious hamburger.',
        'effect_text': 'Restores 120 HP',
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'heal_hp', 'amount': 120},
    },
    'onigiri': {
        'name': 'Onigiri',
        'description': 'A traditional triangle of rice, wrapped in kelp.',
        'effect_text': 'Restores 200 HP',
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'heal_hp', 'amount': 200},
    },
    'curry_plate': {
        'name': 'Curry Plate',
        'description': 'A dish of chicken and rice covered in spicy curry sauce.',
        'effect_text': 'Restores 400 HP',
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'heal_hp', 'amount': 400},
    },
    'steak': {
        'name': 'Steak',
        'description': 'A medium-rare steak seasoned to perfection.',
        'effect_text': 'Restores 600 HP',
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'heal_hp', 'amount': 600},
    },
    'turkey': {
        'name': 'Turkey',
        'description': 'This is a full sized turkey with stuffing.',
        'effect_text': 'Restores 800 HP',
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'heal_hp', 'amount': 800},
    },
    'three_course_meal': {
        'name': 'Three Course Meal',
        'description': 'Are you sure you can eat that all by yourself?',
        'effect_text': 'Restores 1100 HP',
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'heal_hp', 'amount': 1100},
    },
    'dinosaur_tail': {
        'name': 'Dinosaur Tail',
        'description': 'They say the most succulent dinosaur meat comes from the tail.',
        'effect_text': 'Restores 1500 HP',
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'heal_hp', 'amount': 1500},
    },
    'cookie': {
        'name': 'Cookie',
        'description': "Mrs. Brief's Freshly baked chocolate chip cookie.",
        'effect_text': 'Restores 5 HP',
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'heal_hp', 'amount': 5},
    },

    # ── EP restoratives ─────────────────────────────────────────────────
    'milk': {
        'name': 'Milk',
        'description': 'A carton of milk.',
        'effect_text': 'Restores 20 EP',
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'heal_ep', 'amount': 20},
    },
    'tea': {
        'name': 'Tea',
        'description': 'Care for a spot of tea?',
        'effect_text': 'Restores 40 EP',
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'heal_ep', 'amount': 40},
    },
    'soda': {
        'name': 'Soda',
        'description': "Now that's a tasty beverage!",
        'effect_text': 'Restores 80 EP',
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'heal_ep', 'amount': 80},
    },
    'vanilla_soda': {
        'name': 'Vanilla Soda',
        'description': 'Vanilla makes a good soda even better!',
        'effect_text': 'Restores 125 EP',
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'heal_ep', 'amount': 125},
    },
    'cherry_soda': {
        'name': 'Cherry Soda',
        'description': "There's nothing like a good Cherry Soda!",
        'effect_text': 'Restores 175 EP',
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'heal_ep', 'amount': 175},
    },
    'root_beer': {
        'name': 'Root Beer',
        'description': 'A cool refreshing root beer.',
        'effect_text': 'Restores 250 EP',
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'heal_ep', 'amount': 250},
    },
    'hercule_ade': {
        'name': 'Hercule-ade',
        'description': 'Replenishes your precious electrolytes.',
        'effect_text': 'Restores 350 EP',
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'heal_ep', 'amount': 350},
    },
    'elixir': {
        'name': 'Elixir',
        'description': 'A rare magical liquid that restores your vitality!',
        'effect_text': 'Restores 500 EP',
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'heal_ep', 'amount': 500},
    },
    'super_elixir': {
        'name': 'Super Elixir',
        'description': 'The best magical elixir on the market.',
        'effect_text': 'Restores 750 EP',
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'heal_ep', 'amount': 750},
    },
    'dinosaur_milk': {
        'name': 'Dinosaur Milk',
        'description': 'Where does dinosaur milk come from?',
        'effect_text': 'Restores 2500 EP',
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'heal_ep', 'amount': 2500},
    },

    # ── Special items ────────────────────────────────────────────────────
    'holy_water': {
        'name': 'Holy Water',
        'description': 'The legendary holy water of Korin.',
        'effect_text': 'Greatly increases your stats for 30 seconds. (+25 STR, POW, END, SPD)',
        'category': CATEGORY_SUPPLIES,
        'effect': {
            'type': 'buff',
            'duration': 30.0,
            'stats': {'strength': 25, 'ki_power': 25, 'vitality': 25, 'speed': 25},
        },
    },
    'senzu_bean': {
        'name': 'Senzu Bean',
        'description': "One of Korin's special Senzu Beans.",
        'effect_text': 'Fully restores HP and EP',
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'full_restore'},
    },
    'lazarus_crystal': {
        'name': 'Lazarus Crystal',
        'description': 'A strange blue crystal. Some say carrying them is good luck!',
        'effect_text': "Revives character if KO'd",
        'category': CATEGORY_SUPPLIES,
        'effect': {'type': 'revive', 'hp_ratio': 0.5},
    },

    # ── Body equipment ──────────────────────────────────────────────────
    'dirty_shirt': {
        'name': 'Dirty Shirt',
        'description': 'Maybe you should get this shirt cleaned before you wear it.',
        'effect_text': '+1 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 1}},
    },
    'dirty_gi': {
        'name': 'Dirty Gi',
        'description': 'Maybe you should get this gi cleaned before you wear it.',
        'effect_text': '+1 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 1}},
    },
    'dirty_armor': {
        'name': 'Dirty Armor',
        'description': 'Maybe you should get this armor cleaned before you wear it.',
        'effect_text': '+1 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 1}},
    },
    'prototype_space_armor': {
        'name': 'Prototype Space Armor',
        'description': "It's a prototype so it isn't working yet.",
        'effect_text': '+1 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 1}},
    },
    'cotton_gi': {
        'name': 'Cotton Gi',
        'description': 'A simple cotton gi.',
        'effect_text': '+1 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 1}},
    },
    'wool_sweater': {
        'name': 'Wool Sweater',
        'description': 'A thick knitted wool sweater.',
        'effect_text': '+3 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 3}},
    },
    'leather_jacket': {
        'name': 'Leather Jacket',
        'description': 'A cool looking leather jacket.',
        'effect_text': '+5 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 5}},
    },
    'reflective_tunic': {
        'name': 'Reflective Tunic',
        'description': 'A tunic made of reflective material resistant to energy attacks.',
        'effect_text': '+4 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 4}},
    },
    'clean_shirt': {
        'name': 'Clean Shirt',
        'description': 'A shirt so clean it shines!',
        'effect_text': '+5 END, +4 POW',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 5, 'ki_power': 4}},
    },
    'wooden_armor': {
        'name': 'Wooden Armor',
        'description': 'Armor carved out of wood.',
        'effect_text': '+6 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 6}},
    },
    'fancy_wardrobe': {
        'name': 'Fancy Wardrobe',
        'description': 'This suit is expensive looking.',
        'effect_text': '+5 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 5}},
    },
    'stone_o_yoroi': {
        'name': 'Stone O-Yoroi',
        'description': 'A traditional samurai armor carved out of stone.',
        'effect_text': '+8 END, -12 SPD',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 8, 'speed': -12}},
    },
    'bronze_keiko': {
        'name': 'Bronze Keiko',
        'description': 'A traditional samurai scale armor made of bronze.',
        'effect_text': '+10 END, -5 SPD',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 10, 'speed': -5}},
    },
    'halloween_costume': {
        'name': 'Halloween Costume',
        'description': "It's a scary Halloween Costume.",
        'effect_text': '+12 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 12}},
    },
    'armor_of_darkness': {
        'name': 'Armor of Darkness',
        'description': 'This magic armor makes your HP increase faster when you use melee.',
        'effect_text': '+15 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 15}},
    },
    'jade_keiko': {
        'name': 'Jade Keiko',
        'description': 'A traditional samurai scale armor made of jade that reduces damage done while blocking.',
        'effect_text': '+10 STR',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 10}},
    },
    'clean_gi': {
        'name': 'Clean Gi',
        'description': 'A gi so clean it shines!',
        'effect_text': '+10 POW, +13 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': 10, 'vitality': 13}},
    },
    'brute_coat': {
        'name': 'Brute Coat',
        'description': 'The kind of coat a real tough guy might wear.',
        'effect_text': '+9 STR, +13 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 9, 'vitality': 13}},
    },
    'spiked_breastplate': {
        'name': 'Spiked Breastplate',
        'description': 'This breastplate is covered with sharp spikes.',
        'effect_text': '+16 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 16}},
    },
    'mystic_aegis': {
        'name': 'Mystic Aegis',
        'description': 'A mystical chestplate that reduces the cost of the energy block.',
        'effect_text': '+16 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 16}},
    },
    'do_maru_of_shadows': {
        'name': 'Do-Maru of Shadows',
        'description': 'A traditional samurai armor that is good for stealth.',
        'effect_text': '-5 POW, +20 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': -5, 'vitality': 20}},
    },
    'silver_armor': {
        'name': 'Silver Armor',
        'description': 'A chestplate made of silver.',
        'effect_text': '+22 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 22}},
    },
    'monks_robe': {
        'name': "Monk's Robe",
        'description': 'A simple monks robe that is good for stealth.',
        'effect_text': '+15 POW, +4 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': 15, 'vitality': 4}},
    },
    'pyrite_armor': {
        'name': 'Pyrite Armor',
        'description': "It looks like Gold Armor, but it's fool's gold!",
        'effect_text': '-1 to All Stats',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'strength': -1, 'ki_power': -1, 'vitality': -1, 'speed': -1}},
    },
    'gold_armor': {
        'name': 'Gold Armor',
        'description': 'A chestplate made of gold.',
        'effect_text': '+24 END, -10 SPD',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 24, 'speed': -10}},
    },
    'stylish_haori': {
        'name': 'Stylish Haori',
        'description': 'Wearing this stylish jacket gives you a lot of charisma.',
        'effect_text': '+5 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 5}},
    },
    'armor_of_light': {
        'name': 'Armor of Light',
        'description': 'This magic armor makes your energy regenerate faster when you use melee.',
        'effect_text': '+25 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 25}},
    },
    'rhinestone_leisure_suit': {
        'name': 'Rhinestone Leisure Suit',
        'description': 'This is a really tacky looking suit.',
        'effect_text': '+7 POW, +20 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': 7, 'vitality': 20}},
    },
    'clean_armor': {
        'name': 'Clean Armor',
        'description': 'An armor so clean it shines!',
        'effect_text': '+16 POW, +25 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': 16, 'vitality': 25}},
    },
    'platinum_armor': {
        'name': 'Platinum Armor',
        'description': 'A chestplate made of platinum.',
        'effect_text': '+28 END, -8 SPD',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 28, 'speed': -8}},
    },
    'force_suit': {
        'name': 'Force Suit',
        'description': 'This suit is imbued with the power of science.',
        'effect_text': '+18 STR, +5 POW, +25 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 18, 'ki_power': 5, 'vitality': 25}},
    },
    'enhanced_space_armor': {
        'name': 'Enhanced Space Armor',
        'description': 'This high tech armor emits an electronic aura.',
        'effect_text': '+16 STR, +30 END, -10 SPD',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 16, 'vitality': 30, 'speed': -10}},
    },
    'dragon_armor': {
        'name': 'Dragon Armor',
        'description': 'Armor made from the scales of a dragon. Reduces the cost of energy attacks.',
        'effect_text': '+26 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 26}},
    },
    'super_armor': {
        'name': 'Super Armor',
        'description': 'This armor had magic that makes Super Saiyan last longer.',
        'effect_text': '+29 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 29}},
    },
    'wet_suit': {
        'name': 'Wet Suit',
        'description': 'This suit helps you catch better fish.',
        'effect_text': '+25 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 25}},
    },
    'diamond_armor': {
        'name': 'Diamond Armor',
        'description': 'A chestplate carved out of a giant diamond.',
        'effect_text': '+35 END, -12 SPD',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 35, 'speed': -12}},
    },
    'bad_man_shirt': {
        'name': '"Bad Man" Shirt',
        'description': 'A pink shirt? Who would wear such a ridiculous thing?',
        'effect_text': '+35 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 35}},
    },
    'titanium_breastplate': {
        'name': 'Titanium Breastplate',
        'description': 'A breastplate made of titanium.',
        'effect_text': '+36 END, +10 SPD',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 36, 'speed': 10}},
    },
    'saiyan_armor': {
        'name': 'Saiyan Armor',
        'description': 'Armor like the Saiyans once wore.',
        'effect_text': '+24 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 24}},
    },
    'crystal_o_yoroi': {
        'name': 'Crystal O-Yoroi',
        'description': 'The crystals in this armor make your energy attacks stronger.',
        'effect_text': '+38 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 38}},
    },
    'z_armor': {
        'name': '"Z" Armor',
        'description': 'This magic armor constantly heals you.',
        'effect_text': '+35 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 35}},
    },
    'geromantium_kataginu': {
        'name': 'Geromantium Kataginu',
        'description': 'A traditional samurai clothing made of Geromantium.',
        'effect_text': '+40 END',
        'category': CATEGORY_EQUIP_BODY,
        'slot': 'body',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 40}},
    },

    # ── Hand equipment ──────────────────────────────────────────────────
    'dirty_gloves': {
        'name': 'Dirty Gloves',
        'description': 'Maybe you should get these gloves cleaned before you wear it.',
        'effect_text': '+1 END',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 1}},
    },
    'dirty_gauntlets': {
        'name': 'Dirty Gauntlets',
        'description': 'Maybe you should get these gauntlets cleaned before you wear them.',
        'effect_text': '+1 END',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 1}},
    },
    'prototype_energy_gloves': {
        'name': 'Prototype Energy Gloves',
        'description': "It's a prototype so it isn't working yet.",
        'effect_text': '+1 STR',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 1}},
    },
    'cotton_gloves': {
        'name': 'Cotton Gloves',
        'description': 'A pair of simple cotton gloves.',
        'effect_text': '+1 STR, +1 END',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 1, 'vitality': 1}},
    },
    '1_ton_armbands': {
        'name': '1 Ton Armbands',
        'description': 'Weighted armbands slow you down, but give you more experience as a reward.',
        'effect_text': '-5 SPD, +~10% EXP',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'speed': -5}, 'exp_bonus': 0.10},
    },
    'wool_mittens': {
        'name': 'Wool Mittens',
        'description': 'A pair of thick knitted wool mittens.',
        'effect_text': '+2 STR, +3 END',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 2, 'vitality': 3}},
    },
    'reflective_gloves': {
        'name': 'Reflective Gloves',
        'description': 'A pair of gloves made of reflective material resistant to energy attacks.',
        'effect_text': '+3 STR, +6 END',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 3, 'vitality': 6}},
    },
    '2_ton_armbands': {
        'name': '2 Ton Armbands',
        'description': 'Weighted armsbands slow you down, but give you more experience as a reward.',
        'effect_text': '-8 SPD, +~20% EXP',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'speed': -8}, 'exp_bonus': 0.20},
    },
    'leather_gloves': {
        'name': 'Leather Gloves',
        'description': 'Gloves made of tough leather.',
        'effect_text': '+4 STR, +6 END',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 4, 'vitality': 6}},
    },
    'clean_gloves': {
        'name': 'Clean Gloves',
        'description': 'Gloves so clean they shine!',
        'effect_text': '+5 STR, +7 END',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 5, 'vitality': 7}},
    },
    'brass_knuckles': {
        'name': 'Brass Knuckles',
        'description': 'A pair of brass knuckles.',
        'effect_text': '+9 STR, +6 END',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 9, 'vitality': 6}},
    },
    'pilafs_gloves': {
        'name': "Pilaf's Gloves",
        'description': 'Legend has it wearing these gloves will make you rich!',
        'effect_text': '+8 STR, +6 END, +5 SPD',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 8, 'vitality': 6, 'speed': 5}},
    },
    '10_ton_armbands': {
        'name': '10 Ton Armbands',
        'description': 'Weighted armsbands slow you down, but give you more experience as a reward.',
        'effect_text': '-10 SPD, +~33% EXP',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'speed': -10}, 'exp_bonus': 0.33},
    },
    'iron_bracer': {
        'name': 'Iron Bracer',
        'description': 'An arm guard made of iron.',
        'effect_text': '+11 STR, +7 END',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 11, 'vitality': 7}},
    },
    'silver_gauntlets': {
        'name': 'Silver Gauntlets',
        'description': 'A pair of gauntlets made of silver.',
        'effect_text': '+12 STR, +8 END',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 12, 'vitality': 8}},
    },
    'magicians_gloves': {
        'name': "Magician's Gloves",
        'description': 'These magic gloves reduce the cost of an energy block.',
        'effect_text': '+7 STR, +5 END',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 7, 'vitality': 5}},
    },
    'charge_gloves': {
        'name': 'Charge Gloves',
        'description': 'You can detect power flowing through these high tech gloves.',
        'effect_text': '+13 STR, +5 END',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 13, 'vitality': 5}},
    },
    '20_ton_armbands': {
        'name': '20 Ton Armbands',
        'description': 'Weighted armbands slow you down, but give you more experience as a reward.',
        'effect_text': '-15 SPD, +50% EXP',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'speed': -15}, 'exp_bonus': 0.50},
    },
    'super_gloves': {
        'name': 'Super Gloves',
        'description': 'These gloves have magic that make Super Saiyan last longer.',
        'effect_text': '+13 STR',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 13}},
    },
    'clean_gauntlets': {
        'name': 'Clean Gauntlets',
        'description': 'Gauntlets so clean they shine!',
        'effect_text': '+14 STR, +15 END',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 14, 'vitality': 15}},
    },
    'platinum_gauntlets': {
        'name': 'Platinum Gauntlets',
        'description': 'A pair of gauntlets made of platinum.',
        'effect_text': '+16 STR, +10 END',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 16, 'vitality': 10}},
    },
    'brute_gloves': {
        'name': 'Brute Gloves',
        'description': 'The kind of gloves a real tough guy might wear.',
        'effect_text': '+20 STR, +6 END',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 20, 'vitality': 6}},
    },
    '100_ton_armbands': {
        'name': '100 Ton Armbands',
        'description': 'Weighted armbands slow you down, but give you more experience as a reward.',
        'effect_text': '+20 SPD, +75% EXP',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'speed': 20}, 'exp_bonus': 0.75},
    },
    'diamond_gauntlets': {
        'name': 'Diamond Gauntlets',
        'description': 'A pair of gauntlets carved from a diamond.',
        'effect_text': '+18 STR, +15 END',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 18, 'vitality': 15}},
    },
    'power_gauntlets': {
        'name': 'Power Gauntlets',
        'description': 'These gauntlets are glowing with energy.',
        'effect_text': '+15 STR, +20 POW, +2 END',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 15, 'ki_power': 20, 'vitality': 2}},
    },
    'enhanced_energy_gloves': {
        'name': 'Enhanced Energy Gloves',
        'description': 'These are powerful high tech gloves.',
        'effect_text': '+20 STR, +20 END',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 20, 'vitality': 20}},
    },
    'scuba_gloves': {
        'name': 'Scuba Gloves',
        'description': 'These gloves help you catch better fish.',
        'effect_text': '+15 STR',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 15}},
    },
    'kiloton_armbands': {
        'name': 'Kiloton Armbands',
        'description': 'Weighted armbands slow you down, but give you more experience as a reward.',
        'effect_text': '-25 SPD, +100% EXP',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'speed': -25}, 'exp_bonus': 1.00},
    },
    'saiyan_gloves': {
        'name': 'Saiyan Gloves',
        'description': 'Gloves like the Saiyans once wore.',
        'effect_text': '+20 STR',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 20}},
    },
    'crystal_gauntlets': {
        'name': 'Crystal Gauntlets',
        'description': 'The crystals in these gauntlets make your energy attacks stronger.',
        'effect_text': '+23 STR, +12 END',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 23, 'vitality': 12}},
    },
    'geromantis_gloves': {
        'name': 'Geromantis Gloves',
        'description': 'Looks like Geromantium Gloves, but are actually a cheap knock-off.',
        'effect_text': '-1 to All Stats',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': -1, 'ki_power': -1, 'vitality': -1, 'speed': -1}},
    },
    'geromantium_gloves': {
        'name': 'Geromantium Gloves',
        'description': 'A pair of gloves made of Geromantium.',
        'effect_text': '+25 STR, +20 END',
        'category': CATEGORY_EQUIP_HANDS,
        'slot': 'hands',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 25, 'vitality': 20}},
    },

    # ── Equip: accessory ─────────────────────────────────────────────────
    'dirty_cape': {
        'name': 'Dirty Cape',
        'description': 'Maybe you should get this cape cleaned before you wear it.',
        'effect_text': '+1 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 1}},
    },
    'dirty_belt': {
        'name': 'Dirty Belt',
        'description': 'Maybe you should get this belt cleaned before you wear it.',
        'effect_text': '+1 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 1}},
    },
    'expensive_wristwatch': {
        'name': 'Expensive Wristwatch',
        'description': 'This is an extremely expensive wristwatch.',
        'effect_text': '+2 POW, +1 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': 2, 'vitality': 1}},
    },
    'white_belt': {
        'name': 'White Belt',
        'description': 'This is a white karate belt.',
        'effect_text': '+3 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 3}},
    },
    'rhinestone_sunglasses': {
        'name': 'Rhinestone Sunglasses',
        'description': 'These are some really tacky looking sunglasses.',
        'effect_text': '+2 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 2}},
    },
    'primordial_twisty_straw': {
        'name': 'Primordial Twisty-Straw',
        'description': 'Drinks taste better when you use this ancient straw.',
        'effect_text': '+1 END. (Drinks-set-HP-to-EP-restored effect not yet implemented.)',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 1}},
    },
    'majestic_chopsticks': {
        'name': 'Majestic Chopsticks',
        'description': 'Food tastes better when you use these chopsticks.',
        'effect_text': '+1 END. (+20% HP restored from food not yet implemented.)',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 1}},
    },
    'wool_cap': {
        'name': 'Wool Cap',
        'description': 'A thick knitted wool cap.',
        'effect_text': '+4 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 4}},
    },
    'lucky_charm': {
        'name': 'Lucky Charm',
        'description': 'This charm gives you good luck!',
        'effect_text': '+1 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 1}},
    },
    'stone_menpo': {
        'name': 'Stone Men-po',
        'description': 'An armored samurai face mask made of stone.',
        'effect_text': '+5 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 5}},
    },
    'quartz_amulet': {
        'name': 'Quartz Amulet',
        'description': 'This is an amulet made of quartz.',
        'effect_text': '+2 POW',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': 2}},
    },
    'yellow_belt': {
        'name': 'Yellow Belt',
        'description': 'This is a yellow karate belt.',
        'effect_text': '+6 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 6}},
    },
    'topaz_amulet': {
        'name': 'Topaz Amulet',
        'description': 'This is an amulet made of Topaz.',
        'effect_text': '+5 POW',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': 5}},
    },
    'bandana': {
        'name': 'Bandana',
        'description': 'Only a really vicious criminal would wear something like this.',
        'effect_text': '+3 STR, +3 POW, +3 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 3, 'ki_power': 3, 'vitality': 3}},
    },
    'red_belt': {
        'name': 'Red Belt',
        'description': 'This is a red karate belt.',
        'effect_text': '+9 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 9}},
    },
    'demon_mask': {
        'name': 'Demon Mask',
        'description': 'This a terrifying looking mask.',
        'effect_text': '+7 STR, -5 POW, +9 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 7, 'ki_power': -5, 'vitality': 9}},
    },
    'monocle': {
        'name': 'Monocle',
        'description': 'Wearing this classy monocle gives you lots of charisma.',
        'effect_text': '+1 POW, +4 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': 1, 'vitality': 4}},
    },
    'amethyst_amulet': {
        'name': 'Amethyst Amulet',
        'description': 'This is an amulet made of amethyst.',
        'effect_text': '+7 POW',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': 7}},
    },
    'hares_foot': {
        'name': "Hare's Foot",
        'description': "This item looks like a rabbit's foot... but it's not real.",
        'effect_text': '-1 to All Stats',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'strength': -1, 'ki_power': -1, 'vitality': -1, 'speed': -1}},
    },
    'green_belt': {
        'name': 'Green Belt',
        'description': 'This is a green Karate belt.',
        'effect_text': '+10 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 10}},
    },
    'snorkel': {
        'name': 'Snorkel',
        'description': 'This snorkel helps you catch better fish.',
        'effect_text': '+8 STR, +10 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 8, 'vitality': 10}},
    },
    'clean_cape': {
        'name': 'Clean Cape',
        'description': 'A cape so clean it shines!',
        'effect_text': '+8 POW, +11 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': 8, 'vitality': 11}},
    },
    'garlic_necklace': {
        'name': 'Garlic Necklace',
        'description': 'This protects against the undead.',
        'effect_text': '-5 STR, -10 POW, +25 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'strength': -5, 'ki_power': -10, 'vitality': 25}},
    },
    'rabbits_foot': {
        'name': "Rabbit's Foot",
        'description': 'This rabbit\'s foot gives you good luck!',
        'effect_text': '+1 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 1}},
    },
    'sapphire_amulet': {
        'name': 'Sapphire Amulet',
        'description': 'This is an amulet made of sapphire.',
        'effect_text': '+10 POW',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': 10}},
    },
    'skull_ring': {
        'name': 'Skull Ring',
        'description': "There's an evil aura around this ring...",
        'effect_text': '+9 POW, +12 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': 9, 'vitality': 12}},
    },
    'pure_black_cape': {
        'name': 'Pure Black Cape',
        'description': 'How much more black could this cape be? The answer is none.',
        'effect_text': '+13 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 13}},
    },
    'super_cape': {
        'name': 'Super Cape',
        'description': 'This cape has magic that makes Super Saiyan last longer.',
        'effect_text': '+16 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 16}},
    },
    'talisman_of_light': {
        'name': 'Talisman of Light',
        'description': 'This talisman increases the amount of energy recharged when you use melee attacks.',
        'effect_text': '+9 STR, +10 POW, +12 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 9, 'ki_power': 10, 'vitality': 12}},
    },
    'emerald_amulet': {
        'name': 'Emerald Amulet',
        'description': 'This is an amulet made of emerald.',
        'effect_text': '+12 POW',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': 12}},
    },
    'doom_amulet': {
        'name': 'Doom Amulet',
        'description': "There's an evil aura around this amulet...",
        'effect_text': '+10 STR, +20 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 10, 'vitality': 20}},
    },
    'polka_dot_kazoo': {
        'name': 'Polka-Dot Kazoo',
        'description': 'This is a very silly kazoo.',
        'effect_text': 'No stat bonus.',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {}},
    },
    'iron_kabuto': {
        'name': 'Iron Kabuto',
        'description': 'A traditional samurai helmet made of iron.',
        'effect_text': '+20 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 20}},
    },
    'ox_kings_hat': {
        'name': "Ox King's Hat",
        'description': 'This is the kind of viking hat that Ox-King is often seen wearing.',
        'effect_text': '+11 STR, +10 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 11, 'vitality': 10}},
    },
    'evil_talisman': {
        'name': 'Evil Talisman',
        'description': "There is a terrible cost to this talisman's power.",
        'effect_text': '-10 STR, +18 POW, -5 END, +5 SPD',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'strength': -10, 'ki_power': 18, 'vitality': -5, 'speed': 5}},
    },
    'crystal_pendant': {
        'name': 'Crystal Pendant',
        'description': 'The crystals in this pendant increase the power of energy attacks.',
        'effect_text': '+15 POW, +10 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': 15, 'vitality': 10}},
    },
    'blue_belt': {
        'name': 'Blue Belt',
        'description': 'This is a blue karate belt.',
        'effect_text': '+15 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 15}},
    },
    'mercurys_cap': {
        'name': "Mercury's Cap",
        'description': 'The legendary cap of Mercury.',
        'effect_text': '+12 END, +20 SPD',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 12, 'speed': 20}},
    },
    'four_leaf_clover': {
        'name': 'Four-Leaf Clover',
        'description': 'This four-leaf clover gives you good luck!',
        'effect_text': '+1 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 1}},
    },
    'vampire_cape': {
        'name': 'Vampire Cape',
        'description': 'This cape causes HP to be recharged instead of energy when you use melee attacks.',
        'effect_text': '+14 POW, +22 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': 14, 'vitality': 22}},
    },
    'crisis_ring': {
        'name': 'Crisis Ring',
        'description': "When you're about to die, this ring makes you stronger.",
        'effect_text': '+5 STR, +2 POW, +10 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 5, 'ki_power': 2, 'vitality': 10}},
    },
    'brown_belt': {
        'name': 'Brown Belt',
        'description': 'This is a brown karate belt.',
        'effect_text': '+17 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 17}},
    },
    'ruby_amulet': {
        'name': 'Ruby Amulet',
        'description': 'This is an amulet made of ruby.',
        'effect_text': '+16 POW',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': 16}},
    },
    'eldritch_cameo': {
        'name': 'Eldritch Cameo',
        'description': 'This carving of unfathomable age is etched in a long forgotten ancient language.',
        'effect_text': '-10 STR, -10 POW, +30 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'strength': -10, 'ki_power': -10, 'vitality': 30}},
    },
    'black_belt': {
        'name': 'Black Belt',
        'description': 'This is a black karate belt.',
        'effect_text': '+25 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 25}},
    },
    'diamond_amulet': {
        'name': 'Diamond Amulet',
        'description': 'This is an amulet made of diamond.',
        'effect_text': '+20 POW',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': 20}},
    },
    'clean_belt': {
        'name': 'Clean Belt',
        'description': 'A belt so clean it shines!',
        'effect_text': '+20 POW, +20 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': 20, 'vitality': 20}},
    },
    'geromantium_bandana': {
        'name': 'Geromantium Bandana',
        'description': 'A bandana made of Geromantium.',
        'effect_text': '+40 END',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 40}},
    },
    'gokuu_hat': {
        'name': '"Gokuu" Hat',
        'description': "If you wear this hat while you level up, you'll get a bonus!",
        'effect_text': '+5 END. (Bonus stat point on level-up not yet implemented.)',
        'category': CATEGORY_EQUIP_ACCESSORY,
        'slot': 'accessory',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 5}},
    },

    # ── Equip: feet ──────────────────────────────────────────────────────
    'dirty_tabi': {
        'name': 'Dirty Tabi',
        'description': 'Maybe you should get these tabi boots cleaned before you wear them.',
        'effect_text': '+1 END',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 1}},
    },
    'dirty_shoes': {
        'name': 'Dirty Shoes',
        'description': 'Maybe you should get these shoes cleaned before you wear them.',
        'effect_text': '+1 END',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 1}},
    },
    'prototype_hyper_boots': {
        'name': 'Prototype Hyper Boots',
        'description': "It's a prototype so it isn't working yet.",
        'effect_text': '+1 END, +1 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 1, 'speed': 1}},
    },
    'dirty_boots': {
        'name': 'Dirty Boots',
        'description': 'Maybe you should get these boots cleaned before you wear them.',
        'effect_text': '+1 END',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 1}},
    },
    'cotton_tabi': {
        'name': 'Cotton Tabi',
        'description': 'Simple cotton tabi boots.',
        'effect_text': '+1 END, +1 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 1, 'speed': 1}},
    },
    '1_ton_boots': {
        'name': '1 Ton Boots',
        'description': 'Weighted boots slow you down, but give you more experience as a reward.',
        'effect_text': '-5 SPD, +~10% EXP',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'speed': -5}, 'exp_bonus': 0.10},
    },
    'woolen_shoes': {
        'name': 'Woolen Shoes',
        'description': 'Shoes made of knitted wool.',
        'effect_text': '+2 END, +5 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 2, 'speed': 5}},
    },
    'leather_moccasins': {
        'name': 'Leather Moccasins',
        'description': 'Moccasins made of thick tanned leather.',
        'effect_text': '+4 END, +8 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 4, 'speed': 8}},
    },
    '2_ton_boots': {
        'name': '2 Ton Boots',
        'description': 'Weighted boots slow you down, but give you more experience as a reward.',
        'effect_text': '-8 SPD, +~20% EXP',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'speed': -8}, 'exp_bonus': 0.20},
    },
    'clean_tabi': {
        'name': 'Clean Tabi',
        'description': 'Tabi boots so clean they shine!',
        'effect_text': '+5 POW, +8 END, +10 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': 5, 'vitality': 8, 'speed': 10}},
    },
    'wooden_geta': {
        'name': 'Wooden Geta',
        'description': 'Sandals made of wood.',
        'effect_text': '+6 END, +15 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 6, 'speed': 15}},
    },
    'sneakers': {
        'name': 'Sneakers',
        'description': 'These are sneakers... For sneaking!',
        'effect_text': '+5 END, +25 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 5, 'speed': 25}},
    },
    '10_ton_boots': {
        'name': '10 Ton Boots',
        'description': 'Weighted boots slow you down, but give you more experience as a reward.',
        'effect_text': '-10 SPD, +~33% EXP',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'speed': -10}, 'exp_bonus': 0.33},
    },
    'stone_geta': {
        'name': 'Stone Geta',
        'description': 'Sandals made of stone.',
        'effect_text': '+10 END, +17 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 10, 'speed': 17}},
    },
    'alligator_loafers': {
        'name': 'Alligator Loafers',
        'description': 'These are some expensive shoes!',
        'effect_text': '+2 END, +5 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 2, 'speed': 5}},
    },
    'spirit_geta': {
        'name': 'Spirit Geta',
        'description': 'There is a mysterious supernatural aura around these sandals.',
        'effect_text': '+5 STR, +15 POW, -10 END, +15 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 5, 'ki_power': 15, 'vitality': -10, 'speed': 15}},
    },
    'bronze_plated_boots': {
        'name': 'Bronze Plated Boots',
        'description': 'Leather boots covered in bronze plates.',
        'effect_text': '+10 END, +18 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 10, 'speed': 18}},
    },
    'iron_greaves': {
        'name': 'Iron Greaves',
        'description': 'Shin-guards made of iron.',
        'effect_text': '+12 END, +18 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 12, 'speed': 18}},
    },
    '20_ton_boots': {
        'name': '20 Ton Boots',
        'description': 'Weighted boots slow you down, but give you more experience as a reward.',
        'effect_text': '-15 SPD, +50% EXP',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'speed': -15}, 'exp_bonus': 0.50},
    },
    'flippers': {
        'name': 'Flippers',
        'description': 'These flippers help you catch better fish.',
        'effect_text': '+20 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'speed': 20}},
    },
    'clean_shoes': {
        'name': 'Clean Shoes',
        'description': 'Shoes so clean they shine!',
        'effect_text': '+16 POW, +15 END, +20 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': 16, 'vitality': 15, 'speed': 20}},
    },
    'silver_boots': {
        'name': 'Silver Boots',
        'description': 'Boots made of silver.',
        'effect_text': '+14 END, +20 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 14, 'speed': 20}},
    },
    'silvery_boots': {
        'name': 'Silvery Boots',
        'description': 'These look like Silver Boots, but the silver paint is scratching off.',
        'effect_text': '-1 to All Stats',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'strength': -1, 'ki_power': -1, 'vitality': -1, 'speed': -1}},
    },
    'super_boots': {
        'name': 'Super Boots',
        'description': 'These boots have magic that makes Super Saiyan last longer.',
        'effect_text': '+18 END, +20 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 18, 'speed': 20}},
    },
    '100_ton_boots': {
        'name': '100 Ton Boots',
        'description': 'Weighted boots slow you down, but give you more experience as a reward.',
        'effect_text': '-20 SPD, +75% EXP',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'speed': -20}, 'exp_bonus': 0.75},
    },
    'gold_boots': {
        'name': 'Gold Boots',
        'description': 'Boots made of gold.',
        'effect_text': '+20 END, +15 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 20, 'speed': 15}},
    },
    'shock_boots': {
        'name': 'Shock Boots',
        'description': 'You can detect power flowing through these high tech boots.',
        'effect_text': '+15 POW, +12 END, +20 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': 15, 'vitality': 12, 'speed': 20}},
    },
    'enhanced_hyper_boots': {
        'name': 'Enhanced Hyper Boots',
        'description': 'These are powerful high tech boots.',
        'effect_text': '+18 STR, +18 END, +25 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'strength': 18, 'vitality': 18, 'speed': 25}},
    },
    'kiloton_boots': {
        'name': 'Kiloton Boots',
        'description': 'Weighted boots slow you down, but give you more experience as a reward.',
        'effect_text': '-25 SPD, +100% EXP',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'speed': -25}, 'exp_bonus': 1.00},
    },
    'saiyan_boots': {
        'name': 'Saiyan Boots',
        'description': 'Boots like the Saiyans once wore.',
        'effect_text': '+24 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'speed': 24}},
    },
    'clean_boots': {
        'name': 'Clean Boots',
        'description': 'Boots so clean they shine!',
        'effect_text': '+20 POW, +20 END, +25 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'ki_power': 20, 'vitality': 20, 'speed': 25}},
    },
    'winged_sandals': {
        'name': 'Winged Sandals',
        'description': 'These winged sandals will make you walk and run faster.',
        'effect_text': '+5 END, +40 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 5, 'speed': 40}},
    },
    'soccer_cleats': {
        'name': 'Soccer Cleats',
        'description': 'These soccer cleats pack quite a kick!',
        'effect_text': '+12 END, +30 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 12, 'speed': 30}},
    },
    'geromantium_tabi': {
        'name': 'Geromantium Tabi',
        'description': 'Tabi boots made of Geromantium.',
        'effect_text': '+25 END, +28 SPD',
        'category': CATEGORY_EQUIP_FEET,
        'slot': 'feet',
        'effect': {'type': 'equip_stat', 'stats': {'vitality': 25, 'speed': 28}},
    },
}


def get_item(item_id):
    """Safe lookup — returns None for unknown ids instead of raising."""
    return ITEMS.get(item_id)


def get_items_by_category(category):
    return {iid: data for iid, data in ITEMS.items() if data.get('category') == category}


# ---------------------------------------------------------------------------
# Item drop system — world pickups for items the player drops from the
# pause menu's Equip tab (see pause_menu.py's 'drop_item:<id>' signal,
# returned by _confirm_equip_action() when 'Drop' is confirmed). Modeled on
# core/zeni_system.py's ZeniPickup — same hop-then-settle shape, built with
# the same _lerp-by-distance trick for scaling the arc — but different in
# the two ways that actually matter for a single dropped item rather than a
# shower of coins:
#
#   1. One item, not a stack, tossed in a fully random direction (not "away
#      from a hit"), drawn as a single flat icon sprite
#      (assets/sprites/items/<item_id>.png, or
#      .../equipment/<slot>/<item_id>.png for equip items — mirrors
#      pause_menu.py's own _item_icon_path, see item_icon_path() below)
#      rather than an animated frame strip. Nothing to idle/blink through.
#
#   2. No time-based despawn. A ZeniPickup fades itself out after
#      _LIFETIME seconds; an ItemPickup never does — it "sits there waiting
#      for the player to pick it up or when the player leaves the current
#      room, despawning". That's a room-lifecycle event this file has no
#      visibility into, so it's deliberately NOT implemented here as an age
#      timer. Call ItemPickup.despawn() (or just drop the list) from
#      wherever game.py handles leaving a room.
#
#   3. Collecting one requires the player to press E while standing next
#      to it — same interact-key flow as opening a chest (see
#      ItemPickup.is_player_nearby, and game.py's
#      _handle_interact/_update_chest_pickup for the chest version this
#      mirrors) — not an automatic collect from walking through it.
#
# Usage — the intended call site is game.py, once the pause menu actually
# closes with a drop pending (NOT the moment 'Drop' is confirmed — see the
# pause_menu.py comment on _confirm_equip_action: confirming Drop just
# closes the popup and removes the item from the inventory/list; the item
# should only appear in the world once the whole pause menu closes)::
#
#     from core.items import spawn_item_pickup
#
#     pickup = spawn_item_pickup(item_id, player.x, player.y)
#     self.item_pickups.append(pickup)
#
# Every frame, while the pickup's room is the active one — this only ticks
# the toss/bounce/settle animation and tracks proximity; it does NOT grant
# the item (see nearby_item_pickup / _handle_interact's E-key branch for
# that, same pose-then-grant flow as chests use)::
#
#     for p in self.item_pickups:
#         p.update(dt, player)
#     self.item_pickups = [p for p in self.item_pickups if p.active]
#     self.nearby_item_pickup = next(
#         (p for p in self.item_pickups if p.is_settled and p.is_player_nearby), None)
#
# On E, once nearby_item_pickup is set (mirrors the chest branch exactly)::
#
#     pickup = self.nearby_item_pickup
#     pickup.collect()             # removes it from the world immediately
#     player.start_pickup_item()   # same pose chests use
#     # ...then once player.is_picking_up_item goes False, grant it:
#     player.inventory.append(pickup.item_id)
#
# And when the player leaves the room it was dropped in — no fade, no
# animation, it just isn't there when they come back::
#
#     self.item_pickups.clear()   # or [p.despawn() for p in self.item_pickups]
# ---------------------------------------------------------------------------

_NEARBY_RADIUS    = 12.0   # px — distance at which a settled pickup counts as "nearby" for the E-key interact prompt (see ItemPickup.is_player_nearby)
_NEARBY_RADIUS_SQ = _NEARBY_RADIUS * _NEARBY_RADIUS

# Shared ground shadow, same universal art zeni's big denominations use —
# a dropped item is closer in size to those than to the tiny coin sprites.
SHADOW_ICON_DIR   = os.path.join('assets', 'sprites', 'universal')
_SHADOW_NAME       = 'shadow'

# Zeni fixes the "shadow hidden under a tall sprite" problem with a
# per-denomination override (see ZENI_SHADOW_Y_OFFSETS in zeni_system.py) —
# fine there since it's only 2 sizes. Items span way more icon heights
# (a ring vs. a full-size sword icon, say), so instead of one flat offset
# scale it off each icon's own rendered height, which keeps the shadow
# peeking out from under the icon regardless of size.
_SHADOW_Y_OFFSET_MIN   = 6     # floor for small icons, same as the old flat value
_SHADOW_Y_OFFSET_RATIO = 0.5   # fraction of icon height pushed below center

# Main throw — "it can go far or not": distance is random per drop, and (as
# with zeni) arc height scales with how far it's actually going, via the
# same near/far lerp trick.
_HOP_DISTANCE_MIN = 12    # px — shortest possible toss
_HOP_DISTANCE_MAX = 70    # px — longest possible toss

_HOP_DURATION      = 0.45
_HOP_PEAK_FRACTION = 0.4   # fraction of hop duration spent rising

_HOP_HEIGHT_NEAR = 10   # px — arc height for the shortest toss
_HOP_HEIGHT_FAR  = 20   # px — arc height for the longest toss

_FALL_EASE = 2.2   # >= 2 so the fall starts at zero speed (no snap at the peak) — see zeni_system.py's comment on this same constant

# Bounce chain after the main throw lands — "it almost always jumps on
# landing, sometimes even twice": a high-probability first bounce, then a
# much smaller chance of a second, smaller-still one after that. Each
# bounce keeps travelling in the SAME direction as the main throw, just
# covering less ground and rising less high than the hop before it — reads
# as a thrown object skidding/bouncing to a stop rather than a second
# random toss.
_BOUNCE_1_CHANCE = 0.9    # odds of a bounce after the main throw lands
_BOUNCE_2_CHANCE = 0.3    # odds of a second, smaller bounce after the first

_BOUNCE_DISTANCE_FACTOR = 0.35   # each bounce covers this much of the previous hop's distance
_BOUNCE_HEIGHT_FACTOR   = 0.45   # ...and rises this much of the previous hop's height
_BOUNCE_DURATION_FACTOR = 0.6    # ...and takes this much of the previous hop's time

_MIN_BOUNCE_DISTANCE = 1.0   # floor so a decaying chain never divides down to a visible 0px "hop"
_MIN_BOUNCE_HEIGHT   = 1.0
_MIN_BOUNCE_DURATION = 0.12


def _lerp_by_distance(distance, near_value, far_value):
    """Same helper as zeni_system.py's — linearly interpolate between
    *near_value* (at _HOP_DISTANCE_MIN) and *far_value* (at
    _HOP_DISTANCE_MAX), clamped to that range."""
    span = _HOP_DISTANCE_MAX - _HOP_DISTANCE_MIN
    t = 0.0 if span <= 0 else (distance - _HOP_DISTANCE_MIN) / span
    t = max(0.0, min(1.0, t))
    return near_value + (far_value - near_value) * t


def item_icon_path(item_id):
    """Same convention pause_menu.py's _item_icon_path uses: equip items
    (body/hands/feet/accessory) live under a per-slot equipment/
    subfolder rather than the flat items/ folder consumables and story
    items use."""
    data = get_item(item_id) or {}
    slot = data.get('slot')
    if slot:
        return os.path.join('assets', 'sprites', 'items', 'equipment', slot, f'{item_id}.png')
    return os.path.join('assets', 'sprites', 'items', f'{item_id}.png')


class ItemPickup:
    """A single dropped item sitting in the world.

    Spawns with a random-direction throw that decays into 1-3 hops total
    (the main throw, then usually one bounce, occasionally a second — see
    _BOUNCE_1_CHANCE/_BOUNCE_2_CHANCE), then settles and waits for the
    player to press E while standing nearby — same interact-key flow as
    opening a chest (see game.py's _handle_interact / nearby_item_pickup),
    not an automatic walk-into-it collect. Doesn't expire on its own — see the item drop
    system comment block above for despawn-on-room-leave handling.
    """

    _icon_cache   = {}
    _shadow_cache = {}

    def __init__(self, item_id, x, y):
        self.item_id = item_id
        self.x = x
        self.y = y

        angle = random.uniform(0.0, 2 * math.pi)
        self.direction = (math.cos(angle), math.sin(angle))

        hop_distance = random.uniform(_HOP_DISTANCE_MIN, _HOP_DISTANCE_MAX)
        hop_height   = _lerp_by_distance(hop_distance, _HOP_HEIGHT_NEAR, _HOP_HEIGHT_FAR)
        self._hops = [self._make_hop(hop_distance, _HOP_DURATION, hop_height)]

        if random.random() < _BOUNCE_1_CHANCE:
            self._hops.append(self._make_bounce(self._hops[-1]))
            if random.random() < _BOUNCE_2_CHANCE:
                self._hops.append(self._make_bounce(self._hops[-1]))

        self._hop_index   = 0
        self._hop_timer   = 0.0
        self._hop_start_x = x
        self._hop_start_y = y
        self._hop_z       = 0.0   # visual height off the ground (cosmetic only)
        self.is_settled   = False
        self.is_player_nearby = False   # set every frame once settled — see update()

        self.collected = False
        self.active    = True

        self.draw_layer = DrawLayer.ITEMS
        self.y_sort     = True

    @staticmethod
    def _make_hop(distance, duration, height):
        return {'distance': distance, 'duration': max(0.05, duration),
                'height': max(1.0, height), 'peak_fraction': _HOP_PEAK_FRACTION,
                'fall_ease': _FALL_EASE}

    @classmethod
    def _make_bounce(cls, prev_hop):
        distance = max(_MIN_BOUNCE_DISTANCE, prev_hop['distance'] * _BOUNCE_DISTANCE_FACTOR)
        height   = max(_MIN_BOUNCE_HEIGHT,   prev_hop['height']   * _BOUNCE_HEIGHT_FACTOR)
        duration = max(_MIN_BOUNCE_DURATION, prev_hop['duration'] * _BOUNCE_DURATION_FACTOR)
        return cls._make_hop(distance, duration, height)

    @classmethod
    def _load_icon(cls, item_id):
        if item_id not in cls._icon_cache:
            try:
                raw = pygame.image.load(item_icon_path(item_id)).convert_alpha()
                w, h = raw.get_size()
                scaled = pygame.transform.scale(
                    raw, (max(1, round(w * RENDER_SCALE)), max(1, round(h * RENDER_SCALE))))
            except Exception:
                scaled = None
            cls._icon_cache[item_id] = scaled
        return cls._icon_cache[item_id]

    @classmethod
    def _load_shadow(cls):
        if 'shadow' not in cls._shadow_cache:
            try:
                raw = pygame.image.load(os.path.join(SHADOW_ICON_DIR, f'{_SHADOW_NAME}.png')).convert_alpha()
                w, h = raw.get_size()
                scaled = pygame.transform.scale(
                    raw, (max(1, round(w * RENDER_SCALE)), max(1, round(h * RENDER_SCALE))))
            except Exception:
                scaled = None
            cls._shadow_cache['shadow'] = scaled
        return cls._shadow_cache['shadow']

    def despawn(self):
        """Called by game.py when the player leaves the room this pickup
        was dropped in — no fade, no animation, it's just gone."""
        self.active = False

    def get_collision_rect(self):
        icon = self._load_icon(self.item_id)
        w, h = icon.get_size() if icon else (16, 16)
        return pygame.Rect(int(self.x - w / 2), int(self.y - h / 2), w, h)

    def get_sort_key(self):
        """(layer, y) tuple used by LayerManager.draw_all — see DrawableObject.

        self.y is the icon's *center* (see draw(), which blits the icon
        centered on self.y), same as Player.x/y is the player's center.
        Player.get_sort_key() sorts by the player's feet (center +
        half height) rather than the raw center, so this has to do the
        same with the icon's own base, or the two get compared on
        different reference points — the item's visual bottom edge can
        sit well below where its sort key says it is, letting it draw in
        front of the player long after it should have flipped behind.
        icon.get_height() is already RENDER_SCALE-scaled (see
        _load_icon), so convert back to world units before adding it to
        self.y, which is itself in world space.
        """
        icon = self._load_icon(self.item_id)
        half_height = (icon.get_height() / RENDER_SCALE / 2) if icon else 0
        return (self.draw_layer, self.y + half_height)

    def update(self, dt, player):
        if not self.active:
            return

        if not self.is_settled:
            hop = self._hops[self._hop_index]
            self._hop_timer += dt
            t = min(1.0, self._hop_timer / hop['duration'])

            # Same asymmetric arc as ZeniPickup.update(): sin-eased rise,
            # power-eased fall (fall_ease >= 2 so it leaves the peak at
            # zero vertical speed — no snap). peak_x_fraction is left equal
            # to peak_fraction here (constant horizontal speed through the
            # hop) since these are short, snappy hops rather than zeni's
            # far-flung coins that needed the lopsided "creep to a stop"
            # version.
            peak_fraction = hop['peak_fraction']
            if t <= peak_fraction:
                rt = t / peak_fraction if peak_fraction > 0 else 1.0
                x_progress = rt * peak_fraction
                height_progress = math.sin(rt * math.pi / 2)
            else:
                ft = (t - peak_fraction) / (1.0 - peak_fraction) if peak_fraction < 1.0 else 1.0
                x_progress = peak_fraction + (1.0 - peak_fraction) * ft
                height_progress = 1.0 - ft ** hop['fall_ease']

            self.x = self._hop_start_x + self.direction[0] * hop['distance'] * x_progress
            self.y = self._hop_start_y + self.direction[1] * hop['distance'] * x_progress
            self._hop_z = hop['height'] * height_progress

            if t >= 1.0:
                self._hop_index  += 1
                self._hop_timer   = 0.0
                self._hop_start_x = self.x
                self._hop_start_y = self.y
                self._hop_z       = 0.0
                if self._hop_index >= len(self._hops):
                    self.is_settled = True
        else:
            dx = player.x - self.x
            dy = player.y - self.y
            self.is_player_nearby = (dx * dx + dy * dy) <= _NEARBY_RADIUS_SQ

    def collect(self):
        """Called by game.py's E-key interact handler (mirrors how chests
        work — see _handle_interact) once the player actually confirms
        picking this up; walking into a settled pickup no longer collects
        it on its own (see is_player_nearby above, set every frame
        instead). Removes the pickup from the world immediately — same as
        a chest flipping to its opened sprite — while game.py plays the
        pickup pose and grants the item to the inventory once that
        animation finishes."""
        self.collected = True
        self.active    = False


    def draw(self, surface, camera, colors=None, render_scale=RENDER_SCALE):
        # colors is accepted-but-unused, same as ZeniPickup.draw — kept so
        # the LayerManager can call every drawable in draw_all's mixed list
        # (zeni pickups, item pickups, enemies, etc.) with one uniform
        # signature instead of type-checking each object first.
        icon = self._load_icon(self.item_id)
        if icon is None:
            return

        center_x = self.x * render_scale - camera.x
        center_y = self.y * render_scale - camera.y

        shadow = self._load_shadow()
        if shadow is not None:
            y_offset = max(_SHADOW_Y_OFFSET_MIN, icon.get_height() * _SHADOW_Y_OFFSET_RATIO)
            shadow_x = int(center_x - shadow.get_width() / 2)
            shadow_y = int(center_y - shadow.get_height() / 2) + int(y_offset)
            surface.blit(shadow, (shadow_x, shadow_y))

        screen_x = int(center_x - icon.get_width() / 2)
        screen_y = int(center_y - self._hop_z * render_scale - icon.get_height() / 2)
        surface.blit(icon, (screen_x, screen_y))


def spawn_item_pickup(item_id, x, y):
    """Turn a dropped item_id into a single ItemPickup world object
    centered on (x, y) — the usual call site is the player's position at
    the moment the pause menu closes with a pending drop (see the item
    drop system comment block above)."""
    return ItemPickup(item_id, x, y)