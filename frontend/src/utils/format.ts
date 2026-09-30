export type Role = 'user' | 'admin'
export type Session = { role: Role; actor_id: string }

export function parseCodes(input: string): string[] {
  return [...new Set(input.split(/\r\n?|\n/).map(line => line.trim()).filter(Boolean))]
}

export function isUuid(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
}

export function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : {}
}

export function validQuantity(value: number): boolean {
  return Number.isInteger(value) && value >= 1 && value <= 1000
}

export function expiryToIso(value: string, now = Date.now()): string | null {
  if (!value) return null
  const date = new Date(value)
  if (!Number.isFinite(date.getTime()) || date.getTime() <= now) throw new Error('invalidExpiry')
  return date.toISOString()
}

export const claimStatuses = ['pending', 'queued', 'processing', 'awaiting_confirmation', 'confirmed', 'completed', 'failed', 'cancelled', 'expired'] as const
export type ClaimStatus = typeof claimStatuses[number]
export function safeSnapshot(value: unknown): { status: ClaimStatus | 'unknown' } {
  const data = record(record(value).data)
  return { status: claimStatuses.includes(data.status as ClaimStatus) ? data.status as ClaimStatus : 'unknown' }
}

export function safeOperationId(value: unknown): string | undefined {
  const id = record(record(value).data).operation_id
  return isUuid(id) ? id : undefined
}
