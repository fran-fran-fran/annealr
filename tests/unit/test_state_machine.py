# Unit tests for annealr.state_machine
import pytest
from annealr.profile import Segment, Profile
from annealr.executor import SegmentExecutor
from annealr.state_machine import AnnealrStateMachine


def make_simple_profile():
    return Profile("test", segments=[
        Segment('ramp', 85, ramp_rate=15),
        Segment('soak', 85, duration_s=1800),
        Segment('ramp', 45),
    ])


def make_pps_profile():
    return Profile("pps_cf", segments=[
        Segment('ramp', 80, ramp_rate=15),
        Segment('soak', 80, duration_s=1800),
        Segment('ramp', 200, ramp_rate=6),
        Segment('soak', 200, duration_s=7200),
        Segment('ramp', 45),
    ])


class TestStateMachineLifecycle:
    def test_initial_state(self):
        sm = AnnealrStateMachine()
        assert sm.state == 'idle'
        assert sm.can_start()
        assert not sm.can_pause()
        assert not sm.can_resume()
        assert not sm.can_cancel()
        assert sm.profile is None
        assert sm.executor is None

    def test_start_returns_first_segment(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        seg = sm.start(p, now=100.0)
        assert seg is not None
        assert seg.kind == 'ramp'
        assert seg.target_c == 85.0

    def test_start_sets_state_ramping(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        assert sm.state == 'ramping'
        assert sm.is_active()
        assert sm.is_running()
        assert sm.can_pause()
        assert not sm.can_start()

    def test_start_sets_profile(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        assert sm.profile is p
        assert sm.stage_index == 0

    def test_cannot_start_twice(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        with pytest.raises(RuntimeError, match="Cannot start"):
            sm.start(p, 200.0)

    def test_cannot_start_while_paused(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.pause(150.0)
        with pytest.raises(RuntimeError, match="Cannot start"):
            sm.start(p, 200.0)

    def test_start_empty_profile_raises(self):
        sm = AnnealrStateMachine()
        p = Profile("empty", segments=[])
        with pytest.raises(RuntimeError, match="no segments"):
            sm.start(p, 100.0)

    def test_cancel_from_ramping(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.cancel()
        assert sm.state == 'cancelled'
        assert not sm.is_active()
        assert sm.can_start()
        assert sm.executor is None

    def test_cancel_from_soaking(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.advance_stage(200.0, 85.0)
        assert sm.state == 'soaking'
        sm.cancel()
        assert sm.state == 'cancelled'

    def test_cancel_from_paused(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.pause(150.0)
        sm.cancel()
        assert sm.state == 'cancelled'
        assert not sm.is_active()

    def test_cannot_cancel_when_idle(self):
        sm = AnnealrStateMachine()
        with pytest.raises(RuntimeError, match="Cannot cancel"):
            sm.cancel()

    def test_cannot_cancel_when_complete(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.complete()
        with pytest.raises(RuntimeError, match="Cannot cancel"):
            sm.cancel()

    def test_complete_sets_state(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.complete()
        assert sm.state == 'complete'
        assert sm.can_start()
        assert sm.executor is None

    def test_error_sets_state(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.error("test failure")
        assert sm.state == 'error'
        assert sm.can_start()
        assert sm.executor is None

    def test_restart_after_complete(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.complete()
        seg = sm.start(p, 200.0)
        assert seg is not None
        assert sm.state == 'ramping'
        assert sm.stage_index == 0

    def test_restart_after_cancel(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.cancel()
        seg = sm.start(p, 200.0)
        assert seg is not None
        assert sm.state == 'ramping'

    def test_restart_after_error(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.error("something broke")
        seg = sm.start(p, 200.0)
        assert seg is not None
        assert sm.state == 'ramping'
        assert sm.stage_index == 0


class TestPauseResume:
    def test_pause_from_ramping(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        prev = sm.pause(150.0)
        assert prev == 'ramping'
        assert sm.state == 'paused'
        assert sm.is_active()
        assert not sm.is_running()
        assert not sm.can_pause()
        assert sm.can_resume()

    def test_pause_from_soaking(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.advance_stage(200.0, 85.0)
        assert sm.state == 'soaking'
        prev = sm.pause(250.0)
        assert prev == 'soaking'
        assert sm.state == 'paused'

    def test_pause_from_cooling(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.advance_stage(200.0, 85.0)   # -> soak
        sm.advance_stage(2000.0, 85.0)  # -> cooling
        assert sm.state == 'cooling'
        prev = sm.pause(3000.0)
        assert prev == 'cooling'
        assert sm.state == 'paused'

    def test_cannot_pause_when_idle(self):
        sm = AnnealrStateMachine()
        with pytest.raises(RuntimeError, match="Cannot pause"):
            sm.pause(100.0)

    def test_cannot_pause_when_already_paused(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.pause(150.0)
        with pytest.raises(RuntimeError, match="Cannot pause"):
            sm.pause(200.0)

    def test_cannot_pause_when_complete(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.complete()
        with pytest.raises(RuntimeError, match="Cannot pause"):
            sm.pause(200.0)

    def test_resume_from_ramping_returns_ramping(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.pause(150.0)
        resumed = sm.resume(200.0)
        assert resumed == 'ramping'
        assert sm.state == 'ramping'
        assert not sm.can_resume()
        assert sm.can_pause()

    def test_resume_from_soaking_returns_soaking(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.advance_stage(200.0, 85.0)
        sm.pause(250.0)
        resumed = sm.resume(300.0)
        assert resumed == 'soaking'
        assert sm.state == 'soaking'

    def test_cannot_resume_when_not_paused(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        with pytest.raises(RuntimeError, match="Cannot resume"):
            sm.resume(200.0)

    def test_cannot_resume_when_idle(self):
        sm = AnnealrStateMachine()
        with pytest.raises(RuntimeError, match="Cannot resume"):
            sm.resume(100.0)

    def test_cannot_resume_when_complete(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.complete()
        with pytest.raises(RuntimeError, match="Cannot resume"):
            sm.resume(200.0)


class TestTimeAccounting:
    def test_adjusted_time_no_pause(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        assert sm.adjusted_time(200.0) == pytest.approx(200.0)
        assert sm.adjusted_time(500.0) == pytest.approx(500.0)

    def test_adjusted_time_single_pause(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.pause(200.0)     # paused at t=200
        sm.resume(300.0)    # resumed at t=300, 100s pause
        # At t=400, adjusted = 400 - 100 = 300
        assert sm.adjusted_time(400.0) == pytest.approx(300.0)

    def test_adjusted_time_multiple_pauses(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.pause(200.0)
        sm.resume(250.0)    # 50s pause
        sm.pause(350.0)
        sm.resume(400.0)    # 50s pause, total 100s
        assert sm.adjusted_time(500.0) == pytest.approx(400.0)

    def test_adjusted_time_frozen_while_paused(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.pause(200.0)
        # While paused, adjusted time is frozen
        assert sm.adjusted_time(300.0) == pytest.approx(200.0)
        assert sm.adjusted_time(500.0) == pytest.approx(200.0)
        assert sm.adjusted_time(1000.0) == pytest.approx(200.0)

    def test_adjusted_time_zero_length_pause(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.pause(200.0)
        sm.resume(200.0)    # zero-length pause
        # No time should be lost
        assert sm.adjusted_time(400.0) == pytest.approx(400.0)


class TestStageAdvancement:
    def test_advance_to_soak(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        assert sm.stage_index == 0
        next_seg = sm.advance_stage(200.0, 85.0)
        assert next_seg is not None
        assert next_seg.kind == 'soak'
        assert next_seg.target_c == 85.0
        assert sm.stage_index == 1
        assert sm.state == 'soaking'

    def test_advance_to_cooling(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.advance_stage(200.0, 85.0)   # -> soak
        next_seg = sm.advance_stage(2000.0, 85.0)  # -> ramp to 45 (cooling)
        assert next_seg is not None
        assert next_seg.kind == 'ramp'
        assert next_seg.target_c == 45.0
        assert sm.state == 'cooling'

    def test_advance_past_end_returns_none(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.advance_stage(200.0, 85.0)   # -> soak
        sm.advance_stage(2000.0, 85.0)  # -> cooling
        result = sm.advance_stage(5000.0, 45.0)
        assert result is None

    def test_advance_through_full_pps_profile(self):
        sm = AnnealrStateMachine()
        p = make_pps_profile()
        sm.start(p, 100.0)
        # ramp -> soak -> ramp -> soak -> cooling
        temps = [80.0, 80.0, 200.0, 200.0, 45.0]
        for i, temp in enumerate(temps[:-1]):
            seg = sm.advance_stage(float(i * 1000), temp)
            assert seg is not None
        result = sm.advance_stage(9999.0, 45.0)
        assert result is None


class TestSetExecutor:
    def test_set_executor(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        seg = p.segments[0]
        ex = SegmentExecutor(seg, 100.0, 22.0)
        sm.set_executor(ex)
        assert sm.executor is ex


class TestGetStatus:
    def test_idle_status(self):
        sm = AnnealrStateMachine()
        status = sm.get_status(100.0)
        assert status['state'] == 'idle'
        assert status['profile'] is None
        assert status['stage'] is None
        assert status['stage_index'] == 0
        assert status['stage_count'] == 0

    def test_running_status_fields(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        seg = p.segments[0]
        ex = SegmentExecutor(seg, 100.0, 22.0)
        sm.set_executor(ex)

        status = sm.get_status(200.0, 50.0)
        assert status['state'] == 'ramping'
        assert status['profile'] == 'test'
        assert status['stage_count'] == 3
        assert status['stage_index'] == 0
        assert status['stage'] is not None
        assert status['stage']['label'] == 'Ramp to 85 deg C'
        assert status['stage']['target'] == 85.0
        assert status['stage']['kind'] == 'ramp'

    def test_paused_status(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        seg = p.segments[0]
        ex = SegmentExecutor(seg, 100.0, 22.0)
        sm.set_executor(ex)
        sm.pause(200.0)

        status = sm.get_status(300.0, 50.0)
        assert status['state'] == 'paused'
        assert status['profile'] == 'test'
        assert status['stage'] is not None

    def test_cancelled_status(self):
        sm = AnnealrStateMachine()
        p = make_simple_profile()
        sm.start(p, 100.0)
        sm.cancel()
        status = sm.get_status(200.0)
        assert status['state'] == 'cancelled'
