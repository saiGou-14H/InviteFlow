import { reactive, readonly } from 'vue'
import { ApiClient } from '../api'
import type { Role, Session } from '../utils/format'

const sessions = reactive<Record<Role, Session | null>>({ user: null, admin: null })
export const api = new ApiClient(undefined, (role, session) => { sessions[role] = session })
export function useAuth() { return { sessions: readonly(sessions), api } }
