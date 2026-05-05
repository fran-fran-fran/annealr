# kalico-anneal: Annealing and drying controller for Kalico/Klipper
#
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# SPDX-License-Identifier: GPL-3.0-or-later
#
# File: profile.py
# Description: Profile and Segment data classes, config parsing,
#              validation, serialization, and persistence.

import logging


class Segment:
    """A single segment in an annealing profile.

    Ramp segments move from the previous endpoint to target_c.
    Soak segments hold at target_c for duration_s.
    """
    __slots__ = ('kind', 'target_c', 'duration_s', 'ramp_rate')

    def __init__(self, kind, target_c, duration_s=None, ramp_rate=None):
        if kind not in ('ramp', 'soak'):
            raise ValueError("Segment kind must be 'ramp' or 'soak'")
        self.kind = kind
        self.target_c = float(target_c)
        self.duration_s = float(duration_s) if duration_s is not None else None
        self.ramp_rate = float(ramp_rate) if ramp_rate is not None else None

    def is_descending_from(self, start_temp_c):
        """True if this segment's target is below the starting temperature."""
        return self.kind == 'ramp' and self.target_c < start_temp_c - 1.0

    def compute_duration_s(self, start_temp_c):
        """Compute segment duration from context.

        For ramps with RATE: duration = abs(delta_T) / rate * 60
        For ramps with DURATION: already set
        For ramps with neither (unconstrained): returns None (ETA unknown)
        For soaks: already set
        """
        if self.kind == 'soak':
            return self.duration_s
        if self.duration_s is not None:
            return self.duration_s
        if self.ramp_rate is not None and self.ramp_rate > 0:
            delta = abs(self.target_c - start_temp_c)
            return (delta / self.ramp_rate) * 60.0
        return None  # unconstrained ramp

    def compute_ramp_rate(self, start_temp_c):
        """Compute ramp rate from context (deg C/min).

        Returns None for unconstrained ramps or soaks.
        """
        if self.kind == 'soak':
            return None
        if self.ramp_rate is not None:
            return self.ramp_rate
        if self.duration_s is not None and self.duration_s > 0:
            delta = abs(self.target_c - start_temp_c)
            return delta / (self.duration_s / 60.0)
        return None

    def make_label(self):
        """Generate a descriptive label from segment data."""
        if self.kind == 'ramp':
            return "Ramp to %.0f deg C" % self.target_c
        else:
            return "Soak at %.0f deg C" % self.target_c

    def __repr__(self):
        parts = ["Segment(%s, target=%.1f" % (self.kind, self.target_c)]
        if self.duration_s is not None:
            parts.append("duration=%.0fs" % self.duration_s)
        if self.ramp_rate is not None:
            parts.append("rate=%.1f deg C/min" % self.ramp_rate)
        return ", ".join(parts) + ")"

    def __eq__(self, other):
        if not isinstance(other, Segment):
            return NotImplemented
        return (self.kind == other.kind
                and self.target_c == other.target_c
                and self.duration_s == other.duration_s
                and self.ramp_rate == other.ramp_rate)


