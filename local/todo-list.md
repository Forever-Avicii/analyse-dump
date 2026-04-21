# AI Agent Implementation Todo

## Week 1: Lightweight Closed Loop

- [x] 1. Tool protocol unification (`ok/data/error/metrics`) and unified executor
- [x] 2. Agent loop runnable end-to-end
- [x] 3. `analyze-agent` CLI command available
- [x] 4. Baseline scenarios automated in tests

## Week 2: Planner + Replan

- [x] 1. Explicit planner generates step plan
- [x] 2. Replan when tool output conflicts or fails
- [x] 3. Budget control (`max_steps`, `max_seconds`)

## Week 3: Memory + Verifier

- [x] 1. Session memory to avoid repeated exploration
- [ ] 2. Case memory persistence in SQLite
- [ ] 3. Verifier marks conclusions as `confirmed` vs `hypothesis`

## Notes

- Real-world data validation is intentionally excluded per request.
