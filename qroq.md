# Groq test with llama 3.3

```bash
python3 cli.py start --openai-url https://api.groq.com/openai --mode aggressive --output-on
```

```bash
python3 benchmark.py \
  --mode aggressive \
  --model "llama-3.3-70b-versatile" \
  --api-key gsk_J*********************** \
  --provider openai \
  --verbose
```

LCO Comprehensive Benchmark
Mode : aggressive
Model : llama-3.3-70b-versatile
Proxy : [localhost](http://127.0.0.1:8000)
Conversations: 12 across 4 categories (Customer Support, Data Analysis, Documentation, Coding Assistant)
Measurement : single request, savings from response headers

Warming up llama-3.3-70b-versatile ... ready

[ 1/12] Customer Support: Billing dispute — long history
→ [Baseline] 1105ms in=339 out=198tok
→ [Optimized] 348ms in=216 out=23tok
in_saved=178 out_saved=175 total=65.7%

[ 2/12] Customer Support: Technical support — repeated context
→ [Baseline] 1112ms in=345 out=323tok
→ [Optimized] 708ms in=227 out=76tok
in_saved=185 out_saved=247 total=64.7%

[ 3/12] Customer Support: Onboarding help — sign-off heavy
→ [Baseline] 1212ms in=331 out=318tok
→ [Optimized] 380ms in=225 out=44tok
in_saved=162 out_saved=274 total=67.2%

[ 4/12] Data Analysis: Data interpretation — verbose history
→ [Baseline] 1687ms in=375 out=386tok
→ [Optimized] 400ms in=216 out=51tok
in_saved=210 out_saved=335 total=71.6%

[ 5/12] Data Analysis: ML concepts — AI opener heavy
→ [Baseline] 1286ms in=359 out=388tok
→ [Optimized] 353ms in=230 out=37tok
in_saved=211 out_saved=351 total=75.2%

[ 6/12] Data Analysis: SQL explanation — repeated explanations
→ [Baseline] 1313ms in=343 out=385tok
→ [Optimized] 320ms in=227 out=37tok
in_saved=174 out_saved=348 total=71.7%

[ 7/12] Documentation: API documentation explanation
→ [Baseline] 1576ms in=373 out=466tok
→ [Optimized] 580ms in=246 out=117tok
in_saved=213 out_saved=349 total=67.0%

[ 8/12] Documentation: Architecture explanation — very verbose
→ [Baseline] 1376ms in=369 out=417tok
→ [Optimized] 443ms in=237 out=65tok
in_saved=240 out_saved=352 total=75.3%

[ 9/12] Documentation: Process explanation — heavy redundancy
→ [Baseline] 1501ms in=390 out=410tok
→ [Optimized] 601ms in=244 out=73tok
in_saved=200 out_saved=337 total=67.1%

[10/12] Coding Assistant: Python patterns — mixed prose+code
→ [Baseline] 1266ms in=276 out=542tok
→ [Optimized] 729ms in=209 out=80tok
in_saved=136 out_saved=462 total=73.1%

[11/12] Coding Assistant: API design — verbose explanation
→ [Baseline] 840ms in=324 out=166tok
→ [Optimized] 1203ms in=224 out=304tok
in_saved=182 out_saved=0 total=37.1%

[12/12] Coding Assistant: Debugging session — long history
→ [Baseline] 1426ms in=385 out=481tok
→ [Optimized] 285ms in=252 out=46tok
in_saved=199 out_saved=435 total=73.2%

Results by category — aggressive mode

Customer Support
Conversation In orig In used In saved Out Out saved Reduction
──────────────────────── ──────── ──────── ──────── ──────── ──────── ─────────
Billing dispute — long 339 216 178 23 175 65.7%
Technical support — rep 345 227 185 76 247 64.7%
Onboarding help — sign- 331 225 162 44 274 67.2%
──────────────────────── ──────── ──────── ──────── ──────── ──────── ─────────
Subtotal (3) 1015 668 525 143 696 65.9%

Data Analysis
Conversation In orig In used In saved Out Out saved Reduction
──────────────────────── ──────── ──────── ──────── ──────── ──────── ─────────
Data interpretation — v 375 216 210 51 335 71.6%
ML concepts — AI opener 359 230 211 37 351 75.2%
SQL explanation — repea 343 227 174 37 348 71.7%
──────────────────────── ──────── ──────── ──────── ──────── ──────── ─────────
Subtotal (3) 1077 673 595 125 1034 72.9%

Documentation
Conversation In orig In used In saved Out Out saved Reduction
──────────────────────── ──────── ──────── ──────── ──────── ──────── ─────────
API documentation expla 373 246 213 117 349 67.0%
Architecture explanatio 369 237 240 65 352 75.3%
Process explanation — h 390 244 200 73 337 67.1%
──────────────────────── ──────── ──────── ──────── ──────── ──────── ─────────
Subtotal (3) 1132 727 653 255 1038 69.7%

Coding Assistant
Conversation In orig In used In saved Out Out saved Reduction
──────────────────────── ──────── ──────── ──────── ──────── ──────── ─────────
Python patterns — mixed 276 209 136 80 462 73.1%
API design — verbose ex 324 224 182 304 0 37.1%
Debugging session — lon 385 252 199 46 435 73.2%
──────────────────────── ──────── ──────── ──────── ──────── ──────── ─────────
Subtotal (3) 985 685 517 430 897 61.2%

═══════════════════════════════════════════════════════════════════════════════════════
GRAND TOTAL (12) 4209 2753 2290 953 3665 67.5%

Cost savings (GPT-4o: $2.50/1M input · $10.00/1M output)
Input : 2290 tokens → $0.00573
Output : 3665 tokens → $0.03665
Total : saved → $0.0424 / 12 requests

Extrapolated:
1,000 req/day → $3.53/day
10,000 req/day → $35.31/day

Dashboard: [dashboard](http://127.0.0.1:8000/lco/dashboard)
