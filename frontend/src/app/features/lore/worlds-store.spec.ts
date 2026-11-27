import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { provideApi } from '../../core/api/providers';
import { BookFields, LoreBook } from './lore.model';
import { LORE_SEARCH_DELAY_MS, WorldsStore } from './worlds-store';

function world(id: string, overrides: Partial<LoreBook> = {}): LoreBook {
  return {
    id,
    kind: 'world',
    character_id: null,
    name: `Mondo ${id}`,
    description: '',
    scan_depth: 4,
    token_budget: null,
    status: 'active',
    entry_count: 0,
    created_at: '2026-11-27T10:00:00.000Z',
    updated_at: '2026-11-27T10:00:00.000Z',
    ...overrides,
  };
}

const FIELDS: BookFields = { name: 'Aldara', description: '', scan_depth: 4, token_budget: null };

const isList = (request: { url: string }): boolean => request.url === '/api/v1/lore/worlds';

describe('WorldsStore', () => {
  let store: WorldsStore;
  let backend: HttpTestingController;

  beforeEach(() => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({ providers: [provideApi(), provideHttpClientTesting()] });
    store = TestBed.inject(WorldsStore);
    backend = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    vi.useRealTimers();
    backend.verify();
  });

  it('asks for the tab it shows and for the search once typing has rested', async () => {
    const loaded = store.showTab('archived');
    const first = backend.expectOne(isList);
    expect(first.request.params.get('status')).toBe('archived');
    expect(first.request.params.has('q')).toBe(false);
    first.flush({ items: [world('a', { status: 'archived' })] });
    await loaded;
    expect(store.items().map((item) => item.id)).toEqual(['a']);
    expect(store.loading()).toBe(false);

    store.search('mare');
    store.search('mare e');
    backend.expectNone(isList);
    await vi.advanceTimersByTimeAsync(LORE_SEARCH_DELAY_MS);
    const searched = backend.expectOne(isList);
    expect(searched.request.params.get('q')).toBe('mare e');
    searched.flush({ items: [] });
  });

  it('creates a world with POST and reloads, or keeps the reason of a refusal', async () => {
    const created = store.create(FIELDS);
    const post = backend.expectOne('/api/v1/lore/worlds');
    expect(post.request.method).toBe('POST');
    expect(post.request.body).toEqual(FIELDS);
    post.flush(world('a'));
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isList).flush({ items: [world('a')] });
    expect((await created)?.id).toBe('a');

    const refused = store.create({ ...FIELDS, name: '' });
    backend
      .expectOne('/api/v1/lore/worlds')
      .flush({ code: 'VALIDATION_ERROR', detail: 'Il nome non può essere vuoto.' }, { status: 422, statusText: 'Unprocessable Entity' });
    expect(await refused).toBeNull();
    expect(store.actionError()?.detail).toBe('Il nome non può essere vuoto.');
  });

  it('archives, restores and deletes through their routes', async () => {
    const archive = store.toggleArchive(world('a'));
    backend.expectOne('/api/v1/lore/books/a/archive').flush(world('a', { status: 'archived' }));
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isList).flush({ items: [] });
    expect(await archive).toBe(true);

    const restore = store.toggleArchive(world('a', { status: 'archived' }));
    backend.expectOne('/api/v1/lore/books/a/unarchive').flush(world('a'));
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isList).flush({ items: [] });
    expect(await restore).toBe(true);

    const removed = store.remove(world('a'));
    const request = backend.expectOne('/api/v1/lore/books/a');
    expect(request.request.method).toBe('DELETE');
    request.flush(null, { status: 204, statusText: 'No Content' });
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isList).flush({ items: [] });
    expect(await removed).toBe(true);
  });
});
