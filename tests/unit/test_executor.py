# Unit tests for annealr.executor
import pytest
from annealr.profile import Segment
from annealr.executor import SegmentExecutor


class TestSoakExecutor:
    def test_setpoint_constant(self):
        seg = Segment('soak', 130, duration_s=3600)
        ex = SegmentExecutor(seg, t_start=100.0, t_initial_c=130.0)
        assert ex.setpoint_at(100.0) == 130.0
        assert ex.setpoint_at(200.0) == 130.0
        assert ex.setpoint_at(3800.0) == 130.0

    def test_completes_on_duration(self):
        seg = Segment('soak', 130, duration_s=3600)
        ex = SegmentExecutor(seg, t_start=100.0, t_initial_c=130.0)
        assert ex.is_complete(100.0, 130.0) is False
        assert ex.is_complete(3699.0, 130.0) is False
        assert ex.is_complete(3700.0, 130.0) is True
        assert ex.is_complete(4000.0, 130.0) is True

    def test_progress_time_based(self):
        seg = Segment('soak', 130, duration_s=1000)
        ex = SegmentExecutor(seg, t_start=0.0, t_initial_c=130.0)
        assert ex.progress(0.0, 130.0) == pytest.approx(0.0)
        assert ex.progress(500.0, 130.0) == pytest.approx(0.5)
        assert ex.progress(1000.0, 130.0) == pytest.approx(1.0)

    def test_remaining_s(self):
        seg = Segment('soak', 130, duration_s=600)
        ex = SegmentExecutor(seg, t_start=0.0, t_initial_c=130.0)
        assert ex.remaining_s(0.0, 130.0) == pytest.approx(600.0)
        assert ex.remaining_s(200.0, 130.0) == pytest.approx(400.0)
        assert ex.remaining_s(600.0, 130.0) == pytest.approx(0.0)


class TestAscendingRampWithRate:
    def test_setpoint_trajectory(self):
        # 100 deg C to 200 deg C at 10 deg C/min = 0.1667 deg C/s
        seg = Segment('ramp', 200, ramp_rate=10)
        ex = SegmentExecutor(seg, t_start=0.0, t_initial_c=100.0)

        # At t=0, setpoint = 100
        assert ex.setpoint_at(0.0) == pytest.approx(100.0)
        # At t=60s (1min), setpoint = 110
        assert ex.setpoint_at(60.0) == pytest.approx(110.0)
        # At t=300s (5min), setpoint = 150
        assert ex.setpoint_at(300.0) == pytest.approx(150.0)
        # At t=600s (10min), setpoint should be capped at 200
        assert ex.setpoint_at(600.0) == pytest.approx(200.0)
        # Beyond that, still 200
        assert ex.setpoint_at(900.0) == pytest.approx(200.0)

    def test_completes_on_duration(self):
        seg = Segment('ramp', 200, ramp_rate=10)
        ex = SegmentExecutor(seg, t_start=0.0, t_initial_c=100.0)
        # Duration = 100 deg C / 10 deg C/min * 60 = 600s
        assert ex.is_complete(599.0, 199.0) is False
        assert ex.is_complete(600.0, 200.0) is True

    def test_progress_time_based(self):
        seg = Segment('ramp', 200, ramp_rate=10)
        ex = SegmentExecutor(seg, t_start=0.0, t_initial_c=100.0)
        assert ex.progress(0.0, 100.0) == pytest.approx(0.0)
        assert ex.progress(300.0, 150.0) == pytest.approx(0.5)
        assert ex.progress(600.0, 200.0) == pytest.approx(1.0)


class TestAscendingRampWithDuration:
    def test_setpoint_trajectory(self):
        # 100 deg C to 200 deg C in 600s = 10 deg C/min
        seg = Segment('ramp', 200, duration_s=600)
        ex = SegmentExecutor(seg, t_start=0.0, t_initial_c=100.0)

        assert ex.setpoint_at(0.0) == pytest.approx(100.0)
        assert ex.setpoint_at(300.0) == pytest.approx(150.0)
        assert ex.setpoint_at(600.0) == pytest.approx(200.0)

    def test_completes_on_duration(self):
        seg = Segment('ramp', 200, duration_s=600)
        ex = SegmentExecutor(seg, t_start=0.0, t_initial_c=100.0)
        assert ex.is_complete(599.0, 199.0) is False
        assert ex.is_complete(600.0, 200.0) is True


