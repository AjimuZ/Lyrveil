// Screen 1 of docs/frontend.md: the conversation list with search, an archived tab, row actions and the new-chat dialog.
import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  Injector,
  OnDestroy,
  OnInit,
  afterNextRender,
  effect,
  inject,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';

import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { TextKey, Translator } from '../../core/i18n/translator';
import { ObEmptyState } from '../../shared/ui/empty-state';
import { ObSkeleton } from '../../shared/ui/skeleton';
import { ShortcutService } from '../shortcuts/shortcut-service';
import { formatActivity } from './activity-time';
import { ChatsStore } from './chats-store';
import { Conversation, ConversationActivity, ConversationStatus } from './chats.model';
import { NewChatDialog } from './new-chat-dialog';

const ACTIVITY_TEXT: Readonly<Record<ConversationActivity, TextKey>> = {
  typing: 'chats.activity.typing',
  drawing: 'chats.activity.drawing',
  queued: 'chats.activity.queued',
};

interface Tab {
  readonly status: ConversationStatus;
  readonly label: TextKey;
}

/**
 * The list of conversations. Each row links to its chat, shows the primary avatar (the initial while it has none), the title, the character, the time
 * of the last activity and whether it is pinned, and has a menu with pin, archive and delete. Loading, empty and error
 * states are shown. A row also shows the unread count, a one-line preview of the newest finished message and, while
 * the character types, draws or waits in the queue, that hint instead of the preview. The rows follow the event
 * socket while the screen is open (`ChatsStore.follow`). Ctrl+K focuses the search field and Ctrl+N opens the new-chat
 * dialog, on this screen or from any other (`ShortcutService.pending`).
 */
@Component({
  selector: 'lyr-chat-list-screen',
  imports: [RouterLink, TranslatePipe, NewChatDialog, ObEmptyState, ObSkeleton],
  templateUrl: './chat-list-screen.html',
  styleUrl: './chat-list-screen.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  // Esc closes an open row menu from anywhere, as docs/frontend.md (Keyboard) promises.
  host: { '(document:keydown.escape)': 'closeMenu()' },
})
export class ChatListScreen implements OnInit, AfterViewInit, OnDestroy {
  protected readonly store = inject(ChatsStore);
  private readonly translator = inject(Translator);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly newChat = viewChild.required(NewChatDialog);
  private readonly searchField = viewChild<ElementRef<HTMLInputElement>>('searchField');
  private readonly shortcuts = inject(ShortcutService);
  private readonly injector = inject(Injector);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);

  protected readonly tabs: readonly Tab[] = [
    { status: 'active', label: 'chats.tab.active' },
    { status: 'archived', label: 'chats.tab.archived' },
  ];
  /** The id of the row whose menu is open, or null. */
  protected readonly openMenu = signal<string | null>(null);

  constructor() {
    // A request made before this screen existed is performed once its view is drawn; one made while it is on screen,
    // right away. Either way the request is consumed, so it never fires twice.
    effect(() => {
      const request = this.shortcuts.pending();
      if (request === null) {
        return;
      }
      untracked(() =>
        afterNextRender(
          () => {
            this.shortcuts.consume();
            if (request === 'search') {
              this.searchField()?.nativeElement.select();
              this.searchField()?.nativeElement.focus();
            } else {
              this.openNewChat();
            }
          },
          { injector: this.injector },
        ),
      );
    });
  }

  ngOnInit(): void {
    void this.store.showTab(this.store.tab());
    this.store.follow();
  }

  ngOnDestroy(): void {
    this.store.unfollow();
  }

  ngAfterViewInit(): void {
    // `/chats?scenario=<id>` is where "Inizia conversazione" of a scenario lands: open the picker with it chosen, and
    // drop the parameter so a reload does not open the picker again.
    const scenario = this.route.snapshot.queryParamMap.get('scenario');
    if (scenario !== null) {
      void this.router.navigate([], {
        relativeTo: this.route,
        queryParams: { scenario: null },
        queryParamsHandling: 'merge',
        replaceUrl: true,
      });
      void this.newChat().open(scenario);
    }
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

  /** Esc in the search field clears it and hands focus to the first conversation (or nowhere when the list is empty). */
  protected leaveSearch(event: Event): void {
    const field = event.target as HTMLInputElement;
    if (field.value !== '') {
      field.value = '';
      this.store.search('');
    }
    const first = this.host.nativeElement.querySelector<HTMLElement>('a.chat');
    if (first === null) {
      field.blur();
    } else {
      first.focus();
    }
  }

  protected time(conversation: Conversation): string {
    return formatActivity(conversation.last_message_at ?? conversation.created_at, new Date(), this.translator.locale());
  }

  /** The one-line preview of the newest finished message: the user's own are prefixed, an image without a caption is
   * named. Empty while the chat has no finished message. */
  protected preview(conversation: Conversation): string {
    const last = conversation.last_message;
    if (last === null || last === undefined) {
      return '';
    }
    const text = last.text !== '' ? last.text : last.kind === 'text' ? '' : this.translator.t('chats.preview.image');
    return last.author === 'user' && text !== '' ? this.translator.t('chats.preview.you', { text }) : text;
  }

  protected activityText(activity: ConversationActivity): string {
    return this.translator.t(ACTIVITY_TEXT[activity]);
  }

  protected unreadLabel(conversation: Conversation): string {
    return this.translator.plural('chats.unread', conversation.unread_count);
  }

  protected initial(conversation: Conversation): string {
    return Array.from(conversation.title.trim())[0]?.toLocaleUpperCase(this.translator.locale()) ?? '?';
  }
}
