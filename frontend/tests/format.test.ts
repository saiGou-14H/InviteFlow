import { describe, expect, it } from 'vitest'
import { expiryToIso, isUuid, parseCodes, record, safeOperationId, safeSnapshot, validQuantity } from '../src/utils/format'

const id = '8d1d6e36-12e7-4d19-aee0-2b4d5294d258'
describe('code parsing', () => {
  it('trims blanks, deduplicates in order, and preserves case', () => {
    expect(parseCodes('  AbC  \r\n\nxyz\nAbC\nabc \rTail\r\nxyz')).toEqual(['AbC', 'xyz', 'abc', 'Tail'])
  })
  it('does not split codes on internal spaces or commas', () => { expect(parseCodes('A B\nA,B')).toEqual(['A B', 'A,B']) })
  it('returns no codes for whitespace', () => { expect(parseCodes(' \n\t\r')).toEqual([]) })
})
describe('input validation', () => {
  it('accepts complete UUIDs including newer UUID versions', () => {
    expect(isUuid(id)).toBe(true)
    expect(isUuid('018f58e3-527d-7a95-b579-f5b1878296c1')).toBe(true)
    expect(isUuid(id.toUpperCase())).toBe(true)
  })
  it.each(['../secret', '', 'not-a-uuid', `${id}/followup`, ` ${id}`, null, 123])('rejects invalid UUID %s', value => { expect(isUuid(value)).toBe(false) })
  it.each([1, 1000, 99])('accepts valid quantity %s', value => { expect(validQuantity(value)).toBe(true) })
  it.each([0, -1, 1001, 2.5, NaN, Infinity])('rejects invalid quantity %s', value => { expect(validQuantity(value)).toBe(false) })
  it('converts expiry to UTC, leaves blank null, rejects past and invalid dates', () => {
    const now = Date.parse('2030-01-01T00:00:00Z')
    expect(expiryToIso('')).toBeNull()
    expect(expiryToIso('2030-01-02T08:00:00+08:00', now)).toBe('2030-01-02T00:00:00.000Z')
    expect(() => expiryToIso('2020-01-01', now)).toThrow('invalidExpiry')
    expect(() => expiryToIso('not-a-date', now)).toThrow('invalidExpiry')
  })
})
describe('safe response projection', () => {
  it('only exposes a whitelisted claim status', () => {
    expect(safeSnapshot({ data: { status: 'processing', csrf_token: 'secret', code: 'PRIVATE', email: 'private@example.test' } })).toEqual({ status: 'processing' })
    expect(safeSnapshot({ data: { status: 'secret internal server detail' } })).toEqual({ status: 'unknown' })
    expect(safeSnapshot(null)).toEqual({ status: 'unknown' })
  })
  it('only exposes UUID operation ids', () => {
    expect(safeOperationId({ data: { operation_id: id, password: 'secret' } })).toBe(id)
    expect(safeOperationId({ data: { operation_id: '<script>secret</script>' } })).toBeUndefined()
    expect(safeOperationId({ operation_id: id })).toBeUndefined()
  })
  it('rejects arrays and null as records', () => { expect(record([])).toEqual({}); expect(record(null)).toEqual({}) })
})
