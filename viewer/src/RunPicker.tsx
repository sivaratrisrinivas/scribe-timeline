/**
 * Choosing what to look at: strategy, then condition, then repeat.
 *
 * Three controls rather than one list of twelve runs, because the evidence has
 * three levels and a reader moves through them in that order. Each control shows
 * only what the bundle holds, so there is no switch to nothing.
 *
 * Every button names the condition it leads to, because a condition id on its own
 * is not enough to recognise: `vad_2` and `manual_2` differ by a strategy that
 * decides the entire finding, and the pair has to be read as one name.
 *
 * This component holds no arithmetic and no policy of its own. What survives a
 * switch -- which repeat a reader keeps looking at -- is `selection.ts`, so the
 * behaviour is tested without a DOM and cannot differ between the three levels.
 */

import type { ReactNode } from "react";

import type { CommitStrategy, RunIndexEntry } from "./runIndex.js";
import {
  selectCondition,
  selectStrategy,
  strategiesIn,
  type Selection,
} from "./selection.js";

export interface RunPickerProps {
  readonly groups: ReadonlyMap<string, readonly RunIndexEntry[]>;
  readonly selection: Selection;
  readonly onSelect: (selection: Selection) => void;
}

/** The conditions of one strategy, in bundle order. */
function conditionsOf(
  groups: ReadonlyMap<string, readonly RunIndexEntry[]>,
  strategy: CommitStrategy,
): Array<[string, readonly RunIndexEntry[]]> {
  return [...groups.entries()].filter(([, runs]) => runs[0]?.commitStrategy === strategy);
}

function Switch({
  label,
  hint,
  children,
}: {
  readonly label: string;
  readonly hint: string;
  readonly children: ReactNode;
}) {
  // Slugged, because `aria-labelledby` takes a space-separated list of ids: an id
  // containing the label's own space would resolve to two ids that do not exist, and
  // the group would end up unnamed for anyone reading it with a screen reader.
  const labelId = `picker-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <div className="picker__level">
      <p className="picker__label">
        <span id={labelId}>{label}</span>
        <span className="picker__hint">{hint}</span>
      </p>
      {/* A group, not a radiogroup: switching between conditions is a move, and the
          buttons carry `aria-pressed` so which one is current is available without
          relying on the styling. */}
      <div className="picker__options" role="group" aria-labelledby={labelId}>
        {children}
      </div>
    </div>
  );
}

function Option({
  pressed,
  onClick,
  children,
  detail,
  className = "picker__option",
}: {
  readonly pressed: boolean;
  readonly onClick: () => void;
  readonly children: ReactNode;
  readonly detail: string;
  readonly className?: string;
}) {
  return (
    <button
      type="button"
      className={className}
      aria-pressed={pressed}
      onClick={onClick}
    >
      {children}
      <span className="picker__detail">{detail}</span>
    </button>
  );
}

export function RunPicker({ groups, selection, onSelect }: RunPickerProps) {
  const strategies = strategiesIn(groups);
  const selectedStrategy = groups.get(selection.conditionId)?.[0]?.commitStrategy;
  const conditions = selectedStrategy === undefined ? [] : conditionsOf(groups, selectedStrategy);
  const repeats = groups.get(selection.conditionId) ?? [];

  return (
    <nav className="picker" aria-label="Choose what to look at">
      <Switch label="Commit strategy" hint="who chose the cut points">
        {strategies.map((strategy) => {
          const ofStrategy = conditionsOf(groups, strategy);
          return (
            <Option
              key={strategy}
              pressed={selectedStrategy === strategy}
              onClick={() => {
                const next = selectStrategy(groups, strategy, selection.repeatIndex);
                if (next !== null) onSelect(next);
              }}
              detail={ofStrategy.map(([id]) => id).join(", ")}
            >
              {strategy === "vad" ? "VAD — the server chose" : "manual — the runner chose"}
            </Option>
          );
        })}
      </Switch>

      <Switch label="Condition" hint="how much speech preceded the marker">
        {conditions.map(([conditionId, runs]) => (
          <Option
            key={conditionId}
            pressed={selection.conditionId === conditionId}
            onClick={() => {
              const next = selectCondition(groups, conditionId, selection.repeatIndex);
              if (next !== null) onSelect(next);
            }}
            detail={`${runs[0]!.commitStrategy} · ${runs.length} repeat${runs.length === 1 ? "" : "s"}`}
          >
            {conditionId}
          </Option>
        ))}
      </Switch>

      <Switch label="Repeat" hint="one run of that condition">
        {repeats.map((run) => (
          <Option
            key={run.runId}
            className="picker__option picker__option--repeat"
            pressed={selection.repeatIndex === run.repeatIndex}
            onClick={() => onSelect({ conditionId: selection.conditionId, repeatIndex: run.repeatIndex })}
            detail={run.hasAudio ? "audio rebuilt" : "no audio"}
          >
            repeat {run.repeatIndex}
          </Option>
        ))}
      </Switch>
    </nav>
  );
}
