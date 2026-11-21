"""CPU gate for online-control cases, whatever the robot, the engine or the action space.

    python tools/case-kit/gate.py <task dir> [--json out.json] [--keep DIR]

It builds the task's own images (environment/ and tests/) with Docker and checks the contract in docs/CASE.md:

  layout     task.toml: a separate verifier environment without network, a root collect hook, one export directory
             in artifacts besides /app/output, an agent user
  isolation  as the agent: the service directory, the state directory and the collector cannot be read; sudo grants
             exactly one launcher, with no arguments, and nothing else; the image holds no /tests or /solution
  api        the launcher starts the service; /run/<service>/endpoint.json gives url and token; GET /v1/info answers
             with api, backend, budgets and left
  envelope   no token: 401; an unknown op: 404; invalid JSON: 400; each with a JSON body {"ok": false, ...}
  finish     finish is accepted once; a second finish and every op listed in info after it are refused
  nop        that episode (it did nothing but finish), collected and verified: reward 0
  oracle     solution/solve.sh as the agent, collected and verified: reward 1
  recording  the oracle's export verifies (casekit.recording) and ends with finish; one edited line: reward 0
  replay     one recorded state changed and the chain re-sealed: reward 0 (the verifier replays, it does not trust)
  truncated  the finish entry removed and the chain re-sealed: reward 0
  unfinished an episode the agent never finishes, collected: the recording ends with the collector's marked finish;
             the oracle's recording with its finish marked as the collector's and the chain re-sealed: reward 0

Exit code 0 only if every check passes. Needs Docker and Python 3.11+ on the host; nothing else.
"""
import argparse
import copy
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from casekit import recording  # noqa: E402

PROBE_IMAGE = "python:3.12-slim-bookworm@sha256:6dd8b6dab6b3adc7812bab69eca5dbdc1a5cc690ad28306d234ffb4ef8ac9753"
SUDO_LINE = re.compile(r'^\s*(\S+)\s+ALL\s*=\s*\(root\)\s*NOPASSWD:\s*(/\S+)\s+""\s*$')
RESULTS = []


def check(name, ok, detail=""):
    detail = detail if len(detail) <= 600 else detail[:600] + " ..."
    RESULTS.append({"check": name, "pass": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)
    return ok


def sh(args, timeout=1800, check_rc=False, **kw):
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, **kw)
    if check_rc and r.returncode:
        raise RuntimeError(f"{' '.join(map(str, args))[:200]} failed ({r.returncode}): {(r.stderr or r.stdout)[-1500:]}")
    return r


# ---------------------------------------------------------------------------------------------- recording tampering
def _write(export, entries, seal):
    body = "".join(recording.canonical(e) + "\n" for e in entries)
    (export / "recording.jsonl").write_text(body)
    seal = dict(seal, entries=len(entries), last_hash=entries[-1]["hash"] if entries else recording.GENESIS,
                recording_sha256=hashlib.sha256(body.encode()).hexdigest())
    (export / "seal.json").write_text(json.dumps(seal, indent=2) + "\n")


def rechain(entries):
    prev = recording.GENESIS
    for i, e in enumerate(entries):
        e["seq"], e["prev"] = i, prev
        e["hash"] = recording.entry_hash(e)
        prev = e["hash"]
    return entries


def _numeric_leaf(obj, path=()):
    if isinstance(obj, bool):
        return None
    if isinstance(obj, (int, float)):
        return path
    items = sorted(obj.items()) if isinstance(obj, dict) else enumerate(obj) if isinstance(obj, list) else []
    for k, v in items:
        found = _numeric_leaf(v, path + (k,))
        if found is not None:
            return found
    return None


def tamper_state(export):
    """Change one number in a recorded state (late in the episode) and re-seal a valid chain. Returns what changed."""
    entries, seal = recording.verify(export)
    entries = copy.deepcopy(entries)
    for i in range(len(entries) - 2, -1, -1):
        path = _numeric_leaf(entries[i].get("state"))
        if path:
            node = entries[i]["state"]
            for k in path[:-1]:
                node = node[k]
            node[path[-1]] = round(float(node[path[-1]]) + 0.01, 6)
            _write(export, rechain(entries), seal)
            return f"entry {i} state {'.'.join(map(str, path))} +0.01"
    raise ValueError("no recorded state with a number to change")


