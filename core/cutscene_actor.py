"""
core/cutscene_actor.py

Wraps a real game entity (Player, Enemy, BossEnemy) for use inside a cutscene.

AI and input are suppressed by simply not calling the entity's normal update() —
CutsceneActor.update() drives the sprite and any active tweens instead.
The entity draws itself normally, so every sprite/costume/transform works
without any extra rendering code.
"""

import math
import pygame
from config.settings import RENDER_SCALE


# Maps a scripted attack_type to the sprite animation state that plays it.
# Keys match the attack_type values exposed in the cutscene editor; extend
# this alongside _ACTION_PARAMS['attack'] in cutscene_editor.py whenever a
# new scripted attack type is added.
_ATTACK_ANIMATIONS = {
    'melee':    ('melee', 'melee'),
    'kiblast':  ('kiblast', 'kiblast'),
    'charge':   ('charge', 'charge'),
    'firebeam': ('firebeam', 'firebeam'),
    'kamehameha':          ('charge', 'firebeam'),
    'big_bang_kamehameha': ('charge', 'firebeam'),
    'flame_kamehameha':    ('charge', 'firebeam'),
    'final_flash': ('charge_final_flash', 'firebeam'),
    'genkidama':   ('charge_genkidama', 'firebeam'),
    'banshee_blast':   ('banshee_blast', 'banshee_blast'),
    'big_bang_attack': ('big_bang_attack', 'big_bang_attack'),
    'burning_attack': ('kiblast', 'kiblast_hold1'),
    'ultra_volleyball_attack': ('kiblast', 'kiblast'),
    'bullet':                  ('kiblast', 'kiblast'),
    'masenko': ('hold_masenko', 'idle'),
    'dragon_fist': ('dragon_fist', 'dragon_fist'),
    'ghost_kamikaze_attack': ('ghost_kamikaze_cast', 'ghost_kamikaze_hold'),
}


