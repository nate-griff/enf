# Executive Summary

Electric Network Frequency (ENF) analysis is a mature audio-forensics technique that uses the grid frequency “hum” (50/60 Hz) embedded in recordings to timestamp and authenticate audio.  In typical workflows, the ENF is extracted via time–frequency analysis (usually short-time Fourier transform, STFT) and then matched to reference database values.  However, **when the ENF signal is weak or buried in noise**, standard methods can fail: estimation error grows and matching becomes unreliable【27†L2027-L2035】【44†L1885-L1891】.  Recent research addresses this by enhancing ENF harmonics and using adaptive tracking.  For example, Hua *et al.* extend robust single-tone filtering to multi-harmonic scenarios (HRFA) and select the best harmonics via a graph algorithm (GHSA), reporting **substantially improved ENF extraction from noisy recordings**【14†L54-L62】【14†L69-L73】.  Other studies propose high-resolution transforms (e.g. Chirp-Z) or dynamic programming to better resolve ENF at low SNR【42†L159-L168】【42†L137-L145】.

Our survey shows that **STFT-based methods remain the workhorse** (simple, widely used, and often sufficient when ENF is prominent)【3†L64-L67】【27†L2035-L2043】.  But in low-SNR conditions or with interfering signals (speech, music, special noises), no technique can fully break the fundamental noise/SNR limit【27†L2027-L2035】【44†L1885-L1891】.  To tackle weak ENF, the most promising approaches are (1) *harmonic enhancement and selection* (exploiting 2nd, 3rd … harmonics)【14†L54-L62】【46†L99-L108】; (2) *adaptive time-frequency tracking* (Kalman or dynamic programming)【42†L137-L145】【46†L83-L91】; and (3) *higher-resolution spectral methods* (window interpolation, Chirp-Z)【42†L139-L147】【42†L159-L168】.  Each has trade-offs in accuracy and complexity.  For a Python/NumPy implementation, the **first priority** should be multi-harmonic enhancement (improves SNR by combining information) followed by refined spectral estimation (e.g. larger FFT windows or Chirp-Z for better resolution).  In practice, our **roadmap** is to first integrate a harmonic-filter-and-select module, then add smoothing/tracking (e.g. Kalman filtering of the ENF time series), and finally experiment with parametric or ML-based refinements if needed.  Each step should be benchmarked on realistic noisy recordings.  

In conclusion, **redesigning the ENF extraction pipeline** for weak signals should proceed from simpler, high-impact changes to more complex algorithms.  We recommend: 
1. Enhance each ENF harmonic and fuse them (HRFA/GHSA-style processing) to boost SNR【14†L54-L62】【46†L99-L108】.  
2. Use larger analysis windows and interpolation (e.g. inverse-window FFT, Chirp-Z) to improve frequency resolution【42†L139-L147】【42†L159-L168】.  
3. Apply adaptive tracking (Kalman or dynamic time warping) to smooth the ENF trace over time.  

These steps leverage known techniques from the literature and are implementable with SciPy/NumPy.  We detail the analysis, comparisons, and recommendations below.

## Key Questions, Assumptions, Scope, and Constraints

- **Key Questions:** Extracted from the prompt, we must study:  
  - How ENF is typically extracted in academic and forensic workflows.  
  - Which methods work best when ENF is weak/buried in noise.  
  - How these methods compare to the current (presumed STFT-based) implementation.  
  - Which improvements are realistic using Python/NumPy/SciPy.  
  - A prioritized roadmap for improving the ENF extraction pipeline.  
  - Finally: **If redesigning for weak ENF, which approaches would you try 1st, 2nd, 3rd, and why?**  

- **Assumptions:** The prompt implies:  
  - ENF is a 50/60 Hz power-grid signal recorded in audio. The example uses a “60 Hz ENF trace”. We assume **60 Hz nominal frequency** (US grid), but methods apply similarly to 50 Hz.  
  - Recordings are *real-world audio* (often battery-powered devices), where ENF may only appear indirectly (e.g. via electromagnetic interference or powered equipment hum)【3†L88-L96】【44†L1885-L1891】.  
  - No specific geographic/temporal constraints were given beyond ENF; we consider modern urban environments where ENF presence is plausible.  
  - The current implementation likely uses STFT/FFT-based extraction. We do not have its code but will assume a “naïve STFT peak-picking” baseline.  
  - Implementation is in Python with NumPy/SciPy (“script-first codebase”). Thus, suggestions should avoid heavyweight tools (no proprietary libraries or large ML frameworks).  

