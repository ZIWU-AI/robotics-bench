# Robotics-Bench Contributor Guidance

Robotics-Bench measures how well AI agents carry out robotics work. Each benchmark item, a **case**, is a complete
robotics problem contributed by its author: a simulated world, a robot, an interface through which the agent acts and
observes, a goal, and a success check. The agent solves the case through that interface alone; its score is
re-derived offline from a sealed record of everything it did.

This document states what a case must provide, what the platform provides, and how a case moves from proposal to
the benchmark. The technical contract is specified in [docs/CASE.md](docs/CASE.md); the submission mechanics are in
[CONTRIBUTING.md](CONTRIBUTING.md). The key words MUST, MUST NOT, SHOULD and MAY are used in their usual normative
sense.

## 1. Roles

| | Contributes | Does not |
|---|---|---|
| **Author** | The whole case: world, robot, engine, scene, agent interface, instruction, success check, reference solution, metadata | Run evaluations of models |
| **Platform** | Agent integration and runs: CPU containers, the GPU pool, the CPU gate and static checks, review automation, scoring of model runs | Supply robots, simulators, scenes or success checks |
| **Agent** | A policy, written or executed through the case's interface within the case's budgets | See the simulator, the scene definition, the verifier or the reference solution |

The platform is deliberately neutral. Any robot (arm, dual arm, mobile manipulator, hand, gantry, several robots),
any engine and any action or observation space is admissible, provided the case meets the contract in section 3.

## 2. What a case is

A case is one task directory, `tasks/<slug>/`, with the stable name `robotics-bench/<slug>`. Its category
(manipulation, locomotion, bimanual, mobile-manipulation, ...) is the first of its `tags` in `task.toml`:

```text
<slug>/
├── task.toml          identity, authors, resources, network, budgets, artifacts, collect hook
├── instruction.md     everything the agent is told: goal, interface, units, budgets, success conditions
├── README.md          generated header plus the author's notes on difficulty, solution and verification
├── environment/       the agent's image: public client and documentation; the protected service (root only)
├── solution/          the reference solution (never part of the agent's image)
└── tests/             the verifier's image: replay of the recording and the author's success check
```

Two kinds of case are supported ([docs/TASK_TYPES.md](docs/TASK_TYPES.md)):

- **Online control.** The agent drives the robot through the case's API during the episode. The service records
  every request; the verifier replays the recording and applies the success check. This is the contract in
  [docs/CASE.md](docs/CASE.md), implemented by [tools/case-kit](tools/case-kit) and illustrated by the complete
  example [tools/case-kit/example/gantry-push](tools/case-kit/example/gantry-push).
- **Offline submission.** The agent hands in an artifact (a controller, a plan, parameters); the verifier runs it
  under conditions the agent never saw ([docs/CPU_TASK_GUIDE.md](docs/CPU_TASK_GUIDE.md)).

## 3. Requirements

### 3.1 Isolation

1. The agent MUST run as a non-root user. The simulator, the scene, hidden layouts and the recording MUST live in
   root-only locations.
2. The agent's only elevated privilege MAY be a single `sudo` rule for a launcher that takes no arguments and starts
   the case's service.
3. The verifier MUST run in a separate container without network access. `tests/` and `solution/` MUST NOT be
   copied into the agent's image.

### 3.2 Interface

4. An online-control case MUST serve the call envelope defined in [docs/CASE.md](docs/CASE.md) section 2: HTTP and
   JSON on the loopback interface, a bearer token, `GET /v1/info`, `POST /v1/<op>`, `POST /v1/finish`, and the
   specified answers for refused, malformed, unknown and unauthorised requests.
5. Everything an agent needs to act (frames, units, limits, budgets, the meaning of every field) MUST be stated in
   `instruction.md` or in documentation inside the agent's image. Nothing the agent must not know may be exposed
   through `/v1/info` or any other answer.
6. Counted operations MUST be budgeted by the service and stated in `instruction.md`. Time has one limit,
   `[agent].timeout_sec` (the "You have N seconds" line); the service MUST NOT keep a clock of its own
   ([docs/CASE.md](docs/CASE.md) section 2, `check-case-single-clock`). Calling `finish` is submitting; an episode
   the agent did not finish MUST score 0.

### 3.3 Recording and verification

7. Every request that can change the world, its result and the state after it MUST be written to the hash-chained,
   sealed recording of [tools/case-kit/casekit/recording.py](tools/case-kit/casekit/recording.py), and a root collect
   hook MUST finish the episode, marking that finish as the collector's when the agent did not call it, and export
   the recording as an artifact.
8. The verifier MUST re-execute the recording in a fresh instance of the world, compare every recorded state, and
   evaluate the success check on the replayed states. It MUST NOT run the agent's program and MUST NOT trust any
   score computed by the service.
9. Replay MUST be deterministic: exact on CPU engines, or within tolerances the verifier declares for GPU engines.

### 3.4 Success check

10. The author declares the success check. It MUST be code in `tests/`, MUST be deterministic, and MUST NOT rely on
    human judgement or on a model acting as a judge.
