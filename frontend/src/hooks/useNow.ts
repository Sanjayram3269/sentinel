/**
 * Ticking wall clock.
 *
 * The header clock is the operator's only independent check that the console is
 * actually live, so it updates on a real interval rather than being frozen at
 * mount. Pausing on hidden tabs keeps it from doing pointless work in the
 * background.
 */

import { useEffect, useState } from 'react'

export function useNow(intervalMs = 1000): Date {
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined

    const start = () => {
      stop()
      timer = setInterval(() => setNow(new Date()), intervalMs)
    }
    const stop = () => {
      if (timer !== undefined) clearInterval(timer)
      timer = undefined
    }
    const onVisibility = () => {
      if (document.hidden) {
        stop()
      } else {
        setNow(new Date())
        start()
      }
    }

    if (!document.hidden) start()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      stop()
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [intervalMs])

  return now
}