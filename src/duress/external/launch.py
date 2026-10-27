"""POSIX resource limits applied before exec, without a shell."""
import os
import resource
import sys


def main():
    memory_mb, processes, *command = sys.argv[1:]
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
    # RLIMIT_AS is not enforced consistently on macOS. The process-count
    # monitor remains authoritative; NPROC is deliberately not set per user.
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (int(memory_mb) * 1024**2,) * 2)
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    main()
