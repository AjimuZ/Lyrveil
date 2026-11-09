// Screen 2 of docs/frontend.md: the conversation with its messages, live reply, composer and message actions.
import { NgTemplateOutlet } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  afterNextRender,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { RouterLink } from '@angular/router';

import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { ObDialog } from '../../shared/ui/dialog';
import { ObEmptyState } from '../../shared/ui/empty-state';
import { ObIcon } from '../../shared/ui/icon';
import { ObSkeleton } from '../../shared/ui/skeleton';
import { Translator } from '../../core/i18n/translator';
import { Announcer } from '../../core/shell/announcer';
import { WIDE_QUERY, matchesMedia, watchMedia } from '../../core/shell/viewport';
import { ShortcutService } from '../shortcuts/shortcut-service';
import { ChatFind, foldText } from './chat-find';
import { ConversationStore, isActive } from './conversation-store';
import { MAX_MESSAGE_CHARS, Message, imageOriginalUrl, imageThumbnailUrl } from './conversation.model';
import { loadPanelCollapsed, savePanelCollapsed } from './context-panel-storage';
import { dayKey, formatClock, formatDayLabel } from './day-label';
import { loadDraft, saveDraft } from './draft-storage';
import { ImageDialog } from './image-dialog';
import { ImageProgress, PHASE_TEXT, progressFraction } from './image-request';

/** The composer shows its counter from this many characters, so it is quiet until the limit is near. */
export const COUNTER_FROM = 7000;

/** How far from the bottom, in pixels, the list still counts as "at the bottom" and follows new text. */
const BOTTOM_SLACK_PX = 80;
/** How close to the top the list loads the next older page. */
const TOP_SLACK_PX = 40;

/** The statuses of a character message that count as "the character has started answering". */
const STARTED: ReadonlySet<string> = new Set(['streaming', 'complete', 'interrupted']);

/** What the template draws for one message: its text, state and which actions apply. */
export interface MessageRow {
  readonly kind: 'message';
  readonly key: string;
  readonly message: Message;
  /** The text to show: the streamed text while the reply is produced, the stored text otherwise. */
  readonly text: string;
  readonly clock: string;
  readonly mine: boolean;
  /** The reply is produced but nothing has arrived: show the typing indicator or the queue note. */
  readonly waiting: boolean;
  /** The reply is queued behind image work: the queue note says it answers once the image is done. */
  readonly afterImage: boolean;
  readonly canEdit: boolean;
  readonly canDelete: boolean;
  readonly canRegenerate: boolean;
  readonly isLast: boolean;
  /** The user's message shows two checks: the server says `read`, or the character has started answering after it. */
  readonly read: boolean;
  /** The thumbnail and the original of the message's image, or null when it has none (yet). */
  readonly image: { readonly thumbnailUrl: string; readonly originalUrl: string } | null;
  /** The message is an image still being drawn: show its progress instead of the typing indicator. */
  readonly drawing: boolean;
  /** The latest progress of the image being drawn, null until the first event. */
  readonly progress: ImageProgress | null;
  /** The share of the current pass that is done, 0 to 100, or null when the phase has no count. */
  readonly percent: number | null;
}

export interface DayRow {
  readonly kind: 'day';
  readonly key: string;
  readonly label: string;
}

/**
 * The conversation screen. It opens the chat named by the route, follows it live through the store, and draws the
 * messages grouped under day separators. Message text is always bound as text, never as markup (T-SEC-03).
 *
 * Bubble actions are edit, regenerate (latest text reply), alternatives (latest text reply), delete and copy. The
 * composer's image button opens the image form; an image message shows its thumbnail, and while it is drawn the phase
 * and the step count of the current pass. The text typed and not sent is kept per chat (`draft-storage.ts`), the
 * user's messages show one check or two (the character started answering), and the newest finished reply is reported
 * read to the server. The lightbox, regenerating an image, cancelling a draw, search, "Ricorda questo" and virtual
 * scrolling are planned (docs/frontend.md), so the screen draws no control for them.
 *
 * The context panel (P08.2) holds links to the character's profile and Memory Studio, the part of the panel that
 * other screens back today. From 1024 px it is a collapsible 320 px column beside the messages, its state remembered
 * per browser; below that the same content opens in a dialog on request.
 *
 * Keyboard and assistive technology (P08.3): the messages are a `role="log"` that is not live itself, so a streaming
 * reply is never read piece by piece; the finished reply is announced once through `Announcer`. Ctrl+F opens a find bar
 * over the loaded messages, ArrowUp in an empty composer edits the user's latest message, and Esc leaves the find bar,
 * the editor or a delete confirmation.
 */