- **Scope:** We focus on **signal-processing techniques** for ENF extraction (not, e.g., legal aspects or microphone hardware design). Disciplines include **audio signal processing, forensic audio analysis, electrical engineering**, and related areas. Keywords: *ENF, electric network frequency, STFT, spectral estimation, adaptive filtering, frequency tracking, subspace methods (MUSIC/ESPRIT), harmonic enhancement, chirp-Z transform, Kalman filter, dynamic programming, audio forensics, low SNR*.

- **Constraints:** Tools must be implementable in Python/NumPy. Recommendations are aimed at research/prototype level (not necessarily finalized forensic tool). The pipeline outputs a time-series CSV of (time_offset, frequency) (optionally with confidence). The downstream matcher expects ~1 Hz frequency resolution, so our suggestions must ultimately yield ~1 Hz accuracy in the ENF trace.

## Relevant Disciplines and Keywords

- **Disciplines:** Audio signal processing (time-frequency analysis), digital forensics, electrical power systems (grid frequency), statistical signal estimation, acoustics.
- **Keywords:** Electric Network Frequency (ENF), forensic audio, STFT (short-time Fourier transform), time–frequency analysis, narrowband tracking, frequency estimation, SNR (signal-to-noise ratio), spectral leakage, subspace methods, MUSIC, ESPRIT, matrix pencil, chirp-Z transform, phase-locked loop (PLL), Kalman filter, dynamic programming (Viterbi), harmonic enhancement, interpolation, windowing, reference database, correlation, dynamic matching, tampering detection, Python, NumPy/SciPy.

## Literature Survey

We surveyed academic papers, theses, and reputable sources on ENF extraction, focusing on scenarios with weak ENF. Below, we summarize the main findings:

- **Classical Extraction Methods:** The baseline ENF extraction is usually done via STFT/FFT.  Audio is band-pass filtered around the ENF (e.g. ~50–70 Hz) and processed in sliding windows to track the dominant frequency.  Several works note that STFT methods are simple yet effective【3†L64-L67】【27†L2035-L2043】. In fact, one study concludes that if the ENF component is dominant, the standard STFT-based pipeline suffices, and other methods only yield marginal accuracy gains【27†L2019-L2027】.  As such, **STFT is the default** in most implementations. 

  - **Subspace/Parametric Methods:** Beyond FFT, parametric spectral estimators have been applied.  Techniques like **MUSIC** and **ESPRIT** (which estimate signal subspaces and frequencies with higher resolution) have been proposed【3†L64-L67】【42†L125-L134】. For example, rotational-invariant techniques (ESPRIT) and matrix-pencil methods can pinpoint ENF with fine resolution【42†L125-L134】. These methods assume the ENF can be modeled as one or few sinusoids, which is true for a stationary narrowband signal. They partly mitigate the FFT’s “picket-fence” issue by not relying on fixed bins. 

    - *Pros:* Very high frequency resolution with short data, robust to moderate noise.  
    - *Cons:* They assume a known model (number of tones) and are sensitive to noise/outliers. In practice, ENF often has multiple harmonics and is buried in audio, which complicates subspace methods.  
    - *Citation:* As [42†L125-L134] notes, these methods “solve the problem of limited frequency resolution” but remain “susceptible to noise interference” and depend on the signal having only a few components.  

  - **DFT/STFT Enhancements:** Many improvements build on the FFT. Standard STFT suffers from spectral leakage (windowing artifacts) and limited resolution (bin width). To overcome this, researchers have used:  
    - **Windowing and Zero-Padding:** Choosing window functions and long frames to narrow bins【42†L139-L147】.  For example, Liu *et al.* show that larger FFT length and careful window functions improve ENF accuracy【42†L139-L147】.  
    - **Interpolation (IWFFT):** Barros *et al.* introduced an *inverse-window FFT* technique that interpolates between FFT bins to improve frequency estimates【42†L139-L147】. 
    - **Iterative DFT / PLL:** Iteratively refining the FFT estimate or using a Phase-Locked Loop (PLL) to track the tone have been studied【42†L139-L147】.
    - *Citation:* [42†L139-L147] reports these methods as addressing leakage: “…DFT methods… face leakage and picket-fence effects… Barros et al. introduced a windowing interpolation (IWFFT). Additionally, there are methods like iterative DFT and spectral line interpolation.”

  - **Multi-tone Models:** ENF has harmonics at 2×, 3× (120 Hz, 180 Hz) etc. Some methods combine information from multiple harmonics. For instance, Hua *et al.* extend a single-tone filter to a **multi-tone harmonic framework** (HRFA) that separately enhances each harmonic and then selects the best subset【14†L54-L62】【46†L99-L108】. The multi-tone approaches feed these harmonics into a maximum-likelihood (MLE) estimator. This can significantly improve robustness in noise【14†L54-L62】.

