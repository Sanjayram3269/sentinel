.PHONY: test train demo experiments

test:
	PYTHONPATH=. pytest tests/

train:
	PYTHONPATH=. python -m sentinel_ai.prediction.train

demo:
	PYTHONPATH=. python -m sentinel_ai.demo.demo_flagship

experiments:
	PYTHONPATH=. python -m sentinel_ai.experiments.run_experiments
