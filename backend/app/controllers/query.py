import asyncio
import logging
import re
import time

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from backend.app.schema_link import (
    model_budget,
    link_relevant_tables,
    compact_schema,
    compute_confidence,
    extract_table_names_from_prev_query,
)
from backend.app.llm import generate_sql
from backend.app.sqlvalidate import validate_sql

log = logging.getLogger(__name__)


async def run_query(user_id, db, kb, cfg, providers, session_log, req_data):
    if not db.connected(user_id):
        raise HTTPException(409, "No database connected")
    q = req_data.question.strip()
    if not q:
        raise HTTPException(400, "Empty question")
    context = req_data.context
    glossary = kb.get_glossary(db.get_db_id(user_id))
    retrieved = kb.retrieve_similar(db.get_db_id(user_id), q, k=3)
    budget = model_budget(cfg.get("provider", "ollama"), cfg.get("model", ""))
    full_schema = db.get_schema(user_id)
    context_tables = (
        extract_table_names_from_prev_query(context[-1].sql, db.get_dialect(user_id))
        if context
        else set()
    )
    tables = link_relevant_tables(
        q, full_schema, glossary, retrieved, budget["max_tables"], context_tables
    )
    schema_text = compact_schema(full_schema, tables, budget["samples"])
    try:
        gen = await generate_sql(
            providers.get(),
            q,
            schema_text,
            db.get_dialect(user_id),
            glossary,
            retrieved,
            budget["max_examples"],
            context,
        )
    except Exception:
        log.warning("SQL generation failed during run_query", exc_info=True)
        out = {
            "answered": True,
            "sql": "",
            "restatement": "",
            "confidence": "low",
            "confidence_reason": "Could not form a query - try rephrasing",
            "based_on_verified": False,
            "execution_error": "Could not form a query - try rephrasing",
            "columns": [],
            "rows": [],
        }
        out["query_id"] = session_log.log_query(db.get_db_id(user_id), q, out)
        return out
    sql = gen.get("sql", "")
    restatement = gen.get("restatement", "")
    exec_result = await run_in_threadpool(db.execute, user_id, sql)
    confidence, reason, based = compute_confidence(retrieved, exec_result)
    out = {
        "answered": True,
        "sql": sql,
        "restatement": restatement,
        "confidence": confidence,
        "confidence_reason": reason,
        "based_on_verified": based,
        "columns": exec_result.get("columns", []),
        "rows": exec_result.get("rows", []),
        "truncated": exec_result.get("truncated", False),
        "tables_used": tables,
    }
    if "error" in exec_result:
        out["execution_error"] = exec_result["error"]
    out["query_id"] = session_log.log_query(db.get_db_id(user_id), q, out)
    return out


async def feedback(user_id, db, kb, session_log, req_data):
    if not db.connected(user_id):
        raise HTTPException(409, "No database connected")
    session_log.log_feedback(req_data.query_id, req_data.verdict, req_data.reason)
    if req_data.verdict == "correct":
        kb.add_verified(
            db.get_db_id(user_id), req_data.question, req_data.sql, req_data.restatement
        )
    out = {"ok": True, "trust": kb.trust_level(db.get_db_id(user_id))}
    if req_data.verdict == "correct":
        out["starters"] = [
            v["question"] for v in kb.get_verified(db.get_db_id(user_id))[:6]
        ]
    return out


async def verify(user_id, db, kb, req_data):
    if not db.connected(user_id):
        raise HTTPException(409, "No database connected")
    kb.add_verified(
        db.get_db_id(user_id), req_data.question, req_data.sql, req_data.restatement
    )
    return {"ok": True, "trust": kb.trust_level(db.get_db_id(user_id))}


async def execute(user_id, db, req_data):
    if not db.connected(user_id):
        raise HTTPException(409, "No database connected")
    res = await run_in_threadpool(db.execute, user_id, req_data.sql)
    if "error" in res:
        raise HTTPException(400, res["error"])
    return res


# ── Streaming helpers ──────────────────────────────────────────────


def _generate_hints(question, linked_tables, glossary, retrieved):
    hints = []
    if linked_tables:
        table_list = ", ".join(linked_tables[:4])
        hints.append(f"Scanning tables: {table_list}")
    if glossary:
        g = glossary[0]
        hints.append(f"Translating business term: '{g['term']}'")
        if len(glossary) > 1:
            hints.append(f"Cross-referencing {len(glossary)} glossary definitions")
    if retrieved:
        n = len(retrieved)
        hints.append(f"Reviewing {n} verified query pattern{'s' if n > 1 else ''}")
    ql = question.lower()
    if any(w in ql for w in ["total", "sum", "revenue", "sales"]):
        hints.append("Setting up aggregation with SUM")
    elif any(w in ql for w in ["average", "avg", "mean"]):
        hints.append("Setting up aggregation with AVG")
    elif any(w in ql for w in ["count", "how many", "number of"]):
        hints.append("Setting up COUNT aggregation")
    elif any(w in ql for w in ["top", "best", "highest", "most"]):
        hints.append("Planning ordering with LIMIT")
    elif any(w in ql for w in ["list", "show", "find", "get"]):
        hints.append("Preparing SELECT with filters")
    else:
        hints.append("Analyzing query structure")
    hints.append("Verifying column names against schema")
    hints.append("Building final SQL statement")
    return hints


def _extract_target_from_error(error):
    m = re.search(r"'([^']+)'", error)
    return m.group(1) if m else "query"


