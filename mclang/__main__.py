from __future__ import annotations

import argparse
import sys

from .compile import compile_source
from .ir import lower
from .parse import Parser


def cmd_compile(args: argparse.Namespace) -> None:
    """Compile a .mcl source file into a directory of .mcfunction files."""
    source_path = args.source
    out_dir = args.output

    try:
        with open(source_path, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        print(f"Error: source file not found: {source_path}", file=sys.stderr)
        sys.exit(1)
    except OSError as exc:
        print(f"Error reading source file: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        ast = Parser(text).parse()
        ir = lower(ast)
        compile_source(text, out_dir)
    except NotImplementedError as exc:
        print(f"CompileError: {exc}", file=sys.stderr)
        sys.exit(1)
    except SyntaxError as exc:
        print(f"CompileError: {exc}", file=sys.stderr)
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m mclang",
        description="Compile .mcl source into Minecraft .mcfunction datapacks.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    compile_parser = subparsers.add_parser(
        "compile",
        help="Compile a .mcl source file into .mcfunction files.",
    )
    compile_parser.add_argument(
        "source",
        help="Path to the .mcl source file.",
    )
    compile_parser.add_argument(
        "output",
        help="Output directory for generated .mcfunction files.",
    )
    compile_parser.set_defaults(func=cmd_compile)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
