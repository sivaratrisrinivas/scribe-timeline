/**
 * A `Playback` the test drives.
 *
 * jsdom will not play audio and will not report a position, so the viewer's
 * transport is stubbed here rather than simulated. The stub records seeks and lets
 * a test move the playhead, which is exactly the surface `RunViewer` uses -- so a
 * test that passes is a statement about the component, not about jsdom.
 */

import type { Playback } from "../playback.js";

export class FakePlayback implements Playback {
  public readonly seeks: number[] = [];
  #timeMs: number | null = null;
  readonly #listeners = new Set<(timeMs: number | null) => void>();

  public constructor(initialMs: number | null = null) {
    this.#timeMs = initialMs;
  }

  public currentTimeMs(): number | null {
    return this.#timeMs;
  }

  public seek(ms: number): void {
    this.seeks.push(ms);
    this.advanceTo(ms);
  }

  /** Move the playhead as playback would, notifying subscribers. */
  public advanceTo(timeMs: number | null): void {
    this.#timeMs = timeMs;
    for (const listener of this.#listeners) listener(timeMs);
  }

  public subscribe(onTime: (timeMs: number | null) => void): () => void {
    this.#listeners.add(onTime);
    onTime(this.#timeMs);
    return () => this.#listeners.delete(onTime);
  }

  public dispose(): void {
    this.#listeners.clear();
  }
}