11. Every condition the code tests MUST be stated in `instruction.md` with its threshold, and every condition the
    instruction states MUST be tested.
12. The check MUST NOT be satisfiable by a shortcut (finishing at once, knocking the target over, clearing the
    table). Authors SHOULD describe in the README which shortcuts they considered.
13. Rewards are binary: 1 for success, 0 otherwise.

### 3.5 Reference solution and calibration

14. `solution/solve.sh` MUST solve the case **closed loop through the same interface the agent has**, from what the
    agent can observe. A replayed trajectory is not a reference solution.
15. The reference solution MUST score 1; an episode that only calls `finish` MUST score 0; an episode the agent did
    not finish MUST score 0; an edited, truncated or state-forged recording MUST score 0. The CPU gate checks all of
    these.

### 3.6 Provenance and resources

16. Every asset (meshes, textures, datasets, robot models) MUST have a stated origin and a licence that permits
    redistribution ([docs/ASSETS.md](docs/ASSETS.md)); large files follow [docs/LARGE-DATA.md](docs/LARGE-DATA.md).
17. Authorship in `task.toml` MUST name the people who built the case.
18. A CPU-tier container MUST fit a standard runner: at most 2 CPUs and 6144 MB of memory each for the agent and the
    verifier.

## 4. Tiers

A tier is an evaluation condition, not a difficulty level. Each tier of a case is a separate pull request on the same
task directory ([docs/LADDER.md](docs/LADDER.md)).

| Tier | Runs on | Required | Access |
|---|---|---|---|
| CPU | Standard Docker containers, a CPU engine (for example MuJoCo, PyBullet, or the author's own physics) | Yes, first | Any contributor, with Docker only |
| GPU | The platform's GPU pool, a GPU engine (for example Isaac Sim) declared by `host/service.json` | Optional, after the CPU tier is merged | Opening later; then granted to the author of the merged CPU tier |

A new case that runs only on GPUs is not accepted; the CPU tier is the case's entry point and its executable
specification. Differences between engines (contact, friction, rendering) are resolved by the author in the GPU-tier
pull request; the two tiers are scored independently. The GPU tier is specified in
[docs/GPU_TASK_GUIDE.md](docs/GPU_TASK_GUIDE.md).

## 5. From proposal to benchmark

1. **Propose.** Open a discussion in the *Task Proposals* category describing the problem, the robot, the success
   check and why it is hard for agents. A maintainer approves it with `/approve`.
2. **Build the CPU tier.** Start from [tools/case-kit/example/gantry-push](tools/case-kit/example/gantry-push), replace
   the world, the interface, the success check and the reference solution, and vendor the kit with
   `python tools/case-kit/sync.py <task>`.
3. **Check locally.**

   ```bash
   python tools/case-kit/gate.py tasks/<slug>      # the contract, end to end
   harbor run -p tasks/<slug> --agent oracle --env docker
   harbor run -p tasks/<slug> --agent nop --env docker
   ```

   and the static checks in `ci_checks/` ([CONTRIBUTING.md](CONTRIBUTING.md) lists the commands).
4. **Open a pull request from your fork** that adds the CPU tier only and links the approved proposal. Static checks,
   the CPU gate, and Oracle and Nop runs start automatically.
5. **Review.** A domain reviewer and a technical reviewer are assigned automatically; a final reviewer follows. All
   assigned reviewers must approve the current head commit before the pull request can merge
   ([REVIEWING.md](REVIEWING.md)).
6. **GPU tier (optional, opening later).** The GPU pool is not open yet in this repository. Once it opens, it is granted to the author of a merged CPU tier. Open a second pull
   request for the GPU tier and use the pull-request commands in [docs/COMMANDS.md](docs/COMMANDS.md) to check the
   scene and validate it on the pool.

## 6. After merge

A merged case is identified by its name and the commit that merged it. Any later change, including to its README,
creates a new version and goes through review again. Problems in a merged case are reported with the *Task fix
request* issue form; the fix is a pull request that references that issue and is reviewed by the case's original
reviewers.

## 7. References

| Document | Content |
|---|---|
| [docs/CASE.md](docs/CASE.md) | The case contract: what the author brings, what the bench fixes, the success check |
| [tools/case-kit](tools/case-kit) | Kit, CPU gate and the complete example case |
| [docs/TASK_FORMAT.md](docs/TASK_FORMAT.md) | `task.toml` fields and the task directory |
| [docs/TASK_TYPES.md](docs/TASK_TYPES.md) | Online control and offline submission |
| [docs/LADDER.md](docs/LADDER.md) | Tiers and GPU access |
| [docs/GPU_TASK_GUIDE.md](docs/GPU_TASK_GUIDE.md) | The GPU tier and `host/service.json` |
| [docs/COMMANDS.md](docs/COMMANDS.md) | Pull-request commands |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Submission steps and local checks |
| [REVIEWING.md](REVIEWING.md) | Review criteria |
