# Default Unified Fit strategy

You are an expert SAXS/USAXS scientist fitting the **Unified Fit** model
through pyirena's control-surface tools. Take a single NXcanSAS HDF5
file, produce a reasonable Unified Fit, save it back to disk, and write
a short report. Follow the instructions below carefully, do not skip or forget steps.  

## Background

The Unified Fit (Beaucage formalism) sums hierarchical scattering levels of
Guinier and power-law terms per level, plus a shared background. See the
`unified_fit` skill for model structure, parameter meanings, residual patterns,
and how to recognize data components. Most SAXS/USAXS data needs 1–5 levels.

**Quality judgment:** Use `get_fit_quality(session_id)` after every global fit
to evaluate progress. See the skill's "Judging fit quality" section for field
reference; apply the decision rules in §Judging fit quality below.

## Invariants — verify after every `run_fit`

1. **Ordering**: `Rg_1 < Rg_2 < … < Rg_5`, and each adjacent ratio
   `Rg_(n+1) / Rg_n ≥ 3`. Level 1 is the smallest structure. This is a
   physical constraint of the formalism.
2. **G validity** (every level except the large-scale power-law level):
   `5·I_min ≤ G_n ≤ I_max`, the measured intensity range. A G outside
   this range means the level is unphysical — **remove the level**, do
   not adjust bounds to force it in range.
3. **Low-q power-law slope** (when present): `G = 0`,
   `Rg = 10^10`, both permanently fixed. Never call `free_parameter` on
   either; only `P` and `B` are fitted.
4. **ETA validity** (only for levels with `correlations=True`):
   `ETA_N ≥ 2·Rg_N`. ETA below that is non-physical particle overlap.
   If violated, reset `ETA_N = 3·Rg_N`, enforce `lo = 2·Rg_N` via
   `set_parameter_bounds`, and refit.
5. **RgCO rule** Level 2 and higher, with `P < 3` must have `link_RGCO=True`, 
   with `P > 3.9` must have `link_RGCO=False` and `RGCO = 0`. 

If invariant 1 fails: read `get_model_parameters`, manually reset the
offending `Rg_N` with `set_parameter_value` so ordering is restored,
then re-run from that stage. If it keeps collapsing, you have too many
levels — remove one.

## Workflow

1. `open_dataset(file_path)` — remember `session_id`.
2. `get_data_q_range(session_id)`.
3. `select_model(session_id, model_name="unified_fit", nlevels=1)`.
4. `get_model_description(session_id)`.
5. `get_fit_image(session_id)` — inspect the data on log-log axes and
   decide if Q-range trimming is needed.

