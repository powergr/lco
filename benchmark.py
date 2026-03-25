#!/usr/bin/env python3
"""
LCO Comprehensive Benchmark
============================
12 conversations across 4 categories testing all compression pipeline stages.

Categories
──────────
1. Customer Support  (3 convos) — long boilerplate-heavy exchanges
2. Data Analysis     (3 convos) — verbose explanations with repetition
3. Documentation     (3 convos) — long prose answers, minimal code
4. Coding Assistant  (3 convos) — mixed prose+code (tests mixed compressor)

Each conversation has 4–8 turns of realistic history before the final
user message, with progressively longer assistant responses.

Usage
─────
  python3 benchmark.py                        All categories, light mode
  python3 benchmark.py --mode aggressive      All categories, aggressive
  python3 benchmark.py --category support     One category only
  python3 benchmark.py --turns 6              Longer conversations
  python3 benchmark.py --model qwen2.5:7b     Specific model
  python3 benchmark.py --dry-run              No LLM needed
"""

from __future__ import annotations
import argparse
import os
import sys
import time
import httpx


# ── Conversation library ──────────────────────────────────────────────────────

CONVERSATIONS = {

"support": [
  {
    "name": "Billing dispute — long history",
    "messages": [
      {"role": "system", "content":
        "You are a helpful customer support agent for a SaaS company. "
        "You are a helpful customer support agent for a SaaS company. "
        "Always be professional and empathetic. Always be professional and empathetic."},
      {"role": "user", "content": "Hi, I was charged twice for my subscription last month."},
      {"role": "assistant", "content":
        "Hello! I sincerely apologize for the inconvenience this has caused you. "
        "I completely understand how frustrating it can be to see unexpected charges on your account. "
        "I'm here to help you resolve this as quickly as possible. "
        "I'm here to help you resolve this as quickly as possible. "
        "Could you please provide me with your account email address so I can look into this for you? "
        "I want to make sure we get this sorted out right away. "
        "Please let me know if you have any questions in the meantime!"},
      {"role": "user", "content": "My email is user@example.com. This happened on March 1st."},
      {"role": "assistant", "content":
        "Thank you for providing that information! I've located your account. "
        "I can see that there was indeed a duplicate charge on March 1st. "
        "I sincerely apologize for this error on our end. "
        "This should not have happened and I completely understand your frustration. "
        "I have initiated a refund for the duplicate charge of $29.99. "
        "The refund should appear on your statement within 5-7 business days. "
        "Is there anything else I can help you with today? "
        "Please don't hesitate to reach out if you have any other questions!"},
      {"role": "user", "content": "How do I make sure this doesn't happen again?"},
    ],
  },
  {
    "name": "Technical support — repeated context",
    "messages": [
      {"role": "system", "content": "You are a technical support specialist. Be thorough."},
      {"role": "user", "content": "My application keeps crashing on startup."},
      {"role": "assistant", "content":
        "Certainly! I'd be happy to help you troubleshoot this issue. "
        "Application crashes on startup can be caused by several different factors. "
        "Application crashes on startup can be caused by several different factors. "
        "Let me walk you through the most common solutions step by step. "
        "First, please try clearing the application cache. "
        "Second, make sure your operating system is fully updated. "
        "Third, try reinstalling the application completely. "
        "I hope one of these solutions resolves your issue! "
        "Please let me know which operating system you are using."},
      {"role": "user", "content": "I'm on Windows 11. I tried clearing cache but it still crashes."},
      {"role": "assistant", "content":
        "Thank you for that information! As an AI assistant, I can guide you further. "
        "Since you're on Windows 11 and clearing the cache didn't help, let's try some additional steps. "
        "Since you're on Windows 11 and clearing the cache didn't help, let's try some additional steps. "
        "Please check the Event Viewer for error logs. Press Win+R, type eventvwr.msc, and look under Windows Logs > Application. "
        "Also, try running the application as administrator by right-clicking the icon. "
        "Additionally, check if any recent Windows updates might have caused compatibility issues. "
        "Please let me know what errors you see in the Event Viewer and I will be happy to assist further!"},
      {"role": "user", "content": "Event viewer shows: 'Failed to load DLL: vcruntime140.dll'"},
    ],
  },
  {
    "name": "Onboarding help — sign-off heavy",
    "messages": [
      {"role": "system", "content": "You are an onboarding specialist. Guide new users warmly."},
      {"role": "user", "content": "I just signed up. Where do I start?"},
      {"role": "assistant", "content":
        "Welcome aboard! We're so excited to have you join us! "
        "Getting started is easy and I'm here to guide you every step of the way. "
        "First, complete your profile by clicking on your avatar in the top right. "
        "Second, connect your first data source from the Integrations menu. "
        "Third, explore the dashboard to familiarise yourself with the interface. "
        "I hope this helps you get started! Please feel free to ask me anything at all. "
        "Our team is always here to help you succeed. Don't hesitate to reach out!"},
      {"role": "user", "content": "What integrations do you support?"},
      {"role": "assistant", "content":
        "Great question! We support a wide variety of integrations to help you connect your existing tools. "
        "We support a wide variety of integrations to help you connect your existing tools. "
        "In the productivity category, we integrate with Slack, Microsoft Teams, and Google Workspace. "
        "For CRM systems, we support Salesforce, HubSpot, and Pipedrive. "
        "For data warehouses, we connect with BigQuery, Snowflake, and Redshift. "
        "We also support REST APIs for custom integrations. "
        "I hope that answers your question! Is there a specific integration you're looking for? "
        "Let me know and I'll be happy to provide more details. Feel free to ask anything!"},
      {"role": "user", "content": "How do I set up the Slack integration specifically?"},
    ],
  },
],

"analysis": [
  {
    "name": "Data interpretation — verbose history",
    "messages": [
      {"role": "system", "content": "You are a data analyst. Explain concepts clearly and thoroughly."},
      {"role": "user", "content": "What is the difference between mean, median, and mode?"},
      {"role": "assistant", "content":
        "Certainly! As a data analyst, I can explain these three measures of central tendency. "
        "The mean is calculated by adding all values and dividing by the count. "
        "The mean is calculated by adding all values and dividing by the count. "
        "The median is the middle value when all values are sorted in order. "
        "The median is the middle value when all values are sorted in order. "
        "The mode is the value that appears most frequently in the dataset. "
        "For example: in the dataset [1, 2, 2, 3, 4], mean=2.4, median=2, mode=2. "
        "I hope this explanation helps! Let me know if you need any clarification."},
      {"role": "user", "content": "When should I use each one?"},
      {"role": "assistant", "content":
        "Great question! Each measure has its appropriate use cases. "
        "Use the mean when your data is normally distributed and has no significant outliers. "
        "Use the mean when your data is normally distributed and has no significant outliers. "
        "The mean is sensitive to extreme values, so a few very high or low values can skew it significantly. "
        "Use the median when your data has outliers or is skewed, such as income data. "
        "Use the median when your data has outliers or is skewed. "
        "The median is robust to outliers and gives a better picture of the typical value. "
        "Use the mode for categorical data or when you want the most common value. "
        "I hope that clarifies things! Please let me know if you have further questions."},
      {"role": "user", "content": "Can you explain skewness and how it affects which measure to use?"},
    ],
  },
  {
    "name": "ML concepts — AI opener heavy",
    "messages": [
      {"role": "system", "content": "You are an ML educator. Explain with examples."},
      {"role": "user", "content": "What is overfitting in machine learning?"},
      {"role": "assistant", "content":
        "As an AI language model, I can explain overfitting in machine learning. "
        "Overfitting occurs when a model learns the training data too well, including its noise. "
        "Overfitting occurs when a model learns the training data too well, including its noise. "
        "The model performs excellently on training data but poorly on new, unseen data. "
        "Think of it like a student who memorises exam answers instead of understanding concepts. "
        "They pass the practice test but fail the real exam with different questions. "
        "I hope this analogy helps clarify the concept! Let me know if you need more detail."},
      {"role": "user", "content": "How do I detect and prevent overfitting?"},
      {"role": "assistant", "content":
        "Absolutely! As an AI, I can walk you through both detection and prevention methods. "
        "To detect overfitting, monitor both training and validation loss during training. "
        "To detect overfitting, monitor both training and validation loss during training. "
        "If training loss decreases but validation loss increases, you have overfitting. "
        "To prevent overfitting, you have several effective techniques available. "
        "First, use regularisation techniques like L1 or L2 regularisation. "
        "Second, apply dropout layers in neural networks during training. "
        "Third, use cross-validation to better estimate model performance. "
        "Fourth, collect more training data if possible. "
        "Fifth, reduce model complexity by using fewer parameters. "
        "I hope these strategies help you address overfitting in your models!"},
      {"role": "user", "content": "What is the bias-variance tradeoff and how does it relate?"},
    ],
  },
  {
    "name": "SQL explanation — repeated explanations",
    "messages": [
      {"role": "system", "content": "You are a database expert. Teach SQL clearly."},
      {"role": "user", "content": "What is a JOIN in SQL?"},
      {"role": "assistant", "content":
        "A JOIN in SQL is used to combine rows from two or more tables based on a related column. "
        "A JOIN in SQL is used to combine rows from two or more tables based on a related column. "
        "There are several types of JOINs available in SQL. "
        "An INNER JOIN returns only rows where there is a match in both tables. "
        "A LEFT JOIN returns all rows from the left table and matching rows from the right. "
        "A RIGHT JOIN returns all rows from the right table and matching rows from the left. "
        "A FULL OUTER JOIN returns all rows from both tables. "
        "Please let me know if you need examples of any specific type of JOIN!"},
      {"role": "user", "content": "Can you show me an example with real tables?"},
      {"role": "assistant", "content":
        "Of course! I'd be happy to show you a practical example with real tables. "
        "Let's say we have two tables: customers and orders. "
        "The customers table has: customer_id, name, email. "
        "The orders table has: order_id, customer_id, product, amount. "
        "An INNER JOIN to get all customers with their orders would be: "
        "SELECT customers.name, orders.product, orders.amount "
        "FROM customers INNER JOIN orders ON customers.customer_id = orders.customer_id. "
        "This returns only customers who have placed at least one order. "
        "I hope this example makes JOINs clearer! Feel free to ask for more examples."},
      {"role": "user", "content": "What about performance? How do I optimise queries with JOINs?"},
    ],
  },
],

"docs": [
  {
    "name": "API documentation explanation",
    "messages": [
      {"role": "system", "content": "You are a technical writer creating clear API documentation."},
      {"role": "user", "content": "Explain REST API principles to a beginner."},
      {"role": "assistant", "content":
        "Certainly! I'd be delighted to explain REST API principles in a beginner-friendly way. "
        "REST stands for Representational State Transfer. "
        "It is an architectural style for designing networked applications. "
        "REST APIs use HTTP requests to perform operations on resources. "
        "The six core principles of REST are as follows. "
        "First, client-server separation: the client and server are independent. "
        "Second, statelessness: each request contains all necessary information. "
        "Third, cacheability: responses can be cached to improve performance. "
        "Fourth, uniform interface: a consistent way to interact with resources. "
        "Fifth, layered system: the architecture can have multiple layers. "
        "Sixth, code on demand: servers can send executable code to clients. "
        "I hope this overview helps you understand REST principles!"},
      {"role": "user", "content": "What HTTP methods are used and when?"},
      {"role": "assistant", "content":
        "Great question! HTTP methods are fundamental to REST API design. "
        "GET is used to retrieve a resource or collection of resources. "
        "GET is used to retrieve a resource or collection of resources. "
        "It should never modify data and should be safe and idempotent. "
        "POST is used to create a new resource. "
        "It sends data in the request body and is not idempotent. "
        "PUT is used to update an existing resource completely. "
        "PUT is used to update an existing resource completely. "
        "It replaces the entire resource with the provided data. "
        "PATCH is used for partial updates to an existing resource. "
        "DELETE is used to remove a resource. "
        "I hope this clarifies when to use each HTTP method!"},
      {"role": "user", "content": "How should I handle authentication and error responses in a REST API?"},
    ],
  },
  {
    "name": "Architecture explanation — very verbose",
    "messages": [
      {"role": "system", "content": "You are a software architect. Explain design patterns clearly."},
      {"role": "user", "content": "What is microservices architecture?"},
      {"role": "assistant", "content":
        "Microservices architecture is an approach to building applications as a collection of small, independent services. "
        "Microservices architecture is an approach to building applications as a collection of small, independent services. "
        "Each service is responsible for a specific business capability. "
        "Each service is responsible for a specific business capability and can be deployed independently. "
        "Services communicate with each other through well-defined APIs, typically REST or message queues. "
        "This is in contrast to monolithic architecture where all functionality is in one deployable unit. "
        "The benefits include independent scaling, technology flexibility, and fault isolation. "
        "I hope this gives you a good overview of microservices! Let me know if you want more detail."},
      {"role": "user", "content": "What are the main challenges with microservices?"},
      {"role": "assistant", "content":
        "Absolutely! While microservices offer many benefits, they also introduce significant challenges. "
        "The first major challenge is distributed system complexity. "
        "When you have many services communicating over a network, you introduce latency and potential failures. "
        "When you have many services communicating over a network, you introduce latency and potential failures. "
        "The second challenge is data management. "
        "Each service should ideally have its own database, which makes data consistency difficult. "
        "The third challenge is service discovery and load balancing. "
        "Services need to find each other dynamically as they scale up and down. "
        "The fourth challenge is operational complexity. "
        "Monitoring, logging, and debugging across many services requires sophisticated tooling. "
        "The fifth challenge is network security between services. "
        "I hope this gives you a realistic picture of microservices challenges!"},
      {"role": "user", "content": "When should a team choose microservices vs a monolith?"},
    ],
  },
  {
    "name": "Process explanation — heavy redundancy",
    "messages": [
      {"role": "system", "content": "You are an agile coach explaining processes."},
      {"role": "user", "content": "What is the Scrum framework?"},
      {"role": "assistant", "content":
        "Certainly! Scrum is an agile framework for developing and delivering complex products. "
        "Scrum is an agile framework for developing and delivering complex products. "
        "It consists of roles, events, and artifacts. "
        "The three Scrum roles are the Product Owner, Scrum Master, and Development Team. "
        "The three Scrum roles are the Product Owner, Scrum Master, and Development Team. "
        "The main events are the Sprint, Sprint Planning, Daily Scrum, Sprint Review, and Retrospective. "
        "Sprints are time-boxed iterations, typically two weeks long. "
        "The artifacts include the Product Backlog, Sprint Backlog, and Increment. "
        "I hope this gives you a solid foundation in Scrum! Please let me know if you need more detail."},
      {"role": "user", "content": "How does the Daily Scrum work in practice?"},
      {"role": "assistant", "content":
        "Great question! The Daily Scrum is a brief synchronisation meeting. "
        "The Daily Scrum is a brief synchronisation meeting held every day. "
        "It is time-boxed to 15 minutes maximum. "
        "It is time-boxed to 15 minutes maximum regardless of team size. "
        "Traditionally, each team member answers three questions. "
        "First: What did I do yesterday that helped the team meet the Sprint Goal? "
        "Second: What will I do today to help the team meet the Sprint Goal? "
        "Third: Do I see any impediment that prevents me or the team from meeting the Sprint Goal? "
        "The Scrum Master facilitates the meeting and helps remove any impediments raised. "
        "Please let me know if you have any questions about the Daily Scrum!"},
      {"role": "user", "content": "What's the difference between Scrum and Kanban, and when should I use each?"},
    ],
  },
],

"coding": [
  {
    "name": "Python patterns — mixed prose+code",
    "messages": [
      {"role": "system", "content": "You are a Python expert. Provide clear explanations with examples."},
      {"role": "user", "content": "What are Python decorators?"},
      {"role": "assistant", "content":
        "Certainly! Python decorators are a very powerful and useful feature. "
        "A decorator is a function that takes another function and extends its behaviour. "
        "A decorator is a function that takes another function and extends its behaviour without modifying it. "
        "They use the @ symbol syntax as a shorthand. "
        "Here is a simple example of a decorator that logs function calls. "
        "I hope this introduction helps! Let me know if you need more examples."},
      {"role": "user", "content": "Can you show me a practical decorator example with arguments?"},
      {"role": "assistant", "content":
        "Of course! Here is a practical decorator that accepts arguments. "
        "Decorators with arguments require an extra level of nesting. "
        "Decorators with arguments require an extra level of nesting as shown below. "
        "The outer function accepts the decorator arguments. "
        "The middle function accepts the function to decorate. "
        "The inner function is the actual wrapper. "
        "Here is an example of a retry decorator with configurable attempts. "
        "I hope this example shows how flexible decorators can be! "
        "Please let me know if you need clarification on any part of this."},
      {"role": "user", "content": "How do decorators interact with class methods and what is @property?"},
    ],
  },
  {
    "name": "API design — verbose explanation",
    "messages": [
      {"role": "system", "content": "You are a FastAPI expert. Explain best practices."},
      {"role": "user", "content": "How do I structure a FastAPI project?"},
      {"role": "assistant", "content":
        "Great question! Structuring a FastAPI project well from the start saves a lot of time. "
        "A well-organised FastAPI project typically follows this directory structure. "
        "The main application file should be kept simple and focused on configuration. "
        "Routes should be organised into separate router modules by domain or feature. "
        "Database models, schemas, and business logic should each have their own directories. "
        "I recommend using a structure with app/, routers/, models/, schemas/, and services/ directories. "
        "I hope this gives you a solid foundation for your project structure!"},
      {"role": "user", "content": "How should I handle database connections and sessions?"},
      {"role": "assistant", "content":
        "Excellent question! Database connection management is crucial for a production FastAPI app. "
        "You should use SQLAlchemy with async support for modern FastAPI applications. "
        "You should use SQLAlchemy with async support for modern FastAPI applications. "
        "The key principle is to never create a new connection for every request. "
        "Instead, use a connection pool managed by SQLAlchemy. "
        "Use FastAPI's dependency injection system to provide database sessions. "
        "Always ensure sessions are properly closed after each request using try/finally. "
        "For async applications, use asyncpg with SQLAlchemy's async engine. "
        "Please let me know if you need code examples for any of these patterns!"},
      {"role": "user", "content": "What's the best way to handle authentication and JWT tokens in FastAPI?"},
    ],
  },
  {
    "name": "Debugging session — long history",
    "messages": [
      {"role": "system", "content": "You are a debugging expert. Help identify and fix code issues."},
      {"role": "user", "content": "My Python script is very slow when processing large CSV files."},
      {"role": "assistant", "content":
        "I'd be happy to help you optimise your CSV processing! "
        "There are several common reasons why CSV processing can be slow in Python. "
        "There are several common reasons why CSV processing can be slow in Python. "
        "First, reading the entire file into memory at once can cause memory issues and slowdowns. "
        "Second, using pandas read_csv with default settings can be suboptimal for large files. "
        "Third, row-by-row processing in Python loops is much slower than vectorised operations. "
        "Fourth, unnecessary data type conversions add overhead. "
        "Could you share how you are currently reading and processing the file? "
        "I will be happy to suggest specific optimisations once I understand your approach!"},
      {"role": "user", "content": "I'm using a for loop to read each row and do calculations."},
      {"role": "assistant", "content":
        "That explains the slowness! Row-by-row Python loops are notoriously slow for large datasets. "
        "Row-by-row Python loops are notoriously slow for large datasets. "
        "The solution is to use pandas vectorised operations instead of explicit loops. "
        "Pandas operations are implemented in C and are dramatically faster. "
        "For a file with 1 million rows, a pandas vectorised operation can be 100x faster than a for loop. "
        "For a file with 1 million rows, vectorised operations can be 100x faster than Python loops. "
        "If memory is a concern, you can also process the file in chunks using the chunksize parameter. "
        "Let me know what specific calculations you are performing and I can show you the optimised version!"},
      {"role": "user", "content": "I'm calculating the average sale per customer and filtering rows where amount > 100."},
    ],
  },
],

}


