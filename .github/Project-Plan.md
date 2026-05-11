# Electric Network Frequency Analysis Tool Project Plan

## Purpose

This project aims to build a local analysis tool that can compare electric network frequency (ENF) fluctuations extracted from media against recorded grid reference data. The first target is a research-oriented prototype that helps narrow down plausible match windows for human investigation. It is intended to be an investigative aid and correlation tool, not a courtroom-grade proof system.

The tool should begin as a CLI-first workflow and then grow into a richer desktop GUI for inspection, comparison, and visualization.

## Project Scope Framework

### 1. What question am I trying to answer?

Can a local audio or video recording be compared against known 60 Hz electric grid fluctuation data from a selected North American grid to produce a ranked list of plausible timestamp matches?

For the first version, the tool should answer that question in a practical, explainable way:

- Accept a media file as input.
- Extract a usable ENF-like waveform around 60 Hz +/- 1 Hz.
- Compare that waveform against reference data from one user-selected grid.
- Rank candidate windows by match quality.
- Present enough supporting evidence that a human can inspect the result and decide whether the match is meaningful.

### 2. What artifacts do I need to answer this question?

#### Reference artifacts

- Daily CSV files already generated from the FNET image collection pipeline.
- A merged reference dataset built from those daily CSVs.
- Grid-specific reference series for the four supported 60 Hz regions:
  - EI
  - WECC
  - ERCOT
  - Quebec

#### Query artifacts

- Local audio files.
- Local video files.
- Audio extracted from video inputs during processing.

#### Derived artifacts

- Extracted ENF waveform from the query media.
- Cleaned and normalized comparison windows.
- Ranked candidate matches.
- Match scores and score breakdowns.
- Optional overlay plots for visual inspection.
- Structured output files for later review or scripting.

### 3. How will I process these artifacts in order to answer this question?

The planned processing pipeline is:

1. Accept a local audio or video file.
2. If the input is video, extract audio internally using ffmpeg.
3. Isolate ENF behavior near 60 Hz +/- 1 Hz from the media.
4. Clean and normalize the extracted waveform enough to support comparison.
5. Load the selected grid's reference data from daily CSV files or a merged dataset.
6. Compare the query waveform against candidate windows in the reference series.
7. Score candidate windows using a composite approach that includes:
   - Percent of query samples within a configurable Hz threshold from the reference window.
   - Shape similarity, likely correlation-based or another normalized similarity method.
8. Rank the best candidate windows.
9. Return the top matches and supporting artifacts for human review.

The exact matching method is intentionally not fixed yet. The project should compare multiple approaches early and choose the one that performs best across realistic recordings, different noise conditions, and explainability needs.

### 4. How will I present my answer and its support?

#### CLI output

The CLI should be the first production path. It should provide:

- Console summary of the best matches.
- User-selected grid input.
- Optional top-N output count, with a default of 3.
- Configurable threshold cutoff, with a default score threshold.
- Structured output file such as JSON or CSV.
- Optional exported overlay image comparing the query waveform against a selected match.

#### GUI output

The GUI should follow the CLI once matching works well enough to inspect visually. It should support:

- Scrollable and zoomable waveform inspection.
- Overlay of the query waveform with one candidate match at a time.
- Easy switching among top candidate matches.
- Reuse of the current viewer's interaction style for time navigation.
- Visual inspection as support for the CLI result, not as a separate analysis engine.

## Initial Product Boundary

### In scope for the first roadmap

- 60 Hz grids only.
- Four selectable North American grids: EI, WECC, ERCOT, Quebec.
- Audio and video input from the start.
- Internal audio extraction for video inputs.
- Matching within a user-selected grid rather than automatic grid inference.
- Local, cross-platform workflow.
- CLI-first delivery.
- Later desktop GUI for inspection.
- Daily CSV support plus a merged reference dataset.
- Composite scoring with threshold coverage plus waveform similarity.
- Algorithm comparison before locking the matcher design.

### Explicitly out of scope for now

- 50 Hz system support.
- Live media or live stream ingestion.
- Large-scale public-video benchmarking.
- Strong forensic or courtroom-grade claims.
- Cloud deployment or web deployment.
- Fully automated geographic grid detection.