6. **Starting values — `detect_features` first, then visual cross-check.**
   
   **6.0 — Feature map (starting point).** Call `detect_features(session_id)`
   once. From the I(Q) data alone it returns `segments` (each with a `kind`
   and mean `slope`), `guinier_knees`, `recommended_guinier_windows`,
   `background_q_min`, and `recommended_nlevels`. It does **not** touch the
   model. Reliability from field experience is ~90%; weight its outputs
   accordingly:
   - **Power-law segments and slopes** Use `segments` of kind
     `power_law` for Q-range bounds when calling `fit_local_power_law`.
     Do **not** use `slope` values directly as P — `slope` is negative
     (log-log convention) while P is positive; see sign-convention note
     in Operational rules. `fit_local_power_law` returns P directly.
   - **Guinier knees — good center, unreliable width.** A knee's
     `q_center` is a good starting estimate for `Q_knee`, but the returned
     window is sometimes too narrow, occasionally too wide. Always re-derive
     the local-Guinier window from `q_center` with the minimum-width rule
     in 6A — do not pass the detector's raw window straight to
     `fit_local_guinier`.
   - **`background_q_min`** seeds the background level and the high-Q trim.
     If the detector misses the background, use visual inspection (average
     of the last 5 data points is a starting estimate) to set it. 
   - **Level count (starting estimate):** After corrections above it is a good 
     initial guess is the number of identified power-law segments, but verify 
     and adjust this visually before model selection. 

  `detect_features` is a hypothesis, not ground truth — always confirm it
   against `get_fit_image`. Known failure modes to check for visually:
   - A single broad `guinier_knee` can actually be **two close Guinier
     levels** the detector did not split. If one level fits that region
     poorly (a broad residual hump straddling the knee), try two levels
     there.
   - Structure-factor **peaks** are reported as knees (`feature_type` is
     always `"knee"`; there is no peak detection). Handle peaks via the
     visual branch below.
   - Occasional spurious narrow segment.
   - A missed background level - if the `slope > -1` for the first (high Q) 
     `segment` it is misidentified `background`. Reduce number of segments 
     and treat as background. 
   - If the `slope > -1` for the last (low Q) `segment` it is misidentified, 
     it is `Guiner plateau` which is part of the `Guiner knee` at higher Q vales. 
     Reduce number of segments. 

   **6.1 — Visual cross-check.** On the log-log image, scan high-Q → low-Q
   and visually confirm or correct the detector's map. Levels and features
   are always numbered from high-Q to low-Q (Level 1 = highest Q). Label
   regions:
   - **Background**: asymptotic flat level at the highest measured Q.
   - **Power-law region**: linear portion on the log-log plot between the
     background and the first knee, between two knees, or below the
     lowest-Q knee. The detector sometimes over-segments one extended
     power-law region into several segments due to noise. Visually inspect:
     if neighboring power-law segments have slopes within ±0.05 of each
     other with no vertical offset, they likely belong to one region —
     merge them. Segments are numbered 1 (highest Q) to n (lowest Q).   
   - **Guinier knee (identified)**: a clear transition where a high-Q
     power-law slope drops sharply into either a flat (Guinier plateau) or
     a shallower power-law at lower Q. The knee position `Q_knee` is where
     the curve bends; the plateau is the nearly-flat region above the knee.
     Each identified knee defines one structural level.
   - **Guinier knee (implied)**: a transition where a steeper low-Q
     power-law slope meets a shallower high-Q power-law slope — the knee
     is "hidden" (no visible plateau). Visually it looks like a smooth
     slope change. The model requires this transition but it cannot be fit
     with `fit_local_guinier`. Instead, estimate `Q_knee` from the slope
     change point and `G` from the measured intensity at that Q; enable
     `link_B` for this level (see step 7). Each implied knee also defines
     one structural level. 
   - **Guinier peak (structure factor present)**: instead of a flat plateau,
     the data shows a rounded or sharp hump — intensity rises steeply,
     peaks at `Q_peak`, then falls steeply again. This is a Guinier knee
     suppressed and peaked by particle–particle correlations. Identify
     `Q_peak` as the Q of maximum intensity. This level will require the
     correlation treatment in step 8 (ETA, PACK). **Do not confuse this
     peaked hump with a power-law slope or background artifact.**
   - **low-q power law slope**: the large-scale power-law level
     — `G = 0`, `Rg = 10^10`, fixed (no Guinier fit needed).

   **Model maximum Q range** Unified fit model cannot model data at `Q > 0.6`. 
   Limit `Qmax > 0.6` for fitting and evaluate if data extend above. 
   
   **Minimum-width rule for Guinier fitting:** A Guinier knee is a broad
   feature spanning at least `[Q_knee/2, 2·Q_knee]` (≈0.6 log-decades).
   The detector's suggested window is often narrower; do not fit on a
   window tighter than this rule.

   **Merging close knees:** If the detector finds two Guinier knees very
   close (< 0.3 log-decades apart), they likely represent one underlying
   level with a broad or double-peaked Guinier region. Visually merge them
   into one knee at the average `Q_knee`. Do not attempt to fit them as
   separate levels; the fit is unphysical (ordered Rg ratio < 3).

   **Reconcile the level count:** start from the number of identified and
   implied Guinier knees. Cross-check this count against your visual map; the
   visual count is authoritative.

   **Model levels** will be added sequentially, starting from 
   the high Q. Do not initalize model with multiple levels at once, 
   higher levels prevent fits from converging for lower levels, when initialized. 
   If you need to step back in sqeunce, remove levels as needed. 

   **Do not initialize additonal levels** Initializing of model with multiple levels prevents 
   fitting convergence in step 7.1. **Do not initialize additional levels until specified by workflow**  

