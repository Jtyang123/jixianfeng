import test from 'node:test'
import assert from 'node:assert/strict'
import {
  addWorkflowNode, applyVisualPositions, computeLoopZones, connectWorkflowNodes,
  findNodeByVisualId, flattenVisualNodes,
} from '../src/utils/workflowCanvas.js'

function definition() {
  return { nodes: [
    { id: 'A01', type: 'agent', agent_id: 'demand_agent', depends_on: [], ui: { x: 10, y: 20 } },
    { id: 'LOOP', type: 'loop', depends_on: ['A01'], ui: { x: 240, y: 20 }, body: { nodes: [
      { id: 'A02', type: 'agent', agent_id: 'matching_agent', depends_on: [], ui: { x: 270, y: 160 } },
      { id: 'A03', type: 'agent', agent_id: 'verifier_agent', depends_on: ['A02'], ui: { x: 490, y: 160 } },
    ] } },
  ] }
}

test('flattens and scopes nested loop nodes', () => {
  const nodes = flattenVisualNodes(definition().nodes)
  assert.deepEqual(nodes.map((item) => item.visualId), ['A01', 'LOOP', 'LOOP/A02', 'LOOP/A03'])
  assert.equal(nodes[2].scopePath, 'LOOP')
  assert.deepEqual(computeLoopZones(nodes).map((item) => item.id), ['LOOP'])
})

test('adds a node inside a loop with parent-relative coordinates', () => {
  const def = definition()
  const visual = flattenVisualNodes(def.nodes)
  const result = addWorkflowNode(def, {
    id: 'REWORK', type: 'agent', agent_id: 'rework_policy', loop_id: 'LOOP',
  }, visual)
  assert.equal(result.scopePath, 'LOOP')
  assert.equal(def.nodes[1].body.nodes[2].id, 'REWORK')
  assert.ok(def.nodes[1].body.nodes[2].ui.x > def.nodes[1].ui.x)
  assert.equal(findNodeByVisualId(def.nodes, 'LOOP/REWORK').node.agent_id, 'rework_policy')
})

test('connects nodes only within the same scope', () => {
  const def = definition()
  connectWorkflowNodes(def, 'LOOP/A02', 'LOOP/A03')
  assert.deepEqual(def.nodes[1].body.nodes[1].depends_on, ['A02'])
  assert.throws(() => connectWorkflowNodes(def, 'A01', 'LOOP/A03'), /同一层级/)
})

test('writes visual positions back into nested JSON definitions', () => {
  const def = definition()
  const visual = flattenVisualNodes(def.nodes)
  visual.find((item) => item.visualId === 'LOOP/A03').x = 777
  applyVisualPositions(def.nodes, visual)
  assert.equal(def.nodes[1].body.nodes[1].ui.x, 777)
})
