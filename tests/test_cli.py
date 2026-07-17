import pytest

from paulsha_hippo import cli


def test_version_flag_prints_and_exits_zero(capsys):
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "hippo 0.1.1"


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
