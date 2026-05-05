# Unit tests for annealr.profile
import pytest
from annealr.profile import (
    Segment, Profile, parse_segment_line, format_profile_for_config,
)


# ── Segment construction ─────────────────────────────────────────────────

class TestSegment:
    def test_ramp_with_rate(self):
        seg = Segment('ramp', 200, ramp_rate=6)
        assert seg.kind == 'ramp'
        assert seg.target_c == 200.0
        assert seg.ramp_rate == 6.0
        assert seg.duration_s is None

    def test_ramp_with_duration(self):
        seg = Segment('ramp', 200, duration_s=720)
        assert seg.kind == 'ramp'
        assert seg.target_c == 200.0
        assert seg.duration_s == 720.0
        assert seg.ramp_rate is None

    def test_ramp_unconstrained(self):
        seg = Segment('ramp', 85)
        assert seg.ramp_rate is None
        assert seg.duration_s is None

    def test_soak(self):
        seg = Segment('soak', 130, duration_s=2700)
        assert seg.kind == 'soak'
        assert seg.target_c == 130.0
        assert seg.duration_s == 2700.0

    def test_soak_zero_duration(self):
        # Zero duration soak is allowed (end-of-profile marker)
        seg = Segment('soak', 85, duration_s=0)
        assert seg.duration_s == 0.0

    def test_invalid_kind(self):
        with pytest.raises(ValueError, match="must be 'ramp' or 'soak'"):
            Segment('cool', 100)

    def test_invalid_kind_empty_string(self):
        with pytest.raises(ValueError, match="must be 'ramp' or 'soak'"):
            Segment('', 100)

    def test_target_stored_as_float(self):
        seg = Segment('ramp', 200)
        assert isinstance(seg.target_c, float)

    def test_equality(self):
        a = Segment('ramp', 200, ramp_rate=6)
        b = Segment('ramp', 200, ramp_rate=6)
        assert a == b

    def test_inequality_different_rate(self):
        a = Segment('ramp', 200, ramp_rate=6)
        b = Segment('ramp', 200, ramp_rate=8)
        assert a != b

    def test_inequality_different_kind(self):
        a = Segment('ramp', 200, duration_s=600)
        b = Segment('soak', 200, duration_s=600)
        assert a != b

    def test_inequality_different_target(self):
        a = Segment('ramp', 200, ramp_rate=6)
        b = Segment('ramp', 150, ramp_rate=6)
        assert a != b


class TestSegmentComputations:
    def test_compute_duration_from_rate(self):
        seg = Segment('ramp', 200, ramp_rate=10)
        # From 100 deg C to 200 deg C at 10 deg C/min = 10 min = 600s
        d = seg.compute_duration_s(100.0)
        assert d == pytest.approx(600.0)

    def test_compute_duration_from_rate_descending(self):
        seg = Segment('ramp', 45, ramp_rate=5)
        # From 200 deg C to 45 deg C = 155 deg C / 5 deg C/min = 31 min = 1860s
        d = seg.compute_duration_s(200.0)
        assert d == pytest.approx(1860.0)

    def test_compute_duration_explicit(self):
        seg = Segment('ramp', 200, duration_s=900)
        d = seg.compute_duration_s(100.0)
        assert d == 900.0

    def test_compute_duration_unconstrained(self):
        seg = Segment('ramp', 200)
        d = seg.compute_duration_s(100.0)
        assert d is None

    def test_compute_duration_soak(self):
        seg = Segment('soak', 130, duration_s=2700)
        d = seg.compute_duration_s(130.0)
        assert d == 2700.0

    def test_compute_duration_soak_zero(self):
        seg = Segment('soak', 85, duration_s=0)
        d = seg.compute_duration_s(85.0)
        assert d == 0.0

    def test_compute_rate_from_duration(self):
        seg = Segment('ramp', 200, duration_s=600)
        # From 100 deg C to 200 deg C in 600s = 10 deg C/min
        r = seg.compute_ramp_rate(100.0)
        assert r == pytest.approx(10.0)

    def test_compute_rate_explicit(self):
        seg = Segment('ramp', 200, ramp_rate=6)
        r = seg.compute_ramp_rate(100.0)
        assert r == 6.0

    def test_compute_rate_unconstrained(self):
        seg = Segment('ramp', 200)
        r = seg.compute_ramp_rate(100.0)
        assert r is None

    def test_compute_rate_soak_returns_none(self):
        seg = Segment('soak', 130, duration_s=3600)
        r = seg.compute_ramp_rate(130.0)
        assert r is None

    @pytest.mark.parametrize("target,start,expected", [
        (45,  200.0, True),   # clearly descending
        (200, 45.0,  False),  # clearly ascending
        (85,  85.5,  False),  # within 1 deg tolerance, not descending
        (45,  46.5,  True),   # delta=1.5 > 1.0 threshold, is descending
        (44,  46.0,  True),   # 2 deg below, is descending
    ])
    def test_is_descending_parametrized(self, target, start, expected):
        seg = Segment('ramp', target)
        assert seg.is_descending_from(start) is expected

    def test_make_label_ramp(self):
        seg = Segment('ramp', 200)
        assert seg.make_label() == "Ramp to 200 deg C"

    def test_make_label_soak(self):
        seg = Segment('soak', 130, duration_s=2700)
        assert seg.make_label() == "Soak at 130 deg C"

    @pytest.mark.parametrize("target,expected", [
        (85,  "Ramp to 85 deg C"),
        (200, "Ramp to 200 deg C"),
        (45,  "Ramp to 45 deg C"),
    ])
    def test_make_label_ramp_various_targets(self, target, expected):
        seg = Segment('ramp', target)
        assert seg.make_label() == expected


