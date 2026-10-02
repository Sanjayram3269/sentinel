function MapView() {
  return (
    <div className="map-panel">
      <div className="map-placeholder">

        <div className="map-content">

          {/* MAP HEADER */}
          <div className="map-label">
            <span className="status-dot"></span>
            LIVE SCENARIO
          </div>

          <div className="map-location">
            BENGALURU · URBAN RESPONSE ZONE
          </div>


          {/* MAP ROADS */}

          <div className="road road-main"></div>
          <div className="road road-secondary"></div>
          <div className="road road-cross"></div>
          <div className="road road-diagonal"></div>


          {/* ROUTES */}

          <div className="map-route route-one"></div>
          <div className="map-route route-two"></div>


          {/* VEHICLE */}

          <div className="map-center vehicle-marker">
            <span className="pulse"></span>

            <span className="vehicle-label">
              UNIT-07
            </span>
          </div>


          {/* DESTINATION */}

          <div className="destination-marker">
            <span></span>

            <div>
              <strong>HOSPITAL</strong>
              <small>DESTINATION</small>
            </div>
          </div>


          {/* INCIDENT */}

          <div className="incident-marker">
            <span></span>

            <div>
              <strong>INCIDENT</strong>
              <small>TRAFFIC SURGE</small>
            </div>
          </div>


          {/* MAP LEGEND */}

          <div className="map-legend">

            <div>
              <span className="legend-route"></span>
              ACTIVE ROUTE
            </div>

            <div>
              <span className="legend-vehicle"></span>
              RESPONSE UNIT
            </div>

            <div>
              <span className="legend-incident"></span>
              INCIDENT
            </div>

          </div>


          {/* TELEMETRY */}

          <div className="map-telemetry">

            <div>
              <span>ACTIVE UNITS</span>
              <strong>01</strong>
            </div>

            <div>
              <span>NETWORK</span>
              <strong>98%</strong>
            </div>

            <div>
              <span>SCENARIO</span>
              <strong>01</strong>
            </div>

          </div>

        </div>

      </div>
    </div>
  )
}

export default MapView