def truncate(export):
    entries, seal = recording.verify(export)
    if len(entries) < 2:
        raise ValueError("too few entries")
    _write(export, rechain(copy.deepcopy(entries[:-1])), seal)


def collector_finish(export):
    """Mark the final finish as the collector's (as if the agent had never called it) and re-seal the chain."""
    entries, seal = recording.verify(export)
    entries = copy.deepcopy(entries)
    if not entries or entries[-1]["op"] != "finish":
        raise ValueError("the recording does not end with finish")
    entries[-1]["request"] = {"by": "collector"}
    _write(export, rechain(entries), seal)


def edit_line(export):
    lines = (export / "recording.jsonl").read_text().splitlines()
    e = json.loads(lines[0])
    e["op"] = e["op"] + "-edited"
    lines[0] = recording.canonical(e)
    (export / "recording.jsonl").write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------------------------- task.toml
def layout(task):
    cfg = tomllib.loads((task / "task.toml").read_text())
    ver, agent = cfg.get("verifier", {}), cfg.get("agent", {})
    collect = [c for c in ver.get("collect", []) if c.get("user") == "root"]
    exports = [a for a in cfg.get("artifacts", []) if a.rstrip("/") != "/app/output"]
    problems = []
    if ver.get("environment_mode") != "separate":
        problems.append("verifier.environment_mode must be \"separate\"")
    if ver.get("environment", {}).get("network_mode") != "no-network":
        problems.append("verifier.environment.network_mode must be \"no-network\"")
    if len(collect) != 1:
        problems.append("exactly one [[verifier.collect]] hook with user = \"root\" is needed")
    if len(exports) != 1:
        problems.append("artifacts must list /app/output and exactly one export directory")
    if not agent.get("user") or agent.get("user") == "root":
        problems.append("agent.user must be a non-root user")
    for p in ("environment/Dockerfile", "tests/Dockerfile", "tests/test.sh", "solution/solve.sh", "instruction.md"):
        if not (task / p).is_file():
            problems.append(f"{p} is missing")
    if problems:
        return None, problems
    cmd = collect[0]["command"]
    script = next((w for w in shlex.split(cmd) if w.endswith(".py") and w.startswith("/")), None)
    if script is None:
        return None, [f"the collect command names no absolute .py script: {cmd}"]
    export = exports[0].rstrip("/")
    return {"collect": cmd, "collect_script": script, "service_dir": str(Path(script).parent), "export": export,
            "state_dir": str(Path(export).parent), "agent": agent["user"],
            "verifier_workdir": ver.get("environment", {}).get("workdir", "/tests"),
            "agent_timeout": float(agent.get("timeout_sec", 3600))}, []


# ---------------------------------------------------------------------------------------------- docker helpers
class Episode:
    def __init__(self, image, name, agent):
        self.name, self.agent = name, agent
        sh(["docker", "rm", "-f", name])
        sh(["docker", "run", "-d", "--name", name, "-u", "root", "--entrypoint", "sleep", image, "infinity"],
           check_rc=True)

    def run(self, cmd, user=None, timeout=600):
        return sh(["docker", "exec", "-u", user or self.agent, self.name, "sh", "-c", cmd], timeout=timeout)

    def close(self):
        sh(["docker", "rm", "-f", self.name])


def verify_export(tests_image, export_host, lay, out):
    logs = out / "logs"
    shutil.rmtree(logs, ignore_errors=True)
    logs.mkdir(parents=True)
    r = sh(["docker", "run", "--rm", "--network", "none", "-u", "root", "-w", lay["verifier_workdir"],
            "-v", f"{export_host}:{lay['export']}", "-v", f"{logs}:/logs/verifier", "--entrypoint", "sh",
            tests_image, "/tests/test.sh"], timeout=3600)
    reward = (logs / "reward.txt").read_text().strip() if (logs / "reward.txt").is_file() else None
    details = {}
    if (logs / "details.json").is_file():
        try:
            details = json.loads((logs / "details.json").read_text())
        except ValueError:
            pass
    return reward, details, (r.stdout + r.stderr)[-800:]


