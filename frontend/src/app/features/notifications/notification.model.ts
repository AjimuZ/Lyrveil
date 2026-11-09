// Notification shapes of GET /notifications and the notification.created event (docs/api.md).

/** One in-app notice. `read` is false until the user marks it. `conversation_id` is null for a notice about no chat. */
export interface AppNotification {
  readonly id: string;
  readonly kind: string;
  readonly conversation_id: string | null;
  readonly message_id: string | null;
  readonly title: string;
  readonly body: string;
  readonly read: boolean;
  readonly read_at: string | null;
  readonly created_at: string;
}

/** The answer of GET /notifications. `unread_count` counts every unread notice, not only this page. */
export interface NotificationList {
  readonly items: readonly AppNotification[];
  readonly next_cursor: string | null;
  readonly unread_count: number;
}

/** Narrows the `notification` of a `notification.created` payload, or returns null for anything else. */
export function toNotification(value: unknown): AppNotification | null {
  if (typeof value !== 'object' || value === null) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const { id, kind, title, body, created_at: createdAt } = record;
  if (
    typeof id !== 'string' ||
    typeof kind !== 'string' ||
    typeof title !== 'string' ||
    typeof body !== 'string' ||
    typeof createdAt !== 'string'
  ) {
    return null;
  }
  const conversationId = record['conversation_id'];
  const messageId = record['message_id'];
  const readAt = record['read_at'];
  return {
    id,
    kind,
    title,
    body,
    conversation_id: typeof conversationId === 'string' ? conversationId : null,
    message_id: typeof messageId === 'string' ? messageId : null,
    read: record['read'] === true,
    read_at: typeof readAt === 'string' ? readAt : null,
    created_at: createdAt,
  };
}