class TestUnconstrainedAscendingRamp:
    def test_setpoint_immediate(self):
        seg = Segment('ramp', 200)
        ex = SegmentExecutor(seg, t_start=0.0, t_initial_c=100.0)
        # Unconstrained: setpoint jumps to target immediately
        assert ex.setpoint_at(0.0) == 200.0
        assert ex.setpoint_at(100.0) == 200.0

    def test_completes_on_temperature(self):
        seg = Segment('ramp', 200)
        ex = SegmentExecutor(seg, t_start=0.0, t_initial_c=100.0)
        assert ex.is_complete(60.0, 150.0) is False
        assert ex.is_complete(60.0, 197.0) is False
        assert ex.is_complete(60.0, 198.1) is True  # within 2 deg C tolerance


class TestDescendingRamp:
    def test_setpoint_trajectory(self):
        # 200 deg C to 120 deg C at 3 deg C/min = 0.05 deg C/s
        seg = Segment('ramp', 120, ramp_rate=3)
        ex = SegmentExecutor(seg, t_start=0.0, t_initial_c=200.0)

        assert ex.setpoint_at(0.0) == pytest.approx(200.0)
        # At t=60s: 200 - 3 = 197
        assert ex.setpoint_at(60.0) == pytest.approx(197.0)
        # At t=600s (10min): 200 - 30 = 170
        assert ex.setpoint_at(600.0) == pytest.approx(170.0)
        # At t=1600s (26.7min): 200 - 80 = 120, capped
        assert ex.setpoint_at(1600.0) == pytest.approx(120.0)
        # Beyond: stays at 120
        assert ex.setpoint_at(2000.0) == pytest.approx(120.0)

    def test_completes_on_temperature(self):
        seg = Segment('ramp', 120, ramp_rate=3)
        ex = SegmentExecutor(seg, t_start=0.0, t_initial_c=200.0)
        # Descending ramps complete on temperature reached, not duration
        assert ex.is_complete(100.0, 150.0) is False
        assert ex.is_complete(100.0, 121.9) is True  # within 2 deg C tolerance

    def test_progress_temperature_based(self):
        seg = Segment('ramp', 120, ramp_rate=3)
        ex = SegmentExecutor(seg, t_start=0.0, t_initial_c=200.0)
        # 200 -> 120, total delta = 80
        # At 160 deg C: (200-160)/(200-120) = 40/80 = 0.5
        assert ex.progress(500.0, 160.0) == pytest.approx(0.5)
        # At 120 deg C: complete
        assert ex.progress(2000.0, 120.0) == pytest.approx(1.0)


class TestUnconstrainedDescendingRamp:
    def test_setpoint_immediate(self):
        seg = Segment('ramp', 45)
        ex = SegmentExecutor(seg, t_start=0.0, t_initial_c=200.0)
        assert ex.setpoint_at(0.0) == 45.0

    def test_completes_on_temperature(self):
        seg = Segment('ramp', 45)
        ex = SegmentExecutor(seg, t_start=0.0, t_initial_c=200.0)
        assert ex.is_complete(3600.0, 50.0) is False
        assert ex.is_complete(3600.0, 46.5) is True


class TestElapsedAndFormatStatus:
    def test_elapsed(self):
        seg = Segment('soak', 130, duration_s=3600)
        ex = SegmentExecutor(seg, t_start=100.0, t_initial_c=130.0)
        assert ex.elapsed_s(200.0) == pytest.approx(100.0)

    def test_format_status_soak(self):
        seg = Segment('soak', 130, duration_s=3600)
        ex = SegmentExecutor(seg, t_start=0.0, t_initial_c=130.0)
        status = ex.format_status(1800.0, 129.5)
        assert "Soak at 130" in status
        assert "soaking" in status
        assert "129.5" in status

    def test_format_status_ramp(self):
        seg = Segment('ramp', 200, ramp_rate=10)
        ex = SegmentExecutor(seg, t_start=0.0, t_initial_c=100.0)
        status = ex.format_status(300.0, 148.0)
        assert "Ramp to 200" in status
        assert "148.0" in status

    def test_format_status_cooling(self):
        seg = Segment('ramp', 45, ramp_rate=2)
        ex = SegmentExecutor(seg, t_start=0.0, t_initial_c=200.0)
        status = ex.format_status(1800.0, 140.0)
        assert "cooling" in status
        assert "45" in status
