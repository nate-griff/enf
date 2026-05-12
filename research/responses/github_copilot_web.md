Yes — based on the literature, I’d redesign your ENF extraction around **continuity-constrained tracking plus stronger multi-harmonic evidence**, not around better local peak-picking alone.

## A. Executive Summary

### Top 3 recommended directions
1. **Replace per-frame max-peak selection with continuity-constrained tracking**
   - Use a spectrogram/candidate lattice plus **dynamic programming / Viterbi-style ridge tracking**.
   - This is the best near-term improvement for your exact failure mode.

2. **Move multi-harmonic combination earlier in the pipeline**
   - Use **harmonic-summed evidence** or constrained cross-harmonic candidate scoring before final frequency selection, instead of only “extract separately, then fuse.”

3. **Upgrade the spectral estimator in weak-SNR regions**
   - Try **multi-taper spectral estimation** first.
   - If needed, test **subspace estimators** like MUSIC/ESPRIT in narrow windows for difficult samples.

### Why the current method is likely failing
Your current pipeline is strong when ENF is the dominant narrowband component, but weak when the band contains competing tones. The key failure is likely:

- **wrong tone chosen in individual frames**
- then **smoothed into a plausible-looking but incorrect trace**
- with **no temporal continuity model** to reject implausible jumps or “sticky” local interferers

That matches your benchmark symptom: **same-day plausible correlation, wrong exact time window**.

### Which methods seem most likely to help exact-time recovery
Most likely, in order:

1. **STFT candidate extraction + Viterbi/dynamic ridge tracking**
2. **Harmonic-summed / jointly scored multi-harmonic tracking**
3. **Multi-taper or subspace-based candidate generation feeding the tracker**

---

## B. Current Pipeline Assessment

### Strengths
- Simple, explainable, script-first
- Good fit for NumPy/SciPy
- Reasonable baseline for strong hum recordings
- QIFFT is a sensible refinement over raw FFT bin selection
- Harmonic-2 default is empirically justified in your repo context

### Weaknesses
- **Framewise independence**
- **single strongest peak wins**
- **no explicit uncertainty model**
- **no continuity-aware optimization**
- **post hoc smoothing instead of model-based tracking**
- **multi-harmonic logic is local and heuristic**

### Likely failure modes
1. **Wrong narrowband interferer selected**
   - fan/motor/inverter/transformer tone inside the search band

2. **True ENF is weaker but temporally more consistent**
   - current method can’t exploit that consistency well

3. **Averaging and median filtering hide bad frame decisions**
   - produces smooth but wrong trend

4. **Separate per-harmonic extraction loses joint evidence**
   - if h2 is weak at one moment and h3 is cleaner, the current fusion may still underuse that

5. **Weak ENF requires variance reduction**
   - ordinary Hann+FFT may be too noisy in hard cases

---

## C. Literature Review

Below are the most relevant sources surfaced by the research, with emphasis on practical implications for your pipeline.

### 1. Hajj-Ahmad et al. / related ENF forensic work
**Topic:** foundational ENF extraction and forensic matching pipelines  
**Why it matters:** Much of the standard forensic ENF workflow uses short-time spectral estimation around the nominal frequency or harmonics, followed by correlation/matching. This supports your current baseline as “normal,” but also shows its limitations in low-SNR conditions.

A useful high-level survey source:
- **ENF Based Digital Multimedia Forensics: Survey, Application, Challenges and Future Work**  
  Link: https://www.researchgate.net/publication/373694742_Enf_Based_Digital_Multimedia_Forensics_Survey_Application_Challenges_And_Future_Work/fulltext/64f87327f160f748d6d15928/Enf-Based-Digital-Multimedia-Forensics-Survey-Application-Challenges-And-Future-Work.pdf

**Why it matters here:**  
Confirms that STFT/peak-based extraction is common, but also that weak indirect ENF remains a hard problem and often motivates better enhancement and tracking.

---

### 2. ENF Detection in Audio Recordings via Multi-Harmonic Combining
**Citation:** IEEE paper on multi-harmonic combining  
**Link:** https://ieeexplore.ieee.org/document/9528023

**Short summary:**  
This work argues that using multiple harmonics improves detection/extraction robustness, especially when some harmonics are weak or corrupted. Rather than trusting one band, it exploits multiple ENF-related components.

**Why it matters here:**  
Directly supports the idea that your current “single harmonic + local pick” is leaving useful information on the table. Also supports moving toward **multi-harmonic evidence accumulation**.

---

