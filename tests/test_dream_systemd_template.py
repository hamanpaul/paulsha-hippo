from __future__ import annotations

import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "paulsha_hippo" / "dream"


class SystemdTemplateTests(unittest.TestCase):
    def test_timer_has_workday_morning_schedule(self):
        timer = (BASE / "systemd" / "paulsha-memory-dream.timer").read_text(encoding="utf-8")
        self.assertIn("OnCalendar", timer)
        self.assertIn("Mon..Fri", timer)

    def test_service_invokes_require_idle(self):
        service = (BASE / "systemd" / "paulsha-memory-dream.service").read_text(encoding="utf-8")
        self.assertIn("dream run", service)
        self.assertIn("--require-idle", service)
        self.assertIn("--promoter llm", service)
        self.assertNotIn("--promoter identity", service)

    def test_wrapper_script_exists(self):
        path = BASE / "scripts" / "dream-idle-wrapper.sh"
        self.assertTrue(path.exists())
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!"))
        self.assertIn("PSC_MEMORY_ROOT", text)
        self.assertIn("--require-idle", text)
        self.assertIn("--promoter llm", text)
        self.assertNotIn("--promoter identity", text)


if __name__ == "__main__":
    unittest.main()
