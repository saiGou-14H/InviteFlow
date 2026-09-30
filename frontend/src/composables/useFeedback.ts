import { ref } from 'vue'
import { ApiFailure, type ApiResult } from '../api'
import type { TextKey } from '../i18n'
import { safeOperationId } from '../utils/format'

export type Notice = { key: TextKey; kind: 'info' | 'error'; operationId?: string }
export function useFeedback() {
  const busy = ref(false)
  const notice = ref<Notice | null>(null)
  function error(key: TextKey) { notice.value = { key, kind: 'error' } }
  function info(key: TextKey) { notice.value = { key, kind: 'info' } }
  function accepted(result: ApiResult) {
    if (result.status !== 202) throw new ApiFailure('invalidResponse')
    notice.value = { key: 'accepted', kind: 'info', operationId: safeOperationId(result.payload) }
  }
  async function run(action: () => Promise<void>) {
    if (busy.value) return
    busy.value = true
    notice.value = null
    try { await action() }
    catch (cause) { error(cause instanceof ApiFailure ? cause.key : 'unexpected') }
    finally { busy.value = false }
  }
  return { busy, notice, run, error, info, accepted }
}
