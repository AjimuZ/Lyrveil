// Screen 2 of docs/frontend.md: the conversation with its messages, live reply, composer and message actions.
import {
  ChangeDetectionStrategy,
  Component,
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
import { Translator } from '../../core/i18n/translator';
import { ConversationStore, isActive } from './conversation-store';
import { MAX_MESSAGE_CHARS, Message } from './conversation.model';
import { dayKey, formatClock, formatDayLabel } from './day-label';

/** The composer shows its counter from this many characters, so it is quiet until the limit is near. */
export const COUNTER_FROM = 7000;

/** How far from the bottom, in pixels, the list still counts as "at the bottom" and follows new text. */
const BOTTOM_SLACK_PX = 80;
/** How close to the top the list loads the next older page. */
const TOP_SLACK_PX = 40;

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
  readonly canEdit: boolean;
  readonly canDelete: boolean;
  readonly canRegenerate: boolean;
  readonly isLast: boolean;
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
 * Bubble actions are edit, regenerate (latest reply), alternatives (latest reply), delete and copy. The context
 * panel, images, search, "Ricorda questo" and virtual scrolling are planned (docs/frontend.md), so the screen draws
 * no control for them.
 */
@Component({
  selector: 'lyr-conversation-screen',
  imports: [RouterLink, TranslatePipe],
  providers: [ConversationStore],
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
  /** Whether new text scrolls into view: true until the user scrolls up. */
  private followBottom = true;

  protected readonly maxChars = MAX_MESSAGE_CHARS;
  protected readonly draft = signal('');
  /** The message being edited and the text in its editor, or null. */
  protected readonly editing = signal<{ readonly id: string; readonly text: string } | null>(null);
  protected readonly confirmingDelete = signal<string | null>(null);
  protected readonly copied = signal(false);

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
    const locale = this.translator.locale();
    const now = new Date();
    const rows: (DayRow | MessageRow)[] = [];
    let currentDay = '';
    messages.forEach((message, index) => {
      const day = dayKey(message.created_at);
      if (day !== '' && day !== currentDay) {
        currentDay = day;
        rows.push({ kind: 'day', key: `day-${day}`, label: formatDayLabel(message.created_at, now, locale) });
      }
      const active = isActive(message);
      const text = active ? (streams[message.id] ?? message.content) : message.content;
      rows.push({
        kind: 'message',
        key: message.id,
        message,
        text,
        clock: formatClock(message.created_at, locale),
        mine: message.author === 'user',
        waiting: active && text === '',
        canEdit: message.author !== 'system' && !active,
        canDelete: !active,
        canRegenerate: latestReply?.id === message.id && !active,
        isLast: index === messages.length - 1,
      });
    });
    return rows;
  });

  constructor() {
    effect(() => {
      const id = this.conversationId();
      untracked(() => void this.store.open(id));
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
    this.followBottom = true;
    void this.store.send(text);
    this.composer()?.nativeElement.focus();
  }

  /** Enter sends; Shift+Enter keeps the browser's new line. Enter that confirms an input-method choice does nothing. */
  protected composerKey(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      this.submit();
    }
  }

  protected typed(event: Event): void {
    this.draft.set((event.target as HTMLTextAreaElement).value);
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
