import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { provideApi } from '../../core/api/providers';
import { ChatsStore, SEARCH_DELAY_MS } from './chats-store';
import { Conversation } from './chats.model';

function chat(id: string, overrides: Partial<Conversation> = {}): Conversation {
  return {
    id,
    character_id: 'c1',
    character_name: 'Alba',
    title: `Chat ${id}`,
    language: null,
    effective_language: 'it',
    status: 'active',
    pinned: false,
    pinned_at: null,
    shares_memory: false,
    unread_count: 0,
    last_message_at: null,
    created_at: '2026-11-07T10:00:00.000Z',
    updated_at: '2026-11-07T10:00:00.000Z',
    ...overrides,
  };
}

const isList = (request: { url: string }): boolean => request.url === '/api/v1/conversations';

describe('ChatsStore', () => {
  let store: ChatsStore;
  let backend: HttpTestingController;

  beforeEach(() => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({ providers: [provideApi(), provideHttpClientTesting()] });
    store = TestBed.inject(ChatsStore);
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
    request.flush({ items: [chat('a', { status: 'archived' })] });
    await loaded;
    expect(store.items().map((item) => item.id)).toEqual(['a']);
    expect(store.loading()).toBe(false);
  });

  it('requests a search once typing rests and drops an answer that a newer request overtook', async () => {
    store.search('al');
    store.search('alba');
    await vi.advanceTimersByTimeAsync(SEARCH_DELAY_MS - 1);
    backend.expectNone(isList);
    await vi.advanceTimersByTimeAsync(1);
    const first = backend.expectOne(isList);
    expect(first.request.params.get('q')).toBe('alba');

    const second = store.reload();
    const newer = backend.expectOne(isList);
    newer.flush({ items: [chat('new')] });
    await second;
    first.flush({ items: [chat('old')] });
    await vi.advanceTimersByTimeAsync(0);
    expect(store.items().map((item) => item.id)).toEqual(['new']);
  });

  it('deletes a chat, offers its restore and brings it back', async () => {
    const target = chat('a');
    const deleting = store.remove(target);
    backend.expectOne('/api/v1/conversations/a').flush(null, { status: 204, statusText: 'No Content' });
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isList).flush({ items: [] });
    await deleting;
    expect(store.justDeleted()).toEqual(target);

    const restoring = store.restoreDeleted();
    backend.expectOne('/api/v1/conversations/a/undelete').flush(chat('a'));
    await vi.advanceTimersByTimeAsync(0);
    backend.expectOne(isList).flush({ items: [chat('a')] });
    await restoring;
    expect(store.justDeleted()).toBeNull();
    expect(store.items().map((item) => item.id)).toEqual(['a']);
  });

  it('reports a refused action and keeps the rows', async () => {
    const pinning = store.togglePin(chat('a'));
    const request = backend.expectOne('/api/v1/conversations/a');
    expect(request.request.body).toEqual({ pinned: true });
    request.flush({ code: 'NOT_FOUND', detail: 'Conversazione non trovata.' }, { status: 404, statusText: 'Not Found' });
    expect(await pinning).toBe(false);
    expect(store.actionError()?.code).toBe('NOT_FOUND');
  });
});
