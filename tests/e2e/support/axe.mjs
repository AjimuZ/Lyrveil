// Runs axe on the current page once the entrance animations have finished.
//
// The message "arrive" and dialog "rise" animations (docs/design-system.md, Motion) start at opacity 0. axe reads the
// computed color of the text while it runs, so a scan taken mid-fade reports a color-contrast violation that the settled
// screen does not have. Only finite animations are awaited: an endless one, such as the typing squares, never finishes.
import AxeBuilder from '@axe-core/playwright';

/** Waits until no finite animation is running on the page. */
export async function settleAnimations(page) {
  await page.evaluate(async () => {
    const finite = document.getAnimations().filter((animation) => animation.effect?.getComputedTiming().iterations !== Infinity);
    await Promise.all(finite.map((animation) => animation.finished.catch(() => undefined)));
  });
}

/** Returns the axe violations of the settled page as `{ rule, targets }` pairs; an empty list means none. */
export async function axeViolations(page) {
  await settleAnimations(page);
  const results = await new AxeBuilder({ page }).analyze();
  return results.violations.map((violation) => ({ rule: violation.id, targets: violation.nodes.map((node) => node.target) }));
}
