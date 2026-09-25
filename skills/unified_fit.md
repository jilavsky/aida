# Unified Fit — Parameter Reference and Residual Guide

Reference for small-angle scattering data components and the Beaucage
Unified Fit model. The fitting workflow, ordering invariant, validity
rules, and mandatory checks are defined in the strategy file — this file
covers parameter meanings, residual shape interpretation, and level-option
semantics.

## Small-angle scattering data components

Small-angle data has up to three components: *background*,
*low-Q power-law slope*, and *small-angle scattering* modelled by the
Unified Fit. Most, but not all, data contain all three. Always identify
whether a background and a low-Q power-law slope are present before
interpreting additional scattering above those baselines.

## Model structure

The Unified Fit (Beaucage, 1995/1996) sums structural levels from
smallest to largest. Level 1 is the smallest structure (highest-Q knee,
smallest Rg). Each level contributes a Guinier term (G, Rg) and a
power-law term (B, P), plus an optional high-Q roll-off (RgCO) and an
optional correlation correction (ETA, PACK).

Each Unified Fit level may have three distict observable features:
- `Guinier plateau` is typically flat or close to flat (P < 1) area from minimum 
   Q measured to Guinier knee. Often obstructed by scattering from larger features. Does 
   not require additional model, Guinier formula implies this.  
- `Guinier knee`is where intensity smoothly transitions from flat (Guinier plateau) to
   power law slope.   
- `Power law slope` is high power law drop in intensity above the Guinier knee. 

The highest-numbered level often represents structures too large for their
Guinier region to fall within the measured Q range — by convention this
level has G = 0 and a very large fixed Rg, leaving only the power-law
contribution.

## Locating features with `detect_features`

`detect_features(session_id)` segments the I(Q) curve in log-log space and is the preferred starting point for finding knees and fitting windows. Its power-law slopes/ranges are reliable; its Guinier-knee *centers* are good estimates but the suggested *widths* are often too narrow, so re-derive the local-Guinier window from the knee center (minimum `[Q_knee/2, 2·Q_knee]`). If the two Guinier levels are too close (Rg,n+1​/Rg,n​ < 3), remove the power-law segment in between and replace with one Guinier level centered on average of the two `Q_knee`.   
Any knee it reports already satisfies the steep→shallow physical constraint
(`|slope_low_q| < |slope_high_q|`). It is ~90% reliable — confirm visually:
it can miss the background level, lump two close levels into one broad knee,
and report structure-factor peaks as knees. It also does **not** find
*implied* Guinier levels (a steeper low-Q power-law meeting a shallower
high-Q one, with no fittable plateau) — those are estimated from the slopes
with `link_B`; see the strategy's implied-Guinier-levels note.

## Parameter meanings

**Rg** — radius of gyration in Å. Identify the Guinier region by finding
a **near-flat (slope ≈ 0) plateau** on the log-log plot. The Guinier
knee is the HIGH-Q boundary of that plateau — where the curve transitions
into steeper power-law decay. `Rg ≈ 1/Q_knee` is a rough estimate for
choosing a fitting window. **A slope change from steep to less steep is
not a knee** — it is a larger structure's power-law blending into the
next level; the genuine plateau (slope ≈ 0) lies at lower Q and is obstructed. 
Use `fit_local_guinier(session_id, q_min, q_max)` on a window bracketing
the flat region to obtain a reliable Rg.

**G** — Guinier prefactor (cm⁻¹). Sets the level's intensity at Q → 0.
Obtain from `fit_local_guinier` together with Rg — do not read off the
plot. By convention the large-scale power-law level has G = 0.

**P** — power-law exponent. Always positive (1–4 range). The intensity
drops as I ∝ Q^(−P), so the log-log slope equals −P. Physical
interpretation:
- P = 4: smooth surface (Porod)
- 3 < P < 4: rough surface or broad size distribution
- P = 3: mass fractal surface or collapsed polymer
- P < 3: mass fractal interior, polymer chains, or size distribution

`fit_local_power_law` returns P directly (positive). `detect_features`
segment `slope` fields are negative (slope = −P) — do not assign them
to model parameter P without negating.

Obtain a starting value from `fit_local_power_law` on the linear
power-law region of the log-log plot (between knees, or above the
highest-Q knee). If no clean power-law region is visible, start at P = 4.

**B** — power-law prefactor. Obtain from `fit_local_power_law` together
with P. B and P are correlated — if B hits its lower bound, P drifts to
compensate. For smooth surfaces (P ≈ 4) B is tied to G and Rg through
the Porod relation; enabling `link_B` (see level options) has the model
compute B automatically instead of fitting it freely.

**RgCO** (RgCutoff) — high-Q exponential roll-off. Defaults to 0 (off)
and should remain so unless the data shows a true hierarchical structure
(fractal aggregate, or elongated/disc-like particle with two principal
dimensions). When needed, RgCO_N is tied to Rg_(N−1) — the cleanest way
to do this is `link_RGCO=True`. Never fit RgCO as a free parameter.

**ETA** — correlation distance in Å. Approximately the nearest-neighbour
centre-to-centre distance between particles. As a soft physical floor,
ETA should not be much smaller than ~2·Rg for near-spherical particles
(they would overlap); for very anisotropic shapes a smaller ETA can still
be physical. Default ETA = 10 Å is wrong for any level with Rg ≳ 50 Å.

**PACK** — packing factor (0–8). Strength of liquid-like ordering.
Default 0 disables the correction entirely.

**background** — flat incoherent background. A good starting estimate is
the average intensity of the last 3–5 data points at the highest Q.

