import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { provideApi } from '../../core/api/providers';
import { Persona, PersonaFields } from './persona.model';
import { PersonasStore } from './personas-store';

function persona(id: string, overrides: Partial<Persona> = {}): Persona {
  return {
    id,
    name: `Persona ${id}`,
    grammatical_gender: 'non_indicato',
    age: null,
    appearance: '',
    about: '',
    is_default: false,
    status: 'active',
    conversation_count: 0,
    created_at: '2026-11-27T10:00:00.000Z',
    updated_at: '2026-11-27T10:00:00.000Z',
    ...overrides,
  };
}

const FIELDS: PersonaFields = { name: 'Giulio', grammatical_gender: 'maschile', age: 34, appearance: '', about: '' };

const isList = (request: { url: string }): boolean => request.url === '/api/v1/personas';

describe('PersonasStore', () => {
  let store: PersonasStore;
  let backend: HttpTestingController;

  beforeEach(() => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({ providers: [provideApi(), provideHttpClientTesting()] });
    store = TestBed.inject(PersonasStore);
    backend = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    vi.useRealTimers();
    backend.verify();
  });

  it('asks for the tab it shows', async () => {
    const loaded = store.showTab('archived');
    const request = backend.expectOne(isList);
    expect(request.request.params.get('status')).toBe('archived');
    request.flush({ items: [persona('a', { status: 'archived' })] });
    await loaded;
    expect(store.items().map((item) => item.id)).toEqual(['a']);
    expect(store.loading()).toBe(false);
  });

  it('creates with POST, replaces with PUT, and reloads the list after each', async () => {
    const created = store.save(null, FIELDS);
    const post = backend.expectOne('/api/v1/personas');
    expect(post.request.method).toBe('POST');
    expect(post.request.body).toEqual(FIELDS);
    post.flush(persona('a'));
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isList).flush({ items: [persona('a')] });
    expect((await created)?.id).toBe('a');

    const replaced = store.save('a', FIELDS);
    const put = backend.expectOne('/api/v1/personas/a');
    expect(put.request.method).toBe('PUT');
    put.flush(persona('a', { name: FIELDS.name }));
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isList).flush({ items: [] });
    expect((await replaced)?.name).toBe('Giulio');
  });

  it('keeps the reason of a refused save and changes nothing', async () => {
    const refused = store.save(null, FIELDS);
    backend
      .expectOne('/api/v1/personas')
      .flush(
        { code: 'CONTENT_POLICY_BLOCKED', detail: 'Il campo «di te» descrive un minore.' },
        { status: 422, statusText: 'Unprocessable Entity' },
      );
    expect(await refused).toBeNull();
    expect(store.actionError()?.detail).toBe('Il campo «di te» descrive un minore.');
  });

  it('sets the default, archives, restores and deletes through their routes', async () => {
    const makeDefault = store.makeDefault(persona('a'));
    backend.expectOne('/api/v1/personas/a/default').flush(persona('a', { is_default: true }));
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isList).flush({ items: [] });
    expect(await makeDefault).toBe(true);

    const archive = store.toggleArchive(persona('a'));
    backend.expectOne('/api/v1/personas/a/archive').flush(persona('a', { status: 'archived' }));
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isList).flush({ items: [] });
    expect(await archive).toBe(true);

    const restore = store.toggleArchive(persona('a', { status: 'archived' }));
    backend.expectOne('/api/v1/personas/a/unarchive').flush(persona('a'));
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isList).flush({ items: [] });
    expect(await restore).toBe(true);

    const removed = store.remove(persona('a'));
    const request = backend.expectOne('/api/v1/personas/a');
    expect(request.request.method).toBe('DELETE');
    request.flush(null, { status: 204, statusText: 'No Content' });
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isList).flush({ items: [] });
    expect(await removed).toBe(true);
  });

  it('lists only the active personas for the picker', async () => {
    const listed = store.listActive();
    const request = backend.expectOne(isList);
    expect(request.request.params.get('status')).toBe('active');
    request.flush({ items: [persona('a', { is_default: true })] });
    expect((await listed).map((item) => item.id)).toEqual(['a']);
  });
});
