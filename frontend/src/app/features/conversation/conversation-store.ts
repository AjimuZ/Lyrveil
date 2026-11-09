// State of one open conversation: its messages, the reply that streams in, sending, edit, delete, variants and paging.
import { DestroyRef, Injectable, computed, inject, signal } from '@angular/core';
import { Subscription, firstValueFrom } from 'rxjs';

import { ApiClient } from '../../core/api/api-client';
import { ApiError, toApiError } from '../../core/api/api-error';
import { DataEventType, EventsSocket, ServerEvent } from '../../core/realtime/events-socket';
import { Outfit, StoredCharacter } from '../characters/card.model';
import { Conversation } from '../chats/chats.model';
import { ImageProgress, ImageRequestBody, toImageProgress } from './image-request';
import {
  Message,
  MessagePage,
  PendingSend,
  RegenerateResult,
  SendResult,
  Variant,
  VariantList,
} from './conversation.model';

/** How many messages one page of history holds. */
export const PAGE_SIZE = 50;

/** How long the store waits before it asks REST for the newest messages while events cannot be trusted. */
export const CATCH_UP_MS = 2000;

/** A piece of a streaming reply: `offset` counts UTF-16 code units of the text before it. */
interface Delta {
  readonly messageId: string;
  readonly offset: number;
  readonly text: string;
}

/**
 * Returns `text` with the delta that starts at `offset` applied, or null when the delta starts beyond the end of
 * `text`, which means pieces are missing. A delta that overlaps the end adds only its new tail, and one that lies
 * inside `text` changes nothing, so a replay that repeats a delta is harmless.
 */
export function applyDelta(text: string, offset: number, delta: string): string | null {
  if (offset > text.length) {
    return null;
  }
  if (offset + delta.length <= text.length) {
    return text;
  }
  return text + delta.slice(text.length - offset);
}

/** True while the server still produces the message, so its text is not final. */
export function isActive(message: Message): boolean {
  return message.status === 'queued' || message.status === 'streaming';
}

/** The `label_key`s of `gpu.state` that mean the GPU is busy with an image (docs/gpu-orchestration.md, Labels). */
const IMAGE_WORK_LABELS: ReadonlySet<string> = new Set(['gpu.state.waiting', 'gpu.state.drawing']);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Returns the message in an event payload, or null when the payload lacks the fields the screen relies on. */
function toMessage(value: unknown): Message | null {
  if (!isRecord(value)) {
    return null;
  }
  const { id, conversation_id: conversationId, seq, status, content } = value;
  if (typeof id !== 'string' || typeof conversationId !== 'string' || typeof seq !== 'number') {
    return null;
  }
  if (typeof status !== 'string' || typeof content !== 'string') {
    return null;
  }
  return value as unknown as Message;
}

/**
 * Holds what one conversation screen shows. Provide it on the screen (`providers: [ConversationStore]`) so each open
 * chat starts clean, and call `open` with the chat's id. Reads are `Readonly` signals; every method is an intent.
 *
 * REST is the source of truth. The store applies the `message.*` events of the socket for speed, and reloads the
 * newest page through REST whenever it cannot trust them: after `resync.required`, while the socket is not open and a
 * reply is still being produced, and while pieces of a streaming reply are missing. That last case is the page
 * reload during streaming: the text REST returns is at most one flush interval behind, so the live pieces that
 * start beyond it wait in `gaps` until a reload brings the text up to them.
 *
 * An answer that arrives after `open` was called again for another chat is dropped.
 */
@Injectable()
export class ConversationStore {
  private readonly api = inject(ApiClient);
  private readonly socket = inject(EventsSocket);

  private conversationId = '';
  private generation = 0;
  /** The highest `seq` already reported to the server as read in this chat. */
  private reportedRead = 0;
  private subscriptions: Subscription[] = [];
  private inbox: Delta[] = [];
  private gaps = new Map<string, Delta[]>();
  private cancelFrame: (() => void) | null = null;
  private catchUpTimer: ReturnType<typeof setTimeout> | null = null;