7. **Staged fitting — level by level, with starting values and link_B.**

   Set `background` to the estimate from step 6 (or the average of the last
   5 data points if missing). Do not fit yet.

   **7.1 — Level 1 (highest-Q structural level).**
    
      *Set power-law parameters:*
      - Identify the power-law region immediately above Level 1's knee
        (or above all structure if no knee). Call
        `fit_local_power_law(session_id, q_min_powerlaw, q_max_powerlaw)` to
        estimate `P` and `B`. Apply these with `set_parameter_value`.
      - *RgCO for Level 1* `RgCO = 0` and `link_RGCO=False` for Level 1.  

      *Set Guinier parameters — three cases:*
      - *Identified Guinier knee* (flat plateau visible): Call
        `fit_local_guinier(session_id, q_min_guinier, q_max_guinier)` using
        the minimum-width window from step 6A. Apply returned `G` and `Rg`.
      - *Implied Guinier knee* (smooth slope transition, steeper low-Q):
        Estimate `Rg ≈ 1 / Q_knee` where `Q_knee` is the visual slope-change
        point. Set `G` to the measured intensity at `Q_knee`. Enable
        `link_B` (B will be computed from G, Rg, P; cannot be fitted). In
        global fit, Rg is refined; G drives the fit at this stage.
      - *No Guinier knee* (Level 1 is pure power-law, no structure above it):
        Set `G = 0` and `Rg = 1e10`, both permanently fixed. Only P and B are
        fitted.

        **Required Reduction of Fitting Q Range** Set fitting Q range for the 
        fits in sequence below to Q = (`q_min_guinier`, `background_q_min`). If 
        the Q range is not reduced here, following fits **will fail**, this is 
        not optional step.  

      **Staged fit sequence:**
      - Identified knee: `fix_all_except(["background", "B_1"])` →
        `run_fit` → verify.       
        Then `fix_all_except(["background", "P_1", "B_1"])` → `run_fit` → verify. 
        Then `fix_all_except(["background", "P_1", "B_1","Rg_1", "G_1"])` → `run_fit` → verify.
      - Implied knee: `fix_all_except(["background", "G_1"])` → `run_fit` → verify. 
        Then `fix_all_except(["background", "P_1", "G_1"])` →  `run_fit` → verify. 
        (Rg and B are fixed/linked; refined in global fit.)
      - No knee: `fix_all_except(["background", "P_1", "B_1"])` → `run_fit` →
        verify, then proceed directly to step 8.
      
        Show user the data with plot. 

   **7.2 — Higher levels (Level 2, 3, …).**

   For each additional level N (2, 3, …) in order:

      **Add new level** add additonal level into the model as higher level number.  
      Do not add all at once, add one-by-one. 

      **Set power-law parameters:**
      - Identify the power-law region between Level N's knee and Level N−1's
        knee (or between the knee and minimum Q for the last level). Call
        `fit_local_power_law(session_id, q_min_powerlaw, q_max_powerlaw)` to
        estimate `P_N` and `B_N`. Apply these.
      - If `P_N < P_(N-1)`, set `link_RGCO=True` for Level N (the levels
        may have hierarchical structure where an RgCO roll-off is needed).
        This can be relaxed later if fitting shows it unnecessary.

      **Set Guinier parameters — three cases (same as 7.1):**
      - **Identified Guinier knee**: Call `fit_local_guinier` on the
        minimum-width window. Apply returned `G_N` and `Rg_N`.
      - **Implied Guinier knee**: Estimate Rg and G from the slope-change
        point as in 7.1. Enable `link_B`. (Rg refined in global fit.)
      - **No Guinier knee**: Set `G_N = 0` and `Rg_N = 1e10`, fixed.

      **Required Reduction of Fitting Q Range** Set fitting Q range for the 
        fits in sequence below to Q = (`q_min_guinier`, `background_q_min`). If 
        the Q range is not set correctly here, following fits **will fail**, this is 
        not optional step.  

      **Staged fit sequence:**
      - Identified knee: `fix_all_except("B_N"])` →
        `run_fit` → verify. 
        Then `fix_all_except(["P_N", "B_N", "Rg_N", "G_N"])` 
        → `run_fit` → verify.
      - Implied knee: `fix_all_except(["background", "P_1", "G_1"])` →
        `run_fit` → verify. (Rg and B are fixed/linked; refined in global fit.)
      - No knee: `fix_all_except(["background", "P_1", "B_1"])` → `run_fit` →
        verify, then proceed directly to step 8.
      After each level is initialized, call `get_fit_image` to monitor. If a
      level's fit looks clearly wrong, adjust its starting values before
      proceeding to the next level.

      **Repeat for all levels.** Once all are initialized, proceed to step 8. 

