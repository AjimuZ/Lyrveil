import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { provideApi } from '../../core/api/providers';
import { MemoryDetail, MemoryItem } from './memory.model';
import { MEMORY_SEARCH_DELAY_MS, MemoryStudioStore } from './memory-store';

function memory(id: string, overrides: Partial<MemoryItem> = {}): MemoryItem {
  return {
    id,
    character_id: 'c1',
    scope: 'conversation',
    conversation_id: 'chat1',
    kind: 'user_fact',
    content: `Ricordo ${id}`,
    subject_key: null,
    value_text: null,
    provenance: 'stated',
    durability: 'lasting',
    status: 'active',
    importance: 3,
    confidence: 0.95,
    locked: false,
    needs_review: false,
    expires_at: null,
    conflict_group_id: null,
    version: 1,
    updated_at: '2026-11-07T10:00:00.000Z',
    source_count: 1,
    ...overrides,
  };
}

const isMemories = (request: { url: string }): boolean => request.url === '/api/v1/memories';
const isChats = (request: { url: string }): boolean => request.url === '/api/v1/conversations';

describe('MemoryStudioStore', () => {
  let store: MemoryStudioStore;
  let backend: HttpTestingController;

  beforeEach(() => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({ providers: [provideApi(), provideHttpClientTesting()] });
    store = TestBed.inject(MemoryStudioStore);
    backend = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    vi.useRealTimers();
    backend.verify();
  });

  /** Opens the screen for character c1 and answers the chat list and the first page of memories. */
  async function open(items: readonly MemoryItem[], conversation: string | null = null): Promise<TestRequest> {
    const opened = store.open('c1', conversation);
    backend.expectOne(isChats).flush({ items: [] });
    const first = backend.expectOne(isMemories);
    first.flush({ items, next_cursor: null });
    await opened;
    return first;
  }

  it('asks for the memories in use of the character and of the chosen chat', async () => {
    const request = await open([memory('a')], 'chat1');

    expect(request.request.params.get('character_id')).toBe('c1');
    expect(request.request.params.get('conversation_id')).toBe('chat1');
    expect(request.request.params.getAll('status')).toEqual(['active', 'uncertain']);
    expect(request.request.params.has('q')).toBe(false);
    expect(store.memories().data.map((item) => item.id)).toEqual(['a']);
    expect(store.memories().loading).toBe(false);
  });

  it('sends each filter and a search once typing rests', async () => {
    await open([]);

    const filtered = store.setFilters({ status: 'superseded', kind: 'promise', provenance: 'inferred', lock: 'locked' });
    const request = backend.expectOne(isMemories);
    expect(request.request.params.getAll('status')).toEqual(['superseded']);
    expect(request.request.params.get('kind')).toBe('promise');
    expect(request.request.params.get('provenance')).toBe('inferred');
    expect(request.request.params.get('locked')).toBe('true');
    request.flush({ items: [], next_cursor: null });
    await filtered;

    store.search('gat');
    store.search('gatto');
    await vi.advanceTimersByTimeAsync(MEMORY_SEARCH_DELAY_MS - 1);
    backend.expectNone(isMemories);
    await vi.advanceTimersByTimeAsync(1);
    const searched = backend.expectOne(isMemories);
    expect(searched.request.params.get('q')).toBe('gatto');
    searched.flush({ items: [], next_cursor: null });
    backend.expectOne((request) => request.url === '/api/v1/episodes').flush({ items: [] });
  });

  it('appends the next page with its cursor', async () => {
    const opened = store.open('c1', null);
    backend.expectOne(isChats).flush({ items: [] });
    backend.expectOne(isMemories).flush({ items: [memory('a')], next_cursor: 'cursor-1' });
    await opened;
    expect(store.hasMore()).toBe(true);

    const more = store.loadMore();
    const request = backend.expectOne(isMemories);
    expect(request.request.params.get('cursor')).toBe('cursor-1');
    request.flush({ items: [memory('b')], next_cursor: null });
    await more;

    expect(store.memories().data.map((item) => item.id)).toEqual(['a', 'b']);
    expect(store.hasMore()).toBe(false);
  });

  it('edits with PATCH, locks, confirms and reloads the list after each', async () => {
    await open([memory('a')]);
    const item = memory('a');

    const edited = store.edit(item, { content: 'Nuovo testo', importance: 5 });
    const patch = backend.expectOne('/api/v1/memories/a');
    expect(patch.request.method).toBe('PATCH');
    expect(patch.request.body).toEqual({ content: 'Nuovo testo', importance: 5 });
    patch.flush({ ...item, sources: [], conflict: [] });
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isMemories).flush({ items: [memory('a', { content: 'Nuovo testo' })], next_cursor: null });
    expect(await edited).toBe(true);
    expect(store.memories().data[0]?.content).toBe('Nuovo testo');

    for (const [work, path] of [
      [() => store.setLocked(item, true), '/api/v1/memories/a/lock'],
      [() => store.setLocked(item, false), '/api/v1/memories/a/unlock'],
      [() => store.confirm(item), '/api/v1/memories/a/confirm'],
    ] as const) {
      const done = work();
      const post = backend.expectOne(path);
      expect(post.request.method).toBe('POST');
      post.flush({ ...item, sources: [], conflict: [] });
      await vi.advanceTimersByTimeAsync(0);
      backend.expectOne(isMemories).flush({ items: [item], next_cursor: null });
      expect(await done).toBe(true);
    }
  });

  it('keeps the reason of a refused edit and changes nothing', async () => {
    await open([memory('a')]);

    const refused = store.edit(memory('a'), { content: 'Doppione' });
    backend
      .expectOne('/api/v1/memories/a')
      .flush(
        { code: 'CONFLICT', detail: 'Esiste già un ricordo con lo stesso contenuto.' },
        { status: 409, statusText: 'Conflict' },
      );

    expect(await refused).toBe(false);
    expect(store.actionError()?.detail).toBe('Esiste già un ricordo con lo stesso contenuto.');
    backend.expectNone(isMemories);
  });

  it('opens the sources and the history of a memory and refreshes them after a change', async () => {
    await open([memory('a')]);
    const detail: MemoryDetail = {
      ...memory('a'),
      sources: [{ message_id: 'm1', conversation_id: 'chat1', seq: 1, author: 'user', excerpt: 'Ciao', valid: true }],
      conflict: [],
    };

    const shown = store.openDetails('a');
    backend.expectOne('/api/v1/memories/a').flush(detail);
    backend.expectOne('/api/v1/memories/a/revisions').flush({ items: [] });
    await shown;
    expect(store.opened()['a']?.detail.sources[0]?.seq).toBe(1);

    const locked = store.setLocked(memory('a'), true);
    backend.expectOne('/api/v1/memories/a/lock').flush(detail);
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isMemories).flush({ items: [memory('a', { locked: true })], next_cursor: null });
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne('/api/v1/memories/a').flush({ ...detail, locked: true });
    backend.expectOne('/api/v1/memories/a/revisions').flush({ items: [] });
    await locked;
    expect(store.opened()['a']?.detail.locked).toBe(true);

    store.closeDetails('a');
    expect(store.opened()).toEqual({});
  });

  it('deletes with DELETE and reads the list again', async () => {
    await open([memory('a')]);

    const removed = store.remove(memory('a'));
    const request = backend.expectOne('/api/v1/memories/a');
    expect(request.request.method).toBe('DELETE');
    request.flush(null, { status: 204, statusText: 'No Content' });
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isMemories).flush({ items: [], next_cursor: null });

    expect(await removed).toBe(true);
    expect(store.memories().data).toEqual([]);
  });

  it('reads the relationship and its history of the chosen chat only, and nothing without one', async () => {
    await open([], 'chat1');

    const loaded = store.loadRelationship();
    backend.expectOne('/api/v1/conversations/chat1/relationship').flush({ written: false });
    backend.expectOne('/api/v1/conversations/chat1/relationship/history').flush({ items: [] });
    await loaded;
    expect(store.relationship().data?.written).toBe(false);

    const selected = store.selectConversation(null);
    backend.expectOne(isMemories).flush({ items: [], next_cursor: null });
    await selected;
    await store.loadRelationship();
    await store.loadSummaries();
    backend.expectNone((request) => request.url.includes('/relationship') || request.url.includes('/summaries'));
  });

  it('keeps the rows and sets the error when a read fails', async () => {
    await open([memory('a')]);

    const reload = store.reloadMemories();
    backend.expectOne(isMemories).flush({ code: 'INTERNAL' }, { status: 500, statusText: 'Server Error' });
    await reload;

    expect(store.memories().data.map((item) => item.id)).toEqual(['a']);
    expect(store.memories().error?.code).toBe('INTERNAL');
  });
});
