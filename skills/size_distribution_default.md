# Default Size Distribution strategy

You are an expert SAXS/USAXS scientist fitting a **particle size distribution**
P(r) by inverting I(Q) through pyirena's control-surface tools. Take a single
NXcanSAS HDF5 file, produce a physically sensible size distribution, save it
back to disk, and write a short report. Follow the steps below in order; do not
skip steps.

## When this model applies

A size distribution recovers the volume distribution of scatterer sizes over the
Q-band where their signal sits above the background. This is one of the most
common SAXS/USAXS analyses: **broad size distributions of precipitates in metals,
or pores/voids in rocks, minerals, and solids**, sitting on a low-Q power-law
upturn and a high-Q flat background. These are the *bread-and-butter* case — a
broad distribution is normal and expected.

Do **not** require a single dilute population or a clean Guinier knee. A broad
distribution has no sharp knee and scatters as a smoothly rolling, power-law-like
region; that is exactly what you fit, not a disqualifier. The size-distribution
result is routinely cross-checked against other techniques (porosity, imaging)
and agrees well even though the sphere/spheroid form factor is an idealisation
(real pores are not spheres).

Prefer **Unified Fit** only for genuinely *hierarchical, multi-population*
structure — several clearly distinct Guinier knees at different size scales that
you want to characterise separately. When in doubt for a single broad population,
a size distribution over the recommended Q-range is the right call.

`suggest_sizes_setup` (step 2) recommends the setup and flags suitability; its
`warnings` are advisory context, not a stop sign (see step 2).

## Background

The inversion recovers P(r), the volume-weighted distribution of particle
radii, from the background-subtracted I(Q). See the `size_distribution` skill
for method choices, the contrast ↔ volume-fraction relationship, the size-grid
heuristic, error handling, and how to read the results. Key facts you will use:

- **Inversion methods:** `maxent` (recommended default — smoothest distribution
  consistent with the data), `regularization`, `tnnls`, `montecarlo`. Use
  MaxEnt unless the user asks otherwise.
- **Complex background:** subtracted before inversion as
  `power_law_B·q^(-power_law_P) + flat`. Fit each term over its own Q-window.
- **Inversion Q-range** is the *shared* `set_fit_q_range` tool — it defines the
  Q window the distribution is fitted over (the particle / Guinier-knee region).
- **Size grid** is in radius r [Å]; heuristic `r ≈ π/Q` over the inversion range.

## Workflow

1. **`open_dataset(file_path)`** — remember `session_id`.

2. **`suggest_sizes_setup(session_id)`** — call this BEFORE configuring anything.
   It returns `suitable` (bool), a `recommended` block (`r_min`, `r_max`,
   `inversion_q_min/max`, `power_law_q_min/max`, `background_q_min/max`,
   `flat_background`), and `warnings`. The recommended inversion Q-range is chosen
   where the particle signal is at least ~2× the fitted background, and `r_min`/
   `r_max` are `π/Q` over that range, rounded outward to tidy values.
   - If `suitable` is **true** (the usual case, including broad distributions on a
     power-law + flat background): use the `recommended` values as your starting
     point. A `null` window means that term wasn't needed — skip the
     corresponding fit.
   - If `suitable` is **false**: it means no Q-band with clearly discernible
     particle signal was found. Read the `warnings`. **Do not refuse solely
     because of a "multiple knees" or "several levels" note** — a broad single
     distribution still applies. Only recommend Unified Fit (and stop) when the
     data are genuinely hierarchical with distinct populations the user wants
     separated, or when there is truly no signal above background. Otherwise
     proceed with the recommended range and note the caveat in your report.

3. **`select_sizes_model(session_id, method="maxent")`.**

4. **`set_shape(session_id, shape="sphere", contrast=1.0)`** — use `contrast=1.0`
   unless the user gave a real contrast. With `contrast=1.0` the recovered
   volume fraction is **relative, not absolute** — say so in the report. Use
   `shape="spheroid"` (with `aspect_ratio`) only if the user asks.

5. **`set_size_grid(session_id, r_min=…, r_max=…, n_bins=200, log_spacing=True)`**
   — use the recommended `r_min`/`r_max`. Widen the range a little if you are
   unsure; a too-narrow grid piles the distribution at an edge (see step 9).

6. **`set_error_handling(session_id, error_scale=1.0)`** — start unchanged. If
   the inversion chases noise (jagged P(r)), raise `error_scale`, or switch to
   fractional errors (`fractional_error=True, fractional_error_value=0.03`) when
   the file σ are unreliable.

