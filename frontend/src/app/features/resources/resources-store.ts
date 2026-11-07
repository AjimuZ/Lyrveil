// AI resources screen state: the snapshot of GET /system/resources, kept live by the event WebSocket and a slow poll.
import { Injectable, inject, signal } from '@angular/core';
import { Subscription, firstValueFrom } from 'rxjs';

import { ApiClient } from '../../core/api/api-client';
import { ApiError, toApiError } from '../../core/api/api-error';
import { EventsSocket } from '../../core/realtime/events-socket';
import { LiveGpu, Resources } from './resources.model';

/** How often the snapshot is read while the screen is open: the card's memory has no event, so it needs a poll. */
export const RESOURCES_POLL_MS = 3000;
/** How long a burst of `gpu.state` events rests before the snapshot is read again. */
export const RESOURCES_EVENT_DELAY_MS = 250;

/**
 * Holds what the AI resources screen shows. `start` reads the snapshot, follows `gpu.state` (the state and queue show
 * at once, and a read follows after a short rest), reads again after a resync and every RESOURCES_POLL_MS, and `stop`
 * ends all of it. A read that a newer one overtook is dropped. A failed read keeps the previous values and sets
 * `error`, which the next good read clears.
 */
@Injectable({ providedIn: 'root' })
export class ResourcesStore {
  private readonly api = inject(ApiClient);
  private readonly socket = inject(EventsSocket);
  private latestRead = 0;
  private subscriptions: Subscription[] = [];
  private pollTimer: ReturnType<typeof setInterval> | undefined;
  private eventTimer: ReturnType<typeof setTimeout> | undefined;

  private readonly snapshotState = signal<Resources | null>(null);
  private readonly liveState = signal<LiveGpu | null>(null);
  private readonly loadingState = signal(true);
  private readonly errorState = signal<ApiError | null>(null);

  readonly snapshot = this.snapshotState.asReadonly();
  /** The latest `gpu.state` event, or null when a read has arrived since. */
  readonly live = this.liveState.asReadonly();
  /** True until the first answer arrives. */
  readonly loading = this.loadingState.asReadonly();
  readonly error = this.errorState.asReadonly();

  /** Starts following. Calling it again restarts the following. */
  start(): void {
    this.stop();
    this.loadingState.set(true);
    this.socket.connect();
    void this.refresh();
    this.subscriptions = [
      this.socket.on('gpu.state').subscribe((event) => {
        const state = event.data['state'];
        const labelKey = event.data['label_key'];
        const queue = event.data['queue'];
        const blocked = event.data['blocked_reason'];
        if (typeof state === 'string') {
          this.liveState.set({
            state,
            labelKey: typeof labelKey === 'string' ? labelKey : null,
            queue: typeof queue === 'number' ? queue : null,
            blockedReason: typeof blocked === 'string' ? blocked : null,
          });
          this.readSoon();
        }
      }),
      this.socket.resync$.subscribe(() => void this.refresh()),
    ];
    this.pollTimer = setInterval(() => void this.refresh(), RESOURCES_POLL_MS);
  }

  /** Stops the poll and the subscriptions. The last values stay for the next visit's first paint. */
  stop(): void {
    for (const subscription of this.subscriptions) {
      subscription.unsubscribe();
    }
    this.subscriptions = [];
    clearInterval(this.pollTimer);
    clearTimeout(this.eventTimer);
  }

  /** Reads the snapshot now. */
  async refresh(): Promise<void> {
    const ticket = ++this.latestRead;
    try {
      const snapshot = await firstValueFrom(this.api.get<Resources>('/system/resources'));
      if (ticket === this.latestRead) {
        this.snapshotState.set(snapshot);
        this.liveState.set(null);
        this.errorState.set(null);
      }
    } catch (failure) {
      if (ticket === this.latestRead) {
        this.errorState.set(toApiError(failure));
      }
    } finally {
      if (ticket === this.latestRead) {
        this.loadingState.set(false);
      }
    }
  }

  private readSoon(): void {
    clearTimeout(this.eventTimer);
    this.eventTimer = setTimeout(() => void this.refresh(), RESOURCES_EVENT_DELAY_MS);
  }
}