# ── Profile validation ───────────────────────────────────────────────────

class TestProfileValidation:
    def test_empty_profile(self):
        p = Profile("test", segments=[])
        errors = p.validate(5, 230)
        assert any("no segments" in e for e in errors)

    def test_starts_with_soak(self):
        p = Profile("test", segments=[
            Segment('soak', 85, duration_s=3600),
        ])
        errors = p.validate(5, 230)
        assert any("First segment cannot be a soak" in e for e in errors)

    def test_target_out_of_range_high(self):
        p = Profile("test", segments=[
            Segment('ramp', 250, ramp_rate=10),
        ])
        errors = p.validate(5, 230, max_temp_margin=5)
        assert any("outside controllable range" in e for e in errors)

    def test_target_out_of_range_low(self):
        p = Profile("test", segments=[
            Segment('ramp', 5, ramp_rate=10),
        ])
        errors = p.validate(5, 230)
        assert any("outside controllable range" in e for e in errors)

    def test_target_at_exact_boundary_high(self):
        # max_temp=230, margin=5 -> ceiling=225; target=225 should pass
        p = Profile("test", segments=[
            Segment('ramp', 225, ramp_rate=10),
        ])
        errors = p.validate(5, 230, max_temp_margin=5)
        assert not any("outside controllable range" in e for e in errors)

    def test_target_just_above_boundary_high(self):
        # target=226 should fail
        p = Profile("test", segments=[
            Segment('ramp', 226, ramp_rate=10),
        ])
        errors = p.validate(5, 230, max_temp_margin=5)
        assert any("outside controllable range" in e for e in errors)

    def test_ramp_to_same_temp(self):
        p = Profile("test", segments=[
            Segment('ramp', 85, ramp_rate=10),
            Segment('ramp', 85.3, ramp_rate=5),
        ])
        errors = p.validate(5, 230)
        assert any("essentially equal" in e for e in errors)

    def test_valid_profile_no_errors(self):
        p = Profile("pps_cf", segments=[
            Segment('ramp', 80, ramp_rate=15),
            Segment('soak', 80, duration_s=1800),
            Segment('ramp', 200, ramp_rate=6),
            Segment('soak', 200, duration_s=7200),
            Segment('ramp', 45),
        ])
        errors = p.validate(5, 230)
        assert errors == []

    def test_valid_profile_returns_list(self):
        p = Profile("test", segments=[
            Segment('ramp', 85, ramp_rate=10),
        ])
        errors = p.validate(5, 230)
        assert isinstance(errors, list)

    def test_multiple_validation_errors_returned(self):
        # Both too-high target and starts-with-soak
        p = Profile("test", segments=[
            Segment('soak', 85, duration_s=3600),
            Segment('ramp', 300, ramp_rate=10),
        ])
        errors = p.validate(5, 230)
        assert len(errors) >= 2


# ── Profile estimates ─────────────────────────────────────────────────────

