// Screen 10 of docs/frontend.md: GPU state, VRAM, queues, the active model and its launch level, updated live.
import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, computed, inject } from '@angular/core';

import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { TextKey } from '../../core/i18n/translator';
import { GPU_STATES, GpuStateName, RequiredMode } from './resources.model';
import { ResourcesStore } from './resources-store';

const STATE_TEXT = {
  IDLE: 'resources.state.idle',
  LOADING_LLM: 'resources.state.loadingLlm',
  LLM_READY: 'resources.state.llmReady',
  GENERATING_TEXT: 'resources.state.generatingText',
  UNLOADING_LLM: 'resources.state.unloadingLlm',
  LOADING_IMAGE: 'resources.state.loadingImage',
  IMAGE_READY: 'resources.state.imageReady',
  GENERATING_IMAGE: 'resources.state.generatingImage',
  UNLOADING_IMAGE: 'resources.state.unloadingImage',
  RECOVERING: 'resources.state.recovering',
  ERROR: 'resources.state.error',
} as const satisfies Record<GpuStateName, TextKey>;

const MODE_TEXT = {
  llm: 'resources.mode.llm',
  image: 'resources.mode.image',
  none: 'resources.mode.none',
} as const satisfies Record<RequiredMode, TextKey>;

const QUEUE_TEXT = {
  llm: 'resources.queue.llm',
  image: 'resources.queue.image',
  none: 'resources.queue.none',
} as const satisfies Record<RequiredMode, TextKey>;

const LEVEL_TEXT: Readonly<Record<string, TextKey>> = {
  A: 'resources.level.a',
  B: 'resources.level.b',
  C: 'resources.level.c',
  CUSTOM: 'resources.level.custom',
};

const QUEUE_ROWS: readonly RequiredMode[] = ['llm', 'image', 'none'];

/**
 * The AI resources. The GPU state shows in words and as its code, from the last `gpu.state` event or the last read; the
 * card's memory shows as a gauge with the numbers; the queue shows its total and each class (text, images, no GPU); the
 * active text model shows with its launch level. A value the backend could not read shows as unknown. The memory and
 * disk gauges, the job list, the tokens per second, the recent transitions and the preflight results are planned.
 */
@Component({
  selector: 'lyr-resources-screen',
  imports: [TranslatePipe],
  templateUrl: './resources-screen.html',
  styleUrl: './resources-screen.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ResourcesScreen implements OnInit, OnDestroy {
  protected readonly store = inject(ResourcesStore);
  protected readonly queueRows = QUEUE_ROWS.map((mode) => ({ mode, label: QUEUE_TEXT[mode] }));

  /** The GPU state to show: the newest `gpu.state` event until a read replaces it, then the read's. */
  protected readonly stateName = computed(() => this.store.live()?.state ?? this.store.snapshot()?.gpu?.state ?? null);

  protected readonly stateText = computed<TextKey | null>(() => {
    const name = this.stateName();
    const known = GPU_STATES.find((state) => state === name);
    return known === undefined ? null : STATE_TEXT[known];
  });

  /** Whether the load is held back for VRAM, which the state alone does not tell. */
  protected readonly blockedForVram = computed(
    () => (this.store.live()?.blockedReason ?? this.store.snapshot()?.gpu?.blocked_reason ?? null) === 'GPU_INSUFFICIENT_VRAM',
  );

  /** The share of the card's memory in use, 0 to 100, or null when it is unknown. */
  protected readonly usedPercent = computed(() => {
    const vram = this.store.snapshot()?.vram;
    return vram === null || vram === undefined || vram.total_mib === 0
      ? null
      : Math.round((vram.used_mib / vram.total_mib) * 100);
  });

  ngOnInit(): void {
    this.store.start();
  }

  ngOnDestroy(): void {
    this.store.stop();
  }

  protected levelText(level: string | null): TextKey | null {
    return level === null ? null : (LEVEL_TEXT[level] ?? null);
  }

  protected modeText(mode: RequiredMode): TextKey {
    return MODE_TEXT[mode];
  }
}
