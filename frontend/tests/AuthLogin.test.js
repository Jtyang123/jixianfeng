import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AuthLogin from '../src/views/AuthLogin.vue'

describe('AuthLogin demo credentials', () => {
  it('prefills the demo administrator account and password', () => {
    const wrapper = mount(AuthLogin)
    expect(wrapper.get('input[autocomplete="username"]').element.value).toBe('admin')
    expect(wrapper.get('input[autocomplete="current-password"]').element.value).toBe('4JHcg071wjryH1e_uupnZX1Q')
    expect(wrapper.get('[data-testid="demo-credentials"]').text()).toContain('admin')
    expect(wrapper.get('[data-testid="demo-credentials"]').text()).toContain('4JHcg071wjryH1e_uupnZX1Q')
  })
})