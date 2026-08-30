import pygame
import math


class TransformationSystem:
    """
    Manages the transformation bar and transformation state
    """

    def __init__(self, player, game_config):
        self.player = player
        self.game_config = game_config

        # Current transformation progress (0.0 to 1.0)
        self.progress = 0.0

        # Is transformation available?
        self.is_ready = False

        # Shine effect when ready
        self.shine_timer = 0.0
        self.shine_duration = 1.0
        self.is_shining = False

        # Has the player been notified?
        self.ready_notification_shown = False

        # Transformation state
        self.is_transformed = False
        self.is_transforming = False  # Currently playing transform animation
        self.is_untransforming = False  # Currently playing untransform animation

        # Transformation animation progress (0.0 to 1.0)
        self.transform_animation_progress = 0.0
        # Default duration in seconds — matches the real 'transform' sprite
        # animation length (measured ~3.5-4s). Used as the fallback whenever
        # a transformation doesn't have its own "Charge Duration" configured
        # on the Transformations tab (see _resolve_transform_charge_duration).
        # The actual value used for the active transform is resolved fresh
        # in start_transform() and stored back into this same attribute.
        self.DEFAULT_TRANSFORM_ANIMATION_DURATION = 3.75
        self.transform_animation_duration = self.DEFAULT_TRANSFORM_ANIMATION_DURATION

        # Tracks whether we've already told the sprite to release its
        # frame 3<->4 hold-loop for the current transform. Reset each time
        # a new transform starts (see start_transform/reset).
        self._hold_released = False

        # Transformed Ki (used while transformed)
        self.transformed_ki = 100.0
        self.max_transformed_ki = 100.0
        self.transformed_ki_drain_rate = 10.0  # Ki per second while transformed

        # Original sprite info (to restore after transformation)
        self.original_character = None
        self.original_costume = None

        # How many tiers deep the player currently is in a transformation
        # chain: 0 for a base-level form (e.g. SSJ), 1 for a form one
        # "requires" step up from base (e.g. SSJ3), and so on. Incremented
        # each time start_transform() advances a tier, reset to 0 whenever
        # a base-level form is (re)started or the player fully untransforms.
        # SpriteHUD uses this to stack one frozen, fully-lit ki-bar slot per
        # completed prior tier instead of reusing/overwriting the same bar
        # rect for every tier.
        self.tier_depth = 0

        # ki_color (see current_transform_ki_color above) captured for each
        # tier already climbed past this session, index-aligned with
        # tier_depth (frozen_tier_colors[i] is the color for slot i). Reset
        # alongside tier_depth.
        self.frozen_tier_colors = []

        # transformed_ki / max_transformed_ki fraction captured at the exact
        # moment each prior tier was left behind, index-aligned with
        # frozen_tier_colors. This is what lets SpriteHUD freeze that tier's
        # bar exactly where it actually was (e.g. partially drained from
        # combat) instead of snapping it to a misleading "full" the instant
        # the next tier starts charging.
        self.frozen_tier_fills = []

        # Sprite folder for the active transformation (resolved from character config)
        self.current_transform_costume = None

        # Folder form-name (e.g. "ssj", "ssj3") of the transformation tier the
        # player is CURRENTLY sitting in — None while untransformed. Derived
        # from current_transform_costume rather than stored independently
        # (see active_form_name), so it needs no extra save-file field and
        # can't drift out of sync with it.
        #
        # This is what a "tier 2" form's "requires" field (see
        # character_creator.py's Transformations tab) is compared against:
        # a form with requires == "ssj" can only be started while
        # active_form_name == "ssj", i.e. the player has already
        # transformed into SSJ and is choosing to advance from there,
        # rather than transform directly from base.

        # Custom ki-bar color for the active transformation, as a '#RRGGBB'
        # string — set on the Transformations tab (character_creator.py's
        # "Ki Bar Color" picker). None means this form has no override
        # configured, so SpriteHUD falls back to transformed_ki_bar.png's
        # baked-in colors untouched.
        self.current_transform_ki_color = None

        # Whether the active transformation shows/fills the transformed-ki
        # charge bar while is_transforming (see character_creator.py's
        # "Show Charge Bar" checkbox on the Transformations tab). True is
        # the historical/default behavior. When False, start_transform()
        # releases the sprite's held frames immediately instead of waiting
        # on a timer, so the transform animation just plays straight through
        # at its own natural pace and the player lands in the transformed
        # state the moment the animation finishes — no bar, no fixed charge
        # duration.
        self.current_transform_ki_bar_enabled = True

    def update(self, dt, enemies_defeated_this_frame=0):
        """
        Update transformation progress

        Args:
            dt: Delta time
            enemies_defeated_this_frame: Number of enemies defeated in this frame
        """
        # Update transformation animation progress
        if self.is_transforming:
            # When the charge bar is disabled for this transformation,
            # start_transform() already released the sprite's hold up front
            # (see there) so the animation just plays through on its own —
            # there's no timer to advance and nothing left to do here.
            # Player.update() calls complete_transform() once
            # sprite.is_animation_finished() reports the animation reached
            # its last frame naturally.
            if self.current_transform_ki_bar_enabled:
                self.transform_animation_progress += dt / self.transform_animation_duration
                self.transform_animation_progress = min(1.0, self.transform_animation_progress)

                # Once the charge timer completes, let the sprite's held
                # animation (looping frames 3<->4, see CharacterSpriteLoader)
                # continue on to its final frame and finish. Guarded so it only
                # fires once per transform — release_hold() itself is already
                # idempotent, but there's no reason to call it every frame.
                if self.transform_animation_progress >= 1.0 and not self._hold_released:
                    self._hold_released = True
                    self.player.sprite.release_hold('transform', 'down')

            # Don't fill regular progress during transformation
            return

        # Handle transformation state first
        if self.is_transformed:
            # Bar-disabled forms don't use the separate transformed-ki
            # resource at all — the player just plays on their normal ki
            # bar for the whole transformation, with nothing invisibly
            # ticking down in the background to force an untransform.
            if self.current_transform_ki_bar_enabled:
                # Drain transformed ki over time
                self.transformed_ki -= self.transformed_ki_drain_rate * dt

                # Check if transformation should end
                if self.transformed_ki <= 0:
                    self.transformed_ki = 0
                    self.start_untransform()

            # Don't fill progress bar while transformed
            return

        # Handle shine effect when ready
        if self.is_ready:
            if self.is_shining:
                self.shine_timer += dt
                if self.shine_timer >= self.shine_duration:
                    # Loop the shine effect
                    self.shine_timer = 0.0
            else:
                self.is_shining = True
            return

        # Fill based on configured mode
        if self.game_config.transformation_fill_mode == 'time':
            # Fill over time
            fill_rate = 1.0 / self.game_config.transformation_fill_time
            self.progress += fill_rate * dt

        elif self.game_config.transformation_fill_mode == 'combat':
            # Fill through defeating enemies
            if enemies_defeated_this_frame > 0:
                points_per_enemy = self.game_config.transformation_points_per_enemy
                progress_gain = enemies_defeated_this_frame * points_per_enemy
                self.progress += progress_gain

        # Clamp progress
        if self.progress >= 1.0:
            self.progress = 1.0
            self.is_ready = True
            self.is_shining = True
            self.shine_timer = 0.0

    def get_shine_alpha(self):
        """Get the alpha value for the shine effect (0-255)"""
        if not self.is_shining:
            return 0

        # Pulse effect: fade in and out continuously
        normalized_time = self.shine_timer / self.shine_duration

        # Create a smooth pulse using sine wave (continuous loop)
        pulse = math.sin(normalized_time * math.pi * 2)  # Full sine wave cycle

        # Map to 0-255 range with minimum brightness
        alpha = int(abs(pulse) * 150) + 105  # Range from 105 to 255
        return alpha

    @property
    def active_form_name(self):
        """Folder form-name (e.g. 'ssj') of the tier currently active, or
        None while untransformed. See the comment on current_transform_costume
        above — this is the identifier tier-gating ('requires') compares
        against."""
        if not self.is_transformed:
            return None
        return self._form_name_from_costume(self.current_transform_costume)

    @staticmethod
    def _form_name_from_costume(costume_path):
        """'{costume}/transformations/{form}' -> '{form}'. Mirrors
        Game._transformation_form_id() in game.py — keep the two in sync."""
        marker = '/transformations/'
        if not costume_path or marker not in costume_path:
            return None
        return costume_path.split(marker, 1)[1]

    def start_transform(self, form_name=None):
        """
        Called when player initiates transformation (X key).

        form_name: the specific transformation tier to start, by its folder
          form-name (e.g. "ssj", "ssj3") — the same identifier game.py's ki
          mode slots and "requires" gating use. None (the default) means
          "whichever form the active costume registers first", the
          historical single-tier behavior, for callers that don't target a
          specific tier.

        A form whose "requires" field names another form's form-name can
        only be started while active_form_name currently equals that
        required form — i.e. the player must already be sitting in the
        prerequisite tier. A form with no "requires" (a base-level form)
        can only be started from the untransformed state, gated by the
        usual charge/readiness meter (is_ready), exactly as before.

        Returns True if a transformation animation started.
        """
        if self.is_transforming or self.is_untransforming:
            return False

        # character lives on self.player (set in Player.__init__), not on
        # self.player.sprite — the sprite object never gets a .character
        # attribute, so this always silently missed and fell back to the
        # hardcoded 'goku' default, regardless of who was actually being
        # played. That's why transforming only ever worked for Goku:
        # every other character resolved _resolve_transform_costume()
        # against GOKU's config/costume instead of their own, found no
        # matching transformation, and start_transform() silently
        # returned False.
        character = getattr(self.player, 'character', 'goku')

        # Resolve the sprite folder for this transformation from the character
        # config BEFORE committing to any state change. This replaces the old
        # hardcoded 'ssj' costume string — SSJ is a transformation, not a
        # costume, and its folder is defined in the character's JSON under
        # "transformations[n].costume", scoped to whichever costume is
        # currently worn.
        transform_costume = self._resolve_transform_costume(character, form_name)
        if not transform_costume:
            # The active costume has no transformation registered for the
            # requested form — nothing to transform into. Bail out here
            # rather than starting the transform animation and having
            # nowhere to land (leaves the player stuck on a placeholder
            # sprite).
            return False

        target_form_name = form_name or self._form_name_from_costume(transform_costume)
        requires = self._resolve_transform_requires(character, transform_costume)

        if requires:
            # Advancing a tier (e.g. SSJ -> SSJ3): only allowed while the
            # player is already sitting in exactly the prerequisite form.
            # Note this deliberately does NOT check is_ready — that meter
            # is only for earning the very first (base-level) transform;
            # it's left False the moment that first transform starts and
            # TransformationSystem.update() never fills it again while
            # is_transformed, so it has nothing to do with tier-advances.
            if not self.is_transformed or self.active_form_name != requires:
                return False
            # Freeze the tier we're advancing FROM — both its color and its
            # actual current ki fraction, exactly as it stood the instant
            # we leave it — into its own backdrop slot (see SpriteHUD),
            # before current_transform_ki_color/transformed_ki get
            # overwritten below for the new tier. Capturing the real
            # fraction here (rather than always treating it as full) is
            # what keeps that backdrop bar frozen in place instead of
            # snapping to 100% the moment the next tier starts charging.
            self.frozen_tier_colors.append(self.current_transform_ki_color)
            self.frozen_tier_fills.append(
                self.transformed_ki / self.max_transformed_ki if self.max_transformed_ki > 0 else 1.0
            )
            self.tier_depth += 1
        else:
            # Base-level form (no prerequisite): only reachable from the
            # untransformed state, gated by the usual charge meter.
            if self.is_transformed or not self.is_ready:
                return False
            # Starting a fresh chain from base — clear any stacked slots
            # left over from a previous transformation this life.
            self.tier_depth = 0
            self.frozen_tier_colors = []
            self.frozen_tier_fills = []

        self.is_transforming = True
        self.is_ready = False
        self.transform_animation_progress = 0.0  # Reset animation progress
        self._hold_released = False  # Reset hold-release gate for this new transform

        # Store original (true base) sprite info — but only when starting
        # from the untransformed state. A tier-advance (requires is set)
        # runs this same function while is_transformed is already True;
        # skipping the capture there is what lets start_untransform() keep
        # reverting all the way back to the player's real base form/costume
        # even after climbing multiple tiers, instead of "reverting" to
        # whichever intermediate tier the player advanced from.
        if not self.is_transformed:
            # Always capture fresh here (not just the first time ever) —
            # this only runs when we're not currently transformed/
            # transforming, so there's no risk of clobbering in-progress
            # state, and the player's character/costume may have changed
            # (via character switch) since the last transform. A "set once"
            # guard here previously left this stuck on whichever character
            # the player FIRST transformed as for the rest of the session —
            # so switching characters and transforming again would revert
            # to the old character's sprite in complete_transform(), e.g.
            # transforming as Gohan showing up as Goku's SSJ sprite.
            self.original_character = character
            # costume lives on self.player (set in Player.__init__), not on
            # self.player.sprite — create_character_sprite()'s return value
            # never gets a .costume attribute. Reading it off .sprite always
            # missed and silently fell back to 'base', which is why
            # untransforming used to snap the player back to their base
            # costume even if they'd been wearing something else.
            self.original_costume = getattr(self.player, 'costume', 'base')

        self.current_transform_costume = transform_costume
        self.current_transform_ki_color = self._resolve_transform_ki_color(character, transform_costume)
        self.current_transform_ki_bar_enabled = self._resolve_transform_ki_bar_enabled(character, transform_costume)
        self.transform_animation_duration = self._resolve_transform_charge_duration(character, transform_costume)

        # Set transformation animation. self.player.sprite is whatever the
        # player currently looks like — the base sprite for a fresh
        # transform, or the PREVIOUS tier's transformed sprite when
        # advancing (e.g. the SSJ sprite when charging up to SSJ3) — so a
        # tier-advance plays whatever 'transform' animation is defined
        # under that tier's own sprite folder.
        self.player.sprite.set_animation('transform', self.player.direction)
        self.player.current_animation_state = 'transform'

        if not self.current_transform_ki_bar_enabled:
            # No charge bar for this form — skip the timed hold entirely
            # so the animation plays straight through at its own natural
            # pace instead of pausing on frames 2<->3 to wait on a timer.
            # complete_transform() then fires the instant the animation
            # finishes on its own (see Player.update()'s 'transform'
            # animation-state check), with no fixed duration involved.
            self._hold_released = True
            self.player.sprite.release_hold('transform', 'down')

        return True

    def complete_transform(self):
        """
        Called when transformation animation finishes
        Switches to transformed sprite sheets
        """
        if self.is_transforming:
            self.is_transforming = False
            self.is_transformed = True
            self.transformed_ki = self.max_transformed_ki
            self.transform_animation_progress = 1.0  # Ensure it's at 100%

            # Use the folder resolved in start_transform (e.g. "ssj", "ssj2") —
            # NOT a hardcoded string, because the transformation form is defined
            # in assets/characters/{id}.json, not hard-wired here.
            from core.sprite_system import create_character_sprite
            # self.original_character is always set by start_transform() before
            # this runs, so this is just a defensive fallback — but fixed to
            # read self.player.character (not self.player.sprite.character,
            # which doesn't exist) for the same reason as start_transform() above.
            character = self.original_character or getattr(self.player, 'character', 'goku')
            transform_costume = self.current_transform_costume or 'ssj'  # 'ssj' only as last-resort fallback

            self.player.sprite = create_character_sprite(character, transform_costume, 32, 32)
            self.player.sprite.set_animation('idle', self.player.direction)
            self.player.current_animation_state = 'idle'

            print(f"Transformation complete! Transformed Ki: {self.transformed_ki}")

    def start_untransform(self):
        """
        Called when transformed Ki reaches 0
        Starts untransform animation
        """
        if self.is_transformed and not self.is_untransforming:
            self.is_untransforming = True
            self.is_transformed = False

            # Cancel any in-progress attack. If a melee/blast was started in the
            # same frame that ki hits 0, start_untransform() will overwrite
            # current_animation_state before the melee branch can clear
            # is_attacking — leaving the player permanently frozen. Clearing it
            # here (and again in complete_untransform as a safety net) prevents that.
            self.player.is_attacking = False
            self.player.pending_blast = None

            # Set untransform animation
            self.player.sprite.set_animation('untransform', self.player.direction)
            self.player.current_animation_state = 'untransform'

    def complete_untransform(self):
        """
        Called when untransform animation finishes
        Reverts to base sprite sheets
        """
        if self.is_untransforming:
            self.is_untransforming = False

            # Safety net: ensure is_attacking is cleared even if start_untransform
            # interrupted a melee/blast mid-animation (would otherwise freeze movement).
            self.player.is_attacking = False
            self.player.pending_blast = None

            # Restore original sprite sheets
            from core.sprite_system import create_character_sprite
            character = self.original_character or 'goku'
            costume = self.original_costume or 'base'

            self.player.sprite = create_character_sprite(character, costume, 32, 32)
            self.player.sprite.set_animation('idle', self.player.direction)
            self.player.current_animation_state = 'idle'

            # Reset transformation progress
            self.progress = 0.0
            self.is_ready = False
            self.is_shining = False
            self.shine_timer = 0.0
            self.transform_animation_progress = 0.0

            # Full revert to base clears the whole tier stack, not just the
            # top of it — the next transform (from either slot) starts a
            # brand new chain.
            self.tier_depth = 0
            self.frozen_tier_colors = []
            self.frozen_tier_fills = []

            print("Reverted to base form")

    def can_player_act(self):
        """Check if player can perform actions during transformation states"""
        return not (self.is_transforming or self.is_untransforming)

    def reset(self):
        """Reset transformation progress"""
        self.progress = 0.0
        self.is_ready = False
        self.is_shining = False
        self.shine_timer = 0.0
        self.ready_notification_shown = False
        self.is_transformed = False
        self.is_transforming = False
        self.is_untransforming = False
        self.transformed_ki = self.max_transformed_ki
        self.transform_animation_progress = 0.0
        self.transform_animation_duration = self.DEFAULT_TRANSFORM_ANIMATION_DURATION
        self.current_transform_costume = None
        self.current_transform_ki_color = None
        self.current_transform_ki_bar_enabled = True
        self.tier_depth = 0
        self.frozen_tier_colors = []
        self.frozen_tier_fills = []
        self._hold_released = False

    def get_display_transform_costume(self, form_name=None):
        """Return the sprite-folder costume path (str) to use for HUD display
        purposes (e.g. the transform ki-mode icon).

        form_name: which specific tier's costume to preview (e.g. "ssj3") —
          pass this when the HUD is showing the icon for a particular
          'transform:<form_name>' ki-mode slot, so the icon reflects
          whichever slot the player has cycled to (SSJ vs SSJ3) even if
          they haven't pressed the transform key yet, rather than always
          showing whatever tier is currently active. Takes priority over
          current_transform_costume when given.

          None (default) keeps the old single-tier behavior: while
          actively transforming/transformed this is just
          current_transform_costume; otherwise there's nothing set on this
          instance yet, so fall back to resolving the costume's first
          registered form fresh — the same lookup start_transform() would
          do for the base 'transform' slot.
        """
        character = getattr(self.player, 'character', None)
        if not character:
            return None

        if form_name is not None:
            return self._resolve_transform_costume(character, form_name)

        if self.current_transform_costume:
            return self.current_transform_costume

        return self._resolve_transform_costume(character)

    def can_start_transform(self, form_name=None):
        """Read-only check mirroring start_transform()'s eligibility logic,
        with no side effects — used by the HUD to decide whether a
        transform ki-mode slot's icon should show as available (full
        opacity) or unavailable (dimmed) right now, without actually
        attempting the transform.
        """
        if self.is_transforming or self.is_untransforming:
            return False

        character = getattr(self.player, 'character', 'goku')
        transform_costume = self._resolve_transform_costume(character, form_name)
        if not transform_costume:
            return False

        requires = self._resolve_transform_requires(character, transform_costume)
        if requires:
            return self.is_transformed and self.active_form_name == requires
        return not self.is_transformed and self.is_ready

    def _resolve_transform_costume(self, char_id: str, form_name: str = None):
        """Return the costume path for the player's active transformation.

        form_name: if given (e.g. "ssj3"), resolve THAT specific tier's
          costume path instead of the costume-wide default — used when
          advancing to (or re-targeting) a particular transformation tier.
          Returns None if the active costume has no such form registered
          (no filesystem fallback in this case — an explicit form_name is
          only ever passed for a form the caller already knows is
          configured, via _transform_mode_slots() in game.py).

        Sourced from assets/characters/{char_id}.json — the same config
        character_creator.py writes via sync_transformations() — rather than
        re-scanning the sprite folders directly. This matters now that:
          1. Transformation sprites live nested under the base costume
             (assets/sprites/player/{char_id}/{base_costume}/transformations/{form}/),
             not directly under the character folder.
          2. A character can have more than one registered transformation
             (SSJ, SSJ2, ...), so "just list the folder and take an entry"
             no longer reliably picks the right one — os.listdir() sorts
             alphabetically, which has nothing to do with which
             transformation the creator tool actually registered first or
             which one the game intends to use.

        Currently always resolves to the FIRST entry in cfg["transformations"]
        (authoring order, not alphabetical) — there's no in-game selection
        between multiple forms yet. If/when the game adds a way to pick
        between SSJ/SSJ2/etc, this is the place to index into the list
        instead of always taking [0].
        """
        from dev_tools import character_creator

        cfg = character_creator.load_config(char_id)

        # Scope to the costume this character is CURRENTLY configured to
        # wear — the same cfg["costume"] field game.py's has_transformation
        # check uses to decide whether "transform" is even offered as a ki
        # mode. Previously this took transformations[0] unconditionally,
        # so a character with a transformation on one costume but not
        # another would resolve to whichever costume's transformation
        # happened to be registered first — not necessarily the one
        # actually being worn — pointing the sprite loader at frames that
        # don't exist for the active costume (the stuck purple placeholder
        # cube / freeze).
        # IMPORTANT: cfg["costume"] is a design-time field — whatever costume
        # was selected in the character creator's Identity tab when the JSON
        # was last saved. It does NOT track costume switches that happen at
        # runtime (see Game._handle_set_player_skin_action, which only sets
        # self.player.costume in memory and never writes back to disk). So
        # the live-equipped costume must take priority here, or a character
        # whose config default differs from what the player currently has
        # equipped will always transform into the CONFIG's costume's form
        # instead of the one actually being worn.
        active_costume = (getattr(self.player, 'costume', None)
                           or cfg.get("costume")
                           or "base")
        prefix = f"{active_costume}/transformations/"

        transformations = cfg.get("transformations", [])

        if form_name is not None:
            # A specific tier was requested — match it exactly rather than
            # just taking whatever's first. No filesystem fallback here:
            # the caller only passes an explicit form_name for a form it
            # already knows is registered (see _transform_mode_slots() in
            # game.py, which is built from this same cfg).
            target = f"{prefix}{form_name}"
            for t in transformations:
                if t.get("costume") == target:
                    return target
            return None

        for t in transformations:
            costume = t.get("costume")
            if costume and costume.startswith(prefix):
                return costume

        # Fallback: no transformation registered in the config for the
        # active costume (e.g. an old project whose sprites were never run
        # through sync_transformations()). Scan the filesystem directly as
        # a last resort, scoped the same way — this mirrors
        # discover_transformations()'s nested path.
        import os
        transforms_dir = f"assets/sprites/player/{char_id}/{active_costume}/transformations"
        if os.path.isdir(transforms_dir):
            entries = sorted(
                e for e in os.listdir(transforms_dir)
                if os.path.isdir(os.path.join(transforms_dir, e))
                and not e.startswith(".")
            )
            if entries:
                return f"{active_costume}/transformations/{entries[0]}"

        # Nothing found for the active costume — return None rather than
        # guessing a hardcoded folder name that may not exist for this
        # character. start_transform() treats this as "nothing to
        # transform into" and aborts instead of starting an animation with
        # nowhere to land.
        return None

    def _resolve_transform_ki_color(self, char_id: str, transform_costume: str):
        """Return the custom ki-bar color ('#RRGGBB') configured for this
        transformation on the Transformations tab, or None if the form
        hasn't been given one (SpriteHUD then falls back to
        transformed_ki_bar.png's baked-in colors, same as before this
        feature existed).

        Looked up the same way _resolve_transform_costume() finds the
        sprite folder — by matching `transform_costume` against each
        entry's "costume" field in assets/characters/{char_id}.json.
        """
        if not transform_costume:
            return None

        from dev_tools import character_creator

        cfg = character_creator.load_config(char_id)
        for t in cfg.get("transformations", []):
            if t.get("costume") == transform_costume:
                return t.get("ki_color")
        return None

    def _resolve_transform_ki_bar_enabled(self, char_id: str, transform_costume: str) -> bool:
        """Return whether the transformed-ki charge bar should be shown and
        filled while this transformation's animation plays, per the "Show
        Charge Bar" checkbox on the Transformations tab. Defaults to True
        (the historical behavior) so existing/unconfigured forms are
        unaffected.
        """
        if not transform_costume:
            return True

        from dev_tools import character_creator

        cfg = character_creator.load_config(char_id)
        for t in cfg.get("transformations", []):
            if t.get("costume") == transform_costume:
                return t.get("ki_bar_enabled", True)
        return True

    def _resolve_transform_charge_duration(self, char_id: str, transform_costume: str) -> float:
        """Return the charge-bar fill duration (seconds) configured for this
        transformation on the Transformations tab, or the class default if
        it hasn't been given one (None, missing, or <= 0 all fall back).

        Irrelevant when the charge bar is disabled for this form (see
        _resolve_transform_ki_bar_enabled) — start_transform() releases the
        sprite's hold immediately in that case, so no timer is used.
        """
        if transform_costume:
            from dev_tools import character_creator

            cfg = character_creator.load_config(char_id)
            for t in cfg.get("transformations", []):
                if t.get("costume") == transform_costume:
                    duration = t.get("charge_duration")
                    if duration and duration > 0:
                        return float(duration)
                    break

        return self.DEFAULT_TRANSFORM_ANIMATION_DURATION

    def _resolve_transform_requires(self, char_id: str, transform_costume: str):
        """Return the prerequisite form-name (e.g. "ssj") configured for
        this transformation on the Transformations tab's "Requires"
        picker, or None if it has no prerequisite (a base-level form,
        reachable directly from the untransformed state).

        Looked up the same way the other per-form resolvers above find
        their setting — by matching `transform_costume` against each
        entry's "costume" field in assets/characters/{char_id}.json.
        """
        if not transform_costume:
            return None

        from dev_tools import character_creator

        cfg = character_creator.load_config(char_id)
        for t in cfg.get("transformations", []):
            if t.get("costume") == transform_costume:
                return t.get("requires")
        return None

    def add_progress(self, amount):
        """Manually add progress (for special events, items, etc)"""
        if not self.is_ready and not self.is_transformed:
            self.progress = min(1.0, self.progress + amount)