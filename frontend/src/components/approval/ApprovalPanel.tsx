/**
 * Human approval governance.
 *
 * The design constraint is that an operator can never mistake this screen for
 * something the system did on its own. So:
 *
 * - The reviewer must be named before any decision button enables. The backend
 *   requires `reviewer_id` for a reason.
 * - "Evidence reviewed" is an explicit checkbox, not a silent default. When the
 *   plan has no CLEARPATH evidence the checkbox's label says so.
 * - Reject requires a written reason, mirroring the backend's own contract.
 * - Replan opens an explanation first. It never silently changes the mission,
 *   and it never inherits an old approval: a replan starts a new human cycle.
 */

import { useState } from 'react'
import type { ReactNode } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  GitBranch,
  RefreshCw,
  ShieldCheck,
  UserCheck,
  XCircle,
} from 'lucide-react'
import { Button, Panel, StatusBadge, Unavailable } from '../ui/primitives'
import { Modal } from '../ui/Modal'
import { planStatusLabel, planStatusTone } from '../../lib/status'
import type { PlanReviewRead } from '../../types/api'
import type {
  ApprovalAction,
  UseApprovalActionsResult,
} from '../../hooks/useApprovalActions'

interface ApprovalPanelProps {
  review: PlanReviewRead | null
  missionId: string
  actions: UseApprovalActionsResult
  onChanged: () => void
  onOpenPlans: () => void
  demo?: boolean
}

type DialogKind = 'approve' | 'reject' | 'modify' | null

