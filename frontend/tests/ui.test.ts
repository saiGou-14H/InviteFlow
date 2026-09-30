import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import UserWorkspace from '../src/components/UserWorkspace.vue'
import AdminView from '../src/views/AdminView.vue'
import App from '../src/App.vue'
import { locale } from '../src/i18n'

const id = '8d1d6e36-12e7-4d19-aee0-2b4d5294d258'
const json = (payload: unknown, status = 200) => new Response(JSON.stringify(payload), { status })
let hasUser = false
let hasAdmin = false
let businessStatus = 501
let unknownSnapshot = false
const session = (role: 'user' | 'admin') => json({ status: 'ok', data: { role, actor_id: id, csrf_token: `${role}-csrf` } })
const fetcher = vi.fn<typeof fetch>()
enableAutoUnmount(afterEach)
beforeEach(() => {
  locale.value = 'zh'
  hasUser = false; hasAdmin = false; businessStatus = 501; unknownSnapshot = false
  fetcher.mockReset()
  fetcher.mockImplementation(async (input) => {
    const path = String(input)
    if (path === '/healthz') return json({ status: 'ok' })
    if (path === '/readyz') return json({ error: {} }, 503)
    if (path === '/api/v1/capabilities') return json({ service: 'inviteflow', version: '0.1.0', supported_roles: ['user', 'admin'], implemented: [], reserved_hooks: ['claim.confirm'] })
    if (path === '/api/v1/public/session') return hasUser ? session('user') : json({}, 401)
    if (path === '/api/v1/staff/session') return hasAdmin ? session('admin') : json({}, 401)
    if (path === '/api/v1/public/sessions') { hasUser = true; return session('user') }
    if (path === '/api/v1/staff/login') { hasAdmin = true; return session('admin') }
    if (path === '/api/v1/public/logout') { hasUser = false; return json({ status: 'ok' }) }
    if (path === '/api/v1/staff/logout') { hasAdmin = false; return json({ status: 'ok' }) }
    if (businessStatus === 501) return json({ error: { code: 'HOOK_NOT_IMPLEMENTED', message: 'SECRET raw backend error', hook: 'PRIVATE token' } }, 501)
    if (path === `/api/v1/claims/${id}`) return json({ status: 'ok', data: { status: unknownSnapshot ? 'SECRET raw status' : 'processing', password: 'PRIVATE', code: 'DO-NOT-DISPLAY' } })
    return json({ status: 'accepted', data: { operation_id: id, password: 'PRIVATE', codes: ['DO-NOT-DISPLAY'] } }, businessStatus)
  })
  vi.stubGlobal('fetch', fetcher)
})
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('user workspace', () => {
  it('requires explicit session creation and disables business forms until authenticated', async () => {
    const wrapper = mount(UserWorkspace)
    await flushPromises()
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/public/session')
    expect(wrapper.find('form button.primary').attributes('disabled')).toBeDefined()
    expect(fetcher.mock.calls.some(call => call[0] === '/api/v1/public/sessions')).toBe(false)
    await wrapper.get('.session-strip button.primary').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('用户会话已建立')
    expect(wrapper.find('form button.primary').attributes('disabled')).toBeUndefined()
    const create = fetcher.mock.calls.find(call => call[0] === '/api/v1/public/sessions')
    expect(create?.[1]?.body).toBe('{}')
  })
  it('submits deduplicated case-preserving codes with CSRF and shows an honest 501', async () => {
    hasUser = true
    const wrapper = mount(UserWorkspace)
    await flushPromises()
    await wrapper.get('#invite-codes').setValue(' AbC \n\nAbC\nabc\nLast ')
    await wrapper.findAll('form')[0].trigger('submit')
    await flushPromises()
    const call = fetcher.mock.calls.find(call => call[0] === '/api/v1/claims/batches')
    expect(JSON.parse(call?.[1]?.body as string)).toEqual({ codes: ['AbC', 'abc', 'Last'] })
    expect(call?.[1]?.headers).toHaveProperty('X-CSRF-Token', 'user-csrf')
    expect(call?.[1]?.headers).toHaveProperty('Idempotency-Key')
    expect(wrapper.get('[role=alert]').text()).toContain('未接入业务 Hook（501）')
    expect(wrapper.text()).not.toContain('SECRET')
    expect(wrapper.text()).not.toContain('PRIVATE')
    expect(wrapper.find('pre').exists()).toBe(false)
    expect(wrapper.find('.operation').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('服务器已接收请求（202）')
  })
  it('rejects blank or excessive code input without calling the hook', async () => {
    hasUser = true
    const wrapper = mount(UserWorkspace)
    await flushPromises()
    await wrapper.findAll('form')[0].trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('请输入 1–100')
    await wrapper.get('#invite-codes').setValue(Array.from({ length: 101 }, (_, i) => `code${i}`).join('\n'))
    await wrapper.findAll('form')[0].trigger('submit')
    await flushPromises()
    expect(fetcher.mock.calls.some(call => call[0] === '/api/v1/claims/batches')).toBe(false)
  })
  it('supports safe 202 acknowledgment without displaying secret response fields', async () => {
    hasUser = true; businessStatus = 202
    const wrapper = mount(UserWorkspace)
    await flushPromises()
    await wrapper.get('#invite-codes').setValue('MY-CODE')
    await wrapper.findAll('form')[0].trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('不代表业务完成')
    expect(wrapper.get('.operation').text()).toContain(id)
    expect(wrapper.text()).not.toContain('PRIVATE')
    expect(wrapper.text()).not.toContain('DO-NOT-DISPLAY')
    expect((wrapper.get('#invite-codes').element as HTMLTextAreaElement).value).toBe('')
  })
  it('does not treat an unexpected 200 business response as accepted', async () => {
    hasUser = true; businessStatus = 200
    const wrapper = mount(UserWorkspace)
    await flushPromises()
    await wrapper.get('#invite-codes').setValue('MY-CODE')
    await wrapper.findAll('form')[0].trigger('submit')
    await flushPromises()
    expect(wrapper.get('[role=alert]').text()).toContain('无法识别')
    expect(wrapper.find('.operation').exists()).toBe(false)
  })
  it('validates own claim UUID, projects status only, and keeps future actions disabled', async () => {
    hasUser = true; businessStatus = 202
    const wrapper = mount(UserWorkspace)
    await flushPromises()
    await wrapper.get('#claim-id').setValue('../invalid')
    await wrapper.findAll('form')[1].trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('格式正确的 UUID')
    await wrapper.get('#claim-id').setValue(id)
    await wrapper.findAll('form')[1].trigger('submit')
    await flushPromises()
    expect(wrapper.get('.snapshot').text()).toContain('处理中')
    expect(wrapper.text()).not.toContain('PRIVATE')
    for (const button of wrapper.findAll('.future-actions button')) expect(button.attributes('disabled')).toBeDefined()
    unknownSnapshot = true
    await wrapper.findAll('form')[1].trigger('submit')
    await flushPromises()
    expect(wrapper.get('.snapshot').text()).toContain('未提供可识别状态')
    expect(wrapper.text()).not.toContain('SECRET')
  })
  it('logs out with CSRF and clears in-memory codes', async () => {
    hasUser = true
    const wrapper = mount(UserWorkspace)
    await flushPromises()
    await wrapper.get('#invite-codes').setValue('SECRET-CODE')
    await wrapper.findAll('.session-strip button')[1].trigger('click')
    await flushPromises()
    const call = fetcher.mock.calls.find(call => call[0] === '/api/v1/public/logout')
    expect(call?.[1]?.headers).toHaveProperty('X-CSRF-Token', 'user-csrf')
    expect(wrapper.text()).toContain('已退出该会话')
    expect((wrapper.get('#invite-codes').element as HTMLTextAreaElement).value).toBe('')
  })
})

describe('admin workspace', () => {
  it('signs in with a real form, clears password, and signs out', async () => {
    const wrapper = mount(AdminView)
    await flushPromises()
    await wrapper.get('#username').setValue('operator')
    await wrapper.get('#password').setValue('private-password')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    const login = fetcher.mock.calls.find(call => call[0] === '/api/v1/staff/login')
    expect(JSON.parse(login?.[1]?.body as string)).toEqual({ username: 'operator', password: 'private-password' })
    expect(wrapper.find('#password').exists()).toBe(false)
    expect(wrapper.text()).toContain('管理员会话已建立')
    await wrapper.findAll('.session-strip button')[1].trigger('click')
    await flushPromises()
    expect((wrapper.get('#password').element as HTMLInputElement).value).toBe('')
    expect(wrapper.find('#quantity').exists()).toBe(false)
    const logout = fetcher.mock.calls.find(call => call[0] === '/api/v1/staff/logout')
    expect(logout?.[1]?.headers).toHaveProperty('X-CSRF-Token', 'admin-csrf')
  })
  it('submits admin CDK and reconcile hooks and displays 501 without false success', async () => {
    hasAdmin = true
    const wrapper = mount(AdminView)
    await flushPromises()
    await wrapper.get('#quantity').setValue('5')
    await wrapper.get('#max-uses').setValue('2')
    await wrapper.findAll('form')[0].trigger('submit')
    await flushPromises()
    const batch = fetcher.mock.calls.find(call => call[0] === '/api/v1/admin/cdk-batches')
    expect(JSON.parse(batch?.[1]?.body as string)).toEqual({ quantity: 5, max_uses: 2, expires_at: null })
    expect(batch?.[1]?.headers).toHaveProperty('X-CSRF-Token', 'admin-csrf')
    expect(wrapper.get('[role=alert]').text()).toContain('501')
    await wrapper.get('#reconcile-claim').setValue(id)
    await wrapper.get('#reason').setValue('  核查状态  ')
    await wrapper.findAll('form')[1].trigger('submit')
    await flushPromises()
    const reconcile = fetcher.mock.calls.find(call => call[0] === `/api/v1/admin/claims/${id}/reconcile`)
    expect(JSON.parse(reconcile?.[1]?.body as string)).toEqual({ reason: '核查状态' })
    expect(reconcile?.[1]?.headers).toHaveProperty('Idempotency-Key')
    expect(wrapper.get('[role=alert]').text()).toContain('501')
    expect(wrapper.text()).not.toContain('SECRET')
    expect(wrapper.text()).not.toContain('服务器已接收请求')
  })
  it('validates integer bounds, UUID and reason before invoking hooks', async () => {
    hasAdmin = true
    const wrapper = mount(AdminView)
    await flushPromises()
    await wrapper.get('#quantity').setValue('1001')
    await wrapper.findAll('form')[0].trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('1–1000 的整数')
    await wrapper.get('#reconcile-claim').setValue(id)
    await wrapper.get('#reason').setValue('  ')
    await wrapper.findAll('form')[1].trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('1–500 个字符')
    expect(fetcher.mock.calls.some(call => String(call[0]).includes('/admin/'))).toBe(false)
  })
})

it('switches real UI labels to English and displays genuine readiness/capability values', async () => {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: UserWorkspace }, { path: '/admin', component: AdminView }] })
  await router.push('/'); await router.isReady()
  const wrapper = mount(App, { global: { plugins: [router] } })
  await flushPromises()
  expect(wrapper.text()).toContain('基础能力已接入')
  expect(wrapper.get('.service-panel').text()).toContain('未就绪')
  expect(wrapper.get('.service-panel').text()).toContain('0 / 1')
  await wrapper.get('.language').trigger('click')
  expect(wrapper.text()).toContain('Your invitation starts here.')
  expect(wrapper.text()).toContain('Submit invite codes')
  expect(wrapper.text()).toContain('Not ready')
  expect(document.documentElement.lang).toBe('en')
  await router.push('/admin'); await flushPromises()
  expect(wrapper.text()).toContain('Admin sign in')
  expect(wrapper.text()).toContain('The backend must enforce authorization')
})
