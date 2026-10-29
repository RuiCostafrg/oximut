"""Linux bubblewrap boundary. Only explicitly supplied candidate paths are writable."""
import os
from pathlib import Path
import resource
import platform
import shutil
import signal
import stat
import subprocess
import tempfile


def safe_tree(root, *, external_links=()):
    root = Path(root).resolve()
    allowed = {Path(p): Path(target) for p, target in external_links}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                target = path.resolve(strict=True)
                if not target.is_relative_to(root) and allowed.get(path) != target:
                    raise ValueError(f"escaping candidate symlink: {path}")
            elif not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValueError(f"special candidate file: {path}")
            elif stat.S_ISREG(mode) and path.stat().st_nlink != 1:
                raise ValueError(f"hardlinked candidate file: {path}")


def read_regular(path, limit=16 * 1024 * 1024):
    # Never follow a candidate report link, block on a FIFO, or read unbounded data.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError(f"invalid candidate output: {path}")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f"oversized candidate output: {path}")
        return data


def run(command, *, cwd, writable, readonly=(), timeout=300, network=False,
        node_options=None):
    if 'proot' in platform.release().lower() or not Path('/usr/bin/python3').is_file():
        raise RuntimeError('native Linux with system Python required; PRoot is unsupported')
    if os.geteuid() == 0:
        raise RuntimeError('candidate execution requires an unprivileged host UID')
    bwrap = shutil.which('bwrap')
    if not bwrap:
        raise RuntimeError('bubblewrap required for candidate execution (no unsafe fallback)')
    args = [bwrap, '--unshare-all', '--unshare-user', '--disable-userns', '--die-with-parent', '--new-session',
            '--cap-drop', 'ALL', '--clearenv', '--proc', '/proc', '--dev', '/dev',
            '--dir', '/tmp', '--tmpfs', '/home', '--dir', '/home/candidate',
            '--setenv', 'HOME', '/home/candidate', '--setenv', 'LANG', 'C.UTF-8']
    # No host home, checkout, runner environment, socket, or /run mount.
    for path in ('/usr', '/bin', '/sbin', '/lib', '/lib64', '/etc/ssl/certs',
                 '/etc/resolv.conf', '/etc/hosts'):
        if Path(path).exists():
            args += ['--ro-bind', path, path]
    # Ubuntu's /usr/bin/cc and c++ links traverse /etc/alternatives.
    # Expose only compiler aliases resolving into the trusted /usr toolchain.
    args += ['--dir', '/etc/alternatives']
    for name in ('cc', 'c++'):
        alias = Path('/etc/alternatives') / name
        if alias.is_file() and alias.resolve().is_relative_to('/usr'):
            # Preserve canonical /usr location for GCC's cc1/collect2 lookup.
            args += ['--symlink', str(alias.resolve()), str(alias)]
    # Mount only standalone tool distributions, never rustup/cargo homes or PATH dirs.
    tool_dirs = []
    node = Path(shutil.which('node') or '/usr/bin/node').resolve()
    if not node.is_relative_to('/usr'):
        tool_dirs.append(node.parent.parent)
    rustc = subprocess.check_output(['rustup', 'which', 'rustc'], text=True).strip() if shutil.which('rustup') else None
    if rustc:
        tool_dirs.append(Path(rustc).parent.parent)
    for directory in tool_dirs:
        args += ['--ro-bind', str(directory), str(directory)]
    path = ':'.join(str(p / 'bin') for p in tool_dirs) + ':/usr/bin:/bin'
    args += ['--setenv', 'PATH', path, '--setenv', 'CARGO_HOME', '/home/candidate/.cargo']
    if node_options:
        args += ['--setenv', 'NODE_OPTIONS', node_options]
    if network:
        args += ['--share-net']
    for option, paths in (('--ro-bind', readonly), ('--bind', writable)):
        for item in paths:
            item = str(Path(item).resolve())
            args += [option, item, item]
    args += ['--chdir', str(cwd), '--', *map(str, command)]

    def limits():
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (512 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 1024))
        resource.setrlimit(resource.RLIMIT_NPROC, (1024, 1024))

    # File-backed logs cannot keep communicate() alive via an inherited pipe.
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(args, stdout=log, stderr=log, start_new_session=True,
                                   cwd='/tmp', preexec_fn=limits, env={'PATH': '/usr/bin:/bin'})
        try:
            process.wait(timeout=timeout)
        finally:
            # Killing the namespace init kills even setsid/double-fork descendants.
            # bwrap's parent-death signal also covers cancellation of this harness.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        log.seek(0)
        output = log.read(1024 * 1024).decode(errors='replace')
    if process.returncode:
        raise RuntimeError(f'isolated command exited {process.returncode}: {output}')
    return output.strip()
