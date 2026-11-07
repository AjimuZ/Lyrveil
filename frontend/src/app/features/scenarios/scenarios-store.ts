// Scenario screen state: the list for a tab and a search, and the actions on a scenario.
import { Injectable, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { ApiClient } from '../../core/api/api-client';
import { ApiError, toApiError } from '../../core/api/api-error';
import { Character, CharacterList } from '../chats/chats.model';
import { Scenario, ScenarioFields, ScenarioList, ScenarioStatus } from './scenario.model';

/** How long typing in the search box rests before the list is requested again. */
export const SCENARIO_SEARCH_DELAY_MS = 250;

/**
 * Holds what the scenario screen shows. Reads are `Readonly` signals; every action calls the REST API and then
 * reloads the list, because the server is the source of truth (docs/frontend.md, State). A reload that a newer one
 * overtook is dropped. A failed reload keeps the previous rows and sets `error`; a failed action sets `actionError`
 * and changes nothing.
 */
@Injectable({ providedIn: 'root' })
export class ScenariosStore {
  private readonly api = inject(ApiClient);
  private latestReload = 0;
  private searchTimer: ReturnType<typeof setTimeout> | undefined;

  private readonly itemsState = signal<readonly Scenario[]>([]);
  private readonly loadingState = signal(true);
  private readonly errorState = signal<ApiError | null>(null);
  private readonly actionErrorState = signal<ApiError | null>(null);
  private readonly tabState = signal<ScenarioStatus>('active');
  private readonly queryState = signal('');

  readonly items = this.itemsState.asReadonly();
  /** True until the first answer of the current tab and search arrives. */
  readonly loading = this.loadingState.asReadonly();
  readonly error = this.errorState.asReadonly();
  /** The failure of the last action, or null. */
  readonly actionError = this.actionErrorState.asReadonly();
  readonly tab = this.tabState.asReadonly();
  readonly query = this.queryState.asReadonly();

  /** Requests the list for the current tab and search. */
  async reload(): Promise<void> {
    const ticket = ++this.latestReload;
    try {
      const list = await firstValueFrom(
        this.api.get<ScenarioList>('/scenarios', { status: this.tabState(), q: this.queryState().trim() || null }),
      );
      if (ticket === this.latestReload) {
        this.itemsState.set(list.items);
        this.errorState.set(null);
      }
    } catch (failure) {
      if (ticket === this.latestReload) {
        this.errorState.set(toApiError(failure));
      }
    } finally {
      if (ticket === this.latestReload) {
        this.loadingState.set(false);
      }
    }
  }

  async showTab(tab: ScenarioStatus): Promise<void> {
    this.tabState.set(tab);
    this.loadingState.set(true);
    await this.reload();
  }

  /** Records the search text and reloads once typing has rested for SCENARIO_SEARCH_DELAY_MS. */
  search(text: string): void {
    this.queryState.set(text);
    clearTimeout(this.searchTimer);
    this.searchTimer = setTimeout(() => void this.reload(), SCENARIO_SEARCH_DELAY_MS);
  }

  /**
   * Creates the scenario, or replaces the fields of the one named by `id`. Returns the saved scenario, or null after
   * setting `actionError`; the server's reason (an empty title, a refused text) is in its `detail`.
   */
  async save(id: string | null, fields: ScenarioFields): Promise<Scenario | null> {
    this.actionErrorState.set(null);
    try {
      const saved = await firstValueFrom(
        id === null ? this.api.post<Scenario>('/scenarios', fields) : this.api.put<Scenario>(`/scenarios/${id}`, fields),
      );
      await this.reload();
      return saved;
    } catch (failure) {
      this.actionErrorState.set(toApiError(failure));
      return null;
    }
  }

  /** Archives an active scenario or makes an archived one active. */
  async toggleArchive(scenario: Scenario): Promise<boolean> {
    const action = scenario.status === 'archived' ? 'unarchive' : 'archive';
    return this.act(() => firstValueFrom(this.api.post<Scenario>(`/scenarios/${scenario.id}/${action}`)));
  }

  /** Deletes the scenario for good; conversations that started from it keep their copy. */
  async remove(scenario: Scenario): Promise<boolean> {
    return this.act(() => firstValueFrom(this.api.delete<null>(`/scenarios/${scenario.id}`)));
  }

  /** Returns the active scenarios, newest change first. Rejects with an ApiError. */
  async listActive(): Promise<readonly Scenario[]> {
    const list = await firstValueFrom(this.api.get<ScenarioList>('/scenarios', { status: 'active' }));
    return list.items;
  }

  /** Returns the active characters, for the optional character of a scenario. Rejects with an ApiError. */
  async listCharacters(): Promise<readonly Character[]> {
    const list = await firstValueFrom(this.api.get<CharacterList>('/characters'));
    return list.items;
  }

  clearActionError(): void {
    this.actionErrorState.set(null);
  }

  private async act(work: () => Promise<unknown>): Promise<boolean> {
    this.actionErrorState.set(null);
    try {
      await work();
    } catch (failure) {
      this.actionErrorState.set(toApiError(failure));
      return false;
    }
    await this.reload();
    return true;
  }
}
