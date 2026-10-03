"""无 UI 的 Service 命令入口。"""

from __future__ import annotations

import argparse
import signal
from typing import Optional

from abyssfs.paths import CONFIG_PATH
from abyssfs.protocol.base import FileServerProtocol
from abyssfs.service.factory import ServiceFactory


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Run AbyssFS without the PyQt UI")
    value.add_argument(
        "--config",
        default=str(CONFIG_PATH),
        help="Path to the AbyssFS JSON configuration",
    )
    value.add_argument(
        "--static-facade",
        action="store_true",
        help="Disable service facade code hot loading",
    )
    return value


def run(config: str, *, hot: bool = True) -> int:
    runtime = ServiceFactory.open(config, FileServerProtocol.FTP, hot=hot)
    service = runtime.api()

    def stop(_signum=None, _frame=None) -> None:
        service.stop()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    result = service.start()
    if not result.ok:
        raise RuntimeError(result.message or "AbyssFS service failed to start")
    try:
        service.wait()
    finally:
        service.stop()
    return 0


def main(argv: Optional[list] = None) -> int:
    args = parser().parse_args(argv)
    return run(args.config, hot=not args.static_facade)

