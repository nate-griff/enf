# Research Prompt: Better Signal-Processing Approaches for ENF Extraction from Noisy Audio

## Objective

Research state-of-the-art and practical signal-processing methods for extracting **Electric Network Frequency (ENF)** from **real-world audio recordings where the ENF is weak, indirect, or heavily contaminated by other sounds**.

The specific goal is to help improve the ENF extraction stage in a local Python-based tool so that it can recover a more accurate 60 Hz ENF trace from recordings where the ENF is **not** a dominant nearby hum.

This research should focus on:

1. **How ENF is typically extracted in academic literature and forensic workflows**
2. **Which methods are most effective when ENF is weak and buried in background audio**
3. **How those methods compare to our current implementation**
4. **Which improvements are most realistic to implement in a script-first Python codebase using NumPy/SciPy**
5. **A concrete, prioritized roadmap for improving our extraction pipeline**

Please use:

- academic papers
- theses/dissertations if useful
- conference papers
- forensic-audio / signal-processing references
- reputable engineering articles or blog posts when relevant

Please prioritize **actual ENF extraction literature** over generic audio DSP content, but include generic DSP techniques if they are directly relevant to weak narrowband tracking.

---

## Repository / Tool Context

This is a small Python repository organized around standalone scripts:

- `enf_extract.py` — extract ENF trace from audio/video to CSV
- `enf_compare.py` — compare extracted trace against grid reference data
- `enf_view.py` — inspect comparison results

The project is a **research / investigative aid**, not a forensic proof engine.

The output of extraction is a CSV with:

```csv
offset_seconds,frequency_hz
```

Optionally, recent experimental work added:

```csv
offset_seconds,frequency_hz,confidence_score
```

The downstream matcher expects a 1 Hz-ish ENF trace and compares it against 1-second grid reference data.

---

## Current Extraction Pipeline

The current implementation is centered in `enf_extract.py`.

### Input Handling

- Reads WAV and FLAC directly
- Converts video and other audio formats to mono 48 kHz WAV via `ffmpeg`
- Converts stereo to mono by averaging channels
- Normalizes samples to approximately `[-1, 1]`

### Core Single-Harmonic Extraction Method

The current default extraction method is:

1. Choose a nominal frequency, usually **60 Hz**
2. Choose one harmonic, default **harmonic 2** (120 Hz)
3. Bandpass filter the signal around `nominal * harmonic` with a Butterworth bandpass
4. Split the filtered signal into overlapping frames
5. Apply a Hann window to each frame
6. Run an FFT on each frame with configurable zero-padding
7. Search for the strongest FFT peak inside the expected frequency band
8. Use **QIFFT** (quadratically interpolated FFT peak estimation) for sub-bin frequency estimation
9. Divide by the harmonic number to map the estimate back to the 60 Hz fundamental
10. Aggregate multiple estimates into approximately one estimate per second
11. Optionally apply a median filter to suppress isolated spikes

### Important Current Parameters

- `--nominal` — default 60 Hz
- `--harmonic` — default 2
- `--bandwidth` — default 0.5 Hz half-bandwidth
- `--frame-sec` — default 1.0 s
- `--overlap` — default 0.5
- `--pad-factor` — default 16
- `--median-window` — default 3

### Why Harmonic 2 Is the Current Default

In practice, harmonic 2 (120 Hz) has often been cleaner than the 60 Hz fundamental in this codebase’s recordings. It appears less contaminated by some environmental/mechanical interference in certain recordings.

---

## Recent Experimental Multi-Harmonic Changes

An **experimental** multi-harmonic mode was added, but benchmarking suggests it is not a full solution.

### Current Experimental CLI

- `--multi-harmonic`
- `--harmonics 1,2,3`
- `--harmonic-fusion weighted|vote|mean`
- `--confidence-output`
- `--detail-output`

### Current Experimental Multi-Harmonic Logic

For requested harmonics (default `1,2,3`):

