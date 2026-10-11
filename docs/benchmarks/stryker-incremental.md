# Stryker incremental benchmark

Measured on 2026-10-11 on macOS arm64, Node 26.8.2, Bun 1.3.13, Python 3.13.12, Stryker/core and Vitest runner 10.0.0, Vitest 4.1.11.

Three trials per module, with incremental/full execution order alternating. Each trial seeds the cache with the earlier tests, edits the test file, then runs the exact same final tests with and without incremental mode. Timings are the median wall time for the mutation step, including process startup, sandboxing and the dry run. Cache seeding, model calls, test validation, and hidden-bug evaluation are excluded. Every mutant status and score matched in all 24 comparisons.

| Project / source | Full (s) | Incremental (s) | Reused / total | Time saved |
| --- | ---: | ---: | ---: | ---: |
| examples / src/backoff.ts | 7.404 | 7.628 | 0 / 45 | -3.0% |
| examples / src/cart.ts | 3.468 | 2.717 | 30 / 88 | 21.7% |
| examples / src/intervals.ts | 2.633 | 2.367 | 19 / 61 | 10.1% |
| examples / src/pagination.ts | 2.566 | 1.345 | 55 / 56 | 47.6% |
| examples / src/password.ts | 3.123 | 2.959 | 16 / 89 | 5.3% |
| examples / src/rate-limiter.ts | 2.638 | 2.558 | 6 / 60 | 3.0% |
| examples / src/slugify.ts | 2.502 | 2.546 | 0 / 50 | -1.8% |
| defu / src/defu.ts | 2.758 | 1.983 | 53 / 67 | 28.1% |

The sum of the seven example medians decreased from **24.334s to 22.120s (9.1%)**. The six existing-test-to-oracle comparisons alone improved by **4.6%**. The original upstream defu suite improved by **28.1%** after adding one merge assertion. These numbers describe mutation rounds, not total evaluation time.

For the six modules with existing tests, the edit replaces the weak initial suite with its bundled oracle. Pagination has no initial test file: its cache is seeded with the oracle and its edit adds an assertion that does not cover the source. This represents a later round with little new coverage, and is deliberately reported separately. Backoff and slugify reused no mutants; startup and incremental bookkeeping can erase the benefit when all old tests are invalidated.

The defu source and original test suite are from [unjs/defu at 82632b6](https://github.com/unjs/defu/tree/82632b66f5914e9946edce300e10633a3d5c0cb7). The measured edit appends a passing test for merging `{ a: 1 }` and `{ b: 2 }`. Earlier local test edits were excluded from this comparison.

## Reproduce

Install the locked backend and example dependencies first. Run the benchmark with no other mutation jobs in progress:

```bash
git clone https://github.com/unjs/defu.git /tmp/regress-defu-benchmark
git -C /tmp/regress-defu-benchmark checkout 82632b66f5914e9946edce300e10633a3d5c0cb7
uv run python scripts/benchmark_incremental.py --trials 3 \
  --larger /tmp/regress-defu-benchmark --output /tmp/incremental-benchmark.json
```

All edits happen in temporary project copies. The benchmark asserts identical scores and mutant statuses before reporting timings. [Raw measurements](stryker-incremental.json) retain all trials.
