"""Apply a Landlock sandbox (Linux >= 6.7, ABI >= 4) to this process, then exec a command.

Kernel-enforced, unprivileged, irreversible after exec:
  * filesystem: only the --ro paths are readable/executable; only the --rw paths are writable;
    everything else (home directory, SSH keys, credential files, other projects, other datasets,
    reference answers) is inaccessible
  * network: all TCP bind and connect are denied (ABI 4). UDP and unix sockets are not covered by
    Landlock ABI 4; the server has no code paths using them.
  * no_new_privs is set; the environment is replaced by an explicit allowlist (--env).

Usage: landlock_exec.py --ro P ... --rw P ... --env K=V ... -- cmd args...
"""
import ctypes
import os
import struct
import sys

SYS_CREATE, SYS_ADD, SYS_RESTRICT = 444, 445, 446
RULE_PATH_BENEATH = 1
FS_ALL_V3 = (1 << 15) - 1          # EXECUTE..TRUNCATE (ABI 3)
FS_RO = 1 | 4 | 8                  # EXECUTE | READ_FILE | READ_DIR
FS_FILE_RO = 1 | 4                 # rules on regular files may only carry file rights
NET_BIND, NET_CONNECT = 1, 2
PR_SET_NO_NEW_PRIVS = 38
libc = ctypes.CDLL(None, use_errno=True)


def die(msg):
    sys.stderr.write(f"landlock_exec: {msg}\n")
    sys.exit(126)


def main(argv):
    ro, rw, env, i = [], [], {}, 0
    while i < len(argv) and argv[i] != "--":
        flag, val = argv[i], argv[i + 1]
        {"--ro": ro, "--rw": rw}.get(flag, []).append(val) if flag in ("--ro", "--rw") else None
        if flag == "--env":
            k, _, v = val.partition("=")
            env[k] = v
        i += 2
    cmd = argv[i + 1:]
    if not cmd:
        die("no command")
    abi = libc.syscall(SYS_CREATE, None, 0, 1)
    if abi < 4:
        die(f"Landlock ABI {abi} < 4 (network restriction unavailable); refusing to run unsandboxed")
    attr = struct.pack("QQ", FS_ALL_V3, NET_BIND | NET_CONNECT)  # handled_access_fs, handled_access_net
    buf = ctypes.create_string_buffer(attr)
    rfd = libc.syscall(SYS_CREATE, buf, len(attr), 0)
    if rfd < 0:
        die(f"create_ruleset failed errno={ctypes.get_errno()}")

    def allow(path, rights):
        try:
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        except FileNotFoundError:
            return
        if not os.path.isdir(path):
            rights &= FS_FILE_RO if rights == FS_RO else (FS_ALL_V3 & ~(8 | 16 | 32 | 64 | 128 | 256 | 512 | 1024 | 2048 | 4096 | 8192))
        pb = ctypes.create_string_buffer(struct.pack("=Qi", rights, fd))
        if libc.syscall(SYS_ADD, rfd, RULE_PATH_BENEATH, pb, 0) != 0:
            die(f"add_rule {path} errno={ctypes.get_errno()}")
        os.close(fd)

    for p in ro:
        allow(p, FS_RO)
    for p in rw:
        allow(p, FS_ALL_V3)
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        die("prctl(NO_NEW_PRIVS) failed")
    if libc.syscall(SYS_RESTRICT, rfd, 0) != 0:
        die(f"restrict_self failed errno={ctypes.get_errno()}")
    os.execve(cmd[0], cmd, env)


if __name__ == "__main__":
    main(sys.argv[1:])
