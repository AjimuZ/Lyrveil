// The bell icon of the shell: the unread badge, the list of notices it opens and the brief toast of a new one.
import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { Router } from '@angular/router';

import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { Translator } from '../../core/i18n/translator';
import { formatActivity } from '../chats/activity-time';
import { AppNotification } from './notification.model';
import { NotificationsStore } from './notifications-store';

/**
 * In-app notifications only (docs/scheduler.md, Notifications): a bell with the number of unread notices, a list that
 * opens under it, and a toast that shows a new notice for a few seconds. A notice that belongs to a chat opens that
 * chat and counts as read. Browser notifications are not used.
 */
@Component({
  selector: 'lyr-notification-center',
  imports: [TranslatePipe],
  templateUrl: './notification-center.html',
  styleUrl: './notification-center.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class NotificationCenter implements OnInit {
  protected readonly store = inject(NotificationsStore);
  private readonly translator = inject(Translator);
  private readonly router = inject(Router);

  protected readonly open = signal(false);

  ngOnInit(): void {
    this.store.start();
  }

  protected toggle(): void {
    this.open.update((value) => !value);
  }

  protected close(): void {
    this.open.set(false);
  }

  /** The bell's accessible name: its purpose, then the number of unread notices when there are any. */
  protected bellLabel(): string {
    const count = this.store.unreadCount();
    const name = this.translator.t('notifications.bell');
    return count > 0 ? `${name}: ${this.translator.plural('notifications.unread', count)}` : name;
  }

  protected time(notification: AppNotification): string {
    return formatActivity(notification.created_at, new Date(), this.translator.locale());
  }

  /** Marks the notice read and, when it belongs to a chat, opens the chat. */
  protected async openNotification(notification: AppNotification): Promise<void> {
    if (!notification.read) {
      await this.store.markRead(notification);
    }
    if (notification.conversation_id !== null) {
      this.close();
      this.store.dismissToast();
      await this.router.navigate(['/chats', notification.conversation_id]);
    }
  }
}
