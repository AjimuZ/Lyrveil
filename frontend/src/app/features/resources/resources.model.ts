// Shapes of GET /system/resources. The committed OpenAPI document does not hold the route yet; once
// `backend/openapi.json` is regenerated, replace these interfaces with the generated types.

export type RequiredMode = 'llm' | 'image' | 'none';

/** The orchestrator's GPU state. A name the screen does not know is shown as unknown, not guessed. */
export const GPU_STATES = [
  'IDLE',
  'LOADING_LLM',
  'LLM_READY',
  'GENERATING_TEXT',
  'UNLOADING_LLM',
  'LOADING_IMAGE',
  'IMAGE_READY',
  'GENERATING_IMAGE',
  'UNLOADING_IMAGE',
  'RECOVERING',
  'ERROR',
] as const;
export type GpuStateName = (typeof GPU_STATES)[number];

export interface BlockedVram {
  readonly mode: 'llm' | 'image';
  readonly free_mib: number;
  readonly required_mib: number;
}

export interface GpuInfo {
  readonly state: string;
  readonly label_key: string | null;
  readonly desired_mode: RequiredMode;
  readonly draining: boolean;
  readonly blocked_reason: string | null;
  readonly blocked_vram: BlockedVram | null;
}

export interface VramInfo {
  readonly device_name: string;
  readonly total_mib: number;
  readonly used_mib: number;
  readonly free_mib: number;
}

export interface QueueCounts {
  readonly queued: number;
  readonly running: number;
}

export interface QueueInfo extends QueueCounts {
  readonly by_mode: Readonly<Record<RequiredMode, QueueCounts>>;
}

export interface ActiveModel {
  readonly profile_id: string;
  readonly display_name: string;
  /** `A`, `B` or `C` (lowered after an allocation failure), `CUSTOM`, or null when the launch file has none. */
  readonly level: string | null;
  readonly context_size: number | null;
}

/** What the screen shows. A part the backend could not read is null and shows as unknown. */
export interface Resources {
  readonly gpu: GpuInfo | null;
  readonly vram: VramInfo | null;
  readonly queue: QueueInfo;
  readonly model: ActiveModel | null;
}

/** The last `gpu.state` event: newer than the last REST read until the next read arrives. */
export interface LiveGpu {
  readonly state: string;
  readonly labelKey: string | null;
  readonly queue: number | null;
  readonly blockedReason: string | null;
}