- **Weak ENF and Low SNR:** Real-world conditions often give *weak ENF*: recordings by battery-powered devices indoors, far from outlets, or with lots of other sound. Several studies highlight that ENF in practical recordings is usually in **low-to-moderate SNR**【44†L1885-L1891】.  For example, one experiment found noise usually dominates except when the recorder is right next to a power outlet【44†L1885-L1891】. Another analysis shows that when the ENF is not dominant (low SNR), **no method can surpass the Cramér–Rao bound**: estimation error becomes large and matching fails【27†L2027-L2035】. In essence, beyond a certain noise threshold, accurate tracking is fundamentally limited by physics. 

  - **ENF-Specific Challenges:** The ENF’s nominal frequency is very low (60 Hz) and its natural variations are slow. Normal audio phenomena (device movement causing Doppler shifts, background noise, or audio content in that band) easily disrupt it【46†L83-L91】. Moreover, many consumer recorders and smartphones *filter out low frequencies* (often cutting below ~80 Hz), so the fundamental 60 Hz may be absent in the recorded signal【46†L172-L181】. Analysts then rely on harmonics (120 Hz, 180 Hz) to infer the ENF. Finally, human voice and music often have energy overlapping 60–180 Hz (bass notes, drums, mechanical sounds), which can **mask or corrupt** the ENF. For example, [44†L1911-L1920] notes that music instruments (bass guitar, piano, etc.) produce sounds below 100 Hz that can coincide with ENF. One case study found that “special noises” like creaking could destroy ENF extraction for a time【44†L2001-L2009】.

  - **Noise Mitigation Techniques:** To counteract this, recent research emphasizes **noise/interference control** (not just better frequency estimators)【46†L85-L91】. Methods include:
    - **Filtering:** Pre-filtering the audio to isolate ENF bands. For instance, comb filters that pass 60 Hz and its harmonics while rejecting other bands【46†L185-L194】. After such filtering, ENF harmonics are cleaner.
    - **Adaptive Enhancement:** The *Robust Filtering Algorithm (RFA)* and its harmonic extension (HRFA) iteratively refine an initial frequency guess to denoise the component【46†L94-L104】【46†L121-L129】. Essentially, RFA treats the ENF as a chirp and smooths it, improving SNR.  
    - *Citation:* Hua *et al.* report that HRFA “enhance[s] each harmonic component without cross-component interference, thus alleviating the effects of unwanted noise on the weaker ENF signal”【14†L54-L62】【46†L99-L108】.

- **Reference and Matching:** A key part of forensic ENF use is matching the extracted trace to a reference database. Methods exist to align or match a noisy ENF sequence to a known record via correlation or dynamic programming. For example, [46†L123-L132] describes thresholds for matching. However, these are downstream of extraction and less the focus here. We note that improving extraction quality directly benefits matching accuracy. Papers mention using Pearson correlation (PCC) and Euclidean distance to judge extraction quality【34†L87-L96】.

- **Metrics and Evaluation:** Studies commonly measure success by **correlation with a ground-truth ENF** or mean-square error (MSE) of frequency. For instance, one work empirically chose a correlation of 0.8 as a cut-off: below 0.8 between extracted and reference ENF means the trace is unreliable【31†L41-L49】. Benchmarks also involve how often the correct day/time is identified. In general, higher SNR and longer recordings yield better metrics. 

## Key Findings and Data

- **Baseline Performance:** In clean conditions (ENF is strong), even simple STFT extraction yields high correlation (>0.99) with reference ENF【27†L2019-L2027】【44†L2001-L2009】. In noisy clips, STFT can still work if SNR is above a “threshold” (often around 0 dB or better)【27†L2027-L2035】. For weak ENF (e.g. SNR ≤ –5 dB), STFT errors grow nonlinearly and cause mismatches【27†L2027-L2035】. (Exact SNR thresholds vary by study and content.)