@Component({
  selector: 'lyr-conversation-screen',
  imports: [
    RouterLink,
    NgTemplateOutlet,
    TranslatePipe,
    ChatFind,
    ImageDialog,
    ObDialog,
    ObEmptyState,
    ObIcon,
    ObSkeleton,
  ],
  providers: [ConversationStore],
  host: { '(document:visibilitychange)': 'becameVisible()', '(keydown.escape)': 'escape($event)' },
  templateUrl: './conversation-screen.html',
  styleUrl: './conversation-screen.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ConversationScreen {
  /** The `:conversationId` of the route, bound by the router. */
  readonly conversationId = input.required<string>();

  protected readonly store = inject(ConversationStore);
  private readonly translator = inject(Translator);
  private readonly injector = inject(Injector);
  private readonly scroller = viewChild<ElementRef<HTMLElement>>('scroller');
  private readonly composer = viewChild<ElementRef<HTMLTextAreaElement>>('composer');
  private readonly imageDialog = viewChild.required(ImageDialog);
  private readonly editor = viewChild<ElementRef<HTMLTextAreaElement>>('editor');
  private readonly finder = viewChild(ChatFind);
  private readonly shortcuts = inject(ShortcutService);
  private readonly announcer = inject(Announcer);
  /** Whether new text scrolls into view: true until the user scrolls up. */
  private followBottom = true;
  /** The id of the newest finished reply already announced; undefined until the chat has loaded (history is no news). */
  private announcedReply: string | null | undefined = undefined;

  protected readonly maxChars = MAX_MESSAGE_CHARS;
  protected readonly draft = signal('');
  /** The message being edited and the text in its editor, or null. */
  protected readonly editing = signal<{ readonly id: string; readonly text: string } | null>(null);
  protected readonly confirmingDelete = signal<string | null>(null);
  protected readonly copied = signal(false);

  /** The find bar (Ctrl+F) is open. */
  protected readonly findOpen = signal(false);
  protected readonly findQuery = signal('');
  /** The 0-based place of the current match, or null for the newest one (a chat is read from the bottom). */
  private readonly findPosition = signal<number | null>(null);

  /** The desktop layout applies (1024 px and up): the context panel is a column rather than a dialog. */
  protected readonly wide = signal(matchesMedia(WIDE_QUERY));
  /** The user collapsed the desktop column; remembered per browser. */
  protected readonly panelCollapsed = signal(loadPanelCollapsed());
  /** The panel is open as a dialog (tablet and mobile). Not remembered: the dialog is for a single look. */
  protected readonly panelDialogOpen = signal(false);
  /** What the header button reports as expanded, for the layout in use. */
  protected readonly panelShown = computed(() => (this.wide() ? !this.panelCollapsed() : this.panelDialogOpen()));

  protected readonly length = computed(() => this.draft().length);
  protected readonly tooLong = computed(() => this.length() > MAX_MESSAGE_CHARS);
  protected readonly canSend = computed(() => this.draft().trim() !== '' && !this.tooLong());
  protected readonly showCounter = computed(() => this.length() >= COUNTER_FROM);
  protected readonly name = computed(() => this.store.conversation()?.character_name ?? '');
  protected readonly initial = computed(
    () => Array.from(this.name().trim())[0]?.toLocaleUpperCase(this.translator.locale()) ?? '?',
  );

  /** Day separators and messages in display order. */
  protected readonly rows = computed<readonly (DayRow | MessageRow)[]>(() => {
    const messages = this.store.messages();
    const streams = this.store.streams();
    const latestReply = this.store.latestReply();
    const imageProgress = this.store.imageProgress();
    const waitingForImage = this.store.waitingForImage();
    const locale = this.translator.locale();
    const now = new Date();
    const rows: (DayRow | MessageRow)[] = [];
    // The simulated read state follows the live events too, so it does not wait for a reload: the same rule as the
    // server's (lyrveil.persistence.messages), a later character message that is streaming or finished.
    const answeredUpTo = messages.reduce(
      (latest, m) => (m.author === 'character' && STARTED.has(m.status) ? Math.max(latest, m.seq) : latest),
      0,
    );
    let currentDay = '';
    messages.forEach((message, index) => {
      const day = dayKey(message.created_at);
      if (day !== '' && day !== currentDay) {
        currentDay = day;
        rows.push({ kind: 'day', key: `day-${day}`, label: formatDayLabel(message.created_at, now, locale) });
      }
      const active = isActive(message);
      const text = active ? (streams[message.id] ?? message.content) : message.content;
      const drawing = active && message.kind === 'image';
      const progress = drawing ? (imageProgress[message.id] ?? null) : null;
      const fraction = progress === null ? null : progressFraction(progress);
      rows.push({
        kind: 'message',
        key: message.id,
        message,
        text,
        clock: formatClock(message.created_at, locale),
        mine: message.author === 'user',
        waiting: active && text === '',
        afterImage: waitingForImage.has(message.id),
        canEdit: message.author !== 'system' && !active,
        canDelete: !active,
        // Regenerating an image is a gallery action (P05.6); the text route does not apply to it.
        canRegenerate: latestReply?.id === message.id && !active && message.kind !== 'image' && message.kind !== 'mixed',
        isLast: index === messages.length - 1,
        read: message.author === 'user' && (message.read_state === 'read' || message.seq < answeredUpTo),
        image:
          typeof message.image_id === 'string'
            ? { thumbnailUrl: imageThumbnailUrl(message.image_id), originalUrl: imageOriginalUrl(message.image_id) }
            : null,
        drawing,
        progress,
        percent: fraction === null ? null : Math.round(fraction * 100),
      });
    });
    return rows;
  });

  /** The keys of the messages that contain the find text, in display order. Empty while the bar is closed. */
  protected readonly matches = computed<readonly string[]>(() => {
    const locale = this.translator.locale();
    const needle = this.findOpen() ? foldText(this.findQuery().trim(), locale) : '';
    if (needle === '') {
      return [];
    }
    return this.rows().flatMap((row) => (row.kind === 'message' && foldText(row.text, locale).includes(needle) ? [row.key] : []));
  });
  protected readonly matchSet = computed(() => new Set(this.matches()));
  /** The 0-based place of the current match, -1 when there is none. */
  protected readonly matchIndex = computed(() => {
    const count = this.matches().length;
    const position = this.findPosition();
    return count === 0 ? -1 : position === null ? count - 1 : Math.min(position, count - 1);
  });
  protected readonly currentMatch = computed(() => this.matches()[this.matchIndex()] ?? null);

  /** The id of the message being edited, so focus follows the editor opening and closing, not each keystroke. */
  private readonly editingId = computed(() => this.editing()?.id ?? null);

  constructor() {
    // Ctrl+F inside the chat opens the find bar (or returns to its field when it is already open).
    let seenFindRequests = this.shortcuts.findRequests();
    effect(() => {
      const requests = this.shortcuts.findRequests();
      untracked(() => {
        if (requests === seenFindRequests) {
          return;
        }
        seenFindRequests = requests;
        this.findOpen.set(true);
        afterNextRender(() => this.finder()?.focus(), { injector: this.injector });
      });
    });
    // The current match scrolls into view, which also ends following the bottom.
    effect(() => {
      const key = this.currentMatch();
      if (key === null) {
        return;
      }
      this.followBottom = false;
      afterNextRender(() => document.getElementById(`msg-${key}`)?.scrollIntoView?.({ block: 'center' }), {
        injector: this.injector,
      });
    });
    // Opening the editor puts the caret at the end of the text; closing it returns focus to the composer.
    let wasEditing = false;
    effect(() => {
      const editing = this.editingId() !== null;
      untracked(() => {
        if (editing) {
          afterNextRender(
            () => {
              const field = this.editor()?.nativeElement;
              field?.focus();
              field?.setSelectionRange(field.value.length, field.value.length);
            },
            { injector: this.injector },
          );
        } else if (wasEditing) {
          this.composer()?.nativeElement.focus();
        }
        wasEditing = editing;
      });
    });
    // A reply that finishes is announced once, whole. Streaming text is not (the log is `aria-live="off"`), and the
    // history that is on screen when the chat opens is not news, so the first loaded state only sets the baseline.
    effect(() => {
      const loading = this.store.loading();
      const latest = this.store
        .messages()
        .filter((message) => message.author === 'character' && message.status === 'complete')
        .at(-1);
      untracked(() => {
        if (loading) {
          return;
        }
        if (this.announcedReply === undefined) {
          this.announcedReply = latest?.id ?? null;
        } else if (latest !== undefined && latest.id !== this.announcedReply) {
          this.announcedReply = latest.id;
          const text = latest.content.trim() !== '' ? latest.content : this.imageAlt();
          this.announcer.announce(this.translator.t('conv.announce.reply', { name: this.name(), text }));
        }
      });
    });
    // The panel changes form with the width; a dialog left open while the window grows would otherwise disappear.
    const stopWatching = watchMedia(WIDE_QUERY, (matches) => {
      this.wide.set(matches);
      this.panelDialogOpen.set(false);
    });
    inject(DestroyRef).onDestroy(stopWatching);
    effect(() => {
      const id = this.conversationId();
      untracked(() => {
        this.announcedReply = undefined;
        this.closeFind(false);
        this.draft.set(loadDraft(id));
        void this.store.open(id);
      });
    });
    // Reading: once the newest finished reply is on screen the chat counts as read, so its unread count clears in the
    // list. A reply that arrives while the tab is hidden is marked when the tab is shown again (`becameVisible`).
    effect(() => {
      this.store.newestFinishedReply();
      untracked(() => void this.store.markRead());
    });
    // New rows, a growing reply and a pending message scroll into view while the user stays at the bottom.
    effect(() => {
      this.rows();
      this.store.pending();
      if (this.followBottom) {
        afterNextRender(() => this.scrollToBottom(), { injector: this.injector });
      }
    });
  }

  /** The header button: collapses or restores the desktop column, or opens the dialog on a narrower screen. */
  protected togglePanel(): void {
    if (this.wide()) {
      const collapsed = !this.panelCollapsed();
      this.panelCollapsed.set(collapsed);
      savePanelCollapsed(collapsed);
    } else {
      this.panelDialogOpen.set(true);
    }
  }

  protected onScroll(): void {
    const element = this.scroller()?.nativeElement;
    if (element === undefined) {
      return;
    }
    this.followBottom = element.scrollHeight - element.scrollTop - element.clientHeight <= BOTTOM_SLACK_PX;
    if (element.scrollTop <= TOP_SLACK_PX && this.store.hasOlder()) {
      void this.older();
    }
  }

  /** Loads the next older page and keeps the message the user was reading where it was. */
  protected async older(): Promise<void> {
    const element = this.scroller()?.nativeElement;
    const fromBottom = element === undefined ? 0 : element.scrollHeight - element.scrollTop;
    this.followBottom = false;
    await this.store.loadOlder();
    afterNextRender(
      () => {
        if (element !== undefined) {
          element.scrollTop = element.scrollHeight - fromBottom;
        }
      },
      { injector: this.injector },
    );
  }

  protected submit(): void {
    if (!this.canSend()) {
      return;
    }
    const text = this.draft();
    this.draft.set('');
    saveDraft(this.conversationId(), '');
    this.followBottom = true;
    void this.store.send(text);
    this.composer()?.nativeElement.focus();
  }

  /**
   * Enter sends; Shift+Enter keeps the browser's new line. Enter that confirms an input-method choice does nothing.
   * ArrowUp in an empty composer opens the editor of the user's latest editable message (docs/frontend.md, Keyboard).
   */
  protected composerKey(event: KeyboardEvent): void {
    if (event.isComposing) {
      return;
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.submit();
    } else if (event.key === 'ArrowUp' && !event.shiftKey && !event.altKey && !event.ctrlKey && !event.metaKey) {
      const last = [...this.rows()]
        .reverse()
        .find((row) => row.kind === 'message' && row.mine && row.canEdit && row.text !== '');
      if (this.draft() === '' && last?.kind === 'message') {
        event.preventDefault();
        this.startEdit(last.message);
      }
    }
  }

  /** Esc leaves the find bar, then the editor, then a delete confirmation. A dialog handles its own Esc. */
  protected escape(event: Event): void {
    if (event.defaultPrevented || (event.target instanceof Element && event.target.closest('dialog') !== null)) {
      return;
    }
    if (this.findOpen()) {
      this.closeFind(true);
    } else if (this.editing() !== null) {
      this.cancelEdit();
    } else if (this.confirmingDelete() !== null) {
      this.cancelDelete();
    }
  }

  protected onFindQuery(text: string): void {
    this.findQuery.set(text);
    this.findPosition.set(null);
  }

  /** Moves to the next (1) or previous (-1) match, wrapping around the ends. */
  protected stepFind(delta: -1 | 1): void {
    const count = this.matches().length;
    if (count > 0) {
      this.findPosition.set((this.matchIndex() + delta + count) % count);
    }
  }

  /** Closes the find bar and forgets its text; `refocus` returns the caret to the composer. */
  protected closeFind(refocus: boolean): void {
    this.findOpen.set(false);
    this.findQuery.set('');
    this.findPosition.set(null);
    if (refocus) {
      this.composer()?.nativeElement.focus();
    }
  }

  protected typed(event: Event): void {
    const text = (event.target as HTMLTextAreaElement).value;
    this.draft.set(text);
    saveDraft(this.conversationId(), text);
  }

  /** The tab was shown again: replies that arrived while it was hidden are now read. */
  protected becameVisible(): void {
    void this.store.markRead();
  }

  protected startEdit(message: Message): void {
    this.confirmingDelete.set(null);
    this.editing.set({ id: message.id, text: message.content });
  }

  protected editTyped(event: Event): void {
    const current = this.editing();
    if (current !== null) {
      this.editing.set({ ...current, text: (event.target as HTMLTextAreaElement).value });
    }
  }

  protected cancelEdit(): void {
    this.editing.set(null);
  }

  protected async saveEdit(message: Message): Promise<void> {
    const current = this.editing();
    if (current === null || current.text.trim() === '') {
      return;
    }
    if (await this.store.edit(message, current.text)) {
      this.editing.set(null);
    }
  }

  /** The edit warning applies to any message that is not the newest one. */
  protected isOlder(message: Message): boolean {
    return this.store.messages().at(-1)?.id !== message.id;
  }

  protected askDelete(message: Message): void {
    this.editing.set(null);
    this.confirmingDelete.set(message.id);
  }

  protected cancelDelete(): void {
    this.confirmingDelete.set(null);
  }

  protected async confirmDelete(message: Message): Promise<void> {
    this.confirmingDelete.set(null);
    await this.store.remove(message);
  }

  /** Copies the text; a browser that refuses the clipboard just shows no confirmation. */
  protected async copy(text: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      return;
    }
    this.copied.set(true);
    setTimeout(() => this.copied.set(false), 2500);
  }

  protected alternativeLabel(index: number, count: number): string {
    return this.translator.t('conv.alt.position', { index: index + 1, count });
  }

  protected counter(): string {
    return this.translator.t('conv.composer.counter', { count: this.length(), max: MAX_MESSAGE_CHARS });
  }

  /** Opens the image form; the text already typed in the composer becomes its request. */
  protected openImageForm(): void {
    void this.imageDialog().open(this.draft().trim());
  }

  protected phaseText(progress: ImageProgress): string {
    return this.translator.t(PHASE_TEXT[progress.phase]);
  }

  protected stepText(progress: ImageProgress): string {
    return this.translator.t('conv.image.step', { value: progress.value ?? 0, max: progress.max ?? 0 });
  }

  protected imageAlt(): string {
    return this.translator.t('conv.image.alt', { name: this.name() });
  }

  protected typingLabel(): string {
    return this.translator.t('conv.typing', { name: this.name() });
  }

  private scrollToBottom(): void {
    const element = this.scroller()?.nativeElement;
    if (element !== undefined) {
      element.scrollTop = element.scrollHeight;
    }
  }
}
