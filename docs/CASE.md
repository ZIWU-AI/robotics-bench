# What a case contains

A case is one complete, self-contained robotics task: a world, a robot, a way to act and observe, a goal, a check
that decides success, and a solution that proves the goal is reachable. **You choose the robot, the engine, the
action and observation spaces, the scene and the success check.** The bench fixes only the contract that makes any
case fair, verifiable and impossible to game: how the agent reaches your world, what gets recorded, and how the
result is re-derived offline.

Start from [`tools/case-kit/example/gantry-push`](../tools/case-kit/example/gantry-push): a three-axis gantry
pushing a puck in MuJoCo, under 500 lines in total, that passes the gate.

## 1. What you bring

| Part | What it is | Your choice | In the example |
|---|---|---|---|
| World | engine, robot model (any arm, gripper, mobile base, gantry, hand, several robots), objects, cameras, initial layout | free | `environment/service/sim.py`, `scene.json` |
| Agent API | the operations the agent may call, their arguments and answers: joint or Cartesian targets, primitives, images, numbers; documented for the agent | free inside the envelope (section 2) | `environment/service/serve.py`, `environment/app/client.py` |
| Instruction | `instruction.md`: the goal, the API, the budgets, and the success conditions in words | free | `instruction.md` |
| Success check (verify) | **you declare it.** Code that decides success from the replayed states: what counts as done, with which thresholds | free, but deterministic code (section 3) | `sim.success()`, `tests/verify.py` |
| Reference solution | `solution/solve.sh`: solves the task **closed loop through the same API the agent has**, from what the agent can observe | required | `solution/oracle.py` |
| Metadata | `task.toml`, `README.md`, the origin and licence of every asset | required | `task.toml` |

Layouts are yours too. A case may declare several layouts (for example development and evaluation ones). The
evaluation layouts live only in the protected service directory and in `tests/`, never in `/app`, `instruction.md`
or anything else the agent can read.

## 2. What the bench fixes (the contract)

Each point is checked by the gate (`python tools/case-kit/gate.py <task>`, section 5).

1. **Isolation.** The agent works as a non-root user. Your simulator, scene and recording are in root-only
   directories. The agent's only privilege is one `sudo` rule for a launcher that takes no arguments and starts your
   service. The verifier runs in a separate container without network; `tests/` and `solution/` are never in the
   agent's image.
2. **Call envelope.** One episode served over HTTP + JSON on 127.0.0.1 with a bearer token
   ([`casekit/episode.py`](../tools/case-kit/casekit/episode.py) implements it):
   - `GET /v1/info` returns `api`, `backend`, `budgets`, `left` (and whatever else your agent needs);
   - `POST /v1/<op>` for your operations; `POST /v1/finish` ends the episode, after which everything is refused;
   - a refusal is `200 {"ok": false, "refused": [...]}` and changes nothing in the world; a malformed request is
     `400`, an unknown op `404`, a missing token `401`, all with a JSON body;
   - the launcher writes `/run/<service>/endpoint.json` with `url` and `token` for the agent's client.
3. **Recording.** Every request that can change the world, its result and the state after it go into a hash-chained,
   sealed recording ([`casekit/recording.py`](../tools/case-kit/casekit/recording.py)). A root collect hook finishes
   the episode if the agent did not (that finish is recorded as the collector's) and exports the recording as an
   artifact.
4. **Replay, not trust.** The verifier re-executes the recording in a fresh copy of your world, compares every
   recorded state, and evaluates your success check on the replayed states. It never runs the agent's program and
   never trusts a score the service computed. Same layout plus same commands must give the same states: exactly on
   CPU engines, or within tolerances you declare in the verifier.
5. **Budgets.** Counted operations (actions, observations, ...) are budgeted by the service and declared in
   `instruction.md`. Time has one limit only: `[agent].timeout_sec` (the template's 18000 s), which starts with the
   agent and is the "You have N seconds" line; when it runs out the collector seals the episode. The service keeps
   no clock of its own (`casekit` rejects a `wall_s` budget, `check-case-single-clock` rejects one described in
   `instruction.md`), because a clock that starts at the first request tells the agent it has time it does not. Calling `finish` is submitting; an episode the agent did not finish scores 0, even
   when the world ended in a successful state.
6. **Calibration of the task.** The reference solution scores 1; an episode that only calls `finish` scores 0; an
   episode the agent did not finish scores 0; an edited, truncated or state-forged recording scores 0.

## 3. Declaring the success check

The success check is yours; the bench only fixes how it runs.

- It is code in `tests/`, evaluated on replayed states. No human judgement, no model as a judge.
- It is deterministic: the same recording always gives the same reward.
- It matches `instruction.md` exactly: every condition the code tests is stated in the instruction with its
  threshold, and nothing stated is left untested.
- It cannot be satisfied by a shortcut: think about what a lazy or adversarial policy would do (knock the target
  over, push everything off the table, finish at once) and make sure it fails. Reviewers will try.
- It reads only recorded or replayed state. Any ground truth it needs (object poses, contacts) belongs in the
  snapshot your service records.

## 4. What the platform provides

- The case kit (`tools/case-kit`): recording, call envelope, root launcher and collector, verifier helpers, the
  gate, and `sync.py` to vendor them into your task.
- Harbor runs, CI checks, and review.
- After your CPU tier is merged, the GPU pool for your GPU tier, once the pool opens (see
  [LADDER.md](LADDER.md)).

## 5. Building one

1. Copy `tools/case-kit/example/gantry-push` to `tasks/<name>` and rename the service, the
   directories and the API.
2. Replace `sim.py` with your world, `serve.py` with your API, `verify.py` and the success function with your check,
   `oracle.py` with your solution. Keep `environment/service/casekit/` and `tests/casekit/` and run
   `python tools/case-kit/sync.py <task>` whenever the kit changes.
3. Files your service and verifier share (the simulator module, the scene) are copies in two build contexts; keep
   them identical.
4. Run the gate locally (Docker, about a minute plus your oracle's run time):

       python tools/case-kit/gate.py tasks/<name> --keep /tmp/my-gate

   `--keep` leaves the exports, verifier logs and the oracle log there.
5. Run the task's static checks and Harbor Oracle and Nop, then open the PR for the CPU tier.
