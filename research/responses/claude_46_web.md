# ENF Extraction Signal Processing Research Report
### Better Methods for Weak and Noisy Audio Recordings

*Prepared for: Nathan Griffin — ENF Analysis Tool Research*  
*Date: May 2026*

---

## A. Executive Summary

### Top Three Recommended Directions

**1. SNR-weighted Spectrum Combining across harmonics (near-term, highest ROI)**  
Before tracking individual harmonic peaks, compute a per-frame local SNR estimate at each harmonic band and combine spectrogram slices using SNR-derived weights. This is the Hajj-Ahmad et al. (2013) approach. It directly addresses the problem that your harmonic heuristic (prefer h2, then h3, then h1) does not know whether any harmonic is actually reliable in a given frame. It requires no architectural change to `enf_extract.py`—only a replacement of the fusion step.

**2. Iterative Dynamic Programming / Ridge Tracking on the spectrogram (medium-term, the biggest qualitative fix)**  
Replace the per-frame max-peak search with a globally coherent, continuity-constrained frequency track over the full spectrogram. The AMTC algorithm (Zhu et al., 2020/IEEE TIFS) is the closest published method to an off-the-shelf solution and was specifically validated on ENF audio at −8 dB SNR. Dynamic programming + adaptive trace compensation is the single most impactful change for recovering the *correct time window* rather than just a plausible-looking one.

**3. Robust Filtering Algorithm (RFA) / Harmonic RFA pre-enhancement (near-term parallel investment)**  
Before frequency estimation, apply the RFA (Hua et al., 2019/2021) as a narrowband-specific denoising stage. This operates in the time domain as a specialized adaptive notch-like filter that suppresses out-of-band and in-band noise without the broad averaging of a Butterworth filter. In the published ENF literature this is the standard preprocessing step when SNR is adversely low.

### Why the Current Method Fails

The current `enf_extract.py` design is a **frame-independent, single-peak-picking STFT pipeline**. Each frame is processed independently: the strongest FFT peak inside the bandwidth window is taken as the ENF estimate for that frame. When ENF is weak relative to motor harmonics, fan blade tones, HVAC noise, or other narrowband interference, the strongest peak will often be the interferer, not the ENF. Nothing in the pipeline discriminates between a true slowly-drifting ENF trace and a nearly-stationary hum. The frame-to-frame independence means no continuity model catches when the tracker has jumped to an interference tone.

### Which Methods Most Help Exact-Time Recovery

Exact-time recovery specifically requires **fine-grained temporal fidelity**—the extracted trace must follow the true ENF fluctuation pattern, not a smooth average or a competing steady tone. Methods ranked by impact on this specific failure mode:

1. **Dynamic programming / ridge tracking** — directly imposes temporal continuity, forcing the tracker to follow a physically plausible slowly-varying path instead of jumping between interference peaks.
2. **Harmonic spectrum combining (SNR-weighted)** — reduces the probability of locking onto a false peak in any given frame by pooling evidence across harmonics before committing to a frequency estimate.
3. **RFA pre-enhancement** — improves the input signal quality before peak-picking so the spectrogram itself is less contaminated.

---

## B. Current Pipeline Assessment

### What the Current Implementation Does

`enf_extract.py` implements a textbook-quality single-harmonic STFT extraction pipeline:

1. Mono downmix and normalization
2. 4th-order Butterworth bandpass around `nominal × harmonic ± bandwidth`
3. Hann-windowed overlapping frames with zero-padding (16×)
4. FFT peak search in the bandpass region
5. QIFFT quadratic interpolation for sub-bin accuracy
6. Frame aggregation to 1 Hz cadence
7. Median filter smoothing

This is a solid implementation of what the 2008–2013 ENF literature would call the standard STFT approach. It works well when the ENF is the dominant energy in the bandpass region.

### Strengths

- **Correct algorithmic foundation.** STFT + quadratic interpolation is the baseline that virtually all ENF papers use and compare against.
- **Second-harmonic default is well-justified.** The 120 Hz band is commonly observed to have better SNR than 60 Hz in consumer recordings due to lower ambient noise power density and higher power-line harmonic energy from nonlinear loads.
- **Zero-padding factor of 16 is appropriate.** At 48 kHz and 1-second frames, 16× padding gives ~0.001 Hz bin spacing, well under the 0.01 Hz matching threshold.
- **Median filter is appropriate.** It correctly handles isolated spike outliers without phase-smearing the underlying trend.
- **Multi-harmonic experiment is directionally correct.** The literature does use multiple harmonics; the issue is the fusion strategy.

### Weaknesses

| Weakness | Explanation |
|---|---|
| Per-frame independence | No continuity model. A frame that captures an interference peak instead of ENF has no prior frame evidence to correct it. |
| Fusion uses fixed harmonic preference, not SNR | The `weighted` fusion prefers h2 regardless of whether h2 is actually clean in a given frame. |
| Butterworth bandpass may not suppress in-band interferers | A narrowband fan tone at 118 Hz is inside the 120 ± 0.5 Hz passband and passes through. |
| Confidence is agreement-based, not signal-quality based | Two harmonics agreeing on the wrong frequency both look confident. |
| No preprocessing stage for source separation | Speech, music, fan noise, and motor harmonics are not attenuated before frequency estimation. |
| Aggregation hides per-frame uncertainty | Averaging multiple per-frame estimates makes a noisy frame look as credible as a clean one. |

