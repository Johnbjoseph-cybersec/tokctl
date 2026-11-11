import json, os, subprocess, sys, time, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(ROOT, "tokctl", "hooks")


def run(hook, payload, env=None):
    e = dict(os.environ, **(env or {}))
    p = subprocess.run([sys.executable, os.path.join(HOOKS, hook)], input=json.dumps(payload), capture_output=True, text=True, env=e)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else None


def test_post_bash_enforce_replaces_output(tmp_path):
    env = {"TOKCTL_STATE": str(tmp_path)}
    (tmp_path / "config.json").write_text(json.dumps({"mode": "enforce"}))
    out = "\n".join(f"a.py::t{i} PASSED" for i in range(400)) + "\n=== 400 passed ==="
    res = run("post_bash.py", {"tool_name": "Bash", "session_id": "s1", "tool_use_id": "t1", "tool_input": {"command": "pytest"}, "tool_response": {"stdout": out, "stderr": "", "interrupted": False, "isImage": False}}, env)
    new = res["hookSpecificOutput"]["updatedToolOutput"]
    assert res["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "PASSED" not in new["stdout"] and "400 passed" in new["stdout"] and "tokctl show" in new["stdout"]
    assert new["interrupted"] is False and new["isImage"] is False
    led = [json.loads(l) for l in open(tmp_path / "ledger.jsonl")]
    assert led[0]["class"] == "tests" and led[0]["in"] > led[0]["out"]
    sid = led[0]["spill"]
    p = subprocess.run([sys.executable, os.path.join(ROOT, "tokctl", "cli.py"), "show", sid, "-g", "t399"], capture_output=True, text=True, env=dict(os.environ, **env))
    assert "a.py::t399 PASSED" in p.stdout


def test_post_bash_shadow_logs_but_changes_nothing(tmp_path):
    env = {"TOKCTL_STATE": str(tmp_path)}
    (tmp_path / "config.json").write_text(json.dumps({"mode": "shadow"}))
    out = "\n".join(f"a.py::t{i} PASSED" for i in range(400))
    res = run("post_bash.py", {"tool_name": "Bash", "session_id": "s", "tool_use_id": "t", "tool_input": {"command": "pytest"}, "tool_response": {"stdout": out, "stderr": ""}}, env)
    assert res is None and os.path.exists(tmp_path / "ledger.jsonl")


def test_post_bash_off_switch(tmp_path):
    env = {"TOKCTL_STATE": str(tmp_path)}
    (tmp_path / "OFF").write_text("x")
    out = "\n".join(f"a.py::t{i} PASSED" for i in range(400))
    assert run("post_bash.py", {"tool_name": "Bash", "tool_input": {"command": "pytest"}, "tool_response": {"stdout": out}}, env) is None


def test_post_bash_small_and_read_untouched(tmp_path):
    env = {"TOKCTL_STATE": str(tmp_path)}
    assert run("post_bash.py", {"tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_response": {"stdout": "a\nb"}}, env) is None
    code = "def f():\n    pass\n" * 500
    assert run("post_bash.py", {"tool_name": "Bash", "tool_input": {"command": "sed -n 1,900p x.py"}, "tool_response": {"stdout": code}}, env) is None


def test_post_bash_garbage_input_never_fails(tmp_path):
    p = subprocess.run([sys.executable, os.path.join(HOOKS, "post_bash.py")], input="not json", capture_output=True, text=True, env=dict(os.environ, TOKCTL_STATE=str(tmp_path)))
    assert p.returncode == 0 and p.stdout == ""


def _transcript(path, entries):
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _read_use(file_path, ts, offset=None, limit=None):
    inp = {"file_path": file_path}
    if offset: inp["offset"] = offset
    if limit: inp["limit"] = limit
    return {"type": "assistant", "timestamp": ts, "message": {"content": [{"type": "tool_use", "id": "x", "name": "Read", "input": inp}]}}


def test_pre_read_denies_unchanged_recent_reread(tmp_path):
    env = {"TOKCTL_STATE": str(tmp_path)}
    target = tmp_path / "f.py"; target.write_text("hi")
    os.utime(target, (time.time() - 3600, time.time() - 3600))
    tr = tmp_path / "t.jsonl"
    future = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 60))
    _transcript(tr, [_read_use(str(target), future)])
    res = run("pre_read.py", {"tool_name": "Read", "session_id": "s", "transcript_path": str(tr), "tool_input": {"file_path": str(target)}}, env)
    assert res["hookSpecificOutput"]["permissionDecision"] == "deny" and "has not changed" in res["hookSpecificOutput"]["permissionDecisionReason"]


