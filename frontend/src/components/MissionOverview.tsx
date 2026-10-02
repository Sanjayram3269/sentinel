import { currentMission } from '../data/mission'

function MissionOverview() {
  return (
    <div className="overview-card">
      <div className="overview-header">
        <span>ACTIVE MISSION</span>

        <div className="mission-meta">
          <span className="mission-id">{currentMission.id}</span>
          <span className={`mission-badge ${currentMission.status.toLowerCase()}`}>
            {currentMission.status}
          </span>
        </div>
      </div>

      <div className="overview-content">
        <div>
          <span>ORIGIN</span>
          <strong>{currentMission.origin}</strong>
        </div>

        <div>
          <span>DESTINATION</span>
          <strong>{currentMission.destination}</strong>
        </div>

        <div>
          <span>VEHICLE</span>
          <strong>{currentMission.vehicle}</strong>
        </div>
      </div>
    </div>
  )
}

export default MissionOverview