class TestProfileEstimates:
    def test_total_duration(self):
        p = Profile("test", segments=[
            Segment('ramp', 80, ramp_rate=15),
            Segment('soak', 80, duration_s=1800),
            Segment('ramp', 200, ramp_rate=6),
            Segment('soak', 200, duration_s=7200),
        ])
        total = p.estimate_total_duration_s(start_temp_c=22.0)
        # ~3.9*60 + 1800 + 20*60 + 7200 = 234 + 1800 + 1200 + 7200 = 10434s
        assert total > 10000
        assert total < 11000

    def test_unconstrained_ramp_contributes_zero(self):
        p = Profile("test", segments=[
            Segment('ramp', 85, ramp_rate=15),
            Segment('soak', 85, duration_s=3600),
            Segment('ramp', 45),  # unconstrained
        ])
        total = p.estimate_total_duration_s(22.0)
        ramp_time = abs(85 - 22) / 15 * 60
        assert total == pytest.approx(ramp_time + 3600, abs=1.0)

    def test_single_segment_duration(self):
        p = Profile("test", segments=[
            Segment('soak', 85, duration_s=3600),
        ])
        total = p.estimate_total_duration_s(85.0)
        assert total == pytest.approx(3600.0)

    def test_summary_lines_structure(self):
        p = Profile("test", segments=[
            Segment('ramp', 85, ramp_rate=15),
            Segment('soak', 85, duration_s=3600),
        ])
        lines = p.summary_lines()
        assert len(lines) >= 3  # 2 segments + total line
        assert "Ramp to 85" in lines[0]
        assert "Soak at 85" in lines[1]
        assert "Total:" in lines[-1]

    def test_summary_lines_total_format(self):
        p = Profile("test", segments=[
            Segment('soak', 85, duration_s=7200),  # 120 min = 2h
        ])
        lines = p.summary_lines(85.0)
        total_line = lines[-1]
        assert "Total:" in total_line
        assert "2h" in total_line


# ── Config parsing ────────────────────────────────────────────────────────

class TestParseSegmentLine:
    def test_ramp_with_rate(self):
        seg = parse_segment_line("ramp, 200, rate=6")
        assert seg.kind == 'ramp'
        assert seg.target_c == 200.0
        assert seg.ramp_rate == 6.0
        assert seg.duration_s is None

    def test_ramp_with_duration(self):
        seg = parse_segment_line("ramp, 130, duration=10")
        assert seg.kind == 'ramp'
        assert seg.target_c == 130.0
        assert seg.duration_s == 600.0  # 10 min * 60
        assert seg.ramp_rate is None

    def test_ramp_unconstrained(self):
        seg = parse_segment_line("ramp, 45")
        assert seg.kind == 'ramp'
        assert seg.target_c == 45.0
        assert seg.ramp_rate is None
        assert seg.duration_s is None

    def test_soak(self):
        seg = parse_segment_line("soak, 30")
        assert seg.kind == 'soak'
        assert seg.duration_s == 1800.0  # 30 min * 60

    def test_soak_zero_duration(self):
        seg = parse_segment_line("soak, 0")
        assert seg.kind == 'soak'
        assert seg.duration_s == 0.0

    def test_whitespace_tolerance(self):
        seg = parse_segment_line("  ramp , 80 , rate=15  ")
        assert seg.kind == 'ramp'
        assert seg.target_c == 80.0
        assert seg.ramp_rate == 15.0

    def test_uppercase_kind(self):
        # Parser lowercases the kind, so RAMP is treated as ramp
        seg = parse_segment_line("RAMP, 200")
        assert seg.kind == 'ramp'
        assert seg.target_c == 200.0

    def test_unknown_kind(self):
        with pytest.raises(ValueError, match="Unknown segment kind"):
            parse_segment_line("cool, 45")

    def test_too_few_parts(self):
        with pytest.raises(ValueError, match="at least"):
            parse_segment_line("ramp")

    def test_bad_value(self):
        with pytest.raises(ValueError, match="Cannot parse value"):
            parse_segment_line("ramp, abc")

    def test_unknown_param(self):
        with pytest.raises(ValueError, match="Unknown segment parameter"):
            parse_segment_line("ramp, 200, speed=5")

    def test_bad_rate_value(self):
        with pytest.raises(ValueError, match="Cannot parse rate"):
            parse_segment_line("ramp, 200, rate=fast")

    def test_bad_duration_value(self):
        with pytest.raises(ValueError, match="Cannot parse duration"):
            parse_segment_line("ramp, 200, duration=long")

    @pytest.mark.parametrize("line,expected_target,expected_rate", [
        ("ramp, 80, rate=15",  80.0,  15.0),
        ("ramp, 130, rate=8",  130.0, 8.0),
        ("ramp, 200, rate=6",  200.0, 6.0),
        ("ramp, 45, rate=3",   45.0,  3.0),
    ])
    def test_ramp_parsing_parametrized(self, line, expected_target, expected_rate):
        seg = parse_segment_line(line)
        assert seg.target_c == expected_target
        assert seg.ramp_rate == expected_rate


# ── Round-trip ────────────────────────────────────────────────────────────

