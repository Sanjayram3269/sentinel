function Header() {
  return (
    <header className="topbar">
      <div className="brand">
        <div className="brand-mark">S</div>

        <div>
          <div className="brand-name">SENTINEL</div>
          <div className="brand-subtitle">
            EMERGENCY RESPONSE COMMAND CENTER
          </div>
        </div>
      </div>

      <div className="header-status">
        <div className="live-indicator">
          <span className="status-dot"></span>
          <span>SYSTEM ONLINE</span>
        </div>

        <div className="header-divider"></div>

        <div className="live-badge">
          LIVE
        </div>
      </div>
    </header>
  )
}

export default Header