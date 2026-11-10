// Durations measured on a camera, tagged HW, keyed by phase id: {phase: {s: 12.4, tag: 'HW', source: '...'}}.
//
// A progress fill or an estimate ("about 40 s") may appear only for a phase listed here.
// Nothing has been measured for the app's phases yet, so it ships empty and every wait shows its elapsed counter
// instead.
export const MEASURED = Object.freeze({});

export function measured(phase) { return MEASURED[phase] && MEASURED[phase].tag === 'HW' ? MEASURED[phase].s : null; }
