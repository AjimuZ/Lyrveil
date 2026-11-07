// Server access of the Character Creator: the tag vocabulary, one character's card, create and save.
import { Injectable, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { ApiClient } from '../../core/api/api-client';
import { Character } from '../chats/chats.model';
import { StoredCharacter, Vocabulary, VocabularyList } from './card.model';

/**
 * Reads and writes character cards through the REST API. Every method rejects with an `ApiError`; a refused save
 * carries the server's Italian `detail`.
 */
@Injectable({ providedIn: 'root' })
export class CharactersStore {
  private readonly api = inject(ApiClient);
  private vocabularyRequest: Promise<Vocabulary> | null = null;
  private readonly createdState = signal<string | null>(null);

  /** The id of the character the Creator just created, until the edit screen shows its notice. */
  readonly justCreated = this.createdState.asReadonly();

  /** Returns the tag vocabulary. It is requested once; a failed request is forgotten, so the next call retries. */
  vocabulary(): Promise<Vocabulary> {
    this.vocabularyRequest ??= firstValueFrom(this.api.get<VocabularyList>('/characters/vocabulary')).then(
      (list) => new Vocabulary(list.items),
      (failure: unknown) => {
        this.vocabularyRequest = null;
        throw failure;
      },
    );
    return this.vocabularyRequest;
  }

  load(id: string): Promise<StoredCharacter> {
    return firstValueFrom(this.api.get<StoredCharacter>(`/characters/${encodeURIComponent(id)}`));
  }

  /** Creates the character from a card body and remembers its id in `justCreated`. */
  async create(card: Record<string, unknown>): Promise<Character> {
    const created = await firstValueFrom(this.api.post<Character>('/characters', card));
    this.createdState.set(created.id);
    return created;
  }

  /** Saves the whole card. The server adds a version only when something changed. */
  update(id: string, card: Record<string, unknown>): Promise<StoredCharacter> {
    return firstValueFrom(this.api.put<StoredCharacter>(`/characters/${encodeURIComponent(id)}`, card));
  }

  /** Returns whether `id` was just created, and forgets it so a reload does not show the notice again. */
  takeCreated(id: string): boolean {
    const created = this.createdState() === id;
    if (created) {
      this.createdState.set(null);
    }
    return created;
  }
}
