"""Create a portable, secret-free AgentOS owner-state export."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from personal_agent.portable_state import export_owner_state

parser=argparse.ArgumentParser();parser.add_argument('data');parser.add_argument('archive');args=parser.parse_args()
try: print(export_owner_state(args.data,args.archive))
except ValueError as exc: parser.error(str(exc))
