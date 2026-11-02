"""Whole-file writes for files that another thread or the web server may read at the same moment."""
from __future__ import annotations

import os
import pathlib
import threading
import time


def write_atomic(path: pathlib.Path, text: str) -> None:
    """Write through a temporary file and os.replace, so neither a reader nor a crash sees half a file."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:  # some mounts do not support fsync; the rename below still keeps the file whole
                pass
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:  # Windows refuses while another handle reads the old file
                if attempt == 4:
                    raise
                time.sleep(0.05)
    finally:
        if tmp.exists():
            tmp.unlink()
