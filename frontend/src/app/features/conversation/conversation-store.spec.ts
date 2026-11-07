import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { provideApi } from '../../core/api/providers';
import { EVENTS_SOCKET_FACTORY, EVENTS_SOCKET_URL, SocketLike } from '../../core/realtime/events-socket';
import { Conversation } from '../chats/chats.model';
import { CATCH_UP_MS, ConversationStore, applyDelta } from './conversation-store';
import { Message, MessagePage, Variant } from './conversation.model';

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

function message(id: string, seq: number, overrides: Partial<Message> = {}): Message {
  return {
    id,
    conversation_id: 'c1',
    seq,
    author: 'character',
    kind: 'text',
    status: 'complete',
    read_state: null,
    active_variant_id: `v-${id}`,
    content: `Testo ${id}`,
    variant_count: 1,
    edited_at: null,
    created_at: '2026-11-07T10:00:00.000Z',
    ...overrides,
  };
}

const chat: Conversation = {
  id: 'c1',
  character_id: 'ch1',
  character_name: 'Alba',
  title: 'Alba',
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
};

const page = (items: Message[], next: number | null = null): MessagePage => ({ items, next_before_seq: next });
const isChat = (request: { url: string }): boolean => request.url === '/api/v1/conversations/c1';
const isNewest = (request: { url: string; params: { has(name: string): boolean } }): boolean =>
  request.url === '/api/v1/conversations/c1/messages' && !request.params.has('before_seq');

describe('applyDelta', () => {
  it('appends at the end, adds only the new tail of an overlap, ignores a repeat and reports a gap', () => {
    expect(applyDelta('Ciao', 4, ' mondo')).toBe('Ciao mondo');
    expect(applyDelta('Ciao mon', 4, ' mondo')).toBe('Ciao mondo');
    expect(applyDelta('Ciao mondo', 4, ' mondo')).toBe('Ciao mondo');
    expect(applyDelta('Ciao', 5, 'x')).toBeNull();
  });
});

