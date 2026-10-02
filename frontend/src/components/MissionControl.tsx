import { currentMission } from '../data/mission'

function MissionControl() {
  return (
    <div className="panel">
      <div className="control-header">
        <h2>LIVE MISSION CONTROL</h2>
        <span className="control-live">LIVE</span>
      </div>

      <div className="mission-status">
        <span>STATUS</span>
        <strong>{currentMission.status}</strong>
      </div>

      <div className="route-info">
        <div>
          <span>ETA</span>
          <strong>
            {currentMission.eta !== null
              ? `${currentMission.eta} min`
              : '--'}
          </strong>
        </div>

        <div>
          <span>RISK</span>
          <strong className={
            currentMission.risk
              ? `risk-${currentMission.risk.toLowerCase()}`
              : ''
          }>
            {currentMission.risk ?? '--'}
          </strong>
        </div>

        <div>
          <span>CONFIDENCE</span>
          <strong>
            {currentMission.confidence !== null
              ? `${currentMission.confidence}%`
              : '--'}
          </strong>
        </div>
      </div>
    </div>
  )
}

export default MissionControl