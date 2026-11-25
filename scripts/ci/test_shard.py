"""Print one share of the test files for a sharded `validate` run (#1150).

Every ``tests/test_*.py`` goes to exactly one of ``--total`` shares, whole, so the
shards together run the whole suite and each file keeps `--dist loadfile`'s
one-worker-per-file behaviour.  Files are placed largest first on the lightest
share (longest-processing-time first, with file size standing in for run time);
the order is deterministic, so every shard computes the same split.
"""
import argparse
from pathlib import Path


def shares(root, total):
    files = sorted(Path(root, 'tests').glob('test_*.py'), key=lambda path: (-path.stat().st_size, path.name))
    bins = [[0, index, []] for index in range(total)]
    for path in files:
        lightest = min(bins, key=lambda share: (share[0], share[1]))
        lightest[0] += path.stat().st_size
        lightest[2].append(path.relative_to(root).as_posix())
    return [sorted(share[2]) for share in bins]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=int, required=True, help='1-based share number')
    parser.add_argument('--total', type=int, required=True)
    parser.add_argument('--root', default='.')
    args = parser.parse_args(argv)
    if not 1 <= args.index <= args.total:
        parser.error('--index must be between 1 and --total')
    print(' '.join(shares(args.root, args.total)[args.index - 1]))


if __name__ == '__main__':
    main()
