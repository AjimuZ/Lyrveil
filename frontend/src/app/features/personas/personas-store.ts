// Persona screen state: the list for a tab, and the actions on a persona.
import { Injectable, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { ApiClient } from '../../core/api/api-client';
import { ApiError, toApiError } from '../../core/api/api-error';
import { Persona, PersonaFields, PersonaList, PersonaStatus } from './persona.model';

/**
 * Holds what **Le tue persone** shows. Reads are `Readonly` signals; every action calls the REST API and then reloads
 * the list, because the server is the source of truth (docs/frontend.md, State). A reload that a newer one overtook is
 * dropped. A failed reload keeps the previous rows and sets `error`; a failed action sets `actionError` and changes
 * nothing.
 */
@Injectable({ providedIn: 'root' })
export class PersonasStore {
  private readonly api = inject(ApiClient);
  private latestReload = 0;

  private readonly itemsState = signal<readonly Persona[]>([]);
  private readonly loadingState = signal(true);
  private readonly errorState = signal<ApiError | null>(null);
  private readonly actionErrorState = signal<ApiError | null>(null);
  private readonly tabState = signal<PersonaStatus>('active');

  readonly items = this.itemsState.asReadonly();
  /** True until the first answer of the current tab arrives. */
  readonly loading = this.loadingState.asReadonly();
  readonly error = this.errorState.asReadonly();
  /** The failure of the last action, or null. */
  readonly actionError = this.actionErrorState.asReadonly();
  readonly tab = this.tabState.asReadonly();

  /** Requests the list for the current tab: the default persona first, then by name. */
  async reload(): Promise<void> {
    const ticket = ++this.latestReload;
    try {
      const list = await firstValueFrom(this.api.get<PersonaList>('/personas', { status: this.tabState() }));
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

  async showTab(tab: PersonaStatus): Promise<void> {
    this.tabState.set(tab);
    this.loadingState.set(true);
    await this.reload();
  }

  /**
   * Creates the persona, or replaces the fields of the one named by `id`. Returns the saved persona, or null after
   * setting `actionError`; the server's reason (a blank name, an age under 18, a refused text) is in its `detail`.
   */
  async save(id: string | null, fields: PersonaFields): Promise<Persona | null> {
    this.actionErrorState.set(null);
    try {
      const saved = await firstValueFrom(
        id === null ? this.api.post<Persona>('/personas', fields) : this.api.put<Persona>(`/personas/${id}`, fields),
      );
      await this.reload();
      return saved;
    } catch (failure) {
      this.actionErrorState.set(toApiError(failure));
      return null;
    }
  }

  /** Makes an active persona the default one. */
  makeDefault(persona: Persona): Promise<boolean> {
    return this.act(() => firstValueFrom(this.api.post<Persona>(`/personas/${persona.id}/default`)));
  }

  /** Archives an active persona or brings an archived one back. */
  toggleArchive(persona: Persona): Promise<boolean> {
    const action = persona.status === 'archived' ? 'unarchive' : 'archive';
    return this.act(() => firstValueFrom(this.api.post<Persona>(`/personas/${persona.id}/${action}`)));
  }

  /** Deletes a persona that no conversation uses; the server answers 409 otherwise. */
  remove(persona: Persona): Promise<boolean> {
    return this.act(() => firstValueFrom(this.api.delete<null>(`/personas/${persona.id}`)));
  }

  /** Returns the active personas for the new-chat picker, the default first. Rejects with an ApiError. */
  async listActive(): Promise<readonly Persona[]> {
    const list = await firstValueFrom(this.api.get<PersonaList>('/personas', { status: 'active' }));
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
