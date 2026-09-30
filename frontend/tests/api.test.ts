import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../src/api'
import type { Role } from '../src/utils/format'

const id = '8d1d6e36-12e7-4d19-aee0-2b4d5294d258'
const json = (payload: unknown, status = 200) => new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
const session = (role: Role, token = `${role}-csrf`) => json({ status: 'ok', data: { role, actor_id: id, csrf_token: token } })
function setup() {
  const fetcher = vi.fn<typeof fetch>()
  const onSession = vi.fn()
  return { fetcher, onSession, api: new ApiClient(fetcher, onSession) }
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

describe('session and CSRF contract', () => {
  it('creates public sessions with {}, browser Origin, and same-origin credentials', async () => {
    const { fetcher, api } = setup()
    fetcher.mockResolvedValueOnce(session('user'))
    expect(await api.createUserSession()).toEqual({ role: 'user', actor_id: id })
    expect(fetcher).toHaveBeenCalledWith('/api/v1/public/sessions', expect.objectContaining({ method: 'POST', body: '{}', credentials: 'same-origin', redirect: 'error', cache: 'no-store' }))
    expect(fetcher.mock.calls[0][1]?.headers).toEqual({ Accept: 'application/json', 'Content-Type': 'application/json' })
  })
  it('sends admin login credentials without CSRF or idempotency headers', async () => {
    const { fetcher, api } = setup()
    fetcher.mockResolvedValueOnce(session('admin'))
    await api.login('operator', 'password')
    const [url, options] = fetcher.mock.calls[0]
    expect(url).toBe('/api/v1/staff/login')
    expect(JSON.parse(options?.body as string)).toEqual({ username: 'operator', password: 'password' })
    expect(options?.headers).not.toHaveProperty('X-CSRF-Token')
    expect(options?.headers).not.toHaveProperty('Idempotency-Key')
    expect(options?.headers).not.toHaveProperty('Origin')
  })
  it('restores separate tokens and sends matching role token plus unique command UUID', async () => {
    const { fetcher, api, onSession } = setup()
    fetcher.mockResolvedValueOnce(session('user')).mockResolvedValueOnce(session('admin')).mockImplementation(async () => json({ status: 'accepted' }, 202))
    await api.restore('user'); await api.restore('admin')
    await api.request('/claims/batches', { method: 'POST', body: { codes: ['AbC'] }, role: 'user', command: true })
    await api.request('/admin/cdk-batches', { method: 'POST', body: { quantity: 1 }, role: 'admin', command: true })
    const userHeaders = fetcher.mock.calls[2][1]?.headers as Record<string, string>
    const adminHeaders = fetcher.mock.calls[3][1]?.headers as Record<string, string>
    expect(userHeaders['X-CSRF-Token']).toBe('user-csrf')
    expect(adminHeaders['X-CSRF-Token']).toBe('admin-csrf')
    expect(userHeaders['Idempotency-Key']).toMatch(/^[0-9a-f-]{36}$/)
    expect(adminHeaders['Idempotency-Key']).not.toBe(userHeaders['Idempotency-Key'])
    expect(onSession).toHaveBeenCalledWith('user', { role: 'user', actor_id: id })
    expect(onSession.mock.calls.some(call => JSON.stringify(call).includes('csrf'))).toBe(false)
  })
  it('sends distinct command UUIDs on HTTP origins without crypto.randomUUID', async () => {
    const getRandomValues = crypto.getRandomValues.bind(crypto)
    vi.stubGlobal('crypto', { getRandomValues })
    const weakRandom = vi.spyOn(Math, 'random')
    const { fetcher, api } = setup()
    fetcher.mockResolvedValueOnce(session('user')).mockImplementation(async () => json({}, 202))
    await api.restore('user')
    await api.request('/claims/batches', { method: 'POST', role: 'user', command: true })
    await api.request('/claims/batches', { method: 'POST', role: 'user', command: true })
    const keys = fetcher.mock.calls.slice(1).map(call => (call[1]?.headers as Record<string, string>)['Idempotency-Key'])
    expect(keys[0]).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    expect(keys[1]).not.toBe(keys[0])
    expect(weakRandom).not.toHaveBeenCalled()
  })
  it('rejects commands safely when secure random generation is unavailable', async () => {
    vi.stubGlobal('crypto', { getRandomValues: () => { throw new Error('PRIVATE_RUNTIME_DETAIL') } })
    const { fetcher, api } = setup()
    fetcher.mockResolvedValueOnce(session('user'))
    await api.restore('user')
    await expect(api.request('/claims/batches', { method: 'POST', role: 'user', command: true }))
      .rejects.toMatchObject({ key: 'unavailable', message: 'unavailable' })
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('never sends a mutation without the required role token', async () => {
    const { fetcher, api } = setup()
    await expect(api.request('/claims/batches', { method: 'POST', role: 'user', command: true })).rejects.toMatchObject({ key: 'unauthorized' })
    await expect(api.request('/admin/cdk-batches', { method: 'POST', anonymous: true })).rejects.toMatchObject({ key: 'unauthorized' })
    expect(fetcher).not.toHaveBeenCalled()
  })
  it('clears only the expired role on 401', async () => {
    const { fetcher, api, onSession } = setup()
    fetcher.mockResolvedValueOnce(session('user')).mockResolvedValueOnce(session('admin')).mockResolvedValueOnce(json({ error: {} }, 401)).mockResolvedValueOnce(json({}, 202))
    await api.restore('user'); await api.restore('admin')
    await expect(api.request(`/claims/${id}`, { role: 'user' })).rejects.toMatchObject({ key: 'unauthorized' })
    expect(onSession).toHaveBeenCalledWith('user', null)
    await expect(api.request('/claims/batches', { method: 'POST', role: 'user' })).rejects.toMatchObject({ key: 'unauthorized' })
    await api.request('/admin/cdk-batches', { method: 'POST', role: 'admin', command: true })
    expect(fetcher.mock.calls[3][1]?.headers).toHaveProperty('X-CSRF-Token', 'admin-csrf')
  })
  it('treats expired restore as logged out, but does not swallow infrastructure failures', async () => {
    const { fetcher, api } = setup()
    fetcher.mockResolvedValueOnce(json({}, 401)).mockResolvedValueOnce(json({}, 503))
    expect(await api.restore('user')).toBeNull()
    await expect(api.restore('admin')).rejects.toMatchObject({ key: 'unavailable' })
  })
  it('sends logout CSRF without a business idempotency key and clears session', async () => {
    const { fetcher, api, onSession } = setup()
    fetcher.mockResolvedValueOnce(session('admin')).mockResolvedValueOnce(json({ status: 'ok' }))
    await api.restore('admin'); await api.logout('admin')
    expect(fetcher.mock.calls[1][0]).toBe('/api/v1/staff/logout')
    expect(fetcher.mock.calls[1][1]?.headers).toHaveProperty('X-CSRF-Token', 'admin-csrf')
    expect(fetcher.mock.calls[1][1]?.headers).not.toHaveProperty('Idempotency-Key')
    expect(onSession).toHaveBeenLastCalledWith('admin', null)
    await expect(api.logout('admin')).rejects.toMatchObject({ key: 'unauthorized' })
  })
  it('retains session if logout fails rather than claiming cookie deletion', async () => {
    const { fetcher, api, onSession } = setup()
    fetcher.mockResolvedValueOnce(session('user')).mockRejectedValueOnce(new Error('offline'))
    await api.restore('user')
    await expect(api.logout('user')).rejects.toMatchObject({ key: 'network' })
    expect(onSession).not.toHaveBeenCalledWith('user', null)
  })
  it('rejects a mismatched role or missing CSRF token', async () => {
    const { fetcher, api } = setup()
    fetcher.mockResolvedValueOnce(session('admin')).mockResolvedValueOnce(json({ status: 'ok', data: { role: 'user', actor_id: id } }))
    await expect(api.restore('user')).rejects.toMatchObject({ key: 'invalidResponse' })
    await expect(api.createUserSession()).rejects.toMatchObject({ key: 'invalidResponse' })
  })
  it('ignores stale 401 invalidation after a newer restore succeeds', async () => {
    const { fetcher, api, onSession } = setup()
    let finishOld!: (value: Response) => void
    fetcher.mockImplementationOnce(() => new Promise<Response>(resolve => { finishOld = resolve }))
      .mockResolvedValueOnce(session('user', 'fresh-token'))
      .mockResolvedValueOnce(json({}, 202))
    const oldRestore = api.restore('user')
    await api.restore('user')
    finishOld(json({}, 401))
    expect(await oldRestore).toBeNull()
    expect(onSession).not.toHaveBeenCalledWith('user', null)
    await api.request('/claims/batches', { method: 'POST', role: 'user', command: true })
    expect(fetcher.mock.calls[2][1]?.headers).toHaveProperty('X-CSRF-Token', 'fresh-token')
  })
  it('never writes session, credentials, or codes to local/session storage', async () => {
    const storage = vi.spyOn(Storage.prototype, 'setItem')
    const { fetcher, api } = setup()
    fetcher.mockResolvedValueOnce(session('user')).mockResolvedValueOnce(json({}, 202))
    await api.createUserSession()
    await api.request('/claims/batches', { method: 'POST', body: { codes: ['SECRET'] }, role: 'user', command: true })
    expect(storage).not.toHaveBeenCalled()
  })
})

describe('safe errors and status probes', () => {
  it('maps 501 to a safe hook message without rendering raw server secrets', async () => {
    const { fetcher, api } = setup()
    fetcher.mockResolvedValue(json({ error: { code: 'HOOK_NOT_IMPLEMENTED', message: 'password=SECRET', hook: 'PRIVATE-TOKEN' } }, 501))
    await expect(api.request('/claims/batches')).rejects.toMatchObject({ key: 'hookPending', status: 501, message: 'hookPending' })
  })
  it.each([[403, 'forbidden'], [422, 'invalidRequest'], [429, 'rateLimited'], [503, 'unavailable'], [404, 'unexpected']])('maps status %s to safe key %s', async (status, key) => {
    const { fetcher, api } = setup()
    fetcher.mockResolvedValue(json({}, status as number))
    await expect(api.request('/capabilities')).rejects.toMatchObject({ key })
  })
  it('rejects malformed success JSON instead of fabricating success', async () => {
    const { fetcher, api } = setup()
    fetcher.mockResolvedValue(new Response('<html>private details</html>'))
    await expect(api.request('/capabilities')).rejects.toMatchObject({ key: 'invalidResponse' })
  })
  it('keeps health/readiness outside the API prefix and handles 503', async () => {
    const { fetcher, api } = setup()
    fetcher.mockResolvedValueOnce(json({ status: 'ok' })).mockResolvedValueOnce(json({ error: {} }, 503))
    expect(await api.probe('/healthz')).toBe('ok')
    expect(await api.probe('/readyz')).toBe('unavailable')
    expect(fetcher.mock.calls.map(call => call[0])).toEqual(['/healthz', '/readyz'])
  })
  it('rejects non-JSON health output and reports network errors as unconfirmed', async () => {
    const { fetcher, api } = setup()
    fetcher.mockResolvedValueOnce(new Response('html')).mockRejectedValueOnce(new Error('offline'))
    expect(await api.probe('/healthz')).toBe('error')
    expect(await api.probe('/readyz')).toBe('error')
  })
  it('validates capabilities before displaying actual counts', async () => {
    const { fetcher, api } = setup()
    fetcher.mockResolvedValueOnce(json({ service: 'inviteflow', version: '0.1.0', supported_roles: ['user', 'admin'], implemented: [], reserved_hooks: ['claim.confirm'] })).mockResolvedValueOnce(json({ implemented: 15 }))
    expect((await api.capabilities()).implemented).toEqual([])
    await expect(api.capabilities()).rejects.toMatchObject({ key: 'invalidResponse' })
  })
  it('aborts stalled requests with a safe timeout error', async () => {
    vi.useFakeTimers()
    const fetcher = vi.fn<typeof fetch>((_input, init) => new Promise((_resolve, reject) => { init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))) }))
    const api = new ApiClient(fetcher, undefined, 20)
    const assertion = expect(api.request('/capabilities')).rejects.toMatchObject({ key: 'timeout' })
    await vi.advanceTimersByTimeAsync(21)
    await assertion
  })
  it.each(['https://external.test/', '//external.test/', '/path?secret=1'])('rejects an unsafe path %s', async path => {
    const { fetcher, api } = setup()
    await expect(api.request(path)).rejects.toMatchObject({ key: 'invalidRequest' })
    expect(fetcher).not.toHaveBeenCalled()
  })
})
