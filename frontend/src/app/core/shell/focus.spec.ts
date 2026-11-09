import { focusMainHeading, pathOf } from './focus';

/** Appends a child element built without markup strings and returns it. */
function add(parent: HTMLElement, tag: string, text = ''): HTMLElement {
  const element = document.createElement(tag);
  element.textContent = text;
  parent.append(element);
  return element;
}

describe('focusMainHeading', () => {
  afterEach(() => {
    document.body.replaceChildren();
  });

  it('focuses the first heading of main and makes it focusable by script only', () => {
    add(document.body, 'a', 'link');
    const main = add(document.body, 'main');
    const heading = add(main, 'h1', 'Titolo');
    add(main, 'h1', 'Altro');
    expect(focusMainHeading()).toBe(true);
    expect(document.activeElement).toBe(heading);
    expect(heading.getAttribute('tabindex')).toBe('-1');
  });

  it('falls back to main when the screen has no heading yet', () => {
    const main = add(document.body, 'main');
    add(main, 'p', 'Caricamento');
    expect(focusMainHeading()).toBe(true);
    expect(document.activeElement).toBe(main);
  });

  it('does nothing without a main', () => {
    add(document.body, 'p', 'vuoto');
    expect(focusMainHeading()).toBe(false);
  });
});

describe('pathOf', () => {
  it('drops the query string and the fragment', () => {
    expect(pathOf('/chats?scenario=1#top')).toBe('/chats');
    expect(pathOf('/gallery')).toBe('/gallery');
  });
});