### Likely Failure Modes

**Mode 1 — Locking onto a competing narrowband tone.** The most common failure on the hard samples. A fan motor or HVAC unit produces a stable harmonic at, say, 119.8 Hz. The Butterworth filter passes it. The FFT peak finds it as the strongest in-band peak. The QIFFT refines it to high precision. The result looks smooth and confident but tracks the fan speed, not the ENF.

**Mode 2 — Correct-day, wrong-time matching.** If the extracted trace has the right coarse frequency range but the wrong fine-grained fluctuation pattern (because it is tracking a mix of true ENF and interference), the correlation matcher finds the right day (similar average frequency) but cannot resolve the correct 5-minute window.

**Mode 3 — SNR collapse on certain recordings.** Very short recordings, or recordings from devices with aggressive high-pass filtering, may have essentially zero usable ENF energy even at harmonic 2. The pipeline extracts a trace anyway, but it is pure noise.

---

## C. Literature Review

### C.1 — Foundational ENF Pipeline Papers

**[1] Grigoras, C. (2005, 2007). "Digital audio recording analysis — the electric network frequency (ENF) criterion." *Int. J. Speech Lang. Law* 12(1):43–49; *Forensic Sci. Int.* (2007).**

The original forensic application of ENF. Established the core workflow: bandpass filter around 50 Hz, downsample, compare spectrogram against reference database. Still the conceptual baseline for the entire field. Relevant as context for why STFT is the field's starting point.

**[2] Cooper, A.J. (2008/2009). "An automated approach to the Electric Network Frequency (ENF) criterion — Theory and practice." *Metropolitan Police Forensic Audio Laboratory reports.*  
→ ResearchGate: https://www.researchgate.net/publication/250014951**

Describes the full operational forensic workflow used by UK Metropolitan Police. Uses STFT + quadratic peak interpolation as the core estimator. Discusses practical issues with weak traces and recommends harmonic use. Confirms QIFFT as the standard forensic practice. The most important practical reference for understanding what the field actually deploys.

**[3] Hajj-Ahmad, A., Garg, R., and Wu, M. (2013). "Spectrum combining for ENF signal estimation." *IEEE Signal Processing Letters* 20(9):885–888.**  
→ MAST Lab: https://mast.umd.edu/research.php?t=enf

**Key paper for this project.** Proposes combining per-harmonic spectrogram strips using local SNR weights computed independently per frame per harmonic. Shows the Cramér–Rao bound argument that M harmonics combined properly give theoretically O(M³) improvement in estimation accuracy. Demonstrates that SNR-adaptive weighting outperforms fixed-harmonic selection. This is the principled replacement for the current fixed-preference fusion.

**[4] Bykhovsky, D. and Cohen, A. (2013). "Electrical network frequency (ENF) maximum-likelihood estimation via a multitone harmonic model." *IEEE Trans. Inf. Forensics Security.*  
→ ResearchGate: https://www.researchgate.net/publication/260299660**

Derives the MLE for ENF under a multi-tone harmonic signal model. Shows that jointly estimating frequency across all harmonics (rather than estimating each harmonic independently and fusing) is theoretically superior. Practically expensive but sets the upper bound for what multi-harmonic approaches can achieve. Useful as a target architecture.

**[5] Ojowu, O., Karlsson, J., Li, J., and Liu, Y. (2012). "ENF extraction from digital recordings using adaptive techniques and frequency tracking." *IEEE Trans. Inf. Forensics Security* 7(4):1330–1338.**  
→ IEEE Xplore: https://ieeexplore.ieee.org/document/6193429

Introduced the **time-recursive iterative adaptive approach (TRIAA)** as a higher-resolution alternative to standard STFT, followed by **dynamic programming** for frequency tracking over time. Showed that adaptive spectral estimation + DP continuity constraints improves tracking accuracy under interference. The ancestor of the AMTC method below. Implementation is computationally expensive.

### C.2 — Robust Estimation and Preprocessing

**[6] Lin, X. and Kang, X. (2018). "Robust electric network frequency estimation with rank reduction and linear prediction." *ACM Trans. Multimedia Computing, Communications and Applications* 14(4), article 84.**  
→ ACM DL: https://dl.acm.org/doi/10.1145/3232082

Proposes using **low-rank signal matrix decomposition** (rank reduction via SVD/PCA) to separate the ENF subspace from noise before estimation. Combined with linear prediction (ESPRIT/root-MUSIC style) for high-resolution frequency estimation. The rank-reduction step is analogous to subspace methods in array processing. Works well when ENF occupies a predictable low-dimensional subspace within the covariance matrix of the STFT. Relevant for cases where ENF is weak but present across many frames.

**[7] Hua, G., Bi, H., Thing, V.L.L. (2019). "On practical issues of electric network frequency based audio forensics." *IEEE Access.*  
→ Semantic Scholar: https://www.semanticscholar.org/paper/On-Practical-Issues.../54cba7c6db8b2b1d9aa6b605f6197496210e39f9**

