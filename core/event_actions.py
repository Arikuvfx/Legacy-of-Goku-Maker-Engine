"""
core/event_actions.py — the "what happens" half of the flag system.

flag_manager.py answers "did the thing happen / is the condition met".
This module answers "then run these actions" — an ordered list of small
serializable action dicts, executed by an EventRunner that Game owns.

Design
------
Each action is a plain dict: {'type': '<action_type>', ...params}. A
sequence is just a list of these, so it's trivial to save alongside a
Trigger Box / NPC / quest step / whatever fires it, and trivial for a
dev-tool editor to build with dropdowns + fields (same spirit as the
flag condition tree in flag_manager.py).

Handlers are registered by Game, not hard-coded here — this module has no
idea what your Player/DialogueBox/SoundManager classes look like, so it
just calls whatever callable you register for each action type:

    runner = EventRunner()
    runner.register_handler('play_sound', lambda sound_id: self.sound_manager.play_sfx(sound_id))
    runner.register_handler('add_zeni', lambda mode, amount: self.player.modify_zeni(mode, amount))
    ...
    runner.register_handler('dialogue_box', self._run_dialogue_action, blocking=True)
    runner.register_handler('play_cutscene', self._run_cutscene_action, blocking=True)

Blocking vs. non-blocking
--------------------------
Most actions (give item, screen shake, play sound, set stat...) fire and
the sequence moves straight to the next action the same frame.

Some actions take real time or need player input (open a dialogue box,
run a cutscene, fade the screen, start/stop a timer the sequence should
wait out) — register those with blocking=True. Their handler receives an
extra `on_complete` callback and MUST call it once the thing is actually
done (dialogue closed, cutscene finished, fade complete, timer ended),
which is what lets the sequence continue.

dialogue_choice is special-cased: its handler gets `options` (list of
{'text': str, 'actions': [...]}) and an `on_choice(index)` callback —
call it with whichever option the player picked, and the runner splices
that option's own action list in and keeps going. This is how branching
dialogue trees are built out of otherwise-linear sequences.

Usage
-----
    runner.run_sequence(actions, on_finished=lambda: print('done'))
"""


class _SequenceState:
    def __init__(self, actions, on_finished):
        self.actions = list(actions)
        self.index = 0
        self.on_finished = on_finished


class EventRunner:
    def __init__(self):
        self._handlers = {}         # action_type -> callable
        self._blocking_types = set()
        self._active_sequences = []

    # ── Registration ─────────────────────────────────────────────────────────

    def register_handler(self, action_type, handler, blocking=False):
        """handler signature:
             non-blocking: handler(**params)
             blocking:     handler(on_complete, **params)  — must call on_complete() when done
             dialogue_choice only: handler(prompt=None, options=[...], on_choice=callable)
        """
        self._handlers[action_type] = handler
        if blocking:
            self._blocking_types.add(action_type)

    # ── Running sequences ─────────────────────────────────────────────────────

    def run_sequence(self, actions, on_finished=None):
        """Start executing an action list. Runs synchronously through any
        non-blocking actions until it hits a blocking one (or the dialogue
        choice special case), then returns — the rest resumes later via the
        registered handlers' on_complete/on_choice callbacks."""
        state = _SequenceState(actions, on_finished)
        self._active_sequences.append(state)
        self._advance(state)

    def _advance(self, state):
        while state.index < len(state.actions):
            action = state.actions[state.index]
            state.index += 1
            action_type = action.get('type')

            if action_type == 'dialogue_choice':
                handler = self._handlers.get('dialogue_choice')
                if handler is None:
                    continue  # no dialogue system registered — skip the choice, keep going
                self._run_choice(handler, action, state)
                return

            handler = self._handlers.get(action_type)
            if handler is None:
                continue  # unregistered action type — no-op, don't crash the sequence

            params = {k: v for k, v in action.items() if k != 'type'}
            is_blocking = action.get('blocking', action_type in self._blocking_types)

            if is_blocking:
                def on_complete(state=state):
                    self._advance(state)
                handler(on_complete=on_complete, **params)
                return
            else:
                handler(**params)

        if state in self._active_sequences:
            self._active_sequences.remove(state)
        if state.on_finished:
            state.on_finished()

    def _run_choice(self, handler, action, state):
        def on_choice(index, action=action, state=state):
            options = action.get('options', [])
            if 0 <= index < len(options):
                branch = options[index].get('actions', [])
                state.actions[state.index:state.index] = branch
            self._advance(state)

        handler(prompt=action.get('prompt'), options=action.get('options', []), on_choice=on_choice)


