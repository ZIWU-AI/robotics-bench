# Robotics-Bench

Robotics-Bench evaluates AI agents on robotics work. Each case is a complete robotics problem built by its
contributor: a simulated world, a robot, an interface for acting and observing, a goal and a success check. An agent
solves the case through that interface alone, and its result is re-derived offline from a sealed recording of every
command it sent.

The benchmark does not prescribe a robot, a simulator or an action space. It fixes only what every case must satisfy
to be fair and verifiable:

- **Isolation.** The agent sees the case's interface and documentation, never the simulator, the scene definition,
  the verifier or the reference solution.
- **Recording.** Every command and the state that followed it are written to a hash-chained, sealed recording.
- **Replay.** A separate verifier without network re-executes the recording in a fresh world and applies the
  author's success check to the replayed states.
- **Calibration.** The reference solution, which works through the same interface, scores 1; doing nothing, and any
  forged recording, scores 0.

## Contributing a case

1. Read the [contributor guidance](GUIDANCE.md) and the [case contract](docs/CASE.md).
2. Start from the complete example [`tools/case-kit/example/gantry-push`](tools/case-kit/example/gantry-push): a
   three-axis gantry pushing a puck in MuJoCo, under 500 lines.
3. Check it locally with `python tools/case-kit/gate.py <task>`.
4. Propose it in *Discussions → Task Proposals*, then open a pull request with the CPU tier from your fork
   ([CONTRIBUTING.md](CONTRIBUTING.md)).

Every case starts with a CPU tier that runs in ordinary Docker containers, and the CPU tier is a complete case on its
own. An optional GPU tier on the bench's GPU pool will open later ([docs/LADDER.md](docs/LADDER.md)).

## Repository

| Path | Content |
|---|---|
| `tasks/<case>/` | Cases |
| `tools/case-kit/` | The robot-independent case kit, the CPU gate and the example case |
| `tools/isaac-remote/` | The GPU-pool runner, scene check and grant logic |
| `ci_checks/` | Static checks run on every task pull request |
| `docs/` | Contract, task format, tiers, commands, assets |
| `.github/` | Review and validation automation |

## Acknowledgements

Robotics-Bench runs on the [Harbor](https://github.com/harbor-framework/harbor) framework. Parts of its review and
validation automation are adapted from [Terminal-Bench-Science](https://github.com/harbor-framework/terminal-bench-science)
under the Apache License 2.0; see [NOTICE](NOTICE).

## License

[Apache License 2.0](LICENSE).
