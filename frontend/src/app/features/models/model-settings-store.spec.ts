import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { provideApi } from '../../core/api/providers';
import { JOB_POLL_MS, ModelSettingsStore } from './model-settings-store';
import { ModelProfile, ModelSettings } from './model-settings.model';

function profile(id: string, overrides: Partial<ModelProfile> = {}): ModelProfile {
  return {
    id,
    display_name: `Profilo ${id}`,
    file: `${id}.gguf`,
    bytes: 1000,
    license: 'Apache-2.0',
    license_url: 'https://example.test/license',
    min_tier: 'reduced',
    custom: false,
    state: 'verified',
    download_command: null,
    template: 'gemma4',
    selectable: true,
    quality_evidence: null,
    ...overrides,
  };
}

function settings(active = 'a', overrides: Partial<ModelSettings> = {}): ModelSettings {
  return {
    tier: 'reduced',
    active: { text: active, image: 'img' },
    text: [profile('a'), profile('b')],
    image: [profile('img', { file: 'img.safetensors' })],
    local_files: [],
    launch: null,
    ...overrides,
  };
}

const isSettings = (request: { url: string }): boolean => request.url === '/api/v1/settings/models';
const isJob = (request: { url: string }): boolean => request.url === '/api/v1/jobs/j1';
const queued = (kind = 'maintenance') => ({ id: 'j1', kind, status: 'queued', queue_position: 1 });
const job = (status: string, error: Partial<{ code: string; detail: string }> = {}) => ({
  id: 'j1',
  status,
  error_code: error.code ?? null,
  error_detail: error.detail ?? null,
});

describe('ModelSettingsStore', () => {
  let store: ModelSettingsStore;
  let backend: HttpTestingController;

  beforeEach(() => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({ providers: [provideApi(), provideHttpClientTesting()] });
    store = TestBed.inject(ModelSettingsStore);
    backend = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    store.stopFollowing();
    vi.useRealTimers();
    backend.verify();
  });

  it('loads the profiles and keeps them when a later load fails', async () => {
    const first = store.load();
    backend.expectOne(isSettings).flush(settings());
    await first;
    expect(store.settings()?.text.map((item) => item.id)).toEqual(['a', 'b']);
    expect(store.loading()).toBe(false);

    const second = store.load();
    backend.expectOne(isSettings).flush({ code: 'INTERNAL' }, { status: 500, statusText: 'Server Error' });
    await second;
    expect(store.error()?.code).toBe('INTERNAL');
    expect(store.settings()?.text).toHaveLength(2);
  });

  it('patches the chosen text profile, shows the new settings and offers Applica ora', async () => {
    const chosen = store.select('b');
    const request = backend.expectOne(isSettings);
    expect(request.request.method).toBe('PATCH');
    expect(request.request.body).toEqual({ llm_profile_id: 'b' });
    request.flush(settings('b'));

    expect(await chosen).toBe(true);
    expect(store.settings()?.active.text).toBe('b');
    expect(store.changed()).toBe(true);
  });

  it('shows the refusal of a profile and changes nothing', async () => {
    const chosen = store.select('b');
    backend
      .expectOne(isSettings)
      .flush({ code: 'MODEL_PROFILE_UNAVAILABLE', detail: 'Manca il file.' }, { status: 409, statusText: 'Conflict' });

    expect(await chosen).toBe(false);
    expect(store.actionError()?.code).toBe('MODEL_PROFILE_UNAVAILABLE');
    expect(store.actionError()?.detail).toBe('Manca il file.');
    expect(store.changed()).toBe(false);
  });

  it('applies now: queues the reload and follows its job until it completes', async () => {
    const applied = store.applyNow();
    const request = backend.expectOne((item) => item.url === '/api/v1/settings/models/apply');
    expect(request.request.method).toBe('POST');
    request.flush(queued());
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isJob).flush(job('queued'));
    await vi.advanceTimersByTimeAsync(0);
    expect(store.reload()?.status).toBe('queued');

    await vi.advanceTimersByTimeAsync(JOB_POLL_MS);
    backend.expectOne(isJob).flush(job('running'));
    await vi.advanceTimersByTimeAsync(0);
    expect(store.reload()?.status).toBe('running');

    await vi.advanceTimersByTimeAsync(JOB_POLL_MS);
    backend.expectOne(isJob).flush(job('completed'));
    expect((await applied)?.status).toBe('completed');
    expect(store.changed()).toBe(false);
    backend.expectNone(isJob);
  });

  it('reports a failed reload with the server words and keeps Applica ora offered', async () => {
    const chosen = store.select('b');
    backend.expectOne(isSettings).flush(settings('b'));
    await chosen;

    const applied = store.applyNow();
    backend.expectOne((item) => item.url === '/api/v1/settings/models/apply').flush(queued());
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isJob).flush(job('failed', { code: 'MODEL_FILE_MISSING', detail: 'Il file è sparito.' }));

    const ended = await applied;
    expect(ended?.status).toBe('failed');
    expect(ended?.errorDetail).toBe('Il file è sparito.');
    expect(store.changed()).toBe(true);
  });

  it('registers a custom file, follows the hash job and reloads the profiles when it completes', async () => {
    const registered = store.register('text', 'mio.gguf', 'chatml');
    const request = backend.expectOne((item) => item.url === '/api/v1/settings/models/custom');
    expect(request.request.body).toEqual({ kind: 'text', file: 'mio.gguf', template: 'chatml' });
    request.flush(queued(), { status: 202, statusText: 'Accepted' });
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isJob).flush(job('completed'));
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isSettings).flush(settings('a', { text: [profile('a'), profile('custom-0123456789ab', { custom: true })] }));

    expect((await registered)?.status).toBe('completed');
    expect(store.settings()?.text.some((item) => item.custom)).toBe(true);
  });

  it('sends no template for an image file', async () => {
    const registered = store.register('image', 'mio.safetensors', null);
    const request = backend.expectOne((item) => item.url === '/api/v1/settings/models/custom');
    expect(request.request.body).toEqual({ kind: 'image', file: 'mio.safetensors' });
    request.flush({ code: 'VALIDATION_ERROR', detail: 'Nome non valido.' }, { status: 422, statusText: 'Unprocessable' });

    expect(await registered).toBeNull();
    expect(store.actionError()?.code).toBe('VALIDATION_ERROR');
  });

  it('stops polling when the screen closes', async () => {
    const applied = store.applyNow();
    backend.expectOne((item) => item.url === '/api/v1/settings/models/apply').flush(queued());
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isJob).flush(job('running'));
    await vi.advanceTimersByTimeAsync(0);

    store.stopFollowing();
    await vi.advanceTimersByTimeAsync(JOB_POLL_MS * 3);

    expect((await applied)?.status).toBe('running');
    backend.expectNone(isJob);
  });
});
