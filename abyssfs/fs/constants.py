class DeleteConst:
    QUEUE_DIR = ".abyssfs_delete_queue"
    JOB_PREFIX = "job"
    FILE_SUFFIX = ".file"
    DIR_SUFFIX = ".dir"
    WORKER_NAME = "AbyssFSDeleteWorker"
    LOG_TAG = "[DeleteQueue]"


class DeleteConfig:
    RETRIES = 5
    RETRY_DELAY_SEC = 1.0
    ID_BYTES = 8
