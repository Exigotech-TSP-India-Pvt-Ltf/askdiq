from app.core.stats import percentile


def test_percentile_empty_returns_zero():
    assert percentile([], 0.5) == 0.0


def test_percentile_single_value():
    assert percentile([42.0], 0.5) == 42.0
    assert percentile([42.0], 1.0) == 42.0


def test_percentile_p50_p100():
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert percentile(values, 1.0) == 50.0
    assert percentile(values, 0.0) == 10.0
    # nearest-rank p50 of 5 sorted values lands on the 3rd (index 2).
    assert percentile(values, 0.5) == 30.0


def test_percentile_unsorted_input():
    values = [30.0, 10.0, 50.0, 20.0, 40.0]
    assert percentile(values, 1.0) == 50.0
