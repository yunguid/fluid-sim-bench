# Benchmark rules

These rules govern every run. `bench/run.py` and `bench/sandbox/job.py`
enforce them, and each run's `manifest.json` records a hash of this file, so a
result can always be traced to the rules it ran under.

The principle: **time is measured, never budgeted.** Limits exist only to
detect stalled infrastructure, and the rules for testing the finished program
are stated to the model in the prompt.

## 1. Generation: the model's turn

| Rule | Setting |
| --- | --- |
| Output length | No limit. Each response asks for the pinned provider's maximum output. When a response stops at that limit, the conversation continues: the model's output so far (and its reasoning, where the provider allows sending it back) is returned to it with an instruction to continue exactly where it stopped, until it finishes on its own. Every turn is logged. |
| Total generation time | No limit. A model may reason for as long as it wants. |
| Stuck | The only stop: 3 continuations in a row that produce no tokens at all (`stuck`, a model failure). Hidden reasoning counts as output, so a model that is still working is never stopped. |
| Reasoning effort | The model decides how long to think. Models whose single-response output limit is so large that reasoning can never crowd out the answer (above 131k tokens: Gemini, Grok, Kimi, DeepSeek, MiniMax, Mistral) run at their highest effort (`max`, else `xhigh`, else `high`). OpenAI models also run at : OpenAI applies effort levels natively, so a high level does not reserve a fixed share of the output (left at its default, GPT reasoned for only about 200 tokens). The other models capped near 128k (Claude, GLM, Qwen) get no reasoning setting, so they use their own default and stop thinking when they judge they are done: forcing `max` there turns into a fixed budget of about 95% of the cap, which the model fills, leaving no room for the answer, and a thought cut off mid-way cannot be continued. Local models run with thinking on (`high` where levels exist). The setting used is recorded per job. |
| Other sampling settings | Provider defaults, recorded. |
| Stalled connection | No data for 15 minutes (API) or 30 minutes (local) is an infrastructure failure. Streams send keep-alives while a model reasons, so a slow model never trips this. |
| Infrastructure failures | Network errors, HTTP errors, rate limits, provider errors, and streams that end without a finish reason. Retried up to 5 times with backoff (30 s to 8 min). If all fail, the job is `infra-error`: excluded from scores and retried by the next pass of the same run. |
| Model failures | No code block, empty answer, stuck, failed build, crash. Never retried. The 3 trials are independent samples, and all are reported. `--retry-failed` exists only for fixing harness bugs, and its use is recorded in the manifest. |

## 2. Fair timing

| Rule | Setting |
| --- | --- |
| Measured | Time to first token, total generation time, prompt, output and reasoning tokens, tokens per second (local), cost. Reported in a separate efficiency view and never part of the quality score. |
| API providers | Each model is pinned to one provider with fallbacks off (`models.json`, field `pin`): the model maker's own service where OpenRouter has it, otherwise the highest-precision host. The serving provider is recorded per job. |
| Concurrency | At most 6 API requests at once. Local models run one at a time with the GPU to themselves, and each model is loaded before its first job is timed. |

## 3. Testing the program (stated in the prompt)

Entries are browser programs: C++ compiled to WebAssembly with Emscripten,
drawing with WebGL2. All building and testing happens in a sandbox
container: no network, read-only system, all capabilities dropped, 8 CPUs and
8 GB of memory, write access only to the job's own folder. Emscripten's
library cache is prepared once and read-only for entries.

| Rule | Setting |
| --- | --- |
| Build | The fixed `em++` command in the prompt must succeed within 5 minutes. |
| Page | Every entry runs in the same fixed page (`bench/web/shell.html`), which gives it a full-window canvas and records errors and frame counts. |
| Browser | Headless Chromium, 1280×720, software WebGL2 (SwiftShader), driven over the DevTools protocol. |
| Startup | The program must draw frames within 30 seconds of the page loading. |
| Run | After 2 seconds of settling, a 15-second test runs. The program must still be drawing frames at the end, with no uncaught error or abort. A page that stops responding for 30 seconds fails. |
| Input | The same for every entry: at second 5, the left button is held and the cursor traces a figure-eight through the middle of the window for about 5 seconds, as real browser mouse events. |
| Recorded | Screenshots at 1, 4, 7.5, 11 and 14 s (11 s is the poster), frames per second in software, how much the image changes without input (1 s to 4 s) and from the drag (4 s to 11 s), console errors, sokol validation and shader errors. |
| Judging | People run every working entry live in the gallery on their own GPU, alone or side by side. |

## 4. Spending

No spending limit by default: models use as many tokens as they need. A run
can optionally be given `--budget USD`, after which no new API job starts
(running jobs are never stopped; skipped jobs are `budget-skip` and continue
on the next pass). Spend is shown live per model.

## 5. Agent track (planned)

No token, time, or turn limit. The model works through tools and finishes by
calling `finish`. The only stop is the same `stuck` rule as above. Tool calls
follow the testing rules in section 3.
