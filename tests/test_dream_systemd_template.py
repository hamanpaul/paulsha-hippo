from __future__ import annotations

import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "paulsha_hippo" / "dream"


class SystemdTemplateTests(unittest.TestCase):
    def test_timer_is_hourly(self):
        timer = (BASE / "systemd" / "paulsha-memory-dream.timer").read_text(encoding="utf-8")
        self.assertIn("OnCalendar=hourly", timer)
        self.assertNotIn("Mon..Fri", timer)
        self.assertIn("Persistent=true", timer)

    def test_service_invokes_require_idle(self):
        service = (BASE / "systemd" / "paulsha-memory-dream.service").read_text(encoding="utf-8")
        self.assertIn("dream run", service)
        self.assertIn("--require-idle", service)
        self.assertIn("--promoter llm", service)
        self.assertNotIn("--promoter identity", service)

    def test_service_has_portable_resource_caps(self):
        service = (BASE / "systemd" / "paulsha-memory-dream.service").read_text(encoding="utf-8")
        self.assertIn("CPUWeight=20", service)
        self.assertIn("MemoryHigh=20%", service)
        self.assertIn("MemoryMax=30%", service)
        self.assertIn("TasksMax=256", service)
        self.assertNotIn("CPUQuota", service)

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
