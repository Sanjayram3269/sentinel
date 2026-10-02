import { useState } from 'react'

interface WhatIfPanelProps {
  onScenarioGenerated: (scenario: string) => void
}

function WhatIfPanel({ onScenarioGenerated }: WhatIfPanelProps) {
  const [selectedScenario, setSelectedScenario] = useState<string | null>(null)
  const [isGenerating, setIsGenerating] = useState(false)
  const [generated, setGenerated] = useState(false)

  const scenarios = [
    'ROAD CLOSURE',
    'TRAFFIC SURGE',
    'HOSPITAL UNAVAILABLE',
  ]

  const generateScene = () => {
    if (!selectedScenario) return

    setIsGenerating(true)
    setGenerated(false)

    setTimeout(() => {
      setIsGenerating(false)
      setGenerated(true)

      onScenarioGenerated(selectedScenario)
    }, 1200)
  }

  return (
    <div className="scenario-generator">

      <div className="scenario-generator-header">
        <div>
          <span>GENERATE SCENE</span>

          <small>
            TEST EMERGENCY CONDITIONS BEFORE DEPLOYMENT
          </small>
        </div>

        <span className="scenario-code">
          SIMULATION
        </span>
      </div>


      <div className="scenario-selector">

        {scenarios.map((scenario) => (
          <button
            key={scenario}
            className={
              selectedScenario === scenario
                ? 'scenario-selected'
                : ''
            }
            onClick={() => {
              setSelectedScenario(scenario)
              setGenerated(false)
            }}
            disabled={isGenerating}
          >
            {scenario}
          </button>
        ))}

      </div>


      <div className="scenario-generate-row">

        <div className="selected-scenario">

          <span>SELECTED SCENARIO</span>

          <strong>
            {selectedScenario ?? 'NONE'}
          </strong>

        </div>

        <button
          className="generate-scene-button"
          onClick={generateScene}
          disabled={!selectedScenario || isGenerating}
        >
          {isGenerating
            ? 'GENERATING...'
            : 'GENERATE SCENE'}
        </button>

      </div>


      {generated && (
        <div className="scenario-results">

          <div className="results-heading">
            <span>SCENE GENERATED</span>

            <small>
              ANALYSIS COMPLETE
            </small>
          </div>


          <div className="scenario-metrics">

            <div>
              <span>ETA</span>
              <strong>11 min</strong>
            </div>

            <div>
              <span>RISK</span>
              <strong className="scenario-risk">
                MEDIUM
              </strong>
            </div>

            <div>
              <span>ROUTES</span>
              <strong>02</strong>
            </div>

            <div>
              <span>CONFIDENCE</span>
              <strong>87%</strong>
            </div>

          </div>


          <div className="scenario-description">

            <span>SCENERY</span>

            <p>
              {selectedScenario === 'ROAD CLOSURE'
                ? 'Primary road blocked. Response vehicle requires alternate corridor.'
                : selectedScenario === 'TRAFFIC SURGE'
                  ? 'Traffic density increased along the primary corridor. Alternate route evaluated.'
                  : 'Destination hospital unavailable. Secondary medical facility required.'}
            </p>

          </div>


          <div className="scenario-solution">

            <div>
              <span>RECOMMENDED SOLUTION</span>

              <strong>
                Alternate Route 02
              </strong>
            </div>

            <div className="solution-status">
              READY
            </div>

          </div>

        </div>
      )}

    </div>
  )
}

export default WhatIfPanel