# ─────────────────────────────────────────────────────────────────────────────
# Action builders — one function per action type from the spec. Purely
# convenience (they just build the dict) but keep param names consistent and
# give dev-tools/editors something concrete to introspect.
# ─────────────────────────────────────────────────────────────────────────────

def dialogue_box(speaker_type, text, speaker_name=None, portrait=None):
    """speaker_type: 'character' | 'narrator' | 'info'. portrait e.g. 'Goku_02'."""
    return {'type': 'dialogue_box', 'speaker_type': speaker_type, 'text': text,
            'speaker_name': speaker_name, 'portrait': portrait}


def set_portrait(character_name, portrait_id):
    return {'type': 'set_portrait', 'character_name': character_name, 'portrait_id': portrait_id}


def dialogue_choice(options, prompt=None):
    """options: list of {'text': str, 'actions': [...]} — actions run if that option is picked."""
    return {'type': 'dialogue_choice', 'options': options, 'prompt': prompt}


def timer_start(timer_id, duration):
    return {'type': 'timer_start', 'timer_id': timer_id, 'duration': duration}


def timer_pause(timer_id):
    return {'type': 'timer_pause', 'timer_id': timer_id}


def timer_stop(timer_id):
    return {'type': 'timer_stop', 'timer_id': timer_id}


def zeni(mode, amount):
    """mode: 'set' | 'add' | 'remove'."""
    return {'type': 'zeni', 'mode': mode, 'amount': amount}


def item(mode, item_id, quantity=1):
    """mode: 'add' | 'remove'."""
    return {'type': 'item', 'mode': mode, 'item_id': item_id, 'quantity': quantity}


def level(mode, amount, character_id=None):
    """mode: 'set' | 'add' | 'remove'."""
    return {'type': 'level', 'mode': mode, 'amount': amount, 'character_id': character_id}


def exp(mode, amount, character_id=None):
    return {'type': 'exp', 'mode': mode, 'amount': amount, 'character_id': character_id}


def stat(mode, stat_name, amount, character_id=None):
    return {'type': 'stat', 'mode': mode, 'stat_name': stat_name, 'amount': amount,
            'character_id': character_id}


def resource(mode, resource_name, amount):
    """resource_name: 'health' | 'energy' | 'transformation_gauge'."""
    return {'type': 'resource', 'mode': mode, 'resource_name': resource_name, 'amount': amount}


def skill(mode, skill_id):
    """mode: 'add' | 'remove'."""
    return {'type': 'skill', 'mode': mode, 'skill_id': skill_id}


def set_player_character(character_id, skin_id=None):
    return {'type': 'set_player_character', 'character_id': character_id, 'skin_id': skin_id}


def set_player_skin(skin_id):
    return {'type': 'set_player_skin', 'skin_id': skin_id}


def character_list(mode, character_id):
    """mode: 'add' | 'remove' — from the playable-character roster."""
    return {'type': 'character_list', 'mode': mode, 'character_id': character_id}


def screen_fade(direction, duration=0.5):
    """direction: 'in' | 'out'. Registered as blocking — waits for the fade
    to finish before the sequence continues."""
    return {'type': 'screen_fade', 'direction': direction, 'duration': duration}


def screen_shake(intensity, duration=0.3):
    return {'type': 'screen_shake', 'intensity': intensity, 'duration': duration}


