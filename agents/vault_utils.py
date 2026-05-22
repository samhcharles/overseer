"""
Shared vault I/O helpers.

All writes to vault files go through vault_write_atomic so that a process
crash mid-write never leaves a partially-written note. The temp file is
created in path.parent (same directory as the target) to guarantee both
live on the same filesystem mount — preserving the atomicity of os.replace().
"""
import os
import tempfile
from contextlib import suppress
from pathlib import Path


def vault_write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        with suppress(OSError):
            os.unlink(tmp_path)
        raise