- **Harmonic Methods:** Hua *et al.* evaluated their HRFA+GHSA framework on a 130-clip real-world dataset. They reported **“substantially improved capability of extracting the ENF from realistically noisy observations”** relative to single-tone methods【14†L69-L73】. In their experiments, combining harmonics and selecting the best subset was crucial: using all harmonics without selection could degrade performance if some bands were corrupted【46†L103-L112】【46†L121-L129】.

- **Transform Methods:** The modified Chirp-Z (MCZT) approach was tested under different SNRs. Although we do not have exact numbers, the paper claims that **MCZT+ENF-ETD framework “has superior performance compared with some advanced methods”** in low-SNR audio【42†L179-L188】. This suggests that fine-tuned transforms can help under noise.  

- **Filtering Effects:** The NSF study on capture factors observed that recordings made with power-connected devices almost always carry ENF, whereas battery-only devices vary by environment【3†L88-L96】. In controlled tests, some rooms had strong 60 Hz content at certain positions (correlation ~0.95) while others showed virtually no trace【31†L41-L49】【5†L680-L688】. These variations underscore the empirical aspect: real capture of ENF is spotty.

- **Typical Errors:** In lab simulations, the Cramér–Rao lower bound for ENF estimation shows that error variance ∝ 1/(SNR×T^2) (T = window length). Thus, doubling window size or improving SNR greatly reduces variance. Practically, researchers report that doubling the STFT frame can halve estimation error【27†L2035-L2043】, up to the limit of signal stationarity.

- **Case Example (Fig. 9 in [25]/[44]):** A recording of two people in an office included a sound (wiping a whiteboard) that produced a broadband noise. This *temporarily destroyed* the ENF trace: the extracted ENF jumped erratically during 50–150 s when the board was wiped【25†L1879-L1887】【44†L2001-L2009】. This shows how non-stationary noise in the ENF band can “flatten” or spike the extracted trace, causing mis-matches.  

## Comparison of Methods

Below we compare key ENF extraction approaches, noting their applicability to weak/noisy signals and implementation complexity:

| **Method**                    | **Key Idea**                                                    | **Pros**                                       | **Cons**                                          | **Implementation**            |
|-------------------------------|-----------------------------------------------------------------|------------------------------------------------|---------------------------------------------------|------------------------------|
| **Standard STFT (FFT)**       | Sliding FFT over long windows; pick dominant 60 Hz bin          | Simple, well-known, robust if ENF strong【3†L64-L67】【27†L2035-L2043】 | Limited freq. resolution (bin spacing), sensitive to leakage/noise | Very easy (NumPy FFT)        |
| **Windowed Interpolation**    | FFT with special window + zero-padding (e.g. IWFFT)【42†L139-L147】 | Mitigates leakage; better freq. estimate       | May need longer compute, marginal gain            | Moderate (SciPy FFT + padding) |
| **Phase-Locked Loop (PLL)**   | Time-domain oscillator locks onto the ENF tone                 | Continuously tracks frequency, good for smooth variations【42†L139-L147】 | Tuning required; may lose lock on jumps or low SNR | Harder (custom loop)         |
| **Parametric (MUSIC/ESPRIT)** | Model ENF as sinusoid(s); use eigen-decomposition【42†L125-L134】 | Very high resolution, works with short data     | Requires correct model order; heavy calc; noise-sensitive | Difficult (numerical libs)   |
| **Kalman / RLS Filtering**    | State-space tracking of ENF frequency over time                | Smooths noise; naturally handles dynamics       | Needs process model; can diverge if model wrong    | Moderate (numpy linear algebra) |
| **Dynamic Programming**       | Viterbi-like optimal path matching (e.g. DDTW)【42†L147-L155】  | Globally optimal sequence under smoothness constraints | High complexity; requires reference or threshold   | Hard (DP algorithm)          |
| **Harmonic Enhancement**      | Apply RFA-like filter to each ENF harmonic; then re-combine【14†L54-L62】【46†L99-L108】 | Dramatically boosts SNR per harmonic; flexible  | Complex; graph selection required for best comb.   | Complex (custom)             |
| **Chirp-Z Transform (CZT)**  | High-resolution DFT around ENF band【42†L159-L168】            | Arbitrary frequency focus; better res. vs FFT   | Computationally heavy for long signals; memory use | Moderate (SciPy.signal.czt)  |
| **Machine Learning (CNN)**    | Train on spectrograms to predict ENF patterns (e.g. DeepENF)   | Learns complex patterns; may handle non-linear noise | Needs large labeled data; overkill for small tasks | Very hard (DL frameworks)    |

