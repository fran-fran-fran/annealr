# annealr: Annealing and drying controller for Kalico/Klipper
#
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# SPDX-License-Identifier: GPL-3.0-or-later
#
# File: executor.py
# Description: SegmentExecutor - computes setpoint trajectory and
#              completion criteria for each profile segment.


class SegmentExecutor:
    """Executes a single segment by computing the setpoint trajectory.

    Created when a segment begins, consumed until the segment completes.
    The executor does NOT interact with the heater - it only computes
    what the setpoint should be at any given time. The caller is
    responsible for feeding setpoints to the heater control loop.
    """
    __slots__ = (
        'segment', 't_start', 't_initial_c', '_effective_rate',
        '_effective_duration_s', '_is_descending',
    )

    def __init__(self, segment, t_start, t_initial_c):
        """
        Args:
            segment: Segment instance being executed.
            t_start: Reactor monotonic time when this segment began.
            t_initial_c: Chamber temperature at segment start (deg C).
        """
        self.segment = segment
        self.t_start = float(t_start)
        self.t_initial_c = float(t_initial_c)

        if segment.kind == 'ramp':
            self._is_descending = segment.target_c < t_initial_c - 1.0
            self._effective_rate = segment.compute_ramp_rate(t_initial_c)
            self._effective_duration_s = segment.compute_duration_s(t_initial_c)
        else:
            self._is_descending = False
            self._effective_rate = None
            self._effective_duration_s = segment.duration_s

    def setpoint_at(self, now):
        """Compute the desired temperature setpoint at reactor time `now`.

        Args:
            now: Current reactor monotonic time (seconds).

        Returns:
            Target temperature in deg C.
        """
        elapsed = now - self.t_start

        if self.segment.kind == 'soak':
            # Soak: hold at the target temperature
            return self.segment.target_c

        # Ramp segment
        if self._effective_rate is not None and self._effective_rate > 0:
            # Controlled ramp: move at the specified rate
            rate_per_s = self._effective_rate / 60.0  # deg C/s
            if self._is_descending:
                # Descending: subtract from initial
                sp = self.t_initial_c - rate_per_s * elapsed
                return max(sp, self.segment.target_c)
            else:
                # Ascending: add to initial
                sp = self.t_initial_c + rate_per_s * elapsed
                return min(sp, self.segment.target_c)
        else:
            # Unconstrained ramp: step directly to target
            return self.segment.target_c

    def is_complete(self, now, current_temp_c):
        """Check whether this segment is complete.

        For soaks: duration elapsed.
        For ascending ramps with rate/duration: duration elapsed.
        For ascending ramps unconstrained: temperature reached.
        For descending ramps: temperature reached (duration is a hint).

        Args:
            now: Current reactor monotonic time.
            current_temp_c: Current measured chamber temperature.

        Returns:
            True if the segment should be considered complete.
        """
        elapsed = now - self.t_start

        if self.segment.kind == 'soak':
            if self._effective_duration_s is None:  # pragma: no cover
                return False  # soaks always have duration_s set by Segment
            return elapsed >= self._effective_duration_s

        # Ramp segment
        target = self.segment.target_c
        tolerance = 2.0  # deg C

        if self._is_descending:
            # Descending ramps complete on temperature reached
            return current_temp_c <= target + tolerance
        else:
            # Ascending ramps
            if self._effective_rate is not None:
                # Controlled: complete on duration elapsed
                if self._effective_duration_s is not None:
                    return elapsed >= self._effective_duration_s
                # Rate specified but no duration computed (delta ~0?)
                return current_temp_c >= target - tolerance  # pragma: no cover
            else:
                # Unconstrained: complete on temperature reached
                return current_temp_c >= target - tolerance

    def elapsed_s(self, now):
        """Seconds elapsed since segment start."""
        return now - self.t_start

    def progress(self, now, current_temp_c):
        """Compute segment progress as a float 0.0 to 1.0.

        Descending ramps use temperature-based progress.
        Everything else uses time-based progress.
        """
        elapsed = now - self.t_start

        if self._is_descending:
            # Temperature-based progress
            delta_total = self.t_initial_c - self.segment.target_c
            if delta_total <= 0:  # pragma: no cover
                return 1.0  # blocked by profile validator (start != target)
            delta_done = self.t_initial_c - current_temp_c
            return max(0.0, min(1.0, delta_done / delta_total))
        else:
            # Time-based progress
            if self._effective_duration_s is None or \
               self._effective_duration_s <= 0:
                # Unconstrained ramp: use temperature-based as fallback
                if self.segment.kind == 'ramp':  # pragma: no cover
                    delta_total = abs(
                        self.segment.target_c - self.t_initial_c)
                    if delta_total <= 0:
                        return 1.0  # blocked by profile validator
                    delta_done = abs(current_temp_c - self.t_initial_c)  # pragma: no cover
                    return max(0.0, min(1.0, delta_done / delta_total))  # pragma: no cover
                return 0.0  # pragma: no cover -- soak with no duration, should not occur
            return max(0.0, min(1.0, elapsed / self._effective_duration_s))

    def remaining_s(self, now, current_temp_c):
        """Estimate remaining time in seconds.

        For descending ramps this is an estimate based on progress.
        For unconstrained ramps returns None (unknown).
        """
        if self._effective_duration_s is not None and \
           self._effective_duration_s > 0:
            if self._is_descending:
                # Use progress to estimate remaining
                p = self.progress(now, current_temp_c)  # pragma: no cover
                if p <= 0:  # pragma: no cover
                    return self._effective_duration_s  # progress clamp
                if p >= 1.0:  # pragma: no cover
                    return 0.0  # progress clamp
                elapsed = now - self.t_start  # pragma: no cover
                estimated_total = elapsed / p  # pragma: no cover
                return max(0.0, estimated_total - elapsed)  # pragma: no cover
            else:
                return max(0.0, self._effective_duration_s - (now - self.t_start))
        return None  # pragma: no cover -- unconstrained ramp, duration unknown

    def format_status(self, now, current_temp_c):
        """Generate a human-readable status string for this segment."""
        label = self.segment.make_label()
        elapsed = self.elapsed_s(now)
        elapsed_m = int(elapsed // 60)
        elapsed_s = int(elapsed % 60)

        if self.segment.kind == 'soak':
            remaining = self.remaining_s(now, current_temp_c)
            if remaining is not None:
                rem_m = int(remaining // 60)
                rem_s = int(remaining % 60)
                return ("%s: soaking %dm%02ds / %dm, temp %.1f deg C"
                        % (label, elapsed_m, elapsed_s,
                           int((self._effective_duration_s or 0) / 60),
                           current_temp_c))
            return "%s: soaking %dm%02ds, temp %.1f deg C" % (  # pragma: no cover
                label, elapsed_m, elapsed_s, current_temp_c)

        # Ramp
        sp = self.setpoint_at(now)
        if self._is_descending:
            p = self.progress(now, current_temp_c)
            return ("%s: cooling %.1f deg C -> %.0f deg C (%.0f%% there)"
                    % (label, current_temp_c, self.segment.target_c,
                       p * 100))
        else:
            return ("%s: ramping %.1f deg C / %.0f deg C"
                    % (label, current_temp_c, self.segment.target_c))
