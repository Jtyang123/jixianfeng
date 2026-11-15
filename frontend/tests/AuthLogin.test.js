import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import AuthLogin from '../src/views/AuthLogin.vue'

const sourcePath = resolve(process.cwd(), 'src/views/AuthLogin.vue')

describe('AuthLogin demo credentials', () => {
  it('never hardcodes a credential in the component source', () => {
    const source = readFileSync(sourcePath, 'utf8')
    // 密码只能来自构建期注入（VITE_DEMO_PASSWORD），不允许写成字面量
    expect(source).not.toMatch(/DEMO_PASSWORD\s*=\s*['"][^'"]+['"]/)
    expect(source).toMatch(/import\.meta\.env\.VITE_DEMO_PASSWORD/)
  })

  it('shows the credential hint only when a password was injected', () => {
    const wrapper = mount(AuthLogin)
    const credentials = wrapper.find('[data-testid="demo-credentials"]')
    expect(credentials.exists()).toBe(Boolean(import.meta.env.VITE_DEMO_PASSWORD))
    expect(wrapper.get('input[autocomplete="current-password"]').element.value)
      .toBe(import.meta.env.VITE_DEMO_PASSWORD || '')
  })
})
