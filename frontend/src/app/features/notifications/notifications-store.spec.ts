import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { provideApi } from '../../core/api/providers';
import { EVENTS_SOCKET_FACTORY, EVENTS_SOCKET_URL, SocketLike } from '../../core/realtime/events-socket';
import { AppNotification } from './notification.model';
import { NotificationsStore, TOAST_MS } from './notifications-store';

class FakeSocket implements SocketLike {
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  send(): void {
    // The server side is not simulated: the tests only look at what the store does with incoming frames.
  }

  close(): void {
    // Nothing to release.
  }

  receive(frame: object): void {
    this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent);
  }
}

function notice(id: string, overrides: Partial<AppNotification> = {}): AppNotification {
  return {
    id,
    kind: 'proactive_message',
    conversation_id: 'chat-1',
    message_id: 'message-1',
    title: 'Alba',
    body: 'Ti penso.',
    read: false,
    read_at: null,
    created_at: '2026-11-09T10:00:00.000Z',
    ...overrides,
  };
}

let nextEventId = 0;
const created = (notification: AppNotification) => ({
  v: 1,
  id: ++nextEventId,
  type: 'notification.created',
  ts: '2026-11-09T10:00:00Z',
  data: { notification },
});

const isList = (request: { url: string; method: string }): boolean =>
  request.url === '/api/v1/notifications' && request.method === 'GET';

describe('NotificationsStore', () => {
  let store: NotificationsStore;
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
            const socket = new FakeSocket();
            sockets.push(socket);
            return socket;
          },
        },
      ],
    });
    store = TestBed.inject(NotificationsStore);
    backend = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    store.stop();
    vi.useRealTimers();
    backend.verify();
  });

  async function started(items: AppNotification[], unread: number): Promise<void> {
    store.start();
    backend.expectOne(isList).flush({ items, next_cursor: null, unread_count: unread });
    await vi.advanceTimersByTimeAsync(0);
  }

  it('loads the list and the unread count on start', async () => {
    await started([notice('n2'), notice('n1', { read: true })], 1);

    expect(store.items().map((item) => item.id)).toEqual(['n2', 'n1']);
    expect(store.unreadCount()).toBe(1);
    expect(store.toast()).toBeNull();
  });

  it('adds a notification.created event to the list, the badge and a toast that hides by itself', async () => {
    await started([], 0);

    sockets[0]?.receive(created(notice('n1')));

    expect(store.items().map((item) => item.id)).toEqual(['n1']);
    expect(store.unreadCount()).toBe(1);
    expect(store.toast()?.id).toBe('n1');

    await vi.advanceTimersByTimeAsync(TOAST_MS);
    expect(store.toast()).toBeNull();
    expect(store.items()).toHaveLength(1);
  });

  it('counts a replayed event only once', async () => {
    await started([], 0);

    sockets[0]?.receive(created(notice('n1')));
    sockets[0]?.receive(created(notice('n1')));

    expect(store.items()).toHaveLength(1);
    expect(store.unreadCount()).toBe(1);
  });

  it('marks one notice read from the server answer and lowers the badge', async () => {
    await started([notice('n2'), notice('n1')], 2);
    const target = store.items()[0] as AppNotification;

    const done = store.markRead(target);
    const request = backend.expectOne('/api/v1/notifications/n2/read');
    expect(request.request.method).toBe('POST');
    request.flush(notice('n2', { read: true, read_at: '2026-11-09T10:05:00.000Z' }));

    expect(await done).toBe(true);
    expect(store.items().map((item) => item.read)).toEqual([true, false]);
    expect(store.unreadCount()).toBe(1);
  });

  it('keeps the list and reports the failure when marking is refused', async () => {
    await started([notice('n1')], 1);

    const done = store.markRead(store.items()[0] as AppNotification);
    backend.expectOne('/api/v1/notifications/n1/read').flush({ code: 'NOT_FOUND' }, { status: 404, statusText: 'Not Found' });

    expect(await done).toBe(false);
    expect(store.actionError()?.code).toBe('NOT_FOUND');
    expect(store.unreadCount()).toBe(1);
    expect(store.items()[0]?.read).toBe(false);
  });

  it('marks every notice read and hides the toast', async () => {
    await started([notice('n2'), notice('n1')], 2);
    sockets[0]?.receive(created(notice('n3')));
    expect(store.unreadCount()).toBe(3);

    const done = store.markAllRead();
    backend.expectOne('/api/v1/notifications/read-all').flush({ updated: 3 });

    expect(await done).toBe(true);
    expect(store.unreadCount()).toBe(0);
    expect(store.items().every((item) => item.read)).toBe(true);
    expect(store.toast()).toBeNull();
  });

  it('reloads through REST when the server asks for a resync', async () => {
    await started([], 0);

    sockets[0]?.receive({ v: 1, id: 1, type: 'resync.required', ts: '2026-11-09T10:00:00Z', data: {} });
    backend.expectOne(isList).flush({ items: [notice('n1')], next_cursor: null, unread_count: 1 });
    await vi.advanceTimersByTimeAsync(0);

    expect(store.unreadCount()).toBe(1);
  });
});
