// Shapes of the message routes and message events. The committed OpenAPI document does not hold these routes yet;
// once `backend/openapi.json` is regenerated, replace these interfaces with the generated types.
import type { ApiError } from '../../core/api/api-error';

/** The lifecycle of a message. A reply is `queued`, then `streaming`, then ends `complete`, `failed` or `interrupted`. */
export type MessageStatus = 'queued' | 'streaming' | 'complete' | 'failed' | 'interrupted';

export type MessageAuthor = 'user' | 'character' | 'system';

/** One message as REST and the `message.*` events carry it. `content` is the active variant's text. */
export interface Message {
  readonly id: string;
  readonly conversation_id: string;
  readonly seq: number;
  readonly author: MessageAuthor;
  readonly kind: string;
  readonly status: MessageStatus;
  readonly read_state: string | null;
  readonly active_variant_id: string | null;
  readonly content: string;
  readonly variant_count: number;
  readonly edited_at: string | null;
  readonly created_at: string;
  /** The image of the active variant, once drawn; null or absent for a message with none. */
  readonly image_id?: string | null;
}

/** `items` ascend by `seq`; `next_before_seq` is the `before_seq` of the next older page, null at the start. */
export interface MessagePage {
  readonly items: readonly Message[];
  readonly next_before_seq: number | null;
}

/** The answer of `POST /conversations/{id}/messages`. */
export interface SendResult {
  readonly message: Message;
  readonly reply: Message;
}

/** Where the thumbnail of a generated image is served from; the file is on disk, so it loads with ComfyUI off. */
export const imageThumbnailUrl = (imageId: string): string => `/media/images/${encodeURIComponent(imageId)}/thumbnail`;
export const imageOriginalUrl = (imageId: string): string => `/media/images/${encodeURIComponent(imageId)}/original`;

/** The answer of `POST /messages/{id}/regenerate`. */
export interface RegenerateResult {
  readonly message: Message;
  readonly variant_id: string;
}

export interface Variant {
  readonly id: string;
  readonly message_id: string;
  readonly variant_no: number;
  readonly content: string;
  readonly source: string;
  readonly status: MessageStatus;
  readonly created_at: string;
}

export interface VariantList {
  readonly items: readonly Variant[];
}

/** The longest text the server accepts in one message. */
export const MAX_MESSAGE_CHARS = 8000;

/** A message the user sent that the server has not confirmed yet, or whose send failed and can be retried. */
export interface PendingSend {
  readonly clientMessageId: string;
  readonly content: string;
  readonly state: 'sending' | 'failed';
  /** Why the last attempt failed; null while it is still sending. */
  readonly error: ApiError | null;
  readonly createdAt: string;
}
