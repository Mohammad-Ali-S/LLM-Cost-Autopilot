# Benchmark

**10 prompts** (5 easy / 3 medium / 2 hard), each replayed twice: once pinned to `claude-opus-5` (baseline) and once through the autopilot router.

| Metric | Baseline (always claude-opus-5) | Autopilot |
| --- | --- | --- |
| Total cost | $0.4161 | **$0.0014** |
| Savings | - | **$0.4147 (99.7%)** |
| Judge pass rate | 100% (reference) | 80.0% (10 judged) |
| p50 latency | 4023 ms | 285 ms |
| p95 latency | 16729 ms | 6661 ms |
| Errors | 0 | 0 |

Autopilot routing: cheap: 8, premium: 2

Every prompt in the workload is unique, so the savings above come from routing alone - the response cache contributes nothing to these numbers.
