"""Read live SDK pane registrations; registrations never grant capture access."""
import itertools
import json
import os
from pathlib import Path
import re
import stat

TOKEN = re.compile(r'^[0-9a-f]{32}$')
_PROC = Path('/proc')


def _private_directory(path):
    info = path.lstat()
    if (not path.is_absolute() or path.resolve() != path or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ValueError('Unsafe application-source directory')


def _directory(env):
    runtime = Path(env.get('XDG_RUNTIME_DIR', ''))
    _private_directory(runtime)
    directory = runtime / 'kilix-capture-sources'
    _private_directory(directory)
    return directory


def _identity(pid):
    if type(pid) is not int or pid <= 0:
        raise ValueError('Invalid application-source process')
    process = _PROC / str(pid)
    if process.stat().st_uid != os.getuid():
        raise ValueError('Foreign application-source process')
    fields = (process / 'stat').read_text().rsplit(')', 1)[1].split()
    if fields[0] in {'Z', 'X'}:
        raise ValueError('Application-source process exited')
    return int(fields[1]), fields[19]


def _unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('Duplicate application-source field')
        result[key] = value
    return result


def read(token, env):
    """A stale, foreign or substituted record is unavailable, never redirected."""
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        return None
    try:
        directory = _directory(env)
        dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(dfd)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                return None
            fd = os.open(token + '.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
        finally:
            os.close(dfd)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
                    or info.st_nlink != 1 or info.st_size > 8192):
                return None
            data = stream.read(8193)
        if len(data) > 8192:
            return None
        value = json.loads(data, object_pairs_hook=_unique_object)
        if (not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1
                or value.get('id') != token or not isinstance(value.get('label'), str)
                or not isinstance(value.get('display'), str) or not re.fullmatch(r':\d+', value['display'])
                or value.get('desktop_display') != env['DISPLAY'].removesuffix('.0')
                or value['display'] == value['desktop_display']):
            return None
        identities = {}
        for role in ('owner', 'server', 'app'):
            pid = value[role + '_pid']
            identities[role] = _identity(pid)
            if identities[role][1] != value[role + '_start']:
                return None
        if any(identities[role][0] != value['owner_pid'] for role in ('server', 'app')):
            return None
        if (type(value.get('xid')) is not int or not 1 <= value['xid'] <= 0xffffffff
                or any(type(value.get(key)) is not int or not 1 <= value[key] <= 16384 for key in ('width','height'))
                or value['width'] * value['height'] > 67108864):
            return None
        authority = Path(value['authority'])
        info = authority.lstat()
        if (not authority.is_absolute() or authority.resolve() != authority or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1
                or (info.st_dev, info.st_ino) != (value['authority_device'], value['authority_inode'])):
            return None
        server = _PROC / str(value['server_pid'])
        if (server / 'exe').resolve().name != 'Xvfb':
            return None
        with (server / 'cmdline').open('rb') as stream:
            command = stream.read(65537)
        if len(command) > 65536:
            return None
        args = command.rstrip(b'\0').split(b'\0')
        if (len(args) < 2 or args[1] != value['display'].encode()
                or args.count(b'-auth') != 1 or args[args.index(b'-auth') + 1] != os.fsencode(authority)
                or args.count(b'-nolisten') != 1 or args[args.index(b'-nolisten') + 1] != b'tcp'
                or args.count(b'-screen') != 1 or args[args.index(b'-screen') + 1] != b'0'):
            return None
        # `kilix run` allocates a large framebuffer and sizes the screen to its
        # pane with RandR, so the registered size is at most the -screen size.
        screen = re.fullmatch(rb'([1-9][0-9]{0,4})x([1-9][0-9]{0,4})x24', args[args.index(b'-screen') + 2])
        if screen is None or value['width'] > int(screen[1]) or value['height'] > int(screen[2]):
            return None
        if any(_identity(value[role + '_pid']) != identities[role] for role in identities):
            return None
        value['label'] = ' '.join(value['label'].split())[:200] or 'Application'
        return value
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return None


def records(env):
    try:
        directory = _directory(env)
        paths = sorted(itertools.islice(directory.iterdir(), 512))
    except (OSError, ValueError):
        return []
    answer = []
    for path in paths:
        if path.suffix == '.json':
            value = read(path.stem, env)
            if value is not None:
                answer.append(value)
                if len(answer) == 128:
                    break
    return answer
