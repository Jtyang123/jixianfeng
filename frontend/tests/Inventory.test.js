import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import Inventory from '../src/views/Inventory.vue'

const api = vi.hoisted(() => ({
  inventory: vi.fn(async () => ({
    snapshot: { id: 1, label: 'test snapshot', row_count: 1 },
    summary: [{ material_id: 1, code: 'MAT-001', name: '测试物资', category: '生活保障', base_unit: '件', total_quantity: 10, locked_quantity: 4, available_quantity: 6, warehouse_count: 1, updated_at: '2026-11-09T10:00:00+08:00' }],
    items: [{ id: 1, warehouse_id: 'WH-A', warehouse_name: '仓库 A', material_id: 1, material_name: '测试物资', material_code: 'MAT-001', quantity: 10, locked_quantity: 4, unit: '件', status: 'available' }],
    warehouses: [{ id: 'WH-A', name: '仓库 A', region: '测试区', status: 'open' }],
  })),
  materials: vi.fn(async () => []),
  inventoryReservations: vi.fn(async () => ({
    count: 2,
    totals: [],
    items: [
      { id: 'RES-1', run_id: 'RUN-TEST', material_name: '测试物资', warehouse_name: '仓库 A', warehouse_id: 'WH-A', quantity: 2, base_unit: '件', run_status: 'approved', expires_at: '2026-11-10T10:00:00+08:00' },
      { id: 'RES-2', run_id: 'RUN-TEST', material_name: '测试物资', warehouse_name: '仓库 A', warehouse_id: 'WH-A', quantity: 2, base_unit: '件', run_status: 'approved', expires_at: '2026-11-10T10:00:00+08:00' },
    ],
  })),
  releaseReservation: vi.fn(async () => ({ count: 2 })),
  createInventoryEntry: vi.fn(),
  updateInventoryEntry: vi.fn(),
  deleteInventoryEntry: vi.fn(),
  uploadInventory: vi.fn(),
}))

vi.mock('../src/api', () => ({
  api,
  formatNumber: (value) => String(value ?? 0),
  formatTime: (value) => String(value ?? ''),
}))

describe('Inventory reservation management', () => {
  afterEach(() => vi.restoreAllMocks())

  it('opens reservations and releases all reservations for a run', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = mount(Inventory)
    await flushPromises()

    const tab = wrapper.findAll('button').find((button) => button.text().includes('库存预留 2'))
    expect(tab).toBeTruthy()
    await tab.trigger('click')
    expect(wrapper.text()).toContain('RUN-TEST')

    const release = wrapper.findAll('button').find((button) => button.text().includes('释放该运行预留'))
    expect(release).toBeTruthy()
    await release.trigger('click')
    await flushPromises()

    expect(api.releaseReservation).toHaveBeenCalledWith('RUN-TEST')
    expect(wrapper.text()).toContain('已释放 2 条库存预留')
  })
})