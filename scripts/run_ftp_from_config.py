"""Run the configured FTP server as a long-lived background process."""

from __future__ import annotations

import logging
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "ftp_users.json"
LOG_PATH = ROOT / "logs" / "ftp_background.log"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from abyssfs.protocol.base import FileServerProtocol
from abyssfs.service import ServiceFactory


def main() -> int:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(LOG_PATH),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        encoding="utf-8",
    )

    runtime = ServiceFactory.open(str(CONFIG_PATH), FileServerProtocol.FTP)
    service = runtime.api()
    if service.get_protocol() != FileServerProtocol.FTP:
        raise RuntimeError(
            f"configured protocol is {service.get_protocol().value!r}, expected 'ftp'"
        )

    def stop(_signum=None, _frame=None) -> None:
        logging.info("stop requested")
        service.stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    status = service.stat()
    logging.info("starting FTP server on %s:%s", status.host, status.port)
    try:
        result = service.start()
        if not result.ok:
            raise RuntimeError(result.message or "failed to start FTP service")
        service.wait()
    finally:
        service.stop()
        logging.info("FTP server exited")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        logging.exception("FTP background process failed")
        raise
