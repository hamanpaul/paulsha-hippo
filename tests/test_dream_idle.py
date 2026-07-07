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


if __name__ == "__main__":
    unittest.main()
