#!/usr/bin/env python3.7
"""
Bridge between different chat protocols (chiefly Telegram and Teamspeak).

Usage:
    bridge [options]

Options:
    -d, --debug                 Turn on debug logging.
    -c <file>, --config <file>  Specify an alternative config file [default: config.toml]
    -h, --help                  Display this help.
"""
from aiorun import run
import asyncio
from docopt import docopt
from core.bridge import Bridge
import core.logging
import toml
import os
import logging
from typing import Dict, Any

def main(args: Dict) -> None:
    if not os.path.exists(args['--config']):
        return print(f'Error: configuration file {args["--config"]!r} does not exist.')

    with open(args['--config'], 'r') as f:
        config = dict(toml.load(f))

    if (args['--debug']):
        core.logging.init(level=logging.DEBUG)
    else:
        core.logging.init(level=logging.INFO)

    bridge = Bridge(config)

    run(bridge.start(), loop=asyncio.get_event_loop(), stop_on_unhandled_errors=True)

main(docopt(__doc__, help=True))