class AttackEffectVisual:
    """Standalone visual-only stand-in for a scripted attack's spawned
    effect — a separate object from the actor that cast it, the same way
    a real Projectile/BeamAttack is its own object rather than living on
    the Player. Exists purely so the cutscene editor/runtime can preview
    what an 'attack' action will look like, independent of on_spawn_attack
    (which still owns spawning the *real* gameplay object — see game.py's
    _cutscene_spawn_attack). Nothing here ever touches game state.

    Loaded straight off assets/sprites/attacks/{attack_id}/ — the same
    global roster character_creator.py's Attacks tab reads from (see its
    discover_attacks()) — so every attack any character can be equipped
    with is automatically previewable here with zero per-attack wiring.

    Sprite-sheet conventions (folder contents, all optional beyond having
    *some* main sheet):
      {attack_id}.png / attack.png / first *.png alphabetically
          Main sheet — a single row of frame_w x frame_h frames (16x16
          unless the folder has its own sprite_size.txt, same convention
          character_creator.py uses for attack icons). Looped while the
          effect is active. If a frame is wider than it is tall, it's
          treated as a "beam"-style effect: draw width is scaled from a
          stub up to the frame's full width over the effect's lifetime,
          so a beam visibly grows out from the caster instead of popping
          in at full length.
      hit.png / impact.png / collision.png
          Optional "collision" sprite — swapped to for a short beat once
          the effect's lifetime ends (i.e. whenever it would have hit
          something) if the folder has one, instead of just vanishing.
    """

    # attack_id -> {'main': [Surface,...], 'collision': Surface|None,
    #               'is_beam': bool}
    _cache: dict = {}

    _COLLISION_NAMES  = ('hit.png', 'impact.png', 'collision.png')
    _COLLISION_HOLD_S = 0.15   # how long the collision sprite is held before vanishing
    _ANIM_FPS         = 10.0

    def __init__(self, attack_id, x, y, direction='down', duration=0.5,
                 target_x=None, target_y=None):
        self.attack_id = attack_id
        self.x         = float(x)
        self.y         = float(y)
        self.direction = direction
        self.duration  = max(0.05, float(duration))
        self.target_x  = target_x
        self.target_y  = target_y
        self.elapsed   = 0.0
        self.finished  = False
        self._collision_elapsed = None   # None until the collision beat starts

        # Beam-style effects (see is_beam below) grow from a stub up to a
        # max width over `duration`. With no target_x/target_y that max is
        # just the sprite sheet's full frame width (old behaviour — grows
        # to whatever length the art was drawn at). With a target point,
        # the beam instead travels to (and holds at) that point, then
        # never grows further even if `duration` keeps running.
        #
        # Locked to the facing axis rather than aimed freely — a
        # 4-directional beam only ever travels straight along the
        # direction it's facing, so only the matching axis of the target
        # is used: vertical delta for up/down, horizontal delta for
        # left/right. Diagonal directions (flying-only in this game) fall
        # back to straight-line distance since there's no single axis to
        # lock to.
        self._max_width_px = None  # None = uncapped, grow to full sheet width
        if direction in ('up', 'down'):
            if target_y is not None:
                travel_distance = abs(float(target_y) - self.y)
                self._max_width_px = max(4.0, travel_distance * RENDER_SCALE)
        elif direction in ('left', 'right'):
            if target_x is not None:
                travel_distance = abs(float(target_x) - self.x)
                self._max_width_px = max(4.0, travel_distance * RENDER_SCALE)
        elif target_x is not None and target_y is not None:
            travel_distance = math.hypot(float(target_x) - self.x,
                                          float(target_y) - self.y)
            self._max_width_px = max(4.0, travel_distance * RENDER_SCALE)

        data = self._load(attack_id)
        self._frames           = data['main']
        self._collision_frame  = data['collision']
        self.is_beam           = data['is_beam']

    @classmethod
    def _load(cls, attack_id):
        """Load (and cache) the sprite data for *attack_id*. Mirrors the
        priority order character_creator.load_attack_icon() uses for its
        main-sheet fallback, minus the icon.png/HUD-icon steps (those are
        for the picker thumbnail, not the in-scene effect)."""
        if attack_id in cls._cache:
            return cls._cache[attack_id]

        import os, glob as _glob

        folder = os.path.join('assets', 'sprites', 'attacks', attack_id)
        frame_w = frame_h = 16
        size_path = os.path.join(folder, 'sprite_size.txt')
        if os.path.isfile(size_path):
            try:
                with open(size_path) as f:
                    w, h = f.read().strip().lower().split('x')
                frame_w, frame_h = int(w), int(h)
            except Exception:
                pass

        frames: list = []
        collision = None
        if os.path.isdir(folder):
            pngs = sorted(_glob.glob(os.path.join(folder, '*.png')))
            reserved = set(cls._COLLISION_NAMES) | {'icon.png'}
            main_candidates = (
                [p for p in pngs if os.path.basename(p) in (f'{attack_id}.png', 'attack.png')]
                + [p for p in pngs if os.path.basename(p) not in reserved]
            )
            for path in main_candidates:
                try:
                    sheet = pygame.image.load(path).convert_alpha()
                    fw = min(frame_w, sheet.get_width())
                    fh = min(frame_h, sheet.get_height())
                    if fw <= 0 or fh <= 0:
                        continue
                    frames = [
                        sheet.subsurface(pygame.Rect(i * fw, 0, fw, fh))
                        for i in range(sheet.get_width() // fw)
                    ]
                    break
                except Exception:
                    continue

            for name in cls._COLLISION_NAMES:
                cpath = os.path.join(folder, name)
                if os.path.isfile(cpath):
                    try:
                        collision = pygame.image.load(cpath).convert_alpha()
                    except Exception:
                        collision = None
                    break

        is_beam = bool(frames) and frames[0].get_width() > frames[0].get_height()
        data = {'main': frames, 'collision': collision, 'is_beam': is_beam}
        cls._cache[attack_id] = data
        return data

    def update(self, dt, world_width=None, world_height=None):
        self.elapsed += dt
        if self._collision_elapsed is not None:
            self._collision_elapsed += dt
            if self._collision_elapsed >= self._COLLISION_HOLD_S:
                self.finished = True
            return
        if self.elapsed >= self.duration:
            if self._collision_frame is not None:
                self._collision_elapsed = 0.0
            else:
                self.finished = True

    def draw(self, screen, camera, colors=None):
        sx = int(self.x * RENDER_SCALE - camera.x)
        sy = int(self.y * RENDER_SCALE - camera.y)

        if self._collision_elapsed is not None and self._collision_frame is not None:
            surf = self._collision_frame
            screen.blit(surf, (sx - surf.get_width() // 2, sy - surf.get_height() // 2))
            return

        if not self._frames:
            return

        frame_i = int(self.elapsed * self._ANIM_FPS) % len(self._frames)
        surf    = self._frames[frame_i]

        if self.is_beam:
            # Grow the beam's on-screen width from a stub up to its max
            # width over the action's duration — cheap, but reads as the
            # beam extending out from the caster rather than the whole
            # thing just appearing at full length. Max width is either the
            # full sheet (no target set) or the target-point distance
            # computed in __init__ — whichever's smaller, since the beam
            # can never visually exceed the art it was drawn with.
            growth  = min(1.0, self.elapsed / self.duration)
            full_w  = surf.get_width()
            if self._max_width_px is not None:
                full_w = min(full_w, int(self._max_width_px))
            w = max(2, int(full_w * growth))
            surf   = pygame.transform.scale(surf, (w, surf.get_height()))
            # Beams grow outward from the caster, so anchor on the caster
            # side rather than centring — centring would make a growing
            # beam expand in both directions at once. The art is drawn
            # facing right, so a left-facing beam needs both its anchor
            # edge flipped (grow from sx leftward, not rightward) and the
            # frame itself mirrored so its tip still reads as the leading
            # edge instead of the trailing one.
            if self.direction == 'left':
                surf = pygame.transform.flip(surf, True, False)
                screen.blit(surf, (sx - w, sy - surf.get_height() // 2))
            else:
                screen.blit(surf, (sx, sy - surf.get_height() // 2))
        else:
            screen.blit(surf, (sx - surf.get_width() // 2, sy - surf.get_height() // 2))


# ──────────────────────────────────────────────────────────────────────────
# Real-attack-class previews
# ──────────────────────────────────────────────────────────────────────────
# AttackEffectVisual above is a generic, attack-agnostic stand-in — it knows
# nothing about beam growth math, fixed segment chains, arcing throws, etc.,
# it just grows a single sprite's width if the frame happens to be wider
# than it is tall. The classes in attacks/*.py already implement all of
# that correctly (that's their whole job in real gameplay), and every one
# of them is already a self-contained x/y/direction object that updates and
# draws itself with no other game state required — so previewing with the
# REAL class instead of a generic placeholder is mostly just a matter of
# constructing it correctly and feeding its update() method whatever extra
# arguments it happens to want (world bounds, an anchor point, an enemy
# list, ...) beyond plain dt.
#
# create_attack_effect() is what CutsceneRuntime._fire_attack_effect calls;
# it tries the real class first and only falls back to AttackEffectVisual
# if attack_type isn't mapped below or construction/import fails for any
# reason (missing module, changed constructor, etc.) — same
# graceful-degradation philosophy every attacks/*.py file already uses for
# its own sprite loading.

# Unit vector per cardinal direction — used to synthesize a target point for
# attacks that need one but weren't given target_x/target_y (e.g. masenko
# aimed by whichever direction the actor is facing rather than by an
# explicit aim point).
_DIR_UNIT = {'up': (0, -1), 'down': (0, 1), 'left': (-1, 0), 'right': (1, 0)}


class _RealAttackEffect:
    """Adapter that lets a real attacks/*.py instance sit in
    CutsceneRuntime._attack_effects, which expects the AttackEffectVisual
    interface: .update(dt, world_width, world_height), .draw(screen, camera,
    colors), and a .finished flag.

    update_kind picks which extra arguments the wrapped object's own
    update() wants, since that's not consistent across attacks/*.py:
      'dt'      — update(dt) only (most BeamAttack-family beams, masenko,
                  energy-sword-free effects)
      'world'   — update(world_width, world_height, dt) (Projectile,
                  bullet_projectile, GenkidamaBlast, BigBangAttackBlast —
                  anything that deactivates by leaving the room)
      'anchor'  — update(dt, anchor_x, anchor_y) (DragonFistAttack, which
                  re-anchors its trailing chain to the caster every frame)
      'enemies' — update(dt, enemies) (GhostKamikazeAttack; no real enemy
                  list exists for a preview, so this always passes [])

    stop_method is the no-arg method to call once our own preview
    `duration` has elapsed and the object hasn't finished on its own (e.g.
    BeamAttack.start_decay, FlameKamehamehaAttack.stop,
    DragonFistAttack.start_retract) — None means just force
    `obj.active = False` directly. Either way, a hard `duration + 2.0`
    second cap forces the object inactive regardless, so a preview can
    never hang open forever if a stop path doesn't fully finish it off.
    """

    def __init__(self, obj, duration, update_kind='dt', stop_method=None,
                 anchor_entity=None, target_distance_px=None):
        self._obj = obj
        self._duration = max(0.05, float(duration))
        self._update_kind = update_kind
        self._stop_method = stop_method
        self._anchor_entity = anchor_entity
        self._elapsed = 0.0
        self._stop_requested = False
        # Screen-px distance (from the beam's own origin) at which growth
        # should be held, or None to leave the real class's own default
        # behaviour (e.g. plain kamehameha's max_length=inf — grows until
        # stop_method fires) untouched. Only meaningful for BeamAttack-
        # family objects, which expose report_obstruction(); anything else
        # (Projectile, MasenkoProjectile, DragonFistAttack, ...) just
        # ignores it via the hasattr guard in update() below.
        self._target_distance_px = target_distance_px

    @property
    def finished(self):
        return not getattr(self._obj, 'active', True)

    def update(self, dt, world_width=100000, world_height=100000):
        obj = self._obj
        self._elapsed += dt

        if not self._stop_requested and self._elapsed >= self._duration:
            self._stop_requested = True
            try:
                if self._stop_method and hasattr(obj, self._stop_method):
                    getattr(obj, self._stop_method)()
                else:
                    obj.active = False
            except Exception:
                obj.active = False

        if not getattr(obj, 'active', True):
            return

        # BeamAttack's report_obstruction() only holds for the frame it was
        # called on (see beam.py's update(): the reported distance is
        # consumed and reset to None every frame) — exactly like a real
        # wall/enemy has to keep reporting contact each frame it's still
        # blocking. So this needs to fire every frame the effect is alive,
        # not just once at construction, or the beam would grow unbounded
        # again the very next frame.
        if self._target_distance_px is not None and hasattr(obj, 'report_obstruction'):
            try:
                obj.report_obstruction(self._target_distance_px)
            except Exception as e:
                print(f"[cutscene attack DEBUG] report_obstruction() failed on "
                      f"{type(obj).__name__}: {e}")

        try:
            if self._update_kind == 'world':
                obj.update(world_width, world_height, dt)
            elif self._update_kind == 'anchor':
                # anchor_entity is always the caster's own entity (a
                # Player/Enemy/NPC/BossEnemy), which always has x/y — the
                # `obj` fallback below only exists for the pathological case
                # where anchor_entity is missing entirely. NOTE: this must
                # NOT be written as getattr(self._anchor_entity, 'x', obj.x)
                # — Python evaluates default arguments eagerly regardless of
                # whether the attribute lookup succeeds, so obj.x would be
                # accessed (and can raise) even when anchor_entity.x exists.
                # Several real attacks/*.py classes (e.g. DragonFistAttack)
                # don't expose plain .x/.y attributes at all, which is
                # exactly what was crashing here.
                anchor = self._anchor_entity
                if anchor is not None and hasattr(anchor, 'x') and hasattr(anchor, 'y'):
                    ax, ay = anchor.x, anchor.y
                else:
                    ax = getattr(obj, 'x', 0.0)
                    ay = getattr(obj, 'y', 0.0)
                obj.update(dt, ax, ay)
            elif self._update_kind == 'enemies':
                obj.update(dt, [])
            else:
                obj.update(dt)
        except Exception as e:
            # Any per-attack quirk we didn't anticipate ends the preview
            # instead of crashing the cutscene — same fail-safe spirit as
            # every attacks/*.py file's own try/except sprite loading.
            import traceback
            print(f"[cutscene attack DEBUG] update() failed on "
                  f"{type(obj).__name__} (update_kind={self._update_kind!r}): {e}")
            traceback.print_exc()
            obj.active = False
            return

        # Hard cap: never let a preview outlive its own duration by more
        # than a couple seconds, even if stop_method didn't fully close it.
        if self._elapsed >= self._duration + 2.0:
            obj.active = False

    def draw(self, screen, camera, colors=None):
        try:
            self._obj.draw(screen, camera, colors)
        except Exception as e:
            import traceback
            print(f"[cutscene attack DEBUG] draw() failed on "
                  f"{type(self._obj).__name__}: {e}")
            traceback.print_exc()


def _target_point(x, y, direction, target_x, target_y, distance=150.0):
    """(target_x, target_y) if both were given, else a point `distance`
    world units out from (x, y) along `direction` — used by attacks that
    need an aim point (masenko, ghost_kamikaze) but weren't scripted with
    an explicit target_x/target_y."""
    if target_x is not None and target_y is not None:
        return target_x, target_y
    ux, uy = _DIR_UNIT.get(direction, (0, 1))
    return x + ux * distance, y + uy * distance


def _beam_stop_distance_px(x, y, direction, target_x, target_y):
    """Travel distance from (x, y) to (target_x, target_y), locked to the
    facing axis — same convention AttackEffectVisual uses for its
    `_max_width_px` cap: only the vertical delta matters facing up/down,
    only the horizontal delta matters facing left/right, since a
    4-directional beam can't actually travel diagonally toward an
    off-axis point. Diagonal directions (flying-only) fall back to
    straight-line distance since there's no single axis to lock to.

    Returns None if the coordinate this direction actually needs wasn't
    given — deliberately does NOT require both target_x and target_y to
    be set, since the picker only ever writes the one axis relevant to
    the current direction. Requiring both meant a leftover/missing value
    in the *other* field (e.g. after changing direction after picking a
    target under a different one) silently disabled the cap entirely.

    Converted to screen px (world units * RENDER_SCALE) because that's
    the unit BeamAttack.report_obstruction() expects — it treats its
    `distance` argument as already-scaled screen space, the same way a
    wall or enemy's own reported obstruction distance would be.
    """
    if direction in ('up', 'down'):
        if target_y is None:
            return None
        travel = abs(float(target_y) - y)
    elif direction in ('left', 'right'):
        if target_x is None:
            return None
        travel = abs(float(target_x) - x)
    else:
        if target_x is None or target_y is None:
            return None
        travel = math.hypot(float(target_x) - x, float(target_y) - y)
    return travel * RENDER_SCALE


# Same per-direction spawn nudge entities.player.Player._get_spawn_offset()
# applies before constructing almost every real fired attack (see e.g.
# Player.fire_beam_auto(), fire_final_flash_auto(), release_genkidama(), ...
# in player.py) — a world-unit (x, y) offset from the caster's raw position
# so the effect originates from roughly where the character's hands are
# instead of dead-center on their body. It's duplicated here (rather than
# imported from entities.player) because it needs to apply even when
# `entity` isn't a real Player — a cutscene actor can be an enemy/NPC/boss
# too — and because entities.player already imports from this module's
# sibling attacks/*.py classes, so importing back from there risks a
# circular import.
#
# Skipping this offset entirely was what made every beam/blast-family
# attack in the cutscene editor/runtime spawn dead-center on the caster
# instead of offset out to the side/front the way real gameplay does —
# most visible on left/right, where the offset sits on the beam's own
# travel axis (12 world px) rather than just nudging it up/down.
_DIRECTION_SPAWN_OFFSETS = {
    'up':    (0,   -15),
    'down':  (0,    10),
    'left':  (-12,   4),
    'right': (12,    4),
}


def _spawn_offset(direction):
    return _DIRECTION_SPAWN_OFFSETS.get(direction, (0, 0))


def _build_real_attack_object(attack_type, x, y, direction, target_x, target_y, entity):
    """Construct the real attacks/*.py object for `attack_type`, plus the
    update_kind/stop_method _RealAttackEffect needs to drive it. Returns
    None if attack_type has no real-class mapping (melee/charge just play a
    pose with no spawned object, and a handful of attacks — energy_sword,
    instant_transmission — are too tightly coupled to a live Player to
    fake safely here) so the caller falls back to AttackEffectVisual.

    x/y here are still the caster's raw anchor position — each branch below
    applies whatever spawn offset (if any) that attack_type's real player.py
    fire_*_auto()/release_*() method applies before constructing its object,
    so the cutscene preview spawns from the same point real gameplay does.
    """
    if attack_type == 'kiblast':
        from attacks import Projectile
        ox, oy = _spawn_offset(direction)
        return Projectile(x + ox, y + oy, direction), 'world', None

    if attack_type == 'bullet':
        from attacks.bullet_projectile import bullet_projectile
        ux, uy = _DIR_UNIT.get(direction, (0, 1))
        return bullet_projectile(x, y, ux, uy, speed=220, damage=0,
                                  direction=direction), 'world', None

    if attack_type == 'kamehameha':
        from attacks.beam import BeamAttack
        ox, oy = _spawn_offset(direction)
        return BeamAttack(x + ox, y + oy, direction), 'dt', 'start_decay'

    if attack_type == 'banshee_blast':
        from attacks.banshee_blast import BansheeBlastAttack
        ox, oy = _spawn_offset(direction)
        return BansheeBlastAttack(x + ox, y + oy, direction), 'dt', 'start_decay'

    if attack_type == 'final_flash':
        from attacks.final_flash import FinalFlashAttack
        ox, oy = _spawn_offset(direction)
        return FinalFlashAttack(x + ox, y + oy, direction), 'dt', 'start_decay'

    if attack_type == 'big_bang_kamehameha':
        from attacks.big_bang_kamehameha import BigBangKamehamehaAttack
        ox, oy = _spawn_offset(direction)
        return BigBangKamehamehaAttack(x + ox, y + oy, direction), 'dt', 'start_decay'

    if attack_type == 'flame_kamehameha':
        from attacks.flame_kamehameha import FlameKamehamehaAttack
        ox, oy = _spawn_offset(direction)
        return FlameKamehamehaAttack(x + ox, y + oy, direction), 'dt', 'stop'

    if attack_type == 'ultra_volleyball_attack':
        from attacks.ultra_volleyball_attack import UltraVolleyballAttack
        # Real gameplay spawns this from get_blast_spawn_position() — the
        # same shared offset table as kiblast (see game.py's
        # pending_ultra_volleyball == 'ready' branch).
        ox, oy = _spawn_offset(direction)
        # Self-terminates at travel_distance — no stop_method needed.
        return UltraVolleyballAttack(x + ox, y + oy, direction), 'dt', None

    if attack_type == 'burning_attack':
        from attacks.burning_attack import BurningAttack
        ox, oy = _spawn_offset(direction)
        # Projectile subclass — same update(world_w, world_h, dt) contract.
        return BurningAttack(x + ox, y + oy, direction), 'world', None

    if attack_type == 'big_bang_attack':
        from attacks.big_bang_attack import BigBangAttackBlast
        ox, oy = _spawn_offset(direction)
        return BigBangAttackBlast(x + ox, y + oy, direction), 'world', None

    if attack_type == 'genkidama':
        from attacks.genkidama import GenkidamaBlast
        ox, oy = _spawn_offset(direction)
        return GenkidamaBlast(x + ox, y + oy, direction, state=3), 'world', None

    if attack_type == 'masenko':
        from attacks.masenko import MasenkoProjectile
        # Real gameplay spawns masenko from wherever its own hold-charge
        # overlay was sitting (see Player.release_masenko()), not the
        # shared _DIRECTION_SPAWN_OFFSETS table — there's no equivalent
        # hold-effect object tracked here, so this intentionally stays at
        # the raw caster position, same as before.
        tx, ty = _target_point(x, y, direction, target_x, target_y)
        return MasenkoProjectile(x, y, tx, ty, direction=direction), 'dt', None

    if attack_type == 'dragon_fist':
        from attacks.dragon_fist import DragonFistAttack
        # Real gameplay spawns this with no offset at all (see
        # Player._advance_dragon_fist_lunge()) — nothing to fix here.
        return DragonFistAttack(x, y, direction), 'anchor', 'start_retract'

    if attack_type == 'ghost_kamikaze_attack':
        from attacks.ghost_kamikaze_attack import GhostKamikazeAttack, get_ghost_kamikaze_spawn_offset
        # This attack uses its own tuned spawn offset, separate from the
        # shared table (see get_ghost_kamikaze_spawn_offset in
        # attacks/ghost_kamikaze_attack.py and the matching comment in
        # Player.start_ghost_kamikaze()).
        ox, oy = get_ghost_kamikaze_spawn_offset(direction)
        return GhostKamikazeAttack(x + ox, y + oy, direction), 'enemies', 'cancel'

    return None


def create_attack_effect(attack_type, x, y, direction='down', duration=0.5,
                          target_x=None, target_y=None, entity=None):
    """Build the best available preview effect for a scripted attack —
    the real attacks/*.py class if attack_type is mapped in
    _build_real_attack_object() and it constructs/updates without error,
    otherwise the generic AttackEffectVisual (also the only path for
    'melee'/'charge', which have no spawned object at all).

    target_x/target_y, when given, cap how far the beam-style effect grows
    before holding. For the AttackEffectVisual fallback this directly caps
    the sprite's draw width (see its own docstring). For real attacks/*.py
    classes, it works by re-reporting the target as an obstruction every
    frame via BeamAttack.report_obstruction() — the same mechanism a wall
    or enemy uses to stop a beam — so it only takes effect for BeamAttack-
    family attacks (kamehameha, banshee_blast, final_flash,
    big_bang_kamehameha, flame_kamehameha, ...). Attacks with their own
    aim point built in (masenko, ghost_kamikaze — see _target_point() in
    _build_real_attack_object) already use target_x/target_y directly and
    aren't affected by this. Attacks with no distance/aim concept at all
    (Projectile-family, DragonFistAttack, ...) just ignore it.
    """
    try:
        built = _build_real_attack_object(attack_type, x, y, direction,
                                           target_x, target_y, entity)
    except Exception as e:
        import traceback
        print(f"[cutscene attack DEBUG] '{attack_type}' construction failed, "
              f"falling back to AttackEffectVisual: {e}")
        traceback.print_exc()
        built = None

    if built is not None:
        obj, update_kind, stop_method = built
        # Only meaningful for BeamAttack-family classes (report_obstruction
        # is how they cap their own growth); harmless no-op otherwise since
        # _RealAttackEffect.update() checks hasattr(obj, 'report_obstruction')
        # before ever using it.
        target_distance_px = None
        if hasattr(obj, 'report_obstruction'):
            target_distance_px = _beam_stop_distance_px(x, y, direction, target_x, target_y)
        return _RealAttackEffect(obj, duration, update_kind=update_kind,
                                  stop_method=stop_method, anchor_entity=entity,
                                  target_distance_px=target_distance_px)

    return AttackEffectVisual(attack_type, x, y, direction=direction,
                               duration=duration,
                               target_x=target_x, target_y=target_y)


class _MoveTween:
    """Linear interpolation between two world positions."""

    def __init__(self, entity, target_x, target_y, duration):
        self.entity   = entity
        self.start_x  = float(entity.x)
        self.start_y  = float(entity.y)
        self.end_x    = float(target_x)
        self.end_y    = float(target_y)
        self.duration = float(duration)
        self.elapsed  = 0.0
        self.finished = False

    def update(self, dt):
        self.elapsed += dt
        t = min(1.0, self.elapsed / self.duration)
        self.entity.x = self.start_x + (self.end_x - self.start_x) * t
        self.entity.y = self.start_y + (self.end_y - self.start_y) * t
        if t >= 1.0:
            self.finished = True


class _FlyTween:
    """Arc-path movement for flying entities.

    World x/y move linearly from start to end (same as _MoveTween).
    A purely visual vertical offset — fly_offset_y in world units — follows a
    sine curve that peaks at arc_height at the midpoint and returns to zero on
    landing. The offset is applied during draw() so the shadow stays anchored
    at the true ground position throughout the flight.
    """

    def __init__(self, entity, target_x, target_y, duration, arc_height: float = 48.0):
        self.entity       = entity
        self.start_x      = float(entity.x)
        self.start_y      = float(entity.y)
        self.end_x        = float(target_x)
        self.end_y        = float(target_y)
        self.duration     = float(duration)
        self.arc_height   = float(arc_height)
        self.elapsed      = 0.0
        self.finished     = False
        self.fly_offset_y = 0.0  # current visual lift in world units

    def update(self, dt):
        self.elapsed += dt
        t = min(1.0, self.elapsed / self.duration)

        # Move the world position linearly — the shadow follows this.
        self.entity.x = self.start_x + (self.end_x - self.start_x) * t
        self.entity.y = self.start_y + (self.end_y - self.start_y) * t

        # Sine arc gives a smooth rise and fall over the full flight.
        self.fly_offset_y = self.arc_height * math.sin(t * math.pi)

        if t >= 1.0:
            self.fly_offset_y = 0.0
            self.finished     = True


# ──────────────────────────────────────────────────────────────────────────
# Charge-effect previews
# ──────────────────────────────────────────────────────────────────────────
# AttackEffectVisual/_RealAttackEffect above only ever cover what's spawned
# at RELEASE — the beam/projectile/blast itself. Up until now nothing
# covered the charge-UP phase: attack() below only ever set charge_anim on
# the actor's own sprite, never the separate player-anchored glow/particle
# object real gameplay layers on top of that pose (see e.g.
# Player.start_charging_beam() constructing a KamehamehaChargeEffect
# alongside the 'charge' animation). _build_charge_effect() is that missing
# piece — it mirrors _build_real_attack_object()'s per-attack_type mapping,
# just against the charge-effect classes instead of the fired ones.

def _build_charge_effect(attack_type, entity):
    """Construct the charge-up visual(s) attack_type shows in real gameplay
    while its pose is held, or None if it doesn't have one (melee/kiblast/
    dragon_fist/ultra_volleyball_attack/ghost_kamikaze_attack/bullet all
    fire or engage instantly — see their respective player.py start_*()
    methods never constructing a charge-effect object).

    Every real charge-effect class already only ever reads
    .x/.y/.direction/.height (and, for a couple, .width via getattr with a
    fallback) off its `player` argument (see e.g.
    KamehamehaChargeEffect.draw) — exactly what a CutsceneActor's wrapped
    entity already exposes — so these construct and drive identically
    whether `entity` is the real live Player or a cutscene actor's entity.

    Returns a list (masenko needs two simultaneous effects — the aim
    indicator and the hold overlay — everything else needs exactly one),
    or None on either "no charge effect for this attack_type" or a
    construction failure (missing asset, changed constructor, ...) — same
    graceful-degradation contract create_attack_effect() itself already
    uses for the release side.
    """
    try:
        if attack_type == 'kamehameha':
            from attacks.beam import KamehamehaChargeEffect
            return [KamehamehaChargeEffect(entity)]

        if attack_type == 'banshee_blast':
            from attacks.banshee_blast import BansheeBlastChargeEffect
            return [BansheeBlastChargeEffect(entity)]

        if attack_type == 'final_flash':
            from attacks.final_flash import FinalFlashChargeEffect
            return [FinalFlashChargeEffect(entity)]

        if attack_type == 'big_bang_kamehameha':
            from attacks.big_bang_kamehameha import BigBangKamehamehaChargeEffect
            return [BigBangKamehamehaChargeEffect(entity)]

        if attack_type == 'flame_kamehameha':
            from attacks.beam import KamehamehaChargeEffect
            return [KamehamehaChargeEffect(entity, attack_name='flame_kamehameha')]

        if attack_type == 'genkidama':
            from attacks.genkidama import GenkidamaChargeEffect
            return [GenkidamaChargeEffect(entity)]

        if attack_type == 'big_bang_attack':
            from attacks.big_bang_attack import BigBangAttackChargeEffect
            return [BigBangAttackChargeEffect(entity)]

        if attack_type == 'burning_attack':
            from attacks.burning_attack import BurningChargeEffect
            return [BurningChargeEffect(entity)]

        if attack_type == 'masenko':
            from attacks.masenko import MasenkoAimIndicator, MasenkoHoldEffect
            return [MasenkoAimIndicator(entity), MasenkoHoldEffect(entity, mode='hold')]

    except Exception as e:
        import traceback
        print(f"[cutscene attack DEBUG] charge effect for '{attack_type}' "
              f"failed to build: {e}")
        traceback.print_exc()
        return None

    return None


def create_charge_effect(attack_type, entity):
    """Public entry point — see _build_charge_effect() for the mapping.
    Always returns a list (possibly empty), never None, so callers can
    iterate it directly without an extra `or []`."""
    return _build_charge_effect(attack_type, entity) or []


class _AttackAction:
    """Drives a scripted attack: holds an attack pose for `duration` seconds
    and fires `on_release` exactly once when elapsed reaches `release_delay`.

    Splitting "how long the pose is held" from "when the effect actually
    spawns" lets a slow wind-up animation still release its projectile/hit
    partway through, with follow-through afterward — same idea as the real
    player's charge → release → recovery timing, just externally scripted.
    """

    def __init__(self, attack_type, duration, release_delay, on_release=None,
                 target_x=None, target_y=None):
        self.attack_type   = attack_type
        self.duration      = max(0.0, float(duration))
        self.release_delay = max(0.0, min(float(release_delay), self.duration))
        self.on_release    = on_release
        self.target_x      = target_x
        self.target_y      = target_y
        self.elapsed       = 0.0
        self.fired         = False
        self.finished       = False

    def update(self, dt):
        self.elapsed += dt
        if not self.fired and self.elapsed >= self.release_delay:
            self.fired = True
            if self.on_release:
                self.on_release(self)
        if self.elapsed >= self.duration:
            self.finished = True


class CutsceneActor:
    """Controls a game entity during a cutscene."""

    def __init__(self, actor_id, entity):
        self.actor_id    = actor_id
        self.entity      = entity
        self._tween      = None  # active _MoveTween or _FlyTween, or None
        self._charge_effects: list = []  # active charge-up visual(s) — see attack()
        self.show_shadow = True  # toggled by the 'set_shadow' action — see set_shadow_visible()

    # ── Position pass-throughs ────────────────────────────────────────────────

    @property
    def x(self):
        return self.entity.x

    @x.setter
    def x(self, value):
        self.entity.x = value

    @property
    def y(self):
        return self.entity.y

    @y.setter
    def y(self, value):
        self.entity.y = value

    @property
    def fly_offset_y(self) -> float:
        """Current visual lift in world units (0.0 when not flying)."""
        if isinstance(self._tween, _FlyTween):
            return self._tween.fly_offset_y
        return 0.0

    def set_shadow_visible(self, visible: bool):
        """Show or hide this actor's ground shadow.

        Driven by the 'set_shadow' cutscene action (see CutsceneRuntime._do_actor
        and the editor's _ACTOR_ACTIONS). Handy for actors floating, flying, or
        standing on something that shouldn't have a normal ground shadow.
        """
        self.show_shadow = bool(visible)

    # ── Animation & movement API ──────────────────────────────────────────────

    def set_animation(self, state, direction='down'):
        """Set animation state and facing direction immediately.

        If the sprite hasn't loaded this state yet (e.g. walk2.png added after
        init), we call load_animation_all_directions first so the key exists
        before set_animation looks it up.
        """
        self.entity.direction = direction
        sprite = getattr(self.entity, 'sprite', None)
        if sprite:
            key = f"{state}_{direction}"
            if key not in sprite.animations:
                self._hot_load_animation(sprite, state)
            sprite.set_animation(state, direction)
        if hasattr(self.entity, 'current_animation_state'):
            self.entity.current_animation_state = state

    def set_costume(self, costume: str):
        """Switch the actor to a different costume folder.

        Reloads the entity's sprite from
        assets/sprites/player/{character}/{costume}/ while preserving the
        current animation state and facing direction.
        """
        entity    = self.entity
        character = getattr(entity, 'character', None)
        if not character:
            return
        from core.sprite_system import create_character_sprite
        current_anim = getattr(entity, 'current_animation_state', 'idle')
        current_dir  = getattr(entity, 'direction', 'down')
        entity.sprite    = create_character_sprite(character, costume, 32, 32)
        entity.direction = current_dir
        self.set_animation(current_anim, current_dir)

    @staticmethod
    def _hot_load_animation(sprite, state):
        """Load all directions of *state* from {sprite.base_path}/{state}.png.

        Auto-detects 4-directional vs 8-directional by checking how many
        sprite-height rows the sheet has.
        """
        import os, pygame

        path = os.path.join(sprite.base_path, f"{state}.png")
        if not os.path.isfile(path):
            return

        # Detect direction count from the sheet's row count.
        try:
            tmp = pygame.image.load(path)
            rows = tmp.get_height() // sprite.sprite_height
        except Exception:
            return

        use_8 = (rows >= 8)
        sprite.load_animation_all_directions(
            state, frame_duration=0.1, loop=True,
            num_variants=1, use_8_directions=use_8,
        )

    def face(self, direction):
        """Change facing direction without restarting the current animation."""
        self.entity.direction = direction

    def move_to(self, target_x, target_y, duration=1.0, anim_state='walk', direction=None):
        """Tween entity to (target_x, target_y) over duration seconds.

        Auto-picks walk direction from the movement vector unless direction is
        specified explicitly. Pass duration <= 0 for an instant warp.
        """
        if duration <= 0:
            self.entity.x = float(target_x)
            self.entity.y = float(target_y)
            return

        # Derive direction from whichever axis has the larger delta.
        if direction is None:
            dx = target_x - self.entity.x
            dy = target_y - self.entity.y
            if abs(dx) >= abs(dy):
                direction = 'right' if dx >= 0 else 'left'
            else:
                direction = 'down' if dy >= 0 else 'up'

        self.set_animation(anim_state, direction)
        self._tween = _MoveTween(self.entity, target_x, target_y, duration)

    def teleport(self, x, y):
        """Instant position change with no animation change."""
        self.entity.x = float(x)
        self.entity.y = float(y)
        self._tween   = None

    @staticmethod
    def _fly_direction_from_vector(dx: float, dy: float) -> str:
        """Map a movement vector to an 8-way direction string.

        Uses the same atan2 angle logic as FlyingController._update_player_direction
        so cutscene fly directions match normal in-game flying exactly.
        """
        angle = math.degrees(math.atan2(dy, dx))
        if angle < 0:
            angle += 360

        if   angle >= 337.5 or angle < 22.5:  return 'right'
        elif 22.5  <= angle < 67.5:            return 'down_right'
        elif 67.5  <= angle < 112.5:           return 'down'
        elif 112.5 <= angle < 157.5:           return 'down_left'
        elif 157.5 <= angle < 202.5:           return 'left'
        elif 202.5 <= angle < 247.5:           return 'up_left'
        elif 247.5 <= angle < 292.5:           return 'up'
        else:                                  return 'up_right'

    def fly_to(self, target_x, target_y, duration=1.0,
               arc_height: float = 48.0, direction=None):
        """Fly entity to (target_x, target_y) over duration seconds.

        The sprite rises to arc_height world units at the midpoint and lands
        smoothly. The shadow stays on the ground the whole time. Pass
        duration <= 0 for an instant warp, or arc_height=0 for a flat glide.
        """
        if duration <= 0:
            self.entity.x = float(target_x)
            self.entity.y = float(target_y)
            return

        if direction is None:
            dx = target_x - self.entity.x
            dy = target_y - self.entity.y
            direction = self._fly_direction_from_vector(dx, dy)

        self.set_animation('flying', direction)
        self._tween = _FlyTween(self.entity, target_x, target_y, duration, arc_height)

    def attack(self, attack_type, direction=None, target_x=None, target_y=None,
               duration=0.6, release_delay=None, on_release=None):
        """Play a scripted attack: sets the matching animation state, holds it
        for `duration` seconds, and fires `on_release(action)` once elapsed
        reaches `release_delay` (defaults to firing partway through the pose,
        leaving room afterward for the release frame and the spawned effect
        to actually be visible — see the note below).

        This never spawns the actual projectile/hit itself — CutsceneActor
        has no access to the game's projectile/melee lists. `on_release` is
        the hook a caller (CutsceneRuntime) wires up to actually do that.
        `target_x`/`target_y` are just carried along on the action for that
        caller to use (e.g. aiming a blast at a point instead of a facing
        direction).

        IMPORTANT: release_delay must end up strictly less than `duration`.
        CutsceneRuntime._fire_attack_effect sizes the spawned effect's
        lifetime as (duration - release_delay); if the two are equal, the
        effect gets clamped to a bare 0.15s and dies before it can render
        (a beam-type effect never finishes opening). Defaulting release_delay
        to the full duration would do exactly that, so instead we default it
        to fire 2/3 of the way through the pose, guaranteeing the effect
        (and the release animation frame itself, which CutsceneActor.update()
        would otherwise overwrite with 'idle' on the very same tick it's set)
        gets real time on screen.
        """
        if direction is None:
            direction = self.entity.direction
        if release_delay is None:
            release_delay = duration * (2.0 / 3.0)

        charge_anim, release_anim = _ATTACK_ANIMATIONS.get(
            attack_type, ('melee', 'melee'))
        self.set_animation(charge_anim, direction)

        # Player-anchored charge-up glow/particle effect(s) real gameplay
        # shows alongside charge_anim above — see _build_charge_effect().
        # Cleared the instant release fires (below), mirroring how
        # player.py's own current_*_charge_effect gets set to None at the
        # exact moment its beam/blast/etc. actually spawns.
        self._charge_effects = create_charge_effect(attack_type, self.entity)

        def _on_release(action, _self=self, _release_anim=release_anim,
                         _direction=direction, _caller=on_release):
            _self.set_animation(_release_anim, _direction)
            _self._charge_effects = []
            if _caller:
                _caller(action)

        self._tween = _AttackAction(
            attack_type, duration, release_delay,
            on_release=_on_release, target_x=target_x, target_y=target_y,
        )

    @property
    def attack_target(self):
        """(target_x, target_y) of the in-progress attack, or (None, None)."""
        if isinstance(self._tween, _AttackAction):
            return self._tween.target_x, self._tween.target_y
        return None, None

    # ── Frame update ──────────────────────────────────────────────────────────

    def update(self, dt):
        # Keep the sprite animation ticking.
        if hasattr(self.entity, 'sprite') and self.entity.sprite:
            self.entity.sprite.update(dt)

        # Charge-effect previews tick independently of _tween's own update
        # below — they're cleared directly by attack()'s _on_release the
        # instant release fires (see attack()), not driven by
        # _AttackAction itself.
        for ce in self._charge_effects:
            try:
                ce.update(dt)
            except Exception as e:
                import traceback
                print(f"[cutscene attack DEBUG] charge effect update() "
                      f"failed on {type(ce).__name__}: {e}")
                traceback.print_exc()

        if not self._tween:
            return

        self._tween.update(dt)
        if not self._tween.finished:
            return

        # Tween finished — settle into idle.
        was_flying  = isinstance(self._tween, _FlyTween)
        self._tween = None

        landing_dir = self.entity.direction
        if was_flying:
            # Collapse diagonals to their nearest cardinal so idle looks natural
            # (mirrors FlyingController._complete_flight behaviour).
            if landing_dir in ('up_right', 'up_left'):
                landing_dir = 'up'
            elif landing_dir in ('down_right', 'down_left'):
                landing_dir = 'down'
            self.entity.direction = landing_dir

        self.set_animation('idle', landing_dir)

    # ── Rendering ─────────────────────────────────────────────────────────────

    # Shadow sprites are loaded once and shared across all CutsceneActor instances.
    # We keep our own cache here because cutscene actors bypass the LayerManager
    # entirely, so we can't rely on LayerManager's cache being available.
    _shadow_sprite     = None  # shadow.png    (small)
    _shadow_sprite_big = None  # shadowbig.png (big)
    _shadow_cache: dict = {}   # (shadow_w, big) -> scaled Surface

    @classmethod
    def _ensure_shadow_sprites(cls):
        """Load shadow sprites from disk exactly once; fall back to a plain ellipse."""
        if cls._shadow_sprite is not None:
            return

        # Small shadow
        try:
            cls._shadow_sprite = pygame.image.load(
                'assets/sprites/universal/shadow.png'
            ).convert_alpha()
        except Exception:
            s = pygame.Surface((32, 12), pygame.SRCALPHA)
            pygame.draw.ellipse(s, (0, 0, 0, 80), s.get_rect())
            cls._shadow_sprite = s

        # Big shadow (used by larger entities like bosses)
        try:
            cls._shadow_sprite_big = pygame.image.load(
                'assets/sprites/universal/shadowbig.png'
            ).convert_alpha()
        except Exception:
            s = pygame.Surface((64, 20), pygame.SRCALPHA)
            pygame.draw.ellipse(s, (0, 0, 0, 80), s.get_rect())
            cls._shadow_sprite_big = s

    @classmethod
    def _get_scaled_shadow(cls, shadow_w: int, big: bool = False):
        """Return a cached shadow surface scaled to the entity width.

        Uses the same 0.32 scale factor as LayerManager._get_scaled_shadow()
        so shadows look identical to normal gameplay.
        """
        source = cls._shadow_sprite_big if big else cls._shadow_sprite
        if source is None:
            return None

        key = (shadow_w, big)
        if key not in cls._shadow_cache:
            orig_w    = source.get_width()
            orig_h    = source.get_height()
            target_w  = max(8, int(shadow_w * RENDER_SCALE * 0.32))
            target_h  = max(4, int(orig_h * target_w / orig_w))
            cls._shadow_cache[key] = pygame.transform.scale(source, (target_w, target_h))

        return cls._shadow_cache[key]

    def _draw_shadow(self, screen, camera):
        """Draw the ground shadow, replicating LayerManager._draw_shadow() exactly.

        Entities don't draw their own shadows — that's normally handled by the
        LayerManager. Since cutscene actors bypass it, we reproduce the logic here.
        """
        if not self.show_shadow:
            return

        obj = self.entity

        # Only shadow entity types that the LayerManager would shadow.
        type_name = type(obj).__name__
        if not any(t in type_name for t in ('Player', 'Enemy', 'BossEnemy', 'NPC')):
            return

        self._ensure_shadow_sprites()

        use_big       = getattr(obj, 'shadow_size', 'small') == 'big'
        entity_height = getattr(obj, 'height', 32)
        shadow_w      = getattr(obj, 'shadow_width', getattr(obj, 'width', 32))

        shadow_surf = self._get_scaled_shadow(shadow_w, big=use_big)
        if shadow_surf is None:
            return

        # Position formula matches LayerManager._draw_shadow() exactly.
        feet_x  = (obj.x * RENDER_SCALE) - camera.x + 0.7
        feet_y  = (obj.y * RENDER_SCALE) - camera.y + (entity_height * RENDER_SCALE) // 2.25
        feet_y += getattr(obj, 'shadow_y_offset', 0)

        sx = int(feet_x - shadow_surf.get_width()  // 2)
        sy = int(feet_y - shadow_surf.get_height() // 2)
        screen.blit(shadow_surf, (sx, sy))

    # HUD helpers to suppress during a cutscene draw call.
    # Without this, health bars, aggro rings, name tags, etc. would bleed into
    # the cinematic view. Add new names here if new indicator helpers are added
    # to any entity class.
    _INDICATOR_METHODS = (
        '_draw_health_bar', 'draw_health_bar',
        '_draw_indicator',  'draw_indicator',
        '_draw_aggro',      'draw_aggro',
        '_draw_name_tag',   'draw_name_tag',
        '_draw_hud',        'draw_hud',
        '_draw_status_bar', 'draw_status_bar',
    )

    def draw(self, screen, camera, colors):
        """Draw the wrapped entity with cutscene-safe rendering.

        Steps:
          1. Draw the shadow at the true ground position (before any fly offset).
          2. Shift entity.y upward by the current arc lift so the sprite floats.
          3. Temporarily replace known HUD helpers with no-ops so game-state
             indicators (health bars, aggro rings, etc.) don't bleed into the scene.
          4. Call entity.draw() with in_cutscene=True so entities can branch on
             that flag themselves if needed.
          5. Restore entity.y and all suppressed methods.
        """
        entity = self.entity

        # 1. Shadow always stays at the real ground position, even during flight.
        self._draw_shadow(screen, camera)

        # 2. Lift the sprite for the fly arc (shadow above is already at ground).
        fly_off = self.fly_offset_y
        if fly_off:
            entity.y -= fly_off

        # 3. Swap out HUD helpers with no-ops for the duration of this draw call.
        _noop = lambda *a, **kw: None  # noqa: E731
        _restored: dict = {}
        for name in self._INDICATOR_METHODS:
            if name in entity.__dict__:
                # Instance-level override — save and replace.
                _restored[name] = ('instance', entity.__dict__[name])
                entity.__dict__[name] = _noop
            elif getattr(type(entity), name, None) is not None:
                # Class-level method — shadow it on the instance.
                _restored[name] = ('class', None)
                setattr(entity, name, _noop)

        # 4. Draw the entity itself.
        if hasattr(entity, 'draw'):
            entity.in_cutscene = True
            try:
                entity.draw(screen, camera, colors)
            finally:
                entity.in_cutscene = False

        # 5. Restore fly offset and all suppressed methods.
        if fly_off:
            entity.y += fly_off

        for name, (origin, original) in _restored.items():
            if origin == 'instance':
                entity.__dict__[name] = original
            else:
                # Remove the instance shadow so the class method is visible again.
                entity.__dict__.pop(name, None)

    def draw_charge_effects(self, screen, camera, colors, behind):
        """Draw this actor's active charge-effect preview(s) (see attack())
        that belong on the requested side of the actor sprite.

        Mirrors the front/behind-by-direction split LayerManager applies
        in real gameplay via each effect's own draw_layer (only 'up'-facing
        charges draw behind — see KamehamehaChargeEffect etc.) — resolved
        inline here since cutscene actors bypass LayerManager entirely
        (see draw()'s own docstring).
        """
        from core.draw_layers import DrawLayer
        for ce in self._charge_effects:
            if hasattr(ce, 'get_sort_key'):
                try:
                    ce.get_sort_key()  # some effects (masenko) only refresh
                                        # draw_layer here, not on direction change
                except Exception:
                    pass
            is_behind = getattr(ce, 'draw_layer', DrawLayer.EFFECTS_FRONT) == DrawLayer.EFFECTS_BEHIND
            if is_behind != behind:
                continue
            try:
                ce.draw(screen, camera, colors)
            except Exception as e:
                import traceback
                print(f"[cutscene attack DEBUG] charge effect draw() "
                      f"failed on {type(ce).__name__}: {e}")
                traceback.print_exc()