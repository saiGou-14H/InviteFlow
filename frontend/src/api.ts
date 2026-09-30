import { isUuid, record, type Role, type Session } from './utils/format'

export type ErrorKey = 'network' | 'timeout' | 'hookPending' | 'unauthorized' | 'forbidden' | 'invalidRequest' | 'rateLimited' | 'unavailable' | 'unexpected' | 'invalidResponse'
export class ApiFailure extends Error {
  constructor(public readonly key: ErrorKey, public readonly status = 0) { super(key) }
}
export type ApiResult = { status: number; payload: unknown }
export type Capabilities = { service: string; version: string; implemented: string[]; reserved_hooks: string[] }
export type ProbeState = 'ok' | 'unavailable' | 'error'
export type RequestOptions = { method?: 'GET' | 'POST'; body?: unknown; role?: Role; command?: boolean; anonymous?: boolean }

/** No token, cookie, credential, code, or raw server error is persisted or logged. */
export class ApiClient {
  private readonly tokens: Partial<Record<Role, string>> = {}
  private readonly generations: Record<Role, number> = { user: 0, admin: 0 }
  constructor(
    private readonly fetcher: typeof fetch = (...args) => fetch(...args),
    private readonly onSession: (role: Role, session: Session | null) => void = () => {},
    private readonly timeoutMs = 15000,
  ) {}

  private clear(role: Role): void {
    delete this.tokens[role]
    this.generations[role]++
    this.onSession(role, null)
  }

  async request(path: string, options: RequestOptions = {}): Promise<ApiResult> {
    if (!path.startsWith('/') || path.startsWith('//') || /[?#\\]/.test(path)) throw new ApiFailure('invalidRequest')
    const method = options.method ?? 'GET'
    const requestGeneration = options.role ? this.generations[options.role] : undefined
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (options.body !== undefined) headers['Content-Type'] = 'application/json'
    if (method === 'POST') {
      const exempt = options.anonymous && (path === '/public/sessions' || path === '/staff/login')
      if (!exempt) {
        const token = options.role && this.tokens[options.role]
        if (!token) throw new ApiFailure('unauthorized', 401)
        headers['X-CSRF-Token'] = token
      }
      if (options.command && !exempt) headers['Idempotency-Key'] = crypto.randomUUID()
    }
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), this.timeoutMs)
    try {
      const response = await this.fetcher(`/api/v1${path}`, {
        method, headers, credentials: 'same-origin', cache: 'no-store', redirect: 'error',
        body: options.body === undefined ? undefined : JSON.stringify(options.body), signal: controller.signal,
      })
      if (!response.ok) {
        if (response.status === 401 && options.role && requestGeneration === this.generations[options.role]) this.clear(options.role)
        const key: ErrorKey = response.status === 501 ? 'hookPending'
          : response.status === 401 ? 'unauthorized' : response.status === 403 ? 'forbidden'
          : response.status === 400 || response.status === 422 ? 'invalidRequest'
          : response.status === 429 ? 'rateLimited' : response.status >= 500 ? 'unavailable' : 'unexpected'
        // Deliberately do not consume error.message/hook: either can contain secrets.
        throw new ApiFailure(key, response.status)
      }
      let payload: unknown
      try { payload = await response.json() } catch { throw new ApiFailure('invalidResponse') }
      return { status: response.status, payload }
    } catch (error) {
      if (error instanceof ApiFailure) throw error
      throw new ApiFailure(controller.signal.aborted ? 'timeout' : 'network')
    } finally { clearTimeout(timer) }
  }

  private async loadSession(role: Role, path: string, body?: unknown): Promise<Session> {
    const generation = ++this.generations[role]
    try {
      const response = await this.request(path, body === undefined ? { role } : { method: 'POST', body, anonymous: true, role })
      const envelope = record(response.payload)
      const data = record(envelope.data)
      if (envelope.status !== 'ok' || data.role !== role || !isUuid(data.actor_id) || typeof data.csrf_token !== 'string' || !data.csrf_token || data.csrf_token.length > 4096) throw new ApiFailure('invalidResponse')
      const session: Session = { role, actor_id: data.actor_id }
      if (generation === this.generations[role]) {
        this.tokens[role] = data.csrf_token
        this.onSession(role, session)
      }
      return session
    } catch (error) {
      if (generation === this.generations[role]) this.clear(role)
      throw error
    }
  }

  async restore(role: Role): Promise<Session | null> {
    try { return await this.loadSession(role, role === 'user' ? '/public/session' : '/staff/session') }
    catch (error) { if (error instanceof ApiFailure && error.status === 401) return null; throw error }
  }
  createUserSession(): Promise<Session> { return this.loadSession('user', '/public/sessions', {}) }
  login(username: string, password: string): Promise<Session> { return this.loadSession('admin', '/staff/login', { username, password }) }
  async logout(role: Role): Promise<void> {
    const response = await this.request(role === 'user' ? '/public/logout' : '/staff/logout', { method: 'POST', role })
    if (record(response.payload).status !== 'ok') throw new ApiFailure('invalidResponse')
    this.clear(role)
  }
  async capabilities(): Promise<Capabilities> {
    const data = record((await this.request('/capabilities')).payload)
    const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(item => typeof item === 'string')
    if (typeof data.service !== 'string' || typeof data.version !== 'string' || !strings(data.implemented) || !strings(data.reserved_hooks) || !strings(data.supported_roles) || !data.supported_roles.includes('user') || !data.supported_roles.includes('admin')) throw new ApiFailure('invalidResponse')
    return { service: data.service, version: data.version, implemented: data.implemented, reserved_hooks: data.reserved_hooks }
  }
  async probe(path: '/healthz' | '/readyz'): Promise<ProbeState> {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), this.timeoutMs)
    try {
      const response = await this.fetcher(path, { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal })
      if (response.status === 503) return 'unavailable'
      if (!response.ok) return 'error'
      return record(await response.json()).status === 'ok' ? 'ok' : 'error'
    } catch { return 'error' } finally { clearTimeout(timer) }
  }
}