### 3. Robust ENF Estimation Based on Harmonic Enhancement and …
**Link:** https://arxiv.org/abs/2011.03414

**Short summary:**  
This is one of the most relevant papers for your use case. It focuses on robust ENF estimation using harmonic enhancement and a more principled selection/fusion process across harmonics.

**Why it matters here:**  
This is close to your problem statement:
- weak/partial harmonics
- noisy recordings
- need for better than naive fusion

The main practical takeaway is that **harmonic evidence should be enhanced and selected structurally**, not just independently estimated and averaged later.

---

### 4. Power Signature for Multimedia Forensics
**Link:** https://link.springer.com/chapter/10.1007/978-981-16-7621-5_10

**Short summary:**  
A broader chapter on ENF/power-signature extraction approaches used in multimedia forensics.

**Why it matters here:**  
Useful for framing “standard practice” and situating your pipeline in the mainstream: time-frequency analysis, harmonic use, and robust estimation methods are common themes.

---

### 5. Comparing ENF Extraction Methods
**Link:** https://github.com/gusakovy/comparing-enf-extraction-methods

**Short summary:**  
A practical comparison resource spanning multiple extraction approaches.

**Why it matters here:**  
Even though it is not itself a paper, it’s useful engineering evidence that FFT peak-picking is not the only viable path and that **MUSIC/ESPRIT, Blackman-Tukey, Capon, and related estimators** are worth evaluating.

---

### 6. Detection of Electric Network Frequency in Audio Using Multi-HCNet
**Link:** https://www.mdpi.com/1424-8220/25/12/3697  
PMC: https://pmc.ncbi.nlm.nih.gov/articles/PMC12196992/

**Short summary:**  
A recent deep-learning approach using higher-order harmonic information and learned fusion.

**Why it matters here:**  
It reinforces that multi-harmonic information is powerful, including when the fundamental is missing. However, this is **not** the first thing I’d recommend for your repo because:
- more complexity
- data/training burden
- lower explainability
- harder debugging

Still useful as evidence that the problem is real and that simple local methods are often insufficient.

---

## D. Candidate Methods Compared

| Method | Type | Likely robustness to weak ENF | Implementation complexity | Compute cost | Explainability | Fit for current codebase | Fit for exact-time failure mode |
|---|---|---:|---:|---:|---:|---:|---:|
| Current bandpass + FFT peak + QIFFT | local spectral peak | Low-Medium | Low | Low | High | Excellent | Weak |
| Multi-taper + peak/QIFFT | improved spectral estimate | Medium | Low-Medium | Medium | High | Excellent | Good |
| Harmonic-summed spectrum | multi-harmonic evidence | Medium-High | Medium | Medium | High | Very good | Very good |
| Per-frame top-K candidates + Viterbi | continuity-constrained tracking | High | Medium | Medium | High | Very good | Excellent |
| Kalman smoothing after candidate selection | state-space smoothing | Medium | Medium | Low-Medium | High | Very good | Good |
| HMM / Viterbi on candidate lattice | probabilistic tracker | High | Medium | Medium | Medium-High | Good | Excellent |
| Instantaneous frequency / phase vocoder refinement | phase-based local estimation | Medium | Medium | Medium | Medium | Good | Good |
| Reassigned / synchrosqueezed spectrogram | sharpened TF representation | Medium-High | High | High | Medium | Fair | Good |
| MUSIC / ESPRIT | parametric/subspace | High in narrowband/low-SNR cases | Medium-High | Medium | Medium | Fair-Good | Very good |
| Particle filter | nonlinear tracker | Medium-High | High | High | Medium | Fair | Good |
| Wavelet / EMD / EEMD / VMD preprocessing | decomposition/denoising | Uncertain-Medium | Medium-High | Medium-High | Medium-Low | Fair | Mixed |
| Deep learning multi-harmonic model | learned feature extraction | Potentially High | High | High | Low | Poor | Potentially strong but impractical first |

### Bottom line from the table
For your repo, the best tradeoff is:

- **first:** harmonic-aware candidate generation + **Viterbi tracking**
- **second:** add **multi-taper spectral estimation**
- **third:** test **MUSIC/ESPRIT candidate generation** on hardest samples only

---

## E. Concrete Recommended Experiments

## 1. Replace max-peak with top-K candidate extraction + dynamic ridge tracking
### What stage it touches
`enf_extract.py` core framewise estimation

