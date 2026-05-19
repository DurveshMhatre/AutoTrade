"""
Trade Manager Module — Phase 3 + Ranging Mode
==================================
Handles the 3-tier trailing stop and partial profit logic for open positions.

TRENDING mode (default):
  Tier 1: Take 33% profit at 1.5R (locks in, reduces risk to zero)
  Tier 2: Take 33% profit at 3R (main target)
  Tier 3: Trail remaining 34% with ATR-based stop

RANGING mode (new):
  Fixed TP at opposite S&R level, fixed SL below support.
  Close 100% at TP — no trailing, no partials.
"""

import logging

logger = logging.getLogger(__name__)

class TrailingStopManager:
    """
    Manages dynamic exits for a single position.
    Supports both TRENDING and RANGING exit modes.
    """

    def __init__(self, entry_price: float, side: str, atr: float, regime: str,
                 tp_override: float = None, sl_override: float = None):
        self.entry = entry_price
        self.side = side.lower()
        self.regime = regime

        if regime == "RANGING":
            # ── RANGING mode: tight fixed exits, no trailing ──────────
            # SL = use provided override (below support) or ATR-based tight stop
            self.initial_stop = sl_override if sl_override else (
                entry_price * (1 - 0.008) if self.side == "buy" else entry_price * (1 + 0.008)
            )
            self.R = abs(entry_price - self.initial_stop)

            # TP = use provided override (at resistance) or 1.5R
            if tp_override:
                self.tier1_target = tp_override
            else:
                self.tier1_target = entry_price + (1.5 * self.R) if self.side == "buy" else entry_price - (1.5 * self.R)

            self.tier2_target = None   # No tier 2 in ranging
            self.tier1_hit = False
            self.tier2_hit = True      # Skip tier 2 entirely
            self.trailing_stop = self.initial_stop

        else:
            # ── Standard trending mode (existing logic unchanged) ─────
            self.initial_stop = self._compute_stop(entry_price, atr, regime)

            # R = distance from entry to stop
            self.R = abs(entry_price - self.initial_stop)

            if self.side == "buy":
                self.tier1_target = entry_price + (1.5 * self.R)
                self.tier2_target = entry_price + (3.0 * self.R)
            else:
                self.tier1_target = entry_price - (1.5 * self.R)
                self.tier2_target = entry_price - (3.0 * self.R)

            self.trailing_stop = self.initial_stop
            self.tier1_hit = False
            self.tier2_hit = False

    def _compute_stop(self, price: float, atr: float, regime: str) -> float:
        """Calculate ATR-based stop distance depending on regime."""
        multiplier = {"STRONG_TREND_UP": 2.5, "STRONG_TREND_DOWN": 2.5,
                      "WEAK_TREND_UP": 1.8, "RANGING": 1.2, "CHOP": 1.0, 
                      "CAPITULATION": 1.5}.get(regime, 2.0)
        dist = atr * multiplier
        return price - dist if self.side == "buy" else price + dist

    def _target_hit(self, current_price: float, target: float) -> bool:
        if self.side == "buy":
            return current_price >= target
        return current_price <= target

    def _stop_hit(self, current_price: float) -> bool:
        if self.side == "buy":
            return current_price <= self.trailing_stop
        return current_price >= self.trailing_stop

    def update(self, current_price: float, current_atr: float) -> dict:
        """
        Evaluate price and return necessary actions.
        """
        actions = []

        if self.regime == "RANGING":
            # ── Ranging mode: close 100% at TP level (no partial, no trailing) ──
            if not self.tier1_hit:
                if (self.side == "buy" and current_price >= self.tier1_target) or \
                   (self.side == "sell" and current_price <= self.tier1_target):
                    self.tier1_hit = True
                    actions.append({"action": "close_all", "pct": 1.0, "reason": "ranging_TP_hit"})

            # Check stop (fixed, no trailing in ranging)
            if self._stop_hit(current_price):
                actions.append({"action": "close_all", "reason": "ranging_SL_hit"})

            return {"trailing_stop": self.trailing_stop, "actions": actions}

        else:
            # ── Standard trending logic ─────────────────────────────────
            # Tier 1: move stop to breakeven when 1.5R hit
            if not self.tier1_hit and self._target_hit(current_price, self.tier1_target):
                self.tier1_hit = True
                self.trailing_stop = self.entry  # stop to breakeven = risk-free trade
                actions.append({"action": "partial_close", "pct": 0.33, "reason": "1.5R target hit"})

            # Tier 2: take more at 3R
            if self.tier1_hit and not self.tier2_hit and self._target_hit(current_price, self.tier2_target):
                self.tier2_hit = True
                actions.append({"action": "partial_close", "pct": 0.33, "reason": "3R target hit"})

            # Trail remaining with ATR
            if self.tier2_hit:
                new_trail = current_price - (current_atr * 2.0) if self.side == "buy" else current_price + (current_atr * 2.0)
                if self.side == "buy" and new_trail > self.trailing_stop:
                    self.trailing_stop = new_trail
                elif self.side == "sell" and new_trail < self.trailing_stop:
                    self.trailing_stop = new_trail

            # Check stop hit
            if self._stop_hit(current_price):
                actions.append({"action": "close_all", "reason": "trailing stop hit"})

            return {"trailing_stop": self.trailing_stop, "actions": actions}
