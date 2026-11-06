import { TestBed } from '@angular/core/testing';

import {
  EVENTS_SOCKET_FACTORY,
  EVENTS_SOCKET_URL,
  EventsSocket,
  PING_INTERVAL_MS,
  RECONNECT_DELAYS_MS,
  ServerEvent,
  SocketLike,
} from './events-socket';

/** A scripted socket: the test decides when it opens, what it receives and when it drops. */
class FakeSocket implements SocketLike {
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  readonly sent: unknown[] = [];
  closed = false;

  constructor(readonly url: string) {}

  send(data: string): void {
    this.sent.push(JSON.parse(data));
  }

  close(): void {
    this.closed = true;
  }

  open(): void {
    this.onopen?.(new Event('open'));
  }

  receive(frame: object): void {
    this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent);
  }

  drop(): void {
    this.onerror?.(new Event('error'));
    this.onclose?.(new CloseEvent('close'));
  }
}

const event = (id: number, type = 'message.created', data: object = {}) => ({ v: 1, id, type, ts: '2026-11-06T10:00:00Z', data });
const connected = (bootId: string) => ({ v: 1, type: 'connected', boot_id: bootId });

describe('EventsSocket', () => {
  let sockets: FakeSocket[];
  let client: EventsSocket;
  let received: ServerEvent[];
  let resyncs: number;

  beforeEach(() => {
    vi.useFakeTimers();
    sockets = [];
    TestBed.configureTestingModule({
      providers: [
        { provide: EVENTS_SOCKET_URL, useValue: 'ws://lyrveil.test/ws' },
        {
          provide: EVENTS_SOCKET_FACTORY,
          useValue: (url: string) => {
            const socket = new FakeSocket(url);
            sockets.push(socket);
            return socket;
          },
        },
      ],
    });
    client = TestBed.inject(EventsSocket);
    received = [];
    resyncs = 0;
    client.events$.subscribe((item) => received.push(item));
    client.resync$.subscribe(() => (resyncs += 1));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const current = (): FakeSocket => {
    const socket = sockets[sockets.length - 1];
    if (socket === undefined) {
      throw new Error('no socket was created');
    }
    return socket;
  };

  /** Drops the current socket, waits the backoff and returns the socket of the next attempt. */
  const reconnect = (delay: number): FakeSocket => {
    current().drop();
    vi.advanceTimersByTime(delay);
    return current();
  };

  it('connects to the address, says hello with no position yet and delivers events by type', () => {
    const typing: string[] = [];
    client.on('typing.started').subscribe((item) => typing.push(item.type));
    client.connect();
    expect(client.state()).toBe('connecting');
    expect(current().url).toBe('ws://lyrveil.test/ws');

    current().open();
    expect(client.state()).toBe('open');
    expect(current().sent).toEqual([{ type: 'hello', last_event_id: null, boot_id: null }]);

    current().receive(event(1, 'typing.started', { conversation_id: 'c1' }));
    current().receive(event(2, 'message.created', { message: { id: 'm1' } }));
    expect(received.map((item) => item.id)).toEqual([1, 2]);
    expect(received[0]?.data).toEqual({ conversation_id: 'c1' });
    expect(typing).toEqual(['typing.started']);
    expect(resyncs).toBe(0);
  });

  it('ignores frames that are malformed, of another version or of an unknown type', () => {
    client.connect();
    current().open();
    current().onmessage?.({ data: 'not json' } as MessageEvent);
    current().onmessage?.({ data: new ArrayBuffer(2) } as MessageEvent);
    current().receive({ ...event(1), v: 2 });
    current().receive(event(2, 'something.new'));
    current().receive({ v: 1, type: 'message.created', ts: 'x', data: {} });
    current().receive(event(3));
    expect(received.map((item) => item.id)).toEqual([3]);
  });

  it('reconnects after 1, 2, 5 and then every 10 seconds', () => {
    expect(RECONNECT_DELAYS_MS).toEqual([1000, 2000, 5000, 10000]);
    client.connect();
    expect(sockets).toHaveLength(1);
    for (const [index, delay] of [1000, 2000, 5000, 10000, 10000].entries()) {
      current().drop();
      expect(client.state()).toBe('reconnecting');
      vi.advanceTimersByTime(delay - 1);
      expect(sockets, `before attempt ${index + 2}`).toHaveLength(index + 1);
      vi.advanceTimersByTime(1);
      expect(sockets, `at attempt ${index + 2}`).toHaveLength(index + 2);
    }
  });

  it('starts again from the shortest delay once a connection has served a frame', () => {
    client.connect();
    reconnect(1000);
    reconnect(2000);
    current().open();
    current().receive(connected('boot-1'));
    reconnect(1000);
    expect(sockets).toHaveLength(4);
  });

  it('asks for replay from the last delivered event ID with the announced boot ID', () => {
    client.connect();
    current().open();
    current().receive(connected('boot-1'));
    current().receive(event(1841));
    current().receive(event(1842));

    const next = reconnect(1000);
    expect(client.state()).toBe('reconnecting');
    next.open();
    expect(client.state()).toBe('open');
    expect(next.sent).toEqual([{ type: 'hello', last_event_id: 1842, boot_id: 'boot-1' }]);

    // The replay starts at the gap, and may repeat what was already delivered.
    next.receive(event(1842));
    next.receive(event(1843));
    next.receive(event(1844));
    expect(received.map((item) => item.id)).toEqual([1841, 1842, 1843, 1844]);
    expect(resyncs).toBe(0);
  });

  it('resyncs, and not delivers, when the server reports the gap as too old', () => {
    client.connect();
    current().open();
    current().receive(connected('boot-1'));
    current().receive(event(10));

    const next = reconnect(1000);
    next.open();
    next.receive(connected('boot-1'));
    next.receive({ v: 1, id: 4000, type: 'resync.required', ts: 'x', data: {} });
    expect(resyncs).toBe(1);
    expect(received.map((item) => item.id)).toEqual([10]);

    // The next reconnection resumes from the position the server stated.
    const third = reconnect(1000);
    third.open();
    expect(third.sent).toEqual([{ type: 'hello', last_event_id: 4000, boot_id: 'boot-1' }]);
    expect(resyncs).toBe(1);
  });

  it('resyncs when a reconnection has no event ID to replay from', () => {
    client.connect();
    current().open();
    current().receive(connected('boot-1'));
    expect(resyncs).toBe(0);

    reconnect(1000).open();
    expect(resyncs).toBe(1);
  });

  it('resyncs once when the backend restarted, however many signals say so', () => {
    client.connect();
    current().open();
    current().receive(connected('boot-1'));
    current().receive(event(1842));

    const next = reconnect(1000);
    next.open();
    next.receive(connected('boot-2'));
    next.receive({ v: 1, type: 'resync.required', ts: 'x', data: {} });
    expect(resyncs).toBe(1);

    // IDs restarted with the new process, so low ones are new events and the old position is gone.
    next.receive(event(3));
    expect(received.map((item) => item.id)).toEqual([1842, 3]);
    const third = reconnect(1000);
    third.open();
    expect(third.sent).toEqual([{ type: 'hello', last_event_id: 3, boot_id: 'boot-2' }]);
  });

  it('sends a ping on an open connection and stops when it is lost', () => {
    client.connect();
    current().open();
    vi.advanceTimersByTime(PING_INTERVAL_MS);
    expect(current().sent).toEqual([{ type: 'hello', last_event_id: null, boot_id: null }, { type: 'ping' }]);
    const first = current();
    first.drop();
    vi.advanceTimersByTime(PING_INTERVAL_MS - 1000);
    expect(first.sent).toHaveLength(2);
  });

  it('stops for good on disconnect, and resumes from the same position on connect', () => {
    client.connect();
    current().open();
    current().receive(connected('boot-1'));
    current().receive(event(7));

    const first = current();
    client.disconnect();
    expect(first.closed).toBe(true);
    expect(client.state()).toBe('closed');
    first.drop();
    vi.advanceTimersByTime(60_000);
    expect(sockets).toHaveLength(1);

    client.connect();
    current().open();
    expect(current().sent).toEqual([{ type: 'hello', last_event_id: 7, boot_id: 'boot-1' }]);
    expect(resyncs).toBe(0);
  });

  it('ignores a second connect while running and callbacks of a replaced socket', () => {
    client.connect();
    client.connect();
    expect(sockets).toHaveLength(1);
    const stale = current();
    stale.open();
    reconnect(1000);
    stale.receive(event(99));
    stale.drop();
    expect(received).toEqual([]);
    expect(sockets).toHaveLength(2);
  });
});
