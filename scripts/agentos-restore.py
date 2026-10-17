"""Restore a portable AgentOS owner-state export into an empty directory."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from personal_agent.portable_state import restore_owner_state

parser=argparse.ArgumentParser();parser.add_argument('archive');parser.add_argument('data');args=parser.parse_args()
try: print(restore_owner_state(args.archive,args.data))
except ValueError as exc: parser.error(str(exc))
