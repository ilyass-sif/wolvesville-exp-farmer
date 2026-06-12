"""
inference.py — Bayesian Belief Network + CSP Solver for Wolvesville
====================================================================
Architecture:
  - WolvesvilleCSP:  Manages HARD constraints. Maintains a set of (slot, role)
                      pairs that are IMPOSSIBLE given known game facts.
  - WolvesvilleBBN:  Manages SOFT evidence. Stores likelihood weights for each
                      (slot, role) pair based on observed behavior.
  - GibbsSampler:    MCMC sampler that generates valid role assignments weighted
                      by BBN evidence, then marginalizes to get posteriors.
"""

import random
import math
import re
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Aura oracle: which aura does each role present?
# ---------------------------------------------------------------------------
ROLE_AURA: Dict[str, str] = {
    # EVIL
    "werewolf": "EVIL", "regular-werewolf": "EVIL", "junior-werewolf": "EVIL",
    "wolf-seer": "EVIL", "wolf-shaman": "EVIL", "shadow-wolf": "EVIL",
    "nightmare-wolf": "EVIL", "nightmare-werewolf": "EVIL", "kitten-wolf": "EVIL",
    "sorcerer": "GOOD", "storm-wolf": "EVIL", "werewolf-berserk": "EVIL",
    "wolf-summoner": "EVIL", "split-wolf": "EVIL", "alpha-werewolf": "UNKNOWN", # Alpha is UNKNOWN
    "guardian-wolf": "EVIL", "voodoo-wolf": "EVIL", "wolf-pacifist": "EVIL",
    "swamp-wolf": "EVIL", "blind-werewolf": "EVIL", "shadow-wolf": "EVIL", "wolffluencer": "EVIL",
    "ghost-wolf": "UNKNOWN",
    "jww": "EVIL",
    
    # GOOD
    "seer": "GOOD", "aura-seer": "GOOD", "doctor": "GOOD", "bodyguard": "GOOD",
    "tough-guy": "GOOD", "priest": "GOOD", "mayor": "GOOD", "villager": "GOOD",
    "regular-villager": "GOOD", "cursed": "GOOD", "cursed-human": "GOOD",
    "flower-child": "GOOD", "cupid": "GOOD", "loudmouth": "GOOD", "harlot": "GOOD",
    "red-lady": "GOOD", "ghost-lady": "GOOD", "beast-hunter": "GOOD",
    "night-watchman": "GOOD", "flagger": "GOOD", "trapper": "GOOD",
    "pacifist": "GOOD", "grumpy-grandma": "GOOD", "avenger": "GOOD",
    "analyst": "GOOD", "oracle": "GOOD", "spirit-seer": "GOOD",
    "detective": "GOOD", "medium": "UNKNOWN", "witch": "UNKNOWN",
    
    # UNKNOWN / SOLOS
    "jailer": "UNKNOWN", "warden": "UNKNOWN", "vigilante": "UNKNOWN", "gunner": "UNKNOWN",
    "serial-killer": "UNKNOWN", "arsonist": "UNKNOWN", "bomber": "UNKNOWN",
    "bandit": "UNKNOWN", "accomplice": "UNKNOWN", "sect-leader": "UNKNOWN",
    "fool": "UNKNOWN", "headhunter": "UNKNOWN", "illusionist": "UNKNOWN",
    "corruptor": "UNKNOWN", "zombie": "UNKNOWN", "alchemist": "UNKNOWN",
    "evil-detective": "UNKNOWN", "cannibal": "UNKNOWN", "astronomer": "UNKNOWN", "soulbinder": "UNKNOWN", "ferryman": "UNKNOWN",
    "shapeshifter": "UNKNOWN",
}


# ---------------------------------------------------------------------------
# Claim likelihood: P(player claims role R | player's true role is T)
# ---------------------------------------------------------------------------
# Rows = true role bucket, Cols = claimed role bucket
# "boring" = roles a wolf would bluff (Doc, Vill, etc.)
# "power"  = strong village roles that only villagers claim (Seer, BG, etc.)
# "wolf"   = wolf roles
# "solo"   = solo killer roles
#
# These are log-odds weights, not raw probabilities, and are deliberately
# asymmetric: a wolf is unlikely to claim Seer; a Seer is very likely to
# claim Seer once they share info.