## Core Deliverables

### CLI prototype

The first usable milestone should be a CLI tool that:

- Accepts an input media file.
- Accepts a selected grid.
- Accepts an optional top-N result count, default 3.
- Accepts an optional accuracy threshold cutoff.
- Extracts the query waveform.
- Compares it against reference data.
- Returns ranked matches.
- Exports structured results.
- Optionally exports a waveform overlay image.

### GUI follow-on tool

The GUI milestone should provide:

- Interactive inspection of the query waveform and candidate matches.
- Shared time navigation behavior inspired by the existing CSV viewer.
- One-candidate-at-a-time overlay view.
- Fast inspection of the top-ranked windows returned by the CLI pipeline.

## Technical Plan

### Reference data strategy

The project already has a useful upstream reference-data pipeline in the ImageExtraction directory. That should remain the source of truth for collecting and extracting grid fluctuation data.

For the prototype:

- Keep daily CSV files as a supported source.
- Build a merged CSV workflow for comparison jobs.
- Defer a move to SQLite or Parquet until there is a clear scale problem.

### Matching strategy

The project should not commit to one matcher too early. Instead, it should run an algorithm comparison across several candidate methods, then choose the one that best balances accuracy, stability, and explainability.

Candidate methods to compare:

- Sliding-window threshold scoring.
- Normalized cross-correlation.
- Composite scoring that combines threshold coverage and correlation.
- Normalized or detrended comparison variants if raw ENF traces prove too noisy.

Evaluation criteria for algorithm comparison:

- Stability across recordings with different background noise levels.
- Ability to surface plausible matches in the top results.
- Low false-positive behavior within a selected grid.
- Score explainability.
- Practical runtime for local use.

### Packaging strategy

This is an academic project, so it shouldn't be too many files. I want to just run it as python {filename} rather than a full packaged product. There should be a few scripts, such as the CLI, the GUI, the collection service, and the extraction service. This will probably make the files much longer, but that's ok since I want it to be a simpler to understand filestructure for non developers. 

## Reuse Of Existing Work

The current repository already contains valuable pieces that should be reused rather than replaced:

- The ImageExtraction pipeline already collects and extracts reference grid data.
- The CSV viewer already demonstrates a practical scroll and zoom interaction model.
- Existing argparse-based scripts show a workable CLI style for local tools.
- A months worth of image extraction data already exists in source_data (both the images and the CSVs).
- An audio recording from today (4/20/26 at 12:36 EST) is about 5 minutes of a recording next to a fan that should have grid ENF artifacts.

This means the project is not starting from zero. The main missing layers are:

- Query media ENF extraction.
- Reference-data merging and indexing for matching.
- Matching and scoring logic.
- A unified CLI entry point.
- A comparison-oriented GUI.

## Risks And Unknowns

The major unresolved items are research and signal-quality questions, not just engineering tasks.

### Open questions

- What minimum clip length is realistic for useful matching in noisy conditions?
- Is `0.01 Hz` the right default threshold, or should it be tuned after literature review and experiments?
- What preprocessing is needed for noisy recordings captured in different environments?
- When does merged CSV stop being sufficient and need replacement with a stronger storage model?
- Which similarity measure is most explainable while still robust?

### Project posture

This tool should be described as a lead generator and correlation aid. It can help investigators narrow down candidate windows worth manual review, but it should not be framed as an automated proof engine.

## Validation Strategy

The project does not need a hard validation gate before the prototype exists, but it does need a structured validation path.

Validation sources:

- Synthetic tests generated from reference traces.
- Known local recordings with at least approximate time and location context.
- Public web videos with generally known location and time context.

Validation goals:

- Confirm the matcher surfaces plausible results near the top.
- Measure how score quality degrades with noisy inputs.
- Compare candidate algorithms under the same test conditions.
- Learn what clip lengths are usable in practice.

## Roadmap

### Phase 1: Repository and data foundation

