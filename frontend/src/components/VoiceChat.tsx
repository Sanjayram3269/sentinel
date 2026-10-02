import { useState } from 'react'

interface VoiceChatProps {
  scenario: string | null
}

function VoiceChat({ scenario }: VoiceChatProps) {
  const [isSpeaking, setIsSpeaking] = useState(false)

  const getExplanation = () => {
    if (scenario === 'ROAD CLOSURE') {
      return 'A road closure has been detected. SENTINEL recommends an alternate corridor to maintain emergency response time.'
    }

    if (scenario === 'TRAFFIC SURGE') {
      return 'A traffic surge is affecting the primary corridor. An alternate route has been evaluated to reduce response delay.'
    }

    if (scenario === 'HOSPITAL UNAVAILABLE') {
      return 'The selected hospital is unavailable. SENTINEL recommends redirecting the response to an alternate medical facility.'
    }

    return 'Generate a scenario and I will explain the situation, risk, and recommended response.'
  }

  const speakScenario = () => {
    const text = getExplanation()

    if (!('speechSynthesis' in window)) {
      return
    }

    window.speechSynthesis.cancel()

    const speech = new SpeechSynthesisUtterance(text)

    speech.rate = 0.95
    speech.pitch = 1
    speech.volume = 1

    speech.onstart = () => {
      setIsSpeaking(true)
    }

    speech.onend = () => {
      setIsSpeaking(false)
    }

    speech.onerror = () => {
      setIsSpeaking(false)
    }

    window.speechSynthesis.speak(speech)
  }

  return (
    <aside className="voice-chat">

      <div className="voice-header">

        <div>
          <span>AI RESPONSE ASSISTANT</span>

          <small>
            SCENARIO EXPLANATION
          </small>
        </div>

        <span className="voice-status">
          {scenario ? 'ACTIVE' : 'READY'}
        </span>

      </div>


      <div className="voice-message">

        <div className="ai-mark">
          AI
        </div>

        <div className="message-content">

          <span className="message-label">
            SENTINEL AI
          </span>

          <p>
            {getExplanation()}
          </p>

        </div>

      </div>


      <div className="voice-summary">

        <span>CURRENT SCENARIO</span>

        <strong>
          {scenario ?? 'Awaiting generated scene'}
        </strong>

      </div>


      <div className="voice-control">

        <button
          className={
            isSpeaking
              ? 'voice-button listening'
              : 'voice-button'
          }
          onClick={speakScenario}
          disabled={!scenario}
        >

          <span className="voice-icon">
            {isSpeaking ? '■' : '●'}
          </span>

          {isSpeaking
            ? 'SPEAKING...'
            : 'EXPLAIN SCENARIO'}

        </button>

        <span className="voice-hint">
          {isSpeaking
            ? 'SENTINEL is explaining the scenario...'
            : scenario
              ? 'Voice explanation available'
              : 'Generate a scenario first'}
        </span>

      </div>

    </aside>
  )
}

export default VoiceChat