A critical reference. Introduces the **Robust Filtering Algorithm (RFA)** — a narrowband signal enhancement method that improves the input-signal SNR specifically for ENF components *before* frequency estimation. Differs from a simple Butterworth in that it adaptively models and suppresses the residual in-band noise. The paper explicitly concludes that for many practical recordings, noise and interference remain the dominant open problem, and that preprocessing is more impactful than estimator choice.

**[8] Hua, G., Liao, H., Zhang, H., Ye, D., and Ma, J. (2021). "Robust ENF estimation based on harmonic enhancement and maximum weight clique." *IEEE Trans. Inf. Forensics Security* 16:3874–3887.**  
→ arXiv: https://arxiv.org/abs/2011.03414

**Directly relevant.** Extends the RFA to multi-harmonic (**HRFA** — Harmonic RFA) and adds a **graph-based harmonic selection algorithm (GHSA)** that identifies which harmonic components are reliable using a maximum weight clique formulation. Rather than using all harmonics or using a fixed preference, GHSA selects the *best subset* of harmonics that are mutually consistent. Evaluated on ENF-WHU (130 real-world recordings). Shows substantial improvement over both single-harmonic and naive multi-harmonic baselines. This is state-of-the-art (2021) for robust multi-harmonic ENF extraction.

**[9] Karantaidis, G. and Kotropoulos, C. (2018). "Assessing spectral estimation methods for electric network frequency extraction." *Proc. 22nd Pan-Hellenic Conference on Informatics*, pp. 202–207.**

Comparative study of STFT, MUSIC, ESPRIT, and RELAX for ENF extraction. Finds that parametric methods (MUSIC, ESPRIT) offer better frequency resolution than STFT at short frame lengths, but their advantage shrinks as frame length increases and is sensitive to model order selection. RELAX (a relaxation-based sine-fitting algorithm) is shown to be competitive with subspace methods at lower complexity. Relevant as an honest assessment of when MUSIC/ESPRIT actually help.

### C.3 — Frequency Tracking Under Low SNR

**[10] Zhu, Q., Wu, M., and Koushanfar, F. (2020). "Adaptive multi-trace carving for robust frequency tracking in forensic applications." *IEEE Trans. Inf. Forensics Security* 15:3835–3849.**  
→ arXiv: https://arxiv.org/abs/2005.06686  
→ IEEE Xplore: https://ieeexplore.ieee.org/document/9220114

**The most important single paper for this project's failure mode.** Introduces **Adaptive Multi-Trace Carving (AMTC)**: takes a spectrogram as input, runs iterative dynamic programming forward and backward to find the highest-energy time-frequency ridge consistent with slow frequency evolution, then compensates (subtracts) found traces to reveal weaker secondary traces. Explicitly tested on ENF audio at −8.2 dB estimated SNR. In Figure 13 of the paper, AMTC outperforms quadratic interpolation, particle filter, and YAAPT on a real ENF recording. The key insight: rather than picking the local maximum in each frame independently, DP finds the globally optimal path across the spectrogram that respects an energy-continuity prior. This is a direct, principled fix for the current pipeline's per-frame independence problem.

**[11] Zhu, Q. et al. (2019). "Adaptive multi-trace carving based on dynamic programming." *Proc. IEEE ICASSP 2019.*  
→ IEEE Xplore: https://ieeexplore.ieee.org/document/8645216**

Conference precursor to [10]. Lighter description of the core DP formulation. Useful for understanding the algorithm before the full TIFS version.

**[12] Esquef, P.A.A., Apolinario, J.A., and Biscainho, L.W.P. (2014). "Edit detection in speech recordings via instantaneous electric network frequency variations." *IEEE Trans. Inf. Forensics Security* 9(12):2314–2326.**

Uses the **instantaneous frequency** of the ENF component (computed via analytic signal / Hilbert transform) rather than STFT peak estimation. Shows higher temporal resolution and sensitivity to fine-grained ENF variations. Particularly interesting for exact-time recovery because instantaneous frequency tracks the *phase evolution* of the ENF, not just its bin-average power. Computationally practical with SciPy.

### C.4 — Multi-Taper Spectral Estimation

**[13] Percival, D.B. and Walden, A.T. (1993). *Spectral Analysis for Physical Applications.* Cambridge University Press.**  
**[14] Thomson, D.J. (1982). "Spectrum estimation and harmonic analysis." *Proc. IEEE* 70(9):1055–1096.**

The theoretical foundation for **multi-taper spectral estimation (MTSE)**. Multi-taper methods apply K orthogonal Slepian taper sequences to the same data window, compute K spectra, and average them. This controls spectral leakage with a provably optimal bias-variance trade-off. For ENF work, multi-taper is relevant because a narrow-bandwidth ENF signal at −20 dB relative power may be completely obscured by spectral leakage from a nearby stronger tone under a single Hann window, but visible under optimal multi-taper. The `spectrum_combining` reference [3] specifically mentions multi-taper. scipy.signal has no built-in multi-taper, but `nitime` and standalone Slepian implementations exist in NumPy.

### C.5 — Instantaneous Frequency and Phase-Based Methods

