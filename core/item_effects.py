"""
Applies consumable item effects to the player and manages timed item buffs
(currently just Holy Water's +25 STR/POW/END/SPD for 30 seconds), plus
equip-slot items (currently just body armor) whose stat bonuses apply
for as long as the item stays worn.

Wiring into game.py:

    from data.items import ITEMS
    from systems.item_effects import use_item, tick_item_buffs, equip_item, unequip_item

    # __init__:
    self.active_item_buffs = []
    self.player.equipped = {}          # slot -> item_id
    self.player.equipped_bonuses = {}  # slot -> {stat_key: amount applied}

    # main update loop, once per frame:
    tick_item_buffs(self.player, self.active_item_buffs, dt)

    # wherever pause_menu.handle_input()'s result is handled:
    if result and result.startswith('use_item:'):
        item_id = result.split(':', 1)[1]
        outcome = use_item(self.player, item_id, self.active_item_buffs)
        self.pause_menu.flash_item_message(outcome.message)
    elif result and result.startswith('equip_item:'):
        item_id = result.split(':', 1)[1]
        outcome = equip_item(self.player, item_id)
        self.pause_menu.flash_item_message(outcome.message)
    elif result and result.startswith('unequip_item:'):
        slot = result.split(':', 1)[1]
        outcome = unequip_item(self.player, slot)
        self.pause_menu.flash_item_message(outcome.message)

Armband-type hand equipment (see data/items.py) carries an 'exp_bonus'
float (0.10 = +10%) instead of / alongside 'stats'. equip_item()/
unequip_item() below maintain the running total on player.equip_exp_bonus
as items are worn/removed, but nothing multiplies XP by it yet — wherever
XP is currently awarded needs one line added:

    xp_gained *= 1 + getattr(self.player, 'equip_exp_bonus', 0.0)
"""

from dataclasses import dataclass

from core.items import get_item

# Candidate attribute names for each buffable stat — mirrors
# PauseMenu._stat_key_for's fallback list so a buff lands on whichever key
# the player's stats dict actually uses (full name vs. short name).
_STAT_KEY_CANDIDATES = {
    'strength': ('strength', 'str'),
    'ki_power': ('ki_power', 'pow'),
    'vitality': ('vitality', 'end'),
    'speed':    ('speed', 'spd'),
}


@dataclass
class ItemUseResult:
    success: bool
    message: str


@dataclass
class EquipResult:
    success: bool
    message: str


def _resolve_stat_key(stats, stat_id):
    for key in _STAT_KEY_CANDIDATES.get(stat_id, (stat_id,)):
        if key in stats:
            return key
    return _STAT_KEY_CANDIDATES.get(stat_id, (stat_id,))[0]


def use_item(player, item_id, active_buffs):
    """
    Applies item_id's effect to player and removes one instance of it from
    player.inventory. active_buffs is the list passed to tick_item_buffs —
    buff-type items append their timers to it here.

    Returns an ItemUseResult. heal_hp / heal_ep / full_restore always
    consume the item, even if the relevant stat is already at max — the
    heal amount just has no further effect in that case. revive and buff
    still have their own preconditions (must be KO'd / conscious,
    respectively) that block the item from being consumed when unmet.
    """
    item = get_item(item_id)
    if item is None:
        return ItemUseResult(False, "Unknown item.")

    inventory = getattr(player, 'inventory', None)
    if not inventory or inventory.count(item_id) <= 0:
        return ItemUseResult(False, f"You don't have any {item['name']}.")

    hp     = getattr(player, 'hp', 0)
    max_hp = getattr(player, 'max_hp', 0)
    ep     = getattr(player, 'ki', 0)
    max_ep = getattr(player, 'max_ki', 0)
    is_ko  = hp <= 0

    effect = item.get('effect', {})
    etype  = effect.get('type')

    if etype == 'revive':
        if not is_ko:
            return ItemUseResult(False, f"{item['name']} can only be used when KO'd.")
        ratio = effect.get('hp_ratio', 0.5)
        player.hp = max(1, round(max_hp * ratio))

    elif is_ko:
        # Every other effect requires the character to be conscious.
        return ItemUseResult(False, "Can't use items while KO'd — try a Lazarus Crystal.")

    elif etype == 'heal_hp':
        # Always consumed, even at full HP — the heal just has no
        # further effect (min() clamps it to max_hp).
        player.hp = min(max_hp, hp + effect.get('amount', 0))

    elif etype == 'heal_ep':
        # Always consumed, even at full EP — same as heal_hp above.
        player.ki = min(max_ep, ep + effect.get('amount', 0))

    elif etype == 'full_restore':
        # Always consumed, even if HP and EP are already both full.
        player.hp = max_hp
        player.ki = max_ep

    elif etype == 'buff':
        stats = getattr(player, 'stats', None)
        if stats is None:
            return ItemUseResult(False, "Can't apply buff — no stats found.")
        duration = effect.get('duration', 0.0)
        for stat_id, amount in effect.get('stats', {}).items():
            key = _resolve_stat_key(stats, stat_id)
            stats[key] = stats.get(key, 0) + amount
            active_buffs.append({'stat_key': key, 'amount': amount, 'remaining': duration})

    else:
        return ItemUseResult(False, "This item can't be used right now.")

    inventory.remove(item_id)
    return ItemUseResult(True, f"Used {item['name']}! {item.get('effect_text', '')}")


