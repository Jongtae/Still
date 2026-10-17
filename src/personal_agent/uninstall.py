"""`agentos uninstall`: remove Still from this computer in one place (#1545).

The steps are the existing ones, run in order: each background service through
``ServiceController.uninstall``, the data folders and their Keychain keys only
when the owner types ``delete``, then the program through the package manager
that installed it.
"""
import argparse
import fcntl
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .api_credentials import KEY_SERVICE as API_KEY_SERVICE
from .browser_jar import SERVICE as JAR_SERVICE, KeychainKey, store_account
from .family_setup import family_instances
from .first_run import Terminal, terminal_language
from .messages import msg
from .service_control import DEFAULT_DATA_RELATIVE, DEFAULT_INSTANCES_RELATIVE, LABEL, service_action

PACKAGE = 'personal-agentos'


def services(home):
    """The installed background services: None for the owner's, then family instance names."""
    agents = Path(home) / 'Library/LaunchAgents'
    owner = [None] if (agents / f'{LABEL}.plist').is_file() else []
    return owner + family_instances(home)


def data_folders(home, env):
    owner = Path(env.get('AGENTOS_DATA') or Path(home) / DEFAULT_DATA_RELATIVE).expanduser()
    instances = Path(home) / DEFAULT_INSTANCES_RELATIVE
    found = [owner] if owner.is_dir() else []
    if instances.is_dir():
        found += sorted(path for path in instances.iterdir() if path.is_dir())
    return found


def running(folder):
    """Whether an Still process holds this data folder (its instance lock)."""
    lock = Path(folder) / 'private' / 'instance.lock'
    if not lock.is_file():
        return False
    with lock.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


def program_remover(prefix, home, which=shutil.which):
    """(label, command) that removes this installation, or (None, None) when it cannot tell."""
    prefix = str(prefix)
    if '/Cellar/agentos/' in prefix:
        brew = which('brew')
        return ('Homebrew', [brew, 'uninstall', 'agentos']) if brew else (None, None)
    if f'/uv/tools/{PACKAGE}' in prefix:
        uv = which('uv') or str(Path(home) / '.local/bin/uv')
        return ('uv', [uv, 'tool', 'uninstall', PACKAGE]) if Path(uv).is_file() else (None, None)
    return None, None


def delete_keys(folder, key_factory=KeychainKey):
    """Delete the Keychain keys that encrypt this data folder's secrets; return how many existed."""
    from .quickstart import GOOGLE_OAUTH_KEY_SERVICE
    folder = Path(folder)
    keys = ((folder, API_KEY_SERVICE), (folder, GOOGLE_OAUTH_KEY_SERVICE),
            (folder / 'private' / 'browser-profile', JAR_SERVICE))
    return sum(bool(key_factory(store_account(root), service).delete()) for root, service in keys)


def run(term, *, home, env, platform, prefix, assume_yes=False, delete_data=False,
        action=service_action, runner=subprocess.run, key_factory=KeychainKey, which=shutil.which):
    """Return 0 when done or cancelled, 1 when a step could not be completed."""
    mac = platform == 'darwin'
    jobs = services(home) if mac else []
    folders = data_folders(home, env)
    label, command = program_remover(prefix, home, which)
    term.say(msg('uninstall.title'))
    term.say('   ' + msg('uninstall.item_services', count=len(jobs)))
    term.say('   ' + (msg('uninstall.item_program', method=label) if label else msg('uninstall.item_program_manual')))
    term.say(msg('uninstall.data_kept'))
    for folder in folders:
        term.say(f'   {folder}')
    if not assume_yes and term.ask(msg('uninstall.confirm') + ' ').lower() not in ('y', 'yes'):
        term.say(msg('uninstall.cancelled'))
        return 0
    if folders and not assume_yes:
        delete_data = term.ask(msg('uninstall.delete_data_prompt') + ' ') == 'delete'
    ok = True
    for instance in jobs:
        name = instance or 'agentos'
        receipt = action('uninstall', instance=instance) if instance else action('uninstall')
        if receipt.get('ok'):
            term.say('   ' + msg('uninstall.service_removed', name=name))
        else:
            term.say('   ' + msg('uninstall.service_failed', name=name))
            ok = False
    busy = [folder for folder in folders if running(folder)]
    if busy:
        for folder in busy:
            term.say('   ' + msg('uninstall.still_running', path=str(folder)))
        return 1
    for folder in folders:
        if delete_data:
            if mac:
                delete_keys(folder, key_factory)
            shutil.rmtree(folder)
            term.say('   ' + msg('uninstall.data_deleted', path=str(folder)))
        else:
            term.say('   ' + msg('uninstall.data_kept_at', path=str(folder)))
    if command:
        if runner(command, check=False).returncode == 0:
            term.say('   ' + msg('uninstall.program_removed'))
        else:
            term.say('   ' + msg('uninstall.program_failed', command=' '.join(command)))
            ok = False
    else:
        term.say('   ' + msg('uninstall.program_manual'))
    term.say(msg('uninstall.outside'))
    for line in ('outside_telegram', 'outside_google', 'outside_tools'):
        term.say('   ' + msg(f'uninstall.{line}'))
    term.say(msg('uninstall.done') if ok else msg('uninstall.incomplete'))
    return 0 if ok else 1


def main(argv):
    parser = argparse.ArgumentParser(prog='agentos uninstall', description='Remove Still from this computer.')
    parser.add_argument('--yes', action='store_true', help='Do not ask; keep the data unless --delete-data is given.')
    parser.add_argument('--delete-data', action='store_true',
                        help='With --yes, also delete every Still data folder and its Keychain keys. Irreversible.')
    args = parser.parse_args(argv)
    if args.delete_data and not args.yes:
        parser.error('--delete-data needs --yes; without them the command asks in the terminal.')
    if not args.yes and not sys.stdin.isatty():
        parser.error('run it in a terminal, or pass --yes')
    return run(Terminal(terminal_language()), home=Path.home(), env=os.environ, platform=sys.platform,
               prefix=sys.prefix, assume_yes=args.yes, delete_data=args.delete_data)