CLAIM_LIKELIHOOD: Dict[str, Dict[str, float]] = {
    # Rows = true role bucket, Cols = claimed role bucket
    "village-boring": {
        "village-boring":       2.0,   # Very likely honest
        "village-investigator": 0.5,
        "village-verifiable":  -1.0,   # Boring roles rarely pretend to be gunners
        "wolf":                -4.0,
        "solo":                -3.0,
        "silent":               0.5,
    },
    "village-investigator": {
        "village-boring":       0.0,
        "village-investigator": 2.5,   # Strong claim to prove themselves
        "village-verifiable":  -2.0,
        "wolf":                -5.0,
        "solo":                -4.0,
        "silent":               0.3,
    },
    "village-verifiable": {
        "village-boring":       -1.0,
        "village-investigator": -1.0,
        "village-verifiable":    3.5,  # Very strong, hard to fake
        "wolf":                 -6.0,
        "solo":                 -5.0,
        "silent":                0.1,
    },
    "wolf": {
        "village-boring":       1.5,   # Most common wolf bluff
        "village-investigator": 1.2,   # Common fake seer bluff
        "village-verifiable":  -2.0,   # Risky bluff, rarely done
        "wolf":                 -6.0,
        "solo":                 -2.0,
        "silent":                1.0,
    },
    "solo": {
        "village-boring":       0.5,
        "village-investigator": 0.5,
        "village-verifiable":  -2.0,
        "wolf":                 -5.0,
        "solo":                 -3.0,
        "silent":                1.5,
    },
}

# Role bucket assignment
ROLE_BUCKET: Dict[str, str] = {
    "villager": "village-boring", "cursed": "village-boring",
    "regular-villager": "village-boring", "doctor": "village-boring",
    "bodyguard": "village-boring", "medium": "village-boring",
    "flower-child": "village-boring", "cupid": "village-boring",
    "loudmouth": "village-boring", "red-lady": "village-boring", "harlot": "village-boring",
    "ghost-lady": "village-boring", "night-watchman": "village-boring",
    "flagger": "village-boring", "trapper": "village-boring",
    "grumpy-grandma": "village-boring", "avenger": "village-boring",
    "priest": "village-boring", "tough-guy": "village-boring", "soulbinder": "village-boring", "ferryman": "village-boring",
    
    "seer": "village-investigator", "aura-seer": "village-investigator",
    "spirit-seer": "village-investigator", "detective": "village-investigator",
    "analyst": "village-investigator", "oracle": "village-investigator",
    "witch": "village-investigator", "astronomer": "village-investigator",

    "gunner": "village-verifiable", "vigilante": "village-verifiable",
    "mayor": "village-verifiable", "jailer": "village-verifiable", "warden": "village-verifiable",
    "beast-hunter": "village-verifiable", "pacifist": "village-verifiable",
    "marksman": "village-verifiable",
    
    # Wolves
    "werewolf": "wolf", "regular-werewolf": "wolf", "junior-werewolf": "wolf", "wolf-seer": "wolf",
    "wolf-shaman": "wolf", "shadow-wolf": "wolf", "nightmare-wolf": "wolf", "nightmare-werewolf": "wolf",
    "kitten-wolf": "wolf", "sorcerer": "wolf", "storm-wolf": "wolf",
    "werewolf-berserk": "wolf", "wolf-summoner": "wolf",
    "alpha-werewolf": "wolf", "split-wolf": "wolf",
    "swamp-wolf": "wolf", "blind-werewolf": "wolf", "ghost-wolf": "wolf", "wolffluencer": "wolf",
    "voodoo-wolf": "wolf", "guardian-wolf": "wolf", "wolf-pacifist": "wolf",
    # Solos
    "serial-killer": "solo", "arsonist": "solo", "bomber": "solo",
    "bandit": "solo", "accomplice": "solo", "sect-leader": "solo",
    "fool": "solo", "headhunter": "solo", "illusionist": "solo",
    "corruptor": "solo", "zombie": "solo", "alchemist": "solo",
    "evil-detective": "solo", "cannibal": "solo", "shapeshifter": "solo",
}

