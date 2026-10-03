"""线程安全、版本化的目录运行时注册表。"""

from __future__ import annotations

import threading
from typing import Dict, Iterable, List, Optional, Tuple

from abyssfs.model.entities import UserProfile, VirtualDirectory


class DirectoryUser:
    def __init__(
        self,
        profile: UserProfile,
        dirs: Iterable[VirtualDirectory],
        version: int,
    ) -> None:
        self.profile = profile
        self.dirs: Tuple[VirtualDirectory, ...] = tuple(dirs)
        self.version = version


class DirectorySnapshot:
    def __init__(self, version: int, users: Dict[str, DirectoryUser]) -> None:
        self.version = version
        self.users = dict(users)


class DirectoryRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshot = DirectorySnapshot(0, {})

    def replace(
        self,
        users: Iterable[UserProfile],
        *,
        refresh: bool = True,
    ) -> DirectorySnapshot:
        with self._lock:
            version = self._snapshot.version + 1
            entries: Dict[str, DirectoryUser] = {}
            for source in users:
                profile = UserProfile.from_dict(source.to_dict())
                dirs = profile.get_mountable_virtual_dirs(
                    strict_required=True,
                    refresh=refresh,
                )
                entries[profile.username] = DirectoryUser(profile, dirs, version)
            self._snapshot = DirectorySnapshot(version, entries)
            return self._snapshot

    def restore(self, snapshot: DirectorySnapshot) -> None:
        with self._lock:
            self._snapshot = snapshot

    def snapshot(self) -> DirectorySnapshot:
        return self._snapshot

    def user(self, username: str) -> Optional[DirectoryUser]:
        return self._snapshot.users.get(username)

    def profiles(self) -> List[UserProfile]:
        return [entry.profile for entry in self._snapshot.users.values()]

    def version(self) -> int:
        return self._snapshot.version

