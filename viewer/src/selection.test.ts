/**
 * Moving between the three levels of the evidence.
 *
 * The rules that matter are the ones about what survives a switch. Holding the
 * repeat index across a condition change is what lets a reader compare repeat 2
 * with repeat 2 rather than two unrelated runs; a switch that reset it would still
 * look like a comparison and would not be one.
 */

import { describe, expect, it } from "vitest";

import { groupByCondition, parseRunIndex, type RunIndexEntry } from "./runIndex.js";
import {
  entryFor,
  selectCondition,
  selectStrategy,
  strategiesIn,
} from "./selection.js";

function entry(
  conditionId: string,
  repeatIndex: number,
  commitStrategy: RunIndexEntry["commitStrategy"] = conditionId.startsWith("manual")
    ? "manual"
    : "vad",
): Record<string, unknown> {
  return {
    run_id: `${conditionId}__rep${repeatIndex}`,
    condition_id: conditionId,
    commit_strategy: commitStrategy,
    repeat_index: repeatIndex,
    audio_path: `audio/${conditionId}__rep${repeatIndex}.wav`,
    has_audio: true,
    commits: [],
  };
}

function groupsOf(runs: Record<string, unknown>[]) {
  return groupByCondition(
    parseRunIndex({ note: "test", runs }).runs,
  );
}

/** The bundle this project ships: a three-step VAD family and its manual control. */
const full = () =>
  groupsOf([
    entry("vad_0", 0),
    entry("vad_1", 0),
    entry("vad_1", 1),
    entry("vad_2", 0),
    entry("vad_2", 1),
    entry("vad_2", 2),
    entry("manual_2", 0),
    entry("manual_2", 1),
    entry("manual_2", 2),
  ]);

describe("the strategies a reader can switch between", () => {
  it("are the ones the bundle holds, with VAD first", () => {
    expect(strategiesIn(full())).toEqual(["vad", "manual"]);
  });

  it("do not include one the bundle has no run for", () => {
    // Offering a switch to nothing would look like a broken control rather than an
    // absent condition.
    expect(strategiesIn(groupsOf([entry("vad_0", 0)]))).toEqual(["vad"]);
  });
});

describe("switching strategy", () => {
  it("lands on that strategy's first condition", () => {
    // The control is a check on the family beside it, so each switch has to put the
    // reader on that side rather than leaving them where they were.
    expect(selectStrategy(full(), "manual", 2)).toEqual({
      conditionId: "manual_2",
      repeatIndex: 2,
    });
    expect(selectStrategy(full(), "vad", 0)?.conditionId).toBe("vad_0");
  });

  it("holds the repeat where the condition it lands on has one", () => {
    const repeated = groupsOf([entry("vad_0", 0), entry("vad_0", 1), entry("manual_2", 0)]);

    expect(selectStrategy(repeated, "vad", 1)).toEqual({
      conditionId: "vad_0",
      repeatIndex: 1,
    });
  });

  it("falls back to the first repeat when the condition it lands on has no such one", () => {
    // The anchor was only run once. Landing on a repeat it does not have would name
    // a run the bundle does not hold.
    expect(selectStrategy(full(), "vad", 2)).toEqual({ conditionId: "vad_0", repeatIndex: 0 });
  });

  it("is absent for a strategy the bundle does not hold", () => {
    expect(selectStrategy(full(), "manual", 0) && strategiesIn(full())).not.toEqual(["vad"]);
    expect(selectStrategy(groupsOf([entry("vad_0", 0)]), "manual", 0)).toBeNull();
  });
});

describe("switching condition", () => {
  it("keeps the repeat when the new condition has it", () => {
    // Repeat 2 of vad_1 and repeat 2 of vad_2 are the same repetition of different
    // conditions. Resetting to repeat 0 would still look like a comparison.
    expect(selectCondition(full(), "vad_1", 2)).toEqual({
      conditionId: "vad_1",
      repeatIndex: 0,
    });
    expect(selectCondition(full(), "vad_0", 0)).toEqual({
      conditionId: "vad_0",
      repeatIndex: 0,
    });
  });

  it("falls back to the first repeat where the condition has no such one", () => {
    // vad_0 was only run once. Asking for its repeat 2 must not name a run that
    // does not exist.
    expect(selectCondition(full(), "vad_0", 2)?.repeatIndex).toBe(0);
  });

  it("is absent for a condition the bundle does not hold", () => {
    expect(selectCondition(full(), "vad_9", 0)).toBeNull();
  });
});

describe("a selection names exactly one run", () => {
  it("resolves to that run's index entry", () => {
    expect(entryFor(full(), { conditionId: "manual_2", repeatIndex: 1 })?.runId).toBe(
      "manual_2__rep1",
    );
  });

  it("is undefined rather than a guess when the run is not in the bundle", () => {
    expect(entryFor(full(), { conditionId: "vad_2", repeatIndex: 9 })).toBeUndefined();
  });
});