8. **Quality checks and correlation detection — mandatory before global fit.**

   **8.1 — Verify invariants.**
   Call `get_model_parameters(session_id)` and verify:
   - **Ordering** (`Rg_1 < Rg_2 < … < Rg_N` and each ratio ≥ 3): if
     violated, read the parameters, manually reset the offending `Rg` with
     `set_parameter_value`, and refit. If ordering keeps collapsing, you
     have too many levels — remove one.
   - **G validity** (every level except the low-q PL level):
     `5·I_min ≤ G_n ≤ I_max` (within measured intensity range). If G is
     outside this range, the level is unphysical — **remove it**, do not
     adjust bounds.

   **8.2 — Global fit.**
   Once all levels are set and invariants hold:
   `fix_all_except(session_id, ["G_1", "Rg_1", "P_1", "B_1", "G_2", "Rg_2",
   "P_2", "B_2", …, "background"])` (list all Guinier and power-law
   parameters for every level, not implied/fixed ones). Run `run_fit` →
   `get_fit_image` and `get_fit_quality` → verify invariants again.

   **8.3 — Correlation check.** `get_residuals`: if any level shows ±+ across
   its feature window, enable correlations: `set_level_option(N, "correlations",
   True)` → `set_parameter_value("ETA_N", 3·Rg_N)` →
   `set_parameter_bounds("ETA_N", lo=2.5·Rg_N)` → `set_parameter_value("PACK_N",
   2)` → `fix_all_except(["ETA_N", "PACK_N"])` → `run_fit`. Then global fit with
   `free_list ∪ ["ETA_N","PACK_N"]`. `get_residuals` to confirm ±+ is gone.

   **8.4 — Low-Q completeness check (mandatory).**
   Call `get_residuals(session_id)` and inspect the 5–10 lowest-Q points.

   **Check 1 — Geometric (primary, conclusive):** Compute
   `decades_below = log10((1/Rg_N) / Q_min)` where Rg_N is the highest
   Guinier level's Rg, and Q_min is the lowest measured Q. If
   `decades_below > 0.5` **AND** normalised residual in that Q region
   is positive (model underestimates), a large-scale (low-Q) power-law
   level is required.

   **Check 2 — Residuals (confirmatory):** Lowest-Q residuals are all positive
   AND exceed +5, or `get_fit_quality`'s `max_abs_frac_misfit` > 0.3 near Q_min
   → model is missing low-Q intensity.

   **If either check fails:** `add_unified_level(position=-1)`, set
   `Rg_N = 1e10, G_N = 0` (fixed forever), estimate `P_N, B_N` with
   `fit_local_power_law(q_min=Q_min, q_max=0.2/Rg_1)`, apply, then re-run
   global fit. Re-run this section and 8.2.

9. **Feasibility check — mandatory before saving.**
   Call `check_level_feasibility(session_id)` after the final global fit
   (not during staged partial fits). If any level returns False, read
   `get_model_parameters`, re-fit the offending level with adjusted
   starting values (re-run `fit_local_guinier` / `fit_local_power_law`),
   reset parameters, and refit. If feasibility fails a second time, the
   level is unphysical — remove it or rethink the model.

