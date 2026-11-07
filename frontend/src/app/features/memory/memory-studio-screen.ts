// Screen 5 of docs/frontend.md: the Memory Studio. It draws the Ricordi, Eventi, Rapporto and Riassunti tabs, and
// Diagnostica only while diagnostic mode is on; conflicts to resolve and proposals for the profile are not drawn.
import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ApiError } from '../../core/api/api-error';
import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { TextKey, Translator } from '../../core/i18n/translator';
import {
  EpisodeLink,
  MAX_MEMORY_CHARS,
  MEMORY_KINDS,
  MEMORY_PROVENANCES,
  MemoryItem,
  MemoryKind,
  MemoryProvenance,
  MemoryRevision,
  MemoryStatus,
  PROMPT_BLOCKS,
  PROMPT_BUDGETS,
  PromptBlock,
  PromptBudget,
  RELATIONSHIP_DIMENSIONS,
  RelationshipDimension,
  SCORE_PARTS,
  ScorePart,
  SummaryContent,
} from './memory.model';
import { LockFilter, MemoryFilters, MemoryStudioStore, StatusFilter } from './memory-store';

type TabId = 'memories' | 'events' | 'relationship' | 'summaries' | 'diagnostics';

interface Tab {
  readonly id: TabId;
  readonly label: TextKey;
}

const TABS: readonly Tab[] = [
  { id: 'memories', label: 'memory.tab.memories' },
  { id: 'events', label: 'memory.tab.events' },
  { id: 'relationship', label: 'memory.tab.relationship' },
  { id: 'summaries', label: 'memory.tab.summaries' },
];

/** Drawn after the others, and only while diagnostic mode is on. */
const DIAGNOSTICS_TAB: Tab = { id: 'diagnostics', label: 'memory.tab.diagnostics' };

const BLOCK_LABEL: Readonly<Record<PromptBlock, TextKey>> = {
  global: 'memory.diag.block.global',
  identity: 'memory.diag.block.identity',
  communication: 'memory.diag.block.communication',
  scenario: 'memory.diag.block.scenario',
  summary: 'memory.diag.block.summary',
  history: 'memory.diag.block.history',
  state: 'memory.diag.block.state',
  memories: 'memory.diag.block.memories',
  anchor: 'memory.diag.block.anchor',
  current: 'memory.diag.block.current',
};

const BUDGET_LABEL: Readonly<Record<PromptBudget, TextKey>> = {
  context_limit: 'memory.diag.budget.context_limit',
  reply_reserve: 'memory.diag.budget.reply_reserve',
  safety_margin: 'memory.diag.budget.safety_margin',
  available: 'memory.diag.budget.available',
  memory_target: 'memory.diag.budget.memory_target',
  summary_target: 'memory.diag.budget.summary_target',
};

const PART_LABEL: Readonly<Record<ScorePart, TextKey>> = {
  fts: 'memory.diag.part.fts',
  importance: 'memory.diag.part.importance',
  recency: 'memory.diag.part.recency',
  topic: 'memory.diag.part.topic',
  kind: 'memory.diag.part.kind',
  confidence: 'memory.diag.part.confidence',
  status_factor: 'memory.diag.part.status_factor',
  locked_bonus: 'memory.diag.part.locked_bonus',
  previous_bonus: 'memory.diag.part.previous_bonus',
};

const REDUCTION_LABEL: Readonly<Record<string, TextKey>> = {
  examples: 'memory.diag.reduction.examples',
  summaries: 'memory.diag.reduction.summaries',
  memories: 'memory.diag.reduction.memories',
  history: 'memory.diag.reduction.history',
  identity: 'memory.diag.reduction.identity',
  state: 'memory.diag.reduction.state',
  history_below_floor: 'memory.diag.reduction.history_below_floor',
};

const STATUS_LABEL: Readonly<Record<MemoryStatus, TextKey>> = {
  active: 'memory.status.active',
  uncertain: 'memory.status.uncertain',
  superseded: 'memory.status.superseded',
  contradicted: 'memory.status.contradicted',
  expired: 'memory.status.expired',
  invalidated: 'memory.status.invalidated',
};

const KIND_LABEL: Readonly<Record<MemoryKind, TextKey>> = {
  user_fact: 'memory.kind.user_fact',
  user_preference: 'memory.kind.user_preference',
  character_fact: 'memory.kind.character_fact',
  narrative_event: 'memory.kind.narrative_event',
  promise: 'memory.kind.promise',
  decision: 'memory.kind.decision',
  open_thread: 'memory.kind.open_thread',
  boundary: 'memory.kind.boundary',
  identity_candidate: 'memory.kind.identity_candidate',
};