def spam_qte(qte_id=None, fill_per_press=0.08, drain_rate=0.15, start_progress=0.0):
    """Bottom-middle mash-E-or-Q QTE bar (see ui/spam_qte.py). Blocking —
    the sequence resumes once the player has filled the bar to the right
    edge. fill_per_press/drain_rate/start_progress are all fractions of
    the full bar (0.0-1.0): fill_per_press is how much one E/Q press adds,
    drain_rate is how much the bar continuously loses per second, and
    start_progress is where the bar begins. qte_id is just a label for
    dev-tools/save data to key off of (e.g. flag conditions) — it plays
    no role in the fill logic itself."""
    return {'type': 'spam_qte', 'qte_id': qte_id, 'fill_per_press': fill_per_press,
            'drain_rate': drain_rate, 'start_progress': start_progress}


def weather(mode, weather_type=None):
    """mode: 'set' | 'stop'."""
    return {'type': 'weather', 'mode': mode, 'weather_type': weather_type}


def room_music(mode, track=None):
    """mode: 'set' | 'stop'."""
    return {'type': 'room_music', 'mode': mode, 'track': track}


def play_sound(sound_id):
    return {'type': 'play_sound', 'sound_id': sound_id}


def play_character_animation(character_id, animation_id, wait=False):
    """wait=True marks this call blocking — sequence pauses until the
    animation finishes."""
    return {'type': 'play_character_animation', 'character_id': character_id,
            'animation_id': animation_id, 'blocking': wait}


def save_game(save_id=None):
    return {'type': 'save_game', 'save_id': save_id}


def change_map(room_name, spawn_x=None, spawn_y=None, wait=True):
    return {'type': 'change_map', 'room_name': room_name, 'spawn_x': spawn_x,
            'spawn_y': spawn_y, 'blocking': wait}


def set_player_location(x, y, rotation=None):
    return {'type': 'set_player_location', 'x': x, 'y': y, 'rotation': rotation}


def spawn_enemies(enemies):
    """enemies: list of {'enemy_type': str, 'x': num, 'y': num}."""
    return {'type': 'spawn_enemies', 'enemies': enemies}


def spawn_npc(npc_id, x, y, animation=None):
    return {'type': 'spawn_npc', 'npc_id': npc_id, 'x': x, 'y': y, 'animation': animation}


def play_cutscene(cutscene_id):
    return {'type': 'play_cutscene', 'cutscene_id': cutscene_id}


def quest(mode, quest_id):
    """mode: 'add' | 'remove'."""
    return {'type': 'quest', 'mode': mode, 'quest_id': quest_id}


def modify_quest_variable(quest_id, variable_name, mode, value):
    """mode: 'set' | 'add' | 'remove'."""
    return {'type': 'modify_quest_variable', 'quest_id': quest_id,
            'variable_name': variable_name, 'mode': mode, 'value': value}


def world_map_location(mode, map_name, name):
    """mode: 'add' | 'remove' — show or hide a location pin that's already
    placed on this map in the World Map Editor (matched by its 'name'
    field, e.g. the pin's x/y/room/icon/height are whatever was set there).
    This only toggles visibility of an existing pin — it doesn't create or
    edit one."""
    return {'type': 'world_map_location', 'mode': mode, 'map_name': map_name, 'name': name}


def set_custom_variable(var_name, mode, value=None):
    """mode: 'set' | 'add' | 'remove'. Maps straight onto
    FlagManager.set_variable/add_variable/remove_variable — register this
    action type's handler to call straight through to those."""
    return {'type': 'set_custom_variable', 'var_name': var_name, 'mode': mode, 'value': value}


# Every action type name, for a dev-tool's action-type dropdown.
ACTION_TYPES = [
    'dialogue_box', 'set_portrait', 'dialogue_choice',
    'timer_start', 'timer_pause', 'timer_stop',
    'zeni', 'item', 'level', 'exp', 'stat', 'resource', 'skill',
    'set_player_character', 'set_player_skin', 'character_list',
    'screen_fade', 'screen_shake', 'spam_qte', 'weather', 'room_music', 'play_sound',
    'play_character_animation', 'save_game', 'change_map',
    'set_player_location', 'spawn_enemies', 'spawn_npc', 'play_cutscene',
    'quest', 'modify_quest_variable', 'set_custom_variable', 'world_map_location',
]