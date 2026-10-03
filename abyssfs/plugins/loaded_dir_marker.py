"""Display-only directory marker plugin.

When enabled, a directory is displayed with a leading ``#`` after that
directory has been listed once. The marker is never written to disk.
"""

from __future__ import annotations

import os
from typing import Iterable, Set


MARKER_PREFIX = "#"


class LoadedDirMarker:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self._loaded_dirs: Set[str] = set()

    def mark_loaded(self, real_path: str) -> None:
        if self.enabled and real_path:
            self._loaded_dirs.add(self._normalize(real_path))

    def is_loaded(self, real_path: str) -> bool:
        return self.enabled and self._normalize(real_path) in self._loaded_dirs

    def display_name(self, name: str, real_path: str) -> str:
        if self.is_loaded(real_path) and not name.startswith(MARKER_PREFIX):
            return f"{MARKER_PREFIX}{name}"
        return name

    def storage_name(self, name: str, sibling_real_paths: Iterable[str]) -> str:
        if not self.enabled or not name.startswith(MARKER_PREFIX):
            return name

        candidate = name[len(MARKER_PREFIX):]
        normalized_siblings = {self._normalize(path) for path in sibling_real_paths}
        candidate_path = next(
            (
                path for path in normalized_siblings
                if os.path.basename(path) == candidate and path in self._loaded_dirs
            ),
            None,
        )
        return candidate if candidate_path else name

    @staticmethod
    def _normalize(real_path: str) -> str:
        return os.path.normcase(os.path.normpath(real_path))
