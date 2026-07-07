import unittest
from pathlib import Path
from unittest import mock


class TestWakeupCLI(unittest.TestCase):
    def test_explicit_project_prints_brief(self):
        # When --project is provided, run should call build_brief and print its result
        import paulsha_hippo.wakeup.cli as cli

        args = mock.Mock()
        args.memory_root = '/fake/root'
        args.project = 'myproj'
        args.cwd = None
        args.k = 3
        args.char_budget = 100
        args.now = None

        with mock.patch('paulsha_hippo.wakeup.cli.build_brief', return_value='BRIEF') as bb:
            with mock.patch('builtins.print') as p:
                rc = cli.run(args)
                bb.assert_called_once()
                p.assert_called_once_with('BRIEF')
                self.assertEqual(rc, 0)

    def test_unknown_project_prints_nothing(self):
        # When project is unknown and build_brief returns empty, nothing is printed
        import paulsha_hippo.wakeup.cli as cli

        args = mock.Mock()
        args.memory_root = '/fake/root'
        args.project = None
        args.cwd = None
        args.k = 1
        args.char_budget = 50
        args.now = None

        with mock.patch('paulsha_hippo.wakeup.cli.resolve_project', return_value='_unknown'):
            with mock.patch('paulsha_hippo.wakeup.cli.build_brief', return_value='') as bb:
                with mock.patch('builtins.print') as p:
                    rc = cli.run(args)
                    bb.assert_called_once()
                    p.assert_not_called()
                    self.assertEqual(rc, 0)



    def test_wakeup_main_parsing_defaults(self):
        # Integration-level test: calling wakeup.cli.main should parse defaults
        from paulsha_hippo import paths
        import paulsha_hippo.wakeup.cli as wakeup_cli

        with mock.patch.dict("os.environ", {"PSC_AGENTS_ROOT": "/tmp/psc-agents"}, clear=False):
            default_root = str(paths.memory_root())
            with mock.patch('paulsha_hippo.wakeup.cli.build_brief', return_value='BRIEF') as bb:
                # call main without specifying --memory-root; should use facade default
                rc = wakeup_cli.main(['--project', 'myproj'])
            bb.assert_called_once()
            called_args, called_kwargs = bb.call_args
            # first positional arg is memory_root Path
            assert str(called_args[0]) == default_root
            # now should be materialized to end with Z
            now_val = called_kwargs.get('now')
            assert now_val is not None and now_val.endswith('Z')
            assert rc == 0

    def test_root_wakeup_registration_smoke(self):
        # Smoke test for root CLI registration: call top-level main
        import paulsha_hippo.cli as mem_cli
        import paulsha_hippo.wakeup.cli as wakeup_cli

        with mock.patch('paulsha_hippo.wakeup.cli.build_brief', return_value='BRIEF') as bb:
            with mock.patch('builtins.print') as p:
                rc = mem_cli.main(['wakeup', '--project', 'myproj'])
                bb.assert_called_once()
                p.assert_called_once_with('BRIEF')
                assert rc == 0


if __name__ == '__main__':
    unittest.main()
