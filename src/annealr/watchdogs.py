# annealr: Annealing and drying controller for Kalico/Klipper
#
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# SPDX-License-Identifier: GPL-3.0-or-later
#
# File: watchdogs.py
# Description: Profile-aware safety watchdogs - ramp timeout,
#              soak drift detection, and cooling stall detection.

import logging


class RampTimeoutWatchdog:
    """Detects ramps that take excessively longer than expected.

    If a ramp has a known expected duration (from rate or explicit duration)
    and the actual elapsed time exceeds safety_factor x expected, the ramp
    is considered stalled.
    """

    def __init__(self, safety_factor=3.0):
        self.safety_factor = safety_factor
        self.log = logging.getLogger('annealr.watchdog.ramp')
        self._expected_duration_s = None

    def begin(self, segment, start_temp_c):
        """Called when a ramp segment begins."""
        self._expected_duration_s = segment.compute_duration_s(start_temp_c)

    def check(self, elapsed_s, current_temp_c, target_c):
        """Check for ramp timeout.

        Returns:
            None if ok, or an error message string if timed out.
        """
        if self._expected_duration_s is None:
            # Unconstrained ramp - no timeout to enforce
            return None

        if self._expected_duration_s <= 0:
            return None

        limit = self._expected_duration_s * self.safety_factor
        if elapsed_s > limit:
            return (
                "Ramp timeout: %.0fs elapsed, expected ~%.0fs "
                "(%.1fx safety factor). Current %.1f deg C, target %.0f deg C"
                % (elapsed_s, self._expected_duration_s,
                   self.safety_factor, current_temp_c, target_c))
        return None

    def reset(self):
        self._expected_duration_s = None


class SoakDriftWatchdog:
    """Detects sustained temperature drift during soak phases.

    If temperature deviates from the soak target by more than
    drift_limit_c for more than max_consecutive consecutive checks,
    the soak is considered drifting.
    """

    def __init__(self, drift_limit_c=10.0, max_consecutive=6):
        self.drift_limit_c = drift_limit_c
        self.max_consecutive = max_consecutive
        self.log = logging.getLogger('annealr.watchdog.drift')
        self._consecutive_count = 0

    def check(self, current_temp_c, target_c):
        """Check for soak drift.

        Returns:
            None if ok, or an error message string if drifting.
        """
        drift = abs(current_temp_c - target_c)
        if drift > self.drift_limit_c:
            self._consecutive_count += 1
            if self._consecutive_count >= self.max_consecutive:
                return (
                    "Soak drift: %.1f deg C from target %.0f deg C "
                    "for %d consecutive checks"
                    % (drift, target_c, self._consecutive_count))
        else:
            self._consecutive_count = 0
        return None

    def reset(self):
        self._consecutive_count = 0


class CoolStallWatchdog:
    """Detects failure to cool during descending ramp segments.

    If the chamber hasn't dropped by at least min_drop_c within
    check_window_s seconds during a cooling segment, something
    is likely wrong (fan failure, sealed door, etc.).
    """

    def __init__(self, min_drop_c=5.0, check_window_s=300.0,
                 grace_period_s=60.0):
        self.min_drop_c = min_drop_c
        self.check_window_s = check_window_s
        self.grace_period_s = grace_period_s
        self.log = logging.getLogger('annealr.watchdog.cool')
        self._start_temp_c = None

    def begin(self, start_temp_c):
        """Called when a cooling segment begins."""
        self._start_temp_c = start_temp_c

    def check(self, elapsed_s, current_temp_c):
        """Check for cooling stall.

        Returns:
            None if ok, or an error message string if stalled.
        """
        if self._start_temp_c is None:
            return None

        if elapsed_s < self.grace_period_s:
            return None

        if elapsed_s < self.check_window_s:
            return None

        actual_drop = self._start_temp_c - current_temp_c
        if actual_drop < self.min_drop_c:
            return (
                "Cooling stall: only dropped %.1f deg C in %.0fmin "
                "(expected at least %.1f deg C). "
                "Check chamber fan and door seal."
                % (actual_drop, elapsed_s / 60.0, self.min_drop_c))
        return None

    def reset(self):
        self._start_temp_c = None


class WatchdogManager:
    """Coordinates all watchdogs for the current segment."""

    def __init__(self, ramp_safety_factor=3.0, soak_drift_limit_c=10.0,
                 soak_drift_checks=6, cool_min_drop_c=5.0,
                 cool_check_window_s=300.0, cool_grace_s=60.0):
        self.ramp_wd = RampTimeoutWatchdog(ramp_safety_factor)
        self.soak_wd = SoakDriftWatchdog(soak_drift_limit_c,
                                          soak_drift_checks)
        self.cool_wd = CoolStallWatchdog(cool_min_drop_c,
                                          cool_check_window_s,
                                          cool_grace_s)
        self.log = logging.getLogger('annealr.watchdog')

    def begin_segment(self, segment, start_temp_c):
        """Reset watchdogs and configure for the new segment."""
        self.ramp_wd.reset()
        self.soak_wd.reset()
        self.cool_wd.reset()

        if segment.kind == 'ramp':
            self.ramp_wd.begin(segment, start_temp_c)
            if segment.is_descending_from(start_temp_c):
                self.cool_wd.begin(start_temp_c)

    def check(self, state, elapsed_s, current_temp_c, target_c):
        """Run the appropriate watchdog check for the current state.

        Args:
            state: Current state machine state string.
            elapsed_s: Seconds elapsed in current segment.
            current_temp_c: Current chamber temperature.
            target_c: Current segment target temperature.

        Returns:
            None if all ok, or an error message string.
        """
        if state == 'ramping':
            return self.ramp_wd.check(elapsed_s, current_temp_c, target_c)
        elif state == 'soaking':
            return self.soak_wd.check(current_temp_c, target_c)
        elif state == 'cooling':
            return self.cool_wd.check(elapsed_s, current_temp_c)
        return None