def get_role_team(role: str) -> str:
    """Returns a team identifier for the Detective's SAME/DIFFERENT logic."""
    bucket = ROLE_BUCKET.get(role.lower().replace(" ", "-"), "village-boring")
    if bucket.startswith("village"):
        return "village"
    if bucket == "wolf":
        return "wolves"
    # Every solo killer is their own unique team in Detective logic
    return f"solo-{role}"

CLAIMED_ROLE_BUCKET: Dict[str, str] = {
    "doctor": "village-boring", "bodyguard": "village-boring",
    "villager": "village-boring", "medium": "village-boring",
    "loudmouth": "village-boring", "flower-child": "village-boring",
    "night-watchman": "village-boring", "priest": "village-boring",
    
    "seer": "village-investigator", "aura-seer": "village-investigator",
    "spirit-seer": "village-investigator", "detective": "village-investigator",
    "analyst": "village-investigator", "oracle": "village-investigator",
    
    "gunner": "village-verifiable", "vigilante": "village-verifiable",
    "mayor": "village-verifiable", "jailer": "village-verifiable",
    "beast-hunter": "village-verifiable", "pacifist": "village-verifiable",
    "marksman": "village-verifiable", "avenger": "village-verifiable",
}


# ---------------------------------------------------------------------------
# CSP — Hard Constraints
# ---------------------------------------------------------------------------