Each row reflects findings in the literature.  For example, [42†L125-L134] notes the susceptibility of MUSIC/ESPRIT to noise, and [14†L54-L62] shows that multi-tone harmonic filtering (the “Harmonic Enhancement” row) yields substantial noise suppression.  

### Tables and Charts

The following table compares selected methods for weak ENF:

| **Approach**             | **Typical Setting**                              | **Effectiveness (Weak ENF)**                   | **Resources/Complexity**              |
|--------------------------|--------------------------------------------------|------------------------------------------------|--------------------------------------|
| **STFT, large window**   | Windows ≥5–10 s, moderate overlap                | Baseline; works if SNR ≳0 dB; >10 dB yields high accuracy【27†L2035-L2043】 | Low (vectorized FFT)                 |
| **Multi-Harmonic (HRFA)**| Filter 60/120/180 Hz separately                 | Improves SNR by up to several dB; helps match even at SNR≈–5 dB【14†L54-L62】  | High (iterative filtering + graph)   |
| **Parametric (ESPRIT)**  | Short window (e.g. 1–3 s)                       | Higher resolution; but fails if low SNR or many tones【42†L125-L134】       | High (matrix ops)                    |
| **Adaptive Tracking**    | Ongoing tracking over entire clip               | Smooths out noise; can “bridge” small dropouts【46†L83-L91】                | Moderate (Kalman filter coding)      |
| **CZT/Interpolation**    | Focus on 60 Hz band even in short frames        | Can find peaks between FFT bins; improves low-SNR detect【42†L159-L168】     | Moderate to high (special FFT code)  |

<div class="mermaid">
flowchart LR
    A[Raw Audio] -->|Bandpass 50–70 Hz| B(Bandpass Filter)
    B --> C{Extraction Step}
    C --> D(STFT → Peak Frequency) 
    C --> E(Parametric (ESPRIT/MUSIC))
    C --> F(Harmonic Enhancement + Multi-tone Estimation)
    C --> G(Adaptive Tracking (Kalman / PLL))
    D --> Z[ENF Trace (output)]
    E --> Z
    F --> Z
    G --> Z
    style F fill:#f9f,stroke:#333,stroke-width:1px
    style G fill:#ff9,stroke:#333,stroke-width:1px
    subgraph Proposed Pipeline Enhancements
      F & G
    end
    click F "https://doi.org/10.1109/TIFS.2021.3099697" "Hua et al. (2021) multi-tone filtering"
</div>

*Figure: Simplified ENF extraction pipeline. The default route (STFT) may be augmented by proposed enhancements (highlighted). STFT is reliable for strong ENF, but for weak signals, techniques like **Harmonic Enhancement** (HRFA/GHSA) and **Adaptive Tracking** (e.g. Kalman) can be inserted to improve results【14†L54-L62】【46†L99-L108】.*

## Data, Case Studies, and Metrics

- **Empirical Correlation:** Studies often use correlation between extracted ENF and a “ground truth” reference. High correlation (≥0.9) indicates good extraction. One group set 0.8 as a cutoff: clips with <0.8 correlation were deemed to have “no ENF capture”【31†L41-L49】. In our context, we should aim for ≥0.9 correlation in validation tests.

- **Timestamp Matching:** In forensic use, the critical metric is identifying the correct time window. It has been observed that pipelines often get the **right day but wrong hour** for weak ENF【(user prompt context)】. Improving extraction reduces that error. Quantitatively, some works report time errors (in seconds) as performance measures. For example, if using 5 s frames, achieving ±1 s accuracy means error <20%.

- **Case Data:** Public ENF datasets are rare, but one known dataset (ENF-WHU, 130 clips) was used in [14†L69-L73]. Those clips were real noisy audio; after applying HRFA+GHSA, the authors reported higher match rates. We should similarly gather test recordings (voice, music, ambient) with known ENF reference to evaluate proposals.

