import sys
import os

def test_smoke_no_backend():
    # Remove any existing loaded modules from sentinel_ai to ensure a fresh import
    for key in list(sys.modules.keys()):
        if key.startswith("sentinel_ai"):
            del sys.modules[key]
            
    # Mock missing backend packages
    sys.modules['xgboost'] = None
    sys.modules['ortools'] = None
    
    try:
        import sentinel_ai.api
        import sentinel_ai.contracts
        success = True
    except ImportError as e:
        success = False
        assert False, f"Smoke test failed, backend dependency leaked: {e}"
        
    assert success
