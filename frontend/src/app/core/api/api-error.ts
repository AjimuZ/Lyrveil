// Maps a failed API call to a typed error whose text comes from the Italian catalog.
import { HttpErrorResponse } from '@angular/common/http';

import type { TextKey } from '../i18n/translator';

/** The `code` values of docs/api.md (Errors) and the catalog text that explains each one to the user. */
const SERVER_ERROR_TEXT = {
  VALIDATION_ERROR: 'error.validation',
  NOT_FOUND: 'error.notFound',
  CONFLICT: 'error.conflict',
  PAYLOAD_TOO_LARGE: 'error.payloadTooLarge',
  UNSUPPORTED_MEDIA: 'error.unsupportedMedia',
  CONTENT_POLICY_BLOCKED: 'error.contentPolicyBlocked',
  IMPORT_INVALID: 'error.importInvalid',
  PROMPT_SYNTAX_REFUSED: 'error.promptSyntaxRefused',
  FORBIDDEN_ORIGIN: 'error.forbiddenOrigin',
  ORCHESTRATOR_UNAVAILABLE: 'error.orchestratorUnavailable',
  MODEL_PROFILE_UNAVAILABLE: 'error.modelProfileUnavailable',
  INTERNAL: 'error.internal',
} as const satisfies Record<string, TextKey>;

/** A code the backend sends in an `application/problem+json` body. */
export type ServerErrorCode = keyof typeof SERVER_ERROR_TEXT;

/** `NETWORK` is no answer at all, or a proxy error; `UNKNOWN` is anything else that is not a known server code. */
export type ApiErrorCode = ServerErrorCode | 'NETWORK' | 'UNKNOWN';

/** The catalog key of the message for every code. A test checks that each key has a text. */
export const API_ERROR_TEXT: Readonly<Record<ApiErrorCode, TextKey>> = {
  ...SERVER_ERROR_TEXT,
  NETWORK: 'error.network',
  UNKNOWN: 'error.unknown',
};

/** One invalid field of a `VALIDATION_ERROR`, as the backend reports it. */
export interface FieldError {
  readonly field: string;
  readonly code: string;
}

// What the backend answers for a status when the body carries no known code.
const CODE_BY_STATUS: Readonly<Record<number, ApiErrorCode>> = {
  404: 'NOT_FOUND',
  409: 'CONFLICT',
  413: 'PAYLOAD_TOO_LARGE',
  415: 'UNSUPPORTED_MEDIA',
  422: 'VALIDATION_ERROR',
  // The proxy answers these when the backend is down or restarting, with no problem+json body of ours.
  502: 'NETWORK',
  503: 'NETWORK',
  504: 'NETWORK',
};

/**
 * A failed API call. `textKey` names the catalog message to show (`Translator.t(error.textKey)`), `detail` is the
 * server's own explanation when it sent one (for example the download command of `MODEL_PROFILE_UNAVAILABLE`), and
 * `status` is 0 when no response arrived. The `message` never holds server text, so logging it leaks nothing.
 */
export class ApiError extends Error {
  readonly code: ApiErrorCode;
  readonly status: number;
  readonly detail: string | null;
  readonly fieldErrors: readonly FieldError[];

  constructor(code: ApiErrorCode, status: number, detail: string | null = null, fieldErrors: readonly FieldError[] = []) {
    super(`${code} (HTTP ${status})`);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.detail = detail;
    this.fieldErrors = fieldErrors;
  }

  get textKey(): TextKey {
    return API_ERROR_TEXT[this.code];
  }
}

function isServerCode(value: unknown): value is ServerErrorCode {
  return typeof value === 'string' && Object.hasOwn(SERVER_ERROR_TEXT, value);
}

function readFieldErrors(value: unknown): FieldError[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const found: FieldError[] = [];
  for (const item of value as unknown[]) {
    if (typeof item === 'object' && item !== null) {
      const { field, code } = item as { field?: unknown; code?: unknown };
      if (typeof field === 'string' && typeof code === 'string') {
        found.push({ field, code });
      }
    }
  }
  return found;
}

/**
 * Returns the typed error for anything a request can throw. An `ApiError` is returned as it is. For an
 * `HttpErrorResponse` the body's `code` wins when it is a known one; otherwise the status decides: 0 and the proxy's
 * 502, 503 and 504 are `NETWORK`, the statuses of docs/api.md map to their code, any other 5xx is `INTERNAL`, and the
 * rest is `UNKNOWN`. Anything else thrown is `UNKNOWN` with status 0.
 */
export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error;
  }
  if (!(error instanceof HttpErrorResponse)) {
    return new ApiError('UNKNOWN', 0);
  }
  const body: unknown = error.error;
  const problem = typeof body === 'object' && body !== null ? (body as Record<string, unknown>) : {};
  const detail = typeof problem['detail'] === 'string' ? problem['detail'] : null;
  const fieldErrors = readFieldErrors(problem['errors']);
  const serverCode = problem['code'];
  if (isServerCode(serverCode)) {
    return new ApiError(serverCode, error.status, detail, fieldErrors);
  }
  if (error.status === 0) {
    return new ApiError('NETWORK', 0);
  }
  const code = CODE_BY_STATUS[error.status] ?? (error.status >= 500 ? 'INTERNAL' : 'UNKNOWN');
  return new ApiError(code, error.status, detail, fieldErrors);
}
