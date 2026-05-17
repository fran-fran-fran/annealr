# Unit tests for annealr.watchdogs
import pytest
from annealr.profile import Segment
from annealr.watchdogs import (
    RampTimeoutWatchdog, SoakDriftWatchdog, CoolStallWatchdog,
    WatchdogManager,
)


class TestRampTimeout:
    def test_no_timeout_within_limit(self):
        wd = RampTimeoutWatchdog(safety_factor=3.0)
        seg = Segment('ramp', 200, ramp_rate=10)
        wd.begin(seg, 100.0)
        # Expected: 100 deg C / 10 deg C/min * 60 = 600s, limit = 1800s
        assert wd.check(500, 150.0, 200.0) is None
        assert wd.check(1799, 195.0, 200.0) is None

    def test_timeout_returns_string(self):
        wd = RampTimeoutWatchdog(safety_factor=3.0)
        seg = Segment('ramp', 200, ramp_rate=10)
        wd.begin(seg, 100.0)
        result = wd.check(1801, 150.0, 200.0)
        assert isinstance(result, str)
        assert "Ramp timeout" in result
        assert "1801" in result or "1800" in result or "elapsed" in result.lower()

    def test_timeout_message_contains_target(self):
        wd = RampTimeoutWatchdog(safety_factor=3.0)
        seg = Segment('ramp', 200, ramp_rate=10)
        wd.begin(seg, 100.0)
        result = wd.check(1801, 150.0, 200.0)
        assert "200" in result

    def test_timeout_exact_boundary(self):
        wd = RampTimeoutWatchdog(safety_factor=3.0)
        seg = Segment('ramp', 200, ramp_rate=10)
        wd.begin(seg, 100.0)
        # limit = 600 * 3 = 1800s; at exactly 1800s, should not trigger
        assert wd.check(1800, 195.0, 200.0) is None
        # at 1801s, should trigger
        result = wd.check(1801, 195.0, 200.0)
        assert result is not None

    def test_unconstrained_ramp_never_times_out(self):
        wd = RampTimeoutWatchdog(safety_factor=3.0)
        seg = Segment('ramp', 200)
        wd.begin(seg, 100.0)
        assert wd.check(99999, 150.0, 200.0) is None

    def test_reset_clears_timeout(self):
        wd = RampTimeoutWatchdog(safety_factor=3.0)
        seg = Segment('ramp', 200, ramp_rate=10)
        wd.begin(seg, 100.0)
        wd.reset()
        # After reset, no timeout even with large elapsed time
        assert wd.check(99999, 100.0, 200.0) is None

    @pytest.mark.parametrize("safety_factor,elapsed,should_trigger", [
        (2.0, 1200, False),  # 600 * 2 = 1200, exactly at limit
        (2.0, 1201, True),   # just over
        (3.0, 1800, False),  # 600 * 3 = 1800, at limit
        (3.0, 1801, True),   # just over
    ])
    def test_safety_factor_parametrized(self, safety_factor, elapsed, should_trigger):
        wd = RampTimeoutWatchdog(safety_factor=safety_factor)
        seg = Segment('ramp', 200, ramp_rate=10)
        wd.begin(seg, 100.0)
        result = wd.check(elapsed, 150.0, 200.0)
        assert (result is not None) == should_trigger


