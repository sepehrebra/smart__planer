"""Safe application errors with no SQL, credentials or private request bodies."""


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def not_found():
    return AppError(404, "not_found", "این مورد پیدا نشد.")


def stale_version():
    return AppError(409, "stale_version", "این کار تغییر کرده است؛ اطلاعات تازه را دریافت کنید.")
