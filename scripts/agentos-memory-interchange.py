"""Export the owner's Memory as a Portable Memory bundle, or import one as reviewable candidates (#38)."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from personal_agent.memory_interchange import MemoryInterchangeError, export_memory, import_memory
from personal_agent.quickstart_store import QuickStore

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('operation', choices=('export', 'import'))
parser.add_argument('data', help='the Still data folder')
parser.add_argument('bundle', help='the bundle folder to write (export) or read (import)')
args = parser.parse_args()
try:
    store = QuickStore(Path(args.data))
    run = export_memory if args.operation == 'export' else import_memory
    print(json.dumps(run(store, 'local-owner', Path(args.bundle)), ensure_ascii=False, indent=1))
except (MemoryInterchangeError, ValueError) as exc:
    parser.error(str(exc))