  private readonly conversationState = signal<Conversation | null>(null);
  private readonly messagesState = signal<readonly Message[]>([]);
  private readonly nextBeforeState = signal<number | null>(null);
  private readonly loadingState = signal(true);
  private readonly loadingOlderState = signal(false);
  private readonly errorState = signal<ApiError | null>(null);
  private readonly actionErrorState = signal<ApiError | null>(null);
  private readonly streamsState = signal<Readonly<Record<string, string>>>({});
  private readonly typingState = signal(false);
  private readonly pendingState = signal<readonly PendingSend[]>([]);
  private readonly variantsState = signal<{ readonly messageId: string; readonly items: readonly Variant[] } | null>(null);
  private readonly imageProgressState = signal<Readonly<Record<string, ImageProgress>>>({});
  /** Whether the last `gpu.state` reported image work. It is global, so it survives a change of chat. */
  private readonly gpuImageWorkState = signal(false);

  readonly conversation = this.conversationState.asReadonly();
  /** The loaded messages, ascending by `seq`. Text of a reply still being produced is in `streams`. */
  readonly messages = this.messagesState.asReadonly();
  /** True until the first page arrives. */
  readonly loading = this.loadingState.asReadonly();
  readonly loadingOlder = this.loadingOlderState.asReadonly();
  /** Why the conversation could not be opened, or null. */
  readonly error = this.errorState.asReadonly();
  /** The failure of the last edit, delete, regenerate, variant change or page load, or null. */
  readonly actionError = this.actionErrorState.asReadonly();
  /** The text received so far of each reply being produced, by message id. */
  readonly streams = this.streamsState.asReadonly();
  /** Messages the user sent that the server has not confirmed, or whose send failed. */
  readonly pending = this.pendingState.asReadonly();
  /** True when older messages exist on the server. */
  readonly hasOlder = computed(() => this.nextBeforeState() !== null);
  /** The last message when a character wrote it: the only one that can be regenerated or have alternatives. */
  readonly latestReply = computed(() => {
    const last = this.messagesState().at(-1);
    return last?.author === 'character' ? last : null;
  });
  /** The `seq` of the newest character message that has finished (0 when none): what reading the chat covers. */
  readonly newestFinishedReply = computed(() =>
    this.messagesState().reduce(
      (latest, m) =>
        m.author === 'character' && (m.status === 'complete' || m.status === 'interrupted') ? Math.max(latest, m.seq) : latest,
      0,
    ),
  );
  /** True while the character is generating: a `typing` event, or a reply the server marks streaming. */
  readonly typing = computed(() => this.typingState() || this.messagesState().some((m) => m.status === 'streaming'));
  /** True while the newest reply waits in the queue. */
  readonly queued = computed(() => this.messagesState().some((m) => m.status === 'queued'));
  /** The latest progress of each image being drawn, by message id. It is dropped once the message is final. */
  readonly imageProgress = this.imageProgressState.asReadonly();
  /** True while an image of this chat reports progress, so the header can say the character is drawing. */
  readonly drawing = computed(() => {
    const progress = this.imageProgressState();
    return this.messagesState().some((m) => m.kind === 'image' && isActive(m) && progress[m.id] !== undefined);
  });
  /**
   * The ids of queued text replies that wait for image work (T-ORC-13): a reply queued after an image of this chat
   * that is still being made, or any queued reply while `gpu.state` reports image work. The server answers them once
   * the text model is back, so the bubble says so instead of the plain queue note.
   */
  readonly waitingForImage = computed<ReadonlySet<string>>(() => {
    const gpuBusy = this.gpuImageWorkState();
    const ids = new Set<string>();
    let imageBefore = false;
    for (const m of this.messagesState()) {
      if (m.kind === 'image' && isActive(m)) {
        imageBefore = true;
      } else if (m.author === 'character' && m.kind === 'text' && m.status === 'queued' && (imageBefore || gpuBusy)) {
        ids.add(m.id);
      }
    }
    return ids;
  });
  /** The position among the alternatives of the latest reply, or null when it has none to choose from. */
  readonly alternatives = computed(() => {
    const variants = this.variantsState();
    const reply = this.latestReply();
    if (variants === null || reply === null || variants.messageId !== reply.id || isActive(reply)) {
      return null;
    }
    const index = variants.items.findIndex((variant) => variant.id === reply.active_variant_id);
    return index < 0 ? null : { index, count: variants.items.length };
  });