class WolvesvilleCSP:
    """
    Maintains the hard constraints of the game.
    After any update, call `arc_consistent_roles(slot)` to get the set
    of roles that are still *possible* for a given slot.
    """

    def __init__(self):
        # role_name -> count still "available" in the game pool
        self.role_pool: Dict[str, int] = {}
        # slot -> frozenset of roles confirmed impossible for that slot
        self._impossible: Dict[int, Set[str]] = defaultdict(set)
        # slot -> confirmed role (if 1.0 certainty)
        self.confirmed: Dict[int, str] = {}
        # aura constraints: slot -> "GOOD" | "EVIL" | "UNKNOWN"
        self.aura_constraints: Dict[int, str] = {}
        # team constraints: list of dicts e.g. {"slot1": 8, "slot2": 10, "value": "SAME"}
        self.team_constraints: List[Dict] = []
        # all roles in universe (from game settings)
        self.all_roles: List[str] = []

    def reset(self, roles_in_game: List[str]):
        """Initialize from game-settings-changed payload."""
        self.role_pool = {}
        self._impossible = defaultdict(set)
        self.confirmed = {}
        self.aura_constraints.clear()
        self.team_constraints.clear()
        self.all_roles = []

        # Normalize role strings to lowercase-hyphenated
        for r in roles_in_game:
            if not isinstance(r, str):
                continue
            key = r.lower().replace(" ", "-")
            
            # Canonical mapping fixes
            if key == "regular-werewolf": key = "werewolf"
            if key == "regular-villager": key = "villager"
            
            self.role_pool[key] = self.role_pool.get(key, 0) + 1
            if key not in self.all_roles:
                self.all_roles.append(key)

    def sync_from_state(self, players: dict, roles: list, my_role: str, my_slot: int):
        """Wipe and rebuild all constraints from a GameState snapshot."""
        self.reset(roles)
        
        # 1. Sync teammates and revealed deaths
        for p_id, p in players.items():
            if p.grid_idx is not None:
                slot = p.grid_idx + 1
                if p.known_role:
                    self.confirm_role(slot, p.known_role)
                    
        # 2. Sync our own role (overrides any contradictions)
        if my_role and my_slot:
            self.confirm_role(my_slot, my_role)

    def confirm_role(self, slot: int, role: str):
        """A player's role has been revealed (death, teammate, etc.)."""
        role = role.lower().replace(" ", "-")
        
        if self.confirmed.get(slot) == role:
            return
            
        self.confirmed[slot] = role

        # Ensure the role exists in our universe
        if role not in self.all_roles:
            self.all_roles.append(role)
        if role not in self.role_pool:
            self.role_pool[role] = 1

        # Remove one token from the pool for this confirmed slot
        if self.role_pool[role] > 0:
            self.role_pool[role] -= 1

        # Mark every other role as impossible for this slot
        for r in self.all_roles:
            if r != role:
                self._impossible[slot].add(r)

    def apply_aura(self, slot: int, aura: str, strict: bool = True):
        """
        Apply an aura-seer / seer scan result.
        If strict=True (bot's own result), we match the exact aura.
        If strict=False (chat claim), 'GOOD' can also mean any Village-team role.
        """
        aura_val = aura.upper()
        self.aura_constraints[slot] = aura_val
        
        # Immediately make incompatible roles impossible
        for r in self.all_roles:
            role_aura = ROLE_AURA.get(r)
            role_bucket = ROLE_BUCKET.get(r, "")
            
            if not role_aura: continue

            if not strict and aura_val == "GOOD":
                # Loosened: GOOD can mean GOOD aura OR any Village role
                if role_aura != "GOOD" and not role_bucket.startswith("village"):
                    self._impossible[slot].add(r)
            else:
                # EVIL and UNKNOWN remain strict, as does bot's own GOOD scans
                if role_aura != aura_val:
                    self._impossible[slot].add(r)

    def apply_team_constraint(self, slot1: int, slot2: int, value: str):
        """Apply a team comparison constraint (SAME or DIFFERENT)."""
        self.team_constraints.append({"slot1": slot1, "slot2": slot2, "value": value})

    def mark_not_wolf(self, slot: int):
        """Mark all werewolf-team roles as impossible for this slot."""
        for r in self.all_roles:
            if ROLE_BUCKET.get(r) == "wolf":
                self._impossible[slot].add(r)

    def possible_roles(self, slot: int) -> List[str]:
        """Return roles still possible for this slot."""
        if slot in self.confirmed:
            return [self.confirmed[slot]]
        impossible = self._impossible[slot]
        return [r for r in self.all_roles if r not in impossible]

    def total_role_count(self, role: str) -> int:
        """Total instances of this role across the whole game (pool + confirmed)."""
        confirmed_count = sum(1 for r in self.confirmed.values() if r == role)
        return self.role_pool.get(role, 0) + confirmed_count

    def is_globally_valid(self, assignment: Dict[int, str]) -> bool:
        """
        Quick feasibility check: does this full assignment respect role counts?
        Does NOT re-check aura/confirm constraints (assumed pre-filtered).
        """
        counts: Dict[str, int] = defaultdict(int)
        for slot, role in assignment.items():
            # FINAL GUARD: Ensure impossible roles never get probability
            if role in self._impossible[slot]:
                continue
            counts[role] += 1

        for role in self.all_roles:
            total = self.total_role_count(role)
            if counts.get(role, 0) > total:
                return False
        return True


# ---------------------------------------------------------------------------
# BBN — Evidence / Likelihood Model
# ---------------------------------------------------------------------------

