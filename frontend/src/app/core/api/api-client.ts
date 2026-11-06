// Typed HTTP access to the REST API under /api/v1, the only place that builds request URLs.
import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

/** The base path of every REST route (docs/api.md). nginx proxies it to the backend. */
export const API_BASE_PATH = '/api/v1';

/** Query values; `null` and `undefined` are left out, so an unset filter does not reach the URL. */
export type ApiQuery = Readonly<Record<string, string | number | boolean | null | undefined>>;

function toParams(query: ApiQuery | undefined): HttpParams {
  let params = new HttpParams();
  for (const [name, value] of Object.entries(query ?? {})) {
    if (value !== null && value !== undefined) {
      params = params.set(name, String(value));
    }
  }
  return params;
}

/**
 * Thin wrapper over `HttpClient` for the REST API. `path` starts with `/` and is relative to `API_BASE_PATH`. The
 * caller names the response type, normally one of `core/api/generated`. Failures arrive as `ApiError` through the
 * interceptor of `provideApi`. Bodies are JSON; the client sets `Content-Type: application/json` itself.
 */
@Injectable({ providedIn: 'root' })
export class ApiClient {
  private readonly http = inject(HttpClient);

  get<T>(path: string, query?: ApiQuery): Observable<T> {
    return this.http.get<T>(API_BASE_PATH + path, { params: toParams(query) });
  }

  post<T>(path: string, body?: unknown): Observable<T> {
    return this.http.post<T>(API_BASE_PATH + path, body ?? null);
  }

  put<T>(path: string, body: unknown): Observable<T> {
    return this.http.put<T>(API_BASE_PATH + path, body);
  }

  patch<T>(path: string, body: unknown): Observable<T> {
    return this.http.patch<T>(API_BASE_PATH + path, body);
  }

  delete<T>(path: string): Observable<T> {
    return this.http.delete<T>(API_BASE_PATH + path);
  }
}
