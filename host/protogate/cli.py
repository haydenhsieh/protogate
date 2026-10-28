"""One-request command line interface."""

import argparse
import json
import sys

from .client import ProtoGate, ProtoGateError


def main() -> None:
    parser = argparse.ArgumentParser(description="Send one JSON command to ProtoGate")
    transport = parser.add_mutually_exclusive_group(required=True)
    transport.add_argument("--serial", metavar="PORT")
    transport.add_argument("--tcp", metavar="HOST")
    parser.add_argument("--port", type=int, default=4242, help="TCP port (default: 4242)")
    parser.add_argument("command", nargs="?", help="JSON object; read stdin when omitted")
    args = parser.parse_args()
    try:
        command = json.loads(args.command) if args.command else json.load(sys.stdin)
        if not isinstance(command, dict):
            raise ValueError("command must be a JSON object")
        gate = (ProtoGate.serial(args.serial) if args.serial
                else ProtoGate.tcp(args.tcp, args.port))
        with gate:
            print(json.dumps(gate.request(command)))
    except (ValueError, ProtoGateError, OSError) as exc:
        parser.exit(2, f"protogate: {exc}\n")


if __name__ == "__main__":
    main()
