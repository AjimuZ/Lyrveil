import { KeyEventLike, conversationIdOf, neighbourId, shortcutFor } from './shortcuts';

function key(key: string, modifiers: Partial<KeyEventLike> = {}): KeyEventLike {
  return { key, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...modifiers };
}

describe('shortcutFor', () => {
  it('maps the documented combinations to their actions', () => {
    expect(shortcutFor(key('k', { ctrlKey: true }), false)).toBe('search');
    expect(shortcutFor(key('K', { metaKey: true }), false)).toBe('search');
    expect(shortcutFor(key('n', { ctrlKey: true }), false)).toBe('newChat');
    expect(shortcutFor(key('f', { ctrlKey: true }), false)).toBe('findInChat');
    expect(shortcutFor(key('ArrowUp', { altKey: true }), false)).toBe('previousChat');
    expect(shortcutFor(key('ArrowDown', { altKey: true }), false)).toBe('nextChat');
    expect(shortcutFor(key('?', { shiftKey: true }), false)).toBe('help');
  });

  it('lets the combinations with Ctrl or Alt work inside a field, but not the bare question mark', () => {
    expect(shortcutFor(key('k', { ctrlKey: true }), true)).toBe('search');
    expect(shortcutFor(key('ArrowDown', { altKey: true }), true)).toBe('nextChat');
    expect(shortcutFor(key('?', { shiftKey: true }), true)).toBeNull();
  });

  it('leaves browser shortcuts with an extra modifier and plain keys alone', () => {
    expect(shortcutFor(key('n', { ctrlKey: true, shiftKey: true }), false)).toBeNull();
    expect(shortcutFor(key('k', { ctrlKey: true, altKey: true }), false)).toBeNull();
    expect(shortcutFor(key('ArrowUp', { altKey: true, shiftKey: true }), false)).toBeNull();
    expect(shortcutFor(key('ArrowUp'), false)).toBeNull();
    expect(shortcutFor(key('k'), false)).toBeNull();
    expect(shortcutFor(key('Escape'), false)).toBeNull();
  });

  it('ignores keys that confirm an input-method choice', () => {
    expect(shortcutFor(key('k', { ctrlKey: true, isComposing: true }), false)).toBeNull();
  });
});

describe('neighbourId', () => {
  const ids = ['a', 'b', 'c'];

  it('steps through the list and stops at its ends', () => {
    expect(neighbourId(ids, 'b', 1)).toBe('c');
    expect(neighbourId(ids, 'b', -1)).toBe('a');
    expect(neighbourId(ids, 'c', 1)).toBeNull();
    expect(neighbourId(ids, 'a', -1)).toBeNull();
  });

  it('starts at the first row going forward and at the last going back when there is no current chat', () => {
    expect(neighbourId(ids, null, 1)).toBe('a');
    expect(neighbourId(ids, 'gone', -1)).toBe('c');
    expect(neighbourId([], 'a', 1)).toBeNull();
  });
});

describe('conversationIdOf', () => {
  it('reads the id of a conversation URL only', () => {
    expect(conversationIdOf('/chats/abc?x=1')).toBe('abc');
    expect(conversationIdOf('/chats')).toBeNull();
    expect(conversationIdOf('/gallery')).toBeNull();
  });
});