def _extract_suggestion(error, schema):
    m = re.search(r"Unknown column: '([^']+)'.*table '([^']+)'", error)
    if m and schema:
        bad_col = m.group(1).lower()
        tbl = m.group(2)
        info = schema.get(tbl) if isinstance(schema, dict) else None
        if info:
            cols = [c["name"] for c in info.get("columns", [])]
            similar = [c for c in cols if c.lower().startswith(bad_col[:3])]
            if similar:
                return f"Available in {tbl}: {', '.join(similar)}"
    return None


def _build_checks(verdict, schema=None):
    if verdict.get("ok"):
        return [{"target": "query", "status": "ok", "message": "All tables and columns exist"}]
    checks = []
    for e in verdict.get("errors", []):
        checks.append({
            "target": _extract_target_from_error(e),
            "status": "error",
            "message": e,
            "suggestion": _extract_suggestion(e, schema),
        })
    return checks


# ── Streaming query controller ────────────────────────────────────


async def run_query_stream(user_id, db, kb, cfg, providers, session_log, req_data):
    if not db.connected(user_id):
        yield {"kind": "error", "message": "No database connected"}
        return
    q = req_data.question.strip()
    if not q:
        yield {"kind": "error", "message": "Empty question"}
        return
    context = req_data.context
    query_start = time.monotonic()

    glossary = kb.get_glossary(db.get_db_id(user_id))
    retrieved = kb.retrieve_similar(db.get_db_id(user_id), q, k=3)
    budget = model_budget(cfg.get("provider", "ollama"), cfg.get("model", ""))
    full_schema = db.get_schema(user_id)
    context_tables = (
        extract_table_names_from_prev_query(context[-1].sql, db.get_dialect(user_id))
        if context else set()
    )
    tables = link_relevant_tables(
        q, full_schema, glossary, retrieved, budget["max_tables"], context_tables
    )
    schema_text = compact_schema(full_schema, tables, budget["samples"])
    provider_obj = providers.get()

    yield {
        "kind": "schema_linked",
        "tables": list(full_schema.keys()),
        "linked": tables,
        "glossary": glossary,
        "verified_count": len(retrieved),
    }

    hints = _generate_hints(q, tables, glossary, retrieved)
    max_iterations = 3
    feedback = ""
    last_sql = ""
    last_restatement = ""
    success = False

    for attempt in range(1, max_iterations + 1):
        llm_coro = generate_sql(
            provider_obj, q, schema_text, db.get_dialect(user_id),
            glossary, retrieved, budget["max_examples"], context,
            feedback=feedback,
        )
        llm_task = asyncio.create_task(llm_coro)
        try:
            hint_idx = 0
            while True:
                done, _ = await asyncio.wait([llm_task], timeout=2.5)
                if done:
                    gen_result = llm_task.result()
                    break
                yield {
                    "kind": "hint",
                    "message": hints[hint_idx % len(hints)],
                    "elapsed": round(time.monotonic() - query_start, 1),
                }
                hint_idx += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("SQL generation failed during streaming", exc_info=True)
            yield {"kind": "error", "message": "Query generation failed"}
            return
        finally:
            if not llm_task.done():
                llm_task.cancel()

        sql = (gen_result.get("sql") or "").strip()
        restatement = (gen_result.get("restatement") or "").strip()
        last_sql = sql
        last_restatement = restatement

        if not sql:
            yield {"kind": "error", "message": "Model returned empty SQL"}
            return

        yield {"kind": "sql", "attempt": attempt, "sql": sql}

        verdict = validate_sql(sql, full_schema, db.get_dialect(user_id))
        checks = _build_checks(verdict, full_schema)
        passed = verdict.get("ok", False)

        yield {"kind": "validation", "attempt": attempt, "checks": checks, "passed": passed}

        if passed:
            success = True
            break

        if attempt < max_iterations:
            errors = verdict.get("errors", [])
            error_msg = errors[0] if errors else "Validation failed"
            yield {
                "kind": "repair",
                "attempt": attempt,
                "total": max_iterations,
                "error": error_msg,
                "suggestion": _extract_suggestion(error_msg, full_schema) or "Retrying with corrections",
                "old_sql": sql,
            }
            fb_lines = [
                "The previous SQL attempt was:",
                sql,
                "",
                "Problems found:",
            ]
            fb_lines += [f"- {e}" for e in errors]
            fb_lines.append("Return a corrected SQL query that fixes these problems.")
            feedback = "\n".join(fb_lines)

    if not success:
        yield {"kind": "error", "message": f"Could not generate valid SQL after {max_iterations} attempts"}
        return

    try:
        exec_start = time.monotonic()
        exec_result = await run_in_threadpool(db.execute, user_id, sql)
        exec_elapsed = round(time.monotonic() - exec_start, 3)
    except Exception as e:
        exec_result = {"error": str(e)}
        exec_elapsed = 0

    yield {
        "kind": "execution",
        "rows": len(exec_result.get("rows", [])),
        "elapsed": exec_elapsed,
        "truncated": exec_result.get("truncated", False),
    }

    confidence, reason, based = compute_confidence(retrieved, exec_result)

    yield {
        "kind": "confidence",
        "level": confidence,
        "reason": reason,
        "based_on_verified": based,
    }

    out = {
        "answered": True,
        "sql": sql,
        "restatement": last_restatement,
        "confidence": confidence,
        "confidence_reason": reason,
        "based_on_verified": based,
        "columns": exec_result.get("columns", []),
        "rows": exec_result.get("rows", []),
        "truncated": exec_result.get("truncated", False),
        "tables_used": tables,
    }
    if "error" in exec_result:
        out["execution_error"] = exec_result["error"]
    out["query_id"] = session_log.log_query(db.get_db_id(user_id), q, out)

    yield {"kind": "result", "data": out}
