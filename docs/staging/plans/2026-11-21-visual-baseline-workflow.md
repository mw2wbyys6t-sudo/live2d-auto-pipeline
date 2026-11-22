---
intent: Establish a reproducible, license-gated PNG and PSD visual baseline with explicit Live2D motion acceptance.
success_criteria: Synthetic fixtures validate; real samples without evidence are BLOCKED; PNG/PSD targets and first-pass motion scope are explicit; focused tests pass.
risk_level: medium
auto_approve: true
branch: feat/forearm-elbow
worktree: false
dirty_worktree: allow
---

## Steps

- [ ] **Step 1: Confirm scope and baseline policy**
action: Update `docs/staging/plans/2026-11-21-vision-to-live2d-solution.md` to state PNG is the visual segmentation/reconstruction baseline and PSD is the layer-structure roundtrip baseline; record license evidence requirements and the first-pass motion list: head turn, blink/mouth, arm/elbow, walk, with trunk and hair/clothing secondary motion as extended stress cases.
loop: false
verify:
  type: shell
  command: Select-String -Path docs/staging/plans/2026-11-21-vision-to-live2d-solution.md -Pattern '首阶段同时纳入 PNG 与 PSD'

- [ ] **Step 2: Lock baseline contract with tests**
action: Add `tests/unit/test_visual_baseline.py` cases for valid synthetic PNG, PSD structure requirements, and real sample license evidence missing; verify real samples without verified evidence are BLOCKED, not counted as pass.
loop: false
verify: python -m pytest tests/unit/test_visual_baseline.py -q

- [ ] **Step 3: Implement manifest validator and report**
action: Add `tools/visual_baseline.py` to validate schema version, unique sample IDs, confined sample/evidence paths, SHA-256, PNG targets (`segmentation`, `pixel_provenance`, `reconstruction`, `alpha_integrity`), PSD structure targets (`group_path`, `layer_name`, `canvas_offset`, `draw_order`, `visibility`), action scope, and annotation version; emit JSON with PASS/FAIL/BLOCKED and ensure missing authorization evidence takes precedence as BLOCKED.
loop: until python -m pytest tests/unit/test_visual_baseline.py -q passes
max_iterations: 3
verify: python -m pytest tests/unit/test_visual_baseline.py -q

- [ ] **Step 4: Add non-sensitive synthetic fixture**
action: Add `tests/unit/fixtures/visual_baseline/dataset.json` and its text-only structure fixture; compute and store the fixture SHA-256. Do not add any existing `output/` or `Work/` image unless its owner and license are confirmed.
loop: false
verify:
  - type: shell
    command: python tools/visual_baseline.py tests/unit/fixtures/visual_baseline/dataset.json
  - type: artifact
    path: tests/unit/fixtures/visual_baseline
    assert:
      kind: exists

- [ ] **Step 5: Run focused regression checks**
action: Run the baseline tests together with PSD structure/roundtrip, layer provenance, rig visual gate, and deformation stress gate unit tests; inspect the final diff and ensure unrelated user changes remain untouched.
loop: false
verify: python -m pytest tests/unit/test_visual_baseline.py tests/unit/test_psd_structure.py tests/unit/test_psd_validator.py tests/unit/test_layer_provenance.py tests/unit/test_rig_visual_gate.py tests/unit/test_deform_stress_gate.py -q
