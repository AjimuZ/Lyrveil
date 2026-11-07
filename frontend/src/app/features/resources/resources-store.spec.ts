import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { provideApi } from '../../core/api/providers';
import { EVENTS_SOCKET_FACTORY, EVENTS_SOCKET_URL, SocketLike } from '../../core/realtime/events-socket';
import { Resources } from './resources.model';
import { RESOURCES_EVENT_DELAY_MS, RESOURCES_POLL_MS, ResourcesStore } from './resources-store';

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

let nextEventId = 0;
const gpuState = (state: string, labelKey: string | null, queue: number) => ({
  v: 1,
  id: ++nextEventId,
  type: 'gpu.state',
  ts: '2026-11-07T10:00:00Z',
  data: { state, label_key: labelKey, queue, blocked_reason: null },
});

function snapshot(state: string, queued = 0): Resources {
  return {
    gpu: { state, label_key: null, desired_mode: 'llm', draining: false, blocked_reason: null, blocked_vram: null },
    vram: { device_name: 'Fake GPU', total_mib: 12282, used_mib: 1489, free_mib: 10793 },
    queue: {
      queued,
      running: 0,
      by_mode: {
        llm: { queued, running: 0 },
        image: { queued: 0, running: 0 },
        none: { queued: 0, running: 0 },
      },
    },
    model: { profile_id: 'gemma-4-12b', display_name: 'Gemma 4 12B', level: 'A', context_size: 16384 },
  };
}

const isResources = (request: { url: string }): boolean => request.url === '/api/v1/system/resources';

describe('ResourcesStore', () => {
  let store: ResourcesStore;
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
    store = TestBed.inject(ResourcesStore);
    backend = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    store.stop();
    vi.useRealTimers();
    backend.verify();
  });

  it('reads the snapshot on start and again every poll interval', async () => {
    store.start();
    backend.expectOne(isResources).flush(snapshot('LLM_READY'));
    await vi.advanceTimersByTimeAsync(0);
    expect(store.loading()).toBe(false);
    expect(store.snapshot()?.vram?.used_mib).toBe(1489);

    await vi.advanceTimersByTimeAsync(RESOURCES_POLL_MS);
    backend.expectOne(isResources).flush(snapshot('LLM_READY', 2));
    await vi.advanceTimersByTimeAsync(0);
    expect(store.snapshot()?.queue.queued).toBe(2);
  });

  it('shows a gpu.state event at once, reads after the events rest, and then drops the event', async () => {
    store.start();
    backend.expectOne(isResources).flush(snapshot('LLM_READY'));
    await vi.advanceTimersByTimeAsync(0);
    sockets[0]?.receive(gpuState('LOADING_IMAGE', 'gpu.state.waiting', 3));
    sockets[0]?.receive(gpuState('GENERATING_IMAGE', 'gpu.state.drawing', 3));

    expect(store.live()).toEqual({ state: 'GENERATING_IMAGE', labelKey: 'gpu.state.drawing', queue: 3, blockedReason: null });
    backend.expectNone(isResources);

    await vi.advanceTimersByTimeAsync(RESOURCES_EVENT_DELAY_MS);
    backend.expectOne(isResources).flush(snapshot('GENERATING_IMAGE'));
    await vi.advanceTimersByTimeAsync(0);
    expect(store.live()).toBeNull();
    expect(store.snapshot()?.gpu?.state).toBe('GENERATING_IMAGE');
  });

  it('keeps the previous values and reports the failure when a read fails', async () => {
    store.start();
    backend.expectOne(isResources).flush(snapshot('LLM_READY'));
    await vi.advanceTimersByTimeAsync(0);

    await vi.advanceTimersByTimeAsync(RESOURCES_POLL_MS);
    backend.expectOne(isResources).flush({ code: 'INTERNAL' }, { status: 500, statusText: 'Server Error' });
    await vi.advanceTimersByTimeAsync(0);

    expect(store.error()?.code).toBe('INTERNAL');
    expect(store.snapshot()?.gpu?.state).toBe('LLM_READY');
  });
});
