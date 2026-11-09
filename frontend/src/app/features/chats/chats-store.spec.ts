import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { provideApi } from '../../core/api/providers';
import { EVENTS_SOCKET_FACTORY, EVENTS_SOCKET_URL, SocketLike } from '../../core/realtime/events-socket';
import { ChatsStore, LIVE_DELAY_MS, SEARCH_DELAY_MS } from './chats-store';
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

class FakeSocket implements SocketLike {
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  send(): void {
    // The server side is not simulated.
  }

  close(): void {
    // Nothing to release.
  }

  open(): void {
    this.onopen?.(new Event('open'));
    this.receive({ v: 1, type: 'connected', boot_id: 'boot' });
  }

  receive(frame: object): void {
    this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent);
  }
}

let nextEventId = 0;
const event = (type: string, data: object) => ({ v: 1, id: ++nextEventId, type, ts: '2026-11-07T10:00:00Z', data });

describe('ChatsStore', () => {
  let store: ChatsStore;
  let backend: HttpTestingController;
  let sockets: FakeSocket[];

  beforeEach(() => {
    vi.useFakeTimers();
    sockets = [];
    TestBed.configureTestingModule({
      providers: [
        provideApi(),
        provideHttpClientTesting(),
        { provide: EVENTS_SOCKET_URL, useValue: 'ws://lyrveil.test/ws' },
        {
          provide: EVENTS_SOCKET_FACTORY,
          useValue: () => {
            const created = new FakeSocket();
            sockets.push(created);
            return created;
          },
        },
      ],
    });
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

  it('reloads the rows once after a burst of message events while it follows, and stops after unfollow', async () => {
    store.follow();
    const socket = sockets[0] as FakeSocket;
    socket.open();
    socket.receive(event('message.created', { message: {} }));
    socket.receive(event('typing.started', { conversation_id: 'a' }));
    socket.receive(event('message.completed', { message: {} }));
    await vi.advanceTimersByTimeAsync(LIVE_DELAY_MS - 1);
    backend.expectNone(isList);
    await vi.advanceTimersByTimeAsync(1);
    backend
      .expectOne(isList)
      .flush({ items: [chat('a', { unread_count: 2, activity: 'typing', last_message: { author: 'character', kind: 'text', text: 'Ciao' } })] });
    await vi.advanceTimersByTimeAsync(0);
    expect(store.items()[0]?.unread_count).toBe(2);

    store.unfollow();
    socket.receive(event('message.created', { message: {} }));
    await vi.advanceTimersByTimeAsync(LIVE_DELAY_MS * 2);
    backend.expectNone(isList);
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
