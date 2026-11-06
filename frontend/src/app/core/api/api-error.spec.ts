import { HttpErrorResponse } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';

import { Translator } from '../i18n/translator';
import { API_ERROR_TEXT, ApiError, ApiErrorCode, toApiError } from './api-error';

const problem = (status: number, body: unknown) => new HttpErrorResponse({ status, error: body });

describe('toApiError', () => {
  let translator: Translator;

  beforeEach(() => {
    translator = TestBed.inject(Translator);
  });

  it('has an Italian message in the catalog for every code', () => {
    for (const [code, key] of Object.entries(API_ERROR_TEXT)) {
      const text = translator.t(key);
      expect(text, code).not.toBe(key);
      expect(text.trim(), code).not.toBe('');
    }
  });

  it('maps the code of a problem+json body to its code and Italian message', () => {
    const error = toApiError(
      problem(422, {
        type: 'https://lyrveil.invalid/errors/validation-error',
        title: 'Dati non validi',
        status: 422,
        code: 'VALIDATION_ERROR',
        detail: "L'età deve essere almeno 18.",
        errors: [{ field: 'age', code: 'too_small' }, { field: 7 }, 'x'],
      }),
    );
    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe('VALIDATION_ERROR');
    expect(error.status).toBe(422);
    expect(error.detail).toBe("L'età deve essere almeno 18.");
    expect(error.fieldErrors).toEqual([{ field: 'age', code: 'too_small' }]);
    expect(translator.t(error.textKey)).toBe('I dati inseriti non sono validi.');
  });

  it('keeps the code of the body over the status', () => {
    const error = toApiError(problem(503, { code: 'ORCHESTRATOR_UNAVAILABLE', detail: 'x' }));
    expect(error.code).toBe('ORCHESTRATOR_UNAVAILABLE');
    expect(translator.t(error.textKey)).toBe('Il gestore delle risorse AI non risponde.');
  });

  it('falls back to the status when the body has no known code', () => {
    const cases: [number, unknown, ApiErrorCode][] = [
      [404, null, 'NOT_FOUND'],
      [409, '<html>', 'CONFLICT'],
      [413, {}, 'PAYLOAD_TOO_LARGE'],
      [415, { code: 'SURPRISE' }, 'UNSUPPORTED_MEDIA'],
      [422, undefined, 'VALIDATION_ERROR'],
      [502, '<html>', 'NETWORK'],
      [503, null, 'NETWORK'],
      [504, null, 'NETWORK'],
      [500, null, 'INTERNAL'],
      [418, null, 'UNKNOWN'],
      [0, new ProgressEvent('error'), 'NETWORK'],
    ];
    for (const [status, body, code] of cases) {
      expect(toApiError(problem(status, body)).code, `${status}`).toBe(code);
    }
  });

  it('says that Lyrveil does not answer when no response arrived', () => {
    const error = toApiError(problem(0, new ProgressEvent('error')));
    expect(error.status).toBe(0);
    expect(translator.t(error.textKey)).toBe('Lyrveil non risponde. Controlla che sia avviato e riprova.');
  });

  it('returns an ApiError unchanged and wraps anything else as UNKNOWN without server text', () => {
    const original = new ApiError('CONFLICT', 409);
    expect(toApiError(original)).toBe(original);
    const other = toApiError(new TypeError('secret text'));
    expect(other.code).toBe('UNKNOWN');
    expect(other.message).toBe('UNKNOWN (HTTP 0)');
    expect(translator.t(other.textKey)).toBe('Si è verificato un errore imprevisto.');
  });
});