7. **Complex background — fit each term over its own window, then confirm.**
   The complex background is `power_law_B·q^(-power_law_P) + flat`. Fit it FIRST;
   the inversion works on the background-subtracted data.
   - If `recommended.power_law_q_min/max` is present:
     `fit_power_law_background(session_id, q_min=…, q_max=…)` over that low-Q
     steep-slope window. **Power-law exponent P convention:**
     - **Powders / discrete particles** (e.g. precipitates in a metal matrix
       measured as a powder): fix **P = 4** (Porod) — pass `fit_P=False`.
     - **Solid / bulk materials** (pores in rock, minerals, solids): let P
       **float between 3 and 4** (`fit_P=True`), but never below 3. If a free fit
       returns P < 3, clamp/fix it at 3–4.
     - If unsure, start with P = 4 fixed.
   - If `recommended.background_q_min/max` is present:
     `fit_flat_background(session_id, q_min=…, q_max=…)` over that high-Q flat
     window (run AFTER the power-law fit when both are present). The recommended
     `flat_background` value is a good sanity check for the fitted level.
   - `get_background_preview_image(session_id)` — visually confirm the
     background tracks the data's baseline before inverting. If it is clearly
     wrong, adjust the windows (or `set_background` directly) and re-preview.

8. **`set_fit_q_range(session_id, q_min=…, q_max=…)`** — set the **inversion
   window** to `recommended.inversion_q_min/max`. This is the shared Q-range tool.
   **Choosing the window is the most important decision.** The rule: fit only the
   Q-band where the particle signal is *clearly discernible above the complex
   background* — as a guide, where I(Q) is at least ~2× the background (less is OK
   when the signal is genuinely weak). The recommendation already applies this.
   - Even though the background is subtracted, **do not extend the high-Q end into
     the background-dominated, noisy tail.** There the model cannot describe the
     data and the inversion fits noise, which destabilises Regularization/Monte
     Carlo and distorts P(r) — pull the high-Q end back to where signal is real.
   - Likewise keep the low-Q end above the steep power-law upturn.
   - After setting the inversion window, if you changed it substantially, update
     the size grid too — either reuse `recommended.r_min/r_max` or derive
     `r ≈ π/Q` over the new window (round outward so the grid brackets the range).

9. **`run_sizes_fit(session_id, random_seed=42)`** — returns `success`,
   `chi_squared`, `volume_fraction`, `rg`, `peak_r`, `n_iterations`.

10. **`get_sizes_fit_image(session_id)`** — inspect the two panels:
    - *Top (I vs Q):* does the model + background track the data across the
      inversion range?
    - *Bottom (P(r)):* is the distribution a clean, physically plausible peak
      that is **not** piled against `r_min` or `r_max`?
    - **Grid-edge check:** if `peak_r` sits at (or within ~10% of) `r_min` or
      `r_max`, the grid is too narrow — widen it with `set_size_grid` and refit
      (back to step 9). Repeat until the peak is well inside the grid.

11. **Pre-save image — required.** Call `get_sizes_fit_image(session_id)` once
    more so the GUI shows the final fit, not a stale intermediate.

12. **`save_sizes_fit(session_id)`** — writes the distribution back to the file.

13. **Report** (final user-facing message): method, shape, contrast (state
    whether the volume fraction is absolute or relative), `volume_fraction`,
    `rg`, `peak_r`, the size range covered, `chi_squared`, and a one-line
    quality note from the image.

## Judging fit quality

The same σ caveat as Unified Fit applies: reported uncertainties in SAXS are
often mis-scaled, so **do not chase a particular reduced χ²**. Judge the fit
primarily from `get_sizes_fit_image`:
- Model tracks the data through the inversion window, AND
- P(r) is a single (or few) physically plausible peak(s), smooth, not piled at a
  grid edge, not a forest of noise spikes.

If P(r) is jagged → increase `error_scale` (or use fractional errors) and refit.
If P(r) is piled at an edge → widen the grid and refit.
If the model misses the data shape badly even with a good background → first
re-check the inversion Q-range (are you including background-dominated high-Q?)
and the background fit. Only if the shape still cannot be matched — and the misfit
looks like distinct hierarchical populations — consider Unified Fit.

## Operational rules

- **`suggest_sizes_setup` first, always.** Do not configure the model before you
  have its recommendations and suitability verdict.
- **`random_seed=42` on every `run_fit` / `run_sizes_fit`.**
- **Call `get_sizes_fit_image` after every `run_sizes_fit`.** The user follows
  progress through the GUI plot.
- **Don't run the same fit twice unchanged.** Between `run_sizes_fit` calls,
  change at least one of: grid, method, error handling, background, or inversion
  Q-range.
- If a tool returns `{"error", "code", "suggestion"}`, read the suggestion and
  adjust — do not retry blindly.

## Stopping criteria

Stop when **any** hold:
- The image shows the model tracking the data and a plausible, edge-free P(r) →
  proceed to save.
- ≥ 6 fits with no meaningful improvement in the image/P(r).
- `suggest_sizes_setup` returned `suitable=false` **and** the reason is genuinely
  no signal above background or clearly distinct hierarchical populations the user
  wants separated → recommend Unified Fit and stop. (A "multiple knees / several
  levels" note alone is **not** a reason to stop on a broad single population.)
- Tool error (file missing, empty Q range) — surface it.
