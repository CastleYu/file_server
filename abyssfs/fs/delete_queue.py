from __future__ import annotations

import errno
import logging
import os
import queue
import threading
import time
import uuid

from abyssfs import ospath
from abyssfs.fs.constants import DeleteConfig, DeleteConst

_logger = logging.getLogger(__name__)


class DeleteJob:
    def __init__(self, path: str, is_dir: bool, attempt: int = 0) -> None:
        self.path = path
        self.is_dir = is_dir
        self.attempt = attempt


class AsyncDeleteService:
    _default: "AsyncDeleteService | None" = None
    _default_lock = threading.Lock()

    def __init__(self) -> None:
        self._jobs: "queue.Queue[DeleteJob]" = queue.Queue()
        self._worker = threading.Thread(
            target=self._run,
            name=DeleteConst.WORKER_NAME,
            daemon=True,
        )
        self._worker.start()

    @classmethod
    def default(cls) -> "AsyncDeleteService":
        with cls._default_lock:
            if cls._default is None:
                cls._default = cls()
            return cls._default

    @classmethod
    def is_internal_name(cls, name: str) -> bool:
        return name == DeleteConst.QUEUE_DIR

    @classmethod
    def is_internal_path(cls, path: str) -> bool:
        normalized = os.path.normpath(path).replace("\\", "/")
        return DeleteConst.QUEUE_DIR in normalized.split("/")

    def file(self, path: str) -> str:
        return self._stage(path, is_dir=False)

    def dir(self, path: str) -> str:
        if ospath.listdir(path):
            raise OSError(errno.ENOTEMPTY, "Directory not empty", path)
        return self._stage(path, is_dir=True)

    def _stage(self, path: str, is_dir: bool) -> str:
        real = os.path.normpath(path)
        if self.is_internal_path(real):
            raise PermissionError(errno.EACCES, "Cannot delete internal queue path", path)

        parent = os.path.dirname(real) or os.curdir
        qdir = os.path.join(parent, DeleteConst.QUEUE_DIR)
        ospath.makedirs(qdir, exist_ok=True)
        staged = os.path.join(qdir, self._job_name(is_dir))
        ospath.replace(real, staged)
        self._jobs.put(DeleteJob(staged, is_dir))
        _logger.info(f"{DeleteConst.LOG_TAG} 已隔离删除目标: {real!r} -> {staged!r}")
        return staged

    def _job_name(self, is_dir: bool) -> str:
        suffix = DeleteConst.DIR_SUFFIX if is_dir else DeleteConst.FILE_SUFFIX
        return f"{DeleteConst.JOB_PREFIX}-{time.time_ns()}-{uuid.uuid4().hex[:DeleteConfig.ID_BYTES]}{suffix}"

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            try:
                self._delete(job)
            finally:
                self._jobs.task_done()

    def _delete(self, job: DeleteJob) -> None:
        try:
            if job.is_dir:
                ospath.rmtree(job.path)
            else:
                ospath.remove(job.path)
            self._clean_queue_dir(job.path)
            _logger.info(f"{DeleteConst.LOG_TAG} 物理删除完成: {job.path!r}")
        except FileNotFoundError:
            self._clean_queue_dir(job.path)
        except Exception as exc:
            if job.attempt < DeleteConfig.RETRIES:
                job.attempt += 1
                time.sleep(DeleteConfig.RETRY_DELAY_SEC)
                self._jobs.put(job)
                _logger.warning(
                    f"{DeleteConst.LOG_TAG} 删除失败，稍后重试: "
                    f"path={job.path!r}, attempt={job.attempt}, err={exc}"
                )
                return
            _logger.error(
                f"{DeleteConst.LOG_TAG} 删除失败且已达重试上限: "
                f"path={job.path!r}, err={exc}"
            )

    def _clean_queue_dir(self, path: str) -> None:
        qdir = os.path.dirname(path)
        try:
            ospath.rmdir(qdir)
        except OSError:
            pass
