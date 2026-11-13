import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import PlannerTracePanel from '../src/components/PlannerTracePanel.vue'

describe('PlannerTracePanel', () => {
  it('renders initial plan, 11 decisions and one replan marker', () => {
    const steps = [
      'demand_plan', 'knowledge_search', 'inventory_query', 'route_query',
      'vehicle_query', 'generate_plans', 'verify_plans', 'delivery_package',
    ].map((tool, index) => ({ id: `S${index + 1}`, tool, reason: `${tool} reason` }))
    const decisions = Array.from({ length: 11 }, (_, index) => ({
      step: { id: `S${(index % steps.length) + 1}`, tool: steps[index % steps.length].tool },
      decision: {
        decision: index === 6 ? 'replan' : (index === 10 ? 'stop' : 'continue'),
        reason: `decision ${index + 1}`,
        repair_policy: index === 6 ? 'route_failover' : '',
      },
      deterministic: index < 2,
    }))
    const wrapper = mount(PlannerTracePanel, {
      props: {
        plannerTrace: {
          initial_plan: { steps },
          decisions,
          executed_steps: 11,
        },
        plannerDecision: { replans: 1 },
        llmCallCount: 10,
      },
    })

    expect(wrapper.findAll('.planner-initial-steps article')).toHaveLength(8)
    expect(wrapper.findAll('[data-testid="planner-decision"]')).toHaveLength(11)
    expect(wrapper.findAll('.planner-decision-row--replan')).toHaveLength(1)
    expect(wrapper.text()).toContain('重规划')
    expect(wrapper.text()).toContain('计划内推进')
  })
})