class TestSoakDrift:
    def test_no_drift_within_band_returns_none(self):
        wd = SoakDriftWatchdog(drift_limit_c=10.0, max_consecutive=6)
        for _ in range(20):
            assert wd.check(135.0, 130.0) is None

    def test_drift_at_limit_does_not_trigger(self):
        # Exactly at drift_limit_c should not trigger
        wd = SoakDriftWatchdog(drift_limit_c=10.0, max_consecutive=6)
        for _ in range(10):
            assert wd.check(140.0, 130.0) is None  # 10.0 deg, not > 10.0

    def test_drift_above_limit_counts(self):
        wd = SoakDriftWatchdog(drift_limit_c=10.0, max_consecutive=6)
        for i in range(5):
            assert wd.check(141.0, 130.0) is None  # 11 deg drift, counts
        result = wd.check(141.0, 130.0)  # 6th consecutive
        assert result is not None

    def test_drift_message_contains_expected_fields(self):
        wd = SoakDriftWatchdog(drift_limit_c=10.0, max_consecutive=3)
        for _ in range(2):
            wd.check(145.0, 130.0)
        result = wd.check(145.0, 130.0)
        assert isinstance(result, str)
        assert "drift" in result.lower() or "Soak" in result
        assert "130" in result  # target temperature

    def test_recovery_resets_counter(self):
        wd = SoakDriftWatchdog(drift_limit_c=10.0, max_consecutive=6)
        for _ in range(4):
            wd.check(145.0, 130.0)
        wd.check(132.0, 130.0)  # back in band
        for _ in range(4):
            assert wd.check(145.0, 130.0) is None  # counter reset

    def test_reset_clears_counter(self):
        wd = SoakDriftWatchdog(drift_limit_c=10.0, max_consecutive=3)
        for _ in range(2):
            wd.check(145.0, 130.0)
        wd.reset()
        # Needs 3 more consecutive to trigger
        for _ in range(2):
            assert wd.check(145.0, 130.0) is None

    def test_negative_drift_also_triggers(self):
        # Drift below target should also be caught
        wd = SoakDriftWatchdog(drift_limit_c=10.0, max_consecutive=3)
        for _ in range(2):
            wd.check(119.0, 130.0)  # 11 deg below target
        result = wd.check(119.0, 130.0)
        assert result is not None


