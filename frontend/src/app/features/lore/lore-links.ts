// REST calls of the world links, shared by the world editor and the character, scenario and conversation screens.
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { ApiClient } from '../../core/api/api-client';
import {
  LinkKind,
  LoreBook,
  LoreBookList,
  LoreLink,
  LoreLinkList,
  TargetLink,
  TargetLinkList,
  targetParams,
} from './lore.model';

/**
 * Reads and changes the links between worlds and their targets. Every method calls the server and returns what it
 * answered; none keeps state, so each screen holds its own list and reads again after a change (docs/frontend.md,
 * State). A failure is thrown as the HTTP error, for the caller to turn into an `ApiError` with `toApiError`.
 */
@Injectable({ providedIn: 'root' })
export class LoreLinks {
  private readonly api = inject(ApiClient);

  /** The links of a world, oldest first. */
  async ofBook(bookId: string): Promise<readonly LoreLink[]> {
    return (await firstValueFrom(this.api.get<LoreLinkList>(`/lore/books/${bookId}/links`))).items;
  }

  /** The worlds linked to one target, oldest link first, archived worlds included. */
  async ofTarget(kind: LinkKind, targetId: string): Promise<readonly TargetLink[]> {
    return (await firstValueFrom(this.api.get<TargetLinkList>('/lore/links', targetParams(kind, targetId)))).items;
  }

  /** The active worlds, by name. */
  async activeWorlds(): Promise<readonly LoreBook[]> {
    return (await firstValueFrom(this.api.get<LoreBookList>('/lore/worlds', { status: 'active' }))).items;
  }

  /** Links the world to the target. */
  add(bookId: string, kind: LinkKind, targetId: string): Promise<LoreLink> {
    return firstValueFrom(this.api.post<LoreLink>(`/lore/books/${bookId}/links`, targetParams(kind, targetId)));
  }

  /** Removes a link only: the world and the target stay. */
  async remove(linkId: string): Promise<void> {
    await firstValueFrom(this.api.delete<null>(`/lore/links/${linkId}`));
  }
}