### What to change
For each frame:
- do not keep only the strongest peak
- keep top **K** peaks in the allowed band per harmonic
- score a full path over time using:
  - spectral strength
  - continuity penalty
  - optional harmonic consistency bonus

### Why
This directly addresses “wrong narrowband component chosen in some frames.”

### Test first on
- `bose_headphones_near_fan`
- `macbook_pro_bathroom_exhaust_fan`
- `short_audio`

### Success looks like
- best match moves from “same day, wrong window” toward correct timestamp window
- extracted trace has more realistic short-term variation
- fewer plateaus on stable non-ENF interferers

### Failure looks like
- path becomes over-smoothed
- tracker locks onto a wrong but smooth tone
- exact-window performance unchanged

---

## 2. Add harmonic-summed evidence before tracking
### What stage it touches
candidate-generation stage in `enf_extract.py`

### What to change
For each fundamental candidate `f`, score:
- energy near `f`
- energy near `2f`
- energy near `3f`
- optionally weighted by empirical harmonic reliability

Instead of extracting h1/h2/h3 separately and fusing after, create a **joint score map** over the fundamental.

### Why
This is more principled than post-fusion. It lets weak harmonics support one another.

### Test first on
- `Microwave`
- `Transformer`
- `DFOR661`
- then exact-timestamp failures

### Success looks like
- improved candidate discrimination in noisy frames
- more stable fundamental path across difficult segments
- better exact-time ranking than single-harmonic h2

---

## 3. Compare multi-taper PSD against current FFT
### What stage it touches
spectral estimation inside each frame

### What to change
Use multiple DPSS tapers and average spectra before peak/candidate extraction.

### Why
Multi-taper often reduces variance and suppresses spurious local peaks in low-SNR conditions.

### Test first on
all hard samples, especially those with weak but persistent hum

### Success looks like
- fewer unstable frame-to-frame peaks
- stronger separation between ENF-related ridge and nearby clutter
- tracker path becomes easier to recover

---

## 4. Add Kalman smoothing after candidate generation
### What stage it touches
post-candidate frequency tracking

### What to change
Use a simple state-space model:
- state: frequency, possibly slope
- observation: candidate frequency from each frame
- measurement variance based on local peak sharpness or harmonic agreement

### Why
Cheaper than full HMM/Viterbi and easy to explain.

### Test first on
same exact-timestamp set

### Success looks like
- modest improvement over raw per-frame picks
- cleaner recovery without flattening true fluctuations

### Note
Kalman alone is probably **not enough** if candidate generation is often wrong.

---

## 5. Prototype MUSIC/ESPRIT on difficult segments only
### What stage it touches
alternative narrowband estimator

### What to change
Use subspace estimation in the narrow band for selected windows/harmonics.

### Why
Could outperform FFT peak interpolation when ENF is weak and closely spaced interferers exist.

### Test first on
short, difficult, exact-time failures:
- `short_audio`
- `bose_headphones_near_fan`

### Success looks like
- candidate frequencies align better with the reference-correlated truth window
- clearer separation of close tones than FFT/QIFFT

### Risk
More sensitive to parameter choice; more engineering effort.

---

## F. Implementation Sketches

## 1. Multi-harmonic score map + Viterbi tracking

```python name=enf_tracking_sketch.py
import numpy as np

def build_fundamental_score_grid(specs_by_harmonic, f_grid, harmonics, weights, tol_hz):
    """
    specs_by_harmonic[h] = (freq_axis_h, power_by_frame_h) where
    power_by_frame_h has shape [n_frames, n_freq_bins]
    returns score_grid shape [n_frames, len(f_grid)]
    """
    n_frames = next(iter(specs_by_harmonic.values()))[1].shape[0]
    score_grid = np.zeros((n_frames, len(f_grid)), dtype=float)

    for j, f0 in enumerate(f_grid):
        total = 0.0
        for h, w in zip(harmonics, weights):
            fh = h * f0
            freq_axis, power = specs_by_harmonic[h]
            idx = np.where(np.abs(freq_axis - fh) <= tol_hz)[0]
            if idx.size:
                total += w * power[:, idx].max(axis=1)
        score_grid[:, j] = total
    return score_grid

def viterbi_track(score_grid, f_grid, jump_penalty_hz=0.01):
    n_frames, n_states = score_grid.shape
    dp = np.full((n_frames, n_states), -np.inf)
    back = np.zeros((n_frames, n_states), dtype=int)

    dp[0] = score_grid[0]

    for t in range(1, n_frames):
        for j in range(n_states):
            transition_cost = -jump_penalty_hz * np.abs(f_grid - f_grid[j])
            vals = dp[t-1] + transition_cost
            k = np.argmax(vals)
            dp[t, j] = vals[k] + score_grid[t, j]
            back[t, j] = k

    path = np.zeros(n_frames, dtype=int)
    path[-1] = np.argmax(dp[-1])
    for t in range(n_frames - 2, -1, -1):
        path[t] = back[t + 1, path[t + 1]]
    return f_grid[path]
```

