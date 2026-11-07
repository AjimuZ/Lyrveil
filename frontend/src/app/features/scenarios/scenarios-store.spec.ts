import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { provideApi } from '../../core/api/providers';
import { Scenario, ScenarioFields } from './scenario.model';
import { SCENARIO_SEARCH_DELAY_MS, ScenariosStore } from './scenarios-store';

function scenario(id: string, overrides: Partial<Scenario> = {}): Scenario {
  return {
    id,
    title: `Scenario ${id}`,
    description: '',
    setting: '',
    opening_message: '',
    character_id: null,
    language: null,
    status: 'active',
    created_at: '2026-11-07T10:00:00.000Z',
    updated_at: '2026-11-07T10:00:00.000Z',
    ...overrides,
  };
}

const FIELDS: ScenarioFields = {
  title: 'Sera al faro',
  description: '',
  setting: '',
  opening_message: 'Ciao',
  character_id: null,
  language: null,
};

const isList = (request: { url: string }): boolean => request.url === '/api/v1/scenarios';

describe('ScenariosStore', () => {
  let store: ScenariosStore;
  let backend: HttpTestingController;

  beforeEach(() => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({ providers: [provideApi(), provideHttpClientTesting()] });
    store = TestBed.inject(ScenariosStore);
    backend = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    vi.useRealTimers();
    backend.verify();
  });

  it('asks for the tab and leaves a blank search out', async () => {
    const loaded = store.showTab('archived');
    const request = backend.expectOne(isList);
    expect(request.request.params.get('status')).toBe('archived');
    expect(request.request.params.has('q')).toBe(false);
    request.flush({ items: [scenario('a', { status: 'archived' })] });
    await loaded;
    expect(store.items().map((item) => item.id)).toEqual(['a']);
    expect(store.loading()).toBe(false);
  });

  it('requests a search once typing rests', async () => {
    store.search('fa');
    store.search('faro');
    await vi.advanceTimersByTimeAsync(SCENARIO_SEARCH_DELAY_MS - 1);
    backend.expectNone(isList);
    await vi.advanceTimersByTimeAsync(1);
    const request = backend.expectOne(isList);
    expect(request.request.params.get('q')).toBe('faro');
    request.flush({ items: [] });
  });

  it('creates with POST, replaces with PUT, and reloads the list after each', async () => {
    const created = store.save(null, FIELDS);
    const post = backend.expectOne('/api/v1/scenarios');
    expect(post.request.method).toBe('POST');
    expect(post.request.body).toEqual(FIELDS);
    post.flush(scenario('a'));
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isList).flush({ items: [scenario('a')] });
    expect((await created)?.id).toBe('a');

    const replaced = store.save('a', FIELDS);
    const put = backend.expectOne('/api/v1/scenarios/a');
    expect(put.request.method).toBe('PUT');
    put.flush(scenario('a', { title: FIELDS.title }));
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isList).flush({ items: [] });
    expect((await replaced)?.title).toBe('Sera al faro');
  });

  it('keeps the reason of a refused save and changes nothing', async () => {
    const refused = store.save(null, FIELDS);
    backend
      .expectOne('/api/v1/scenarios')
      .flush(
        { code: 'CONTENT_POLICY_BLOCKED', detail: 'Il campo «descrizione» descrive un minore.' },
        { status: 422, statusText: 'Unprocessable Entity' },
      );
    expect(await refused).toBeNull();
    expect(store.actionError()?.detail).toBe('Il campo «descrizione» descrive un minore.');
  });

  it('archives, restores and deletes through their routes', async () => {
    const archive = store.toggleArchive(scenario('a'));
    backend.expectOne('/api/v1/scenarios/a/archive').flush(scenario('a', { status: 'archived' }));
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isList).flush({ items: [] });
    expect(await archive).toBe(true);

    const restore = store.toggleArchive(scenario('a', { status: 'archived' }));
    backend.expectOne('/api/v1/scenarios/a/unarchive').flush(scenario('a'));
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isList).flush({ items: [] });
    expect(await restore).toBe(true);

    const removed = store.remove(scenario('a'));
    const request = backend.expectOne('/api/v1/scenarios/a');
    expect(request.request.method).toBe('DELETE');
    request.flush(null, { status: 204, statusText: 'No Content' });
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isList).flush({ items: [] });
    expect(await removed).toBe(true);
  });
});