class TestFormatProfileForConfig:
    def test_round_trip(self):
        p = Profile("test", "A test profile", [
            Segment('ramp', 80, ramp_rate=15),
            Segment('soak', 80, duration_s=1800),
            Segment('ramp', 200, ramp_rate=6),
            Segment('soak', 200, duration_s=7200),
            Segment('ramp', 45),
        ])
        result = format_profile_for_config(p)
        assert 'segments' in result
        assert isinstance(result['segments'], str)

        # Parse back
        segments_str = result['segments']
        rebuilt = []
        prev_target = None
        for line in segments_str.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            seg = parse_segment_line(line)
            if seg.kind == 'soak' and prev_target is not None:
                seg.target_c = prev_target
            if seg.kind == 'ramp':
                prev_target = seg.target_c
            rebuilt.append(seg)

        assert len(rebuilt) == len(p.segments)
        for orig, parsed in zip(p.segments, rebuilt):
            assert orig.kind == parsed.kind
            assert orig.target_c == pytest.approx(parsed.target_c, abs=0.2)
            if orig.ramp_rate is not None:
                assert orig.ramp_rate == pytest.approx(parsed.ramp_rate, abs=0.2)

    def test_description_included_when_present(self):
        p = Profile("test", "My description", [
            Segment('ramp', 85, ramp_rate=10),
        ])
        result = format_profile_for_config(p)
        assert 'description' in result
        assert result['description'] == "My description"

    def test_description_omitted_when_empty(self):
        p = Profile("test", "", [
            Segment('ramp', 85, ramp_rate=10),
        ])
        result = format_profile_for_config(p)
        assert 'description' not in result


    def test_round_trip_duration_based_ramp(self):
        # Ramp with explicit duration (not rate) — covers format_profile_for_config
        # duration branch (line 298)
        p = Profile("test", "", [
            Segment('ramp', 200, duration_s=720),
            Segment('soak', 200, duration_s=3600),
        ])
        result = format_profile_for_config(p)
        assert 'segments' in result
        # The serialized form should contain duration= not rate=
        assert 'duration=' in result['segments']
        assert 'rate=' not in result['segments']


# ── Repr ──────────────────────────────────────────────────────────────────

class TestSegmentRepr:
    def test_repr_ramp_with_rate(self):
        seg = Segment('ramp', 200, ramp_rate=6)
        r = repr(seg)
        assert 'ramp' in r
        assert '200' in r
        assert '6' in r

    def test_repr_ramp_with_duration(self):
        seg = Segment('ramp', 200, duration_s=600)
        r = repr(seg)
        assert 'ramp' in r
        assert '600' in r

    def test_repr_ramp_unconstrained(self):
        seg = Segment('ramp', 85)
        r = repr(seg)
        assert 'ramp' in r
        assert '85' in r

    def test_repr_soak(self):
        seg = Segment('soak', 130, duration_s=3600)
        r = repr(seg)
        assert 'soak' in r
        assert '130' in r

    def test_repr_is_string(self):
        seg = Segment('ramp', 200, ramp_rate=6)
        assert isinstance(repr(seg), str)

    def test_eq_with_non_segment_returns_not_implemented(self):
        seg = Segment('ramp', 200, ramp_rate=6)
        result = seg.__eq__("not a segment")
        assert result is NotImplemented

    def test_eq_with_none_returns_not_implemented(self):
        seg = Segment('ramp', 200, ramp_rate=6)
        result = seg.__eq__(None)
        assert result is NotImplemented

    def test_profile_repr(self):
        p = Profile("pps_cf", segments=[
            Segment('ramp', 85, ramp_rate=10),
        ])
        r = repr(p)
        assert 'pps_cf' in r
        assert '1' in r


class TestProfileReprAndSummary:
    def test_summary_lines_duration_based_ramp(self):
        # Ramp with DURATION rather than RATE
        p = Profile("test", segments=[
            Segment('ramp', 200, duration_s=720),  # 12 min
        ])
        lines = p.summary_lines(100.0)
        # Should show duration-based detail
        assert any("12" in line or "min" in line for line in lines)

    def test_summary_lines_unconstrained_ramp(self):
        p = Profile("test", segments=[
            Segment('ramp', 85),  # unconstrained
        ])
        lines = p.summary_lines(22.0)
        assert any("unconstrained" in line.lower() or "ETA unknown" in line
                   for line in lines)

    def test_summary_lines_long_profile_shows_hours(self):
        # Profile longer than 60 min should show hours format
        p = Profile("test", segments=[
            Segment('soak', 85, duration_s=7200),  # 120 min = 2h
        ])
        lines = p.summary_lines(85.0)
        total_line = lines[-1]
        assert "2h" in total_line

    def test_summary_lines_short_profile_no_hours(self):
        # Profile under 60 min should not show hours
        p = Profile("test", segments=[
            Segment('soak', 85, duration_s=1800),  # 30 min
        ])
        lines = p.summary_lines(85.0)
        total_line = lines[-1]
        assert "h" not in total_line


class TestValidationNegativeDuration:
    def test_negative_soak_duration_caught(self):
        # Soak with negative duration - possible via hand-edited printer.cfg
        p = Profile("test", segments=[
            Segment('ramp', 85, ramp_rate=10),
            Segment('soak', 85, duration_s=-60),
        ])
        errors = p.validate(5, 230)
        assert any("negative" in e.lower() for e in errors)