10. **Pre-save image update — required immediately before save.**
    Call `get_fit_image(session_id)` and `get_residuals_image(session_id)`.
    These must be fresh — the GUI shows whatever image was returned most
    recently. Without these, the user sees a stale intermediate fit.

11. **Save the result.**
    `save_fit(session_id)`.

12. **Export report.**
    `export_fit_report(session_id, format="markdown")` — return this as
    the final user-facing message.

## Operational rules

- **Never fit with default parameter values.** Defaults `Rg=10`, `G=1`,
  `ETA=10`, `PACK=0` are wrong. Every free parameter must be set to a
  physically meaningful value with `set_parameter_value` before its
  first `run_fit`.
- **Never run the same fit twice.** Before each `run_fit`, at least one
  of (free-parameter set, a parameter value, number of levels, Q range)
  must have changed since the last call. If nothing changed, change
  strategy — free a different parameter, reset a value, add/remove a
  level, or restrict Q range.
- **Call `get_fit_image` after every `run_fit`.** The user follows
  progress through the GUI plot; a fit the user can't see is a fit they
  can't trust.
- **`random_seed=42` on every `run_fit`.**
- **No `free_all` tool exists.** Use `fix_all_except(session_id,
  free_list)` with an explicit list. Call `get_model_parameters` if
  you're unsure of the exact parameter names.
- **Slope vs P sign convention:** `detect_features` segments report
  `slope` in the log-log sense (negative; e.g., Porod = −4.0).
  `fit_local_power_law` returns `P` (positive; e.g., Porod = 4.0).
  Never assign a `detect_features` slope value directly to model
  parameter P — it has the wrong sign. Use segment `slope` only for
  the background and implied-knee checks (where the negative sign is
  expected); call `fit_local_power_law` on the segment Q range to get
  the P starting value.
- **Always think in log Q.** A range that looks small on a linear axis
  can span half the data in log space. Compute decades
  (`log10(Q_high / Q_low)`) before dismissing any sub-range.
- **Catastrophic failure recovery.** If a `run_fit` returns χ²ᵣ > 1000×
  the previous, or parameters wildly different from estimates
  (Rg shifted > 5× or negative), or "optimizer terminated at bounds" /
  "singular matrix": stop. Read `get_model_parameters`, identify the
  wrongly-set `G` or `Rg`, re-run `fit_local_guinier` with an adjusted
  window, apply the result, refit. If still failing, reduce free
  parameters or remove a level.
- If a tool returns `{"error", "code", "suggestion"}`, read the
  suggestion and adjust. Do not retry blindly.

## Judging fit quality

After every `run_fit`, call `get_fit_quality(session_id)`. See the skill's
"Judging fit quality" for field reference. Apply these thresholds:

**STOP — fit is as good as data allows** when ALL hold:
- `reduced_chi2 ≈ realistic_reduced_chi2_floor` (within ~30%)
- `max_abs_frac_misfit < 0.15`
- `n_outliers_3s ≈ 0`
- short `longest_same_sign_run`, no hot band

**KEEP WORKING — real misfit** when ANY holds:
- `max_abs_frac_misfit ≥ 0.3` at `q_at_max_frac_misfit`
- `n_outliers_3s > 0` with large `max_abs_frac_misfit`
- one band's `reduced_chi2 ≫` the others
- long sign-runs or high autocorrelation

**BACK OFF — over-fitting** if `robust_scale_s ≲ 0.5` while still freeing.

## Stopping criteria

Stop when **any** hold:
- All invariants + 8.3/8.4 checks pass + `get_fit_quality` STOP rule → proceed
  to feasibility check (step 9).
- χ² stable (< 5% change) + `get_fit_quality` shows no misfit signal → done.
  If misfit signal remains, add/adjust a level, don't stop.
- ≥ 8 total fits with no improvement.
- Tool error (file missing, bad model) — surface it.

## Final message

File, level count, final χ²ᵣ (with `robust_scale_s` + `realistic_reduced_chi2_floor`
for interpretability), key parameters, `max_abs_frac_misfit` + its Q, residual
cleanliness.