def _c(t, code): return f"\033[{code}m{t}\033[0m" if sys.stdout.isatty() else t
def GREEN(t):  return _c(t, "32")
def YELLOW(t): return _c(t, "33")
def BOLD(t):   return _c(t, "1")
def DIM(t):    return _c(t, "2")
def RED(t):    return _c(t, "31")
def CYAN(t):   return _c(t, "36")


CATEGORY_NAMES = {
    "support":  "Customer Support",
    "analysis": "Data Analysis",
    "docs":     "Documentation",
    "coding":   "Coding Assistant",
}


def check_proxy(url: str) -> dict:
    try:
        return httpx.get(f"{url}/lco/status", timeout=3).json()
    except Exception:
        return {}


def send_one(client: httpx.Client, url: str, body: dict,
             headers: dict, mode: str) -> dict:
    req_headers = {**headers, "x-lco-mode-override": mode}
    t0 = time.perf_counter()
    try:
        r = client.post(f"{url}/v1/chat/completions",
                        json=body, headers=req_headers, timeout=120)
    except httpx.TimeoutException:
        return {"error": "timeout after 120s"}
    ms = (time.perf_counter() - t0) * 1000
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}: {r.text[:120]}"}
    data  = r.json()
    usage = data.get("usage", {})
    content = ""
    if data.get("choices"):
        content = (data["choices"][0].get("message") or {}).get("content") or ""
    return {
        "input_tokens":  usage.get("prompt_tokens")     or usage.get("input_tokens",    0),
        "output_tokens": usage.get("completion_tokens") or usage.get("output_tokens",   0),
        "latency_ms":    ms,
        "in_saved":      int(r.headers.get("x-lco-input-saved",  "0")),
        "out_saved":     int(r.headers.get("x-lco-output-saved", "0")),
        "lco_mode":      r.headers.get("x-lco-mode", mode),
        "content_len":   len(content),
    }


