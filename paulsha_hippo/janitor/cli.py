"""
Janitor CLI interface for psc memory janitor commands.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from . import config as janitor_config
from . import scanner


def run(args: argparse.Namespace) -> int:
    """
    Execute janitor scan command.
    
    Args:
        args: Parsed command-line arguments with:
            - memory_root: str
            - knowledge_root: str | None
            - now: str | None
            - override: str | None
            - dry_run: bool
    
    Returns:
        Exit code (0 for success)
    """
    # Load configuration
    override_path: str | Path | None | object
    if hasattr(args, 'override') and args.override is not None:
        override_path = args.override
    else:
        # Use default override behavior (check ~/.config/paulshaclaw/janitor.override.yaml)
        override_path = janitor_config._DEFAULT_SENTINEL
    
    config, config_hash = janitor_config.load_config(override_path=override_path)
    
    # Determine paths
    memory_root = Path(args.memory_root)
    if args.knowledge_root:
        knowledge_root = Path(args.knowledge_root)
    else:
        knowledge_root = memory_root / "knowledge"
    
    # Determine timestamp
    if args.now:
        now = args.now
    else:
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    
    # Run scan
    result = scanner.run_scan(
        memory_root=memory_root,
        knowledge_root=knowledge_root,
        config=config,
        config_hash=config_hash,
        now=now,
        dry_run=args.dry_run,
    )
    
    # Print result
    print(json.dumps(result, sort_keys=True, indent=2))
    
    return 0