class WolvesvilleBBN:
    """
    Maintains soft evidence as log-likelihood weights per (slot, role) pair.
    Higher weight = more evidence pointing toward that role.
    """

    def __init__(self):
        # slot -> role -> accumulated log-likelihood
        self._log_weights: Dict[int, Dict[str, float]] = defaultdict(lambda: defaultdict(float))

    def reset(self):
        self._log_weights.clear()

    def add_claim(self, slot: int, claimed_role_text: str, all_roles: List[str]):
        """
        A player has verbally claimed a role.
        Update likelihoods: P(this claim | true_role) for each candidate role.
        """
        claim_bucket = CLAIMED_ROLE_BUCKET.get(claimed_role_text.lower(), "village-boring")

        for candidate_role in all_roles:
            true_bucket = ROLE_BUCKET.get(candidate_role, "village-boring")
            weight = CLAIM_LIKELIHOOD.get(true_bucket, {}).get(claim_bucket, 0.0)
            
            # Specific Match Bonus: If they claim the EXACT role, give a massive boost
            # This distinguishes Seer from Aura Seer within the same investigator bucket
            if candidate_role == claimed_role_text.lower().replace(" ", "-"):
                weight += 4.0
                
            self._log_weights[slot][candidate_role] += weight

    def add_vote_against(self, voter_slot: int, target_slot: int, all_roles: List[str]):
        """
        A player voted against another player. Wolves tend to vote
        against strong village roles; villagers tend to vote against wolves.
        This gives very weak signal but is useful cumulatively.
        """
        # Slight evidence the voter is NOT a villager voting randomly
        for role in all_roles:
            bucket = ROLE_BUCKET.get(role, "village-boring")
            if bucket == "wolf":
                self._log_weights[voter_slot][role] += 0.05  # wolves vote strategically
            elif bucket == "village-boring":
                self._log_weights[voter_slot][role] -= 0.03

    def add_silent_observation(self, slot: int, all_roles: List[str]):
        """Player has been suspiciously quiet — slight wolf/solo signal."""
        for role in all_roles:
            bucket = ROLE_BUCKET.get(role, "village-boring")
            if bucket in ("wolf", "solo"):
                self._log_weights[slot][role] += 0.2
            else:
                self._log_weights[slot][role] -= 0.1

    def add_caught_lying(self, slot: int, all_roles: List[str]):
        """Player was caught mathematically lying (e.g., aura contradicts confirmed CSP)."""
        for role in all_roles:
            bucket = ROLE_BUCKET.get(role, "village-boring")
            if "village" in bucket:
                self._log_weights[slot][role] -= 10.0  # Impossible for good roles to lie maliciously
            elif bucket == "wolf":
                self._log_weights[slot][role] += 1.0
            elif bucket == "solo":
                self._log_weights[slot][role] += 2.0  # Fools / Solos lie frequently

    def log_weight(self, slot: int, role: str) -> float:
        """Return the accumulated log-likelihood for (slot, role)."""
        return self._log_weights[slot].get(role, 0.0)

    def unnormalized_prob(self, slot: int, role: str) -> float:
        """Convert log-weight to linear scale."""
        return math.exp(self.log_weight(slot, role))


# ---------------------------------------------------------------------------
# Gibbs Sampler
# ---------------------------------------------------------------------------