class Profile:
    """An annealing profile: a named, ordered sequence of segments."""
    __slots__ = ('name', 'description', 'segments')

    def __init__(self, name, description="", segments=None):
        self.name = str(name)
        self.description = str(description)
        self.segments = list(segments) if segments else []

    def validate(self, min_temp, max_temp, max_temp_margin=5.0):
        """Validate the complete profile. Returns list of error strings."""
        errors = []
        if not self.segments:
            errors.append("Profile has no segments")
            return errors

        if self.segments[0].kind == 'soak':
            errors.append(
                "First segment cannot be a soak "
                "(no temperature established yet)")

        t_min = min_temp + 5.0
        t_max = max_temp - max_temp_margin

        current_temp = None
        for i, seg in enumerate(self.segments):
            if seg.kind == 'ramp':
                if not (t_min <= seg.target_c <= t_max):
                    errors.append(
                        "Segment %d: target %.1f deg C outside controllable "
                        "range [%.0f, %.0f] deg C"
                        % (i, seg.target_c, t_min, t_max))

                if current_temp is not None:
                    if abs(seg.target_c - current_temp) < 1.0:
                        errors.append(
                            "Segment %d: ramp target %.1f deg C is "
                            "essentially equal to current %.1f deg C "
                            "(use soak instead)"
                            % (i, seg.target_c, current_temp))

                current_temp = seg.target_c

            elif seg.kind == 'soak':
                if current_temp is None:
                    errors.append(
                        "Segment %d: soak without prior temperature"
                        % i)
                if seg.duration_s is not None and seg.duration_s < 0:
                    errors.append(
                        "Segment %d: negative soak duration" % i)
                # soak target inherits from previous; keep current_temp

        return errors

    def estimate_total_duration_s(self, start_temp_c=22.0):
        """Estimate total profile duration in seconds.

        For unconstrained ramps, duration is unknown and those segments
        contribute 0 to the estimate (making this a lower bound).
        """
        total = 0.0
        current_temp = start_temp_c
        for seg in self.segments:
            d = seg.compute_duration_s(current_temp)
            if d is not None:
                total += d
            if seg.kind == 'ramp':
                current_temp = seg.target_c
        return total

    def summary_lines(self, start_temp_c=22.0):
        """Generate human-readable summary lines for the profile."""
        lines = []
        current_temp = start_temp_c
        for i, seg in enumerate(self.segments):
            label = seg.make_label()
            d = seg.compute_duration_s(current_temp)

            if seg.kind == 'ramp':
                if seg.ramp_rate is not None:
                    detail = "@ %.0f deg C/min" % seg.ramp_rate
                elif seg.duration_s is not None:
                    detail = "in %.0fmin" % (seg.duration_s / 60.0)
                else:
                    detail = "(unconstrained)"

                eta = "[~%.0fmin]" % (d / 60.0) if d else "[ETA unknown]"
                lines.append(
                    "%2d. %-22s ramp  -> %.0f deg C %s  %s"
                    % (i, label, seg.target_c, detail, eta))
                current_temp = seg.target_c

            elif seg.kind == 'soak':
                lines.append(
                    "%2d. %-22s soak  @ %.0f deg C       [%.0fmin]"
                    % (i, label, seg.target_c, (d or 0) / 60.0))

        total = self.estimate_total_duration_s(start_temp_c)
        hours = int(total // 3600)
        mins = int((total % 3600) // 60)
        if hours > 0:
            lines.append("Total: ~%dh %dmin" % (hours, mins))
        else:
            lines.append("Total: ~%dmin" % mins)

        return lines

    def __repr__(self):
        return "Profile(%s, %d segments)" % (self.name, len(self.segments))


# ── Config section parsing ────────────────────────────────────────────────

def parse_segment_line(line):
    """Parse a single segment line from the config format.

    Format: kind, value [, rate=X] [, duration=X]
    Examples:
        ramp,  80, rate=15
        soak,  30
        ramp, 130, duration=10
        ramp,  45
    """
    parts = [p.strip() for p in line.split(',')]
    if len(parts) < 2:
        raise ValueError(
            "Segment line must have at least 'kind, value': %s" % line)

    kind = parts[0].lower()
    if kind not in ('ramp', 'soak'):
        raise ValueError("Unknown segment kind '%s'" % kind)

    try:
        value = float(parts[1])
    except ValueError:
        raise ValueError(
            "Cannot parse value '%s' as number in: %s" % (parts[1], line))

    rate = None
    duration = None

    for part in parts[2:]:
        part = part.strip()
        if part.startswith('rate='):
            try:
                rate = float(part[5:])
            except ValueError:
                raise ValueError(
                    "Cannot parse rate value in: %s" % part)
        elif part.startswith('duration='):
            try:
                duration = float(part[9:])
            except ValueError:
                raise ValueError(
                    "Cannot parse duration value in: %s" % part)
        elif part:
            raise ValueError("Unknown segment parameter: %s" % part)

    if kind == 'ramp':
        target_c = value
        duration_s = duration * 60.0 if duration is not None else None
        return Segment('ramp', target_c, duration_s=duration_s, ramp_rate=rate)
    else:
        # soak: value is duration in minutes
        duration_s = value * 60.0
        return Segment('soak', 0.0, duration_s=duration_s)


def load_profile_from_config(config):  # pragma: no cover
    """Load a Profile from a [anneal_profile <name>] config section.
    Requires Klipper ConfigWrapper; covered by Tier 2 integration tests.

    Args:
        config: Klipper ConfigWrapper for the section.
    Returns:
        Profile instance.
    """
    name = config.get_name().split(None, 1)[1]
    description = config.get('description', '')
    segments_raw = config.get('segments')

    segments = []
    prev_target = None
    for line in segments_raw.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        seg = parse_segment_line(line)
        # For soaks, inherit target from previous ramp
        if seg.kind == 'soak' and prev_target is not None:
            seg.target_c = prev_target
        if seg.kind == 'ramp':
            prev_target = seg.target_c
        segments.append(seg)

    return Profile(name, description, segments)


def format_profile_for_config(profile):
    """Serialize a Profile to a config-section-ready string.

    Returns a dict of {key: value} pairs suitable for configfile.set().
    """
    lines = []
    for seg in profile.segments:
        if seg.kind == 'ramp':
            parts = ["ramp", "%.1f" % seg.target_c]
            if seg.ramp_rate is not None:
                parts.append("rate=%.1f" % seg.ramp_rate)
            elif seg.duration_s is not None:
                parts.append("duration=%.1f" % (seg.duration_s / 60.0))
            lines.append(", ".join(parts))
        elif seg.kind == 'soak':
            duration_min = (seg.duration_s or 0) / 60.0
            lines.append("soak, %.1f" % duration_min)

    segments_str = "\n".join("    " + line for line in lines)

    result = {}
    if profile.description:
        result['description'] = profile.description
    result['segments'] = "\n" + segments_str

    return result
