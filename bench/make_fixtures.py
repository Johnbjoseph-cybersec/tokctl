#!/usr/bin/env python3
"""Generate public-safe synthetic fixtures. Deterministic; re-run to regenerate."""
import json, os, random
random.seed(7)
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
os.makedirs(D, exist_ok=True)

def put(name, cmd, out):
    open(os.path.join(D, name + ".cmd"), "w").write(cmd + "\n")
    open(os.path.join(D, name + ".out"), "w").write(out)

# 1 pytest: 400 passing, 2 failing
lines = ["============================= test session starts ==============================", "collected 402 items", ""]
for i in range(400):
    lines.append(f"tests/test_mod{i % 17}.py::test_case_{i} PASSED [{int(100 * (i + 1) / 402):3d}%]")
lines += ["tests/test_auth.py::test_token_expiry FAILED [ 99%]", "tests/test_auth.py::test_refresh FAILED [100%]", "",
          "=================================== FAILURES ===================================", "_________________________ test_token_expiry _________________________",
          "    assert token.expires_at > now", "E   AssertionError: assert datetime(2026,1,1) > datetime(2026,1,2)", "",
          "=========================== short test summary info ============================", "FAILED tests/test_auth.py::test_token_expiry - AssertionError",
          "FAILED tests/test_auth.py::test_refresh - TimeoutError", "========================= 2 failed, 400 passed in 12.31s ========================="]
put("pytest", "cd /repo && pytest -q tests/", "\n".join(lines))

# 2 npm install
lines = []
for i in range(300):
    lines.append(f"npm http fetch GET 200 https://registry.npmjs.org/pkg-{i} {random.randint(20, 900)}ms")
lines += ["npm WARN deprecated inflight@1.0.6: This module is not supported", "", "added 1283 packages, and audited 1284 packages in 41s", "", "212 packages are looking for funding", "found 0 vulnerabilities"]
put("npm_install", "cd /app && npm install", "\n".join(lines))

# 3 docker logs with repeats
lines = []
for i in range(900):
    kind = random.choice(["INFO request GET /health 200 2ms"] * 20 + ["INFO worker heartbeat ok"] * 10 + ["WARN slow query 1203ms SELECT * FROM events"] + ["ERROR connection reset by peer"])
    lines.append(f"2026-11-11T10:{i // 60 % 60:02d}:{i % 60:02d}.{random.randint(0, 999):03d}Z {kind}")
put("docker_logs", "docker logs --tail 1000 api", "\n".join(lines))

# 4 ls -la
lines = ["total 128"]
for i in range(120):
    lines.append(f"-rw-r--r--@  1 someuser  staff  {random.randint(100, 90000):6d} Nov {random.randint(1, 11):2d} {random.randint(0, 23):02d}:{random.randint(0, 59):02d} file_{i:03d}.py")
put("ls_la", "ls -la /repo/src", "\n".join(lines))

# 5 git log full
lines = []
for i in range(80):
    lines += [f"commit {random.getrandbits(160):040x}", "Author: Dev <dev@example.com>", f"Date:   Tue Nov {1 + i % 11} 10:0{i % 10}:00 2026 +0000", "", f"    feat: change number {i}", ""]
put("git_log", "git log -80", "\n".join(lines))

# 6 big JSON
data = {"items": [{"id": i, "name": f"item-{i}", "tags": ["a", "b"], "price": i * 1.5} for i in range(600)], "page": 1, "total": 600}
put("json_api", "curl -s http://localhost:8000/api/items", json.dumps(data, indent=2))

# 7 code read (must be byte-identical)
code = "\n".join(["import os", "", "", "", "def f(x):  ", "    return x\r", "", "class A:", "    pass"] * 40)
put("code_read", "sed -n '1,400p' app/models.py", code)

# 8 grep results with long lines
lines = [f"src/file{i}.js:{i * 7}: " + "x" * random.randint(40, 1500) for i in range(300)]
put("grep_long", "grep -rn 'needle' src/", "\n".join(lines))
print("fixtures written to", D)
