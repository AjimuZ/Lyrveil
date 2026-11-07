"""Errors that services raise for a request they refuse; the API turns each into its problem+json answer."""


class DomainError(Exception):
    """A refused request. `code` is the error code of docs/api.md (Errors) and `detail` the Italian text shown to the
    user. It never holds private text from the database."""

    code = "INTERNAL"
    status = 500

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFoundError(DomainError):
    """The resource does not exist, or is hidden from the caller (404 `NOT_FOUND`)."""

    code = "NOT_FOUND"
    status = 404


class ConflictError(DomainError):
    """The state of the resource does not allow the operation (409 `CONFLICT`)."""

    code = "CONFLICT"
    status = 409


class ValidationFailedError(DomainError):
    """A value breaks a rule that only the service can check (422 `VALIDATION_ERROR`)."""

    code = "VALIDATION_ERROR"
    status = 422


class PayloadTooLargeError(DomainError):
    """A message is over the size limit (413 `PAYLOAD_TOO_LARGE`)."""

    code = "PAYLOAD_TOO_LARGE"
    status = 413