const PROVENANCE_LABEL: Readonly<Record<MemoryProvenance, TextKey>> = {
  stated: 'memory.provenance.stated',
  inferred: 'memory.provenance.inferred',
  fictional: 'memory.provenance.fictional',
  user_confirmed: 'memory.provenance.user_confirmed',
};

const REASON_LABEL: Readonly<Record<string, TextKey>> = {
  edit: 'memory.reason.edit',
  confirm: 'memory.reason.confirm',
  update: 'memory.reason.update',
  correction: 'memory.reason.correction',
  contradiction: 'memory.reason.contradiction',
  subject_change: 'memory.reason.subject_change',
  subject_conflict: 'memory.reason.subject_conflict',
  inference_conflict: 'memory.reason.inference_conflict',
  expired: 'memory.reason.expired',
  superseded: 'memory.reason.superseded',
  source_changed: 'memory.reason.source_changed',
  invalidated: 'memory.reason.invalidated',
  replacement_invalidated: 'memory.reason.replacement_invalidated',
  conflict_cleared: 'memory.reason.conflict_cleared',
  reevaluation: 'memory.reason.reevaluation',
};

const ACTOR_LABEL: Readonly<Record<MemoryRevision['actor'], TextKey>> = {
  user: 'memory.actor.user',
  extraction: 'memory.actor.extraction',
  consistency: 'memory.actor.consistency',
};

const CHANGE_LABEL: Readonly<Record<string, TextKey>> = {
  relationship_change: 'memory.change.relationship_change',
  emotion_change: 'memory.change.emotion_change',
  thread_opened: 'memory.change.thread_opened',
  thread_closed: 'memory.change.thread_closed',
  goal_change: 'memory.change.goal_change',
  narrative_change: 'memory.change.narrative_change',
  user_correction: 'memory.change.user_correction',
};

const DIMENSION_LABEL: Readonly<Record<RelationshipDimension, TextKey>> = {
  familiarity: 'memory.dimension.familiarity',
  trust: 'memory.dimension.trust',
  confidence: 'memory.dimension.confidence',
  tension: 'memory.dimension.tension',
};

const SUMMARY_LISTS = [
  { name: 'events', label: 'memory.summary.events' },
  { name: 'decisions', label: 'memory.summary.decisions' },
  { name: 'relationship_changes', label: 'memory.summary.relationship_changes' },
  { name: 'unresolved', label: 'memory.summary.unresolved' },
  { name: 'key_facts', label: 'memory.summary.key_facts' },
] as const satisfies readonly { name: keyof SummaryContent; label: TextKey }[];

const STATUS_FILTERS: readonly { value: StatusFilter; label: TextKey }[] = [
  { value: 'in_use', label: 'memory.status.inUse' },
  { value: 'all', label: 'memory.status.all' },
  ...(['active', 'uncertain', 'superseded', 'contradicted', 'expired', 'invalidated'] as const).map((value) => ({
    value,
    label: STATUS_LABEL[value],
  })),
];

const LOCK_FILTERS: readonly { value: LockFilter; label: TextKey }[] = [
  { value: 'all', label: 'memory.lock.all' },
  { value: 'locked', label: 'memory.lock.locked' },
  { value: 'unlocked', label: 'memory.lock.unlocked' },
];

/**
 * The Memory Studio of one character (`/characters/:id/memory`, optionally `?conversation=<chat id>`). **Ricordi**
 * lists the memories with search and filters; each card shows its text, origin, importance, confidence and state, and
 * can be edited, locked, confirmed or deleted, and opened to show its source messages, the memory it contradicts and
 * its history. A memory in an unresolved contradiction is labeled "Incerto" with a note. **Eventi**, **Rapporto** and
 * **Riassunti** show the episodes, the relationship (in words first, numbers only in an advanced section) and the
 * summary checkpoints, each with the messages it rests on. **Diagnostica** exists only while diagnostic mode is on: for
 * a chosen reply it shows the stored prompt assembly (budgets, tokens per block, the memories with their score parts,
 * the summaries and the full prompt when it was kept). Rapporto, Riassunti and Diagnostica need one chosen
 * conversation. The server is the source of truth: every action goes through the store, which reads again.
 */