- [ ] Reorganize the repository into a clearer package-oriented structure.
- [ ] Preserve the existing ImageExtraction workflow as the upstream reference-data source.
- [ ] Define a reference-data schema for merged grid data.
- [ ] Create a utility to merge daily CSV files into a single comparison-ready dataset.
- [ ] Add validation for missing rows, duplicate timestamps, and inconsistent region data.
- [ ] Document how reference data is refreshed as new daily CSVs are collected.

### Phase 2: Query media ingest and ENF extraction

- [ ] Define accepted input formats for audio and video.
- [ ] Add internal audio extraction for video inputs using ffmpeg.
- [ ] Build a first-pass ENF extraction pipeline for 60 Hz media.
- [ ] Add preprocessing and normalization steps for noisy recordings.
- [ ] Export the extracted query waveform as a reusable intermediate artifact.
- [ ] Create smoke tests using a small set of local media files.

### Phase 3: Algorithm comparison and matcher design

- [ ] Define a small, repeatable comparison dataset for testing candidate matching methods.
- [ ] Implement sliding-window threshold scoring.
- [ ] Implement normalized cross-correlation scoring.
- [ ] Implement a composite score combining threshold coverage and shape similarity.
- [ ] Compare candidate methods against the same sample recordings.
- [ ] Choose the initial matcher based on accuracy, explainability, and runtime.
- [ ] Document why the selected matcher was chosen.

### Phase 4: CLI matcher prototype

- [ ] Create a unified CLI entry point for the project.
- [ ] Add required CLI arguments for input file and selected grid.
- [ ] Add optional CLI arguments for top-N results and score threshold cutoff.
- [ ] Run matching against the selected grid only.
- [ ] Return ranked candidate windows with score breakdowns.
- [ ] Export results to a machine-readable file.
- [ ] Add optional output for a waveform overlay image.
- [ ] Write user-facing CLI documentation and examples.

### Phase 5: Evaluation and tuning

- [ ] Build a small evaluation harness for repeated matching experiments.
- [ ] Test the matcher on known local recordings.
- [ ] Test the matcher on a limited set of public web videos.
- [ ] Compare performance across different noise conditions and clip lengths.
- [ ] Tune the default threshold and scoring weights using observed results.
- [ ] Document prototype limitations clearly.

### Phase 6: GUI comparison tool

- [ ] Reuse the existing CSV viewer interaction model as the basis for the comparison GUI.
- [ ] Build a waveform comparison workspace for query versus matched reference windows.
- [ ] Support scroll and zoom for both the query and reference views.
- [ ] Support one-candidate-at-a-time overlay inspection.
- [ ] Make it easy to step through the top-ranked matches returned by the CLI.
- [ ] Add export options for comparison screenshots or plots.

### Phase 7: Polish and future direction

- [ ] Clean up packaging and dependency management.
- [ ] Add unit tests around data merging, alignment, and scoring.
- [ ] Add example workflows for common usage patterns.
- [ ] Review the codebase for cross-platform behavior.
- [ ] Decide whether to formalize the project as an installable package.
- [ ] Reassess storage format once the dataset grows further.
- [ ] Plan future expansion to broader validation, more recordings, and possibly 50 Hz support.

## Near-Term Execution Order

If progress needs to stay focused, the immediate priority order should be:

1. Merge and normalize reference data.
2. Prototype query-side ENF extraction from audio and video.
3. Run algorithm comparison and select the first matcher.
4. Build the CLI around that matcher.
5. Add evaluation artifacts and overlay output.
6. Build the GUI inspector afterward.

## Success Criteria For The Prototype

The first prototype should be considered successful if it can:

- Accept a local media file.
- Extract a usable ENF waveform.
- Compare it against a selected 60 Hz grid reference dataset.
- Return ranked candidate windows.
- Provide a score that is understandable enough to support manual review.
- Produce output artifacts that make debugging and visual inspection practical.

## Notes For Later Research

These items should remain visible so they do not get buried during implementation:

- Review academic ENF literature before locking minimum clip length assumptions.
- Review literature and experiments before locking the default Hz threshold.
- Track which recordings fail and why.
- Keep a small benchmark set of example media once enough recordings are available.
- Avoid overstating certainty in any result presentation.