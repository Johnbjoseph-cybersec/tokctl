import json
from tokctl import filter as flt


def test_small_untouched():
    r = flt.govern("ls", "a\nb\n")
    assert r.text == "a\nb\n" and not r.transforms


def test_read_class_is_byte_exact_under_cap():
    s = ("x  \r\n\n\n\n" * 500)
    r = flt.govern("sed -n '1,50p' f.py", s)
    assert r.klass == "read" and r.text == s


def test_read_class_capped_with_marker_above_cap():
    s = "\n".join(f"line {i}" for i in range(20000))
    r = flt.govern("cat big.txt", s)
    assert r.truncated and "[tokctl:" in r.text and r.out_chars <= flt.CAPS["read"] + 200
    assert r.text.startswith("line 0\n") and r.text.endswith("line 19999")


def test_pytest_noise_removed_failures_kept():
    lines = [f"t/test_{i}.py::test_x PASSED [ {i}%]" for i in range(100)] + ["t/test_a.py::test_y FAILED", "E   AssertionError: boom", "=== 1 failed, 100 passed ==="]
    r = flt.govern("pytest -q", "\n".join(lines * 2))
    assert r.klass == "tests" and "PASSED" not in r.text and "AssertionError: boom" in r.text and "1 failed" in r.text


def test_dedupe_folds_consecutive_duplicates():
    r = flt.govern("python3 run.py", ("same line here\n" * 300) + "end\n")
    assert "repeated 299 more times" in r.text and r.text.count("same line here") == 1


def test_listing_compacts_ls_long():
    row = "-rw-r--r--@  1 user  staff  12345 Nov  3 10:22 name_{}.py"
    r = flt.govern("ls -la", "total 8\n" + "\n".join(row.format(i) for i in range(200)))
    assert r.klass == "listing" and "-     12345 Nov  3 10:22 name_7.py" in r.text and "total 8" not in r.text and "user  staff" not in r.text


def test_json_structure_when_big():
    data = {"items": [{"id": i, "name": "n" * 20} for i in range(800)], "total": 800}
    r = flt.govern("curl -s http://x/api", json.dumps(data))
    assert "JSON structure" in r.text and "[800 ×" in r.text and r.out_chars < 2000


def test_logs_keep_errors_and_tail():
    lines = [f"2026-11-11T10:00:{i % 60:02d}Z INFO heartbeat" for i in range(1000)]
    lines[500] = "2026-11-11T10:05:00Z ERROR disk full"
    r = flt.govern("docker logs api", "\n".join(lines))
    assert r.klass == "logs" and "ERROR disk full" in r.text and r.out_chars < 8200


def test_git_log_one_line_per_commit():
    s = "".join(f"commit {i:040x}\nAuthor: a <a@b>\nDate:   Mon Nov 1 10:00:00 2026\n\n    msg {i}\n\n" for i in range(60))
    r = flt.govern("git log", s)
    assert r.klass == "git" and r.text.count("\n") < 70 and "msg 59" in r.text


def test_footer_mentions_spill():
    r = flt.govern("pytest", "\n".join(f"a.py::t{i} PASSED" for i in range(300)))
    f = flt.footer(r, "abc123")
    assert "tokctl show abc123" in f and "tests" in f


def test_classify_priority_read_wins_over_grep():
    assert flt.classify("cat f.py | grep x") == "read"
    assert flt.classify("grep -rn x src/") == "search"
    assert flt.classify("cd a && npm test") == "tests"
    assert flt.classify("docker compose logs --tail 200 api") == "logs"