ETA and PACK have **no effect** unless the level's `correlations` flag is
True (see level options below). Without that flag, changes to ETA or PACK
values are silently ignored by the intensity calculation.

## Level options (boolean flags)

Set with `set_level_option(session_id, level, option, enabled)`.

**correlations** — Enables the Born-Green liquid-like ordering correction.
ETA and PACK are inactive until this is True. **Must be set to True before
fitting ETA or PACK.** Default False.

**link_B** — Computes B from G, Rg, and P via the Porod invariant instead
of treating B as a free parameter. With `link_B=True`, B cannot be
fitted; this removes one degree of freedom and stabilises early staged fits.

**link_RGCO** — Keeps level N's RgCO synchronised to the current Rg of
level N−1 automatically. Enable only for genuinely coupled levels
(primary particle + fractal aggregate, or two principal dimensions of a
non-spherical particle). Do not enable for independent structural levels.

**mass_fractal** — Not used in this workflow; ignore.

## Residual pattern recognition

Define each level's feature window as roughly Q_knee/2 to 2·Q_knee,
i.e. about 1/(2·Rg) to 2/Rg. Count zero-crossings of the normalised
residuals inside that window:

- **> 5 zero-crossings, no recognisable shape** — random scatter,
  acceptable.
- **≤ 3 zero-crossings with a recognisable shape** — systematic misfit
  (see patterns below).

Common systematic shapes:

- **+-+ across the knee** (negative low-Q side, positive at the peak,
  negative high-Q side, or reversed): particle–particle correlations are
  missing — call `set_level_option(session_id, N, "correlations", True)`,
  then fit ETA and PACK for that level. Residuals of ±10–25 with this
  shape are misfit, not noise.
- **Monotone slope across the feature**: P is wrong; free P.
- **Single broad hump**: Rg is wrong, or a level is missing.
- **S-shape (negative then positive with increasing Q)**: Rg too large.
- **Inverted S-shape**: Rg too small.
- **Systematic rise at very low Q**: large-scale power-law level missing —
  see the Low-Q power-law slope section below.
- **High residuals only at high Q**: background too low, or the upper Q
  range needs restricting.
- **High-frequency noise everywhere**: data uncertainties are
  under-estimated (common in SAXS/USAXS reduction). Acceptable provided
  no low-frequency systematic pattern is present.

## Judging fit quality — `get_fit_quality`

`get_fit_quality(session_id)` is the preferred quality judge. Reported σ in
SAXS/USAXS are routinely mis-estimated, so the absolute value of `reduced_chi2`
(and `get_chi_squared`) is not a reliable target — chasing χ²ᵣ ≈ 1 wastes
effort, and blindly dismissing a high χ²ᵣ hides real misfits. `get_fit_quality`
returns σ-scale-independent diagnostics that resolve both. It reports facts
only; the decision thresholds live in the strategy.

Fields and what they mean:

- **`robust_scale_s`** — MAD-based estimate of how many times the actual
  point-to-point scatter exceeds the reported σ. s ≈ 1: σ honest. s ≈ 3:
  σ ~3× too small, so a `reduced_chi2` of ~9 is the *best achievable* — this
  is **`realistic_reduced_chi2_floor`** (= s²). When `reduced_chi2 ≈
  realistic_reduced_chi2_floor`, the χ² is fully explained by mis-scaled σ,
  not by misfit — do not try to push it lower.
- **`max_abs_frac_misfit`** (+ **`q_at_max_frac_misfit`**) — largest
  |(I−M)/I|, completely independent of σ. The gross-misfit backstop: 0.3 means
  the model is 30% off the data at that Q — a real local misfit no matter how
  unreliable σ is. (This is why a normalised residual of 20–50 is never
  automatically "fine": with σ at a few % it implies the model is ~100% off,
  i.e. M ≈ 0.)
- **`n_outliers_3s`** / **`frac_outliers_3s`** — points beyond
  3·robust_scale_s, i.e. genuine outliers *after* accounting for a mis-scaled
  σ. A handful among otherwise-tight residuals = localized misfit, not noise.
- **`longest_same_sign_run`** / **`sign_autocorr_lag1`** — structure in the
  residual sequence. Long runs / high autocorrelation signal a wrong
  functional form (systematic), distinct from a pure σ-scale problem, even
  when magnitudes are modest.
- **`bands`** — the same metrics per Q-decade. One band's `reduced_chi2` far
  above the others localizes the misfit in Q; uneven bands are themselves a
  misfit signal.

`get_residuals` complements this: besides the normalised residual it also
returns `rescaled_residual` (r / robust_scale_s — scatter vs. the data's own
noise floor) and `frac_misfit_percent` ((I−M)/I in %). Use the residual
*shape* patterns above for diagnosis and `get_fit_quality` scalars for the
stop/continue decision.

## Low-Q power-law slope

When the log-log plot shows a straight line at low Q with no flattening
plateau or visible knee, the Guinier region lies below Q_min. This feature
is too large for the measured Q range.

**Identification:** Intensity is linear on a log-log plot at low Q,
indicating the structural feature has Rg > 1/Q_min.

**Modelling action:** Use `fit_local_power_law` on the linear low-Q region
to obtain starting values for P and B. Then configure the level:
- Set G = 0 (Guinier plateau not visible; never fit G for this level)
- Set Rg = 10¹⁰ (by convention; never fit Rg for this level)
- Fit only P and B

**Physical interpretation of P:**
- P ≈ 4: smooth interfaces (Porod scattering)
- 3 < P < 4: surface fractals
- P < 3: mass fractals
