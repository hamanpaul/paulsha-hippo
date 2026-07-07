from paulsha_hippo import cli


def test_version_flag_prints_and_exits_zero(capsys):
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "hippo 0.1.0"


def test_no_args_prints_usage_and_exits_2(capsys):
    assert cli.main([]) == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_dream_status_subcommand_exists():
    # 去 memory 前綴層：hippo dream ... 直接可達
    assert cli.main(["dream", "--help"]) == 0
