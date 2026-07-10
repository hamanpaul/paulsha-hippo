import os
import unittest
from unittest import mock

from paulsha_hippo import paths


class ProjectRegistryPathTests(unittest.TestCase):
    def test_prefers_psc_config_root(self):
        with mock.patch.dict(os.environ, {"PSC_CONFIG_ROOT": "/data/psc-config-root"}, clear=False):
            self.assertEqual(
                str(paths.project_registry_path("/data/custom-memory")),
                "/data/psc-config-root/.agents/config/paulsha/project-hippo.yaml",
            )

    def test_paulshaclaw_shaped_psc_config_root_uses_home_base(self):
        with mock.patch.dict(
            os.environ, {"PSC_CONFIG_ROOT": "/data/home-x/.config/paulshaclaw"}, clear=False
        ):
            self.assertEqual(
                str(paths.project_registry_path()),
                "/data/home-x/.agents/config/paulsha/project-hippo.yaml",
            )

    def test_memory_root_variant_uses_sibling_config_dir(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PSC_CONFIG_ROOT", None)
            self.assertEqual(
                str(paths.project_registry_path("/data/agents/memory")),
                "/data/agents/config/paulsha/project-hippo.yaml",
            )

    def test_default_under_agents_root(self):
        with mock.patch.dict(os.environ, {"HIPPO_AGENTS_ROOT": "/data/agents2"}, clear=False):
            for name in ("PSC_CONFIG_ROOT", "PSC_AGENTS_ROOT"):
                os.environ.pop(name, None)
            self.assertEqual(
                str(paths.project_registry_path()),
                "/data/agents2/config/paulsha/project-hippo.yaml",
            )


if __name__ == "__main__":
    unittest.main()