@Component({
  selector: 'lyr-memory-studio',
  imports: [RouterLink, TranslatePipe],
  templateUrl: './memory-studio-screen.html',
  styleUrl: './memory-studio-screen.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class MemoryStudioScreen {
  protected readonly store = inject(MemoryStudioStore);
  private readonly translator = inject(Translator);

  /** The character, from the route. */
  readonly id = input.required<string>();
  /** The conversation to start with, from the `conversation` query parameter. */
  readonly conversation = input<string | undefined>();

  protected readonly tabs = computed(() => (this.store.diagnosticMode() ? [...TABS, DIAGNOSTICS_TAB] : TABS));
  protected readonly blocks = PROMPT_BLOCKS;
  protected readonly budgets = PROMPT_BUDGETS;
  protected readonly scoreParts = SCORE_PARTS;
  protected readonly statusFilters = STATUS_FILTERS;
  protected readonly lockFilters = LOCK_FILTERS;
  protected readonly kinds = MEMORY_KINDS.map((value) => ({ value, label: KIND_LABEL[value] }));
  protected readonly provenances = MEMORY_PROVENANCES.map((value) => ({ value, label: PROVENANCE_LABEL[value] }));
  protected readonly dimensions = RELATIONSHIP_DIMENSIONS;
  protected readonly summaryLists = SUMMARY_LISTS;
  protected readonly maxChars = MAX_MEMORY_CHARS;
  protected readonly importances = [1, 2, 3, 4, 5] as const;

  protected readonly tab = signal<TabId>('memories');
  /** The memory being edited and its draft, or null. */
  protected readonly editing = signal<string | null>(null);
  protected readonly draftContent = signal('');
  protected readonly draftImportance = signal(3);
  protected readonly attempted = signal(false);
  /** The memory whose delete waits for a confirmation, or null. */
  protected readonly confirmingDelete = signal<string | null>(null);
  protected readonly busy = signal(false);

  constructor() {
    effect(() => {
      const character = this.id();
      const conversation = this.conversation() ?? null;
      untracked(() => void this.start(character, conversation));
    });
  }

  protected async showTab(tab: TabId): Promise<void> {
    this.tab.set(tab);
    await this.loadTab();
  }

  protected async chooseConversation(event: Event): Promise<void> {
    const value = this.valueOf(event);
    await this.store.selectConversation(value === '' ? null : value);
    await this.loadTab();
  }

  protected search(event: Event): void {
    this.store.search(this.valueOf(event));
  }

  protected async filterStatus(event: Event): Promise<void> {
    await this.store.setFilters({ status: this.valueOf(event) as StatusFilter });
  }

  protected async filterKind(event: Event): Promise<void> {
    await this.store.setFilters({ kind: this.valueOf(event) as MemoryFilters['kind'] });
  }

  protected async filterProvenance(event: Event): Promise<void> {
    await this.store.setFilters({ provenance: this.valueOf(event) as MemoryFilters['provenance'] });
  }

  protected async filterLock(event: Event): Promise<void> {
    await this.store.setFilters({ lock: this.valueOf(event) as LockFilter });
  }

  protected startEdit(memory: MemoryItem): void {
    this.store.clearActionError();
    this.attempted.set(false);
    this.draftContent.set(memory.content);
    this.draftImportance.set(memory.importance);
    this.editing.set(memory.id);
  }

  protected cancelEdit(): void {
    this.editing.set(null);
    this.store.clearActionError();
  }

  protected draftValid(): boolean {
    const length = this.draftContent().trim().length;
    return length >= 1 && length <= MAX_MEMORY_CHARS;
  }

  protected async saveEdit(memory: MemoryItem, event: Event): Promise<void> {
    event.preventDefault();
    this.attempted.set(true);
    if (!this.draftValid() || this.busy()) {
      return;
    }
    const saved = await this.run(() =>
      this.store.edit(memory, { content: this.draftContent().trim(), importance: this.draftImportance() }),
    );
    if (saved) {
      this.editing.set(null);
    }
  }

  protected async toggleLock(memory: MemoryItem): Promise<void> {
    await this.run(() => this.store.setLocked(memory, !memory.locked));
  }

  protected async confirm(memory: MemoryItem): Promise<void> {
    await this.run(() => this.store.confirm(memory));
  }

  protected async confirmRemove(memory: MemoryItem): Promise<void> {
    this.confirmingDelete.set(null);
    await this.run(() => this.store.remove(memory));
  }

  protected async toggleDetails(memory: MemoryItem): Promise<void> {
    if (memory.id in this.store.opened()) {
      this.store.closeDetails(memory.id);
    } else {
      await this.store.openDetails(memory.id);
    }
  }

  /** Whether a memory in use can still be confirmed: not yet confirmed, and active or uncertain. */
  protected canConfirm(memory: MemoryItem): boolean {
    return memory.provenance !== 'user_confirmed' && (memory.status === 'active' || memory.status === 'uncertain');
  }

  protected statusLabel(status: MemoryStatus): TextKey {
    return STATUS_LABEL[status];
  }

  protected kindLabel(kind: MemoryKind): TextKey {
    return KIND_LABEL[kind];
  }

  /** The label of a kind the server named as plain text, or null for one this screen does not know. */
  protected kindName(kind: string): TextKey | null {
    return (KIND_LABEL as Readonly<Record<string, TextKey>>)[kind] ?? null;
  }

  protected provenanceLabel(provenance: MemoryProvenance): TextKey {
    return PROVENANCE_LABEL[provenance];
  }

  protected sourcesText(count: number): string {
    return this.translator.plural('memory.sources.count', count);
  }

  protected reasonLabel(reason: string): TextKey | null {
    return REASON_LABEL[reason] ?? null;
  }

  protected actorLabel(actor: MemoryRevision['actor']): TextKey {
    return ACTOR_LABEL[actor];
  }

  protected changeLabel(kind: string): TextKey | null {
    return CHANGE_LABEL[kind] ?? null;
  }

  protected dimensionLabel(dimension: RelationshipDimension | null): TextKey | null {
    return dimension === null ? null : DIMENSION_LABEL[dimension];
  }

  protected dimensionName(dimension: RelationshipDimension): TextKey {
    return DIMENSION_LABEL[dimension];
  }

  /** The catalog key and placeholders of the sentence for a link between two episodes. */
  protected linkLabel(link: EpisodeLink): TextKey {
    if (link.relation === 'related') {
      return 'memory.link.related';
    }
    return `memory.link.${link.relation}.${link.direction}`;
  }

  protected blockLabel(block: PromptBlock): TextKey {
    return BLOCK_LABEL[block];
  }

  protected budgetLabel(budget: PromptBudget): TextKey {
    return BUDGET_LABEL[budget];
  }

  protected partLabel(part: ScorePart): TextKey {
    return PART_LABEL[part];
  }

  protected reductionLabel(name: string): TextKey | null {
    return REDUCTION_LABEL[name] ?? null;
  }

  /** The message ranges as "3–7, 9–12". */
  protected ranges(gaps: readonly (readonly [number, number])[]): string {
    return gaps.map(([from, to]) => `${from}–${to}`).join(', ');
  }

  protected decimals(value: number): string {
    return value.toFixed(2);
  }

  protected async chooseReply(event: Event): Promise<void> {
    const value = this.valueOf(event);
    await this.store.selectReply(value === '' ? null : value);
  }

  protected percent(confidence: number | null): number {
    return Math.round((confidence ?? 0) * 100);
  }

  protected list(content: SummaryContent, name: keyof SummaryContent): readonly (string | number)[] {
    return content[name] ?? [];
  }

  protected failureText(failure: ApiError): string {
    return failure.detail ?? this.translator.t(failure.textKey);
  }

  protected when(iso: string): string {
    const date = new Date(iso);
    return Number.isNaN(date.getTime())
      ? iso
      : new Intl.DateTimeFormat(this.translator.locale(), { dateStyle: 'medium', timeStyle: 'short' }).format(date);
  }

  protected valueOf(event: Event): string {
    return (event.target as HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement).value;
  }

  private async start(character: string, conversation: string | null): Promise<void> {
    this.tab.set('memories');
    this.editing.set(null);
    this.confirmingDelete.set(null);
    await this.store.open(character, conversation);
  }

  private async loadTab(): Promise<void> {
    switch (this.tab()) {
      case 'events':
        await this.store.loadEpisodes();
        break;
      case 'relationship':
        await this.store.loadRelationship();
        break;
      case 'summaries':
        await this.store.loadSummaries();
        break;
      case 'diagnostics':
        await this.store.loadReplies();
        break;
      case 'memories':
        break;
    }
  }

  private async run(work: () => Promise<boolean>): Promise<boolean> {
    this.busy.set(true);
    try {
      return await work();
    } finally {
      this.busy.set(false);
    }
  }
}
