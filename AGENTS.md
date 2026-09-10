# Repository scope

This repository only runs evaluator-only Agent tests. Do not add SFT, RL,
teacher-rollout, private-gold judge, or training-data export workflows.

- Preserve raw tool history and all attempt traces.
- Never expose private gold to the Agent.
- Require `OCR_BACKEND=baidu`; local OCR and OCR fallback are prohibited.
- Keep datasets, images, traces, and credentials outside the Git checkout.
- Run server processes with `OMP_NUM_THREADS=1`.
- Modify code locally, commit it, then fast-forward server checkouts.
