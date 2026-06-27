.PHONY: test eval quality gate bench

# 跑完整的单元测试套件。
test:
	PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py'

# 离线 golden hit@k 评测。
eval:
	PYTHONPATH=src python3 -m codekb.cli eval --skip-missing-expected

# 回答质量报告。
quality:
	PYTHONPATH=src python3 -m codekb.cli quality-check --skip-missing-expected

# 质量门禁,硬卡口(FAIL 时非零退出)—— 供 CI / pre-commit 使用。
gate:
	PYTHONPATH=src python3 -m codekb.cli quality-check --skip-missing-expected

# 单次 ask() 调用的延迟基准测试(p50/p95/p99/max)。
bench:
	PYTHONPATH=src python3 -m codekb.cli bench-latency --warmup 1 --repeats 3
