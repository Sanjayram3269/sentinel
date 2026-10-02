from sentinel_ai.data.loader import load_dataset
df = load_dataset("synthetic", 50, 100)
rates = df.groupby("scenario_type")["route_fails"].mean()
print("Positive rate per scenario type:")
print(rates)