def test_pre_read_allows_when_file_changed_or_range_differs_or_compacted(tmp_path):
    env = {"TOKCTL_STATE": str(tmp_path)}
    target = tmp_path / "f.py"; target.write_text("hi")
    tr = tmp_path / "t.jsonl"
    past = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 3600))
    _transcript(tr, [_read_use(str(target), past)])  # file mtime (now) is newer than the read -> changed
    assert run("pre_read.py", {"tool_name": "Read", "transcript_path": str(tr), "tool_input": {"file_path": str(target)}}, env) is None
    os.utime(target, (time.time() - 7200, time.time() - 7200))
    assert run("pre_read.py", {"tool_name": "Read", "transcript_path": str(tr), "tool_input": {"file_path": str(target), "offset": 10}}, env) is None
    _transcript(tr, [_read_use(str(target), past), {"type": "user", "isCompactSummary": True, "message": {"content": "summary"}}])
    assert run("pre_read.py", {"tool_name": "Read", "transcript_path": str(tr), "tool_input": {"file_path": str(target)}}, env) is None


def test_pre_compact_prints_instructions():
    p = subprocess.run([sys.executable, os.path.join(HOOKS, "pre_compact.py")], input="{}", capture_output=True, text=True)
    assert p.returncode == 0 and "OPEN THREADS" in p.stdout and len(p.stdout) < 10000


def _turn(calls, ctx):
    return {"type": "assistant", "message": {"usage": {"input_tokens": 1, "cache_read_input_tokens": ctx, "cache_creation_input_tokens": 0}, "content": [{"type": "tool_use", "id": f"i{k}", "name": "Bash", "input": {}} for k in range(calls)]}}


def test_ctx_nudge_fires_only_when_heavy_and_single_call(tmp_path):
    env = {"TOKCTL_STATE": str(tmp_path)}
    tr = tmp_path / "t.jsonl"
    _transcript(tr, [_turn(3, 50_000), _turn(1, 60_000), _turn(1, 61_000), _turn(1, 62_000)])
    assert run("ctx_nudge.py", {"session_id": "s", "transcript_path": str(tr), "tool_name": "Bash"}, env) is None  # light context
    _transcript(tr, [_turn(3, 400_000), _turn(1, 450_000), _turn(1, 451_000), _turn(1, 452_000)])
    res = run("ctx_nudge.py", {"session_id": "s2", "transcript_path": str(tr), "tool_name": "Bash"}, env)
    assert res and "452k tokens" in res["hookSpecificOutput"]["additionalContext"]
    assert run("ctx_nudge.py", {"session_id": "s2", "transcript_path": str(tr), "tool_name": "Bash"}, env) is None  # throttled
    _transcript(tr, [_turn(3, 400_000), _turn(2, 450_000), _turn(1, 451_000), _turn(1, 452_000)])
    assert run("ctx_nudge.py", {"session_id": "s3", "transcript_path": str(tr), "tool_name": "Bash"}, env) is None  # batching already happening


def test_prompt_ctx_line_only_when_heavy(tmp_path):
    env = {"TOKCTL_STATE": str(tmp_path)}
    tr = tmp_path / "t.jsonl"
    _transcript(tr, [_turn(1, 120_000)])
    assert run("prompt_ctx.py", {"session_id": "p", "transcript_path": str(tr), "prompt": "hi"}, env) is None
    _transcript(tr, [_turn(1, 640_000)])
    res = run("prompt_ctx.py", {"session_id": "p2", "transcript_path": str(tr), "prompt": "hi"}, env)
    assert res and "640k tokens" in res["hookSpecificOutput"]["additionalContext"]
    assert run("prompt_ctx.py", {"session_id": "p2", "transcript_path": str(tr), "prompt": "hi"}, env) is None


def test_ctx_nudge_groups_records_by_request_id(tmp_path):
    # one API turn with 3 tool calls is written as 3 records sharing a requestId: that is a batched turn, not three single ones
    env = {"TOKCTL_STATE": str(tmp_path)}
    tr = tmp_path / "t.jsonl"
    recs = []
    for t in range(4):
        for k in range(3):
            r = _turn(1, 450_000 + t); r["requestId"] = f"req{t}"; recs.append(r)
    _transcript(tr, recs)
    assert run("ctx_nudge.py", {"session_id": "g", "transcript_path": str(tr), "tool_name": "Bash"}, env) is None


def test_subagent_handoff_when_heavy(tmp_path):
    env = {"TOKCTL_STATE": str(tmp_path)}
    tr = tmp_path / "a.jsonl"
    _transcript(tr, [_turn(1, 420_000)])
    res = run("ctx_nudge.py", {"session_id": "s", "agent_id": "agent1", "transcript_path": str(tr), "tool_name": "Bash", "cwd": str(tmp_path)}, env)
    assert res and "hand" in res["hookSpecificOutput"]["additionalContext"] and "NEXT ACTION" in res["hookSpecificOutput"]["additionalContext"]
    assert run("ctx_nudge.py", {"session_id": "s", "agent_id": "agent1", "transcript_path": str(tr), "tool_name": "Bash"}, env) is None
    _transcript(tr, [_turn(1, 100_000)])
    assert run("ctx_nudge.py", {"session_id": "s", "agent_id": "agent2", "transcript_path": str(tr), "tool_name": "Bash"}, env) is None
