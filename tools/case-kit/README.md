# Case kit

The robot-independent parts of an online-control case. What a case is and what the contract requires:
[docs/CASE.md](../../docs/CASE.md).

| File | Runs where | What it does |
|---|---|---|
| `casekit/recording.py` | service, verifier | hash-chained recording, seal, `verify()` |
| `casekit/episode.py` | service | the call envelope: `Episode` base class, `@op`, `Refused`, `BadRequest`, `run()` |
| `casekit/launch.py` | service (root) | `launch()` starts the service once; `finish_and_export()` for the collect hook (its own finish is marked as the collector's) |
| `casekit/verify.py` | verifier | `load_export()` checks chain, seal, API, scene and that the agent called finish; `main(grade)` writes the reward |
| `sync.py` | your machine, CI | vendors `casekit/` into every `casekit/` directory of a task; `--check` for CI |
| `gate.py` | your machine, CI | the CPU gate: builds the task's images and checks the contract end to end |
| `example/gantry-push/` | | a complete case: three-axis gantry, puck, MuJoCo, closed-loop reference solution |

## Writing the service

```python
from casekit.episode import BadRequest, Episode, Refused, op, run

class MyRobot(Episode):
    API, BACKEND = "my-robot-api-v1", "mujoco"

    def snapshot(self):                 # recorded after every recorded op; the replay must reproduce it
        return {...}

    @op(budget="actions")               # counted against BUDGETS["actions"], recorded
    def act(self, body):
        if malformed: raise BadRequest("...")    # 400, not counted
        if unsafe:    raise Refused("...")       # 200 ok:false, counted, recorded, nothing moves
        ...
        return {"executed": True, ...}           # whatever the verifier needs to replay it

    @op(budget="observations")
    def observe(self, body): ...

    @op(record=False)                   # free, not recorded: only for queries that change nothing
    def state(self, body): ...
```

`run(episode, state_dir, run_dir)` serves it and writes `run_dir/endpoint.json` for the agent. The task's
`launch.py` (behind the sudo launcher) calls `casekit.launch.launch(serve.py, state_dir, run_dir, backend)`; its
`collect.py` calls `launch` and then `finish_and_export(state_dir, state_dir / "export")`.

## Running the gate

```
python tools/case-kit/sync.py --check <task>
python tools/case-kit/gate.py <task> [--json gate.json] [--keep DIR]
```

Requires Docker and Python 3.11+. Checks: layout, isolation, api, envelope, finish, nop, oracle, recording, replay,
truncated, unfinished (the list with details is at the top of `gate.py`).

## Tests

```
cd tools/case-kit && python -m unittest discover -s tests -v
```