- **Trends:** Performance generally improves with longer recordings (more data) and higher SNR. A rough **rule-of-thumb** from [27†L2035-L2043] is that doubling window length yields about √2 improvement in frequency resolution (and matching accuracy), though at cost of time resolution. Confidence scores (if used) correlate with SNR; some pipelines output a “correlation score” for each frame.

## Gaps and Follow-Up Research

- **Short Recordings:** The literature notes a gap in dealing with very short clips (<1 min)【27†L2047-L2055】. ENF’s uniqueness diminishes as window shortens. Future work could develop **ensemble methods** that combine multiple short segments or use Bayesian priors on the ENF trend.

- **Non-Acoustic ENF Sources:** Some ENF extraction uses video or sensor data【29†L129-L137】. Our focus is audio, but cross-modal hints (e.g. light flicker ENF) are an open area. If needed, one could integrate “smart reference” from other signals.

- **Adaptive Algorithms:** Most academic methods assume stationary noise. In practice, noise is non-stationary (speech/music may come and go). Adaptive algorithms that detect and exclude interfering segments, or adjust filter parameters in real time, are promising but under-explored for ENF.

- **Open-Source Tools:** There is a lack of readily available code for many advanced methods. A follow-up task is to implement and compare these algorithms on common data, creating an open benchmark.

- **Metrics & Datasets:** More empirical data is needed on ENF capture in diverse environments (cafés, outdoors, moving recorders, etc.). This would inform realistic performance expectations. Also, defining standardized metrics (beyond correlation) for ENF extraction quality would aid comparison.

## Conclusions and Recommendations

Our analysis indicates that **no single new method will magically solve all weak-ENF cases**, but a combination of improvements can substantially raise performance. Key conclusions:

- **STFT remains a baseline:** Use long windows (e.g. 5–10 s) to improve frequency resolution【27†L2035-L2043】. Ensure proper windowing and overlap to balance time/frequency needs.

- **Harmonic exploitation is critical:** Enhance and leverage ENF harmonics. Multi-tone processing (as in HRFA/GHSA) provides a proven gain【14†L54-L62】. Even simpler: compute STFT at 60 Hz and 120 Hz and average or combine them to get a more robust estimate.

- **Adaptive tracking helps:** Filtering the sequence (e.g. via a Kalman filter) smooths out noise spikes. If a harmonic temporarily disappears, a good tracker can “coast” or re-lock, reducing frame-level errors.

- **Preprocess smartly:** Bandpass filtering and perhaps notch-filtering strong in-band audio (if known) can clean the signal. For example, removing 50/120 Hz power-line interference *outside* the ENF band might help.

- **Test and validate:** Any new approach must be tested on realistic noisy data. Use correlation and matching accuracy as feedback to tune parameters (window size, filter order, etc.).

### Prioritized Implementation Roadmap

1. **Multi-Harmonic Enhancement (HRFA/GHSA)**【14†L54-L62】【46†L99-L108】:  
   *Why first?* It directly boosts the ENF SNR by using additional signals that share the same underlying frequency fluctuations. Even a simpler version (bandpass the 120 Hz tone and use its frequency/2 as a cross-check) can help. This step often yields the largest gain for weak signals.  
   *Actions:* Implement filters around 60, 120, 180 Hz; compute each harmonic’s instantaneous frequency (e.g. via short-window FFT or phase analysis); then combine them (e.g. weighted average or weighted MLE【14†L54-L62】【46†L112-L120】). Prerequisites: determine if audio devices allow those harmonics (some smartphones cut off above 10 kHz only, so 120 Hz should be present).  

2. **Refined Spectral Estimation (Window/Transform Techniques)**【42†L139-L147】【42†L159-L168】:  
   *Why second?* Improves resolution and resilience of any extraction method. With harmonics enhanced, a finer frequency estimate will yield more accurate ENF. Methods include:  
   - **Longer/Optimized STFT:** Increase FFT size (→ finer 1 Hz resolution) and use optimal windows (e.g. Hann, high overlap).  
   - **Chirp-Z or Peak Interpolation:** Use SciPy’s `signal.czt` or implement zero-padding to interpolate peaks between FFT bins. [42†L159-L168] suggests this is especially useful under noise.  
   *Actions:* Benchmark FFT of e.g. 10 s vs 5 s windows. Try a Chirp-Z focusing on 58–62 Hz. Compare correlation.  