### Why this is a good fit
- NumPy-friendly
- explainable
- easy to add debug outputs
- lets you inspect score grids and chosen paths

---

## 2. Top-K candidate lattice per frame

```python name=candidate_lattice_sketch.py
import numpy as np
from scipy.signal import find_peaks

def extract_topk_candidates(freq_axis, mag_spectrum, fmin, fmax, k=5):
    mask = (freq_axis >= fmin) & (freq_axis <= fmax)
    fa = freq_axis[mask]
    ma = mag_spectrum[mask]

    peaks, props = find_peaks(ma)
    if peaks.size == 0:
        idx = np.argmax(ma)
        return [(fa[idx], ma[idx])]

    order = np.argsort(ma[peaks])[::-1][:k]
    return [(fa[peaks[i]], ma[peaks[i]]) for i in order]
```

Then build transitions over candidates, not over a dense frequency grid.

---

## 3. Multi-taper spectrum estimation sketch

```python name=multitaper_sketch.py
import numpy as np
from scipy.signal.windows import dpss

def multitaper_psd(frame, fs, nw=3.5, kmax=4, nfft=None):
    if nfft is None:
        nfft = len(frame)
    tapers = dpss(len(frame), NW=nw, Kmax=kmax)
    psd = None
    for taper in tapers:
        xw = frame * taper
        X = np.fft.rfft(xw, n=nfft)
        p = np.abs(X) ** 2
        psd = p if psd is None else psd + p
    psd /= len(tapers)
    freqs = np.fft.rfftfreq(nfft, d=1.0/fs)
    return freqs, psd
```

### Practical note
This is one of the easiest upgrades to trial without redesigning the whole script.

---

## 4. Simple Kalman smoother on frequency observations

```python name=kalman_freq_sketch.py
import numpy as np

def kalman_smooth_freq(obs, obs_var, process_var=1e-4):
    n = len(obs)
    x = np.zeros(n)
    P = np.zeros(n)

    x[0] = obs[0]
    P[0] = obs_var[0]

    for t in range(1, n):
        # predict
        x_pred = x[t-1]
        P_pred = P[t-1] + process_var

        # update
        K = P_pred / (P_pred + obs_var[t])
        x[t] = x_pred + K * (obs[t] - x_pred)
        P[t] = (1 - K) * P_pred

    return x
```

This is best used after improving observations, not as the primary fix.

---

## G. Better Preprocessing / Denoising: What seems actually useful

## Most promising for your case

### 1. Multi-taper spectral estimation
Useful because your issue is not just broadband noise; it’s unstable and misleading narrowband competition. Multi-taper reduces variance and makes peaks more trustworthy.

### 2. Harmonic evidence accumulation before selection
This is more useful than generic denoising because ENF is structurally harmonic across the power-signature pathway.

### 3. Narrow adaptive weighting by harmonic reliability
Rather than hardcoding “prefer h2,” estimate per-frame reliability using:
- local peak prominence
- spectral flatness nearby
- cross-harmonic agreement
- temporal continuity

## Potentially useful but lower-confidence
### Wavelet denoising
Could help broadband clutter, but less targeted to your failure mode.

### Spectral subtraction
May help in stationary-noise cases, but weak for hum-vs-hum discrimination.

### EMD/EEMD/VMD
Interesting research tools, but I would not prioritize them in a small script-first repo. More complexity, less predictable payoff.

### Source separation
Possible, but likely too heavy and less explainable for your first few iterations.

---

## H. Multi-Harmonic Methods in the Literature: Practical takeaway

### Is multi-harmonic extraction common?
Yes. Especially when the fundamental is weak, absent, or contaminated.

### Before or after per-frame estimation?
The better literature direction is generally **before or during** candidate scoring/tracking, not only after.

### More principled than “extract separately then fuse”?
Yes:
- harmonic summation
- joint candidate scoring
- harmonic consistency constraints
- weighted subset selection
- graph/optimization-based fusion

### What this means for your repo
Your current experimental multi-harmonic mode is useful, but still too close to the original local estimator. The likely improvement is to make harmonics contribute to the **selection problem itself**.

