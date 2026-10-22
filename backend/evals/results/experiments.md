# Fact-checker experiments

The fact_checker suite's 30 claims (16 planted errors, 14 true) through three checker variants (evals/experiments.py). The per-analysis estimates scale production's measured stage-B costs (127 analyses: check $0.0815, writer $0.2087) by each variant's measured check cost against variant a.

| Variant | Unsupported claims removed | Supported claims removed | Check cost, 5 groups | Est. check cost per analysis | Est. stage-B cost per analysis |
|---|---|---|---|---|---|
| a: verdict first, prompt v3, fast model | 50.0% (8/16) | 0.0% (0/14) | $0.0009 | $0.00064 | $0.0023 |
| b: reason first, prompt v4, fast model | 75.0% (12/16) | 0.0% (0/14) | $0.0011 | $0.00075 | $0.0024 |
| c: reason first, prompt v4, smart model | 100.0% (16/16) | 0.0% (0/14) | $0.0016 | $0.00113 | $0.0028 |
