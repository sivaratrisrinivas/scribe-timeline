/**
 * Playback, behind a narrow interface.
 *
 * The interface exists because `<audio>` cannot be driven in a test: jsdom reports
 * `duration` as NaN and `readyState` as 0 forever, and `play()` does not advance
 * time. So the viewer's transport is expressed as an interface it *reads* rather
 * than one it owns, and the browser media element is adapted to it in one place.
 *
 * That place is also where the only seconds/milliseconds conversion in the viewer
 * lives, which is deliberate. The run record's word timestamps are already in
 * milliseconds, while the media element counts seconds. Getting that wrong scales
 * every word's position by 1000 rather than shifting it, and a scaling error is
 * the kind a reader cannot detect in a result that still looks like a timeline.
 *
 * Position is reported as `null` until the audio has metadata. Before that, the
 * element's `currentTime` reads 0 -- not because playback is at the start, but
 * because nothing is loaded. Drawing a playhead at 0 there would put a plausible
 * position on a track that has no position yet.
 */

export interface Playback {
  /** Where the audio is, in milliseconds, or null while that is unknown. */
  currentTimeMs(): number | null;
  /** Move the audio to `ms`, clamped to the start. */
  seek(ms: number): void;
  /** Called on every time update, and once immediately with the current position. */
  subscribe(onTime: (timeMs: number | null) => void): () => void;
  /** Stop listening to the element. Nothing may be seeked or observed afterwards. */
  dispose(): void;
}

/** The part of `HTMLMediaElement` the viewer uses.
 *
 *  Structural rather than the DOM type, so a test can supply an element whose
 *  `readyState` and `currentTime` it controls -- neither of which jsdom will move
 *  on its own. Play and pause are absent because the `<audio>` element's own
 *  controls drive them; the viewer never calls them, so it does not model them.
 */
export interface AudioElementLike {
  currentTime: number;
  readonly readyState: number;
  addEventListener(type: string, listener: () => void): void;
  removeEventListener(type: string, listener: () => void): void;
}

/** `HTMLMediaElement.HAVE_METADATA`: enough is loaded to seek to a position. */
export const HAVE_METADATA = 1;

const TIME_EVENTS = ["timeupdate", "loadedmetadata", "seeked"] as const;

export class ElementPlayback implements Playback {
  readonly #element: AudioElementLike;
  readonly #listeners = new Set<(timeMs: number | null) => void>();
  readonly #elementListeners: Array<[string, () => void]> = [];
  #pendingSeekMs: number | null = null;
  #disposed = false;

  public constructor(element: AudioElementLike) {
    this.#element = element;
    for (const event of TIME_EVENTS) {
      const listener = (): void => {
        this.#flushPendingSeek();
        this.#publish();
      };
      element.addEventListener(event, listener);
      this.#elementListeners.push([event, listener]);
    }
  }

  public currentTimeMs(): number | null {
    if (this.#element.readyState < HAVE_METADATA) return null;
    return this.#element.currentTime * 1000;
  }

  public seek(ms: number): void {
    if (this.#disposed) {
      throw new Error("this playback has been disposed; a new one is needed for the element");
    }
    const target = Math.max(0, ms);
    // Before metadata the element silently drops a seek, so a click on a word would
    // look like it did nothing. Hold it and apply it once the audio can be seeked.
    if (this.#element.readyState < HAVE_METADATA) {
      this.#pendingSeekMs = target;
      return;
    }
    this.#element.currentTime = target / 1000;
    this.#publish();
  }

  public subscribe(onTime: (timeMs: number | null) => void): () => void {
    this.#listeners.add(onTime);
    onTime(this.currentTimeMs());
    return () => this.#listeners.delete(onTime);
  }

  /** Detach from the element.
   *
   *  Required rather than tidy. React's StrictMode mounts effects twice in
   *  development, so without this the first transport would keep three listeners on
   *  a live `<audio>` for the life of the page -- publishing to subscribers nobody
   *  holds any more, on every time update.
   */
  public dispose(): void {
    for (const [event, listener] of this.#elementListeners) {
      this.#element.removeEventListener(event, listener);
    }
    this.#elementListeners.length = 0;
    this.#listeners.clear();
    this.#pendingSeekMs = null;
    this.#disposed = true;
  }

  #flushPendingSeek(): void {
    if (this.#pendingSeekMs === null || this.#element.readyState < HAVE_METADATA) return;
    this.#element.currentTime = this.#pendingSeekMs / 1000;
    this.#pendingSeekMs = null;
  }

  #publish(): void {
    const time = this.currentTimeMs();
    for (const listener of this.#listeners) listener(time);
  }
}
