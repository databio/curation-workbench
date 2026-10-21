// jsdom has no IntersectionObserver. Tests drive LazyList's sentinel through
// `triggerIntersection()`; observers register here.
type IOCallback = (entries: { isIntersecting: boolean; target: Element }[]) => void;
const observers = new Set<{ cb: IOCallback; els: Set<Element> }>();
class FakeIntersectionObserver {
  private rec: { cb: IOCallback; els: Set<Element> };
  constructor(cb: IOCallback) {
    this.rec = { cb, els: new Set() };
    observers.add(this.rec);
  }
  observe(el: Element) {
    this.rec.els.add(el);
  }
  unobserve(el: Element) {
    this.rec.els.delete(el);
  }
  disconnect() {
    observers.delete(this.rec);
  }
  takeRecords() {
    return [];
  }
}
(globalThis as unknown as { IntersectionObserver: unknown }).IntersectionObserver = FakeIntersectionObserver;

/** Fire every observed element as visible (the end of a LazyList scrolled into view). */
export function triggerIntersection(): void {
  for (const rec of [...observers]) rec.cb([...rec.els].map((target) => ({ isIntersecting: true, target })));
}
