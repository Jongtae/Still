"""Print one share of the test files for a sharded `validate` run (#1150).

Every ``tests/test_*.py`` (or, with ``--files``, every listed file: the tests a text or
release scope selected) goes to exactly one of ``--total`` shares, whole, so the
shards together run the whole set and each file keeps `--dist loadfile`'s
one-worker-per-file behaviour.  Files are placed largest first on the lightest
share (longest-processing-time first, with file size standing in for run time);
the order is deterministic, so every shard computes the same split.
"""
import argparse
from pathlib import Path


def shares(root, total, names=None):
    paths = (Path(root, 'tests').glob('test_*.py') if names is None
             else (Path(root, name) for name in dict.fromkeys(names)))
    files = sorted(paths, key=lambda path: (-path.stat().st_size, path.name))
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
    parser.add_argument('--files', help='space-separated test files to split instead of every test file')
    args = parser.parse_args(argv)
    if not 1 <= args.index <= args.total:
        parser.error('--index must be between 1 and --total')
    names = args.files.split() if args.files is not None else None
    print(' '.join(shares(args.root, args.total, names)[args.index - 1]))


if __name__ == '__main__':
    main()
