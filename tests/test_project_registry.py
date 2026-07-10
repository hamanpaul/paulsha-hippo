import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

try:
    import yaml  # PyYAML：僅測試側的獨立 oracle（standard YAML parser）；產品 code 維持 stdlib-only
except ImportError:  # pragma: no cover - 本 repo 測試環境有 PyYAML；缺件時跳過 interop 測試
    yaml = None

from paulsha_hippo import paths
from paulsha_hippo.importer.config import ProjectConfig
from paulsha_hippo.importer.registry import (
    auto_write_enabled,
    load_registry,
    merge_discovery,
    parse_registry,
    record_discovery,
    registry_schema_version,
    render_registry,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


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


class _ScratchDirTestCase(unittest.TestCase):
    def setUp(self):
        self.scratch = REPO_ROOT / ".test-work"
        self.scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.scratch)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        try:
            self.scratch.rmdir()
        except OSError:
            pass


class RenderParseTests(_ScratchDirTestCase):
    def test_render_registry_is_deterministic_and_sorted(self):
        projects = (
            ProjectConfig(slug="zeta", roots=("/data/z2", "/data/z1", "/data/z1"), remotes=()),
            ProjectConfig(
                slug="alpha",
                roots=(),
                remotes=("github.com/acme/alpha",),
                aliases=("a2", "a1"),
            ),
        )
        expected = (
            "# GENERATED — 本檔由 paulsha-hippo 自動產生（project registry discovery record），請勿手改。\n"
            "# 使用者 override 請寫 manual 檔（projects.yaml / project-cortex.yaml），詳見契約文件。\n"
            "# contract: docs/project-registry-contract.md\n"
            "schema_version: 1\n"
            "projects:\n"
            '  - slug: "alpha"\n'
            "    roots: []\n"
            "    remotes:\n"
            '      - "github.com/acme/alpha"\n'
            '    aliases: ["a1", "a2"]\n'
            '  - slug: "zeta"\n'
            "    roots:\n"
            '      - "/data/z1"\n'
            '      - "/data/z2"\n'
            "    remotes: []\n"
            "    aliases: []\n"
        )
        self.assertEqual(render_registry(projects), expected)

    def test_render_registry_empty_projects(self):
        rendered = render_registry(())
        self.assertTrue(rendered.endswith("schema_version: 1\nprojects: []\n"))

    def test_parse_registry_round_trips_render(self):
        projects = (
            ProjectConfig(
                slug="alpha",
                roots=("/data/a",),
                remotes=("github.com/acme/alpha",),
                aliases=("a1",),
            ),
            ProjectConfig(slug="zeta", roots=("/data/z",), remotes=(), aliases=()),
        )
        self.assertEqual(parse_registry(render_registry(projects)), projects)

    def test_parse_registry_tolerates_comments_and_empty(self):
        self.assertEqual(parse_registry(""), ())
        self.assertEqual(parse_registry("# only comment\nschema_version: 1\nprojects: []\n"), ())

    def test_registry_schema_version_reads_header(self):
        self.assertEqual(registry_schema_version("schema_version: 1\nprojects: []\n"), 1)
        self.assertIsNone(registry_schema_version("projects: []\n"))
        self.assertIsNone(registry_schema_version("schema_version: abc\n"))

    def test_load_registry_missing_file_returns_empty(self):
        self.assertEqual(load_registry(self.root / "absent.yaml"), ())
        self.assertEqual(load_registry(None), ())

    def test_load_registry_reads_rendered_file(self):
        path = self.root / "project-hippo.yaml"
        projects = (ProjectConfig(slug="alpha", roots=("/data/a",), remotes=()),)
        path.write_text(render_registry(projects), encoding="utf-8")
        self.assertEqual(load_registry(path), projects)

    def test_load_registry_corrupt_bytes_fail_open(self):
        # 檔案與手寫 project-cortex.yaml 同層，手改／壞 byte 情境真實存在：
        # UnicodeDecodeError（ValueError 子類）必須 fail-open 回空，不得炸穿讀取端。
        path = self.root / "project-hippo.yaml"
        path.write_bytes(b"schema_version: 1\nprojects:\n  - slug: alpha\n\xff\xfe\xfa\n")
        self.assertEqual(load_registry(path), ())