1. Run the same single-harmonic extraction pipeline independently for each harmonic
2. Smooth each harmonic trace individually
3. Fuse the harmonic traces into a single 60 Hz trace using one of:
   - `weighted` — prefers harmonic 2, then 3, then 1
   - `vote` — returns one real candidate estimate rather than an invented midpoint when harmonics disagree
   - `mean` — simple average
4. Optionally emit a `confidence_score`

### Extra Runtime Behavior Added

- Unsupported harmonics relative to Nyquist are handled explicitly
- In multi-harmonic mode:
  - default unsupported harmonics are skipped with a warning
  - explicitly requested unsupported harmonics fail cleanly
- In single-harmonic mode:
  - invalid harmonic/sample-rate combinations also fail cleanly

This experimental path is useful context, but **do not assume it is the right long-term solution**.

---

## Current Failure Pattern

The extraction pipeline works best when the recording contains a **very strong, nearby, obvious hum**.

The user’s actual problem is:

- recordings near a fan with a strong ENF-like hum can match well
- recordings where ENF is only a weak background component often fail
- the extracted trace is often strong enough to produce a plausible match on the right day, but **not the correct time window**
- this suggests the extracted trace often contains:
  - a partially related signal
  - the wrong narrowband component
  - a distorted or flattened ENF trend
  - or a strong local interference source that looks more stable than true ENF

### Suspected Root Problems

Potential failure modes in the current approach include:

1. **Peak-picking the wrong narrowband component**
   - The strongest FFT peak in the band may not be true ENF
   - It may instead be a motor harmonic, inverter tone, or other hum-like line

2. **No continuity model**
   - Each frame is handled mostly independently
   - There is no explicit tracker that prefers physically plausible ENF evolution over time

3. **Bandpass + local peak search may be too brittle**
   - If ENF is weak relative to nearby interference, max-peak search is easy to fool

4. **No explicit denoising / source separation stage**
   - Weak ENF may need preprocessing before narrowband tracking

5. **Aggregation may hide uncertainty**
   - Averaging overlapping frames into 1 Hz output can make a bad estimate look smooth

6. **Confidence is very simple**
   - Current confidence is not based on true signal quality or probabilistic tracking

7. **One-dimensional peak estimation may be too naive**
   - ENF in difficult audio may need ridge-tracking, harmonically constrained tracking, or parametric frequency estimation

---

## Benchmark Context

A benchmark inventory was built from difficult recordings under:

```text
sample_data\audio_samples\samples_that_dont_match
```

The benchmark distinguishes:

- **exact timestamp** cases — filename includes a usable local timestamp
- **date window** cases — filename only gives the date, and known recording window is roughly 10 AM-4 PM local in EI

The evaluation explicitly avoids rewarding obvious false positives that have:

- high correlation
- but very poor absolute threshold coverage

### Difficult Sample Set

Examples include:

- `bose_headphones_near_fan`
- `macbook_pro_bathroom_exhaust_fan`
- `short_audio`
- `Microwave`
- `Transformer`
- `DFOR661`

---

## Single-Harmonic Benchmark Findings

A compact single-harmonic sweep was run across harmonics 1, 2, and 3 plus tuning variations.

### High-Level Result

- **No exact timestamp hits were recovered**
- Some date-window cases improved
- Harmonic 2 was usually strongest for exact-time samples
- Harmonic 3 sometimes helped date-window cases
- Tuning alone improved some cases but did **not** solve the core problem

### Notable Examples

#### Exact-Timestamp Cases

For exact-time samples, the best single-harmonic runs were usually still:

- `plausible_but_wrong_window`

Examples:

- `bose_headphones_near_fan`
  - `h2_base` score ≈ `0.720`
  - `h2_tight_long` score ≈ `0.788`
  - still wrong time window

- `macbook_pro_bathroom_exhaust_fan`
  - best single-harmonic score ≈ `0.439`
  - still wrong time window

- `short_audio`
  - best single-harmonic score ≈ `0.608`
  - still wrong time window

#### Date-Window Cases

Some date-window cases improved enough to land inside the correct date/time block:

- `Microwave`
  - harmonic 1, 2, and 3 all produced `date_window_hit` in some settings
