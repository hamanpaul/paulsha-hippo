import unittest


class DreamIdleTest(unittest.TestCase):
    def test_is_idle_true(self):
        from paulsha_hippo.lib import idle
        self.assertTrue(idle.is_idle(max_load=1.0, probe=lambda: (0.2, 0.3, 0.4)))

    def test_is_idle_false(self):
        from paulsha_hippo.lib import idle
        self.assertFalse(idle.is_idle(max_load=1.0, probe=lambda: (3.0, 1.0, 1.0)))

    def test_probe_raises_oserror(self):
        from paulsha_hippo.lib import idle

        def bad_probe():
            raise OSError("no load")

        # fail-safe: if probe can't determine load, we consider system idle
        self.assertTrue(idle.is_idle(probe=bad_probe))

    def test_probe_raises_attributeerror(self):
        from paulsha_hippo.lib import idle

        def bad_probe():
            raise AttributeError("no load attribute")

        # fail-safe: AttributeError should be treated same as OSError
        self.assertTrue(idle.is_idle(probe=bad_probe))

    def test_probe_returns_too_short_tuple(self):
        from paulsha_hippo.lib import idle

        # probe returns an empty tuple -> IndexError when accessing [0]
        self.assertTrue(idle.is_idle(probe=lambda: ()))

    def test_only_uses_1min_load_true(self):
        from paulsha_hippo.lib import idle

        # only the 1-minute load should be used
        self.assertTrue(idle.is_idle(max_load=1.0, probe=lambda: (0.2, 9.0, 9.0)))

    def test_only_uses_1min_load_false(self):
        from paulsha_hippo.lib import idle

        # ensure later load averages don't affect decision
        self.assertFalse(idle.is_idle(max_load=1.0, probe=lambda: (2.0, 0.1, 0.1)))

    def test_probe_scalar_raises_typeerror(self):
        """Scalar probe results are not supported; should raise TypeError."""
        from paulsha_hippo.lib import idle

        with self.assertRaises(TypeError):
            idle.is_idle(probe=lambda: 0.5)

    def test_probe_list_raises_typeerror(self):
        """List probe results should be rejected; only tuples allowed."""
        from paulsha_hippo.lib import idle

        with self.assertRaises(TypeError):
            idle.is_idle(probe=lambda: [0.2, 0.3, 0.4])

    def test_mem_headroom_above_threshold(self):
        from paulsha_hippo.lib import idle
        self.assertTrue(idle.has_mem_headroom(0.20, probe=lambda: {"MemTotal": 1000, "MemAvailable": 300}))

    def test_mem_headroom_below_threshold(self):
        from paulsha_hippo.lib import idle
        self.assertFalse(idle.has_mem_headroom(0.20, probe=lambda: {"MemTotal": 1000, "MemAvailable": 150}))

    def test_mem_headroom_at_threshold_is_false(self):
        from paulsha_hippo.lib import idle
        # 嚴格大於：剛好 20% 不放行
        self.assertFalse(idle.has_mem_headroom(0.20, probe=lambda: {"MemTotal": 1000, "MemAvailable": 200}))

    def test_mem_headroom_failsafe_on_oserror(self):
        from paulsha_hippo.lib import idle

        def boom():
            raise OSError("no meminfo")

        self.assertTrue(idle.has_mem_headroom(0.20, probe=boom))

    def test_mem_headroom_failsafe_on_missing_field(self):
        from paulsha_hippo.lib import idle
        self.assertTrue(idle.has_mem_headroom(0.20, probe=lambda: {"MemTotal": 1000}))

    def test_mem_headroom_zero_total_is_true(self):
        from paulsha_hippo.lib import idle
        self.assertTrue(idle.has_mem_headroom(0.20, probe=lambda: {"MemTotal": 0, "MemAvailable": 0}))

    def test_read_load1_returns_value(self):
        from paulsha_hippo.lib import idle
        self.assertEqual(idle.read_load1(probe=lambda: (2.5, 1.0, 1.0)), 2.5)

    def test_read_load1_oserror_returns_none(self):
        from paulsha_hippo.lib import idle

        def bad_probe():
            raise OSError("no load")

        # 診斷用讀值：跟 is_idle 一樣 fail-safe，但用 None 表示「量不到」，
        # 不像 is_idle 那樣把失敗折成布林值。
        self.assertIsNone(idle.read_load1(probe=bad_probe))

    def test_read_load1_attributeerror_returns_none(self):
        from paulsha_hippo.lib import idle

        def bad_probe():
            raise AttributeError("no load attribute")

        self.assertIsNone(idle.read_load1(probe=bad_probe))

    def test_read_load1_short_tuple_returns_none(self):
        from paulsha_hippo.lib import idle
        self.assertIsNone(idle.read_load1(probe=lambda: ()))

    def test_read_load1_non_tuple_returns_none(self):
        from paulsha_hippo.lib import idle
        # is_idle 對非 tuple 探針會 raise TypeError；read_load1 純屬顯示用途，
        # 不應把顯示失敗變成呼叫端的例外，一律降階為 None。
        self.assertIsNone(idle.read_load1(probe=lambda: [0.2, 0.3, 0.4]))
        self.assertIsNone(idle.read_load1(probe=lambda: 0.5))

    def test_read_load1_default_probe_uses_getloadavg(self):
        from paulsha_hippo.lib import idle
        value = idle.read_load1()
        self.assertIsInstance(value, float)


if __name__ == "__main__":
    unittest.main()