**[15] Boashash, B. (1992). "Estimating and interpreting the instantaneous frequency of a signal." *Proc. IEEE* 80(4):520–568.**

The classic reference for **instantaneous frequency (IF) estimation**. IF computed as the time derivative of the instantaneous phase from the analytic signal (Hilbert transform) gives the true instantaneous frequency of a narrowband sinusoid without frame-to-frame averaging. For ENF, IF provides much finer time resolution than 1-frame STFT bins. The limitation is sensitivity to noise: even small noise components cause IF to fluctuate wildly. Pre-filtering must be adequate before IF estimation is useful.

**[16] Auger, F. and Flandrin, P. (1995). "Improving the readability of time-frequency and time-scale representations by the reassignment method." *IEEE Trans. Signal Process.* 43(5):1068–1089.**

Introduces **reassigned STFT**: for each time-frequency bin, the energy is relocated to the centroid of its surrounding neighborhood rather than the bin center. Produces a much sharper TF representation. For ENF, a reassigned spectrogram can make the ENF trace visually and algorithmically distinguishable from nearby interference even at low SNR. The `ssqueezepy` library implements synchrosqueezing (a related improvement) in Python.

### C.6 — Variational Mode Decomposition and Source Separation

**[17] Dragomiretskiy, K. and Zosso, D. (2014). "Variational mode decomposition." *IEEE Trans. Signal Process.* 62(3):531–544.**  
**[18] 1D-CNN audio tampering detection (ENR method) — PMC11099188, Scientific Reports 2024.**

VMD decomposes a signal into a set of narrowband intrinsic mode functions with specified center frequencies. In the ENF context, it can be used to isolate the ENF component from broadband noise and competing harmonics *before* frequency estimation. The 2024 Scientific Reports paper (PMC11099188) combines VMD with RFA as an ENF Noise Reduction (ENR) module and reports substantial improvement in low-SNR scenarios. Python implementation: `vmdpy` package.

---

## D. Candidate Methods Compared

| Method | Type | Weak ENF Robustness | Implementation Complexity | Compute Cost | Explainability | Fit for Current Codebase | Exact-Time Help |
|---|---|---|---|---|---|---|---|
| **Current QIFFT / max-peak** | Non-parametric, frame-independent | Poor — locks onto dominant tone | Baseline | Low | High | Already present | Poor |
| **SNR-weighted spectrum combining** (Hajj-Ahmad 2013) | Non-parametric, multi-harmonic | Moderate–Good | Low — modifies fusion step only | Low | High | Excellent | Moderate |
| **Dynamic programming ridge tracking (AMTC-style)** | Non-parametric, time-continuous | Good — explicitly tested at −8 dB | Medium | Medium | High | Good | **High** |
| **RFA / HRFA pre-enhancement** | Adaptive filtering, pre-processing | Good | Medium | Medium | Medium | Good | Moderate–High |
| **Kalman / EKF tracking** | Parametric, state-space | Moderate | Medium–High | Medium | Medium | Moderate | High |
| **Multi-taper spectral estimation** | Non-parametric, spectral | Moderate | Low–Medium | Low–Medium | High | Good | Moderate |
| **Instantaneous frequency (Hilbert)** | Analytic signal | Moderate (needs pre-filtering) | Low | Low | High | Excellent | Moderate |
| **Reassigned STFT / synchrosqueezing** | Non-parametric, TF | Moderate–Good | Medium | Medium | Medium | Moderate | Moderate |
| **MUSIC / ESPRIT** | Parametric, subspace | Moderate (fragile model order) | Medium | Medium | Low | Moderate | Moderate |
| **VMD source separation** | Decomposition | Good for isolating components | Medium | Medium–High | Medium | Good | Moderate |
| **RELAX iterative sine fitting** | Parametric, iterative | Moderate | Medium | Medium–High | Medium | Moderate | Moderate |
| **Low-rank / rank reduction (SVD)** | Matrix, pre-processing | Good | High | High | Low | Poor | Moderate |
| **Graph-based harmonic selection (GHSA)** | Graph theory, combinatorial | Good | High | Medium | Low | Poor near-term | High |
| **Bykhovsky-Cohen joint MLE** | Parametric, theoretical | Excellent | Very high | High | Low | Poor near-term | High |

**Guidance for selecting from this table:**

- For **exact-time recovery**, prioritize the methods in the "High" column on the right: DP ridge tracking, HRFA + GHSA combination, and Kalman tracking.
- For **near-term improvements** with least refactoring: SNR-weighted spectrum combining and multi-taper estimation.
- For **preprocessing** before any estimator: RFA and/or VMD.

---

## E. Concrete Recommended Experiments

### Experiment 1 — SNR-weighted spectrum combining (replace fusion step)
**Code stage:** `qifft_extract()` — the multi-harmonic fusion section  
**Test samples first:** `bose_headphones_near_fan`, `macbook_pro_bathroom_exhaust_fan` (exact-timestamp cases that score "plausible but wrong window")  
**What to implement:** For each frame and each harmonic, estimate the local SNR in the harmonic band (signal energy / noise floor estimate). Combine per-harmonic QIFFT estimates using weights proportional to local SNR, not fixed harmonic preference. The noise floor can be estimated from bins 3–10 Hz away from the harmonic center.  
**Success:** The top match moves closer to the known correct UTC window on exact-timestamp samples.  
**Failure:** The SNR estimator itself is noisy and the combined estimate is worse than h2 alone. In that case, fall back to computing SNR over a longer smoothing window (e.g., 10-frame rolling average before using as weight).

