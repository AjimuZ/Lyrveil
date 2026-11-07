// Model settings screen state: the profiles with their local state, and the actions that change them.
import { Injectable, inject, signal } from '@angular/core';
import { Observable, firstValueFrom } from 'rxjs';

import { ApiClient } from '../../core/api/api-client';
import { ApiError, toApiError } from '../../core/api/api-error';
import { CustomTemplate, JobState, ModelKind, ModelSettings, QueuedJob } from './model-settings.model';

/** How long the screen waits between two reads of a maintenance job it follows. */
export const JOB_POLL_MS = 1000;

/** Which action a followed job belongs to: **Applica ora**, or the hash of a custom file. */
export type JobPurpose = 'reload' | 'register';

/** A maintenance job as the screen shows it. `errorDetail` is the server's own words, in Italian. */
export interface FollowedJob {
  readonly purpose: JobPurpose;
  readonly id: string;
  readonly status: JobState['status'];
  readonly errorCode: string | null;
  readonly errorDetail: string | null;
}

const TERMINAL: ReadonlySet<JobState['status']> = new Set(['completed', 'failed', 'cancelled']);

/**
 * Holds what the model settings screen shows. Reads are `Readonly` signals. Every action calls the REST API and then
 * reloads the settings, because the server is the source of truth (docs/frontend.md, State). **Applica ora** and a
 * custom registration are queued jobs: the store polls `GET /jobs/{id}` every JOB_POLL_MS until the job ends, since
 * the backend publishes no job event yet. A failed load keeps the previous settings and sets `error`; a failed action
 * sets `actionError` and changes nothing.
 */
@Injectable({ providedIn: 'root' })
export class ModelSettingsStore {
  private readonly api = inject(ApiClient);
  // One follower per purpose: following the hash of a file must not stop following a reload. A new following, or
  // `stopFollowing`, bumps the generation, which ends the loop of the previous one.
  private readonly generations: Record<JobPurpose, number> = { reload: 0, register: 0 };
  private readonly wakes = new Map<JobPurpose, () => void>();
  private readonly timers = new Map<JobPurpose, ReturnType<typeof setTimeout>>();

  private readonly settingsState = signal<ModelSettings | null>(null);
  private readonly loadingState = signal(true);
  private readonly errorState = signal<ApiError | null>(null);
  private readonly actionErrorState = signal<ApiError | null>(null);
  private readonly reloadState = signal<FollowedJob | null>(null);
  private readonly registrationState = signal<FollowedJob | null>(null);
  private readonly changedState = signal(false);

  readonly settings = this.settingsState.asReadonly();
  /** True until the first answer arrives. */
  readonly loading = this.loadingState.asReadonly();
  readonly error = this.errorState.asReadonly();
  /** The failure of the last action, or null. */
  readonly actionError = this.actionErrorState.asReadonly();
  /** The latest **Applica ora** job, or null. */
  readonly reload = this.reloadState.asReadonly();
  /** The latest custom registration job, or null. */
  readonly registration = this.registrationState.asReadonly();
  /** True once a profile was chosen in this visit and no reload has been applied since: **Applica ora** shows. */
  readonly changed = this.changedState.asReadonly();

  /** Requests the settings. */
  async load(): Promise<void> {
    try {
      this.settingsState.set(await firstValueFrom(this.api.get<ModelSettings>('/settings/models')));
      this.errorState.set(null);
    } catch (failure) {
      this.errorState.set(toApiError(failure));
    } finally {
      this.loadingState.set(false);
    }
  }

  /** Makes the text profile the active one (the launch file is rewritten; the runtime loads it at its next start).
   * Returns whether the server accepted it. */
  async select(profileId: string): Promise<boolean> {
    this.actionErrorState.set(null);
    try {
      const changed = this.api.patch<ModelSettings>('/settings/models', { llm_profile_id: profileId });
      this.settingsState.set(await firstValueFrom(changed));
    } catch (failure) {
      this.actionErrorState.set(toApiError(failure));
      return false;
    }
    this.changedState.set(true);
    return true;
  }

  /** **Applica ora**: queues the reload and follows its job to the end. Returns the final job, or null when the
   * request was refused (see `actionError`). */
  async applyNow(): Promise<FollowedJob | null> {
    this.actionErrorState.set(null);
    this.reloadState.set(null);
    const queued = await this.queue(() => this.api.post<QueuedJob>('/settings/models/apply'));
    if (queued === null) {
      return null;
    }
    const ended = await this.follow('reload', queued.id, this.reloadState);
    if (ended?.status === 'completed') {
      this.changedState.set(false);
    }
    return ended;
  }

  /** Registers a file that is already in its model folder. The hash job is followed to the end, and the settings are
   * reloaded when it completes. Returns the final job, or null when the request was refused. */
  async register(kind: ModelKind, file: string, template: CustomTemplate | null): Promise<FollowedJob | null> {
    this.actionErrorState.set(null);
    this.registrationState.set(null);
    const body = kind === 'text' ? { kind, file, template } : { kind, file };
    const queued = await this.queue(() => this.api.post<QueuedJob>('/settings/models/custom', body));
    if (queued === null) {
      return null;
    }
    const ended = await this.follow('register', queued.id, this.registrationState);
    if (ended?.status === 'completed') {
      await this.load();
    }
    return ended;
  }

  /** Removes a custom registration, never its file. Returns whether the server accepted it. */
  async unregister(profileId: string): Promise<boolean> {
    this.actionErrorState.set(null);
    try {
      await firstValueFrom(this.api.delete<null>(`/settings/models/custom/${profileId}`));
    } catch (failure) {
      this.actionErrorState.set(toApiError(failure));
      return false;
    }
    await this.load();
    return true;
  }

  clearActionError(): void {
    this.actionErrorState.set(null);
  }

  /** Stops following jobs, such as when the screen closes. The jobs themselves keep running on the server. */
  stopFollowing(): void {
    for (const purpose of ['reload', 'register'] as const) {
      this.generations[purpose] += 1;
      clearTimeout(this.timers.get(purpose));
      this.wakes.get(purpose)?.();
      this.wakes.delete(purpose);
    }
  }

  private async queue(request: () => Observable<QueuedJob>): Promise<QueuedJob | null> {
    try {
      return await firstValueFrom(request());
    } catch (failure) {
      this.actionErrorState.set(toApiError(failure));
      return null;
    }
  }

  /** Polls the job until it ends or `stopFollowing` is called, publishing each reading through `target`. Returns the
   * last job read, or null when none was. A failed read sets `actionError` and ends the following. */
  private async follow(
    purpose: JobPurpose,
    id: string,
    target: { set(job: FollowedJob): void },
  ): Promise<FollowedJob | null> {
    const generation = ++this.generations[purpose];
    let last: FollowedJob | null = null;
    while (generation === this.generations[purpose]) {
      let job: JobState;
      try {
        job = await firstValueFrom(this.api.get<JobState>(`/jobs/${id}`));
      } catch (failure) {
        if (generation === this.generations[purpose]) {
          this.actionErrorState.set(toApiError(failure));
        }
        return last;
      }
      if (generation !== this.generations[purpose]) {
        return last;
      }
      last = { purpose, id, status: job.status, errorCode: job.error_code, errorDetail: job.error_detail };
      target.set(last);
      if (TERMINAL.has(job.status)) {
        return last;
      }
      await new Promise<void>((resolve) => {
        this.wakes.set(purpose, resolve);
        this.timers.set(purpose, setTimeout(resolve, JOB_POLL_MS));
      });
    }
    return last;
  }
}
