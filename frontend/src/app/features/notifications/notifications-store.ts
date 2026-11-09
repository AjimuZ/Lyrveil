// In-app notifications state: the list under the bell, the unread count and the toast, kept live by the event WebSocket.
import { Injectable, inject, signal } from '@angular/core';
import { Subscription, firstValueFrom } from 'rxjs';

import { ApiClient } from '../../core/api/api-client';
import { ApiError, toApiError } from '../../core/api/api-error';
import { EventsSocket } from '../../core/realtime/events-socket';
import { AppNotification, NotificationList, toNotification } from './notification.model';

/** How long a toast stays before it hides on its own. */
export const TOAST_MS = 6000;
/** The newest notices the list loads; older ones are not offered in 1.0. */
export const LIST_LIMIT = 50;

/**
 * Holds what the bell icon shows (docs/scheduler.md, Notifications). `start` loads the list, follows
 * `notification.created` (the notice joins the list, the badge grows and a toast shows for TOAST_MS) and reloads after a
 * resync. Marking a notice read goes through REST and then changes the local copy from the answer. A failed load keeps
 * the previous list and sets `error`; a failed mark sets `actionError` and changes nothing. Browser notifications are
 * not used.
 */
@Injectable({ providedIn: 'root' })
export class NotificationsStore {
  private readonly api = inject(ApiClient);
  private readonly socket = inject(EventsSocket);
  private latestRead = 0;
  private subscriptions: Subscription[] = [];
  private toastTimer: ReturnType<typeof setTimeout> | undefined;

  private readonly itemsState = signal<readonly AppNotification[]>([]);
  private readonly unreadState = signal(0);
  private readonly toastState = signal<AppNotification | null>(null);
  private readonly errorState = signal<ApiError | null>(null);
  private readonly actionErrorState = signal<ApiError | null>(null);

  /** Newest first. */
  readonly items = this.itemsState.asReadonly();
  /** All unread notices, also those beyond the loaded list. */
  readonly unreadCount = this.unreadState.asReadonly();
  /** The notice that just arrived, or null once the toast hid. */
  readonly toast = this.toastState.asReadonly();
  readonly error = this.errorState.asReadonly();
  readonly actionError = this.actionErrorState.asReadonly();

  /** Loads the list and starts following. Does nothing while already following. */
  start(): void {
    if (this.subscriptions.length > 0) {
      return;
    }
    this.socket.connect();
    this.subscriptions = [
      this.socket.on('notification.created').subscribe((event) => {
        const notification = toNotification(event.data['notification']);
        if (notification !== null) {
          this.receive(notification);
        }
      }),
      this.socket.resync$.subscribe(() => void this.refresh()),
    ];
    void this.refresh();
  }

  /** Stops following and hides the toast. The last list stays. */
  stop(): void {
    for (const subscription of this.subscriptions) {
      subscription.unsubscribe();
    }
    this.subscriptions = [];
    this.dismissToast();
  }

  /** Loads the newest notices and the unread count. A read that a newer one overtook is dropped. */
  async refresh(): Promise<void> {
    const ticket = ++this.latestRead;
    try {
      const list = await firstValueFrom(this.api.get<NotificationList>('/notifications', { limit: LIST_LIMIT }));
      if (ticket === this.latestRead) {
        this.itemsState.set(list.items);
        this.unreadState.set(list.unread_count);
        this.errorState.set(null);
      }
    } catch (failure) {
      if (ticket === this.latestRead) {
        this.errorState.set(toApiError(failure));
      }
    }
  }

  /** Marks one notice read. Returns false, with `actionError` set, when the server refused. */
  async markRead(notification: AppNotification): Promise<boolean> {
    this.actionErrorState.set(null);
    try {
      const marked = await firstValueFrom(this.api.post<AppNotification>(`/notifications/${notification.id}/read`));
      const before = this.itemsState().find((item) => item.id === notification.id);
      this.itemsState.update((items) => items.map((item) => (item.id === marked.id ? marked : item)));
      if (before !== undefined && !before.read) {
        this.unreadState.update((count) => Math.max(0, count - 1));
      }
      if (this.toastState()?.id === notification.id) {
        this.dismissToast();
      }
      return true;
    } catch (failure) {
      this.actionErrorState.set(toApiError(failure));
      return false;
    }
  }

  /** Marks every notice read. Returns false, with `actionError` set, when the server refused. */
  async markAllRead(): Promise<boolean> {
    this.actionErrorState.set(null);
    try {
      await firstValueFrom(this.api.post<{ updated: number }>('/notifications/read-all'));
    } catch (failure) {
      this.actionErrorState.set(toApiError(failure));
      return false;
    }
    const now = new Date().toISOString();
    this.itemsState.update((items) => items.map((item) => (item.read ? item : { ...item, read: true, read_at: now })));
    this.unreadState.set(0);
    this.dismissToast();
    return true;
  }

  /** Hides the toast. The notice stays in the list. */
  dismissToast(): void {
    clearTimeout(this.toastTimer);
    this.toastState.set(null);
  }

  // A replayed event can repeat a notice the list already holds: it neither counts twice nor shows again.
  private receive(notification: AppNotification): void {
    if (this.itemsState().some((item) => item.id === notification.id)) {
      return;
    }
    this.itemsState.update((items) => [notification, ...items].slice(0, LIST_LIMIT));
    if (!notification.read) {
      this.unreadState.update((count) => count + 1);
      this.toastState.set(notification);
      clearTimeout(this.toastTimer);
      this.toastTimer = setTimeout(() => this.toastState.set(null), TOAST_MS);
    }
  }
}