### Experiment 2 — Dynamic programming frequency tracker (replace per-frame peak-pick)
**Code stage:** Replace or augment the per-frame peak-picking in `qifft_extract()` with a two-pass DP ridge tracker operating on the full spectrogram.  
**Test samples first:** The entire exact-timestamp benchmark, especially `Microwave`, `Transformer`, `DFOR661` — cases most likely dominated by competing narrowband sources.  
**Algorithm:**
1. Build the magnitude spectrogram for the filtered signal (shape: `n_frames × n_freq_bins`).
2. Restrict to the ENF band (e.g., bins corresponding to 59.5–60.5 Hz).
3. Run Viterbi DP forward: `cost[t, f] = energy[t, f] + max_f'(cost[t-1, f'] - lambda * |f - f'|²)` where `lambda` controls the smoothness prior.
4. Backtrack the optimal path.
**Success:** On exact-timestamp samples, the top match UTC window is within ±60 seconds of the known correct window.  
**Failure indicator:** The DP path still locks onto a non-ENF tone that is persistent across all frames (e.g., a constant fan speed). If this happens, the RFA pre-enhancement experiment (Experiment 3) is a prerequisite.

### Experiment 3 — RFA pre-enhancement before estimation
**Code stage:** Insert a new function `rfa_enhance(signal, sr, nominal, harmonic)` between `bandpass_filter()` and `qifft_extract()` in `main()`.  
**Algorithm sketch:** The RFA (Hua 2019) operates by modeling the signal in the narrowband as a sinusoidal component plus additive noise, estimating and iteratively removing the noise component using an adaptive predictor. A simplified but effective version is a **narrowband adaptive notch enhancement** using scipy's IIR adaptive notch or a NLMS algorithm targeting the ENF band.  
**Test samples first:** Short recordings where the ENF is weakest (`short_audio`, `Microwave`).  
**Success:** The input SNR in the ENF band increases by ≥3 dB (measurable from the band's spectral power ratio before vs. after).  
**Failure:** Adaptive filter diverges or introduces artifacts. Fix: reduce step size or switch to a fixed-width notch centered on the running QIFFT estimate.

### Experiment 4 — Multi-taper PSD vs. current FFT
**Code stage:** Replace `np.fft.rfft(frame * window, n=n_padded)` in `qifft_extract()` with a multi-taper power spectrum estimate.  
**Implementation:** Use DPSS (Discrete Prolate Spheroidal Sequence) tapers from `scipy.signal.windows.dpss(frame_len, NW=3, Kmax=5)`. Average the K taper spectra. Find the peak in the averaged PSD. Apply QIFFT on the averaged spectrum.  
**Test samples first:** The full hard-sample benchmark, since multi-taper reduces spectral leakage broadly.  
**Success:** Average RMSE vs. reference grid improves on the exact-timestamp samples.  
**Failure:** Multi-taper degrades frequency resolution for short frames (the time-bandwidth product limits how narrow the main lobe can be). If so, only use multi-taper for frames ≥ 2 seconds.

### Experiment 5 — Instantaneous frequency extraction for clean-enough recordings
**Code stage:** Add `--method hilbert` option to `enf_extract.py`. After bandpass filtering, compute `scipy.signal.hilbert(filtered)`, extract instantaneous phase, unwrap, differentiate, and divide by 2π for instantaneous frequency. Apply median smoothing more aggressively.  
**Test samples first:** `fan.wav` (the known-good case) as a sanity check; then `bose_headphones_near_fan` which presumably has reasonable ENF pickup from the fan.  
**Success:** IF-based extraction produces a trace that matches the reference equally well or better than QIFFT on the known-good case.  
**Key caveat:** IF is extremely noise-sensitive. The bandpass must be narrower (bandwidth 0.2 Hz or less) before IF estimation is usable. Otherwise IF jumps are dominated by noise phase derivatives.

---

## F. Implementation Sketches

### F.1 — SNR-Weighted Spectrum Combining (Python/NumPy)

```python
def snr_weighted_combine(
    signal: np.ndarray,
    sr: int,
    nominal: float,
    harmonics: list[int],
    frame_len: int,
    hop: int,
    pad_factor: int,
    bandwidth: float = 0.5,
    noise_guard_hz: float = 3.0,  # Hz away from harmonic to estimate noise floor
) -> tuple[np.ndarray, np.ndarray]:
    """
    Spectrum-combining ENF estimator.
    For each frame, compute per-harmonic local SNR and weight QIFFT estimates.
    Returns (timestamps, freq_estimates_at_60Hz_fundamental).
    """
    n_padded = frame_len * pad_factor
    freq_res = sr / n_padded
    window = np.hanning(frame_len)
    n_frames = max(1, (len(signal) - frame_len) // hop + 1)

    timestamps = np.empty(n_frames)
    freq_estimates = np.empty(n_frames)

    for i in range(n_frames):
        start = i * hop
        frame = signal[start: start + frame_len] * window
        spectrum = np.abs(np.fft.rfft(frame, n=n_padded))

        weighted_freq_sum = 0.0
        weight_sum = 0.0

        for h in harmonics:
            target = nominal * h
            # Signal bins in [target - bandwidth, target + bandwidth]
            b_lo = int(np.floor((target - bandwidth) / freq_res))
            b_hi = int(np.ceil((target + bandwidth) / freq_res))

            # Noise floor: bins in [target ± (bandwidth + guard)]
            # excluding the signal band
            n_lo = int(np.floor((target - bandwidth - noise_guard_hz) / freq_res))
            n_hi_left = b_lo - 1
            n_lo_right = b_hi + 1
            n_hi = int(np.ceil((target + bandwidth + noise_guard_hz) / freq_res))

            signal_power = np.mean(spectrum[b_lo:b_hi + 1] ** 2)
            noise_bins = np.concatenate([
                spectrum[max(0, n_lo):max(0, n_hi_left)],
                spectrum[min(len(spectrum), n_lo_right):min(len(spectrum), n_hi)],
            ])
            noise_power = np.mean(noise_bins ** 2) if len(noise_bins) > 0 else 1e-12
            snr = signal_power / max(noise_power, 1e-12)

            # QIFFT peak in signal band
            k = b_lo + np.argmax(spectrum[b_lo:b_hi + 1])
            if 1 <= k < len(spectrum) - 1:
                a, b, g = spectrum[k - 1], spectrum[k], spectrum[k + 1]
                denom = a - 2 * b + g
                delta = 0.5 * (a - g) / denom if abs(denom) > 1e-12 else 0.0
            else:
                delta = 0.0

            f_harmonic = (k + delta) * freq_res
            f_fund = f_harmonic / h

            # SNR as weight (or use log-SNR for stability)
            snr_weight = max(snr, 0.0)
            weighted_freq_sum += snr_weight * f_fund
            weight_sum += snr_weight

        freq_estimates[i] = weighted_freq_sum / weight_sum if weight_sum > 1e-12 else nominal
        timestamps[i] = (start + frame_len / 2) / sr

    return timestamps, freq_estimates
```

---

### F.2 — Dynamic Programming Ridge Tracker (Python/NumPy)

```python
def dp_ridge_track(
    signal: np.ndarray,
    sr: int,
    nominal: float,
    frame_len: int,
    hop: int,
    pad_factor: int,
    bandwidth: float = 0.5,
    smoothness_lambda: float = 2e6,  # penalizes freq jumps; tune this
) -> tuple[np.ndarray, np.ndarray]:
    """
    Viterbi-style dynamic programming tracker over the STFT spectrogram.
    Returns (timestamps, freq_estimates).
    """
    n_padded = frame_len * pad_factor
    freq_res = sr / n_padded
    window = np.hanning(frame_len)
    n_frames = max(1, (len(signal) - frame_len) // hop + 1)

    b_lo = int(np.floor((nominal - bandwidth) / freq_res))
    b_hi = int(np.ceil((nominal + bandwidth) / freq_res))
    n_bins = b_hi - b_lo + 1

    # Build magnitude spectrogram over ENF band (shape: n_frames x n_bins)
    spec = np.zeros((n_frames, n_bins))
    for i in range(n_frames):
        start = i * hop
        frame = signal[start: start + frame_len] * window
        mag = np.abs(np.fft.rfft(frame, n=n_padded))
        spec[i] = mag[b_lo: b_lo + n_bins]

    # Normalize each frame so energy doesn't dominate over shape
    for i in range(n_frames):
        rms = np.sqrt(np.mean(spec[i] ** 2))
        if rms > 1e-12:
            spec[i] /= rms

    # DP forward pass
    # cost[t, b] = best cumulative score to reach bin b at time t
    cost = np.full((n_frames, n_bins), -np.inf)
    backptr = np.zeros((n_frames, n_bins), dtype=int)
    cost[0] = spec[0]

    for t in range(1, n_frames):
        # Transition cost: penalize frequency jumps
        # Efficient: vectorized over (prev_bin, curr_bin) pairs
        for b in range(n_bins):
            # penalty = lambda * (b - prev_b)^2 in bin units
            prev_bins = np.arange(n_bins)
            transitions = cost[t - 1] - smoothness_lambda * (freq_res ** 2) * (b - prev_bins) ** 2
            best_prev = np.argmax(transitions)
            cost[t, b] = transitions[best_prev] + spec[t, b]
            backptr[t, b] = best_prev

    # Backtrack
    path = np.zeros(n_frames, dtype=int)
    path[-1] = np.argmax(cost[-1])
    for t in range(n_frames - 2, -1, -1):
        path[t] = backptr[t + 1, path[t + 1]]

    freq_estimates = (b_lo + path) * freq_res
    timestamps = np.array([(i * hop + frame_len / 2) / sr for i in range(n_frames)])
    return timestamps, freq_estimates
```

**Note on `smoothness_lambda`:** This is the key hyperparameter. A value of `2e6` with `freq_res ≈ 0.001 Hz` and 0.5 s hops means a 0.01 Hz jump (just 10 bins at 0.001 Hz/bin) costs ~2 points on the normalized scale. Start with 1e5–1e7 and tune against your `fan.wav` known-good sample. Higher lambda = smoother but slower to follow true ENF variations; lower = allows more jumps, which can re-introduce noise.

**Vectorized version** (avoid the inner loop over `b`):
```python
# Vectorized DP step (n_bins x n_bins transition matrix)
for t in range(1, n_frames):
    prev = cost[t - 1]  # shape: (n_bins,)
    bin_idx = np.arange(n_bins)
    # transition matrix [b, prev_b]
    jump_penalty = smoothness_lambda * (freq_res ** 2) * (bin_idx[:, None] - bin_idx[None, :]) ** 2
    trans = prev[None, :] - jump_penalty  # shape: (n_bins, n_bins)
    best = np.argmax(trans, axis=1)
    cost[t] = trans[np.arange(n_bins), best] + spec[t]
    backptr[t] = best
```

---

### F.3 — Multi-Taper Spectral Estimation Drop-In

```python
from scipy.signal.windows import dpss

def multitaper_spectrum(frame: np.ndarray, n_padded: int, NW: float = 3.0, K: int = 5) -> np.ndarray:
    """
    Compute multi-taper PSD estimate for a single frame.
    Returns magnitude spectrum (sqrt of power) of length n_padded//2 + 1.
    """
    N = len(frame)
    tapers, _ = dpss(N, NW, Kmax=K, return_ratios=True)
    # tapers shape: (K, N)
    power = np.zeros(n_padded // 2 + 1)
    for taper in tapers:
        windowed = frame * taper
        fft_result = np.fft.rfft(windowed, n=n_padded)
        power += np.abs(fft_result) ** 2
    power /= K
    return np.sqrt(power)  # return magnitude for compatibility with existing QIFFT code
```

Drop this into `qifft_extract()` by replacing:
```python
spectrum = np.abs(np.fft.rfft(frame, n=n_padded))
```
with:
```python
spectrum = multitaper_spectrum(frame, n_padded, NW=3.0, K=5)
```
No other changes needed. The QIFFT step that follows is unchanged.

---

### F.4 — Instantaneous Frequency Extraction

```python
from scipy.signal import hilbert, butter, sosfiltfilt

def instantaneous_frequency_enf(
    signal: np.ndarray,
    sr: int,
    nominal: float,
    bandwidth: float = 0.2,  # narrower than QIFFT; required for IF stability
    smooth_window: int = 5,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Instantaneous frequency ENF estimator via Hilbert transform.
    """
    lo, hi = nominal - bandwidth, nominal + bandwidth
    sos = butter(6, [lo, hi], btype='bandpass', fs=sr, output='sos')
    filtered = sosfiltfilt(sos, signal)

    analytic = hilbert(filtered)
    instantaneous_phase = np.unwrap(np.angle(analytic))
    inst_freq = np.diff(instantaneous_phase) / (2 * np.pi) * sr  # in Hz

    # Downsample to 1 Hz by median over 1-second windows
    n_seconds = len(inst_freq) // sr
    timestamps = np.arange(n_seconds) + 0.5
    freq_estimates = np.array([
        np.median(inst_freq[s * sr: (s + 1) * sr])
        for s in range(n_seconds)
    ])
    return timestamps, freq_estimates
```

---

### F.5 — Simple Per-Frame SNR Confidence Score

```python
def frame_snr_db(spectrum: np.ndarray, b_lo: int, b_hi: int, guard_bins: int = 30) -> float:
    """Estimate per-frame in-band SNR in dB for use as confidence weight."""
    signal_power = np.mean(spectrum[b_lo:b_hi + 1] ** 2)
    noise_lo = max(0, b_lo - guard_bins)
    noise_hi_r = min(len(spectrum), b_hi + guard_bins)
    noise_samples = np.concatenate([
        spectrum[noise_lo:b_lo],
        spectrum[b_hi + 1:noise_hi_r],
    ])
    noise_power = np.mean(noise_samples ** 2) if len(noise_samples) > 0 else 1e-12
    return 10 * np.log10(signal_power / max(noise_power, 1e-12))
```

Use this to emit a `confidence_score` column in the output CSV. Frames with SNR < −10 dB should be flagged and optionally excluded from the matching step.

---

## G. Research Gaps and Open Questions

### G.1 — The True Nature of Your Hard Samples

The benchmarking shows "same-day match but wrong time window." This could be caused by one of two distinct failure modes with different remedies:

**Scenario A:** The recording contains *no usable ENF* — the extracted trace is structured noise (fan harmonics, motor tones) that happens to have a similar coarse frequency range to the ENF. In this case, no extraction algorithm will recover the correct time window because the ENF is genuinely not present.

**Scenario B:** The recording contains *weak but present ENF* mixed with stronger interference. The interference dominates the per-frame peak-pick and smears the ENF trace. In this case, better tracking (DP, Kalman) or better preprocessing (RFA, VMD) should help.

It is not currently possible to distinguish these cases without ground-truth. **Recommendation:** For each hard sample, compute the spectrogram at the known correct UTC start time and visually inspect whether the ENF is even faintly visible as a ridge in the 59.5–60.5 Hz band. If it is not visible at all, the recording likely falls in Scenario A. Only samples in Scenario B will benefit from better algorithms.

### G.2 — Smoothness Prior Calibration

The DP tracker's `smoothness_lambda` must be calibrated to real ENF dynamics. The actual EI grid's ENF fluctuates at most ≈ ±0.05 Hz over a 1-second period. Over a 0.5-second hop, the expected maximum change is ≈ 0.025 Hz. Setting `lambda` such that a jump of 0.025 Hz incurs a small cost and a jump of 0.1 Hz incurs a large cost is the target. This requires empirical calibration against your known-good `fan.wav` sample.

### G.3 — The HRFA/GHSA Complexity Trade-off

The Hua et al. (2021) HRFA + GHSA system is state-of-the-art but involves solving a maximum weight clique problem, which is NP-hard in general. The practical Bron-Kerbosch implementation works well for small cliques (5–10 harmonics), but implementation complexity is significant. **A useful shortcut:** instead of the full GHSA, a simpler thresholding approach (drop any harmonic whose SNR is below 0 dB) often recovers most of the gain at much lower implementation cost.

### G.4 — Compression Artifacts (Noted in Project Goals)

MP3 and AAC compression apply frequency-domain quantization that can destroy the phase coherence of weak narrowband signals. ENF at low SNR in compressed recordings may be further damaged. The literature (Rodríguez et al., 2013 EUSIPCO; ENF MP3 robustness paper, *Circuits, Systems, Signal Processing* 2018) suggests that harmonic 2 is more robust to compression than harmonic 1, which is consistent with your current default. This open question should be part of your benchmark: record known-good samples and test with various compression settings.

### G.5 — Reference Grid Sampling Rate Mismatch

Your FNET data samples the grid at approximately 1 sample/38.6 s (image collection) → after extraction, at approximately 1 sample/1 s. The ENF varies on sub-second timescales. If the FNET reference CSV has insufficient temporal resolution, even a perfect extraction from audio cannot match at the 5-second window level. It would be worth verifying whether FNET publishes higher-resolution grid data through their API.

---

## Final Answer: Three Pipelines to Try, In Order

### Pipeline 1 (Try First — Minimal Code Change)

**SNR-weighted spectrum combining + multi-taper PSD**

Replace the fixed harmonic weighting fusion in the existing multi-harmonic mode with per-frame SNR-weighted combining (Section F.1). Simultaneously replace the single Hann-windowed FFT with the multi-taper PSD (Section F.3). These two changes together address the most likely cause of wrong-harmonic selection and improve spectral leakage rejection.

*Why first:* Both changes are drop-in replacements of ≤ 50 lines. No architectural changes to `enf_extract.py`. Risk is low. If the hard samples improve, it confirms that the fusion + spectral estimation steps were the dominant failure mode.

*Expected result:* Moderate improvement on exact-timestamp cases. The same-day match score may climb but exact-window recovery is not guaranteed without continuity constraints.

---

### Pipeline 2 (Try Second — Core Fix for Exact-Time Recovery)

**Multi-taper + SNR-weighting across harmonics + DP ridge tracker**

Build on Pipeline 1 by replacing the per-frame peak-pick with the DP tracker (Section F.2). Use the combined multi-harmonic spectrogram (SNR-weighted sum of per-harmonic spectrograms) as input to the DP, not just a single-harmonic spectrogram. The DP forces temporal continuity across the full recording, which is the key mechanism that converts "plausible but wrong window" matches into correct-window matches.

*Why second:* More code than Pipeline 1 but directly targets the identified failure mode. The DP tracker is the method with the clearest published evidence of working at genuinely low ENF SNR (AMTC paper, −8.2 dB test).

*Expected result:* Should produce meaningful improvement on Scenario B samples (weak-but-present ENF). Exact-time recovery rate on the benchmark should improve.

---

### Pipeline 3 (Try Third — Preprocessing-First Approach)

**VMD + RFA pre-enhancement → SNR-weighted spectrum combining → DP tracker**

Add a full preprocessing stage before any frequency estimation. Apply VMD to isolate components near the ENF harmonics, then run RFA on each isolated component to further suppress noise. Feed the enhanced signal into Pipeline 2.

*Why third:* Most complex, requires external libraries (`vmdpy`), and involves tuning of VMD parameters (number of modes, bandwidth constraint). But for recordings that are genuinely hostile (fan + speech + HVAC), this may be the only path to recovering usable ENF. The published ENR method (2024 Scientific Reports) reports measurable accuracy improvement specifically from combining VMD with RFA.

*Expected result:* If the hard samples contain any ENF at all, this pipeline should have the best chance of recovering it. Failure here strongly suggests the samples are Scenario A (no recoverable ENF).

---

*Report ends. All citations are to publicly accessible papers. Recommended reading order for implementation: [3] (spectrum combining), [10] (AMTC), [8] (HRFA), [7] (practical issues). The MAST Lab page (https://mast.umd.edu/research.php?t=enf) also links to the spectrum-combining dataset and code.*