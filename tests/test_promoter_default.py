"""#175: atomizer 出貨預設 promoter 必須是 llm；identity 僅限顯式選用。"""
from __future__ import annotations

import argparse
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.atomizer import cli as atomizer_cli
from paulsha_hippo.atomizer import config as atomizer_config
from paulsha_hippo.atomizer.llm_promoter import LLMPromoter
from paulsha_hippo.atomizer.promoter import IdentityPromoter


class PromoterDefaultTests(unittest.TestCase):
    def test_shipped_config_default_promoter_is_llm(self):
        # override_path=None 停用 ~/.config/paulshaclaw/atomizer.override.yaml，
        # 只驗 repo 內建 atomizer.yaml（唯一實際生效的預設來源）。
        cfg, _ = atomizer_config.load_config(override_path=None)
        self.assertEqual(cfg.default_promoter, "llm")

    def test_build_promoter_without_flag_is_llm(self):
        # 鎖 atomizer/cli.py:73 `args.promoter or config.default_promoter`：
        # 未帶 --promoter（None）不得再落 IdentityPromoter。
        cfg, _ = atomizer_config.load_config(override_path=None)
        args = argparse.Namespace(promoter=None, agent_command=None)
        with TemporaryDirectory() as tmp:
            promoter = atomizer_cli._build_promoter(args, cfg, Path(tmp))
        self.assertIsInstance(promoter, LLMPromoter)
        self.assertNotIsInstance(promoter, IdentityPromoter)

    def test_explicit_identity_flag_still_honored(self):
        # identity 保留為顯式選項（測試/離線 deterministic 用）。
        cfg, _ = atomizer_config.load_config(override_path=None)
        args = argparse.Namespace(promoter="identity", agent_command=None)
        with TemporaryDirectory() as tmp:
            promoter = atomizer_cli._build_promoter(args, cfg, Path(tmp))
        self.assertIsInstance(promoter, IdentityPromoter)

    def test_config_without_promoter_key_fails_safe_to_identity(self):
        # code-level fallback（config.py:297 get("promoter", "identity")）維持
        # identity：缺 key 的精簡 config 不得隱性升級成外呼 LLM（fail-safe）。
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "atomizer.yaml").write_text(
                "schema_version: 1\n", encoding="utf-8"
            )
            cfg, _ = atomizer_config.load_config(
                default_dir=config_dir, override_path=None
            )
        self.assertEqual(cfg.default_promoter, "identity")


if __name__ == "__main__":
    unittest.main()
