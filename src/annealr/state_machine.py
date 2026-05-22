# annealr: Annealing and drying controller for Kalico/Klipper
#
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# SPDX-License-Identifier: GPL-3.0-or-later
#
# File: state_machine.py
# Description: AnnealrStateMachine - manages run lifecycle, state
#              transitions, and pause/resume time accounting.

import logging

VALID_STATES = (
    'idle', 'ramping', 'soaking', 'cooling',
    'paused', 'complete', 'cancelled', 'error',
)

ACTIVE_STATES = ('ramping', 'soaking', 'cooling')
PAUSABLE_STATES = ('ramping', 'soaking', 'cooling')
CANCELLABLE_STATES = ('ramping', 'soaking', 'cooling', 'paused')


class AnnealrStateMachine:
    """Manages the lifecycle of an annealing run.

    Tracks the current state, active profile, segment index,
    and pause/resume time accounting.
    """

    def __init__(self):
        self.log = logging.getLogger('annealr.state')
        self.state = 'idle'
        self.profile = None
        self.stage_index = 0
        self.executor = None

        # Pause accounting
        self._paused_phase = None      # state before pause
        self._pause_start = 0.0        # reactor time when paused
        self._total_pause_offset = 0.0 # accumulated pause time for executor

        # Run tracking
        self._run_start_time = 0.0

    def is_active(self):
        """True if an annealr run is in progress (including paused)."""
        return self.state in ACTIVE_STATES or self.state == 'paused'

    def is_running(self):
        """True if actively executing (not paused)."""
        return self.state in ACTIVE_STATES

    def can_pause(self):
        return self.state in PAUSABLE_STATES

    def can_resume(self):
        return self.state == 'paused'

    def can_cancel(self):
        return self.state in CANCELLABLE_STATES

    def can_start(self):
        return self.state in ('idle', 'complete', 'cancelled', 'error')

    # ── State transitions ─────────────────────────────────────────────

    def start(self, profile, now):
        """Begin executing a profile.

        Args:
            profile: Profile instance to execute.
            now: Current reactor monotonic time.

        Returns:
            First segment's Segment object, or raises RuntimeError.
        """
        if not self.can_start():
            raise RuntimeError(
                "Cannot start: state is '%s'" % self.state)
        if not profile.segments:
            raise RuntimeError("Profile '%s' has no segments" % profile.name)

        self.profile = profile
        self.stage_index = 0
        self._run_start_time = now
        self._total_pause_offset = 0.0
        self._paused_phase = None

        first_seg = profile.segments[0]
        self.state = self._state_for_segment(first_seg, start_temp_c=None)
        self.log.info(
            "Anneal started: %s (%d segments)",
            profile.name, len(profile.segments))

        return first_seg

    def pause(self, now):
        """Pause the current run.

        Returns the state that was active before pausing.
        """
        if not self.can_pause():
            raise RuntimeError(
                "Cannot pause: state is '%s'" % self.state)

        self._paused_phase = self.state
        self._pause_start = now
        self.state = 'paused'
        self.log.info("Anneal paused (was %s)", self._paused_phase)
        return self._paused_phase

    def resume(self, now):
        """Resume a paused run.

        Adjusts the pause offset so the executor's time references
        remain correct.

        Returns the state being resumed into.
        """
        if not self.can_resume():
            raise RuntimeError(
                "Cannot resume: state is '%s'" % self.state)

        pause_duration = now - self._pause_start
        self._total_pause_offset += pause_duration

        self.state = self._paused_phase
        self._paused_phase = None
        self.log.info(
            "Anneal resumed (paused %.1fs, total offset %.1fs)",
            pause_duration, self._total_pause_offset)

        return self.state

    def cancel(self):
        """Cancel the current run."""
        if not self.can_cancel():
            raise RuntimeError(
                "Cannot cancel: state is '%s'" % self.state)

        prev = self.state
        self.state = 'cancelled'
        self.executor = None
        self.log.info("Anneal cancelled (was %s)", prev)

    def complete(self):
        """Mark the run as complete."""
        self.state = 'complete'
        self.executor = None
        self.log.info("Anneal complete: %s", self.profile.name)

    def error(self, reason):
        """Transition to error state."""
        self.state = 'error'
        self.executor = None
        self.log.error("Anneal error: %s", reason)

    def advance_stage(self, now, current_temp_c):
        """Advance to the next segment.

        Returns the next Segment, or None if the profile is complete.
        """
        self.stage_index += 1
        if self.stage_index >= len(self.profile.segments):
            return None

        seg = self.profile.segments[self.stage_index]
        self.state = self._state_for_segment(seg, current_temp_c)
        self.log.info(
            "Stage %d/%d: %s",
            self.stage_index, len(self.profile.segments) - 1,
            seg.make_label())

        return seg

    # ── Executor management ───────────────────────────────────────────

    def set_executor(self, executor):
        """Set the active segment executor."""
        self.executor = executor

    def adjusted_time(self, now):
        """Return reactor time adjusted for pause offsets.

        The executor was created with a t_start based on reactor time.
        When paused and resumed, the executor's internal elapsed
        computation would be wrong without adjusting for pause duration.
        We subtract the accumulated pause offset from `now` so the
        executor sees time as if pauses never happened.
        """
        if self.state == 'paused':
            # While paused, freeze time at the moment of pause
            return self._pause_start - self._total_pause_offset
        return now - self._total_pause_offset

    # ── Status ────────────────────────────────────────────────────────

    def get_status(self, eventtime, current_temp_c=0.0):
        """Build a status dict for Moonraker/UI consumption."""
        status = {
            'state': self.state,
            'profile': self.profile.name if self.profile else None,
            'stage_index': self.stage_index,
            'stage_count': (len(self.profile.segments)
                           if self.profile else 0),
            'stage': None,
            'elapsed_s': 0.0,
            'remaining_s': 0.0,
            'progress': 0.0,
            'run_elapsed_s': 0.0,
        }

        if self.profile and self._run_start_time > 0:
            if self.state == 'paused':
                status['run_elapsed_s'] = (
                    self._pause_start - self._run_start_time
                    - self._total_pause_offset
                    + (self._pause_start - self._pause_start))
            else:
                status['run_elapsed_s'] = (
                    eventtime - self._run_start_time
                    - self._total_pause_offset)

        if (self.profile and self.state in ACTIVE_STATES + ('paused',)
                and self.stage_index < len(self.profile.segments)):
            seg = self.profile.segments[self.stage_index]
            adj_time = self.adjusted_time(eventtime)

            stage_info = {
                'index': self.stage_index,
                'label': seg.make_label(),
                'target': seg.target_c,
                'kind': seg.kind,
                'rate': None,
            }

            if self.executor:
                stage_info['elapsed_s'] = self.executor.elapsed_s(adj_time)
                remaining = self.executor.remaining_s(
                    adj_time, current_temp_c)
                stage_info['remaining_s'] = remaining if remaining else 0.0
                stage_info['progress'] = self.executor.progress(
                    adj_time, current_temp_c)
                if self.executor._effective_rate is not None:
                    rate = self.executor._effective_rate
                    if self.executor._is_descending:
                        rate = -rate
                    stage_info['rate'] = round(rate, 2)

            status['stage'] = stage_info

        return status

    # ── Internal ──────────────────────────────────────────────────────

    def _state_for_segment(self, segment, start_temp_c):
        """Determine the state machine state for a given segment."""
        if segment.kind == 'soak':
            return 'soaking'
        if start_temp_c is not None and segment.is_descending_from(start_temp_c):
            return 'cooling'
        return 'ramping'