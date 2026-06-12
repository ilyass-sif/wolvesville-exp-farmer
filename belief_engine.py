"""
belief_engine.py — The "Brain" of the Bot's Suspicions (BBN + CSP Edition)
===========================================================================
Wraps the GibbsSampler inference engine and exposes a clean interface
to the rest of the bot. 

Evidence feeding order:
  1. Hard facts (deaths, scans, teammates) → WolvesvilleCSP
  2. Soft observations (claims, votes, silence) → WolvesvilleBBN
  3. Run sampler → update probability matrix

The matrix is always slot -> {role -> posterior probability}.
"""

import json
import re
from typing import Optional
from collections import defaultdict

from inference import WolvesvilleCSP, WolvesvilleBBN, GibbsSampler, ROLE_AURA, get_role_team
from logger import BotLogger


# Chat claim extraction is now handled by extractor.py (LLM-based)


# ---------------------------------------------------------------------------
# BeliefEngine
# ---------------------------------------------------------------------------

class BeliefEngine:
    def __init__(self, client):
        self.client = client

        # Inference components
        self.csp = WolvesvilleCSP()
        self.bbn = WolvesvilleBBN()
        self.sampler = GibbsSampler(self.csp, self.bbn, n_samples=600, burn_in=100)

        # Cached posteriors: slot -> {role -> prob}
        self.matrix: dict = {}
        # Claim log: slot -> claimed_role
        self.claims: dict = {}
        # Investigation log: slot -> list of investigation dicts
        self.investigation_log: dict = defaultdict(list)
        # Players caught in a mathematical lie
        self.caught_lying: set = set()
        # Active player slots (non-dead)
        self._active_slots: list = []
        # Whether the CSP has been initialized with roles
        self._initialized: bool = False

        # Clear file on startup
        self.save_belief_state()

    def reset(self):
        """Wipe everything for a new game."""
        self.csp = WolvesvilleCSP()
        self.bbn = WolvesvilleBBN()
        self.sampler = GibbsSampler(self.csp, self.bbn, n_samples=600, burn_in=100)
        self.matrix = {}
        self.claims = {}
        self.investigation_log = defaultdict(list)
        self.caught_lying = set()
        self.hidden_role_slots = set()
        self._active_slots = []
        self._initialized = False
        self.save_belief_state()

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _try_initialize(self) -> bool:
        """Build CSP from current game state. Returns True when ready."""
        if self._initialized:
            return True

        s = self.client.state
        roles_list = s.roles_in_game
        my_role = s.my_role
        my_player_id = s.my_player_id
        my_player = s.players.get(my_player_id) if my_player_id else None
        my_slot = (my_player.grid_idx + 1) if (my_player and my_player.grid_idx is not None) else None

        if not roles_list or not my_role or not my_slot:
            return False

        # Initialize CSP with the full role list
        self.csp.reset(roles_list)

        # Build active slots from all players who have a grid index
        all_slots = []
        for p in s.players.values():
            if p.grid_idx is not None:
                slot = p.grid_idx + 1
                if slot in range(1, 17):
                    all_slots.append(slot)
                    # If we already know the role (teammate or revealed death), confirm it in CSP
                    if p.known_role and p.known_role not in ("", "???", "unknown"):
                        role_key = str(p.known_role).lower().replace(" ", "-")
                        # Map 'regular' roles to canonical names for BBN consistency
                        if role_key == "regular-werewolf": role_key = "werewolf"
                        if role_key == "regular-villager": role_key = "villager"
                        self.confirm_player_role(slot, role_key)

        self._active_slots = sorted(all_slots)

        # Lock in our own role (we know it for certain)
        my_role_key = my_role.lower().replace(" ", "-")
        if my_role_key == "regular-werewolf": my_role_key = "werewolf"
        if my_role_key == "regular-villager": my_role_key = "villager"
        
        # Ensure our own role is actually in the pool (CSP)
        if my_role_key not in self.csp.all_roles:
            self.csp.all_roles.append(my_role_key)
            if my_role_key not in self.csp.role_pool:
                self.csp.role_pool[my_role_key] = 1
        
        self.confirm_player_role(my_slot, my_role_key)

        self._initialized = True

        # Replay events and run initial inference
        self._refresh()
        return True

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def _refresh(self):
        """Syncs with GameState, runs sampler, and saves output."""
        s = self.client.state
        
        # 1. Force a full state sync to get current teammates/roles/me
        my_slot = None
        my_player = s.players.get(s.my_player_id) if s.my_player_id else None
        if my_player and my_player.grid_idx is not None:
            my_slot = my_player.grid_idx + 1
            
        self.csp.sync_from_state(s.players, s.roles_in_game, s.my_role, my_slot)
        
        # 2. Re-apply all hard facts from the timeline (ensures reveals/scans persist)
        self._replay_timeline(silent=True)

        # 3. Ensure _active_slots is current (only include valid 1-16 slots)
        self._active_slots = sorted([
            p.grid_idx + 1 for p in s.players.values() 
            if p.grid_idx is not None and (p.grid_idx + 1) in range(1, 17)
        ])

        if not self._active_slots or not self.csp.all_roles:
            return

        self.matrix = self.sampler.run(self._active_slots)
        
        # Auto-confirm: if a slot converges to 1.0 for a single role, confirm it
        # This bridges the Gibbs posterior back into CSP hard constraints
        for slot, probs in self.matrix.items():
            if self.csp.confirmed.get(slot):
                continue  # Already confirmed
            for role, prob in probs.items():
                if prob >= 1.0:
                    BotLogger.belief(f"AUTO-CONFIRM: Slot {slot} converged to 1.0 for {role}")
                    self.confirm_player_role(slot, role)
                    break
        
        self.save_belief_state()

    def _replay_timeline(self, silent: bool = False):
        """Iterates over all events and applies hard facts to CSP."""
        s = self.client.state
        for event in s.event_timeline:
            etype = event.get("type")
            if etype == "TEAMMATE_REVEAL":
                self._handle_teammate(event, silent=silent)
            elif etype == "DEATH":
                self._handle_death(event, silent=silent)
            elif etype == "SYSTEM":
                self._handle_system(event, silent=silent)
            elif etype == "REVEAL":
                self._handle_reveal(event, silent=silent)
            elif etype == "AURA":
                self._handle_aura(event, silent=silent)
            elif etype == "TEAM_RESULT":
                self._handle_team_result(event, silent=silent)
        
        # Re-apply investigations from confirmed reliable players
        # (these live in investigation_log, not in the event timeline,
        #  so they get lost when sync_from_state resets the CSP)
        reliable_roles = {"seer", "aura-seer", "detective", "analyst", "spirit-seer"}
        for slot, logs in self.investigation_log.items():
            confirmed_role = self.csp.confirmed.get(slot)
            if confirmed_role in reliable_roles:
                for log in logs:
                    self._apply_single_investigation(log)

    def get_beliefs(self) -> dict:
        return {
            "matrix": self.matrix,
            "claims": self.claims,
            "investigations": dict(self.investigation_log),
        }

    def add_external_info(self, info: list):
        """
        Receives structured information from the LLM extractor.
        info: list of dicts like [{"slot": 5, "role": "doctor"}]
        """
        if not self._initialized:
            if not self._try_initialize():
                return

        dirty = False
        for item in info:
            slot = item.get("slot")
            itype = item.get("type", "self_claim")
            
            if slot:
                # Ensure the slot is actually in the game
                if slot not in self._active_slots and slot in range(1, 17):
                    self._active_slots.append(slot)
                    self._active_slots.sort()

                if itype == "self_claim":
                    role = item.get("role")
                    if role:
                        role_key = role.lower().replace(" ", "-")
                        if self.claims.get(slot) != role_key:
                            BotLogger.belief(f"Claims Detected: Slot {slot} = {role_key}")
                            self.claims[slot] = role_key
                            self.bbn.add_claim(slot, role_key, self.csp.all_roles)
                            dirty = True
                elif itype in ("investigation_aura", "investigation_role", "investigation_team"):
                    if item not in self.investigation_log[slot]:
                        BotLogger.belief(f"Logged Investigation for Slot {slot}: {item}")
                        self.investigation_log[slot].append(item)
                        
                        # Cross-reference with CSP to catch mathematical lies
                        if slot not in self.caught_lying and self._check_if_lying(slot, item):
                            BotLogger.belief(f"CAUGHT LYING: Slot {slot} made an impossible claim! {item}")
                            self.caught_lying.add(slot)
                            self.bbn.add_caught_lying(slot, self.csp.all_roles)
                            dirty = True

                        # NEW: Auto-claim logic
                        if slot not in self.claims:
                            # Must be a NEW investigation never said by anyone else
                            is_new = True
                            
                            # Normalize current item for comparison
                            target1 = item.get("target1")
                            target2 = item.get("target2")
                            # Sort targets so (15, 2) == (2, 15)
                            current_targets = sorted([target1, target2]) if target1 and target2 else []
                            
                            for other_slot, logs in self.investigation_log.items():
                                if other_slot == slot: continue
                                for l in logs:
                                    if l.get("type") != itype: continue
                                    
                                    # Compare based on type
                                    if itype == "investigation_team":
                                        other_targets = sorted([l.get("target1"), l.get("target2")])
                                        if current_targets == other_targets and l.get("value") == item.get("value"):
                                            is_new = False
                                            break
                                    elif itype in ("investigation_aura", "investigation_role"):
                                        if l.get("target") == item.get("target") and l.get("value") == item.get("value"):
                                            is_new = False
                                            break
                                if not is_new: break

                            if is_new:
                                auto_role = None
                                if itype == "investigation_team":
                                    is_det_dead = any(p.known_role == "detective" and not p.is_alive for p in self.client.state.players.values())
                                    auto_role = "medium" if is_det_dead else "detective"
                                elif itype == "investigation_aura":
                                    if item.get("value") == "UNKNOWN":
                                        auto_role = "aura-seer"
                                elif itype == "investigation_role":
                                    auto_role = "seer"
                                    
                                if auto_role:
                                    BotLogger.belief(f"AUTO-CLAIM: Slot {slot} posted NEW {itype} -> tagging as {auto_role}")
                                    self.claims[slot] = auto_role
                                    self.bbn.add_claim(slot, auto_role, self.csp.all_roles)
                                    dirty = True

                        # If already confirmed reliable, apply immediately
                        if self.csp.confirmed.get(slot) in {"seer", "aura-seer", "detective", "analyst", "spirit-seer"}:
                            self._apply_single_investigation(item)
                            dirty = True
        
        if dirty:
            self._refresh()

    # ------------------------------------------------------------------
    # Facts & Constraints
    # ------------------------------------------------------------------

    def confirm_player_role(self, slot: int, role_key: str):
        """Confirms a role and retroactively applies their investigations if they are reliable."""
        if self.csp.confirmed.get(slot) == role_key:
            return
        self.csp.confirm_role(slot, role_key)
        
        reliable_roles = {"seer", "aura-seer", "detective", "analyst", "spirit-seer"}
        if role_key in reliable_roles:
            logs = self.investigation_log.get(slot, [])
            if logs:
                BotLogger.belief(f"Applying proven investigations for Slot {slot} ({role_key})")
                for log in logs:
                    self._apply_single_investigation(log)

    def _apply_single_investigation(self, log: dict):
        itype = log.get("type")
        if itype == "investigation_aura":
            self.csp.apply_aura(log["target"], log["value"], strict=False)
        elif itype == "investigation_role":
            self.confirm_player_role(log["target"], log["value"])
        elif itype == "investigation_team":
            val = "SAME" if log.get("value") in (True, "SAME") else "DIFFERENT"
            self.csp.apply_team_constraint(log["target1"], log["target2"], val)

    def _check_if_lying(self, slot: int, log: dict) -> bool:
        """Returns True if the investigation claim is mathematically impossible according to the CSP."""
        itype = log.get("type")
        
        if itype == "self_claim":
            val = log.get("role")
            if not val: return False
            if val not in self.csp.possible_roles(slot):
                return True
            # If there are no more instances of this role available globally
            # and the slot itself isn't already confirmed to be it
            if self.csp.role_pool.get(val, 0) <= 0 and self.csp.confirmed.get(slot) != val:
                return True
        elif itype == "investigation_aura":
            target = log.get("target")
            val = log.get("value")
            if not target or not val: return False
            possible_roles = self.csp.possible_roles(target)
            
            if val == "GOOD":
                # Loosened: GOOD can mean GOOD aura OR any Village role
                can_be_good = False
                for r in possible_roles:
                    if ROLE_AURA.get(r) == "GOOD" or ROLE_BUCKET.get(r, "").startswith("village"):
                        can_be_good = True
                        break
                if not can_be_good: return True
            else:
                # EVIL and UNKNOWN remain strict
                possible_auras = {ROLE_AURA.get(r, "UNKNOWN") for r in possible_roles}
                if val not in possible_auras:
                    return True
        elif itype == "investigation_role":
            target = log.get("target")
            val = log.get("value")
            if not target or not val: return False
            if val not in self.csp.possible_roles(target):
                return True
        elif itype == "investigation_team":
            t1 = log.get("target1")
            t2 = log.get("target2")
            val = log.get("value")
            if not t1 or not t2 or not val: return False
            p1 = self.csp.possible_roles(t1)
            p2 = self.csp.possible_roles(t2)
            
            can_be_true = False
            for r1 in p1:
                for r2 in p2:
                    team1 = get_role_team(r1)
                    team2 = get_role_team(r2)
                    is_same = (team1 == team2)
                    
                    if (val == "SAME" and is_same) or (val == "DIFFERENT" and not is_same):
                        can_be_true = True
                        break
                if can_be_true:
                    break
            if not can_be_true:
                return True
        return False

    # ------------------------------------------------------------------
    # Event Router
    # ------------------------------------------------------------------

    def process_event(self, event: dict, game_id: str):
        """Entry point for all game events."""
        # Guard against stale events
        if self.client.state.game_id and game_id != self.client.state.game_id:
            return

        # Try to initialize if not ready
        if not self._initialized:
            if not self._try_initialize():
                return

        # 1. Handle the new event specifically for logging
        etype = event.get("type")
        if etype == "TEAMMATE_REVEAL": self._handle_teammate(event)
        elif etype == "DEATH": self._handle_death(event)
        elif etype == "SYSTEM": self._handle_system(event)
        elif etype == "REVEAL": self._handle_reveal(event)
        elif etype == "AURA": self._handle_aura(event)
        elif etype == "TEAM_RESULT": self._handle_team_result(event)

        # 2. Trigger a full silent refresh to update matrix/CSP
        self._refresh()

    # ------------------------------------------------------------------
    # Event Handlers
    # ------------------------------------------------------------------

    def _handle_death(self, event: dict, silent: bool = False) -> bool:
        slot = event.get("slot")
        role = event.get("role")

        if not slot:
            return False

        if self.csp.confirmed.get(slot):
            return False

        # Keep dead players in _active_slots so they stay in the matrix
        if slot not in self._active_slots and slot in range(1, 17):
            self._active_slots.append(slot)
            self._active_slots.sort()

        if role and role not in ("", "???", "unknown", "None", None):
            role_key = str(role).lower().replace(" ", "-")
            if not silent: BotLogger.belief(f"Death reveal: Slot {slot} = {role_key}")
            self.confirm_player_role(slot, role_key)
            return True

        if slot not in self.hidden_role_slots:
            if not silent: BotLogger.belief(f"Slot {slot} died but role is hidden.")
            self.hidden_role_slots.add(slot)
            
        return True  # Still refresh, active_slots changed

    def _handle_chat(self, event: dict) -> bool:
        # Chat is now processed in batches by the bot's QwenExtractor,
        # which feeds directly into add_external_info().
        return False

    def _handle_vote(self, event: dict) -> bool:
        # Votes carry weak signal — accumulate but don't immediately re-run sampler
        voter_name = event.get("voter", "")
        voter_slot = self._extract_slot(voter_name)
        target_name = event.get("target", "")
        target_slot = self._extract_slot(target_name)

        if voter_slot and target_slot:
            self.bbn.add_vote_against(voter_slot, target_slot, self.csp.all_roles)
            return True
        return False

    def _handle_aura(self, event: dict, silent: bool = False) -> bool:
        slot = event.get("slot")
        aura = event.get("aura")
        if slot and aura:
            # Only log if new or changed? Aura can be tricky. 
            # For now, let's just log if not confirmed.
            if not self.csp.confirmed.get(slot):
                if not silent: BotLogger.belief(f"Aura scan: Slot {slot} aura = {aura}")
            self.csp.apply_aura(slot, aura)
            return True
        return False

    def _handle_team_result(self, event: dict, silent: bool = False) -> bool:
        slot1 = event.get("slot1")
        slot2 = event.get("slot2")
        are_equal = event.get("are_equal")
        if slot1 and slot2 and are_equal is not None:
            val = "SAME" if are_equal else "DIFFERENT"
            if not silent: BotLogger.belief(f"Detective result: Slot {slot1} and {slot2} are on {val} teams.")
            self.csp.apply_team_constraint(slot1, slot2, val)
            return True
        return False

    def _handle_reveal(self, event: dict, silent: bool = False) -> bool:
        slot = event.get("slot")
        role = event.get("role")
        if slot and role:
            role_key = str(role).lower().replace(" ", "-")
            if self.csp.confirmed.get(slot) != role_key:
                if not silent: BotLogger.belief(f"General reveal: Slot {slot} = {role_key}")
            self.confirm_player_role(slot, role_key)
            return True
        return False

    def _handle_teammate(self, event: dict, silent: bool = False) -> bool:
        slot = event.get("slot")
        role = event.get("role")
        if slot and role:
            role_key = str(role).lower().replace(" ", "-")
            if self.csp.confirmed.get(slot) != role_key:
                if not silent: BotLogger.belief(f"Teammate reveal: Slot {slot} = {role_key}")
            self.confirm_player_role(slot, role_key)
            return True
        return False

    def _handle_system(self, event: dict, silent: bool = False) -> bool:
        msg_key = event.get("msg_key")
        args = event.get("msg_args", {})

        if msg_key in ("game-seer-result", "game-spirit-seer-result"):
            player_data = args.get("player", {})
            username = player_data.get("player-username", "")
            aura = player_data.get("player-aura", "")
            role = player_data.get("player-role", "")
            slot = self._extract_slot(username)
            if slot:
                if role:
                    role_key = role.lower().replace(" ", "-")
                    if not silent and self.csp.confirmed.get(slot) != role_key:
                        BotLogger.belief(f"Seer role reveal: Slot {slot} = {role_key}")
                    self.confirm_player_role(slot, role_key)
                elif aura:
                    if not silent and not self.csp.confirmed.get(slot):
                        BotLogger.belief(f"Seer aura scan: Slot {slot} aura = {aura}")
                    self.csp.apply_aura(slot, aura)
                return True

        elif msg_key == "game-detective-result":
            # Bot-based detective results are handled via TEAM_RESULT events
            pass

        elif msg_key in (
            "game-loudmouth-revealed",
            "game-vigilante-shot",
            "game-gunner-killed-player",
            "game-wolf-seer-view-role",
            "game-aura-seer-view-role",
            "game-revealed-role-private",
            "game-fortune-teller-card-used-killed",
            "game-priest-killed-werewolf",
            "game-priest-killed-self"
        ):
            # Process main player
            player_data = args.get("player", {})
            username = player_data.get("player-username", "")
            role = player_data.get("player-role", "")
            slot = self._extract_slot(username)
            if slot and role:
                role_key = role.lower().replace(" ", "-")
                if not silent and self.csp.confirmed.get(slot) != role_key:
                    BotLogger.belief(f"Role reveal ({msg_key}): Slot {slot} = {role_key}")
                self.confirm_player_role(slot, role_key)

            # Process target player (if revealed in the same message)
            target_data = args.get("target-player", {})
            t_username = target_data.get("player-username", "")
            t_role = target_data.get("player-role", "")
            t_slot = self._extract_slot(t_username)
            if t_slot and t_role:
                t_role_key = t_role.lower().replace(" ", "-")
                if not silent and self.csp.confirmed.get(t_slot) != t_role_key:
                    BotLogger.belief(f"Target reveal ({msg_key}): Slot {t_slot} = {t_role_key}")
                self.confirm_player_role(t_slot, t_role_key)
            
            # Special case: Priest killed self means the victim (player) is NOT a wolf
            if msg_key == "game-priest-killed-self":
                v_data = args.get("player", {})
                v_name = v_data.get("player-username", "")
                v_slot = self._extract_slot(v_name)
                if v_slot:
                    if not silent: BotLogger.belief(f"Priest died! Slot {v_slot} is confirmed NOT a wolf.")
                    self.csp.mark_not_wolf(v_slot)

            return True

        elif msg_key == "game-shadow-wolf-double-votes":
            # Shadow wolf is active — log but no matrix update
            pass

        elif msg_key in ("game-mayor-revealed", "game-mayor-revealed-role"):
            player_data = args.get("player", {})
            player_name = player_data.get("player-username", args.get("player-username", ""))
            slot = self._extract_slot(player_name)
            if slot:
                if not silent and self.csp.confirmed.get(slot) != "mayor":
                    BotLogger.belief(f"Mayor revealed: Slot {slot}")
                self.confirm_player_role(slot, "mayor")
                return True

        return False

    def _handle_phase(self, event: dict) -> bool:
        # On each new day, add silence observations for players who haven't spoken
        # (This is a future enhancement — for now, just return False)
        return False

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_belief_state(self):
        """Dumps current beliefs to live_belief.json."""
        try:
            with open("live_belief.json", "w", encoding="utf-8") as f:
                json.dump(self.get_beliefs(), f, indent=4)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Utils
    # ------------------------------------------------------------------

    def _extract_slot(self, speaker_str: str) -> Optional[int]:
        """Extracts slot number from '[2] Username' or '2 Username'."""
        if not speaker_str:
            return None
        try:
            if "[" in speaker_str and "]" in speaker_str:
                return int(speaker_str.split("[")[1].split("]")[0])
            first_word = speaker_str.strip().split(" ")[0]
            if first_word.isdigit():
                return int(first_word)
        except Exception:
            pass
        return None
