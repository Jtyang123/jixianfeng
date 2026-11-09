import { describe, expect, it } from 'vitest'
import { lastEventSeq, mergeRunEvent, runEventsUrl } from '../src/utils/runEvents.js'
import { approvalPresentation, scoreDeltaLabel } from '../src/utils/runPresentation.js'

describe('run event reconnection helpers', () => {
  it('builds the reconnect URL from the highest received sequence', () => {
    expect(lastEventSeq([{ seq: 2 }, { seq: 7 }, { seq: 4 }])).toBe(7)
    expect(runEventsUrl('RUN-1', [{ seq: 2 }, { seq: 7 }])).toBe('/api/runs/RUN-1/events?after=7')
  })

  it('deduplicates replayed events and keeps sequence order', () => {
    const initial = [{ seq: 1 }, { seq: 3 }]
    expect(mergeRunEvent(initial, { seq: 3 })).toHaveLength(2)
    expect(mergeRunEvent(initial, { seq: 2 }).map((item) => item.seq)).toEqual([1, 2, 3])
  })
})

  it('never labels human escalation as approved', () => {
    expect(approvalPresentation('human_escalation', false)).toBe('review')
    expect(approvalPresentation('ready', false)).toBe('pending')
    expect(approvalPresentation('ready', true)).toBe('approved')
    expect(scoreDeltaLabel(65)).toBe('+65')
    expect(scoreDeltaLabel(-4)).toBe('-4')
  })
