class IncusError(Exception):
    """Error returned by the Incus API (or raised by the client). Go: the plain ``error`` return value."""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.message = message
        self.status_code = status_code

    @property
    def is_not_found(self) -> bool:
        return self.status_code == 404

    @staticmethod
    def check(err: BaseException, code: int) -> bool:
        """Go: api.StatusErrorCheck."""
        return isinstance(err, IncusError) and err.status_code == code