class TestCoolStall:
    def test_no_stall_with_good_cooling_returns_none(self):
        wd = CoolStallWatchdog(min_drop_c=5.0, check_window_s=300.0,
                               grace_period_s=120.0)
        wd.begin(200.0)
        # After 300s, dropped 30 deg C - well above minimum
        assert wd.check(300.0, 170.0) is None

    def test_stall_returns_string(self):
        wd = CoolStallWatchdog(min_drop_c=5.0, check_window_s=300.0,
                               grace_period_s=120.0)
        wd.begin(200.0)
        result = wd.check(300.0, 198.0)  # only dropped 2 deg C
        assert isinstance(result, str)
        assert "stall" in result.lower() or "Cooling" in result

    def test_stall_message_contains_drop_amount(self):
        wd = CoolStallWatchdog(min_drop_c=5.0, check_window_s=300.0,
                               grace_period_s=120.0)
        wd.begin(200.0)
        result = wd.check(300.0, 198.0)
        assert "2.0" in result or "2" in result

    def test_grace_period_no_check(self):
        wd = CoolStallWatchdog(min_drop_c=5.0, check_window_s=300.0,
                               grace_period_s=120.0)
        wd.begin(200.0)
        # Within grace period - never triggers regardless of temperature
        assert wd.check(0.0,  200.0) is None
        assert wd.check(60.0, 200.0) is None
        assert wd.check(119.0, 200.0) is None

    def test_before_check_window_no_trigger(self):
        wd = CoolStallWatchdog(min_drop_c=5.0, check_window_s=300.0,
                               grace_period_s=120.0)
        wd.begin(200.0)
        # Past grace (>120s) but before check window (<300s) - no trigger
        assert wd.check(130.0, 200.0) is None
        assert wd.check(200.0, 200.0) is None

    def test_at_exact_check_window_boundary(self):
        wd = CoolStallWatchdog(min_drop_c=5.0, check_window_s=300.0,
                               grace_period_s=120.0)
        wd.begin(200.0)
        # At exactly check_window_s with insufficient drop
        result = wd.check(300.0, 198.0)
        assert result is not None

    def test_not_begun_never_triggers(self):
        wd = CoolStallWatchdog()
        assert wd.check(9999.0, 200.0) is None

    def test_reset_clears_state(self):
        wd = CoolStallWatchdog(min_drop_c=5.0, check_window_s=300.0,
                               grace_period_s=120.0)
        wd.begin(200.0)
        wd.reset()
        # After reset, no stall detection
        assert wd.check(9999.0, 200.0) is None

    def test_sufficient_drop_at_boundary_no_stall(self):
        wd = CoolStallWatchdog(min_drop_c=5.0, check_window_s=300.0,
                               grace_period_s=120.0)
        wd.begin(200.0)
        # Exactly min_drop_c should not trigger
        assert wd.check(300.0, 195.0) is None  # dropped exactly 5 deg C

    # ── Rate-aware tests ─────────────────────────────────────────────

    def test_slow_ramp_with_rate_does_not_stall(self):
        """The original bug: 0.5 deg C/min ramp was aborting because the
        fixed 5 deg C threshold was unreachable in the check window.
        With rate-awareness, expected drop is scaled to the profile rate.
        """
        wd = CoolStallWatchdog(min_drop_c=5.0, check_window_s=300.0,
                               grace_period_s=120.0, rate_fraction=0.25)
        wd.begin(140.0, ramp_rate=0.5)  # 0.5 deg C/min

        # After 5 min (300s), at 0.5 deg/min the planned drop is 2.5 deg C.
        # Expected minimum = 2.5 * 0.25 = 0.625 deg C (floored to 0.5)
        # Actual drop of 1.2 deg C > 0.625 → should NOT stall
        assert wd.check(300.0, 138.8) is None  # dropped 1.2 deg C

    def test_slow_ramp_truly_stalled_still_caught(self):
        """Even with a slow rate, a truly dead cooling system (zero drop)
        should still be caught."""
        wd = CoolStallWatchdog(min_drop_c=5.0, check_window_s=300.0,
                               grace_period_s=120.0, rate_fraction=0.25)
        wd.begin(140.0, ramp_rate=0.5)

        # Zero temperature drop after 5 min
        result = wd.check(300.0, 140.0)
        assert result is not None
        assert "stall" in result.lower() or "Cooling" in result

    def test_fast_ramp_uses_rate_based_threshold(self):
        """A fast cooling ramp should have a higher threshold derived
        from the rate, not just the fixed minimum."""
        wd = CoolStallWatchdog(min_drop_c=5.0, check_window_s=300.0,
                               grace_period_s=120.0, rate_fraction=0.25)
        wd.begin(200.0, ramp_rate=10.0)  # 10 deg C/min

        # After 5 min, planned drop = 50 deg C.
        # Expected minimum = 50 * 0.25 = 12.5 deg C
        # A drop of only 3 deg C should trigger stall
        result = wd.check(300.0, 197.0)
        assert result is not None

    def test_unconstrained_ramp_uses_fixed_threshold(self):
        """Without a rate, the watchdog falls back to the fixed min_drop_c."""
        wd = CoolStallWatchdog(min_drop_c=5.0, check_window_s=300.0,
                               grace_period_s=120.0)
        wd.begin(200.0, ramp_rate=None)  # unconstrained

        # Dropped only 3 deg C → should stall (fixed 5 deg C threshold)
        result = wd.check(300.0, 197.0)
        assert result is not None

    def test_rolling_window_advances_after_passing(self):
        """After a successful check, the window slides forward so that
        subsequent checks measure from the new position."""
        wd = CoolStallWatchdog(min_drop_c=5.0, check_window_s=300.0,
                               grace_period_s=120.0)
        wd.begin(200.0)

        # First window passes: dropped 10 deg C in 300s
        assert wd.check(300.0, 190.0) is None

        # Window should now start from 190°C at t=300s
        # Next check at t=600s: needs 5 deg C drop from 190
        # Only dropped 1 deg C from 190 → should stall
        result = wd.check(600.0, 189.0)
        assert result is not None

    def test_rolling_window_continues_on_steady_cooling(self):
        """Steady cooling should pass every window check."""
        wd = CoolStallWatchdog(min_drop_c=5.0, check_window_s=300.0,
                               grace_period_s=120.0)
        wd.begin(200.0)

        # Window 1: 200 → 190 in 300s
        assert wd.check(300.0, 190.0) is None
        # Window 2: 190 → 180 in 300s (t=600)
        assert wd.check(600.0, 180.0) is None
        # Window 3: 180 → 170 in 300s (t=900)
        assert wd.check(900.0, 170.0) is None

    def test_rate_fraction_sanity_floor(self):
        """Even with an extremely slow rate, the expected drop should never
        go below 0.5 deg C (sanity floor)."""
        wd = CoolStallWatchdog(min_drop_c=5.0, check_window_s=300.0,
                               grace_period_s=120.0, rate_fraction=0.25)
        wd.begin(140.0, ramp_rate=0.1)  # very slow: 0.1 deg C/min

        # Planned drop in 5min = 0.5 deg C. Fraction = 0.5 * 0.25 = 0.125
        # Sanity floor of 0.5 applies. Actual drop of 0.3 → stall
        result = wd.check(300.0, 139.7)
        assert result is not None

    def test_begin_without_rate_defaults_to_fixed(self):
        """Calling begin() without ramp_rate should use the fixed threshold."""
        wd = CoolStallWatchdog(min_drop_c=5.0, check_window_s=300.0,
                               grace_period_s=120.0)
        wd.begin(200.0)  # no ramp_rate argument

        # Should use fixed 5 deg C threshold
        assert wd.check(300.0, 196.0) is not None  # only 4 deg C drop
        wd.reset()
        wd.begin(200.0)
        assert wd.check(300.0, 195.0) is None      # exactly 5 deg C


