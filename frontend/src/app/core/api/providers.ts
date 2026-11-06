// Registers Angular's HttpClient with the interceptor that turns every failed request into an ApiError.
import { HttpInterceptorFn, provideHttpClient, withFetch, withInterceptors } from '@angular/common/http';
import { EnvironmentProviders, makeEnvironmentProviders } from '@angular/core';
import { catchError, throwError } from 'rxjs';

import { toApiError } from './api-error';

/** Rethrows any failure of a request as an `ApiError`, so callers handle one error type. */
export const apiErrorInterceptor: HttpInterceptorFn = (request, next) =>
  next(request).pipe(catchError((error: unknown) => throwError(() => toApiError(error))));

/** Providers of the HTTP layer: `HttpClient` on `fetch`, with the error interceptor. Used by `app.config.ts`. */
export function provideApi(): EnvironmentProviders {
  return makeEnvironmentProviders([provideHttpClient(withFetch(), withInterceptors([apiErrorInterceptor]))]);
}