def dry_run(convo: dict, mode: str) -> dict:
    chars = sum(len(str(m.get("content", ""))) for m in convo["messages"])
    base_in = chars // 4
    f = {"light": 0.82, "medium": 0.60, "aggressive": 0.40}.get(mode, 0.82)
    in_saved  = int(base_in * (1 - f))
    out_saved = int(90 * 0.30)
    return {
        "input_tokens":  base_in - in_saved,
        "output_tokens": 90,
        "latency_ms":    0,
        "in_saved":      in_saved,
        "out_saved":     out_saved,
        "lco_mode":      mode,
        "content_len":   360,
    }


def warmup(client: httpx.Client, proxy: str, model: str, headers: dict) -> None:
    print(DIM(f"  Warming up {model} ... "), end="", flush=True)
    try:
        r = client.post(
            f"{proxy}/v1/chat/completions",
            json={"model": model,
                  "messages": [{"role": "user", "content": "Say: ready"}],
                  "stream": False},
            headers=headers,
            timeout=90,
        )
        print(DIM("ready\n") if r.status_code == 200 else DIM(f"({r.status_code})\n"))
    except Exception:
        print(DIM("skipped\n"))


def main():
    ap = argparse.ArgumentParser(description="LCO Comprehensive Benchmark")
    ap.add_argument("--proxy",    default="http://127.0.0.1:8000")
    ap.add_argument("--provider", default="ollama",
                    choices=["ollama", "openai", "anthropic"])
    ap.add_argument("--model",    default=os.getenv("LCO_OLLAMA_CHAT_MODEL", "qwen2.5:7b"))
    ap.add_argument("--api-key",  default="ollama")
    ap.add_argument("--mode",     default="light",
                    choices=["light", "medium", "aggressive"])
    ap.add_argument("--category", default="all",
                    choices=["all", "support", "analysis", "docs", "coding"])
    ap.add_argument("--dry-run",  action="store_true")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="Show input messages and compression summary per conversation")
    args = ap.parse_args()

    if not args.dry_run:
        status = check_proxy(args.proxy)
        if not status:
            print(RED(f"\n  Proxy not reachable at {args.proxy}"))
            print("  Start it with: python3 cli.py start\n")
            sys.exit(1)
        if status.get("compression_mode") == "passthrough":
            print(YELLOW(
                f"\n  Note: proxy is in passthrough mode globally.\n"
                f"  This benchmark sends x-lco-mode-override:{args.mode} per request.\n"
                f"  To make permanent: python3 lco_ctl.py mode {args.mode}\n"
            ))

    headers = {"Authorization": f"Bearer {args.api_key}",
               "Content-Type": "application/json"}
    if args.provider == "anthropic":
        headers = {"x-api-key": args.api_key, "Content-Type": "application/json"}

    # Build conversation list
    if args.category == "all":
        categories = list(CONVERSATIONS.keys())
    else:
        categories = [args.category]

    all_convos = []
    for cat in categories:
        for convo in CONVERSATIONS[cat]:
            all_convos.append((cat, convo))

    print(f"\n  {BOLD('LCO Comprehensive Benchmark')}")
    print(f"  Mode        : {YELLOW(args.mode)}")
    if not args.dry_run:
        print(f"  Model       : {args.model}")
        print(f"  Proxy       : {args.proxy}")
    print(f"  Conversations: {len(all_convos)} across "
          f"{len(categories)} categories "
          f"({', '.join(CATEGORY_NAMES.get(c,c) for c in categories)})")
    print(f"  Measurement : single request, savings from response headers\n")

    if args.dry_run:
        print(DIM("  --dry-run: estimated numbers only\n"))

    results_by_cat: dict[str, list] = {c: [] for c in categories}
    total_n = len(all_convos)

    with httpx.Client() as client:
        if not args.dry_run:
            warmup(client, args.proxy, args.model, headers)

        for idx, (cat, convo) in enumerate(all_convos):
            body = {"model": args.model, "messages": convo["messages"],
                    "stream": False}
            cat_label = CATEGORY_NAMES.get(cat, cat)

            print(f"  [{idx+1:2d}/{total_n}] {CYAN(cat_label)}: {convo['name']}")

            if args.dry_run:
                res = dry_run(convo, args.mode)
            else:
                if args.verbose:
                    print()
                    print(DIM("  ── Input messages ──────────────────────────────────"))
                    for _msg in convo["messages"]:
                        _role = _msg["role"].upper()
                        _text = str(_msg.get("content",""))
                        _trunc = _text[:250] + ("…" if len(_text) > 250 else "")
                        print(f"  [{_role}] {_trunc}")
                    print()

                print("         → ", end="", flush=True)
                res = send_one(client, args.proxy, body, headers, args.mode)
                if "error" in res:
                    print(RED(f"FAILED: {res['error']}"))
                    continue
                print(DIM(f"{res['latency_ms']:.0f}ms  out={res['output_tokens']}tok"))

                if args.verbose:
                    _ins  = res.get("in_saved",  0)
                    _outs = res.get("out_saved", 0)
                    print()
                    print(DIM("  ── Compression result ───────────────────────────────"))
                    print(f"  Input : {res['input_tokens']+_ins} tok "
                          f"→ {res['input_tokens']} tok  (saved {GREEN(str(_ins))})")
                    print(f"  Output: {res['output_tokens']+_outs} tok "
                          f"→ {res['output_tokens']} tok  (saved {GREEN(str(_outs))})")
                    print(DIM("  (run proxy with --log-level DEBUG to see full text in logs)"))
                    print()

            in_saved    = res.get("in_saved", 0)
            out_saved   = res.get("out_saved", 0)
            total_saved = in_saved + out_saved
            in_original = res["input_tokens"] + in_saved
            orig_total  = in_original + res["output_tokens"] + out_saved
            pct         = total_saved / max(1, orig_total) * 100

            rec = {
                "name":        convo["name"],
                "in_original": in_original,
                "in_used":     res["input_tokens"],
                "in_saved":    in_saved,
                "out_tokens":  res["output_tokens"],
                "out_saved":   out_saved,
                "total_saved": total_saved,
                "pct":         pct,
                "latency_ms":  res["latency_ms"],
            }
            results_by_cat[cat].append(rec)

            in_s  = GREEN(str(in_saved))  if in_saved  > 0 else DIM("0")
            out_s = GREEN(str(out_saved)) if out_saved > 0 else DIM("0")
            tot_s = GREEN(f"{pct:.1f}%")  if pct       > 0 else DIM("0.0%")
            print(f"         in_saved={in_s}  out_saved={out_s}  total={tot_s}\n")

    # ── Results by category ───────────────────────────────────────────────────
    print(f"\n  {BOLD('Results by category — ' + args.mode + ' mode')}\n")

    W = [24, 8, 8, 8, 8, 8, 9]
    H = ["Conversation", "In orig", "In used", "In saved", "Out", "Out saved", "Reduction"]
    sep = "  " + "  ".join("─"*w for w in W)

    grand = {"io":0,"iu":0,"is":0,"ot":0,"os":0,"ts":0,"n":0}

    for cat in categories:
        recs = results_by_cat.get(cat, [])
        if not recs:
            continue
        print(f"  {BOLD(CYAN(CATEGORY_NAMES.get(cat,cat)))}")
        print("  " + "  ".join(h.ljust(W[i]) for i,h in enumerate(H)))
        print(sep)

        cat_t = {"io":0,"iu":0,"is":0,"ot":0,"os":0,"ts":0}
        for r in recs:
            row = [
                r["name"][:W[0]-1],
                str(r["in_original"]),
                str(r["in_used"]),
                GREEN(str(r["in_saved"])) if r["in_saved"] > 0 else DIM("0"),
                str(r["out_tokens"]),
                GREEN(str(r["out_saved"])) if r["out_saved"] > 0 else DIM("0"),
                GREEN(f"{r['pct']:.1f}%")  if r["pct"] > 0    else DIM("0.0%"),
            ]
            print("  " + "  ".join(str(v).ljust(W[i]) for i,v in enumerate(row)))
            for k,v in [("io",r["in_original"]),("iu",r["in_used"]),("is",r["in_saved"]),
                        ("ot",r["out_tokens"]),("os",r["out_saved"]),("ts",r["total_saved"])]:
                cat_t[k] += v

        # Category subtotal
        n = len(recs)
        cp = cat_t["ts"] / max(1, cat_t["io"]+cat_t["ot"]+cat_t["os"]) * 100
        print(sep)
        cat_row = [
            f"Subtotal ({n})",
            str(cat_t["io"]), str(cat_t["iu"]),
            GREEN(str(cat_t["is"])) if cat_t["is"] > 0 else DIM("0"),
            str(cat_t["ot"]),
            GREEN(str(cat_t["os"])) if cat_t["os"] > 0 else DIM("0"),
            GREEN(f"{cp:.1f}%")     if cp > 0           else DIM("0.0%"),
        ]
        print("  " + "  ".join(str(v).ljust(W[i]) for i,v in enumerate(cat_row)))
        print()

        for k in grand:
            if k == "n": grand["n"] += n
            elif k in cat_t: grand[k] += cat_t[k]

    # ── Grand total ───────────────────────────────────────────────────────────
    gp = grand["ts"] / max(1, grand["io"]+grand["ot"]+grand["os"]) * 100
    print("  " + "═"*(sum(W)+len(W)*2))
    grand_row = [
        BOLD(f"GRAND TOTAL ({grand['n']})"),
        str(grand["io"]), str(grand["iu"]),
        GREEN(BOLD(str(grand["is"]))),
        str(grand["ot"]),
        GREEN(BOLD(str(grand["os"]))),
        GREEN(BOLD(f"{gp:.1f}%")),
    ]
    print("  " + "  ".join(str(v).ljust(W[i]) for i,v in enumerate(grand_row)))

    # ── Cost estimate ─────────────────────────────────────────────────────────
    in_cost  = grand["is"] / 1_000_000 * 2.50
    out_cost = grand["os"] / 1_000_000 * 10.00
    total_cost = in_cost + out_cost
    per = total_cost / max(1, grand["n"])

    print(f"\n  {BOLD('Cost savings')}  (GPT-4o: $2.50/1M input · $10.00/1M output)")
    print(f"  Input  : {grand['is']:6} tokens → {GREEN(f'${in_cost:.5f}')}")
    print(f"  Output : {grand['os']:6} tokens → {GREEN(f'${out_cost:.5f}')}")
    print(f"  Total  :          saved → {GREEN(BOLD(f'${total_cost:.4f}'))} / {grand['n']} requests")
    print(f"\n  Extrapolated:")
    print(f"    1,000 req/day  → {GREEN(f'${per*1000:.2f}/day')}")
    print(f"   10,000 req/day  → {GREEN(f'${per*10000:.2f}/day')}")

    # ── Tips ──────────────────────────────────────────────────────────────────
    if grand["is"] == 0 and not args.dry_run:
        print(YELLOW(f"\n  Input savings = 0. Enable: python3 lco_ctl.py mode {args.mode}"))
    if grand["os"] == 0 and not args.dry_run:
        print(YELLOW("\n  Output savings = 0. Enable: python3 lco_ctl.py output on"))
    print(f"\n  {DIM('Dashboard: http://127.0.0.1:8000/lco/dashboard')}\n")


if __name__ == "__main__":
    main()