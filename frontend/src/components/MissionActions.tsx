import { useState } from 'react'

function MissionActions() {
  const [decision, setDecision] = useState<string | null>(null)

  return (
    <div className="mission-actions">
      <div className="actions-header">
        <div>
          <span>MISSION DECISION</span>
          <small>OPERATOR AUTHORIZATION REQUIRED</small>
        </div>

        <span className="decision-badge">AWAITING APPROVAL</span>
      </div>

      <div className="action-buttons">
        <button
          className="approve-button"
          onClick={() => setDecision('APPROVED')}
        >
          APPROVE ROUTE
        </button>

        <button
          className="reject-button"
          onClick={() => setDecision('REJECTED')}
        >
          REJECT ROUTE
        </button>
      </div>

      {decision && (
        <div className={`decision-result ${decision.toLowerCase()}`}>
          MISSION {decision}
        </div>
      )}
    </div>
  )
}

export default MissionActions