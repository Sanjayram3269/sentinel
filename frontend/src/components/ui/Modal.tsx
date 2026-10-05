/**
 * Modal dialog.
 *
 * Hand-rolled rather than pulled from a component library: SENTINEL needs one
 * dialog shape, and a library would be an order of magnitude more code than the
 * behaviour required. What it does implement is the behaviour people actually
 * depend on: Escape closes it, focus moves into it and is restored on close,
 * Tab is trapped, and the backdrop is inert to assistive tech.
 */

import { useCallback, useEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { X } from 'lucide-react'

interface ModalProps {
  open: boolean
  title: string
  description?: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  /** Widens the panel for content-heavy dialogs such as the replan explanation. */
  width?: 'md' | 'lg'
}

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

const WIDTH = { md: 'max-w-md', lg: 'max-w-2xl' } as const

export function Modal({
  open,
  title,
  description,
  onClose,
  children,
  footer,
  width = 'md',
}: ModalProps) {
  const panelRef = useRef<HTMLDivElement | null>(null)
  const restoreRef = useRef<HTMLElement | null>(null)

  const focusables = useCallback(
    () =>
      Array.from(
        panelRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [],
      ).filter((node) => node.offsetParent !== null || node === document.activeElement),
    [],
  )

  useEffect(() => {
    if (!open) return
    restoreRef.current = document.activeElement as HTMLElement | null

    // Move focus into the dialog on the next frame, once it is mounted.
    const raf = requestAnimationFrame(() => {
      const nodes = focusables()
      if (nodes.length > 0) nodes[0].focus()
      else panelRef.current?.focus()
    })

    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onClose()
        return
      }
      if (event.key !== 'Tab') return
      const nodes = focusables()
      if (nodes.length === 0) {
        event.preventDefault()
        panelRef.current?.focus()
        return
      }
      const first = nodes[0]
      const last = nodes[nodes.length - 1]
      const active = document.activeElement
      if (event.shiftKey && (active === first || active === panelRef.current)) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && active === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown, true)
    return () => {
      cancelAnimationFrame(raf)
      document.removeEventListener('keydown', onKeyDown, true)
      document.body.style.overflow = previousOverflow
      restoreRef.current?.focus?.()
    }
  }, [open, onClose, focusables])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        aria-label="Close dialog"
        tabIndex={-1}
        onClick={onClose}
        className="absolute inset-0 cursor-default bg-void/80 backdrop-blur-sm"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={`panel relative z-10 w-full ${WIDTH[width]} shadow-[0_24px_60px_-20px_rgba(0,0,0,0.9)]`}
      >
        <header className="panel-header">
          <h2 className="panel-title">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="rounded p-1 text-ink-400 transition-colors hover:bg-graphite-800 hover:text-ink-100"
          >
            <X size={14} />
          </button>
        </header>
        <div className="flex flex-col gap-3 p-4">
          {description !== undefined && (
            <p className="text-[11px] leading-relaxed text-ink-300">{description}</p>
          )}
          {children}
        </div>
        {footer !== undefined && (
          <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-graphite-700 px-4 py-3">
            {footer}
          </footer>
        )}
      </div>
    </div>
  )
}
