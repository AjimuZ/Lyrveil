// DOM helpers for the specs of the ui primitives, so a missing element fails with its selector instead of a null error.

/** The first element of `parent` that matches `selector`; throws when there is none. */
export function find<T extends Element = HTMLElement>(parent: ParentNode, selector: string): T {
  const found = parent.querySelector<T>(selector);
  if (found === null) {
    throw new Error(`nothing matches ${selector}`);
  }
  return found;
}

/** Every element of `parent` that matches `selector`, as an array. */
export function findAll<T extends Element = HTMLElement>(parent: ParentNode, selector: string): T[] {
  return [...parent.querySelectorAll<T>(selector)];
}

/** The item at `index`; throws when the list is shorter. */
export function nth<T>(items: readonly T[], index: number): T {
  const item = items[index];
  if (item === undefined) {
    throw new Error(`no item at index ${index} of ${items.length}`);
  }
  return item;
}

/** The text of an element, empty when it has none. */
export function textOf(element: Element): string {
  return element.textContent ?? '';
}
