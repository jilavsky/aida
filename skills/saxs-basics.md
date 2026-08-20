# SAXS/USAXS basics

Small-angle X-ray scattering (SAXS) and ultra-small-angle X-ray scattering
(USAXS) probe nanoscale-to-micron-scale structure — pore/particle size,
shape, size distribution, fractal structure, and phase composition — by
measuring scattered X-ray intensity as a function of scattering angle.

## Conventions used at this beamline (12-ID / 9-ID, APS)

- **Q is in Å⁻¹.** Q = (4π/λ)·sin(θ/2), where θ is the scattering angle.
  Never assume nm⁻¹ or a different convention unless a file's metadata says
  otherwise.
- **Intensity is in cm⁻¹ (absolute units)** wherever the data has been
  calibrated. If a tool result doesn't state units, don't assume absolute
  calibration — say so rather than treating relative and absolute intensity
  as interchangeable.
- **NXcanSAS HDF5** is the interchange format for reduced data at this lab.
  Files carry both the raw reduced I(Q) curve and, when analyses have been
  run, fit results (Unified Fit, Size Distribution, Simple Fits, Modeling,
  WAXS Peak Fit) stored alongside it.
- **USAXS extends SAXS to lower Q** (larger real-space features, up to
  microns) using a Bonse-Hart camera geometry rather than pinhole SAXS —
  the two are often combined (desmeared/merged) to cover several decades of
  Q in one curve.
- **Irena/Igor terminology.** This lab's data reduction and analysis
  heritage is the Irena package (Igor Pro), now reimplemented in pyIrena.
  Keep the names users already know — "Unified Fit," "Size Distribution,"
  "Simple Fits," "Modeling," "Invariant" — rather than inventing new labels
  for the same analysis.

## The five analysis approaches (know when each applies)

- **Simple Fits** — one feature over a restricted Q range: a Guinier radius
  of gyration (Rg), a Porod slope/exponent, an invariant. Fast, low-level,
  good starting point for "what's the size of X" questions.
- **Unified Fit** (Beaucage) — a whole multi-level curve with structural
  levels at different length scales; returns per-level Rg, G (Guinier
  prefactor), B (Porod prefactor), P (Porod exponent), and correlations
  between levels.
- **Size Distribution ("Sizes")** — inverts a dilute, single-population
  scattering curve to a real-space size histogram (volume fraction vs.
  radius). Only valid for dilute, non-interacting, roughly spherical
  populations — say so if the sample looks concentrated or structured.
- **Modeling** — several structural components at once, or a specific form
  factor (core-shell, cylinder, etc.) that Unified Fit's generic levels
  can't capture. Slower to set up, most flexible.
- **WAXS Peak Fit** — wide-angle patterns; questions about crystalline peak
  position (d-spacing), width (crystallite size / strain), and integrated
  area (phase fraction).

Rg (radius of gyration), a fractal dimension (df), and an invariant are the
three numbers most often quoted in a quick answer — know what each one
actually measures before quoting it: Rg is a size metric that assumes a
specific shape model is valid over the fitted Q range; a fractal dimension
only means something over the Q range where power-law scaling actually
holds; the invariant is proportional to the total scattering contrast
squared times phase volume fraction, useful for tracking a sample series
even without a full model fit.

## Scientific correctness over cleverness

If a result looks surprising (a fit converged to an unphysical parameter, a
size distribution with negative volume fraction, an Rg that doesn't match
the visible knee in the curve), say so rather than reporting it uncritically
— these things happen with real SAS fits and a domain expert would flag
them. When in doubt about whether a derived quantity is defined the way
Irena/pyIrena defines it, prefer checking against tool output over guessing
from general scattering-theory memory: this lab's specific conventions
(units, sign conventions, subgroup indexing for multi-level fits) can differ
from a textbook default.