describe('ConversationStore', () => {
  let store: ConversationStore;
  let backend: HttpTestingController;
  let socket: FakeSocket;
  let sockets: FakeSocket[];

  beforeEach(() => {
    vi.useFakeTimers();
    // Without requestAnimationFrame the store batches deltas on a 16 ms timer, which the fake clock drives.
    vi.stubGlobal('requestAnimationFrame', undefined);
    sockets = [];
    TestBed.configureTestingModule({
      providers: [
        ConversationStore,
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
    store = TestBed.inject(ConversationStore);
    backend = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    backend.verify();
  });

  /** Opens the chat, answers both requests with `items`, and connects the fake socket. */
  async function openWith(items: Message[], next: number | null = null): Promise<void> {
    const opened = store.open('c1');
    backend.expectOne(isChat).flush(chat);
    backend.expectOne(isNewest).flush(page(items, next));
    await opened;
    // `open` connected the socket; the server now accepts it.
    socket = sockets[0] as FakeSocket;
    socket.open();
  }

  /** Delivers deltas and lets the batching timer fire. */
  async function stream(...deltas: { offset: number; text: string }[]): Promise<void> {
    for (const delta of deltas) {
      socket.receive(event('message.delta', { message_id: 'r1', variant_id: 'v-r1', ...delta }));
    }
    await vi.advanceTimersByTimeAsync(20);
  }

  it('opens a chat with its newest page', async () => {
    await openWith([message('m1', 1), message('m2', 2)], 1);
    expect(store.conversation()?.character_name).toBe('Alba');
    expect(store.messages().map((item) => item.id)).toEqual(['m1', 'm2']);
    expect(store.loading()).toBe(false);
    expect(store.hasOlder()).toBe(true);
  });

  it('shows the error when the chat cannot be opened', async () => {
    const opened = store.open('c1');
    backend.expectOne(isChat).flush({ code: 'NOT_FOUND' }, { status: 404, statusText: 'Not Found' });
    backend.expectOne(isNewest).flush({ code: 'NOT_FOUND' }, { status: 404, statusText: 'Not Found' });
    await opened;
    expect(store.error()?.code).toBe('NOT_FOUND');
    expect(store.loading()).toBe(false);
  });

  it('adds streamed pieces in order, once per batch, and drops the live text when the reply completes (T-MSG-03)', async () => {
    await openWith([message('u1', 1, { author: 'user' }), message('r1', 2, { status: 'streaming', content: '' })]);
    expect(store.typing()).toBe(true);

    await stream({ offset: 0, text: 'Ciao' }, { offset: 4, text: ' mondo' });
    expect(store.streams()['r1']).toBe('Ciao mondo');

    // A repeated piece, as a replay delivers it, changes nothing.
    await stream({ offset: 4, text: ' mondo' });
    expect(store.streams()['r1']).toBe('Ciao mondo');

    socket.receive(event('message.completed', { message: message('r1', 2, { content: 'Ciao mondo!' }) }));
    expect(store.streams()['r1']).toBeUndefined();
    expect(store.messages().at(-1)?.content).toBe('Ciao mondo!');
    expect(store.typing()).toBe(false);
  });

  it('discards the streamed words when a check restarts the reply', async () => {
    await openWith([message('r1', 1, { status: 'streaming', content: '' })]);
    await stream({ offset: 0, text: 'Parole scartate' });
    socket.receive(event('message.restarted', { message_id: 'r1', variant_id: 'v-r1', check: 'V6' }));
    expect(store.streams()['r1']).toBe('');
    await stream({ offset: 0, text: 'Parole nuove' });
    expect(store.streams()['r1']).toBe('Parole nuove');
  });

  it('resumes a reply after a page reload by waiting for the stored text to reach the live pieces (T-MSG-07)', async () => {
    // The reload found the reply streaming with the 2-second flush: "Ciao" is stored, more has been said since.
    await openWith([message('r1', 1, { status: 'streaming', content: 'Ciao' })]);
    expect(store.streams()['r1']).toBeUndefined();

    await stream({ offset: 4, text: ' mondo' });
    expect(store.streams()['r1']).toBe('Ciao mondo');

    // This piece starts beyond what the page has seen, so it waits instead of leaving a hole in the text.
    await stream({ offset: 15, text: '!' });
    expect(store.streams()['r1']).toBe('Ciao mondo');

    await vi.advanceTimersByTimeAsync(CATCH_UP_MS);
    backend.expectOne(isNewest).flush(page([message('r1', 1, { status: 'streaming', content: 'Ciao mondo ciao' })]));
    await vi.advanceTimersByTimeAsync(0);
    expect(store.streams()['r1']).toBe('Ciao mondo ciao!');
    // Nothing waits any more and the socket is open, so no further reload is scheduled.
    await vi.advanceTimersByTimeAsync(CATCH_UP_MS * 2);
  });

  it('reloads through REST when the server asks for a resync, keeping older pages (T-MSG-12)', async () => {
    await openWith([message('m3', 3), message('m4', 4)], 3);
    const older = store.loadOlder();
    const olderRequest = backend.expectOne((request) => request.params.get('before_seq') === '3');
    olderRequest.flush(page([message('m1', 1), message('m2', 2)]));
    await older;
    expect(store.messages().map((item) => item.id)).toEqual(['m1', 'm2', 'm3', 'm4']);
    expect(store.hasOlder()).toBe(false);

    socket.receive({ v: 1, id: 99, type: 'resync.required', ts: '2026-11-07T10:00:00Z', data: {} });
    backend.expectOne(isNewest).flush(page([message('m3', 3), message('m4', 4, { content: 'Corretto' }), message('m5', 5)], 3));
    await vi.advanceTimersByTimeAsync(0);
    expect(store.messages().map((item) => item.id)).toEqual(['m1', 'm2', 'm3', 'm4', 'm5']);
    expect(store.messages()[3]?.content).toBe('Corretto');
    // The older pages stay loaded, so the resync does not bring back the cursor to a page already shown.
    expect(store.hasOlder()).toBe(false);
  });

  it('shows a sent message at once, replaces it with the confirmed pair, and keeps it for a retry on failure (T-MSG-02)', async () => {
    await openWith([]);
    const sending = store.send('Ciao!');
    expect(store.pending().map((item) => [item.content, item.state])).toEqual([['Ciao!', 'sending']]);

    const first = backend.expectOne((request) => request.method === 'POST' && request.url.endsWith('/messages'));
    const clientId = (first.request.body as { client_message_id: string }).client_message_id;
    first.flush({ code: 'NETWORK' }, { status: 503, statusText: 'Service Unavailable' });
    await sending;
    expect(store.pending().map((item) => item.state)).toEqual(['failed']);
    expect(store.pending()[0]?.content).toBe('Ciao!');

    const retried = store.retry(clientId);
    const second: TestRequest = backend.expectOne((request) => request.method === 'POST' && request.url.endsWith('/messages'));
    expect((second.request.body as { client_message_id: string }).client_message_id).toBe(clientId);
    second.flush({
      message: message('u1', 1, { author: 'user', content: 'Ciao!', read_state: 'sent' }),
      reply: message('r1', 2, { status: 'queued', content: '' }),
      job: { id: 'j1', kind: 'chat_reply', status: 'queued', queue_position: 1 },
    });
    await retried;
    expect(store.pending()).toEqual([]);
    expect(store.messages().map((item) => item.id)).toEqual(['u1', 'r1']);
    expect(store.queued()).toBe(true);
  });

  it('edits and deletes a message and reports a refusal (T-MSG-05, T-MSG-06)', async () => {
    await openWith([message('u1', 1, { author: 'user', content: 'Prima' }), message('r1', 2)]);
    const edited = store.edit(store.messages()[0] as Message, 'Dopo');
    const patch = backend.expectOne((request) => request.method === 'PATCH' && request.url === '/api/v1/messages/u1');
    expect(patch.request.body).toEqual({ content: 'Dopo' });
    patch.flush(message('u1', 1, { author: 'user', content: 'Dopo', edited_at: '2026-11-07T11:00:00.000Z' }));
    expect(await edited).toBe(true);
    expect(store.messages()[0]?.content).toBe('Dopo');

    const removed = store.remove(store.messages()[1] as Message);
    backend.expectOne((request) => request.method === 'DELETE' && request.url === '/api/v1/messages/r1').flush(null, { status: 204, statusText: 'No Content' });
    expect(await removed).toBe(true);
    expect(store.messages().map((item) => item.id)).toEqual(['u1']);

    const refused = store.remove(store.messages()[0] as Message);
    backend
      .expectOne((request) => request.method === 'DELETE' && request.url === '/api/v1/messages/u1')
      .flush({ code: 'CONFLICT' }, { status: 409, statusText: 'Conflict' });
    expect(await refused).toBe(false);
    expect(store.actionError()?.code).toBe('CONFLICT');
    expect(store.messages().map((item) => item.id)).toEqual(['u1']);
  });

  it('regenerates the latest reply and steps between its alternatives (T-MSG-04)', async () => {
    const variants = (active: string): Variant[] =>
      ['v1', 'v2'].map((id, index): Variant => ({
        id,
        message_id: 'r1',
        variant_no: index + 1,
        content: `Versione ${id}`,
        source: 'generated',
        status: 'complete',
        created_at: active,
      }));
    await openWith([message('u1', 1, { author: 'user' }), message('r1', 2, { variant_count: 2, active_variant_id: 'v2' })]);
    backend.expectOne('/api/v1/messages/r1/variants').flush({ items: variants('a') });
    await vi.advanceTimersByTimeAsync(0);
    expect(store.alternatives()).toEqual({ index: 1, count: 2 });

    const stepped = store.stepAlternative(-1);
    const activate = backend.expectOne((request) => request.method === 'POST' && request.url === '/api/v1/messages/r1/variants/v1/activate');
    activate.flush(message('r1', 2, { variant_count: 2, active_variant_id: 'v1', content: 'Versione v1' }));
    await stepped;
    // The message changed, so the versions are read again before the control shows a position.
    backend.expectOne('/api/v1/messages/r1/variants').flush({ items: variants('b') });
    await vi.advanceTimersByTimeAsync(0);
    expect(store.alternatives()).toEqual({ index: 0, count: 2 });
    expect(await store.stepAlternative(-1)).toBe(false);

    const regenerated = store.regenerate(store.latestReply() as Message);
    backend
      .expectOne((request) => request.method === 'POST' && request.url === '/api/v1/messages/r1/regenerate')
      .flush({ message: message('r1', 2, { status: 'queued', content: '', variant_count: 3, active_variant_id: 'v3' }), variant_id: 'v3', job: {} });
    await regenerated;
    expect(store.messages().at(-1)?.status).toBe('queued');
    expect(store.alternatives()).toBeNull();
    backend.expectOne('/api/v1/messages/r1/variants').flush({ items: [] });
  });

  describe('image requests (T-IMG-07)', () => {
    const body = { request: 'una foto al porto', aspect: 'auto', outfit: null, quality: 'alta', seed: null, steps: 20, cfg: 6.5 } as const;
    const isImageRequest = (request: { method: string; url: string }): boolean =>
      request.method === 'POST' && request.url === '/api/v1/conversations/c1/images';
    const placeholder = (): Message => message('i1', 2, { kind: 'image', status: 'queued', content: '', active_variant_id: 'v-i1' });
    const progress = (phase: string, value: number | null = null, max: number | null = null) =>
      socket.receive(event('image.progress', { job_id: 'j1', message_id: 'i1', phase, value, max }));

    it('posts the form and shows the placeholder with the user request', async () => {
      await openWith([]);
      const asked = store.requestImage(body);
      const request = backend.expectOne(isImageRequest);
      expect(request.request.body).toEqual(body);
      request.flush({
        message: message('u1', 1, { author: 'user', content: body.request }),
        reply: placeholder(),
        job: { id: 'j1', kind: 'image_request', status: 'queued', queue_position: 1 },
      });
      expect(await asked).toBeNull();
      expect(store.messages().map((item) => [item.id, item.kind])).toEqual([
        ['u1', 'text'],
        ['i1', 'image'],
      ]);
    });

    it('returns the failure to the dialog and leaves the action error alone', async () => {
      await openWith([]);
      const asked = store.requestImage(body);
      backend
        .expectOne(isImageRequest)
        .flush({ code: 'VALIDATION_ERROR', detail: 'Il vestito non esiste.' }, { status: 422, statusText: 'Unprocessable Entity' });
      const failure = await asked;
      expect(failure?.code).toBe('VALIDATION_ERROR');
      expect(failure?.detail).toBe('Il vestito non esiste.');
      expect(store.actionError()).toBeNull();
      expect(store.messages()).toEqual([]);
    });

    it('follows the progress of both passes and drops it when the message completes', async () => {
      await openWith([message('u1', 1, { author: 'user' }), placeholder()]);
      expect(store.drawing()).toBe(false);

      progress('prompt');
      expect(store.imageProgress()['i1']).toEqual({ phase: 'prompt', value: null, max: null });
      expect(store.drawing()).toBe(true);

      progress('sampling', 14, 28);
      expect(store.imageProgress()['i1']).toEqual({ phase: 'sampling', value: 14, max: 28 });
      progress('refining', 5, 20);
      expect(store.imageProgress()['i1']).toEqual({ phase: 'refining', value: 5, max: 20 });

      // An event with no message (character mode) or with a phase the screen does not know is ignored.
      socket.receive(event('image.progress', { job_id: 'j2', message_id: null, phase: 'sampling', value: 1, max: 2 }));
      socket.receive(event('image.progress', { job_id: 'j1', message_id: 'i1', phase: 'unknown', value: 1, max: 2 }));
      expect(Object.keys(store.imageProgress())).toEqual(['i1']);
      expect(store.imageProgress()['i1']?.phase).toBe('refining');

      socket.receive(event('message.completed', { message: message('i1', 2, { kind: 'mixed', content: 'Eccola.', image_id: 'img1' }) }));
      expect(store.imageProgress()['i1']).toBeUndefined();
      expect(store.drawing()).toBe(false);
      expect(store.messages().at(-1)?.image_id).toBe('img1');

      // A late progress event for a finished image does not bring the bar back.
      progress('saving');
      expect(store.imageProgress()['i1']).toBeUndefined();
    });

    it('drops the progress of an image that failed', async () => {
      await openWith([placeholder()]);
      progress('loading');
      expect(store.drawing()).toBe(true);
      socket.receive(event('message.failed', { message_id: 'i1', error_code: 'RUNTIME_UNAVAILABLE' }));
      expect(store.imageProgress()['i1']).toBeUndefined();
      expect(store.messages()[0]?.status).toBe('failed');
    });

    it('reads the named outfits of the chat character, and none when the card cannot be read', async () => {
      await openWith([]);
      const found = store.outfits();
      backend.expectOne('/api/v1/characters/ch1').flush({
        id: 'ch1',
        card: { visual: { outfits: [{ key: 'notte', label: 'Di notte', description: '', tags_en: [] }] } },
      });
      expect((await found).map((outfit) => outfit.key)).toEqual(['notte']);

      const failed = store.outfits();
      backend.expectOne('/api/v1/characters/ch1').flush({ code: 'NOT_FOUND' }, { status: 404, statusText: 'Not Found' });
      expect(await failed).toEqual([]);
    });
  });
});
