"""Shared helper: isolate ``HIPPO_CONFIG_ROOT`` so atomizer tests can freely
rewrite the "canonical" config without ever touching the operator's real
``~/.config/paulsha-hippo/config.yaml``.

``tests/conftest.py`` already does this via an autouse pytest fixture
(``_isolate_runtime_config``), but that fixture only exists while pytest is
doing the collecting -- a test file run directly
(``python3 tests/test_atomizer_e2e.py``) or via ``python3 -m unittest``
never loads ``conftest.py`` at all, so any test that calls
``paths.atomizer_config_path()`` unprotected falls straight through to the
real file. That is exactly what happened on 2026-08-25 (paulsha-hippo#141):
a test wrote a throwaway single-profile ``fake-agent`` config over the real
one, and knowledge distillation silently died in production for a week.

Every test that mutates the canonical atomizer config MUST wrap the
relevant block in :func:`isolated_atomizer_config` instead of relying on the
ambient pytest fixture -- that way the isolation holds regardless of how the
test process was invoked.
"""
from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE = _REPO_ROOT / "paulsha_hippo" / "atomizer" / "atomizer.yaml"


@contextmanager
def isolated_atomizer_config() -> Iterator[Path]:
    """Point ``HIPPO_CONFIG_ROOT`` at a fresh tmp dir for the ``with`` block.

    The tmp dir is seeded with a copy of the shipped ``atomizer.yaml``
    template so callers can load it, mutate the in-memory document exactly
    like they would the real canonical config, and write it back. Yields the
    path to the (already-seeded) ``config.yaml``.

    Nothing under ``HIPPO_CONFIG_ROOT`` can ever alias the operator's real
    config -- the override is restored on exit even if the block raises.
    """
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        canonical = root / "config.yaml"
        shutil.copyfile(_TEMPLATE, canonical)
        with mock.patch.dict("os.environ", {"HIPPO_CONFIG_ROOT": str(root)}):
            yield canonical
