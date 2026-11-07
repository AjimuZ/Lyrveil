// Server access of the Character Creator and the profile: the tag vocabulary, one character's card, create and save,
// the assisted draft and its job, versions with diff and restore, and duplicate, archive and purge.
import { Injectable, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { ApiClient } from '../../core/api/api-client';
import { Character, Conversation, ConversationList } from '../chats/chats.model';
import { AssetList, CharacterAsset, CropRect } from './asset.model';
import {
  DraftResult,
  JobDetail,
  PurgePreview,
  QueuedJob,
  StoredCharacter,
  VersionDiff,
  VersionList,
  VersionSummary,
  Vocabulary,
  VocabularyList,
} from './card.model';

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

  /**
   * Queues an assisted draft from `description`; with `characterId` it rewrites that character's card and keeps its
   * locks. Nothing is saved: the job's result is a card for the Creator to show.
   */
  startDraft(description: string, characterId: string | null): Promise<QueuedJob> {
    return firstValueFrom(this.api.post<QueuedJob>('/characters/drafts', { description, character_id: characterId }));
  }

  /** Reads the state, and once it completed the result, of the draft job `jobId`. */
  draftJob(jobId: string): Promise<JobDetail<DraftResult>> {
    return firstValueFrom(this.api.get<JobDetail<DraftResult>>(`/jobs/${encodeURIComponent(jobId)}`));
  }

  /** Lists the versions of a character, newest first. */
  async versions(id: string): Promise<readonly VersionSummary[]> {
    const list = await firstValueFrom(this.api.get<VersionList>(`${this.path(id)}/versions`));
    return list.items;
  }

  /** Compares two versions field by field. */
  diff(id: string, from: number, to: number): Promise<VersionDiff> {
    return firstValueFrom(this.api.get<VersionDiff>(`${this.path(id)}/versions/${from}/diff/${to}`));
  }

  /** Appends a version holding the card of `versionNo` and returns the character. */
  restore(id: string, versionNo: number): Promise<StoredCharacter> {
    return firstValueFrom(this.api.post<StoredCharacter>(`${this.path(id)}/versions/${versionNo}/restore`));
  }

  /** Lists the active conversations of one character, in the order of the chat list. */
  async conversations(id: string): Promise<readonly Conversation[]> {
    const list = await firstValueFrom(this.api.get<ConversationList>('/conversations', { character_id: id }));
    return list.items;
  }

  /** Starts a chat with the character, following its language. */
  startChat(id: string): Promise<Conversation> {
    return firstValueFrom(this.api.post<Conversation>('/conversations', { character_id: id, language: null }));
  }

  /** Copies the card into a new active character; conversations and memories are not copied. */
  duplicate(id: string): Promise<Character> {
    return firstValueFrom(this.api.post<Character>(`${this.path(id)}/duplicate`));
  }

  archive(id: string): Promise<Character> {
    return firstValueFrom(this.api.post<Character>(`${this.path(id)}/archive`));
  }

  unarchive(id: string): Promise<Character> {
    return firstValueFrom(this.api.post<Character>(`${this.path(id)}/unarchive`));
  }

  purgePreview(id: string): Promise<PurgePreview> {
    return firstValueFrom(this.api.get<PurgePreview>(`${this.path(id)}/purge-preview`));
  }

  /** Deletes the character for good. The server refuses unless `confirmName` equals the first name. */
  async purge(id: string, confirmName: string): Promise<void> {
    await firstValueFrom(this.api.post<null>(`${this.path(id)}/purge`, { confirm_name: confirmName }));
  }

  /** Lists the live avatars of a character, newest first. */
  async avatars(id: string): Promise<readonly CharacterAsset[]> {
    const list = await firstValueFrom(this.api.get<AssetList>(`${this.path(id)}/assets`, { kind: 'avatar' }));
    return list.items;
  }

  /** Uploads an image as an avatar (multipart). The first avatar becomes the primary one on the server. */
  uploadAvatar(id: string, file: File): Promise<CharacterAsset> {
    const body = new FormData();
    body.append('kind', 'avatar');
    body.append('file', file, file.name);
    return firstValueFrom(this.api.post<CharacterAsset>(`${this.path(id)}/assets`, body));
  }

  /** Saves a square of an avatar as a new 512 x 512 avatar; the original stays. */
  cropAvatar(id: string, assetId: string, rect: CropRect): Promise<CharacterAsset> {
    return firstValueFrom(
      this.api.post<CharacterAsset>(`${this.path(id)}/assets/${encodeURIComponent(assetId)}/crop`, rect),
    );
  }

  /** Makes the avatar the only primary one. */
  setPrimaryAvatar(id: string, assetId: string): Promise<CharacterAsset> {
    return firstValueFrom(
      this.api.post<CharacterAsset>(`${this.path(id)}/assets/${encodeURIComponent(assetId)}/primary`),
    );
  }

  /**
   * Queues the drawing of an avatar through the shared image job (`POST /characters/{id}/assets/generate`) and returns
   * the job. When it completes the image joins the avatars; it is the primary one only if the character had none.
   */
  async generateAvatar(id: string): Promise<QueuedJob> {
    const accepted = await firstValueFrom(
      this.api.post<{ readonly job: QueuedJob }>(`${this.path(id)}/assets/generate`, { purpose: 'avatar' }),
    );
    return accepted.job;
  }

  /** Reads the state of a job by id: its status, the stage it runs, and the Italian reason when it failed. */
  job(jobId: string): Promise<JobDetail> {
    return firstValueFrom(this.api.get<JobDetail>(`/jobs/${encodeURIComponent(jobId)}`));
  }

  private path(id: string): string {
    return `/characters/${encodeURIComponent(id)}`;
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
