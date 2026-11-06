// Owns the event WebSocket: connects to /ws, reconnects with backoff and replays from the last event ID.
import { DOCUMENT } from '@angular/common';
import { DestroyRef, Injectable, InjectionToken, inject, signal } from '@angular/core';
import { Observable, Subject, filter } from 'rxjs';

/** Every event type of docs/api.md (Event WebSocket). `resync.required` is not delivered as an event: see `resync$`. */
export const EVENT_TYPES = [
  'gpu.state',
  'job.updated',
  'typing.started',
  'typing.stopped',
  'message.created',
  'message.delta',
  'message.restarted',
  'message.completed',
  'message.failed',
  'message.updated',
  'message.deleted',
  'message.read',
  'conversation.updated',
  'image.progress',
  'image.completed',
  'image.proposed',
  'memory.updated',
  'memory.conflict',
  'notification.created',
  'resync.required',
] as const;

export type EventType = (typeof EVENT_TYPES)[number];

/** The types a store can subscribe to with `EventsSocket.on`. */
export type DataEventType = Exclude<EventType, 'resync.required'>;

/** One server event. `data` is the raw payload of its type; each feature store narrows it to the shape it expects. */
export interface ServerEvent<T extends DataEventType = DataEventType> {
  /** Grows for the life of the backend process; the client resumes from the newest one it delivered. */
  readonly id: number;
  readonly type: T;
  readonly ts: string;
  readonly data: Readonly<Record<string, unknown>>;
}

/** The part of `WebSocket` the client uses, so tests can replace the browser's implementation. */
export interface SocketLike {
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  send(data: string): void;
  close(): void;
}

/** Waits before each reconnection attempt, in milliseconds. The last delay repeats until a connection succeeds. */
export const RECONNECT_DELAYS_MS: readonly number[] = [1000, 2000, 5000, 10000];

/** Time between `ping` frames on an open connection; it keeps the proxy from closing an idle one. */
export const PING_INTERVAL_MS = 25_000;

/**
 * `idle` before `connect`, `connecting` for the first attempt, `open` while connected, `reconnecting` from the moment
 * a connection is lost until the next one opens, `closed` after `disconnect`.
 */
export type SocketState = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed';

/** The WebSocket address on the page's own origin, which nginx proxies to the backend. */
export const EVENTS_SOCKET_URL = new InjectionToken<string>('EVENTS_SOCKET_URL', {
  providedIn: 'root',
  factory: () => {
    const { protocol, host } = inject(DOCUMENT).location;
    return `${protocol === 'https:' ? 'wss:' : 'ws:'}//${host}/ws`;
  },
});

/** Creates the socket for an address. Replaced in tests. */
export const EVENTS_SOCKET_FACTORY = new InjectionToken<(url: string) => SocketLike>('EVENTS_SOCKET_FACTORY', {
  providedIn: 'root',
  factory: () => (url: string) => new WebSocket(url),
});

const KNOWN_TYPES: ReadonlySet<string> = new Set(EVENT_TYPES);

type Frame =
  | { readonly kind: 'connected'; readonly bootId: string }
  | { readonly kind: 'resync'; readonly id: number | null }
  | { readonly kind: 'event'; readonly event: ServerEvent };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Returns the frame in raw, or null for anything that is not a version 1 frame of a type this client knows. */
function parseFrame(raw: unknown): Frame | null {
  if (typeof raw !== 'string') {
    return null;
  }
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(value) || value['v'] !== 1) {
    return null;
  }
  const type = value['type'];
  const id = value['id'];
  if (type === 'connected') {
    return typeof value['boot_id'] === 'string' ? { kind: 'connected', bootId: value['boot_id'] } : null;
  }
  if (typeof type !== 'string' || !KNOWN_TYPES.has(type)) {
    return null;
  }
  const eventId = typeof id === 'number' && Number.isSafeInteger(id) ? id : null;
  if (type === 'resync.required') {
    return { kind: 'resync', id: eventId };
  }
  if (eventId === null) {
    return null;
  }
  const ts = value['ts'];
  const data = value['data'];
  return {
    kind: 'event',
    event: {
      id: eventId,
      type: type as DataEventType,
      ts: typeof ts === 'string' ? ts : '',
      data: isRecord(data) ? data : {},
    },
  };
}

/**
 * The only owner of the event WebSocket (docs/frontend.md, State). Stores subscribe with `on(type)` and reload through
 * REST whenever `resync$` emits. REST is the source of truth, so a lost event never loses data.
 *
 * Protocol (docs/api.md, ADR 0016): after each open the client sends `hello` with the newest event ID it delivered and
 * the `boot_id` the server announced in its `connected` frame. The server replays what is missing, or answers
 * `resync.required`. The client also asks for a resync on its own when it cannot name a position: a reconnection
 * with no event delivered yet, or a `boot_id` that changed, which means the backend restarted.
 */
