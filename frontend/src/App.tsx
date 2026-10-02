import { useState } from 'react'
import './App.css'

import Header from './components/Header'
import VoiceChat from './components/VoiceChat'
import MapView from './components/MapView'
import MissionControl from './components/MissionControl'
import MissionOverview from './components/MissionOverview'
import RouteComparison from './components/RouteComparison'
import WhatIfPanel from './components/WhatIfPanel'
import MissionTimeline from './components/MissionTimeline'
import MissionActions from './components/MissionActions'

import { mockRoutes } from './data/routes'

function App() {
  const [activePage, setActivePage] = useState('Dashboard')
  const [generatedScenario, setGeneratedScenario] = useState<string | null>(null)

  const pages = [
    'Dashboard',
    'Scenarios',
    'Simulation',
    'Solutions',
    'Analytics',
  ]

  return (
    <div className="app">

      {/* LEFT NAVIGATION */}
      <aside className="sidebar">

        <div className="sidebar-brand">
          <div className="brand-mark">S</div>

          <div>
            <div className="brand-name">SENTINEL</div>

            <div className="brand-subtitle">
              EMERGENCY RESPONSE
            </div>
          </div>
        </div>

        <div className="sidebar-section">

          <span className="sidebar-label">
            COMMAND
          </span>

          {pages.map((page, index) => (
            <button
              key={page}
              className={
                activePage === page
                  ? 'nav-item active'
                  : 'nav-item'
              }
              onClick={() => setActivePage(page)}
            >
              <span>
                {String(index + 1).padStart(2, '0')}
              </span>

              {page}
            </button>
          ))}

        </div>

        <div className="sidebar-footer">
          <span className="status-dot"></span>
          SYSTEM ONLINE
        </div>

      </aside>


      {/* MAIN WORKSPACE */}
      <main className="workspace">

        <Header />


        {/* CURRENT PAGE */}
        <div className="page-indicator">
          <span>COMMAND /</span>
          <strong>{activePage.toUpperCase()}</strong>
        </div>


        {/* MAIN COMMAND AREA */}
        <section className="command-layout">

          <div className="map-area">
            <MapView />
          </div>


          {/* RIGHT METRICS */}
          <aside className="metrics-area">

            <div className="metrics-heading">
              <span>LIVE METRICS</span>
              <small>REAL-TIME</small>
            </div>

            <div className="metric">
              <span>MSE</span>
              <strong>0.82</strong>
            </div>

            <div className="metric">
              <span>MSMR</span>
              <strong>0.91</strong>
            </div>

            <div className="metric">
              <span>ETA</span>
              <strong>08:42</strong>
            </div>

            <div className="metric">
              <span>RISK</span>
              <strong className="metric-safe">
                LOW
              </strong>
            </div>

            <div className="metric">
              <span>CONFIDENCE</span>
              <strong>94%</strong>
            </div>

          </aside>

        </section>


        {/* MISSION INFORMATION */}
        <section className="mission-layer">

          <MissionOverview />

          <MissionControl />

          <RouteComparison
            routes={mockRoutes}
          />

        </section>


        {/* SCENARIO GENERATION */}
        <section className="scenario-layer">

          <div className="section-heading">

            <div>
              <span>
                SCENARIO GENERATOR
              </span>

              <small>
                GENERATE • ANALYZE • SOLVE
              </small>
            </div>

          </div>


          <div className="scenario-workspace">

  <div className="scenario-main">
    <WhatIfPanel
  onScenarioGenerated={setGeneratedScenario}
/>

    <MissionTimeline />

    <MissionActions />
  </div>

    <VoiceChat scenario={generatedScenario} />
</div>

        </section>

      </main>

    </div>
  )
}

export default App