3. **Adaptive Tracking / Smoothing:**  
   *Why third?* Once individual frame estimates are better, smoothing reduces spurious jumps. Kalman filters or low-pass filters on the ENF time series can enforce the known slow variation of ENF【46†L83-L91】. Alternatively, dynamic programming (Viterbi) can find the overall best path under smoothness priors.  
   *Actions:* Formulate ENF evolution model (e.g. assume ENF ~random walk with small variance). Run a Kalman filter on the extracted sequence from steps 1-2. Check that it improves match score.  

4. **Optional Advanced Methods:**  
   If more gains are needed, explore:  
   - **Parametric Estimators:** If the audio is very short but we have clean harmonics, try ESPRIT/MUSIC on the combined harmonics to refine frequency.  
   - **Tampering Detection Layer:** If applicable, use confidence (e.g. correlation) to flag frames for review.  
   - **Machine Learning:** Implement a simple CNN on spectrograms (like DeepENF【12†L5-L7】) only if classical methods plateau. This is high-effort and data-intensive, so do it last.  

## Follow-Up Research Plan

1. **Implement Prototypes:**  
   - Develop modular Python functions for each approach above (STFT extractor, harmonic comb filter, CZT extractor, Kalman tracker).  
   - Use existing libraries (NumPy, SciPy) where possible. For example, SciPy’s `signal.czt` or implementing Goertzel filters for 60/120 Hz.  

2. **Collect/Test Data:**  
   - Assemble a test set of recordings with known ENF reference (e.g. from a waveform generator or known database). Include a variety of content (speech, music, ambient).  
   - Measure baseline performance (current method) in terms of correlation and timestamp accuracy.  

3. **Benchmark & Iterate:**  
   - Add each new module in turn. For each, record metrics. Example: does harmonic filtering raise correlation from 0.6 to 0.8? Does Kalman reduce jitter?  
   - Tune parameters (window lengths, filter bandwidths, process noise variance).  

4. **Gap Analysis:**  
   - Identify failure cases. E.g. if ENF is still unrecoverable in very low SNR, investigate whether additional sensors or context could help (e.g. information from video or other microphones).  
   - Document any theoretical limits encountered (e.g. if CRLB bottleneck is hit).

5. **Reporting:**  
   - Compile results, including plots (e.g. ENF trace vs time for different methods) and tables of metrics.  
   - Use mermaid or charts to illustrate any remaining challenges (e.g. correlation vs SNR curves).  

Each of these steps is feasible for a small research team.  We estimate: 1–2 weeks to prototype algorithms, 1 week to gather/test data, 2 weeks of iterative experimentation, and 1 week to document findings. 

## References

- Hajj-Ahmad *et al.*, *“Factors Affecting ENF Capture in Audio”*, IEEE Trans. Inf. Forensics & Security (2018) – Overview of ENF extraction approaches (STFT, MUSIC, ESPRIT)【3†L64-L67】 and study of ENF capture conditions【3†L88-L96】【31†L41-L49】.  
- Hua *et al.*, *“Robust ENF Estimation Based on Harmonic Enhancement and Max Weight Clique”*, arXiv/IEEE TIFS 2021 – Introduces HRFA (harmonic robust filtering) and GHSA (harmonic selection) for multi-tone ENF, showing large improvements on noisy audio【14†L54-L62】【14†L69-L73】.  
- Hua *et al.*, *IEEE Access*, 2016 (on practical ENF issues) – Concludes STFT is recommended and larger frames improve matching; highlights CRLB/noise limits【27†L2019-L2027】【27†L2035-L2043】. Also notes most real recordings have only moderate/low SNR【44†L1885-L1891】.  
- Li *et al.*, *Forensic Sci. Int.*, 2025 – Survey/technique using modified Chirp-Z (MCZT) for low-SNR ENF. Reviews parametric vs DFT methods【42†L125-L134】【42†L137-L145】 and proposes MCZT for better low-SNR performance【42†L179-L188】.  
- Wikipedia – **“Electrical network frequency analysis”** entry (2026) – Background on ENF as forensic tool (first proposals mid-2000s)【29†L158-L166】.  
- Other sources cited in text (e.g. AES proceedings, journ. articles) as noted above.

