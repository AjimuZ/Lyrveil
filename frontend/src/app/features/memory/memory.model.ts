// Shapes of the Memory Studio routes of docs/api.md (Memory). The committed OpenAPI document does not hold these routes
// yet; once `backend/openapi.json` is regenerated, replace these interfaces with the generated types.

export const MEMORY_KINDS = [
  'user_fact',
  'user_preference',
  'character_fact',
  'narrative_event',
  'promise',
  'decision',
  'open_thread',
  'boundary',
  'identity_candidate',
] as const;
export type MemoryKind = (typeof MEMORY_KINDS)[number];

export const MEMORY_PROVENANCES = ['stated', 'inferred', 'fictional', 'user_confirmed'] as const;
export type MemoryProvenance = (typeof MEMORY_PROVENANCES)[number];

export const MEMORY_STATUSES = ['active', 'uncertain', 'superseded', 'contradicted', 'expired', 'invalidated'] as const;
export type MemoryStatus = (typeof MEMORY_STATUSES)[number];

/** The longest text of a memory (`ck_memories_content`). */
export const MAX_MEMORY_CHARS = 280;

/** A message that a memory, an episode or a change of the relationship rests on. */
export interface MemorySource {
  readonly message_id: string;
  readonly conversation_id: string;
  readonly seq: number;
  readonly author: string;
  /** The start of the text that was read. */
  readonly excerpt: string;
  /** False once the message was deleted or shows another variant. */
  readonly valid: boolean;
}

export interface ConflictPeer {
  readonly id: string;
  readonly content: string;
  readonly status: MemoryStatus;
  readonly provenance: MemoryProvenance;
  readonly confidence: number;
}

/** One row of `GET /memories`. */
export interface MemoryItem {
  readonly id: string;
  readonly character_id: string;
  readonly scope: 'conversation' | 'shared';
  readonly conversation_id: string | null;
  readonly kind: MemoryKind;
  readonly content: string;
  readonly subject_key: string | null;
  readonly value_text: string | null;
  readonly provenance: MemoryProvenance;
  readonly durability: 'lasting' | 'temporary';
  readonly status: MemoryStatus;
  readonly importance: number;
  readonly confidence: number;
  readonly locked: boolean;
  readonly needs_review: boolean;
  readonly expires_at: string | null;
  readonly conflict_group_id: string | null;
  readonly version: number;
  readonly updated_at: string;
  readonly source_count: number;
}

/** `GET /memories/{id}`: a memory with its sources and the other memories of its conflict group. */
export interface MemoryDetail extends MemoryItem {
  readonly sources: readonly MemorySource[];
  readonly conflict: readonly ConflictPeer[];
}

export interface MemoryPage {
  readonly items: readonly MemoryItem[];
  readonly next_cursor: string | null;
}

/** One entry of `GET /memories/{id}/revisions`: the memory as it was under `version`. */
export interface MemoryRevision {
  readonly version: number;
  readonly actor: 'user' | 'extraction' | 'consistency';
  readonly reason: string;
  readonly created_at: string;
  readonly content: string | null;
  readonly status: MemoryStatus | null;
  readonly provenance: MemoryProvenance | null;
  readonly confidence: number | null;
  readonly importance: number | null;
}

export interface EpisodeLink {
  readonly direction: 'from' | 'to';
  readonly relation: 'causes' | 'follows' | 'related';
  readonly episode_id: string;
  readonly title: string;
}

export interface Episode {
  readonly id: string;
  readonly conversation_id: string;
  readonly title: string;
  readonly description: string;
  readonly occurred_at: string;
  readonly participants: readonly string[];
  readonly importance: number;
  readonly sources: readonly MemorySource[];
  readonly links: readonly EpisodeLink[];
}

export interface SummaryContent {
  readonly events?: readonly string[];
  readonly decisions?: readonly string[];
  readonly relationship_changes?: readonly string[];
  readonly unresolved?: readonly string[];
  readonly key_facts?: readonly string[];
  readonly source_seqs?: readonly number[];
}

export interface Summary {
  readonly id: string;
  readonly level: number;
  readonly from_seq: number;
  readonly to_seq: number;
  readonly version: number;
  readonly status: 'active' | 'superseded' | 'invalidated';
  readonly text: string;
  readonly content: SummaryContent;
  readonly token_count: number;
  readonly created_at: string;
}

export const RELATIONSHIP_DIMENSIONS = ['familiarity', 'trust', 'confidence', 'tension'] as const;
export type RelationshipDimension = (typeof RELATIONSHIP_DIMENSIONS)[number];

export interface Relationship {
  /** False before any extraction run wrote the state of the conversation. */
  readonly written: boolean;
  /** The narrative when there is one, otherwise the four bands in words. */
  readonly description: string;
  readonly narrative: string;
  readonly expectations: string;
  readonly boundaries: string;
  readonly emotional_state: string | null;
  readonly current_topic: string | null;
  readonly open_threads: readonly string[];
  readonly goals: readonly string[];
  readonly narrative_changes: readonly string[];
  readonly recent_events: readonly { readonly id: string; readonly title: string; readonly occurred_at: string }[];
  readonly bands: Readonly<Record<RelationshipDimension, string>>;
  readonly values: Readonly<Record<RelationshipDimension, number>>;
}

export interface StateChange {
  readonly id: string;
  readonly event_kind: string;
  readonly dimension: RelationshipDimension | null;
  readonly delta: number | null;
  readonly value_before: string | null;
  readonly value_after: string | null;
  readonly reason: string;
  readonly status: 'applied' | 'invalidated';
  readonly created_at: string;
  readonly sources: readonly MemorySource[];
}

/** The fields a correction can change; absent fields stay as they are. */
export interface MemoryChange {
  readonly content?: string;
  readonly importance?: number;
}
