"""Generate standalone Ditto MCP JSON; no shell, Bun, or fixed checkout path required."""
import argparse
import json
from pathlib import Path
import shutil
import sys

from portable import command


def configuration(include_dart=False):
    directory = str(Path(__file__).resolve().parent)
    uv = shutil.which('uv')
    if not uv:
        raise RuntimeError('Install uv and put it on PATH before generating configuration')
    roles = ['jadx', 'apktool', 'r2flutter', 'mobile-control']
    servers = {f'ditto-{role}': {
        'command': uv, 'args': ['--directory', directory, 'run', '--locked', 'server.py'],
        'env': {'DITTO_ROLE': role},
    } for role in roles}
    if include_dart:
        argv = command('dart', ['mcp-server'])
        servers['dart'] = {'command': argv[0], 'args': argv[1:]}
    return {'mcpServers': servers}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='write a new config file; does not overwrite an existing file')
    parser.add_argument('--dart', action='store_true')
    args = parser.parse_args()
    try:
        payload = json.dumps(configuration(args.dart), indent=2) + '\n'
        if args.output:
            with args.output.open('x', encoding='utf-8') as handle:
                handle.write(payload)
        else:
            print(payload, end='')
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
