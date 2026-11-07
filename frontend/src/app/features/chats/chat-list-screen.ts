// Screen 1 of docs/frontend.md: the conversation list with search, an archived tab, row actions and the new-chat dialog.
import { ChangeDetectionStrategy, Component, OnInit, inject, signal, viewChild } from '@angular/core';
import { RouterLink } from '@angular/router';

import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { TextKey, Translator } from '../../core/i18n/translator';
import { formatActivity } from './activity-time';
import { ChatsStore } from './chats-store';
import { Conversation, ConversationStatus } from './chats.model';
import { NewChatDialog } from './new-chat-dialog';

interface Tab {
  readonly status: ConversationStatus;
  readonly label: TextKey;
}

/**
 * The list of conversations. Each row links to its chat, shows the avatar initial, the title, the character, the time
 * of the last activity and whether it is pinned, and has a menu with pin, archive and delete. Loading, empty and error
 * states are shown. Unread counts, previews and typing flags stay out until the services behind them exist (P07, P05).
 */
@Component({
  selector: 'lyr-chat-list-screen',
  imports: [RouterLink, TranslatePipe, NewChatDialog],
  templateUrl: './chat-list-screen.html',
  styleUrl: './chat-list-screen.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  // Esc closes an open row menu from anywhere, as docs/frontend.md (Keyboard) promises.
  host: { '(document:keydown.escape)': 'closeMenu()' },
})
export class ChatListScreen implements OnInit {
  protected readonly store = inject(ChatsStore);
  private readonly translator = inject(Translator);
  private readonly newChat = viewChild.required(NewChatDialog);

  protected readonly tabs: readonly Tab[] = [
    { status: 'active', label: 'chats.tab.active' },
    { status: 'archived', label: 'chats.tab.archived' },
  ];
  /** The id of the row whose menu is open, or null. */
  protected readonly openMenu = signal<string | null>(null);

  ngOnInit(): void {
    void this.store.showTab(this.store.tab());
  }

  protected openNewChat(): void {
    this.openMenu.set(null);
    void this.newChat().open();
  }

  protected toggleMenu(conversation: Conversation): void {
    this.openMenu.update((current) => (current === conversation.id ? null : conversation.id));
  }

  protected closeMenu(): void {
    this.openMenu.set(null);
  }

  protected async run(conversation: Conversation, action: 'pin' | 'archive' | 'delete'): Promise<void> {
    this.openMenu.set(null);
    if (action === 'pin') {
      await this.store.togglePin(conversation);
    } else if (action === 'archive') {
      await this.store.toggleArchive(conversation);
    } else {
      await this.store.remove(conversation);
    }
  }

  protected search(event: Event): void {
    this.store.search((event.target as HTMLInputElement).value);
  }

  protected time(conversation: Conversation): string {
    return formatActivity(conversation.last_message_at ?? conversation.created_at, new Date(), this.translator.locale());
  }

  protected initial(conversation: Conversation): string {
    return Array.from(conversation.title.trim())[0]?.toLocaleUpperCase(this.translator.locale()) ?? '?';
  }
}