class YamlQuotingContractTests(_ScratchDirTestCase):
    """Quoting 契約釘（#14 blocking 修復）：動態值必以 double-quoted scalar 落盤。

    舊 renderer 不加引號直接插值，`/tmp/team #1/widget` 會被標準 YAML parser
    讀成 `/tmp/team`（`#` 起註解）、`/tmp/a: b/widget` 變 nested mapping、
    `[scratch]` 變 list——自家 parse_registry 讀 raw string，round-trip 測試假綠，
    但獨立 consumer（cortex 用標準 YAML parser）會靜默掉專案／拿錯值。
    本組測試以 PyYAML 當獨立 oracle（僅測試側依賴；產品 code stdlib-only）。
    """

    # 覆蓋契約列舉的特殊字元：#、`: `、[、]、,、"、\、前導／尾隨空白。
    # 各清單值採已排序去重形（render canonical 序），使 round-trip 可整組比對。
    SPECIAL_PROJECTS = (
        ProjectConfig(slug="  padded slug  ", roots=(), remotes=(), aliases=()),
        ProjectConfig(
            slug='team #1: [prod, "v2"]\\beta',
            roots=("  /data/padded  ", "/tmp/a: b/widget", "/tmp/team #1/widget"),
            remotes=("back\\slash", 'git@host:acme/widget "beta"'),
            aliases=("[scratch]", "a, b", 'quo"te'),
        ),
    )

    @unittest.skipIf(yaml is None, "PyYAML unavailable：跳過 standard-YAML interop oracle")
    def test_standard_yaml_parser_reads_back_exact_values(self):
        data = yaml.safe_load(render_registry(self.SPECIAL_PROJECTS))
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(
            data["projects"],
            [
                {"slug": "  padded slug  ", "roots": [], "remotes": [], "aliases": []},
                {
                    "slug": 'team #1: [prod, "v2"]\\beta',
                    "roots": ["  /data/padded  ", "/tmp/a: b/widget", "/tmp/team #1/widget"],
                    "remotes": ["back\\slash", 'git@host:acme/widget "beta"'],
                    "aliases": ["[scratch]", "a, b", 'quo"te'],
                },
            ],
        )

    def test_own_parser_round_trips_special_values(self):
        self.assertEqual(
            parse_registry(render_registry(self.SPECIAL_PROJECTS)), self.SPECIAL_PROJECTS
        )

    @unittest.skipIf(yaml is None, "PyYAML unavailable：跳過 standard-YAML interop oracle")
    def test_record_discovery_file_readable_by_standard_yaml_parser(self):
        # 端到端：經真實 producer（record_discovery）落盤的檔案，標準 parser 讀回原值。
        path = self.root / "project-hippo.yaml"
        record_discovery(
            slug="team #1",
            roots=("/tmp/team #1/widget", "/tmp/a: b/widget"),
            remotes=(),
            aliases=("[scratch]",),
            registry_path=path,
        )
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        (item,) = data["projects"]
        self.assertEqual(item["slug"], "team #1")
        self.assertEqual(item["roots"], ["/tmp/a: b/widget", "/tmp/team #1/widget"])
        self.assertEqual(item["aliases"], ["[scratch]"])

    @unittest.skipIf(yaml is None, "PyYAML unavailable：跳過 standard-YAML interop oracle")
    def test_fixture_parses_identically_with_standard_and_own_parser(self):
        # 契約 fixture 是兩類 consumer 的共同錨點：standard parser 與手寫 parser 必須同義。
        text = ProducerContractTests.FIXTURE.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        own = [
            {
                "slug": project.slug,
                "roots": list(project.roots),
                "remotes": list(project.remotes),
                "aliases": list(project.aliases),
            }
            for project in parse_registry(text)
        ]
        self.assertEqual(own, data["projects"])

    def test_parse_registry_reads_legacy_unquoted_format(self):
        # 向後相容：quoting 改版前已落盤的 v1 檔（plain scalar）仍要能讀，
        # 下一次寫入才有機會 canonical 化為 quoted 形（契約 §7 低版→現版升級寫入）。
        legacy = (
            "schema_version: 1\n"
            "projects:\n"
            "  - slug: github.com/acme/widget\n"
            "    roots:\n"
            "      - /data/projects/widget\n"
            "    remotes: []\n"
            "    aliases: [a1, a2]\n"
        )
        self.assertEqual(
            parse_registry(legacy),
            (
                ProjectConfig(
                    slug="github.com/acme/widget",
                    roots=("/data/projects/widget",),
                    remotes=(),
                    aliases=("a1", "a2"),
                ),
            ),
        )


