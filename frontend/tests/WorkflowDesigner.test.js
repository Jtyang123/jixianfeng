import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import WorkflowDesigner from '../src/views/WorkflowDesigner.vue'

const api = vi.hoisted(() => {
  const created = {
    id: 'TPL-E2E-instance',
    name: 'E2E 模板实例',
    description: '组件交互测试',
    stage: 'standalone',
    enabled: true,
    version: 1,
    definition: {
      nodes: [
        { id: 'A01', type: 'agent', agent_id: 'demand_agent', depends_on: [], priority: 90, ui: { x: 20, y: 20 } },
        { id: 'TOOL', type: 'tool', tool_id: 'inventory_query', arguments: {}, depends_on: [], priority: 80, ui: { x: 260, y: 20 } },
      ],
    },
  }
  return {
    created,
    workflows: vi.fn(async () => [created]),
    agents: vi.fn(async () => []),
    tools: vi.fn(async () => [{ id: 'inventory_query', name: '库存查询' }]),
    workflowTemplates: vi.fn(async () => [{ id: 'TPL-E2E', name: 'E2E 模板', description: '测试', stage: 'standalone' }]),
    instantiateWorkflowTemplate: vi.fn(async () => created),
    saveWorkflow: vi.fn(async () => ({ ...created, version: 2 })),
    runWorkflow: vi.fn(async () => ({ status: 'success', completed: ['A01', 'TOOL'], metrics: { completed_nodes: 2, skipped_nodes: 0, failed_nodes: 0, peak_concurrency: 1, end_to_end_ms: 12 } })),
    validateWorkflow: vi.fn(async () => ({ execution_order: ['A01', 'TOOL'], definition: created.definition })),
    deleteWorkflow: vi.fn(async () => ({ deleted: true })),
  }
})

vi.mock('../src/api', () => ({ api }))

describe('WorkflowDesigner template interaction', () => {
  afterEach(() => vi.restoreAllMocks())

  it('instantiates a template, connects nodes, saves and test-runs it', async () => {
    vi.spyOn(window, 'prompt').mockReturnValue('E2E 实例')
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = mount(WorkflowDesigner, { props: { user: { role: 'admin' } } })
    await flushPromises()

    const instantiate = wrapper.findAll('button').find((button) => button.text().includes('实例化'))
    expect(instantiate).toBeTruthy()
    await instantiate.trigger('click')
    await flushPromises()
    expect(api.instantiateWorkflowTemplate).toHaveBeenCalledWith('TPL-E2E', { name: 'E2E 实例' })

    const outputs = wrapper.findAll('.workflow-node-port--output')
    const inputs = wrapper.findAll('.workflow-node-port--input')
    expect(outputs.length).toBeGreaterThanOrEqual(2)
    expect(inputs.length).toBeGreaterThanOrEqual(2)
    outputs[0].element.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0, clientX: 30, clientY: 30 }))
    inputs[1].element.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0, clientX: 300, clientY: 50 }))
    await flushPromises()

    const saveButton = wrapper.findAll('button').find((button) => button.text().includes('保存并发布'))
    await saveButton.trigger('click')
    await flushPromises()
    const savedDefinition = api.saveWorkflow.mock.calls.at(-1)[1].definition
    expect(savedDefinition.nodes[1].depends_on).toContain('A01')

    const runButton = wrapper.findAll('button').find((button) => button.text().includes('保存并试运行'))
    await runButton.trigger('click')
    await flushPromises()
    expect(api.runWorkflow).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('试运行完成')
  })
})
