export function lastEventSeq(events) {
  return (events || []).reduce((max, item) => Math.max(max, Number(item.seq || 0)), 0)
}

export function runEventsUrl(runId, events) {
  return `/api/runs/${runId}/events?after=${lastEventSeq(events)}`
}

export function mergeRunEvent(events, event) {
  if (!event || !Number.isFinite(Number(event.seq))) return events
  if (events.some((item) => Number(item.seq) === Number(event.seq))) return events
  return [...events, event].sort((left, right) => Number(left.seq) - Number(right.seq))
}
