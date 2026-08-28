import argparse

import pytest

from paulsha_hippo import cli


def test_version_flag_prints_and_exits_zero(capsys):
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "hippo 0.1.2"


def test_no_args_prints_usage_and_exits_2(capsys):
    assert cli.main([]) == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_dream_status_subcommand_exists():
    # 去 memory 前綴層：hippo dream ... 直接可達
    assert cli.main(["dream", "--help"]) == 0


@pytest.mark.parametrize("bad_pct", ["nan", "-1", "150"])
def test_dream_run_rejects_out_of_range_min_avail_mem_pct(tmp_path, bad_pct):
    parser = cli._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([
            "dream", "run",
            "--memory-root", str(tmp_path),
            "--min-avail-mem-pct", bad_pct,
        ])


def test_dream_run_accepts_valid_min_avail_mem_pct(tmp_path):
    parser = cli._build_parser()
    args = parser.parse_args([
        "dream", "run",
        "--memory-root", str(tmp_path),
        "--min-avail-mem-pct", "20",
    ])
    assert args.min_avail_mem_pct == 20.0


def test_mark_episodic_exit_codes(tmp_path, capsys):
    # review round 1 #3: one CLI-level test covering all three
    # mark-episodic exit-code paths -- --revert found -> 0, --revert
    # not-found/not-migrated -> 1, warnings on the scan/apply path -> 1.
    knowledge = tmp_path / "knowledge" / "proj"
    knowledge.mkdir(parents=True)
    (knowledge / "n--sl-1.md").write_text(
        "---\nslice_id: sl-1\nmemory_layer: knowledge\nproject: proj\n"
        "title: \"session-handoff-2026-08-12\"\n---\nx\n",
        encoding="utf-8",
    )

    assert cli.main([
        "knowledge", "mark-episodic", "--memory-root", str(tmp_path), "--apply",
    ]) == 0
    capsys.readouterr()

    # --revert found -> exit 0
    assert cli.main([
        "knowledge", "mark-episodic", "--memory-root", str(tmp_path),
        "--revert", "sl-1",
    ]) == 0
    out = capsys.readouterr().out
    assert '"reverted": true' in out

    # --revert not-found -> exit 1
    assert cli.main([
        "knowledge", "mark-episodic", "--memory-root", str(tmp_path),
        "--revert", "sl-nope",
    ]) == 1
    out = capsys.readouterr().out
    assert '"reverted": false' in out and "not-found" in out

    # --revert not-migrated -> exit 1 (pipeline-style episodic note, no
    # episodic_demoted_by marker -- Task 13's publish-time demotion shape)
    (knowledge / "n--sl-2.md").write_text(
        "---\nslice_id: sl-2\nmemory_layer: episodic\nproject: proj\n"
        "episodic_reason: title:session-state\ntitle: \"handoff\"\n---\nx\n",
        encoding="utf-8",
    )
    assert cli.main([
        "knowledge", "mark-episodic", "--memory-root", str(tmp_path),
        "--revert", "sl-2",
    ]) == 1
    out = capsys.readouterr().out
    assert '"reverted": false' in out and "not-migrated" in out

    # scan/apply path with warnings -> exit 1
    (knowledge / "n--sl-bad.md").write_bytes(
        b"---\nslice_id: sl-bad\nmemory_layer: knowledge\n---\n\xff\xfe not utf-8\n"
    )
    assert cli.main([
        "knowledge", "mark-episodic", "--memory-root", str(tmp_path), "--dry-run",
    ]) == 1
    err = capsys.readouterr().err
    assert "warning:" in err


def _iter_parsers(parser: argparse.ArgumentParser):
    """Yield `parser` and every subparser reachable via `add_subparsers()`,
    recursively (e.g. `followups` nests its own list/verify/close/extract
    subparsers under the top-level `knowledge`/`memory` ones)."""
    yield parser
    subparsers_group = getattr(parser, "_subparsers", None)
    if subparsers_group is None:
        return
    for action in subparsers_group._group_actions:
        for sub in getattr(action, "choices", {}).values():
            yield from _iter_parsers(sub)


def test_help_renders_for_every_subcommand_without_exception():
    # issue #139: an un-escaped `%` in a `help=` string (e.g. "~70% token")
    # made argparse's `%`-formatting of help text raise ValueError at
    # `--help` time. Render top-level help and every subparser's help
    # recursively so any future unescaped `%` fails here instead of at
    # `hippo --help` in the field.
    parser = cli._build_parser()
    for sub_parser in _iter_parsers(parser):
        sub_parser.format_help()
