# kalico-anneal: Annealing and drying controller for Kalico/Klipper
#
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# SPDX-License-Identifier: GPL-3.0-or-later
#
# File: anneal_manager.py
# Description: AnnealManager - main plugin class. Registers GCode commands,
#              manages profile storage, drives the segment executor and
#              watchdogs, and exposes status for Moonraker/UI.

import logging

from . import profile as prof
from .executor import SegmentExecutor
from .state_machine import AnnealStateMachine
from .watchdogs import WatchdogManager

TICK_INTERVAL = 5.0
STATUS_LOG_INTERVAL = 60.0  # seconds between console status updates

# Validation limits
MAX_RATE_C_PER_MIN = 200.0
MAX_DURATION_MIN = 1440.0  # 24 hours


class PendingProfile:
    """Transaction buffer for interactive profile definition."""
    __slots__ = ('name', 'description', 'segments')

    def __init__(self, name, description=""):
        self.name = name
        self.description = description
        self.segments = []


class AnnealManager:
    """Top-level annealing controller.

    Manages profile storage, GCode command registration,
    the reactor tick loop, and Moonraker status reporting.
    """

    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.log = logging.getLogger('anneal')

        # Config
        self.heater_name = config.get('heater', 'annealer')

        # Runtime state
        self.heater = None
        self._profiles = {}
        self._pending = None  # PendingProfile transaction buffer
        self._sm = AnnealStateMachine()
        self._watchdogs = WatchdogManager()
        self._timer = None
        self._last_status_log = 0.0

        # Register commands
        self._register_commands()

        # Event handlers
        self.printer.register_event_handler(
            'klippy:ready', self._handle_ready)

    # ── Startup ───────────────────────────────────────────────────────

    def _handle_ready(self):
        pheaters = self.printer.lookup_object('heaters')
        self.heater = pheaters.lookup_heater(self.heater_name)

    def register_profile(self, profile):
        """Register a profile (called by anneal_profile load_config_prefix)."""
        self._profiles[profile.name] = profile
        self.log.info(
            "Loaded anneal profile: %s (%d segments)",
            profile.name, len(profile.segments))

    # ── GCode registration ────────────────────────────────────────────

    def _register_commands(self):
        g = self.gcode
        g.register_command(
            'ANNEAL_PROFILE_BEGIN', self.cmd_profile_begin,
            desc="Begin defining a new anneal profile")
        g.register_command(
            'ANNEAL_PROFILE_RAMP', self.cmd_profile_ramp,
            desc="Add a ramp segment to the profile being defined")
        g.register_command(
            'ANNEAL_PROFILE_SOAK', self.cmd_profile_soak,
            desc="Add a soak segment to the profile being defined")
        g.register_command(
            'ANNEAL_PROFILE_END', self.cmd_profile_end,
            desc="Finish and commit the profile being defined")
        g.register_command(
            'ANNEAL_PROFILE_ABORT', self.cmd_profile_abort,
            desc="Discard the profile being defined")
        g.register_command(
            'ANNEAL_PROFILES', self.cmd_list_profiles,
            desc="List all available anneal profiles")
        g.register_command(
            'ANNEAL_PROFILE_SHOW', self.cmd_show_profile,
            desc="Show details of a specific profile")
        g.register_command(
            'ANNEAL_PROFILE_DELETE', self.cmd_delete_profile,
            desc="Delete a saved profile")
        g.register_command(
            'ANNEAL_START', self.cmd_start,
            desc="Start an annealing run")
        g.register_command(
            'ANNEAL_PAUSE', self.cmd_pause,
            desc="Pause the active annealing run")
        g.register_command(
            'ANNEAL_RESUME', self.cmd_resume,
            desc="Resume a paused annealing run")
        g.register_command(
            'ANNEAL_CANCEL', self.cmd_cancel,
            desc="Cancel the active annealing run")
        g.register_command(
            'ANNEAL_STATUS', self.cmd_status,
            desc="Report current annealing state")

    # ── Profile definition commands ───────────────────────────────────

    def cmd_profile_begin(self, gcmd):
        if self._pending is not None:
            raise gcmd.error(
                "Profile '%s' already in progress. "
                "Run ANNEAL_PROFILE_END to commit or "
                "ANNEAL_PROFILE_ABORT to discard."
                % self._pending.name)

        name = gcmd.get('NAME')
        description = gcmd.get('DESCRIPTION', '')
        self._pending = PendingProfile(name, description)
        self._respond("Defining profile '%s'. Add segments with "
                      "ANNEAL_PROFILE_RAMP and ANNEAL_PROFILE_SOAK."
                      % name)

    def cmd_profile_ramp(self, gcmd):
        if self._pending is None:
            raise gcmd.error(
                "No profile being defined. "
                "Run ANNEAL_PROFILE_BEGIN first.")

        target = gcmd.get_float('TARGET')
        rate = gcmd.get_float('RATE', default=None)
        duration_min = gcmd.get_float('DURATION', default=None)

        # Mutual exclusion
        if rate is not None and duration_min is not None:
            raise gcmd.error(
                "Specify RATE or DURATION, not both")

        # Rate validation
        if rate is not None:
            if rate <= 0:
                raise gcmd.error("RATE must be > 0, got %.1f" % rate)
            if rate > MAX_RATE_C_PER_MIN:
                raise gcmd.error(
                    "RATE %.0f deg C/min exceeds limit (%.0f)"
                    % (rate, MAX_RATE_C_PER_MIN))

        # Duration validation
        if duration_min is not None:
            if duration_min <= 0:
                raise gcmd.error(
                    "DURATION must be > 0, got %.1f" % duration_min)
            if duration_min > MAX_DURATION_MIN:
                raise gcmd.error(
                    "DURATION %.0fmin exceeds limit (%.0fh)"
                    % (duration_min, MAX_DURATION_MIN / 60))

        # Temperature range validation
        if self.heater is not None:
            t_min = self.heater.min_temp + 5.0
            t_max = self.heater.max_temp - 5.0  # default margin
            if not (t_min <= target <= t_max):
                raise gcmd.error(
                    "Target %.1f deg C outside controllable range "
                    "[%.0f, %.0f] deg C"
                    % (target, t_min, t_max))

        # Continuity validation
        start_temp = self._pending_start_temp()
        if abs(target - start_temp) < 1.0:
            raise gcmd.error(
                "Ramp target %.1f deg C is essentially equal to "
                "starting temperature %.1f deg C. Did you mean SOAK?"
                % (target, start_temp))

        # Build segment
        duration_s = duration_min * 60.0 if duration_min is not None else None
        seg = prof.Segment('ramp', target, duration_s=duration_s,
                           ramp_rate=rate)
        self._pending.segments.append(seg)

        # Feedback
        label = seg.make_label()
        if rate is not None:
            detail = "@ %.0f deg C/min" % rate
        elif duration_min is not None:
            detail = "in %.0fmin" % duration_min
        else:
            detail = "(unconstrained)"
        self._respond("Added %s %s (%d segments)"
                      % (label, detail, len(self._pending.segments)))

    def cmd_profile_soak(self, gcmd):
        if self._pending is None:
            raise gcmd.error(
                "No profile being defined. "
                "Run ANNEAL_PROFILE_BEGIN first.")

        duration_min = gcmd.get_float('DURATION')

        if duration_min < 0:
            raise gcmd.error(
                "DURATION must be >= 0, got %.1f" % duration_min)
        if duration_min > MAX_DURATION_MIN:
            raise gcmd.error(
                "DURATION %.0fmin exceeds limit (%.0fh)"
                % (duration_min, MAX_DURATION_MIN / 60))

        if not self._pending.segments:
            raise gcmd.error(
                "First segment cannot be a soak "
                "(no temperature established). Add a RAMP first.")

        prev_target = self._pending.segments[-1].target_c
        seg = prof.Segment('soak', prev_target,
                           duration_s=duration_min * 60.0)
        self._pending.segments.append(seg)

        self._respond("Added %s for %.0fmin (%d segments)"
                      % (seg.make_label(), duration_min,
                         len(self._pending.segments)))

    def cmd_profile_end(self, gcmd):
        if self._pending is None:
            raise gcmd.error("No profile being defined.")

        p = prof.Profile(self._pending.name, self._pending.description,
                         self._pending.segments)
        self._pending = None

        # Validate
        if self.heater is not None:
            errors = p.validate(self.heater.min_temp, self.heater.max_temp)
        else:
            errors = p.validate(5, 230)

        if errors:
            raise gcmd.error(
                "Profile validation failed:\n  "
                + "\n  ".join(errors))

        # Commit to runtime store
        self._profiles[p.name] = p

        # Stage for SAVE_CONFIG
        self._stage_profile_for_save(p)

        # Summary
        total_s = p.estimate_total_duration_s()
        total_m = int(total_s // 60)
        self._respond(
            "Profile '%s' defined with %d segments (~%dmin). "
            "Available for ANNEAL_START. "
            "Run SAVE_CONFIG to persist to printer.cfg."
            % (p.name, len(p.segments), total_m))

    def cmd_profile_abort(self, gcmd):
        if self._pending is None:
            raise gcmd.error("No profile being defined.")

        name = self._pending.name
        self._pending = None
        self._respond("Profile '%s' discarded." % name)

    def cmd_list_profiles(self, gcmd):
        if not self._profiles:
            self._respond("No profiles defined.")
            return

        lines = ["Available profiles:"]
        for name, p in sorted(self._profiles.items()):
            total_s = p.estimate_total_duration_s()
            total_m = int(total_s // 60)
            lines.append("  %s: %d segments, ~%dmin%s"
                         % (name, len(p.segments), total_m,
                            (" - %s" % p.description) if p.description
                            else ""))
        self._respond("\n".join(lines))

    def cmd_show_profile(self, gcmd):
        name = gcmd.get('NAME')
        p = self._profiles.get(name)
        if p is None:
            raise gcmd.error(
                "Unknown profile '%s'. "
                "Available: %s"
                % (name, ', '.join(sorted(self._profiles))))

        lines = ["Profile: %s" % p.name]
        if p.description:
            lines.append("Description: %s" % p.description)
        lines.append("Segments:")
        lines.extend("  " + line for line in p.summary_lines())
        self._respond("\n".join(lines))

    def cmd_delete_profile(self, gcmd):
        name = gcmd.get('NAME')
        if name not in self._profiles:
            raise gcmd.error("Unknown profile '%s'" % name)
        if self._sm.is_active() and self._sm.profile \
                and self._sm.profile.name == name:
            raise gcmd.error(
                "Cannot delete '%s': currently in use" % name)

        del self._profiles[name]
        self._respond("Profile '%s' deleted from runtime store. "
                      "Remove [anneal_profile %s] from printer.cfg "
                      "to delete permanently." % (name, name))

    # ── Run control commands ──────────────────────────────────────────

    def cmd_start(self, gcmd):
        name = gcmd.get('PROFILE')
        p = self._profiles.get(name)
        if p is None:
            raise gcmd.error(
                "Unknown profile '%s'. "
                "Available: %s"
                % (name, ', '.join(sorted(self._profiles))))

        try:
            first_seg = self._sm.start(p, self.reactor.monotonic())
        except RuntimeError as e:
            raise gcmd.error(str(e))

        # Read current chamber temperature
        current_temp = self._get_current_temp()

        # Set up executor for first segment
        now = self.reactor.monotonic()
        executor = SegmentExecutor(first_seg, now, current_temp)
        self._sm.set_executor(executor)

        # Initialize watchdogs
        self._watchdogs.begin_segment(first_seg, current_temp)

        # Set heater target
        sp = executor.setpoint_at(now)
        self._set_heater_target(sp)

        # Start tick loop
        self._schedule_tick(now)

        self._respond(
            "Annealing started: %s (%d segments). "
            "%s"
            % (p.name, len(p.segments), first_seg.make_label()))

    def cmd_pause(self, gcmd):
        try:
            self._sm.pause(self.reactor.monotonic())
        except RuntimeError as e:
            raise gcmd.error(str(e))
        # Heater holds at current target - intentional
        self._cancel_tick()
        self._respond("Annealing paused. Heater holding. "
                      "Run ANNEAL_RESUME to continue.")

    def cmd_resume(self, gcmd):
        try:
            now = self.reactor.monotonic()
            self._sm.resume(now)
        except RuntimeError as e:
            raise gcmd.error(str(e))
        self._schedule_tick(now)
        self._respond("Annealing resumed.")

    def cmd_cancel(self, gcmd):
        try:
            self._sm.cancel()
        except RuntimeError as e:
            raise gcmd.error(str(e))
        self._cancel_tick()
        self._set_heater_target(0)
        self._respond("Annealing cancelled. Heater off.")

    def cmd_status(self, gcmd):
        if not self._sm.is_active() and self._sm.state == 'idle':
            self._respond("Anneal: idle")
            return

        now = self.reactor.monotonic()
        temp = self._get_current_temp()
        target = self._get_heater_target()
        status = self._sm.get_status(now, temp)

        parts = [
            "State: %s" % status['state'],
        ]
        if status['profile']:
            parts.append("Profile: %s" % status['profile'])
        if status['stage']:
            s = status['stage']
            parts.append("Stage: %d/%d - %s"
                         % (s['index'], status['stage_count'] - 1,
                            s['label']))
        parts.append("Temp: %.1f deg C / target %.1f deg C" % (temp, target))

        if self._sm.executor and self._sm.is_running():
            adj = self._sm.adjusted_time(now)
            parts.append(self._sm.executor.format_status(adj, temp))

        self._respond(" | ".join(parts))

    # ── Tick loop ─────────────────────────────────────────────────────

    def _tick(self, eventtime):
        """Reactor timer callback. Called every TICK_INTERVAL."""
        try:
            return self._process_tick(eventtime)
        except Exception as e:
            self.log.exception("Error in anneal tick")
            self._emergency_stop("Tick error: %s" % str(e))
            return self.reactor.NEVER

    def _process_tick(self, eventtime):
        if not self._sm.is_running():
            return self.reactor.NEVER

        now = eventtime
        adj_time = self._sm.adjusted_time(now)
        current_temp = self._get_current_temp()
        executor = self._sm.executor
        seg = self._sm.profile.segments[self._sm.stage_index]

        # Update setpoint
        sp = executor.setpoint_at(adj_time)
        self._set_heater_target(sp)

        # Run watchdog
        elapsed = executor.elapsed_s(adj_time)
        wd_error = self._watchdogs.check(
            self._sm.state, elapsed, current_temp, seg.target_c)
        if wd_error:
            self._emergency_stop(wd_error)
            return self.reactor.NEVER

        # Periodic status logging
        if now - self._last_status_log >= STATUS_LOG_INTERVAL:
            self._last_status_log = now
            status_str = executor.format_status(adj_time, current_temp)
            self._respond("[%s] %s" % (self._sm.profile.name, status_str))

        # Check segment completion
        if executor.is_complete(adj_time, current_temp):
            next_seg = self._sm.advance_stage(now, current_temp)
            if next_seg is None:
                # Profile complete
                self._finish_profile(current_temp)
                return self.reactor.NEVER
            else:
                # Start next segment
                new_executor = SegmentExecutor(
                    next_seg, adj_time, current_temp)
                self._sm.set_executor(new_executor)
                self._watchdogs.begin_segment(next_seg, current_temp)
                sp = new_executor.setpoint_at(adj_time)
                self._set_heater_target(sp)
                self._respond(
                    "Stage %d/%d: %s"
                    % (self._sm.stage_index,
                       len(self._sm.profile.segments) - 1,
                       next_seg.make_label()))

        return eventtime + TICK_INTERVAL

    def _finish_profile(self, current_temp):
        """Handle end of profile - Mode A or Mode B."""
        profile = self._sm.profile
        last_seg = profile.segments[-1]

        # Turn off heater
        self._set_heater_target(0)

        # Determine completion message
        if last_seg.kind == 'soak':
            # Mode A: ended with soak
            msg = ("Annealing complete (%s). "
                   "Chamber at %.1f deg C. Heater off. "
                   "Open the door for faster cooldown, "
                   "or wait for natural cooldown."
                   % (profile.name, current_temp))
        else:
            # Mode B: ended with descending ramp
            msg = ("Annealing complete (%s). "
                   "Chamber at %.1f deg C (target was %.0f deg C). "
                   "Safe to open."
                   % (profile.name, current_temp, last_seg.target_c))

        self._sm.complete()
        self._cancel_tick()
        self._respond(msg)

    def _emergency_stop(self, reason):
        """Abort the run due to a safety condition."""
        self._cancel_tick()
        self._set_heater_target(0)
        self._sm.error(reason)
        self._respond("!! ANNEAL ABORT: %s" % reason)
        self.log.error("ANNEAL ABORT: %s", reason)

    # ── Timer management ──────────────────────────────────────────────

    def _schedule_tick(self, now):
        if self._timer is not None:
            self.reactor.update_timer(self._timer, now + TICK_INTERVAL)
        else:
            self._timer = self.reactor.register_timer(
                self._tick, now + TICK_INTERVAL)

    def _cancel_tick(self):
        if self._timer is not None:
            self.reactor.unregister_timer(self._timer)
            self._timer = None

    # ── Heater interaction ────────────────────────────────────────────

    def _get_current_temp(self):
        """Read current chamber temperature."""
        if self.heater is None:
            return 0.0
        now = self.reactor.monotonic()
        temp, _ = self.heater.get_temp(now)
        return temp

    def _get_heater_target(self):
        """Read current heater target."""
        if self.heater is None:
            return 0.0
        now = self.reactor.monotonic()
        _, target = self.heater.get_temp(now)
        return target

    def _set_heater_target(self, target):
        """Set heater target temperature."""
        if self.heater is not None:
            self.heater.set_temp(target)

    # ── Profile persistence ───────────────────────────────────────────

    def _stage_profile_for_save(self, p):
        """Write profile to configfile for SAVE_CONFIG persistence."""
        try:
            configfile = self.printer.lookup_object('configfile')
            section_name = "anneal_profile %s" % p.name
            values = prof.format_profile_for_config(p)
            for key, val in values.items():
                configfile.set(section_name, key, val)
        except Exception as e:
            self.log.warning(
                "Could not stage profile '%s' for save: %s", p.name, e)

    # ── Helpers ───────────────────────────────────────────────────────

    def _pending_start_temp(self):
        """What temperature will the next pending segment start at?"""
        if not self._pending or not self._pending.segments:
            return 22.0  # assumed ambient for validation
        return self._pending.segments[-1].target_c

    def _respond(self, msg):
        self.gcode.respond_info(msg)

    # ── Moonraker status ──────────────────────────────────────────────

    def get_status(self, eventtime):
        temp = self._get_current_temp()
        return self._sm.get_status(eventtime, temp)


def load_config(config):
    return AnnealManager(config)
