def test_demo_no_literals():
    with open("sentinel_ai/demo/demo_flagship.py", "r") as f:
        content = f.read()
    
    literals = [
        "14.6", "46.6", "4.6", "95.5", "99.9", "0%"
    ]
    for lit in literals:
        assert lit not in content, f"Found literal {lit} in demo source"