class MergeDiscoveryTests(unittest.TestCase):
    def test_merge_unions_and_sorts_same_slug(self):
        existing = (
            ProjectConfig(slug="alpha", roots=("/data/b",), remotes=("github.com/acme/alpha",)),
        )
        merged = merge_discovery(
            existing, ProjectConfig(slug="alpha", roots=("/data/a", "/data/b"), remotes=())
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].roots, ("/data/a", "/data/b"))
        self.assertEqual(merged[0].remotes, ("github.com/acme/alpha",))

    def test_merge_appends_new_slug(self):
        existing = (ProjectConfig(slug="alpha", roots=("/data/a",), remotes=()),)
        merged = merge_discovery(existing, ProjectConfig(slug="beta", roots=("/data/b",), remotes=()))
        self.assertEqual([project.slug for project in merged], ["alpha", "beta"])


class RecordDiscoveryTests(_ScratchDirTestCase):
    def registry_path(self) -> Path:
        return self.root / "project-hippo.yaml"

    def test_first_discovery_creates_file(self):
        path = self.registry_path()
        changed = record_discovery(
            slug="alpha",
            roots=("/data/a",),
            remotes=("github.com/acme/alpha",),
            registry_path=path,
        )
        self.assertTrue(changed)
        parsed = parse_registry(path.read_text(encoding="utf-8"))
        self.assertEqual([project.slug for project in parsed], ["alpha"])

    def test_repeat_discovery_is_idempotent(self):
        path = self.registry_path()
        record_discovery(
            slug="alpha",
            roots=("/data/a",),
            remotes=("github.com/acme/alpha",),
            registry_path=path,
        )
        before = path.read_bytes()
        changed = record_discovery(
            slug="alpha",
            roots=("/data/a",),
            remotes=("github.com/acme/alpha",),
            registry_path=path,
        )
        self.assertFalse(changed)
        self.assertEqual(path.read_bytes(), before)

    def test_empty_slug_raises_value_error(self):
        with self.assertRaises(ValueError):
            record_discovery(slug="", roots=("/data/a",), registry_path=self.registry_path())

    def test_whitespace_only_slug_raises_value_error(self):
        # 寫入端與 reader 丟棄邊界對齊（回歸釘）：全空白 slug 若放行落盤，
        # parse_registry 讀回即被 _finalize_registry_item 靜默丟棄，之後任一筆
        # discovery 的 parse→merge→render 重繪會把該 entry 無聲永久抹除。
        path = self.registry_path()
        with self.assertRaises(ValueError):
            record_discovery(slug="   ", roots=("/data/weird",), registry_path=path)
        self.assertFalse(path.exists())

    def test_corrupt_registry_treated_as_absent_and_rewritten(self):
        # 壞 bytes 視同缺檔：寫入端不炸、下一筆 discovery 重寫 canonical bytes（自癒）。
        path = self.registry_path()
        path.write_bytes(b"\xff\xfe corrupt registry bytes\n")
        changed = record_discovery(slug="alpha", roots=("/data/a",), registry_path=path)
        self.assertTrue(changed)
        parsed = parse_registry(path.read_text(encoding="utf-8"))
        self.assertEqual([project.slug for project in parsed], ["alpha"])

    def test_newer_schema_version_refuses_write_and_keeps_bytes_intact(self):
        # 混版部署回歸釘（契約 §7 前向防護）：新版 producer 已寫 schema_version: 2
        # （含 v1 不認識的 per-project 與頂層欄位），仍在跑的 v1 producer 下一筆
        # discovery 不得 parse→render 降級重繪——v2 bytes 必須逐 byte 完全不變。
        path = self.registry_path()
        v2_text = (
            "# GENERATED — v2 producer output\n"
            "schema_version: 2\n"
            "last_scan: 2026-07-11T00:00:00Z\n"
            "projects:\n"
            "  - slug: alpha\n"
            "    roots:\n"
            "      - /data/a\n"
            "    remotes: []\n"
            "    aliases: []\n"
            "    labels: [core, v2-only]\n"
        )
        path.write_text(v2_text, encoding="utf-8")
        before = path.read_bytes()
        with self.assertLogs("paulsha_hippo.importer", level="WARNING") as captured:
            changed = record_discovery(slug="beta", roots=("/data/b",), registry_path=path)
        self.assertFalse(changed)
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(any("schema_version" in message for message in captured.output))

    def test_same_schema_version_still_merges(self):
        # 對照組：schema_version 等於支援版本時照常併入（防護只擋「更高」版本）。
        path = self.registry_path()
        record_discovery(slug="alpha", roots=("/data/a",), registry_path=path)
        changed = record_discovery(slug="beta", roots=("/data/b",), registry_path=path)
        self.assertTrue(changed)
        parsed = parse_registry(path.read_text(encoding="utf-8"))
        self.assertEqual([project.slug for project in parsed], ["alpha", "beta"])

    def test_lock_and_artifacts_use_fixed_names_only(self):
        path = self.registry_path()
        for index in range(5):
            record_discovery(slug=f"p-{index}", roots=(f"/data/p-{index}",), registry_path=path)
        names = {item.name for item in self.root.iterdir()}
        self.assertLessEqual(
            names,
            {"project-hippo.yaml", ".project-hippo.yaml.lock", ".project-hippo.yaml.tmp"},
        )
        self.assertIn(".project-hippo.yaml.lock", names)

    def test_concurrent_discoveries_all_land(self):
        path = self.registry_path()
        slugs = [f"proj-{index:02d}" for index in range(8)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(
                pool.map(
                    lambda slug: record_discovery(
                        slug=slug, roots=(f"/data/{slug}",), registry_path=path
                    ),
                    slugs,
                )
            )
        parsed = parse_registry(path.read_text(encoding="utf-8"))
        self.assertEqual([project.slug for project in parsed], sorted(slugs))


class CrashRecoveryTests(_ScratchDirTestCase):
    def test_interrupted_replace_keeps_previous_bytes_and_recovers(self):
        path = self.root / "project-hippo.yaml"
        record_discovery(slug="alpha", roots=("/data/a",), registry_path=path)
        before = path.read_bytes()
        with mock.patch(
            "paulsha_hippo.importer.registry.os.replace",
            side_effect=OSError("simulated crash"),
        ):
            with self.assertRaises(OSError):
                record_discovery(slug="beta", roots=("/data/b",), registry_path=path)
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(record_discovery(slug="beta", roots=("/data/b",), registry_path=path))
        slugs = [project.slug for project in parse_registry(path.read_text(encoding="utf-8"))]
        self.assertEqual(slugs, ["alpha", "beta"])

    def test_sigkill_mid_write_leaves_complete_canonical_file(self):
        path = self.root / "project-hippo.yaml"
        record_discovery(slug="crash-proj", roots=("/data/crash/seed",), registry_path=path)
        child_code = textwrap.dedent(
            """
            import sys
            from paulsha_hippo.importer.registry import record_discovery

            registry_path = sys.argv[1]
            index = 0
            while True:
                index += 1
                record_discovery(
                    slug="crash-proj",
                    roots=(f"/data/crash/root-{index:06d}",),
                    registry_path=registry_path,
                )
            """
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", child_code, str(path)],
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(0.4)
        finally:
            proc.kill()
            proc.wait(timeout=5)
        text = path.read_text(encoding="utf-8")
        parsed = parse_registry(text)
        self.assertEqual([project.slug for project in parsed], ["crash-proj"])
        self.assertGreaterEqual(len(parsed[0].roots), 1)
        # canonical 自洽：任何殘缺（torn write）都會使 render(parse(x)) != x
        self.assertEqual(render_registry(parsed), text)
        names = {item.name for item in self.root.iterdir()}
        self.assertLessEqual(
            names,
            {"project-hippo.yaml", ".project-hippo.yaml.lock", ".project-hippo.yaml.tmp"},
        )


class AutoWriteEnabledTests(_ScratchDirTestCase):
    def write_config(self, text: str) -> Path:
        path = self.root / "config.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_missing_config_defaults_off(self):
        self.assertFalse(auto_write_enabled(self.root / "absent.yaml"))

    def test_corrupt_bytes_config_defaults_off(self):
        # auto_write_enabled 於 pipeline 的 fail-open try 之外被呼叫：
        # corrupt config.yaml（UnicodeDecodeError）必須回 False 而非 raise。
        path = self.root / "config.yaml"
        path.write_bytes(b"project_registry:\n  auto_write: true\n\xff\xfe\n")
        self.assertFalse(auto_write_enabled(path))

    def test_enabled_when_true(self):
        path = self.write_config("memory_root: /data/agents/memory\nproject_registry:\n  auto_write: true\n")
        self.assertTrue(auto_write_enabled(path))

    def test_disabled_when_false_or_wrong_section(self):
        self.assertFalse(auto_write_enabled(self.write_config("project_registry:\n  auto_write: false\n")))
        self.assertFalse(auto_write_enabled(self.write_config("other_section:\n  auto_write: true\n")))
        self.assertFalse(auto_write_enabled(self.write_config("auto_write: true\n")))

    def test_truthy_variants(self):
        for raw in ("true", "True", "yes", "on", "1", '"true"'):
            path = self.write_config(f"project_registry:\n  auto_write: {raw}\n")
            self.assertTrue(auto_write_enabled(path), raw)

    def test_default_path_uses_hippo_config_root(self):
        with mock.patch.dict(os.environ, {"HIPPO_CONFIG_ROOT": str(self.root)}, clear=False):
            self.write_config("project_registry:\n  auto_write: true\n")
            self.assertTrue(auto_write_enabled())


class ProducerContractTests(_ScratchDirTestCase):
    FIXTURE = REPO_ROOT / "tests" / "fixtures" / "registry" / "project-hippo.expected.yaml"
    CONTRACT_DOC = REPO_ROOT / "docs" / "project-registry-contract.md"
    MARKER = "<!-- contract-fixture:tests/fixtures/registry/project-hippo.expected.yaml -->"

    def test_producer_output_matches_fixture_byte_for_byte(self):
        path = self.root / "paulsha" / "project-hippo.yaml"
        record_discovery(
            slug="github.com/acme/widget",
            roots=("/data/projects/widget",),
            remotes=("github.com/acme/widget",),
            registry_path=path,
        )
        record_discovery(
            slug="scratch-notes",
            roots=("/data/scratch/notes",),
            remotes=(),
            registry_path=path,
        )
        self.assertEqual(path.read_bytes(), self.FIXTURE.read_bytes())

    def test_contract_doc_canonical_example_matches_fixture(self):
        doc = self.CONTRACT_DOC.read_text(encoding="utf-8")
        self.assertIn(self.MARKER, doc)
        after = doc.split(self.MARKER, 1)[1]
        self.assertTrue(after.lstrip().startswith("```yaml"))
        block = after.split("```yaml\n", 1)[1].split("```", 1)[0]
        self.assertEqual(block, self.FIXTURE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
