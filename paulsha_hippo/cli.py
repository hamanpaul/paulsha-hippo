"""hippo CLI 入口（骨架期：僅 --version；命令樹於 code 遷入時擴充）。"""
import sys

from paulsha_hippo import __version__

_USAGE = "usage: hippo --version"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--version"]:
        print(f"hippo {__version__}")
        return 0
    print(_USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