- `Transformer`
  - harmonic 1 and 3 produced `date_window_hit` in some settings
- `DFOR661`
  - harmonic 3 produced `date_window_hit` in some settings

### Interpretation

This suggests:

- the system is often extracting *some* grid-related trend
- but not with enough fidelity to identify the exact recording time
- strong but wrong-window matches imply the extracted trace shape can still resemble true ENF at coarse scale
- the extraction likely lacks either:
  - enough robustness to isolate true ENF from nearby interference
  - enough tracking to preserve the right short-term variations

---

## Multi-Harmonic Benchmark Findings

A focused rerun tested:

- `mh_weighted`
- `mh_vote`
- `mh_mean`

### High-Level Result

- Multi-harmonic mode **still did not recover exact timestamp hits**
- `vote` was often the strongest fusion method
- some weak/date-window cases remained plausible or hit the right date window
- the evidence did **not** justify replacing the default single-harmonic path

### Examples

#### Exact-Timestamp Cases

- `bose_headphones_near_fan`
  - `mh_vote` score ≈ `0.694`
  - still wrong window

- `macbook_pro_bathroom_exhaust_fan`
  - `mh_vote` score ≈ `0.434`
  - still wrong window

- `short_audio`
  - `mh_vote` score ≈ `0.583`
  - still wrong window

#### Date-Window Cases

- `Microwave`
  - `mh_weighted`, `mh_vote`, and `mh_mean` all produced `date_window_hit`

### Interpretation

The current multi-harmonic implementation may help when harmonics provide complementary evidence, but a simple fuse-after-extract approach does **not** solve the deeper extraction problem.

---

## Important Technical Details About the Current Algorithm

Please assume the following about the current implementation:

### Frequency Estimation

- It is **framewise FFT peak search**, not a parametric sinusoid estimator
- It relies on **magnitude maxima inside a narrow band**
- It uses **quadratic interpolation**, not phase-based refinement, reassignment, MUSIC, ESPRIT, or Kalman filtering

### Time Resolution

- It ends with roughly **1 Hz cadence**
- Overlapping frame estimates are averaged into 1-second bins

### Smoothing

- It uses only a **median filter**
- There is no explicit state-space model or continuity-constrained tracker

### Fusion / Confidence

- Multi-harmonic fusion is simple, heuristic, and local
- Confidence is an agreement heuristic, not a proper signal-quality metric or posterior probability

### Downstream Constraint

Any improved method should ideally still be capable of producing a final output trace that can feed the current comparison stage:

```csv
offset_seconds,frequency_hz
```

Optional extra debug outputs are acceptable, but a clean final trace is still needed.

---

## What I Want You To Research

Please research the following in depth.

### 1. Typical ENF Extraction Approaches

Find how ENF is commonly extracted in:

- forensic audio analysis
- video ENF extraction literature where audio ideas still apply
- weak-hum / indirect-ENF scenarios

Please summarize common pipelines and cite specific papers.

I especially want to know:

- what is considered standard practice
- what methods are used when ENF is weak
- whether QIFFT / STFT peak interpolation is common, and what usually comes after it

### 2. Better Frequency-Tracking Methods

Research whether we should replace or augment the current framewise max-peak method with something like:

- ridge tracking on spectrogram/STFT
- Viterbi / dynamic programming over time-frequency candidates
- Kalman filtering / state-space tracking
- particle filtering
- hidden Markov models
- phase vocoder / instantaneous frequency estimation
- reassigned spectrogram / synchrosqueezed transforms
- MUSIC / ESPRIT / other parametric estimators
- autocorrelation-based narrowband tracking
- PLL / adaptive notch / adaptive sinusoid tracking

For each promising method, explain:

- why it might work better than current QIFFT peak-picking
- what assumptions it makes
- how hard it would be to implement in a small Python codebase
- whether it is robust for weak background ENF in consumer recordings

### 3. Better Preprocessing / Denoising

Research how people improve ENF extraction **before** final frequency estimation.

Potential areas:

- multi-stage bandpass design
- comb filters / harmonic summation
- spectral subtraction
- denoising methods specific to hum-like components
- source separation
- EMD / EEMD / VMD / decomposition methods
- multi-taper spectral estimation
- wavelet denoising
- cepstral / harmonic enhancement ideas
- averaging across harmonics before tracking instead of after tracking

I want to know which preprocessing stages are actually used in ENF work and which are likely to help in our specific failure mode.

### 4. Multi-Harmonic Methods in the Literature

Research whether better multi-harmonic approaches exist than our current heuristic fusion.

Examples to investigate:

- harmonic summation in the spectrum before peak-picking
- jointly constrained harmonic tracking
- harmonic product spectrum
- comb-based estimators
- weighted evidence accumulation across harmonics
- cross-harmonic consistency constraints

I want to know whether:

- multi-harmonic extraction is common in ENF literature
- it is usually done before or after per-frame frequency estimation
- there are more principled ways to exploit harmonics than “extract separately, then fuse”

### 5. Weak / Indirect ENF Scenarios

This is especially important.

Please focus on papers or techniques that deal with:

- ENF not being the strongest tone in the recording
- recordings from phones / consumer mics
- appliance noise, fan noise, HVAC noise, transformer hum, mixed environmental recordings
- ENF as a weak background modulation rather than a dominant direct signal

I need methods that are realistic for those scenarios, not just clean lab examples.

### 6. What We Should Try Next

After the literature review, produce a prioritized recommendation list:

1. **Best near-term improvement** (smallest change, highest likely payoff)
2. **Best medium-complexity improvement**
3. **Best ambitious / research-heavy improvement**

For each recommendation, include:

- why it is promising
- how it compares to current QIFFT extraction
- expected implementation difficulty
- expected computational cost
- likely robustness on our hard samples
- whether it should replace or augment the current pipeline

---

## Desired Output Format

Please produce a structured report with these sections:

### A. Executive Summary

- top 3 recommended directions
- brief statement of why the current method is failing
- which methods seem most likely to help exact-time recovery

### B. Current Pipeline Assessment

- concise technical critique of the current approach
- strengths
- weaknesses
- likely failure modes

### C. Literature Review

For each relevant paper / source:

- full citation
- direct link if possible
- short summary
- why it matters here

### D. Candidate Methods Compared

Please include a comparison table with columns like:

- method
- type
- likely robustness to weak ENF
- implementation complexity
- compute cost
- explainability
- fit for current codebase
- fit for our exact-time failure mode

### E. Concrete Recommended Experiments

Please give specific experiment ideas we can run on our current repository, for example:

- replace peak-pick with dynamic ridge tracking
- add harmonic-summed spectrogram before tracking
- compare multi-taper PSD vs current FFT
- add Kalman smoothing after per-frame candidate generation

For each experiment, say:

- what code stage it would touch
- what benchmark samples to test first
- what success/failure would look like

### F. Implementation Sketches

Where possible, include **algorithm-level pseudocode** or implementation sketches suitable for NumPy/SciPy/Python.

### G. Research Gaps / Open Questions

Anything unclear, contradictory, or especially worth validating experimentally.

---

## Constraints / Preferences

Please keep these in mind:

1. The codebase is small and script-first
2. We prefer **clear, explainable** methods over black-box magic
3. We still want a final output trace that can be compared against 1 Hz reference data
4. We are open to better DSP, but not to a solution that is wildly impractical to implement or debug
5. We care more about **recovering the true ENF trend** than about making a trace merely look smooth
6. We specifically need help with recordings where ENF is weak, indirect, or masked by other audio

---

## Benchmark Takeaway To Keep In Mind

The key observation from our benchmarking is:

> We can often get a **plausible same-day match**, but we frequently miss the **correct exact time window**.

That means the extraction is often not completely random. It is capturing something related, but not accurately enough. Please bias your recommendations toward methods that improve **fine-grained temporal fidelity** and **robust tracking of the true ENF component**, not just coarse correlation.

---

## Final Question To Answer

After doing the research, answer this directly:

> If you were redesigning this ENF extraction stage for weak real-world audio, what exact signal-processing pipeline would you try first, second, and third — and why?

