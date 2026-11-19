"""Cross-platform nonblocking file lock for scheduled tasks."""

import sys

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


def acquire_process_lock(lock_file) -> None:
    """Acquire a non-blocking process lock on Unix or Windows."""

    if sys.platform == "win32":
        try:
            lock_file.seek(0)
            if not lock_file.read(1):
                lock_file.seek(0)
                lock_file.write("0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BlockingIOError from exc
    else:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