def collect(ep, lay, dest):
    r = ep.run(lay["collect"], user="root", timeout=600)
    if r.returncode:
        raise RuntimeError(f"collect failed: {(r.stderr or r.stdout)[-800:]}")
    shutil.rmtree(dest, ignore_errors=True)
    sh(["docker", "cp", f"{ep.name}:{lay['export']}", str(dest)], check_rc=True)
    return dest


PROBE = r'''
import json, sys, urllib.error, urllib.request
url, token = sys.argv[1].rstrip("/"), sys.argv[2]
op = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
def call(method, path, body=None, auth=True, raw=None):
    h = {"Content-Type": "application/json"}
    if auth:
        h["Authorization"] = "Bearer " + token
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(url + path, data=data, method=method, headers=h)
    try:
        with op(req, timeout=120) as r:
            code, text = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read()
    try:
        obj = json.loads(text)
    except ValueError:
        obj = None
    return {"code": code, "json": obj}
out = {"noauth": call("GET", "/v1/info", auth=False), "info": call("GET", "/v1/info"),
       "unknown": call("POST", "/v1/casegate-unknown-op", {}), "badjson": call("POST", "/v1/finish", raw=b"{not json")}
out["finish"] = call("POST", "/v1/finish", {})
out["finish_again"] = call("POST", "/v1/finish", {})
ops = [o for o in ((out["info"]["json"] or {}).get("ops") or []) if o not in ("info", "finish")]
out["after"] = {o: call("POST", "/v1/" + o, {}) for o in ops}
print(json.dumps(out))
'''


def _err(resp, code):
    j = resp.get("json")
    return resp.get("code") == code and isinstance(j, dict) and j.get("ok") is False


