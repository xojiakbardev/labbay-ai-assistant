/**
 * Keeps a chat's scroll container pinned to the newest message — deterministically,
 * without timers.
 *
 * `pinned` is true while the reader is at (or near) the bottom. While pinned,
 * any size change of the scroll container (composer growing, keyboard opening,
 * a pane being shown again) or of its content (a new message, an image
 * finishing loading) re-pins to the bottom in the same frame, via a
 * ResizeObserver. Once the reader scrolls up, nothing moves under them.
 *
 * Bind `container` to the scrolling element and `content` to the element
 * that wraps the messages inside it, and call `onScroll` from its scroll
 * event (and `onUserIntent` from wheel/touchstart, so a smooth scroll the
 * reader interrupts doesn't keep fighting them).
 */
export function useChatScroll(threshold = 80) {
  const container = ref<HTMLElement | null>(null);
  const content = ref<HTMLElement | null>(null);
  const pinned = ref(true);
  // A smooth scroll to the bottom is on its way: the intermediate scroll
  // events it fires must not read as "the reader scrolled up".
  let autoScrolling = false;

  function distanceFromBottom(el: HTMLElement): number {
    return el.scrollHeight - el.scrollTop - el.clientHeight;
  }

  function prefersReducedMotion(): boolean {
    return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function jumpToBottom() {
    const el = container.value;
    if (el) el.scrollTop = el.scrollHeight;
  }

  function scrollToBottom(smooth = false) {
    pinned.value = true;
    const el = container.value;
    if (!el) return;
    if (smooth && !prefersReducedMotion()) {
      autoScrolling = true;
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    } else {
      autoScrolling = false;
      jumpToBottom();
    }
  }

  function onScroll() {
    const el = container.value;
    if (!el) return;
    if (distanceFromBottom(el) <= threshold) {
      pinned.value = true;
      autoScrolling = false;
    } else if (!autoScrolling) {
      pinned.value = false;
    }
  }

  function onUserIntent() {
    autoScrolling = false;
  }

  /** Stops following the bottom without moving (e.g. before prepending older messages). */
  function unpin() {
    autoScrolling = false;
    pinned.value = false;
  }

  watch(
    [container, content],
    ([box, inner], _old, onCleanup) => {
      if (!box) return;
      const observer = new ResizeObserver(() => {
        if (pinned.value) jumpToBottom();
      });
      observer.observe(box);
      if (inner) observer.observe(inner);
      onCleanup(() => observer.disconnect());
    },
    { flush: "post" }
  );

  return { container, content, pinned, scrollToBottom, onScroll, onUserIntent, unpin };
}
