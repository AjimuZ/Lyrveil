import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { ApiClient } from './api-client';
import { ApiError } from './api-error';
import type { LiveReport, ReadyReport } from './generated';
import { provideApi } from './providers';

describe('ApiClient with provideApi', () => {
  let client: ApiClient;
  let backend: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideApi(), provideHttpClientTesting()] });
    client = TestBed.inject(ApiClient);
    backend = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    backend.verify();
  });

  it('reads a typed response under the base path and leaves unset query values out', () => {
    let report: LiveReport | undefined;
    client.get<LiveReport>('/health/live', { cursor: null, limit: 50, q: undefined, locked: false }).subscribe((value) => (report = value));
    const request = backend.expectOne((candidate) => candidate.url === '/api/v1/health/live');
    expect(request.request.method).toBe('GET');
    expect(request.request.params.keys()).toEqual(['limit', 'locked']);
    expect(request.request.params.get('limit')).toBe('50');
    request.flush({ status: 'alive' });
    expect(report).toEqual({ status: 'alive' });
  });

  it('sends JSON bodies with the write methods', () => {
    client.post<unknown>('/characters', { name: 'Ada' }).subscribe();
    const created = backend.expectOne('/api/v1/characters');
    expect(created.request.method).toBe('POST');
    expect(created.request.body).toEqual({ name: 'Ada' });
    created.flush({});

    client.patch<unknown>('/settings', { a: 1 }).subscribe();
    expect(backend.expectOne('/api/v1/settings').request.method).toBe('PATCH');
    client.put<unknown>('/scenarios/1', { a: 1 }).subscribe();
    expect(backend.expectOne('/api/v1/scenarios/1').request.method).toBe('PUT');
    client.delete<unknown>('/images/1').subscribe();
    expect(backend.expectOne('/api/v1/images/1').request.method).toBe('DELETE');
  });

  it('fails with an ApiError that holds the code of the problem body', () => {
    let failure: unknown;
    client.get<ReadyReport>('/missing').subscribe({ error: (error: unknown) => (failure = error) });
    backend
      .expectOne('/api/v1/missing')
      .flush({ code: 'NOT_FOUND', title: 'Non trovato', status: 404 }, { status: 404, statusText: 'Not Found' });
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).code).toBe('NOT_FOUND');
    expect((failure as ApiError).textKey).toBe('error.notFound');
  });

  it('fails with NETWORK when the connection breaks', () => {
    let failure: unknown;
    client.get<LiveReport>('/health/live').subscribe({ error: (error: unknown) => (failure = error) });
    backend.expectOne((candidate) => candidate.url === '/api/v1/health/live').error(new ProgressEvent('error'));
    expect((failure as ApiError).code).toBe('NETWORK');
  });
});