# ---------------------------------------------------------------------------------------------- the gate
def gate(task, work):
    lay, problems = layout(task)
    if not check("layout", not problems, "; ".join(problems) or
                 f"collect {lay['collect_script']}, export {lay['export']}, agent {lay['agent']}"):
        return
    tag = "casegate-" + hashlib.sha256(str(task).encode()).hexdigest()[:10]
    env_img, tests_img = f"{tag}-env", f"{tag}-tests"
    t0 = time.time()
    sh(["docker", "build", "-q", "-t", env_img, str(task / "environment")], timeout=3600, check_rc=True)
    sh(["docker", "build", "-q", "-t", tests_img, str(task / "tests")], timeout=3600, check_rc=True)
    print(f"(built both images in {time.time() - t0:.0f} s)", flush=True)
    agent = lay["agent"]

    # isolation -------------------------------------------------------------------------------------------------
    def as_agent(cmd):
        return sh(["docker", "run", "--rm", "-u", agent, "--entrypoint", "sh", env_img, "-c", cmd], timeout=300)

    def as_root(cmd):
        return sh(["docker", "run", "--rm", "-u", "root", "--entrypoint", "sh", env_img, "-c", cmd], timeout=300)

    issues = []
    for what, cmd in (("list the service directory", f"ls {shlex.quote(lay['service_dir'])}"),
                      ("list the state directory", f"ls {shlex.quote(lay['state_dir'])}"),
                      ("read the collector", f"cat {shlex.quote(lay['collect_script'])}"),
                      ("use sudo for anything else", "sudo -n true")):
        if as_agent(cmd + " >/dev/null 2>&1").returncode == 0:
            issues.append(f"the agent can {what}")
    sudoers = as_root("cat /etc/sudoers /etc/sudoers.d/* 2>/dev/null").stdout
    groups = as_root(f"id -nG {shlex.quote(agent)}").stdout.split()
    principals = {agent, "ALL"} | {"%" + g for g in groups}
    lines = [ln.strip() for ln in sudoers.splitlines()
             if ln.strip() and not ln.lstrip().startswith(("#", "Defaults", "@include")) and ln.split()[0] in principals]
    granted = [m.group(2) for ln in lines if (m := SUDO_LINE.match(ln)) and m.group(1) == agent]
    others = [ln for ln in lines if not ((m := SUDO_LINE.match(ln)) and m.group(1) == agent)]
    if len(granted) != 1:
        issues.append(f"sudoers must grant {agent} exactly one launcher with \"\" (no arguments); found {granted}")
    if others:
        issues.append(f"other sudo rights of {agent}: {others}")
    if set(groups) & {"sudo", "wheel", "admin", "root"}:
        issues.append(f"{agent} is in {sorted(set(groups) & {'sudo', 'wheel', 'admin', 'root'})}")
    launcher = granted[0] if len(granted) == 1 else None
    if launcher and as_agent(f"sudo -n {shlex.quote(launcher)} x >/dev/null 2>&1").returncode == 0:
        issues.append("the launcher accepts an argument")
    if as_root("test -e /tests || test -e /solution").returncode == 0:
        issues.append("the agent image contains /tests or /solution")
    check("isolation", not issues, "; ".join(issues) or f"launcher {launcher}; service, state and collector closed")
    if not launcher:
        return

    # api, envelope, finish, nop ---------------------------------------------------------------------------------
    ep = Episode(env_img, f"{tag}-nop", agent)
    try:
        r = ep.run(f"sudo -n {shlex.quote(launcher)}", timeout=300)
        files = ep.run("for f in /run/*/endpoint.json; do [ -f \"$f\" ] && echo \"$f\"; done").stdout.split()
        if r.returncode or len(files) != 1:
            check("api", False, f"launcher rc {r.returncode} {(r.stderr or '')[-300:]}; endpoint files {files}")
            return
        endpoint = json.loads(ep.run(f"cat {files[0]}").stdout)
        probe = work / "probe.py"
        probe.write_text(PROBE)
        pr = sh(["docker", "run", "--rm", "--network", f"container:{ep.name}", "-v", f"{probe}:/probe.py:ro",
                 PROBE_IMAGE, "python", "-I", "/probe.py", endpoint["url"], endpoint["token"]], timeout=600)
        res = json.loads(pr.stdout) if pr.returncode == 0 else {}
        info = (res.get("info") or {}).get("json") or {}
        check("api", res.get("info", {}).get("code") == 200 and all(k in info for k in ("api", "backend", "budgets", "left")),
              f"{files[0]}; info api={info.get('api')} backend={info.get('backend')} budgets={info.get('budgets')}"
              + ("" if res else f"; probe failed: {pr.stderr[-400:]}"))
        env_ok = res and _err(res["noauth"], 401) and _err(res["unknown"], 404) and _err(res["badjson"], 400)
        check("envelope", env_ok, ", ".join(f"{k} -> {res.get(k, {}).get('code')}" for k in ("noauth", "unknown", "badjson")))
        fin, again, after = res.get("finish", {}), res.get("finish_again", {}), res.get("after", {})
        refused_after = [o for o, a in after.items() if not ((a.get("json") or {}).get("ok") is False)]
        check("finish", fin.get("code") == 200 and (fin.get("json") or {}).get("ok") is True
              and (again.get("json") or {}).get("ok") is False and not refused_after,
              f"finish ok; second finish refused: {(again.get('json') or {}).get('ok') is False}; ops after finish "
              f"accepted: {refused_after or 'none'} (of {sorted(after) or 'none listed'})")
        export = collect(ep, lay, work / "nop_export")
        reward, details, log = verify_export(tests_img, export, lay, work / "nop")
        check("nop", reward == "0", f"reward {reward}; {details.get('problems') or details.get('error') or ''}"
              + ("" if reward == "0" else f"; {log}"))
    finally:
        ep.close()

    # unfinished: the agent starts the service and never calls finish ---------------------------------------------
    ep = Episode(env_img, f"{tag}-unfinished", agent)
    try:
        ep.run(f"sudo -n {shlex.quote(launcher)}", timeout=300)
        unfinished = collect(ep, lay, work / "unfinished_export")
    finally:
        ep.close()
    try:
        last = (recording.verify(unfinished)[0] or [{}])[-1]
    except ValueError as e:
        last = {"error": str(e)}
    marked = last.get("op") == "finish" and (last.get("request") or {}).get("by") == "collector"
    marked_detail = f"collected without a finish from the agent: last entry {last.get('op')} {last.get('request')}"

    # oracle ------------------------------------------------------------------------------------------------------
    ep = Episode(env_img, f"{tag}-oracle", agent)
    try:
        sh(["docker", "cp", str(task / "solution"), f"{ep.name}:/solution"], check_rc=True)
        ep.run("chmod -R a+rX /solution", user="root")
        t0 = time.time()
        r = ep.run("cd /app 2>/dev/null; sh /solution/solve.sh", timeout=min(lay["agent_timeout"], 7200))
        took = time.time() - t0
        (work / "oracle.log").write_text(r.stdout + r.stderr)
        export = collect(ep, lay, work / "oracle_export")
    finally:
        ep.close()
    reward, details, log = verify_export(tests_img, export, lay, work / "oracle")
    check("oracle", reward == "1", f"solve.sh rc {r.returncode} in {took:.0f} s; reward {reward}; used "
          f"{details.get('used')}" + ("" if reward == "1" else f"; {details.get('problems') or details.get('error')}"
                                      f"; solve.sh: {(r.stdout + r.stderr)[-500:]}"))

    # recording, replay, truncated ---------------------------------------------------------------------------------
    try:
        entries, seal = recording.verify(export)
        rec_ok = entries and entries[-1]["op"] == "finish"
        detail = f"{len(entries)} entries, ends with {entries[-1]['op'] if entries else None}"
    except ValueError as e:
        rec_ok, detail = False, f"does not verify: {e}"
    if rec_ok:
        bad = work / "edited"
        shutil.copytree(export, bad, dirs_exist_ok=True)
        edit_line(bad)
        rw, _, _ = verify_export(tests_img, bad, lay, work / "edited_run")
        rec_ok = rw == "0"
        detail += f"; edited line: reward {rw}"
    check("recording", rec_ok, detail)
    if reward != "1":
        check("replay", False, "needs an oracle episode with reward 1")
        check("truncated", False, "needs an oracle episode with reward 1")
        check("unfinished", False, marked_detail + "; needs an oracle episode with reward 1")
        return
    bad = work / "state_tampered"
    shutil.copytree(export, bad, dirs_exist_ok=True)
    what = tamper_state(bad)
    rw, d, _ = verify_export(tests_img, bad, lay, work / "state_run")
    check("replay", rw == "0", f"{what}: reward {rw}; {d.get('problems') or d.get('error') or ''}")
    bad = work / "truncated"
    shutil.copytree(export, bad, dirs_exist_ok=True)
    truncate(bad)
    rw, d, _ = verify_export(tests_img, bad, lay, work / "truncated_run")
    check("truncated", rw == "0", f"finish entry removed: reward {rw}; {d.get('problems') or d.get('error') or ''}")
    bad = work / "collector_finished"
    shutil.copytree(export, bad, dirs_exist_ok=True)
    collector_finish(bad)
    rw, d, _ = verify_export(tests_img, bad, lay, work / "collector_run")
    check("unfinished", marked and rw == "0", f"{marked_detail}; oracle with the collector's finish: reward {rw}; "
          f"{d.get('problems') or d.get('error') or ''}")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("task", type=Path)
    p.add_argument("--json")
    p.add_argument("--keep", help="keep exports, verifier logs and the oracle log in this directory")
    a = p.parse_args()
    work = Path(a.keep).resolve() if a.keep else Path(tempfile.mkdtemp(prefix="casegate-"))
    work.mkdir(parents=True, exist_ok=True)
    try:
        gate(a.task.resolve(), work)
    except Exception as e:
        check("gate run", False, f"{type(e).__name__}: {e}")
    ok = bool(RESULTS) and all(r["pass"] for r in RESULTS)
    print(f"\nCASE GATE {'PASS' if ok else 'FAIL'} ({sum(r['pass'] for r in RESULTS)}/{len(RESULTS)})")
    if a.json:
        Path(a.json).write_text(json.dumps({"task": str(a.task), "pass": ok, "checks": RESULTS}, indent=2) + "\n")
    if not a.keep:
        shutil.rmtree(work, ignore_errors=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
