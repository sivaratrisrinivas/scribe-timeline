/**
 * Which run the page is showing.
 *
 * The evidence has three levels, and a reader moves between them in that order:
 * the commit strategy, the condition within it, and the repeat within that. Each
 * is a real switch, and collapsing them into one list of twelve runs would make a
 * reader compare two single runs as though that were a measurement.
 *
 * Two rules about what survives a switch, both of which are the difference between
 * a comparison and a coincidence:
 *
 * - **Changing strategy lands on that strategy's own first condition.** Comparing
 *   VAD with the manual control means looking at each side; starting from whichever
 *   condition happened to be showing would silently keep the reader's eye on one.
 * - **Changing condition keeps the repeat where the bundle has it.** Repeat 2 of
 *   `vad_1` and repeat 2 of `vad_2` are the same repetition of different
 *   conditions, so holding the index lets a reader compare like with like. Where
 *   the new condition has no such repeat, its first one is used rather than a
 *   number that is not there.
 */

import type { CommitStrategy, RunIndexEntry } from "./runIndex.js";

export interface Selection {
  readonly conditionId: string;
  readonly repeatIndex: number;
}

function firstConditionOf(groups: ReadonlyMap<string, readonly RunIndexEntry[]>, strategy: CommitStrategy) {
  for (const [conditionId, entries] of groups) {
    if (entries[0]?.commitStrategy === strategy) return conditionId;
  }
  return undefined;
}

/** The run a selection names, or undefined if the bundle has no such run. */
export function entryFor(
  groups: ReadonlyMap<string, readonly RunIndexEntry[]>,
  selection: Selection,
): RunIndexEntry | undefined {
  return groups.get(selection.conditionId)?.find((run) => run.repeatIndex === selection.repeatIndex);
}

/** The condition a run belongs to, found by its id.
 *
 *  The reverse of `entryFor`, and needed because the run id is the one thing the URL
 *  carries: everything else on the page has to be recovered from it, and recovering a
 *  condition from the run id's own text would be inferring structure from a filename.
 */
export function conditionOf(
  groups: ReadonlyMap<string, readonly RunIndexEntry[]>,
  runId: string,
): readonly RunIndexEntry[] | undefined {
  for (const entries of groups.values()) {
    if (entries.some((run) => run.runId === runId)) return entries;
  }
  return undefined;
}

/** The repeat to show for `conditionId`, preferring the one already being shown. */
export function selectCondition(
  groups: ReadonlyMap<string, readonly RunIndexEntry[]>,
  conditionId: string,
  currentRepeatIndex: number,
): Selection | null {
  const entries = groups.get(conditionId);
  const first = entries?.[0];
  if (first === undefined) return null;
  const held = entries?.find((run) => run.repeatIndex === currentRepeatIndex);
  return { conditionId, repeatIndex: held?.repeatIndex ?? first.repeatIndex };
}

/** The condition to show for `strategy`: that strategy's first, at the held repeat. */
export function selectStrategy(
  groups: ReadonlyMap<string, readonly RunIndexEntry[]>,
  strategy: CommitStrategy,
  currentRepeatIndex: number,
): Selection | null {
  const conditionId = firstConditionOf(groups, strategy);
  return conditionId === undefined ? null : selectCondition(groups, conditionId, currentRepeatIndex);
}

/** The strategies the bundle actually holds, VAD first.
 *
 *  Read off the index rather than hard-coded, so a bundle with no manual control --
 *  or one where every run is manual -- does not offer a switch to nothing.
 */
export function strategiesIn(
  groups: ReadonlyMap<string, readonly RunIndexEntry[]>,
): readonly CommitStrategy[] {
  const present = new Set<CommitStrategy>();
  for (const entries of groups.values()) {
    const strategy = entries[0]?.commitStrategy;
    if (strategy !== undefined) present.add(strategy);
  }
  return (["vad", "manual"] as const).filter((strategy) => present.has(strategy));
}