  constructor() {
    inject(DestroyRef).onDestroy(() => this.dispose());
  }

  /** Loads the chat and its newest page, connects the socket and starts following events. Safe to call again. */
  async open(conversationId: string): Promise<void> {
    this.reset(conversationId);
    const ticket = this.generation;
    this.listen();
    this.socket.connect();
    try {
      const [conversation, page] = await Promise.all([
        firstValueFrom(this.api.get<Conversation>(`/conversations/${conversationId}`)),
        firstValueFrom(this.api.get<MessagePage>(`/conversations/${conversationId}/messages`, { limit: PAGE_SIZE })),
      ]);
      if (ticket !== this.generation) {
        return;
      }
      this.conversationState.set(conversation);
      this.mergeNewest(page);
      this.typingState.set(false);
      this.afterChange();
    } catch (failure) {
      if (ticket === this.generation) {
        this.errorState.set(toApiError(failure));
      }
    } finally {
      if (ticket === this.generation) {
        this.loadingState.set(false);
      }
    }
  }

  /**
   * Tells the server the user has read the chat up to its newest finished reply, which clears the chat's unread count.
   * Does nothing while nothing new was finished since the last mark, or while the page is hidden (a reply that arrives
   * in a background tab stays unread until the tab is shown). A failed mark is forgotten, so the next call retries it.
   * Never rejects.
   */
  async markRead(): Promise<void> {
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
      return;
    }
    const upTo = this.newestFinishedReply();
    if (upTo <= this.reportedRead) {
      return;
    }
    const ticket = this.generation;
    const before = this.reportedRead;
    this.reportedRead = upTo;
    try {
      const updated = await firstValueFrom(
        this.api.post<Conversation>(`/conversations/${this.conversationId}/read`, { up_to_seq: upTo }),
      );
      if (ticket === this.generation) {
        this.conversationState.set(updated);
      }
    } catch {
      if (ticket === this.generation && this.reportedRead === upTo) {
        this.reportedRead = before;
      }
    }
  }

  /** Opens the chat again after a failed first load. */
  retryOpen(): Promise<void> {
    return this.open(this.conversationId);
  }

  /** Reloads the newest page through REST and settles the streaming state against it. Never rejects. */
  async refresh(): Promise<void> {
    const ticket = this.generation;
    try {
      const page = await firstValueFrom(
        this.api.get<MessagePage>(`/conversations/${this.conversationId}/messages`, { limit: PAGE_SIZE }),
      );
      if (ticket === this.generation) {
        this.mergeNewest(page);
        this.typingState.set(false);
        this.afterChange();
      }
    } catch {
      // The messages on screen stay; the next trigger tries again.
    }
  }

  /** Adds the page of messages before the oldest one loaded. */
  async loadOlder(): Promise<void> {
    const before = this.nextBeforeState();
    if (before === null || this.loadingOlderState()) {
      return;
    }
    const ticket = this.generation;
    this.loadingOlderState.set(true);
    try {
      const page = await firstValueFrom(
        this.api.get<MessagePage>(`/conversations/${this.conversationId}/messages`, { before_seq: before, limit: PAGE_SIZE }),
      );
      if (ticket === this.generation) {
        const known = new Set(page.items.map((item) => item.id));
        this.messagesState.update((list) => [...page.items, ...list.filter((item) => !known.has(item.id))]);
        this.nextBeforeState.set(page.next_before_seq);
      }
    } catch (failure) {
      if (ticket === this.generation) {
        this.actionErrorState.set(toApiError(failure));
      }
    } finally {
      if (ticket === this.generation) {
        this.loadingOlderState.set(false);
      }
    }
  }

  /**
   * Sends `content` as a new message. It shows at once as pending; a failure keeps it there with the reason, so
   * nothing typed is lost, and `retry` sends it again with the same client id, which the server treats as the same
   * message.
   */
  send(content: string): Promise<void> {
    const entry: PendingSend = {
      clientMessageId: crypto.randomUUID(),
      content,
      state: 'sending',
      error: null,
      createdAt: new Date().toISOString(),
    };
    this.pendingState.update((list) => [...list, entry]);
    return this.transmit(entry);
  }

  /** Sends a failed pending message again. */
  retry(clientMessageId: string): Promise<void> {
    const entry = this.pendingState().find((item) => item.clientMessageId === clientMessageId);
    if (entry === undefined || entry.state !== 'failed') {
      return Promise.resolve();
    }
    const sending: PendingSend = { ...entry, state: 'sending', error: null };
    this.replacePending(sending);
    return this.transmit(sending);
  }

  /** Drops a failed pending message the user does not want to send. */
  discard(clientMessageId: string): void {
    this.pendingState.update((list) => list.filter((item) => item.clientMessageId !== clientMessageId));
  }

  /** Replaces the text of a message with a new `edited` variant. Resolves false and sets `actionError` on failure. */
  edit(message: Message, content: string): Promise<boolean> {
    return this.act(async () => {
      this.receive(await firstValueFrom(this.api.patch<Message>(`/messages/${message.id}`, { content })));
    });
  }

  /** Deletes a message. Resolves false and sets `actionError` on failure. */
  remove(message: Message): Promise<boolean> {
    return this.act(async () => {
      await firstValueFrom(this.api.delete<null>(`/messages/${message.id}`));
      this.dropMessage(message.id);
    });
  }

  /** Asks for another version of the latest reply; it streams in as the active variant. */
  regenerate(message: Message): Promise<boolean> {
    return this.act(async () => {
      const result = await firstValueFrom(this.api.post<RegenerateResult>(`/messages/${message.id}/regenerate`));
      this.receive(result.message);
    });
  }

  /** Shows the previous (-1) or next (1) alternative of the latest reply that can be shown. */
  stepAlternative(direction: -1 | 1): Promise<boolean> {
    const reply = this.latestReply();
    const variants = this.variantsState();
    if (reply === null || variants === null || variants.messageId !== reply.id) {
      return Promise.resolve(false);
    }
    const start = variants.items.findIndex((variant) => variant.id === reply.active_variant_id);
    // A variant still streaming cannot be shown (the server answers 409), so the step skips it.
    let index = start + direction;
    while (index >= 0 && index < variants.items.length && variants.items[index]?.status === 'streaming') {
      index += direction;
    }
    const target = variants.items[index];
    if (start < 0 || target === undefined) {
      return Promise.resolve(false);
    }
    return this.act(async () => {
      this.receive(
        await firstValueFrom(this.api.post<Message>(`/messages/${reply.id}/variants/${target.id}/activate`)),
      );
    });
  }

  clearActionError(): void {
    this.actionErrorState.set(null);
  }

  /**
   * Asks for an image with `POST /conversations/{id}/images`. The user's request and the image placeholder join the
   * list from the answer; the progress then arrives as `image.progress` events. Resolves null on success and the
   * failure otherwise, which the image dialog shows itself, so `actionError` is left alone.
   */
  async requestImage(body: ImageRequestBody): Promise<ApiError | null> {
    const ticket = this.generation;
    try {
      const result = await firstValueFrom(this.api.post<SendResult>(`/conversations/${this.conversationId}/images`, body));
      if (ticket === this.generation) {
        this.receive(result.message);
        this.receive(result.reply);
      }
      return null;
    } catch (failure) {
      return toApiError(failure);
    }
  }

  /**
   * Returns the named outfits of the chat's character, for the image form. A character whose card cannot be read
   * offers none: the form still has the scene's choice and the usual outfit.
   */
  async outfits(): Promise<readonly Outfit[]> {
    const characterId = this.conversationState()?.character_id;
    if (characterId === undefined) {
      return [];
    }
    try {
      const character = await firstValueFrom(
        this.api.get<StoredCharacter>(`/characters/${encodeURIComponent(characterId)}`),
      );
      return character.card.visual.outfits;
    } catch {
      return [];
    }
  }

  private async transmit(entry: PendingSend): Promise<void> {
    const ticket = this.generation;
    try {
      const result = await firstValueFrom(
        this.api.post<SendResult>(`/conversations/${this.conversationId}/messages`, {
          client_message_id: entry.clientMessageId,
          content: entry.content,
          intent: 'auto',
        }),
      );
      if (ticket !== this.generation) {
        return;
      }
      this.discard(entry.clientMessageId);
      this.receive(result.message);
      this.receive(result.reply);
    } catch (failure) {
      if (ticket === this.generation) {
        this.replacePending({ ...entry, state: 'failed', error: toApiError(failure) });
      }
    }
  }

  private replacePending(entry: PendingSend): void {
    this.pendingState.update((list) => list.map((item) => (item.clientMessageId === entry.clientMessageId ? entry : item)));
  }

  private async act(work: () => Promise<void>): Promise<boolean> {
    const ticket = this.generation;
    this.actionErrorState.set(null);
    try {
      await work();
      return true;
    } catch (failure) {
      if (ticket === this.generation) {
        this.actionErrorState.set(toApiError(failure));
      }
      return false;
    }
  }

  private reset(conversationId: string): void {
    this.stopListening();
    this.generation += 1;
    this.conversationId = conversationId;
    this.reportedRead = 0;
    this.inbox = [];
    this.gaps.clear();
    this.conversationState.set(null);
    this.messagesState.set([]);
    this.nextBeforeState.set(null);
    this.loadingState.set(true);
    this.loadingOlderState.set(false);
    this.errorState.set(null);
    this.actionErrorState.set(null);
    this.streamsState.set({});
    this.typingState.set(false);
    this.pendingState.set([]);
    this.variantsState.set(null);
    this.imageProgressState.set({});
  }

  private dispose(): void {
    this.stopListening();
    this.generation += 1;
  }

  private stopListening(): void {
    for (const subscription of this.subscriptions) {
      subscription.unsubscribe();
    }
    this.subscriptions = [];
    this.cancelFrame?.();
    this.cancelFrame = null;
    if (this.catchUpTimer !== null) {
      clearTimeout(this.catchUpTimer);
      this.catchUpTimer = null;
    }
  }

  private listen(): void {
    const on = <T extends DataEventType>(type: T, handler: (event: ServerEvent<T>) => void): void => {
      this.subscriptions.push(this.socket.on(type).subscribe(handler));
    };
    const message = (event: ServerEvent) => this.receive(toMessage(event.data['message']));
    on('message.created', message);
    on('message.updated', message);
    on('message.completed', (event) => {
      this.endStream(this.messageId(event.data['message']));
      message(event);
    });
    on('message.failed', (event) => this.markFailed(event.data['message_id']));
    on('message.deleted', (event) => {
      // The payload carries either the id or the whole message (docs/api.md, Event WebSocket).
      const id = event.data['message_id'] ?? this.messageId(event.data['message']);
      if (typeof id === 'string') {
        this.dropMessage(id);
      }
    });
    on('message.delta', (event) => this.queueDelta(event.data));
    on('message.restarted', (event) => this.restart(event.data['message_id']));
    on('image.progress', (event) => this.setImageProgress(event.data));
    on('image.completed', (event) => this.endImage(event.data['message_id']));
    on('typing.started', (event) => this.setTyping(event.data, true));
    on('typing.stopped', (event) => this.setTyping(event.data, false));
    on('gpu.state', (event) => {
      const label = event.data['label_key'];
      this.gpuImageWorkState.set(typeof label === 'string' && IMAGE_WORK_LABELS.has(label));
    });
    this.subscriptions.push(this.socket.resync$.subscribe(() => void this.refresh()));
  }

  private messageId(value: unknown): string | null {
    return isRecord(value) && typeof value['id'] === 'string' ? value['id'] : null;
  }

  /**
   * Keeps the latest progress of an image of this chat. An event for a message that is already final, or for no
   * message (character mode), changes nothing; one for a message not loaded yet is kept, because the answer of the
   * request can arrive after the first events.
   */
  private setImageProgress(data: Readonly<Record<string, unknown>>): void {
    const id = data['message_id'];
    const progress = toImageProgress(data);
    if (typeof id !== 'string' || progress === null) {
      return;
    }
    const known = this.messagesState().find((item) => item.id === id);
    if (known !== undefined && !isActive(known)) {
      return;
    }
    this.imageProgressState.update((all) => ({ ...all, [id]: progress }));
  }

  /** Forgets the progress of an image whose message is final. */
  private endImage(id: unknown): void {
    if (typeof id === 'string' && this.imageProgressState()[id] !== undefined) {
      this.imageProgressState.set(Object.fromEntries(Object.entries(this.imageProgressState()).filter(([key]) => key !== id)));
    }
  }

  private setTyping(data: Readonly<Record<string, unknown>>, typing: boolean): void {
    if (data['conversation_id'] === this.conversationId) {
      this.typingState.set(typing);
    }
  }

  /** Puts a message of this chat into the list, in `seq` order, replacing the one with the same id. */
  private receive(message: Message | null): void {
    if (message === null || message.conversation_id !== this.conversationId) {
      return;
    }
    this.messagesState.update((list) => [...list.filter((item) => item.id !== message.id), message].sort((a, b) => a.seq - b.seq));
    if (message.status !== 'streaming') {
      this.endStream(message.id);
    }
    if (!isActive(message)) {
      this.endImage(message.id);
    }
    if (this.variantsState()?.messageId === message.id) {
      this.variantsState.set(null);
    }
    this.afterChange();
  }

  private dropMessage(id: string): void {
    this.messagesState.update((list) => list.filter((item) => item.id !== id));
    this.endStream(id);
    this.endImage(id);
    this.afterChange();
  }

  private markFailed(id: unknown): void {
    if (typeof id !== 'string') {
      return;
    }
    // The failed reply keeps the words that were streamed, which the stored text may not hold yet.
    const streamed = this.streamsState()[id] ?? '';
    this.messagesState.update((list) =>
      list.map((item) =>
        item.id === id
          ? { ...item, status: 'failed' as const, content: streamed.length > item.content.length ? streamed : item.content }
          : item,
      ),
    );
    this.endStream(id);
    this.endImage(id);
    this.typingState.set(false);
    this.afterChange();
  }

  /** Forgets the live text of a reply once the stored message holds its final text. */
  private endStream(id: string | null): void {
    if (id === null) {
      return;
    }
    this.gaps.delete(id);
    this.inbox = this.inbox.filter((delta) => delta.messageId !== id);
    if (this.streamsState()[id] !== undefined) {
      this.streamsState.set(Object.fromEntries(Object.entries(this.streamsState()).filter(([key]) => key !== id)));
    }
  }

  /** A corrected check stopped the reply: the streamed words are discarded and new deltas start from offset 0. */
  private restart(id: unknown): void {
    if (typeof id !== 'string') {
      return;
    }
    this.gaps.delete(id);
    this.inbox = this.inbox.filter((delta) => delta.messageId !== id);
    this.streamsState.update((streams) => ({ ...streams, [id]: '' }));
  }

  private queueDelta(data: Readonly<Record<string, unknown>>): void {
    const { message_id: messageId, offset, text } = data;
    if (typeof messageId !== 'string' || typeof offset !== 'number' || typeof text !== 'string') {
      return;
    }
    this.inbox.push({ messageId, offset, text });
    if (this.cancelFrame === null) {
      // One render per animation frame however many tokens arrive, so a long reply does not render per token.
      const run = (): void => {
        this.cancelFrame = null;
        this.flushDeltas();
      };
      if (typeof requestAnimationFrame === 'function') {
        const handle = requestAnimationFrame(run);
        this.cancelFrame = () => cancelAnimationFrame(handle);
      } else {
        const handle = setTimeout(run, 16);
        this.cancelFrame = () => clearTimeout(handle);
      }
    }
  }

  private flushDeltas(): void {
    const batch = this.inbox;
    this.inbox = [];
    const streams = { ...this.streamsState() };
    let changed = false;
    for (const delta of batch) {
      const known = streams[delta.messageId] ?? this.messagesState().find((m) => m.id === delta.messageId)?.content ?? '';
      const next = applyDelta(known, delta.offset, delta.text);
      if (next === null) {
        const waiting = this.gaps.get(delta.messageId) ?? [];
        waiting.push(delta);
        this.gaps.set(delta.messageId, waiting);
      } else if (streams[delta.messageId] !== next) {
        streams[delta.messageId] = next;
        changed = true;
      }
    }
    if (changed) {
      this.streamsState.set(streams);
    }
    this.scheduleCatchUp();
  }

  /**
   * Merges the newest page into the list: the messages in the page's `seq` range are replaced by the page's, older
   * ones stay, and newer ones that an event delivered stay. Then it settles the streaming state against the new text.
   */
  private mergeNewest(page: MessagePage): void {
    const first = page.items[0];
    const last = page.items.at(-1);
    const kept =
      first === undefined || last === undefined
        ? []
        : this.messagesState().filter((item) => item.seq < first.seq || item.seq > last.seq);
    const hasOlderLoaded = kept.some((item) => first !== undefined && item.seq < first.seq);
    this.messagesState.set([...kept, ...page.items].sort((a, b) => a.seq - b.seq));
    if (!hasOlderLoaded) {
      this.nextBeforeState.set(page.next_before_seq);
    }
    this.settleStreams();
  }

  /**
   * Brings the live text in line with the stored messages: a reply that ended loses its live text, one that is still
   * produced takes the stored text when that is longer, and the pieces that were waiting for it are applied.
   */
  private settleStreams(): void {
    const byId = new Map(this.messagesState().map((item) => [item.id, item]));
    const streams: Record<string, string> = {};
    for (const [id, text] of Object.entries(this.streamsState())) {
      const message = byId.get(id);
      if (message === undefined) {
        streams[id] = text;
      } else if (isActive(message)) {
        streams[id] = text.length < message.content.length ? message.content : text;
      }
    }
    for (const [id, waiting] of [...this.gaps]) {
      const message = byId.get(id);
      if (message === undefined || !isActive(message)) {
        this.gaps.delete(id);
        continue;
      }
      let text = streams[id] ?? message.content;
      const remaining: Delta[] = [];
      for (const delta of [...waiting].sort((a, b) => a.offset - b.offset)) {
        const next = applyDelta(text, delta.offset, delta.text);
        if (next === null) {
          remaining.push(delta);
        } else {
          text = next;
        }
      }
      streams[id] = text;
      if (remaining.length === 0) {
        this.gaps.delete(id);
      } else {
        this.gaps.set(id, remaining);
      }
    }
    this.streamsState.set(streams);
  }

  private afterChange(): void {
    void this.syncVariants();
    this.scheduleCatchUp();
  }

  private needsCatchUp(): boolean {
    return this.gaps.size > 0 || (this.socket.state() !== 'open' && this.messagesState().some(isActive));
  }

  private scheduleCatchUp(): void {
    if (this.catchUpTimer !== null || !this.needsCatchUp()) {
      return;
    }
    this.catchUpTimer = setTimeout(() => {
      this.catchUpTimer = null;
      // The socket may have opened, or the missing pieces arrived, while the timer ran.
      if (this.needsCatchUp()) {
        void this.refresh().then(() => this.scheduleCatchUp());
      }
    }, CATCH_UP_MS);
  }

  /** Loads the versions of the latest reply when it has more than one and they are not loaded yet. */
  private async syncVariants(): Promise<void> {
    const reply = this.latestReply();
    if (reply === null || reply.variant_count < 2) {
      if (this.variantsState() !== null) {
        this.variantsState.set(null);
      }
      return;
    }
    const loaded = this.variantsState();
    if (loaded?.messageId === reply.id && loaded.items.length === reply.variant_count) {
      return;
    }
    const ticket = this.generation;
    try {
      const list = await firstValueFrom(this.api.get<VariantList>(`/messages/${reply.id}/variants`));
      if (ticket === this.generation && this.latestReply()?.id === reply.id) {
        this.variantsState.set({ messageId: reply.id, items: list.items });
      }
    } catch {
      // Without the list the alternatives control stays hidden; the next change tries again.
    }
  }
}