class GibbsSampler:
    """
    Swap-based MCMC sampler for Wolvesville role inference.

    Uses swap moves: pick two unconfirmed slots, swap their roles if
    CSP-valid, accept/reject based on BBN likelihood ratio.
    This avoids the degenerate behavior of single-slot Gibbs sampling
    when most roles have count=1.
    """

    def __init__(self, csp: WolvesvilleCSP, bbn: WolvesvilleBBN, n_samples: int = 600, burn_in: int = 100):
        self.csp = csp
        self.bbn = bbn
        self.n_samples = n_samples
        self.burn_in = burn_in

    def _initial_assignment(self, active_slots: List[int]) -> Dict[int, str]:
        """
        Build a random starting assignment that respects CSP constraints.
        """
        for _ in range(100):
            assignment: Dict[int, str] = {}

            # Lock in confirmed slots
            for slot, role in self.csp.confirmed.items():
                if slot in active_slots:
                    assignment[slot] = role

            # Build available pool for unconfirmed slots
            unconfirmed = [s for s in active_slots if s not in assignment]
            available_roles: List[str] = []
            for role, count in self.csp.role_pool.items():
                available_roles.extend([role] * count)
            random.shuffle(available_roles)

            # Greedy assignment: try to assign each slot a valid role
            assigned_roles: List[str] = []
            for slot in unconfirmed:
                possible = set(self.csp.possible_roles(slot))
                chosen = None
                for r in available_roles:
                    if r in possible and r not in assigned_roles:
                        # Check if we haven't exceeded this role's pool count
                        used = assigned_roles.count(r)
                        if used < self.csp.role_pool.get(r, 0):
                            chosen = r
                            break
                if chosen is None:
                    # Fallback: pick any possible role
                    for r in available_roles:
                        if r in possible:
                            chosen = r
                            break
                if chosen is None:
                    chosen = available_roles[0] if available_roles else "villager"
                assignment[slot] = chosen
                assigned_roles.append(chosen)
                if chosen in available_roles:
                    available_roles.remove(chosen)

            if self._is_team_valid(assignment):
                break

        return assignment

    def _is_team_valid(self, assignment: Dict[int, str]) -> bool:
        """Check if assignment violates any team constraints (Detective/Medium)."""
        for tc in self.csp.team_constraints:
            s1, s2, val = tc["slot1"], tc["slot2"], tc["value"]
            if s1 in assignment and s2 in assignment:
                team1 = get_role_team(assignment[s1])
                team2 = get_role_team(assignment[s2])
                
                if val == "SAME":
                    if team1 != team2: return False
                else: # DIFFERENT
                    if team1 == team2: return False
                    
        return True

    def _score(self, assignment: Dict[int, str], slots: List[int]) -> float:
        """Sum of log-weights for the given slots in the assignment."""
        total = 0.0
        for s in slots:
            total += self.bbn.log_weight(s, assignment[s])
        return total

    def run(self, active_slots: List[int]) -> Dict[int, Dict[str, float]]:
        """
        Run the swap-based MCMC sampler and return posterior marginals.
        """
        if not active_slots or not self.csp.all_roles:
            return {}

        assignment = self._initial_assignment(active_slots)
        unconfirmed = [s for s in active_slots if s not in self.csp.confirmed]

        # Frequency counters for marginals
        counts: Dict[int, Dict[str, int]] = {s: defaultdict(int) for s in active_slots}
        total_samples = 0

        for iteration in range(self.n_samples + self.burn_in):
            if len(unconfirmed) >= 2:
                # Pick two random unconfirmed slots
                s1, s2 = random.sample(unconfirmed, 2)
                r1, r2 = assignment[s1], assignment[s2]

                # Only swap if different roles
                if r1 != r2:
                    # Check CSP validity of the swap
                    possible_s1 = self.csp.possible_roles(s1)
                    possible_s2 = self.csp.possible_roles(s2)

                    if r2 in possible_s1 and r1 in possible_s2:
                        # Make swap and check team validity
                        assignment[s1] = r2
                        assignment[s2] = r1

                        if self._is_team_valid(assignment):
                            # Metropolis-Hastings acceptance:
                            score_before = self.bbn.log_weight(s1, r1) + self.bbn.log_weight(s2, r2)
                            score_after = self.bbn.log_weight(s1, r2) + self.bbn.log_weight(s2, r1)

                            delta = score_after - score_before
                            if delta < 0 and random.random() >= math.exp(delta):
                                # Reject, revert swap
                                assignment[s1] = r1
                                assignment[s2] = r2
                        else:
                            # Revert swap if invalid
                            assignment[s1] = r1
                            assignment[s2] = r2

            # Collect samples after burn-in
            if iteration >= self.burn_in:
                for slot in active_slots:
                    role = assignment.get(slot)
                    if role and role not in self.csp._impossible[slot]:
                        counts[slot][role] += 1
                total_samples += 1

        # Normalize to posteriors
        posteriors: Dict[int, Dict[str, float]] = {}
        for slot in active_slots:
            if slot in self.csp.confirmed:
                role = self.csp.confirmed[slot]
                posteriors[slot] = {r: (1.0 if r == role else 0.0) for r in self.csp.all_roles}
            else:
                slot_counts = counts[slot]
                total = total_samples if total_samples > 0 else 1
                posteriors[slot] = {
                    r: round(slot_counts.get(r, 0) / total, 4)
                    for r in self.csp.all_roles
                }

        return posteriors
