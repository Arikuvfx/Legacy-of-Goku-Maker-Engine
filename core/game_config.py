import random


class GameConfig:
    """Global configuration for game systems - editable via dev menu"""

    def __init__(self):
        # Level/XP System
        self.max_level = 99

        # XP-per-level curve. Fit directly against Buu's Fury's actual
        # "XP needed for next level" data — but only the readings that
        # landed almost exactly on a clean power-law regression (levels
        # 94-120, minus two that were clearly partial reads): those are
        # the readings taken right after leveling, so they're trustworthy.
        # Every OTHER reading (levels 60-90, plus the two skipped above)
        # is a partial/late read — screenshot taken after the player had
        # already earned some XP into that level — and was checked to sit
        # at or below this curve, which it does for all of them. That's
        # good confirmation the model is right.
        #
        # Fit: requirement(level) = 1.243115e-07 * level^5.7806
        # (residuals within ~2-3% across the 24 trustworthy points, and
        # level 99's fitted value of 42,705 lands almost exactly on the
        # real level-99 reading of 42,745).
        #
        # This is a pure power law with NO additive base — extrapolating
        # it down to low levels gives an essentially-zero requirement
        # (level 10 ~= 0.08 XP, level 20 ~= 4 XP, level 30 ~= 43 XP), which
        # is a legitimate consequence of the fit, not an error: a level^5.78
        # relationship is extremely compressed at the low end. That's also
        # why this curve is evaluated on RAW level rather than normalized
        # through min_level/max_level like HP/EP - it was fit on Buu's
        # Fury's own absolute level numbers, and reapplying it that way
        # means your level 99 costs what their level 99 actually cost,
        # rather than being rescaled to fit whatever max_level you set.
        #
        # base_xp_requirement/xp_floor_growth_per_level exist purely as a
        # playability floor for those early levels, since a level-up that
        # mathematically rounds to 0 XP would be an instant, unearned
        # level-up. The floor RAMPS LINEARLY (base + growth*(level-1))
        # rather than staying flat, for two reasons: a flat floor makes
        # every early level cost the exact same XP for ~29 levels (until
        # the real curve overtakes it), so with a flat floor sitting at
        # exactly one enemy's reward the player silently blows through
        # dozens of levels one kill apiece, and the HUD/pause menu just
        # keeps snapping back to the same "0 / floor" numbers every time —
        # looks frozen even though level-ups are actually firing constantly.
        # A gentle ramp means each floored level costs a bit more than the
        # last, so the XP bar visibly fills instead of resetting to an
        # identical number, and it can't degenerate into "floor == this
        # enemy's XP reward" for more than one level at a time.
        self.base_xp_requirement = 25         # floor at min_level: no level-up can cost less than this
        self.xp_floor_growth_per_level = 15   # each level's floor rises by this much over the last
        self.xp_curve_scale = 1.243115e-07
        self.xp_curve_exponent = 5.7806

        # Stats System
        self.max_stat_value = 99
        self.stat_points_per_level = 3
        self.starting_stat_value = 1

        # HP / EP growth curve — level-driven, replacing the old
        # stat-point-derived scaling (vitality/energy are free to be
        # repurposed for other bonuses now).
        #
        # Modeled on a power-law fit of Buu's Fury's actual HP/EP-per-level
        # data (levels 60-120, tight fit, confirmed against the level-99
        # data point too):
        #     HP(level) ~= 0.00034 * level^3.17
        #     EP(level) ~= 0.00130 * level^2.72
        # Extrapolating that fit to level 200 lands around 6700 HP / 2360 EP,
        # matching the observed "mid 7000s" ceiling for a maxed character.
        #
        # Because that exponent was fit against *absolute* levels in a
        # specific 60-200 game, it's re-applied here to *normalized*
        # progress through your own min_level..max_level range instead, so
        # the same acceleration shape holds whether your cap is 20 or 500:
        #   t = (level - min_level) / (max_level - min_level)
        #   value = base + (target - base) * t^exponent
        #
        # To mirror the original game's feel at your own max_level, a good
        # starting guess for target_hp/ep_at_max_level is the fitted curve
        # evaluated at your max_level, e.g. for max_level=99:
        #   HP: 0.00034 * 99**3.17  ~= 721
        #   EP: 0.00130 * 99**2.72  ~= 348
        # Defaults below are deliberately a bit higher than that literal
        # extrapolation (better UX for a shorter, 1-99 journey) — tune
        # freely via the dev menu.
        self.min_level = 1
        self.base_hp = 100
        self.base_ep = 100
        self.target_hp_at_max_level = 9999   # tune per max_level, see notes above
        self.target_ep_at_max_level = 9999
        self.hp_curve_exponent = 2.8         # >1 = back-loaded growth (matches observed acceleration)
        self.ep_curve_exponent = 2.3         # EP accelerates a bit less sharply than HP in the source data
        # How far into the curve your min_level should already sit, on the
        # same 0..1 t-scale _curve_value() uses. The original design had
        # players starting at (hardcoded) level 60 out of a 1-99 range,
        # which put t at (60-1)/(99-1) ≈ 0.6 — already well past the flat
        # early part of a t**2.8 curve, so growth felt substantial from the
        # very first level-up. Anchoring min_level at that same t means
        # whatever level you actually start players at gets that same
        # "already warmed up" feel, instead of always starting flat at t=0.
        # t still reaches 1.0 (== target_*_at_max_level) at max_level either way.
        self.curve_start_t = 0.6
        self.hp_variance = 0.10              # +/-10% random roll applied to each level's HP gain
        self.ep_variance = 0.10
        self.hp_ep_cap = 9999                # hard ceiling regardless of curve/roll
        # Safety-net floor so a level-up is never literally imperceptible —
        # expressed as a FRACTION of the curve's own uniform average
        # per-level rate ((target-base)/span), not a fixed HP/EP number.
        # A fixed number only worked for the max_level it was tuned at: the
        # curve's real per-level increment shrinks roughly as 1/span (same
        # total growth spread over more levels), so a fixed floor that once
        # sat comfortably below the curve can end up dominating it entirely
        # once max_level grows — exactly the bug that motivated this. Tying
        # it to the average rate instead means it scales down with the
        # curve automatically and (with a fraction < 1) essentially never
        # binds in practice; it's a floor against pathological edge cases,
        # not a growth mechanic in its own right.
        self.hp_min_gain_fraction = 0.3
        self.ep_min_gain_fraction = 0.3

        # -------------------------------------------------------------------
        # Melee Damage (STR vs. target END/defense)
        # -------------------------------------------------------------------
        # Fit directly against sampled Buu's Fury melee hits, all taken
        # against a fixed Enemy END (defense) of 20:
        #   STR 27 -> base 48-52 (avg 50),   crit 71-77  (avg 74)
        #   STR 30 -> base 61-66 (avg 63.5), crit 91-99  (avg 95)
        #   STR 50 -> base 217-238 (avg 227.5), crit 325-351 (avg 340.3)
        #   STR 75 -> base 666-731 (avg 698.5), crit 1003-1058 (avg 1033.5)
        #
        # base(STR) is a clean power law — log-log regression on the four
        # averages above gives scale=0.0100, exponent=2.577 almost exactly
        # (predicts 48.8 / 63.9 / 239.0 / 679.5 against the actual 50 /
        # 63.5 / 227.5 / 698.5 — all within the observed per-bracket spread).
        # Like the XP curve, this is evaluated on RAW strength rather than
        # normalized through min/max_stat_value, since it was fit on Buu's
        # Fury's own absolute STR numbers.
        #
        # crit/base ratio is consistent across every bracket (1.48, 1.496,
        # 1.496, 1.480) — a flat 1.5x crit multiplier is well supported.
        #
        # END was NOT varied in the sample (always 20), so there's no data
        # to fit a defense curve against — only this much is actually
        # measured: at END 20, base(STR) above is correct. Everything
        # about how OTHER END values scale damage is a design choice, not
        # a fit. The mitigation formula below is built to satisfy that one
        # constraint exactly (factor == 1.0 when target END == melee_
        # reference_end) while staying bounded in both directions: as
        # target END -> 0, factor -> 2.0 (never an unbounded multiplier),
        # and as target END -> inf, factor decays smoothly toward 0 rather
        # than going negative. melee_defense_curve_exponent just lets that
        # falloff be sharpened/softened later without touching the anchor.
        #
        # Per-hit variance (0.10) mirrors hp_variance/ep_variance and matches
        # the observed spread — e.g. STR 75 base spans 666-731, a ~9% swing
        # on a 698.5 average.
        #
        # crit_chance is NOT derivable from this data either (the samples
        # show what a crit looks like when one happens, not how often one
        # happens) — 0.15 is a starting design value, tune freely.
        self.str_curve_scale = 0.0100
        self.str_curve_exponent = 2.577

        # Low-STR floor — same problem/fix as base_xp_requirement /
        # xp_floor_growth_per_level above: the power curve is fit against
        # STR 27-75 and is essentially zero below that (STR 1 -> 0.01,
        # STR 7 -> 1.5), so without a floor every early STR point rounds
        # down to the same melee_min_damage and feels like it does nothing.
        # Ramps linearly from melee_base_floor at starting_stat_value, and
        # is designed to fall below the real curve well before STR 27 (the
        # lowest calibrated point) so it never distorts the fitted range —
        # crossover lands around STR 18-19 with these defaults.
        self.melee_base_floor = 1
        self.melee_base_floor_growth_per_str = 0.75

        self.melee_reference_end = 20          # the Enemy END the STR curve above was measured against
        self.melee_defense_curve_exponent = 1.0  # >1 = defense matters more; <1 = defense matters less
        self.melee_defense_factor_cap = 2.0    # hard ceiling on the low-END damage-boost side

        self.crit_damage_multiplier = 1.5
        self.crit_chance = 0.15                # design value — not derived from the sampled data

        self.melee_variance = 0.10             # +/-10% random roll applied to each hit, like hp/ep_variance
        self.melee_min_damage = 1              # floor so a hit can never round down to 0

        # -------------------------------------------------------------------
        # Ki Blast Super Attack Damage (POW vs. target END/defense)
        # -------------------------------------------------------------------
        # Same fitting approach as melee, against a separate sample set —
        # ki blast base damage at Enemy END 20:
        #   POW 27 -> 35-38 (avg 36.5)
        #   POW 30 -> 41-44 (avg 42.5)
        #   POW 50 -> 87-95 (avg 91.0)
        #   POW 75 -> 179-195 (avg 187.0)
        #
        # Log-log regression gives scale=0.1914, exponent=1.588 (predicts
        # 35.9 / 42.4 / 95.5 / 181.7 against the actual averages above —
        # all within the observed per-bracket spread). Notably gentler
        # than the melee exponent (2.577), so ki blasts scale with POW
        # more smoothly and don't need as aggressive a low-stat floor.
        #
        # No crit data was given for ki blasts (unlike melee, every sample
        # here is a single ungrouped range) — this system deliberately has
        # no crit chance/multiplier of its own. Reuse crit_damage_multiplier/
        # crit_chance above later if you want blasts to crit too.
        #
        # Variance is tighter than melee's — the observed spread is ~4-5%
        # of the average across every bracket (e.g. POW 75 spans 179-195,
        # ~4.3% of 187), vs. melee's ~10%.
        #
        # END was fixed at 20 for this sample too, same blind spot as
        # melee re: other END values — reuses the identical bounded
        # mitigation shape (factor 1.0 at the reference END, capped at 2x
        # on the low-END side) via its own independently-tunable fields,
        # in case you want blasts to be mitigated differently than melee.
        self.pow_curve_scale = 0.1914
        self.pow_curve_exponent = 1.588

        # Low-POW floor — same reasoning as melee_base_floor: the curve is
        # fit against POW 27-75 and gets small (not as extreme as melee's,
        # but POW 1 -> 0.19) below that range without a floor. Crosses
        # under the real curve by around POW 8-9 with these defaults —
        # much earlier than melee's, since this curve isn't as compressed.
        self.ki_blast_base_floor = 1
        self.ki_blast_base_floor_growth_per_pow = 0.5

        self.ki_blast_reference_end = 20              # Enemy END this curve was measured against
        self.ki_blast_defense_curve_exponent = 1.0
        self.ki_blast_defense_factor_cap = 2.0

        self.ki_blast_variance = 0.05          # tighter than melee_variance — see notes above
        self.ki_blast_min_damage = 1

        # Enemy XP Rewards
        self.basic_enemy_xp = 25
        self.strong_enemy_xp = 75
        self.boss_enemy_xp = 200

        #Transformatio Bar
        self.transformation_fill_time = 1.0  # Fill in 30 seconds instead of 60
        self.transformation_points_per_enemy = 0.05  # Need 20 enemies instead of 10
        self.transformation_fill_mode = 'time'  # or 'time'

    def get_xp_for_level(self, level):
        """XP required to advance from *level* to *level + 1*.

        Pure power curve (see the field comments above for the fit and
        why it deliberately has no additive base). max() against a
        linearly-ramping floor is just a playability guard for early
        levels the curve hasn't caught up to yet — both terms are
        individually non-decreasing in level, so the result is guaranteed
        monotonic without needing to look at neighboring levels at all.
        """
        level = max(1, level)
        raw   = self.xp_curve_scale * (level ** self.xp_curve_exponent)
        floor = self.base_xp_requirement + self.xp_floor_growth_per_level * (level - self.min_level)
        return int(max(raw, floor))

    # -------------------------------------------------------------------
    # HP / EP growth curve
    # -------------------------------------------------------------------

    def hp_curve_value(self, level):
        """Reference (un-rolled) max HP at *level* per the growth curve."""
        return self._curve_value(level, self.base_hp, self.target_hp_at_max_level, self.hp_curve_exponent)

    def ep_curve_value(self, level):
        """Reference (un-rolled) max EP at *level* per the growth curve."""
        return self._curve_value(level, self.base_ep, self.target_ep_at_max_level, self.ep_curve_exponent)

    def _curve_value(self, level, base, target, exponent):
        span = max(1, self.max_level - self.min_level)
        progress = (level - self.min_level) / span      # 0..1 raw progress through your level range
        progress = min(1.0, max(0.0, progress))
        # Anchor so min_level starts at curve_start_t instead of 0 — see the
        # comment on curve_start_t above for why.
        t = self.curve_start_t + (1.0 - self.curve_start_t) * progress
        return base + (target - base) * (t ** exponent)

    def hp_min_gain(self, base_hp=None):
        """Floor for a single level-up's HP gain — see hp_min_gain_fraction."""
        base = self.base_hp if base_hp is None else base_hp
        span = max(1, self.max_level - self.min_level)
        uniform_rate = (self.target_hp_at_max_level - base) / span
        return self.hp_min_gain_fraction * uniform_rate

    def ep_min_gain(self, base_ep=None):
        """Floor for a single level-up's EP gain — see ep_min_gain_fraction."""
        base = self.base_ep if base_ep is None else base_ep
        span = max(1, self.max_level - self.min_level)
        uniform_rate = (self.target_ep_at_max_level - base) / span
        return self.ep_min_gain_fraction * uniform_rate

    # -------------------------------------------------------------------
    # Melee damage
    # -------------------------------------------------------------------

    def melee_base_damage(self, strength):
        """Reference (un-rolled, pre-defense) melee power at *strength*.

        Pure power curve fit against Buu's Fury sample data — see the
        field comments above. Evaluated on raw strength, same reasoning
        as get_xp_for_level() using raw level. max() against a
        linearly-ramping floor guards the low-STR range the curve was
        never fit against (see melee_base_floor's comment) — both terms
        are non-decreasing in strength, so the result stays monotonic.
        """
        strength = max(1, strength)
        raw = self.str_curve_scale * (strength ** self.str_curve_exponent)
        floor = self.melee_base_floor + self.melee_base_floor_growth_per_str * (strength - self.starting_stat_value)
        return max(raw, floor)

    def melee_defense_factor(self, target_end):
        """Multiplier applied to melee_base_damage() for a defender with
        *target_end* (their END/defense stat). 1.0 at melee_reference_end
        (the value the STR curve was calibrated against), capped at
        melee_defense_factor_cap on the low-END side, and never negative.
        See the field comments above for why this shape was chosen.
        """
        target_end = max(0, target_end)
        ref = self.melee_reference_end
        raw = (2 * ref) / (ref + target_end) if (ref + target_end) > 0 else self.melee_defense_factor_cap
        raw = raw ** self.melee_defense_curve_exponent
        return min(self.melee_defense_factor_cap, max(0.0, raw))

    def roll_melee_damage(self, strength, target_end=0, force_crit=None, rng=random):
        """Roll one melee hit's final damage.

        Args:
            strength:   Attacker's STR stat.
            target_end: Defender's END/defense stat.
            force_crit: Pass True/False to force the crit roll's outcome
                        (e.g. for testing); leave None to roll normally
                        against crit_chance.
            rng:        Injectable random module/instance for testing.

        Returns (damage: int, is_crit: bool).
        """
        base = self.melee_base_damage(strength) * self.melee_defense_factor(target_end)

        is_crit = rng.random() < self.crit_chance if force_crit is None else force_crit
        if is_crit:
            base *= self.crit_damage_multiplier

        roll = rng.uniform(1 - self.melee_variance, 1 + self.melee_variance)
        damage = int(max(self.melee_min_damage, round(base * roll)))
        return damage, is_crit

    def apply_incoming_melee_mitigation(self, raw_damage, defender_end):
        """Reduce a flat incoming melee hit (e.g. an enemy's fixed
        attack_damage) by the defender's END, using the same mitigation
        curve roll_melee_damage() applies to the attacker's side.

        Unlike roll_melee_damage(), this has no STR/crit/variance to work
        with — the source is a flat design-set number, not something
        rolled off a STR curve — so it's just that number scaled by
        melee_defense_factor() and floored at melee_min_damage, same as
        any other melee hit.
        """
        mitigated = raw_damage * self.melee_defense_factor(defender_end)
        return int(max(self.melee_min_damage, round(mitigated)))

    # -------------------------------------------------------------------
    # Ki blast damage
    # -------------------------------------------------------------------

    def ki_blast_base_damage(self, pow_stat):
        """Reference (un-rolled, pre-defense) ki blast power at *pow_stat*.

        Same shape as melee_base_damage() — power curve fit + linear
        low-stat floor — just against the ki-blast sample data and its
        own POW curve fields. See the field comments above.
        """
        pow_stat = max(1, pow_stat)
        raw = self.pow_curve_scale * (pow_stat ** self.pow_curve_exponent)
        floor = self.ki_blast_base_floor + self.ki_blast_base_floor_growth_per_pow * (pow_stat - self.starting_stat_value)
        return max(raw, floor)

    def ki_blast_defense_factor(self, target_end):
        """Multiplier applied to ki_blast_base_damage() for a defender
        with *target_end*. Same bounded shape as melee_defense_factor(),
        just against ki_blast's own reference_end/exponent/cap fields so
        it can be tuned independently of melee.
        """
        target_end = max(0, target_end)
        ref = self.ki_blast_reference_end
        raw = (2 * ref) / (ref + target_end) if (ref + target_end) > 0 else self.ki_blast_defense_factor_cap
        raw = raw ** self.ki_blast_defense_curve_exponent
        return min(self.ki_blast_defense_factor_cap, max(0.0, raw))

    def roll_ki_blast_damage(self, pow_stat, target_end=0, rng=random):
        """Roll one ki blast hit's final damage.

        No crit involved — see the field comments above for why. Returns
        a plain int, unlike roll_melee_damage()'s (damage, is_crit) pair.
        """
        base = self.ki_blast_base_damage(pow_stat) * self.ki_blast_defense_factor(target_end)
        roll = rng.uniform(1 - self.ki_blast_variance, 1 + self.ki_blast_variance)
        return int(max(self.ki_blast_min_damage, round(base * roll)))