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


class ContentPolicyBlockedError(DomainError):
    """A content safeguard of ADR 0018 refused the data (422 `CONTENT_POLICY_BLOCKED`), so nothing was written or
    queued. `detail` names the refused field, never the refused text. Deliberately not a ValidationFailedError: a
    caller that maps validation errors to its own code, as the import does with IMPORT_INVALID, keeps this one."""

    code = "CONTENT_POLICY_BLOCKED"
    status = 422


class ImportInvalidError(DomainError):
    """A character file is not valid or not of a supported version (422 `IMPORT_INVALID`). Nothing is imported."""

    code = "IMPORT_INVALID"
    status = 422


class PayloadTooLargeError(DomainError):
    """A message or an upload is over its size limit, in bytes or in pixels (413 `PAYLOAD_TOO_LARGE`)."""

    code = "PAYLOAD_TOO_LARGE"
    status = 413


class UnsupportedMediaError(DomainError):
    """An upload is not a PNG, JPEG or WebP image that decodes, or its declared type disagrees with its bytes (415
    `UNSUPPORTED_MEDIA`)."""

    code = "UNSUPPORTED_MEDIA"
    status = 415
