export function flattenVisualNodes(nodes, scopePath = '', parentX = 24, parentY = 30) {
  const flattened = []
  for (const [index, node] of (nodes || []).entries()) {
    const visualId = scopePath ? `${scopePath}/${node.id}` : node.id
    const x = Number(node.ui?.x ?? (scopePath ? parentX + 30 + (index % 3) * 220 : 48 + (index % 4) * 230))
    const y = Number(node.ui?.y ?? (scopePath ? parentY + 130 + Math.floor(index / 3) * 120 : 54 + Math.floor(index / 4) * 130))
    flattened.push({ ...node, visualId, node_id: node.id, scopePath, parentLoopId: scopePath ? scopePath.split('/').pop() : '', x, y })
    if (node.type === 'loop' && Array.isArray(node.body?.nodes)) {
      flattened.push(...flattenVisualNodes(node.body.nodes, visualId, x, y))
    }
  }
  return flattened
}

export function computeLoopZones(nodes) {
  return (nodes || []).filter((node) => node.type === 'loop').map((loop) => {
    const children = nodes.filter((node) => node.scopePath === loop.visualId)
    if (!children.length) {
      return { id: loop.visualId, label: loop.node_id, x: loop.x - 20, y: loop.y + 60, width: 260, height: 130 }
    }
    const xs = children.map((node) => node.x)
    const ys = children.map((node) => node.y)
    return {
      id: loop.visualId,
      label: loop.node_id,
      x: Math.max(8, Math.min(loop.x + 18, ...xs) - 18),
      y: Math.max(8, Math.min(loop.y + 105, ...ys) - 24),
      width: Math.max(260, Math.max(loop.x + 22, ...xs.map((value) => value + 184)) - Math.min(loop.x, ...xs) + 38),
      height: Math.max(110, Math.max(...ys.map((value) => value + 84)) - Math.min(loop.y + 105, ...ys) + 36),
    }
  })
}

export function findNodeByVisualId(nodes, visualId, scopePath = '') {
  for (const node of nodes || []) {
    const currentId = scopePath ? `${scopePath}/${node.id}` : node.id
    if (currentId === visualId) return { node, nodes, scopePath }
    if (node.type === 'loop' && Array.isArray(node.body?.nodes)) {
      const found = findNodeByVisualId(node.body.nodes, visualId, currentId)
      if (found) return found
    }
  }
  return null
}

export function applyVisualPositions(nodes, visualNodes, scopePath = '') {
  const positions = new Map((visualNodes || []).map((node) => [node.visualId, { x: Math.round(node.x), y: Math.round(node.y) }]))
  for (const node of nodes || []) {
    const visualId = scopePath ? `${scopePath}/${node.id}` : node.id
    node.ui = positions.get(visualId) || node.ui
    if (node.type === 'loop' && Array.isArray(node.body?.nodes)) applyVisualPositions(node.body.nodes, visualNodes, visualId)
  }
  return nodes
}

export function createWorkflowNode(draft, loopVisual = null, index = 0) {
  const id = String(draft.id || '').trim()
  if (!/^[A-Za-z0-9_-]{2,40}$/.test(id)) throw new Error('节点 ID 需为 2–40 位字母、数字、下划线或短横线')
  const node = {
    id, type: draft.type, depends_on: [],
    priority: 80,
    ui: {
      x: loopVisual ? loopVisual.x + 30 + (index % 3) * 220 : 48 + (index % 4) * 230,
      y: loopVisual ? loopVisual.y + 140 + Math.floor(index / 3) * 120 : 54 + Math.floor(index / 4) * 130,
    },
  }
  if (draft.type === 'agent') node.agent_id = draft.agent_id
  if (draft.type === 'tool') Object.assign(node, { tool_id: draft.tool_id, arguments: {} })
  if (draft.type === 'subworkflow') node.workflow_id = draft.workflow_id
  if (draft.type === 'condition') {
    node.when = {
      field: draft.condition_field,
      operator: 'eq',
      value: draft.condition_value === 'true' ? true : draft.condition_value === 'false' ? false : draft.condition_value,
    }
  }
  if (draft.type === 'loop') {
    node.max_iterations = 3
    node.until = { field: 'verification.verdict', operator: 'eq', value: 'pass' }
    node.on_exhausted = 'human_escalation'
    node.body = { nodes: [{ id: `${id}_STEP`, type: 'condition', depends_on: [], when: { field: 'event.ready', operator: 'eq', value: true } }] }
  }
  return node
}

export function addWorkflowNode(definition, draft, visualNodes = []) {
  let nodes = definition.nodes
  let scopePath = ''
  if (draft.loop_id) {
    const loopEntry = findNodeByVisualId(definition.nodes, draft.loop_id)
    if (!loopEntry || loopEntry.node.type !== 'loop' || !Array.isArray(loopEntry.node.body?.nodes)) throw new Error('目标循环体不存在')
    nodes = loopEntry.node.body.nodes
    scopePath = draft.loop_id
  }
  if (nodes.some((node) => node.id === String(draft.id || '').trim())) throw new Error(`当前作用域已经存在节点 ${draft.id}`)
  const loopVisual = draft.loop_id ? visualNodes.find((node) => node.visualId === draft.loop_id) : null
  const node = createWorkflowNode(draft, loopVisual, nodes.length)
  nodes.push(node)
  return { definition, node, scopePath }
}

export function connectWorkflowNodes(definition, sourceVisualId, targetVisualId) {
  const targetEntry = findNodeByVisualId(definition.nodes, targetVisualId)
  const sourceEntry = findNodeByVisualId(definition.nodes, sourceVisualId)
  if (!targetEntry || !sourceEntry) throw new Error('连接节点不存在')
  if (sourceEntry.scopePath !== targetEntry.scopePath) throw new Error('只能在同一层级或同一个循环体内建立依赖')
  const dependencies = new Set(targetEntry.node.depends_on || [])
  dependencies.add(sourceEntry.node.id)
  targetEntry.node.depends_on = [...dependencies]
  return { definition, source: sourceEntry.node, target: targetEntry.node }
}
