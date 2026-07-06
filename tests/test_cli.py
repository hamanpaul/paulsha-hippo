from paulsha_hippo import cli


def test_version_flag_prints_and_exits_zero(capsys):
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "hippo 0.1.0"


def test_no_args_prints_usage_and_exits_nonzero(capsys):
    assert cli.main([]) == 2
    assert "usage" in capsys.readouterr().err.lower()