def tick_item_buffs(player, active_buffs, dt):
    """Call once per frame. Counts down active item buffs and reverts each
    stat bonus the instant its timer runs out."""
    if not active_buffs:
        return
    stats = getattr(player, 'stats', None)
    still_active = []
    for buff in active_buffs:
        buff['remaining'] -= dt
        if buff['remaining'] <= 0:
            if stats is not None:
                key = buff['stat_key']
                stats[key] = max(0, stats.get(key, 0) - buff['amount'])
        else:
            still_active.append(buff)
    active_buffs[:] = still_active


def equip_item(player, item_id):
    """
    Equips item_id into its item['slot']. If something is already equipped
    in that slot, it's unequipped first (bonuses reverted) before the new
    item's bonuses are applied. Equip items are NOT removed from inventory —
    they're just "worn"; swapping between owned equip items is free.
    """
    item = get_item(item_id)
    if item is None:
        return EquipResult(False, "Unknown item.")

    slot = item.get('slot')
    if slot is None or item.get('effect', {}).get('type') != 'equip_stat':
        return EquipResult(False, f"{item['name']} can't be equipped.")

    inventory = getattr(player, 'inventory', None)
    if not inventory or inventory.count(item_id) <= 0:
        return EquipResult(False, f"You don't have any {item['name']}.")

    equipped = getattr(player, 'equipped', None)
    if equipped is None:
        equipped = player.equipped = {}
    bonuses = getattr(player, 'equipped_bonuses', None)
    if bonuses is None:
        bonuses = player.equipped_bonuses = {}

    if equipped.get(slot) == item_id:
        return EquipResult(False, f"{item['name']} is already equipped.")

    if slot in equipped:
        unequip_item(player, slot)

    stats = getattr(player, 'stats', None)
    if stats is None:
        return EquipResult(False, "Can't equip — no stats found.")

    applied = {}
    for stat_id, amount in item.get('effect', {}).get('stats', {}).items():
        key = _resolve_stat_key(stats, stat_id)
        stats[key] = stats.get(key, 0) + amount
        applied[key] = applied.get(key, 0) + amount

    exp_bonus = item.get('effect', {}).get('exp_bonus', 0.0)
    if exp_bonus:
        exp_bonuses = getattr(player, 'equipped_exp_bonuses', None)
        if exp_bonuses is None:
            exp_bonuses = player.equipped_exp_bonuses = {}
        exp_bonuses[slot] = exp_bonus
        player.equip_exp_bonus = getattr(player, 'equip_exp_bonus', 0.0) + exp_bonus

    equipped[slot] = item_id
    bonuses[slot] = applied
    return EquipResult(True, f"Equipped {item['name']}!")


def unequip_item(player, slot):
    """Reverts whatever's equipped in slot using the bonuses actually
    applied at equip time (not re-derived from the item), so it's correct
    even if the item's stat values change later in dev."""
    equipped = getattr(player, 'equipped', None)
    bonuses = getattr(player, 'equipped_bonuses', None)
    if not equipped or slot not in equipped:
        return EquipResult(False, "Nothing equipped in that slot.")

    item_id = equipped[slot]
    item = get_item(item_id)
    name = item['name'] if item else item_id

    stats = getattr(player, 'stats', None)
    applied = (bonuses or {}).get(slot, {})
    if stats is not None:
        for key, amount in applied.items():
            stats[key] = stats.get(key, 0) - amount

    exp_bonuses = getattr(player, 'equipped_exp_bonuses', None)
    if exp_bonuses and slot in exp_bonuses:
        player.equip_exp_bonus = getattr(player, 'equip_exp_bonus', 0.0) - exp_bonuses[slot]
        del exp_bonuses[slot]

    del equipped[slot]
    if bonuses is not None:
        bonuses.pop(slot, None)

    return EquipResult(True, f"Unequipped {name}.")