---

## I. Research Gaps / Open Questions

1. **How much true short-term ENF variation survives in your recordings?**
   - Some same-day / wrong-window results may reflect recording channels that preserve only coarse trend.

2. **Which harmonic families are most reliable in your actual sample classes?**
   - Fan/HVAC recordings may favor different harmonics than transformer/appliance recordings.

3. **Can confidence be grounded in path evidence instead of local agreement?**
   - A path posterior or normalized Viterbi margin may be more meaningful than current confidence heuristics.

4. **Do some failure cases need source-type-specific handling?**
   - e.g. fan recordings may contain quasi-stationary motor tones that require stronger continuity penalties or harmonic exclusion logic.

5. **Is 1-second final cadence hiding usable sub-second evidence?**
   - You can still output 1 Hz CSV, but the internal tracker may benefit from finer frame cadence before downsampling.

---

## Final direct answer

## If I were redesigning this ENF extraction stage for weak real-world audio, what exact pipeline would I try first, second, and third — and why?

### First pipeline to try
**Multi-taper spectrogram + harmonic-summed score map + Viterbi ridge tracking**

**Pipeline**
1. mono / normalize
2. band-limit around candidate harmonic regions
3. compute **multi-taper spectra** on overlapping sub-second or 1-second frames
4. build a **fundamental-frequency score map** by summing evidence from harmonics 1/2/3 (weighted, per-frame)
5. run **Viterbi/dynamic programming** to choose the most temporally plausible path
6. downsample/aggregate to final 1 Hz output
7. emit path-confidence/debug info

**Why first**
- Best balance of payoff, complexity, and explainability
- Directly targets wrong-frame peak selection
- Uses harmonics more intelligently
- Easy to implement in NumPy/SciPy
- Most likely to improve **exact-time recovery**

**Should it replace or augment current pipeline?**
Augment first, then likely replace as the “robust” mode if it benchmarks better.

---

### Second pipeline to try
**Top-K per-frame candidates from one or more harmonics + HMM/Viterbi tracker with reliability-aware scoring**

**Pipeline**
1. current or multi-taper spectral estimation
2. extract top-K candidates per frame per harmonic
3. score candidates by:
   - peak prominence
   - local SNR
   - harmonic agreement
   - continuity
4. run **candidate-lattice Viterbi**
5. smooth lightly if needed
6. output final 1 Hz trace plus confidence

**Why second**
- Slightly more explicit than dense score-map tracking
- Easier to debug candidate-by-candidate
- Lets you inspect whether the correct ENF was present but not selected
- Very good for benchmark-driven iteration

**Should it replace or augment current pipeline?**
Augment. It’s also a strong diagnostic framework.

---

### Third pipeline to try
**Subspace estimator (MUSIC/ESPRIT) for candidate generation + continuity-constrained tracking**

**Pipeline**
1. isolate narrow harmonic bands
2. run **MUSIC or ESPRIT** per frame or selected hard segments
3. generate candidate frequencies
4. fuse across harmonics
5. apply Viterbi/Kalman/HMM continuity tracking
6. output 1 Hz trace

**Why third**
- Higher potential in low-SNR, closely spaced narrowband interference
- More research-heavy and parameter-sensitive
- Good for the hardest samples if FFT-family methods still fail

**Should it replace or augment current pipeline?**
Augment first, probably as an advanced mode for hard recordings.

---

## My prioritized recommendation list

### 1. Best near-term improvement
**Dynamic ridge tracking over a harmonic-aware score map**
- Smallest change with highest likely payoff
- Most aligned with your failure mode
- Medium implementation effort
- Medium compute
- Strong expected robustness gain on hard exact-time samples

### 2. Best medium-complexity improvement
**Multi-taper candidate generation + reliability-aware multi-harmonic tracking**
- Better observations + better path selection
- Medium effort
- Very practical in SciPy
- Good chance of reducing wrong-window false matches

### 3. Best ambitious improvement
**MUSIC/ESPRIT-based multi-harmonic candidate extraction with probabilistic tracking**
- Highest research value among classical methods
- Harder to tune
- Potentially strong on weak ENF under nearby interference
- Better fit than deep learning for your repo’s current constraints

If you want, I can next turn this into a **repo-specific implementation plan for `enf_extract.py`**, with:
- CLI additions,
- internal function breakdown,
- benchmark order,
- and a concrete “Phase 1 / Phase 2 / Phase 3” patch plan.