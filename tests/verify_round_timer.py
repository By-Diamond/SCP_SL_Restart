"""回合估算状态机：可控时钟，不联网，不写运行文件。"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from monitor import Monitor

ADDR = "127.0.0.1:7777"


class RoundTimerTests(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(config.DEFAULTS, servers=[{"addr": ADDR, "name": "测试服", "timer": True}])
        self.monitor = Monitor(self.cfg)
        self.monitor._write = lambda *args: None
        self.now = 0.0
        self.monitor._clock = lambda: self.now

    def tick(self, players, seconds=1):
        self.now += seconds
        result = None if players is None else {"players": players, "maxplayers": 45}
        with patch("a2s.query", return_value=result):
            self.monitor._round()
        return self.monitor.snapshot["text"]

    def test_lobby_and_countdown_are_excluded(self):
        self.tick(0)
        self.assertEqual(self.tick(1), "[测试服] 1人")
        for _ in range(30):
            self.tick(1)
        self.assertEqual(self.monitor._timer.get(ADDR, 0), 0)
        self.assertIn("等待 0:45", self.tick(2))
        for _ in range(44):
            self.tick(2)
        self.assertIn("等待 0:01", self.monitor.snapshot["text"])
        self.assertIn(" 0:00", self.tick(2))
        self.assertIn(" 0:01", self.tick(2))

    def test_countdown_cancels_below_threshold(self):
        self.tick(2)
        for _ in range(10):
            self.tick(2)
        self.assertEqual(self.tick(1), "[测试服] 1人")
        self.assertIn("等待 0:45", self.tick(2))

    def test_running_round_keeps_counting_with_one_player(self):
        self.cfg["round_start_delay"] = 2
        self.tick(2)
        self.tick(2)
        self.tick(2)
        self.assertIn(" 0:01", self.tick(1))
        self.tick(0)
        self.assertEqual(self.monitor._timer[ADDR], 0)
        self.assertEqual(self.tick(1), "[测试服] 1人")

    def test_separated_failures_do_not_trigger_restart(self):
        self.tick(5)
        self.tick(None)
        self.tick(5)
        self.assertEqual(self.monitor._fails[ADDR], 0)
        self.tick(None)
        self.assertEqual(self.monitor.take_restarts(), [])
        self.tick(None)
        self.assertEqual(self.monitor.take_restarts(), [("测试服", ADDR)])

    def test_pause_gap_and_failed_query_cancel_countdown(self):
        self.tick(2)
        self.tick(2)
        self.monitor.set_paused(True)
        self.monitor.set_paused(False)
        self.assertIn("等待 0:45", self.tick(2))
        self.assertIn("等待 0:45", self.tick(2, seconds=100))
        self.tick(None)
        self.assertIn("等待 0:45", self.tick(2))

    def test_no_catchup_after_long_gap_or_failed_running_query(self):
        self.cfg["round_start_delay"] = 0
        self.tick(2)
        self.tick(2)
        before = self.monitor._timer[ADDR]
        self.tick(2, seconds=100)
        self.assertEqual(self.monitor._timer[ADDR], before)
        self.tick(None)
        self.assertEqual(self.monitor._timer[ADDR], before)

    def test_manual_refresh_while_paused_does_not_advance_round(self):
        self.cfg["round_start_delay"] = 0
        self.tick(2)
        self.tick(2)
        before = self.monitor._timer[ADDR]
        self.monitor.set_paused(True)
        self.tick(2)
        self.assertEqual(self.monitor._timer[ADDR], before)

    def test_timer_reset_and_custom_settings(self):
        self.cfg.update(round_min_players=3, round_start_delay=2)
        self.assertEqual(self.tick(2), "[测试服] 2人")
        self.assertIn("等待 0:02", self.tick(3))
        self.tick(3)
        self.assertIn(" 0:00", self.tick(3))
        self.monitor.reset_round_timers()
        self.assertIn("等待 0:02", self.tick(3))

    def test_countdown_uses_real_intervals_and_counts_only_excess(self):
        self.cfg["round_start_delay"] = 3
        self.tick(2)
        self.tick(2, seconds=2)
        self.assertIn(" 0:01", self.tick(2, seconds=2))

    def test_full_server_starts_round_after_one_second(self):
        """倒计时中满员：持续 1 秒就直接开局，不再等满 45 秒。"""
        self.tick(45)
        self.assertIn(" 0:00", self.tick(45))
        self.assertIn(" 0:01", self.tick(45))

    def test_not_full_keeps_countdown(self):
        self.tick(44)
        for _ in range(5):
            self.assertIn("等待 ", self.tick(44))

    def test_quiet_restart_is_ignored(self):
        """过去 10 秒一直 ≤2 人且没开局：人数归零不算重启。"""
        for _ in range(3):
            self.tick(2)
        self.assertIn("[测试服] 0人", self.tick(0))
        self.assertEqual(self.monitor.take_restarts(), [])

    def test_quiet_offline_is_ignored(self):
        self.tick(2)
        self.tick(None)
        self.assertEqual(self.monitor.take_restarts(), [])
        self.tick(None)
        self.assertEqual(self.monitor.take_restarts(), [])

    def test_restart_with_players_is_still_reported(self):
        self.tick(10)
        self.tick(10)
        self.tick(0)
        self.assertEqual(self.monitor.take_restarts(), [("测试服", ADDR)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