@Injectable({ providedIn: 'root' })
export class EventsSocket {
  private readonly url = inject(EVENTS_SOCKET_URL);
  private readonly createSocket = inject(EVENTS_SOCKET_FACTORY);
  private readonly eventStream = new Subject<ServerEvent>();
  private readonly resyncStream = new Subject<void>();
  private readonly stateSignal = signal<SocketState>('idle');

  /** Connection state, for the shell's "Lyrveil non risponde" notice. */
  readonly state = this.stateSignal.asReadonly();
  /** Every event once, in order, never an ID at or below one already delivered. Excludes `resync.required`. */
  readonly events$: Observable<ServerEvent> = this.eventStream.asObservable();
  /** Emits when the client's copy of the server may have a gap: the subscriber must reload through REST. */
  readonly resync$: Observable<void> = this.resyncStream.asObservable();

  private socket: SocketLike | null = null;
  private lastEventId: number | null = null;
  private bootId: string | null = null;
  private attempt = 0;
  private everOpened = false;
  private resyncRequested = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;

  constructor() {
    inject(DestroyRef).onDestroy(() => {
      this.disconnect();
      this.eventStream.complete();
      this.resyncStream.complete();
    });
  }

  /** Emits the events of one type. */
  on<T extends DataEventType>(type: T): Observable<ServerEvent<T>> {
    return this.events$.pipe(filter((event): event is ServerEvent<T> => event.type === type));
  }

  /**
   * Starts connecting and keeps the connection alive, reconnecting after 1, 2, 5 and then every 10 seconds. Does
   * nothing while already running. After `disconnect` it resumes from the last delivered event.
   */
  connect(): void {
    const state = this.stateSignal();
    if (state === 'connecting' || state === 'open' || state === 'reconnecting') {
      return;
    }
    this.attempt = 0;
    this.openSocket();
  }

  /** Closes the connection and stops reconnecting. The position in the event stream is kept for `connect`. */
  disconnect(): void {
    this.clearTimers();
    const socket = this.socket;
    this.socket = null;
    socket?.close();
    this.stateSignal.set('closed');
  }

  private openSocket(): void {
    this.stateSignal.set(this.attempt === 0 ? 'connecting' : 'reconnecting');
    this.resyncRequested = false;
    const socket = this.createSocket(this.url);
    this.socket = socket;
    // A callback from a socket that is no longer the current one is a late event of a replaced connection.
    socket.onopen = () => {
      if (this.socket === socket) {
        this.handleOpen(socket);
      }
    };
    socket.onmessage = (message) => {
      if (this.socket === socket) {
        this.handleFrame(parseFrame(message.data));
      }
    };
    // A failed connection fires `error` and then `close`; the identity check makes the second one a no-op.
    const lost = () => {
      if (this.socket === socket) {
        this.handleLoss();
      }
    };
    socket.onclose = lost;
    socket.onerror = lost;
  }

  private handleOpen(socket: SocketLike): void {
    this.stateSignal.set('open');
    socket.send(JSON.stringify({ type: 'hello', last_event_id: this.lastEventId, boot_id: this.bootId }));
    // After a loss with no event ever delivered there is no position to replay from.
    if (this.everOpened && this.lastEventId === null) {
      this.requestResync();
    }
    this.everOpened = true;
    this.pingTimer = setInterval(() => {
      socket.send(JSON.stringify({ type: 'ping' }));
    }, PING_INTERVAL_MS);
  }

  private handleFrame(frame: Frame | null): void {
    if (frame === null) {
      return;
    }
    // A valid frame proves the server is serving this connection, so the next loss starts from the shortest delay.
    this.attempt = 0;
    switch (frame.kind) {
      case 'connected':
        if (this.bootId !== null && this.bootId !== frame.bootId) {
          // Event IDs restart with the backend process, so the old position means nothing.
          this.lastEventId = null;
          this.requestResync();
        }
        this.bootId = frame.bootId;
        break;
      case 'resync':
        this.lastEventId = frame.id;
        this.requestResync();
        break;
      case 'event':
        // The replay may repeat events this client already delivered.
        if (this.lastEventId === null || frame.event.id > this.lastEventId) {
          this.lastEventId = frame.event.id;
          this.eventStream.next(frame.event);
        }
        break;
    }
  }

  private handleLoss(): void {
    this.socket = null;
    this.clearTimers();
    this.stateSignal.set('reconnecting');
    const last = RECONNECT_DELAYS_MS[RECONNECT_DELAYS_MS.length - 1] ?? 0;
    const delay = RECONNECT_DELAYS_MS[this.attempt] ?? last;
    this.attempt += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.openSocket();
    }, delay);
  }

  // One resync per connection, however many signals (client check, server frame) ask for it.
  private requestResync(): void {
    if (!this.resyncRequested) {
      this.resyncRequested = true;
      this.resyncStream.next();
    }
  }

  private clearTimers(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.pingTimer !== null) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }
}
