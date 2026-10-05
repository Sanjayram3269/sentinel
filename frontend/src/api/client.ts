/**
 * Typed fetch wrapper.
 *
 * Two things matter here beyond convenience:
 *
 * 1. Errors are *readable*. The backend returns structured problems like
 *    `{"detail": {"code": "PLAN_VERSION_MISMATCH", ...}}`, and an operator
 *    debugging an approval needs to see that code, not "[object Object]".
 * 2. Unavailability is *distinguishable*. A timeout, an HTTP error, and a
 *    network failure are different states with different meanings, so they are
 *    separate error kinds rather than one generic `Error`.
 */

const DEFAULT_TIMEOUT_MS = 10_000

export const API_BASE_URL: string =
  (import.meta.env['VITE_API_BASE_URL'] as string | undefined) ?? '/api/v1'

export type ApiErrorKind =
  | 'network'
  | 'timeout'
  | 'not_found'
  | 'client'
  | 'server'
  | 'parse'

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status: number | null
  /** Backend machine-readable code, e.g. "PLAN_VERSION_MISMATCH". */
  readonly code: string | null
  readonly detail: string

  constructor(
    message: string,
    options: {
      kind: ApiErrorKind
      status?: number | null
      code?: string | null
      detail?: string
    },
  ) {
    super(message)
    this.name = 'ApiError'
    this.kind = options.kind
    this.status = options.status ?? null
    this.code = options.code ?? null
    this.detail = options.detail ?? message
  }

  /** True when the backend could not be reached at all. */
  get isOffline(): boolean {
    return this.kind === 'network' || this.kind === 'timeout'
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST'
  body?: unknown
  timeoutMs?: number
  signal?: AbortSignal
}

function classify(status: number): ApiErrorKind {
  if (status === 404) return 'not_found'
  if (status >= 500) return 'server'
  return 'client'
}

/**
 * Pulls a human-readable message and code out of the backend's several error
 * shapes: FastAPI's `detail` string, its `detail` object, and a validation
 * array of `{loc, msg}` entries.
 */
function parseProblem(body: unknown): { code: string | null; detail: string } {
  if (typeof body === 'string' && body.trim() !== '') {
    return { code: null, detail: body.slice(0, 300) }
  }
  if (body && typeof body === 'object') {
    const detail = (body as Record<string, unknown>)['detail']
    if (typeof detail === 'string') {
      return { code: null, detail: detail.slice(0, 300) }
    }
    if (detail && typeof detail === 'object') {
      const record = detail as Record<string, unknown>
      const code = typeof record['code'] === 'string' ? record['code'] : null
      const text =
        typeof record['detail'] === 'string'
          ? record['detail']
          : typeof record['message'] === 'string'
            ? record['message']
            : JSON.stringify(record)
      return { code, detail: text.slice(0, 300) }
    }
    if (Array.isArray(detail)) {
      const first = detail[0]
      if (first && typeof first === 'object') {
        const msg = (first as Record<string, unknown>)['msg']
        return {
          code: null,
          detail: typeof msg === 'string' ? msg : JSON.stringify(first),
        }
      }
    }
    return { code: null, detail: JSON.stringify(body).slice(0, 300) }
  }
  return { code: null, detail: 'The backend returned an unreadable error body.' }
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, timeoutMs = DEFAULT_TIMEOUT_MS } = options

  // Compose the caller's signal with our timeout so either can abort the
  // request, instead of leaking a timer on every call.
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  const onExternalAbort = () => controller.abort()
  options.signal?.addEventListener('abort', onExternalAbort)

  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers: {
        Accept: 'application/json',
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      signal: controller.signal,
    })

    if (!response.ok) {
      let parsed: unknown = null
      try {
        parsed = await response.json()
      } catch {
        parsed = await response.text().catch(() => null)
      }
      const { code, detail } = parseProblem(parsed)
      throw new ApiError(`Request failed (${response.status})`, {
        kind: classify(response.status),
        status: response.status,
        code,
        detail,
      })
    }

    if (response.status === 204) return undefined as T
    try {
      return (await response.json()) as T
    } catch {
      throw new ApiError('The backend returned a malformed response.', {
        kind: 'parse',
        status: response.status,
      })
    }
  } catch (error) {
    if (error instanceof ApiError) throw error
    // AbortError surfaces for both our timeout and a caller-initiated cancel;
    // only the former is a timeout worth reporting.
    if (error instanceof DOMException && error.name === 'AbortError') {
      if (options.signal?.aborted) throw error
      throw new ApiError(`The backend did not respond within ${timeoutMs}ms.`, {
        kind: 'timeout',
      })
    }
    throw new ApiError(
      'Cannot reach the SENTINEL backend. It may be offline.',
      { kind: 'network' },
    )
  } finally {
    clearTimeout(timer)
    options.signal?.removeEventListener('abort', onExternalAbort)
  }
}

export const api = {
  get: <T>(path: string, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'GET' }),
  post: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'POST', body }),
}

/** Probes the backend for a simple health signal. */
export async function probeHealth(signal?: AbortSignal): Promise<boolean> {
  try {
    const response = await fetch('/health', { signal, method: 'GET' })
    return response.ok
  } catch {
    return false
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError
}
