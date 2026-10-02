const events = [
  {
    time: '19:28:14',
    title: 'Mission created',
    detail: 'Emergency response request received',
    status: 'complete',
  },
  {
    time: '19:28:22',
    title: 'Route analysis complete',
    detail: '2 viable routes evaluated',
    status: 'complete',
  },
  {
    time: '19:28:26',
    title: 'Route selected',
    detail: 'AI recommended Route 001',
    status: 'active',
  },
  {
    time: '--:--:--',
    title: 'Vehicle deployment',
    detail: 'Awaiting mission approval',
    status: 'pending',
  },
]

function MissionTimeline() {
  return (
    <div className="timeline-panel">
      <div className="timeline-header">
        <div>
          <span>MISSION TIMELINE</span>
          <small>EVENT STREAM</small>
        </div>

        <span className="timeline-live">LIVE</span>
      </div>

      <div className="timeline">
        {events.map((event, index) => (
          <div className="timeline-item" key={`${event.title}-${index}`}>
            <div className={`timeline-dot ${event.status}`}></div>

            <div className="timeline-content">
              <div className="timeline-top">
                <strong>{event.title}</strong>
                <span>{event.time}</span>
              </div>

              <p>{event.detail}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default MissionTimeline