export function ApprovalPanel({
  review,
  missionId,
  actions,
  onChanged,
  onOpenPlans,
  demo = false,
}: ApprovalPanelProps) {
  const [reviewerId, setReviewerId] = useState('')
  const [evidenceReviewed, setEvidenceReviewed] = useState(false)
  const [comment, setComment] = useState('')
  const [replanOpen, setReplanOpen] = useState(false)
  const [dialog, setDialog] = useState<DialogKind>(null)

  const plan = review?.plan ?? null
  const approval = review?.approval ?? null
  const status = approval?.approval_state ?? plan?.status ?? null
  const planId = approval?.plan_id ?? plan?.id ?? ''
  const planVersion = approval?.plan_version ?? plan?.version ?? 0
  const hasEvidence = review?.simulation_evidence?.comparable ?? false
  const offline = demo

  const reviewerReady = reviewerId.trim().length > 0 && !offline
  const canDecide =
    reviewerReady && status !== 'APPROVED' && status !== 'EXECUTION_AUTHORIZED' &&
    status !== 'EXECUTING'

  const target = { missionId, planId, planVersion }

  const submit = async (kind: Exclude<DialogKind, null>) => {
    const common = {
      ...target,
      reviewerId: reviewerId.trim(),
      reviewerRole: 'Duty Operator',
      evidenceReviewed,
    }
    const result =
      kind === 'approve'
        ? await actions.approve({ ...common, comment: comment.trim() || undefined })
        : kind === 'reject'
          ? await actions.reject({ ...common, comment: comment.trim() })
          : await actions.modify({ ...common, field: 'ROUTE', reason: comment.trim() })
    setDialog(null)
    setComment('')
    if (result !== null) onChanged()
  }

  const replan = async () => {
    const result = await actions.requestReplan(target, 'Operator requested replan from the command centre.')
    setReplanOpen(false)
    if (result !== null) onChanged()
  }

  return (
    <Panel
      title="Human Approval"
      ariaLabel="Human approval and plan governance"
      className="border-warn-500/40"
      actions={
        <>
          <StatusBadge
            label={planStatusLabel(status)}
            tone={planStatusTone(status)}
            live={status === 'EXECUTING'}
          />
          <Button
            variant="ghost"
            onClick={() => setReplanOpen(true)}
            title="Replanning starts a new human approval cycle"
            ariaLabel="Replan this mission"
            className="!px-1.5"
          >
            <RefreshCw size={12} />
            Replan
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2.5">
        <div className="flex items-center gap-2 rounded border border-warn-500/50 bg-warn-500/10 px-2.5 py-2">
          <UserCheck size={14} className="shrink-0 text-warn-400" />
          <div className="flex flex-col gap-0.5">
            <span className="text-[11px] font-bold uppercase tracking-[0.1em] text-warn-400">
              Human review required
            </span>
            <span className="text-[10px] leading-relaxed text-ink-300">
              {approval?.authoritative_note ??
                'No plan is executable until a named human approves this exact version.'}
            </span>
          </div>
        </div>

        <dl className="grid grid-cols-2 gap-2 text-[11px]">
          <Field label="Plan version" value={`v${planVersion || '—'}`} />
          <Field
            label="Authorised"
            value={
              approval?.authorized_for_execution === true ? (
                <span className="text-go-400">yes</span>
              ) : (
                <Unavailable>no</Unavailable>
              )
            }
          />
        </dl>

        <label className="flex flex-col gap-1">
          <span className="label-caps">Reviewer ID (required)</span>
          <input
            type="text"
            value={reviewerId}
            onChange={(event) => setReviewerId(event.target.value)}
            placeholder="e.g. operator-01"
            autoComplete="off"
            disabled={offline}
            className="rounded border border-graphite-600 bg-graphite-950 px-2 py-1.5 font-mono text-[11px] text-ink-100 placeholder:text-ink-400 focus:border-intel-500 focus:outline-none disabled:cursor-not-allowed disabled:text-ink-400"
          />
        </label>

        <label className="flex items-start gap-2 rounded border border-graphite-700 bg-graphite-900/50 px-2.5 py-2">
          <input
            type="checkbox"
            checked={evidenceReviewed}
            onChange={(event) => setEvidenceReviewed(event.target.checked)}
            disabled={offline}
            className="mt-0.5 h-3 w-3 accent-[#0891b2]"
          />
          <span className="flex flex-col gap-0.5 text-[10px] leading-relaxed text-ink-300">
            <span className="font-semibold uppercase tracking-[0.08em] text-ink-200">
              I reviewed the available evidence
            </span>
            {hasEvidence ? (
              <span>A measured CLEARPATH comparison is attached to this plan version.</span>
            ) : (
              <span className="text-warn-400">
                No CLEARPATH measurement exists for this plan. Approving means
                approving an unmeasured plan.
              </span>
            )}
          </span>
        </label>

        <div className="grid grid-cols-3 gap-1.5">
          <Button
            variant="approve"
            disabled={!canDecide || dialog !== null}
            onClick={() => setDialog('approve')}
            ariaLabel="Approve this plan version"
          >
            <CheckCircle2 size={12} />
            Approve
          </Button>
          <Button
            variant="secondary"
            disabled={!canDecide || dialog !== null}
            onClick={() => setDialog('modify')}
            ariaLabel="Modify this plan"
          >
            <GitBranch size={12} />
            Modify
          </Button>
          <Button
            variant="danger"
            disabled={!canDecide || dialog !== null}
            onClick={() => setDialog('reject')}
            ariaLabel="Reject this plan"
          >
            <XCircle size={12} />
            Reject
          </Button>
        </div>

        {offline && (
          <p className="rounded border border-warn-500/50 bg-warn-500/10 px-2.5 py-2 text-[10px] leading-relaxed text-warn-400">
            The backend is unreachable, so no decision can be recorded. Buttons are
            disabled rather than pretending to work offline.
          </p>
        )}

        <DecisionStatus
          pending={actions.pending}
          error={actions.error}
          errorCode={actions.errorCode}
          outcome={actions.outcome}
          onClear={actions.clearOutcome}
          onOpenPlans={onOpenPlans}
        />

        {review && review.warnings.length > 0 && (
          <ul className="flex flex-col gap-1 border-t border-graphite-700 pt-2">
            {review.warnings.map((warning) => (
              <li key={warning} className="flex gap-1.5 text-[10px] leading-relaxed text-ink-400">
                <AlertTriangle size={11} className="mt-0.5 shrink-0 text-warn-400" />
                <span>{warning}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <DecisionDialog
        kind={dialog}
        onClose={() => setDialog(null)}
        onConfirm={submit}
        comment={comment}
        onCommentChange={setComment}
        pending={actions.pending}
        planVersion={planVersion}
        reviewerId={reviewerId.trim()}
        hasEvidence={hasEvidence}
      />

      <ReplanModal
        open={replanOpen}
        onClose={() => setReplanOpen(false)}
        onConfirm={replan}
        pending={actions.pending === 'replan'}
        planVersion={planVersion}
      />
    </Panel>
  )
}

function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="label-caps">{label}</dt>
      <dd className="stat-value text-xs text-ink-100">{value}</dd>
    </div>
  )
}

function DecisionDialog({
  kind,
  onClose,
  onConfirm,
  comment,
  onCommentChange,
  pending,
  planVersion,
  reviewerId,
  hasEvidence,
}: {
  kind: DialogKind
  onClose: () => void
  onConfirm: (kind: Exclude<DialogKind, null>) => void | Promise<void>
  comment: string
  onCommentChange: (value: string) => void
  pending: ApprovalAction | null
  planVersion: number
  reviewerId: string
  hasEvidence: boolean
}) {
  const copy = {
    approve: {
      title: 'Approve plan',
      description: `Record your approval of plan version ${planVersion}. This decision applies to that exact version only; any later change invalidates it.`,
      cta: 'Approve plan',
      placeholder: 'Optional note for the record',
      required: false,
      button: 'approve' as const,
    },
    reject: {
      title: 'Reject plan',
      description: `Reject plan version ${planVersion}. A rejection must state why, and it is recorded against your reviewer ID.`,
      cta: 'Reject plan',
      placeholder: 'Reason for rejection (required)',
      required: true,
      button: 'danger' as const,
    },
    modify: {
      title: 'Modify plan',
      description:
        'A modification creates a new plan version and invalidates any existing approval. The new version starts its own human approval cycle.',
      cta: 'Apply modification',
      placeholder: 'Reason for the modification (required)',
      required: true,
      button: 'primary' as const,
    },
  }

  if (kind === null) return null
  const config = copy[kind]
  const commentValid = config.required ? comment.trim().length > 0 : true

  return (
    <Modal
      open
      title={config.title}
      description={config.description}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant={config.button}
            disabled={!commentValid || pending !== null}
            loading={pending !== null}
            onClick={() => void onConfirm(kind)}
          >
            {config.cta}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-2 rounded border border-graphite-700 bg-graphite-900/60 px-2.5 py-2 text-[10px] text-ink-300">
          <ShieldCheck size={12} className="shrink-0 text-intel-400" />
          <span>
            Reviewer <span className="font-mono text-ink-100">{reviewerId}</span> · version{' '}
            <span className="font-mono text-ink-100">v{planVersion}</span>
          </span>
        </div>
        {!hasEvidence && (
          <p className="rounded border border-warn-500/50 bg-warn-500/10 px-2.5 py-2 text-[10px] leading-relaxed text-warn-400">
            No CLEARPATH measurement is attached to this plan version. You are
            deciding without a simulated benefit figure.
          </p>
        )}
        <label className="flex flex-col gap-1">
          <span className="label-caps">
            {kind === 'approve' ? 'Comment' : 'Reason'}
            {config.required && <span className="text-alert-400"> *</span>}
          </span>
          <textarea
            value={comment}
            onChange={(event) => onCommentChange(event.target.value)}
            rows={3}
            placeholder={config.placeholder}
            className="resize-y rounded border border-graphite-600 bg-graphite-950 px-2 py-1.5 text-[11px] text-ink-100 placeholder:text-ink-400 focus:border-intel-500 focus:outline-none"
          />
        </label>
      </div>
    </Modal>
  )
}

function ReplanModal({
  open,
  onClose,
  onConfirm,
  pending,
  planVersion,
}: {
  open: boolean
  onClose: () => void
  onConfirm: () => void | Promise<void>
  pending: boolean
  planVersion: number
}) {
  return (
    <Modal
      open={open}
      width="lg"
      title="Replan this mission"
      description="Replanning is the closing step of SENTINEL's closed loop. It is deliberate, never automatic, and never inherits the approval you already gave."
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={pending} onClick={() => void onConfirm()}>
            <RefreshCw size={12} />
            Request replan
          </Button>
        </>
      }
    >
      <ol className="flex flex-col gap-2 text-[11px] leading-relaxed text-ink-300">
        <Step index={1}>
          SENTINEL re-optimises the plan from current incident, vehicle and
          network state. This replaces the operator's view of plan v{planVersion}.
        </Step>
        <Step index={2}>
          The new version arrives as{' '}
          <span className="font-semibold text-ink-100">DRAFT</span> or{' '}
          <span className="font-semibold text-ink-100">READY FOR REVIEW</span>. The
          previous approval does not carry over.
        </Step>
        <Step index={3}>
          A new simulation pair is required before the new version is meaningful,
          and a new human approval is required before it is executable.
        </Step>
      </ol>
      <p className="flex items-start gap-2 rounded border border-warn-500/50 bg-warn-500/10 px-2.5 py-2 text-[10px] leading-relaxed text-warn-400">
        <AlertTriangle size={12} className="mt-0.5 shrink-0" />
        Nothing changes on the current plan until the new version is reviewed. The
        live route stays in place while the replan is prepared.
      </p>
    </Modal>
  )
}

function Step({ index, children }: { index: number; children: ReactNode }) {
  return (
    <li className="flex gap-2.5">
      <span className="stat-value flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-intel-600 text-[10px] text-intel-300">
        {index}
      </span>
      <span>{children}</span>
    </li>
  )
}

function DecisionStatus({
  pending,
  error,
  errorCode,
  outcome,
  onClear,
  onOpenPlans,
}: {
  pending: ApprovalAction | null
  error: string | null
  errorCode: string | null
  outcome: { message: string; detail: string | null } | null
  onClear: () => void
  onOpenPlans: () => void
}) {
  if (pending !== null) {
    return (
      <p className="flex items-center gap-2 rounded border border-intel-600/50 bg-intel-600/10 px-2.5 py-2 text-[10px] text-intel-300">
        <span
          aria-hidden="true"
          className="h-2.5 w-2.5 animate-spin rounded-full border border-current border-t-transparent"
        />
        Recording decision on the backend…
      </p>
    )
  }
  if (error !== null) {
    return (
      <div className="rounded border border-alert-500/60 bg-alert-500/10 px-2.5 py-2">
        <p className="text-[10px] font-semibold uppercase tracking-[0.08em] text-alert-400">
          Decision refused
        </p>
        <p className="mt-0.5 text-[10px] leading-relaxed text-ink-300">{error}</p>
        {errorCode !== null && (
          <p className="mt-0.5 font-mono text-[10px] text-ink-400">{errorCode}</p>
        )}
      </div>
    )
  }
  if (outcome !== null) {
    return (
      <div className="rounded border border-go-500/50 bg-go-500/10 px-2.5 py-2">
        <p className="flex items-start gap-1.5 text-[10px] font-semibold leading-relaxed text-go-400">
          <CheckCircle2 size={12} className="mt-0.5 shrink-0" />
          {outcome.message}
        </p>
        {outcome.detail !== null && (
          <p className="mt-1 text-[10px] leading-relaxed text-ink-300">{outcome.detail}</p>
        )}
        <div className="mt-1.5 flex gap-1.5">
          <button
            type="button"
            onClick={onOpenPlans}
            className="rounded border border-graphite-600 px-2 py-1 text-[10px] uppercase tracking-[0.07em] text-ink-300 transition-colors hover:border-intel-500 hover:text-intel-300"
          >
            View plans
          </button>
          <button
            type="button"
            onClick={onClear}
            className="rounded border border-transparent px-2 py-1 text-[10px] uppercase tracking-[0.07em] text-ink-400 transition-colors hover:text-ink-200"
          >
            Dismiss
          </button>
        </div>
      </div>
    )
  }
  return null
}