class TestWatchdogManager:
    def test_ramp_check_ok(self):
        wm = WatchdogManager(ramp_safety_factor=3.0)
        seg = Segment('ramp', 200, ramp_rate=10)
        wm.begin_segment(seg, 100.0)
        assert wm.check('ramping', 500, 150.0, 200.0) is None

    def test_ramp_check_timeout(self):
        wm = WatchdogManager(ramp_safety_factor=3.0)
        seg = Segment('ramp', 200, ramp_rate=10)
        wm.begin_segment(seg, 100.0)
        result = wm.check('ramping', 2000, 150.0, 200.0)
        assert result is not None
        assert isinstance(result, str)

    def test_soak_check_ok(self):
        wm = WatchdogManager(soak_drift_limit_c=5.0, soak_drift_checks=3)
        seg = Segment('soak', 130, duration_s=3600)
        wm.begin_segment(seg, 130.0)
        assert wm.check('soaking', 100, 132.0, 130.0) is None

    def test_soak_check_drift(self):
        wm = WatchdogManager(soak_drift_limit_c=5.0, soak_drift_checks=3)
        seg = Segment('soak', 130, duration_s=3600)
        wm.begin_segment(seg, 130.0)
        for _ in range(2):
            assert wm.check('soaking', 100, 140.0, 130.0) is None
        result = wm.check('soaking', 100, 140.0, 130.0)
        assert result is not None
        assert isinstance(result, str)

    def test_cooling_check_stall(self):
        wm = WatchdogManager(cool_min_drop_c=5.0,
                             cool_check_window_s=300.0,
                             cool_grace_s=120.0)
        seg = Segment('ramp', 45, ramp_rate=2)
        wm.begin_segment(seg, 200.0)
        result = wm.check('cooling', 300.0, 199.0, 45.0)
        assert result is not None
        assert isinstance(result, str)

    def test_idle_state_always_returns_none(self):
        wm = WatchdogManager()
        assert wm.check('idle', 0, 22.0, 22.0) is None

    def test_wrong_state_returns_none(self):
        wm = WatchdogManager()
        assert wm.check('complete', 100, 45.0, 45.0) is None
        assert wm.check('paused', 100, 130.0, 130.0) is None

    def test_begin_segment_resets_all_watchdogs(self):
        wm = WatchdogManager(soak_drift_limit_c=5.0, soak_drift_checks=3)
        seg1 = Segment('soak', 130, duration_s=3600)
        wm.begin_segment(seg1, 130.0)
        # Build up drift count
        for _ in range(2):
            wm.check('soaking', 100, 140.0, 130.0)
        # New segment resets counter
        seg2 = Segment('ramp', 200, ramp_rate=6)
        wm.begin_segment(seg2, 130.0)
        seg3 = Segment('soak', 200, duration_s=3600)
        wm.begin_segment(seg3, 200.0)
        # Counter should be reset - needs 3 consecutive again
        for _ in range(2):
            assert wm.check('soaking', 100, 210.0, 200.0) is None


class TestRampTimeoutZeroDuration:
    def test_zero_expected_duration_no_timeout(self):
        # A ramp with zero computed duration (e.g. start == target)
        # should never trigger a timeout
        wd = RampTimeoutWatchdog(safety_factor=3.0)
        # Manually set _expected_duration_s to 0 to exercise the guard
        wd._expected_duration_s = 0.0
        assert wd.check(99999, 85.0, 85.0) is None
