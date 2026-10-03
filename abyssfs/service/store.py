"""与 UI 和业务模型无关的原子 JSON 配置仓库。"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, Optional


class ConfigStore:
    def __init__(self, path: str) -> None:
        self.path = path

    def load(self) -> Optional[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return None
        with open(self.path, "r", encoding="utf-8") as stream:
            return json.load(stream)

    def save(self, data: Dict[str, Any]) -> None:
        target = os.path.abspath(self.path)
        directory = os.path.dirname(target) or "."
        os.makedirs(directory, exist_ok=True)
        temp_path = None
        try:
            fd, temp_path = tempfile.mkstemp(
                prefix=os.path.basename(target) + ".",
                suffix=".tmp",
                dir=directory,
                text=True,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, indent=4, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, target)
            temp_path = None
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

