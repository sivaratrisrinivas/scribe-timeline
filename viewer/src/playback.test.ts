import { describe, expect, it } from "vitest";

import { ElementPlayback, HAVE_METADATA, type AudioElementLike } from "./playback.js";

/** An audio element whose load state and play state the test controls.
 *
 * jsdom never advances `readyState` past 0 and never plays, so the cases this
 * module has to get right -- a seek before metadata, a position before metadata --
 * are unreachable against a real jsdom element.
 */
class FakeAudio implements AudioElementLike {
  public currentTime = 0;
  public readyState = 0;
  public readonly listeners = new Map<string, Set<() => void>>();

  public addEventListener(type: string, listener: () => void): void {
    const set = this.listeners.get(type) ?? new Set();
    set.add(listener);
    this.listeners.set(type, set);
  }

  public removeEventListener(type: string, listener: () => void): void {
    this.listeners.get(type)?.delete(listener);
  }

  public emit(type: string): void {
    for (const listener of this.listeners.get(type) ?? []) listener();
  }

  /** Model the audio finishing loading. */
  public load(): void {
    this.readyState = HAVE_METADATA;
    this.emit("loadedmetadata");
  }
}

function loaded(): { element: FakeAudio; playback: ElementPlayback } {
  const element = new FakeAudio();
  const playback = new ElementPlayback(element);
  element.load();
  return { element, playback };
}

describe("position", () => {
  it("is reported in milliseconds", () => {
    const { element, playback } = loaded();
    element.currentTime = 6.4;

    expect(playback.currentTimeMs()).toBe(6400);
  });

  it("is unknown rather than zero before the audio has loaded", () => {
    // jsdom reports 0 here, and so does a real element before metadata. Drawing a
    // playhead at 0 would put a position on a track that does not have one yet.
    const playback = new ElementPlayback(new FakeAudio());

    expect(playback.currentTimeMs()).toBeNull();
  });
});

describe("seeking", () => {
  it("sets the element's position in seconds", () => {
    const { element, playback } = loaded();

    playback.seek(12_380);

    expect(element.currentTime).toBe(12.38);
  });

  it("clamps a position before the start of the audio", () => {
    const { element, playback } = loaded();

    playback.seek(-500);

    expect(element.currentTime).toBe(0);
  });

  it("holds a seek made before the audio has loaded and applies it on load", () => {
    // A real element drops a seek set before metadata. Without holding it, clicking
    // a word on a freshly loaded page would look like it did nothing.
    const element = new FakeAudio();
    const playback = new ElementPlayback(element);

    playback.seek(12_380);
    expect(element.currentTime).toBe(0);

    element.load();

    expect(element.currentTime).toBe(12.38);
  });

  it("keeps only the most recent held seek, not a queue of them", () => {
    // Clicking two words before the audio loads should land on the second. Applying
    // both in turn would move the audio through a position nobody asked for.
    const element = new FakeAudio();
    const playback = new ElementPlayback(element);

    playback.seek(1000);
    playback.seek(6000);
    element.load();

    expect(element.currentTime).toBe(6);
  });
});

describe("subscribers", () => {
  it("are told the position as soon as they subscribe", () => {
    const { element, playback } = loaded();
    element.currentTime = 3;

    const seen: Array<number | null> = [];
    playback.subscribe((time) => seen.push(time));

    expect(seen).toEqual([3000]);
  });

  it("are told about every time update", () => {
    const { element, playback } = loaded();
    const seen: Array<number | null> = [];
    playback.subscribe((time) => seen.push(time));

    element.currentTime = 1;
    element.emit("timeupdate");
    element.currentTime = 2;
    element.emit("timeupdate");

    expect(seen).toEqual([0, 1000, 2000]);
  });

  it("stop being told once they unsubscribe", () => {
    const { element, playback } = loaded();
    const seen: Array<number | null> = [];
    const stop = playback.subscribe((time) => seen.push(time));

    stop();
    element.currentTime = 9;
    element.emit("timeupdate");

    expect(seen).toEqual([0]);
  });

  it("are given more than one subscriber independently", () => {
    const { element, playback } = loaded();
    const first: Array<number | null> = [];
    const second: Array<number | null> = [];
    const stopFirst = playback.subscribe((time) => first.push(time));
    playback.subscribe((time) => second.push(time));

    stopFirst();
    element.currentTime = 4;
    element.emit("timeupdate");

    expect(first).toEqual([0]);
    expect(second).toEqual([0, 4000]);
  });
});

describe("holding a seek across load", () => {
  it("applies a held seek when the audio loads, and not before", () => {
    const element = new FakeAudio();
    const playback = new ElementPlayback(element);

    playback.seek(6000);
    expect(element.currentTime).toBe(0);

    element.load();

    expect(element.currentTime).toBe(6);
  });
});

describe("disposal", () => {
  it("detaches from the element, so a time update reaches nobody", () => {
    // React's StrictMode mounts twice in development. Without detaching, the first
    // transport keeps listening to a live `<audio>` for the life of the page.
    const { element, playback } = loaded();
    const seen: Array<number | null> = [];
    playback.subscribe((time) => seen.push(time));

    playback.dispose();
    element.currentTime = 5;
    element.emit("timeupdate");

    // Only the value delivered on subscription; the time update after disposal
    // reached nobody.
    expect(seen).toEqual([0]);
  });

  it("leaves no listeners on the element at all", () => {
    const element = new FakeAudio();
    const playback = new ElementPlayback(element);
    expect([...element.listeners.values()].every((set) => set.size === 1)).toBe(true);

    playback.dispose();

    expect([...element.listeners.values()].every((set) => set.size === 0)).toBe(true);
  });

  it("refuses to seek a disposed transport rather than moving the audio silently", () => {
    // A seek after disposal would move an element this transport no longer owns,
    // which is exactly the kind of surprise that hides a wiring bug.
    const element = loaded().element;
    const playback = new ElementPlayback(element);
    playback.dispose();

    expect(() => playback.seek(1000)).toThrow(/disposed/);
  });
});
